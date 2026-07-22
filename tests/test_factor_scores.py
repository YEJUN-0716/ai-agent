"""
app.calc_factor_scores 동작 고정 테스트.

이 함수가 팩터 랭킹의 심장이다. signal_worker.py 가 스캔 결과를 여기서 받아
매수 후보를 정하고, UI 의 팩터 랭킹 탭도 같은 함수를 쓴다. 그런데 7,600줄
모놀리스 한가운데 있어서, 무관한 UI 수정이 점수 공식을 바꿔도 알 방법이 없다.

여기서 (1) 반환 스키마 (2) 원점수 공식 (3) 정규화·가중 합성 (4) 실패 종목
처리를 못 박아 둔다. modules/ 로 추출할 때 이 테스트가 **한 줄도 바뀌지 않고**
통과해야 동작이 보존된 것이다 — signal_engine 추출 때와 같은 절차다.

네트워크를 타지 않는다. 이 함수에는 외부 호출 진입점이 셋 있다:
  1. download_stock(tk, ...)          — 가격
  2. yf.Ticker(tk).info               — 재무
  3. _load_ic_factor_weights_4f()     — 내부에서 get_market_regime() 이 SPY 를
                                        내려받는다. 눈에 잘 안 띄는 세 번째 경로다.
셋 다 patch_market 픽스처가 막는다.
"""
import time

import numpy as np
import pandas as pd
import pytest

import app
from modules import factor_formulas as ff

FULL_YEAR_BARS = 300   # skip-1M 모멘텀(-21 / -252)이 성립하는 최소 길이보다 넉넉히


def _ramp(start, end, n=FULL_YEAR_BARS):
    """직선 추세에 잔진동을 얹은 종가 시퀀스.

    완전 단조 증가면 일간수익률 분산이 비현실적으로 작아 변동성 팩터가
    무의미해진다. 진동을 섞어야 low_vol 원점수가 실제처럼 움직인다.
    """
    i = np.arange(n)
    return list(np.linspace(start, end, n) + 1.5 * np.sin(i / 3))


def _prices(closes, volume=1_000_000.0):
    """합성 OHLCV. calc_factor_scores 는 Close/Volume 만 본다."""
    close = pd.Series(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(close), freq="B")
    return pd.DataFrame({
        "Open": close.values,
        "High": close.values * 1.01,
        "Low": close.values * 0.99,
        "Close": close.values,
        "Volume": [volume] * len(close),
    }, index=idx)


# 재무 지표가 전부 채워진 기준 종목. 파생값이 손으로 검산되는 숫자로 골랐다.
#   EP = 100/20 = 5.0,  BP = 100/4 = 25.0,  FCF수익률 = 1000/50000*100 = 2.0
#   ROE = 25.0%,  이익률 = 15.0%,  발생액품질 = (1000/800)*50 = 62.5
CLEAN_INFO = {
    "shortName": "Clean Corp",
    "trailingPE": 20.0,
    "priceToBook": 4.0,
    "returnOnEquity": 0.25,
    "profitMargins": 0.15,
    "freeCashflow": 1000.0,
    "marketCap": 50000.0,
    "netIncomeToCommon": 800.0,
}


@pytest.fixture
def patch_market(monkeypatch):
    """가격·재무·IC가중치·슬립을 전부 합성값으로 갈아끼운다."""
    def _install(frames, infos=None, dart=None, earnings=None, ic_weights=None):
        infos = infos or {}
        earnings = earnings or {}

        def fake_download(tk, start=None, end=None, interval="1d"):
            return frames.get(tk, pd.DataFrame())

        class FakeTicker:
            def __init__(self, tk):
                self._tk = tk

            @property
            def info(self):
                value = infos.get(self._tk, {})
                if isinstance(value, Exception):
                    raise value
                return value

            @property
            def earnings_history(self):
                return earnings.get(self._tk)

        monkeypatch.setattr(app, "download_stock", fake_download)
        monkeypatch.setattr(app.yf, "Ticker", FakeTicker)
        monkeypatch.setattr(app, "_dart_fallback_batch", lambda tickers: dart or {})
        # None 이면 기본 가중치 경로. 진짜 함수는 SPY 를 내려받으므로 반드시 막는다.
        monkeypatch.setattr(app, "_load_ic_factor_weights_4f",
                            lambda regime=None: ic_weights)
        # 종목당 0.3초 레이트리밋 슬립 — 테스트에서는 의미 없다.
        monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    return _install


