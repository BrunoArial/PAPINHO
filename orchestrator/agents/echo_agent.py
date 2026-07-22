from ..agent import Agent
from ..models import Message

class EchoAgent(Agent):
    """
    Agente que ecoa mensagens que mencionam seu nome.
    Útil para testes e demonstrações.
    """

    async def on_message(self, message: Message):
        # Ignora mensagens de sistema
        if message.role == "system":
            return
            
        if self.name.lower() in message.content.lower():
            # Pega apenas os primeiros 50 caracteres da mensagem original para não acumular lixo
            safe_content = message.content[:50] + ("..." if len(message.content) > 50 else "")
            self.publish(f"Echoing your message: {safe_content}")