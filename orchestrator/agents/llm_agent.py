import os
import re
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv
from orchestrator.agent import Agent
from orchestrator.models import Message

load_dotenv()

class LLMAgent(Agent):
    # PASSO 1: Adicionado max_tokens=1500 como parâmetro no __init__
    def __init__(self, name: str, persona: str, bus, is_default_responder: bool = False, model: str = "qwen/qwen3.6-27b", max_tokens: int = 1500):
        super().__init__(name, persona=persona, bus=bus)
        self.is_default_responder = is_default_responder
        self.model = model
        
        # Salvando o max_tokens na classe para usar depois
        self.max_tokens = max_tokens
        
        self.client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
            timeout=30.0
        )
        self.memory = []

    async def on_message(self, message: Message):
        if message.role == "system" or message.sender == self.name:
            return

        # --- NOVO ROTEAMENTO BLINDADO ---
        # Arranca asteriscos (**), vírgulas e qualquer pontuação que possa cegar o rfind
        texto_limpo_para_roteamento = re.sub(r'[^a-zA-Z0-9\s]', '', message.content.lower())

        nomes_agentes = ["qwen", "revisor", "gemini"]
        ultimo_nome = None
        maior_indice = -1

        for nome in nomes_agentes:
            indice = texto_limpo_para_roteamento.rfind(nome)
            if indice > maior_indice:
                maior_indice = indice
                ultimo_nome = nome

        is_addressed_to_me = (ultimo_nome == self.name.lower())

        # DEBUG: sempre printa o que o agente viu, mesmo que desista.
        # Sem isso, "Revisor não respondeu" vira "vai ver o log".
        import sys
        print(
            f"\n[DEBUG-LLM] {self.name} acordou. sender_original={message.sender!r} "
            f"ultimo_nome_detectado={ultimo_nome!r} addressed_to_me={is_addressed_to_me}",
            file=sys.stderr, flush=True,
        )

        if not is_addressed_to_me and not (self.is_default_responder and message.sender == "User"):
            return

        # --- A MÁGICA DO BASTÃO DE FALA (MUTEX) COMEÇA AQUI ---
        async with self.bus.bastao:
            
            # CLAUDE ERROU AQUI: DEVOLVA ESTA TRAVA!
            if self.bus.history() and self.bus.history()[-1] is not message:
                return

            try:
                # Hard-cap assíncrono: 35s. Se a Groq travar sem fechar o
                # socket, asyncio.wait_for levanta TimeoutError em vez de
                # pendurar para sempre. O timeout=30.0 do AsyncOpenAI é
                # só de socket HTTP — não cobre o caso de request aceito
                # mas resposta nunca chegando.
                response_text = await asyncio.wait_for(
                    self._call_llm(message),
                    timeout=35.0,
                )

                # Se a API da Groq censurar ou bugar e devolver None
                if not response_text:
                    raise ValueError("A resposta da API da Groq veio completamente vazia (possível bloqueio de segurança).")

                # Limpa o texto AQUI, antes de publicar para os colegas lerem
                texto_limpo = re.sub(r"<think>.*?(?:</think>|$)\n*", "", response_text, flags=re.DOTALL).strip()

                # TRAVA DO VÁCUO: Se o filtro apagou tudo ou a IA não falou nada útil
                if not texto_limpo:
                    raise ValueError("A IA processou, mas o texto final ficou vazio após a limpeza.")

                # Só publica se tudo deu certo
                nova_mensagem = Message(sender=self.name, role="assistant", content=texto_limpo)
                self.bus.publish(nova_mensagem)

            except asyncio.TimeoutError:
                # ESTE é o erro que estava sumindo sem traceback: a animação
                # antiga matava o turno antes da hora, e sem wait_for este
                # caminho nem era alcançado. Agora o Revisor cai aqui e
                # passa o bastão pro Gemini, que fecha a mesa.
                erro_msg = Message(
                    sender=self.name,
                    role="assistant",
                    content=(
                        f"⏱️ Timeout (35s) na minha API Groq. "
                        f"Gemini, pegue o bastão e continue o debate sem mim "
                        f"— sintetize e feche com [SOLUÇÃO FINAL]."
                    ),
                )
                self.bus.publish(erro_msg)
            except BaseException as e:
                # BaseException pega TUDO, até Timeouts severos e erros de vazia.
                # SEMPRE aponta o fallback pro Gemini, que é quem fecha o
                # ciclo com [SOLUÇÃO FINAL]. Antes, o fallback ia pro Qwen,
                # e se o Qwen também falhasse a mesa ficava órfã.
                erro_msg = Message(
                    sender=self.name,
                    role="assistant",
                    content=(
                        f"❌ Falha na minha API Groq: {type(e).__name__}: {e}\n\n"
                        f"Gemini, minha API falhou ou me deixou no vácuo. "
                        f"Pegue o bastão, sintetize o debate e feche com "
                        f"[SOLUÇÃO FINAL]."
                    ),
                )
                self.bus.publish(erro_msg)

    async def _call_llm(self, message: Message) -> str:
        self.memory.append({"role": "user", "content": message.content})
        if len(self.memory) > 4:
            self.memory = self.memory[-4:]
            
        messages_payload = [{"role": "system", "content": self.persona}] + self.memory

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages_payload,
            temperature=0.7,
            # PASSO 2: Agora ele usa a variável dinâmica definida no agente
            max_tokens=self.max_tokens,
        )
        
        reply_text = response.choices[0].message.content
        self.memory.append({"role": "assistant", "content": reply_text})
        return reply_text