import asyncio
from orchestrator.bus import MessageBus
from orchestrator.models import Message
from orchestrator.agents.llm_agent import LLMAgent
from logger_agent import LoggerAgent
from orchestrator.agents.gemini_agent import GeminiAgent

async def display_messages(bus: MessageBus):
    """Fica ouvindo o barramento e imprime as mensagens na tela."""
    async for msg in bus.subscribe():
        # Ignora mensagens de sistema para o chat ficar mais limpo
        if msg.role == "system":
            continue
        # Se não for você (User) quem mandou, imprime na tela
        if msg.sender != "User":
            print(f"\n[{msg.sender}]: {msg.content}")

async def main():
    print("Iniciando o Orquestrador de Agentes...")
    bus = MessageBus()

    # 1. ADICIONANDO OS AGENTES
    
    llama_bot = LLMAgent(
        name="Llama", 
        persona="Você é um assistente criativo de negócios. Dê ideias geniais, diretas e curtas. "
                "REGRA DE OURO: O seu trabalho é apenas criar e debater. Toda vez que você falar, você DEVE terminar a sua resposta com a pergunta EXATA: 'O que você acha disso, Revisor?'. "
                "NUNCA encerre a conversa e nunca diga que a ideia foi finalizada. Deixe o fechamento para a equipe de marketing.", 
        bus=bus
    )
    
    revisor_bot = LLMAgent(
        name="Revisor", 
        persona="Você é o Revisor. Sua única função é criticar as ideias do Llama para melhorá-las. "
                "REGRA DE OURO 1: Se a ideia ainda precisar de ajustes, devolva a bola para o criador terminando sua resposta EXATAMENTE com a frase: 'O que você acha da minha crítica, Llama?'. NUNCA faça perguntas a si mesmo (Revisor). "
                "REGRA DE OURO 2: Quando a ideia estiver excelente e sem falhas, encerre a sua participação dizendo APENAS a frase exata: 'APROVADO! Passo a bola para o Gemini.'", 
        bus=bus
    )

    gemini_bot = GeminiAgent(
        name="Gemini",
        persona="Você é a inteligência artificial Gemini 1.5 Flash. Você SÓ vai falar quando for chamado. Quando o Revisor aprovar a ideia, sua função é ler a ideia final e criar: 1) Um NOME comercial incrível, 2) Um SLOGAN chiclete, 3) Uma estratégia de vendas de 2 linhas. Termine sua resposta dizendo apenas 'Reunião encerrada!'. NÃO faça perguntas no final.",
        bus=bus
    )

    logger_bot = LoggerAgent(name="Logger", bus=bus, arquivo_log="minhas_ideias.txt")

    await llama_bot.start()
    await revisor_bot.start()
    await gemini_bot.start() # Ligando o Gemini
    await logger_bot.start()

    # 2. INICIA O OUVINTE NA TELA
    # Isso roda em background para mostrar as mensagens das IAs
    asyncio.create_task(display_messages(bus))

    print("=================================================")
    print("Chat iniciado! Digite sua mensagem e aperte Enter.")
    print("Para sair, digite 'sair' ou 'exit'.")
    print("=================================================\n")

    # 3. O SEU LOOP DE ENTRADA NA CONVERSA
    while True:
        # Pede para você digitar no terminal (rodando em uma thread separada para não travar o async)
        user_input = await asyncio.to_thread(input, "Você: ")
        
        if user_input.lower() in ['sair', 'exit', 'quit']:
            break
            
        # Publica a sua mensagem no barramento para os agentes lerem
        nova_mensagem = Message(sender="User", role="user", content=user_input)
        bus.publish(nova_mensagem)
        
        # Dá tempo suficiente para os agentes pensarem e imprimirem as respostas na tela
        # antes de mostrar o próximo "Você: "
        await asyncio.sleep(1.5)

    # 4. DESLIGANDO TUDO
    print("\nEncerrando agentes...")
    await llama_bot.stop()
    await revisor_bot.stop()
    await gemini_bot.stop() # Desligando o Gemini
    await logger_bot.stop()
    print("Chat encerrado!")

if __name__ == "__main__":
    asyncio.run(main())