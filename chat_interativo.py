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
        persona="Você é um filósofo criativo e sonhador. Você adora propor ideias ousadas, explorar conceitos abstratos e pensar fora da caixa. Você está em uma mesa redonda com o Revisor e o Gemini."
                "Sempre que você terminar o seu raciocínio, você DEVE passar a palavra para um dos seus colegas de debate fazendo uma pergunta direta e citando o nome dele (Llama, Revisor ou Gemini). Nunca termine uma fala sem direcionar a conversa para alguém.",
        bus=bus,
        is_default_responder=True, # Isto significa que ele vai responder mesmo quando não for chamado pelo nome (Anfitrião da conversa).
        model="llama-3.1-8b-instant" # Aqui você pode escolher o modelo que deseja usar.
    )
    
    revisor_bot = LLMAgent(
        name="Revisor", 
        persona="Você é um pragmático realista, cético e focado em fatos. Sua função no debate é apontar furos, trazer a conversa para a realidade e questionar as utopias do Llama ou as estratégias do Gemini."
                "Sempre que você terminar o seu raciocínio, você DEVE passar a palavra para um dos seus colegas de debate fazendo uma pergunta direta e citando o nome dele (Llama, Revisor ou Gemini). Nunca termine uma fala sem direcionar a conversa para alguém.",
        bus=bus,
        model="llama-3.3-70b-versatile"
    )

    gemini_bot = GeminiAgent(
        name="Gemini",
        persona="Você é um estrategista lógico e equilibrado. Você tenta encontrar o meio-termo entre a loucura criativa do Llama e o ceticismo do Revisor, propondo planos práticos e estruturados."
                "Sempre que você terminar o seu raciocínio, você DEVE passar a palavra para um dos seus colegas de debate fazendo uma pergunta direta e citando o nome dele (Llama, Revisor ou Gemini). Nunca termine uma fala sem direcionar a conversa para alguém.",
        bus=bus
        # model="gemini-3.1-flash-lite" (Este é o modelo padrão, mas é possível mudar para outro modelo Gemini em gemini_agente.py).
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