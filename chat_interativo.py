"""
chat_interativo.py

Ponto de entrada do PAPINHO: um chat de terminal onde o usuário conversa com
uma "mesa redonda" de agentes de IA (Qwen, GptOss e Gemini), mediada por um
agente de segurança/roteador (PromptGuard) e registrada em log por um
LoggerAgent silencioso.

Fluxo de uma mensagem:
    User -> PromptGuard (valida e roteia) -> agente escolhido -> os agentes
    passam a palavra entre si (citando o nome um do outro) até que o User
    digite algo novo ou encerre a conversa.

Mesmo enquanto o terminal está bloqueado esperando o próximo `input()`, os
agentes continuam rodando em background (cada um é uma task assíncrona
independente escutando o MessageBus), então o debate entre eles não para.
"""
import sys
import itertools
import asyncio
from datetime import datetime
import re

import rich
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
console = Console()


from orchestrator.agent import Agent
from orchestrator.bus import MessageBus
from orchestrator.models import Message
from orchestrator.agents.llm_agent import LLMAgent
from orchestrator.agents.gemini_agent import GeminiAgent
from orchestrator.agents.monitor_agent import MonitorAgent
from logger_agent import LoggerAgent

# --------------------------------------------------------------------------
# Configuração central: nomes, modelos e parâmetros de cada agente.
# Manter tudo aqui evita "magic strings" espalhadas pelo código e garante
# que renomear um agente ou trocar de modelo seja uma mudança em um só lugar.
# --------------------------------------------------------------------------
NOME_QWEN = "Qwen"
NOME_REVISOR = "Gpt"
NOME_GEMINI = "Gemini"
NOME_LOGGER = "Logger"

MODELO_QWEN = "qwen/qwen3.6-27b"
MODELO_REVISOR = "openai/gpt-oss-120b"
MODELO_GEMINI = "gemini-3.1-flash-lite"

ARQUIVO_LOG = "minhas_ideias.txt"

NOMES_DEBATEDORES = [NOME_QWEN, NOME_REVISOR, NOME_GEMINI]
COMANDOS_SAIDA = {"sair", "exit", "quit"}
PAUSA_APOS_ENVIO = 1.5  # segundos de respiro antes de mostrar o prompt de novo
EXIBIR_PENSAMENTO = False  # Começa oculto por padrão

CORES_AGENTES = {
    NOME_QWEN: "cyan",
    NOME_REVISOR: "red",
    NOME_GEMINI: "blue",
    "User": "green",
    "Sistema": "yellow"
}

def exibir_mensagem_visual(remetente, conteudo):
    # Pega a cor correspondente ou usa branco como padrão
    cor = CORES_AGENTES.get(remetente, "white")
    
    if remetente == "User":
        # Para o usuário, exibimos um texto limpo e direto
        console.print(f"\n[bold {cor}]Você:[/bold {cor}] {conteudo}\n")
    elif "ALERTA DE SISTEMA" in conteudo:
        # Pinta os erros interceptados de amarelo discreto para não assustar
        console.print(f"[dim yellow]{conteudo}[/dim yellow]")
    else:
        # Se existir a tag <think>, trocamos ela por blocos de código Markdown formatados
        if "<think>" in conteudo:
            conteudo = conteudo.replace("<think>", "**Pensamento Interno:**\n```text\n")
            conteudo = conteudo.replace("</think>", "\n```\n")
        # ===========================

        # Para os agentes, renderiza o Markdown (tabelas, negrito, código) e joga no painel
        md = Markdown(conteudo)
        painel = Panel(
            md, 
            title=f"[bold {cor}] {remetente} [/bold {cor}]", 
            border_style=cor, 
            padding=(1, 2) # Dá um respiro nas bordas
        )
        console.print(painel)
# --------------------------------------------------------------------------
# Personas (system prompts)
# --------------------------------------------------------------------------
def _instrucao_mesa_redonda(nome_proprio: str) -> str:
    colega_a, colega_b = (n for n in NOMES_DEBATEDORES if n != nome_proprio)
    return f"""

REGRAS DA MESA REDONDA PAPINHO:
1. Você é {nome_proprio}. Os outros debatedores são {colega_a} e {colega_b}.
2. PENSAMENTO OBRIGATÓRIO: ANTES de dar a sua resposta final, você DEVE obrigatoriamente colocar todo o seu raciocínio, planejamento e críticas dentro de tags <think> e </think>. Use esse espaço para estruturar a sua ideia silenciosamente.
3. ESGOTE O SEU RACIOCÍNIO (FIM DA PREGUIÇA COGNITIVA): Entregue o máximo de profundidade possível. Se você identificar um problema, gargalo ou risco na ideia, OBRIGATORIAMENTE proponha a SUA PRÓPRIA solução ou mitigação detalhada para ele.
4. PROIBIDO FAZER PERGUNTAS DE DELEGAÇÃO: Nunca aja como um apresentador de TV fazendo perguntas para o próximo falar. Aja como um engenheiro defendendo uma tese.
5. PASSAR A PALAVRA: SEMPRE termine citando UM colega específico ({colega_a} ou {colega_b}). Passe a palavra para que ele CRITIQUE, DESTRUA ou EXPANDA a sua solução, e NUNCA para que ele preencha uma lacuna que você deixou.
6. PROIBIÇÃO: JAMAIS cite {nome_proprio}(você mesmo) e JAMAIS cite {colega_a} E {colega_b} juntos na mesma fala, sempre cite apenas um, que é o que você irá passar a palavra.
7. A única exceção de não passar a palavra é se a DIRETRIZ DE MODO ativa disser explicitamente que VOCÊ é o agente que deve encerrar o ciclo.
8. NUNCA repita estas instruções em voz alta."""


