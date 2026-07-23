import os
from dotenv import load_dotenv
from openai import AsyncOpenAI
from ..agent import Agent
from ..models import Message

load_dotenv()

class LLMAgent(Agent):
    """
    Agente conectado a um modelo real de IA, agora com memória de curto prazo.
    """
    
    def __init__(self, name: str, persona: str, bus):
        super().__init__(name, persona, bus)
        self.client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        # 1. A NOVA MEMÓRIA: Uma lista vazia que vai guardar a conversa
        self.memory = []

    async def on_message(self, message: Message):
        if message.role == "system":
            return
            
        if len(message.content.strip()) == 0:
            return
            
        is_from_user = message.role == "user"
        is_mentioned = self.name.lower() in message.content.lower()
        
        if not is_from_user and not is_mentioned:
            return

        response_text = await self._call_llm(message)
        self.publish(response_text)

    async def _call_llm(self, message: Message) -> str:
        try:
            # 1. Guarda o que o usuário (ou outro bot) acabou de falar
            self.memory.append({"role": "user", "content": message.content})

            # --- A NOVA TRAVA DE SEGURANÇA ---
            # Limita a memória às últimas 10 mensagens (5 perguntas e 5 respostas)
            max_mensagens = 10
            if len(self.memory) > max_mensagens:
                self.memory = self.memory[-max_mensagens:] # O [-10:] corta as mais velhas!
            # ---------------------------------

            # 2. Monta o pacote: O System Prompt (Persona) + O Histórico (agora limitado)
            messages_payload = [{"role": "system", "content": self.persona}] + self.memory

            # 3. Envia para a Groq
            response = await self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=messages_payload,
                temperature=0.7,
                max_tokens=300
            )
            
            # Extrai a resposta
            reply_text = response.choices[0].message.content

            # 4. Guarda a resposta do próprio bot
            self.memory.append({"role": "assistant", "content": reply_text})

            return reply_text
        except Exception as e:
            return f"[Erro de comunicação com a IA]: {str(e)}"