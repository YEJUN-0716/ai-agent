"""발행 판별 — 같은 판정을 두 번 보내지 않고, 새 판정은 놓치지 않는다.

네트워크가 필요한 main() 은 여기서 테스트하지 않는다. 판별 로직만 순수
함수로 떼어내 고정한다.

scorecard_worker 는 함수 안에서 import 한다 — price_panel 이 최상단에서
yfinance 를 끌어온다. tests/test_analyst_log.py 의 패턴을 따른다.
"""
from modules import publish_log as pl


def _sw():
    import scorecard_worker
    return scorecard_worker


def test_first_ever_publish_is_new(tmp_path):
    stats = {5: {"chart": {"n": 1}}}

    assert _sw().new_horizons(stats, root=tmp_path) == [5]


def test_same_sample_is_not_republished(tmp_path):
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)
    stats = {5: {"chart": {"n": 1}}}

    assert _sw().new_horizons(stats, root=tmp_path) == []


def test_grown_sample_is_republished(tmp_path):
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)
    stats = {5: {"chart": {"n": 4}}}

    assert _sw().new_horizons(stats, root=tmp_path) == [5]


def test_empty_stats_is_skipped(tmp_path):
    """채점된 날이 없으면 발행하지 않는다."""
    assert _sw().new_horizons({5: {}}, root=tmp_path) == []


def test_uses_largest_n_across_analysts(tmp_path):
    """애널리스트마다 채점된 날 수가 다를 수 있다 — 최대값으로 판단한다."""
    stats = {5: {"chart": {"n": 4}, "ict": {"n": 2}}}

    assert _sw().new_horizons(stats, root=tmp_path) == [5]


def test_top_by_slug_takes_highest_scores():
    day = {"scores": {
        "AAPL": {"chart": 73.8, "ict": 20.0},
        "MSFT": {"chart": 41.8},
        "NVDA": {"chart": 61.9, "ict": 90.0},
    }}

    top = _sw().top_by_slug(day, limit=2)

    assert top["chart"] == [("AAPL", 73.8), ("NVDA", 61.9)]
    assert top["ict"] == [("NVDA", 90.0), ("AAPL", 20.0)]


def test_top_by_slug_skips_absent_slug():
    """점수가 없는 종목은 그 슬러그 순위에 넣지 않는다."""
    day = {"scores": {"AAPL": {"chart": 73.8}, "MSFT": {"ict": 50.0}}}

    top = _sw().top_by_slug(day, limit=5)

    assert top["chart"] == [("AAPL", 73.8)]
    assert top["ict"] == [("MSFT", 50.0)]
