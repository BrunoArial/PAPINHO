# PAPINHO — Orquestrador Multi-Agente de IA

**PAPINHO** é uma arquitetura em Python que coloca vários agentes LLM para conversar entre si em uma "mesa redonda" assíncrona. Você digita uma ideia ou um problema técnico no terminal e três personas — **Qwen (Explorador)**, **Gpt (Auditor)** e **Gemini (Estrategista)** — debatem até chegar a uma solução estruturada. Toda a transcrição fica gravada em `minhas_ideias.txt`.

## Visão geral

- **Barramento pub/sub em memória** (`MessageBus`): única fonte de verdade para as mensagens.
- **Agentes como `asyncio.Task`s em background**: o debate roda mesmo enquanto o terminal espera o próximo `input()`.
- **Bastão de fala** (`asyncio.Lock`): garante que apenas um agente fale por vez — sem respostas atropeladas.
- **Modo padrão (Força-Tarefa)**: a mesa roda livremente e só o Estrategista encerra, marcando `[SOLUÇÃO FINAL]`.
- **Modos alternáveis**: `/crashtest`, `/sintese`, `/debate`, `/livre`, `/curto`, `/codigo`, `/explica`, `/revisa`, `/brainstorm`, `/decide` — cada um com diretriz própria.
- **Pensamento interno visível opcional**: comando `/pensamento` mostra (ou oculta) os blocos `<think>…</think>` que os modelos produzem.
- **Monitor silencioso**: `MonitorAgent` detecta falas órfãs (agente que não citou nenhum colega) e injeta um sinal interno para a mesa continuar.
- **Interface CLI Premium**: Utiliza a biblioteca `rich` para renderização de painéis coloridos, spinners de carregamento assíncrono e formatação Markdown nativa no terminal.

## Instalação

```bash
pip install -r requirements.txt
```

Dependências: `openai`, `python-dotenv`, `google-genai`, `rich`.

## Configuração

Copie `.env.example` para `.env` e preencha:

```
GROQ_API_KEY="sua_chave_da_groq"
GEMINI_API_KEY="sua_chave_do_google_ai_studio"
```

As duas chaves são **obrigatórias** para o chat real.

## Como rodar

**Chat interativo (entrypoint principal):**
```bash
python chat_interativo.py
```

No Windows há um launcher que aponta para o CPython instalado via `uv`:
```bat
run_demo.cmd
```

**Demo sem APIs (smoke test do plumbing):**
```bash
python run_demo.py
```
Sobe `RuleBasedAgent`, `EchoAgent` e `LLMAgent` (sem chamadas reais de LLM), publica três mensagens de usuários fictícios e imprime o histórico final.

## Comandos do chat

| Comando | O que faz |
|---|---|
| `/ajuda` ou `/help` | Mostra o painel com todos os modos e comandos |
| `/pensamento` | Liga/desliga a exibição do pensamento interno (`<think>…</think>`) |
| `/crashtest [tema]` | Modo CRASH TEST: encontrar falhas; Auditor com peso duplo |
| `/sintese [tema]` | Modo SÍNTESE: Estrategista lidera e encerra rápido |
| `/debate [tema]` | Debate controlado de uma volta |
| `/livre [tema]` | Exploração solta, sem formato forçado |
| `/curto [tema]` | Resposta em 2-3 frases; só um agente |
| `/codigo [tema]` | Foco em arquitetura de código |
| `/explica [tema]` | Modo pedagógico, passo a passo |
| `/revisa [tema]` | Revisão de texto (Auditor lidera) |
| `/brainstorm [tema]` | Ideação pura, crítica proibida |
| `/decide [tema]` | Apoio à decisão com critérios e tradeoffs |
| `sair` / `exit` / `quit` | Encerra a malha de agentes |
| *(sem barra)* | Entra no modo padrão (Força-Tarefa) |

## Arquitetura

```
┌──────────┐      publica       ┌─────────────┐
│   User   │ ─────────────────▶ │ MessageBus  │
└──────────�                    │ (pub/sub +  │
                                │  history)   │
                                └──────┬──────┘
                                       │ subscribe()
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
   ┌─────────┐                   ┌─────────┐                    ┌─────────┐
   │  Qwen   │ ◀── menciona ──── │   Gpt   │ ◀── menciona ──── │ Gemini  │
   │Groq LLM │                   │Groq LLM │                   │ Google  │
   └────┬────┘                   └────┬────�                   └────┬────┘
        │ publica                    │ publica                    │ publica
        └────────────────────────────┴────────────────────────────┘
                                       │
                              ┌────────┴────────┐
                              ▼                 ▼
                        ┌──────────┐     ┌──────────────┐
                        │  Logger  │     │   Monitor    │
                        │ → .txt   │     │ (anti-órfão) │
                        └──────────┘     └──────────────�
```

### Fluxo de uma mensagem

