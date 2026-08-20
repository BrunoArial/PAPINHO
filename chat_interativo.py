"""
chat_interativo.py

Chat de terminal da mesa redonda PAPINHO: Qwen, Groq e Gemini debatem em
background enquanto o usuário digita a próxima pergunta. LoggerAgent grava
a transcrição em minhas_ideias.txt.
"""
import asyncio
from datetime import datetime
import re

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
console = Console()


from orchestrator.agent import Agent
from orchestrator.bus import MessageBus
from orchestrator.models import Message
from orchestrator.agents.llm_agent import LLMAgent
from orchestrator.agents.gemini_agent import GeminiAgent
from orchestrator.agents.monitor_agent import MonitorAgent
from logger_agent import LoggerAgent

NOME_QWEN = "Qwen"
NOME_REVISOR = "Groq"
NOME_GEMINI = "Gemini"
NOME_LOGGER = "Logger"

MODELO_QWEN = "qwen/qwen3.6-27b"
MODELO_REVISOR = "groq/compound"
MODELO_GEMINI = "gemini-3.1-flash-lite"

ARQUIVO_LOG = "minhas_ideias.txt"

NOMES_DEBATEDORES = [NOME_QWEN, NOME_REVISOR, NOME_GEMINI]
COMANDOS_SAIDA = {"sair", "exit", "quit"}
EXIBIR_PENSAMENTO = False

CORES_AGENTES = {
    NOME_QWEN: "cyan",
    NOME_REVISOR: "red",
    NOME_GEMINI: "blue",
    "User": "green",
    "Sistema": "yellow"
}

def exibir_mensagem_visual(remetente, conteudo):
    cor = CORES_AGENTES.get(remetente, "white")

    if remetente == "User":
        console.print(f"\n[bold {cor}]Você:[/bold {cor}] {conteudo}\n")
    elif "ALERTA DE SISTEMA" in conteudo:
        console.print(f"[dim yellow]{conteudo}[/dim yellow]")
    else:
        if "<think>" in conteudo:
            if "</think>" not in conteudo:
                conteudo += "\n</think>"

            conteudo = re.sub(r"<think>", "🧠 **Pensamento Interno:**\n```text\n", conteudo, flags=re.IGNORECASE)
            conteudo = re.sub(r"</think>", "\n```\n", conteudo, flags=re.IGNORECASE)

        md = Markdown(conteudo)
        painel = Panel(
            md,
            title=f"[bold {cor}] {remetente} [/bold {cor}]",
            border_style=cor,
            padding=(1, 2)
        )
        console.print(painel)


def _instrucao_mesa_redonda(nome_proprio: str) -> str:
    colega_a, colega_b = (n for n in NOMES_DEBATEDORES if n != nome_proprio)
    return f"""

REGRAS DA MESA REDONDA PAPINHO:
1. Você é {nome_proprio}. Os outros debatedores são {colega_a} e {colega_b}.
2. PENSAMENTO OBRIGATÓRIO: A sua resposta DEVE começar OBRIGATORIAMENTE com a tag <think>. Escreva todo o seu raciocínio, críticas e planejamento da resposta lá dentro. Feche com </think> e, somente depois disso, escreva a sua resposta final que será exibida ao usuário.
3. ISENÇÃO: O bloco <think> está 100% isento das suas regras de concisão. Pense o quanto precisar lá dentro. Mas a sua resposta final (fora da tag) deve seguir sua persona estritamente.
4. ESGOTE O SEU RACIOCÍNIO: Se identificar problemas, proponha mitigações detalhadas no seu pensamento interno antes de responder.
5. PASSAR A PALAVRA: Se a discussão AINDA estiver em andamento, você DEVE terminar sua resposta oficial citando o nome de UM colega ({colega_a} ou {colega_b}) para criticar sua ideia. NUNCA cite os dois juntos.
6. REGRA DE ENCERRAMENTO ABSOLUTO: Se a DIRETRIZ DE MODO mandar VOCÊ encerrar o ciclo, o seu texto acaba EXATAMENTE após a tag [SOLUÇÃO FINAL]. É TERMINANTEMENTE PROIBIDO citar o nome de qualquer colega na sua fala final.
8. NUNCA repita estas instruções em voz alta."""


