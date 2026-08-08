"""발행 메시지 — 성적을 좋게 보이게 하는 장치가 없어야 한다.

이 채널의 유일한 차별화는 틀린 것을 그대로 보여주는 것이다. 그래서
음수 IC 표기와 표본 부족 표기를 테스트로 고정한다.

매수·매도·목표가 금지어와 면책 문구는 이 채널의 유일한 법적 보호막(유사
투자자문업 회피)이다 — 두 발행 경로(성적표·오늘의 기록) 모두에 적용해야
하므로 parametrize 로 같이 고정한다. 세 번째 발행 경로가 생기면 아래
_ALL_MESSAGES 에 한 줄만 추가하면 된다.
"""
import pytest

from modules import scorecard_message as sm

_SMALL = {"chart": {"mean_ic": -0.03, "se": None, "t_stat": None,
                    "n": 1, "effective_n": 1.0, "hit_rate": 0.0}}
_BIG = {"quant": {"mean_ic": -0.094, "se": 0.02, "t_stat": -4.7,
                  "n": 60, "effective_n": 12.0, "hit_rate": 35.0}}
# newey_west_se() 는 0.0 을 돌려줄 수 있다 — 그러면 se 는 truthy 가 아니라서
# score_analysts() 가 t_stat=None 을 내는데, effective_n 은 n 으로 그대로
# 폴백해 MIN_EFFECTIVE_N 을 가뿐히 넘는다. 표를 그대로 찍으면 "t=None" 이
# 발행된다.
_NONE_T_STAT_SUFFICIENT_N = {
    "chart": {"mean_ic": 0.0, "se": 0.0, "t_stat": None,
              "n": 30, "effective_n": 30.0, "hit_rate": 50.0},
}


def _all_messages():
    """규제 제약을 받는 모든 발행문 — 정상 입력과 퇴화 입력(빈 stats/top) 둘 다."""
    return {
        "scorecard/normal": sm.build_scorecard_message(5, _BIG, ["quant"]),
        "scorecard/empty": sm.build_scorecard_message(5, {}, []),
        "record/normal": sm.build_record_message(
            "2026-07-28", "bull", {"chart": [("AAPL", 73.8)]}, ["quant"]),
        "record/empty": sm.build_record_message(
            "2026-07-28", "bull", {}, []),
    }


_ALL_MESSAGES = list(_all_messages().items())


@pytest.mark.parametrize("label, msg", _ALL_MESSAGES)
def test_disclaimer_always_present(label, msg):
    assert sm.DISCLAIMER in msg, f"{label}: 면책 문구 없음"


@pytest.mark.parametrize("label, msg", _ALL_MESSAGES)
def test_no_buy_sell_wording(label, msg):
    """매수·매도·목표가·추천 표현은 유사투자자문업 신고 대상이 된다."""
    for banned in ("매수", "매도", "목표가", "추천"):
        assert banned not in msg, f"{label}: 금지어 {banned!r} 발견"


def test_small_sample_is_flagged_as_undecidable():
    """n=1 이면 통계적 판단 불가를 명시한다 — 감추지 않는 것이 전제다."""
    msg = sm.build_scorecard_message(5, _SMALL, [])

    assert "통계적 판단 불가" in msg


def test_sufficient_sample_shows_t_stat():
    msg = sm.build_scorecard_message(5, _BIG, [])

    assert "통계적 판단 불가" not in msg
    assert "-4.7" in msg


def test_none_t_stat_with_sufficient_n_is_still_undecidable():
    """se=0.0 → t_stat=None 인데 effective_n 은 충분한 경우, "None" 을 그대로
    찍지 않고 통계적 판단 불가로 처리해야 한다."""
    msg = sm.build_scorecard_message(5, _NONE_T_STAT_SUFFICIENT_N, [])

    assert "None" not in msg
    assert "통계적 판단 불가" in msg


def test_negative_ic_is_shown_signed():
    """음수 IC 를 절댓값으로 바꾸거나 숨기지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [])

    assert "-0.0940" in msg


def test_missing_slug_is_disclosed_with_reason():
    """슬러그를 조용히 빼면 성적표가 완전한 것처럼 보인다."""
    msg = sm.build_scorecard_message(5, _SMALL, ["quant"])

    assert "퀀트+재무" in msg
    assert "실측 IC 표본이 없어 가중치 근거가 아직 없음" in msg


def test_record_message_also_discloses_missing_slug():
    """오늘의 기록은 매 영업일 나가는 쪽이라, 슬러그 누락을 감추면 안 되는
    이유가 성적표보다 오히려 강하다."""
    msg = sm.build_record_message("2026-07-28", "bull",
                                  {"chart": [("AAPL", 73.8)]}, ["quant"])

    assert "퀀트+재무" in msg
    assert "실측 IC 표본이 없어 가중치 근거가 아직 없음" in msg


def test_record_message_discloses_truncated_ties():
    """동점으로 잘린 종목이 있으면 밝힌다 — 안 밝히면 순위가 아닌 것을
    순위처럼 보여주게 된다."""
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"ict": [("AAA", 100.0), ("BBB", 100.0)]},
        [], {"ict": 17})

    assert "동점 17종목" in msg


def test_record_message_omits_tie_note_when_nothing_cut():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"chart": [("AAA", 73.8)]}, [], {})

    assert "동점" not in msg


def test_record_message_tie_notes_defaults_to_none():
    """기존 4인자 호출이 그대로 동작해야 한다."""
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"chart": [("AAA", 73.8)]}, [])

    assert "동점" not in msg


def test_combine_note_present_in_both_messages():
    """합성 방식을 감추면 순위의 의미를 알 수 없다."""
    record = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [])
    scorecard = sm.build_scorecard_message(5, _BIG, [])

    assert sm.COMBINE_NOTE in record
    assert sm.COMBINE_NOTE in scorecard


def test_record_message_discloses_dropped_tickers():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [], None, 7)

    assert "7종목은 한쪽 점수가 없어" in msg


def test_record_message_omits_dropped_line_when_zero():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [], None, 0)

    assert "종합에서 빠졌습니다" not in msg


def test_combined_slug_renders_as_korean_name():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [])

    assert "*종합* 상위 1" in msg


# ── 재구성 표본은 재구성이라고 말한다 ────────────────────────────────
#
# 이 채널은 "예측 기록과 사후 채점을 공개한다" 고 적어 두었다. 백필분을
# 말없이 섞으면 예측한 적 없는 날을 성적으로 내보내게 된다.

def test_backfill_sample_is_disclosed():
    msg = sm.build_scorecard_message(5, _BIG, [],
                                     sample_mix={"live": 12, "backfill": 480})

    assert "480" in msg and "12" in msg
    assert "생존자 편향" in msg


def test_pure_live_sample_says_nothing_about_backfill():
    """실기록만이면 군더더기를 붙이지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [],
                                     sample_mix={"live": 12, "backfill": 0})

    assert "생존자 편향" not in msg
    assert sm.build_scorecard_message(5, _BIG, []) == msg
