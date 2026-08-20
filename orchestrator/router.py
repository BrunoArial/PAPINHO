"""
orchestrator/router.py

Roteamento por menção de debatedor baseado em tokenização por word boundaries.

Substitui a lógica divergente que existia em LLMAgent (rfind sobre texto limpo de pontuação)
e GeminiAgent (substring com aliases hifenizados) por uma única função pura, testável e
determinística.

Regras:
- Tokeniza o conteúdo em palavras (regex \\b\\w+\\b), preservando ordem.
- Normaliza tokens: minúsculas + remoção de hífens/underscores + colapso de whitespace.
- Retorna True se o nome do agente casa com pelo menos um token E foi o ÚLTIMO debatedor
  citado na mensagem. Isso resolve "Qwen e Gemini" -> só Gemini age.
- Ignora tokens menores que 3 caracteres para evitar falsos positivos (ex.: "eu" -> e).

Função adicional `extract_addressed_agents` retorna o conjunto de debatedores citados,
útil para o MonitorAgent detectar falas órfãs.
"""
import re
from dataclasses import dataclass
from typing import Iterable, List, Set

from .models import Message


_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:[-_][A-Za-zÀ-ÖØ-öø-ÿ0-9]+)*", flags=re.UNICODE)

# Lista canônica dos debatedores ativos; a comparação continua normalizada.
DEFAULT_DEBATEDORES: tuple[str, ...] = ("Qwen", "Groq", "Gemini")
ROUTING_RESOLVED_METADATA_KEY = "_routing_resolved"
ROUTING_UNAVAILABLE_METADATA_TYPE = "routing_unavailable"
MAX_TURN_HOPS = 12
_TERMINAL_MARKERS = ("[SOLUÇÃO FINAL]", "ENCERRA O CICLO")


@dataclass(frozen=True)
class RoutingPolicy:
    default_responder: str
    monitor_enabled: bool = True
    # Agentes autorizados a encerrar a rodada quando houver um marcador
    # terminal explícito. Isto não torna toda fala desses agentes terminal.
    terminal_responders: tuple[str, ...] = ()
    # Contribuições que precisam existir antes de uma conclusão definitiva.
    required_final_contributors: tuple[str, ...] = ()
    # Modos deliberadamente de uma única fala (síntese/decisão).
    auto_finalize_responders: tuple[str, ...] = ()


MODE_POLICIES: dict[str, RoutingPolicy] = {
    "padrao": RoutingPolicy(
        "Qwen",
        terminal_responders=("Gemini",),
        required_final_contributors=("Qwen", "Groq"),
    ),
    "/curto": RoutingPolicy("Qwen", monitor_enabled=False),
    "/livre": RoutingPolicy("Qwen", monitor_enabled=False),
    "/debate": RoutingPolicy(
        "Qwen",
        terminal_responders=("Gemini",),
        required_final_contributors=("Qwen", "Groq"),
    ),
    "/brainstorm": RoutingPolicy(
        "Qwen",
        terminal_responders=("Gemini",),
        required_final_contributors=("Qwen", "Groq"),
    ),
    "/crashtest": RoutingPolicy("Groq"),
    "/sintese": RoutingPolicy(
        "Gemini",
        terminal_responders=("Gemini",),
        auto_finalize_responders=("Gemini",),
    ),
    "/codigo": RoutingPolicy("Gemini"),
    "/explica": RoutingPolicy("Gemini"),
    "/decide": RoutingPolicy(
        "Gemini",
        terminal_responders=("Gemini",),
        auto_finalize_responders=("Gemini",),
    ),
    "/revisa": RoutingPolicy("Groq"),
}


def policy_for_mode(mode: str | None) -> RoutingPolicy:
    return MODE_POLICIES.get(mode or "padrao", MODE_POLICIES["padrao"])


