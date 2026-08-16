"""라인 하나 진입 규칙(D·E)의 **R 분모**를 못 박는다.

측정 스크립트지만 여기서 나온 숫자 하나로 자동 주문을 켤지 말지가 갈린다.
분모가 틀리면 표는 멀쩡해 보이면서 결론만 뒤집힌다 — 실제로 3a 가 그렇게
죽었다(docs/measurements/2026-08-12-entry-rule.md).

못 박는 것 세 가지:
  1. E 는 **다음 봉 시가**에 체결된다 (되돌림을 안 기다린다).
  2. D·E 의 1R 은 **산 값 − 손절**이다 (플랜 위험이 아니다).
  3. 갭으로 더 좋게 사면 위험폭이 좁아져 **비용이 몇 R 인지가 커진다.**
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import measure_entry_rule as m  # noqa: E402


def _plan(*, low=100.0, high=104.0, stop=99.0, target=110.0):
    """진입 구간 [100,104] · ref 102 · 손절 99 · 목표 110 인 롱 플랜."""
    return {"direction": "long", "entry": {"low": low, "high": high,
                                           "ref": (low + high) / 2},
            "stop": stop, "targets": [target], "rr": [
                (target - (low + high) / 2) / ((low + high) / 2 - stop)]}


def _ctx(bars: list[tuple[float, float, float]], i=0):
    """(open, high, low) 봉들. i 다음 봉부터 체결을 찾는다."""
    o, h, l = (np.array([b[k] for b in bars], dtype=float) for k in range(3))
    return {"highs": h, "lows": l, "opens": None, "all_opens": o,
            "sessions": None, "i": i}


def test_E_체결가는_다음봉_시가다():
    plan = _plan()
    # 다음 봉 시가 105 → 거기서 사고 목표 110 을 같은 봉에 찍는다.
    ctx = _ctx([(103, 103, 103), (105, 110, 104), (110, 111, 109)])
    res = m._sim("E_시장가", plan, ctx)
    assert res["outcome"] == "win"
    assert res["fill_idx"] == 1
    # 1R = 105 - 99 = 6, 이익 = 110 - 105 = 5 → +0.833R.
    # 플랜 위험(102-99=3)으로 셌으면 +1.67R 이 나온다 — 두 배 틀린다.
    assert res["r"] == pytest.approx(5 / 6, abs=1e-3)
    assert res["risk_pct"] == pytest.approx(6 / 105 * 100, abs=1e-3)


def test_D_하단은_손절폭이_좁아_문턱에_걸린다():
    """구간 하단(100)의 손절폭은 1% — 문턱을 올려 두면 skip 이어야 한다."""
    plan = _plan()
    ctx = _ctx([(103, 103, 103), (102, 103, 99.5), (101, 102, 100)])
    assert m._sim("D_하단지정가", plan, ctx)["outcome"] != "skip"
    m_old = m.MIN_RISK_PCT
    m.MIN_RISK_PCT = 2.0          # 1% < 2% → 그 라인에서는 안 거는 판
    try:
        assert m._sim("D_하단지정가", plan, ctx)["outcome"] == "skip"
    finally:
        m.MIN_RISK_PCT = m_old


def test_갭으로_더_좋게_사면_위험폭이_좁아진다():
    """지정가 100 인데 시가 99.5 로 갭 → 99.5 에 산다. 1R = 0.5 로 좁아진다."""
    plan = _plan()
    # 2번 봉에서 체결(저가 100), 그 봉 시가가 99.5 라 지정가보다 낮다.
    bars = [(103, 103, 103), (99.5, 103, 100), (101, 110, 100)]
    ctx = _ctx(bars)
    base = m._sim("D_하단지정가", plan, ctx)
    assert base["fill_idx"] == 1
    gap = m.placeable_r(base, plan, 100.0, ctx["all_opens"],
                        plan_risk=3.0, self_basis=True)
    assert gap["outcome"] == base["outcome"]
    # 위험폭이 1.0 → 0.5 로 반토막 = 같은 6bp 가 두 배 R 을 먹는다.
    assert gap["risk_pct"] == pytest.approx(0.5 / 99.5 * 100, abs=1e-3)
    assert gap["risk_pct"] < base["risk_pct"]


def test_net_은_규칙별_손절폭으로_비용을_나눈다():
    """플랜 손절폭 1% · E 손절폭 2% → 같은 6bp 가 0.06R vs 0.03R."""
    df = pd.DataFrame([{"risk_pct": 1.0, "E_시장가_risk_pct": 2.0,
                        "E_시장가_outcome": "win", "E_시장가_r": 1.0,
                        "B_ref지정가_outcome": "win", "B_ref지정가_r": 1.0}])
    assert m._net(df, "E_시장가", 6.0)[0] == pytest.approx(1 - 0.0006 / 0.02)
    assert m._net(df, "B_ref지정가", 6.0)[0] == pytest.approx(1 - 0.0006 / 0.01)
