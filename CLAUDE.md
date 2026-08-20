# CLAUDE.md

Este arquivo dá orientações ao Claude Code (claude.ai/code) ao trabalhar com o código deste repositório.

## Projeto: PAPINHO

Orquestrador de múltiplos agentes de IA em Python. Uma "mesa redonda" de agentes LLM (Qwen e Groq via Groq, Gemini via Google AI Studio) troca mensagens em um `MessageBus` pub/sub assíncrono em memória. Um `MonitorAgent` silencioso detecta falas órfãs (agente que não cita nenhum colega) e injeta um sinal de continuação. Um `LoggerAgent` grava a transcrição completa em `minhas_ideias.txt`.

A interface do usuário é um chat REPL no terminal (`chat_interativo.py`) com UI em `rich` (painéis coloridos por agente, animação de carregamento, banner de boas-vindas, painel de ajuda). Toda a interação com os modelos acontece em português.

## Comandos

**Chat interativo (entrypoint principal):**
```bash
python chat_interativo.py
```
No Windows, `run_demo.cmd` é um launcher fino que aponta para o CPython instalado via `uv` (apenas para o demo, não para o chat real).

**Demo sem APIs (agentes rule/echo/llm-stub):**
```bash
python run_demo.py
```

**Instalar dependências:**
```bash
pip install -r requirements.txt   # openai, python-dotenv, google-genai
```

**Ambiente:** copie `.env.example` → `.env` e preencha `GROQ_API_KEY` + `GEMINI_API_KEY`. As duas APIs são obrigatórias para `chat_interativo.py`; `run_demo.py` roda sem elas.

Não há suíte de testes, linter ou formatter configurado. Valide alterações rodando o entrypoint manualmente.

## Arquitetura de alto nível

### Fluxo de mensagens
1. Usuário digita uma linha em `loop_conversa()` (`chat_interativo.py:360`).
2. Se houver `/modo` no início, a **DIRETRIZ DE MODO** correspondente (`MODOS_DE_CONVERSA`, `chat_interativo.py:230`) é anexada invisivelmente ao payload.
3. Se nenhum nome de debatedor for citado no texto, `Qwen` é prefixado automaticamente para iniciar a fila (`chat_interativo.py:402`).
4. A linha é publicada como `Message(sender="User", role="user")`.
5. Cada agente decide se responde pelo nome do último debatedor mencionado:
   - `LLMAgent` usa roteamento por **última menção** (`orchestrator/agents/llm_agent.py:32`) — depois de limpar pontuação, aplica `rfind` sobre o texto e só age se o **maior offset** for o próprio nome. Isso evita loops onde nomes anteriores disparam depois do último falante.
   - `GeminiAgent` usa substring-matching mais simples, mas também checa aliases hifenizados (`orchestrator/agents/gemini_agent.py:24`) para tolerar o caso de nomes chegarem "g-e-m-i-n-i" no bus.
6. Antes de chamar o LLM, o agente tenta pegar o **bastão** (`bus.bastao = asyncio.Lock()`, `chat_interativo.py:426`). Quando consegue, valida que a mensagem que o acordou ainda é a última do histórico (`bus.history()[-1] is not message`); se outro colega falou antes, desiste silenciosamente.
7. Em caso de timeout ou erro da API, o agente publica uma mensagem de fallback (com prefixo `[ALERTA DE SISTEMA]`) instruindo outro colega a fechar com `[SOLUÇÃO FINAL]`.
8. Cada agente, ao publicar, cita o nome de UM colega (regra `_instrucao_mesa_redonda`, `chat_interativo.py:103`). O debate continua até o marcador do modo fechar o ciclo (`[SOLUÇÃO FINAL]`, `ENCERRA O CICLO`).
9. O `MonitorAgent` (`orchestrator/agents/monitor_agent.py`) observa falas de debatedores que **não** citam nenhum colega e republica a fala original acrescida de um bloco `[INTERNO-MONITOR: …]` (com `metadata={"type": "monitor_signal"}`) para encorajar a continuação. O `display_messages()` filtra tanto o sufixo quanto o tipo de metadata.

### Abstrações centrais (`orchestrator/`)
- `models.py` — `Message` dataclass: `id`, `sender`, `role`, `content`, `timestamp`, `metadata`. É o único formato de fio. Timestamps são UTC tz-aware (`datetime.now(timezone.utc)`).
- `bus.py` — `MessageBus`: lista em memória + `asyncio.Condition`. `publish()` é sync; `subscribe(start_index)` é iterador assíncrono. `history()` retorna snapshot, `last_index()` retorna o tamanho atual. O comentário em `bus.py` indica que isto é intencionalmente em memória e deve ser trocado por Redis/Kafka/DB para uso multi-processo.
- `agent.py` — `Agent` ABC. Tem nome/persona, roda `_run_loop()` como `asyncio.Task` em background iniciado por `start()`, ignora as próprias mensagens e (por padrão) qualquer `role == "system"`. O flag `is_default_responder` vive na base para evitar redefinição por subclasse (reservado para uso futuro; hoje nenhum agente concreto o ativa). Oferece atalho `publish(content, role, metadata)` que embrulha `self.bus.publish(Message(...))`.

