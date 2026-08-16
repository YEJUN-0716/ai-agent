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


def market_date(now: datetime | None = None) -> date:
    """주문을 찍을 기준 날짜 — 러너의 달력이 아니라 **미국 장의 날짜**.

    date.today() 를 쓰면 한국 시각 기준 날짜가 찍힌다. 러너는 미국 장이 닫힌
    직후(21:30 UTC)에 도는데 그 시각은 한국에서 이미 다음 날 새벽 06:30 이다.
    그래서 7/30 장 신호로 낸 주문이 '7/31 주문'으로 기록되고, 체결 기준인
    "다음 거래일 시가"가 7/31 이 아니라 8/3 으로 밀린다 — 늘 하루 늦게 산다.

    실제로 2026-07-30 예약분이 이틀이 지나도 체결되지 않았다. 미국 동부
    날짜로 찍으면 신호가 나온 장 날짜와 일치하고, 바로 다음 시가에 체결된다.
    """
    ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    return ts.tz_convert("America/New_York").date()


def reserved_krw(state: dict) -> float:
    """대기 중인 매수 주문이 이미 붙잡고 있는 현금."""
    return sum(
        float(o.get("notional_krw", 0.0))
        for o in state.get("pending", [])
        if o.get("side") == "buy"
    )


def available_krw(state: dict) -> float:
    """새 주문에 쓸 수 있는 현금. 현금에서 예약분을 뺀 값이다.

    예약은 체결 전까지 현금을 줄이지 않는다. 그래서 예약분을 빼지 않으면
    러너가 매일 같은 1,000만원을 놓고 새로 주문을 내, 예약 합계가 현금을
    넘어선다 — 2026-07-31 에 1,852만원 예약 대 현금 1,000만원이 됐다.
    """
    return float(state.get("cash_krw", 0.0)) - reserved_krw(state)


def krw_per_usd() -> float:
    """현재 환산에 쓰는 원/달러.

    place_notional_buy 의 금액 단위는 시장을 따르므로(미국은 달러), 원화
    금액을 들고 있는 호출자는 넘기기 전에 직접 환산해야 한다. 그때 쓰라고
    공개한다. 모듈 전역 _FX 를 밖에서 몰래 읽으면 set_fx() 로 갱신된 값과
    어긋나기 쉽다.
    """
    return _FX


