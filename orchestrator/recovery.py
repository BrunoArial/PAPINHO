"""Circuit breaker e handoff estruturado para falhas de agentes."""
from __future__ import annotations

import uuid

from .bus import MessageBus
from .models import Message


RECOVERY_METADATA_TYPE = "agent_recovery_request"
RECOVERY_EXHAUSTED_METADATA_TYPE = "agent_recovery_exhausted"
MAX_RECOVERY_HOPS = 2

_RECOVERY_TARGETS = {
    "qwen": "Gemini",
    "groq": "Gemini",
    "gemini": "Qwen",
}


def failed_agents_for_turn(bus: MessageBus, turn_id: str) -> set[str]:
    failed: set[str] = set()
    for message in bus.history():
        if message.turn_id != turn_id:
            continue
        metadata = message.metadata or {}
        if metadata.get("type") not in (
            RECOVERY_METADATA_TYPE,
            RECOVERY_EXHAUSTED_METADATA_TYPE,
        ):
            continue
        failed.update(str(agent).casefold() for agent in metadata.get("recovery_chain", []))
        if metadata.get("failed_agent"):
            failed.add(str(metadata["failed_agent"]).casefold())
    return failed


def _has_terminal_event(bus: MessageBus, turn_id: str) -> bool:
    return any(
        message.turn_id == turn_id
        and (message.metadata or {}).get("type") == RECOVERY_EXHAUSTED_METADATA_TYPE
        for message in bus.history()
    )


def _infer_source_message(bus: MessageBus, failed_agent_name: str) -> Message | None:
    """Mantém lineage para callers legados que ainda não passam a mensagem causa."""
    failed_norm = failed_agent_name.casefold()
    for message in reversed(bus.history()):
        if message.recipient and message.recipient.casefold() == failed_norm:
            return message
    return None


def _publish_exhausted(
    bus: MessageBus,
    failed_agent_name: str,
    error: BaseException,
    *,
    turn_id: str,
    mode: str,
    hop_count: int,
    chain: list[str],
) -> None:
    if _has_terminal_event(bus, turn_id):
        return
    bus.publish(
        Message(
            sender="System",
            role="system",
            content=(
                "[RECUPERAÇÃO ENCERRADA] Não há outro agente saudável disponível "
                f"após a falha de {failed_agent_name} ({type(error).__name__}: {error})."
            ),
            recipient=None,
            turn_id=turn_id,
            mode=mode,
            hop_count=hop_count,
            metadata={
                "type": RECOVERY_EXHAUSTED_METADATA_TYPE,
                "failed_agent": failed_agent_name,
                "error_type": type(error).__name__,
                "recovery_chain": chain,
            },
        )
    )


def publish_recovery(
    bus: MessageBus,
    failed_agent_name: str,
    error: BaseException,
    source_message: Message | None = None,
) -> bool:
    """Publica no máximo dois fallbacks distintos por rodada."""
    if source_message is None:
        source_message = _infer_source_message(bus, failed_agent_name)
    turn_id = source_message.turn_id if source_message else str(uuid.uuid4())
    mode = source_message.mode if source_message else "padrao"
    hop_count = (source_message.hop_count + 1) if source_message else 1
    source_metadata = (source_message.metadata or {}) if source_message else {}
    chain = [str(agent) for agent in source_metadata.get("recovery_chain", [])]
    failed_norm = failed_agent_name.casefold()

    already_failed = failed_agents_for_turn(bus, turn_id)
    if _has_terminal_event(bus, turn_id):
        return False

    if failed_norm in already_failed:
        # Uma chamada explícita pode tentar novamente um agente cujo circuito
        # automático já foi aberto. Se a nova tentativa também falhar, não
        # engolimos o erro: encerramos a recuperação com um evento visível.
        existing_chain = [
            *chain,
            *(
                agent for agent in already_failed
                if agent not in {item.casefold() for item in chain}
            ),
        ]
        _publish_exhausted(
            bus,
            failed_agent_name,
            error,
            turn_id=turn_id,
            mode=mode,
            hop_count=hop_count,
            chain=existing_chain,
        )
        return False

    if failed_norm not in {agent.casefold() for agent in chain}:
        chain.append(failed_agent_name)

    recovery_target = _RECOVERY_TARGETS.get(failed_norm)
    blocked_targets = {agent.casefold() for agent in chain} | already_failed
    available = bus.available_debaters()
    if available:
        ordered_candidates = [
            candidate
            for candidate in (recovery_target, *available)
            if candidate
        ]
        recovery_target = next(
            (
                candidate for candidate in ordered_candidates
                if candidate.casefold() not in blocked_targets
            ),
            None,
        )
    elif bus.has_subscribers():
        recovery_target = None
    target_failed = bool(
        recovery_target
        and recovery_target.casefold()
        in blocked_targets
    )
    exhausted = (
        not recovery_target
        or len(chain) > MAX_RECOVERY_HOPS
        or target_failed
    )
    if exhausted:
        _publish_exhausted(
            bus,
            failed_agent_name,
            error,
            turn_id=turn_id,
            mode=mode,
            hop_count=hop_count,
            chain=chain,
        )
        return False

    bus.publish(
        Message(
            sender="System",
            role="assistant",
            content=(
                f"[RECUPERAÇÃO] O agente {failed_agent_name} ficou indisponível "
                f"({type(error).__name__}: {str(error) or '(sem mensagem)'}). "
                f"Continue o debate sem ele. {recovery_target}, assuma o próximo turno."
            ),
            recipient=recovery_target,
            turn_id=turn_id,
            mode=mode,
            hop_count=hop_count,
            metadata={
                "type": RECOVERY_METADATA_TYPE,
                "failed_agent": failed_agent_name,
                "error_type": type(error).__name__,
                "recovery_target": recovery_target,
                "recovery_hop": len(chain),
                "recovery_chain": chain,
            },
        )
    )
    return True