def _single(patch_market, closes=None, info=None, **kwargs):
    """종목 하나짜리 스캔 → 그 한 행. 원점수 공식 검증용."""
    patch_market({"AAA": _prices(closes if closes is not None else _ramp(100, 150))},
                 infos={"AAA": CLEAN_INFO if info is None else info})
    result = app.calc_factor_scores(["AAA"], **kwargs)
    assert len(result) == 1
    return result.iloc[0]


# ── 1. 반환 계약 ────────────────────────────────────────────────────

def test_returns_bare_empty_frame_when_nothing_succeeds(patch_market):
    """전 종목 실패 시 빈 DataFrame — 호출부는 .empty 로만 분기한다."""
    patch_market({})
    result = app.calc_factor_scores(["AAA", "BBB"])
    assert result.empty
    assert list(result.columns) == []


def test_row_exposes_raw_scores_normalized_scores_and_rank(patch_market):
    row = _single(patch_market)
    for col in ["ticker", "name", "price",
                "momentum_raw", "value_raw", "quality_raw", "low_vol_raw",
                "momentum", "value", "quality", "low_vol",
                "vol", "per", "pbr", "roe", "composite", "rank"]:
        assert col in row.index, f"{col} 컬럼이 사라졌다 — 호출부가 KeyError 로 죽는다."


def test_price_and_name_come_from_last_bar_and_info(patch_market):
    closes = _ramp(100, 150)
    row = _single(patch_market, closes=closes)
    assert row["price"] == pytest.approx(closes[-1])
    assert row["name"] == "Clean Corp"


def test_name_falls_back_to_ticker_without_fundamentals(patch_market):
    row = _single(patch_market, info={})
    assert row["name"] == "AAA"


# ── 2. 실패·필터 처리 ───────────────────────────────────────────────

def test_failed_tickers_are_dropped_and_recorded(patch_market):
    """실패 종목은 행에서 빠지고 attrs['failed'] 로만 보고된다."""
    patch_market({
        "GOOD": _prices(_ramp(100, 150)),
        "EMPTY": pd.DataFrame(),
        "SHORT": _prices(_ramp(100, 110, n=10)),
    }, infos={"GOOD": CLEAN_INFO})
    result = app.calc_factor_scores(["GOOD", "EMPTY", "SHORT"])
    assert result["ticker"].tolist() == ["GOOD"]
    assert sorted(result.attrs.get("failed", [])) == ["EMPTY", "SHORT"]


def test_ticker_below_thirty_bars_is_rejected(patch_market):
    """30봉 미만은 지표를 신뢰할 수 없어 통째로 버린다."""
    patch_market({"AAA": _prices(_ramp(100, 110, n=29))})
    assert app.calc_factor_scores(["AAA"]).empty


def test_thirty_bars_is_enough(patch_market):
    """경계값 — 30봉은 통과한다."""
    patch_market({"AAA": _prices(_ramp(100, 110, n=30))},
                 infos={"AAA": CLEAN_INFO})
    assert len(app.calc_factor_scores(["AAA"])) == 1


def test_min_avg_volume_filters_illiquid_ticker(patch_market):
    """유동성 필터는 최근 20봉 평균 거래량 기준."""
    patch_market({
        "LIQUID": _prices(_ramp(100, 150), volume=5_000_000.0),
        "THIN": _prices(_ramp(100, 150), volume=1_000.0),
    }, infos={"LIQUID": CLEAN_INFO, "THIN": CLEAN_INFO})
    result = app.calc_factor_scores(["LIQUID", "THIN"], min_avg_volume=100_000)
    assert result["ticker"].tolist() == ["LIQUID"]
    assert result.attrs.get("failed") == ["THIN"]


def test_min_avg_volume_zero_disables_the_filter(patch_market):
    patch_market({"THIN": _prices(_ramp(100, 150), volume=1.0)},
                 infos={"THIN": CLEAN_INFO})
    assert len(app.calc_factor_scores(["THIN"], min_avg_volume=0)) == 1


# ── 3. 원점수 공식 ──────────────────────────────────────────────────

