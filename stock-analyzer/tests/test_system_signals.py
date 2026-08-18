"""
app.generate_system_signals 동작 고정 테스트.

signal_worker.py 가 `import app as core` 로 이 함수를 호출해 텔레그램 매수
알림과 signal_log.json 을 만든다. app.py 는 7,600줄 모놀리스라 UI 쪽을
건드리다 이 함수의 판정 규칙이나 반환 스키마가 깨져도 알아챌 방법이 없었다.
여기서 규칙을 못 박아 두면, 코어 로직을 modules/ 로 추출할 때 회귀를 CI가 잡는다.

네트워크를 타지 않는다 — app.download_stock 을 합성 가격 프레임으로 대체한다.
가격 시나리오는 monotonic 하지 않게 진동을 섞었다. 단조 상승/하락은 RSI 가
0 또는 100 으로 포화돼 실제 시장 구간을 대표하지 못하기 때문이다.

각 시나리오는 "이 프레임이 의도한 국면이 맞는지"를 app 자신의 지표 함수로
먼저 검증한 뒤(_assert_regime) 판정 결과를 확인한다. 그래야 픽스처가 조용히
다른 국면으로 흘러가면서 테스트가 헛돌지 않는다.
"""
import numpy as np
import pandas as pd
import pytest

import app

BARS = 120


def _frame(closes, last_volume_ratio: float = 1.0) -> pd.DataFrame:
    """합성 OHLCV. last_volume_ratio 로 마지막 봉의 거래량만 조절한다."""
    close = pd.Series(closes, dtype=float)
    volume = pd.Series([1_000_000.0] * len(close))
    volume.iloc[-1] = 1_000_000.0 * last_volume_ratio
    return pd.DataFrame({
        "Open": close,
        "High": close * 1.01,
        "Low": close * 0.99,
        "Close": close,
        "Volume": volume,
    })


_i = np.arange(BARS)
# 완만한 상승 + 진동 — RSI 가 중간대에 머무는 정상 상승추세
_UPTREND = np.linspace(100, 150, BARS) + 2.5 * np.sin(_i / 1.4)
_DOWNTREND = np.linspace(150, 100, BARS) + 2.5 * np.sin(_i / 1.4)

# RSI ≈ 66, cp > ma20 > ma60, 과매수 아님
UPTREND = list(_UPTREND)
# 막판 급등 → RSI ≈ 90, 강추세 임계(80)마저 넘김
OVERBOUGHT = list(_UPTREND[:112]) + list(_UPTREND[111] + np.linspace(3, 30, 8))
# RSI ≈ 30, cp < ma20 < ma60
DOWNTREND = list(_DOWNTREND)
# 상승 뒤 급락 → RSI ≈ 28 (과매도), 추세 정렬은 무너진 상태
OVERSOLD = list(_UPTREND[:112]) + list(_UPTREND[111] - np.linspace(1, 14, 8))


