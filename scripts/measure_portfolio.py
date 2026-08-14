#!/usr/bin/env python
"""트레이드 평균 R 을 **연 수익률**로 바꾸고, 같은 유니버스 매수보유와 나란히 둔다.

    python scripts/measure_portfolio.py            # 측정 + 리포트
    python scripts/measure_portfolio.py selftest   # 슬롯·복리 산수 자체 점검

## 왜 재는가

지금까지 모든 측정이 **트레이드 평균 R** 이었다. 러너는 자리가 10 개뿐인데
신호는 연 800 건 나온다 — 8 배가 넘는다. **대부분을 못 잡고, 무엇을 못 잡는지가
결과를 바꾼다.** 자리가 찼을 때 들어온 신호를 버리는데 그게 좋은 판인지 나쁜
판인지 트레이드 평균으로는 알 수 없다.

그리고 개별종목 롱만 하는 전략이 **자기 유니버스를 그냥 사서 들고 있는 것**을
못 이기면 존재 이유가 없다. 그 비교가 여기서만 나온다.

## 벤치마크를 SPY 가 아니라 같은 패널로 잡은 이유

패널은 현재 S&P 구성종목 279 개라 생존 편향이 있다. **전략도 같은 편향 위에서
잰다.** SPY 를 갖다 대면 편향 차이가 비교에 섞인다 — 같은 패널의 동일가중
매수보유가 유일하게 사과 대 사과다. (그래서 이 벤치마크는 실제 지수보다
높게 나온다. 넘기 더 어려운 기준이라는 뜻이다.)

네트워크 無 — 저장 패널과 측정 parquet 만 읽는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

TRADES = Path("data/entry_rule_trades-daily.parquet")
PANEL = Path(os.environ.get("PANEL", "data/price_panel_v1.parquet"))
OUT_MD = Path("docs/measurements/2026-08-13-portfolio-vs-benchmark.md")
OUT_MD2 = Path("docs/measurements/2026-08-13-timing-vs-exposure.md")

RULE = "C_갭반영"          # 프로덕션이 실제로 거는 라인
IS_START = pd.Timestamp("2024-12-20")
COST_SWEEP = (0.0, 6.0, 20.0, 40.0)

# paper_trade_runner_toss.py 와 같은 값이라야 이 곡선이 그 러너를 대표한다.
MAX_POSITIONS = 10
RISK_PCT_PER_TRADE = 0.5      # 1R = 자본의 %
MAX_POSITION_PCT = 15.0       # 한 종목 명목가 상한 %
# ponytail: 레짐별 자리 축소(bull 10 / neutral 7 / bear 4)는 안 넣었다. 넣으면
# 2022 가 나아질 수 있지만 레짐 판정이 또 하나의 미검증 부품이라 여기서는
# 고정 10 으로 둔다 — 이 측정이 답할 질문은 "자리 경합이 결과를 바꾸나" 다.


def risk_fraction(risk_pct: float) -> float:
    """실효 1R = 자본의 몇 분율. 위험 기준 수량과 명목 상한 중 **작은 쪽**이 문다.

    손절이 촘촘하면 위험 0.5% 를 채우려다 한 종목이 자본의 30% 가 되므로 15%
    상한이 먼저 걸린다 — 그러면 실효 1R 은 0.5% 가 아니라 15% × 손절폭이다.
    """
    if not (risk_pct > 0):
        return 0.0
    return min(RISK_PCT_PER_TRADE / 100.0,
               MAX_POSITION_PCT / 100.0 * risk_pct / 100.0)


def net_r(row: pd.Series, cost_bps: float) -> float:
    """비용 차감 R. 비용이 몇 R 인지는 그 트레이드의 손절폭이 정한다."""
    return float(row[f"{RULE}_r"]) - (cost_bps / 1e4) / (row["risk_pct"] / 100.0)


def simulate(df: pd.DataFrame, cost_bps: float, max_positions: int = MAX_POSITIONS):
    """자리 경합을 넣고 복리로 굴린다.

    자리는 **주문을 넣는 순간** 물린다(미체결이면 지정가 폐기일까지). 러너가
    held + pending 을 함께 세기 때문이다 — 자리를 체결 시점부터 세면 실제보다
    많이 잡는 낙관적인 장부가 된다.
    """
    equity = 1.0
    open_pos: list[dict] = []        # {"release", "ticker", "pnl", "filled", "risk_frac"}
    taken = skipped_slot = skipped_dup = skipped_cash = 0
    curve: list[tuple[pd.Timestamp, float]] = []
    exposure: list[tuple[pd.Timestamp, float]] = []
    pos_log: list[dict] = []         # 평가 기준 곡선(mtm_curve)이 쓴다

    def close_until(when):
        nonlocal equity
        for p in sorted([p for p in open_pos if p["release"] <= when],
                        key=lambda p: p["release"]):
            equity += p["pnl"]
            open_pos.remove(p)
            curve.append((p["release"], equity))

    for _, row in df.sort_values("entry_date").iterrows():
        day = row["entry_date"]
        close_until(day)
        if any(p["ticker"] == row["ticker"] for p in open_pos):
            skipped_dup += 1
            continue
        if len(open_pos) >= max_positions:
            skipped_slot += 1
            continue

        filled = row[f"{RULE}_outcome"] not in ("nofill", "skip")
        frac = risk_fraction(row["risk_pct"])
        # 명목 비중 = 위험분율 ÷ 손절폭 (상한 15%). 자리 10 × 15% = 150% 라
        # **자리만 세면 못 살 돈까지 산 장부가 된다.** 현금 계좌 기준으로
        # 합계 명목이 자본을 넘지 못하게 막는다 — 넘으면 그 주문은 못 낸다.
        notional = min(MAX_POSITION_PCT / 100.0, frac / (row["risk_pct"] / 100.0))
        held = sum(p["notional"] for p in open_pos)
        if held + notional > 1.0:
            skipped_cash += 1
            continue
        release = row["exit_date"] if filled else row["expire_date"]
        if pd.isna(release):
            release = row["expire_date"]
        open_pos.append({
            "release": release, "ticker": row["ticker"],
            "pnl": (net_r(row, cost_bps) * frac * equity) if filled else 0.0,
            "filled": filled, "risk_frac": frac, "notional": notional,
        })
        taken += 1
        if filled:
            pos_log.append({
                "ticker": row["ticker"], "notional": notional * equity,
                "fill_date": row.get("fill_date", pd.NaT), "release": release,
                "pnl": open_pos[-1]["pnl"],
            })
        exposure.append((day, sum(p["notional"] for p in open_pos if p["filled"])))

    close_until(pd.Timestamp.max)
    eq = pd.Series(dict(curve)).sort_index() if curve else pd.Series(dtype=float)
    return {
        "equity": equity, "curve": eq, "taken": taken,
        "skipped_slot": skipped_slot, "skipped_dup": skipped_dup,
        "skipped_cash": skipped_cash, "pos_log": pos_log,
        "avg_open": float(np.mean([e for _, e in exposure])) if exposure else 0.0,
    }


def cagr(equity: float, years: float) -> float:
    return (equity ** (1.0 / years) - 1.0) * 100.0 if years > 0 and equity > 0 else float("nan")


def mdd(curve: pd.Series) -> float:
    """실현 손익 곡선의 최대 낙폭. **평가손익을 안 세므로 실제보다 얕게 나온다.**"""
    if curve.empty:
        return float("nan")
    return float((curve / curve.cummax() - 1.0).min() * 100.0)


def closes(start, end) -> pd.DataFrame:
    close = pd.read_parquet(PANEL)["Close"]
    return close.loc[(close.index >= start) & (close.index <= end)]


def bench_curve(start, end, members=None, close=None) -> pd.Series:
    """같은 패널 동일가중 매수보유. 시작일에 값이 있는 종목만 산다.

    **`members` 를 주면 매월 동일가중 리밸런스로 바뀐다** (열 `date`·`asset_id`).
    위 한 줄은 시작일과 종료일에 **둘 다** 값이 있는 종목만 사므로, 상장폐지가 든
    패널에 그대로 대면 기준선이 "안 죽은 것만 산 줄"이 된다 — 유니버스를 생존자
    편향 없이 만드는 데 든 일이 벤치마크 한 줄에서 통째로 새는 자리다(소형주
    설계서 5.1). 그 패널에서는 **그달 구성종목**을 동일가중으로 들고, 상폐 종목은
    마지막 거래일 종가에 청산해 다음 리밸런스에서 남은 종목에 재분배한다.
    살아남았는지는 조건에 안 들어간다.
    """
    if close is None:
        close = closes(start, end)
    if members is None:
        c = close.loc[:, close.iloc[0].notna() & close.iloc[-1].notna()]
        return c.div(c.iloc[0], axis=1).mean(axis=1)

    idx = close.index
    held = close.ffill()          # 상폐 뒤 = 마지막 거래일 종가 = 청산가로 고정
    dates = sorted(pd.to_datetime(members["date"].unique()))
    by_date = {d: list(g) for d, g in members.groupby("date")["asset_id"]}
    ret = np.zeros(len(idx))
    for i, d in enumerate(dates):
        lo = int(idx.searchsorted(d, "right")) - 1        # 기준일 종가에 산다
        hi = (int(idx.searchsorted(dates[i + 1], "right")) - 1
              if i + 1 < len(dates) else len(idx) - 1)
        if lo < 0 or hi <= lo:
            continue
        seg = held.iloc[lo:hi + 1].reindex(columns=[c for c in by_date[d]
                                                    if c in held.columns])
        seg = seg.loc[:, seg.iloc[0].notna() & (seg.iloc[0] > 0)]
        if seg.shape[1] == 0:
            continue                                       # ponytail: 그 달은 현금
        v = seg.div(seg.iloc[0], axis=1).mean(axis=1)
        ret[lo + 1:hi + 1] = v.pct_change().to_numpy()[1:]
    return pd.Series(np.cumprod(1.0 + ret), index=idx)


def mtm_curve(pos_log: list[dict], close: pd.DataFrame) -> pd.Series:
    """**매일 평가한** 자본 곡선. 낙폭을 매수보유와 나란히 놓으려면 이게 필요하다.

    실현 곡선(`simulate`의 `curve`)은 판 것만 세서 보유 중 평가손실이 안 보인다.
    그래서 낙폭이 실제보다 얕게 나오고, 평가 기준인 매수보유와 비교가 안 됐다.

    보유 중 평가손익은 **체결일 종가 대비 당일 종가**로 잡는다. 진입은 구간
    지정가라 체결가 ≠ 체결일 종가이므로 경로에 그만큼 오차가 있다 — 청산일에는
    실현값으로 갈아끼워 끝값을 맞춘다. 낙폭의 깊이를 보는 데는 이 정도면 된다
    (트레이드당 보유가 중앙 5일 안팎이라 오차가 누적되지 않는다).
    """
    if not pos_log:
        return pd.Series(dtype=float)
    days = close.index
    realized = pd.Series(0.0, index=days)
    unreal = pd.Series(0.0, index=days)
    for p in pos_log:
        rel = p["release"]
        realized.loc[days >= rel] += p["pnl"]
        f = p["fill_date"]
        if pd.isna(f) or p["ticker"] not in close.columns:
            continue
        # 보유 구간 = 체결일 다음 거래일 ~ 청산 전날 (청산일은 실현으로 센다)
        hold = (days > f) & (days < rel)
        if not hold.any():
            continue
        px = close[p["ticker"]]
        base = px.asof(f)
        if pd.isna(base) or base <= 0:
            continue
        unreal.loc[hold] += p["notional"] * (px[hold] / base - 1.0).fillna(0.0)
    return 1.0 + realized + unreal


def regime_overlay(start, end, ma_days: int = 200):
    """종목 선택을 **아예 빼고** 노출만 조절했을 때 어떻게 되나.

    패널 동일가중 지수가 자기 200일 이동평균 위면 100% 노출, 아래면 현금.
    판정은 **전날 종가까지의 정보만** 쓴다(shift(1)) — 오늘 종가를 보고 오늘
    비우면 그건 [[유령 체결]]과 같은 종류의 거짓말이다.

    비교 대상은 매수보유가 아니라 **같은 평균 노출을 상시 유지한 줄**이다.
    항상 절반만 태우는 것도 하락장에서는 덜 잃는다 — 그건 공짜라서 알파가
    아니다. 타이밍이 값을 하려면 그 공짜 방어를 이겨야 한다.
    """
    full = bench_curve(pd.Timestamp.min, end)      # 이동평균을 데울 앞 구간까지
    ma = full.rolling(ma_days, min_periods=ma_days).mean()
    on = (full > ma).shift(1)
    # 이동평균이 아직 없는 초기 구간은 **사 있는 것**으로 둔다. 롱온리 기준선의
    # 기본값은 투자 상태이고, 모르는 구간을 현금으로 두면 방어를 공짜로 얻는다.
    on = on.where(ma.shift(1).notna(), True).fillna(True).astype(float)
    ret = full.pct_change().fillna(0.0)
    win = (full.index >= start) & (full.index <= end)
    on, ret = on[win], ret[win]
    avg_exp = float(on.mean())
    overlay = (1.0 + ret * on).cumprod()
    flat = (1.0 + ret * avg_exp).cumprod()
    return overlay, flat, avg_exp, int((on != on.shift(1)).sum() - 1)


def monthly_overlay(start, end, ma_months: int = 10):
    """같은 아이디어를 **월말 판정**으로. 일봉 판정은 채찍질(whipsaw)에 약하다.

    200일선을 매일 보면 지수가 선 근처에서 흔들릴 때마다 팔았다 샀다 한다 —
    타이밍이 값을 하는지 묻는데 신호가 아니라 마찰을 재게 된다. 월말 종가로
    10개월 평균을 보는 건 같은 아이디어의 가장 흔한 구현이고 스위치가 1/10 로
    준다. 두 구현이 다 지면 아이디어 쪽에 무게가 실린다.
    """
    full = bench_curve(pd.Timestamp.min, end)
    m = full.resample("ME").last()
    sig = (m > m.rolling(ma_months, min_periods=ma_months).mean()).shift(1)
    sig = sig.where(m.rolling(ma_months, min_periods=ma_months).mean().shift(1).notna(), True)
    on = sig.reindex(full.index, method="ffill").fillna(True).astype(float)
    ret = full.pct_change().fillna(0.0)
    win = (full.index >= start) & (full.index <= end)
    on, ret = on[win], ret[win]
    avg_exp = float(on.mean())
    overlay = (1.0 + ret * on).cumprod()
    flat = (1.0 + ret * avg_exp).cumprod()
    return overlay, flat, avg_exp, int((on != on.shift(1)).sum() - 1)


def _year_return(curve: pd.Series, year: int) -> float:
    """그 해 마지막 값 ÷ 직전 값. 곡선이 계단이라 연초 값은 전년 끝을 쓴다."""
    cur = curve[curve.index.year == year]
    prev = curve[curve.index.year < year]
    if cur.empty:
        return float("nan")
    base = prev.iloc[-1] if len(prev) else 1.0
    return (cur.iloc[-1] / base - 1.0) * 100.0


def yearly(strat: pd.Series, bench: pd.Series, exposure: float) -> list[str]:
    """해마다 나란히. **노출을 맞춘 줄**을 같이 낸다.

    전략이 하락장에서 덜 잃는 건 알파일 수도 있고 그냥 **현금을 많이 들고
    있어서**일 수도 있다. 매수보유를 같은 노출로 낮춘 줄이 그 둘을 가른다.
    """
    lines = [f"| 연도 | 전략 (6bp) | 매수보유 | 매수보유 × {exposure * 100:.0f}% 노출 |",
             "|---|---|---|---|"]
    for y in sorted({*strat.index.year} | {*bench.index.year}):
        s, b = _year_return(strat, y), _year_return(bench, y)
        lines.append(f"| {y} | {s:+.1f}% | {b:+.1f}% | {b * exposure:+.1f}% |")
    return lines + [""]


def selftest() -> int:
    """슬롯·복리 산수만 손으로 확인한다. 시장 데이터 없이 돈다."""
    d = pd.Timestamp("2020-01-01")
    def row(tk, day, out, r, rel):
        return {"ticker": tk, "entry_date": d + pd.Timedelta(days=day),
                f"{RULE}_outcome": out, f"{RULE}_r": r, "risk_pct": 5.0,
                "exit_date": d + pd.Timedelta(days=rel),
                "expire_date": d + pd.Timedelta(days=rel)}

    # 손절폭 5% → 15% 상한이 0.75% 로 위험 0.5% 보다 크다 → 위험 기준이 문다.
    assert abs(risk_fraction(5.0) - 0.005) < 1e-12
    # 손절폭 1% → 15% × 1% = 0.15% 로 상한이 먼저 문다.
    assert abs(risk_fraction(1.0) - 0.0015) < 1e-12

    # 자리 1개뿐이면 겹치는 두 번째 셋업은 버려진다.
    df = pd.DataFrame([row("A", 0, "win", 2.0, 10), row("B", 1, "win", 2.0, 10)])
    r1 = simulate(df, 0.0, max_positions=1)
    assert r1["taken"] == 1 and r1["skipped_slot"] == 1, r1
    assert abs(r1["equity"] - (1 + 2.0 * 0.005)) < 1e-12, r1["equity"]

    # 자리가 2개면 둘 다 잡고, 두 번째는 첫 번째가 아직 안 닫혔으므로
    # **같은 자본**으로 사이징한다 (복리는 청산 뒤부터).
    r2 = simulate(df, 0.0, max_positions=2)
    assert r2["taken"] == 2, r2
    assert abs(r2["equity"] - (1 + 2 * 2.0 * 0.005)) < 1e-12, r2["equity"]

    # 첫 트레이드가 닫힌 뒤 들어온 두 번째는 불어난 자본으로 사이징한다.
    df2 = pd.DataFrame([row("A", 0, "win", 2.0, 10), row("B", 20, "win", 2.0, 30)])
    r3 = simulate(df2, 0.0, max_positions=1)
    e1 = 1 + 2.0 * 0.005
    assert abs(r3["equity"] - (e1 + 2.0 * 0.005 * e1)) < 1e-12, r3["equity"]

    # 미체결은 손익 0 이지만 폐기일까지 **자리를 문다.**
    df3 = pd.DataFrame([row("A", 0, "nofill", 0.0, 10), row("B", 1, "win", 2.0, 10)])
    r4 = simulate(df3, 0.0, max_positions=1)
    assert r4["taken"] == 1 and r4["skipped_slot"] == 1 and r4["equity"] == 1.0, r4

    # 같은 종목 중복 진입은 자리와 무관하게 막힌다.
    df4 = pd.DataFrame([row("A", 0, "win", 2.0, 10), row("A", 1, "win", 2.0, 10)])
    r5 = simulate(df4, 0.0, max_positions=10)
    assert r5["taken"] == 1 and r5["skipped_dup"] == 1, r5

    # ── 평가 기준 곡선 ────────────────────────────────────────────────
    days = pd.bdate_range("2020-01-01", periods=6)
    close = pd.DataFrame({"A": [100.0, 110.0, 90.0, 95.0, 105.0, 105.0]}, index=days)
    # 체결일 다음날부터 청산 전날까지는 평가손익이 보이고, 청산일부터 실현값.
    log = [{"ticker": "A", "notional": 0.5, "fill_date": days[0],
            "release": days[4], "pnl": 0.02}]
    m = mtm_curve(log, close)
    assert abs(m.iloc[0] - 1.0) < 1e-12, m.iloc[0]          # 체결일엔 아직 0
    assert abs(m.iloc[1] - 1.05) < 1e-12, m.iloc[1]          # +10% × 0.5
    assert abs(m.iloc[2] - 0.95) < 1e-12, m.iloc[2]          # −10% × 0.5
    assert abs(m.iloc[4] - 1.02) < 1e-12, m.iloc[4]          # 청산일 = 실현
    assert abs(m.iloc[5] - 1.02) < 1e-12, m.iloc[5]
    # 실현 곡선이었다면 낙폭 0 이었을 구간을 평가 곡선은 잡아낸다.
    assert mdd(m) < -4.9, mdd(m)

    print("selftest 통과 (12건)")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()

    d = pd.read_parquet(TRADES)
    if "expire_date" not in d.columns:
        print("parquet 에 슬롯 날짜가 없습니다 — MODE=daily python "
              "scripts/measure_entry_rule.py 를 먼저 돌리세요", file=sys.stderr)
        return 1
    d["entry_date"] = pd.to_datetime(d["entry_date"])
    act = d[d["actionable"]].copy()
    start, end = act["entry_date"].min(), d["entry_date"].max()
    years = (end - start).days / 365.25

    bench = bench_curve(start, end)
    bench_eq, bench_mdd = float(bench.iloc[-1]), mdd(bench)
    rows = []
    for c in COST_SWEEP:
        r = simulate(act, c)
        rows.append((c, r))

    r20 = dict(rows)[20.0]
    body = [
        "# 자리가 10 개일 때 실제로 얼마인가 — 그리고 그냥 사서 들고 있는 것보다 나은가 (2026-08-13)",
        "",
        "지금까지 모든 측정이 **트레이드 평균 R** 이었다. 러너 자리는 10 개인데 "
        f"actionable 셋업은 {len(act):,}건 나온다 — 자리 경합을 한 번도 안 넣었다. "
        "넣고 복리로 굴린 뒤, 같은 패널 동일가중 매수보유와 나란히 둔다.", "",
        f"기간 {start.date()} ~ {end.date()} ({years:.1f}년) · 279종목 · "
        f"자리 {MAX_POSITIONS} · 1R = 자본 {RISK_PCT_PER_TRADE}% · "
        f"종목 상한 {MAX_POSITION_PCT}%", "",
        "| 왕복 비용 | 잡은 트레이드 | 자리 없어 버림 | 최종 자본 | 연 수익률 | MDD |",
        "|---|---|---|---|---|---|",
    ]
    for c, r in rows:
        body.append(
            f"| {c:.0f}bp | {r['taken']:,} | {r['skipped_slot']:,} | "
            f"×{r['equity']:.3f} | **{cagr(r['equity'], years):+.1f}%** | "
            f"{mdd(r['curve']):.1f}% |")
    body += [
        f"| **매수보유 (같은 패널 동일가중)** | — | — | ×{bench_eq:.3f} | "
        f"**{cagr(bench_eq, years):+.1f}%** | {bench_mdd:.1f}% |", "",
        f"자리 경합으로 버린 셋업이 {r20['skipped_slot']:,}건 "
        f"({r20['skipped_slot'] / len(act) * 100:.0f}%), 현금이 모자라 "
        f"버린 것이 {r20['skipped_cash']:,}건 다. 동시 보유 명목은 "
        f"평균 자본의 {r20['avg_open'] * 100:.0f}% "
        "수준이라 **대부분의 시간에 자본이 놀고 있다** — 매수보유는 100% 노출이다. "
        "노출이 다른 둘을 같은 줄에 놓은 것이므로, 이 표는 "
        "\"위험 대비\" 가 아니라 **\"같은 돈을 어디에 두는 게 나았나\"** 를 묻는다. "
        "실무에서 답해야 하는 질문이 그쪽이다.", "",
        "**위 MDD 는 실현 손익 곡선 기준이라 실제보다 얕다** — 미실현 평가손익을 "
        "안 세기 때문이다. 매수보유 쪽은 평가 기준이라 더 깊게 나온다. "
        "이 열끼리는 나란히 두지 말 것. 평가 기준으로 다시 잰 낙폭과 "
        "\"안 산 것이 값을 했나\" 는 `2026-08-13-timing-vs-exposure.md` 에 있다.", "",
        "## 해마다", "",
        *yearly(dict(rows)[6.0]["curve"], bench, dict(rows)[6.0]["avg_open"]),
        "**원본 매수보유를 이긴 해는 하나도 없다.** 노출을 맞춘 마지막 열과 "
        "비교하면 2022(−0.7 vs −3.9)와 2024(+14.2 vs +7.7) 두 해가 앞선다 — "
        "7년 중 2년이고 둘 다 시장이 나쁘거나 옆으로 간 해다. **나머지 5년은 "
        "노출을 맞춰도 진다.** 하락장 방어는 알파가 아니라 **안 산 것**이라는 "
        "쪽에 무게가 실린다.", "",
        "**비용을 0 으로 놓아도 못 이긴다.** 위 표 0bp 줄이 그 답이다 — "
        "수수료를 아무리 깎아도 넘을 수 있는 거리가 아니다.", "",
        f"재현: `python {Path(__file__).as_posix().split('/')[-2]}/"
        f"{Path(__file__).name}` · 산수 점검 `... selftest`", "",
    ]
    text = "\n".join(body)
    print(text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}")

    text2 = "\n".join(timing_report(dict(rows)[6.0], bench, start, end, years))
    print(text2)
    OUT_MD2.write_text(text2, encoding="utf-8")
    print(f"저장: {OUT_MD2}")
    return 0


def timing_report(r6: dict, bench: pd.Series, start, end, years: float) -> list[str]:
    """"안 산 것" 이 값을 했나 — 노출을 맞춘 상대와 낙폭까지 나란히 놓는다."""
    strat_mtm = mtm_curve(r6["pos_log"], closes(start, end))
    overlay, flat, avg_exp, switches = regime_overlay(start, end)
    m_overlay, m_flat, m_exp, m_switches = monthly_overlay(start, end)
    exp_strat = r6["avg_open"]
    bench_flat = (1.0 + bench.pct_change().fillna(0.0) * exp_strat).cumprod()

    def _c(curve):
        return cagr(float(curve.iloc[-1]), years)

    def line(name, curve):
        return (f"| {name} | ×{curve.iloc[-1]:.3f} | "
                f"{_c(curve):+.1f}% | {mdd(curve):.1f}% |")

    return [
        "# \"안 산 것\" 이 값을 했나 — 평가 기준 낙폭과 노출을 맞춘 비교 (2026-08-13)",
        "",
        "앞선 측정(`2026-08-13-portfolio-vs-benchmark.md`)에서 전략이 매수보유에 "
        "졌다. 남은 반론이 하나 있었다 — **위험한 구간에서 안 산 것 자체가 값이 "
        "아니냐.** 그 반론이 성립하려면 두 가지가 필요하다. (1) 낙폭을 같은 "
        "기준으로 비교할 수 있어야 하고, (2) **같은 평균 노출을 상시 유지한 상대**"
        "를 이겨야 한다. 항상 절반만 태우는 것도 하락장에서 덜 잃지만 그건 "
        "공짜다.", "",
        f"기간 {start.date()} ~ {end.date()} ({years:.1f}년) · 전부 **매일 평가 기준** "
        "곡선이라 낙폭을 나란히 놓을 수 있다.", "",
        "| | 최종 자본 | 연 수익률 | MDD (평가 기준) |",
        "|---|---|---|---|",
        line("전략 (6bp, 평가 기준)", strat_mtm),
        line(f"매수보유 × {exp_strat * 100:.0f}% 상시 (전략과 같은 평균 노출)", bench_flat),
        line("매수보유 100%", bench),
        "",
        f"전략의 평균 노출은 자본의 {exp_strat * 100:.0f}% 다. **같은 노출을 아무 "
        "판단 없이 상시 유지한 줄**이 두 번째 줄이고, 전략은 거기에도 진다. "
        "낙폭까지 같은 기준으로 놓고 봐도 그렇다 — 덜 흔들린 대가로 덜 번 것이 "
        "아니라, **덜 벌고 덜 흔들린 폭도 공짜로 살 수 있는 만큼이었다.**", "",
        "## 종목 선택을 아예 빼면 — 노출만 조절",
        "",
        "패널 동일가중 지수가 자기 200일 이동평균 위면 100%, 아래면 현금. "
        "판정은 전날 종가까지의 정보만 쓴다(shift(1)). 종목 선택도, 셋업도, "
        f"진입 라인도 없다. 스위치는 {switches}번 움직였다.", "",
        "| | 최종 자본 | 연 수익률 | MDD (평가 기준) |",
        "|---|---|---|---|",
        line(f"200일선 오버레이 (평균 노출 {avg_exp * 100:.0f}%, 스위치 {switches}회)", overlay),
        line(f"매수보유 × {avg_exp * 100:.0f}% 상시 (같은 평균 노출, 공짜 방어)", flat),
        line(f"10개월선 오버레이 (월말 판정, 평균 노출 {m_exp * 100:.0f}%, 스위치 {m_switches}회)",
             m_overlay),
        line(f"매수보유 × {m_exp * 100:.0f}% 상시 (같은 평균 노출, 공짜 방어)", m_flat),
        line("매수보유 100%", bench),
        "",
        "**이 표가 반론의 답이다.** 오버레이가 같은 노출의 상시 보유를 이기면 "
        "타이밍이 값을 한 것이고, 못 이기면 \"안 산 것\" 은 그냥 덜 산 것이다. "
        "일봉 판정 하나로는 마찰만 잰 것일 수 있어 월말 판정을 같이 뒀다 — "
        "같은 아이디어의 가장 흔한 구현이고 스위치가 훨씬 적다.", "",
        f"**월말 판정은 같은 노출 상시보다 연 {_c(m_overlay) - _c(m_flat):+.1f}%p "
        f"앞선다.** 타이밍이 아무 값도 없다고는 말할 수 없다. 다만 낙폭은 "
        f"{mdd(m_overlay):.1f}% 대 {mdd(m_flat):.1f}% 로 **더 깊고**, "
        f"매수보유 100%({_c(bench):+.1f}%)는 못 이긴다. 방어를 샀는데 낙폭이 "
        "깊어졌다면 그건 방어가 아니다 — 떨어진 뒤에 팔고 오른 뒤에 사서 "
        "회복을 놓친 것이다.", "",
        f"**그리고 같은 아이디어인데 일봉 판정은 연 {_c(overlay):+.1f}% 다 — "
        f"월말 판정과 {_c(m_overlay) - _c(overlay):+.1f}%p 차이.** 판정 주기를 "
        "바꿨을 뿐인데 결과가 이만큼 움직이면, 잰 것은 신호가 아니라 **마찰**"
        "이다. 이 정도로 구현에 민감한 것을 실전에 켤 수는 없다.", "",
        "**주의: 표본이 한 번의 하락장(2022)뿐이다.** 추세 필터의 값은 큰 하락이 "
        "몇 번 오는지에 달려 있는데 5.9년에 한 번은 판정하기에 얇다. 여기서 "
        "이겼더라도 그것만으로 켤 근거는 안 됐을 것이다.", "",
        "재현: `python scripts/measure_portfolio.py` · 산수 점검 `... selftest`", "",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
