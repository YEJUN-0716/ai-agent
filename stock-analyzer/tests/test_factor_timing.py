"""
app.get_factor_timing_weights 동작 고정 테스트.

signal_worker.py 가 매 스캔마다 이 함수를 불러 팩터 가중치를 정한다
(FACTOR_TIMING 기본값 true). 즉 **프로덕션 팩터 배분의 실질적 결정자**인데,
VIX/금리 임계값이 app.py 안에 매직넘버로 박혀 있어 바뀌어도 알 방법이 없었다.

네트워크를 타지 않는다 — yf.download 를 합성 VIX/TNX 프레임으로 대체한다.
"""
import pandas as pd
import pytest

import app

FACTOR_KEYS = {"momentum", "value", "quality", "low_vol"}


def _series_frame(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.DataFrame({"Close": [float(v) for v in values]}, index=idx)


@pytest.fixture
def patch_macro(monkeypatch):
    """VIX 수준과 금리 변화폭을 지정해 국면을 만든다."""
    def _install(vix=20.0, rate_change=0.0, bars=60, fail=False):
        # rate_chg = Close[-1] - Close[-30] 이므로 마지막 30봉에 변화를 준다.
        tnx = [4.0] * (bars - 30) + [4.0 + rate_change * i / 29 for i in range(30)]

        def fake_download(symbol, *args, **kwargs):
            if fail:
                raise RuntimeError("yfinance down")
            return _series_frame([vix] * bars) if symbol == "^VIX" else _series_frame(tnx)

        monkeypatch.setattr(app.yf, "download", fake_download)
    return _install


# ── 반환 계약 ──────────────────────────────────────────────────────

def test_returns_four_factor_weights_and_environment(patch_macro):
    patch_macro()
    weights, env = app.get_factor_timing_weights()
    assert set(weights) == FACTOR_KEYS
    for key in ["vix", "vix_avg", "rate", "rate_chg", "regime"]:
        assert key in env


@pytest.mark.parametrize("vix,rate_change", [
    (35.0, 0.0), (10.0, 0.0), (20.0, 0.0),
    (35.0, 0.5), (10.0, -0.5), (20.0, 0.5), (20.0, -0.5),
])
def test_weights_always_sum_to_exactly_one(patch_macro, vix, rate_change):
    """3자리 반올림 후 잔차를 최대 항목에 보정한다 — 합이 0.999 로 새면 안 된다.

    합성점수 스케일이 국면마다 달라지면 BUY_SCORE_MIN 같은 절대 임계값이
    조용히 의미를 잃는다.
    """
    patch_macro(vix=vix, rate_change=rate_change)
    weights, _ = app.get_factor_timing_weights()
    assert sum(weights.values()) == pytest.approx(1.0)


# ── VIX 국면 ───────────────────────────────────────────────────────

def test_high_vix_shifts_weight_to_quality_and_low_vol(patch_macro):
    """VIX > 25: 저변동성은 IC 가 음수여도 하락장 방어용으로 되살린다."""
    patch_macro(vix=35.0)
    weights, env = app.get_factor_timing_weights()
    assert "고변동성" in env["regime"]
    assert weights["quality"] > weights["momentum"]
    assert weights["low_vol"] > weights["momentum"]


def test_low_vix_shifts_weight_to_momentum(patch_macro):
    """VIX < 15: 모멘텀 강조, low_vol 은 P1-B 축소값 유지."""
    patch_macro(vix=10.0)
    weights, env = app.get_factor_timing_weights()
    assert "저변동성" in env["regime"]
    assert weights["momentum"] == max(weights.values())
    assert weights["low_vol"] < weights["value"]


def test_normal_vix_matches_the_default_four_factor_blend(patch_macro):
    """보통 국면 가중치는 calc_factor_scores 기본값과 같아야 한다.

    둘이 어긋나면 SECTOR_NEUTRAL / FACTOR_TIMING 토글만으로 배분이 달라진다.
    """
    patch_macro(vix=20.0)
    weights, env = app.get_factor_timing_weights()
    assert env["regime"] == "보통"
    assert weights == pytest.approx(
        {"momentum": 0.35, "value": 0.25, "quality": 0.32, "low_vol": 0.08})


@pytest.mark.parametrize("vix", [25.0, 15.0])
def test_threshold_values_themselves_land_in_the_normal_regime(patch_macro, vix):
    """경계값은 열린 구간 — 25 와 15 자체는 '보통' 이다."""
    patch_macro(vix=vix)
    _, env = app.get_factor_timing_weights()
    assert env["regime"] == "보통"


# ── 금리 오버레이 ──────────────────────────────────────────────────

def test_rising_rates_tilt_toward_value(patch_macro):
    """금리 상승기(+0.3%p 초과)에는 밸류를 올리고 모멘텀을 내린다."""
    patch_macro(vix=20.0, rate_change=0.5)
    tilted, env = app.get_factor_timing_weights()
    patch_macro(vix=20.0, rate_change=0.0)
    flat, _ = app.get_factor_timing_weights()
    assert "금리상승" in env["regime"]
    assert tilted["value"] > flat["value"]
    assert tilted["momentum"] < flat["momentum"]


def test_falling_rates_tilt_toward_momentum(patch_macro):
    patch_macro(vix=20.0, rate_change=-0.5)
    tilted, env = app.get_factor_timing_weights()
    patch_macro(vix=20.0, rate_change=0.0)
    flat, _ = app.get_factor_timing_weights()
    assert "금리하락" in env["regime"]
    assert tilted["momentum"] > flat["momentum"]
    assert tilted["value"] < flat["value"]


@pytest.mark.parametrize("rate_change", [0.3, -0.3, 0.1, -0.1])
def test_small_rate_moves_do_not_trigger_the_overlay(patch_macro, rate_change):
    """±0.3%p 는 경계 포함 무시 — 잡음에 배분이 흔들리면 회전율만 오른다."""
    patch_macro(vix=20.0, rate_change=rate_change)
    _, env = app.get_factor_timing_weights()
    assert env["regime"] == "보통"


def test_value_tilt_is_capped(patch_macro):
    """고변동성(밸류 0.25) + 금리상승(+0.10) 이어도 상한 0.40 을 넘지 않는다."""
    patch_macro(vix=35.0, rate_change=1.0)
    weights, _ = app.get_factor_timing_weights()
    # 정규화 전 상한 0.40, 정규화 후에도 그 비율을 넘길 수 없다
    assert weights["value"] <= 0.40 + 1e-9


# ── 폴백 ───────────────────────────────────────────────────────────

def test_macro_download_failure_falls_back_to_neutral(patch_macro):
    """VIX/금리 조회가 실패해도 스캔은 계속돼야 한다 — 중립 가정으로 진행."""
    patch_macro(fail=True)
    weights, env = app.get_factor_timing_weights()
    assert env["regime"] == "보통"
    assert env["vix"] == 20
    assert sum(weights.values()) == pytest.approx(1.0)
