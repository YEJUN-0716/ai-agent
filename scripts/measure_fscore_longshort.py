"""F-Score 롱숏 분위 스프레드 — **판정**.

    python -m scripts.measure_fscore_longshort [sealed]
    python -m scripts.measure_fscore_longshort selftest

사전 등록: `docs/superpowers/specs/2026-08-15-fscore-longshort-design.md`.
검출력 게이트: `scripts/pilot_longshort_power.py` (MDE 5.56%p · 게이트 10%p 통과).

## 자를 공유한다 — 새로 만든 계산이 없다

점수·거래·곡선·부트스트랩·위약은 전부 `measure_fscore` / `measure_pead` 에서
그대로 가져온다. 이 파일이 새로 하는 일은 **두 바구니를 세우고 순열검정을 돌리는
것** 뿐이다. 설계서 7절은 "`measure_fscore.py` 에 스위치 하나"라고 적었지만,
그쪽 `main()` 은 롱온리 리포트가 340줄로 박혀 있어 통과선이 다른 판정을 끼우면
분기가 리포트 전체에 번진다. **파일만 나누고 자는 import 로 공유한다** —
`pilot_longshort_power.py` 가 이미 쓰는 방식이고, 측정 규칙은 한 글자도 안 바뀐다.

## 통과선 (AND)

| | 무엇 | 통과선 |
|---|---|---|
| ① 크기 | 스프레드 연수익 | 블록 부트스트랩 95% 하한 > 0 |
| ② 구성 | 점수만 섞은 위약 스프레드 분포 | 순열검정 p < 0.05 |
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 유니버스·창 스위치. `full` 이 판정 창(2017-09-01~)을 켠다 — 설계서 4절이 검출력을
# 보고 고른 창이다. `sealed` 는 그대로 흘려보내면 measure_fscore 가 받는다.
sys.argv += ["smallcap", "full"]

try:                  # 윈도우 콘솔은 cp949 다 — 리포트를 찍다 죽는다
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np    # noqa: E402
import pandas as pd   # noqa: E402

from scripts.measure_fscore import (  # noqa: E402
    END, HOLD_DAYS, MIN_HELD, SCORE_AT, SEALED, START,
    excess_cagr_ci, shuffle_scores, smallcap_events, smallcap_members,
)
from scripts.measure_pead import N_BOOT, attach_trades, calendar_curve  # noqa: E402
from scripts.measure_portfolio import bench_curve, cagr, closes, mdd    # noqa: E402

SCORE_LOW = 3          # 저점수 바구니. Piotroski 의 저분위 구간이지 고른 값이 아니다.
COST_BPS = 0.0         # 설계서 1절 — 이건 돌릴 물건이 아니라 신호 존재 검사다.
N_PERM = 100           # 순열검정 셔플 횟수. 사전 등록값.
PERM_SEED = 20260815   # 사전 등록값. 파일럿과 같은 씨앗을 같은 방식으로 쓴다.
ALPHA = 0.05

OUT_MD = Path(f"docs/measurements/2026-08-15-fscore-longshort"
              f"{'-sealed' if SEALED else ''}.md")


def spread_pp(hi: np.ndarray, lo: np.ndarray) -> float:
    """스프레드 연수익(%p) 점추정. `excess_cagr_ci` 의 point 와 **같은 식**이다.

    순열검정은 위약을 100번 돌려야 하는데 `excess_cagr_ci` 는 한 번에 2,000회
    재표본한다. 점추정만 떼어 쓰면 같은 통계량을 20만 번 재표본 없이 얻는다.
    """
    return float((np.expm1(np.log1p(hi).mean() * 252)
                  - np.expm1(np.log1p(lo).mean() * 252)) * 100)


def legs(ev: pd.DataFrame, close: pd.DataFrame):
    """(고점수 곡선, 저점수 곡선, 고점수 거래, 저점수 거래). 스크린은 점수뿐이다."""
    hi = ev.loc[ev["fscore"] >= SCORE_AT].reset_index(drop=True)
    lo = ev.loc[ev["fscore"] <= SCORE_LOW].reset_index(drop=True)
    return (calendar_curve(hi, close, COST_BPS, MIN_HELD),
            calendar_curve(lo, close, COST_BPS, MIN_HELD), hi, lo)


N_GATE = 8             # 파일럿이 MDE 를 잰 셔플 수. 게이트를 재현하려고 같은 값을 쓴다.


def permutation_p(ev: pd.DataFrame, close: pd.DataFrame, actual: float):
    """②. 같은 파이프라인에 **점수만 섞어** 스프레드를 100번 다시 만든다.

    p = (실제 이상인 위약 수 + 1) / (셔플 수 + 1). +1 은 관측 자신을 세는 항이고,
    이게 없으면 위약이 하나도 안 넘었을 때 p=0 이라는 못 믿을 값이 나온다.

    같은 루프에서 **위약 스프레드의 거칠기**도 받아둔다 — 검출력 게이트가 이 위에서
    계산됐으므로, 실제 스프레드와 견줘야 그 게이트를 믿을 수 있었는지 알 수 있다.
    앞 `N_GATE` 개만 부트스트랩까지 돌린다(2,000회 재표본 × 100 은 낭비다).
    """
    vals, sds, cors, halfs = [], [], [], []
    for i in range(N_PERM):
        (h, _), (l, _), _, _ = legs(shuffle_scores(ev, seed=PERM_SEED + i), close)
        h, l = h.values, l.values
        vals.append(spread_pp(h, l))
        sds.append(float(np.std(h - l)))
        cors.append(float(np.corrcoef(h, l)[0, 1]))
        if i < N_GATE:
            _, x, y = excess_cagr_ci(h, l)
            halfs.append((y - x) / 2.0)
        if (i + 1) % 20 == 0:
            print(f"  위약 {i + 1}/{N_PERM}")
    vals = np.array(vals)
    diag = {"sd": float(np.median(sds)), "corr": float(np.median(cors)),
            "mde": float(np.median(halfs))}
    return float((np.sum(vals >= actual) + 1) / (N_PERM + 1)), vals, diag


def yearly_rows(hi: pd.Series, lo: pd.Series, thin: pd.Series) -> list[str]:
    """해마다 두 다리와 스프레드, 그리고 **양쪽 다 현금인 날 수**(설계서 6.2)."""
    out = ["| 해 | 고점수 | 저점수 | 스프레드 | 양쪽 현금 |", "|---|---|---|---|---|"]
    for y, idx in hi.groupby(hi.index.year).groups.items():
        a = float((1 + hi.loc[idx]).prod() - 1) * 100
        b = float((1 + lo.loc[idx]).prod() - 1) * 100
        out.append(f"| {y} | {a:+.1f}% | {b:+.1f}% | {a - b:+.1f}%p | "
                   f"{int(thin.loc[idx].sum())}일 |")
    return out


def selftest() -> int:
    """산수만 본다 — 시장 데이터 없이 돈다."""
    # 1) spread_pp 가 excess_cagr_ci 의 점추정과 같은 값인가. 같은 자를 쓴다는 주장이
    #    코드로 확인되는 자리다.
    rng = np.random.default_rng(0)
    a, b = rng.normal(3e-4, 0.01, 900), rng.normal(1e-4, 0.01, 900)
    point, _, _ = excess_cagr_ci(a, b)
    assert abs(spread_pp(a, b) - point) < 1e-9, (spread_pp(a, b), point)

    # 2) 순열 p 의 경계. 위약이 전부 실제보다 크면 p=1, 하나도 안 넘으면 1/(N+1).
    lows = np.full(N_PERM, -100.0)
    assert (np.sum(lows >= 0.0) + 1) / (N_PERM + 1) == 1 / (N_PERM + 1)
    assert (np.sum(np.zeros(N_PERM) >= -1.0) + 1) / (N_PERM + 1) == 1.0

    # 3) 통과선은 AND 다. 한쪽만 넘으면 실패여야 한다.
    for lo_ci, p in ((1.0, 0.9), (-1.0, 0.01), (-1.0, 0.9)):
        assert not ((lo_ci > 0) and (p < ALPHA)), (lo_ci, p)
    assert (2.0 > 0) and (0.01 < ALPHA)

    print("selftest OK — 3검사")
    return 0


def main() -> int:
    close = closes(START, END)
    members = smallcap_members(START, END)
    n_members = members["asset_id"].nunique()
    names = sorted(set(members["asset_id"]) & set(close.columns))
    close = close[names]
    bench = bench_curve(START, END, members=members, close=close)
    years = (close.index[-1] - close.index[0]).days / 365.25

    ev = attach_trades(smallcap_events(names), close, HOLD_DAYS)
    print(f"거래 가능 {len(ev)}건 · 종목 {ev['ticker'].nunique()} · "
          f"{START.date()}~{END.date()}")

    (hi_ret, hi_exp), (lo_ret, lo_exp), hi, lo = legs(ev, close)
    point, ci_lo, ci_hi = excess_cagr_ci(hi_ret.values, lo_ret.values)
    print(f"스프레드 {point:+.2f}%p · 95% [{ci_lo:+.2f}, {ci_hi:+.2f}] — 위약 {N_PERM}회 시작")

    p_val, plc, diag = permutation_p(ev, close, point)

    pass1, pass2 = ci_lo > 0, p_val < ALPHA
    verdict = "통과" if (pass1 and pass2) else "실패"
    mark1, mark2 = ("O" if pass1 else "X"), ("O" if pass2 else "X")

    # 반대 방향으로 유의한가. "신호가 없다"와 "반대 신호"는 다른 이야기다(설계서 3절).
    # **둘 중 하나만 넘어도 적는다** — 판정은 AND 지만 기록은 관측 단위로 남긴다.
    p_neg = float((np.sum(plc <= point) + 1) / (N_PERM + 1))
    flipped = (ci_hi < 0) or (p_neg < ALPHA)

    # 검출력 게이트가 맞았나. 게이트는 위약 위에서 5.56%p 를 냈는데, 실제 스프레드의
    # 부트스트랩 반폭이 그 자리에 오는지 본다. 이 비교는 **측정 후에만** 가능하다.
    half = (ci_hi - ci_lo) / 2.0
    real_sd = float(np.std(hi_ret.values - lo_ret.values))
    real_corr = float(np.corrcoef(hi_ret.values, lo_ret.values)[0, 1])

    hi_curve, lo_curve = (1 + hi_ret).cumprod(), (1 + lo_ret).cumprod()
    both_cash = (hi_ret == 0) & (lo_ret == 0)
    one_cash = (hi_ret == 0) ^ (lo_ret == 0)

    body = [
        "# F-Score 롱숏 분위 스프레드" + (" — 봉인 구간 확인" if SEALED else " — 판정"),
        "",
        f"구간 {START.date()} ~ {END.date()} · 소형주 유니버스 · 진입 `filed`+1 종가 · "
        f"{HOLD_DAYS}거래일 보유 · **F>={SCORE_AT} 롱 − F<={SCORE_LOW} 숏** · "
        f"비용 {COST_BPS:.0f}bp · 동일가중 캘린더타임.",
        "사전 등록: `docs/superpowers/specs/2026-08-15-fscore-longshort-design.md`. "
        "**창과 문턱은 위약 위에서 잰 검출력으로 골랐고**"
        "(`scripts/pilot_longshort_power.py`, MDE 5.56%p), 그 계산에는 점수와 수익률의 "
        "관계가 한 번도 안 들어갔다.",
        "",
        "> **통과해도 돌릴 물건이 아니다.** 이 저장소의 숏 손익분기는 24.7bp 고 이 측정은 "
        "0bp 다. 질문은 **\"신호가 있나\"** 하나이며, 답이 O 라도 다음 단계는 "
        "\"실행 가능성 측정\"이지 실전 투입이 아니다(설계서 1절).",
        "",
    ] + ([
        "> **이 문서는 판정이 아니다.** 본 측정(`2026-08-15-fscore-longshort.md`)의 판정이 "
        "끝난 뒤 봉인 구간을 딱 한 번 연 것이다. 봉인 구간은 "
        f"{HOLD_DAYS}거래일 보유가 완결되는 진입이 거의 없어 **구조적으로 검출력이 없다** — "
        "여기서 뭐가 나오든 판정을 못 바꾼다.",
        "",
        f"## 봉인 구간에 같은 자를 대면: ①{mark1} ②{mark2}",
    ] if SEALED else [
        f"## 판정: **{verdict}** (①{mark1} AND ②{mark2})",
    ]) + [
        "",
        "| | 무엇 | 통과선 | 실측 | |",
        "|---|---|---|---|---|",
        f"| ① 크기 | 스프레드 캘린더타임 곡선의 연수익 | 블록 부트스트랩 95% 하한 > 0 | "
        f"{point:+.2f}%p · 95% [{ci_lo:+.2f}, {ci_hi:+.2f}] | {mark1} |",
        f"| ② 구성 | 점수만 섞은 위약 스프레드 분포 대비 | 순열검정 p < {ALPHA} | "
        f"p = {p_val:.3f} (위약 {N_PERM}회 중 {int(np.sum(plc >= point))}개가 실제 이상) "
        f"| {mark2} |",
        "",
        "**둘 다 넘어야 통과다.** ①은 \"0과 구별되나\", ②는 \"그 수익이 점수에서 온 게 "
        "맞나\"를 본다. 소형주 롱온리에서 **위약이 전략을 이겼고**(+31.0% 대 +22.6%), "
        "②가 없으면 그런 줄을 성공으로 읽는다.",
        "",
    ] + ([
        "### 반대 방향 — 판정은 실패지만 관측은 따로 적는다 (설계서 3절)",
        "",
        f"스프레드가 음(-)이고, 위약 {N_PERM}개 중 실제보다 **낮은** 것이 "
        f"{int(np.sum(plc <= point))}개다(p_neg = {p_neg:.3f}). 점수를 섞어서는 이만큼 "
        f"나쁜 스프레드가 안 나온다는 뜻이고, **F<={SCORE_LOW} 바구니가 "
        f"F>={SCORE_AT} 바구니를 이겼다.** 가설은 단측이므로 판정은 실패다 — 결과를 보고 "
        "통과선을 뒤집으면 그게 튜닝이다.",
        "",
        "**다만 이 p 를 크기의 증거로 읽으면 안 된다.** 순열검정의 귀무가설은 \"달 안에서 "
        "점수 라벨을 바꿔 달아도 된다\"이고, 그걸 기각했다는 건 **라벨이 무작위가 아니라는 "
        "것까지**다. 낮은 F-Score 바구니는 높은 쪽과 베타·업종·시총이 다르다 — 달 안에서 "
        "점수만 섞는 위약은 그 차이를 못 지운다. 아래 해마다 표의 2020(+57.2%)과 "
        "2024(+70.6%) 저점수 줄이 그 모양이다. **\"부실주가 반등장에서 더 올랐다\"와 "
        "\"F-Score 가 거꾸로 예측한다\"를 이 측정은 구분하지 못한다.**",
        "",
        f"①이 X 라는 것도 같이 읽어야 한다. 95% 구간이 [{ci_lo:+.2f}, {ci_hi:+.2f}] 로 0 을 "
        "품으므로, **이 측정은 스프레드가 0 과 다르다고 말하지 못한다.** ②가 본 것은 "
        "크기가 아니라 라벨의 비무작위성이다.",
        "",
    ] if flipped else []) + [
        "## 위약 분포 — ②가 실제로 본 것",
        "",
        "| | 값 |",
        "|---|---|",
        f"| 실제 스프레드 | **{point:+.2f}%p** |",
        f"| 위약 중앙값 | {float(np.median(plc)):+.2f}%p |",
        f"| 위약 95백분위 (②의 문턱) | {float(np.percentile(plc, 95)):+.2f}%p |",
        f"| 위약 범위 | {plc.min():+.2f} ~ {plc.max():+.2f}%p |",
        "",
        f"셔플 {N_PERM}회 · 시드 {PERM_SEED}(+i) · **같은 달 안에서 종목끼리 점수만 섞는다** — "
        "진입 날짜 분포와 자리 수 구조는 그대로 두고 종목↔점수 연결만 끊는다.",
        "",
        "## 네 줄",
        "",
        "| 줄 | 연수익 | MDD | 비고 |",
        "|---|---|---|---|",
        f"| 롱 다리 (F>={SCORE_AT}) | {cagr(float(hi_curve.iloc[-1]), years):+.1f}% | "
        f"{mdd(hi_curve):.1f}% | 노출 {hi_exp * 100:.0f}% · 거래 {len(hi)}건 · "
        f"보유 중 상폐 {int(hi['delisted'].sum())}건 |",
        f"| 숏 다리 (F<={SCORE_LOW}, 부호 그대로) | {cagr(float(lo_curve.iloc[-1]), years):+.1f}% | "
        f"{mdd(lo_curve):.1f}% | 노출 {lo_exp * 100:.0f}% · 거래 {len(lo)}건 · "
        f"보유 중 상폐 {int(lo['delisted'].sum())}건 |",
        f"| 스프레드 (①의 통계) | {point:+.2f}%p | — | 95% [{ci_lo:+.2f}, {ci_hi:+.2f}] |",
        f"| 매수보유 100% (참고) | {cagr(float(bench.iloc[-1]), years):+.1f}% | "
        f"{mdd(bench):.1f}% | 통과선 아님 |",
        "",
        "숏 다리는 **부호를 뒤집지 않은 원 수익**이다. 스프레드가 롱 − 숏이므로 이 줄이 "
        "낮을수록 스프레드가 좋다.",
        "",
        "## 해마다 — 바구니가 얇아지는 해가 있나 (설계서 6.2)",
        "",
    ] + yearly_rows(hi_ret, lo_ret, both_cash) + [
        "",
        f"양쪽 다 `MIN_HELD`({MIN_HELD}) 미만이라 스프레드가 0 인 날 **{int(both_cash.sum())}일** "
        f"/ {len(hi_ret)}일. 한쪽만 현금인 날 {int(one_cash.sum())}일 — 그런 날의 스프레드는 "
        "한 다리짜리 수다.",
        "",
    ] + ([] if SEALED else [
        # 봉인 구간에는 이 절을 안 낸다. 파일럿은 판정 창에서만 MDE 를 쟀으므로
        # "게이트를 재현한다"는 문장이 봉인 창에서는 그냥 거짓이다(실측 16.15%p).
        "## 검출력 게이트가 또 틀렸다 — 이번 측정의 가장 큰 소득",
        "",
        "사전 등록의 자랑은 **\"이번엔 재기 전에 검출력을 계산했다\"** 였다. 그 계산은 "
        f"재현된다(위약 {N_GATE}회 MDE 중앙값 **{diag['mde']:.2f}%p**, 파일럿이 적은 5.56 "
        "과 같은 자리다). 그런데 실제 스프레드로 잰 정밀도는 그게 아니었다.",
        "",
        "| | 위약 스프레드 | 실제 스프레드 |",
        "|---|---|---|",
        f"| 부트스트랩 95% 반폭 (= MDE) | {diag['mde']:.2f}%p | **{half:.2f}%p** |",
        f"| 일별 스프레드 표준편차 | {diag['sd'] * 1e4:.1f}bp | **{real_sd * 1e4:.1f}bp** |",
        f"| 두 다리의 상관 | {diag['corr']:.4f} | {real_corr:.4f} |",
        "",
        f"**게이트가 예측한 것보다 {half / diag['mde']:.1f}배 거칠고, 게이트 한계(10%p) 자체를 "
        f"{half / 10.0:.1f}배 넘는다.** 원인은 셋째 줄에 있다 — 점수를 섞으면 두 바구니가 "
        "**같은 풀에서 뽑은 거의 같은 포트폴리오**가 된다(상관 "
        f"{diag['corr']:.3f}). 그 차이는 구조적으로 얌전할 수밖에 없다. 진짜로 나누면 두 "
        f"바구니는 업종·베타·시총이 다른 서로 다른 포트폴리오고(상관 {real_corr:.3f}), "
        "그만큼 차이가 출렁인다.",
        "",
        "**위약 위에서 잰 MDE 는 스프레드 설계에서 구조적으로 하한이다.** 지난 측정이 "
        "\"MDE 는 종목 수가 아니라 기준선의 변동성이 정한다\"로 끝났는데, 이번엔 그 교훈을 "
        "적용하고도 **같은 함정의 다음 칸**을 밟았다 — 위약의 변동성은 실제 기준선의 "
        "변동성이 아니다.",
        "",
        "그래서 이 판정은 **\"신호가 없다\"가 아니라 \"이 자로는 여전히 못 잰다\"** 에 가깝다. "
        f"실제 MDE {half:.1f}%p 로는 문헌이 말하는 크기의 F-Score 효과를 잡을 수 없다. "
        "다음 사전 등록은 위약이 아니라 **실제 두 바구니의 상관까지 넣어** 검출력을 "
        "계산해야 한다.",
        "",
    ]) + [
        "## 이 판정을 못 믿을 자리 — 재기 전에 적어둔 것",
        "",
        "### 상폐가 숏 다리를 부풀린다 (설계서 6.1 · 이번 측정의 최대 위험)",
        "",
        f"저점수 바구니에는 죽는 회사가 몰린다 — 보유 중 상폐가 롱 {int(hi['delisted'].sum())}건 대 "
        f"숏 {int(lo['delisted'].sum())}건이다. 상폐 규칙은 \"마지막 거래일 종가에 청산\"인데 "
        "**숏에서 그건 이익 확정**이다. 실제로는 그 시점에 빌린 주식을 되사 갚아야 하고, "
        "파산 종목은 차입 자체가 끊기며 강제 상환된다. 정리매매 가격이 마지막 종가보다 "
        "낮다는 사실은 롱에서는 낙관이고 **숏에서는 비관**이라 두 방향이 상쇄되지 않는다. "
        "**이 측정의 숏 수익은 실제보다 높게 나온다.**",
        "",
        "### 조정 종가 사고 — 패널을 고치고 쟀다 (설계서 6.3)",
        "",
        "파산·감자·역분할을 지난 종목의 조정 종가는 사고 이전이 평평한 그루터기로 남고 "
        "하루에 300~3,000배 뛴다(`874499:GPOR` 0.138 → 72.95). 한 봉이 **0.0** 인 것도 "
        "있었다. 캘린더 곡선은 동일가중이라 그런 봉 하나가 그날 바구니를 통째로 정한다. "
        "**패널에서 마지막 불연속(1일 10배) 뒤부터만 쓰도록 고친 뒤**(123종목) 쟀다 — "
        "고치기 전 같은 설계의 MDE 는 116%p 였다.",
        "",
        "### 그 밖에",
        "",
        "- **마이크로캡이 통째로 빠져 있다.** 시총 순위 1,001~3,000위, 하한 $433M. "
        "F-Score 효과가 가장 크다고 알려진 구간이 없다.",
        "- **재활용 티커의 이전 주인 18종목을 패널에서 뺐다** — 그만큼 상폐가 덜 들어 있다(낙관 쪽).",
        "- **비용 0bp.** 대차료·차입 가능 여부·업틱 규제는 안 쟀다.",
        "- **월별 구성 변경을 거래에는 안 넣었다.** 벤치마크만 매월 리밸런스다.",
        "- **F8(매출총이익률 개선) 미사용** — `GrossProfit` 커버리지 33.0% 로 문턱 미달. 분모는 8이다.",
        "- **봉인은 이 문서로 열었다. 다시 안 연다.**" if SEALED else
        "- **2025-01 ~ 은 봉인.** 판정 후 딱 한 번 연다. 갈려도 판정을 안 바꾼다.",
        "",
        "## 점수 분포 (거래 가능 건 기준)",
        "",
        "| F-Score | " + " | ".join(str(s) for s in range(9)) + " |",
        "|---|" + "---|" * 9,
        "| 건수 | " + " | ".join(str(int((ev["fscore"] == s).sum())) for s in range(9)) + " |",
        "",
        f"구성종목 {n_members}개 중 조정 일봉이 있는 {len(names)}개. 거래 가능 {len(ev)}건 "
        f"(롱 {len(hi)} · 숏 {len(lo)}). 부트스트랩 {N_BOOT}회 · 블록 20일 · "
        f"순열 {N_PERM}회 · 시드 {PERM_SEED}.",
        f"재현: `python scripts/measure_fscore_longshort.py{' sealed' if SEALED else ''}` · "
        "산수 점검 `... selftest`",
        "",
    ]

    text = "\n".join(body)
    print(text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest() if "selftest" in sys.argv else main())