def _assert_regime(closes, *, trend_up=None, trend_dn=None, overbought=None, oversold=None):
    """픽스처가 의도한 국면에 실제로 들어와 있는지 app 지표로 확인."""
    c = pd.Series(closes, dtype=float)
    df = _frame(closes)
    rsi = float(app.calc_rsi(c).iloc[-1])
    cp = float(c.iloc[-1])
    ma20 = float(c.rolling(20).mean().iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    adx_s, pdi_s, ndi_s = app.calc_adx(df["High"], df["Low"], df["Close"])
    strong_up = (cp > ma20 > ma60 and float(adx_s.iloc[-1]) > 25
                 and float(pdi_s.iloc[-1]) > float(ndi_s.iloc[-1]))
    threshold = 80 if strong_up else 70
    if trend_up is not None:
        assert (cp > ma20 > ma60) is trend_up
    if trend_dn is not None:
        assert (cp < ma20 < ma60) is trend_dn
    if overbought is not None:
        assert (rsi > threshold) is overbought, f"RSI {rsi:.1f} vs 임계 {threshold}"
    if oversold is not None:
        assert (rsi < 35) is oversold, f"RSI {rsi:.1f}"


@pytest.fixture
def patch_prices(monkeypatch):
    """티커별 가격 프레임을 주입한다. 등록되지 않은 티커는 빈 프레임(=다운로드 실패)."""
    def _install(frames: dict):
        def fake_download(ticker, start, end, interval="1d"):
            return frames.get(ticker, pd.DataFrame())
        monkeypatch.setattr(app, "download_stock", fake_download)
    return _install


def _factor_df(rows):
    """[(ticker, composite), ...] → composite 내림차순 팩터 테이블."""
    df = pd.DataFrame(rows, columns=["ticker", "composite"])
    return df.sort_values("composite", ascending=False).reset_index(drop=True)


def _by_ticker(actions):
    return {a["ticker"]: a for a in actions}


# ── 반환 스키마 ─────────────────────────────────────────────────────

def test_returns_actions_and_rebalance_info(patch_prices):
    patch_prices({"AAA": _frame(UPTREND)})
    actions, rebal = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    assert isinstance(actions, list) and len(actions) == 1
    assert set(rebal) == {"next_rebal", "buy_count", "sell_count", "hold_count"}
    # signal_worker.py 가 이 키들을 그대로 읽어 텔레그램 메시지를 만든다.
    assert set(actions[0]) == {
        "ticker", "action", "weight", "price", "alloc", "qty", "reason", "priority", "mom",
    }


# ── 매수 판정 ───────────────────────────────────────────────────────

def test_top_factor_uptrend_with_volume_is_high_priority_buy(patch_prices):
    _assert_regime(UPTREND, trend_up=True, overbought=False)
    patch_prices({"AAA": _frame(UPTREND)})

    actions, rebal = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    action = actions[0]
    assert "매수" in action["action"]
    assert action["priority"] == "HIGH"
    assert action["weight"] == "100.0%"      # top_n=1 → 비중 1/1
    assert rebal["buy_count"] == 1


def test_thin_volume_downgrades_to_conditional_buy_at_70pct(patch_prices):
    """거래량이 20일 평균의 80% 미만이면 조건부 매수 + 비중 70%로 축소."""
    patch_prices({"AAA": _frame(UPTREND, last_volume_ratio=0.3)})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    action = actions[0]
    assert "조건부 매수" in action["action"]
    assert action["priority"] == "NORMAL"
    assert action["weight"] == "70.0%"
    assert "거래량 부족" in action["reason"]


def test_volume_at_threshold_still_confirms(patch_prices):
    """정확히 80%는 확인으로 친다 (>= 비교) — 경계값 고정."""
    patch_prices({"AAA": _frame(UPTREND, last_volume_ratio=0.8)})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    assert actions[0]["action"] == "🟢 매수"


def test_overbought_blocks_buy_even_for_top_factor(patch_prices):
    """팩터 최상위여도 과매수면 매수하지 않고 대기로 내린다."""
    _assert_regime(OVERBOUGHT, trend_up=True, overbought=True)
    patch_prices({"AAA": _frame(OVERBOUGHT)})

    actions, rebal = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 95.0)]), top_n=1, capital=10_000)

    assert actions[0]["action"] == "🟡 대기"
    assert actions[0]["priority"] == "LOW"
    assert rebal["buy_count"] == 0


def test_oversold_rebound_is_high_priority_buy(patch_prices):
    """추세 정렬이 무너져도 과매도 반등은 우선순위 높은 매수.

    팩터 최상위(75+)가 아닌 종목이어야 이 분기를 탄다 — 최상위는 아래
    test_top_factor_branch_takes_precedence_over_rsi_reason 참조.
    """
    _assert_regime(OVERSOLD, trend_up=False, oversold=True, overbought=False)
    patch_prices({"AAA": _frame(OVERSOLD)})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 60.0)]), top_n=1, capital=10_000)

    assert "매수" in actions[0]["action"]
    assert actions[0]["priority"] == "HIGH"
    assert "과매도 반등" in actions[0]["reason"]


