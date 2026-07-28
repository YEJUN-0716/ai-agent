"""발행 이력 — 같은 판정을 두 번 보내지 않기 위한 최소 상태.

실제 data/publish_log/ 는 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
from modules import publish_log as pl


def test_never_published_returns_none(tmp_path):
    assert pl.last_published_n(5, root=tmp_path) is None


def test_round_trip(tmp_path):
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)

    assert pl.last_published_n(5, root=tmp_path) == 1


def test_horizons_are_independent(tmp_path):
    """5일을 발행했다고 21일이 발행된 것은 아니다."""
    pl.record_published("2026-07-30", 5, 3, root=tmp_path)

    assert pl.last_published_n(21, root=tmp_path) is None


def test_largest_n_wins(tmp_path):
    """표본은 단조증가한다 — 파일 순서에 기대지 않고 최대값을 쓴다."""
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)
    pl.record_published("2026-08-06", 5, 6, root=tmp_path)

    assert pl.last_published_n(5, root=tmp_path) == 6


def test_broken_line_is_skipped(tmp_path):
    """깨진 줄 하나가 발행 전체를 막지 않는다."""
    pl.record_published("2026-07-30", 5, 2, root=tmp_path)
    path = tmp_path / "published_2026.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{깨진 줄\n",
                    encoding="utf-8")

    assert pl.last_published_n(5, root=tmp_path) == 2


# --- 오늘의 기록(kind="record") 발행 — 성적표(kind 없음)와 한 파일을
# 공유하지만 조회는 섞이지 않아야 한다.

def test_record_date_never_published_returns_none(tmp_path):
    assert pl.last_published_record_date(root=tmp_path) is None


def test_record_date_round_trip(tmp_path):
    pl.record_published_record("2026-07-28", "2026-07-28", root=tmp_path)

    assert pl.last_published_record_date(root=tmp_path) == "2026-07-28"


def test_rerunning_with_same_log_date_reports_already_published(tmp_path):
    """workflow_dispatch 로 같은 로그 날짜에 다시 돌려도 같은 판정이 나와야
    한다 — 그래야 워커가 재발송을 건너뛸 수 있다."""
    pl.record_published_record("2026-07-28", "2026-07-25", root=tmp_path)

    # 재실행 시점의 published_at 은 다를 수 있어도(예: 다음날 새벽 재시도),
    # 비교 기준은 log_date 다.
    assert pl.last_published_record_date(root=tmp_path) == "2026-07-25"
    pl.record_published_record("2026-07-29", "2026-07-25", root=tmp_path)
    assert pl.last_published_record_date(root=tmp_path) == "2026-07-25"


def test_scorecard_entries_do_not_leak_into_record_query(tmp_path):
    """kind 가 없는(성적표) 항목은 last_published_record_date() 에 잡히지
    않는다 — 두 종류가 같은 파일에 섞여도 서로의 조회를 오염시키면 안 된다."""
    pl.record_published("2026-07-30", 5, 3, root=tmp_path)

    assert pl.last_published_record_date(root=tmp_path) is None


def test_record_entries_do_not_leak_into_scorecard_query(tmp_path):
    """kind="record" 항목은 last_published_n() 의 지평별 집계에 들어가면
    안 된다 — n 도 horizon 도 없는 다른 종류의 발행이다."""
    pl.record_published_record("2026-07-28", "2026-07-28", root=tmp_path)

    assert pl.last_published_n(5, root=tmp_path) is None


def test_legacy_entries_without_kind_still_count_as_scorecard(tmp_path):
    """이 브랜치 이전에 쌓인 published_YYYY.jsonl 에는 kind 키가 아예 없다.
    fallback 이 없으면 기존 파일 전체가 무효가 된다."""
    path = tmp_path / "published_2026.jsonl"
    path.write_text(
        '{"published_at": "2026-07-30", "horizon": 5, "n": 4}\n',
        encoding="utf-8")

    assert pl.last_published_n(5, root=tmp_path) == 4
