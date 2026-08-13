import os
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from orchestrator.agent import Agent
from orchestrator.models import Message

# Garante que as chaves do .env sejam lidas
load_dotenv()

class GeminiAgent(Agent):
    def __init__(self, name: str, persona: str, bus, model: str = "gemini-3.1-flash-lite"):
        super().__init__(name, persona=persona, bus=bus)
        self.model = model
        # Cliente conectado ao Google
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        # Formas alternativas do nome pelas quais este agente pode ser acordado.
        # Inclui o nome limpo e a versão hifenizada usada por _ofuscar_nomes() em
        # chat_interativo.py (cada letra separada por hífen), porque a delegação
        # do PromptGuard chega ao bus já ofuscada para que o match por substring
        # no caminho de User não burle o guardião. Se o Gemini não reconhecesse
        # a forma hifenizada, nunca acordaria quando roteado diretamente.
        self._aliases = [
            self.name.lower(),
            "-".join(self.name.lower()),  # "gemini" -> "g-e-m-i-n-i"
        ]

    async def on_message(self, message: Message):
        # Ignora mensagens de sistema ou de si mesmo
        if message.role == "system" or message.sender == self.name:
            return

        # Só responde se for chamado pelo nome (limpo OU hifenizado) na conversa
        conteudo_lower = message.content.lower()
        chamado = any(alias in conteudo_lower for alias in self._aliases)

        # DEBUG: sempre printa o que o Gemini viu, mesmo que desista.
        # import sys
        # print(
        #    f"\n[DEBUG-GEMINI] Gemini acordou. sender_original={message.sender!r} "
        #    f"chamado={chamado} content_preview={message.content[:80]!r}",
        #    file=sys.stderr, flush=True,
        #)

        if not chamado:
            return
        # Tenta pegar o bastão. Se outro agente já pegou, ele fica esperando aqui.
        async with self.bus.bastao:
       
            # Quando finalmente pegar o bastão, ele confere a mesa.
            # Se a última mensagem do histórico NÃO for mais a que o acordou,
            # significa que o colega (que pegou o bastão antes) já falou e a conversa andou.
            if self.bus.history()[-1] is not message:
                # Assunto já andou. Vai ficar quieto.
                return 

            try:
                # Guilhotina de 35s também para o Gemini!
                response_text = await asyncio.wait_for(self._call_llm(), timeout=35.0)
                nova_mensagem = Message(sender=self.name, role="assistant", content=response_text)
                self.bus.publish(nova_mensagem)
            except asyncio.TimeoutError:
                msg_bruta = next((m.content for m in self.bus.history() if m.sender == "User"), "")
                # Descasca o envelope do PromptGuard e pega só a pergunta do usuário
                if "analise esta mensagem do usuário: '" in msg_bruta:
                    pedido_original = msg_bruta.split("analise esta mensagem do usuário: '")[1].split("\n\n[")[0]
                else:
                    pedido_original = "o desafio principal."
                
                erro_msg = Message(
                    sender=self.name, 
                    role="assistant", 
                    content=(
                        f"[ALERTA DE SISTEMA: IGNORE O TIMEOUT ABAIXO. NÃO DEBATA SOBRE INFRAESTRUTURA DE API]\n"
                        f"A API do Google sofreu um Timeout (35s).\n\n"
                        f"Gpt, assuma a análise focando EXCLUSIVAMENTE no pedido original: {pedido_original}"
                    )
                )
                self.bus.publish(erro_msg)
                
            except Exception as e:
                msg_bruta = next((m.content for m in self.bus.history() if m.sender == "User"), "")
                if "analise esta mensagem do usuário: '" in msg_bruta:
                    pedido_original = msg_bruta.split("analise esta mensagem do usuário: '")[1].split("\n\n[")[0]
                else:
                    pedido_original = "o desafio principal."
                
                erro_msg = Message(
                    sender=self.name, 
                    role="assistant", 
                    content=(
                        f"[ALERTA DE SISTEMA: IGNORE O ERRO ABAIXO. NÃO DEBATA SOBRE INFRAESTRUTURA DE API]\n"
                        f"Tive um problema na API do Google: {str(e)}\n\n"
                        f"Qwen, minha conexão caiu. Assuma a exploração focando EXCLUSIVAMENTE neste pedido: {pedido_original}"
                    )
                )
                self.bus.publish(erro_msg)

    async def _call_llm(self) -> str:
        # A MÁGICA DA MESA REDONDA ESTÁ AQUI:
        # Ele puxa as últimas 10 mensagens do grupo. Assim ele sabe tudo o que o 
        # Llama e o Revisor debateram, mesmo enquanto ele estava "calado".
        historico_recente = self.bus.history()[-10:]
        
        contexto = "Histórico recente da mesa redonda:\n\n"
        for msg in historico_recente:
            if msg.role != "system":
                contexto += f"[{msg.sender}]: {msg.content}\n"
        
        # O gatilho final que força ele a agir como um debatedor natural
        prompt_final = (
            f"{contexto}\n"
            f"--- \n"
            f"Você (nome: {self.name}) acabou de ser mencionado na conversa acima.\n"
            f"Responda seguindo ESTRITAMENTE a sua persona e continue o debate com a equipe."
        )

        # Configura a personalidade e deixa ele mais criativo (temperatura 0.8)
        config = types.GenerateContentConfig(
            system_instruction=self.persona,
            temperature=0.8,
        )

        # Envia tudo para o Google (usando a chamada stateless correta da nova SDK)
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt_final,
            config=config
        )
        
        return response.text