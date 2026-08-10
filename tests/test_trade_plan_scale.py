import numpy as np
import pandas as pd
import pytest

from modules.ict_analysis import calc_ict_adjustment
from modules.trade_plan import MIN_BARS, build_trade_plan


def _wave(n: int, seed: int = 7) -> pd.DataFrame:
    """결정적인 가짜 봉. 추세 + 잔물결이라 구조 신호가 실제로 잡힌다."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.2, 1.2, n)
    low = close - rng.uniform(0.2, 1.2, n)
    open_ = close - rng.normal(0, 0.4, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": np.full(n, 1e6)}, index=idx)


def test_scale_1_matches_no_scale_argument():
    """scale=1 은 인자를 안 준 것과 글자 그대로 같아야 한다."""
    df = _wave(300)
    assert build_trade_plan(df) == build_trade_plan(df, scale=1)
    assert calc_ict_adjustment(df) == calc_ict_adjustment(df, scale=1)


def test_scale_raises_min_bars_requirement():
    """scale 을 키우면 필요한 워밍업 봉 수도 같이 커진다."""
    df = _wave(MIN_BARS + 5)          # scale=1 에는 충분, scale=4 에는 부족
    assert build_trade_plan(df, scale=1)["reason_invalid"] != "데이터 부족"
    assert build_trade_plan(df, scale=4)["reason_invalid"] == "데이터 부족"


def test_scale_changes_the_plan_on_long_history():
    """창이 실제로 넓어지면 결과가 달라진다 — 인자가 먹히는지 확인."""
    df = _wave(1200)
    assert build_trade_plan(df, scale=1) != build_trade_plan(df, scale=4)


def test_scale_must_be_positive_int():
    df = _wave(300)
    with pytest.raises(ValueError):
        build_trade_plan(df, scale=0)