### Agentes concretos (`orchestrator/agents/`)
- `llm_agent.py` — `LLMAgent`. Cliente Groq async via `AsyncOpenAI(base_url="https://api.groq.com/openai/v1")`. Mantém sua própria `memory` rolando das últimas 4 mensagens e envia só esses turnos para a API. Remove blocos `<think>...</think>` antes de publicar. Roteamento blindado: o texto é limpo de pontuação antes do `rfind`.
- `gemini_agent.py` — `GeminiAgent`. Stateless: a cada turno reconstrói contexto de `self.bus.history()[-10:]` (vê o que foi dito enquanto estava ocioso). Usa `genai.Client(...).aio.models.generate_content`. Reconhece tanto o nome limpo quanto aliases hifenizados (`-`.join(name.lower())).
- `monitor_agent.py` — `MonitorAgent`. Sem LLM. Vigia falas dos três debatedores que não citam nenhum colega e republica a fala com `[INTERNO-MONITOR: …]` + `metadata={"type": "monitor_signal"}`. Não cria falas próprias e preserva o `sender` original. Ignora mensagens com `[SOLUÇÃO FINAL]`/`ENCERRA O CICLO` (encerramento explícito) e mensagens de erro de API.
- `echo_agent.py` / `rule_agent.py` — agentes só de demo, usados por `run_demo.py`. Não entram no chat real.
- `logger_agent.py` (raiz do projeto, não em `orchestrator/agents/`) — `LoggerAgent`. Assina o bus, anexa toda mensagem não-system em um `.txt`. O chat real também grava em `minhas_ideias.txt` diretamente de `display_messages()`, então logger + display gravam o mesmo arquivo no entrypoint real.
- O `LLMAgent` também suporta `base_url` customizada no construtor para rodar nós locais via Ollama/vLLM

### Papéis dos agentes (personas em `chat_interativo.py`)
- **Qwen — Explorador.** Pede clarificação quando falta contexto crítico, gera no MÁXIMO 2 caminhos distintos, marca `[verificar: X]` em fatos não confirmados. Voz: curioso, direto, brutalmente conciso.
- **Groq — Auditor.** Classifica cada risco como `[fatal | recuperável | cosmético]`, exige mitigação concreta junto, desafia números/datas sem origem. Voz: incisiva, factual, implacável.
- **Gemini — Estrategista.** Tem formato fixo (`## Resumo / ## Passos / ## Premissas a confirmar / ## O que NÃO estamos vendo`), prioriza pela restrição mais dura. Voz: clara, organizada, diplomática.
- **Monitor — Vigia.** Sem persona; sem LLM. Função puramente estrutural.

As três personas debatedoras são construídas concatenando uma base + `_instrucao_mesa_redonda(nome)` (`chat_interativo.py:103`). Editar personas base ali; comportamento por modo vive em `MODOS_DE_CONVERSA`.

### UI / terminal (`chat_interativo.py`)
- `Console` do `rich` para tudo: banners, painéis coloridos por agente, animação de "pontinhos" enquanto o debate rola.
- `exibir_mensagem_visual()` renderiza Markdown dentro de `Panel` com borda na cor do agente (cyan=User/Qwen, red=Groq, blue=Gemini, yellow=Sistema).
- Blocos `<think>…</think>` são automaticamente fechados se o modelo esquecer o `</think>`, transformados em "🧠 **Pensamento Interno:**" + bloco de código, e filtrados por padrão. Comando `/pensamento` alterna `EXIBIR_PENSAMENTO`.
- Animação de carregamento (`animacao_carregamento`) roda até detectar 2s de silêncio da mesa, então libera `bus.turno_encerrado` para o usuário digitar.

### Regras de roteamento (a parte não óbvia)
- `LLMAgent` faz `re.sub(r'[^a-zA-Z0-9\s]', '', content.lower())` antes do `rfind`, para que `**Qwen,**` ou `Qwen!` ativem o agente com a mesma confiança.
- `GeminiAgent` usa `in` puro, mas a regra de última menção do `LLMAgent` compensa (o `LLMAgent` só age se for o último nome, então não "rouba" a vez do Gemini).
- A terminação é dirigida por modo, não por código: cada modo, no prompt, manda um agente específico (geralmente Gemini) parar de citar colegas. Os marcadores terminais (`[SOLUÇÃO FINAL]`, `ENCERRA O CICLO`) vivem em `MODOS_DE_CONVERSA` (`chat_interativo.py:230`).
- O **bastão** (`bus.bastao`) garante exclusão mútua: dois agentes nunca respondem à mesma mensagem simultaneamente. Combinado com a checagem `history()[-1] is not message`, isso evita respostas duplicadas ou fora de ordem.
- O **Monitor** injeta sinal de continuação em falas órfãs, mas só para debatedores; mensagens de User/Logger/PromptGuard são ignoradas.

