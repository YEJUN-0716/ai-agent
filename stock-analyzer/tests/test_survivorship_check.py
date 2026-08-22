"""survivorship_check 테스트. 순수 계산이라 네트워크를 안 탄다."""
from modules import survivorship_check as sc


def test_known_failures_in_period_includes_boundaries():
    """구간 양 끝은 포함이다."""
    got = sc.known_failures_in_period("2008-09-15", "2008-09-25")
    assert {f["ticker"] for f in got} == {"LEH", "WM", "AIG"}

    assert sc.known_failures_in_period("2019-01-01", "2019-12-31") == []
    assert sc.known_failures_in_period("어제", "오늘") == [], "형식이 틀리면 빈 목록"


def test_annotate_does_not_mutate_input():
    """원본 IC 결과를 건드리지 않고 메타데이터만 붙인다."""
    ic = {"mean_ic": 0.02}
    got = sc.annotate_ic_result(ic, universe_size=276,
                                start_date="2008-01-01", end_date="2008-12-31")
    assert "survivorship_bias" not in ic
    assert got["mean_ic"] == 0.02
    assert got["survivorship_bias"]["known_failures_count"] == 4


def test_tickers_are_real_symbols():
    """티커 오기 방지 — WorldCom 은 WCOM 이다(WCG 는 WellCare)."""
    tickers = {f["ticker"] for f in sc.KNOWN_DELISTED_LARGE_CAPS}
    assert "WCOM" in tickers and "WCG" not in tickers
    assert len(tickers) == len(sc.KNOWN_DELISTED_LARGE_CAPS), "중복 티커"