def requests_terminal_reply(message: Message) -> bool:
    """Indica intenção terminal explícita, sem consultar o histórico da rodada."""
    metadata_type = (message.metadata or {}).get("type")
    if metadata_type in ("agent_recovery_request", "monitor_signal"):
        return False
    if (message.metadata or {}).get("terminal_deferred"):
        return False
    content_normalized = (message.content or "").casefold()
    if (message.metadata or {}).get("terminal") or any(
        marker.casefold() in content_normalized for marker in _TERMINAL_MARKERS
    ):
        return True
    if message.hop_count >= MAX_TURN_HOPS:
        return True
    policy = policy_for_mode(message.mode)
    if message.mode == "/curto" and message.role == "assistant":
        return True
    return any(
        _normalize(message.sender) == _normalize(agent)
        for agent in policy.auto_finalize_responders
    )


def is_terminal_reply(message: Message) -> bool:
    """Valida intenção terminal e a autoridade do remetente para encerrar."""
    if not requests_terminal_reply(message):
        return False
    if message.hop_count >= MAX_TURN_HOPS or message.mode == "/curto":
        return True

    policy = policy_for_mode(message.mode)
    if not policy.terminal_responders:
        return True
    sender = _normalize(message.sender)
    return any(sender == _normalize(agent) for agent in policy.terminal_responders)

# Tokens com menos de 3 caracteres são ignorados para reduzir ruído
# (ex.: "eu" -> e, "ia" -> ia não conta como debatedor).
_MIN_TOKEN_LEN = 3

# Sequências de letras curtas separadas por hífen/underscore (ex.: "g-e-m-i-n-i")
# precisam ser colapsadas em uma única palavra ANTES da tokenização por word
# boundary — caso contrário viram tokens de 1 caractere cada.
_HYPHEN_LETTERS_RE = re.compile(r"(?:[A-Za-zÀ-ÖØ-öø-ÿ][-_]){2,}[A-Za-zÀ-ÖØ-öø-ÿ]")


def _precollapse_hyphenated(content: str) -> str:
    """Colapsa padrões 'g-e-m-i-n-i' em 'gemini' antes da tokenização."""
    if not content:
        return content
    return _HYPHEN_LETTERS_RE.sub(lambda m: m.group(0).replace("-", "").replace("_", ""), content)


def _normalize(token: str) -> str:
    """Minúsculas + remoção de separadores comuns. 'Gemini' -> 'gemini', 'g-e-m-i-n-i' -> 'gemini'."""
    return re.sub(r"[-_]+", "", token.lower()).strip()


def tokenize(content: str) -> List[str]:
    """Tokeniza o conteúdo em palavras normalizadas."""
    if not content:
        return []
    pre = _precollapse_hyphenated(content)
    return [_normalize(m.group(0)) for m in _TOKEN_RE.finditer(pre)]


def filter_debatedores(tokens: Iterable[str], debatedores: Iterable[str] = DEFAULT_DEBATEDORES) -> List[str]:
    """Mantém apenas tokens que casam (normalizado) com algum debatedor."""
    alvo = {_normalize(d) for d in debatedores}
    return [t for t in tokens if t in alvo and len(t) >= _MIN_TOKEN_LEN]


def extract_addressed_agents(content: str, debatedores: Iterable[str] = DEFAULT_DEBATEDORES) -> Set[str]:
    """Retorna o conjunto (preservando capitalização original) de debatedores citados em `content`."""
    alvo_map = {_normalize(d): d for d in debatedores}
    encontrados: Set[str] = set()
    for t in tokenize(content):
        if t in alvo_map:
            encontrados.add(alvo_map[t])
    return encontrados


def last_addressed_agent(content: str, debatedores: Iterable[str] = DEFAULT_DEBATEDORES) -> str | None:
    """Retorna o último debatedor citado em `content` (na capitalização original do catálogo)."""
    alvo_map = {_normalize(d): d for d in debatedores}
    ultimo: str | None = None
    for t in tokenize(content):
        if t in alvo_map:
            ultimo = alvo_map[t]
    return ultimo


