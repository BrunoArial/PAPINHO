import asyncio
from orchestrator.bus import MessageBus
from orchestrator.models import Message
from orchestrator.agents.echo_agent import EchoAgent
from orchestrator.agents.llm_agent import LLMAgent
from logger_agent import LoggerAgent

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
    echo_bot = EchoAgent(name="Echo", persona="Eu repito nomes.", bus=bus)
    
    groq_bot = LLMAgent(
        name="Groq", 
        persona="Você é um assistente criativo. Dê ideias geniais e curtas. Vocês irão debater até acharem a melhor ideia. REGRA DE OURO: Enquanto estiverem debatendo, termine sua resposta com 'O que você acha disso, Revisor?'. Porém, se o Revisor disser que a ideia está perfeita, você DEVE encerrar a conversa dizendo apenas 'Ideia Finalizada!' e NÃO deve mencionar o nome do Revisor.", 
        bus=bus
    )
    
    # groq
    revisor_bot = LLMAgent(
        name="Revisor", 
        persona="Sua função é analisar ideias e apontar pontos fracos. Vocês irão debater até chegarem na ideia perfeita. REGRA DE OURO: Enquanto a ideia precisar melhorar, termine sua resposta com 'O que você acha da minha crítica, Groq?'. Porém, se você achar que a ideia do Groq ficou excelente e não tem mais o que criticar, diga apenas 'APROVADO!' e NÃO mencione o nome do Groq.", 
        bus=bus
    )

    # O NOSSO NOVO AGENTE FANTASMA!
    logger_bot = LoggerAgent(name="Logger", bus=bus, arquivo_log="minhas_ideias.txt")

    await echo_bot.start()
    await groq_bot.start()
    await revisor_bot.start()
    await logger_bot.start() # Ligando o gravador!

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
    await echo_bot.stop()
    await groq_bot.stop()
    await revisor_bot.stop()
    await logger_bot.stop() # Desligando o gravador
    print("Chat encerrado!")

if __name__ == "__main__":
    asyncio.run(main())