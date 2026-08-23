#!/usr/bin/env python
"""트레이드 플랜을 **아무도 안 본 구간**에서 다시 잰다.

    python scripts/measure_trade_plan_oos.py [워커수]

## 왜 필요한가

2026-07-24 측정은 "각 종목 최근 400봉"에서 기대값 +0.61R 을 냈다. 그런데
숏 레짐필터와 저확신 컷은 **그 결과를 보고 나서** 넣은 규칙이다. 규칙을 고른
구간에서 규칙을 재면 잘 나오는 게 당연하다.

이 저장소는 그 함정에 이미 한 번 빠졌다. 팩터 가중치 워크포워드
(data/walkforward_result.json, 2026-07-27):

    인샘플  IC +0.0169 (t +1.36)
    OOS     IC -0.0046 (t -0.28)      과최적화 폭 +0.0215

## 무엇을 하는가

`backtest_trade_plans` 는 봉을 하나씩 걸어가며 `build_trade_plan(df[:i+1])` 로
계획을 세운다 — **백테스트 안에는 미래 참조가 없다.** 문제는 백테스트가
아니라 규칙을 고른 사람이 본 구간이다. 그래서 나누는 기준도 "사람이 본 적
있는가" 다.

    IS   2024-12-20 ~          최근 400봉. 필터를 고를 때 본 구간.
    OOS  2020-03-31 ~ 12-19    그 앞. 규칙을 정할 때 아무도 안 봤다.

전 구간을 한 번에 돌린 뒤 **진입 봉의 날짜로 트레이드를 나눈다.** 구간을
잘라서 각각 돌리면 워밍업(MIN_BARS)이 구간마다 다시 필요해 경계 근처
셋업이 사라진다.

덤으로 연도별 표를 낸다. 지금 표본은 강세장에 심하게 쏠려 있다(백필 501일
중 421일이 bull) — 2022 하락장에서 어떻게 되는지가 이 규칙의 진짜 시험이다.

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
OUT_DIR = Path("docs/measurements")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
MIN_LEN = 120
# 러너 실설정을 그대로 쓴다 — `virtual_broker.LIMIT_FILL_WINDOW`/`PLAN_HOLD_WINDOW`
# 와 같은 값이다. 예전에는 15/30 을 손으로 적어 뒀는데, 러너가 안 쓰는 창으로
# 잰 숫자가 문서로 남아 "어느 판의 값인가" 를 잃어버렸다(2026-08-23).
FILL_WINDOW = int(os.environ.get("FILL_WINDOW", bt.DEFAULT_FILL_WINDOW))
HOLD_WINDOW = int(os.environ.get("HOLD_WINDOW", bt.DEFAULT_HOLD_WINDOW))

# 2026-07-24 측정이 쓴 "최근 400봉" 의 시작. 이 날 이후가 필터를 고를 때 본
# 구간이다. 숫자를 바꿀 일이 생기면 무엇을 본 적 있는지부터 다시 세야 한다.
IS_START = pd.Timestamp("2024-12-20")


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _run_ticker(args):
    """한 종목 전 구간 — 진입 날짜를 붙인 트레이드 목록."""
    tk, df = args
    out = bt.backtest_trade_plans(
        df, fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW)
    for t in out["trades"]:
        t["ticker"] = tk
        t["entry_date"] = df.index[t["idx"]]
    return out["trades"]


def _fmt(s: dict) -> str:
    wr, ex, ar = s["win_rate"], s["expectancy_r"], s["avg_r"]
    wr = " nan" if wr != wr else f"{wr * 100:4.0f}%"
    ex = "  nan" if ex != ex else f"{ex:+5.2f}R"
    ar = "  nan" if ar != ar else f"{ar:+5.2f}R"
    return (f"setups={s['setups']:5d}  filled={s['filled']:5d}  "
            f"W/L={s['wins']:4d}/{s['losses']:4d}  timeout={s['timeouts']:4d}  "
            f"winrate={wr}  expectancy={ex}  avg={ar}")


def _block(label: str, trades: list[dict]) -> str:
    lines = [f"  {label:22} {_fmt(bt._stats(trades))}"]
    for direction, dlab in (("long", "롱"), ("short", "숏")):
        sub = [t for t in trades if t["direction"] == direction]
        if sub:
            lines.append(f"    └ {dlab:20} {_fmt(bt._stats(sub))}")
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else max(os.cpu_count() - 1, 1)

    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    tasks = []
    for tk in tickers:
        df = _ohlcv(panel, tk)
        if len(df) >= MIN_LEN:
            tasks.append((tk, df))

    span = f"{tasks[0][1].index[0].date()} ~ {tasks[0][1].index[-1].date()}"
    print(f"{len(tasks)}종목 · {span} · 워커 {workers}", flush=True)

    trades: list[dict] = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(_run_ticker, tasks):
            trades += got
            done += 1
            if done % 25 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)}종목 · 누적 {len(trades)}건", flush=True)

    oos = [t for t in trades if t["entry_date"] < IS_START]
    ins = [t for t in trades if t["entry_date"] >= IS_START]

    body = [
        f"  ── OOS (규칙을 정할 때 안 본 구간, ~{(IS_START - pd.Timedelta(days=1)).date()}) ──",
        _block("전체", oos),
        "",
        f"  ── IS (필터를 고를 때 본 구간, {IS_START.date()}~) ──",
        _block("전체", ins),
        "",
        "  ── 연도별 (전체) ──",
    ]
    for year in sorted({t["entry_date"].year for t in trades}):
        sub = [t for t in trades if t["entry_date"].year == year]
        body.append(_block(str(year), sub))

    body += ["", "  ── OOS 확신도별 ──"]
    for conf in ("high", "medium", "low"):
        sub = [t for t in oos if t.get("confidence") == conf]
        if sub:
            body.append(_block(conf, sub))

    report = "\n".join(body)
    print("\n" + report)

    gap = bt._stats(ins)["expectancy_r"] - bt._stats(oos)["expectancy_r"]
    verdict = (f"과최적화 폭 {gap:+.2f}R (IS {bt._stats(ins)['expectancy_r']:+.2f}R "
               f"− OOS {bt._stats(oos)['expectancy_r']:+.2f}R)")
    print("\n" + verdict)

    today = _dt.date.today().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = (f"# 트레이드 플랜 OOS 검증 ({today})\n\n"
          f"- 유니버스: {len(tasks)}종목 · {span} (저장 패널)\n"
          f"- 나눈 기준: **사람이 본 적 있는 구간인가.** 2026-07-24 측정이 쓴 "
          f"'최근 400봉'({IS_START.date()}~)이 IS, 그 앞이 OOS\n"
          f"- 숏 레짐필터·저확신 컷은 IS 결과를 보고 넣은 규칙이다 — "
          f"그 구간에서 재면 잘 나오는 게 당연하다\n"
          f"- 백테스트 자체에는 미래 참조가 없다 "
          f"(`build_trade_plan(df[:i+1])`)\n"
          f"- **2026-08-16 정정 — 산 값이 바뀌었다.** 2026-08-09 판(+0.58R)은 "
          f"구간 상단만 닿아도 구간 **중간값**에 산 것으로 적었다. 시장에 "
          f"그 가격이 없었다. 지금은 러너가 실제로 거는 지정가(구간 상단, "
          f"갭이면 그날 시가)로 채점한다 — 셋업·체결 봉·승패는 그대로고 "
          f"**R 만** 다시 낸 값이다 "
          f"(`modules/trade_plan_backtest.placeable_r`, 근거는 "
          f"`2026-08-12-entry-rule-daily.md`)\n"
          f"- **2026-08-23 정정 — timeout 을 장부와 같이 센다.** 예전에는 "
          f"보유 상한을 넘긴 트레이드를 **0R**(안 판 것)로 셌는데, 장부는 "
          f"상한 다음 봉 시가에 실제로 팔고 그 R 을 받는다"
          f"(`virtual_broker.scan_plan_exit`). 지금은 백테스트도 그 값으로 "
          f"센다. 청산 봉이 패널 밖이면 0R 이 아니라 **미결로 뺀다**\n\n"
          f"```\n{report}\n```\n\n"
          f"**{verdict}**\n\n"
          f"비교 대상 — 팩터 가중치 워크포워드(`data/walkforward_result.json`)는 "
          f"IS +0.0169 / OOS −0.0046, 과최적화 폭 +0.0215 였다.\n\n"
          f"창: 체결 {FILL_WINDOW}봉 · 보유 {HOLD_WINDOW}봉 (러너와 같은 값)\n"
          f"생성: `python scripts/measure_trade_plan_oos.py`\n")
    out_path = OUT_DIR / f"{today}-trade-plan-oos.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"\n기록: {out_path}")


if __name__ == "__main__":
    main()
