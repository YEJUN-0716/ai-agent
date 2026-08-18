"""롱숏 분위 스프레드 — **재기 전에 검출력만 잰다.**

    python -m scripts.pilot_longshort_power [셔플 횟수]

`docs/superpowers/specs/2026-08-15-fscore-longshort-design.md` 의 게이트가 여기서
나온다. 소형주 롱온리 측정이 "검출력 부족 — 미측정"으로 끝난 이유가 **기준선의
변동성**이었다(MDE 9.29 → 18.67). 같은 자리를 또 밟지 않으려고, 설계를 고르기 전에
**그 설계가 무엇을 가려낼 수 있는지부터** 잰다.

## 실제 두 바구니로 재되, 결과는 안 본다 (2026-08-16 설계서 1절)

**첫 판은 위약 두 다리로 게이트를 냈고 3.5배 빗나갔다** (5.56%p → 실측 19.73%p).
달 안에서 점수를 섞으면 고점수 바구니와 저점수 바구니가 같은 풀에서 뽑은 거의 같은
포트폴리오가 된다. 상관이 0.956 까지 올라가고(실제는 0.849), 붙어 있는 두 줄의
차이는 구조적으로 얌전할 수밖에 없다. **두 다리를 같은 풀에서 뽑는 설계에서 위약
MDE 는 게이트가 아니라 구조적 하한이다.**

그래서 게이트는 **진짜 F-Score 로 만든 두 바구니**의 일별 수익 두 줄로 낸다. 그
두 줄에서 읽는 것은 `mde_pp` 가 반환하는 **구간의 폭뿐**이고, 점추정·누적수익·부호는
읽지 않는다 — 함수가 아예 반환하지 않는다. 임상시험의 눈가림 표본수 재계산과 같은
장치다: 효과는 가린 채 방해 모수(분산)만 실측으로 채운다.

위약 줄은 지우지 않는다. **참고 하한**으로 같이 찍어, 게이트가 하한보다 얼마나
위에 있는지 보이게 한다.

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv.append("smallcap")          # measure_fscore 의 유니버스 스위치

import numpy as np    # noqa: E402
import pandas as pd   # noqa: E402

from scripts.measure_fscore import (  # noqa: E402
    BM_TOP, END, HOLD_DAYS, MIN_HELD, SCORE_AT, START, attach_bm,
    shuffle_scores, panel_events, panel_members,
)
from scripts.measure_pead import (  # noqa: E402
    MDE_LIMIT_PP, attach_trades, calendar_curve, mde_pp,
)
from scripts.measure_portfolio import bench_curve, closes       # noqa: E402

SCORE_LOW = 3          # 저점수 바구니. Piotroski 의 저분위 구간이지 고른 값이 아니다.
N_SHUFFLE = 20         # 위약은 게이트가 아니라 **참고 하한**을 내는 데만 쓴다.


def designs(ev, close, bench_ret) -> dict:
    """설계별 (전략 줄, 기준선 줄, 얇은 쪽 거래 수). 실제/위약 모두 이 함수를 탄다.

    실제 점수를 넣으면 실제 두 다리, `shuffle_scores` 를 거친 걸 넣으면 위약 두
    다리가 나온다. **같은 코드 경로**여야 두 MDE 를 견줄 수 있다.
    """
    def curve(sub):
        return calendar_curve(sub.reset_index(drop=True), close, 0.0, MIN_HELD)

    hi_f = ev.loc[ev["fscore"] >= SCORE_AT]
    lo_f = ev.loc[ev["fscore"] <= SCORE_LOW]
    bm_hi = ev.loc[ev["bm_pct"] >= BM_TOP]
    bh = bm_hi.loc[bm_hi["fscore"] >= SCORE_AT]
    bl = bm_hi.loc[bm_hi["fscore"] <= SCORE_LOW]
    l0 = hi_f.loc[hi_f["bm_pct"] >= BM_TOP]

    l0_ret, l0_exp = curve(l0)
    return {
        "L0 롱온리 (고BM ∩ F>=7 vs 매수보유)": (l0_ret.values, bench_ret * l0_exp, len(l0)),
        f"A 전 유니버스 (F>={SCORE_AT} − F<={SCORE_LOW})":
            (curve(hi_f)[0].values, curve(lo_f)[0].values, min(len(hi_f), len(lo_f))),
        f"B 고BM 안에서 (F>={SCORE_AT} − F<={SCORE_LOW})":
            (curve(bh)[0].values, curve(bl)[0].values, min(len(bh), len(bl))),
    }


def variants(ev, close, n_shuffle: int) -> dict:
    """문턱 조합별 (실제 MDE, 위약 중앙값, 얇은 쪽 거래 수).

    문턱을 검출력으로 고르는 건 튜닝이 아니다 — `mde_pp` 는 점추정을 반환하지
    않으므로, 고르는 기준에 **수익률과 점수의 관계가 한 번도 안 들어간다.**
    """
    out = {}
    for hi_th, lo_th in ((SCORE_AT, SCORE_LOW), (6, 4)):
        def spread(frame, hi_th=hi_th, lo_th=lo_th):
            hi = frame.loc[frame["fscore"] >= hi_th].reset_index(drop=True)
            lo = frame.loc[frame["fscore"] <= lo_th].reset_index(drop=True)
            return (calendar_curve(hi, close, 0.0, MIN_HELD)[0].values,
                    calendar_curve(lo, close, 0.0, MIN_HELD)[0].values,
                    min(len(hi), len(lo)))

        s, b, size = spread(ev)
        plc = [mde_pp(*spread(shuffle_scores(ev, seed=20260815 + i))[:2])
               for i in range(n_shuffle)]
        out[f"F>={hi_th} − F<={lo_th}"] = (mde_pp(s, b), float(np.median(plc)), size)
    return out


def main(n_shuffle: int = N_SHUFFLE) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    close = closes(START, END)
    members = panel_members(START, END)
    names = sorted(set(members["asset_id"]) & set(close.columns))
    close = close[names]
    bench_ret = bench_curve(START, END, members=members,
                            close=close).pct_change().fillna(0.0).values

    ev = attach_bm(attach_trades(panel_events(names), close, HOLD_DAYS), close)
    print(f"거래 가능 {len(ev)}건 · 종목 {ev['ticker'].nunique()} · "
          f"{START.date()}~{END.date()}")

    real = designs(ev, close, bench_ret)
    floor = {k: [] for k in real}
    for i in range(n_shuffle):
        sh = shuffle_scores(ev, seed=20260815 + i)      # ← 참고 하한용. 게이트 아님.
        for k, (s, b, _) in designs(sh, close, bench_ret).items():
            floor[k].append(mde_pp(s, b))
        if (i + 1) % 5 == 0:
            print(f"  위약 {i + 1}/{n_shuffle}")

    print("\n| 설계 | **MDE (실제 두 줄)** | 위약 중앙값 (참고 하한) | 얇은 쪽 거래 수 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (s, b, size) in real.items():
        m = mde_pp(s, b)
        print(f"| {k} | **{m:.2f}** | {np.median(floor[k]):.2f} | {size} | "
              f"{'O' if m <= MDE_LIMIT_PP else 'X'} |")
    print(f"\n게이트 {MDE_LIMIT_PP:.0f}%p · 위약 셔플 {n_shuffle}회 · 보유 {HOLD_DAYS}일 · 비용 0bp.")
    print("**게이트는 실제 두 바구니로 낸다.** `mde_pp` 는 구간 반폭만 반환하므로 "
          "이 파일은 점추정·부호·누적수익을 한 번도 보지 않는다 (설계서 1절).")
    print("위약 중앙값은 **구조적 하한**이다 — 섞으면 두 바구니가 거의 같은 포트폴리오가 "
          "되어 상관이 올라간다. 게이트로 쓰면 안 된다 (설계서 0절).")

    # 창을 늘리면 검출력이 는다(√T). 전구간은 2차 행이 아니라 **판정 후보**다.
    print(f"\n## 전구간 2017-09-01 ~ {END.date()} — 같은 계산")
    close_f = closes(pd.Timestamp("2017-09-01"), END)
    mem_f = panel_members(pd.Timestamp("2017-09-01"), END)
    names_f = sorted(set(mem_f["asset_id"]) & set(close_f.columns))
    close_f = close_f[names_f]
    ev_f = attach_bm(attach_trades(panel_events(names_f), close_f, HOLD_DAYS), close_f)
    print(f"거래 가능 {len(ev_f)}건")
    print("\n| 설계 (전 유니버스 스프레드) | **MDE (실제)** | 위약 중앙값 | 얇은 쪽 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (real_mde, plc_med, sz) in variants(ev_f, close_f, n_shuffle).items():
        print(f"| {k} | **{real_mde:.2f}** | {plc_med:.2f} | {sz} | "
              f"{'O' if real_mde <= MDE_LIMIT_PP else 'X'} |")

    print("\n## 본구간 — 문턱만 넓히면")
    print("\n| 설계 (전 유니버스 스프레드) | **MDE (실제)** | 위약 중앙값 | 얇은 쪽 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (real_mde, plc_med, sz) in variants(ev, close, n_shuffle).items():
        print(f"| {k} | **{real_mde:.2f}** | {plc_med:.2f} | {sz} | "
              f"{'O' if real_mde <= MDE_LIMIT_PP else 'X'} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 2 else N_SHUFFLE))
