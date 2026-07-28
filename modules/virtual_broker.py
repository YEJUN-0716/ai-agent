"""
가상 브로커 — 실주문 없이 매매를 장부상으로만 체결한다.

modules/toss_trading.py 와 동일한 시그니처·반환 구조를 제공하므로,
러너 입장에서는 어느 쪽을 쓰든 코드가 갈라지지 않는다. 전략 로직은 하나로
유지하고 계좌만 갈아끼운다.

DRY_RUN 과의 차이:
  DRY_RUN 은 주문 전송만 건너뛰고 계좌·보유종목은 여전히 실제 토스 계좌를
  조회한다. 그래서 "샀다고 가정"해도 장부에 남지 않는다.
  이 모듈은 보유 상태 자체를 파일로 들고 있어 매수·매도가 누적된다.

체결 기준:
  신호는 장 마감 후에 나오므로 당일 종가에는 살 수 없다. 주문은 대기열에
  쌓아두고 **다음 거래일 시가**로 체결한다. 실매매와 비교했을 때 오차가
  가장 작은 보수적 가정이다.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

STATE_FILE = os.environ.get("VIRTUAL_PORTFOLIO_FILE", "virtual_portfolio.json")

# 가상 운용 자금 (원). 실제 계좌와 무관하다.
INITIAL_CAPITAL_KRW = float(os.environ.get("VIRTUAL_CAPITAL_KRW", "10000000"))

# 시가 조회 시 하루치만 받으면 휴장일에 비어버리므로 앞뒤로 여유를 둔다.
_PRICE_LOOKAHEAD_DAYS = 7

# 평가액 환산에 쓰는 원/달러. 러너가 실시간 환율을 받아 set_fx()로 주입한다.
# 주입하지 않으면 이 기본값이 쓰이므로 평가액이 실제와 어긋난다.
_FX = float(os.environ.get("KRW_PER_USD", "1400"))


def set_fx(krw_per_usd: float) -> None:
    global _FX
    _FX = float(krw_per_usd)


# ── 상태 입출력 ────────────────────────────────────────────────────────
def _empty_state() -> dict:
    return {
        "cash_krw":         INITIAL_CAPITAL_KRW,
        "positions":        {},   # symbol -> {qty, avg_price_usd, entry_date}
        "pending":          [],   # 다음 거래일 시가로 체결될 주문
        "realized_pnl_krw": 0.0,
        "trades":           [],   # 체결 이력
    }


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return _empty_state()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [가상] 상태 파일을 읽지 못해 새로 시작합니다: {e}")
        return _empty_state()

    # 필드가 빠진 예전 파일도 무너지지 않게 기본값으로 채운다.
    base = _empty_state()
    base.update({k: v for k, v in state.items() if k in base})
    return base


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── 가격 조회 ──────────────────────────────────────────────────────────
def _fetch_ohlc(symbol: str, start: date, end: date) -> pd.DataFrame:
    """[start, end) 구간의 일봉. 실패 시 빈 DataFrame."""
    try:
        df = yf.download(
            symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False,
            auto_adjust=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"  [가상] 가격 조회 실패 {symbol}: {e}")
        return pd.DataFrame()


def next_open_price(symbol: str, after: date) -> tuple[float, str] | None:
    """`after` 다음 거래일의 시가와 그 날짜를 반환. 아직 열리지 않았으면 None."""
    df = _fetch_ohlc(symbol, after + timedelta(days=1),
                     after + timedelta(days=_PRICE_LOOKAHEAD_DAYS))
    if df.empty or "Open" not in df:
        return None
    row = df.iloc[0]
    return float(row["Open"]), df.index[0].date().isoformat()


def last_close_price(symbol: str) -> float:
    """최근 종가. 평가액 계산용. 실패 시 0.0."""
    today = date.today()
    df = _fetch_ohlc(symbol, today - timedelta(days=10), today + timedelta(days=1))
    if df.empty or "Close" not in df:
        return 0.0
    return float(df["Close"].iloc[-1])


# ── 대기 주문 체결 ─────────────────────────────────────────────────────
def settle_pending(state: dict, fx_krw_per_usd: float) -> dict:
    """
    대기 주문을 다음 거래일 시가로 체결한다.

    아직 그 날이 오지 않은 주문은 대기열에 남겨 다음 실행 때 다시 시도한다.
    """
    remaining = []
    for order in state["pending"]:
        placed = datetime.strptime(order["placed_date"], "%Y-%m-%d").date()
        fill = next_open_price(order["symbol"], placed)
        if fill is None:
            remaining.append(order)   # 아직 장이 안 열림
            continue

        price_usd, fill_date = fill
        if order["side"] == "buy":
            state = _fill_buy(state, order, price_usd, fill_date, fx_krw_per_usd)
        else:
            state = _fill_sell(state, order, price_usd, fill_date, fx_krw_per_usd)

    state["pending"] = remaining
    return state


def _fill_buy(state: dict, order: dict, price_usd: float,
              fill_date: str, fx: float) -> dict:
    """주문금액 안에서 살 수 있는 최대 정수 수량으로 체결. 토스는 소수점 매매 불가."""
    price_krw = price_usd * fx
    qty = int(order["notional_krw"] // price_krw) if price_krw > 0 else 0
    if qty < 1:
        print(f"  [가상] {order['symbol']} 매수 불가 — 1주 가격({price_krw:,.0f}원)이 "
              f"주문금액({order['notional_krw']:,.0f}원)을 초과")
        return state

    cost_krw = qty * price_krw
    if cost_krw > state["cash_krw"]:
        print(f"  [가상] {order['symbol']} 매수 불가 — 현금 부족")
        return state

    sym = order["symbol"]
    prev = state["positions"].get(sym)
    if prev:
        total_qty = prev["qty"] + qty
        avg = (prev["avg_price_usd"] * prev["qty"] + price_usd * qty) / total_qty
        state["positions"][sym] = {**prev, "qty": total_qty, "avg_price_usd": avg}
    else:
        state["positions"][sym] = {
            "qty":           qty,
            "avg_price_usd": price_usd,
            "entry_date":    fill_date,
        }

    state["cash_krw"] -= cost_krw
    state["trades"].append({
        "date": fill_date, "symbol": sym, "side": "buy",
        "qty": qty, "price_usd": round(price_usd, 4),
        "amount_krw": round(cost_krw, 0),
    })
    print(f"  [가상] 매수 체결 {sym} {qty}주 @ ${price_usd:,.2f} ({fill_date} 시가)")
    return state


def _fill_sell(state: dict, order: dict, price_usd: float,
               fill_date: str, fx: float) -> dict:
    sym = order["symbol"]
    pos = state["positions"].get(sym)
    if not pos:
        return state

    qty = min(int(order["qty"]), pos["qty"])
    if qty < 1:
        return state

    proceeds_krw = qty * price_usd * fx
    cost_krw     = qty * pos["avg_price_usd"] * fx
    pnl_krw      = proceeds_krw - cost_krw

    state["cash_krw"] += proceeds_krw
    state["realized_pnl_krw"] += pnl_krw

    if qty >= pos["qty"]:
        del state["positions"][sym]
    else:
        state["positions"][sym] = {**pos, "qty": pos["qty"] - qty}

    state["trades"].append({
        "date": fill_date, "symbol": sym, "side": "sell",
        "qty": qty, "price_usd": round(price_usd, 4),
        "amount_krw": round(proceeds_krw, 0),
        "pnl_krw": round(pnl_krw, 0),
        "return_pct": round((price_usd / pos["avg_price_usd"] - 1) * 100, 2),
    })
    print(f"  [가상] 매도 체결 {sym} {qty}주 @ ${price_usd:,.2f} "
          f"({fill_date} 시가, 손익 {pnl_krw:+,.0f}원)")
    return state


# ── toss_trading 호환 인터페이스 ───────────────────────────────────────
# client_id / client_secret / account_seq 는 시그니처 호환을 위해 받기만 한다.

def get_account(client_id: str = "", client_secret: str = "",
                account_seq: str = "") -> dict:
    state = load_state()
    stock_value = sum(
        pos["qty"] * last_close_price(sym) * _FX
        for sym, pos in state["positions"].items()
    )
    return {
        "equity":          state["cash_krw"] + stock_value,
        "buying_power":    state["cash_krw"],
        "account_blocked": False,
        "trading_blocked": False,
    }


def get_positions(client_id: str = "", client_secret: str = "",
                  account_seq: str = "") -> list:
    state = load_state()
    positions = []
    for sym, pos in state["positions"].items():
        price = last_close_price(sym)
        positions.append({
            "symbol":          sym,
            "qty":             str(pos["qty"]),
            "avg_entry_price": str(pos["avg_price_usd"]),
            "current_price":   str(price),
            "unrealized_pl":   str((price - pos["avg_price_usd"]) * pos["qty"]),
            "_raw":            pos,
        })
    return positions


def place_notional_buy(symbol: str, notional_amount: float,
                       client_id: str = "", client_secret: str = "",
                       account_seq: str = "", market: str = "US",
                       dry_run: bool = False) -> dict:
    """주문을 대기열에 넣는다. 실제 체결은 다음 실행 때 다음 거래일 시가로."""
    state = load_state()
    state["pending"].append({
        "side":         "buy",
        "symbol":       symbol,
        "notional_krw": float(notional_amount),
        "placed_date":  date.today().isoformat(),
        "market":       market,
    })
    save_state(state)
    print(f"  [가상] 매수 예약 {symbol} {notional_amount:,.0f}원 — 다음 거래일 시가 체결")
    return {"ok": True, "id": f"virtual-buy-{symbol}-{date.today().isoformat()}",
            "virtual": True}


def place_market_sell(symbol: str, qty: float,
                      client_id: str = "", client_secret: str = "",
                      account_seq: str = "", market: str = "US",
                      dry_run: bool = False) -> dict:
    state = load_state()
    state["pending"].append({
        "side":        "sell",
        "symbol":      symbol,
        "qty":         int(qty),
        "placed_date": date.today().isoformat(),
        "market":      market,
    })
    save_state(state)
    print(f"  [가상] 매도 예약 {symbol} {int(qty)}주 — 다음 거래일 시가 체결")
    return {"ok": True, "id": f"virtual-sell-{symbol}-{date.today().isoformat()}",
            "virtual": True}


def wait_for_fill(order_id: str, client_id: str = "", client_secret: str = "",
                  account_seq: str = "", timeout: int = 0) -> dict:
    """가상 주문은 다음 거래일 시가에 체결되므로 즉시 체결을 기다리지 않는다."""
    return {"ok": True, "status": "pending_next_open", "virtual": True}
