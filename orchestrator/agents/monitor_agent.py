"""
monitor_agent.py

Agente silencioso de orquestração. Não usa LLM. Sua única função é vigiar
o bus e detectar falas dos debatedores (Qwen, Revisor, Gemini) que NÃO
citam nenhum colega debatedor — sintoma clássico de um agente que largou
o microfone antes da vez.

Quando detecta esse padrão, republica a fala original com um sufixo
interno (marcado em metadata como monitor_signal) que incentiva o
próximo LLM a citar um colega para acionar o próximo turno. O sufixo
é filtrado do terminal pelo `display_messages()`.

A arquitetura de pub/sub baseada em menção é preservada: o Monitor
NÃO cria agentes, NÃO injeta falas próprias, e republica usando o
sender original. Ele apenas reforça a regra "mesa continua até
Gemini encerrar" sem mexer no MessageBus nem nas personas.
"""

from orchestrator.agent import Agent
from orchestrator.models import Message


class MonitorAgent(Agent):
    """Vigia silêncio da mesa e republica falas órfãs com sinal de continuação."""

    DEBATEDORES = ("Qwen", "Revisor", "Gemini")

    def __init__(self, name: str, bus):
        super().__init__(name=name, persona="", bus=bus)
        # Persona vazia: este agente nunca chama LLM.
        # Marcamos como default_responder=False explicitamente para clareza.
        self.is_default_responder = False

    async def on_message(self, message: Message):
        # Ignora mensagens de sistema
        if message.role == "system":
            return

        # TRAVA DE SEGURANÇA: Ignora se a mensagem for um erro de API
        if "Tive um problema" in message.content or "Error code:" in message.content:
            return

        # Só observa debatedores. User, Logger, PromptGuard ficam de fora.
        if message.sender not in self.DEBATEDORES:
            return

        # Se a própria fala já tem marcador de encerramento explícito, não me meto.
        if "[SOLUÇÃO FINAL]" in message.content or "ENCERRA O CICLO" in message.content:
            return

        # Se o debatedor já citou algum COLEGA (não a si mesmo), tá tudo certo.
        if any(
            colega.lower() in message.content.lower()
            for colega in self.DEBATEDORES
            if colega != message.sender
        ):
            return

        # Falta: debatedor publicou sem citar ninguém. A mesa vai congelar.
        # Republico a fala com sinal interno de continuação.
        sinal_interno = (
            f"{message.content}\n\n"
            f"[INTERNO-MONITOR: a fala acima não citou nenhum colega. "
            f"A mesa quer continuar o debate. Ao republicar/responder, "
            f"termine citando Revisor ou Gemini para acionar o próximo turno. "
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
