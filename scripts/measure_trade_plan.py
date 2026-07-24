"""
측정 드라이버 — 유니버스 전체 트레이드 플랜 롱/숏 성능
========================================================
`modules.trade_plan_backtest.backtest_trade_plans` 를 저장된 가격 패널의 모든
종목에 돌려, **셋업을 방향·확신도별로 풀링**해 승률·기대값(R)을 낸다.
숏을 롱의 단순 미러로 짠 뒤 "숏이 롱보다 약한가?"를 유니버스 규모로 확인하고,
결과를 docs/measurements/ 에 기록한다 (팩터 비교 선례와 같은 규율).

    python scripts/measure_trade_plan.py [bars=400] [ticker_limit]

입력  data/price_panel_v1.parquet  (MultiIndex 컬럼 (Price,Ticker), 일봉)
출력  콘솔 요약 + docs/measurements/<날짜>-trade-plan-short-vs-long.md
네트워크 無 — 저장 패널만 읽는다.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from modules import trade_plan_backtest as bt  # noqa: E402

PANEL = Path("data/price_panel_v1.parquet")
OUT_DIR = Path("docs/measurements")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MIN_LEN = 120          # 이보다 짧으면 종목 건너뜀
FILL_WINDOW = 15
HOLD_WINDOW = 30


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _fmt(s: dict) -> str:
    """stats dict → 한 줄 (nan 안전)."""
    wr = s["win_rate"]
    ex = s["expectancy_r"]
    ar = s["avg_r"]
    wr = " nan" if wr != wr else f"{wr * 100:4.0f}%"
    ex = "  nan" if ex != ex else f"{ex:+5.2f}R"
    ar = "  nan" if ar != ar else f"{ar:+5.2f}R"
    return (f"setups={s['setups']:5d}  filled={s['filled']:5d}  nofill={s['nofill']:5d}  "
            f"W/L={s['wins']:4d}/{s['losses']:4d}  timeout={s['timeouts']:4d}  "
            f"winrate={wr}  expectancy={ex}  avg={ar}")


def _table(trades: list[dict]) -> str:
    lines = []
    for label, subset in [
        ("전체", trades),
        ("롱  ", [t for t in trades if t["direction"] == "long"]),
        ("숏  ", [t for t in trades if t["direction"] == "short"]),
    ]:
        lines.append(f"  {label}  {_fmt(bt._stats(subset))}")
    # 확신도 × 방향 분해
    lines.append("  ── 확신도 × 방향 ──")
    for conf in ("high", "medium", "low"):
        for direction, dlab in (("long", "롱"), ("short", "숏")):
            sub = [t for t in trades
                   if t.get("confidence") == conf and t["direction"] == direction]
            if sub:
                lines.append(f"  {conf:6} {dlab}  {_fmt(bt._stats(sub))}")
    return "\n".join(lines)


def main() -> None:
    try:  # Windows 콘솔이 cp949 라도 한글/기호를 찍을 수 있게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    bars = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    if limit:
        tickers = tickers[:limit]

    all_trades: list[dict] = []
    used = 0
    for tk in tickers:
        df = _ohlcv(panel, tk).tail(bars)
        if len(df) < MIN_LEN:
            continue
        out = bt.backtest_trade_plans(df, fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW)
        for t in out["trades"]:
            t["ticker"] = tk
        all_trades += out["trades"]
        used += 1

    today = _dt.date.today().isoformat()
    header = (f"트레이드 플랜 롱/숏 성능 — {today}\n"
              f"유니버스 {used}종목 · 최근 {bars}봉 · fill≤{FILL_WINDOW}·hold≤{HOLD_WINDOW}봉 "
              f"· 손절 우선(보수적)\n")
    body = _table(all_trades)
    report = header + "\n" + body + "\n"
    print(report)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = (f"# 트레이드 플랜 롱/숏 성능 측정 ({today})\n\n"
          f"- 유니버스: {used}종목 (저장 패널 `data/price_panel_v1.parquet`)\n"
          f"- 구간: 각 종목 최근 {bars}봉\n"
          f"- 체결 판정: 진입 구간에 되돌림 닿으면 체결, fill≤{FILL_WINDOW}봉 미도달이면 미체결\n"
          f"- 결판: 체결 후 hold≤{HOLD_WINDOW}봉 내 손절/목표 선착, 같은 봉이면 손절 우선(보수적)\n"
          f"- R: 위험 1단위 기준. 목표=+R:R, 손절=-1.0, timeout=0\n\n"
          f"```\n{body}\n```\n\n"
          f"생성: `python scripts/measure_trade_plan.py {bars}`\n")
    out_path = OUT_DIR / f"{today}-trade-plan-short-vs-long.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"\n기록: {out_path}")


if __name__ == "__main__":
    main()
