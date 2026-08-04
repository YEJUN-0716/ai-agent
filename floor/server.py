"""PIXEL TRADING FLOOR 웹 서버.

    python -m floor.server          # http://127.0.0.1:8000/?demo=1

이 PC 안에서만 연다. 사장님 결정이고, 비서 웹 창구(channels/web.py)와 같은 정책이다.
바인딩을 열려면 HOST 를 바꾸는 게 아니라 왜 여는지부터 정해야 한다.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from floor import pipeline, report

# DNS 재바인딩으로 남의 페이지가 이 서버를 대신 부르는 걸 막는다.
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
REPORTS_DIR = BASE_DIR.parent / "reports"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(reports_dir: Path = REPORTS_DIR) -> FastAPI:
    app = FastAPI(title="PIXEL TRADING FLOOR", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def no_cache(request, call_next):
        """이 PC에서만 도는 앱이라 캐시로 얻을 게 없다.

        반대로 화면 파일을 고쳤는데 브라우저가 옛 파일을 붙들고 있으면
        "고쳤는데 안 바뀐다"로 시간을 버린다. 그래서 아예 캐시를 끈다.
        """
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/meta")
    def meta() -> dict:
        return {
            "modes": pipeline.describe_modes(),
            "agents": pipeline.describe_agents(),
        }

    @app.get("/api/analyze")
    def analyze(
        symbol: str = Query(..., min_length=1, max_length=32),
        mode: str = Query("algo", max_length=16),
        demo: int = Query(0),
    ) -> StreamingResponse:
        if demo != 1:
            # 실전 모드는 2단계다. 준비 안 된 걸 조용히 데모로 대체하면
            # 사장님이 합성 숫자를 실측으로 믿게 된다 — 그건 막는다.
            events: Iterator[dict] = iter(
                [
                    {
                        "type": "error",
                        "text": "실전 분석은 아직 준비 중입니다. "
                        "지금은 주소 끝에 ?demo=1 을 붙여 데모 모드로 보세요.",
                    }
                ]
            )
        else:
            events = pipeline.run(symbol, mode, reports_dir=reports_dir)
        return StreamingResponse(
            _sse(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/reports", response_class=HTMLResponse)
    def reports_index() -> HTMLResponse:
        rows = "".join(
            f'<li><a href="/reports/{c.name}">{c.at[:16].replace("T", " ")} · '
            f"{c.symbol} · {c.mode} · <b>{c.action}</b> "
            f"({c.confidence}%)</a></li>"
            for c in report.listing(reports_dir)
        )
        body = rows or "<li>아직 저장된 리포트가 없습니다.</li>"
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>리포트</title>"
            "<style>body{background:#0b0d13;color:#cfd6e6;font-family:ui-monospace,"
            "monospace;max-width:820px;margin:2rem auto;padding:0 1rem;line-height:1.9}"
            "a{color:#8ad;text-decoration:none}a:hover{text-decoration:underline}</style>"
            "<h1>분석 리포트</h1>"
            "<p><a href='/reports/all.zip'>전체 ZIP 다운로드</a> · "
            "<a href='/?demo=1'>플로어로</a></p>"
            f"<ul>{body}</ul>"
        )

    @app.get("/reports/all.zip")
    def reports_zip() -> Response:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            for call in report.listing(reports_dir):
                bundle.writestr(call.name, report.read(reports_dir, call.name))
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="reports.zip"'},
        )

    @app.get("/reports/{name}", response_class=PlainTextResponse)
    def report_one(name: str) -> PlainTextResponse:
        try:
            return PlainTextResponse(report.read(reports_dir, name))
        except report.ReportError as exc:
            return PlainTextResponse(str(exc), status_code=404)

    return app


def main() -> None:
    # 윈도우 콘솔 기본 인코딩이 cp949 라 한글·기호를 찍는 순간 죽는다.
    # 대시 하나를 바꾸는 게 아니라 출력 통로를 UTF-8 로 돌려 다음 것도 막는다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    host = os.getenv("HOST", DEFAULT_HOST)
    port = int(os.getenv("PORT", DEFAULT_PORT))
    print(f"PIXEL TRADING FLOOR — http://{host}:{port}/?demo=1")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
