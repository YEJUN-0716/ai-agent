"""역사적 시나리오 패널 — 기간을 말하는 숫자와 문장이 같은 구간을 가리켜야 한다.

전용 테스트가 0개이던 모듈이다(2·3·4덩어리에서 반복해 나온 자리). 화면은
`period` 옆에 `n_rows` 를 "N거래일" 로 붙이는데, 예전에는 그 수가 워밍업
버퍼까지 포함해서 라벨보다 20여 거래일 컸다.
"""
import pandas as pd

from modules import stress_test as st


def _df(start, end):
    idx = pd.bdate_range(start, end)
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                         "Close": 100.0}, index=idx)


def _fake_backtest(df, **kw):
    return {"전략 수익률": "0.0%"}, pd.DataFrame(index=df.index), pd.DataFrame()


def test_n_rows_counts_only_the_labeled_period():
    sp = st.KNOWN_STRESS_PERIODS["covid_crash_2020"]
    full = _df("2019-06-01", "2020-12-31")

    out = st.replay_historical_scenario(_fake_backtest, full, "covid_crash_2020")

    labeled = _df(sp["start"], sp["end"])
    assert out["n_rows"] == len(labeled)
    assert out["period"] == f"{sp['start']} ~ {sp['end']}"


def test_warmup_buffer_is_still_fed_to_the_backtest():
    """세는 것만 줄인다 — 신호 워밍업용 앞 구간은 여전히 넘긴다."""
    seen = {}

    def spy(df, **kw):
        seen["rows"] = len(df)
        return _fake_backtest(df, **kw)

    st.replay_historical_scenario(spy, _df("2019-06-01", "2020-12-31"),
                                  "covid_crash_2020", buffer_days=30)

    assert seen["rows"] > len(_df("2020-02-01", "2020-04-30"))


def test_unknown_scenario_is_an_error_not_a_crash():
    out = st.replay_historical_scenario(_fake_backtest,
                                        _df("2020-01-01", "2020-12-31"), "없음")
    assert "error" in out
