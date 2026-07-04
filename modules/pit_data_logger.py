"""
⑤ Point-in-Time(PIT) 재무데이터 로거
=================================================================
yfinance 무료 API는 "지금 이 순간"의 재무데이터만 준다.
이 모듈은 앱이 재무데이터를 가져올 때마다 타임스탬프와 함께 SQLite에
스냅샷을 남겨, 장기적으로 진짜 PIT 백테스트가 가능한 자체 DB를 구축한다.

사용법: fundamental_score() / backtest_factor_strategy() 에서
        yf.Ticker(tk).info 호출 직후 _pit.snapshot(tk, info) 한 줄 추가.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class PITStore:
    DEFAULT_FIELDS = [
        'trailingPE', 'forwardPE', 'priceToBook', 'pegRatio',
        'enterpriseToEbitda', 'returnOnEquity', 'returnOnAssets',
        'profitMargins', 'revenueGrowth', 'earningsGrowth',
        'freeCashflow', 'marketCap', 'debtToEquity', 'currentRatio',
    ]

    def __init__(self, db_path: str = "pit_fundamentals.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pit_snapshots (
                    ticker       TEXT NOT NULL,
                    snapshot_ts  TEXT NOT NULL,
                    field        TEXT NOT NULL,
                    value        REAL,
                    raw_json     TEXT,
                    PRIMARY KEY (ticker, snapshot_ts, field)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pit_ticker_field_ts
                ON pit_snapshots (ticker, field, snapshot_ts)
            """)

    def snapshot(self, ticker: str, info: dict, fields: list = None) -> int:
        """
        yf.Ticker(ticker).info 호출 직후 이 메서드를 같이 호출해서 스냅샷 저장.
        fields=None이면 DEFAULT_FIELDS만 저장 (용량 절약).
        """
        if fields is None:
            fields = self.DEFAULT_FIELDS
        ts = datetime.now(timezone.utc).isoformat()
        raw = json.dumps(info, default=str, ensure_ascii=False)
        rows = []
        for f in fields:
            v = info.get(f)
            if v is None:
                continue
            try:
                rows.append((ticker, ts, f, float(v), raw))
            except (TypeError, ValueError):
                continue

        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO pit_snapshots "
                "(ticker, snapshot_ts, field, value, raw_json) VALUES (?, ?, ?, ?, ?)",
                rows
            )
        return len(rows)

    def get_as_of(self, ticker: str, field: str, as_of: str) -> Optional[float]:
        """
        as_of(ISO8601) 시점에 실제로 알 수 있었던 가장 최근 값 반환.
        as_of 이후 데이터는 절대 참조하지 않는다 — 진짜 point-in-time 조회.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT value FROM pit_snapshots "
                "WHERE ticker=? AND field=? AND snapshot_ts<=? "
                "ORDER BY snapshot_ts DESC LIMIT 1",
                (ticker, field, as_of)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def coverage_report(self, ticker: str = None) -> list:
        """스냅샷 현황 요약: 종목별 최초/최근 시각, 스냅샷 수."""
        query = (
            "SELECT ticker, MIN(snapshot_ts), MAX(snapshot_ts), "
            "COUNT(DISTINCT snapshot_ts) FROM pit_snapshots"
        )
        params = ()
        if ticker:
            query += " WHERE ticker=?"
            params = (ticker,)
        query += " GROUP BY ticker"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {'ticker': r[0], 'first_snapshot': r[1],
             'last_snapshot': r[2], 'n_snapshots': r[3]}
            for r in rows
        ]

    def latest_snapshot_age_days(self, ticker: str) -> Optional[float]:
        """마지막 스냅샷이 몇 일 전인지 반환. 없으면 None."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT MAX(snapshot_ts) FROM pit_snapshots WHERE ticker=?",
                (ticker,)
            )
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        last = datetime.fromisoformat(row[0])
        return (datetime.now(timezone.utc) - last).total_seconds() / 86400


def backtest_ready(store: PITStore, ticker: str, backtest_start: str) -> dict:
    """
    지금 쌓인 PIT 데이터로 backtest_start 시점부터 PIT 백테스트가
    가능한지 진단.
    """
    cov = [c for c in store.coverage_report(ticker) if c['ticker'] == ticker]
    if not cov:
        return {
            'ready': False,
            'reason': f"{ticker}에 대한 스냅샷이 아직 없습니다. 로깅을 시작하세요.",
        }
    first = cov[0]['first_snapshot']
    if first > backtest_start:
        return {
            'ready': False,
            'reason': (
                f"스냅샷은 {first[:10]}부터 시작 — "
                f"{backtest_start} 시점 백테스트에는 아직 못 씀. "
                f"{first[:10]} 이후 구간만 진짜 PIT 백테스트 가능."
            ),
        }
    return {
        'ready': True,
        'reason': f"{first[:10]}부터 데이터 있음 — {backtest_start} 이후 PIT 백테스트 가능.",
    }
