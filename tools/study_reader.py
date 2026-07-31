"""PDF를 Claude에게 보여주고 요약·답변을 받아온다.

왜 도구가 직접 API를 부르는가:
  tool_result 에는 문서 블록을 넣을 수 없다 (텍스트와 이미지만 된다).
  그래서 "PDF를 비서에게 건네준다"를 도구 반환값으로는 할 수 없다.
  대신 이 도구가 자기 요청을 만들어 PDF를 붙이고, 받은 답을 글로 돌려준다.

왜 글자를 뽑지 않는가:
  API는 각 페이지를 이미지로 바꿔 텍스트와 함께 모델에 준다. 그림·수식·표를
  본다. pypdf 같은 것으로 글자만 뽑으면 그게 전부 사라진다.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic

from assistant.config import Settings
from tools.study_materials import MAX_PDF_BYTES, StudyError

MAX_TOKENS = 8000

_SUMMARY_PROMPT = """이 자료를 사장님이 나중에 다시 읽을 수 있게 정리해 주세요.

반드시 아래 형태의 JSON만 출력하세요. 다른 말을 붙이지 마세요.

{
  "title": "자료 제목 (파일 이름이 아니라 내용상의 제목)",
  "one_line": "이 자료가 무슨 얘기인지 한 문장",
  "note_body": "마크다운 본문"
}

note_body 에는 다음 두 부분을 넣으세요.

## 핵심
- 요점을 항목으로. 원본을 다시 안 열어도 될 만큼 담되, 옮겨 적지는 마세요.

## 쉬운 말로
어려운 대목을 풀어서 설명합니다. 그림이나 수식이 있으면 그것이 무엇을
말하는지도 우리말로 적어주세요.
"""


def _client(settings: Settings, client):
    return client or anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _document_block(pdf_path: Path) -> dict:
    """PDF를 문서 블록으로. 줄바꿈 없는 base64여야 API가 받는다."""
    if not pdf_path.exists():
        raise StudyError(f"파일을 찾지 못했습니다: {pdf_path.name}")

    size = pdf_path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise StudyError(
            f"{pdf_path.name}은 너무 큽니다 ({size / 1024 / 1024:.0f}MB). "
            "한 번에 보낼 수 있는 크기는 32MB입니다. 자료를 나눠서 넣어주세요."
        )

    encoded = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": encoded,
        },
    }


def _text_of(message) -> str:
    return "".join(
        block.text for block in message.content
        if getattr(block, "type", "") == "text"
    )


def _explain(exc: Exception) -> str:
    """API 오류를 사장님이 읽고 조치할 수 있는 말로."""
    text = str(exc)
    if "credit balance is too low" in text:
        return ("Claude API 크레딧이 떨어졌습니다. "
                "console.anthropic.com → Plans & Billing에서 충전해 주세요.")
    if "too many pages" in text or "600" in text:
        return "쪽수가 너무 많습니다. 한 번에 600쪽까지만 읽을 수 있습니다."
    if "encrypted" in text or "password" in text:
        return "암호가 걸린 PDF는 읽을 수 없습니다. 암호를 푼 뒤 다시 넣어주세요."
    return f"자료를 읽는 중 문제가 생겼습니다: {text}"


def _ask(settings: Settings, pdf_path: Path, question: str, client) -> str:
    document = _document_block(pdf_path)   # 보내기 전에 크기를 막는다
    try:
        message = _client(settings, client).messages.create(
            model=settings.model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": settings.effort},
            messages=[{
                "role": "user",
                # 문서를 텍스트보다 앞에 두는 것이 API 권고사항이다.
                "content": [document, {"type": "text", "text": question}],
            }],
        )
    except Exception as exc:   # noqa: BLE001 — 사장님께 옮겨 전한다
        raise StudyError(_explain(exc)) from exc
    return _text_of(message).strip()


def summarize_pdf(settings: Settings, pdf_path: Path, client=None) -> dict:
    """자료를 읽고 제목·한 줄 요약·본문을 돌려준다."""
    answer = _ask(settings, pdf_path, _SUMMARY_PROMPT, client)

    cleaned = answer.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        data = json.loads(cleaned)
        return {
            "title": str(data["title"]).strip(),
            "one_line": str(data["one_line"]).strip(),
            "note_body": str(data["note_body"]).strip(),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StudyError(
            f"{pdf_path.name} 요약을 정리된 형태로 받지 못했습니다. "
            "다시 시도해 주세요."
        ) from exc


def ask_pdf(settings: Settings, pdf_path: Path, question: str,
            client=None) -> str:
    """보관된 원본을 펼쳐 세부 질문에 답한다."""
    return _ask(settings, pdf_path, question, client)
