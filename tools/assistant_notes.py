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


def _load_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 비서 소유 파일이 망가지면 빈 목록으로 새로 시작한다.
        return []
    return data if isinstance(data, list) else []


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
    items = _load_list(path)

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
    items = _load_list(path)
    remaining = [item for item in items if item.get("symbol") != sym]

    if len(remaining) == len(items):
        return {"symbol": sym, "removed": False}

    _save_list(path, remaining)
    record_audit(settings, "watchlist_remove", sym)
    return {"symbol": sym, "removed": True}


def list_watchlist(settings: Settings) -> list[dict]:
    """관심종목 전체."""
    return _load_list(_watchlist_path(settings))


def add_note(settings: Settings, symbol: str, note: str) -> dict:
    """종목에 대한 내 판단·메모를 남긴다."""
    sym = _normalize_symbol(symbol)
    text = note.strip()
    if not text:
        raise ValueError("메모 내용이 비어 있습니다.")

    path = _notes_path(settings)
    items = _load_list(path)
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
    items = _load_list(_notes_path(settings))
    if symbol:
        sym = _normalize_symbol(symbol)
        items = [item for item in items if item.get("symbol") == sym]
    return items[-limit:][::-1]
