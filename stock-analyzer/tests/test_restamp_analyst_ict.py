"""기록 수리 도구 — 옛 코드 사본이 진짜 옛 코드인가.

`scripts/restamp_analyst_ict.py` 는 저장된 ICT 점수를 고친 코드로 다시 찍는데,
**재현 가능한 것만 덮어쓴다.** 재현의 기준이 되는 것이 `legacy_find_bos_choch`
— PR #169 이전 코드의 사본이다. 이 사본이 진짜와 다르면 수리 도구가 멀쩡한
기록을 훼손하거나(옛값이 안 맞아 전부 건너뜀) 잘못된 값을 덮어쓴다.

여기서 잠그는 것 셋:
  1. 사본은 현재 함수와 **같은 이벤트 집합**을 낸다 (순서만 다르다)
  2. 늦게 깨진 판에서 사본은 실제로 **다른 답**을 집는다 (옛 결함 재현)
  3. verdict 가중치 되찾기가 심어 둔 가중치를 그대로 돌려준다
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from modules.ict_analysis import find_bos_choch, find_swing_points

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import restamp_analyst_ict as m  # noqa: E402


def _wave(n: int = 300, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.03, 1.0, n))
    return pd.DataFrame({
        "Open": close, "High": close + rng.uniform(0.2, 1.2, n),
        "Low": close - rng.uniform(0.2, 1.2, n), "Close": close,
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))


def test_legacy_copy_finds_the_same_events():
    """순서만 다르고 집합은 같아야 한다 — 다르면 알고리즘을 잘못 베낀 것이다."""
    for seed in (1, 5, 9):
        df = _wave(seed=seed)
        swings = find_swing_points(df, lookback=5)
        legacy = m.legacy_find_bos_choch(df, swings)
        assert sorted(legacy, key=lambda e: e["idx"]) == find_bos_choch(df, swings)


def test_legacy_copy_reproduces_the_old_pick():
    """오래된 레벨이 나중에 깨지는 판 — 옛 코드는 최신이 아닌 것을 집었다."""
    closes = [100.0] * 30
    closes[10] = 85.0     # L(90) 하향 돌파 — 먼저
    closes[20] = 115.0    # H(110) 상향 돌파 — 나중
    df = pd.DataFrame({"Close": closes},
                      index=pd.date_range("2024-01-01", periods=30, freq="D"))
    swings = pd.DataFrame([{"idx": 2, "date": None, "price": 110.0, "type": "H"},
                           {"idx": 5, "date": None, "price": 90.0, "type": "L"},
                           {"idx": 8, "date": None, "price": 105.0, "type": "H"}])

    legacy = m.legacy_find_bos_choch(df, swings)
    fixed = find_bos_choch(df, swings)
    assert len(legacy) == len(fixed) == 2
    assert "bear" in legacy[-1]["type"]      # 옛 코드가 집던 것 (먼저 깨진 쪽)
    assert "bull" in fixed[-1]["type"]       # 고친 코드가 집는 것 (나중에 깨진 쪽)


def test_weights_are_recovered_from_the_record():
    """가중치는 매주 바뀐다 — 오늘 파일이 아니라 그날 기록에서 되찾아야 한다."""
    rng = np.random.default_rng(0)
    true_w = np.array([0.61, 0.33, 0.06])
    scores = {}
    for i in range(120):
        c, q, ict = rng.uniform(20, 90, 3)
        v = float(true_w @ np.array([c, q, ict]))
        scores[f"T{i}"] = {"chart": round(c, 1), "quant": round(q, 1),
                           "ict": round(ict, 1), "verdict": round(v, 1)}
    got = m.recover_weights(scores)
    assert got is not None
    w_ict, resid = got
    assert abs(w_ict - true_w[2]) < 0.01
    assert resid <= m.MAX_WEIGHT_RESIDUAL


def test_weights_are_refused_when_verdict_is_not_a_blend():
    """되찾기가 실패하면 verdict 를 건드리지 않는다 — 추정으로 기록을 쓰지 않는다."""
    rng = np.random.default_rng(1)
    scores = {f"T{i}": {"chart": float(rng.uniform(20, 90)),
                        "quant": float(rng.uniform(20, 90)),
                        "ict": float(rng.uniform(20, 90)),
                        "verdict": float(rng.uniform(20, 90))}
              for i in range(120)}
    assert m.recover_weights(scores) is None
