#!/usr/bin/env python
"""트레이드 플랜이 거래비용을 얼마나 견디나 — 손익분기 비용을 구한다.

    python scripts/measure_trade_cost_sensitivity.py [워커수]
    python scripts/measure_trade_cost_sensitivity.py --reuse    # 직전 트레이드 재사용

## 왜 이게 다음 측정인가

2026-08-09 OOS 검증에서 기대값 +0.58R 이 나왔고 과최적화 폭은 -0.01R 이었다.
하지만 그 숫자는 **수수료도 슬리피지도 0 인 세계**의 것이다. 이 규칙은
6.4년에 13,336건을 체결한다 — 비용이 한 번에 조금씩만 깎아도 합계는 크다.

**2026-08-16 정정.** 그 +0.58R 은 걸 수 없는 체결가(구간 중간값) 위에서 잰
값이었다. `trade_plan_backtest` 가 실제 지정가 체결로 바뀌었고 이 표도 그
위에서 다시 냈다 — 아래 손익분기 bp 는 예전 판(편도 45.8bp / 51.8bp)과
비교하면 안 된다.

## R 로는 비용을 못 잰다

R 은 위험 1단위 기준이라 가격 정보가 없다. 같은 +1R 이어도

    손절이 진입가에서 1% 떨어져 있으면  →  왕복 20bp 는 0.20R 을 먹는다
    손절이 진입가에서 5% 떨어져 있으면  →  같은 20bp 가 0.04R

**다섯 배 차이다.** 그래서 트레이드마다 위험이 가격의 몇 %였는지를 보고
비용을 R 로 환산한다. trade_plan_backtest 가 entry_ref·stop_price·
target_price 를 남기는 이유가 이것이다.

    비용(주당) = c × 진입가 + c × 청산가
    비용(R)    = 비용(주당) / |진입가 - 손절가|

청산가는 win 이면 목표, loss 면 손절, timeout 이면 보유 상한 다음 봉 시가다
(갭이면 그 시가 — `placeable_r` 이 걸 수 있는 값으로 다시 낸다).

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

import pandas as pd  # noqa: E402

from modules import trade_plan_backtest as bt  # noqa: E402

PANEL = Path("data/price_panel_v1.parquet")
CACHE = Path("data/trade_plan_trades.parquet")     # data/* 는 gitignore 대상
OUT_DIR = Path("docs/measurements")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MIN_LEN = 120
# 러너 실설정을 그대로 쓴다 — `virtual_broker.LIMIT_FILL_WINDOW`/`PLAN_HOLD_WINDOW`
# 와 같은 값이다. 손으로 적어 둔 15/30 은 러너가 안 쓰는 창이었다(2026-08-23).
FILL_WINDOW = int(os.environ.get("FILL_WINDOW", bt.DEFAULT_FILL_WINDOW))
HOLD_WINDOW = int(os.environ.get("HOLD_WINDOW", bt.DEFAULT_HOLD_WINDOW))

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
        # "open" = 청산 봉이 패널 밖이라 아직 들고 있다 — 손익이 아직 없다.
        if t["outcome"] in ("nofill", "open"):
            continue                       # 체결 안 된 셋업은 비용도 안 든다
        rows.append({
            "ticker": tk, "entry_date": df.index[t["idx"]],
            "direction": t["direction"], "confidence": t["confidence"],
            "outcome": t["outcome"], "r": t["r"],
            "entry_ref": t["entry_ref"], "stop_price": t["stop_price"],
            "target_price": t["target_price"],
            # 비용은 **실제로 낸 값**에 붙는다. 지정가(구간 상단)에 채워지므로
            # entry_ref 가 아니다 — 2026-08-16 정정. 청산도 같다: 갭을 지나간
            # 날은 손절가·목표가가 아니라 그날 시가에 나간다.
            "fill_price": t["fill_price"], "exit_price": t["exit_price"],
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

    if "exit_price" not in df:
        raise SystemExit(
            "저장된 트레이드에 exit_price 가 없습니다 — 손절가·목표가로 되돌아가면\n"
            "갭을 지나간 날의 시장에 없던 가격으로 비용을 잽니다.\n"
            "--reuse 를 빼고 다시 돌리세요 (2026-08-16 이후 판이 필요합니다).")
    # 진입가 + 청산가에 각각 편도 비용이 붙는다. 1bp 당 R 로 환산해 둔다.
    # 분자는 **실제 체결가**(지정가 = 구간 상단, 갭이면 시가), 분모는 플랜
    # 위험이다 — 러너가 사이징하는 값이 플랜 위험이라 R 의 단위가 그것이다.
    df["r_cost_per_bp"] = ((df["fill_price"] + df["exit_price"]) * 1e-4
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

    # 제안 규칙 — "숏 제외 + 손절 ≥ X%". 문턱을 하나만 적으면 그 자리가 왜
    # 거기인지 알 수 없으므로 사다리로 낸다. 지금 프로덕션은 롱 + 등급 A/B
    # (= 손절 > 1.75%) 다 — 새 문턱은 그것보다 나은지로 판단할 것.
    body.append("")
    body.append("  ── 제안 규칙: 숏 제외 + 손절 ≥ X% (프로덕션 현재 = 1.75%) ──")
    long_only = df[df["direction"] == "long"]
    for thr in (0.0, 1.5, 1.75, 2.0, 2.34, 3.0):
        body.append(_curve(f"롱 손절≥{thr:.2f}%", long_only[long_only["risk_pct"] >= thr]))
    picked = long_only[long_only["risk_pct"] >= 2.0]
    body.append(f"    └ 2.00% 구간별: "
                f"OOS {breakeven_bp(picked[picked['entry_date'] < IS_START]):.1f}bp · "
                f"IS {breakeven_bp(picked[picked['entry_date'] >= IS_START]):.1f}bp")

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
          f"- 비용(R) = (체결가 + 청산가) × 편도비용 / |entry_ref − 손절가|\n"
          f"- 청산가: win=목표, loss=손절, timeout=보유 상한 다음 봉 시가\n"
          f"- **2026-08-16 정정 ①.** 체결가는 러너가 거는 지정가(구간 상단, "
          f"갭이면 그날 시가)다 — 예전 판은 여기에 구간 중간값을 넣어 "
          f"손익분기를 편도 45.8bp 로 냈다. 걸 수 없는 가격이었다\n"
          f"- **2026-08-16 정정 ②.** 청산가도 같은 규칙이다. 손절은 아래로 "
          f"갭하면 그날 시가에 나간다 — 롱 손절의 **13.5%** 가 손절가보다 "
          f"나쁘게 청산된다. 손절가를 그대로 적던 판은 전체 손익분기를 "
          f"11.7bp 로 냈다\n"
          f"- **2026-08-23 정정 ③.** timeout 을 장부와 같이 센다 — 보유 상한 "
          f"다음 봉 시가에 실제로 판 값이다. 예전 판은 0R(안 판 것)로 셌다\n"
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
