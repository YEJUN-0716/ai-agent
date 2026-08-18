"""bulls_signals 공용 백본 유틸 단위 테스트 (합성 OHLCV, 네트워크 없음)."""
import numpy as np
import pandas as pd

from modules import bulls_signals as bs


def _mk_df(close, high=None, low=None, volume=None):
    """close 리스트로 최소 OHLCV DataFrame 생성."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    high = close + 0.5 if high is None else np.asarray(high, dtype=float)
    low = close - 0.5 if low is None else np.asarray(low, dtype=float)
    vol = np.full(n, 1000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


# ── 거래량 필터 ─────────────────────────────────────────────────
def test_volume_confirmed_true_when_last_bar_spikes():
    vol = [1000.0] * 30 + [3000.0]           # 마지막 봉 거래량 급증
    df = _mk_df(list(range(1, 32)), volume=vol)
    assert bs.volume_confirmed(df, window=20, k=1.5) is True


def test_volume_confirmed_false_on_flat_volume():
    df = _mk_df(list(range(1, 32)))          # 균일 거래량
    assert bs.volume_confirmed(df, window=20, k=1.5) is False


# ── 추세/횡보 레짐 게이트 ────────────────────────────────────────
def test_trend_regime_up_on_strong_uptrend():
    close = list(np.linspace(100, 200, 80))  # 꾸준한 상승
    df = _mk_df(close)
    assert bs.trend_regime(df) == "trend_up"


def test_trend_regime_range_on_sideways():
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0, 0.3, 120)    # 좁은 횡보
    df = _mk_df(close)
    assert bs.trend_regime(df) == "range"


# ── 볼린저 파생값 ───────────────────────────────────────────────
def test_bollinger_pctb_bounds_and_touch_upper():
    close = list(np.linspace(100, 130, 40))  # 지속 상승 → 상단 근처
    df = _mk_df(close)
    feat = bs.bollinger_features(df, window=20)
    assert feat["pctB"] is not None and feat["pctB"] > 0.5
    assert feat["bandwidth"] is not None and feat["bandwidth"] > 0


def test_bollinger_squeeze_flag_on_contraction():
    # 넓은 변동 후 극도 수축 → 마지막 구간 bandwidth 최저
    wide = list(100 + 10 * np.sin(np.linspace(0, 6 * np.pi, 100)))
    tight = list(130 + 0.05 * np.arange(30))
    df = _mk_df(wide + tight)
    feat = bs.bollinger_features(df, window=20, squeeze_lookback=120)
    assert feat["squeeze"] is True


# ── 공용 다이버전스 ─────────────────────────────────────────────
def test_regular_bull_divergence():
    # 가격은 저점 낮춤(LL), 오실레이터는 저점 높임(HL)
    price = pd.Series([10, 6, 10, 12, 10, 4, 10], dtype=float)
    osc = pd.Series([30, 20, 30, 35, 30, 25, 30], dtype=float)
    div = bs.detect_divergence(price, osc, lookback=60, swing_lookback=1)
    assert div["regular_bull"] is True
    assert div["regular_bear"] is False


def test_regular_bear_divergence():
    # 가격은 고점 높임(HH), 오실레이터는 고점 낮춤(LH)
    price = pd.Series([10, 14, 10, 8, 10, 16, 10], dtype=float)
    osc = pd.Series([50, 70, 50, 45, 50, 60, 50], dtype=float)
    div = bs.detect_divergence(price, osc, lookback=60, swing_lookback=1)
    assert div["regular_bear"] is True
    assert div["regular_bull"] is False


# ── 크로스 + 구간 필터 ──────────────────────────────────────────
def test_golden_cross_in_zone():
    fast = pd.Series([10.0, 12.0, 15.0])     # 아래에서 위로 교차
    slow = pd.Series([14.0, 14.0, 14.0])
    ev = bs.cross_events(fast, slow, zone_low=20, zone_high=80)
    assert ev["golden"] is True
    assert ev["golden_in_zone"] is True      # fast_now=15 <= 20
    assert ev["dead"] is False


def test_dead_cross_out_of_zone():
    fast = pd.Series([50.0, 48.0, 40.0])
    slow = pd.Series([45.0, 45.0, 45.0])
    ev = bs.cross_events(fast, slow, zone_low=20, zone_high=80)
    assert ev["dead"] is True
    assert ev["dead_in_zone"] is False       # fast_now=40 < 80


# ── 박스권 돌파 ─────────────────────────────────────────────────
def _box_then_breakout(breakout_volume):
    # 100~110 박스 여러 번 왕복 후 거래량 동반/미동반 상단 돌파
    box = [100, 110, 100, 110, 100, 110, 100, 110, 100, 110,
           100, 110, 100, 110, 100, 110, 100, 110, 100, 110,
           100, 110, 100, 110, 100, 110, 100, 110]
    close = box + [120]                      # 상단(110) 종가 돌파
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    vol = [1000.0] * len(box) + [breakout_volume]
    return _mk_df(close, high=high, low=low, volume=vol)


def test_box_breakout_up_with_volume():
    df = _box_then_breakout(breakout_volume=5000.0)
    r = bs.detect_box_breakout(df, swing_lookback=1, tol_pct=3.0)
    assert r["in_box"] is True
    assert r["breakout_up"] is True
    assert r["vol_confirmed"] is True
    assert r["fakeout"] is False
    assert r["target"] is not None and r["target"] > r["box_high"]


def test_box_breakout_fakeout_without_volume():
    df = _box_then_breakout(breakout_volume=800.0)   # 거래량 미동반
    r = bs.detect_box_breakout(df, swing_lookback=1, tol_pct=3.0)
    assert r["breakout_up"] is True
    assert r["vol_confirmed"] is False
    assert r["fakeout"] is True


# ── 팩터 합성 ───────────────────────────────────────────────────
def test_bulls_raw_score_returns_float_and_handles_short_data():
    assert bs.bulls_raw_score(_mk_df([1, 2, 3])) == 0.0        # 데이터 부족
    score = bs.bulls_raw_score(_mk_df(list(np.linspace(100, 160, 80))))
    assert isinstance(score, float)


def test_bulls_raw_score_missing_columns_returns_zero():
    df = pd.DataFrame({"Close": [1, 2, 3]})
    assert bs.bulls_raw_score(df) == 0.0


# ── 하위팩터 분리 ───────────────────────────────────────────────
def test_bulls_subfactors_keys_and_short_data():
    sf = bs.bulls_subfactors(_mk_df([1, 2, 3]))
    assert set(sf) == {"breakout", "trend", "reversion"}
    assert sf == {"breakout": 0.0, "trend": 0.0, "reversion": 0.0}


def test_reversion_positive_near_lower_band():
    # 하단 밴드 근처(하락 후 저가권)면 reversion > 0 (반등 기대)
    close = list(np.linspace(130, 100, 40))     # 지속 하락 → %B 낮음
    sf = bs.bulls_subfactors(_mk_df(close))
    assert sf["reversion"] > 0