def test_top_factor_branch_takes_precedence_over_rsi_reason(patch_prices):
    """분기 순서 고정: 팩터 최상위(75+) 판정이 과매도 반등 판정보다 먼저다.

    같은 과매도 국면이어도 composite 가 75 이상이면 사유가 '최상위'로 찍힌다.
    분기 순서를 바꾸면 텔레그램 알림의 사유 문구가 통째로 달라지므로 못 박아 둔다.
    """
    patch_prices({"AAA": _frame(OVERSOLD)})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    assert actions[0]["action"] == "🟢 매수"
    assert "최상위" in actions[0]["reason"]
    assert "과매도 반등" not in actions[0]["reason"]


# ── 매도·비중축소 판정 ──────────────────────────────────────────────

def test_bottom_third_is_marked_sell_with_zero_weight(patch_prices):
    """팩터 하위 1/3은 매도 후보 — 비중 0."""
    tickers = ["AAA", "BBB", "CCC"]
    patch_prices({t: _frame(UPTREND) for t in tickers})

    actions, rebal = app.generate_system_signals(
        tickers,
        factor_df=_factor_df([("AAA", 90.0), ("BBB", 60.0), ("CCC", 20.0)]),
        top_n=1, capital=10_000)

    by = _by_ticker(actions)
    assert by["CCC"]["action"] == "🔴 매도"
    assert by["CCC"]["weight"] == "0.0%"
    assert by["CCC"]["priority"] == "HIGH"
    assert rebal["sell_count"] >= 1


def test_downtrend_outside_candidates_is_trimmed_to_half(patch_prices):
    """매수·매도 후보가 아닌 하락추세 종목은 비중 절반으로 축소."""
    _assert_regime(DOWNTREND, trend_dn=True)
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    frames = {t: _frame(UPTREND) for t in tickers}
    frames["CCC"] = _frame(DOWNTREND)
    patch_prices(frames)

    actions, _ = app.generate_system_signals(
        tickers,
        factor_df=_factor_df([("AAA", 90.0), ("BBB", 80.0), ("CCC", 55.0), ("DDD", 10.0)]),
        top_n=2, capital=10_000,
        weights={"AAA": 0.5, "BBB": 0.3, "CCC": 0.2, "DDD": 0.0})

    by = _by_ticker(actions)
    assert by["CCC"]["action"] == "🟠 비중축소"
    assert by["CCC"]["weight"] == "10.0%"   # 0.2 → 절반


# ── 후보 선정 규칙 ──────────────────────────────────────────────────

def test_top_n_defines_buy_candidates(patch_prices):
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    patch_prices({t: _frame(UPTREND) for t in tickers})
    scores = [("AAA", 95.0), ("BBB", 90.0), ("CCC", 85.0),
              ("DDD", 60.0), ("EEE", 40.0), ("FFF", 20.0)]

    actions, _ = app.generate_system_signals(
        tickers, factor_df=_factor_df(scores), top_n=2, capital=10_000)

    by = _by_ticker(actions)
    # 상위 2종목만 매수, 하위 1/3(2종목)은 매도
    assert "매수" in by["AAA"]["action"] and "매수" in by["BBB"]["action"]
    assert by["EEE"]["action"] == "🔴 매도" and by["FFF"]["action"] == "🔴 매도"


def test_explicit_weights_override_equal_split(patch_prices):
    patch_prices({"AAA": _frame(UPTREND), "BBB": _frame(UPTREND)})

    actions, _ = app.generate_system_signals(
        ["AAA", "BBB"], factor_df=_factor_df([("AAA", 90.0), ("BBB", 85.0)]),
        top_n=2, capital=10_000, weights={"AAA": 0.75, "BBB": 0.25})

    by = _by_ticker(actions)
    assert by["AAA"]["weight"] == "75.0%"
    assert by["AAA"]["alloc"] == "$7,500"


