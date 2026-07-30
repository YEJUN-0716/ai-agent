"""stock-analyzer 결과를 읽는 유일한 창구. 절대 쓰지 않는다.

stock-analyzer 구조가 바뀌면 이 파일만 고치면 된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant.config import Settings


class StockDataError(RuntimeError):
    """주식 데이터를 읽지 못했을 때. 메시지는 사용자에게 그대로 보여준다."""


def _read_json(path: Path) -> Any | None:
    """파일이 없으면 None, 망가졌으면 StockDataError."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise StockDataError(
            f"{path.name} 파일을 읽지 못했습니다: {exc}"
        ) from exc


def get_virtual_portfolio(settings: Settings) -> dict:
    """가상 브로커 보유 현황. 아직 한 번도 안 돌았으면 started=False."""
    data = _read_json(settings.stock_analyzer_path / "virtual_portfolio.json")
    if data is None:
        return {
            "started": False,
            "cash_krw": None,
            "positions": {},
            "pending": [],
            "realized_pnl_krw": 0.0,
            "note": "가상 브로커가 아직 한 번도 실행되지 않았습니다.",
        }
    return {
        "started": True,
        "cash_krw": data.get("cash_krw"),
        "positions": data.get("positions", {}),
        "pending": data.get("pending", []),
        "realized_pnl_krw": data.get("realized_pnl_krw", 0.0),
    }


def get_equity_history(settings: Settings, limit: int = 30) -> list[dict]:
    """가상 브로커 자본 곡선. 최신 것부터 limit개."""
    data = _read_json(settings.stock_analyzer_path / "equity_log.json")
    if data is None:
        return []
    records = data.get("records", [])
    return records[-limit:][::-1]


def get_recent_signals(settings: Settings, limit: int = 10) -> list[dict]:
    """매매 시그널 기록. 최신 것부터 limit개."""
    data = _read_json(settings.stock_analyzer_path / "signal_log.json")
    if data is None:
        return []
    signals = data.get("signals", [])
    return signals[-limit:][::-1]


def get_analyst_scores(settings: Settings, limit: int = 5) -> list[dict]:
    """애널리스트 팀의 종목별 점수 기록. 최신 날짜부터 limit개."""
    log_dir = settings.stock_analyzer_path / "data" / "analyst_log"
    if not log_dir.is_dir():
        return []

    entries: list[dict] = []
    for path in sorted(log_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise StockDataError(
                f"{path.name} 파일을 읽지 못했습니다: {exc}"
            ) from exc
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # 한 줄이 망가져도 나머지는 살린다.
                continue

    entries.sort(key=lambda e: e.get("date", ""))
    return entries[-limit:][::-1]
