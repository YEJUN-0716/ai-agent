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


def test_top_by_slug_tiebreaks_by_ticker_when_scores_equal():
    """ict 는 100.0 에서 자주 동점이 난다(2026-07-23, 19종목). 점수만으로
    정렬하면 dict 삽입 순서에 기대게 되어 같은 로그가 실행마다 다른 목록을
    낼 수 있다 — 티커를 2차 키로 둬 결정론적으로 만든다."""
    day = {"scores": {
        "NVDA": {"ict": 100.0},
        "AAPL": {"ict": 100.0},
        "MSFT": {"ict": 100.0},
    }}

    top = _sw().top_by_slug(day, limit=5)

    assert top["ict"] == [("AAPL", 100.0), ("MSFT", 100.0), ("NVDA", 100.0)]


def test_main_fails_loudly_when_record_message_send_fails(monkeypatch):
    """오늘의 기록 발송 실패는 조용히 넘어가지 않는다 — 워크플로가 실패해야 한다."""
    sw = _sw()
    monkeypatch.setattr(sw.analyst_log, "load_days", lambda: [
        {"date": "2026-07-28", "regime": "bull",
         "scores": {"AAPL": {"chart": 70.0}}},
    ])
    monkeypatch.setattr(sw, "send_tg", lambda msg: False)
    # 이 지평의 발행 이력이 없다는 것을 명시적으로 고정한다 — 실제
    # data/publish_log 상태에 테스트 결과가 좌우되지 않게 한다.
    monkeypatch.setattr(sw.publish_log, "last_published_record_date",
                        lambda root=sw.publish_log.LOG_DIRNAME: None)

    assert sw.main() != 0


def test_main_skips_record_send_without_failing_when_already_published(monkeypatch):
    """같은 log_date 가 이미 발행돼 있으면 발송을 건너뛰되, 이것은 실패가
    아니다 — workflow_dispatch 스모크 테스트가 매번 중복 발행하던 문제,
    그리고 기록기가 밀린 날 어제 날짜를 재발송하던 문제 둘 다 이걸로 막는다."""
    sw = _sw()
    # chart·ict 를 둘 다 준다 — 종합 점수는 두 점수가 모두 있는 종목만
    # 쓰므로, 한쪽만 있는 기록은 채점 대상이 0건이 돼 이 테스트가 보려는
    # 것과 무관한 이유로 실패한다.
    monkeypatch.setattr(sw.analyst_log, "load_days", lambda: [
        {"date": "2026-07-28", "regime": "bull",
         "scores": {"AAPL": {"chart": 70.0, "ict": 50.0}}},
    ])
    monkeypatch.setattr(sw.publish_log, "last_published_record_date",
                        lambda root=sw.publish_log.LOG_DIRNAME: "2026-07-28")
    # 가격 패널을 빈 것으로 둬 채점 경로가 네트워크 없이 안전하게 "새 판정
    # 없음"으로 끝나게 한다 — 이 테스트가 보려는 것은 오직 기록 스킵이다.
    monkeypatch.setattr(sw.price_panel, "load_panel", lambda *a, **k: ({}, {}))

    calls = []
    monkeypatch.setattr(sw, "send_tg", lambda msg: calls.append(msg) or True)

    result = sw.main()

    assert calls == []   # 오늘의 기록은 발송되지 않았다
    assert result == 0   # 스킵은 실패가 아니다


# --- 동점 절단 공개 — "상위 5" 가 순위가 아니라 동점 무리의 임의
# 부분집합일 때 그 사실을 밝힌다. 최종 리뷰 지적(2026-07-28).

def test_cut_tie_counts_reports_truncated_ties():
    """ict 는 100.0 에서 포화된다 — 5개를 보여줘도 뒤에 더 있으면 밝힌다."""
    day = {"scores": {t: {"ict": 100.0} for t in
                      ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")}}

    top = _sw().top_by_slug(day, limit=5)

    assert _sw().cut_tie_counts(day, top) == {"ict": 2}


def test_cut_tie_counts_silent_when_nothing_truncated():
    day = {"scores": {"AAA": {"ict": 90.0}, "BBB": {"ict": 80.0}}}

    top = _sw().top_by_slug(day, limit=5)

    assert _sw().cut_tie_counts(day, top) == {}


def test_cut_tie_counts_only_counts_the_boundary_score():
    """경계 점수와 같은 것만 센다 — 그 위 점수들은 잘린 게 아니다."""
    day = {"scores": {"AAA": {"ict": 100.0}, "BBB": {"ict": 90.0},
                      "CCC": {"ict": 90.0}, "DDD": {"ict": 90.0}}}

    top = _sw().top_by_slug(day, limit=2)   # AAA(100), BBB(90)

    assert _sw().cut_tie_counts(day, top) == {"ict": 2}


# --- 종합 점수 — chart·ict 를 단순 평균해 하나의 순위로 낸다.
# 기록(analyst_log)은 슬러그별로 그대로 남고, 합산은 발행 시점에만 한다.

def test_combined_day_averages_both_analysts():
    day = {"date": "2026-07-28", "regime": "bull",
           "scores": {"AAPL": {"chart": 80.0, "ict": 60.0}}}

    out = _sw().combined_day(day)

    assert out["scores"]["AAPL"] == {"combined": 70.0}
    assert out["date"] == "2026-07-28"
    assert out["regime"] == "bull"


def test_combined_day_drops_ticker_missing_one_analyst():
    """한쪽만 있으면 뺀다 — 중립값으로 채우면 계산 불가가 중립 판단으로
    섞이고, 한 축 점수를 그대로 쓰면 다른 종목과 같은 자로 잰 게 아니다."""
    day = {"scores": {"AAPL": {"chart": 80.0, "ict": 60.0},
                      "MSFT": {"chart": 90.0},
                      "NVDA": {"ict": 90.0}}}

    out = _sw().combined_day(day)

    assert set(out["scores"]) == {"AAPL"}


def test_dropped_ticker_count_reports_the_gap():
    day = {"scores": {"AAPL": {"chart": 80.0, "ict": 60.0},
                      "MSFT": {"chart": 90.0}}}

    assert _sw().dropped_ticker_count(day, _sw().combined_day(day)) == 1


def test_combined_days_discards_days_with_nothing_left():
    days = [{"date": "2026-07-27", "scores": {"AAPL": {"chart": 1.0}}},
            {"date": "2026-07-28",
             "scores": {"AAPL": {"chart": 80.0, "ict": 60.0}}}]

    out = _sw().combined_days(days)

    assert [d["date"] for d in out] == ["2026-07-28"]


def test_combined_output_feeds_existing_ranking_unchanged():
    """변환 결과를 top_by_slug 에 그대로 넣을 수 있어야 한다 — 순위·동점
    공개 코드를 종합용으로 따로 만들지 않는다."""
    day = {"scores": {"AAA": {"chart": 100.0, "ict": 100.0},
                      "BBB": {"chart": 100.0, "ict": 100.0},
                      "CCC": {"chart": 10.0, "ict": 10.0}}}
    combined = _sw().combined_day(day)

    top = _sw().top_by_slug(combined, limit=1)

    assert top["combined"] == [("AAA", 100.0)]
    assert _sw().cut_tie_counts(combined, top) == {"combined": 1}
