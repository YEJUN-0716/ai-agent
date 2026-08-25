"""price_panel 모듈 테스트. 네트워크를 타지 않고 yfinance를 목으로 대체한다."""
import pandas as pd
import numpy as np
import pytest

from modules import price_panel


def _fake_ohlcv(tickers, n_days=200, start="2025-01-01"):
    """yf.download가 여러 티커에 대해 돌려주는 MultiIndex 컬럼 형태를 흉내낸다."""
    idx = pd.date_range(start, periods=n_days, freq="B")
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], tickers]
    )
    rng = np.random.default_rng(0)
    data = rng.uniform(90, 110, size=(n_days, len(cols)))
    return pd.DataFrame(data, index=idx, columns=cols)


def test_cache_miss_downloads_and_returns_both_dicts(tmp_path, monkeypatch):
    """캐시가 없으면 다운로드하고, 기존 루프와 같은 형태의 두 dict를 반환한다."""
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        return _fake_ohlcv(list(tickers))

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    prices, ohlcv = price_panel.load_panel(
        ["AAPL", "MSFT"],
        start="2025-01-01",
        end="2025-10-01",
        cache_path=str(tmp_path / "p.parquet"),
    )

    assert len(calls) == 1
    assert set(prices) == {"AAPL", "MSFT"}
    assert set(ohlcv) == {"AAPL", "MSFT"}
    assert isinstance(prices["AAPL"], pd.Series)
    assert "Close" in ohlcv["AAPL"].columns
    assert not prices["AAPL"].isna().any()


def test_low_coverage_raises(tmp_path, monkeypatch):
    """확보율이 80% 미만이면 조용히 넘어가지 않고 예외를 던진다."""
    def fake_download(tickers, **kwargs):
        # 10개 요청 중 2개만 응답 → 20%
        return _fake_ohlcv(["AAPL", "MSFT"])

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    with pytest.raises(price_panel.PanelCoverageError):
        price_panel.load_panel(
            [f"T{i}" for i in range(8)] + ["AAPL", "MSFT"],
            start="2025-01-01", end="2025-10-01",
            cache_path=str(tmp_path / "p.parquet"),
        )


def test_coverage_is_recorded(tmp_path, monkeypatch):
    """실패한 티커 목록이 last_coverage()로 조회된다."""
    def fake_download(tickers, **kwargs):
        return _fake_ohlcv(["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"])

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    price_panel.load_panel(
        ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "DEAD"],
        start="2025-01-01", end="2025-10-01",
        cache_path=str(tmp_path / "p.parquet"),
    )
    cov = price_panel.last_coverage()
    assert cov["requested"] == 6
    assert cov["resolved"] == 5
    assert cov["failed"] == ["DEAD"]


def test_cache_hit_skips_download(tmp_path, monkeypatch):
    """같은 요청을 두 번 하면 두 번째는 네트워크를 타지 않는다."""
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        return _fake_ohlcv(list(tickers))

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    args = dict(start="2025-01-01", end="2025-10-01", cache_path=cache)
    price_panel.load_panel(["AAPL", "MSFT"], **args)
    assert len(calls) == 1

    prices, ohlcv = price_panel.load_panel(["AAPL", "MSFT"], **args)
    assert len(calls) == 1, "캐시 히트인데 다운로드가 발생했다"
    assert set(prices) == {"AAPL", "MSFT"}


def test_corrupt_cache_rebuilds(tmp_path, monkeypatch):
    """손상된 캐시 파일은 예외 없이 재구축된다."""
    cache = tmp_path / "p.parquet"
    cache.write_bytes(b"this is not parquet")

    monkeypatch.setattr(
        price_panel.yf, "download",
        lambda tickers, **kw: _fake_ohlcv(list(tickers)),
    )

    prices, _ = price_panel.load_panel(
        ["AAPL", "MSFT"], start="2025-01-01", end="2025-10-01",
        cache_path=str(cache),
    )
    assert set(prices) == {"AAPL", "MSFT"}


def test_new_ticker_downloads_only_that_ticker(tmp_path, monkeypatch):
    """티커를 추가하면 신규 종목만 요청한다."""
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(sorted(tickers))
        return _fake_ohlcv(list(tickers))

    monkeypatch.setattr(price_panel.yf, "download", fake_download)
    args = dict(start="2025-01-01", end="2025-10-01", cache_path=cache)

    price_panel.load_panel(["AAPL", "MSFT"], **args)
    price_panel.load_panel(["AAPL", "MSFT", "NVDA"], **args)

    assert calls[1] == ["NVDA"], f"신규 종목만 받아야 하는데 {calls[1]}"


