#!/usr/bin/env python
"""15분봉 패널을 Alpaca 에서 받아 parquet 로 저장한다.

    set -a && . ./.env && set +a
    python scripts/fetch_intraday_panel.py [종목수] [년수]

한 번만 받으면 된다. 이후 측정(measure_trade_plan_intraday.py)은 네트워크를
안 탄다.

유니버스가 30종목인 이유: 백테스트가 봉마다 build_trade_plan 을 재계산하는데
15분봉은 일봉의 26배다. S&P 500 전체로는 며칠 걸린다. 단타는 스프레드가
비용의 전부라 유동성 최상위로 재는 편이 오히려 맞다.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from modules.alpaca_data import get_bars  # noqa: E402
from modules.intraday_session import regular_hours  # noqa: E402

OUT = Path("data/intraday_panel_15m.parquet")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

# paper_trade_runner_toss.UNIVERSE_PRESETS["S&P 500 대형 30"] 과 같은 목록.
# 러너를 import 하면 토스 설정까지 딸려 오므로 여기 적어 둔다.
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JPM", "V",
    "JNJ", "UNH", "XOM", "PG", "HD", "MA", "ABBV", "MRK", "KO", "PEP",
    "COST", "AVGO", "LLY", "WMT", "MCD", "CRM", "ADBE", "CSCO", "ACN", "TMO",
]

# 한 종목씩 부른다. Alpaca 는 **장외 봉까지** 주므로(04:00~20:00 ET, 하루
# 64봉) 정규장 기준으로 셈하면 페이지 수가 네 배로 어긋난다 — 5종목씩
# 묶었더니 60페이지 상한에 걸렸다. 한 종목이면 3년 ≈ 48,000봉 = 5페이지라
# 예측이 선다.
CHUNK = 1
SLEEP_SEC = 0.3
MAX_PAGES = 40


def main() -> int:
    # 이 모듈들의 진단 메시지에 em-dash 가 있다. 윈도우 기본 콘솔(cp949)이
    # 그걸 못 찍어 UnicodeEncodeError 로 수집 전체가 죽는다.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    n_tickers = int(sys.argv[1]) if len(sys.argv) > 1 else len(UNIVERSE)
    years = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    if not os.environ.get("ALPACA_API_KEY"):
        print("ALPACA_API_KEY 가 없습니다. `set -a && . ./.env && set +a` 먼저.",
              file=sys.stderr)
        return 1

    tickers = UNIVERSE[:n_tickers]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(years * 365))
    print(f"{len(tickers)}종목 · {start.date()} ~ {end.date()} · 15Min", flush=True)

    frames: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        got = get_bars(chunk, timeframe="15Min", start=start, end=end,
                       max_pages=MAX_PAGES)
        for tk, df in got.items():
            df = regular_hours(df).dropna(subset=["Close"])
            if len(df):
                frames[tk] = df
        print(f"  {min(i + CHUNK, len(tickers))}/{len(tickers)}종목 · "
              f"{sum(len(d) for d in frames.values()):,}봉 · "
              f"{time.time() - t0:.0f}초", flush=True)
        time.sleep(SLEEP_SEC)

    if not frames:
        print("받은 봉이 없습니다.", file=sys.stderr)
        return 1

    # sort=True 를 명시한다. pandas 가 기본값을 뒤집을 예정이라, 안 적으면
    # 어느 날 시간축이 뒤섞인 패널이 조용히 나온다.
    panel = pd.concat(
        {(f, tk): frames[tk][f] for tk in frames for f in FIELDS},
        axis=1, sort=True)
    panel.columns = pd.MultiIndex.from_tuples(panel.columns)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT)

    span = f"{panel.index[0]} ~ {panel.index[-1]}"
    print(f"\n저장: {OUT} · {len(frames)}종목 · {len(panel):,}행 · {span}")
    print(f"파일 크기: {OUT.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
