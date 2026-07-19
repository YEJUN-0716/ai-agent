"""price_panel 모듈 테스트. 네트워크를 타지 않고 yfinance를 목으로 대체한다."""
import pandas as pd
import numpy as np
import pytest

from modules import price_panel


def _fake_ohlcv(tickers, n_days=200):
    """yf.download가 여러 티커에 대해 돌려주는 MultiIndex 컬럼 형태를 흉내낸다."""
    idx = pd.date_range("2025-01-01", periods=n_days, freq="B")
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
