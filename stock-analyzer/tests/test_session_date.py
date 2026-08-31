"""장 마감 뒤 배치가 찍는 날짜는 러너 벽시계가 아니라 그 장의 날짜여야 한다.

2026-08 말 GitHub 예약 큐 적체로 실제로 깨졌다. 아래 세 시각은 실측이다 —
장부에 8/26 이 통째로 빠지고 장이 서지도 않는 8/29(토)가 찍혔다.
"""
from datetime import datetime, timezone

import pytest

from modules.virtual_broker import market_date, session_date


def _utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("ran_at, trading_day", [
    # 지연 없음: 21:30 UTC = 뉴욕 17:30, 그날 장이다.
    ("2026-08-24T21:54", "2026-08-24"),
    # 실측 +3h28m — 자정(UTC)을 넘겼다. 벽시계는 8/27 이라고 했다.
    ("2026-08-27T00:58", "2026-08-26"),
    # 실측 +8h02m — 뉴욕 날짜도 넘겼다. market_date() 로도 안 잡히던 자리.
    ("2026-08-28T05:32", "2026-08-27"),
    # 실측 +5h46m, 금요일치가 토요일에 떴다.
    ("2026-08-29T03:16", "2026-08-28"),
    # 18시간 밀려 토요일 한낮에 떠도 금요일 장이다.
    ("2026-08-29T15:00", "2026-08-28"),
])
def test_session_date_follows_the_market_not_the_clock(ran_at, trading_day):
    assert session_date(_utc(ran_at)).isoformat() == trading_day


def test_market_date_alone_was_not_enough():
    """왜 기존 market_date() 를 그냥 쓰지 않았는지 — 8시간 밀리면 그것도 틀린다."""
    assert market_date(_utc("2026-08-28T05:32")).isoformat() == "2026-08-28"
    assert session_date(_utc("2026-08-28T05:32")).isoformat() == "2026-08-27"


def test_market_date_still_answers_the_intraday_question():
    """장중 주문 날짜는 여전히 market_date() 다. session_date() 를 쓰면 어제가 된다."""
    noon_ny = _utc("2026-08-27T14:00")          # 뉴욕 10:00, 장중
    assert market_date(noon_ny).isoformat() == "2026-08-27"
    assert session_date(noon_ny).isoformat() == "2026-08-26"
