from ..agent import Agent
from ..models import Message
import asyncio

class LLMAgent(Agent):
    """
    Placeholder para um agente que consulta um LLM.
    ...
    """

    async def on_message(self, message: Message):
        # 1. Ignora mensagens de sistema (como avisos de start/stop)
        if message.role == "system":
            return
            
        # 2. Ignora mensagens vazias
        if len(message.content.strip()) == 0:
            return
            
        # 3. Verifica a origem e o alvo da mensagem
        is_from_user = message.role == "user"
        is_mentioned = self.name.lower() in message.content.lower()
        
        # Se for uma mensagem de outro bot ("assistant") e este agente NÃO foi mencionado, ignore.
        # Isso quebra o loop infinito de respostas automáticas não solicitadas.
        if not is_from_user and not is_mentioned:
            return

        response_text = await self._call_llm(message)
        self.publish(response_text)

    async def _call_llm(self, message: Message) -> str:
        # Simula latência e retorno do LLM. Em produção, troque por chamada real.
        await asyncio.sleep(0.5)
        snippet = message.content[:80].replace('\n', ' ')
        return f"[LLM response to: {snippet}]"