"""
불스 하위팩터 IC 재측정 — 측정 전용 (재설계 실험)
==================================================
bulls_raw_score 단일 스칼라의 IC 상쇄 문제를 풀기 위해, 3개 연속형 하위팩터를
분리해 walk-forward IC + OOS(train/test) 로 측정한다. **라이브 설정 미변경.**

변경점(이전 measure_bulls_ic 대비):
  - forward 21일 → 7일 (셋업이 단기)
  - 유니버스 SP500 전체 (크로스섹션 확대)
  - breakout / trend / reversion 하위팩터 + 횡단면 z 합성(combined) 각각 측정
  - OOS: 앞 75% 기간 train IC vs 뒤 25% test IC

수동 실행:
  python scripts/measure_bulls_subfactors_ic.py            # 기본 3년
  python scripts/measure_bulls_subfactors_ic.py 5
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from modules.bulls_signals import bulls_subfactors
from modules.price_panel import load_panel
from modules.universe import SP500

FORWARD = 7
REBAL = 21
SUBS = ["breakout", "trend", "reversion"]
MIN_HIST = 65
MIN_NAMES = 10


def _zscore(d: dict) -> dict:
    vals = np.array(list(d.values()), dtype=float)
    mu, sigma = vals.mean(), vals.std()
    return {k: (v - mu) / (sigma + 1e-9) for k, v in d.items()}


def main():
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    end = datetime.now()
    start = end - timedelta(days=lookback * 365 + 90)
    print(f"[subfactor_ic] SP500 {len(SP500)}종목 | lookback={lookback}년 "
          f"| rebal={REBAL} forward={FORWARD}")
    print("[subfactor_ic] 데이터 로딩 중...")
    prices, ohlcv = load_panel(SP500, start, end)
    print(f"[subfactor_ic] 확보 {len(prices)}종목 | walk-forward IC 계산 중...")

    closes = pd.DataFrame(prices).dropna(how="all")
    dates = closes.index
    idxs = list(range(MIN_HIST + FORWARD, len(dates) - FORWARD, REBAL))
    if not idxs:
        print("[subfactor_ic] rebal 구간 부족", file=sys.stderr)
        sys.exit(1)

    series = {s: [] for s in SUBS}
    series["combined"] = []

    for idx in idxs:
        as_of = dates[idx]
        raw = {s: {} for s in SUBS}
        for tk, df in ohlcv.items():
            sub = df[df.index <= as_of]
            if len(sub) < MIN_HIST:
                continue
            sf = bulls_subfactors(sub)
            for s in SUBS:
                raw[s][tk] = sf[s]

        fwd_date = dates[min(idx + FORWARD, len(dates) - 1)]
        rets = {}
        for tk in prices:
            cur = prices[tk][prices[tk].index <= as_of]
            fwd = prices[tk][prices[tk].index <= fwd_date]
            if len(cur) and len(fwd):
                cp, fp = float(cur.iloc[-1]), float(fwd.iloc[-1])
                if cp > 0:
                    rets[tk] = fp / cp - 1.0

        # 개별 하위팩터 IC
        z_by_sub = {}
        for s in SUBS:
            common = [t for t in raw[s] if t in rets]
            if len(common) < MIN_NAMES:
                continue
            z_by_sub[s] = _zscore({t: raw[s][t] for t in common})
            x = np.array([raw[s][t] for t in common])
            y = np.array([rets[t] for t in common])
            ic, _ = spearmanr(x, y)
            if not np.isnan(ic):
                series[s].append((as_of, float(ic)))

        # 합성(combined): 하위팩터별 횡단면 z 합
        if len(z_by_sub) == len(SUBS):
            common = set.intersection(*[set(z_by_sub[s]) for s in SUBS]) & set(rets)
            common = list(common)
            if len(common) >= MIN_NAMES:
                comb = {t: sum(z_by_sub[s][t] for s in SUBS) for t in common}
                x = np.array([comb[t] for t in common])
                y = np.array([rets[t] for t in common])
                ic, _ = spearmanr(x, y)
                if not np.isnan(ic):
                    series["combined"].append((as_of, float(ic)))

    print(f"\n하위팩터 IC (forward={FORWARD}일, SP500):")
    print(f"  {'factor':<10} {'mean_IC':>9} {'ICIR':>7} {'양(+)%':>7} {'n':>4} "
          f"{'train_IC':>9} {'test_IC':>9}  판정")
    print(f"  {'-'*70}")
    for s in SUBS + ["combined"]:
        ics = series[s]
        if not ics:
            print(f"  {s:<10} {'(측정 실패)':>9}")
            continue
        arr = np.array([v for _, v in ics])
        m, sd = arr.mean(), arr.std()
        icir = m / (sd + 1e-9)
        pp = (arr > 0).mean() * 100
        split = int(len(arr) * 0.75)
        tr = arr[:split].mean() if split else float("nan")
        te = arr[split:].mean() if split < len(arr) else float("nan")
        oos_ok = np.isfinite(te) and te > 0 and (m <= 0 or te >= 0.5 * m)
        verdict = ("유효+OOS통과" if m > 0.02 and oos_ok
                   else "양수(약)·OOS확인" if m > 0 and oos_ok
                   else "보류")
        print(f"  {s:<10} {m:>+9.4f} {icir:>7.3f} {pp:>7.1f}% {len(arr):>4} "
              f"{tr:>+9.4f} {te:>+9.4f}  {verdict}")

    print("\n[판정] mean_IC>0.02 & test_IC>0(과적합無) = 유효 / 그 외 = 보류")


if __name__ == "__main__":
    main()
