"""애널리스트 채점 — IC 계산과 겹침 보정 표준오차 고정.

여기서 지키는 핵심은 하나다: **겹치는 관측을 독립으로 세지 않는다.**
매일 기록하고 21일 뒤 수익률로 채점하면 관측이 20일씩 겹치는데, 그걸
독립 표본으로 세면 "n=250, 유의함"이라는 잘못된 결론이 나온다.
"""
import numpy as np
import pytest

from modules import analyst_scorecard as sc


# ── Newey–West 표준오차 ──────────────────────────────────────────────

def test_independent_samples_match_plain_se():
    """겹침이 없으면(lag=0) 통상 표준오차와 정확히 같아야 한다."""
    rng = np.random.default_rng(0)
    vals = rng.normal(0.02, 0.15, 200).tolist()

    plain = float(np.std(vals, ddof=1) / np.sqrt(len(vals)))
    assert sc.newey_west_se(vals, lag=0) == pytest.approx(plain, rel=1e-9)


def test_overlap_inflates_se():
    """양의 자기상관이 있으면 표준오차가 커진다 — 표본을 과대평가하지 않는다."""
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.1, 300)
    overlapped = np.convolve(base, np.ones(20) / 20, mode="same").tolist()

    plain = float(np.std(overlapped, ddof=1) / np.sqrt(len(overlapped)))
    assert sc.newey_west_se(overlapped, lag=19) > plain * 1.5


def test_se_is_never_negative():
    """음의 자기상관에서 분산 추정이 음수가 될 수 있다 — 허수가 되면 안 된다."""
    vals = [0.1, -0.1] * 30
    assert sc.newey_west_se(vals, lag=19) >= 0.0


def test_too_few_values_is_nan():
    assert np.isnan(sc.newey_west_se([0.1], lag=0))


# ── 채점 ─────────────────────────────────────────────────────────────

def _day(date, scores):
    return {"date": date, "scores": scores}


def test_perfect_ranking_gives_ic_one():
    days = [_day("2026-01-05", {
        "A": {"chart": 90.0}, "B": {"chart": 70.0}, "C": {"chart": 50.0},
        "D": {"chart": 30.0}, "E": {"chart": 10.0}})]
    fwd = {"2026-01-05": {"A": 5.0, "B": 2.0, "C": 0.0, "D": -2.0, "E": -5.0}}

    got = sc.score_analysts(days, fwd, horizon=5)
    assert got["chart"]["mean_ic"] == pytest.approx(1.0)
    assert got["chart"]["n"] == 1
    assert got["chart"]["hit_rate"] == 100.0


def test_missing_slug_is_excluded_not_zero_filled():
    """점수가 없는 종목은 그 애널리스트 계산에서 빠진다 — 중립값으로 안 채운다."""
    days = [_day("2026-01-05", {
        "A": {"chart": 90.0, "ict": 10.0}, "B": {"chart": 50.0},
        "C": {"chart": 10.0}, "D": {"chart": 70.0}, "E": {"chart": 30.0}})]
    fwd = {"2026-01-05": {"A": 5.0, "B": 2.0, "C": 0.0, "D": -2.0, "E": -5.0}}

    got = sc.score_analysts(days, fwd, horizon=5)
    # chart 는 5종목 다 있으니 계산된다
    assert "chart" in got
    # ict 는 유효 종목이 1개뿐 → 아예 나오지 않는다 (0 으로 채우지 않는다)
    assert "ict" not in got


def test_thin_day_is_skipped():
    """유효 종목 5개 미만인 날은 버린다."""
    days = [_day("2026-01-05", {"A": {"chart": 90.0}, "B": {"chart": 10.0}})]
    fwd = {"2026-01-05": {"A": 5.0, "B": -5.0}}

    assert sc.score_analysts(days, fwd, horizon=5) == {}


def test_day_without_returns_is_skipped():
    days = [_day("2026-01-05", {k: {"chart": 50.0 + i}
                                for i, k in enumerate("ABCDE")})]
    assert sc.score_analysts(days, {}, horizon=5) == {}


