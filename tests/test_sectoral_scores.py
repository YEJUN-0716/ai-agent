"""
app.calc_factor_scores_sectoral 동작 고정 테스트.

**프로덕션 기본 스캔 경로다.** signal_worker.py 는 SECTOR_NEUTRAL 이 기본 true 라
평소 이 함수를 쓴다 (calc_factor_scores 는 토글을 꺼야 돈다). 그런데 지금까지
테스트가 하나도 없었다.

섹터 중립화가 핵심이다 — 팩터 점수를 섹터 **안에서** Z-score 정규화해,
"기술주가 통째로 모멘텀이 높은" 식의 섹터 편향이 랭킹을 독점하지 못하게 한다.
섹터 표본이 3종목 미만이면 통계가 무의미하므로 전체 기준 점수로 되돌린다.

네트워크 진입점은 둘이다 (calc_factor_scores 와 달리 IC 가중치를 안 읽는다):
  1. download_stock(tk, ...)
  2. yf.Ticker(tk).info
"""
import time

import numpy as np
import pandas as pd
import pytest

import app
from modules import factor_formulas as ff

FULL_YEAR_BARS = 300


def _ramp(start, end, n=FULL_YEAR_BARS):
    i = np.arange(n)
    return list(np.linspace(start, end, n) + 1.5 * np.sin(i / 3))


def _prices(closes, volume=1_000_000.0):
    close = pd.Series(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(close), freq="B")
    return pd.DataFrame({
        "Open": close.values,
        "High": close.values * 1.01,
        "Low": close.values * 0.99,
        "Close": close.values,
        "Volume": [volume] * len(close),
    }, index=idx)


def _info(sector, **overrides):
    base = {
        "shortName": f"{sector} Co",
        "sector": sector,
        "trailingPE": 20.0,
        "priceToBook": 4.0,
        "returnOnEquity": 0.25,
        "profitMargins": 0.15,
        "freeCashflow": 1000.0,
        "marketCap": 50000.0,
        "netIncomeToCommon": 800.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def patch_market(monkeypatch):
    def _install(frames, infos=None, dart=None):
        infos = infos or {}

        class FakeTicker:
            def __init__(self, tk):
                self._tk = tk

            @property
            def info(self):
                value = infos.get(self._tk, {})
                if isinstance(value, Exception):
                    raise value
                return value

        monkeypatch.setattr(app, "download_stock",
                            lambda tk, start=None, end=None, interval="1d":
                            frames.get(tk, pd.DataFrame()))
        monkeypatch.setattr(app.yf, "Ticker", FakeTicker)
        monkeypatch.setattr(app, "_dart_fallback_batch", lambda tickers: dart or {})
        monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    return _install


# 기술주 3종목은 모멘텀이 강하고, 에너지 3종목은 약하다. 섹터 중립화가
# 동작하면 "약한 섹터의 1등" 이 "강한 섹터의 꼴등" 을 앞질러야 한다.
STRONG_SECTOR = {"TECH_A": (100, 220), "TECH_B": (100, 200), "TECH_C": (100, 180)}
WEAK_SECTOR = {"NRG_A": (100, 110), "NRG_B": (100, 107), "NRG_C": (100, 104)}


def _two_sector_market():
    frames, infos = {}, {}
    for tk, (lo, hi) in STRONG_SECTOR.items():
        frames[tk] = _prices(_ramp(lo, hi))
        infos[tk] = _info("Technology")
    for tk, (lo, hi) in WEAK_SECTOR.items():
        frames[tk] = _prices(_ramp(lo, hi))
        infos[tk] = _info("Energy")
    return frames, infos


# ── 1. 반환 계약 ────────────────────────────────────────────────────

def test_returns_bare_empty_frame_when_nothing_succeeds(patch_market):
    patch_market({})
    result = app.calc_factor_scores_sectoral(["AAA", "BBB"])
    assert result.empty
    assert list(result.columns) == []


def test_row_exposes_sector_and_both_score_scales(patch_market):
    """섹터 내 점수와 전체 기준 점수(_global)를 함께 들고 있어야 폴백이 가능하다."""
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames))
    for col in ["ticker", "name", "price", "sector", "composite", "rank",
                "momentum", "value", "quality", "low_vol",
                "momentum_global", "value_global", "quality_global", "low_vol_global"]:
        assert col in result.columns


