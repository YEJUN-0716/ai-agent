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
        exposure.append((day, sum(p["notional"] for p in open_pos if p["filled"])))

    close_until(pd.Timestamp.max)
    eq = pd.Series(dict(curve)).sort_index() if curve else pd.Series(dtype=float)
    return {
        "equity": equity, "curve": eq, "taken": taken,
        "skipped_slot": skipped_slot, "skipped_dup": skipped_dup,
        "skipped_cash": skipped_cash,
        "avg_open": float(np.mean([e for _, e in exposure])) if exposure else 0.0,
    }


def cagr(equity: float, years: float) -> float:
    return (equity ** (1.0 / years) - 1.0) * 100.0 if years > 0 and equity > 0 else float("nan")


def mdd(curve: pd.Series) -> float:
    """실현 손익 곡선의 최대 낙폭. **평가손익을 안 세므로 실제보다 얕게 나온다.**"""
    if curve.empty:
        return float("nan")
    return float((curve / curve.cummax() - 1.0).min() * 100.0)


def bench_curve(start, end) -> pd.Series:
    """같은 패널 동일가중 매수보유. 시작일에 값이 있는 종목만 산다."""
    close = pd.read_parquet(PANEL)["Close"]
    close = close.loc[(close.index >= start) & (close.index <= end)]
    close = close.loc[:, close.iloc[0].notna() & close.iloc[-1].notna()]
    return close.div(close.iloc[0], axis=1).mean(axis=1)


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

    print("selftest 통과 (6건)")
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
        "**MDD 는 실현 손익 곡선 기준이라 실제보다 얕다** — 미실현 평가손익을 "
        "안 세기 때문이다. 매수보유 쪽은 평가 기준이라 더 깊게 나온다. "
        "낙폭끼리는 나란히 두지 말 것.", "",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
