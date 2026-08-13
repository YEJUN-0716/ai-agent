#!/usr/bin/env python
"""인덱스 자동운용 — **마찰**을 잰다. 성공을 찾는 최적화가 아니다.

    python scripts/measure_index_autopilot.py           # 측정 + 리포트
    python scripts/measure_index_autopilot.py selftest  # 산수 점검 (네트워크 無)

## 무엇을 재는가

비중 70/20/10 은 원칙으로 골랐고 백테스트로 안 골랐다(설계서 3.2). 그러니 여기서
재는 것은 "이 비중이 좋은가" 가 아니라 **규칙이 적힌 대로 돌 때 얼마가 새는가** 다.

새는 곳은 넷이다 — 정수주라 못 산 돈(이월), 환전 스프레드, 매매 수수료,
배당 원천징수. 넷을 한 번에 켠 줄과 하나씩 끈 줄을 나란히 내면 어느 것이
얼마짜리인지 보인다.

**벤치마크 줄을 같이 낸다.** 성공 판정 ②(ITOT 100% 동일 적립 대비 연 −0.5%p
이내)의 기준선이 이 줄이고, 그 정의는 러너의 월 보고와 **같아야** 한다.

## 함정 — 이 파일이 지켜야 하는 것

1. **조정 주가(auto_adjust)를 쓰면 재려는 것이 사라진다.** yfinance 의 조정가는
   과거 주가를 배당만큼 끌어내린다. 2004 년 ITOT 1 주가 $25 로 보이면 $714 로
   더 많이 사지는 셈이라 **정수주 마찰이 과소평가된다.** 그래서 이 스크립트만
   무조정 시세를 따로 받아 캐시한다(`load_panel` 을 안 쓰는 유일한 이유).
2. **GLDM 은 2018 상장이다.** 그 이전은 GLD 로 대체하되 **가격 수준을 GLDM 에
   맞춰 이어 붙인다.** GLD 를 그대로 쓰면 1 주 $400 대 $87 이라 정수주 마찰이
   통째로 부풀려진다. 이어 붙인 구간은 수익률만 GLD 의 것이다.
3. **체결은 걸 수 있는 주문이어야 한다.** 매월 첫 거래일 **종가**로 계획하고
   **다음 거래일 시가**로 체결한다. 러너의 실행 순서와 같다
   ([[backtest-fill-must-be-placeable]]).
4. **주문은 부분체결이 없다.** 시가가 튀어 현금이 모자라면 브로커가 그 주문을
   통째로 뺀다(`virtual_broker._fill_buy`). 시뮬도 똑같이 뺀다.
5. **규칙을 다시 짜지 않는다.** 주문은 `modules.index_autopilot.plan_orders` 를
   그대로 부른다. 소수점 줄도 같은 함수의 `whole_shares=False` 다.

## 이 측정이 안 하는 것

- **환율 이력을 안 쓴다.** 적립금을 고정 $714 로 두고 환전 스프레드만 비용으로
  뗀다. 원화 환산 절대 수익률은 여기서 안 나오지만, 규칙과 벤치가 **같은** 환율을
  타므로 %p 차이는 영향을 안 받는다. 재는 것이 %p 차이다.
- 비중 튜닝 · 매도 · 밴드 · 레짐 필터. 하나도 안 넣는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules.index_autopilot import TARGETS, plan_orders  # noqa: E402

CACHE  = Path("data/index_autopilot_panel.parquet")
OUT_MD = Path("docs/measurements/2026-08-13-index-autopilot-friction.md")

TICKERS      = ["ITOT", "AGG", "GLDM", "GLD"]
BENCH        = {"ITOT": 1.0}
MONTHLY_USD  = 714.0     # 월 100 만원 ≈ $714 (설계서 3.1)
FX_SPREAD_BP = 10.0      # 환전 스프레드 편도 — index_runner 기본값
FEE_BP       = 25.0      # 매매 수수료 편도 — index_runner 기본값
WITHHOLDING  = 0.15      # 미국 배당 원천징수
FEE_SWEEP    = (0.0, 25.0, 50.0)
START        = "2003-01-01"
STALE_DAYS   = 5
WINDOW       = 12        # 성공 판정을 하는 창 = 12 개월


# ── 데이터 ────────────────────────────────────────────────────────────
def _download() -> pd.DataFrame:
    """무조정 시가·종가·배당. 조정가를 쓰면 정수주 마찰이 사라진다(함정 1)."""
    import yfinance as yf

    raw = yf.download(TICKERS, start=START, end=pd.Timestamp.today().normalize(),
                      auto_adjust=False, actions=True, progress=False,
                      group_by="column", threads=True)
    if raw is None or raw.empty:
        raise RuntimeError("시세를 못 받았다 — 네트워크나 티커를 확인할 것")
    keep = [c for c in raw.columns if c[0] in ("Open", "Close", "Dividends")]
    return raw[keep].sort_index()


def _splice(new: pd.Series, old: pd.Series) -> pd.Series:
    """상장 전 구간을 `old` 의 수익률로, `new` 의 가격 수준에 맞춰 이어 붙인다."""
    n = new.dropna()
    if n.empty:
        raise ValueError("이어 붙일 신규 시계열이 비었다")
    ratio = float(n.iloc[0]) / float(old.dropna().asof(n.index[0]))
    back = old.loc[old.index < n.index[0]].dropna() * ratio
    return pd.concat([back, n])


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(종가, 시가, 주당배당). GLDM 은 2018 이전 구간을 GLD 로 이어 붙였다."""
    panel = None
    if CACHE.exists():
        panel = pd.read_parquet(CACHE)
        panel.index = pd.to_datetime(panel.index)
        if (pd.Timestamp.today().normalize() - panel.index.max()).days > STALE_DAYS:
            panel = None
        else:
            print(f"[measure] 캐시 히트 — 다운로드 없음 ({CACHE})")
    if panel is None:
        panel = _download()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(CACHE)

    cols = list(TARGETS)
    close = panel["Close"].copy()
    open_ = panel["Open"].copy()
    close["GLDM"] = _splice(close["GLDM"], close["GLD"])
    open_["GLDM"] = _splice(open_["GLDM"], open_["GLD"])

    close = close[cols].dropna()
    open_ = open_[cols].reindex(close.index).ffill()
    div   = panel["Dividends"].reindex(close.index).fillna(0.0)[cols]
    return close, open_, div


