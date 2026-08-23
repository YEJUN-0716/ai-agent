"""PC 브라우저 채팅 창구. localhost에만 바인딩한다.

텔레그램과 같은 두뇌를 쓰므로 대화 기록이 이어진다. 매매 승인은
텔레그램과 마찬가지로 이 계층이 직접 실행한다 — 모델에게는 권한이 없다.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from assistant.config import WEB_CHAT_ID, Settings
from channels.chat import ChatChannel
from tools import virtual_trade

# 이 이름으로 온 요청만 받는다. localhost에만 바인딩해도 이 검사가 없으면
# 사장님이 방문한 악성 사이트가 DNS 재바인딩으로 브라우저를 속여
# 127.0.0.1의 /approve를 대신 호출할 수 있다 — 승인 명령 없이 주문이 나간다.
# 재바인딩은 공격자 도메인 이름을 Host 헤더에 실어 보내므로 여기서 걸린다.
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")

_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>업무 비서</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}
 #log{border:1px solid #ddd;border-radius:8px;padding:1rem;height:60vh;overflow:auto;
      white-space:pre-wrap}
 #log div{margin-bottom:.6rem}
 .me{color:#0a7a4a}.bot{color:#222}
 form{display:flex;gap:.5rem;margin-top:1rem}
 textarea{flex:1;padding:.6rem;font:inherit;height:3.2rem}
 button{padding:.6rem 1.2rem;font:inherit;cursor:pointer}
 button[disabled]{opacity:.5;cursor:default}
</style></head><body>
<h1>업무 비서</h1>
<div id="log"></div>
<form id="f"><textarea id="m" placeholder="무엇이든 물어보세요"></textarea>
<button id="send">보내기</button></form>
<script>
const log=document.getElementById('log');
const send=document.getElementById('send');
function add(cls,text){const d=document.createElement('div');
 d.className=cls;d.textContent=text;log.appendChild(d);
 log.scrollTop=log.scrollHeight;return d;}
document.getElementById('f').onsubmit=async e=>{
 e.preventDefault();const box=document.getElementById('m');
 const text=box.value.trim();if(!text)return;
 add('me','나: '+text);box.value='';send.disabled=true;
 const line=add('bot','비서: 생각 중…');
 try{
  const r=await fetch('/chat',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
  const data=await r.json();
  line.textContent='비서: '+(data.reply||data.detail||'응답 없음');
 }catch(err){
  line.textContent='비서: 서버에 연결하지 못했습니다 ('+err+')';
 }finally{send.disabled=false;box.focus();}
};
</script></body></html>
"""


class ChatRequest(BaseModel):
    message: str


class ApproveRequest(BaseModel):
    request_id: str


def create_app(settings: Settings, brain, trade_executor=None) -> FastAPI:
    app = FastAPI(title="업무 비서")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS)
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _PAGE

    # 텔레그램·디스코드와 같은 창구 로직을 태운다. 직접 brain.ask 로 가면
    # `/승인`·`/거절`·`/도움` 이 전부 질문으로 흘러가는데, 안내문은 먹는다고
    # 말한다 — 웹에서만 승인이 안 되던 이유다.
    channel = ChatChannel(settings, brain, trade_executor, name="web")

    @app.post("/chat")
    async def chat(payload: ChatRequest) -> dict:
        question = payload.message.strip()
        if not question:
            raise HTTPException(status_code=400, detail="메시지가 비어 있습니다.")
        # 두뇌는 동기 코드다. 이벤트 루프를 막지 않도록 스레드로 넘긴다.
        reply = await asyncio.to_thread(channel.handle_text, WEB_CHAT_ID, question)
        return {"reply": reply}

    @app.get("/pending")
    async def pending() -> dict:
        return {"pending": virtual_trade.list_pending_requests(settings)}

    @app.post("/approve")
    async def approve(payload: ApproveRequest) -> dict:
        try:
            result = virtual_trade.approve_request(
                settings, payload.request_id, executor=trade_executor
            )
        except virtual_trade.TradeError as exc:
            return {"reply": str(exc)}
        return {
            "reply": f"{result['summary']}를 예약했습니다. "
                     "다음 거래일 시가에 체결됩니다."
        }

    return app
