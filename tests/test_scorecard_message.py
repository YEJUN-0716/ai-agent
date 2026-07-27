"""발행 메시지 — 성적을 좋게 보이게 하는 장치가 없어야 한다.

이 채널의 유일한 차별화는 틀린 것을 그대로 보여주는 것이다. 그래서
음수 IC 표기와 표본 부족 표기를 테스트로 고정한다.
"""
from modules import scorecard_message as sm

_SMALL = {"chart": {"mean_ic": -0.03, "se": None, "t_stat": None,
                    "n": 1, "effective_n": 1.0, "hit_rate": 0.0}}
_BIG = {"quant": {"mean_ic": -0.094, "se": 0.02, "t_stat": -4.7,
                  "n": 60, "effective_n": 12.0, "hit_rate": 35.0}}


def test_scorecard_always_carries_disclaimer():
    msg = sm.build_scorecard_message(5, _SMALL, [])

    assert sm.DISCLAIMER in msg


def test_record_message_always_carries_disclaimer():
    msg = sm.build_record_message("2026-07-28", "bull",
                                  {"chart": [("AAPL", 73.8)]})

    assert sm.DISCLAIMER in msg


def test_small_sample_is_flagged_as_undecidable():
    """n=1 이면 통계적 판단 불가를 명시한다 — 감추지 않는 것이 전제다."""
    msg = sm.build_scorecard_message(5, _SMALL, [])

    assert "통계적 판단 불가" in msg


def test_sufficient_sample_shows_t_stat():
    msg = sm.build_scorecard_message(5, _BIG, [])

    assert "통계적 판단 불가" not in msg
    assert "-4.7" in msg


def test_negative_ic_is_shown_signed():
    """음수 IC 를 절댓값으로 바꾸거나 숨기지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [])

    assert "-0.0940" in msg


def test_missing_slug_is_disclosed_with_reason():
    """슬러그를 조용히 빼면 성적표가 완전한 것처럼 보인다."""
    msg = sm.build_scorecard_message(5, _SMALL, ["quant"])

    assert "퀀트+재무" in msg
    assert "일별 펀더멘털 수집 미구축" in msg


def test_no_buy_sell_wording_in_record_message():
    """매수·매도 표현은 유사투자자문업 신고 대상이 된다."""
    msg = sm.build_record_message("2026-07-28", "bull",
                                  {"chart": [("AAPL", 73.8)]})

    for banned in ("매수", "매도", "목표가", "추천"):
        assert banned not in msg
