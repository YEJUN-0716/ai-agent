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


def test_horizons_include_the_non_overlapping_one_day_window():
    """1일이 맨 앞에 있어야 한다 — lag=0 이라 유효표본이 겉보기 n 과 같고,
    표본이 가장 먼저 차는 지평이다. 나머지는 겹침 때문에 훨씬 늦다."""
    assert sc.HORIZONS == (1, 5, 21, 63)
    assert sc.HORIZONS[0] == 1


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


# ── 국면 판정은 날짜별로도 같은 자를 쓴다 ────────────────────────────
#
# 백필이 과거 날짜의 국면을 매기려면 순수 함수가 필요하다. 문턱을 두 곳에
# 적으면 백필과 실기록의 국면 라벨이 다른 자로 찍힌다.

def _closes(last, ma_level=100.0, n=260):
    import pandas as pd

    idx = pd.bdate_range("2025-01-01", periods=n)
    vals = [ma_level] * (n - 1) + [last]
    return pd.Series(vals, index=idx)


def test_regime_of_reads_the_end_of_the_series():
    import app as core

    assert core.regime_of(_closes(130.0))[0] == "bull"
    assert core.regime_of(_closes(70.0))[0] == "bear"
    assert core.regime_of(_closes(100.0))[0] == "neutral"


def test_regime_of_needs_two_hundred_bars():
    """MA200 을 못 채우면 판정하지 않는다 — 짧은 계열에 억지로 값을 내면
    백필 첫 몇 달의 국면 라벨이 통째로 거짓이 된다."""
    import app as core

    assert core.regime_of(_closes(130.0, n=150)) == ("neutral", 0.0)


# ── 못 재면 못 잰다고 한다 ───────────────────────────────────────────
#
# 표본이 lag 의 두 배에 못 미치면 Newey–West 가 겹침을 못 잰다. 그때 추정
# 표준오차가 통상 표준오차보다 작게 나오면 effective_n 이 n 으로 클램프돼
# "유효표본 = 전체" 가 된다 — 추정기가 무너진 자리에서 가장 확신에 찬 숫자가
# 나온다. 실제로 국면별 분해에서 bear 20일 × 63일 지평이 t -20.6 을 냈다.

def _days_with_ic(n_days, n_tickers=8):
    """단면 상관이 계산되는 최소한의 기록 n일치."""
    days = []
    for i in range(n_days):
        scores = {f"T{j}": {"combined": float((j * 7 + i * 3) % 100)}
                  for j in range(n_tickers)}
        days.append({"date": f"2026-01-{i + 1:02d}", "scores": scores})
    return days


def _returns_for(days, n_tickers=8):
    return {d["date"]: {f"T{j}": float((j * 11 + 5) % 100) - 50
                        for j in range(n_tickers)}
            for d in days}


def test_small_sample_against_long_horizon_refuses_to_judge():
    days = _days_with_ic(20)
    stats = sc.score_analysts(days, _returns_for(days), 63)

    got = stats["combined"]
    assert got["n"] == 20
    assert got["t_stat"] is None
    assert got["se"] is None
    assert got["effective_n"] == 0.0        # 판정선(30)을 절대 못 넘는다
    assert got["mean_ic"] is not None       # IC 자체는 그대로 보여준다


def test_long_enough_sample_still_gets_a_t_stat():
    """가드가 정상 경로를 막지 않는다 — n > 2*lag 면 그대로 잰다."""
    days = _days_with_ic(30)
    stats = sc.score_analysts(days, _returns_for(days), 5)

    assert stats["combined"]["t_stat"] is not None
    assert stats["combined"]["effective_n"] > 0


# ── 표본이 '몇 개'인지와 '무엇'인지는 다른 질문이다 ────────────────────
#
# 21·63일 지평은 선행 구간이 아직 안 지난 최근 기록을 통째로 버린다. 로그
# 기준으로 세면 실기록이 표본에 들어간 것처럼 보이는데, 실측 2026-08-20 기준
# 두 지평의 실기록 채점일은 0 이었고 발행문은 "실기록 18일" 이라고 적었다.

def test_scored_dates_only_counts_days_that_actually_scored():
    days = [
        {"date": "D1", "scores": {t: {"combined": i} for i, t in enumerate("ABCDE")}},
        {"date": "D2", "scores": {t: {"combined": i} for i, t in enumerate("ABCDE")}},
        {"date": "D3", "scores": {t: {"combined": i} for i, t in enumerate("ABCDE")}},
    ]
    # D2 는 선행수익률이 없고(미래가 안 왔다), D3 는 종목이 문턱 미달이다.
    fwd = {
        "D1": {t: float(i) for i, t in enumerate("ABCDE")},
        "D3": {"A": 1.0, "B": 2.0},
    }

    assert sc.scored_dates(days, fwd, "combined") == ["D1"]


def test_scored_dates_skips_slugs_the_day_does_not_have():
    days = [{"date": "D1",
             "scores": {t: {"chart": i} for i, t in enumerate("ABCDE")}}]
    fwd = {"D1": {t: float(i) for i, t in enumerate("ABCDE")}}

    assert sc.scored_dates(days, fwd, "combined") == []
    assert sc.scored_dates(days, fwd, "chart") == ["D1"]


def test_scored_dates_agrees_with_score_analysts_n():
    """같은 규칙이어야 한다 — 갈라지면 발행문의 표본 구성이 n 과 안 맞는다."""
    days = [{"date": f"D{k}",
             "scores": {t: {"combined": (i + k) % 5} for i, t in enumerate("ABCDE")}}
            for k in range(6)]
    fwd = {f"D{k}": {t: float(i) for i, t in enumerate("ABCDE")}
           for k in range(4)}

    stats = sc.score_analysts(days, fwd, 1)["combined"]
    assert len(sc.scored_dates(days, fwd, "combined")) == stats["n"]
