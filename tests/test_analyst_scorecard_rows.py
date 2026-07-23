"""성적표 표시용 행 생성 — 판정 게이트가 새지 않는지 고정.

표시 자체(Streamlit)는 테스트하지 않는다. 여기서 지키는 것은 **언제 '판정
가능'이라고 말하는가** 다. 표본이 모자란데 판정 가능이라고 띄우면 보스가
근거 없이 비중을 옮기게 된다.
"""
import pandas as pd
import pytest

import app


def _prices(tickers, n=60, sep=0.002):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return {t: pd.Series([100.0 * (1 + sep * rank) ** i for i in range(n)],
                         index=idx)
            for rank, t in enumerate(tickers)}


def _days(dates, tickers, slug="chart"):
    return [{"date": d, "scores": {t: {slug: float(rank)}
                                   for rank, t in enumerate(tickers)}}
            for d in dates]


def test_returns_empty_when_no_future_yet():
    """예측 구간이 안 지났으면 채점할 게 없다."""
    tickers = list("ABCDE")
    prices = _prices(tickers, n=10)
    last = prices["A"].index[-1].strftime("%Y-%m-%d")

    assert app._analyst_scorecard_rows(_days([last], tickers), prices, 5) == []


def test_row_carries_effective_n_and_apparent_n():
    tickers = list("ABCDE")
    prices = _prices(tickers)
    dates = [prices["A"].index[i].strftime("%Y-%m-%d") for i in range(10)]

    rows = app._analyst_scorecard_rows(_days(dates, tickers), prices, 5)

    assert len(rows) == 1
    row = rows[0]
    assert row["애널리스트"] == "차트+파동+모멘텀"
    assert row["표본(겉보기)"] == 10
    assert row["유효표본"] <= row["표본(겉보기)"]


def test_small_sample_is_never_decidable():
    """표본이 적으면 IC 가 완벽해도 '판정 가능'이 뜨면 안 된다."""
    tickers = list("ABCDE")
    prices = _prices(tickers)
    dates = [prices["A"].index[i].strftime("%Y-%m-%d") for i in range(10)]

    rows = app._analyst_scorecard_rows(_days(dates, tickers), prices, 5)

    # 단조 상승 종목이라 IC 는 +1.0 이지만 표본이 10개뿐이다
    assert rows[0]["IC"] == pytest.approx(1.0)
    assert rows[0]["유효표본"] < app.ANALYST_SCORECARD_MIN_EFFECTIVE_N
    assert rows[0]["판정"] == "아직 불가"


def test_unknown_slug_falls_back_to_raw_name():
    tickers = list("ABCDE")
    prices = _prices(tickers)
    dates = [prices["A"].index[i].strftime("%Y-%m-%d") for i in range(8)]

    rows = app._analyst_scorecard_rows(
        _days(dates, tickers, slug="newcomer"), prices, 5)

    assert rows[0]["애널리스트"] == "newcomer"


def test_multiple_analysts_each_get_a_row():
    tickers = list("ABCDE")
    prices = _prices(tickers)
    dates = [prices["A"].index[i].strftime("%Y-%m-%d") for i in range(8)]

    days = []
    for d in dates:
        days.append({"date": d, "scores": {
            t: {"chart": float(rank), "ict": float(-rank)}
            for rank, t in enumerate(tickers)}})

    rows = app._analyst_scorecard_rows(days, prices, 5)

    assert {r["애널리스트"] for r in rows} == {"차트+파동+모멘텀", "ICT+CRT"}
    # ict 는 점수를 뒤집었으므로 IC 부호가 반대여야 한다
    by_name = {r["애널리스트"]: r for r in rows}
    assert by_name["차트+파동+모멘텀"]["IC"] > 0
    assert by_name["ICT+CRT"]["IC"] < 0
