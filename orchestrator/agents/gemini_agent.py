import os
from google import genai
from google.genai import types
from orchestrator.agent import Agent
from orchestrator.models import Message

class GeminiAgent(Agent):
    def __init__(self, name: str, persona: str, bus, model: str = "gemini-3.1-flash-lite"):
        super().__init__(name, persona=persona, bus=bus)
        
        # Puxa a chave do Google do seu .env
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = model
        
        # Inicia a sessão com as regras do Marqueteiro
        self.chat = self.client.aio.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(
                system_instruction=self.persona,
                temperature=0.7
            )
        )

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        if self.name.lower() in message.content.lower():
            try:
                texto_envio = f"[{message.sender} disse]: {message.content}"
                response = await self.chat.send_message(texto_envio)
                texto_resposta = response.text

                nova_mensagem = Message(sender=self.name, role="assistant", content=texto_resposta)
                self.bus.publish(nova_mensagem)
                
            except Exception as e:
                print(f"\n[Erro no {self.name} - Gemini]: {e}")