def test_flat_scores_produce_no_ic():
    """점수가 전부 같으면 순위가 정의되지 않는다."""
    days = [_day("2026-01-05", {k: {"chart": 50.0} for k in "ABCDE"})]
    fwd = {"2026-01-05": {k: float(i) for i, k in enumerate("ABCDE")}}

    assert sc.score_analysts(days, fwd, horizon=5) == {}


def test_effective_n_is_smaller_than_apparent_n_when_overlapping():
    """겹치는 창에서는 유효 표본이 겉보기 표본보다 작아야 한다."""
    rng = np.random.default_rng(2)
    days, fwd = [], {}
    # 겹침을 흉내내려면 IC 계열에 자기상관이 있어야 한다.
    # 같은 잠재 신호를 며칠씩 공유하도록 만든다.
    latent = np.repeat(rng.normal(0, 1.0, 12), 5)
    for i in range(60):
        d = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        scores, rets = {}, {}
        for j, t in enumerate("ABCDE"):
            scores[t] = {"chart": float(j)}
            rets[t] = float(j * latent[i] + rng.normal(0, 0.1))
        days.append(_day(d, scores))
        fwd[d] = rets

    got = sc.score_analysts(days, fwd, horizon=21)
    assert got["chart"]["n"] == 60
    assert got["chart"]["effective_n"] < 60


def test_effective_n_never_exceeds_apparent_n():
    rng = np.random.default_rng(3)
    days, fwd = [], {}
    for i in range(40):
        d = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        days.append(_day(d, {t: {"chart": float(rng.normal(50, 10))}
                             for t in "ABCDE"}))
        fwd[d] = {t: float(rng.normal(0, 3)) for t in "ABCDE"}

    got = sc.score_analysts(days, fwd, horizon=5)
    assert got["chart"]["effective_n"] <= got["chart"]["n"]


def test_horizons_are_the_three_agreed_windows():
    assert sc.HORIZONS == (5, 21, 63)


# ── 선행수익률 산출 ──────────────────────────────────────────────────

def _rising_prices(n=30):
    import pandas as pd

    idx = pd.bdate_range("2026-01-01", periods=n)
    return {"A": pd.Series([100.0 + i for i in range(n)], index=idx)}


def test_forward_return_is_horizon_bars_ahead():
    prices = _rising_prices()
    dates = [prices["A"].index[0].strftime("%Y-%m-%d")]

    got = sc.build_forward_returns(prices, dates, horizon=5)

    # 100 → 105 = +5%
    assert got[dates[0]]["A"] == pytest.approx(5.0)


def test_dates_without_future_are_dropped():
    """미래가 아직 안 온 날짜는 마지막 가격으로 때우지 않고 뺀다."""
    prices = _rising_prices(n=30)
    last = prices["A"].index[-1].strftime("%Y-%m-%d")

    assert sc.build_forward_returns(prices, [last], horizon=5) == {}


def test_partial_future_drops_only_that_ticker():
    import pandas as pd

    idx = pd.bdate_range("2026-01-01", periods=30)
    prices = {
        "LONG": pd.Series([100.0 + i for i in range(30)], index=idx),
        "SHORT": pd.Series([100.0 + i for i in range(10)], index=idx[:10]),
    }
    date = idx[7].strftime("%Y-%m-%d")

    got = sc.build_forward_returns(prices, [date], horizon=5)

    assert "LONG" in got[date]
    assert "SHORT" not in got[date]


def test_scorecard_runs_end_to_end_on_built_returns():
    """기록 → 선행수익률 → 채점이 이어 붙는지."""
    import pandas as pd

    idx = pd.bdate_range("2026-01-01", periods=40)
    prices, days = {}, []
    for rank, ticker in enumerate("ABCDE"):
        # 순위가 높을수록 많이 오른다 → 점수와 수익률이 같은 방향
        prices[ticker] = pd.Series(
            [100.0 * (1 + 0.001 * rank) ** i for i in range(40)], index=idx)

    for i in range(5):
        date = idx[i].strftime("%Y-%m-%d")
        days.append(_day(date, {t: {"chart": float(r)}
                                for r, t in enumerate("ABCDE")}))

    fwd = sc.build_forward_returns(prices, [d["date"] for d in days], horizon=5)
    got = sc.score_analysts(days, fwd, horizon=5)

    assert got["chart"]["mean_ic"] == pytest.approx(1.0)
    assert got["chart"]["n"] == 5
