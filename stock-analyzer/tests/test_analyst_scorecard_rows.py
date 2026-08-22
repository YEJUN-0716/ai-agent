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


def test_measurement_slug_never_reaches_the_screen():
    """사전 등록 측정용 슬러그는 화면에 안 뜬다 — 발행문과 같은 가드.

    백필에 섞인 `quant_pit` 이 501일치 들어 있었고, 21일 지평에서 t=1.92 ·
    적중률 63.4% 로 성적표 표에서 제일 좋아 보이는 줄이었다. 봉인은 발행
    경로에만 걸려 있었다.
    """
    tickers = list("ABCDE")
    prices = _prices(tickers)
    dates = [prices["A"].index[i].strftime("%Y-%m-%d") for i in range(8)]

    assert app._analyst_scorecard_rows(
        _days(dates, tickers, slug="quant_pit"), prices, 5) == []
    assert app._analyst_scorecard_rows(
        _days(dates, tickers, slug="newcomer"), prices, 5) == []


def test_slug_name_tables_do_not_drift():
    """화면 이름표와 발행 이름표는 같은 슬러그를 알아야 한다 — 화면이 발행
    쪽 목록으로 거르므로, 한쪽에만 슬러그를 넣으면 이름 대신 슬러그가 뜬다."""
    from modules import scorecard_message as sm

    assert set(app._ANALYST_SLUG_NAMES) == set(sm.SLUG_NAMES)


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


def test_combined_screen_labels_are_pinned_to_combine_slugs():
    """발행문은 COMBINE_SLUGS 에서 이름을 뽑지만(scorecard_message.combine_note)
    화면 라벨 두 곳은 아직 손으로 적혀 있다 — `_ANALYST_SLUG_NAMES['combined']`
    ('종합(차트+ICT)') 와 render_scalp_scorecard 의 '차트+ICT 판정 적중률'.

    라벨을 뽑아 쓰면 '종합(차트+파동+모멘텀 · ICT+CRT)' 처럼 표 칸에 안 맞아서
    손으로 두되, 구성이 바뀌면 여기서 먼저 터뜨린다.
    """
    from modules import analyst_scorecard as asc

    assert asc.COMBINE_SLUGS == ("chart", "ict"), (
        "COMBINE_SLUGS 가 바뀌었다 — app._ANALYST_SLUG_NAMES['combined'] 와 "
        "render_scalp_scorecard 의 '차트+ICT 판정 적중률' 라벨을 같이 고칠 것.")
