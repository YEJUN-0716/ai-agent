"""자산배분 국면 조절 — **재기 전에 검출력만 잰다.**

    python -m scripts.pilot_allocation_power           # MDE 표
    python -m scripts.pilot_allocation_power selftest  # 산수 점검

질문: "국면에 따라 주식/채권/금 비중을 바꾸면 고정 70/20/10 보다 나은가?"
기준선은 러너가 실제로 쓰는 `index_autopilot.TARGETS`.

사전 등록 문서를 쓰는 비용조차 치르기 전에 **자보다 작은 걸 재려는 중인지** 본다
(8-K 노선이 이 계산 하나로 닫혔다). 두 숫자만 나란히 놓는다: 이 설계가 잴 수 있는
최소 효과(MDE)와, 이 노선이 노릴 수 있는 최대 효과(완전예지 천장).

## 눈가림 — 실제 규칙이 아직 없다

`mde_pp` 는 실제 두 줄을 요구하는데(위약 두 다리 함정, 2026-08-16 설계서 0절)
여기엔 아직 규칙이 없다. 대신 **국면 판정만 무작위로 바꾼 줄**을 넣는다. 비중이
움직이는 폭·빈도·리밸런스 시점은 진짜 규칙과 같고 **타이밍만 동전던지기**다.
방해 모수(초과수익의 분산)는 비중이 얼마나 움직이는지가 정하고 타이밍이 맞았는지는
정하지 않으므로, 이 줄의 MDE 는 진짜 규칙의 MDE 와 같다. 효과는 구조적으로 0 이다.

이건 롱숏에서 3.5배 빗나간 위약과 **다르다**. 거기서는 위약이 두 다리를 같은
포트폴리오로 만들어 상관을 인위로 올렸다. 여기서는 두 줄의 상관이 높은 것이 사실
그 자체다 — 진짜 규칙도 자산의 90% 를 기준선과 공유한다.

## 함정

- **GLDM 은 2018-06 상장.** 창이 8년이면 MDE 는 √T 로 벌 수 있는 걸 다 버린다.
  금 노출은 GLD 로 대신 재고(같은 금 현물), 창을 21년으로 늘린 줄을 같이 낸다.
- **총수익 기준.** AGG 수익의 대부분이 배당이라 종가만 쓰면 채권 비교가 통째로
  틀린다. 패널은 auto_adjust=False + actions=True 라 배당을 더해 써야 한다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules.index_autopilot import TARGETS  # noqa: E402
from scripts.measure_pead import mde_pp, excess_cagr_ci  # noqa: E402

CACHE = "data/index_autopilot_panel.parquet"
SEED = 20260903
N_SEED = 20          # 무작위 국면 줄의 씨앗 수 — MDE 의 씨앗 잡음을 중위로 걷어낸다
TILT_PP = (10, 20, 30)   # 위험자산 비중을 국면마다 이만큼(%p) 올리고 내린다


def total_returns(gold: str) -> pd.DataFrame:
    """ITOT/AGG/금 일별 총수익. (종가+배당)/전일종가 − 1."""
    panel = pd.read_parquet(CACHE)
    cols = ["ITOT", "AGG", gold]
    close = panel["Close"][cols]
    div = panel["Dividends"].reindex(close.index).fillna(0.0)[cols]
    rets = (close + div) / close.shift(1) - 1.0
    return rets.dropna()


def port_daily(rets: pd.DataFrame, weights: dict) -> np.ndarray:
    """월초에 `weights[월]` 로 맞추고 그 달은 그대로 두는 포트폴리오의 일별 수익.

    달 안에서는 보유가 표류한다 — 매일 목표로 되돌리면 실제로 낼 수 없는 회전이다.
    """
    out = []
    for period, blk in rets.groupby(rets.index.to_period("M")):
        w = np.array([weights[period][t] for t in rets.columns], dtype=float)
        for r in blk.values:
            pr = float(w @ r)
            out.append(pr)
            w = w * (1.0 + r) / (1.0 + pr)     # 표류
    return np.array(out)


def states(gold: str, tilt_pp: float) -> list[dict]:
    """국면 3종. 주식 비중을 ±tilt 만큼 움직이고, 그 몫은 채권·금이 원래 비율(2:1)로 받는다.

    한쪽만 받게 하면 tilt 20 에서 채권이 0 이 되어 tilt 30 을 못 잰다. 2:1 이면
    tilt 30 이 정확히 100/0/0 — 이 세 자산으로 낼 수 있는 최대 진폭이다.
    """
    t = tilt_pp / 100.0
    base = {"ITOT": TARGETS["ITOT"], "AGG": TARGETS["AGG"], gold: TARGETS["GLDM"]}
    share = {"AGG": base["AGG"] / (base["AGG"] + base[gold]),
             gold: base[gold] / (base["AGG"] + base[gold])}
    on = {"ITOT": base["ITOT"] + t, "AGG": base["AGG"] - t * share["AGG"],
          gold: base[gold] - t * share[gold]}
    off = {"ITOT": base["ITOT"] - t, "AGG": base["AGG"] + t * share["AGG"],
           gold: base[gold] + t * share[gold]}
    for s in (base, on, off):
        assert abs(sum(s.values()) - 1.0) < 1e-9, s
        assert all(v >= -1e-12 for v in s.values()), s
    return [off, base, on]


def blind_line(rets: pd.DataFrame, gold: str, tilt_pp: float, seed: int) -> np.ndarray:
    """국면 판정만 무작위인 줄. 비중이 움직이는 폭·빈도는 진짜 규칙과 같다."""
    ss = states(gold, tilt_pp)
    months = rets.index.to_period("M").unique()
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ss), size=len(months))
    return port_daily(rets, {m: ss[i] for m, i in zip(months, pick)})


def oracle_line(rets: pd.DataFrame, gold: str, tilt_pp: float) -> np.ndarray:
    """완전예지 천장 — 매달 사후에 가장 좋았던 국면을 고른다. 전략이 아니라 상한이다."""
    ss = states(gold, tilt_pp)
    best = {}
    for period, blk in rets.groupby(rets.index.to_period("M")):
        cum = (1.0 + blk).prod() - 1.0
        best[period] = ss[int(np.argmax([sum(s[t] * cum[t] for t in rets.columns) for s in ss]))]
    return port_daily(rets, best)


def skill_line(rets: pd.DataFrame, gold: str, tilt_pp: float, p: float, seed: int) -> np.ndarray:
    """월별 국면을 **정확히 확률 p 로** 맞히는 줄. p=1/3 이 우연이다.

    빗나갈 때는 남은 두 국면 중에서 고른다. "확률 p 로 정답, 아니면 셋 중 무작위"
    로 쓰면 실제 적중률이 p+(1−p)/3 이라 p 가 적중률이 아니게 된다.
    """
    ss = states(gold, tilt_pp)
    rng = np.random.default_rng(seed)
    pick = {}
    for period, blk in rets.groupby(rets.index.to_period("M")):
        cum = (1.0 + blk).prod() - 1.0
        best = int(np.argmax([sum(s[t] * cum[t] for t in rets.columns) for s in ss]))
        if rng.random() < p:
            pick[period] = ss[best]
        else:
            pick[period] = ss[[i for i in range(len(ss)) if i != best][rng.integers(0, len(ss) - 1)]]
    return port_daily(rets, pick)


def hit_rate_needed(mde: float, ceiling: float, n_states: int = 3) -> float:
    """초과수익이 MDE 와 같아지는 월별 국면 적중률.

    적중률 p 로 완전예지를 따라가고 나머지는 무작위면 초과수익은 p 에 **선형**이다
    (우연 1/n 에서 0, p=1 에서 천장). 그래서 격자 시뮬 대신 산수로 낸다 —
    선형성은 selftest 5 가 확인한다.
    """
    chance = 1.0 / n_states
    return chance + (1.0 - chance) * mde / ceiling


def flat_line(rets: pd.DataFrame, gold: str) -> np.ndarray:
    months = rets.index.to_period("M").unique()
    base = states(gold, 0)[1]
    return port_daily(rets, {m: base for m in months})


def run() -> None:
    for gold, label in (("GLDM", "GLDM (러너 실물)"), ("GLD", "GLD (금 대용, 긴 창)")):
        rets = total_returns(gold)
        yrs = len(rets) / 252.0
        flat = flat_line(rets, gold)
        print()
        print(f"### {label} — {rets.index[0].date()}~{rets.index[-1].date()} "
              f"({yrs:.1f}년, {len(rets)}일)")
        print(f"{'주식 ±%p':>9} | {'MDE(연 %p)':>10} | {'완전예지 천장':>12} | "
              f"{'천장/MDE':>8} | {'필요 월적중률':>12}")
        for tilt in TILT_PP:
            mdes = [mde_pp(blind_line(rets, gold, tilt, SEED + i), flat) for i in range(N_SEED)]
            mde = float(np.median(mdes))
            ceil_pt, _, _ = excess_cagr_ci(oracle_line(rets, gold, tilt), flat)
            hit = hit_rate_needed(mde, ceil_pt)
            print(f"{tilt:>9} | {mde:>10.2f} | {ceil_pt:>12.2f} | {ceil_pt / mde:>7.1f}x | "
                  f"{hit * 100:>11.1f}%")
        print("  (3지선다라 우연이 33.3%. tilt 를 키우면 MDE 와 효과가 같이 커져 "
              "비율은 안 변한다 — 손잡이는 창 길이뿐이다.)")


def selftest() -> None:
    idx = pd.bdate_range("2020-01-01", periods=300)
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0, 0.01, (300, 3)), index=idx,
                        columns=["ITOT", "AGG", "GLD"])

    # 1. 세 자산 수익이 같으면 어떤 비중이든 포트폴리오 수익이 그 값이다.
    same = pd.DataFrame(np.repeat(rets[["ITOT"]].values, 3, axis=1),
                        index=idx, columns=rets.columns)
    months = idx.to_period("M").unique()
    w = states("GLD", 25)[2]
    assert np.allclose(port_daily(same, {m: w for m in months}), same["ITOT"].values)

    # 2. 무작위 국면 줄의 초과수익 분산은 tilt 에 비례한다 — 자가 tilt 에 반응해야 한다.
    flat = flat_line(rets, "GLD")
    m10 = mde_pp(blind_line(rets, "GLD", 10, 1), flat)
    m30 = mde_pp(blind_line(rets, "GLD", 30, 1), flat)
    assert 0 < m10 < m30, (m10, m30)

    # 3. 완전예지는 기준선을 이긴다. 못 이기면 국면 선택이 뒤집혀 있다.
    assert excess_cagr_ci(oracle_line(rets, "GLD", 20), flat)[0] > 0

    # 4. tilt 0 이면 세 국면이 같은 비중이라 초과수익이 정확히 0 이다.
    assert np.allclose(blind_line(rets, "GLD", 0, 7), flat)
    # 5. 적중률 p 에 대해 초과수익이 선형이다 — hit_rate_needed 가 기대는 성질.
    #    p=1 은 완전예지와 같고, 우연(1/3)에서 0, 중간(2/3)은 천장의 절반이다.
    assert np.allclose(skill_line(rets, "GLD", 20, 1.0, 3), oracle_line(rets, "GLD", 20))
    eff = lambda p: float(np.mean([                                       # noqa: E731
        excess_cagr_ci(skill_line(rets, "GLD", 20, p, 100 + i), flat)[0] for i in range(40)]))
    top, mid, chance = eff(1.0), eff(2 / 3), eff(1 / 3)
    assert abs(chance) < 0.1 * top, (chance, top)
    assert abs(mid - top / 2) < 0.2 * top, (mid, top)

    print("selftest OK")


if __name__ == "__main__":
    selftest() if len(sys.argv) > 1 and sys.argv[1] == "selftest" else run()
