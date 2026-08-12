# CLAUDE.md

Este arquivo dá orientações ao Claude Code (claude.ai/code) ao trabalhar com o código deste repositório.

## Projeto: PAPINHO

Orquestrador de múltiplos agentes de IA em Python. Uma "mesa redonda" de agentes LLM (Qwen via Groq, Llama-3.3 Revisor via Groq, Gemini via Google AI Studio) troca mensagens em um `MessageBus` pub/sub assíncrono em memória, mediada por um agente guardião/roteador (`PromptGuard`). Um `LoggerAgent` silencioso grava a transcrição completa em `minhas_ideias.txt`.

O usuário interage via REPL no terminal (`chat_interativo.py`); o texto digitado é sanitizado por obfuscation de nomes para que toda mensagem passe pelo guardião em vez de acionar agentes por substring.

## Comandos

**Chat interativo (entrypoint principal):**
```bash
python chat_interativo.py
```
No Windows, `run_demo.cmd` é um launcher fino que aponta para o CPython instalado via `uv`. Para o demo (agentes rule/echo/llm-stub, sem precisar de APIs reais):
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
1. Usuário digita uma linha em `loop_conversa()` (`chat_interativo.py:257`).
2. Nomes de agentes no texto cru são hifenizados por `_ofuscar_nomes()` (`chat_interativo.py:146`) para que substring-matching não burle o guardião.
3. Uma diretriz de modo opcional (`/crashtest | /sintese | /debate | /livre | /curto | /codigo | /explica | /revisa | /brainstorm | /decide`, ou o padrão `padrao`) é anexada invisivelmente ao payload (`chat_interativo.py:214`).
4. A linha é publicada como `Message` endereçada ao `PromptGuard`.
5. `PromptGuard` (LLM com `is_default_responder=True`) valida segurança, classifica intenção (saudação / fato direto / criativa / analítica), e reescreve a mensagem com o nome do agente escolhido escrito por extenso e sem hífen — esse nome é o que aciona o próximo agente. Saudações e perguntas diretas são respondidas pelo próprio guardião com prefixo `[responder-direto]`.
6. Cada agente que responde processa apenas mensagens cujo último nome mencionado (em `LLMAgent.on_message`, `orchestrator/agents/llm_agent.py:38`) bate com o próprio, OU mensagens diretamente do `User` se `is_default_responder=True`. `GeminiAgent` usa substring-matching mais simples (`orchestrator/agents/gemini_agent.py:24`).
7. Agentes respondem citando o nome do próximo colega para continuar o loop, até o marcador terminal do modo ativo (ex.: `[SOLUÇÃO FINAL]`) fechar o ciclo.

### Abstrações centrais (`orchestrator/`)
- `models.py` — `Message` dataclass: `id`, `sender`, `role`, `content`, `timestamp`, `metadata`. É o único formato de fio.
- `bus.py` — `MessageBus`: lista em memória + `asyncio.Condition`. `publish()` é sync; `subscribe(start_index)` é iterador assíncrono. `history()` retorna um snapshot. Comentário em `bus.py` indica que isto é intencionalmente em memória e deve ser trocado por Redis/Kafka/DB para uso multi-processo.
- `agent.py` — `Agent` ABC. Tem nome/persona, roda `_run_loop()` como `asyncio.Task` em background iniciado por `start()`, ignora as próprias mensagens e (por padrão) qualquer `role == "system"`. Oferece atalho `publish()` que embrulha `self.bus.publish(Message(sender=self.name, ...))`.

### Agentes concretos (`orchestrator/agents/`)
- `llm_agent.py` — `LLMAgent`. Cliente Groq async via `AsyncOpenAI(base_url="https://api.groq.com/openai/v1")`. Mantém sua própria `memory` rolando de 10 mensagens e envia só o último turno de usuário para a API. Remove blocos `<think>...</think>` antes de publicar.
- `gemini_agent.py` — `GeminiAgent`. Stateless: a cada turno reconstrói contexto de `self.bus.history()[-10:]` (vê o que foi dito enquanto estava ocioso). Usa `genai.Client(...).aio.models.generate_content`.
- `echo_agent.py` / `rule_agent.py` — agentes só de demo, usados por `run_demo.py`. Não entram no chat real.
- `logger_agent.py` (raiz do projeto, não em `orchestrator/agents/`) — `LoggerAgent`. Assina o bus, anexa toda mensagem não-system em um `.txt`. Nota: o chat real também grava em `minhas_ideias.txt` diretamente de `display_messages()` (`chat_interativo.py:242`), então logger + display gravam o mesmo arquivo no entrypoint real.

