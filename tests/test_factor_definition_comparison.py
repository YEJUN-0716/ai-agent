"""
팩터 정의 비교 (이중화 해소 2단계) 동작 고정
=============================================
IC 파이프라인이 재는 정의(64/22봉·21봉 변동성)와 프로덕션 스캔이 쓰는
정의(12-1 모멘텀·252봉 변동성)를 **같은 실행 안에서 짝지어** 비교하는
경로를 테스트한다. 네트워크는 쓰지 않는다 — load_panel 과 EDGAR 호출을
모두 합성 데이터로 대체한다.

여기서 지키는 것 세 가지:
  1. 기본값(include_prod_defs=False)에서는 주간 IC 산출 경로가 그대로다.
  2. 프로덕션 정의는 factor_scoring 의 상수를 그대로 따라간다.
  3. 비교는 반드시 짝지어(paired) 이뤄진다 — 한쪽만 있는 시점은 버린다.
"""
import numpy as np
import pandas as pd
import pytest

from modules import factor_formulas as ff
from modules import factor_validator as fv
from modules.factor_scoring import (
    MOMENTUM_LOOKBACK_DAYS,
    MOMENTUM_SKIP_DAYS,
    VOL_WINDOW,
)


def _panel(n_tickers=6, n_bars=700, end=None):
    """오늘까지 이어지는 합성 가격 패널 (티커마다 다른 추세·진폭)."""
    end = end or pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=n_bars)
    return {
        chr(ord("A") + i): pd.Series(
            [100 + (i + 1) * 0.25 * j + (3 + i) * np.sin(j / (7 + i))
             for j in range(n_bars)],
            index=idx,
        )
        for i in range(n_tickers)
    }, idx


# ── 1. 기본값은 기존 경로를 건드리지 않는다 ──────────────────────────

def test_prod_definitions_are_off_by_default():
    """플래그를 켜지 않으면 반환 키가 예전 그대로 — 주간 IC 산출 무변경."""
    prices, idx = _panel()
    got = fv._calc_per_factor_zscores(prices, idx[-1])

    sample = got[next(iter(got))]
    assert set(sample) == {"mom_3m", "mom_1m", "low_vol",
                           "value", "quality", "ict", "bulls"}


def test_legacy_windows_unchanged_when_prod_defs_enabled():
    """프로덕션 정의를 켜도 기존 팩터의 구간은 그대로 64봉·21봉이다."""
    prices, idx = _panel()
    got = fv._calc_per_factor_zscores(prices, idx[-1], include_prod_defs=True)

    raw = pd.Series({tk: ff.momentum_pct(s, 64) for tk, s in prices.items()})
    expected = (raw - raw.mean()) / (raw.std() + 1e-9)
    for tk in prices:
        assert got[tk]["mom_3m"] == pytest.approx(expected[tk])


# ── 2. 프로덕션 정의는 factor_scoring 상수를 따라간다 ────────────────

def test_prod_momentum_uses_production_window():
    """mom_12_1 = momentum_pct(252, skip=21) 의 z-score."""
    prices, idx = _panel()
    got = fv._calc_per_factor_zscores(prices, idx[-1], include_prod_defs=True)

    raw = pd.Series({
        tk: ff.momentum_pct(s, MOMENTUM_LOOKBACK_DAYS, skip_bars=MOMENTUM_SKIP_DAYS)
        for tk, s in prices.items()
    })
    expected = (raw - raw.mean()) / (raw.std() + 1e-9)
    for tk in prices:
        assert got[tk]["mom_12_1"] == pytest.approx(expected[tk])


def test_prod_low_vol_uses_production_window_and_sign():
    """low_vol_252 = 252봉 변동성의 z-score에 음부호 (낮을수록 높은 점수)."""
    prices, idx = _panel()
    got = fv._calc_per_factor_zscores(prices, idx[-1], include_prod_defs=True)

    raw = pd.Series({tk: ff.annualized_vol_pct(s, VOL_WINDOW)
                     for tk, s in prices.items()})
    expected = -(raw - raw.mean()) / (raw.std() + 1e-9)
    for tk in prices:
        assert got[tk]["low_vol_252"] == pytest.approx(expected[tk])


