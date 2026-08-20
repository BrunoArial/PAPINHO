"""
orchestrator/agents/gemini_agent.py

GeminiAgent — debatedor que usa o Google genai SDK para gerar respostas.

Mudanças desta versão:
- Roteamento centralizado (não depende mais de aliases hifenizados —
  a normalização do router lida com "g-e-m-i-n-i").
- CancelledError NÃO é capturado como falha.
- Pensamento interno (se vier) é incluído no contexto da próxima chamada.
- Erros viram mensagens de recuperação estruturadas.
"""
import asyncio
import os
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

from ..agent import Agent
from ..models import Message
from ..recovery import publish_recovery
from ..router import is_addressed

load_dotenv()


_THINK_OPEN_RE = re.compile(r"<think>", flags=re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", flags=re.IGNORECASE)


def _split_thinking(content: str) -> tuple[str, str]:
    if not content:
        return "", ""

    thinking_parts: list[str] = []
    visible_parts: list[str] = []
    cursor = 0

    while match_open := _THINK_OPEN_RE.search(content, cursor):
        visible_parts.append(content[cursor : match_open.start()])
        match_close = _THINK_CLOSE_RE.search(content, match_open.end())

        if match_close is None:
            thinking_parts.append(content[match_open.end() :])
            cursor = len(content)
            break

        thinking_parts.append(content[match_open.end() : match_close.start()])
        cursor = match_close.end()

    visible_parts.append(content[cursor:])

    thinking = "\n".join(
        part for raw_part in thinking_parts if (part := raw_part.strip())
    )
    content_limpo = " ".join(
        part for raw_part in visible_parts if (part := raw_part.strip())
    )
    return thinking, content_limpo


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


class GeminiAgent(Agent):
    def __init__(
        self,
        name: str,
        persona: str,
        bus,
        model: str = "gemini-3.1-flash-lite",
        api_key_env: str = "GEMINI_API_KEY",
        thinking_share_limit: int = 500,
    ):
        super().__init__(name, persona=persona, bus=bus)
        self.model = model
        self.client = genai.Client(api_key=os.getenv(api_key_env))
        self.thinking_share_limit = thinking_share_limit

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        addressed = is_addressed(message, self.name)
        if not addressed:
            return

        try:
            response_text = await self.await_with_deadline(
                self._call_llm(),
                timeout=35.0,
                label=f"modelo {self.model}",
            )
            thinking, texto_limpo = _split_thinking(response_text)
            self.bus.publish(
                Message(
                    sender=self.name,
                    role="assistant",
                    content=texto_limpo or response_text.strip(),
                    thinking=thinking,
                    turn_id=message.turn_id,
                    mode=message.mode,
                    hop_count=message.hop_count + 1,
                )
            )
        except asyncio.TimeoutError:
            publish_recovery(
                self.bus,
                self.name,
                TimeoutError(f"Timeout (35s) na chamada do modelo {self.model}"),
                source_message=message,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            publish_recovery(self.bus, self.name, e, source_message=message)

    async def _call_llm(self) -> str:
        # Reconstrói contexto das últimas 10 mensagens visíveis.
        historico_recente = self.bus.history()[-10:]

        contexto = "Histórico recente da mesa redonda:\n\n"
        for msg in historico_recente:
            if msg.role == "system":
                continue
            linha = f"[{msg.sender}]: {msg.content}"
            if msg.thinking:
                thinking_trunc = _truncate(msg.thinking, self.thinking_share_limit)
                linha += f"\n  (pensou: {thinking_trunc})"
            contexto += linha + "\n"

        prompt_final = (
            f"{contexto}\n"
            f"---\n"
            f"Você (nome: {self.name}) acabou de ser mencionado na conversa acima.\n"
            f"Responda seguindo ESTRITAMENTE a sua persona e continue o debate com a equipe."
        )

        config = types.GenerateContentConfig(
            system_instruction=self.persona,
            temperature=0.8,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt_final,
            config=config,
        )

        return response.text