### Modos de conversa (`chat_interativo.py:230`)
O dict `MODOS_DE_CONVERSA` mapeia um comando com barra a um texto livre "DIRETRIZ DE MODO" anexado ao payload do usuário. Chaves aceitas:

- `/crashtest` — procura falhas e riscos; Revisor tem peso duplo.
- `/sintese` — Gemini lidera, organiza passos, encerra.
- `/debate` — uma volta só; Gemini sintetiza e pergunta ao User.
- `/livre` — exploração solta, sem fluxo fixo; User encerra.
- `/curto` — 2-3 frases MÁXIMO, um único agente.
- `/codigo` — Gemini lidera; Qwen/Revisor só se trouxerem coisa técnica. Formato: código → resumo → bugs.
- `/explica` — modo pedagógico, simples→complexo, divergências viram "⚠️ ponto de atenção" inline.
- `/revisa` — revisão de texto; Revisor lidera. Formato por item: Original / Sugestão / Por quê.
- `/brainstorm` — ideação pura; crítica proibida, Revisor fica em silêncio.
- `/decide` — apoio explícito a escolha; Gemini fecha com Opções / Critérios / Tradeoffs / Recomendação.
- `padrao` (sem barra, default — Força-Tarefa) — mesa roda até Gemini perceber maturidade e fechar com `[SOLUÇÃO FINAL]`.

Para adicionar um modo: adicionar entrada no dict. O parse em `loop_conversa()` (`chat_interativo.py:374`) já trata qualquer `/foo` cuja chave exista.

### Comandos do sistema
- `/ajuda` ou `/help` — abre painel amarelo listando todos os comandos.
- `/pensamento` — alterna `EXIBIR_PENSAMENTO` (mostra/oculta `<think>…</think>`).
- `sair` / `exit` / `quit` — encerra a malha com `KeyboardInterrupt` friendly.

### Mapa de arquivos
- `chat_interativo.py` — entrypoint interativo. Tem o registro de agentes, personas, modos, UI rich, I/O do terminal.
- `logger_agent.py` — listener silencioso do bus que grava em disco.
- `orchestrator/` — núcleo reutilizável: `bus`, `agent`, `models`, e o subpacote `agents/`.
- `orchestrator/agents/monitor_agent.py` — vigia silencioso; republica falas órfãs com `[INTERNO-MONITOR: …]`.
- `run_demo.py` + `run_demo.cmd` — smoke test end-to-end com agentes não-LLM; bom para validar o plumbing do bus/agente sem precisar de chaves de API.
- `minhas_ideias.txt` — log da transcrição gerado (gitignored).

## Convenções não óbvias

- Timestamps de `Message` são UTC tz-aware (`datetime.now(timezone.utc)`, `models.py:22`).
- Todos os prompts chegam aos modelos em português; edições de persona devem manter o mesmo registro de língua.
- Personas em `chat_interativo.py` são montadas concatenando uma persona base + `_instrucao_mesa_redonda(nome)` (`chat_interativo.py:103`), que anexa a regra de passar a palavra. Edite personas base ali; comportamento por modo vive em `MODOS_DE_CONVERSA`.
- `MessageBus.publish()` é sync e chamado de contextos sync (input) e async; tenta `loop.call_soon_threadsafe(...)` primeiro e cai pra `asyncio.create_task(...)` se não houver loop (`bus.py:27`).
- Erros de agentes são divulgados ao bus como mensagens `role="assistant"` do agente que falhou, em vez de logados em stderr — então aparecem no terminal do usuário e em `minhas_ideias.txt`. O prefixo `[ALERTA DE SISTEMA: …]` no conteúdo marca o tipo.
- `MonitorAgent` republica com o `sender` original e `metadata={"type": "monitor_signal"}`; tanto o tipo de metadata quanto o sufixo `[INTERNO-MONITOR: …]` são filtrados pelo `display_messages()` antes de exibir/gravar.
- O bloco `<think>…</think>` é fechado à força pelo renderer (`exibir_mensagem_visual`) se o modelo esquecer de fechar — não confie na boa-fé do LLM para balancear a tag.
- O bastão (`bus.bastao`) e o evento `bus.turno_encerrado` são atributos atribuídos em `main()` (`chat_interativo.py:426-428`), não fazem parte do construtor de `MessageBus`. Cuidado ao mexer: qualquer agente que toque o bus antes do `main()` vai quebrar.
- Aviso de Refatoração: A função `_instrucao_mesa_redonda` possui regras de exclusão mútua calibradas para evitar o Prompt Override das tags <think>. Não altere a lógica das regras 5, 6 e 7 sem aprovação explícita, para não quebrar a obediência do `LLMAgent` ou gerar `CancelledError`.