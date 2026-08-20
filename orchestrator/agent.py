from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import Awaitable, Optional, TypeVar

from .bus import MessageBus, _Subscription
from .models import Message


T = TypeVar("T")
AGENT_ERROR_METADATA_TYPE = "agent_processing_error"
AGENT_TASK_ERROR_METADATA_TYPE = "agent_task_error"


class Agent(ABC):
    """Agente supervisionado com subscription pronta antes de `start()` retornar."""

    def __init__(
        self,
        name: str,
        persona: str,
        bus: MessageBus,
        is_default_responder: bool = False,
    ) -> None:
        self.name = name
        self.persona = persona
        self.bus = bus
        self.is_default_responder = is_default_responder
        if is_default_responder:
            self.bus.default_responder = name
        self._task: Optional[asyncio.Task] = None
        self._subscription: _Subscription | None = None
        self._running = False
        self._stopping = False
        self._ready: asyncio.Event | None = None
        self._startup_error: BaseException | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._detached_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        created = False
        async with self._lifecycle_lock:
            if self._task and not self._task.done():
                if not self._running:
                    raise RuntimeError(
                        f"A task anterior de {self.name} ainda não terminou; restart recusado."
                    )
                ready = self._ready
            else:
                start_index = self.bus.last_index()
                self._running = True
                self._stopping = False
                self._startup_error = None
                self._ready = asyncio.Event()
                ready = self._ready
                self._task = asyncio.create_task(
                    self._run_loop(start_index),
                    name=f"agent:{self.name}",
                )
                self._task.add_done_callback(self._on_task_done)
                created = True
            task = self._task

        assert ready is not None
        await asyncio.shield(ready.wait())
        if self._startup_error is not None:
            raise RuntimeError(f"Falha ao iniciar {self.name}") from self._startup_error
        if task is not None and task.done() and not self._running:
            try:
                task_error = task.exception()
            except asyncio.CancelledError as exc:
                raise RuntimeError(f"{self.name} foi cancelado durante o startup") from exc
            if task_error is not None:
                raise RuntimeError(f"{self.name} encerrou durante o startup") from task_error

        if created:
            self.bus.publish(
                Message(
                    sender=self.name,
                    role="system",
                    content=f"{self.name} started. Persona: {self.persona}",
                    metadata={"type": "agent_lifecycle", "state": "started"},
                )
            )

    async def stop(self, timeout: float = 2.0) -> None:
        async with self._lifecycle_lock:
            self._stopping = True
            self._running = False
            task = self._task
            if task and not task.done():
                task.cancel()

        if task and not task.done():
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if task not in done:
                if self._subscription:
                    self._subscription.close_nowait()
                self.bus.turno.abandon_agent(self.name)
                self._publish_error(
                    AGENT_TASK_ERROR_METADATA_TYPE,
                    TimeoutError(f"Shutdown de {self.name} excedeu {timeout:g}s"),
                )
            else:
                try:
                    task.exception()
                except asyncio.CancelledError:
                    pass
        elif task:
            try:
                task.exception()
            except asyncio.CancelledError:
                pass

        for detached in tuple(self._detached_tasks):
            detached.cancel()

        self.bus.publish(
            Message(
                sender=self.name,
                role="system",
                content=f"{self.name} stopped.",
                metadata={"type": "agent_lifecycle", "state": "stopped"},
            )
        )
        self._stopping = False

    async def _run_loop(self, start_index: int) -> None:
        subscription: _Subscription | None = None
        try:
            subscription = self.bus.subscribe(
                start_index=start_index,
                subscriber_name=self.name,
                track_pending=True,
            )
            self._subscription = subscription
            assert self._ready is not None
            self._ready.set()

            async with subscription:
                async for msg in subscription:
                    if not self._running:
                        break
                    if msg.sender == self.name:
                        continue
                    try:
                        with self.bus.turno.activity(self.name):
                            await self.on_message(msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._publish_error(AGENT_ERROR_METADATA_TYPE, exc, source=msg)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._startup_error = exc if subscription is None else None
            raise
        finally:
            if subscription is not None:
                await subscription.aclose()
            self._subscription = None
            self._running = False
            self.bus.turno.abandon_agent(self.name)
            if self._ready is not None:
                self._ready.set()

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._running = False
        self.bus.turno.abandon_agent(self.name)
        if self._stopping:
            return
        if task.cancelled():
            self._publish_error(
                AGENT_TASK_ERROR_METADATA_TYPE,
                asyncio.CancelledError("Task cancelada inesperadamente"),
            )
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            # A exceção também é consumida aqui para não virar aviso tardio do loop.
            self._publish_error(AGENT_TASK_ERROR_METADATA_TYPE, exc)

    def _publish_error(
        self,
        metadata_type: str,
        error: BaseException,
        source: Message | None = None,
    ) -> None:
        self.bus.publish(
            Message(
                sender=self.name,
                role="system",
                content=f"{self.name}: {type(error).__name__}: {error}",
                turn_id=source.turn_id if source else str(uuid.uuid4()),
                mode=source.mode if source else "padrao",
                hop_count=(source.hop_count + 1) if source else 0,
                metadata={
                    "type": metadata_type,
                    "agent": self.name,
                    "error_type": type(error).__name__,
                },
            )
        )

    async def await_with_deadline(
        self,
        awaitable: Awaitable[T],
        timeout: float,
        *,
        label: str,
    ) -> T:
        """Deadline rígido para o caller, sem aguardar cancelamento cooperativo."""
        if self._detached_tasks:
            close = getattr(awaitable, "close", None)
            if close:
                close()
            raise RuntimeError(
                f"{self.name} ainda possui uma chamada anterior em cancelamento"
            )
        child = asyncio.create_task(awaitable, name=f"{self.name}:{label}")
        try:
            done, _ = await asyncio.wait({child}, timeout=timeout)
        except asyncio.CancelledError:
            child.cancel()
            self._observe_detached(child, label)
            raise

        if child in done:
            if child.cancelled():
                raise RuntimeError(f"{label} foi cancelado inesperadamente")
            return child.result()

        child.cancel()
        self._observe_detached(child, label)
        raise asyncio.TimeoutError(f"{label} excedeu {timeout:g}s")

    def _observe_detached(self, task: asyncio.Task, label: str) -> None:
        self._detached_tasks.add(task)

        def consume_result(done_task: asyncio.Task) -> None:
            self._detached_tasks.discard(done_task)
            if done_task.cancelled():
                return
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            if exc is not None and not self._stopping:
                self._publish_error(AGENT_TASK_ERROR_METADATA_TYPE, exc)

        task.add_done_callback(consume_result)

    @abstractmethod
    async def on_message(self, message: Message):
        raise NotImplementedError

    def publish(
        self,
        content: str,
        role: str = "assistant",
        metadata: dict | None = None,
        *,
        recipient: str | None = None,
        source: Message | None = None,
    ) -> None:
        self.bus.publish(
            Message(
                sender=self.name,
                role=role,
                content=content,
                metadata=metadata or {},
                recipient=recipient,
                turn_id=source.turn_id if source else str(uuid.uuid4()),
                mode=source.mode if source else "padrao",
                hop_count=(source.hop_count + 1) if source else 0,
            )
        )