def test_momentum_skips_the_most_recent_month(patch_market):
    """P1-A: 2~12개월 누적 수익률. 최근 1개월은 단기 역전 때문에 제외한다."""
    closes = _ramp(100, 220)
    row = _single(patch_market, closes=closes)
    expected = (closes[-21] / closes[-252] - 1) * 100
    assert row["momentum_raw"] == pytest.approx(round(expected, 2))
    # 최근 1개월을 포함하는 순진한 공식과는 달라야 한다.
    naive = (closes[-1] / closes[-252] - 1) * 100
    assert row["momentum_raw"] != pytest.approx(round(naive, 2))


def test_momentum_is_zero_without_a_full_year_of_bars(patch_market):
    """252봉이 안 되면 모멘텀을 추정하지 않고 0으로 둔다 (짧은 구간 과적합 방지)."""
    row = _single(patch_market, closes=_ramp(100, 220, n=200))
    assert row["momentum_raw"] == 0


def test_low_vol_raw_is_hundred_minus_annualized_volatility(patch_market):
    closes = _ramp(100, 150)
    row = _single(patch_market, closes=closes)
    daily = pd.Series(closes).pct_change().dropna()
    expected_vol = float(daily.tail(252).std()) * np.sqrt(252) * 100
    assert row["vol"] == pytest.approx(round(expected_vol, 1))
    assert row["low_vol_raw"] == pytest.approx(round(100 - expected_vol, 2))


def test_low_vol_raw_is_floored_at_zero_for_wild_names(patch_market):
    """연 변동성 100% 초과여도 음수 점수를 만들지 않는다."""
    whipsaw = [100.0 if i % 2 == 0 else 110.0 for i in range(FULL_YEAR_BARS)]
    row = _single(patch_market, closes=whipsaw)
    assert row["vol"] > 100
    assert row["low_vol_raw"] == 0


def test_value_raw_delegates_to_shared_blend(patch_market):
    """EP 40 / BP 30 / FCF 30 — factor_formulas 와 같은 값이어야 한다."""
    row = _single(patch_market)
    expected = ff.value_raw(ff.earnings_yield(20.0), ff.book_yield(4.0), 2.0)
    assert row["value_raw"] == pytest.approx(round(expected, 2))
    assert row["value_raw"] == pytest.approx(10.1)   # 5*.4 + 25*.3 + 2*.3


def test_quality_raw_delegates_to_shared_blend(patch_market):
    """ROE 45 / 이익률 35 / 발생액품질 20."""
    row = _single(patch_market)
    expected = ff.quality_raw(25.0, 15.0, ff.accrual_quality(1000.0, 800.0))
    assert row["quality_raw"] == pytest.approx(round(expected, 2))
    assert row["quality_raw"] == pytest.approx(29.0)  # 25*.45 + 15*.35 + 62.5*.2


def test_negative_free_cashflow_drags_both_value_and_quality_down(patch_market):
    """현금 소각은 클리핑되지 않는다 — GAAP 흑자여도 페널티가 실려야 한다."""
    healthy = _single(patch_market, info=CLEAN_INFO)
    burning = _single(patch_market, info={**CLEAN_INFO, "freeCashflow": -1000.0})
    assert burning["value_raw"] < healthy["value_raw"]
    assert burning["quality_raw"] < healthy["quality_raw"]


def test_missing_per_and_pbr_score_zero_not_nan(patch_market):
    """적자·결측 기업의 밸류는 0점이다. NaN 이 새면 랭킹 전체가 오염된다."""
    row = _single(patch_market, info={"trailingPE": -8.0, "priceToBook": None})
    assert row["value_raw"] == pytest.approx(0.0)
    assert not pd.isna(row["composite"])


def test_forward_pe_is_used_when_trailing_is_absent(patch_market):
    row = _single(patch_market, info={"forwardPE": 25.0})
    assert row["per"] == 25.0
    assert row["value_raw"] == pytest.approx(round(ff.earnings_yield(25.0) * 0.40, 2))


# ── 4. 정규화와 합성 ────────────────────────────────────────────────

def test_identical_names_all_score_fifty(patch_market):
    """표준편차가 0이면 z-score 가 정의되지 않는다 — NaN 대신 중립 50점."""
    frames = {tk: _prices(_ramp(100, 150)) for tk in ["AAA", "BBB", "CCC"]}
    patch_market(frames, infos={tk: CLEAN_INFO for tk in frames})
    result = app.calc_factor_scores(list(frames))
    for col in ["momentum", "value", "quality", "low_vol", "composite"]:
        assert result[col].tolist() == pytest.approx([50.0, 50.0, 50.0])


