import asyncio
from typing import List, AsyncIterator
from .models import Message

class MessageBus:
    """
    MessageBus em memória com histórico e pub/sub básico.

    - publish(msg): adiciona mensagem ao histórico e notifica assinantes
    - history(since_index=0): retorna lista de mensagens a partir do índice
    - subscribe(start_index=0): async iterator que rende novas mensagens conforme chegam

    Nota: para produção substitua o backend por Redis/Kafka/DB para persistência e multi-processo.
    """

    def __init__(self):
        self._messages: List[Message] = []
        self._cond = asyncio.Condition()

    def publish(self, msg: Message):
        """
        Publica uma mensagem no histórico e notifica assinantes.
        Chamada síncrona segura dentro do event loop.
        """
        self._messages.append(msg)
        # Agenda notificação thread-safe no loop atual
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(self._notify)
        except RuntimeError:
            # Se não houver loop rodando (caso raro), apenas notifica diretamente
            # (útil para execução síncrona em scripts curtos)
            asyncio.create_task(self._notify())

    def _notify(self):
        # _notify agendará uma coroutine que fará notify_all()
        async def _notify_inner():
            async with self._cond:
                self._cond.notify_all()
        # schedule coro no loop atual
        try:
            asyncio.create_task(_notify_inner())
        except RuntimeError:
            # nenhum loop disponível; ignore — quem estiver esperando ficará bloqueado
            pass

    def history(self, since_index: int = 0) -> List[Message]:
        return list(self._messages[since_index:])

    def last_index(self) -> int:
        return len(self._messages)

    async def subscribe(self, start_index: int = 0) -> AsyncIterator[Message]:
        """
        Async iterator que produz mensagens a partir de start_index.

        Uso:
            async for msg in bus.subscribe(start_index=...):
                ...
        """
        idx = start_index
        while True:
            # Produce mensagens já disponíveis
            while idx < len(self._messages):
                yield self._messages[idx]
                idx += 1

            # Espera por nova mensagem
            async with self._cond:
                await self._cond.wait()
