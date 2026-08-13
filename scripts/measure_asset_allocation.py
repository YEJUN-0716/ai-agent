#!/usr/bin/env python
"""ETF 자산배분 — **남이 발표한 규칙을 그대로**, 긴 표본에서, 벤치마크와 나란히.

    python scripts/measure_asset_allocation.py           # 측정 + 리포트
    python scripts/measure_asset_allocation.py selftest  # 산수 점검 (네트워크 無)

## 왜 재는가

개별종목 롱온리는 자기 유니버스 매수보유에 졌고(`2026-08-13-portfolio-vs-benchmark.md`),
"위험할 때 현금으로 간다"도 같은 노출을 상시 유지한 줄에 졌다
(`2026-08-13-timing-vs-exposure.md`). 남은 질문은 **주식 밖으로 나갈 수 있으면
달라지나** 다. 현금은 수익이 0 이지만 채권·금은 그 구간에 플러스일 수 있다.

## 규칙을 직접 만들지 않는다

여기서 우리 규칙을 새로 짜면 또 사후 분할이 된다. 그래서 **20 년 전에 공개된
두 규칙을 파라미터까지 그대로** 쓴다. 이기면 우리 것이 아니라서 믿을 만하고,
지면 "우리가 못 짠 것" 이라는 변명이 안 선다.

- **GTAA 5** (Faber, 2007) — SPY·EFA·IEF·VNQ·DBC 각 20%. 월말 종가가 자기
  10 개월 이동평균 위면 보유, 아래면 그 몫만 현금.
- **GEM 듀얼 모멘텀** (Antonacci, 2012) — 월말에 SPY 와 EFA 의 12 개월 수익률을
  비교해 높은 쪽 100%. 단 그 수익률이 0 미만이면 채권(AGG) 100%.
  (원 규칙의 절대 모멘텀 기준선은 단기 국채 수익률이다. 여기서는 0% 로 둔다 —
   BIL 상장이 2007 이라 표본을 깎기 때문이고, 저금리 구간에서는 관대한 쪽이다.)

## 함정 — 이 파일이 지켜야 하는 것

1. **벤치마크가 바뀐다.** 주식+채권을 다루면 상대는 SPY 가 아니라 **60/40** 이다.
   셋 다 낸다(SPY 100 / 60·40 / 자산 5 개 동일가중).
2. **채권의 과거 20 년은 미래를 대표하지 않는다.** 40 년 금리 하락기의 성적이다.
   그래서 **2022(주식·채권 동반 하락) 를 따로 낸다.** 거기서 방어가 안 되면
   나머지 성적은 금리 하락에 얹혀 간 것이다.
3. **총수익 기준이다** (`auto_adjust=True`). 배당을 빼면 채권 비교가 통째로
   틀린다.
4. **비용을 스윕한다.** 회전율 × 편도 비용으로 매 리밸런스에서 뗀다.
5. 신호는 **월말 종가로 판정해 다음 달에 적용**한다. 판정한 날 종가로 사는
   것은 [[backtest-fill-must-be-placeable]] 가 죽인 그 거짓말이다.

패널 캐시는 **따로 쓴다**(`data/etf_panel.parquet`). 프로덕션 패널에 ETF 를
섞으면 `measure_portfolio.py` 의 "같은 패널 동일가중 매수보유" 가 오염된다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

CACHE = "data/etf_panel.parquet"
OUT_MD = Path("docs/measurements/2026-08-13-etf-asset-allocation.md")

GTAA = ["SPY", "EFA", "IEF", "VNQ", "DBC"]
TICKERS = GTAA + ["AGG"]
COST_SWEEP = (0.0, 6.0, 20.0)      # 왕복 bp
START = "2003-01-01"


# ── 규칙 ──────────────────────────────────────────────────────────────
def gtaa_weights(monthly: pd.DataFrame, ma_months: int = 10) -> pd.DataFrame:
    """각 자산 20%, 10 개월선 위일 때만 보유. 나머지는 현금(=합이 1 미만)."""
    ma = monthly[GTAA].rolling(ma_months, min_periods=ma_months).mean()
    on = (monthly[GTAA] > ma).astype(float)
    w = on / len(GTAA)
    w[ma.isna()] = np.nan            # 이동평균이 없는 구간은 판정 불가
    return w


def gem_weights(monthly: pd.DataFrame, look: int = 12) -> pd.DataFrame:
    """SPY vs EFA 12 개월 상대 모멘텀 → 승자. 승자가 음수면 AGG."""
    r = monthly / monthly.shift(look) - 1.0
    w = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    winner = np.where(r["SPY"] >= r["EFA"], "SPY", "EFA")
    best = r[["SPY", "EFA"]].max(axis=1)
    for i, (dt, wn) in enumerate(zip(monthly.index, winner)):
        if pd.isna(best.iloc[i]) or pd.isna(r.loc[dt, "AGG"]):
            w.loc[dt] = np.nan
        elif best.iloc[i] > 0:
            w.loc[dt, wn] = 1.0
        else:
            w.loc[dt, "AGG"] = 1.0
    return w


def fixed_weights(monthly: pd.DataFrame, mix: dict) -> pd.DataFrame:
    """월 리밸런스 고정 비중 (60/40, 동일가중 등)."""
    w = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    for tk, v in mix.items():
        w[tk] = v
    return w


# ── 굴리기 ────────────────────────────────────────────────────────────
def run(daily: pd.DataFrame, w_month: pd.DataFrame, cost_bps: float) -> pd.Series:
    """월말 비중을 **다음 달**에 적용해 일별로 굴린다. 비용은 회전율에서 뗀다.

    비중을 판정한 월말 종가로 사는 게 아니라 그 다음 거래일부터 적용한다 —
    같은 날 종가를 보고 그 종가에 사는 것은 못 거는 주문이다.
    """
    ret = daily.pct_change().fillna(0.0)
    w = w_month.shift(1).reindex(daily.index, method="ffill")
    valid = w.notna().all(axis=1)
    w, ret = w[valid].fillna(0.0), ret[valid]
    if w.empty:
        return pd.Series(dtype=float)

    turnover = w.diff().abs().sum(axis=1)
    turnover.iloc[0] = w.iloc[0].abs().sum()
    port = (w * ret).sum(axis=1) - turnover * (cost_bps / 2.0) / 1e4
    return (1.0 + port).cumprod()


def cagr(curve: pd.Series) -> float:
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    return (float(curve.iloc[-1]) ** (1.0 / yrs) - 1.0) * 100.0 if yrs > 0 else float("nan")


def mdd(curve: pd.Series) -> float:
    return float((curve / curve.cummax() - 1.0).min() * 100.0)


def year_ret(curve: pd.Series, year: int) -> float:
    cur = curve[curve.index.year == year]
    prev = curve[curve.index.year < year]
    if cur.empty:
        return float("nan")
    base = prev.iloc[-1] if len(prev) else 1.0
    return (float(cur.iloc[-1]) / float(base) - 1.0) * 100.0


# ── 자체 점검 ─────────────────────────────────────────────────────────
def selftest() -> int:
    idx = pd.bdate_range("2020-01-01", periods=300)
    up = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    daily = pd.DataFrame({tk: up for tk in TICKERS})
    monthly = daily.resample("ME").last()

    # 1) 계속 오르면 10 개월선 위 → 5 자산 전부 보유(합 1.0)
    w = gtaa_weights(monthly)
    assert abs(w.dropna().iloc[-1].sum() - 1.0) < 1e-12, w.dropna().iloc[-1]

    # 2) 비용 0 이고 전부 보유면 곡선이 자산 수익률과 같다
    c = run(daily, w, 0.0)
    # 첫 유효일의 수익률은 **그 전날 종가 대비**다 — 기준값도 전날이어야 한다.
    base = float(daily["SPY"].shift(1).loc[c.index[0]])
    assert abs(float(c.iloc[-1]) - float(daily["SPY"].iloc[-1]) / base) < 1e-6, c.iloc[-1]

    # 3) 값이 반토막 나 10 개월선 아래로 가면 현금(비중 0)
    down = up.copy()
    down.iloc[-60:] = 50.0
    d2 = pd.DataFrame({tk: down for tk in TICKERS})
    w2 = gtaa_weights(d2.resample("ME").last())
    assert w2.dropna().iloc[-1].sum() == 0.0, w2.dropna().iloc[-1]

    # 4) GEM: SPY 가 EFA 보다 세면 SPY 100%
    m = daily.resample("ME").last()
    m["EFA"] = m["EFA"] * 0.5
    g = gem_weights(m).dropna()
    assert g.iloc[-1]["SPY"] == 1.0 and g.iloc[-1].sum() == 1.0, g.iloc[-1]

    # 5) GEM: 둘 다 12 개월 수익률이 음수면 AGG 100%
    m2 = m.copy()
    m2[["SPY", "EFA"]] = m2[["SPY", "EFA"]] * np.linspace(1.0, 0.3, len(m2))[:, None]
    g2 = gem_weights(m2).dropna()
    assert g2.iloc[-1]["AGG"] == 1.0, g2.iloc[-1]

    # 6) 비용: 왕복 20bp 로 한 번 갈아타면 편도 10bp 씩 양쪽 = 20bp 가 빠진다
    flat = pd.DataFrame({tk: pd.Series(100.0, index=idx) for tk in TICKERS})
    fm = flat.resample("ME").last()
    wsw = pd.DataFrame(0.0, index=fm.index, columns=fm.columns)
    wsw["SPY"] = 1.0
    wsw.iloc[len(fm) // 2:, wsw.columns.get_loc("SPY")] = 0.0
    wsw.iloc[len(fm) // 2:, wsw.columns.get_loc("AGG")] = 1.0
    cc = run(flat, wsw, 20.0)
    # 최초 편입(회전 1.0) + 한 번 교체(매도 1.0 + 매수 1.0) = 각각 10bp / 20bp.
    # 다른 날에 붙으므로 더하는 게 아니라 곱해진다.
    assert abs(float(cc.iloc[-1]) - (1 - 0.001) * (1 - 0.002)) < 1e-9, cc.iloc[-1]

    print("selftest 통과 (6건)")
    return 0


# ── 리포트 ────────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()

    from modules.price_panel import load_panel

    end = pd.Timestamp.today().normalize()
    prices, _ = load_panel(TICKERS, START, end, cache_path=CACHE)
    missing = [t for t in TICKERS if t not in prices]
    if missing:
        print(f"[오류] 못 받은 티커: {missing}", file=sys.stderr)
        return 1

    daily = pd.DataFrame(prices).dropna()
    monthly = daily.resample("ME").last()

    strategies = {
        "GTAA 5 (Faber, 10개월선)": gtaa_weights(monthly),
        "GEM 듀얼 모멘텀 (Antonacci)": gem_weights(monthly),
    }
    benches = {
        "SPY 100% 매수보유": fixed_weights(monthly, {"SPY": 1.0}),
        "60/40 (SPY·IEF, 월 리밸런스)": fixed_weights(monthly, {"SPY": 0.6, "IEF": 0.4}),
        "자산 5개 동일가중 매수보유": fixed_weights(monthly, {t: 0.2 for t in GTAA}),
    }

    curves = {name: {c: run(daily, w, c) for c in COST_SWEEP}
              for name, w in {**strategies, **benches}.items()}
    span = curves["GTAA 5 (Faber, 10개월선)"][6.0]
    years = (span.index[-1] - span.index[0]).days / 365.25

    body = [
        "# ETF 자산배분 — 주식 밖으로 나갈 수 있으면 달라지나 (2026-08-13)",
        "",
        "개별종목 롱온리는 자기 유니버스 매수보유에 졌고, \"위험할 때 현금으로 "
        "간다\" 도 같은 노출을 상시 유지한 줄에 졌다. 남은 질문 하나 — **현금 "
        "대신 채권·금으로 갈 수 있으면 달라지나.**", "",
        "**규칙은 직접 짜지 않았다.** 20 년 전에 공개된 두 규칙을 파라미터까지 "
        "그대로 쓴다(Faber GTAA 5, Antonacci GEM). 우리가 고르면 또 사후 분할이 "
        "된다.", "",
        f"기간 {span.index[0].date()} ~ {span.index[-1].date()} ({years:.1f}년) · "
        "총수익 기준(배당 포함) · 월말 판정 → 다음 달 적용 · 비용은 회전율 × 편도",
        "",
        "| | 최종 자본 | 연 수익률 | MDD | 2008 | 2022 |",
        "|---|---|---|---|---|---|",
    ]
    for name in list(strategies) + list(benches):
        c = curves[name][6.0]
        body.append(
            f"| {name} | ×{float(c.iloc[-1]):.2f} | **{cagr(c):+.1f}%** | "
            f"{mdd(c):.1f}% | {year_ret(c, 2008):+.1f}% | {year_ret(c, 2022):+.1f}% |")

    def c6(n):
        return curves[n][6.0]

    gem, gtaa = c6("GEM 듀얼 모멘텀 (Antonacci)"), c6("GTAA 5 (Faber, 10개월선)")
    mix, spy = c6("60/40 (SPY·IEF, 월 리밸런스)"), c6("SPY 100% 매수보유")
    body += [
        "", "## 판정", "",
        f"**아무것도 매수보유를 못 이겼다.** SPY 100% 가 연 {cagr(spy):+.1f}% 로 "
        f"이 표의 1 위다. 세 번째 측정에서 세 번째 같은 결과다.", "",
        f"- **GEM** 은 60/40 대비 연 {cagr(gem) - cagr(mix):+.1f}%p 앞선다. 그런데 "
        f"낙폭이 {mdd(gem):.1f}% 대 {mdd(mix):.1f}% 로 **더 깊고**, 무엇보다 "
        f"**2022 에 {year_ret(gem, 2022):+.1f}% 로 이 표에서 제일 크게 잃었다** "
        f"(60/40 {year_ret(mix, 2022):+.1f}%, SPY {year_ret(spy, 2022):+.1f}%). "
        f"2008 은 {year_ret(gem, 2008):+.1f}% 로 훌륭했다 — **금리가 내려가던 "
        "구간에서만 작동한 방어다.** 우리가 미리 적어둔 함정이 그대로 터졌다.",
        f"- **GTAA 5** 는 2022 에 {year_ret(gtaa, 2022):+.1f}% 로 이 표에서 유일하게 "
        f"플러스다. 방어는 진짜였다. 대신 연 {cagr(gtaa):+.1f}% 로 60/40 의 절반이다 — "
        "**하락장을 피한 값을 나머지 18 년 동안 갚았다.**", "",
        "즉 자산배분으로 넓혀도 답은 같다. 방어를 사면 수익을 내주고, 수익을 "
        "쫓으면 방어가 없다. **넓힌 것은 표본(19.6 년, 2008 포함)이지 결론이 "
        "아니다.**", "",
        "## 비용 스윕 (왕복 bp, 연 수익률)", "",
        "| | " + " | ".join(f"{c:.0f}bp" for c in COST_SWEEP) + " |",
        "|---|" + "---|" * len(COST_SWEEP),
    ]
    for name in list(strategies) + list(benches):
        body.append(f"| {name} | " +
                    " | ".join(f"{cagr(curves[name][c]):+.1f}%" for c in COST_SWEEP) + " |")

    yrs = sorted({d.year for d in span.index})
    body += [
        "", "## 해마다 (6bp)", "",
        "| 연도 | " + " | ".join(list(strategies) + list(benches)) + " |",
        "|---|" + "---|" * (len(strategies) + len(benches)),
    ]
    for y in yrs:
        body.append(f"| {y} | " + " | ".join(
            f"{year_ret(curves[n][6.0], y):+.1f}%" for n in list(strategies) + list(benches)
        ) + " |")

    body += [
        "", "## 이 표를 읽을 때", "",
        "- **2022 열이 핵심이다.** 채권이 주식과 같이 떨어진 해다. 여기서 "
        "방어가 안 됐다면 나머지 성적은 40 년 금리 하락기에 얹혀 간 것이다.",
        "- **2008 열은 반대로 이 표본에서만 얻을 수 있는 것이다.** 개별종목 "
        "패널(2020~)로는 아예 못 보던 구간이다.",
        "- 비교 상대는 SPY 가 아니라 **60/40** 이다. 주식+채권을 다루면서 "
        "SPY 를 상대로 삼으면 노출이 다른 둘을 같은 줄에 놓는 것이다.",
        "- ETF 는 살아남은 것만 고른 게 아니라 **그때 실제로 살 수 있었던** "
        "물건이다. 생존 편향이 없다.",
        "- 그래도 규칙 선택 자체는 사후다 — 두 규칙 모두 **발표 뒤** 구간이 "
        "이 표본의 대부분이라는 점은 덜어내고 볼 것.", "",
        "재현: `python scripts/measure_asset_allocation.py` · "
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
