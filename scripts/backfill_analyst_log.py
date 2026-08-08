#!/usr/bin/env python
"""과거 봉으로 차트·ICT 점수를 되돌려 계산한다 — 성적표 표본을 앞당기는 도구.

    python scripts/backfill_analyst_log.py --years 2

결과는 `data/analyst_log_backfill/YYYY.jsonl`. **실기록과 파일을 가른다.**

## 왜 되는가

차트·ICT 점수는 가격 데이터만의 함수다. 그 날짜까지 패널을 자르면 그 날
화면에 떴던 점수가 그대로 나온다. 실기록 5일치 × 8종목으로 확인했다:

    ict    40/40 완전 일치
    chart  30/40 완전 일치 · 평균오차 0.29 · 최대 2.39

chart 가 미세하게 어긋나는 건 야후가 배당·분할로 과거 수정주가를 계속
갱신하기 때문으로 보인다(확정 안 함). 0~100 척도에서 2점이면 단면 순위는
거의 안 움직여 IC 에는 영향이 없지만, **"그날 화면에 뜬 값"은 아니다.**
그래서 실기록과 섞지 않는다.

## 왜 quant·verdict 는 없는가

재무 점수는 그날 조회한 yfinance `.info` 라 시점 데이터다. 지금 다시 받으면
다른 값이 나오고, 과거로 되돌릴 방법이 없다(`pit_fundamentals.db` 는 12행짜리
껍데기다). 그래서 백필은 `chart`·`ict` 두 개뿐이고, **화면 총괄 판정(verdict)
성적표의 시계는 이 도구로 앞당길 수 없다.**

## 생존자 편향 — 멀리 갈수록 심해진다

오늘 살아남은 종목으로 과거를 잰다. 그 사이 지수에서 빠진 종목은 대체로
성적이 나빴던 쪽이라 IC 가 실제보다 좋게 나온다. 기본값을 2년으로 둔 이유가
이것이다. 늘리기 전에 modules/survivorship_check.py 를 읽을 것.
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 점수 계산에 실제로 쓰는 창. 프로덕션(signal_worker.ANALYST_PANEL_DAYS)과
# 같은 값이어야 같은 점수가 나온다.
WINDOW_DAYS = 400
MIN_BARS = 60           # signal_worker.ANALYST_MIN_BARS 와 같은 규칙
REGIME_BENCHMARK = "SPY"


def _score_ticker(task):
    """한 종목의 전 날짜 점수 — {날짜: {"chart": x, "ict": y}}.

    워커 프로세스에서 돈다. 무거운 import 는 여기 안에서 한다 — 부모가
    가진 것을 자식이 물려받지 못하는 spawn(윈도우) 에서도 돌아야 한다.
    """
    ticker, df, dates = task
    import app as core
    from modules import analyst_team
    from modules.ict_analysis import calc_ict_adjustment, ict_factor_score

    out = {}
    for date in dates:
        asof = pd.Timestamp(date)
        cut = df[(df.index <= asof) & (df.index > asof - pd.Timedelta(days=WINDOW_DAYS))]
        if len(cut) < MIN_BARS:
            continue

        row = {}
        # 실기록과 같은 규칙 — 계산 실패는 키를 뺀다. 중립값 50 으로 채우면
        # '계산 불가'가 '중립 판단'으로 성적에 섞인다.
        try:
            t_score, _ = core.technical_score(cut)
            mom = core.calc_momentum(cut) or {}
            row["chart"] = analyst_team.chart_score(t_score, mom.get("score", 50.0))
        except Exception:
            pass
        try:
            row["ict"] = analyst_team.ict_score(
                ict_factor_score(cut), calc_ict_adjustment(cut)["adjustment"])
        except Exception:
            pass

        if row:
            out[date] = row
    return ticker, out


def regimes_for(dates):
    """날짜별 시장 국면 — 실기록과 같은 자(app.regime_of)로 찍는다."""
    import app as core
    import yfinance as yf

    start = pd.Timestamp(dates[0]) - pd.Timedelta(days=WINDOW_DAYS)
    spy = yf.download(REGIME_BENCHMARK, start=start,
                      end=pd.Timestamp(dates[-1]) + pd.Timedelta(days=1),
                      progress=False, auto_adjust=True)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.droplevel(1)
    closes = spy["Close"].dropna()
    return {d: core.regime_of(closes[closes.index <= pd.Timestamp(d)])[0]
            for d in dates}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0,
                    help="몇 년치를 되돌릴지 (기본 2 — 생존자 편향 때문)")
    ap.add_argument("--workers", type=int, default=max(os.cpu_count() - 1, 1))
    args = ap.parse_args()

    import signal_worker
    from modules import analyst_log, price_panel

    tickers = signal_worker._resolve_universe(
        os.environ.get("UNIVERSE", signal_worker.ANALYST_LOG_UNIVERSE))
    if not tickers:
        print("유니버스가 비었다.", file=sys.stderr)
        return 1

    # 실기록이 시작된 날 **앞에서 멈춘다.** 겹치면 어느 쪽이 사실인지
    # 매번 따져야 하는데, 안 겹치게 만들면 그 질문 자체가 안 생긴다.
    live = analyst_log.load_days()
    if not live:
        print("실기록이 없다 — 백필 끝 날짜를 정할 수 없다.", file=sys.stderr)
        return 1
    live_start = pd.Timestamp(live[0]["date"])
    end = live_start - pd.Timedelta(days=1)
    start = live_start - pd.Timedelta(days=int(args.years * 365))

    print(f"유니버스 {len(tickers)}종목 · {start.date()} ~ {end.date()} "
          f"· 워커 {args.workers}")

    _, ohlcv = price_panel.load_panel(
        tickers, start - pd.Timedelta(days=WINDOW_DAYS), end,
        min_trading_days=price_panel.MIN_TRADING_DAYS_SCORING)
    if not ohlcv:
        print("가격 패널을 받지 못했다.", file=sys.stderr)
        return 1

    # 채점할 날짜 = 패널에 실제로 있는 거래일. 휴장일을 따로 알 필요가 없다.
    idx = max((df.index for df in ohlcv.values() if df is not None and len(df)),
              key=len)
    dates = [d.strftime("%Y-%m-%d") for d in idx if start <= d <= end]
    if not dates:
        print("대상 거래일이 없다.", file=sys.stderr)
        return 1
    print(f"대상 거래일 {len(dates)}일 · 예상 {len(dates) * len(ohlcv):,}건")

    by_date = {}
    tasks = [(t, df, dates) for t, df in ohlcv.items()
             if df is not None and len(df) >= MIN_BARS]
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for ticker, scores in pool.map(_score_ticker, tasks):
            for date, row in scores.items():
                by_date.setdefault(date, {})[ticker] = row
            done += 1
            if done % 25 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)}종목", flush=True)

    regimes = regimes_for(dates)
    records = [(d, regimes.get(d, "neutral"), by_date[d])
               for d in dates if by_date.get(d)]
    n = analyst_log.write_days(records, analyst_log.BACKFILL_DIRNAME)

    print(f"\n백필 {n}일 기록 → {analyst_log.BACKFILL_DIRNAME}")
    print("chart·ict 만 있다. quant·verdict 는 시점 데이터라 되돌릴 수 없다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
