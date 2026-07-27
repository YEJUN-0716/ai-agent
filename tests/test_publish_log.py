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
