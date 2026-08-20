# PAPINHO — Orquestrador Multi-Agente de IA

**PAPINHO** é uma arquitetura em Python que coloca vários agentes LLM para conversar entre si em uma "mesa redonda" assíncrona. Você digita uma ideia ou um problema técnico no terminal e três personas — **Qwen (Explorador)**, **Groq (Auditor)** e **Gemini (Estrategista)** — debatem até chegar a uma solução estruturada. Toda a transcrição fica gravada em `minhas_ideias.txt`.

## Visão geral

- **Barramento pub/sub em memória** (`MessageBus`): única fonte de verdade para as mensagens.
- **Agentes como `asyncio.Task`s em background**: o debate roda mesmo enquanto o terminal espera o próximo `input()`.
- **Estado de rodada** (`TurnState`): contabiliza agentes ativos e entregas pendentes até a quiescência real.
- **Modo padrão (Força-Tarefa)**: só o Estrategista encerra, e apenas depois de contribuições válidas de Qwen e Groq.
- **Modos alternáveis**: `/crashtest`, `/sintese`, `/debate`, `/livre`, `/curto`, `/codigo`, `/explica`, `/revisa`, `/brainstorm`, `/decide` — cada um com diretriz própria.
- **Pensamento interno visível opcional**: comando `/pensamento` mostra (ou oculta) os blocos `<think>…</think>` que os modelos produzem.
- **Monitor silencioso**: `MonitorAgent` detecta handoffs inválidos, evita agentes já falhos e injeta no máximo três sinais por rodada.
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

## Testes

```bash
python -m unittest discover -s tests -v
```

A suíte cobre lifecycle e quiescência do bus, roteamento/finalização, recovery e memória do `LLMAgent`. Não há linter ou formatter configurado no repositório.

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
   │  Qwen   │ ◀── menciona ──── │   Groq  │ ◀── menciona ──── │ Gemini  │
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
3. `router.py` resolve um único `recipient`: menção explícita vence; sem menção, o líder estrutural do modo é usado.
4. A mensagem é publicada com `turn_id`, `mode`, `hop_count` e destinatário estruturado — o texto do usuário não é reescrito.
5. Cada agente consulta a mesma decisão de roteamento; o último nome canônico só é usado quando não há `recipient` explícito.
6. O `TurnState` conta agentes ativos e entregas ainda não inspecionadas. A quiescência só ocorre quando ambos chegam a zero e a janela de silêncio termina.
7. Cada resposta herda a rodada e incrementa seu hop. Marcadores finais, líderes terminais por modo e o teto global de hops encerram a cadeia estruturalmente.
8. O `MonitorAgent` resgata falas sem handoff válido, evita agentes já falhos e possui orçamento finito por rodada; `/curto` e `/livre` desativam a intervenção automática.
9. `LoggerAgent` grava toda a transcrição em `minhas_ideias.txt`.

### Agentes

| Agente | Modelo | Papel |
|---|---|---|
| `Qwen` (`LLMAgent`) | `qwen/qwen3.6-27b` via Groq | Explorador — abre caminhos, marca `[verificar: X]` |
| `Groq` (`LLMAgent`) | `openai/gpt-oss-20b` via Groq | Auditor — classifica riscos em `[fatal \| recuperável \| cosmético]` |
| `Gemini` (`GeminiAgent`) | `gemini-3.1-flash-lite` via Google AI Studio | Estrategista — plano final em `## Resumo / ## Passos / ## Premissas / ## O que NÃO estamos vendo` |
| `Logger` (`LoggerAgent`) | — | Grava transcrição em `.txt` |
| `Monitor` (`MonitorAgent`) | — | Vigia silêncio da mesa, injeta sinal de continuação |

**Por que três LLMs diferentes?** Para diversidade de opinião: cada modelo tem vieses e pontos fortes distintos, e o contraste entre eles é parte do produto.

### Regras da mesa redonda

Injetadas em cada persona via `_instrucao_mesa_redonda(nome)`:

1. RACIOCÍNIO INTERNO: os modelos analisam internamente e publicam somente a resposta útil, sem exigir ou expor `<think>…</think>`.
2. PASSAR A PALAVRA: terminar a fala citando UM colega (nunca os dois juntos).
3. ENCERRAMENTO: se a `DIRETRIZ DE MODO` mandar o agente encerrar, o texto acaba EXATAMENTE após `[SOLUÇÃO FINAL]` — proibido citar colegas na fala final.

### Timeouts e resiliência

- `LLMAgent`: deadline configurável por agente (60s para o Auditor), memória limitada por bytes e fallback estruturado em caso de erro. O GPT-OSS usa esforço de raciocínio baixo/oculto e repete uma vez a geração se não houver resposta final visível.
- `GeminiAgent`: deadline de 35s. O caller é liberado mesmo se o SDK demorar a cooperar com o cancelamento.
- Recovery possui lineage por `turn_id`, circuit breaker e no máximo dois fallbacks distintos; exaustão vira evento terminal visível.
- No modo padrão, `/debate` e `/brainstorm`, uma conclusão prematura é reclassificada como `[SÍNTESE PROVISÓRIA]` e roteada ao participante que ainda não contribuiu.

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
├── tests/                   # Testes unittest/IsolatedAsyncioTestCase
└── orchestrator/
    ├── agent.py            # ABC Agent + ciclo de vida (start/stop)
    ├── bus.py              # MessageBus (pub/sub em memória)
    ├── models.py           # Message dataclass
    ├── router.py           # resolução única de destinatário e políticas de modo
    ├── recovery.py         # fallback limitado + circuit breaker
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
- `MessageBus.publish()` é síncrono e confinado ao event loop do orquestrador.
- O histórico é a fonte canônica; filas de tamanho 1 apenas acordam subscribers e podem coalescer notificações sem perder mensagens.
- Erros e exaustões viram eventos estruturados de sistema visíveis no terminal e no log.
- O chat real grava em `minhas_ideias.txt` simultaneamente via `LoggerAgent` e `display_messages()`.

## Próximos passos sugeridos

- Trocar `MessageBus` em memória por Redis/Kafka/DB para uso multi-processo.
- Ampliar continuamente os testes assíncronos determinísticos de lifecycle, roteamento e recovery.
- Externalizar as personas em YAML/JSON para edição sem mexer em código.
