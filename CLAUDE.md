# CLAUDE.md

Orientações para trabalhar no PAPINHO, um orquestrador assíncrono de agentes LLM em Python. O chat real reúne Qwen (Explorador) e Groq (Auditor) via endpoint compatível com OpenAI da Groq, além do Gemini (Estrategista) via Google AI Studio. A interface principal é o REPL `rich` de `chat_interativo.py`, e a transcrição é gravada em `minhas_ideias.txt`.

## Comandos essenciais

```bash
pip install -r requirements.txt
python chat_interativo.py
python run_demo.py
python -m unittest discover -s tests -v
```

No Windows, `run_demo.cmd` executa somente a demo usando o caminho local do CPython instalado via `uv`. Ele não é um launcher portátil nem inicia o chat real.

Para o chat real, copie `.env.example` para `.env` e defina `GROQ_API_KEY` e `GEMINI_API_KEY`. A demo não usa essas chaves. Não há linter ou formatter configurado; a suíte usa `unittest`, inclusive `IsolatedAsyncioTestCase`.

## Arquitetura atual

### Envelope e barramento

- `orchestrator/models.py` define `Message`: `id`, `sender`, `role`, `content`, `thinking`, `timestamp`, `metadata`, `recipient`, `turn_id`, `mode` e `hop_count`. Timestamps são UTC e tz-aware.
- `orchestrator/bus.py` mantém o histórico canônico em memória. Cada subscription usa uma fila de tamanho 1 apenas como wake-up; notificações podem ser coalescidas sem perder mensagens porque o cursor relê o histórico.
- `MessageBus.publish()` é síncrono e confinado ao event loop. Ele aplica a política de finalização, resolve a rota uma vez, faz failover se o destinatário não estiver inscrito e então notifica os subscribers.
- `TurnState` contabiliza `active_agents` e `pending_deliveries`. A rodada só fica quiescente quando ambos zeram e termina a janela de silêncio (1,5 s por padrão). Não existe mais `bus.bastao` nem `bus.turno_encerrado`.

### Roteamento e encerramento

- `orchestrator/router.py` é a fonte única das regras. A prioridade é: `Message.recipient`, alvo estruturado em metadata, última menção textual e, para mensagens do usuário, líder padrão do modo.
- `route_message()` grava o destinatário no envelope e marca `_routing_resolved`; os agentes apenas consultam `is_addressed()`. Não recrie lógica de roteamento dentro de `LLMAgent` ou `GeminiAgent`.
- Menções são tokenizadas e normalizadas, inclusive aliases como `g-e-m-i-n-i`. Se houver vários nomes, vence o último.
- `MODE_POLICIES` define líder inicial, uso do Monitor, agentes autorizados a encerrar, contribuições obrigatórias e auto-finalização.
- No modo padrão, `/debate` e `/brainstorm`, uma conclusão só é definitiva depois de Qwen e Groq contribuírem e deve vir do Gemini. Uma conclusão precoce vira `[SÍNTESE PROVISÓRIA]` e é roteada ao participante ausente.
- `MAX_TURN_HOPS = 12` é a válvula estrutural contra ciclos infinitos. `/curto` termina após uma resposta; `/sintese` e `/decide` permitem auto-finalização do Gemini.

### Ciclo de vida e falhas

- `orchestrator/agent.py` cria a subscription antes de `start()` retornar, supervisiona a task do agente, contabiliza atividade no `TurnState` e fecha subscriptions deterministicamente no shutdown.
- Exceções de processamento e de lifecycle viram eventos de sistema estruturados (`agent_processing_error` e `agent_task_error`). `CancelledError` deve continuar sendo propagado.
- `await_with_deadline()` libera o caller no deadline mesmo quando o SDK demora a cooperar com o cancelamento e observa a task destacada até ela terminar.
- `orchestrator/recovery.py` publica recovery roteável com a mesma lineage (`turn_id`, `mode`, `hop_count`). Há no máximo dois fallbacks distintos por rodada, com circuit breaker para agentes já falhos; a exaustão gera `agent_recovery_exhausted`.

### Agentes

