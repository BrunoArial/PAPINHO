from datetime import datetime
from orchestrator.agent import Agent
from orchestrator.models import Message

class LoggerAgent(Agent):
    """
    Agente silencioso que escuta o barramento e salva todas as mensagens em um arquivo .txt.
    """
    def __init__(self, name: str, bus, arquivo_log: str = "historico_conversa.txt"):
        # Passamos uma persona genérica, pois ele não usa IA
        super().__init__(name, persona="Eu apenas anoto.", bus=bus)
        self.arquivo_log = arquivo_log

    async def on_message(self, message: Message):
        # Ignora mensagens do sistema (instruções internas)
        if message.role == "system":
            return
            
        # Pega a data e hora atual
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Define quem está falando (Usuário ou Agente)
        remetente = "Você" if message.role == "user" else "IA/Agente"
        
        # Monta a linha de texto bonitinha
        texto_para_salvar = f"[{agora}] {remetente}:\n{message.content}\n"
        texto_para_salvar += "-" * 50 + "\n"

        # Abre o arquivo em modo "a" (append = adicionar no final) e salva
        with open(self.arquivo_log, "a", encoding="utf-8") as f:
            f.write(texto_para_salvar)