1. O usuário digita uma linha em `loop_conversa()`.
2. Se houver `/modo` no início, a **DIRETRIZ DE MODO** correspondente (`MODOS_DE_CONVERSA`) é anexada invisivelmente ao payload.
3. Se nenhum nome de debatedor for citado, `Qwen` é prefixado automaticamente para iniciar a fila.
4. A mensagem é publicada como `Message(role="user", sender="User")`.
5. Cada `LLMAgent` / `GeminiAgent` só responde quando seu nome é o **último** mencionado no texto (roteamento por `rfind` em `LLMAgent`, substring-match em `GeminiAgent`).
6. O **bastão** (`bus.bastao`, `asyncio.Lock`) garante que apenas um agente fale por vez. Quando pega o bastão, o agente confirma que a mensagem que o acordou ainda é a última do histórico — caso contrário, o assunto já andou e o agente desiste.
7. Cada agente, ao publicar, cita o nome de UM colega para passar a palavra. A conversa fecha quando o Gemini publica `[SOLUÇÃO FINAL]` (ou quando o marcador explícito do modo aparece).
8. O `MonitorAgent` vigia falas órfãs (sem citação de colega) e republica com um sinal interno pedindo continuação.
9. `LoggerAgent` grava toda a transcrição em `minhas_ideias.txt`.

### Agentes

| Agente | Modelo | Papel |
|---|---|---|
| `Qwen` (`LLMAgent`) | `qwen/qwen3.6-27b` via Groq | Explorador — abre caminhos, marca `[verificar: X]` |
| `Gpt` (`LLMAgent`) | `openai/gpt-oss-120b` via Groq | Auditor — classifica riscos em `[fatal \| recuperável \| cosmético]` |
| `Gemini` (`GeminiAgent`) | `gemini-3.1-flash-lite` via Google AI Studio | Estrategista — plano final em `## Resumo / ## Passos / ## Premissas / ## O que NÃO estamos vendo` |
| `Logger` (`LoggerAgent`) | — | Grava transcrição em `.txt` |
| `Monitor` (`MonitorAgent`) | — | Vigia silêncio da mesa, injeta sinal de continuação |

**Por que três LLMs diferentes?** Para diversidade de opinião: cada modelo tem vieses e pontos fortes distintos, e o contraste entre eles é parte do produto.

### Regras da mesa redonda

Injetadas em cada persona via `_instrucao_mesa_redonda(nome)`:

1. PENSAMENTO OBRIGATÓRIO: a resposta DEVE começar com `<think>…</think>`. O bloco é isento das regras de concisão.
2. PASSAR A PALAVRA: terminar a fala citando UM colega (nunca os dois juntos).
3. ENCERRAMENTO: se a `DIRETRIZ DE MODO` mandar o agente encerrar, o texto acaba EXATAMENTE após `[SOLUÇÃO FINAL]` — proibido citar colegas na fala final.

### Timeouts e resiliência

- `LLMAgent`: `asyncio.wait_for(..., timeout=35.0)` sobre a chamada Groq. Em caso de timeout ou erro, publica uma mensagem de fallback instruindo o Gemini a fechar com `[SOLUÇÃO FINAL]`.
- `GeminiAgent`: mesmo cap de 35s; em erro, passa o bastão para o Qwen ou Gpt.

## Mapa de arquivos

```
papinho/
├── chat_interativo.py      # Entrypoint interativo (terminal rich)
├── logger_agent.py         # Logger silencioso do bus
├── run_demo.py             # Demo sem APIs (RuleBot, Echo, LLMStub)
├── run_demo.cmd            # Launcher Windows para o demo
├── requirements.txt        # openai, python-dotenv, google-genai
├── .env.example            # Modelo para .env (GROQ_API_KEY, GEMINI_API_KEY)
├── minhas_ideias.txt       # Log da transcrição (gerado, gitignored)
└── orchestrator/
    ├── agent.py            # ABC Agent + ciclo de vida (start/stop)
    ├── bus.py              # MessageBus (pub/sub em memória)
    ├── models.py           # Message dataclass
    └── agents/
        ├── llm_agent.py    # LLMAgent (Groq via OpenAI SDK)
        ├── gemini_agent.py # GeminiAgent (Google AI Studio)
        ├── monitor_agent.py# MonitorAgent (anti-órfão)
        ├── rule_agent.py   # RuleBasedAgent (demo)
        └── echo_agent.py   # EchoAgent (demo)
```

## Convenções

- Toda a comunicação com os LLMs é em **português**.
- Timestamps de `Message` são UTC tz-aware (`datetime.now(timezone.utc)`).
- `MessageBus.publish()` é sync e thread-safe; chamado de contextos sync e async.
- Erros de agentes viram mensagens `role="assistant"` no bus (visíveis no terminal e no log), em vez de irem para stderr.
- O chat real grava em `minhas_ideias.txt` simultaneamente via `LoggerAgent` e `display_messages()`.

## Próximos passos sugeridos

- Trocar `MessageBus` em memória por Redis/Kafka/DB para uso multi-processo.
- Adicionar testes automatizados (hoje a validação é rodar `chat_interativo.py` manualmente).
- Externalizar as personas em YAML/JSON para edição sem mexer em código.
