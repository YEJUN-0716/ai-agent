#!/usr/bin/env python
"""백필에 세 번째 슬러그를 채운다 — `quant_pit`.

    python scripts/backfill_quant_pit.py            # 501일 전부
    python scripts/backfill_quant_pit.py --limit 5  # 5종목만 (연기 테스트)

설계서: `docs/superpowers/specs/2026-08-13-quant-pit-design.md` 5절.

## 기존 백필을 **덮어쓰지 않고 얹는다**

`data/analyst_log_backfill/` 는 2024-07-23 ~ 2026-07-22, 501거래일 ·
276종목이고 슬러그는 `chart`·`ict` 둘뿐이다. 차트·ICT 가 실패한 그 표본이고,
`quant_pit` 은 **같은 날짜에** 세 번째 슬러그를 채워 넣는다 — 같은 자로 재야
비교가 된다. 날짜·국면·기존 점수는 그대로 두고 슬러그만 더한다.

`analyst_log.write_days` 는 연도 파일을 통째로 다시 쓰므로, 읽은 날을 **전부**
다시 넘긴다. 한 해만 넘기면 나머지 해가 지워진다.

## 네트워크를 안 쓴다

가격은 `data/price_panel_v1.parquet`, 재무는 `data/edgar_raw/` 캐시다.
`backfill_analyst_log.py` 가 yfinance 를 부르는 것과 다르다 — 같은 입력이면
같은 결과가 나와야 재현이 되고, 이 측정은 재현이 전부다.

## 슬러그를 `quant` 로 안 쓰는 이유

`quant` 는 화면과 실기록의 현행 재무 점수(yfinance `.info`)다. 재구성한 값을
그 이름으로 쓰면 성적표가 무엇을 잰 건지 아무도 말할 수 없게 된다 —
실기록과 백필 파일을 가른 것과 같은 이유다.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

SLUG = "quant_pit"
PANEL = os.environ.get("PANEL", "data/price_panel_v1.parquet")
FIELDS = ("High", "Low", "Close")

# backfill_analyst_log.py 와 같은 창·같은 최소 봉수. MDD·52주위치가 chart·ict
# 와 같은 재료를 보게 하려면 같은 값이어야 한다.
WINDOW_DAYS = 400
MIN_BARS = 60


def _score_ticker(task):
    """한 종목의 전 날짜 점수 — (ticker, {날짜: 점수}). 워커 프로세스에서 돈다."""
    ticker, df, dates = task
    from modules import quant_pit

    parts = quant_pit.load_ticker(ticker)
    if parts is None:
        return ticker, {}

    out = {}
    for date in dates:
        asof = pd.Timestamp(date)
        cut = df[(df.index <= asof) & (df.index > asof - pd.Timedelta(days=WINDOW_DAYS))]
        if len(cut) < MIN_BARS:
            continue
        score = quant_pit.score_from(parts, asof, cut)
        if score is not None:
            out[date] = score
    return ticker, out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(os.cpu_count() - 1, 1))
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 종목만 (연기 테스트)")
    args = ap.parse_args()

    from modules import analyst_log

    days = analyst_log.load_days(analyst_log.BACKFILL_DIRNAME)
    if not days:
        print("백필 기록이 없다 — backfill_analyst_log.py 를 먼저 돌릴 것.",
              file=sys.stderr)
        return 1
    dates = [d["date"] for d in days]
    print(f"백필 {len(days)}일 · {dates[0]} ~ {dates[-1]}")

    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    if args.limit:
        tickers = tickers[:args.limit]

    lo = pd.Timestamp(dates[0]) - pd.Timedelta(days=WINDOW_DAYS)
    hi = pd.Timestamp(dates[-1])
    tasks = []
    for tk in tickers:
        df = pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()
        df = df[(df.index >= lo) & (df.index <= hi)]
        if len(df) >= MIN_BARS:
            tasks.append((tk, df, dates))
    print(f"종목 {len(tasks)} · 워커 {args.workers} · 예상 {len(tasks) * len(dates):,}건")

    by_date: dict[str, dict[str, float]] = {}
    scored = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, (ticker, scores) in enumerate(pool.map(_score_ticker, tasks), 1):
            for date, score in scores.items():
                by_date.setdefault(date, {})[ticker] = score
            scored += len(scores)
            if i % 25 == 0 or i == len(tasks):
                print(f"  {i}/{len(tasks)}종목", flush=True)

    # 기존 행에 슬러그만 얹는다. 그 날 그 종목에 chart·ict 가 없어도 넣는다 —
    # ① 의 단면 IC 는 슬러그별로 따로 세고, ② 의 총괄 판정만 3인을 다 요구한다.
    records = []
    for day in days:
        scores = {tk: dict(row) for tk, row in day.get("scores", {}).items()}
        for tk, score in by_date.get(day["date"], {}).items():
            scores.setdefault(tk, {})[SLUG] = score
        records.append((day["date"], day.get("regime", "neutral"), scores))

    n = analyst_log.write_days(records, analyst_log.BACKFILL_DIRNAME)

    have = [len(by_date.get(d, {})) for d in dates]
    print(f"\n{n}일 기록 → {analyst_log.BACKFILL_DIRNAME} · {SLUG} {scored:,}건")
    print(f"날짜당 종목 수 최소 {min(have)} · 중앙 {int(pd.Series(have).median())} "
          f"· 최대 {max(have)} (유니버스 {len(tasks)})")
    print("빠진 종목은 EDGAR 태그가 없거나 TTM 구간에 분기 구멍이 있는 쪽이다 "
          "— 중립값으로 채우지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