def test_falls_back_to_first_n_tickers_without_factor_df(patch_prices):
    """팩터 테이블이 없으면 입력 순서 앞 top_n개를 매수 후보로 쓴다."""
    patch_prices({t: _frame(UPTREND) for t in ["AAA", "BBB", "CCC"]})

    actions, _ = app.generate_system_signals(
        ["AAA", "BBB", "CCC"], factor_df=None, top_n=1, capital=10_000)

    by = _by_ticker(actions)
    # 매도 후보 리스트가 비므로 어떤 종목도 매도로 찍히지 않는다.
    assert all("매도" not in a["action"] for a in actions)
    assert by["AAA"]["weight"] == "100.0%"     # 후보 → 비중 1/top_n
    assert by["BBB"]["weight"] == "0.0%"       # 비후보 → 비중 0


def test_empty_factor_df_behaves_like_none(patch_prices):
    patch_prices({"AAA": _frame(UPTREND)})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=pd.DataFrame(), top_n=1, capital=10_000)

    assert len(actions) == 1


# ── 통화·수량 포맷 ──────────────────────────────────────────────────

def test_us_ticker_uses_dollar_and_fractional_shares(patch_prices):
    patch_prices({"AAA": _frame(UPTREND)})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    assert actions[0]["price"].startswith("$")
    assert actions[0]["alloc"] == "$10,000"
    assert "." in actions[0]["qty"]      # 소수점 주식 허용


def test_krx_ticker_uses_won_and_whole_shares(patch_prices):
    """KRX(.KS/.KQ)는 원화 표기 + 정수 주 — 소수점 주문이 불가능하다."""
    patch_prices({"005930.KS": _frame(UPTREND)})

    actions, _ = app.generate_system_signals(
        ["005930.KS"], factor_df=_factor_df([("005930.KS", 90.0)]),
        top_n=1, capital=10_000)

    assert actions[0]["price"].startswith("₩")
    assert actions[0]["alloc"].startswith("₩")
    assert "." not in actions[0]["qty"]


# ── 견고성 ──────────────────────────────────────────────────────────

def test_unavailable_ticker_is_skipped_without_failing_the_batch(patch_prices):
    """한 종목 다운로드가 실패해도 나머지 스캔은 계속돼야 한다.

    signal_worker 는 수백 종목을 한 번에 돌린다 — 하나 때문에 전체 알림이
    죽으면 안 된다.
    """
    patch_prices({"AAA": _frame(UPTREND)})   # BBB 는 등록하지 않음 → 빈 프레임

    actions, _ = app.generate_system_signals(
        ["AAA", "BBB"], factor_df=_factor_df([("AAA", 90.0), ("BBB", 80.0)]),
        top_n=2, capital=10_000)

    assert [a["ticker"] for a in actions] == ["AAA"]


def test_short_history_is_skipped(patch_prices):
    """30봉 미만은 지표 신뢰도가 없어 건너뛴다."""
    patch_prices({"AAA": _frame(UPTREND[:25])})

    actions, _ = app.generate_system_signals(
        ["AAA"], factor_df=_factor_df([("AAA", 90.0)]), top_n=1, capital=10_000)

    assert actions == []


def test_counts_are_consistent_with_actions(patch_prices):
    """rebal_info 집계가 actions 와 어긋나면 텔레그램 요약이 거짓말을 한다."""
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    patch_prices({t: _frame(UPTREND) for t in tickers})
    scores = [(t, s) for t, s in zip(tickers, [95.0, 90.0, 85.0, 60.0, 40.0, 20.0])]

    actions, rebal = app.generate_system_signals(
        tickers, factor_df=_factor_df(scores), top_n=2, capital=10_000)

    assert rebal["buy_count"] == sum(1 for a in actions if "매수" in a["action"])
    assert rebal["sell_count"] == sum(
        1 for a in actions if "매도" in a["action"] or "축소" in a["action"])
    assert rebal["hold_count"] == sum(
        1 for a in actions if "관망" in a["action"] or "대기" in a["action"])
