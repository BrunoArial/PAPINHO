from ..agent import Agent
from ..models import Message
import asyncio

class LLMAgent(Agent):
    """
    Placeholder para um agente que consulta um LLM.

    Para integração real, substitua _call_llm por uma implementação que chame
    OpenAI/Anthropic/llama.cpp/etc. Insira tratamento de tokens, truncation,
    retry e monitoramento.
    """

    async def on_message(self, message: Message):
        # Ignora mensagens vazias
        if len(message.content.strip()) == 0:
            return
        response_text = await self._call_llm(message)
        self.publish(response_text)

    async def _call_llm(self, message: Message) -> str:
        # Simula latência e retorno do LLM. Em produção, troque por chamada real.
        await asyncio.sleep(0.5)
        snippet = message.content[:80].replace('\n', ' ')
        return f"[LLM response to: {snippet}]"
