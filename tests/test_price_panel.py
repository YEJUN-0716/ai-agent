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
