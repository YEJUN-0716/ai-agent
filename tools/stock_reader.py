"""stock-analyzer 결과를 읽는 유일한 창구. 절대 쓰지 않는다.

stock-analyzer 구조가 바뀌면 이 파일만 고치면 된다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from assistant.config import Settings
from tools import stock_sync, virtual_trade


class StockDataError(RuntimeError):
    """주식 데이터를 읽지 못했을 때. 메시지는 사용자에게 그대로 보여준다."""


# 현재가는 질문할 때마다 새로 받을 만큼 급하게 변하지 않는다. 10분 재사용한다.
_PRICE_TTL_SEC = 600
_price_cache: dict[str, tuple[float, float]] = {}  # 종목 -> (달러 종가, 받은 시각)


def _last_price_usd(broker, symbol: str) -> float:
    """최근 종가(달러). 못 받으면 0.0.

    ponytail: 종목당 한 번씩 조회한다(10종목이면 조회 10번). 보유 종목이
    수십 개로 늘어 답이 느려지면 한 번에 받아오는 방식으로 바꾼다.
    """
    hit = _price_cache.get(symbol)
    now = time.monotonic()
    if hit is not None and now - hit[1] < _PRICE_TTL_SEC:
        return hit[0]

    try:
        price = float(broker.last_close_price(symbol))
    except Exception:  # noqa: BLE001 — 한 종목 때문에 전체 답을 잃지 않는다
        return 0.0
    if price > 0:
        _price_cache[symbol] = (price, now)
    return price


def _value_positions(settings: Settings, cash_krw: float | None, positions: dict) -> dict:
    """보유 종목의 평가금액·평가손익을 원화로 계산한다.

    현재가를 못 받은 종목은 0원으로 치지 않는다. 0으로 두면 전액 손실처럼
    보이고 총자산도 그만큼 줄어, 사장님이 있지도 않은 손실을 보게 된다.
    빼놓고 계산한 뒤 어느 종목이 빠졌는지 함께 알린다.
    """
    broker_file = settings.stock_analyzer_path / "modules" / "virtual_broker.py"
    if not positions or not broker_file.exists():
        return {}

    try:
        broker = virtual_trade.broker_module(settings)
    except virtual_trade.TradeError as exc:
        return {"valuation_note": f"평가금액을 계산하지 못했습니다: {exc}"}

    fx = broker.krw_per_usd()
    valued: dict[str, dict] = {}
    unpriced: list[str] = []
    stock_value_krw = 0.0
    unrealized_krw = 0.0

    for symbol, position in positions.items():
        row = dict(position)
        qty = float(position.get("qty", 0))
        avg_usd = float(position.get("avg_price_usd", 0.0))
        price_usd = _last_price_usd(broker, symbol)

        if price_usd <= 0:
            unpriced.append(symbol)
            row["valuation"] = "현재가를 받지 못해 평가금액을 내지 못했습니다"
        else:
            value_krw = qty * price_usd * fx
            cost_krw = qty * avg_usd * fx
            row["current_price_usd"] = round(price_usd, 4)
            row["market_value_krw"] = round(value_krw)
            row["unrealized_pnl_krw"] = round(value_krw - cost_krw)
            row["unrealized_pnl_pct"] = (
                round((price_usd / avg_usd - 1) * 100, 2) if avg_usd else None
            )
            stock_value_krw += value_krw
            unrealized_krw += value_krw - cost_krw

        valued[symbol] = row

    result: dict[str, Any] = {
        "positions": valued,
        "stock_value_krw": round(stock_value_krw),
        "unrealized_pnl_krw": round(unrealized_krw),
        "fx_krw_per_usd": round(fx, 2),
    }
    if cash_krw is not None:
        result["total_equity_krw"] = round(cash_krw + stock_value_krw)
    if unpriced:
        result["valuation_note"] = (
            f"현재가를 받지 못한 종목은 평가금액에서 빠졌습니다: "
            f"{', '.join(unpriced)}"
        )
    return result


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


def _broker_or_none(settings: Settings):
    """stock-analyzer 브로커 모듈. 못 불러오면 None.

    현금·자본곡선 계산을 여기서 새로 쓰지 않고 브로커 것을 그대로 부르기
    위해 쓴다. 같은 계산의 사본이 두 개면 한쪽만 고쳐진다.
    """
    if not (settings.stock_analyzer_path / "modules" / "virtual_broker.py").exists():
        return None
    try:
        return virtual_trade.broker_module(settings)
    except virtual_trade.TradeError:
        return None


def get_virtual_portfolio(settings: Settings) -> dict:
    """가상 브로커 보유 현황. 아직 한 번도 안 돌았으면 started=False.

    as_of는 데이터가 언제 것인지, sync_note는 최신화에 실패했을 때의 이유다.
    둘 다 답할 때 함께 알려야 지난 숫자를 현재로 착각하지 않는다.
    """
    sync = stock_sync.refresh(settings)
    data = _read_json(settings.stock_analyzer_path / "virtual_portfolio.json")

    if data is None:
        return {
            "started": False,
            "cash_krw": None,
            "positions": {},
            "pending": [],
            "realized_pnl_krw": 0.0,
            "as_of": sync["as_of"],
            "sync_note": sync["note"],
            "note": "가상 브로커가 아직 한 번도 실행되지 않았습니다.",
        }
    cash_krw = data.get("cash_krw")
    positions = data.get("positions", {})
    result = {
        "started": True,
        "cash_krw": cash_krw,
        "positions": positions,
        "pending": data.get("pending", []),
        "realized_pnl_krw": data.get("realized_pnl_krw", 0.0),
        "as_of": sync["as_of"],
        "sync_note": sync["note"],
    }
    # 현금의 상당액이 예약 매수에 묶여 있다(2026-08-22 실측 67.5%). cash_krw만
    # 말하면 사장님이 쓸 수 있는 돈을 3.1배로 잡고 계획을 세운다.
    broker = _broker_or_none(settings)
    if broker is not None:
        result["reserved_krw"] = broker.reserved_krw(data)
        result["available_krw"] = broker.available_krw(data)

    # 평가금액·평가손익은 현재가가 있어야 나온다. 못 구하면 그 항목만 빠지고
    # 나머지 답은 그대로 나간다.
    result.update(_value_positions(settings, cash_krw, positions))
    return result


def get_data_freshness(settings: Settings) -> dict:
    """읽고 있는 stock-analyzer 데이터가 언제 것인지 알려준다."""
    return stock_sync.refresh(settings)


def get_equity_history(settings: Settings, limit: int = 30) -> dict:
    """가상 브로커 자본 곡선. 최신 것부터 limit개.

    `equity`는 계좌에 있는 돈이라 입금하면 그냥 오른다 — 2026-08-18 입금
    9,000만원이 곡선에 그대로 얹혀 있어, 그것만 읽으면 수익률이 +878%로
    보인다(진짜는 −0.27%). 그래서 입금을 뺀 곡선(equity_ex_deposit)과 그
    곡선의 총수익률을 함께 낸다. 계산은 브로커의 indexed_equity를 부른다.
    """
    stock_sync.refresh(settings)
    data = _read_json(settings.stock_analyzer_path / "equity_log.json")
    records = (data or {}).get("records", [])
    deposited_krw = float(records[-1].get("deposited") or 0.0) if records else 0.0

    broker = _broker_or_none(settings)
    curve = broker.indexed_equity(records) if broker is not None and records else None

    start = max(len(records) - limit, 0)
    window = []
    for offset, rec in enumerate(records[start:]):
        rec = dict(rec)
        if curve is not None:
            rec["equity_ex_deposit"] = round(curve[start + offset], 2)
        window.append(rec)

    total_return_pct = None
    if curve and curve[0]:
        total_return_pct = round((curve[-1] / curve[0] - 1.0) * 100, 2)

    result = {
        "records": window[::-1],
        "deposited_krw": deposited_krw,
        "total_return_pct": total_return_pct,
    }
    if total_return_pct is None and deposited_krw:
        result["note"] = (
            f"자산에 외부 입금 {deposited_krw:,.0f}원이 포함돼 있습니다. "
            "자산이 늘어난 것을 수익으로 말하면 안 됩니다."
        )
    return result


def get_recent_signals(settings: Settings, limit: int = 10) -> list[dict]:
    """매매 시그널 기록. 최신 것부터 limit개."""
    stock_sync.refresh(settings)
    data = _read_json(settings.stock_analyzer_path / "signal_log.json")
    if data is None:
        return []
    signals = data.get("signals", [])
    return signals[-limit:][::-1]


def get_analyst_scores(settings: Settings, limit: int = 5) -> list[dict]:
    """애널리스트 팀의 종목별 점수 기록. 최신 날짜부터 limit개."""
    stock_sync.refresh(settings)
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
