"""발행 메시지 — 성적을 좋게 보이게 하는 장치가 없어야 한다.

이 채널의 유일한 차별화는 틀린 것을 그대로 보여주는 것이다. 그래서
음수 IC 표기와 표본 부족 표기를 테스트로 고정한다.

매수·매도·목표가 금지어와 면책 문구는 이 채널의 유일한 법적 보호막(유사
투자자문업 회피)이다 — 두 발행 경로(성적표·오늘의 기록) 모두에 적용해야
하므로 parametrize 로 같이 고정한다. 세 번째 발행 경로가 생기면 아래
_ALL_MESSAGES 에 한 줄만 추가하면 된다.
"""
import pytest

from modules import analyst_scorecard as asc
from modules import scorecard_message as sm

# 실제 지평 상수를 그대로 쓴다. 테스트가 자기 숫자를 들고 있으면 상수가
# 늘어도 발행문이 옛날 문장을 내보내는 것을 못 잡는다 — 1일 지평이 붙은 뒤에도
# "5·21·63일 뒤 채점" 이 8회 나간 것이 바로 그 사고다.
_HORIZONS = asc.HORIZONS
_COMBINE = asc.COMBINE_SLUGS

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
        "scorecard/normal": sm.build_scorecard_message(5, _BIG, ["quant"], combine_slugs=_COMBINE),
        "scorecard/empty": sm.build_scorecard_message(5, {}, [], combine_slugs=_COMBINE),
        "record/normal": sm.build_record_message(
            "2026-07-28", "bull", {"chart": [("AAPL", 73.8)]}, ["quant"],
            horizons=_HORIZONS, combine_slugs=_COMBINE),
        "record/empty": sm.build_record_message(
            "2026-07-28", "bull", {}, [], horizons=_HORIZONS, combine_slugs=_COMBINE),
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
    msg = sm.build_scorecard_message(5, _SMALL, [], combine_slugs=_COMBINE)

    assert "통계적 판단 불가" in msg


def test_sufficient_sample_shows_t_stat():
    msg = sm.build_scorecard_message(5, _BIG, [], combine_slugs=_COMBINE)

    assert "통계적 판단 불가" not in msg
    assert "-4.7" in msg


def test_none_t_stat_with_sufficient_n_is_still_undecidable():
    """se=0.0 → t_stat=None 인데 effective_n 은 충분한 경우, "None" 을 그대로
    찍지 않고 통계적 판단 불가로 처리해야 한다."""
    msg = sm.build_scorecard_message(5, _NONE_T_STAT_SUFFICIENT_N, [], combine_slugs=_COMBINE)

    assert "None" not in msg
    assert "통계적 판단 불가" in msg


def test_negative_ic_is_shown_signed():
    """음수 IC 를 절댓값으로 바꾸거나 숨기지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [], combine_slugs=_COMBINE)

    assert "-0.0940" in msg


def test_missing_slug_is_disclosed_with_reason():
    """슬러그를 조용히 빼면 성적표가 완전한 것처럼 보인다."""
    msg = sm.build_scorecard_message(5, _SMALL, ["quant"], combine_slugs=_COMBINE)

    assert "퀀트+재무" in msg
    assert "실측 IC 표본이 없어 가중치 근거가 아직 없음" in msg


def test_record_message_also_discloses_missing_slug():
    """오늘의 기록은 매 영업일 나가는 쪽이라, 슬러그 누락을 감추면 안 되는
    이유가 성적표보다 오히려 강하다."""
    msg = sm.build_record_message("2026-07-28", "bull",
                                  {"chart": [("AAPL", 73.8)]}, ["quant"],
                                  horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "퀀트+재무" in msg
    assert "실측 IC 표본이 없어 가중치 근거가 아직 없음" in msg


def test_record_message_discloses_truncated_ties():
    """동점으로 잘린 종목이 있으면 밝힌다 — 안 밝히면 순위가 아닌 것을
    순위처럼 보여주게 된다."""
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"ict": [("AAA", 100.0), ("BBB", 100.0)]},
        [], {"ict": 17}, horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "동점 17종목" in msg


def test_record_message_omits_tie_note_when_nothing_cut():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"chart": [("AAA", 73.8)]}, [], {},
        horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "동점" not in msg


def test_record_message_tie_notes_defaults_to_none():
    """기존 4인자 호출이 그대로 동작해야 한다."""
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"chart": [("AAA", 73.8)]}, [],
        horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "동점" not in msg


def test_combine_note_present_in_both_messages():
    """합성 방식을 감추면 순위의 의미를 알 수 없다."""
    record = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [],
        horizons=_HORIZONS, combine_slugs=_COMBINE)
    scorecard = sm.build_scorecard_message(5, _BIG, [], combine_slugs=_COMBINE)

    assert sm.combine_note(_COMBINE) in record
    assert sm.combine_note(_COMBINE) in scorecard


def test_record_message_discloses_dropped_tickers():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [], None, 7,
        horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "7종목은 한쪽 점수가 없어" in msg


def test_record_message_omits_dropped_line_when_zero():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [], None, 0,
        horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "종합에서 빠졌습니다" not in msg


def test_combined_slug_renders_as_korean_name():
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [],
        horizons=_HORIZONS, combine_slugs=_COMBINE)

    assert "*종합* 상위 1" in msg


# ── 재구성 표본은 재구성이라고 말한다 ────────────────────────────────
#
# 이 채널은 "예측 기록과 사후 채점을 공개한다" 고 적어 두었다. 백필분을
# 말없이 섞으면 예측한 적 없는 날을 성적으로 내보내게 된다.

def test_backfill_sample_is_disclosed():
    msg = sm.build_scorecard_message(5, _BIG, [],
                                     sample_mix={"live": 12, "backfill": 480}, combine_slugs=_COMBINE)

    assert "480" in msg and "12" in msg
    assert "생존자 편향" in msg


def test_pure_live_sample_says_nothing_about_backfill():
    """실기록만이면 군더더기를 붙이지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [],
                                     sample_mix={"live": 12, "backfill": 0}, combine_slugs=_COMBINE)

    assert "생존자 편향" not in msg
    assert sm.build_scorecard_message(5, _BIG, [], combine_slugs=_COMBINE) == msg