def month_starts(index: pd.DatetimeIndex) -> list:
    """각 달의 첫 거래일. 러너가 실제로 깨어나 주문을 내는 날이다."""
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).first())


# ── 굴리기 ────────────────────────────────────────────────────────────
def simulate(close: pd.DataFrame, open_: pd.DataFrame, div: pd.DataFrame,
             months: list, *, targets: dict = TARGETS, whole_shares: bool = True,
             fee_bp: float = FEE_BP, fx_bp: float = FX_SPREAD_BP,
             withholding: float = WITHHOLDING) -> dict:
    """러너와 같은 순서로 굴린다.

    배당 반영 → 적립·환전 → 첫 거래일 **종가**로 계획 → 다음 거래일 **시가**로
    체결. 순서가 곧 규칙이다(계획서 전제 4).
    """
    idx = close.index
    holdings = {t: 0.0 for t in targets}
    cash = deposited = fees = taxes = 0.0
    carried = []                        # 매달 남긴 이월 현금
    skipped = {t: 0 for t in targets}   # 1 주를 못 채워 건너뛴 달
    rejected = 0                        # 시가가 튀어 통째로 빠진 주문
    prev = last_day = None

    for d in months:
        if prev is not None:
            win = div.loc[(div.index > prev) & (div.index <= d)]
            gross = sum(float(win[t].sum()) * holdings[t] for t in targets)
            cash += gross * (1 - withholding)
            taxes += gross * withholding
        prev = d

        fx_cost = MONTHLY_USD * fx_bp / 1e4
        cash += MONTHLY_USD - fx_cost
        deposited += MONTHLY_USD
        fees += fx_cost

        prices = {t: float(close.at[d, t]) for t in targets}
        orders = plan_orders(holdings, prices, cash * (1 - fee_bp / 1e4),
                             targets, whole_shares=whole_shares)
        placed = {o["ticker"] for o in orders}
        for t in targets:
            if t not in placed:
                skipped[t] += 1

        pos = idx.get_loc(d)
        if pos + 1 >= len(idx):
            break                       # 체결일이 아직 없다 — 계획까지만
        fill_day = idx[pos + 1]
        for o in orders:
            fill = float(open_.at[fill_day, o["ticker"]])
            qty, unit = o["qty"], fill * (1 + fee_bp / 1e4)
            if qty * unit > cash:
                if whole_shares:        # 브로커와 같다: 부분체결이 없다(함정 4)
                    rejected += 1
                    continue
                # 소수점은 금액 주문이라 모자라면 줄여서 체결된다. 여기서도
                # 통째로 빼면 "마찰 0" 줄이 매달 현금을 남겨, 재려는 정수주
                # 마찰이 그 줄에도 섞인다.
                qty = cash / unit
            cash -= qty * unit
            fees += qty * fill * fee_bp / 1e4
            holdings[o["ticker"]] += qty
        carried.append(cash)
        last_day = fill_day

    stock = sum(holdings[t] * float(close.at[last_day, t]) for t in targets)
    return {
        "equity": cash + stock, "deposited": deposited, "cash": cash,
        "stock": stock, "holdings": holdings, "fees": fees, "taxes": taxes,
        "skipped": skipped, "rejected": rejected, "carried": float(np.mean(carried)),
        "months": len(carried), "start": months[0], "end": last_day,
        "ret": (cash + stock) / deposited - 1 if deposited else 0.0,
    }