def test_normalized_scores_stay_inside_twenty_to_eighty(patch_market):
    """윈저라이징 + 클리핑 — 극단값 하나가 랭킹을 독점하지 못하게 한다."""
    patch_market({
        "MOON": _prices(_ramp(100, 900)),
        "MILD": _prices(_ramp(100, 130)),
        "FLAT": _prices(_ramp(100, 101)),
    }, infos={tk: CLEAN_INFO for tk in ["MOON", "MILD", "FLAT"]})
    result = app.calc_factor_scores(["MOON", "MILD", "FLAT"])
    assert result["momentum"].between(20, 80).all()


def test_explicit_factor_weights_are_honored(patch_market):
    """momentum 100% 를 주면 합성점수 = 모멘텀 점수, 랭킹도 모멘텀 순."""
    patch_market({
        "STRONG": _prices(_ramp(100, 220)),
        "MILD": _prices(_ramp(100, 130)),
        "WEAK": _prices(_ramp(100, 105)),
    }, infos={tk: CLEAN_INFO for tk in ["STRONG", "MILD", "WEAK"]})
    result = app.calc_factor_scores(
        ["MILD", "WEAK", "STRONG"],
        factor_weights={"momentum": 1.0, "value": 0.0, "quality": 0.0, "low_vol": 0.0},
    )
    assert result["composite"].tolist() == pytest.approx(result["momentum"].tolist())
    assert result["ticker"].tolist() == ["STRONG", "MILD", "WEAK"]


def test_rows_are_sorted_by_composite_and_ranked_from_one(patch_market):
    patch_market({
        "STRONG": _prices(_ramp(100, 220)),
        "MILD": _prices(_ramp(100, 130)),
        "WEAK": _prices(_ramp(100, 105)),
    }, infos={tk: CLEAN_INFO for tk in ["STRONG", "MILD", "WEAK"]})
    result = app.calc_factor_scores(["WEAK", "STRONG", "MILD"])
    assert result["rank"].tolist() == [1, 2, 3]
    assert result["composite"].is_monotonic_decreasing
    assert result.index.tolist() == [0, 1, 2]   # 인덱스도 재설정된다


def test_default_factor_weights_are_pinned(patch_market):
    """IC 파일이 없을 때의 기본 배합: 모멘텀 .35 / 밸류 .25 / 퀄리티 .32 / 저변동 .08.

    P1-B 로 low_vol 을 축소한 상태다 (IC ICIR = -0.199). 이 숫자가 바뀌면
    과거 백테스트·IC 히스토리와 비교가 성립하지 않는다.
    """
    patch_market({
        "AAA": _prices(_ramp(100, 220)),
        "BBB": _prices(_ramp(100, 130)),
    }, infos={"AAA": CLEAN_INFO, "BBB": {**CLEAN_INFO, "trailingPE": 8.0}},
        ic_weights=None)
    result = app.calc_factor_scores(["AAA", "BBB"])
    expected = (result["momentum"] * 0.35 + result["value"] * 0.25
                + result["quality"] * 0.32 + result["low_vol"] * 0.08)
    assert result["composite"].tolist() == pytest.approx(expected.tolist())


def test_ic_weights_blend_half_and_half_with_defaults(patch_market):
    """P2-C: 주간 IC 가중치는 기본값을 덮어쓰지 않고 50:50 으로 섞인다.

    IC 추정치는 표본 잡음이 크다. 통째로 갈아끼우면 한 주의 노이즈가 다음 주
    포트폴리오를 통째로 흔든다.
    """
    ic = {"momentum": 1.0, "value": 0.0, "quality": 0.0, "low_vol": 0.0}
    patch_market({
        "AAA": _prices(_ramp(100, 220)),
        "BBB": _prices(_ramp(100, 130)),
    }, infos={"AAA": CLEAN_INFO, "BBB": {**CLEAN_INFO, "trailingPE": 8.0}},
        ic_weights=ic)
    result = app.calc_factor_scores(["AAA", "BBB"])
    blended = {"momentum": 0.35 * 0.5 + 0.5, "value": 0.25 * 0.5,
               "quality": 0.32 * 0.5, "low_vol": 0.08 * 0.5}
    total = sum(blended.values())
    expected = sum(result[k] * w / total for k, w in blended.items())
    assert result["composite"].tolist() == pytest.approx(expected.tolist())


