"""롱숏 분위 스프레드 — **재기 전에 검출력만 잰다.**

    python -m scripts.pilot_longshort_power [셔플 횟수]

`docs/superpowers/specs/2026-08-15-fscore-longshort-design.md` 의 게이트가 여기서
나온다. 소형주 롱온리 측정이 "검출력 부족 — 미측정"으로 끝난 이유가 **기준선의
변동성**이었다(MDE 9.29 → 18.67). 같은 자리를 또 밟지 않으려고, 설계를 고르기 전에
**그 설계가 무엇을 가려낼 수 있는지부터** 잰다.

## 신호를 안 본다

F-Score 를 **같은 달 안에서 종목끼리 섞은 뒤**(위약) 스프레드 곡선을 만든다.
섞으면 종목↔점수 연결이 끊기므로 남는 건 **바구니 구성이 만드는 변동성**뿐이다.
그 변동성이 MDE 를 정한다. 진짜 점수로는 한 번도 안 돌린다 — 이 파일에는 실제
F-Score 로 만든 수익률을 찍는 줄이 없다.

대형주 때 "매수보유 줄만으로 MDE 를 낸다"와 같은 종류의 장치다. 결과를 보기 전에
낼 수 있어야 게이트로 쓸 수 있다.

## 무엇을 비교하나

| 설계 | 롱 | 숏(기준선) |
|---|---|---|
| L0 롱온리 (지난 측정 재현) | 고BM 5분위 ∩ F>=7 | 같은 노출 매수보유 |
| A 전 유니버스 스프레드 | F>=7 | F<=3 |
| B 고BM 안에서 스프레드 | 고BM ∩ F>=7 | 고BM ∩ F<=3 |

L0 는 **이 계산이 지난 측정의 MDE 를 재현하는지** 보는 대조군이다. 재현 못 하면
이 파일럿의 숫자를 믿을 이유가 없다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv.append("smallcap")          # measure_fscore 의 유니버스 스위치

import numpy as np    # noqa: E402
import pandas as pd   # noqa: E402

from scripts.measure_fscore import (  # noqa: E402
    BM_TOP, END, HOLD_DAYS, MIN_HELD, SCORE_AT, START, attach_bm,
    excess_cagr_ci, shuffle_scores, smallcap_events, smallcap_members,
)
from scripts.measure_pead import attach_trades, calendar_curve  # noqa: E402
from scripts.measure_portfolio import bench_curve, closes       # noqa: E402

SCORE_LOW = 3          # 저점수 바구니. Piotroski 의 저분위 구간이지 고른 값이 아니다.
MDE_LIMIT_PP = 10.0    # 게이트. 롱온리 측정과 **같은 값**이다.
N_SHUFFLE = 20


def mde_pp(strat: np.ndarray, base: np.ndarray) -> float:
    """초과 연수익 추정량의 부트스트랩 95% 폭 ÷ 2 = 1.96 × 표준오차."""
    _, lo, hi = excess_cagr_ci(strat, base)
    return (hi - lo) / 2.0


def variants(ev, close, bench_ret, n_shuffle: int) -> dict:
    """설계 변형별 MDE. **전부 위약(셔플) 위에서만 잰다** — 신호는 안 본다.

    변형을 검출력으로 고르는 건 튜닝이 아니다. 고르는 기준에 **수익률과 점수의
    관계가 한 번도 안 들어가기** 때문이다. 결과를 보고 고르는 것과 다른 일이다.
    """
    out = {}
    for hi_th, lo_th in ((SCORE_AT, SCORE_LOW), (6, 4)):
        key = f"F>={hi_th} − F<={lo_th}"
        vals, size = [], []
        for i in range(n_shuffle):
            sh = shuffle_scores(ev, seed=20260815 + i)
            hi = sh.loc[sh["fscore"] >= hi_th].reset_index(drop=True)
            lo = sh.loc[sh["fscore"] <= lo_th].reset_index(drop=True)
            vals.append(mde_pp(calendar_curve(hi, close, 0.0, MIN_HELD)[0].values,
                               calendar_curve(lo, close, 0.0, MIN_HELD)[0].values))
            size.append(min(len(hi), len(lo)))
        out[key] = (float(np.median(vals)), float(min(vals)), float(max(vals)),
                    int(np.median(size)))
    return out


def main(n_shuffle: int = N_SHUFFLE) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    close = closes(START, END)
    members = smallcap_members(START, END)
    names = sorted(set(members["asset_id"]) & set(close.columns))
    close = close[names]
    bench_ret = bench_curve(START, END, members=members,
                            close=close).pct_change().fillna(0.0).values

    ev = attach_bm(attach_trades(smallcap_events(names), close, HOLD_DAYS), close)
    print(f"거래 가능 {len(ev)}건 · 종목 {ev['ticker'].nunique()} · "
          f"{START.date()}~{END.date()}")

    rows = {"L0 롱온리 (고BM ∩ F>=7 vs 매수보유)": [],
            f"A 전 유니버스 (F>={SCORE_AT} − F<={SCORE_LOW})": [],
            f"B 고BM 안에서 (F>={SCORE_AT} − F<={SCORE_LOW})": []}
    sizes = {k: [] for k in rows}

    for i in range(n_shuffle):
        sh = shuffle_scores(ev, seed=20260815 + i)      # ← 여기서 신호가 사라진다
        hi_f = sh.loc[sh["fscore"] >= SCORE_AT]
        lo_f = sh.loc[sh["fscore"] <= SCORE_LOW]
        bm_hi = sh.loc[sh["bm_pct"] >= BM_TOP]

        def curve(sub):
            return calendar_curve(sub.reset_index(drop=True), close, 0.0, MIN_HELD)[0].values

        l0 = curve(hi_f.loc[hi_f["bm_pct"] >= BM_TOP])
        exposure = calendar_curve(hi_f.loc[hi_f["bm_pct"] >= BM_TOP].reset_index(drop=True),
                                  close, 0.0, MIN_HELD)[1]
        rows["L0 롱온리 (고BM ∩ F>=7 vs 매수보유)"].append(mde_pp(l0, bench_ret * exposure))
        sizes["L0 롱온리 (고BM ∩ F>=7 vs 매수보유)"].append(
            int((hi_f["bm_pct"] >= BM_TOP).sum()))

        key_a = f"A 전 유니버스 (F>={SCORE_AT} − F<={SCORE_LOW})"
        rows[key_a].append(mde_pp(curve(hi_f), curve(lo_f)))
        sizes[key_a].append(int(min(len(hi_f), len(lo_f))))

        key_b = f"B 고BM 안에서 (F>={SCORE_AT} − F<={SCORE_LOW})"
        bh = bm_hi.loc[bm_hi["fscore"] >= SCORE_AT]
        bl = bm_hi.loc[bm_hi["fscore"] <= SCORE_LOW]
        rows[key_b].append(mde_pp(curve(bh), curve(bl)))
        sizes[key_b].append(int(min(len(bh), len(bl))))

        if (i + 1) % 5 == 0:
            print(f"  셔플 {i + 1}/{n_shuffle}")

    print("\n| 설계 | MDE 중앙값 (연 %p) | 범위 | 얇은 쪽 거래 수 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, v in rows.items():
        a = np.array(v)
        print(f"| {k} | **{np.median(a):.2f}** | {a.min():.2f} ~ {a.max():.2f} | "
              f"{int(np.median(sizes[k]))} | "
              f"{'O' if np.median(a) <= MDE_LIMIT_PP else 'X'} |")
    print(f"\n게이트 {MDE_LIMIT_PP:.0f}%p · 위약 셔플 {n_shuffle}회 · "
          f"보유 {HOLD_DAYS}일 · 비용 0bp · **실제 F-Score 로는 한 번도 안 돌렸다.**")

    # 창을 늘리면 검출력이 는다(√T). 전구간은 2차 행이 아니라 **판정 후보**다.
    print(f"\n## 전구간 2017-09-01 ~ {END.date()} — 같은 계산")
    close_f = closes(pd.Timestamp("2017-09-01"), END)
    mem_f = smallcap_members(pd.Timestamp("2017-09-01"), END)
    names_f = sorted(set(mem_f["asset_id"]) & set(close_f.columns))
    close_f = close_f[names_f]
    ev_f = attach_bm(attach_trades(smallcap_events(names_f), close_f, HOLD_DAYS), close_f)
    print(f"거래 가능 {len(ev_f)}건")
    print("\n| 설계 (전 유니버스 스프레드) | MDE 중앙값 | 범위 | 얇은 쪽 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (med, mn, mx, sz) in variants(ev_f, close_f, None, n_shuffle).items():
        print(f"| {k} | **{med:.2f}** | {mn:.2f} ~ {mx:.2f} | {sz} | "
              f"{'O' if med <= MDE_LIMIT_PP else 'X'} |")

    print(f"\n## 본구간 — 문턱만 넓히면")
    print("\n| 설계 (전 유니버스 스프레드) | MDE 중앙값 | 범위 | 얇은 쪽 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (med, mn, mx, sz) in variants(ev, close, None, n_shuffle).items():
        print(f"| {k} | **{med:.2f}** | {mn:.2f} ~ {mx:.2f} | {sz} | "
              f"{'O' if med <= MDE_LIMIT_PP else 'X'} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 2 else N_SHUFFLE))