def test_sector_defaults_to_unknown_without_fundamentals(patch_market):
    patch_market({"AAA": _prices(_ramp(100, 150))}, infos={"AAA": {}})
    assert app.calc_factor_scores_sectoral(["AAA"]).iloc[0]["sector"] == "Unknown"


def test_rows_are_sorted_by_composite_and_ranked_from_one(patch_market):
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames))
    assert result["rank"].tolist() == list(range(1, len(frames) + 1))
    assert result["composite"].is_monotonic_decreasing


# ── 2. 섹터 중립화 — 이 함수의 존재 이유 ────────────────────────────

def test_weak_sector_leader_outranks_strong_sector_laggard(patch_market):
    """섹터 중립화의 정의. 원점수로는 기술주가 에너지주를 전부 앞서지만,
    섹터 내 정규화 후에는 에너지 1등이 기술 꼴등보다 높아야 한다."""
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames)).set_index("ticker")

    tech = result[result["sector"] == "Technology"]
    energy = result[result["sector"] == "Energy"]
    # 전제 확인: 원점수로는 기술주가 압도한다
    assert tech["momentum_raw"].min() > energy["momentum_raw"].max()
    # 중립화 결과: 약한 섹터의 1등이 강한 섹터의 꼴등을 앞선다
    assert energy["momentum"].max() > tech["momentum"].min()


def test_each_sector_is_normalized_to_the_same_scale(patch_market):
    """섹터마다 평균이 50 근처로 맞춰져야 한다 — 그래야 섹터 간 비교가 성립한다."""
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames))
    for sector in ["Technology", "Energy"]:
        scores = result[result["sector"] == sector]["momentum"]
        assert scores.mean() == pytest.approx(50.0, abs=1.0)


def test_thin_sector_falls_back_to_global_scores(patch_market):
    """3종목 미만 섹터는 섹터 내 Z-score 가 무의미하다 — 전체 기준 점수를 쓴다."""
    frames, infos = _two_sector_market()
    frames["SOLO"] = _prices(_ramp(100, 160))
    infos["SOLO"] = _info("Utilities")
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames)).set_index("ticker")

    solo = result.loc["SOLO"]
    for fname in ["momentum", "value", "quality", "low_vol"]:
        assert solo[fname] == pytest.approx(solo[f"{fname}_global"])


def test_exactly_three_members_is_enough_for_sector_normalization(patch_market):
    """경계값 — 3종목이면 섹터 내 정규화를 한다."""
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames)).set_index("ticker")
    tech = result[result["sector"] == "Technology"]
    # 섹터 내 점수가 전체 기준 점수와 달라야 섹터 정규화가 실제로 일어난 것
    assert not np.allclose(tech["momentum"], tech["momentum_global"])


# ── 3. 가중 합성 ────────────────────────────────────────────────────

def test_default_sectoral_weights_are_pinned(patch_market):
    """calc_factor_scores 의 기본값(.35/.25/.32/.08)과 **다르다**.

    이쪽 low_vol 은 0.15 로, P1-B 축소(ICIR=-0.199)가 반영되지 않았다.
    평소에는 signal_worker 가 get_factor_timing_weights() 결과를 넘겨서
    이 기본값이 안 쓰이지만, FACTOR_TIMING=false 면 이 값이 그대로 나간다.
    """
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    result = app.calc_factor_scores_sectoral(list(frames))
    expected = (result["momentum"] * 0.30 + result["value"] * 0.25
                + result["quality"] * 0.30 + result["low_vol"] * 0.15)
    assert result["composite"].tolist() == pytest.approx(expected.tolist())


def test_composite_is_a_plain_weighted_sum_without_renormalization(patch_market):
    """calc_factor_scores 와 다른 점 — 가중치 합으로 나누지 않는다.

    합이 1이 아닌 가중치를 넘기면 합성점수 스케일이 그대로 따라 움직인다.
    get_factor_timing_weights 는 항상 합 1.0 을 주므로 프로덕션에서는 안 물리지만,
    두 경로의 점수를 같은 임계값으로 비교하면 어긋난다.
    """
    frames, infos = _two_sector_market()
    patch_market(frames, infos)
    single = app.calc_factor_scores_sectoral(
        list(frames),
        factor_weights={"momentum": 0.25, "value": 0.25, "quality": 0.25, "low_vol": 0.25},
    ).set_index("ticker")["composite"]
    doubled = app.calc_factor_scores_sectoral(
        list(frames),
        factor_weights={"momentum": 0.5, "value": 0.5, "quality": 0.5, "low_vol": 0.5},
    ).set_index("ticker")["composite"]
    assert doubled.tolist() == pytest.approx((single * 2).reindex(doubled.index).tolist())


