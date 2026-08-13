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
      - role: papel da mensagem ('user', 'assistant', 'system', ...)
      - content: texto da mensagem
      - timestamp: instante de criação (UTC, tz-aware)
      - metadata: dicionário para dados adicionais (por ex. confidence, tokens)
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = "system"
    role: str = "user"
    content: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
