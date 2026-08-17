import asyncio
from abc import ABC, abstractmethod
from typing import Optional
from .models import Message
from .bus import MessageBus

class Agent(ABC):
    """
    Classe base para agentes.

    Cada agente possui um `name` (identificador), uma `persona` (system prompt)
    e uma referência ao MessageBus compartilhado. Ciclo de vida assíncrono:
    start() / stop(). on_message() é o hook a ser sobrescrito.
    """

    def __init__(self, name: str, persona: str, bus: MessageBus, is_default_responder: bool = False):
        self.name = name
        self.persona = persona
        self.bus = bus
        # Quando True, o agente escuta mensagens do User mesmo sem ser citado.
        self.is_default_responder = is_default_responder
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self.bus.publish(Message(sender=self.name, role="system", content=f"{self.name} started. Persona: {self.persona}"))
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.bus.publish(Message(sender=self.name, role="system", content=f"{self.name} stopped."))

    async def _run_loop(self):
        start_idx = self.bus.last_index()
        try:
            async for msg in self.bus.subscribe(start_index=start_idx):
                if not self._running:
                    break
                if msg.sender == self.name:
                    continue
                try:
                    await self.on_message(msg)
                except Exception as e:
                    err_msg = Message(sender=self.name, role="system", content=f"Error processing message: {e}")
                    self.bus.publish(err_msg)
        except asyncio.CancelledError:
            pass

    @abstractmethod
    async def on_message(self, message: Message):
        raise NotImplementedError

    def publish(self, content: str, role: str = "assistant", metadata: dict = None):
        self.bus.publish(Message(sender=self.name, role=role, content=content, metadata=metadata or {}))