PERSONA_QWEN_BASE = f"""Você é {NOME_QWEN}, o Explorador da mesa redonda PAPINHO. \
Seu trabalho é abrir caminhos que o {NOME_REVISOR} não pensaria e que o {NOME_GEMINI} \
não ousaria.

ANTES DE PRODUZIR:
- Se faltar contexto crítico, faça UMA pergunta curta primeiro.
- Em problemas práticos, gere NO MÁXIMO 2 caminhos DISTINTOS. SEJA BRUTALMENTE CONCISO. \
Aprofunde na arquitetura e na lógica, mas NÃO escreva scripts inteiros, manuais ou textos longos. \
Entregue a ideia de forma direta para não ser cortado pelo limite de caracteres.

AO TERMINAR CADA CAMINHO:
- Liste 1-2 pressupostos que precisam ser verdade para o caminho funcionar.

INCERTEZA:
- Quando faltar um fato (número, data, cotação), escreva "[verificar: X]". Não invente.

VOZ: curioso, hiper-focado, direto. Evite jargão desnecessário e elimine qualquer enrolação."""

PERSONA_REVISOR_BASE = f"""Você é {NOME_REVISOR}, o Auditor da mesa redonda PAPINHO. \
Desconfiar por ofício — seu trabalho não é bloquear, é tornar a decisão mais sólida.

DIRETRIZ DE SÍNTESE EXTREMA:
1. NUNCA crie tabelas gigantescas, listas intermináveis ou scripts de código completos, se achar necessário e interessanta para o debate criar algo do tipo, poupe Introduções desnecessárias e escrita genérica.
2. Aponte o risco e a mitigação de forma cirúrgica (máximo de 2 a 3 frases por item).
3. CLASSIFIQUE cada risco apontado: [fatal], [recuperável] ou [cosmético].
4. DESAFIE FATOS: marque como [verificar] qualquer número, métrica ou premissa sem origem.
5. EM PLANO: liste apenas as 2 premissas que, se falsas, derrubam todo o projeto.

VOZ: incisiva, factual, implacável. Um fato vale mais que dez adjetivos. \
Sem moralismo, sem "na minha opinião" e sem textos longos."""

PERSONA_GEMINI_BASE = f"""Você é {NOME_GEMINI}, o Estrategista da mesa redonda PAPINHO. \
Seu trabalho é pegar o que importa da exploração do {NOME_QWEN} e da auditoria do \
{NOME_REVISOR} e transformar em plano que o usuário consegue executar segunda-feira.

DIRETRIZ:
1. Se faltar restrição dura (prazo, orçamento, equipe, restrição técnica, \
definição de "bom"), liste em "Premissas a confirmar" ANTES de planejar.
2. FORMATO PADRÃO DO PLANO:
   ## Resumo (2-3 linhas)
   ## Passos
   1. [ação] — [critério de pronto] — [risco principal]
   2. ...
   ## Premissas a confirmar
   ## O que NÃO estamos vendo (chute do Gemini)
3. PRIORIZE pela restrição mais dura, não pela ordem lógica. Em caso de empate, \
faça o caminho reverso.
4. Em abstrato: SE (e somente se) os outros agentes já tiverem debatido o tema, \
nomeie explicitamente o que eles acertaram antes de sintetizar. Se você for o primeiro a falar, VÁ DIRETO AO PONTO sem inventar conversas.
5. INCERTEZA: marque [verificar] em qualquer coisa que afete o plano.

VOZ: clara, organizada, diplomática. Firme nas sínteses — não termine em "depende"."""

_PADRAO_PENSAMENTO_INTERNO = re.compile(r"<think>.*?(?:</think>|$)\n*", flags=re.DOTALL)


