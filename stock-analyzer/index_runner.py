"""인덱스 자동운용 러너 — 매달 한 번, 몇 번을 깨워도 한 번.

매월 1~8일 매일 깨어나 장부를 보고 이번 달 할 일이 남았는지 스스로 판단한다.
cron 은 휴장일을 모르므로 날짜로 창을 잡고, 중복은 장부의 index_meta 로 막는다.

paper_trade_runner_toss.py 에 넣지 않는 이유: 규칙이 다른 두 시스템을 한 파일에
넣으면 성적이 섞인다(설계서 5.1). 장부도 파일부터 분리한다.

실행 순서가 곧 규칙이다 — place_notional_buy 가 내부에서 load_state() 를 다시
하므로, 메모리에서 고친 state 를 **저장한 뒤에** 주문을 내야 입금·배당 반영이
날아가지 않는다(계획서 전제 4).

설계: docs/superpowers/specs/2026-08-13-index-autopilot-design.md

환경변수:
  VIRTUAL_PORTFOLIO_FILE  index_portfolio.json  **필수.** 안 주면 죽는다
  VIRTUAL_CAPITAL_KRW     시작 자본            (기본 0 을 워크플로가 준다)
  INDEX_MONTHLY_KRW       월 적립금             (기본 1,000,000)
  INDEX_FX_SPREAD_BP      환전 스프레드 편도     (기본 10bp)
  INDEX_FEE_BP            매매 수수료 편도       (기본 25bp)
  INDEX_GAP_BUFFER_BP     갭 대비 남겨 둘 여유   (기본: index_autopilot.GAP_BUFFER_BP)
  INDEX_DIV_WITHHOLDING   미국 배당 원천징수     (기본 0.15)
  TELEGRAM_TOKEN / TELEGRAM_PUBLIC_CHANNEL_ID   없으면 발송만 생략
  DRY_RUN                 false 여야 장부에 쓴다 (기본 true)
"""

from __future__ import annotations

import os
from datetime import date

import yfinance as yf

from modules import virtual_broker as vb
from modules.fx import fetch_krw_per_usd
from modules.index_autopilot import (
    GAP_BUFFER_BP as AP_GAP_BUFFER_BP, TARGETS, plan_orders,
)
from scorecard_worker import send_tg   # 공개 채널 발송 — 성적표와 같은 채널

MONTHLY_KRW     = float(os.environ.get("INDEX_MONTHLY_KRW", "1000000"))
FX_SPREAD_BP    = float(os.environ.get("INDEX_FX_SPREAD_BP", "10"))
FEE_BP          = float(os.environ.get("INDEX_FEE_BP", "25"))
DIV_WITHHOLDING = float(os.environ.get("INDEX_DIV_WITHHOLDING", "0.15"))
# 갭 버퍼는 modules.index_autopilot 이 소유한다 — plan_orders 가 직접 뺀다.
# 여기서 또 빼면 두 번 빠지고, 러너에만 두면 마찰을 재는 쪽이 안 따라온다
# (실제로 그랬다 — scripts/measure_index_autopilot 이 버퍼 없이 굴리고 있었다).
GAP_BUFFER_BP   = float(os.environ.get("INDEX_GAP_BUFFER_BP", str(AP_GAP_BUFFER_BP)))
DRY_RUN         = os.environ.get("DRY_RUN", "true").strip().lower() != "false"

# 미실현 양도세(보고용). 실현하지 않으므로 장부 현금에는 손대지 않는다.
CGT_RATE          = 0.22
CGT_DEDUCTION_KRW = 2_500_000

# 스윙 장부 파일명. 여기에 인덱스 매수가 한 건이라도 섞이면 두 시스템의 성적을
# 영영 분리할 수 없다 — 되돌릴 방법이 없으므로 시작 전에 막는다.
SWING_LEDGER = "virtual_portfolio.json"


def _dividends_since(ticker: str, since: date) -> tuple[float, str | None]:
    """`since` 이후 주당 배당 합계와 마지막 배당일. 없으면 (0.0, None).

    배당은 AGG 수익의 사실상 전부라 빠뜨리면 벤치마크 비교가 통째로 틀어진다.
    반대로 두 번 넣으면 성적을 조용히 위조한다 — 그래서 호출자가 마지막 반영일을
    장부에 적고 그 이후만 받아 간다.
    """
    try:
        ser = yf.Ticker(ticker).dividends
    except Exception as e:
        # 콘솔 인코딩이 cp949 인 로컬에서 em dash 하나가 러너를 죽인다(실측).
        print(f"  [인덱스] 배당 조회 실패 {ticker} : {type(e).__name__}")
        return 0.0, None
    if ser is None or len(ser) == 0:
        return 0.0, None
    after = ser[[d.date() > since for d in ser.index]]
    if len(after) == 0:
        return 0.0, None
    return float(after.sum()), after.index[-1].date().isoformat()


