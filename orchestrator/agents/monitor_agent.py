"""Monitor de handoff com orçamento finito por rodada."""
from __future__ import annotations

from ..agent import Agent
from ..models import Message
from ..recovery import (
    RECOVERY_EXHAUSTED_METADATA_TYPE,
    RECOVERY_METADATA_TYPE,
    failed_agents_for_turn,
)
from ..router import MAX_TURN_HOPS, is_terminal_reply, policy_for_mode, resolve_recipient


MONITOR_SIGNAL_METADATA_TYPE = "monitor_signal"
MONITOR_EXHAUSTED_METADATA_TYPE = "monitor_handoff_exhausted"
MAX_MONITOR_SIGNALS_PER_TURN = 3


class MonitorAgent(Agent):
    """Vigia falas sem destinatário válido e faz handoff limitado."""

    DEBATEDORES = ("Qwen", "Groq", "Gemini")

    def __init__(self, name: str, bus):
        super().__init__(name=name, persona="", bus=bus)

    def _signals_for_turn(self, turn_id: str) -> list[Message]:
        return [
            message
            for message in self.bus.history()
            if message.turn_id == turn_id
            and (message.metadata or {}).get("type") == MONITOR_SIGNAL_METADATA_TYPE
        ]

    def _terminal_already_published(self, turn_id: str) -> bool:
        return any(
            message.turn_id == turn_id
            and (message.metadata or {}).get("type") == MONITOR_EXHAUSTED_METADATA_TYPE
            for message in self.bus.history()
        )

    def _next_healthy_target(self, message: Message, used_targets: set[str]) -> str | None:
        sender_norm = message.sender.casefold()
        failed = failed_agents_for_turn(self.bus, message.turn_id)
        available = {name.casefold() for name in self.bus.available_debaters()}
        names = list(self.DEBATEDORES)
        try:
            start = next(
                index for index, name in enumerate(names) if name.casefold() == sender_norm
            )
        except StopIteration:
            return None

        for offset in range(1, len(names) + 1):
            candidate = names[(start + offset) % len(names)]
            candidate_norm = candidate.casefold()
            if (
                candidate_norm != sender_norm
                and candidate_norm not in failed
                and candidate_norm not in used_targets
                and (not self.bus.has_subscribers() or candidate_norm in available)
            ):
                return candidate
        return None

    def _publish_exhausted(self, message: Message) -> None:
        if self._terminal_already_published(message.turn_id):
            return
        self.bus.publish(
            Message(
                sender="System",
                role="system",
                content=(
                    "[MONITOR ENCERRADO] O limite de handoffs automáticos desta "
                    "rodada foi atingido."
                ),
                turn_id=message.turn_id,
                mode=message.mode,
                hop_count=message.hop_count + 1,
                metadata={"type": MONITOR_EXHAUSTED_METADATA_TYPE},
            )
        )

    async def on_message(self, message: Message):
        metadata_type = (message.metadata or {}).get("type")
        if message.role == "system" or metadata_type in (
            MONITOR_SIGNAL_METADATA_TYPE,
            MONITOR_EXHAUSTED_METADATA_TYPE,
            RECOVERY_METADATA_TYPE,
            RECOVERY_EXHAUSTED_METADATA_TYPE,
            "agent_thinking",
            "agent_stream",
        ):
            return

        if message.sender not in self.DEBATEDORES:
            return
        if not policy_for_mode(message.mode).monitor_enabled or is_terminal_reply(message):
            return

        recipient = resolve_recipient(message, self.DEBATEDORES)
        if (
            recipient
            and recipient.casefold() != message.sender.casefold()
        ):
            # Um handoff explícito deve chegar ao destinatário mesmo que ele
            # tenha falhado antes nesta rodada. O circuit breaker continua
            # valendo apenas para escolhas automáticas do Monitor/recovery.
            return

        signals = self._signals_for_turn(message.turn_id)
        used_targets = {
            str(signal.recipient).casefold() for signal in signals if signal.recipient
        }
        target = self._next_healthy_target(message, used_targets)
        if (
            len(signals) >= MAX_MONITOR_SIGNALS_PER_TURN
            or message.hop_count >= MAX_TURN_HOPS
            or not target
        ):
            self._publish_exhausted(message)
            return

        self.bus.publish(
            Message(
                sender=message.sender,
                role="assistant",
                content=(
                    f"{message.content}\n\n"
                    "[INTERNO-MONITOR: a fala acima não possui um handoff válido. "
                    f"{target}, assuma esta rodada.]"
                ),
                recipient=target,
                turn_id=message.turn_id,
                mode=message.mode,
                hop_count=message.hop_count + 1,
                metadata={
                    "type": MONITOR_SIGNAL_METADATA_TYPE,
                    "target": target,
                    "monitor_hop": len(signals) + 1,
                },
            )
        )
