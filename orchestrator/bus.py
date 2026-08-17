import asyncio
from typing import List, AsyncIterator
from .models import Message

class MessageBus:
    """
    MessageBus em memória com histórico e pub/sub básico.

    Para produção substitua o backend por Redis/Kafka/DB para persistência e multi-processo.
    """

    def __init__(self):
        self._messages: List[Message] = []
        self._cond = asyncio.Condition()

    def publish(self, msg: Message):
        self._messages.append(msg)
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(self._notify)
        except RuntimeError:
            asyncio.create_task(self._notify())

    def _notify(self):
        async def _notify_inner():
            async with self._cond:
                self._cond.notify_all()
        try:
            asyncio.create_task(_notify_inner())
        except RuntimeError:
            pass

    def history(self, since_index: int = 0) -> List[Message]:
        return list(self._messages[since_index:])

    def last_index(self) -> int:
        return len(self._messages)

    async def subscribe(self, start_index: int = 0) -> AsyncIterator[Message]:
        idx = start_index
        while True:
            while idx < len(self._messages):
                yield self._messages[idx]
                idx += 1

            async with self._cond:
                await self._cond.wait()
