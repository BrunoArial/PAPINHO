from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
import uuid


@dataclass
class Message:
    """
    Representa uma mensagem no sistema de conversação.
    Campos:
      - id: identificador único da mensagem
      - sender: nome do agente ou 'system' ou usuário
      - recipient: destinatário resolvido pelo roteador (None quando a fala
        ainda não possui um handoff válido)
      - role: papel da mensagem ('user', 'assistant', 'system', ...)
      - content: texto visível da mensagem (após remoção de <think>...</think>)
      - thinking: raciocínio interno do modelo (bloco <think>), preservado
        para que outros agentes possam ver o processo do colega.
      - timestamp: instante de criação (UTC, tz-aware)
      - metadata: dicionário para dados adicionais (por ex. confidence, tokens,
        type=monitor_signal/agent_recovery_request/agent_thinking/agent_stream)
      - turn_id/hop_count: correlação e orçamento do encadeamento da rodada
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = "system"
    role: str = "user"
    content: str = ""
    thinking: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    recipient: str | None = None
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mode: str = "padrao"
    hop_count: int = 0
