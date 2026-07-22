import asyncio
from abc import ABC, abstractmethod
from typing import Optional
from .models import Message
from .bus import MessageBus

class Agent(ABC):
    """
    Classe base para agentes.

    - Cada agente possui um 'name' (identificador), uma 'persona' (system prompt)
      e uma referência ao MessageBus compartilhado.
    - Ciclo de vida assíncrono: start() / stop().
    - on_message(): hook a ser sobrescrito pelos agentes concretos para reagir a mensagens.
    """

    def __init__(self, name: str, persona: str, bus: MessageBus):
        self.name = name
        self.persona = persona
        self.bus = bus
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Inicia o loop do agente em background."""
        if self._running:
            return
        self._running = True
        # publica mensagem do sistema anunciando que o agente iniciou
        self.bus.publish(Message(sender=self.name, role="system", content=f"{self.name} started. Persona: {self.persona}"))
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Para o agente e aguarda a finalização."""
        self._running = False
        if self._task:
            # cancela a task de subscribe se necessário
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.bus.publish(Message(sender=self.name, role="system", content=f"{self.name} stopped."))

    async def _run_loop(self):
        """
        Loop principal: subscreve o bus a partir do índice atual do histórico,
        e chama on_message() para cada mensagem nova.
        """
        start_idx = self.bus.last_index()
        try:
            async for msg in self.bus.subscribe(start_index=start_idx):
                if not self._running:
                    break
                # Evita processar mensagens deste mesmo agente (comportamento padrão)
                if msg.sender == self.name:
                    continue
                try:
                    await self.on_message(msg)
                except Exception as e:
                    # Erros internos do agente são publicados no bus como mensagens do sistema
                    err_msg = Message(sender=self.name, role="system", content=f"Error processing message: {e}")
                    self.bus.publish(err_msg)
        except asyncio.CancelledError:
            # tarefa cancelada durante shutdown
            pass

    @abstractmethod
    async def on_message(self, message: Message):
        """
        Hook a ser implementado por agentes concretos.
        Deve publicar respostas no bus quando apropriado.
        """
        raise NotImplementedError

    def publish(self, content: str, role: str = "assistant", metadata: dict = None):
        """Atalho para publicar uma mensagem no MessageBus."""
        self.bus.publish(Message(sender=self.name, role=role, content=content, metadata=metadata or {}))
