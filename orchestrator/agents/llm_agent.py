"""
orchestrator/agents/llm_agent.py

LLMAgent — debatedor genérico que chama um modelo de chat compatível com
OpenAI (Groq, Ollama, vLLM, etc.) e publica no MessageBus.

Mudanças desta versão:
- Roteamento centralizado em orchestrator.router (tokenização por boundaries,
  último debatedor citado).
- CancelledError NÃO é capturado como falha — é re-raiseado imediatamente.
- Pensamento interno (<think>...</think>) é extraído e armazenado em
  Message.thinking, e enviado de volta ao próximo agente junto com a
  resposta visível.
- Erros viram mensagens de recuperação estruturadas (recovery.publish_recovery).
"""
import asyncio
import os
import re

from dotenv import load_dotenv
from openai import AsyncOpenAI

from ..agent import Agent
from ..models import Message
from ..recovery import publish_recovery
from ..router import is_addressed

load_dotenv()


_THINK_OPEN_RE = re.compile(r"<think>", flags=re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", flags=re.IGNORECASE)


def _split_thinking(content: str) -> tuple[str, str]:
    """
    Separa o bloco <think>...</think> do resto do conteúdo visível.

    Se o modelo esquecer de fechar a tag, fecha à força (mesma lógica
    que o display_messages aplicava em chat_interativo.py). Se houver
    conteúdo ANTES da tag <think> (ex.: o modelo escreveu "Oi!" antes de
    abrir o bloco), esse conteúdo é preservado como visível.

    Retorna (thinking, content_limpo).
    """
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


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Trunca texto por bytes, preservando o início e o fim sem quebrar UTF-8."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    if max_bytes <= 0:
        return ""

    marker = "\n[… contexto truncado …]\n"
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        return encoded[:max_bytes].decode("utf-8", errors="ignore")

    content_budget = max_bytes - len(marker_bytes)
    beginning_size = content_budget // 2
    ending_size = content_budget - beginning_size
    beginning = encoded[:beginning_size].decode("utf-8", errors="ignore")
    ending = encoded[-ending_size:].decode("utf-8", errors="ignore")
    return beginning + marker + ending


class LLMAgent(Agent):
    def __init__(
        self,
        name: str,
        persona: str,
        bus,
        is_default_responder: bool = False,
        model: str = "qwen/qwen3.6-27b",
        max_tokens: int = 1500,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 95.0,
        response_timeout: float = 35.0,
        api_key_env: str = "GROQ_API_KEY",
        thinking_share_limit: int = 500,
        memory_byte_limit: int = 24_000,
        reasoning_effort: str | None = None,
        include_reasoning: bool | None = None,
        empty_response_retries: int = 1,
    ):
        super().__init__(
            name,
            persona=persona,
            bus=bus,
            is_default_responder=is_default_responder,
        )
        self.model = model
        self.max_tokens = max_tokens
        self.response_timeout = response_timeout
        self.thinking_share_limit = thinking_share_limit
        self.memory_byte_limit = memory_byte_limit
        self.reasoning_effort = reasoning_effort
        self.include_reasoning = include_reasoning
        self.empty_response_retries = max(0, empty_response_retries)

        self.client = AsyncOpenAI(
            api_key=os.getenv(api_key_env),
            base_url=base_url,
            timeout=timeout,
        )
        self.memory: list[dict] = []

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        if not is_addressed(message, self.name):
            return

        try:
            response_text = await self.await_with_deadline(
                self._call_llm(message, commit_memory=False),
                timeout=self.response_timeout,
                label=f"modelo {self.model}",
            )

            if not response_text:
                raise ValueError(
                    "A resposta da API veio completamente vazia (possível bloqueio de segurança)."
                )

            thinking, texto_limpo = _split_thinking(response_text)
            retries = 0
            while not texto_limpo and retries < self.empty_response_retries:
                retries += 1
                response_text = await self.await_with_deadline(
                    self._call_llm(
                        message,
                        commit_memory=False,
                        final_only=True,
                    ),
                    timeout=self.response_timeout,
                    label=f"resposta final do modelo {self.model}",
                )
                thinking, texto_limpo = _split_thinking(response_text)

            if not texto_limpo:
                raise ValueError(
                    "A IA processou, mas não produziu texto final visível após nova tentativa."
                )

            self._commit_memory(message, response_text)
            self.bus.publish(
                Message(
                    sender=self.name,
                    role="assistant",
                    content=texto_limpo,
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
                TimeoutError(
                    f"Timeout ({self.response_timeout:g}s) na chamada do modelo {self.model}"
                ),
                source_message=message,
            )

        except asyncio.CancelledError:
            raise

        except Exception as e:
            publish_recovery(self.bus, self.name, e, source_message=message)

    async def _call_llm(
        self,
        message: Message,
        *,
        commit_memory: bool = True,
        final_only: bool = False,
    ) -> str:
        # Constrói o turno do usuário incluindo o pensamento do autor da
        # mensagem, se houver. Isso dá continuidade ao raciocínio da mesa.
        user_content = self._format_user_turn(message)
        if final_only:
            user_content += (
                "\n\n[CORREÇÃO DE FORMATO] Produza somente a resposta final visível, "
                "sem tags <think>, sem raciocínio interno e sem repetir instruções."
            )

        user_entry = {"role": "user", "content": user_content}
        candidate_memory = self._fit_memory([*self.memory, user_entry])
        messages_payload = [
            {"role": "system", "content": self.persona},
            *candidate_memory,
        ]

        request_options = {
            "model": self.model,
            "messages": messages_payload,
            "temperature": 0.7,
            "max_completion_tokens": self.max_tokens,
        }
        provider_options = {}
        if self.reasoning_effort is not None:
            provider_options["reasoning_effort"] = self.reasoning_effort
        if self.include_reasoning is not None:
            provider_options["include_reasoning"] = self.include_reasoning
        if provider_options:
            # Campos específicos do endpoint compatível da Groq são enviados
            # sem depender da versão instalada do cliente OpenAI.
            request_options["extra_body"] = provider_options

        response = await self.client.chat.completions.create(
            **request_options,
        )

        reply_text = response.choices[0].message.content or ""
        if not reply_text or not _split_thinking(reply_text)[1]:
            return reply_text

        # Só confirma o turno na memória depois que a API respondeu. Falhas
        # e respostas sem conteúdo visível não contaminam tentativas futuras.
        if commit_memory:
            self.memory = self._fit_memory(
                [*candidate_memory, {"role": "assistant", "content": reply_text}]
            )
        return reply_text

    def _commit_memory(self, message: Message, reply_text: str) -> None:
        """Confirma memória somente após a chamada respeitar o deadline."""
        user_entry = {"role": "user", "content": self._format_user_turn(message)}
        candidate_memory = self._fit_memory([*self.memory, user_entry])
        self.memory = self._fit_memory(
            [*candidate_memory, {"role": "assistant", "content": reply_text}]
        )

    def _fit_memory(self, messages: list[dict]) -> list[dict]:
        """Mantém as mensagens mais recentes dentro do orçamento UTF-8."""
        if self.memory_byte_limit <= 0:
            return []

        selected_reversed: list[dict] = []
        remaining = self.memory_byte_limit

        for message in reversed(messages):
            content = str(message.get("content") or "")
            content_size = len(content.encode("utf-8"))

            if content_size <= remaining:
                selected_reversed.append({**message, "content": content})
                remaining -= content_size
                continue

            # A mensagem mais recente nunca é descartada por completo; ela é
            # reduzida preservando começo e fim. Mensagens antigas que não
            # cabem são simplesmente removidas.
            if not selected_reversed and remaining > 0:
                selected_reversed.append(
                    {**message, "content": _truncate_utf8(content, remaining)}
                )
            break

        return list(reversed(selected_reversed))

    def _format_user_turn(self, message: Message) -> str:
        """Inclui o pensamento do autor (se houver) antes da fala visível."""
        if not message.thinking:
            return message.content
        thinking_trunc = _truncate(message.thinking, self.thinking_share_limit)
        return (
            f"[{message.sender} pensou]: {thinking_trunc}\n"
            f"[{message.sender} disse]: {message.content}"
        )