### Papéis dos agentes (personas em `chat_interativo.py`)
- **Qwen — Explorador.** Pede clarificação quando falta contexto crítico, gera 2-4 caminhos distintos, marca `[verificar: X]` em fatos não confirmados. Voz: curioso, direto.
- **Revisor — Auditor.** Classifica cada risco como `[fatal | recuperável | cosmético]`, exige mitigação concreta junto, desafia números/datas sem origem. Voz: incisiva, factual.
- **Gemini — Estrategista.** Tem formato fixo (`## Resumo / ## Passos / ## Premissas a confirmar / ## O que NÃO estamos vendo`), prioriza pela restrição mais dura. Voz: clara, organizada, diplomática.
- **PromptGuard — Porteiro.** Triagem rápida: bloqueia pedidos ilegais/manipulativos, classifica intenção (saudação/fato/criativa/analítica), responde saudações e fatos diretos sozinho com `[responder-direto]`, e roteia o resto citando o agente por extenso e sem hífen.

As três personas debatedoras são construídas concatenando uma base + `_instrucao_mesa_redonda(nome)` (`chat_interativo.py:57`). Editar personas base ali; comportamento por modo vive em `MODOS_DE_CONVERSA`.

### Regras de roteamento (a parte não óbvia)
- `is_default_responder=True` é o flag que permite um agente escutar mensagens `User` cruas. Só `PromptGuard` o tem (`chat_interativo.py:196`).
- `LLMAgent` usa roteamento por **última menção** (`conteudo_lower.rfind(nome)`) — se múltiplos nomes de agente aparecem, só o de maior offset responde. Evita loops onde nomes anteriores disparam depois do último falante.
- `GeminiAgent` usa `in` puro, que a regra de última menção do `LLMAgent` compensa.
- A terminação é dirigida por modo, não por código: cada modo, no prompt, manda um agente específico (geralmente Gemini) parar de citar colegas. Os marcadores terminais (`[SOLUÇÃO FINAL]`, "ENCERRA O CICLO") vivem em `MODOS_DE_CONVERSA` (`chat_interativo.py:214`).
- Nova regra na mesa-redonda (`chat_interativo.py:57`): passar a palavra só se for útil. Se a resposta já cobre a pergunta de forma fechada (o colega só confirmaria), o agente encerra silenciosamente em vez de forçar mais uma fala.

### Modos de conversa (`chat_interativo.py:214`)
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

Para adicionar um modo: adicionar entrada no dict. O parse em `chat_interativo.py:272` já trata qualquer `/foo` cuja chave exista.

### Mapa de arquivos
- `chat_interativo.py` — entrypoint interativo principal. Tem o registro de agentes, personas, modos, I/O do terminal.
- `logger_agent.py` — listener silencioso do bus que grava em disco.
- `orchestrator/` — núcleo reutilizável: `bus`, `agent`, `models`, e o subpacote `agents/`.
- `run_demo.py` + `run_demo.cmd` — smoke test end-to-end com agentes não-LLM; bom para validar o plumbing do bus/agente sem precisar de chaves de API.
- `minhas_ideias.txt` — log da transcrição gerado (gitignored).

## Convenções não óbvias

- Timestamps de `Message` usam `datetime.utcnow()` (`models.py:22`) — UTC naive, sem tzinfo.
- Todos os prompts chegam aos modelos em português; edições de persona devem manter o mesmo registro de língua.
- Personas em `chat_interativo.py` são montadas concatenando uma persona base + `_instrucao_mesa_redonda(nome)` (`chat_interativo.py:57`), que anexa a regra de passar a palavra. Edite personas base ali; comportamento por modo vive em `MODOS_DE_CONVERSA`.
- `MessageBus.publish()` é sync e chamado de contextos sync (input) e async; tenta `loop.call_soon_threadsafe(...)` primeiro e cai pra `asyncio.create_task(...)` se não houver loop (`bus.py:27`).
- Erros de agentes são divulgados ao bus como mensagens `role="assistant"` do agente que falhou, em vez de logados em stderr — então aparecem no terminal do usuário e em `minhas_ideias.txt`.
- O guardião responde diretamente (sem delegar) saudações e perguntas factuais diretas, marcado com prefixo interno `[responder-direto]`. Não espere que uma saudação dispare uma mesa-redonda.