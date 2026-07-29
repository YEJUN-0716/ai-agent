"""비서가 소유하는 관심종목·메모, 그리고 모든 쓰기의 감사 기록.

stock-analyzer에는 저장된 관심종목 목록이 없다 (매일 점수로 자동 산출).
그래서 비서가 자기 파일을 가진다. stock-analyzer 폴더에는 쓰지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assistant.config import Settings

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def load_json_list(settings: Settings, path: Path) -> list[dict]:
    """JSON 리스트 파일을 읽는다. 이 프로젝트의 유일한 손상 파일 정책.

    없으면 조용히 빈 목록. 있는데 못 읽으면 원본을
    `<이름>.<KST타임스탬프>.corrupt`로 옮기고 감사 기록에 남긴 뒤 빈 목록.
    그냥 비우면 다음 쓰기가 덮어써서 기록이 영구히 사라지기 때문이다.

    비서가 소유하는 모든 JSON 리스트 파일(관심종목·메모·대기 중 매매)이
    이 함수를 쓴다. 정책이 갈라지지 않도록 여기 한 곳에만 둔다.
    """
    if not path.exists():
        return []

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        # 읽지도 못하면 옮기는 것도 기대하기 어렵다. 기록만 남긴다.
        record_audit(
            settings, "file_corrupt_failed", f"{path.name}: 읽기 실패 ({exc})"
        )
        return []

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        return data

    # 여기부터는 손상으로 본다 (JSON이 깨졌거나, 리스트가 아니거나).
    _quarantine(settings, path, content)
    return []


def _quarantine(settings: Settings, path: Path, content: str) -> None:
    """망가진 파일을 타임스탬프 붙은 이름으로 치우고 감사 기록에 남긴다.

    마이크로초까지 넣어 같은 초에 두 번 실패해도 백업이 겹치지 않는다.
    원본을 지워야 다음 읽기가 '없는 파일'로 조용히 넘어간다 — 안 지우면
    읽을 때마다 백업이 하나씩 쌓인다.
    """
    stamp = now_kst().strftime("%Y%m%dT%H%M%S.%f%z")
    backup = path.with_name(f"{path.name}.{stamp}.corrupt")

    try:
        backup.write_text(content, encoding="utf-8")
    except OSError as exc:
        record_audit(
            settings, "file_corrupt_failed", f"{path.name}: 보관 실패 ({exc})"
        )
        return

    try:
        path.unlink()
    except OSError as exc:
        record_audit(
            settings,
            "file_corrupt_failed",
            f"{path.name}: {backup.name}에 보관했으나 원본 삭제 실패 ({exc})",
        )
        return

    record_audit(settings, "file_corrupt", f"{path.name} → {backup.name}")


def _save_list(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("종목 코드가 비어 있습니다.")
    return cleaned


def record_audit(settings: Settings, action: str, detail: str) -> None:
    """모든 쓰기 행동을 한 줄씩 남긴다."""
    path = settings.audit_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{now_kst().isoformat(timespec='seconds')} | {action} | {detail}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def read_audit_log(settings: Settings, limit: int = 20) -> list[str]:
    """감사 기록을 최신순으로 limit줄."""
    path = settings.audit_log_path
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines[-limit:][::-1]


def _watchlist_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "watchlist.json"


def _notes_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "notes.json"


def add_watchlist(settings: Settings, symbol: str, reason: str = "") -> dict:
    """관심종목에 추가한다. 이미 있으면 그대로 둔다."""
    sym = _normalize_symbol(symbol)
    path = _watchlist_path(settings)
    items = load_json_list(settings, path)

    if any(item.get("symbol") == sym for item in items):
        return {"symbol": sym, "already_present": True}

    entry = {
        "symbol": sym,
        "added_at": now_kst().date().isoformat(),
        "reason": reason.strip(),
    }
    items.append(entry)
    _save_list(path, items)
    record_audit(settings, "watchlist_add", sym)
    return {"symbol": sym, "already_present": False, "entry": entry}


def remove_watchlist(settings: Settings, symbol: str) -> dict:
    """관심종목에서 뺀다."""
    sym = _normalize_symbol(symbol)
    path = _watchlist_path(settings)
    items = load_json_list(settings, path)
    remaining = [item for item in items if item.get("symbol") != sym]

    if len(remaining) == len(items):
        return {"symbol": sym, "removed": False}

    _save_list(path, remaining)
    record_audit(settings, "watchlist_remove", sym)
    return {"symbol": sym, "removed": True}


def list_watchlist(settings: Settings) -> list[dict]:
    """관심종목 전체."""
    return load_json_list(settings, _watchlist_path(settings))


def add_note(settings: Settings, symbol: str, note: str) -> dict:
    """종목에 대한 내 판단·메모를 남긴다."""
    sym = _normalize_symbol(symbol)
    text = note.strip()
    if not text:
        raise ValueError("메모 내용이 비어 있습니다.")

    path = _notes_path(settings)
    items = load_json_list(settings, path)
    entry = {
        "symbol": sym,
        "note": text,
        "created_at": now_kst().isoformat(timespec="seconds"),
    }
    items.append(entry)
    _save_list(path, items)
    record_audit(settings, "note_add", f"{sym}: {text[:60]}")
    return entry


def list_notes(
    settings: Settings, symbol: str | None = None, limit: int = 20
) -> list[dict]:
    """메모를 최신순으로. symbol을 주면 그 종목만."""
    items = load_json_list(settings, _notes_path(settings))
    if symbol:
        sym = _normalize_symbol(symbol)
        items = [item for item in items if item.get("symbol") == sym]
    return items[-limit:][::-1]