def _apply_dividends(state: dict, fx: float, prices: dict) -> float:
    """세후 배당을 현금에 더하고 반영일을 기록한다. 반환은 이번에 더한 USD.

    벤치마크 ITOT 도 같은 배당을 같은 세율로 받아 재투자한다. 안 주면 벤치가
    매년 배당수익률(ITOT ≈ 1.2%)만큼 낮게 나와, 지고 있어도 이긴 것처럼 보인다 —
    성공 판정 ②의 문턱이 −0.5%p 라 그 편향 하나로 판정이 뒤집힌다.
    """
    meta = state.setdefault("index_meta", {})
    seen = meta.setdefault("dividends", {})
    total_usd = 0.0
    for sym, pos in state["positions"].items():
        # 기록이 없으면 매수일 이후부터. 보유 전 배당을 받으면 없는 돈이 생긴다.
        since = date.fromisoformat(seen.get(sym) or pos["entry_date"])
        per_share, last_day = _dividends_since(sym, since)
        if per_share <= 0 or last_day is None:
            continue
        cash_usd = per_share * pos["qty"] * (1 - DIV_WITHHOLDING)
        total_usd += cash_usd
        state["cash_krw"] += cash_usd * fx
        # 장부 자기 점검의 현금 검산이 이 값을 읽는다. 안 적으면 배당이
        # 들어온 만큼 "이력과 안 맞는다"고 거짓 경보가 뜬다.
        meta["dividends_krw"] = meta.get("dividends_krw", 0.0) + cash_usd * fx
        seen[sym] = last_day
        bench = meta.get("bench_itot_shares", 0.0)
        if sym == "ITOT" and bench and prices.get(sym):
            # 벤치는 소수점으로 즉시 재투자한다. 배당 창은 보유분과 같은 것을
            # 쓴다 — 첫 달 체결 하루 차이만 어긋나고 그 뒤로는 같은 구간이다.
            meta["bench_itot_shares"] = bench + per_share * (1 - DIV_WITHHOLDING) * bench / prices[sym]
        print(f"  [인덱스] 배당 {sym} ${cash_usd:,.2f} (세후, ~{last_day})")
    return total_usd


def _deposit(state: dict, prices: dict, fx: float, month: str) -> list[dict]:
    """이번 달 적립·환전·주문 계획. 주문은 부르는 쪽에서 낸다(전제 4).

    이월 현금까지 합쳐서 계획한다 — 이월 자체가 정수주 적립의 마찰이고,
    두 달치가 모여야 1주가 되는 자산(GLDM)이 그 돈으로 채워진다(설계서 4.1).
    """
    meta = state["index_meta"]
    fx_cost_krw = MONTHLY_KRW * FX_SPREAD_BP / 1e4
    state["cash_krw"] += MONTHLY_KRW - fx_cost_krw

    order_cash_usd = state["cash_krw"] / fx * (1 - FEE_BP / 1e4)
    holdings = {s: p["qty"] for s, p in state["positions"].items()}
    orders = plan_orders(holdings, prices, order_cash_usd,
                         gap_buffer_bp=GAP_BUFFER_BP)

    spent_usd = sum(o["qty"] * o["est_price"] for o in orders)
    fee_usd = spent_usd * FEE_BP / 1e4
    state["cash_krw"] -= fee_usd * fx

    # 벤치마크는 같은 날 같은 **적립금**을 ITOT 100% 에 넣는다. 이월분을 같이
    # 넣으면 우리 쪽에만 남아 있는 현금을 벤치가 먼저 굴리는 셈이 돼, 이 줄이
    # 재려는 것(정수주 지연의 비용)이 사라진다. 벤치는 소수점으로 산다.
    bench_usd = (MONTHLY_KRW - fx_cost_krw) / fx * (1 - FEE_BP / 1e4)
    meta["bench_itot_shares"] = meta.get("bench_itot_shares", 0.0) + bench_usd / prices["ITOT"]

    meta["last_deposit_month"] = month
    meta["deposit_count"] = meta.get("deposit_count", 0) + 1
    meta["deposited_krw"] = meta.get("deposited_krw", 0.0) + MONTHLY_KRW
    meta["fees_krw"] = meta.get("fees_krw", 0.0) + fx_cost_krw + fee_usd * fx
    meta["last_deposit"] = {
        "month":       month,
        "krw":         MONTHLY_KRW,
        "usd":         (MONTHLY_KRW - fx_cost_krw) / fx,
        "fx":          fx,
        "fx_cost_usd": fx_cost_krw / fx,
        "fee_usd":     fee_usd,
        "orders":      orders,
    }
    return orders


