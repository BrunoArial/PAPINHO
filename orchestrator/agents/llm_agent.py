import os
import re
from openai import AsyncOpenAI
from dotenv import load_dotenv
from orchestrator.agent import Agent
from orchestrator.models import Message

load_dotenv()

class LLMAgent(Agent):
    # PASSO 1: Adicionado max_tokens=1500 como parâmetro no __init__
    def __init__(self, name: str, persona: str, bus, is_default_responder: bool = False, model: str = "qwen/qwen3.6-27b", max_tokens: int = 1500):
        super().__init__(name, persona=persona, bus=bus)
        self.is_default_responder = is_default_responder
        self.model = model
        
        # Salvando o max_tokens na classe para usar depois
        self.max_tokens = max_tokens
        
        self.client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        self.memory = []

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        is_addressed_to_me = self.name.lower() in message.content.lower()
        if not is_addressed_to_me and not (self.is_default_responder and message.sender == "User"):
            return

        try:
            response_text = await self._call_llm(message)
            nova_mensagem = Message(sender=self.name, role="assistant", content=response_text)
            self.bus.publish(nova_mensagem)
        except Exception as e:
            erro_msg = Message(sender=self.name, role="assistant", content=f"Tive um problema na minha API Groq: {str(e)}")
            self.bus.publish(erro_msg)

    async def _call_llm(self, message: Message) -> str:
        self.memory.append({"role": "user", "content": message.content})
        if len(self.memory) > 10:
            self.memory = self.memory[-10:]
            
        messages_payload = [{"role": "system", "content": self.persona}] + self.memory

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages_payload,
            temperature=0.7,
            # PASSO 2: Agora ele usa a variável dinâmica definida no agente
            max_tokens=self.max_tokens,
        )
        
        reply_text = response.choices[0].message.content
        self.memory.append({"role": "assistant", "content": reply_text})
        return reply_text