def _canonical_agent_name(agent_name: str | None, debatedores: Iterable[str]) -> str | None:
    """Retorna a grafia canônica do catálogo sem invalidar alvos estruturados."""
    if not agent_name:
        return None
    alvo_norm = _normalize(agent_name)
    for debatedor in debatedores:
        if _normalize(debatedor) == alvo_norm:
            return debatedor
    return agent_name


def resolve_recipient(
    message: Message,
    debatedores: Iterable[str] = DEFAULT_DEBATEDORES,
    default_responder: str | None = None,
) -> str | None:
    """
    Resolve um único destinatário usando, em ordem: campo estruturado,
    metadata estruturada, última menção textual e default para mensagens do
    usuário. Uma fala de agente nunca é roteada de volta ao próprio sender.
    """
    if not message:
        return None

    debatedores_list = list(debatedores)
    metadata = message.metadata or {}

    if metadata.get(ROUTING_RESOLVED_METADATA_KEY):
        return _canonical_agent_name(message.recipient, debatedores_list)

    if message.role == "assistant" and is_terminal_reply(message):
        return None

    recipient = message.recipient
    if not recipient:
        recipient = metadata.get("target") or metadata.get("recovery_target")
    if not recipient and message.role != "system":
        recipient = last_addressed_agent(message.content, debatedores_list)
    if not recipient and (message.role == "user" or message.sender.casefold() == "user"):
        recipient = default_responder or policy_for_mode(message.mode).default_responder

    recipient = _canonical_agent_name(recipient, debatedores_list)
    if (
        recipient
        and message.role != "user"
        and _normalize(recipient) == _normalize(message.sender)
    ):
        return None
    return recipient


def route_message(
    message: Message,
    debatedores: Iterable[str] = DEFAULT_DEBATEDORES,
    default_responder: str | None = None,
) -> Message:
    """Resolve e grava a rota uma única vez no envelope da mensagem."""
    message.recipient = resolve_recipient(message, debatedores, default_responder)
    message.metadata = {
        **(message.metadata or {}),
        ROUTING_RESOLVED_METADATA_KEY: True,
    }
    return message


def is_addressed(message: Message, agent_name: str, debatedores: Iterable[str] = DEFAULT_DEBATEDORES) -> bool:
    """
    Decide se `message` é endereçada a `agent_name`.

    Critério:
      1. Pelo menos um token do conteúdo casa (normalizado) com `agent_name`.
      2. O ÚLTIMO debatedor citado no conteúdo é `agent_name`.

    Casos:
      - "Qwen, ..."               -> True para Qwen, False para outros.
      - "Qwen e Gemini, ..."      -> False para Qwen, True para Gemini (último).
      - "Qwen! ..."               -> True para Qwen (tokenização por boundary lida com pontuação).
      - "g-e-m-i-n-i, ..."        -> True para Gemini (normalização remove hífens).
      - "Você disse para Qwen..." -> True para Qwen.
    """
    if not message:
        return False

    if message.recipient:
        return _normalize(message.recipient) == _normalize(agent_name)

    if (message.metadata or {}).get(ROUTING_RESOLVED_METADATA_KEY):
        return False

    if not message.content:
        return False

    alvo_norm = _normalize(agent_name)
    debatedores_list = list(debatedores)
    debatedores_norm = {_normalize(d) for d in debatedores_list}

    # Tokeniza uma vez só.
    tokens = tokenize(message.content)

    # O nome do agente precisa aparecer como token.
    found_self = any(t == alvo_norm and len(t) >= _MIN_TOKEN_LEN for t in tokens)
    if not found_self:
        return False

    # Encontra o último debatedor citado na mensagem.
    ultimo = None
    for t in tokens:
        if t in debatedores_norm and len(t) >= _MIN_TOKEN_LEN:
            ultimo = t
    return ultimo == alvo_norm
