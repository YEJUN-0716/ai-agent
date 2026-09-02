"""결제일 보정 — 31일 결제가 2월에 사라지면 안 된다. 네트워크 없음."""
import sys

import pytest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import notion_recurring as nr  # noqa: E402


def test_평범한_날은_그대로():
    assert nr.결제일(2026, 10, 2) == date(2026, 10, 2)


def test_없는_날은_말일로_당긴다():
    assert nr.결제일(2027, 2, 31) == date(2027, 2, 28)
    assert nr.결제일(2026, 11, 31) == date(2026, 11, 30)


def test_윤년_2월():
    assert nr.결제일(2028, 2, 30) == date(2028, 2, 29)


def test_등록된_항목은_가계부_선택지_안에_있다():
    분류 = {"식비", "교통", "주거", "생활", "문화", "의료", "저축", "기타"}
    for item in nr.RECURRING:
        assert item["분류"] in 분류, item
        assert item["구분"] in {"지출", "수입"}, item
        assert 1 <= item["일"] <= 31, item


def test_대상달_기본은_이번_달():
    assert nr.대상달([], date(2026, 9, 17)) == date(2026, 9, 1)


def test_대상달을_지정하면_그_달():
    assert nr.대상달(["--month", "2027-02"], date(2026, 9, 17)) == date(2027, 2, 1)


@pytest.mark.parametrize("나쁜값", ["2027-2; rm -rf /", "$(whoami)", "2027", ""])
def test_이상한_달은_거부한다(나쁜값):
    # 이 값은 수동 실행 입력에서 온다 — 밖에서 온 글자를 그대로 믿지 않는다
    with pytest.raises(ValueError):
        nr.대상달(["--month", 나쁜값], date(2026, 9, 17))


def test_month_에_값이_없으면_거부한다():
    with pytest.raises(ValueError):
        nr.대상달(["--month"], date(2026, 9, 17))