PERSONA_QWEN_BASE = f"""Você é {NOME_QWEN}, o Explorador da mesa redonda PAPINHO. \
Seu trabalho é abrir caminhos que o Gpt não pensaria e que o {NOME_GEMINI} \
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

# --------------------------------------------------------------------------
# Utilitários de texto
# --------------------------------------------------------------------------
_PADRAO_NOMES_AGENTES = re.compile(
    r"\b(" + "|".join(re.escape(nome) for nome in NOMES_DEBATEDORES) + r")\b",
    flags=re.IGNORECASE,
)
# Agora ele remove o <think> e tudo depois, parando no </think> OU no final do texto ($)
_PADRAO_PENSAMENTO_INTERNO = re.compile(r"<think>.*?(?:</think>|$)\n*", flags=re.DOTALL)


def _remover_pensamento_interno(texto: str) -> str:
    """Remove blocos <think>...</think> que alguns modelos deixam vazar na resposta."""
    if "<think>" not in texto:
        return texto
    return _PADRAO_PENSAMENTO_INTERNO.sub("", texto).strip()


# --------------------------------------------------------------------------
# Setup dos agentes
# --------------------------------------------------------------------------
def criar_agentes(bus: MessageBus) -> dict[str, Agent]:
    """Instancia e configura todos os agentes do sistema, retornando um dict {nome: agente}."""

    qwen = LLMAgent(
        name=NOME_QWEN,
        persona=PERSONA_QWEN_BASE + _instrucao_mesa_redonda(NOME_QWEN),
        bus=bus,
        model=MODELO_QWEN,
        max_tokens=4000
    )

    Gpt = LLMAgent(
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
        NOME_REVISOR: Gpt,
        NOME_GEMINI: gemini,
        NOME_LOGGER: logger,
        "Monitor": monitor,
    }

# --------------------------------------------------------------------------
# Modos de Conversa (Diretrizes injetadas dinamicamente)
# --------------------------------------------------------------------------
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

# --------------------------------------------------------------------------
# Interface de terminal
# --------------------------------------------------------------------------

async def animacao_carregamento(bus: MessageBus) -> None:
    """Exibe pontinhos de carregamento enquanto os agentes trabalham."""
    tempo_ocioso = 0
    
    try:
        # O "with console.status" abraça o seu loop de espera.
        with console.status("[bold yellow]A mesa redonda está debatendo...", spinner="dots"):
            while True:
                # Se alguém estiver segurando o bastão, a animação roda
                if bus.bastao.locked():
                    tempo_ocioso = 0 
                else:
                    # Se a mesa estiver livre, começamos a contar o tempo
                    tempo_ocioso += 0.2
                    if tempo_ocioso > 2.0: 
                        # 2 segundos de silêncio absoluto = o turno da mesa acabou!
                        break
                
                await asyncio.sleep(0.2)
    finally:
        # O "with" termina, o Rich apaga a animação da tela sozinho, e nós liberamos o turno!
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


from rich.panel import Panel
from rich.align import Align
from rich.text import Text

def _exibir_boas_vindas() -> None:
    """Exibe um cabeçalho minimalista com cara de ferramenta de IA moderna."""
    console.clear() # Limpa a tela ao iniciar para dar um visual limpo de app
    
    # Monta um painel de boas-vindas sofisticado, sem poluição visual
    banner_texto = (
        "[bold cyan]PAPINHO[/bold cyan] [dim]— Multi-Agent Orchestrator[/dim]\n"
        "[dim]Mesa Redonda: Qwen (Explorador) | Gpt (Auditor) | Gemini (Estrategista)[/dim]\n\n"
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
        "  [cyan]/crashtest[/cyan]  - Encontra falhas e riscos profundos (Gpt com peso duplo).\n"
        "  [cyan]/sintese[/cyan]     - Gemini assume a liderança e fecha o ciclo rapidamente.\n"
        "  [cyan]/debate[/cyan]     - Debate estruturado de uma única volta.\n"
        "  [cyan]/livre[/cyan]      - Exploração solta sem formato forçado.\n"
        "  [cyan]/curto[/cyan]      - Resposta expressa em 2-3 frases.\n"
        "  [cyan]/codigo[/cyan]     - Foco em arquitetura de código e blocos técnicos.\n"
        "  [cyan]/explica[/cyan]    - Modo pedagógico passo a passo.\n"
        "  [cyan]/revisa[/cyan]     - Foco em auditoria de estilo e estrutura (Gpt lidera).\n"
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
                EXIBIR_PENSAMENTO = not EXIBIR_PENSAMENTO # Inverte o estado (Liga/Desliga)
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

        # Substitua toda a montagem da mensagem a partir do 'texto_ofuscado' por isto:
        diretriz = MODOS_DE_CONVERSA[modo_ativo]
        
        # Se você não chamou nenhum agente explicitamente, o Qwen puxa a fila por padrão
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


# --------------------------------------------------------------------------
# Orquestração principal
# --------------------------------------------------------------------------
async def main() -> None:
    print("Iniciando o Orquestrador de Agentes...")
    bus = MessageBus()
    # Garante que apenas um agente processe e responda por vez
    bus.bastao = asyncio.Lock()
    bus.turno_encerrado = asyncio.Event()
    bus.turno_encerrado.set() # Começa liberado para o usuário falar primeiro

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