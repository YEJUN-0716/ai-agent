"""애널리스트 점수 기록 저장소 — 왕복과 결측 처리 고정.

실제 data/analyst_log/ 는 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
from modules import analyst_log as al


def test_round_trip(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.14, "ict": 48.5}}, root=tmp_path)

    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert days[0]["date"] == "2026-07-23"
    assert days[0]["regime"] == "bull"
    # 소수 1자리로 절삭된다
    assert days[0]["scores"]["AAPL"]["chart"] == 62.1


def test_missing_slug_stays_missing(tmp_path):
    """계산 불가는 키를 뺀다 — 50 으로 채우면 '중립 판단'으로 성적에 섞인다."""
    al.append_day("2026-07-23", "bull", {"AAPL": {"chart": 62.1}}, root=tmp_path)

    day = al.load_days(tmp_path)[0]
    assert "ict" not in day["scores"]["AAPL"]


def test_none_score_is_dropped(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.1, "ict": None}}, root=tmp_path)

    assert "ict" not in al.load_days(tmp_path)[0]["scores"]["AAPL"]


def test_ticker_with_no_scores_is_dropped(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.1}, "MSFT": {}}, root=tmp_path)

    assert "MSFT" not in al.load_days(tmp_path)[0]["scores"]


def test_days_are_sorted_across_years(tmp_path):
    al.append_day("2027-01-05", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-12-31", "bear", {"A": {"chart": 2.0}}, root=tmp_path)

    assert [d["date"] for d in al.load_days(tmp_path)] == ["2026-12-31", "2027-01-05"]


def test_since_filter(tmp_path):
    al.append_day("2026-07-01", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 2.0}}, root=tmp_path)

    assert len(al.load_days(tmp_path, since="2026-07-10")) == 1


def test_duplicate_date_is_replaced(tmp_path):
    """같은 날 스캔이 두 번 돌아도 그 날이 두 번 계산되면 안 된다."""
    al.append_day("2026-07-23", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 9.0}}, root=tmp_path)

    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert days[0]["scores"]["A"]["chart"] == 9.0


def test_missing_directory_returns_empty(tmp_path):
    assert al.load_days(tmp_path / "nope") == []