# ── 5. 견고성 ───────────────────────────────────────────────────────

def test_fundamentals_failure_still_produces_a_row(patch_market):
    """yfinance 재무가 터져도 가격 기반 팩터는 살아 있어야 한다 — 스캔이 멈추면 안 된다."""
    patch_market({"AAA": _prices(_ramp(100, 220))},
                 infos={"AAA": RuntimeError("yfinance down")})
    result = app.calc_factor_scores(["AAA"])
    assert len(result) == 1
    assert result.iloc[0]["per"] is None
    assert result.iloc[0]["roe"] == 0
    assert result.iloc[0]["momentum_raw"] != 0   # 가격 팩터는 정상 계산


def test_krx_falls_back_to_dart_when_yfinance_has_no_roe(patch_market):
    """yfinance 는 KRX 재무를 자주 비운다. DART 폴백이 ROE·이익률을 메운다."""
    patch_market(
        {"005930.KS": _prices(_ramp(100, 150))},
        infos={"005930.KS": {"trailingPE": 20.0, "priceToBook": 4.0}},
        dart={"005930.KS": {"net_income": 30.0, "equity": 200.0, "margin": 12.5}},
    )
    row = app.calc_factor_scores(["005930.KS"]).iloc[0]
    assert row["roe"] == pytest.approx(15.0)             # 30/200*100
    # 발생액 판정 불가 → 중립 50점
    assert row["quality_raw"] == pytest.approx(
        round(ff.quality_raw(15.0, 12.5, ff.ACCRUAL_NEUTRAL), 2))


def test_dart_fallback_does_not_override_present_yfinance_roe(patch_market):
    """yfinance 값이 있으면 DART 로 덮어쓰지 않는다."""
    patch_market(
        {"005930.KS": _prices(_ramp(100, 150))},
        infos={"005930.KS": CLEAN_INFO},
        dart={"005930.KS": {"net_income": 30.0, "equity": 200.0, "margin": 12.5}},
    )
    assert app.calc_factor_scores(["005930.KS"]).iloc[0]["roe"] == pytest.approx(25.0)


# ── 6. 추가 팩터 (extra_factors=True) ───────────────────────────────

def test_extra_factors_add_normalized_columns(patch_market):
    info = {**CLEAN_INFO, "recommendationMean": 2.0, "shortRatio": 3.0}
    patch_market({
        "AAA": _prices(_ramp(100, 220)),
        "BBB": _prices(_ramp(100, 130)),
    }, infos={"AAA": info, "BBB": {**info, "recommendationMean": 4.0}})
    result = app.calc_factor_scores(["AAA", "BBB"], extra_factors=True)
    for col in ["analyst_raw", "short_raw", "ict_raw", "analyst", "short", "ict"]:
        assert col in result.columns


def test_analyst_and_short_ratings_are_inverted(patch_market):
    """추천지표(1=강력매수)와 공매도비율은 낮을수록 좋다 — 점수는 뒤집혀야 한다."""
    info = {**CLEAN_INFO, "recommendationMean": 2.0, "shortRatio": 3.0}
    patch_market({"AAA": _prices(_ramp(100, 220))}, infos={"AAA": info})
    row = app.calc_factor_scores(["AAA"], extra_factors=True).iloc[0]
    assert row["analyst_raw"] == pytest.approx((5 - 2.0) / 4 * 100)   # 75.0
    assert row["short_raw"] == pytest.approx(100 - 3.0 * 8)           # 76.0


def test_extra_factors_shrink_the_four_factor_base_weight(patch_market):
    """추가 팩터가 붙으면 기본 4팩터가 100% 를 다 가져가면 안 된다.

    합성점수는 여전히 0~100 스케일이어야 하며, 추가 팩터 가중치만큼
    기본 팩터의 몫이 줄어든다.
    """
    info = {**CLEAN_INFO, "recommendationMean": 2.0, "shortRatio": 3.0}
    frames = {tk: _prices(_ramp(100, 150)) for tk in ["AAA", "BBB", "CCC"]}
    patch_market(frames, infos={tk: info for tk in frames})
    result = app.calc_factor_scores(list(frames), extra_factors=True)
    # 전 종목이 동일 → 모든 정규화 점수가 50 → 가중치 합이 1이면 합성도 정확히 50
    assert result["composite"].tolist() == pytest.approx([50.0, 50.0, 50.0])
