"""Barramento em memória com entrega ordenada e estado explícito de rodada.

O histórico é a fonte canônica. Cada subscriber possui uma fila de tamanho
um usada somente como wake-up; notificações podem ser coalescidas sem perder
mensagens, pois o cursor sempre relê o histórico.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, List

from .models import Message
from .router import (
    DEFAULT_DEBATEDORES,
    MAX_TURN_HOPS,
    ROUTING_UNAVAILABLE_METADATA_TYPE,
    policy_for_mode,
    requests_terminal_reply,
    route_message,
)


_NON_CONTRIBUTION_TYPES = {
    "agent_recovery_request",
    "agent_recovery_exhausted",
    "monitor_signal",
    "monitor_handoff_exhausted",
    "routing_unavailable",
    "agent_error",
    "agent_task_error",
}
_FINAL_MARKER_REPLACEMENTS = (
    (re.compile(r"\[SOLUÇÃO FINAL\]", flags=re.IGNORECASE), "[SÍNTESE PROVISÓRIA]"),
    (re.compile(r"ENCERRA O CICLO", flags=re.IGNORECASE), "CONTINUA O CICLO"),
)


@dataclass
class TurnState:
    """Contabiliza processamento e entregas pendentes da rodada."""

    active_agents: set[str] = field(default_factory=set)
    pending_deliveries: int = 0
    last_speaker: str = ""
    last_message_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quiescent_event: asyncio.Event = field(default_factory=asyncio.Event)
    silence_window_ms: int = 1500
    _active_counts: Counter[str] = field(default_factory=Counter, init=False)
    _silence_task: asyncio.Task | None = field(default=None, init=False)
    _generation: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.quiescent_event.set()

    def _changed(self) -> None:
        self._generation += 1
        self._cancel_silence_task()

    def mark_active(self, agent_name: str) -> None:
        """Marca atividade de forma reentrante e com rollback em falha parcial."""
        self._active_counts[agent_name] += 1
        self.active_agents.add(agent_name)
        try:
            self.quiescent_event.clear()
            self._changed()
        except BaseException:
            self._active_counts[agent_name] -= 1
            if self._active_counts[agent_name] <= 0:
                self._active_counts.pop(agent_name, None)
                self.active_agents.discard(agent_name)
            raise

    def mark_idle(self, agent_name: str) -> None:
        count = self._active_counts.get(agent_name, 0)
        if count <= 1:
            self._active_counts.pop(agent_name, None)
            self.active_agents.discard(agent_name)
        else:
            self._active_counts[agent_name] = count - 1
        self._changed()
        self._schedule_if_idle()

    @contextmanager
    def activity(self, agent_name: str):
        self.mark_active(agent_name)
        try:
            yield
        finally:
            self.mark_idle(agent_name)

    def add_pending(self, count: int = 1) -> None:
        if count <= 0:
            return
        self.pending_deliveries += count
        self.quiescent_event.clear()
        self._changed()

    def delivery_done(self, count: int = 1) -> None:
        if count <= 0:
            return
        self.pending_deliveries = max(0, self.pending_deliveries - count)
        self._changed()
        self._schedule_if_idle()

    def drop_pending(self, count: int) -> None:
        self.delivery_done(count)

    def record_publication(self, message: Message, pending_delta: int = 0) -> None:
        self.last_speaker = message.sender
        self.last_message_ts = datetime.now(timezone.utc)
        self.quiescent_event.clear()
        self._changed()
        if pending_delta:
            self.pending_deliveries += pending_delta
        self._schedule_if_idle()

    def abandon_agent(self, agent_name: str) -> None:
        self._active_counts.pop(agent_name, None)
        self.active_agents.discard(agent_name)
        self._changed()
        self._schedule_if_idle()

    def _cancel_silence_task(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = None

    def _schedule_if_idle(self) -> None:
        if self.active_agents or self.pending_deliveries:
            return
        self._cancel_silence_task()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.quiescent_event.set()
            return
        generation = self._generation
        self._silence_task = loop.create_task(self._silence_timer(generation))

    async def _silence_timer(self, generation: int) -> None:
        try:
            await asyncio.sleep(self.silence_window_ms / 1000)
            if (
                generation == self._generation
                and not self.active_agents
                and self.pending_deliveries == 0
            ):
                self.quiescent_event.set()
        except asyncio.CancelledError:
            pass

    def force_quiescence(self) -> bool:
        """Só confirma quiescência quando não existe trabalho real."""
        if self.active_agents or self.pending_deliveries:
            return False
        self._cancel_silence_task()
        self.quiescent_event.set()
        return True


class _Subscription(AsyncIterator[Message]):
    """Cursor registrado de forma síncrona; cleanup não depende do GC."""

    def __init__(
        self,
        bus: "MessageBus",
        start_index: int,
        subscriber_name: str | None,
        track_pending: bool,
    ) -> None:
        self._bus = bus
        self._cursor = max(0, min(start_index, bus.last_index()))
        self.subscriber_name = subscriber_name
        self.track_pending = track_pending
        self._wake_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._closed = False
        self.notification_coalesces = 0
        bus._subscribers.append(self)

        backlog = bus.last_index() - self._cursor
        if track_pending and backlog:
            bus.turno.add_pending(backlog)

    def __aiter__(self) -> "_Subscription":
        return self

    async def __anext__(self) -> Message:
        while not self._closed:
            if self._cursor < self._bus.last_index():
                message = self._bus._messages[self._cursor]
                self._cursor += 1
                if self.track_pending:
                    self._bus.turno.delivery_done()
                return message

            try:
                await self._wake_queue.get()
            except asyncio.CancelledError:
                self.close_nowait()
                raise
        raise StopAsyncIteration

    def notify(self) -> None:
        if self._closed:
            return
        try:
            self._wake_queue.put_nowait(None)
        except asyncio.QueueFull:
            # O histórico preserva as mensagens; apenas o wake-up é coalescido.
            self.notification_coalesces += 1
            self._bus.notification_coalesces += 1

    def close_nowait(self) -> None:
        if self._closed:
            return
        self._closed = True
        outstanding = max(0, self._bus.last_index() - self._cursor)
        if self.track_pending and outstanding:
            self._bus.turno.drop_pending(outstanding)
        try:
            self._bus._subscribers.remove(self)
        except ValueError:
            pass

    async def aclose(self) -> None:
        self.close_nowait()

    async def __aenter__(self) -> "_Subscription":
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.aclose()


class MessageBus:
    """MessageBus confinado a um único event loop."""

    def __init__(
        self,
        silence_window_ms: int = 1500,
        debatedores: Iterable[str] = DEFAULT_DEBATEDORES,
        default_responder: str | None = "Qwen",
    ) -> None:
        self._messages: List[Message] = []
        self._subscribers: list[_Subscription] = []
        self.turno = TurnState(silence_window_ms=silence_window_ms)
        self.debatedores = tuple(debatedores)
        self.default_responder = default_responder
        self.notification_coalesces = 0

    def configure_routing(
        self,
        debatedores: Iterable[str],
        default_responder: str | None,
    ) -> None:
        self.debatedores = tuple(debatedores)
        self.default_responder = default_responder

    def available_debaters(self) -> tuple[str, ...]:
        subscribed = {
            (subscriber.subscriber_name or "").casefold()
            for subscriber in self._subscribers
            if subscriber.track_pending and subscriber.subscriber_name
        }
        return tuple(
            debatedor for debatedor in self.debatedores
            if debatedor.casefold() in subscribed
        )

    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def _contributors_for_turn(self, turn_id: str) -> set[str]:
        return {
            message.sender.casefold()
            for message in self._messages
            if message.turn_id == turn_id
            and message.role == "assistant"
            and message.sender.casefold()
            in {name.casefold() for name in self.debatedores}
            and bool((message.content or "").strip())
            and (message.metadata or {}).get("type") not in _NON_CONTRIBUTION_TYPES
        }

    def _apply_finalization_policy(self, msg: Message) -> None:
        """Só aceita conclusão após os revisores exigidos contribuírem."""
        if msg.role != "assistant" or not requests_terminal_reply(msg):
            return

        # O limite global continua sendo uma válvula de segurança absoluta.
        if msg.hop_count >= MAX_TURN_HOPS or msg.mode == "/curto":
            msg.metadata = {**(msg.metadata or {}), "terminal": True}
            msg.recipient = None
            return

        policy = policy_for_mode(msg.mode)
        sender = msg.sender.casefold()
        allowed = {
            agent.casefold() for agent in policy.terminal_responders
        }
        authorized = not allowed or sender in allowed

        contributors = self._contributors_for_turn(msg.turn_id)
        if sender in {name.casefold() for name in self.debatedores}:
            contributors.add(sender)
        missing = [
            agent
            for agent in policy.required_final_contributors
            if agent.casefold() not in contributors
        ]

        if authorized and not missing:
            msg.metadata = {
                **(msg.metadata or {}),
                "terminal": True,
                "terminal_authorized": True,
            }
            msg.recipient = None
            return

        target = missing[0] if missing else next(
            (
                agent for agent in policy.terminal_responders
                if agent.casefold() != sender
            ),
            None,
        )
        if not target:
            # Sem um finalizador alternativo, não há caminho seguro para
            # prolongar a rodada. O limite estrutural prevalece.
            msg.metadata = {**(msg.metadata or {}), "terminal": True}
            msg.recipient = None
            return
        content = msg.content
        for marker, replacement in _FINAL_MARKER_REPLACEMENTS:
            content = marker.sub(replacement, content)
        msg.content = content
        msg.recipient = target
        msg.metadata = {
            **(msg.metadata or {}),
            "terminal": False,
            "terminal_deferred": True,
            "terminal_deferred_reason": (
                "missing_contributors" if missing else "unauthorized_finalizer"
            ),
            "missing_contributors": missing,
        }

    def publish(self, msg: Message) -> Message:
        self._apply_finalization_policy(msg)
        configured_default = self.default_responder if msg.mode == "padrao" else None
        route_message(msg, self.debatedores, configured_default)

        available = self.available_debaters()
        if (
            msg.recipient
            and self._subscribers
            and msg.recipient.casefold()
            in {name.casefold() for name in self.debatedores}
            and msg.recipient.casefold() not in {name.casefold() for name in available}
        ):
            original_recipient = msg.recipient
            fallback = next(
                (
                    name for name in available
                    if name.casefold() != msg.sender.casefold()
                ),
                None,
            )
            msg.recipient = fallback
            msg.metadata = {
                **(msg.metadata or {}),
                "routing_status": "failover" if fallback else "unavailable",
                "original_recipient": original_recipient,
            }
        self._messages.append(msg)

        subscribers = tuple(self._subscribers)
        tracked = sum(1 for subscriber in subscribers if subscriber.track_pending)
        self.turno.record_publication(msg, pending_delta=tracked)
        for subscriber in subscribers:
            subscriber.notify()

        if (msg.metadata or {}).get("routing_status") == "unavailable":
            self.publish(
                Message(
                    sender="System",
                    role="system",
                    content=(
                        f"[ROTEAMENTO] Nenhum agente ativo pôde receber a mensagem "
                        f"destinada a {msg.metadata.get('original_recipient')}."
                    ),
                    turn_id=msg.turn_id,
                    mode=msg.mode,
                    hop_count=msg.hop_count + 1,
                    metadata={"type": ROUTING_UNAVAILABLE_METADATA_TYPE},
                )
            )
        return msg

    def history(self, since_index: int = 0) -> List[Message]:
        return list(self._messages[since_index:])

    def last_index(self) -> int:
        return len(self._messages)

    def subscribe(
        self,
        start_index: int = 0,
        *,
        subscriber_name: str | None = None,
        track_pending: bool = False,
    ) -> _Subscription:
        return _Subscription(self, start_index, subscriber_name, track_pending)
