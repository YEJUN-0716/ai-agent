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
    # tz 없는 datetime 은 tz_convert 가 못 받는다. 러너는 UTC 로 도므로 UTC 로 읽는다.
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
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
        "positions":        {},   # symbol -> {qty, avg_price_usd, avg_price_krw, entry_date}
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
    """`after` 다음 거래일의 시가와 그 날짜를 반환. 아직 열리지 않았으면 None.

    시가가 없는(NaN) 행은 건너뛴다. 장이 열리기 전에는 오늘 행이 값 없이 먼저
    생기는데, 그대로 집으면 NaN 이 체결가가 된다. NaN 은 어떤 비교도 False 라
    `cost > cash` 현금 검사를 그냥 통과해 현금·평단에 NaN 이 영구히 박힌다 —
    last_close_price 가 같은 이유로 dropna 를 쓴다.
    """
    df = _fetch_ohlc(symbol, after + timedelta(days=1),
                     after + timedelta(days=_PRICE_LOOKAHEAD_DAYS))
    if df.empty or "Open" not in df:
        return None
    opens = df["Open"].dropna()
    if opens.empty:
        return None
    return float(opens.iloc[0]), opens.index[0].date().isoformat()


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
                      meta: dict | None = None) -> dict:
    """진입 구간 지정가 매수를 예약한다. LIMIT_FILL_WINDOW 안에 안 닿으면 폐기.

    시가 시장가로 사면 손절폭과 R:R 이 계획과 달라져, 백테스트가 잰 값이
    이 장부에 적용되지 않는다. 그래서 백테스트가 채점한 방식 그대로 기다린다.

    예약 현금은 `qty × limit_price` 로 잡는다. 체결가는 min(시가, 지정가) 라
    이보다 클 수 없으므로 예약이 부족해지는 일은 없다.
    """
    qty = int(qty)
    if qty < 1:
        raise ValueError(f"{symbol} 수량이 1주 미만입니다 (소수점 거래 불가).")

    notional_krw = qty * float(limit_price) * _FX
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
        before = len(state["trades"])
        state = _fill_sell(state, {"symbol": sym, "qty": qty},
                           exit_hit["price"], exit_hit["date"], fx)
        if len(state["trades"]) == before:
            # _fill_sell 이 아무것도 안 팔았다. 그대로 두면 trades[-1] 이
            # 무관한 이전 거래라 거기에 청산 결과가 찍힌다.
            print(f"  [가상] 플랜 청산 실패 {sym} — 보유가 없어 체결되지 않음")
            continue
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
            "limit_price": order["limit_price"],
            "placed_date": order["placed_date"],
            "grade": (order.get("plan") or {}).get("grade"), "plan": True,
        })
        print(f"  [가상] 미체결 폐기 {sym} — {LIMIT_FILL_WINDOW}거래일 안에 "
              f"${order['limit_price']:,.2f} 미도달")
        return state, False

    price_usd, fill_date = res["price"], res["date"]
    cost_krw = order["qty"] * price_usd * fx
    if cost_krw > state["cash_krw"]:
        # 이것도 폐기다. 안 남기면 주문이 흔적 없이 사라져 체결률의 분모가 준다 —
        # 자기 현금 때문에 못 산 걸 "안 걸린 셋업"과 섞으면 안 되므로 사유를 나눈다.
        state["trades"].append({
            "date": fill_date, "symbol": sym, "side": "nofill",
            "qty": order["qty"], "outcome": "cash_short", "r_realized": 0.0,
            "limit_price": order["limit_price"],
            "placed_date": order["placed_date"],
            "grade": (order.get("plan") or {}).get("grade"), "plan": True,
        })
        print(f"  [가상] {sym} 매수 불가 — 현금 부족 (필요 {cost_krw:,.0f}원)")
        return state, False

    plan = dict(order.get("plan") or {})
    plan["entry_fill"] = round(price_usd, 4)
    _add_position(state, sym, order["qty"], price_usd, fill_date, fx, plan)
    state["cash_krw"] -= cost_krw
    state["trades"].append({
        "date": fill_date, "symbol": sym, "side": "buy",
        "qty": order["qty"], "price_usd": round(price_usd, 4),
        "amount_krw": round(cost_krw, 0),
        # 체결되면 대기 주문은 지워진다. 지정가와 예약일을 여기 옮겨 두지 않으면
        # "얼마에 걸어 얼마에 받았나"와 "며칠 기다렸나"를 나중에 되살릴 수 없다.
        "limit_price": order["limit_price"],
        "placed_date": order["placed_date"],
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


def cost_basis_krw(pos: dict, fx: float) -> float:
    """1주당 원화 원가. **산 날의 환율**로 굳은 값이다.

    장부는 원화로 성과를 재는데 사는 건 달러다. 원가를 avg_price_usd × 오늘
    환율로 잡으면 환율이 움직인 만큼이 손익에서 통째로 사라진다 — 달러가
    오르면 실제로는 번 돈인데 장부는 0 으로 적는다.

    avg_price_krw 가 없는 건 이 필드가 생기기 전(2026-08-19)에 산 포지션이다.
    그때 원가를 되살릴 방법이 없으므로 옛 방식대로 환산한다.
    """
    per_share = pos.get("avg_price_krw")
    return float(per_share) if per_share else float(pos["avg_price_usd"]) * fx


def _add_position(state: dict, sym: str, qty: int, price_usd: float,
                  fill_date: str, fx: float, plan: dict | None = None) -> None:
    """보유에 수량을 **더한다**. 체결 경로가 어느 쪽이든 여기로 들어온다.

    예전엔 _fill_buy 만 평단을 합산하고 _settle_limit_entry 는 포지션을 통째로
    대입했다. 이미 들고 있는 종목이 지정가로 또 체결되면 기존 주식이 조용히
    사라지는데 그 현금은 이미 빠져 있다 — check_state 4번 검사는 터진 **뒤에**
    잡을 뿐이라 막지는 못한다. 합산을 한 곳에 모아 경로가 갈라질 수 없게 한다.

    달러 평단과 **원화 평단을 같이** 들고 간다. 원화 쪽이 체결 시점 환율로
    굳은 원가라 매도할 때 환손익이 손익에 남는다(cost_basis_krw).

    plan 은 기존 것을 이긴 적이 없다. 이미 손절·목표가 걸린 포지션에 새 플랜을
    덮으면 들고 있는 주식의 손절선이 소리 없이 바뀐다.
    """
    price_krw = price_usd * fx
    prev = state["positions"].get(sym)
    if prev:
        total_qty = prev["qty"] + qty
        avg = (prev["avg_price_usd"] * prev["qty"] + price_usd * qty) / total_qty
        avg_krw = (cost_basis_krw(prev, fx) * prev["qty"] + price_krw * qty) / total_qty
        merged = {**prev, "qty": total_qty, "avg_price_usd": avg,
                  "avg_price_krw": avg_krw}
        if plan and not prev.get("plan"):
            merged["plan"] = plan
        state["positions"][sym] = merged
        return
    pos = {"qty": qty, "avg_price_usd": price_usd, "avg_price_krw": price_krw,
           "entry_date": fill_date}
    if plan:
        pos["plan"] = plan
    state["positions"][sym] = pos


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
    _add_position(state, sym, qty, price_usd, fill_date, fx)

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
    # 원가는 **산 날의 환율**로 굳은 값이다. 오늘 환율로 다시 환산하면 환손익이
    # 손익에서 통째로 빠진다 — 달러 오른 만큼 번 돈이 장부에 안 남는다.
    cost_krw     = qty * cost_basis_krw(pos, fx)
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
                       account_seq: str = "",
                       dry_run: bool = False,
                       meta: dict | None = None,
                       qty: int | None = None) -> dict:
    """주문을 대기열에 넣는다. 실제 체결은 다음 실행 때 다음 거래일 시가로.

    meta 는 주문 근거(점수·RSI)다. 체결은 다음 거래일에 일어나므로 그 시점에는
    주문 때의 점수를 알 방법이 없다 — 다시 계산한 점수로 채우면 매수 근거가
    아닌 값이 성적표에 박혀 점수-수익률 관계가 조용히 틀어진다. 그래서 주문에
    실어 두고 체결 기록으로 그대로 옮긴다. 성적표의 점수 구간별 분석이 이 값을
    쓴다. toss_trading 도 같은 인자를 받아 무시하므로 러너 코드는 갈라지지 않는다.

    notional_amount 의 단위는 **달러**다. 장부는 원화로만 기록하므로
    여기서 환산한다.

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
    notional_krw = amount * _FX

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
                      account_seq: str = "",
                      dry_run: bool = False) -> dict:
    state = load_state()
    state["pending"].append({
        "side":        "sell",
        "symbol":      symbol,
        "qty":         int(qty),
        "placed_date": market_date().isoformat(),
    })
    save_state(state)
    print(f"  [가상] 매도 예약 {symbol} {int(qty)}주 — 다음 거래일 시가 체결")
    return {"ok": True, "id": f"virtual-sell-{symbol}-{market_date().isoformat()}",
            "virtual": True}


def wait_for_fill(order_id: str, client_id: str = "", client_secret: str = "",
                  account_seq: str = "", timeout: int = 0) -> dict:
    """가상 주문은 다음 거래일 시가에 체결되므로 즉시 체결을 기다리지 않는다."""
    return {"ok": True, "status": "pending_next_open", "virtual": True}


# ── 장부 자기 점검 ─────────────────────────────────────────────────────
# 이 장부의 예약·체결·만료 규칙은 우리가 직접 만든 것이다. 진짜 브로커라면
# 거래소가 거절해 줄 실수(현금 초과 예약, 없는 주식 매도, 만료 안 된 주문 방치)를
# 아무도 안 막아 준다. 그래서 규칙 바로 옆에 그 규칙을 되짚는 점검을 둔다.
#
# 실제로 난 사고: 2026-07-31 예약 합계 1,852만원 대 현금 1,000만원.

# 체결 금액은 원 단위로 반올림해 trades 에 적히고 현금은 반올림 없이 깎인다.
# 거래 1건당 최대 0.5원이 어긋나므로 그만큼은 오차로 허용한다.
def _rounding_tolerance(n_trades: int) -> float:
    return 1.0 + n_trades


def check_state(state: dict, today: date | None = None) -> list[str]:
    """장부가 스스로 앞뒤가 맞는지 본다. 위반 문구 목록을 반환(빈 목록이면 정상).

    시세를 조회하지 않는다 — 네트워크 없이 파일만 보고 판정한다. 그래서 러너·보고서·
    화면 어디서 불러도 비용이 같다.
    """
    today = today or market_date()
    out = []

    cash = float(state.get("cash_krw", 0.0))
    trades = state.get("trades", []) or []
    pending = state.get("pending", []) or []
    positions = state.get("positions", {}) or {}
    tol = _rounding_tolerance(len(trades))

    # ── 1. 현금과 예약 ────────────────────────────────────────────
    if cash < 0:
        out.append(f"현금이 음수다: {cash:,.0f}원")

    reserved = reserved_krw(state)
    if reserved > cash + tol:
        out.append(f"대기 매수 예약({reserved:,.0f}원)이 현금({cash:,.0f}원)을 넘는다 "
                   f"— 같은 돈으로 두 번 주문했다")

    # ── 2. 현금 보존: 초기자본 + 입금 − 매수 + 매도 ──────────────
    buys = sum(float(t.get("amount_krw", 0.0)) for t in trades if t.get("side") == "buy")
    sells = sum(float(t.get("amount_krw", 0.0)) for t in trades if t.get("side") == "sell")
    # `index_meta` 는 이름과 달리 **두 장부가 다 쓴다.** 인덱스 러너의 월 적립도,
    # 스윙 장부의 증자도 여기 쌓인다(2026-08-18 표본 확보용 9천만원). 파일이 서로
    # 달라 섞이지 않는다 — index_runner 는 index_portfolio.json 밖에 못 건드린다.
    meta = state.get("index_meta") or {}
    deposited = float(meta.get("deposited_krw", 0.0))
    # 인덱스 장부는 매수·매도 말고도 현금이 움직인다 — 환전·거래 수수료(빠짐)와
    # 배당(들어옴). 둘 다 trades 에 안 남으므로 여기서 같이 세지 않으면 첫
    # 수수료·첫 배당에 "장부가 깨졌다"는 거짓 경보가 뜬다. 거짓 경보가 한 번
    # 나오면 진짜 경보도 못 믿게 된다.
    fees = float(meta.get("fees_krw", 0.0))
    dividends = float(meta.get("dividends_krw", 0.0))
    expected = INITIAL_CAPITAL_KRW + deposited + dividends - fees - buys + sells
    if abs(cash - expected) > tol:
        out.append(f"현금이 거래 이력과 안 맞는다: 장부 {cash:,.0f}원 vs "
                   f"이력 계산 {expected:,.0f}원 (차이 {cash - expected:+,.0f}원)")

    # ── 3. 실현손익 = 매도 손익 합 ───────────────────────────────
    realized = float(state.get("realized_pnl_krw", 0.0))
    sold_pnl = sum(float(t.get("pnl_krw", 0.0)) for t in trades if t.get("side") == "sell")
    if abs(realized - sold_pnl) > tol:
        out.append(f"실현손익이 매도 이력과 안 맞는다: 장부 {realized:,.0f}원 vs "
                   f"이력 합 {sold_pnl:,.0f}원")

    # ── 4. 보유 수량 = 매수 − 매도 (종목별) ──────────────────────
    # 체결 경로가 둘이라(_fill_buy 는 평단 합산, _settle_limit_entry 는 통째 대입)
    # 이미 들고 있는 종목이 지정가로 또 체결되면 주식이 조용히 사라질 수 있다.
    net = {}
    for t in trades:
        side, qty = t.get("side"), int(t.get("qty", 0) or 0)
        if side == "buy":
            net[t["symbol"]] = net.get(t["symbol"], 0) + qty
        elif side == "sell":
            net[t["symbol"]] = net.get(t["symbol"], 0) - qty
    for sym in set(net) | set(positions):
        held = int((positions.get(sym) or {}).get("qty", 0) or 0)
        if net.get(sym, 0) != held:
            out.append(f"{sym} 보유 수량이 거래 이력과 안 맞는다: "
                       f"장부 {held}주 vs 이력 {net.get(sym, 0)}주")

    # ── 5. 포지션 형식 ───────────────────────────────────────────
    for sym, pos in positions.items():
        if int(pos.get("qty", 0) or 0) < 1:
            out.append(f"{sym} 보유 수량이 1주 미만이다: {pos.get('qty')}")
        if float(pos.get("avg_price_usd", 0) or 0) <= 0:
            out.append(f"{sym} 평단가가 0 이하다: {pos.get('avg_price_usd')}")

    # ── 6. 대기 주문 형식·정합성 ─────────────────────────────────
    buy_syms = {}
    for i, o in enumerate(pending):
        tag = f"대기 {i + 1}번({o.get('symbol') or '종목불명'})"
        side = o.get("side")
        if side not in ("buy", "sell"):
            out.append(f"{tag} side 가 buy/sell 이 아니다: {side!r}")
            continue
        if not o.get("symbol"):
            out.append(f"{tag} 종목이 비었다")
        try:
            placed = datetime.strptime(o["placed_date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            out.append(f"{tag} placed_date 를 읽을 수 없다: {o.get('placed_date')!r}")
            continue

        if side == "buy":
            if float(o.get("notional_krw", 0) or 0) <= 0:
                out.append(f"{tag} 매수 예약 금액이 0 이하다 — 현금이 안 묶인다")
            buy_syms.setdefault(o.get("symbol"), []).append(i + 1)
            if o.get("kind") == "limit_entry":
                if int(o.get("qty", 0) or 0) < 1:
                    out.append(f"{tag} 지정가 주문인데 수량이 1주 미만이다")
                if float(o.get("limit_price", 0) or 0) <= 0:
                    out.append(f"{tag} 지정가가 0 이하다")
        else:
            pos_qty = int((positions.get(o.get("symbol")) or {}).get("qty", 0) or 0)
            if int(o.get("qty", 0) or 0) > pos_qty:
                out.append(f"{tag} 보유({pos_qty}주)보다 많이 팔려 한다: {o.get('qty')}주")

        # 만료: 체결 창을 거래일로 세지만 여기서는 달력일로 넉넉히 잡는다.
        # ponytail: 거래일 계산이 필요해지면 daily_bars 를 쓰면 되지만, 이 점검의
        # 목적은 "정산이 아예 안 돌고 있다"를 잡는 것이라 여유를 크게 둔다.
        stale_days = LIMIT_FILL_WINDOW * 2 if o.get("kind") == "limit_entry" else 14
        age = (today - placed).days
        if age > stale_days:
            out.append(f"{tag} {age}일째 대기 중 — 만료·체결됐어야 한다 "
                       f"(정산이 안 돌고 있는지 확인)")

    for sym, idxs in buy_syms.items():
        if len(idxs) > 1:
            out.append(f"{sym} 매수 대기가 {len(idxs)}건이다 — 같은 종목을 이중 예약했다")

    return out


# ── 자본곡선 (외부 입금 제거) ─────────────────────────────────────────
# equity_log.json 의 `equity` 는 "지금 계좌에 있는 돈"이라 입금하면 그냥 올라간다.
# 그걸 수익률로 나누면 입금이 수익이 된다 — 2026-08-18 증자 9천만원이 다음 날
# "수익률 +901.7% / 알파 +898.1%" 로 텔레그램에 나갔다(자산 1억 = 초기 1천만
# + 입금 9천만). 기록마다 그 시점의 **누적** 입금을 함께 적고, 두 기록의 차를
# 그 사이 들어온 돈으로 보고 빼면 수익률만 남는다(시간가중수익률).

def equity_returns(records: list) -> list[float]:
    """equity_log 기록 → 구간별 수익률. 외부 입금은 빼고 센다.

    반환 길이는 len(records) - 1 이다(기록 사이의 구간 수).
    `deposited` 가 없는 옛 기록은 0 으로 본다 — 입금 전 기록이라 맞는 값이다.
    """
    rets = []
    for prev, cur in zip(records, records[1:]):
        before = float(prev.get("equity") or 0.0)
        if before <= 0:
            rets.append(0.0)
            continue
        flow = float(cur.get("deposited") or 0.0) - float(prev.get("deposited") or 0.0)
        rets.append((float(cur.get("equity") or 0.0) - flow) / before - 1.0)
    return rets


def indexed_equity(records: list) -> list[float]:
    """입금이 한 번도 없었다면 그려졌을 자본곡선.

    첫 기록의 자산에서 출발해 구간 수익률을 이어 곱한다. 총수익률·낙폭·
    벤치마크 비교는 전부 이 곡선으로 재야 한다(원본 `equity` 로 재면 입금이
    성과가 된다).
    """
    if not records:
        return []
    curve = [float(records[0].get("equity") or 0.0)]
    for r in equity_returns(records):
        curve.append(curve[-1] * (1.0 + r))
    return curve


def _selftest_cli() -> int:
    """`python -m modules.virtual_broker selftest` — 장부 점검 결과를 찍고
    위반이 있으면 종료코드 1."""
    state = load_state()
    problems = check_state(state)
    print(f"장부 점검 — {STATE_FILE}")
    print(f"  현금 {state['cash_krw']:,.0f}원 · 보유 {len(state['positions'])}종목 · "
          f"대기 {len(state['pending'])}건 · 거래 {len(state['trades'])}건")
    if not problems:
        print("  이상 없음")
        return 0
    print(f"  ⚠️ 이상 {len(problems)}건")
    for p in problems:
        print(f"   - {p}")
    return 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(_selftest_cli())
    print("사용법: python -m modules.virtual_broker selftest")
    sys.exit(2)