def test_prod_definitions_require_full_warmup():
    """253봉이 안 되는 종목은 단면에서 빠진다.

    12-1 모멘텀을 낼 수 없는 종목을 0(중립)으로 채워 넣으면, 두 정의가
    서로 다른 단면 위에서 측정돼 짝지은 비교가 성립하지 않는다.
    """
    prices, idx = _panel()
    short_tk = "SHORT"
    prices[short_tk] = pd.Series(
        [100 + j for j in range(200)], index=idx[-200:]
    )

    with_prod = fv._calc_per_factor_zscores(prices, idx[-1], include_prod_defs=True)
    legacy_only = fv._calc_per_factor_zscores(prices, idx[-1])

    assert short_tk not in with_prod, "워밍업 부족 종목이 남아 있다"
    assert short_tk in legacy_only, "기존 경로의 65봉 기준이 바뀌었다"


# ── 3. 짝지은 차이 통계 ──────────────────────────────────────────────

def test_paired_stats_rejects_mismatched_lengths():
    """길이가 다르면 짝이 깨진 것 — 조용히 자르지 말고 실패시킨다."""
    with pytest.raises(ValueError):
        fv._paired_ic_stats([0.1, 0.2, 0.3], [0.1, 0.2])


def test_paired_stats_flags_pure_noise_as_undecided():
    """평균 차이가 0 근처면 |t| < 2 → '구분 불가'."""
    rng = np.random.default_rng(0)
    legacy = rng.normal(0.01, 0.16, 60)
    prod = legacy + rng.normal(0.0, 0.16, 60)

    stats = fv._paired_ic_stats(legacy.tolist(), prod.tolist())
    assert abs(stats["t_stat"]) < fv.PAIRED_T_THRESHOLD
    assert stats["verdict"].startswith("구분 불가")


def test_paired_stats_detects_small_consistent_edge():
    """개별 mean_IC 는 SE 에 묻혀도, 짝지으면 작은 우위가 드러난다.

    이것이 2단계를 '따로 두 번 재기'로 하지 않는 이유다. 두 정의에 공통으로
    실린 시점 변동(±0.16)이 차이에서 상쇄되고 우위만 남는다.
    """
    rng = np.random.default_rng(1)
    common = rng.normal(0.0, 0.16, 60)          # 시점마다 공통으로 실리는 잡음
    legacy = 0.01 + common
    prod = legacy + 0.02 + rng.normal(0.0, 0.01, 60)

    stats = fv._paired_ic_stats(legacy.tolist(), prod.tolist())

    # 각 정의만 보면 SE 에 묻힌다.
    assert abs(stats["legacy_mean_ic"]) < 2 * stats["legacy_se"]
    # 짝지으면 유의하다.
    assert stats["t_stat"] > fv.PAIRED_T_THRESHOLD
    assert stats["verdict"] == "프로덕션 정의 우세"
    assert stats["mean_diff"] == pytest.approx(0.02, abs=0.005)


def test_paired_stats_reports_losing_definition():
    rng = np.random.default_rng(2)
    common = rng.normal(0.0, 0.16, 60)
    legacy = 0.03 + common
    prod = legacy - 0.02 + rng.normal(0.0, 0.01, 60)

    stats = fv._paired_ic_stats(legacy.tolist(), prod.tolist())
    assert stats["t_stat"] < -fv.PAIRED_T_THRESHOLD
    assert stats["verdict"] == "기존 IC 정의 우세"


