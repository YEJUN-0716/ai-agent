"""선수 평점 집계 — 네트워크 없음(FotMob 응답 모양을 직접 만든다).

FotMob 이 스스로 매기는 시즌 평점과 우리 평균이 일치하는지는
`python football/fotmob.py` 의 자체 점검이 실제 데이터로 잰다.
여기서는 평균을 어떻게 내는지(무엇이 몇 번 세어지는지)만 못 박는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "football"))
import epl  # noqa: E402
import fotmob  # noqa: E402
import pytest  # noqa: E402


def _row(pid, rating, minutes=90, match=1):
    return dict(id=pid, name=f"P{pid}", rating=rating, minutes=minutes,
                match=match, date="2026-08-30", opp="Brighton")


def test_경기_평점의_단순_평균이다():
    rows = [_row(1, 8.0, match=1), _row(1, 6.0, minutes=10, match=2)]
    (p,) = fotmob.average(rows)
    # 10분 뛴 경기도 90분 경기와 같은 무게 — 가중하지 않는다는 게 정의다
    assert p["avg"] == 7.0
    assert p["n"] == 2 and p["minutes"] == 100


def test_표본이_얇은_선수를_자른다():
    rows = [_row(1, 9.5), _row(2, 6.0, match=1), _row(2, 6.4, match=2)]
    names = [p["id"] for p in fotmob.average(rows, min_matches=2)]
    assert names == [2], "1경기짜리 9.5 가 표 맨 위에 남으면 안 된다"


def test_평균이_높은_순으로_나온다():
    rows = [_row(1, 6.1), _row(2, 7.9), _row(3, 7.0)]
    assert [p["id"] for p in fotmob.average(rows)] == [2, 3, 1]


def test_평점이_없는_선수는_행이_되지_않는다():
    """벤치는 stats 가 빈 리스트다 — _stat 이 None 을 돌려주고 호출부가 거른다."""
    assert fotmob._stat({"stats": []}, fotmob.RATING) is None
    assert fotmob._stat(
        {"stats": [{"stats": {fotmob.RATING: {"stat": {"value": 7.51}}}}]}, fotmob.RATING
    ) == 7.51


def test_캐시_경로가_data_밖으로_못_나간다(tmp_path):
    """경기 id 는 FotMob 이 준 외부 값이다 — 파일명에 그대로 들어간다."""
    escaped = epl.CACHE_DIR / ".." / "evil.json"
    with pytest.raises(ValueError):
        epl.cached_json("https://example.invalid/x", escaped, 10)
    assert not escaped.exists(), "막기 전에 파일이 이미 쓰였다"