# ── 상태 입출력 ────────────────────────────────────────────────────────
def _empty_state() -> dict:
    return {
        "cash_krw":         INITIAL_CAPITAL_KRW,
        "positions":        {},   # symbol -> {qty, avg_price_usd, entry_date}
        "pending":          [],   # 다음 거래일 시가로 체결될 주문
        "realized_pnl_krw": 0.0,
        "trades":           [],   # 체결 이력
        # 인덱스 자동운용 러너의 멱등성 기록(입금·배당·보고 월). load_state 가
        # 여기 없는 키를 버리므로 칸을 먼저 만들어 둔다. 스윙 장부에는 빈 dict.
        "index_meta":       {},
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


def daily_bars(symbol: str, since: date, until: date | None = None) -> list[tuple]:
    """[since, until] 일봉을 (날짜, 시, 고, 저, 종) 튜플 목록으로.

    체결·청산 판정을 러너가 도는 순간의 종가가 아니라 **일봉 되짚기**로 하기
    위한 재료다. 주말·공휴일·러너 실패로 며칠 빠져도 결과가 안 바뀐다.
    """
    end = (until or date.today()) + timedelta(days=1)
    df = _fetch_ohlc(symbol, since, end)
    if df.empty or "Open" not in df:
        return []
    cols = df[["Open", "High", "Low", "Close"]].dropna()
    return [
        (idx.date().isoformat(), float(o), float(h), float(lo), float(c))
        for idx, (o, h, lo, c) in zip(cols.index, cols.to_numpy())
    ]


def next_open_price(symbol: str, after: date) -> tuple[float, str] | None:
    """`after` 다음 거래일의 시가와 그 날짜를 반환. 아직 열리지 않았으면 None."""
    df = _fetch_ohlc(symbol, after + timedelta(days=1),
                     after + timedelta(days=_PRICE_LOOKAHEAD_DAYS))
    if df.empty or "Open" not in df:
        return None
    row = df.iloc[0]
    return float(row["Open"]), df.index[0].date().isoformat()


def last_close_price(symbol: str) -> float:
    """최근 종가. 평가액 계산용. 실패 시 0.0.

    값이 있는 마지막 행을 집는다. 장이 열리기 전이나 휴장일에는 오늘 행이
    종가 없이(NaN) 먼저 생기는데, 그대로 마지막 행을 집으면 NaN 이 나오고
    그 하나가 평가액·총자산까지 통째로 nan 으로 만든다. 2026-08-04 아침
    보고서가 실제로 "총 자산 nan원"을 찍었다.
    """
    today = date.today()
    df = _fetch_ohlc(symbol, today - timedelta(days=10), today + timedelta(days=1))
    if df.empty or "Close" not in df:
        return 0.0
    closes = df["Close"].dropna()
    return float(closes.iloc[-1]) if not closes.empty else 0.0


# ── 트레이드 플랜 주문 ─────────────────────────────────────────────────
# 백테스트(modules/trade_plan_backtest)가 채점한 규칙 그대로다.
# 숫자가 갈라지면 장부 성적을 백테스트와 같은 단위로 비교할 수 없다.
#
# 2026-08-12 정정: 그 백테스트의 +0.66R 은 걸 수 없는 가격(구간 중간값)에서
# 나온 값이었다. 실제로 걸 수 있는 지정가로 다시 재면 actionable OOS 순평균은
# **+0.022R** 이다 (docs/measurements/2026-08-12-entry-rule-daily.md).
#
# 2026-08-16 해소: `realized_r` 의 분모도 **플랜 위험**(entry_ref - stop)이다.
# 러너가 그 값으로 사이징하므로(plan_position_size) 장부의 R 이 실제 손익과
# 같은 자를 쓴다. 예전엔 체결가로 나눠 손절이 늘 정확히 -1.00R 이었는데,
# 구간 상단에 체결되면 실제 주당 위험이 계획보다 넓어 손실을 과소 기록했다.
LIMIT_FILL_WINDOW = 20    # 진입 구간에 이 거래일 안에 안 닿으면 주문 폐기
PLAN_HOLD_WINDOW  = 40    # 체결 후 이 거래일 안에 손절/목표 안 나면 시가 청산


def scan_limit_fill(bars: list[tuple], limit_price: float,
                    window: int = LIMIT_FILL_WINDOW) -> dict:
    """지정가 진입 판정. bars 는 **주문 다음 거래일부터**의 일봉.

    체결가를 limit_price 로 고정하지 않고 `min(시가, limit_price)` 를 쓴다.
    갭하락으로 진입 구간을 뛰어넘고 열리면 실제로 받는 값은 시가다. 지정가로
    적어두면 장부가 현실보다 좋아진다.

    반환: {"status": "filled"|"expired"|"waiting", ...}
    """
    for day, o, _high, low, _close in bars[:window]:
        if low <= limit_price:
            return {"status": "filled", "date": day, "price": min(o, limit_price)}
    if len(bars) >= window:
        return {"status": "expired"}
    return {"status": "waiting"}


def scan_plan_exit(bars: list[tuple], stop: float, target: float,
                   window: int = PLAN_HOLD_WINDOW) -> dict | None:
    """손절/목표 청산 판정. bars 는 **체결 봉부터**의 일봉. 아직이면 None.

    한 봉에 손절과 목표가 둘 다 걸리면 **손절 우선**이다. 일봉만으로는 어느
    쪽이 먼저였는지 알 수 없으므로 보수적으로 잡는다 (백테스트와 동일).
    """
    for day, o, high, low, _close in bars[:window]:
        if low <= stop:
            return {"outcome": "loss", "date": day, "price": min(o, stop)}
        if high >= target:
            return {"outcome": "win", "date": day, "price": max(o, target)}
    if len(bars) > window:
        day, o = bars[window][0], bars[window][1]
        return {"outcome": "timeout", "date": day, "price": o}
    return None


def realized_r(entry_fill: float, stop: float, exit_price: float,
               entry_ref: float | None = None) -> float:
    """실현 R = (청산가 − 체결가) ÷ **플랜 위험**(entry_ref − 손절가). 롱 기준.

    이 값이 이 장부의 진짜 산출물이다. %수익률과 달리 백테스트의 기대값과
    **같은 단위**라 직접 비교할 수 있다 — 그러려면 분모가 같아야 한다.
    `trade_plan_backtest.placeable_r` 과 같은 자다.

    분자는 실제 체결가, 분모는 플랜 위험이다. 러너는 플랜 위험으로 수량을
    잡으므로(plan_position_size) 1R 의 원화 금액을 정하는 건 플랜 위험이다.
    구간 상단에 체결되면 손절은 -1.00R 보다 **깊다** — 그게 실제 손실이다.

    entry_ref 가 없는 옛 플랜은 체결가로 되돌아간다(2026-08-16 이전 주문).
    """
    risk = (entry_ref if entry_ref else entry_fill) - stop
    if risk <= 0:
        return 0.0
    return (exit_price - entry_fill) / risk


def place_limit_entry(symbol: str, qty: int, limit_price: float, plan: dict,
                      market: str = "US", meta: dict | None = None) -> dict:
    """진입 구간 지정가 매수를 예약한다. LIMIT_FILL_WINDOW 안에 안 닿으면 폐기.

    시가 시장가로 사면 손절폭과 R:R 이 계획과 달라져, 백테스트가 잰 값이
    이 장부에 적용되지 않는다. 그래서 백테스트가 채점한 방식 그대로 기다린다.

    예약 현금은 `qty × limit_price` 로 잡는다. 체결가는 min(시가, 지정가) 라
    이보다 클 수 없으므로 예약이 부족해지는 일은 없다.
    """
    qty = int(qty)
    if qty < 1:
        raise ValueError(f"{symbol} 수량이 1주 미만입니다 (소수점 거래 불가).")

    notional_krw = qty * float(limit_price) * (_FX if market != "KRX" else 1.0)
    state = load_state()
    available = available_krw(state)
    if notional_krw > available:
        raise ValueError(
            f"{symbol} 매수 {notional_krw:,.0f}원을 예약할 수 없습니다 — "
            f"가용 현금 {available:,.0f}원.")

    state["pending"].append({
        "side":         "buy",
        "kind":         "limit_entry",
        "symbol":       symbol,
        "qty":          qty,
        "limit_price":  float(limit_price),
        "notional_krw": notional_krw,
        "placed_date":  market_date().isoformat(),
        "market":       market,
        "plan":         dict(plan),
        "meta":         dict(meta or {}),
    })
    save_state(state)
    print(f"  [가상] 지정가 매수 예약 {symbol} {qty}주 @ ${limit_price:,.2f} "
          f"(손절 ${plan.get('stop', 0):,.2f} / 목표 ${plan.get('target', 0):,.2f}, "
          f"등급 {plan.get('grade', '?')}) — {LIMIT_FILL_WINDOW}거래일 대기")
    return {"ok": True, "id": f"virtual-limit-{symbol}-{market_date().isoformat()}",
            "virtual": True}


def _settle_plan_exits(state: dict, fx: float) -> dict:
    """플랜이 붙은 보유 포지션을 손절/목표/만료로 청산한다.

    체결 봉부터 되짚으므로 러너가 며칠 빠져도 결과가 같다. 반대로 현재
    check_trailing_stops 는 러너가 도는 순간의 종가만 봐서 그 사이의 손절
    이탈을 통째로 놓친다.
    """
    for sym, pos in list(state["positions"].items()):
        plan = pos.get("plan")
        if not plan:
            continue
        entry_date = datetime.strptime(pos["entry_date"], "%Y-%m-%d").date()
        bars = daily_bars(sym, entry_date)
        exit_hit = scan_plan_exit(bars, float(plan["stop"]), float(plan["target"]))
        if exit_hit is None:
            continue

        qty = pos["qty"]
        state = _fill_sell(state, {"symbol": sym, "qty": qty},
                           exit_hit["price"], exit_hit["date"], fx)
        r = realized_r(float(plan["entry_fill"]), float(plan["stop"]),
                       exit_hit["price"], plan.get("entry_ref"))
        state["trades"][-1].update({
            "outcome":    exit_hit["outcome"],
            "r_realized": round(r, 3),
            "grade":      plan.get("grade"),
            "plan":       True,
        })
        print(f"  [가상] 플랜 청산 {sym} {exit_hit['outcome']} "
              f"@ ${exit_hit['price']:,.2f} ({exit_hit['date']}, {r:+.2f}R)")
    return state


def _settle_limit_entry(state: dict, order: dict, fx: float) -> tuple[dict, bool]:
    """(state, 대기열에 남길지) — 체결/폐기되면 False."""
    sym = order["symbol"]
    placed = datetime.strptime(order["placed_date"], "%Y-%m-%d").date()
    bars = daily_bars(sym, placed + timedelta(days=1))
    res = scan_limit_fill(bars, order["limit_price"])

    if res["status"] == "waiting":
        return state, True

    if res["status"] == "expired":
        # 미체결 폐기도 기록에 남긴다. 백테스트에서 셋업의 36% 가 여기로 가므로,
        # 안 남기면 장부 승률이 체결분만 보고 부풀어 보인다.
        state["trades"].append({
            "date": bars[-1][0] if bars else placed.isoformat(),
            "symbol": sym, "side": "nofill", "qty": order["qty"],
            "outcome": "nofill", "r_realized": 0.0,
            "grade": (order.get("plan") or {}).get("grade"), "plan": True,
        })
        print(f"  [가상] 미체결 폐기 {sym} — {LIMIT_FILL_WINDOW}거래일 안에 "
              f"${order['limit_price']:,.2f} 미도달")
        return state, False

    price_usd, fill_date = res["price"], res["date"]
    cost_krw = order["qty"] * price_usd * fx
    if cost_krw > state["cash_krw"]:
        print(f"  [가상] {sym} 매수 불가 — 현금 부족")
        return state, False

    plan = dict(order.get("plan") or {})
    plan["entry_fill"] = round(price_usd, 4)
    state["positions"][sym] = {
        "qty":           order["qty"],
        "avg_price_usd": price_usd,
        "entry_date":    fill_date,
        "plan":          plan,
    }
    state["cash_krw"] -= cost_krw
    state["trades"].append({
        "date": fill_date, "symbol": sym, "side": "buy",
        "qty": order["qty"], "price_usd": round(price_usd, 4),
        "amount_krw": round(cost_krw, 0),
        "meta": dict(order.get("meta") or {}),
        "grade": plan.get("grade"), "stop": plan.get("stop"),
        "target": plan.get("target"), "rr": plan.get("rr"),
        "risk_pct": plan.get("risk_pct"), "plan": True,
    })
    print(f"  [가상] 지정가 체결 {sym} {order['qty']}주 @ ${price_usd:,.2f} ({fill_date})")
    return state, False


# ── 대기 주문 체결 ─────────────────────────────────────────────────────
def settle_pending(state: dict, fx_krw_per_usd: float) -> dict:
    """
    대기 주문을 체결한다. 청산이 **먼저**여야 같은 날 현금·자리가 정확하다.

    kind 가 없는 주문은 예전대로 다음 거래일 시가에 체결한다 — 비서(챗봇)의
    수동 매수가 이 경로를 쓴다.
    """
    state = _settle_plan_exits(state, fx_krw_per_usd)

    remaining = []
    for order in state["pending"]:
        if order.get("kind") == "limit_entry":
            state, keep = _settle_limit_entry(state, order, fx_krw_per_usd)
            if keep:
                remaining.append(order)
            continue

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
    """주문금액 안에서 살 수 있는 최대 정수 수량으로 체결. 토스는 소수점 매매 불가.

    주문에 qty 가 실려 있으면 그 수량 그대로 체결한다. 금액만 넘기면 체결 시가가
    예상보다 낮을 때 계획보다 더 사고, 그 초과분은 다른 자산 몫의 현금이다.
    """
    price_krw = price_usd * fx
    if order.get("qty"):
        qty = int(order["qty"])
    else:
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
        # 주문 시점의 근거(점수·RSI). 성적표가 여기서 읽어 간다.
        "meta": dict(order.get("meta") or {}),
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
        # 매수여력에서 예약분을 뺀다. 예약은 체결 전까지 현금을 줄이지 않으므로,
        # 빼지 않으면 러너가 매일 같은 현금을 놓고 주문을 새로 낸다.
        "buying_power":    available_krw(state),
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
                       dry_run: bool = False,
                       meta: dict | None = None,
                       qty: int | None = None) -> dict:
    """주문을 대기열에 넣는다. 실제 체결은 다음 실행 때 다음 거래일 시가로.

    meta 는 주문 근거(점수·RSI)다. 체결은 다음 거래일에 일어나므로 그 시점에는
    주문 때의 점수를 알 방법이 없다 — 다시 계산한 점수로 채우면 매수 근거가
    아닌 값이 성적표에 박혀 점수-수익률 관계가 조용히 틀어진다. 그래서 주문에
    실어 두고 체결 기록으로 그대로 옮긴다. 성적표의 점수 구간별 분석이 이 값을
    쓴다. toss_trading 도 같은 인자를 받아 무시하므로 러너 코드는 갈라지지 않는다.

    notional_amount 의 단위는 시장을 따른다 — KRX는 원, 그 외는 달러.
    러너와 toss_trading 이 그렇게 넘기기 때문이다. 장부는 원화로만
    기록하므로 여기서 환산한다.

    환산을 빼먹으면 달러 금액이 원화로 둔갑해 주문이 환율 배수만큼
    작아지고, _fill_buy 에서 1주 값보다 작아 영원히 체결되지 않는다.
    실제로 2026-07-30 검증에서 623달러가 623원으로 기록돼 매수 0건이었다.

    반대 방향 실수도 같은 날 나왔다. 원화 금액을 그대로 넘기면 여기서 환율이
    곱해져 50만원이 7억원이 된다. 원화를 들고 있는 호출자는 krw_per_usd() 로
    나눠서 넘길 것.

    qty 를 주면 체결 시가와 무관하게 그 수량으로 체결한다(인덱스 자동운용).
    안 주면 예전대로 주문금액이 허용하는 최대 정수주다.
    """
    amount = float(notional_amount)
    notional_krw = amount if market == "KRX" else amount * _FX

    state = load_state()

    # 없는 돈은 예약하지 않는다.
    #
    # 예약은 체결될 때까지 현금을 줄이지 않는다. 그래서 막지 않으면 러너가
    # 매일 같은 1,000만원을 놓고 주문을 새로 내고, 예약 합계만 불어난다.
    # 그러다 체결 시점에 현금이 모자란 주문은 _fill_buy 가 조용히 버린다 —
    # 사장님은 "예약했습니다"만 듣고 매수는 일어나지 않는다. 여기서 막는다.
    # 러너·비서 모두 이 함수를 지나므로 한 곳만 막으면 된다.
    available = available_krw(state)
    if notional_krw > available:
        raise ValueError(
            f"{symbol} 매수 {notional_krw:,.0f}원을 예약할 수 없습니다 — "
            f"가용 현금 {available:,.0f}원 "
            f"(현금 {state['cash_krw']:,.0f}원 - 예약 {reserved_krw(state):,.0f}원)."
        )

    order = {
        "side":         "buy",
        "symbol":       symbol,
        "notional_krw": notional_krw,
        "placed_date":  market_date().isoformat(),
        "market":       market,
        "meta":         dict(meta or {}),
    }
    if qty is not None:
        order["qty"] = int(qty)   # 안 주면 주문 dict 도 예전과 글자 그대로 같다
    state["pending"].append(order)
    save_state(state)
    print(f"  [가상] 매수 예약 {symbol} {notional_krw:,.0f}원 — 다음 거래일 시가 체결")
    return {"ok": True, "id": f"virtual-buy-{symbol}-{market_date().isoformat()}",
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
        "placed_date": market_date().isoformat(),
        "market":      market,
    })
    save_state(state)
    print(f"  [가상] 매도 예약 {symbol} {int(qty)}주 — 다음 거래일 시가 체결")
    return {"ok": True, "id": f"virtual-sell-{symbol}-{market_date().isoformat()}",
            "virtual": True}


def wait_for_fill(order_id: str, client_id: str = "", client_secret: str = "",
                  account_seq: str = "", timeout: int = 0) -> dict:
    """가상 주문은 다음 거래일 시가에 체결되므로 즉시 체결을 기다리지 않는다."""
    return {"ok": True, "status": "pending_next_open", "virtual": True}
