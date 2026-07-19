"""IC 가중치 도출 로직 테스트."""
import pytest
from ic_weight_updater import derive_ic_regime_weights

BASE = {
    "bull":    {"mom_3m": 0.35, "mom_1m": 0.10, "low_vol": 0.10,
                "value": 0.15, "quality": 0.15, "ict": 0.15},
    "bear":    {"mom_3m": 0.15, "mom_1m": 0.05, "low_vol": 0.30,
                "value": 0.20, "quality": 0.20, "ict": 0.10},
}

# value/quality는 PIT 재무 미확보로 n=0
IC = {
    "mom_3m":  {"mean_ic": 0.0202, "n": 58},
    "mom_1m":  {"mean_ic": 0.0056, "n": 58},
    "low_vol": {"mean_ic": -0.0692, "n": 58},
    "value":   {"mean_ic": 0.0, "n": 0},
    "quality": {"mean_ic": 0.0, "n": 0},
    "ict":     {"mean_ic": 0.0054, "n": 58},
}


def test_unavailable_factors_get_zero_weight():
    """IC가 측정되지 않은 팩터에는 자본을 배분하지 않는다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=["value", "quality"])
    for regime in BASE:
        assert w[regime]["value"] == 0.0
        assert w[regime]["quality"] == 0.0


def test_remaining_weights_renormalize_to_one():
    """제외 후 나머지 팩터의 합이 1이 되어야 한다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=["value", "quality"])
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)


def test_no_unavailable_matches_old_behavior():
    """제외할 팩터가 없으면 기존처럼 전 팩터에 배분한다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=[])
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)
        assert w[regime]["value"] > 0


def test_all_unavailable_falls_back_to_base():
    """전 팩터가 미측정이면 0으로 나누지 않고 기본 가중치를 쓴다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=list(IC.keys()))
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)


def test_default_unavailable_is_none_safe():
    """unavailable을 안 넘겨도 기존 호출부가 깨지지 않는다."""
    w = derive_ic_regime_weights(IC, BASE)
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)