def build_report(state: dict, fx: float, prices: dict) -> str:
    """월 보고 본문. 순수 함수 — 장부와 가격만 읽는다(설계서 8장).

    벤치마크 줄과 미실현 양도세는 빼지 않는다. 성공 판정 기준이 그 두 줄이고,
    절대 수익률은 기준이 아니다(설계서 7장).
    """
    meta = state.get("index_meta", {})
    dep = meta.get("last_deposit", {})
    ordered = {o["ticker"]: o for o in dep.get("orders", [])}
    buys = " · ".join(
        f"{t} {ordered[t]['qty']}주 ${ordered[t]['qty'] * ordered[t]['est_price']:,.2f}"
        if t in ordered else f"{t} 0주(이월)"
        for t in TARGETS
    )

    values = {s: p["qty"] * prices.get(s, 0.0) for s, p in state["positions"].items()}
    stock_usd = sum(values.values())
    weights = " / ".join(
        f"{t} {(values.get(t, 0.0) / stock_usd * 100 if stock_usd else 0):.1f}"
        for t in TARGETS
    )

    deposited = meta.get("deposited_krw", 0.0)
    equity_krw = state["cash_krw"] + stock_usd * fx
    ret = equity_krw / deposited - 1 if deposited else 0.0
    bench_krw = meta.get("bench_itot_shares", 0.0) * prices.get("ITOT", 0.0) * fx
    bench_ret = bench_krw / deposited - 1 if deposited else 0.0

    # 취득원가를 현재 환율로 환산한다 — 매수 시점 환율은 무시.
    # ponytail: 보고용 참고치. 실제 신고는 매도할 때 하고, 이 시스템은 안 판다.
    cost_usd = sum(p["qty"] * p["avg_price_usd"] for p in state["positions"].values())
    gain_krw = (stock_usd - cost_usd) * fx
    tax_krw = max(0.0, gain_krw - CGT_DEDUCTION_KRW) * CGT_RATE

    return "\n".join([
        f"[인덱스 자동운용] {dep.get('month', '?')} ({meta.get('deposit_count', 0)}회차)",
        f"적립 {dep.get('krw', 0):,.0f}원 → ${dep.get('usd', 0):,.2f} "
        f"(환율 {dep.get('fx', fx):,.1f}, 환전비용 ${dep.get('fx_cost_usd', 0):,.2f})",
        f"매수 {buys}",
        f"     수수료 ${dep.get('fee_usd', 0):,.2f} · 이월 ${state['cash_krw'] / fx:,.2f}",
        f"보유 ${stock_usd:,.0f} ({weights})",
        f"누적 적립 {deposited:,.0f}원 → 평가 {equity_krw:,.0f}원 ({ret * 100:+.1f}%)",
        f"벤치 ITOT 100% 적립 대비 {(ret - bench_ret) * 100:+.2f}%p · "
        f"미실현 양도세 {tax_krw:,.0f}원",
    ])


def run(now: date | None = None) -> dict:
    """그날 한 일. 같은 달에 몇 번을 불러도 입금 1회·주문 1세트·보고 1회."""
    if vb.STATE_FILE == SWING_LEDGER:
        raise RuntimeError(
            "VIRTUAL_PORTFOLIO_FILE 이 index_portfolio.json 으로 안 잡혔습니다 — "
            "스윙 장부를 오염시키지 않으려고 아무것도 하지 않고 멈춥니다.")

    today = now or date.today()
    month = today.strftime("%Y-%m")
    fx = fetch_krw_per_usd()
    vb.set_fx(fx)

    state = vb.settle_pending(vb.load_state(), fx)      # 지난번 주문부터 체결
    prices = {t: vb.last_close_price(t) for t in TARGETS}
    div_usd = _apply_dividends(state, fx, prices)       # 벤치 재투자에 가격이 필요하다
    meta = state.setdefault("index_meta", {})

    result = {"month": month, "fx": fx, "dividends_usd": div_usd,
              "deposited": False, "orders": [], "reported": False}

    if meta.get("last_deposit_month") != month:
        result["orders"] = _deposit(state, prices, fx, month)
        result["deposited"] = True

    if not DRY_RUN:
        vb.save_state(state)                            # ★ 주문 전에 저장
        for o in result["orders"]:
            vb.place_notional_buy(o["ticker"], o["qty"] * o["est_price"],
                                  qty=o["qty"])
        if result["orders"]:
            state = vb.load_state()                     # 대기열이 실린 최신 장부
            meta = state.setdefault("index_meta", {})

    # 체결이 끝난 뒤에 보고한다. 주문 낸 날은 대기열이 차 있으므로 다음 실행 몫이다.
    if (not state["pending"] and meta.get("last_deposit_month") == month
            and meta.get("last_report_month") != month):
        msg = build_report(state, fx, prices)
        if DRY_RUN:
            # 드라이런은 주문을 안 내므로 pending 이 비고, 그래서 적립 당일 바로 이
            # 경로를 탄다 — 실전이라면 다음 실행 몫이다. 현금은 매수분만큼 안 줄었고
            # 보유는 아직 0 이라, 이월·보유·평가 세 줄은 체결 전 값이다. 이 경고가
            # 없으면 리허설 출력을 실전 결과로 읽게 된다.
            msg += "\n※ DRY_RUN — 체결 전이라 이월·보유·평가 세 줄은 실제와 다릅니다"
        print(msg)
        if not DRY_RUN and send_tg(msg):
            meta["last_report_month"] = month
            result["reported"] = True
            vb.save_state(state)

    print(f"[인덱스] {month} : 적립 {'O' if result['deposited'] else 'X'} · "
          f"주문 {len(result['orders'])}건 · 보고 {'O' if result['reported'] else 'X'}"
          + (" (DRY_RUN, 장부에 안 씀)" if DRY_RUN else ""))
    return result


if __name__ == "__main__":
    run()