def test_record_message_names_the_real_horizons():
    """채점 지평은 상수에서 온다 — 문장에 박아 두면 지평이 늘어도 안 따라온다."""
    msg = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [],
        horizons=(1, 5, 21, 63), combine_slugs=_COMBINE)

    assert "이 기록은 1·5·21·63거래일 뒤 채점됩니다." in msg

    grown = sm.build_record_message(
        "2026-07-28", "bull", {"combined": [("AAA", 70.0)]}, [],
        horizons=(1, 5, 21, 63, 126), combine_slugs=_COMBINE)
    assert "1·5·21·63·126거래일 뒤 채점" in grown


def test_scorecard_message_labels_the_hit_rate_as_ic():
    """'적중률' 은 두 가지를 가리킨다. 성적표가 내는 것은 IC 쪽이고, 실측
    2026-08-20 기준 21일 지평에서 둘은 56.5% 대 50.2% 로 갈렸다 — 유리한 쪽을
    모호한 이름으로 내보내지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [], combine_slugs=_COMBINE)

    assert "IC 적중률 35.0%" in msg
    assert sm.HIT_RATE_NOTE in msg


def test_backfill_note_counts_the_scored_days_not_the_log():
    """실기록이 한 날도 안 들어간 지평이 실제로 있다(21·63일). 그때 '실기록
    0일' 이라고 적혀야 한다."""
    msg = sm.build_scorecard_message(21, _BIG, [],
                                     sample_mix={"live": 0, "backfill": 497}, combine_slugs=_COMBINE)

    assert "채점된 497일 중 497일" in msg
    assert "실기록 0일" in msg


# ── 판정선을 말하지 않으면 t 는 그냥 숫자다 ──────────────────────────
#
# 실측 2026-08-22: 네 지평의 t 가 -0.15 / -0.20 / +0.22 / -0.23 인데 발행문은
# "t=0.224 · 유효표본 47.3" 으로 끝났다. 판정선(|t| ≥ 2)이 문장에 없으면
# 구독자는 그 숫자가 선을 넘었는지 알 수 없고, 옆줄의 "IC 적중률 56.2%" 만
# 남는다. 화면에는 '판정' 컬럼과 문턱 캡션이 둘 다 있었다 — 가드가 한 경로에만.

def test_below_the_line_says_it_is_below_the_line():
    """판정선을 못 넘으면 못 넘었다고 적는다."""
    weak = {"chart": {"mean_ic": 0.0063, "se": 0.028, "t_stat": 0.224,
                      "n": 502, "effective_n": 47.3, "hit_rate": 56.2}}
    msg = sm.build_scorecard_message(21, weak, [], combine_slugs=_COMBINE)

    assert "0.224" in msg
    assert sm.DECISION_FAIL in msg
    assert f"|t| ≥ {asc.DECIDE_T_THRESHOLD:.1f}" in msg
    assert f"유효표본 ≥ {asc.DECIDE_MIN_EFFECTIVE_N}" in msg


def test_clearing_the_line_says_so():
    strong = {"chart": {"mean_ic": 0.05, "se": 0.01, "t_stat": 5.0,
                        "n": 300, "effective_n": 90.0, "hit_rate": 70.0}}
    msg = sm.build_scorecard_message(21, strong, [], combine_slugs=_COMBINE)

    assert sm.DECISION_PASS in msg
    assert sm.DECISION_FAIL not in msg


def test_big_t_with_thin_effective_sample_still_fails():
    """|t| 만 보면 통과인데 유효표본이 모자란 경우 — 두 조건 다 봐야 한다."""
    msg = sm.build_scorecard_message(5, _BIG, [], combine_slugs=_COMBINE)  # t=-4.7, 유효 12

    assert sm.DECISION_FAIL in msg


def test_undecidable_rows_also_carry_the_line():
    """'통계적 판단 불가' 줄에도 판정선을 붙인다 — 없으면 그 줄만 이유가 없다."""
    msg = sm.build_scorecard_message(5, _SMALL, [], combine_slugs=_COMBINE)

    assert "통계적 판단 불가" in msg
    assert sm.DECISION_FAIL in msg


# ── 종합의 구성은 한 곳에서만 나온다 ────────────────────────────────

def test_combine_note_names_come_from_the_slugs():
    """문장에 이름을 박으면 COMBINE_SLUGS 가 바뀌어도 안 따라온다."""
    assert sm.combine_note(("chart", "ict")) == sm.combine_note(_COMBINE)

    grown = sm.combine_note(("chart", "ict", "quant"))
    assert sm.SLUG_NAMES["quant"] in grown
    assert sm.SLUG_NAMES["quant"] not in sm.combine_note(("chart", "ict"))


def test_scorecard_title_says_trading_days():
    """채점은 봉을 센다 — 21거래일은 달력 중위 30일, 63거래일은 91일이다."""
    msg = sm.build_scorecard_message(63, _BIG, [], combine_slugs=_COMBINE)

    assert "63거래일 지평 성적표" in msg