# ── 4. 원점수 ───────────────────────────────────────────────────────

def test_raw_factors_are_not_rounded_here(patch_market):
    """calc_factor_scores 는 원점수를 2자리로 반올림하지만 이쪽은 그대로 둔다.

    정규화 전 값이라 표시용이 아니다. 두 경로의 원점수를 직접 비교하면
    미세하게 어긋난다는 뜻이기도 하다.
    """
    closes = _ramp(100, 220)
    patch_market({"AAA": _prices(closes)}, infos={"AAA": _info("Technology")})
    row = app.calc_factor_scores_sectoral(["AAA"]).iloc[0]
    assert row["momentum_raw"] == (closes[-21] / closes[-252] - 1) * 100


def test_value_and_quality_use_the_shared_blend(patch_market):
    patch_market({"AAA": _prices(_ramp(100, 150))}, infos={"AAA": _info("Technology")})
    row = app.calc_factor_scores_sectoral(["AAA"]).iloc[0]
    assert row["value_raw"] == pytest.approx(
        ff.value_raw(ff.earnings_yield(20.0), ff.book_yield(4.0), 2.0))
    assert row["quality_raw"] == pytest.approx(
        ff.quality_raw(25.0, 15.0, ff.accrual_quality(1000.0, 800.0)))


def test_momentum_is_zero_without_a_full_year_of_bars(patch_market):
    patch_market({"AAA": _prices(_ramp(100, 220, n=200))},
                 infos={"AAA": _info("Technology")})
    assert app.calc_factor_scores_sectoral(["AAA"]).iloc[0]["momentum_raw"] == 0


# ── 5. 실패 처리 ────────────────────────────────────────────────────

def test_unusable_tickers_are_dropped(patch_market):
    """빈 프레임·30봉 미만은 랭킹에서 빠진다."""
    patch_market({
        "GOOD": _prices(_ramp(100, 150)),
        "EMPTY": pd.DataFrame(),
        "SHORT": _prices(_ramp(100, 110, n=10)),
    }, infos={"GOOD": _info("Technology")})
    result = app.calc_factor_scores_sectoral(["GOOD", "EMPTY", "SHORT"])
    assert result["ticker"].tolist() == ["GOOD"]


def test_failed_tickers_are_not_reported_at_all(patch_market):
    """**알려진 결함 — 현재 동작을 고정한다.**

    calc_factor_scores 와 달리 이 함수는 attrs['failed'] 를 만들지 않는다.
    그런데 signal_worker.py:82 는 `fdf.attrs.get('failed', [])` 를 읽어
    텔레그램 알림에 실패 종목 수를 찍는다. SECTOR_NEUTRAL 이 기본 true 이므로
    **평소 알림의 '실패' 카운트는 실제 실패가 몇이든 항상 0** 이다.

    고치는 것은 동작 변경이라 별도 PR 로 분리한다. 그때 이 테스트가
    "이제 보고한다" 로 바뀌어야 한다.
    """
    patch_market({
        "GOOD": _prices(_ramp(100, 150)),
        "EMPTY": pd.DataFrame(),
    }, infos={"GOOD": _info("Technology")})
    result = app.calc_factor_scores_sectoral(["GOOD", "EMPTY"])
    assert "failed" not in result.attrs


def test_fundamentals_failure_still_produces_a_row(patch_market):
    """yfinance 재무가 터져도 가격 팩터로 랭킹은 계속된다."""
    patch_market({"AAA": _prices(_ramp(100, 220))},
                 infos={"AAA": RuntimeError("yfinance down")})
    result = app.calc_factor_scores_sectoral(["AAA"])
    assert len(result) == 1
    assert result.iloc[0]["sector"] == "Unknown"
    assert result.iloc[0]["momentum_raw"] != 0


def test_krx_falls_back_to_dart_when_yfinance_has_no_roe(patch_market):
    patch_market(
        {"005930.KS": _prices(_ramp(100, 150))},
        infos={"005930.KS": {"sector": "Technology", "trailingPE": 20.0, "priceToBook": 4.0}},
        dart={"005930.KS": {"net_income": 30.0, "equity": 200.0, "margin": 12.5}},
    )
    assert app.calc_factor_scores_sectoral(["005930.KS"]).iloc[0]["roe"] == pytest.approx(15.0)
