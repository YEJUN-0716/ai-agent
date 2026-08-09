#!/usr/bin/env python
"""트레이드 플랜이 거래비용을 얼마나 견디나 — 손익분기 비용을 구한다.

    python scripts/measure_trade_cost_sensitivity.py [워커수]
    python scripts/measure_trade_cost_sensitivity.py --reuse    # 직전 트레이드 재사용

## 왜 이게 다음 측정인가

2026-08-09 OOS 검증에서 기대값 +0.58R 이 나왔고 과최적화 폭은 -0.01R 이었다.
하지만 그 숫자는 **수수료도 슬리피지도 0 인 세계**의 것이다. 이 규칙은
6.4년에 13,336건을 체결한다 — 비용이 한 번에 조금씩만 깎아도 합계는 크다.

## R 로는 비용을 못 잰다

R 은 위험 1단위 기준이라 가격 정보가 없다. 같은 +1R 이어도

    손절이 진입가에서 1% 떨어져 있으면  →  왕복 20bp 는 0.20R 을 먹는다
    손절이 진입가에서 5% 떨어져 있으면  →  같은 20bp 가 0.04R

**다섯 배 차이다.** 그래서 트레이드마다 위험이 가격의 몇 %였는지를 보고
비용을 R 로 환산한다. trade_plan_backtest 가 entry_ref·stop_price·
target_price 를 남기는 이유가 이것이다.

    비용(주당) = c × 진입가 + c × 청산가
    비용(R)    = 비용(주당) / |진입가 - 손절가|

청산가는 win 이면 목표, loss 면 손절, timeout 이면 진입가로 본다.

## 어느 비용 수준을 봐야 하나

미국 주식을 한국 증권사로 사면 수수료(편도 7~25bp)에 환전 스프레드가
붙는다. 그래서 **편도 10~35bp** 구간이 실제로 걸리는 자리다. 여기서는
숫자를 단정하지 않고 곡선을 내고 손익분기점을 표시한다 — 자기 계좌의
실제 비용은 자기가 안다.

네트워크 無 — 저장 패널만 읽는다.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules import trade_plan_backtest as bt  # noqa: E402

PANEL = Path("data/price_panel_v1.parquet")
CACHE = Path("data/trade_plan_trades.parquet")     # data/* 는 gitignore 대상
OUT_DIR = Path("docs/measurements")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MIN_LEN = 120
FILL_WINDOW = 15
HOLD_WINDOW = 30

# 편도 비용(bp). 왕복은 이 값의 두 배가 든다.
COST_BP = [0, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100]


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _run_ticker(args):
    tk, df = args
    out = bt.backtest_trade_plans(
        df, fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW)
    rows = []
    for t in out["trades"]:
        if t["outcome"] == "nofill":
            continue                       # 체결 안 된 셋업은 비용도 안 든다
        rows.append({
            "ticker": tk, "entry_date": df.index[t["idx"]],
            "direction": t["direction"], "confidence": t["confidence"],
            "outcome": t["outcome"], "r": t["r"],
            "entry_ref": t["entry_ref"], "stop_price": t["stop_price"],
            "target_price": t["target_price"],
        })
    return rows


def _collect(workers: int) -> pd.DataFrame:
    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    tasks = [(tk, df) for tk in tickers
             if len(df := _ohlcv(panel, tk)) >= MIN_LEN]
    print(f"{len(tasks)}종목 · 워커 {workers}", flush=True)

    rows, done = [], 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(_run_ticker, tasks):
            rows += got
            done += 1
            if done % 50 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)}종목 · 체결 {len(rows)}건", flush=True)

    df = pd.DataFrame(rows)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE)
    return df


def add_cost_columns(df: pd.DataFrame) -> pd.DataFrame:
    """위험폭(%)과 청산가를 붙인다. 비용 환산의 재료."""
    df = df.copy()
    risk = (df["entry_ref"] - df["stop_price"]).abs()
    df["risk_per_share"] = risk
    df["risk_pct"] = risk / df["entry_ref"] * 100.0

    exit_price = np.where(df["outcome"] == "win", df["target_price"],
                          np.where(df["outcome"] == "loss", df["stop_price"],
                                   df["entry_ref"]))
    df["exit_price"] = exit_price
    # 진입가 + 청산가에 각각 편도 비용이 붙는다. 1bp 당 R 로 환산해 둔다.
    df["r_cost_per_bp"] = ((df["entry_ref"] + df["exit_price"]) * 1e-4
                           / df["risk_per_share"])
    return df[df["risk_per_share"] > 0]


def net_expectancy(df: pd.DataFrame, cost_bp: float) -> float:
    """편도 cost_bp 를 물렸을 때의 체결 트레이드 평균 R."""
    if df.empty:
        return float("nan")
    return float((df["r"] - df["r_cost_per_bp"] * cost_bp).mean())


def breakeven_bp(df: pd.DataFrame) -> float:
    """평균 순 R 이 0 이 되는 편도 비용(bp). 비용에 선형이라 나눗셈 한 번."""
    if df.empty:
        return float("nan")
    gross = float(df["r"].mean())
    per_bp = float(df["r_cost_per_bp"].mean())
    return gross / per_bp if per_bp > 0 else float("nan")


def _curve(label: str, df: pd.DataFrame) -> str:
    if df.empty:
        return f"  {label:16} (표본 없음)"
    cells = "  ".join(f"{net_expectancy(df, c):+5.2f}" for c in COST_BP)
    return f"  {label:16} n={len(df):6d}  {cells}   손익분기 {breakeven_bp(df):5.1f}bp"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = [a for a in sys.argv[1:] if a != "--reuse"]
    reuse = "--reuse" in sys.argv
    workers = int(args[0]) if args else max(os.cpu_count() - 1, 1)

    if reuse and CACHE.exists():
        raw = pd.read_parquet(CACHE)
        print(f"직전 트레이드 재사용: {CACHE} ({len(raw)}건)")
    else:
        raw = _collect(workers)

    df = add_cost_columns(raw)

    head = "편도비용(bp) → " + "  ".join(f"{c:5d}" for c in COST_BP)
    body = ["  위험폭(손절까지 거리) 분포 — 비용 민감도를 정하는 값",
            f"    중앙값 {df['risk_pct'].median():.2f}% · "
            f"하위25% {df['risk_pct'].quantile(.25):.2f}% · "
            f"상위25% {df['risk_pct'].quantile(.75):.2f}%",
            "",
            f"  {'':16} {'':8}  {head}", ""]

    body.append(_curve("전체", df))
    for d, lab in (("long", "롱"), ("short", "숏")):
        body.append(_curve(lab, df[df["direction"] == d]))
    body.append("")

    # 규칙을 고를 때 안 본 구간에서도 같은 비용을 견디는지. 경계는 OOS 측정과
    # 같은 자를 쓴다 — 두 곳에 날짜를 적으면 언젠가 갈라진다.
    from scripts.measure_trade_plan_oos import IS_START
    body.append(f"  ── 구간별 (OOS 경계 {IS_START.date()}) ──")
    body.append(_curve("OOS", df[df["entry_date"] < IS_START]))
    body.append(_curve("IS", df[df["entry_date"] >= IS_START]))
    body.append("")

    body.append("  ── 위험폭 4분위별 (좁은 손절이 먼저 죽는다) ──")
    q = df["risk_pct"].quantile([.25, .5, .75]).tolist()
    bands = [("Q1 좁음", df[df["risk_pct"] <= q[0]]),
             ("Q2", df[(df["risk_pct"] > q[0]) & (df["risk_pct"] <= q[1])]),
             ("Q3", df[(df["risk_pct"] > q[1]) & (df["risk_pct"] <= q[2])]),
             ("Q4 넓음", df[df["risk_pct"] > q[2]])]
    for lab, sub in bands:
        body.append(_curve(lab, sub))

    body.append("")
    body.append("  ── 확신도별 ──")
    for conf in ("high", "medium", "low"):
        body.append(_curve(conf, df[df["confidence"] == conf]))

    report = "\n".join(body)
    print("\n" + report)

    be = breakeven_bp(df)
    print(f"\n전체 손익분기 편도 {be:.1f}bp (왕복 {be * 2:.1f}bp)")

    today = _dt.date.today().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = (f"# 트레이드 플랜 거래비용 민감도 ({today})\n\n"
          f"- 체결 {len(df):,}건 · {raw['entry_date'].min().date()} ~ "
          f"{raw['entry_date'].max().date()}\n"
          f"- 비용(R) = (진입가 + 청산가) × 편도비용 / |진입가 − 손절가|\n"
          f"- 청산가: win=목표, loss=손절, timeout=진입가\n"
          f"- 표의 숫자는 **그 비용에서의 평균 순 R** (체결 트레이드 기준)\n\n"
          f"```\n{report}\n```\n\n"
          f"**전체 손익분기: 편도 {be:.1f}bp (왕복 {be * 2:.1f}bp)**\n\n"
          f"미국 주식을 한국 증권사로 사면 수수료(편도 7~25bp)에 환전 "
          f"스프레드가 붙는다. 실제로 걸리는 자리는 편도 10~35bp 구간이다.\n\n"
          f"생성: `python scripts/measure_trade_cost_sensitivity.py`\n")
    out_path = OUT_DIR / f"{today}-trade-plan-cost-sensitivity.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"기록: {out_path}")


if __name__ == "__main__":
    main()
