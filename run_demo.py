"""
Script de demonstração para executar o orquestrador de agentes.

Exemplo de uso:
    python run_demo.py

Inicia agentes de exemplo, publica mensagens de usuário e deixa eles
interagirem por um tempo configurável.
"""
import asyncio
from orchestrator.bus import MessageBus
from orchestrator.models import Message
from orchestrator.agents import RuleBasedAgent, EchoAgent, LLMAgent

async def run_demo(runtime_seconds: int = 8):
    bus = MessageBus()

    rule_agent = RuleBasedAgent(
        name="RuleBot",
        persona="I detect greetings and help requests.",
        bus=bus,
        rules={"hello": "Hi there! I am RuleBot.", "help": "How can I assist?"}
    )

    echo_agent = EchoAgent(name="Echo", persona="I repeat mentions.", bus=bus)
    llm_agent = LLMAgent(name="LLMStub", persona="Helpful assistant.", bus=bus)

    agents = [rule_agent, echo_agent, llm_agent]

    await asyncio.gather(*(a.start() for a in agents))

    bus.publish(Message(sender="user1", role="user", content="Hello everyone, can you help me?"))
    bus.publish(Message(sender="user2", role="user", content="Hey Echo, what's up?"))
    bus.publish(Message(sender="user3", role="user", content="This is a generic message."))

    await asyncio.sleep(runtime_seconds)

    await asyncio.gather(*(a.stop() for a in agents))

    print("\n=== Conversation history ===")
    for i, m in enumerate(bus.history()):
        ts = m.timestamp.isoformat()
        print(f"{i:02d} [{ts}] {m.sender} ({m.role}): {m.content}")

if __name__ == "__main__":
    asyncio.run(run_demo())
