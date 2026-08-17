import os
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from orchestrator.agent import Agent
from orchestrator.models import Message

load_dotenv()

class GeminiAgent(Agent):
    def __init__(self, name: str, persona: str, bus, model: str = "gemini-3.1-flash-lite"):
        super().__init__(name, persona=persona, bus=bus)
        self.model = model
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        # Aceita tanto o nome limpo quanto a forma hifenizada ("g-e-m-i-n-i"),
        # para tolerar delegação que chega ao bus com hífens por alguma camada antiga.
        self._aliases = [
            self.name.lower(),
            "-".join(self.name.lower()),
        ]

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        conteudo_lower = message.content.lower()
        chamado = any(alias in conteudo_lower for alias in self._aliases)

        if not chamado:
            return

        async with self.bus.bastao:
            if self.bus.history()[-1] is not message:
                return

            try:
                response_text = await asyncio.wait_for(self._call_llm(), timeout=35.0)
                nova_mensagem = Message(sender=self.name, role="assistant", content=response_text)
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
                        f"A API do Google sofreu um Timeout (35s).\n\n"
                        f"Gpt, assuma a análise focando EXCLUSIVAMENTE no pedido original: {pedido_original}"
                    )
                )
                self.bus.publish(erro_msg)

            except Exception as e:
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
                        f"Tive um problema na API do Google: {str(e)}\n\n"
                        f"Qwen, minha conexão caiu. Assuma a exploração focando EXCLUSIVAMENTE neste pedido: {pedido_original}"
                    )
                )
                self.bus.publish(erro_msg)

    async def _call_llm(self) -> str:
        # Reconstrói contexto das últimas 10 mensagens: ele vê o que foi dito
        # enquanto estava ocioso e pode entrar no debate com informação fresca.
        historico_recente = self.bus.history()[-10:]

        contexto = "Histórico recente da mesa redonda:\n\n"
        for msg in historico_recente:
            if msg.role != "system":
                contexto += f"[{msg.sender}]: {msg.content}\n"

        prompt_final = (
            f"{contexto}\n"
            f"--- \n"
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
            config=config
        )

        return response.text