- `LLMAgent`: cliente `AsyncOpenAI`, roteamento centralizado, memória limitada por bytes UTF-8 e commit somente após resposta válida. Se a saída contiver apenas pensamento interno, repete uma vez pedindo texto final. Qwen usa o modelo `qwen/qwen3.6-27b`; o Auditor usa `openai/gpt-oss-20b`, deadline de 60 s, memória de 16.000 bytes e reasoning baixo/oculto.
- `GeminiAgent`: cliente assíncrono `google-genai`, deadline de 35 s e contexto reconstruído das últimas dez mensagens não-system.
- Ambos separam `<think>...</think>` em `Message.thinking`; o texto visível fica em `Message.content`. O pensamento pode entrar, truncado, no contexto do próximo agente e só é exibido ao usuário quando `/pensamento` está ativo.
- `MonitorAgent`: detecta falas sem handoff válido, escolhe outro agente disponível que não tenha falhado e publica no máximo três `monitor_signal` por rodada. `/curto` e `/livre` desativam o Monitor. Ao esgotar as opções, publica `monitor_handoff_exhausted`.
- `LoggerAgent`: listener na raiz do projeto. Registra mensagens não-system e eventos de erro visíveis. `display_messages()` também grava as respostas exibidas no mesmo arquivo.
- `RuleBasedAgent` e `EchoAgent` existem apenas para `run_demo.py`.

## Fluxo do chat

1. `loop_conversa()` espera a quiescência da rodada e lê a entrada.
2. Comandos de modo selecionam uma entrada de `MODOS_DE_CONVERSA`; a diretriz é anexada ao conteúdo enviado.
3. O destinatário inicial é a última menção explícita ou o `default_responder` da política do modo.
4. A mensagem do usuário é publicada com `recipient`, `mode` e uma nova lineage de rodada.
5. Cada resposta herda `turn_id` e `mode`, incrementa `hop_count` e é roteada uma única vez pelo bus.
6. O Monitor corrige handoffs ausentes; recovery trata erros de API; política de finalização e teto de hops encerram a cadeia.
7. A UI mostra respostas e eventos estruturados relevantes; mensagens internas de lifecycle, stream, thinking e monitor são filtradas.

## Modos e comandos

Os modos ficam em `MODOS_DE_CONVERSA`, enquanto o comportamento estrutural correspondente fica em `MODE_POLICIES`. Ao adicionar ou alterar um modo, mantenha os dois mapas coerentes e cubra o fluxo com testes.

- `/crashtest`: Groq inicia.
- `/sintese`, `/codigo`, `/explica` e `/decide`: Gemini inicia.
- `/revisa`: Groq inicia.
- `/debate`, `/livre`, `/curto`, `/brainstorm` e o modo padrão: Qwen inicia.
- `/ajuda` ou `/help`: mostra ajuda; `/pensamento`: alterna pensamento interno; `sair`, `exit` ou `quit`: encerra.

## Mapa de arquivos

- `chat_interativo.py`: entrypoint, personas, modos, registro de agentes e UI.
- `logger_agent.py`: persistência da transcrição.
- `orchestrator/agent.py`: lifecycle e supervisão.
- `orchestrator/bus.py`: histórico, subscriptions, quiescência, failover e finalização.
- `orchestrator/models.py`: envelope `Message`.
- `orchestrator/router.py`: resolução de destinatário e políticas de modo.
- `orchestrator/recovery.py`: fallbacks e circuit breaker.
- `orchestrator/agents/`: integrações LLM, Monitor e agentes da demo.
- `tests/`: lifecycle do bus, memória do LLMAgent, recovery e guardas de roteamento.

## Cuidados ao alterar

- Preserve português nas personas e instruções enviadas aos modelos.
- Não dependa apenas do texto para controle de fluxo; use `recipient`, `turn_id`, `mode`, `hop_count` e metadata estruturada.
- Ao publicar uma resposta derivada, passe a mensagem de origem para preservar lineage.
- Não transforme `publish()` em uma operação thread-safe sem redesenhar o bus; hoje ele pressupõe um único event loop.
- Rode toda a suíte após mudanças em roteamento, terminalidade, lifecycle, memória, Monitor ou recovery.
