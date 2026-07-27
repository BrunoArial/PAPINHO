import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from orchestrator.agent import Agent
from orchestrator.models import Message

load_dotenv()

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
        # Ignora mensagens do sistema ou as próprias mensagens
        if message.role == "system" or message.sender == self.name:
            return

        # Só responde se for chamado pelo nome
        if self.name.lower() in message.content.lower():
            try:
                historico = self.bus.history()[-8:]
                
                texto_envio = "Aqui está o resumo da nossa reunião. Crie o marketing com base nisso:\n\n"
                
                for msg in historico:
                    if msg.role != "system":
                        texto_envio += f"[{msg.sender}]: {msg.content}\n"
                
                # Adiciona o gatilho final
                texto_envio += f"\nAgora é sua vez, crie o nome e slogan!"
                
                # Envia o pacote completo para a IA
                response = await self.chat.send_message(texto_envio)
                texto_resposta = response.text

                # Publica a resposta (lembrando: sem o 'await' na frente, pois o bus é síncrono)
                nova_mensagem = Message(sender=self.name, role="assistant", content=texto_resposta)
                self.bus.publish(nova_mensagem)
                
            except Exception as e:
                mensagem_erro = f"Desculpe equipe, tive um problema de conexão com meus servidores: {e}"
                nova_mensagem_erro = Message(sender=self.name, role="assistant", content=mensagem_erro)
                self.bus.publish(nova_mensagem_erro)