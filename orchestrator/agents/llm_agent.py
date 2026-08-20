import os
import re
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv
from orchestrator.agent import Agent
from orchestrator.models import Message

load_dotenv()

class LLMAgent(Agent):
    def __init__(self, name: str, persona: str, bus, is_default_responder: bool = False, model: str = "qwen/qwen3.6-27b", max_tokens: int = 1500):
        super().__init__(name, persona=persona, bus=bus)
        self.is_default_responder = is_default_responder
        self.model = model
        self.max_tokens = max_tokens

        self.client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
            timeout=95.0
        )
        self.memory = []

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        # Limpa pontuação antes do rfind para que "**Qwen,**" e "Qwen!" ativem igual.
        texto_limpo_para_roteamento = re.sub(r'[^a-zA-Z0-9\s]', '', message.content.lower())

        nomes_agentes = ["qwen", "groq", "gemini"]
        ultimo_nome = None
        maior_indice = -1

        for nome in nomes_agentes:
            indice = texto_limpo_para_roteamento.rfind(nome)
            if indice > maior_indice:
                maior_indice = indice
                ultimo_nome = nome

        is_addressed_to_me = (ultimo_nome == self.name.lower())

        if not is_addressed_to_me and not (self.is_default_responder and message.sender == "User"):
            return

        async with self.bus.bastao:
            if self.bus.history() and self.bus.history()[-1] is not message:
                return

            try:
                response_text = await asyncio.wait_for(
                    self._call_llm(message),
                    timeout=35.0,
                )

                if not response_text:
                    raise ValueError("A resposta da API da Groq veio completamente vazia (possível bloqueio de segurança).")

                texto_limpo = re.sub(r"<think>.*?(?:</think>|$)\n*", "", response_text, flags=re.DOTALL).strip()

                if not texto_limpo:
                    raise ValueError("A IA processou, mas o texto final ficou vazio após a limpeza.")

                nova_mensagem = Message(sender=self.name, role="assistant", content=texto_limpo)
                self.bus.publish(nova_mensagem)

            except asyncio.TimeoutError:
                msg_bruta = next((m.content for m in self.bus.history() if m.sender == "User"), "")
                if "analise esta mensagem do usuário: '" in msg_bruta:
                    pedido_original = msg_bruta.split("analise esta mensagem do usuário: '")[1].split("\n\n[")[0]
                else:
                    pedido_original = "o desafio principal."

                erro_msg = Message(
                    sender=self.name,
                    role="assistant",
                    content=(
                        f"[ALERTA DE SISTEMA: IGNORE O TIMEOUT ABAIXO. NÃO DEBATA SOBRE INFRAESTRUTURA DE API]\n"
                        f"⏱️ Timeout (100s) na minha API Groq.\n\n"
                        f"Gemini, pegue o bastão e continue o debate sem mim — sintetize e feche com [SOLUÇÃO FINAL] "
                        f"baseado EXCLUSIVAMENTE no pedido original: {pedido_original}"
                    ),
                )
                self.bus.publish(erro_msg)

            except BaseException as e:
                msg_bruta = next((m.content for m in self.bus.history() if m.sender == "User"), "")
                if "analise esta mensagem do usuário: '" in msg_bruta:
                    pedido_original = msg_bruta.split("analise esta mensagem do usuário: '")[1].split("\n\n[")[0]
                else:
                    pedido_original = "o desafio principal."

                erro_msg = Message(
                    sender=self.name,
                    role="assistant",
                    content=(
                        f"[ALERTA DE SISTEMA: IGNORE O ERRO ABAIXO. NÃO DEBATA SOBRE INFRAESTRUTURA DE API]\n"
                        f"❌ Falha na minha API Groq: {type(e).__name__}: {e}\n\n"
                        f"Gemini, minha API falhou. Pegue o bastão, sintetize o debate e feche com [SOLUÇÃO FINAL] "
                        f"baseado EXCLUSIVAMENTE no pedido original: {pedido_original}"
                    ),
                )
                self.bus.publish(erro_msg)

    async def _call_llm(self, message: Message) -> str:
        self.memory.append({"role": "user", "content": message.content})
        if len(self.memory) > 4:
            self.memory = self.memory[-4:]

        messages_payload = [{"role": "system", "content": self.persona}] + self.memory

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages_payload,
            temperature=0.7,
            max_tokens=self.max_tokens,
        )

        reply_text = response.choices[0].message.content
        self.memory.append({"role": "assistant", "content": reply_text})
        return reply_text
