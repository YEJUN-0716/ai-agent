"""텍스트 파일을 열 때 encoding 을 빼지 않는다 — 저장소 전체를 훑는다.

기본값은 플랫폼이 정한다. 이 PC(Windows)에서는 cp949 다. `signal_log.json`
처럼 한글·이모지가 들어가는 파일을 encoding 없이 다루면 이렇게 된다
(2026-08-23 실제 파일로 재현):

  · 읽기 → UnicodeDecodeError (byte 434, `"action": "🟢 매수"`)
  · 쓰기 → `open(w)` 가 파일을 먼저 자르고 이모지에서 터진다
           → 8,979바이트 30건이 131바이트 깨진 JSON 으로 남는다
  · 그 뒤 러너·워커·비서(전부 utf-8 명시)가 읽으면 0건

같은 파일을 다루는 여섯 곳 중 넷은 이미 `encoding="utf-8"` 이 있었고 앱만
없었다 — *"고친 사본 옆에 안 고친 사본"*. 그래서 파일을 열거하지 않고
디렉터리를 훑는다. 열거하면 새로 생긴 파일이 목록 밖에서 산다.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "graphify-out", "node_modules", "__pycache__", ".tmp", "content"}


def _sources():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP or "test" in rel.name:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _binary(call: ast.Call) -> bool:
    mode = ""
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        mode = str(call.args[1].value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = str(kw.value.value)
    return "b" in mode


def test_no_text_io_without_encoding():
    offenders = []
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", "")
            if name not in {"open", "read_text", "write_text"}:
                continue
            if any(kw.arg == "encoding" for kw in node.keywords):
                continue
            if name == "open" and _binary(node):
                continue
            offenders.append(f"{rel.as_posix()}:{node.lineno} {name}()")
    assert not offenders, (
        "encoding 없이 텍스트 파일을 연다 — 기본값은 플랫폼이 정한다"
        "(이 PC 는 cp949):\n  " + "\n  ".join(offenders)
    )