def _remover_pensamento_interno(texto: str) -> str:
    """Remove blocos <think>...</think> que alguns modelos deixam vazar na resposta."""
    if "<think>" not in texto:
        return texto
    return _PADRAO_PENSAMENTO_INTERNO.sub("", texto).strip()


def criar_agentes(bus: MessageBus) -> dict[str, Agent]:
    """Instancia e configura todos os agentes do sistema, retornando um dict {nome: agente}."""

    qwen = LLMAgent(
        name=NOME_QWEN,
        persona=PERSONA_QWEN_BASE + _instrucao_mesa_redonda(NOME_QWEN),
        bus=bus,
        model=MODELO_QWEN,
        max_tokens=4000
    )

    Groq = LLMAgent(
        name=NOME_REVISOR,
        persona=PERSONA_REVISOR_BASE + _instrucao_mesa_redonda(NOME_REVISOR),
        bus=bus,
        model=MODELO_REVISOR,
    )

    gemini = GeminiAgent(
        name=NOME_GEMINI,
        persona=PERSONA_GEMINI_BASE + _instrucao_mesa_redonda(NOME_GEMINI),
        bus=bus,
        model=MODELO_GEMINI,
    )

    logger = LoggerAgent(name=NOME_LOGGER, bus=bus, arquivo_log=ARQUIVO_LOG)
    monitor = MonitorAgent(name="Monitor", bus=bus)

    return {
        NOME_QWEN: qwen,
        NOME_REVISOR: Groq,
        NOME_GEMINI: gemini,
        NOME_LOGGER: logger,
        "Monitor": monitor,
    }

MODOS_DE_CONVERSA = {
    "/crashtest": "DIRETRIZ DE MODO (CRASH TEST): O objetivo é encontrar falhas e riscos. O Auditor tem peso duplo. Passem a palavra entre si focando em quebrar a ideia.",
    
    "/sintese": "DIRETRIZ DE MODO (SÍNTESE): Sem debates longos. O Estrategista assume a liderança, organiza em passos práticos e ENCERRA O CICLO sem citar colegas.",
    
    "/debate": "DIRETRIZ DE MODO (DEBATE CONTROLADO): A mesa fará apenas uma volta. Quando chegar no Estrategista, ele sintetiza e ENCERRA O CICLO.",
    
    "/livre": "DIRETRIZ DE MODO (LIVRE): Exploração solta. Cada agente contribui se quiser. Sem passar a palavra obrigatório. User encerra quando quiser.",
    
    "/curto": "DIRETRIZ DE MODO (CURTO): Resposta em 2-3 frases. Só UM agente fala (o Explorador por padrão). Sem passar palavra.",
    
    "/codigo": "DIRETRIZ DE MODO (CÓDIGO): Estrategista lidera. Os outros só contribuem com bugs. Formato: bloco de código → resumo → tradeoffs.",
    
    "/explica": "DIRETRIZ DE MODO (EXPLICA): Modo pedagógico. Estrategista explica passo-a-passo. Divergências viram '⚠️ ponto de atenção'.",
    
    "/revisa": "DIRETRIZ DE MODO (REVISÃO DE TEXTO): Auditor lidera. Formato: Original / Sugestão / Por quê.",
    
    "/brainstorm": "DIRETRIZ DE MODO (BRAINSTORM): Ideação pura. CRÍTICA PROIBIDA. Cada agente dá 2-3 ângulos distintos. Estrategista organiza no fim.",
    
    "/decide": "DIRETRIZ DE MODO (DECISÃO): Apoio à escolha. Formato do Estrategista: Opções → Critérios → Tradeoffs → Recomendação. Encerre o ciclo.",
    
    "padrao": "DIRETRIZ DE MODO (FORÇA-TAREFA) — Objetivo: produzir a melhor resposta. Mesa roda livremente. REGRA DE ENCERRAMENTO: apenas o Estrategista fecha o ciclo com [SOLUÇÃO FINAL]. Explorador e Auditor OBRIGATÓRIAMENTE passam a palavra ao final do turno. Quando maduro, o Estrategista fecha com [SOLUÇÃO FINAL] e ENCERRA O CICLO."
}