def test_extended_end_date_refetches(tmp_path, monkeypatch):
    """요청 종료일이 캐시 최종일보다 뒤면 다시 받는다."""
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        # 실제 yfinance처럼 요청된 end까지만 돌려준다.
        # 요청 범위를 넘겨 반환하면 캐시가 과도하게 채워져 테스트가 무의미해진다.
        calls.append(kwargs.get("end"))
        full = _fake_ohlcv(list(tickers), n_days=600)
        return full.loc[full.index <= pd.Timestamp(kwargs["end"])]

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    price_panel.load_panel(["AAPL", "MSFT"], start="2025-01-01",
                           end="2025-06-01", cache_path=cache)
    price_panel.load_panel(["AAPL", "MSFT"], start="2025-01-01",
                           end="2026-06-01", cache_path=cache)

    assert len(calls) == 2, "날짜가 연장됐는데 다운로드가 없었다"


def test_weekend_gap_is_still_a_cache_hit(tmp_path, monkeypatch):
    """
    호출자가 datetime.now()(시각 포함)를 end로 넘기고 직전 거래일이
    금요일이면, 주말 때문에 캐시가 항상 낡은 것으로 오판되어 매번
    재다운로드가 일어났다. 거래 공백은 캐시 미스가 아니다.
    """
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        full = _fake_ohlcv(list(tickers), n_days=600)
        return full.loc[full.index <= pd.Timestamp(kwargs["end"])]

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    price_panel.load_panel(["AAPL", "MSFT"], start="2025-01-01",
                           end="2025-10-03", cache_path=cache)
    assert len(calls) == 1

    # 주말이 지난 월요일 오전에 다시 호출 — 새 거래일 데이터는 아직 없다
    price_panel.load_panel(["AAPL", "MSFT"], start="2025-01-01",
                           end=pd.Timestamp("2025-10-06 09:30"), cache_path=cache)
    assert len(calls) == 1, "주말 공백을 캐시 미스로 오판했다"


# --- 짧은 구간: 성적표 채점이 밟은 지뢰 -------------------------------------
#
# MIN_TRADING_DAYS(80)는 400일짜리 패널에서 신규상장·거래부진 종목을 걸러내려고
# 넣은 값이다. 그런데 성적표 채점기는 구간을 "기록 첫날 - 워밍업"으로 잡는다.
# 기록이 4일치뿐이던 2026-07-28, 요청 구간은 영업일 14일이었고 80일 문턱에
# 276종목이 전원 탈락해 "데이터 확보율 0/276 (0%)"만 남았다. 다운로드는 멀쩡했다.
# 원인이 구간 길이라는 사실이 메시지에 없어 네트워크 장애로 오인됐다.

SHORT_START, SHORT_END = "2026-07-09", "2026-07-28"   # 영업일 14일


def test_short_window_fails_fast_and_blames_the_window(tmp_path, monkeypatch):
    """구간이 문턱보다 짧으면 받아보기 전에, 구간 탓이라고 말하며 멈춘다."""
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        return _fake_ohlcv(list(tickers), n_days=14, start=SHORT_START)

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    with pytest.raises(price_panel.PanelCoverageError) as err:
        price_panel.load_panel(["AAPL", "MSFT"], start=SHORT_START, end=SHORT_END,
                               cache_path=str(tmp_path / "p.parquet"))

    msg = str(err.value)
    assert "구간" in msg, f"구간 탓임을 말하지 않는다: {msg}"
    assert "14" in msg and "80" in msg, f"영업일/문턱 수치가 없다: {msg}"
    assert calls == [], "실패가 확정된 구간인데 276종목을 받으러 갔다"


def test_short_window_works_when_caller_lowers_threshold(tmp_path, monkeypatch):
    """채점은 기록일과 며칠 뒤 종가만 필요하다 — 긴 과거가 없어도 돌아야 한다."""
    monkeypatch.setattr(
        price_panel.yf, "download",
        lambda tickers, **kw: _fake_ohlcv(list(tickers), n_days=14,
                                          start=SHORT_START),
    )

    prices, ohlcv = price_panel.load_panel(
        ["AAPL", "MSFT"], start=SHORT_START, end=SHORT_END,
        cache_path=str(tmp_path / "p.parquet"),
        min_trading_days=price_panel.MIN_TRADING_DAYS_SCORING,
    )

    assert set(prices) == {"AAPL", "MSFT"}
    assert len(prices["AAPL"]) == 14
    assert set(ohlcv) == {"AAPL", "MSFT"}


