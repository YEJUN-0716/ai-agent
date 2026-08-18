"""
불스 하위팩터 최종 검증 — 측정 전용
====================================
편입 전 마지막 게이트 2개를 확인한다. **라이브 설정 미변경.**
  1) 호라이즌 지속성: forward를 라이브와 같은 21일로 두고 trend/reversion IC 재측정
     (7일에서 유효했던 단기 반전이 21일에도 살아있는지)
  2) 증분성: mom_1m·ict 대비 (a)횡단면 상관 (b)잔차 IC
     — 이미 있는 팩터에 회귀 후 잔차의 IC가 양수로 남으면 "증분 예측력 있음"

breakout(폐기)은 제외. trend / reversion / combined(z_trend+z_reversion)만.

수동 실행:
  python scripts/measure_bulls_final_validation.py        # 기본 3년
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
from modules.factor_validator import _ict_raw_score
from modules.price_panel import load_panel
from modules.universe import SP500

FORWARD = 21
REBAL = 21
MIN_HIST = 65
MIN_NAMES = 15


def _z(a: np.ndarray) -> np.ndarray:
    return (a - a.mean()) / (a.std() + 1e-9)


def _residual_ic(sub_z, mom_z, ict_z, y) -> float:
    """sub_z 를 [1, mom_z, ict_z] 에 회귀한 잔차의 Spearman IC."""
    X = np.column_stack([np.ones_like(sub_z), mom_z, ict_z])
    beta, *_ = np.linalg.lstsq(X, sub_z, rcond=None)
    resid = sub_z - X @ beta
    ic, _ = spearmanr(resid, y)
    return ic


def main():
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    end = datetime.now()
    start = end - timedelta(days=lookback * 365 + 90)
    print(f"[final_val] SP500 {len(SP500)}종목 | lookback={lookback}년 "
          f"| rebal={REBAL} forward={FORWARD}(라이브 동일)")
    print("[final_val] 데이터 로딩 중...")
    prices, ohlcv = load_panel(SP500, start, end)
    print(f"[final_val] 확보 {len(prices)}종목 | trend/reversion + mom/ict 계산 중...")

    closes = pd.DataFrame(prices).dropna(how="all")
    dates = closes.index
    idxs = list(range(MIN_HIST + FORWARD, len(dates) - FORWARD, REBAL))
    if not idxs:
        print("[final_val] rebal 구간 부족", file=sys.stderr)
        sys.exit(1)

    ic = {"trend": [], "reversion": [], "combined": []}
    res_ic = {"trend": [], "reversion": [], "combined": []}
    corr = {"trend_mom": [], "trend_ict": [], "rev_mom": [], "rev_ict": []}

    for idx in idxs:
        as_of = dates[idx]
        fwd_date = dates[min(idx + FORWARD, len(dates) - 1)]

        rows = {}
        for tk, df in ohlcv.items():
            sub = df[df.index <= as_of]
            if len(sub) < MIN_HIST:
                continue
            price = prices[tk][prices[tk].index <= as_of]
            if len(price) < 22:
                continue
            fwd = prices[tk][prices[tk].index <= fwd_date]
            cp = float(price.iloc[-1])
            fp = float(fwd.iloc[-1]) if len(fwd) else np.nan
            if not (cp > 0 and np.isfinite(fp)):
                continue
            sf = bulls_subfactors(sub)
            rows[tk] = {
                "trend": sf["trend"],
                "reversion": sf["reversion"],
                "mom_1m": (cp / float(price.iloc[-22]) - 1.0) * 100,
                "ict": _ict_raw_score(ohlcv, tk, as_of),
                "ret": fp / cp - 1.0,
            }

        if len(rows) < MIN_NAMES:
            continue

        tks = list(rows)
        trend = _z(np.array([rows[t]["trend"] for t in tks]))
        rev = _z(np.array([rows[t]["reversion"] for t in tks]))
        mom = _z(np.array([rows[t]["mom_1m"] for t in tks]))
        ict = _z(np.array([rows[t]["ict"] for t in tks]))
        y = np.array([rows[t]["ret"] for t in tks])
        comb = _z(trend + rev)

        for name, x in (("trend", trend), ("reversion", rev), ("combined", comb)):
            r, _ = spearmanr(x, y)
            if not np.isnan(r):
                ic[name].append(r)
            ri = _residual_ic(x, mom, ict, y)
            if not np.isnan(ri):
                res_ic[name].append(ri)

        for name, x in (("trend", trend), ("reversion", rev)):
            cm, _ = spearmanr(x, mom)
            ci, _ = spearmanr(x, ict)
            key = "trend" if name == "trend" else "rev"
            if not np.isnan(cm):
                corr[f"{key}_mom"].append(cm)
            if not np.isnan(ci):
                corr[f"{key}_ict"].append(ci)

    def _stats(arr):
        a = np.array(arr)
        m, sd = a.mean(), a.std()
        split = int(len(a) * 0.75)
        tr = a[:split].mean() if split else float("nan")
        te = a[split:].mean() if split < len(a) else float("nan")
        return m, m / (sd + 1e-9), (a > 0).mean() * 100, len(a), tr, te

    print("\n[1] 21일 호라이즌 IC (라이브와 동일 rebal/forward):")
    print(f"  {'factor':<10} {'mean_IC':>9} {'ICIR':>7} {'양(+)%':>7} {'n':>4} "
          f"{'train':>8} {'test':>8}")
    print(f"  {'-'*58}")
    for name in ("trend", "reversion", "combined"):
        if not ic[name]:
            print(f"  {name:<10} (측정 실패)")
            continue
        m, icir, pp, n, tr, te = _stats(ic[name])
        print(f"  {name:<10} {m:>+9.4f} {icir:>7.3f} {pp:>7.1f}% {n:>4} "
              f"{tr:>+8.4f} {te:>+8.4f}")

    print("\n[2] 증분성 — 잔차 IC (mom_1m·ict 회귀 후) & 상관:")
    print(f"  {'factor':<10} {'residIC':>9}   {'corr(mom)':>10} {'corr(ict)':>10}")
    print(f"  {'-'*45}")
    cm_t = np.mean(corr["trend_mom"]) if corr["trend_mom"] else float("nan")
    ci_t = np.mean(corr["trend_ict"]) if corr["trend_ict"] else float("nan")
    cm_r = np.mean(corr["rev_mom"]) if corr["rev_mom"] else float("nan")
    ci_r = np.mean(corr["rev_ict"]) if corr["rev_ict"] else float("nan")
    corr_map = {"trend": (cm_t, ci_t), "reversion": (cm_r, ci_r),
                "combined": (float("nan"), float("nan"))}
    for name in ("trend", "reversion", "combined"):
        if not res_ic[name]:
            continue
        rm = np.mean(res_ic[name])
        cmv, civ = corr_map[name]
        cm_s = f"{cmv:>+10.3f}" if np.isfinite(cmv) else f"{'-':>10}"
        ci_s = f"{civ:>+10.3f}" if np.isfinite(civ) else f"{'-':>10}"
        print(f"  {name:<10} {rm:>+9.4f}   {cm_s} {ci_s}")

    print("\n[판정] 21일 mean_IC>0.02 & test>0 & 잔차IC>0.01 & |corr|<0.4 = 편입 가치 있음")


if __name__ == "__main__":
    main()