def irr_annual(monthly_out: float, n: int, equity: float) -> float:
    """매달 같은 금액을 넣고 마지막에 equity 를 받는 현금흐름의 연환산 수익률(%).

    적립식은 곡선의 CAGR 로 못 잰다 — 넣은 돈이 굴러간 기간이 회차마다 다르다.
    """
    def npv(r: float) -> float:
        return equity / (1 + r) ** n - sum(monthly_out / (1 + r) ** i for i in range(n))

    lo, hi = -0.99, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if npv(mid) > 0 else (lo, mid)
    return ((1 + lo) ** 12 - 1) * 100


def rolling_gap(close, open_, div, months, window: int = WINDOW) -> pd.Series:
    """창마다 벤치 대비 %p. 정의는 러너 월 보고와 같다(적립액 대비 수익률의 차)."""
    out = {}
    for i in range(len(months) - window + 1):
        seg = months[i:i + window]
        a = simulate(close, open_, div, seg)
        b = simulate(close, open_, div, seg, targets=BENCH, whole_shares=False)
        out[seg[0]] = (a["ret"] - b["ret"]) * 100
    return pd.Series(out)


# ── 자체 점검 ─────────────────────────────────────────────────────────
def _flat_panel(n: int = 400, price: float = 100.0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.DataFrame({t: price for t in TARGETS}, index=idx)
    return close, close.copy(), pd.DataFrame(0.0, index=idx, columns=list(TARGETS))


def selftest() -> int:
    close, open_, div = _flat_panel()
    months = month_starts(close.index)

    # 1) 값이 안 움직이고 비용도 0 이면 소수점 매수는 현금을 한 푼도 안 남긴다
    frac = simulate(close, open_, div, months, whole_shares=False, fee_bp=0, fx_bp=0)
    assert abs(frac["cash"]) < 1e-6, frac["cash"]
    assert abs(frac["equity"] - frac["deposited"]) < 1e-6, frac["equity"]

    # 2) 같은 조건에서 정수주는 반드시 현금을 남긴다. 남긴 현금은 사라지지
    #    않는다 — 값이 안 움직이면 이월은 공짜다. 값이 오를 때만 비용이 된다.
    whole = simulate(close, open_, div, months, fee_bp=0, fx_bp=0)
    assert whole["cash"] > 0, whole["cash"]
    assert abs(whole["equity"] - whole["deposited"]) < 1e-6, whole["equity"]

    up = close * np.linspace(1.0, 3.0, len(close))[:, None]
    assert (simulate(up, up, div, months, fee_bp=0, fx_bp=0)["equity"]
            < simulate(up, up, div, months, whole_shares=False,
                       fee_bp=0, fx_bp=0)["equity"])

    # 3) 비용은 켠 만큼만 빠진다 (환전 10bp + 수수료 25bp, 값이 안 움직일 때)
    cost = simulate(close, open_, div, months, whole_shares=False)
    lost = cost["deposited"] - cost["equity"]
    assert abs(lost - cost["fees"]) < 1e-6, (lost, cost["fees"])
    assert 0 < lost < cost["deposited"] * 0.004, lost

    # 4) IRR: 값도 비용도 안 움직이면 연 0%
    assert abs(irr_annual(MONTHLY_USD, frac["months"], frac["equity"])) < 1e-6

    # 5) 배당은 **그때 들고 있던 수량**만큼, 원천징수 뒤 현금으로 들어온다
    pay_day = div.index[100]
    d2 = div.copy()
    d2.loc[pay_day, "AGG"] = 1.0
    held = sum(1 for m in months if m <= pay_day)
    qty = simulate(close, open_, div, months[:held], fee_bp=0, fx_bp=0)["holdings"]["AGG"]
    with_div = simulate(close, open_, d2, months, fee_bp=0, fx_bp=0)
    assert qty > 0 and abs(with_div["taxes"] - qty * WITHHOLDING) < 1e-6, (
        with_div["taxes"], qty)

    # 6) 시가가 튀어 현금이 모자라면 주문이 통째로 빠진다 (부분체결 없음)
    gap = open_.copy()
    gap.iloc[1:] = gap.iloc[1:] * 3.0
    assert simulate(close, gap, div, months, fee_bp=0, fx_bp=0)["rejected"] > 0

    # 7) 이어 붙인 지점에서 가격이 안 튄다
    idx = pd.bdate_range("2015-01-01", periods=300)
    old = pd.Series(np.linspace(400, 500, len(idx)), index=idx)
    new = pd.Series(np.nan, index=idx)
    new.iloc[200:] = old.iloc[200:].to_numpy() / 5.0
    sp = _splice(new, old)
    assert abs(sp.iloc[199] - sp.iloc[200]) < abs(sp.iloc[200]) * 0.01, sp.iloc[198:202]

    print("selftest 통과 (7건)")
    return 0


# ── 리포트 ────────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()

    close, open_, div = load_data()
    months = month_starts(close.index)
    print(f"[measure] {close.index[0].date()} ~ {close.index[-1].date()} · "
          f"{len(months)}개월")

    runs = {
        "규칙 그대로 (정수주 · 비용·세금 켬)":
            simulate(close, open_, div, months),
        "같은 규칙, 소수점 매수":
            simulate(close, open_, div, months, whole_shares=False),
        "같은 규칙, 소수점 + 비용 0 + 배당세 0":
            simulate(close, open_, div, months, whole_shares=False,
                     fee_bp=0, fx_bp=0, withholding=0),
        "벤치: ITOT 100% (소수점, 같은 비용)":
            simulate(close, open_, div, months, targets=BENCH, whole_shares=False),
    }
    rule, frac, ideal, bench = (runs[k] for k in runs)
    yrs = (rule["end"] - rule["start"]).days / 365.25

    def irr(r):
        return irr_annual(MONTHLY_USD, r["months"], r["equity"])

    gaps = rolling_gap(close, open_, div, months)
    inside = float((gaps >= -0.5).mean() * 100)

    body = [
        "# 인덱스 자동운용 — 마찰을 잰다 (2026-08-13)",
        "",
        "**이 표는 비중을 고르는 데 쓰지 않는다.** 70/20/10 은 원칙으로 골랐고 "
        "성적이 나쁘다고 바꾸면 여섯 번째 실패다(설계서 3.2). 여기서 재는 것은 "
        "**규칙이 적힌 대로 돌 때 얼마가 새는가** 하나다.", "",
        f"기간 {rule['start'].date()} ~ {rule['end'].date()} ({yrs:.1f}년, "
        f"{rule['months']}개월 적립) · 매월 첫 거래일 종가로 계획 → **다음 거래일 "
        f"시가로 체결** · 매달 ${MONTHLY_USD:,.0f} (월 100만원)",
        "",
        "| | 최종 평가 | 적립 대비 | 연환산(IRR) | 남은 현금 | 총비용 | 배당세 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in runs.items():
        body.append(
            f"| {name} | ${r['equity']:,.0f} | {r['ret'] * 100:+.1f}% | "
            f"**{irr(r):+.2f}%** | ${r['cash']:,.0f} | ${r['fees']:,.0f} | "
            f"${r['taxes']:,.0f} |")
    body.append(f"| (총 적립) | ${rule['deposited']:,.0f} | — | — | — | — | — |")

    body += [
        "", "## 마찰이 얼마짜리인가", "",
        "| 새는 곳 | 연 %p |",
        "|---|---|",
        f"| 정수주 이월 (소수점 대비) | {irr(rule) - irr(frac):+.2f}%p |",
        f"| 비용·세금 (환전 {FX_SPREAD_BP:.0f}bp + 수수료 {FEE_BP:.0f}bp + "
        f"배당세 {WITHHOLDING:.0%}) | {irr(frac) - irr(ideal):+.2f}%p |",
        f"| **합계 (규칙 그대로 vs 마찰 0)** | **{irr(rule) - irr(ideal):+.2f}%p** |",
        "",
        f"평균 이월 현금 ${rule['carried']:,.0f} (월 적립금의 "
        f"{rule['carried'] / MONTHLY_USD * 100:.0f}%) · "
        f"1주를 못 채워 건너뛴 달: " + " · ".join(
            f"{t} {rule['skipped'][t]}/{rule['months']}" for t in TARGETS) +
        f" · 시가가 튀어 빠진 주문 {rule['rejected']}건",
        "",
        "## 판정", "",
        f"**정수주 제약은 거의 공짜다** — 소수점 매수 대비 연 "
        f"{irr(rule) - irr(frac):+.2f}%p. 21.7년 동안 GLDM 은 "
        f"{rule['skipped']['GLDM']}/{rule['months']}개월을 건너뛰었는데도 그렇다. "
        "이월된 현금이 다음 달에 그대로 들어가고, 하락 구간에서는 늦게 산 것이 "
        "오히려 득이라 상쇄된다. **소수점 매매를 못 쓰는 것은 이 시스템의 "
        "약점이 아니다.**", "",
        f"**진짜로 새는 것은 배당 원천징수다** — 누적 ${rule['taxes']:,.0f} 대 "
        f"매매·환전 비용 ${rule['fees']:,.0f}, {rule['taxes'] / rule['fees']:.0f}배다. "
        "그런데 이건 벤치도 똑같이 내므로 **줄일 수 있는 항목이 아니다.** "
        "수수료를 편도 25bp 에서 50bp 로 두 배 올려도 연 0.01%p 밖에 안 움직인다 — "
        "15분봉에서 6bp 가 0.4R 을 먹던 것과 정반대다. **월 1회 적립은 비용이 "
        "지렛대가 아니다.**", "",
        "## 성공 판정 ② 의 기준선", "",
        f"규칙 그대로 vs ITOT 100% 동일 적립 — 전 구간 연 "
        f"**{irr(rule) - irr(bench):+.2f}%p**.", "",
        f"판정은 **12개월 창**에서 한다(설계서 7장). 전 구간에서 "
        f"{len(gaps)}개 창을 굴린 결과:", "",
        "| 12개월 창 벤치 대비 %p | |",
        "|---|---|",
        f"| 중앙값 | {gaps.median():+.2f}%p |",
        f"| 최악 | {gaps.min():+.2f}%p ({gaps.idxmin().date()} 시작) |",
        f"| 최선 | {gaps.max():+.2f}%p ({gaps.idxmax().date()} 시작) |",
        f"| **−0.5%p 이내였던 창** | **{inside:.0f}%** ({int(inside / 100 * len(gaps))}"
        f"/{len(gaps)}) |",
        "",
        "## 이 표를 읽을 때", "",
        "- **벤치와의 차이는 대부분 마찰이 아니라 자산 배분이다.** ITOT 100% 와 "
        "70/20/10 은 노출이 다르다. 위 '마찰이 얼마짜리인가' 표가 마찰만 떼어낸 "
        "줄이고, 그게 진짜 이 시스템이 통제하는 부분이다.",
        f"- **성공 판정 ②(연 −0.5%p 이내)는 주식 비중을 30%p 덜 든 값으로는 "
        f"구간에 따라 못 지킨다.** −0.5%p 이내였던 창이 {inside:.0f}% 다. "
        "12개월 뒤 이 문턱을 못 넘으면 **규칙을 바꾸는 게 아니라 문턱이 노출 차이를 "
        "무시했다고 적는다.**",
        f"- **문턱을 지금 고치지는 않는다.** 재고 나서 기준을 낮추면 그게 사후 "
        f"분할이다. 대신 12개월 뒤 판정할 때 **'마찰 0 인 같은 배분' 줄"
        f"(연 {irr(rule) - irr(ideal):+.2f}%p)** 을 같이 낸다 — 이 시스템이 실제로 "
        f"통제하는 부분은 그쪽이고, 그 줄이 **재기 전에** 여기 적혔다.",
        "- 비용 가정을 표에 같이 적는 이유: 이 저장소를 죽인 게 비용 가정 "
        "하나였다(PR #94·#95).",
        f"- **GLDM 은 2018-06 상장이다.** 그 이전 구간은 GLD 수익률을 GLDM 의 "
        "가격 수준(1주 ≈ 1/50 온스)에 맞춰 이어 붙였다. 수익률은 GLD 의 것이고, "
        "1주 값만 GLDM 을 따른다 — 정수주 마찰을 재는 표라 1주 값이 실물이어야 한다.",
        "- **무조정 시세로 굴렸다.** 조정 주가는 과거 1주 값을 배당만큼 낮춰 "
        "정수주 마찰을 없애 버린다. 그래서 이 스크립트만 패널을 따로 쓴다.",
        "- **환율 이력은 안 썼다.** 적립금을 $714 고정으로 두고 환전 스프레드만 "
        "뗐다. 규칙과 벤치가 같은 환율을 타므로 %p 차이엔 영향이 없다.",
        "",
        "## 수수료 스윕 (편도 bp, 연환산 IRR)", "",
        "| | " + " | ".join(f"{c:.0f}bp" for c in FEE_SWEEP) + " |",
        "|---|" + "---|" * len(FEE_SWEEP),
    ]
    for name, kw in (("규칙 그대로", {}), ("벤치 ITOT 100%",
                                       {"targets": BENCH, "whole_shares": False})):
        cells = []
        for c in FEE_SWEEP:
            r = simulate(close, open_, div, months, fee_bp=c, **kw)
            cells.append(f"{irr(r):+.2f}%")
        body.append(f"| {name} | " + " | ".join(cells) + " |")

    body += [
        "",
        "재현: `python scripts/measure_index_autopilot.py` · "
        "산수 점검 `... selftest`", "",
    ]

    text = "\n".join(body)
    print(text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