def test_paired_stats_zero_variance_does_not_read_as_undecided():
    """모든 시점에서 차이가 같으면 변동이 0 — '구분 불가'로 새면 안 된다."""
    legacy = [0.01] * 10
    prod = [0.03] * 10

    stats = fv._paired_ic_stats(legacy, prod)
    assert stats["mean_diff"] == pytest.approx(0.02)
    assert stats["t_stat"] > fv.PAIRED_T_THRESHOLD
    assert stats["verdict"] == "프로덕션 정의 우세"


# ── 4. 시점별 IC 헬퍼 ────────────────────────────────────────────────

def test_period_ics_skips_thin_cross_sections():
    """공통 종목이 5개 미만이면 그 시점은 통째로 버린다."""
    prices, idx = _panel(n_tickers=3)
    scores = {tk: {"mom_3m": 0.5} for tk in prices}
    got = fv._period_factor_ics(prices, scores, idx[-22], idx[-1], ["mom_3m"])
    assert got == {}


def test_period_ics_returns_one_value_per_factor():
    prices, idx = _panel()
    scores = fv._calc_per_factor_zscores(prices, idx[-22], include_prod_defs=True)
    factors = ["mom_3m", "mom_12_1", "low_vol", "low_vol_252"]
    got = fv._period_factor_ics(prices, scores, idx[-22], idx[-1], factors)

    assert set(got) == set(factors)
    assert all(-1.0 <= v <= 1.0 for v in got.values())


# ── 5. 전체 비교 실행 (네트워크 대체) ────────────────────────────────

@pytest.fixture
def offline_panel(monkeypatch):
    """load_panel 과 EDGAR 조회를 합성 데이터로 대체."""
    prices, idx = _panel()

    monkeypatch.setattr(fv, "load_panel", lambda tks, s, e: (prices, {}))
    monkeypatch.setattr(fv, "_fetch_fin_hist", lambda tks: {})
    monkeypatch.setattr(fv, "_fetch_shares_hist", lambda tks: {})
    monkeypatch.setattr(fv, "_fetch_equity_hist", lambda tks: {})
    return prices, idx


def test_comparison_pairs_every_period(offline_panel):
    """두 정의의 IC 열 길이가 같아야 짝지은 비교가 성립한다."""
    prices, _ = offline_panel
    res = fv.run_factor_definition_comparison(list(prices), lookback_years=1)

    assert res, "비교 결과가 비어 있다"
    lengths = {len(v) for v in res["per_period"].values()}
    assert len(lengths) == 1, f"팩터별 IC 개수가 다르다: {lengths}"
    assert len(res["dates"]) == lengths.pop()
    assert all(p["n"] == len(res["dates"]) for p in res["pairs"])


def test_comparison_covers_both_factor_pairs(offline_panel):
    prices, _ = offline_panel
    res = fv.run_factor_definition_comparison(list(prices), lookback_years=1)

    labels = {p["label"] for p in res["pairs"]}
    assert labels == {"momentum", "low_vol"}
    for p in res["pairs"]:
        assert p["prod"] in res["per_period"]
        assert p["legacy"] in res["per_period"]
        assert "verdict" in p


def test_comparison_measurement_window_respects_lookback(offline_panel):
    """워밍업은 창 앞쪽에서 따로 받아 오고, 리밸런싱은 요청한 창 안에만."""
    prices, _ = offline_panel
    res = fv.run_factor_definition_comparison(list(prices), lookback_years=1)

    first = pd.Timestamp(res["dates"][0])
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=365)
    assert first >= cutoff - pd.Timedelta(days=7)


def test_comparison_writes_no_weights(offline_panel, monkeypatch):
    """가중치 파일을 건드리지 않는다 — 측정 전용."""
    prices, _ = offline_panel

    def _boom(*a, **k):
        raise AssertionError("측정 경로에서 파일을 열었다")

    monkeypatch.setattr("builtins.open", _boom)
    res = fv.run_factor_definition_comparison(list(prices), lookback_years=1)
    assert res
