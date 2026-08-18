"""옵시디언 볼트 쓰기 — 비서가 노트를 만들고 덧붙인다.

두 가지만 할 수 있다: **새 노트 만들기**와 **맨 뒤에 덧붙이기**.
기존 본문을 고치거나 지우는 함수는 여기 없다 — 모델이 판단을 틀려도
사장님이 직접 쓴 글자가 사라지지 않게 하려는 것이다.

쓰는 곳은 볼트의 `비서/` 폴더 하나뿐이다(`settings.assistant_notes_dir`).
경로 검증은 읽기와 같은 `obsidian_search.resolve_in_vault`를 쓰고,
그 위에 "이 폴더 안인가"를 한 겹 더 얹는다.

모든 쓰기는 `assistant_notes.record_audit`에 한 줄씩 남는다.
"""

from __future__ import annotations

from pathlib import Path

from assistant.config import Settings
from tools import assistant_notes
from tools.obsidian_search import SearchError, resolve_in_vault

# 윈도우가 파일 이름에 못 쓰는 글자 + 경로 구분자. 폴더를 파고들지 못하게
# 구분자도 같이 막는다 — 비서 노트는 한 폴더에 평평하게 쌓인다.
BAD_NAME_CHARS = frozenset('<>:"/\\|?*')


class WriteError(RuntimeError):
    """노트를 쓰지 못했을 때. 메시지는 사장님께 그대로 보여준다."""


def _notes_dir(settings: Settings) -> Path:
    path = settings.assistant_notes_dir
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _rel(settings: Settings, target: Path) -> str:
    return target.relative_to(settings.obsidian_vault.resolve()).as_posix()


def _resolve_writable(
    settings: Settings, note_path: str, must_exist: bool
) -> Path:
    """볼트 검증을 통과시킨 뒤, 비서 폴더 안인지 한 번 더 본다."""
    notes_dir = _notes_dir(settings)
    try:
        target = resolve_in_vault(settings, note_path, must_exist=must_exist)
    except SearchError as exc:
        raise WriteError(str(exc)) from exc

    if not target.is_relative_to(notes_dir):
        raise WriteError(
            f"'{notes_dir.name}' 폴더 안에만 쓸 수 있습니다: {note_path}"
        )
    return target


def create_note(settings: Settings, name: str, text: str) -> dict:
    """비서 폴더에 새 노트를 만든다. 같은 이름이 있으면 거절한다.

    덮어쓰기를 허용하지 않는 이유가 전부다 — 이 함수는 무엇도 지우지 않는다.
    """
    title = name.strip().removesuffix(".md").strip()
    if not title:
        raise WriteError("노트 이름이 비어 있습니다.")
    if bad := sorted(set(title) & BAD_NAME_CHARS):
        raise WriteError(f"노트 이름에 쓸 수 없는 글자입니다: {' '.join(bad)}")

    body = text.strip()
    if not body:
        raise WriteError("노트 내용이 비어 있습니다.")

    folder = settings.assistant_notes_dir.name
    target = _resolve_writable(settings, f"{folder}/{title}.md", must_exist=False)
    content = f"# {title}\n\n{body}\n"

    try:
        # "x"는 파일이 없을 때만 연다. 존재 확인과 생성 사이에 끼어들 틈이 없다.
        with target.open("x", encoding="utf-8") as fh:
            fh.write(content)
    except FileExistsError:
        raise WriteError(
            f"같은 이름의 노트가 이미 있습니다: {title}. "
            "덧붙이려면 append_note를 쓰십시오."
        ) from None
    except OSError as exc:
        raise WriteError(f"노트를 저장하지 못했습니다: {exc}") from exc

    rel = _rel(settings, target)
    assistant_notes.record_audit(settings, "note_create", f"{rel}: {body[:60]}")
    return {"path": rel, "title": title, "created": True}


def append_note(
    settings: Settings, note_path: str, text: str, heading: str = ""
) -> dict:
    """기존 노트 맨 뒤에 덧붙인다. 위의 내용은 손대지 않는다.

    Args:
        note_path: search_notes가 알려준 볼트 기준 경로.
        heading: 붙일 덩어리의 소제목. 비우면 날짜만 적는다.
    """
    body = text.strip()
    if not body:
        raise WriteError("덧붙일 내용이 비어 있습니다.")

    target = _resolve_writable(settings, note_path, must_exist=True)
    stamp = assistant_notes.now_kst().strftime("%Y-%m-%d %H:%M")
    title = f"{stamp} {heading.strip()}".strip()
    block = f"\n\n## {title}\n\n{body}\n"

    try:
        with target.open("a", encoding="utf-8") as fh:
            fh.write(block)
    except OSError as exc:
        raise WriteError(f"노트에 덧붙이지 못했습니다: {exc}") from exc

    rel = _rel(settings, target)
    assistant_notes.record_audit(settings, "note_append", f"{rel}: {body[:60]}")
    return {"path": rel, "appended": True, "heading": title}
