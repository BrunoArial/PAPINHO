from ..agent import Agent
from ..models import Message

class RuleBasedAgent(Agent):
    """
    Agente simples baseado em regras: responde quando encontra palavras-chave.
    Regras: dict{ keyword (str) -> resposta (str) }
    """

    def __init__(self, name: str, persona: str, bus, rules: dict):
        super().__init__(name, persona, bus)
        self.rules = rules

    async def on_message(self, message: Message):
        text = message.content.lower()
        for kw, resp in self.rules.items():
            if kw.lower() in text:
                self.publish(resp)
                return
