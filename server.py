import asyncio
import json
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# Importando a infraestrutura que você já construiu no PAPINHO
from orchestrator.bus import MessageBus
from orchestrator.models import Message
from chat_interativo import criar_agentes, NOME_GUARDIAO, _ofuscar_nomes

app = FastAPI(title="PAPINHO Backend")

# Instanciamos o Bus e os Agentes globalmente
bus = MessageBus()
agentes = criar_agentes(bus)

def gerar_evento_padrao(tipo: str, session_id: str, payload: dict) -> dict:
    """Gera o envelope JSON exato que o Claude exigiu no Apêndice B."""
    return {
        "schemaVersion": 1,
        "eventId": str(uuid.uuid4()),
        "sessionId": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": tipo,
        "payload": payload
    }

@app.on_event("startup")
async def startup_event():
    """Quando o servidor ligar, liga os agentes em background."""
    print("Iniciando agentes da Mesa Redonda...")
    await asyncio.gather(*(agente.start() for agente in agentes.values()))

@app.on_event("shutdown")
async def shutdown_event():
    """Quando o servidor desligar, mata os processos dos agentes."""
    print("Desligando agentes...")
    await asyncio.gather(*(agente.stop() for agente in agentes.values()))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = "sessao-teste-1" # No futuro, isso virá da URL ou do front
    
    # 1. Envia o evento de boas-vindas que o React espera ler
    await websocket.send_json(
        gerar_evento_padrao("session_created", session_id, {
            "title": "Mesa Redonda Principal",
            "mode": "debate"
        })
    )

    # 2. TAREFA A: Escutar o MessageBus e enviar para o React
    async def escutar_bus_e_enviar_pro_websocket():
        async for msg in bus.subscribe():
            if msg.role == "system" or msg.sender == "User":
                continue
            
            # Pega o tipo de evento do metadata. Se não tiver, trata como mensagem completa padrão.
            metadata = msg.metadata or {}
            tipo_evento = metadata.get("type", "message_received")
            msg_id = metadata.get("msg_id", str(uuid.uuid4()))

            if tipo_evento == "agent_thinking":
                await websocket.send_json(gerar_evento_padrao("agent_thinking", session_id, {
                    "agentId": msg.sender
                }))

            elif tipo_evento == "agent_stream":
                await websocket.send_json(gerar_evento_padrao("agent_stream", session_id, {
                    "agentId": msg.sender,
                    "messageId": msg_id,
                    "delta": msg.content # Envia apenas o novo token
                }))

            elif tipo_evento == "agent_finished":
                # Dispara a conclusão da animação (cursor parar de piscar)
                await websocket.send_json(gerar_evento_padrao("agent_finished", session_id, {
                    "agentId": msg.sender,
                    "messageId": msg_id
                }))
                
                # Adiciona a mensagem finalizada ao histórico do chat
                await websocket.send_json(gerar_evento_padrao("message_received", session_id, {
                    "messageId": msg_id,
                    "clientId": None,
                    "role": "agent",
                    "agentId": msg.sender,
                    "content": msg.content # O conteúdo total do agente
                }))
            
            elif tipo_evento == "error":
                await websocket.send_json(gerar_evento_padrao("error", session_id, {
                    "agentId": msg.sender,
                    "message": msg.content
                }))

    task_bus = asyncio.create_task(escutar_bus_e_enviar_pro_websocket())

    # 3. TAREFA B: Escutar o React e enviar para o MessageBus
    try:
        while True:
            data = await websocket.receive_text()
            requisicao = json.loads(data)
            
            # Se o usuário digitou algo no frontend:
            if requisicao.get("type") == "send_message":
                conteudo_usuario = requisicao.get("content", "")
                
                # Roteia pro Guardião, exatamente como você fazia no terminal
                mensagem = Message(
                    sender="User",
                    role="user",
                    content=f"{NOME_GUARDIAO}, analise esta mensagem: '{_ofuscar_nomes(conteudo_usuario)}'"
                )
                bus.publish(mensagem)
                
    except WebSocketDisconnect:
        print("Cliente (Frontend) desconectado.")
        task_bus.cancel()