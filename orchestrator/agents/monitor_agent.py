"""
monitor_agent.py

Agente silencioso de orquestração. Não usa LLM. Vigia o bus e detecta falas
dos debatedores (Qwen, Groq, Gemini) que NÃO citam nenhum colega — sintoma
clássico de agente que largou o microfone antes da vez.

Quando detecta o padrão, republica a fala original com um sufixo interno
(marcado em metadata como monitor_signal) que incentiva o próximo LLM a
citar um colega para acionar o próximo turno. O sufixo é filtrado do
terminal pelo display_messages().

A arquitetura de pub/sub por menção é preservada: o Monitor NÃO cria agentes,
NÃO injeta falas próprias e republica usando o sender original.
"""

from orchestrator.agent import Agent
from orchestrator.models import Message


class MonitorAgent(Agent):
    """Vigia silêncio da mesa e republica falas órfãs com sinal de continuação."""

    DEBATEDORES = ("Qwen", "Groq", "Gemini")

    def __init__(self, name: str, bus):
        super().__init__(name=name, persona="", bus=bus)
        self.is_default_responder = False

    async def on_message(self, message: Message):
        if message.role == "system":
            return

        if "Tive um problema" in message.content or "Error code:" in message.content:
            return

        if message.sender not in self.DEBATEDORES:
            return

        if "[SOLUÇÃO FINAL]" in message.content or "ENCERRA O CICLO" in message.content:
            return

        if any(
            colega.lower() in message.content.lower()
            for colega in self.DEBATEDORES
            if colega != message.sender
        ):
            return

        sinal_interno = (
            f"{message.content}\n\n"
            f"[INTERNO-MONITOR: a fala acima não citou nenhum colega. "
            f"A mesa quer continuar o debate. Ao republicar/responder, "
            f"termine citando Groq ou Gemini para acionar o próximo turno. "
            f"Lembre-se: no modo padrão só o Gemini pode encerrar com [SOLUÇÃO FINAL].]"
        )

        self.bus.publish(
            Message(
                sender=message.sender,
                role="assistant",
                content=sinal_interno,
                metadata={"type": "monitor_signal"},
            )
        )