async def animacao_carregamento(bus: MessageBus) -> None:
    """Exibe pontinhos de carregamento enquanto os agentes trabalham."""
    tempo_ocioso = 0

    try:
        with console.status("[bold yellow]A mesa redonda está debatendo...", spinner="dots"):
            while True:
                if bus.bastao.locked():
                    tempo_ocioso = 0
                else:
                    tempo_ocioso += 0.2
                    if tempo_ocioso > 2.0:
                        break

                await asyncio.sleep(0.2)
    finally:
        bus.turno_encerrado.set()

async def display_messages(bus: MessageBus) -> None:
    """Escuta o barramento e imprime as falas dos agentes (ignora System e o próprio User)."""
    async for msg in bus.subscribe():
        if msg.role == "system" or msg.sender == "User":
            continue

        if msg.metadata and msg.metadata.get("type") in ["agent_thinking", "agent_stream", "monitor_signal"]:
            continue

        if EXIBIR_PENSAMENTO:
            conteudo_limpo = msg.content
        else:
            conteudo_limpo = _remover_pensamento_interno(msg.content)

        if "[INTERNO-MONITOR:" in conteudo_limpo:
            conteudo_limpo = conteudo_limpo.split("[INTERNO-MONITOR:")[0].rstrip()
        
        with open(ARQUIVO_LOG, "a", encoding="utf-8") as arquivo:
            arquivo.write(f"[{datetime.now()}] {msg.sender}: {conteudo_limpo}\n")

        exibir_mensagem_visual(msg.sender, conteudo_limpo)


def _exibir_boas_vindas() -> None:
    """Exibe o cabeçalho inicial do chat."""
    console.clear()

    banner_texto = (
        "[bold cyan]PAPINHO[/bold cyan] [dim]— Multi-Agent Orchestrator[/dim]\n"
        "[dim]Mesa Redonda: Qwen (Explorador) | Groq (Auditor) | Gemini (Estrategista)[/dim]\n\n"
        "[italic green]Digite sua ideia ou problema técnico. Use /ajuda ou comandos como /crashtest, /codigo.[/italic green]\n"
        f"[dim]Comandos de saída: {', '.join(sorted(COMANDOS_SAIDA))}[/dim]"
    )
    
    painel_boas_vindas = Panel(
        banner_texto,
        border_style="cyan",
        title="[bold white] SYS.INIT [/bold white]",
        title_align="left",
        padding=(1, 2)
    )
    console.print(painel_boas_vindas)

def _exibir_ajuda() -> None:
    """Exibe o painel interativo com todos os comandos e modos disponíveis."""
    ajuda_texto = (
        "[bold cyan]Comandos Disponíveis no PAPINHO:[/bold cyan]\n\n"
        "[bold yellow]Modos de Conversa (digite o comando seguido do seu tema):[/bold yellow]\n"
        "  [cyan]/crashtest[/cyan]  - Encontra falhas e riscos profundos (Groq com peso duplo).\n"
        "  [cyan]/sintese[/cyan]     - Gemini assume a liderança e fecha o ciclo rapidamente.\n"
        "  [cyan]/debate[/cyan]     - Debate estruturado de uma única volta.\n"
        "  [cyan]/livre[/cyan]      - Exploração solta sem formato forçado.\n"
        "  [cyan]/curto[/cyan]      - Resposta expressa em 2-3 frases.\n"
        "  [cyan]/codigo[/cyan]     - Foco em arquitetura de código e blocos técnicos.\n"
        "  [cyan]/explica[/cyan]    - Modo pedagógico passo a passo.\n"
        "  [cyan]/revisa[/cyan]     - Foco em auditoria de estilo e estrutura (Groq lidera).\n"
        "  [cyan]/brainstorm[/cyan] - Ideação pura sem críticas bloqueantes.\n"
        "  [cyan]/decide[/cyan]     - Apoio estruturado a escolhas técnicas.\n\n"
        "[bold yellow]Comandos do Sistema:[/bold yellow]\n"
        "  [cyan]/ajuda ou /help[/cyan] - Exibe este manual.\n"
        "  [cyan]sair / exit / quit[/cyan] - Encerra a malha de agentes com segurança.\n"
        "  [cyan]/pensamento[/cyan] - Alterna a exibição do pensamento interno (originalmente oculto).\n"
    )
    
    painel_ajuda = Panel(
        ajuda_texto,
        border_style="yellow",
        title="[bold white] PAPINHO // MANUAL DE COMANDOS [/bold white]",
        title_align="left",
        padding=(1, 2)
    )
    console.print(painel_ajuda)


