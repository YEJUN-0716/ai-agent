"""data_integrity 테스트. 순수 계산이라 네트워크를 타지 않는다."""
import numpy as np
import pandas as pd

from modules.data_integrity import check_ohlc_sanity


def _bars(n=50):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    return pd.DataFrame({"Open": close, "High": close, "Low": close,
                         "Close": close, "Volume": 1_000_000.0}, index=idx)


def test_float_noise_is_not_a_data_error():
    """수정주가의 마지막 비트 오차를 데이터 오류로 찍지 않는다.

    실측(279종목 6년): OHLC 위반 14건이 전부 High == Close 인데 High 가 한 비트
    낮은 경우였고, 폭은 종가 대비 1.1e-16 이었다. 화면이 14종목을 근거 없이
    '문제 있음'으로 표시하고 있었다.
    """
    df = _bars()
    df.iloc[10, df.columns.get_loc("High")] = np.nextafter(df["Close"].iloc[10], 0)
    df.iloc[20, df.columns.get_loc("Low")] = np.nextafter(df["Close"].iloc[20], np.inf)
    assert check_ohlc_sanity(df)["is_clean"]


def test_real_broken_bar_is_still_caught():
    """진짜 깨진 봉은 문턱보다 몇 자릿수 크게 어긋나므로 그대로 잡힌다."""
    df = _bars()
    df.iloc[10, df.columns.get_loc("High")] = df["Low"].iloc[10] * 0.99
    result = check_ohlc_sanity(df)
    assert not result["is_clean"]
    assert "High" in result["issues"][0]
