import asyncio
from datetime import datetime
from orchestrator.agent import Agent
from orchestrator.models import Message

class LoggerAgent(Agent):
    """
    Agente silencioso que escuta o barramento e salva todas as mensagens em um arquivo .txt.
    """
    def __init__(self, name: str, bus, arquivo_log: str = "historico_conversa.txt"):
        super().__init__(name, persona="Eu apenas anoto.", bus=bus)
        self.arquivo_log = arquivo_log

    async def on_message(self, message: Message):
        metadata_type = (message.metadata or {}).get("type")
        if message.role == "system" and metadata_type not in {
            "agent_processing_error",
            "agent_task_error",
            "agent_recovery_exhausted",
            "monitor_handoff_exhausted",
            "routing_unavailable",
        }:
            return

        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        remetente = "Você" if message.role == "user" else "IA/Agente"

        texto_para_salvar = f"[{agora}] {remetente}:\n{message.content}\n"
        texto_para_salvar += "-" * 50 + "\n"

        await asyncio.to_thread(self._write, texto_para_salvar)

    def _write(self, text: str) -> None:
        with open(self.arquivo_log, "a", encoding="utf-8") as f:
            f.write(text)