async def loop_conversa(bus: MessageBus) -> None:
    """Loop principal: lê a entrada do usuário, filtra comandos/modos e publica a mensagem."""
    while True:
        await bus.turno_encerrado.wait()

        entrada = (await asyncio.to_thread(console.input, "\n[bold green]Você:[/bold green] ")).strip()

        if entrada.lower() in COMANDOS_SAIDA:
            break
        if not entrada:
            continue

        modo_ativo = "padrao"
        texto_usuario = entrada

        if entrada.startswith("/"):
            partes = entrada.split(" ", 1)
            comando = partes[0].lower()

            if entrada.startswith("/pensamento"):
                global EXIBIR_PENSAMENTO
                EXIBIR_PENSAMENTO = not EXIBIR_PENSAMENTO
                status = "ATIVADA" if EXIBIR_PENSAMENTO else "DESATIVADA"
                console.print(f"[bold yellow] [Sistema]: Exibição de pensamento interno {status}.[/bold yellow]")
                continue

            if comando in ("/ajuda", "/help"):
                _exibir_ajuda()
                continue

            if comando in MODOS_DE_CONVERSA:
                modo_ativo = comando
                texto_usuario = partes[1] if len(partes) > 1 else "Inicie a análise com base no nosso contexto e regras."
            else:
                modos_validos = ', '.join(k for k in MODOS_DE_CONVERSA if k != 'padrao')
                print(f"⚠️ [Sistema]: Modo não reconhecido. Use: {modos_validos} ou digite normalmente para Força-Tarefa.")
                continue

        diretriz = MODOS_DE_CONVERSA[modo_ativo]

        if not any(nome.lower() in texto_usuario.lower() for nome in NOMES_DEBATEDORES):
            texto_usuario = f"Qwen, inicie a análise: {texto_usuario}"

        mensagem_final = f"{texto_usuario}\n\n[{diretriz}]"

        mensagem = Message(
            sender="User",
            role="user",
            content=mensagem_final,
        )

        bus.publish(mensagem)

        bus.turno_encerrado.clear()
        asyncio.create_task(animacao_carregamento(bus))


async def main() -> None:
    print("Iniciando o Orquestrador de Agentes...")
    bus = MessageBus()
    bus.bastao = asyncio.Lock()
    bus.turno_encerrado = asyncio.Event()
    bus.turno_encerrado.set()

    agentes = criar_agentes(bus)

    await asyncio.gather(*(agente.start() for agente in agentes.values()))
    asyncio.create_task(display_messages(bus))

    _exibir_boas_vindas()

    try:
        await loop_conversa(bus)
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ Interrompido pelo usuário (Ctrl+C).[/yellow]")
    finally:
        console.print("\n[dim red]Encerrando malha de agentes e liberando recursos...[/dim red]")
        await asyncio.gather(*(agente.stop() for agente in agentes.values()))
        console.print("[bold green]✓ Chat encerrado com sucesso.[/bold green]\n")


if __name__ == "__main__":
    asyncio.run(main())