def test_default_threshold_still_drops_thin_history(tmp_path, monkeypatch):
    """기본 문턱은 80 그대로다 — IC 파이프라인의 품질 보증을 건드리지 않는다."""
    def fake_download(tickers, **kwargs):
        panel = _fake_ohlcv(list(tickers))
        # THIN 은 마지막 30영업일에만 종가가 있다 (신규상장 흉내)
        panel.loc[panel.index[:-30], ("Close", "THIN")] = np.nan
        return panel

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    prices, _ = price_panel.load_panel(
        ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "THIN"],
        start="2025-01-01", end="2025-10-01",
        cache_path=str(tmp_path / "p.parquet"),
    )

    assert price_panel.MIN_TRADING_DAYS == 80
    assert "THIN" not in prices, "기본 문턱이 느슨해졌다"
    assert set(prices) == {"AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"}


def test_missing_includes_ticker_whose_history_is_truncated():
    """캐시에 있어도 이력이 요청 구간을 못 덮으면 다시 받는다.

    짧은 창으로 처음 받힌 티커는 그대로 두면 영원히 잘린 채 남는다 — 실측으로
    캐시의 CSCO·INTC·PFE 가 538봉, 나머지 276종목은 1602봉이었다.
    """
    panel = _fake_ohlcv(["OLD", "SHORT"], n_days=400, start="2024-01-01")
    panel.loc[panel.index[:300], ("Close", "SHORT")] = np.nan

    missing, ext = price_panel._missing_tickers(
        panel, ["OLD", "SHORT"],
        pd.Timestamp("2024-01-01"), panel.index[-1])
    assert not ext                     # 패널 전체 범위는 요청을 덮는다
    assert missing == ["SHORT"]

    # 짧은 창만 필요하면 그 티커도 이미 충분하다 — 헛되이 다시 받지 않는다
    missing, _ = price_panel._missing_tickers(
        panel, ["OLD", "SHORT"], panel.index[-30], panel.index[-1])
    assert missing == []


def test_missing_includes_ticker_whose_column_is_all_nan():
    """열이 통째로 NaN 인 티커도 다시 받는다 — 잘린 티커와 같은 구멍의 다른 쪽이다.

    일괄 다운로드에서 한 티커만 실패하면 yfinance 가 빈 열을 준다. 그것도
    '캐시에 있음'으로 세면 영원히 다시 안 받는다.
    """
    panel = _fake_ohlcv(["OK", "EMPTY"], n_days=400, start="2024-01-01")
    panel[("Close", "EMPTY")] = np.nan

    missing, ext = price_panel._missing_tickers(
        panel, ["OK", "EMPTY"],
        pd.Timestamp("2024-01-01"), panel.index[-1])
    assert not ext
    assert missing == ["EMPTY"]


def test_missing_includes_ticker_whose_last_bar_is_stale():
    """인덱스가 오늘까지 밀려 있어도, 봉이 며칠 전에 끊긴 티커는 다시 받는다.

    `needs_extension` 은 패널 **전체**의 마지막 날짜만 본다. 60종목짜리 측정
    스크립트가 인덱스를 밀어 놓으면 나머지는 '있음'으로 걸러져 낡은 채 돌아온다
    (실측 2026-08-17~21: 293종목 중 60종목만 봉이 있는데 캐시는 "히트"였다).
    """
    panel = _fake_ohlcv(["FRESH", "STALE"], n_days=400, start="2024-01-01")
    panel.loc[panel.index[-10:], ("Close", "STALE")] = np.nan

    missing, ext = price_panel._missing_tickers(
        panel, ["FRESH", "STALE"],
        pd.Timestamp("2024-01-01"), panel.index[-1])
    assert not ext                     # 패널 전체 범위는 요청을 덮는다
    assert missing == ["STALE"]

    # 옛 구간을 요청하면 그 티커도 낡지 않았다 — 헛되이 다시 받지 않는다
    missing, _ = price_panel._missing_tickers(
        panel, ["FRESH", "STALE"],
        pd.Timestamp("2024-01-01"), panel.index[-30])
    assert missing == []
