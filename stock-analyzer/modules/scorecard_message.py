"""발행 메시지 조립 — 숫자를 문장으로 바꾼다. 계산도 네트워크도 없다.

성적을 좋게 보이게 하는 장치를 두지 않는다. 음수 IC 는 음수로 쓰고, 표본이
모자라면 모자라다고 쓴다. 이 채널의 유일한 차별화가 그것이다.

매수·매도·목표가 표현을 쓰지 않는다 — 점수와 순위, 사후 채점만 발행한다.
"""
# 판정선은 analyst_scorecard 가 소유한다 — 화면(app._analyst_scorecard_rows)과
# 발행문이 **같은 두 숫자**를 읽어야 한다. 여기에 따로 적으면 언젠가 갈라지고,
# 그때 바깥으로 나가는 쪽이 틀린 값을 든다.
from modules.analyst_scorecard import (DECIDE_MIN_EFFECTIVE_N,
                                       DECIDE_T_THRESHOLD)

DISCLAIMER = (
    "이 채널은 예측 기록과 사후 채점을 공개합니다. 투자 자문이나 매매 "
    "권유가 아니며, 투자 판단과 그 결과는 본인에게 귀속됩니다."
)

# 유효 표본이 이보다 적으면 평균 IC 를 판정 근거로 말하지 않는다.
# 겹치는 선행 구간 때문에 겉보기 n 은 쉽게 커지지만 effective_n 은 안 커진다.
MIN_EFFECTIVE_N = 10

SLUG_NAMES = {
    "chart":    "차트+파동+모멘텀",
    "quant":    "퀀트+재무",
    "ict":      "ICT+CRT",
    "combined": "종합",
    # 화면 상단에 뜨는 총괄 판정 점수 그대로. 2026-08-06 부터 기록된다 —
    # 위 셋과 시계가 다르다.
    "verdict":  "총괄 판정(화면)",
}

# 퀀트+재무는 2026-08-07 부터 기록한다(signal_worker.record_analyst_scores).
# 다만 이 채널의 종합 점수는 여전히 차트+ICT 단순평균이다 — 셋을 섞을 가중치를
# 정할 실측 IC 표본이 아직 없다(근거 없는 가중치보다 균등이 정직하다는 결정).
# 앱 화면의 총괄 판정은 ic_weights 기반 블렌드라 이 채널과 다른 값이다.
MISSING_REASON = {"quant": "실측 IC 표본이 없어 가중치 근거가 아직 없음"}

# 종합 점수가 무엇의 평균인지 밝힌다. 합성 방식을 감추면 순위의 의미를
# 알 수 없고, 나중에 가중치를 바꿨을 때 구독자가 알아챌 방법도 없다.
#
# 이름을 문장에 박지 않는다 — `COMBINE_SLUGS` 에 quant 를 넣는 날 이 문장이
# 조용히 거짓이 된다(같은 자리의 `MISSING_SLUGS` 도 함께). 이 저장소가 같은
# 모양으로 네 번 물렸다: 리포트 산문 두 번, 채점 지평 한 번.
# 기본값을 두지 않는 이유도 그때와 같다 — 기본값은 하드코딩이 숨을 자리다.
_COMBINE_NOTE_TAIL = " 의 단순 평균입니다 — 그 점수가 모두 있는 종목만 순위에 넣습니다."


def combine_note(slugs):
    """종합 점수의 합성 방식 문구. 이름은 slugs 에서 뽑는다."""
    return "종합 점수는 " + " · ".join(_slug_name(s) for s in slugs) + _COMBINE_NOTE_TAIL

# "적중률" 은 이 저장소에서 두 가지를 가리킨다. 성적표가 내는 것은 IC 쪽인데
# 라벨이 그냥 "적중률" 이면 구독자는 '매수한 종목이 올랐나' 로 읽는다. 실측
# 2026-08-20 기준 21일 지평에서 그 둘은 56.5% 대 50.2% 로 6.3%p 벌어져 있고,
# 유리한 쪽이 IC 다. 좋아 보이는 쪽을 모호한 이름으로 내보내지 않는다.
# 문구에 매수·매도·목표가·추천을 쓰지 않는다(이 채널의 금지어). 뜻은 "개별
# 종목의 방향이 맞은 비율이 아니다" 로 충분히 전달된다.
HIT_RATE_NOTE = ("IC 적중률은 그날 점수 순위가 실제 수익률 순위와 같은 방향이었던 "
                 "날의 비율입니다 — 개별 종목의 방향이 맞은 비율이 아닙니다.")


def _slug_name(slug):
    return SLUG_NAMES.get(slug, slug)


def publishable(slugs):
    """사람에게 보여줄 슬러그만 — 이름을 아는 것들.

    발행문과 앱 화면이 **같은 이 함수**를 쓴다. 예전에는 발행 경로에만 있었고,
    그래서 백필에 섞인 측정용 슬러그 `quant_pit`(501일)이 성적표 화면에는
    애널리스트 한 줄로 그대로 떴다 — 21일 지평에서 t=1.92 · 적중률 63.4% 로
    표에서 제일 좋아 보이는 자리였다. 가드는 한 곳이 갖는다.

    성적표는 `score_analysts` 가 **기록에서 찾아낸** 슬러그를 그대로 돈다.
    측정용 슬러그(`quant_pit`)를 백필에 채우면 그날 밤 성적표가 판정 전의
    측정 결과를 그대로 채널에 내보낸다 — 사전 등록의 봉인이 발행으로 새는
    자리다. 이름을 아는 슬러그만 낸다. 새 애널리스트를 실제로 붙일 때는
    SLUG_NAMES 에 이름을 넣는 것이 그 발행 스위치가 된다.
    """
    return [s for s in sorted(slugs) if s in SLUG_NAMES]


def _missing_slug_lines(missing_slugs):
    """종합 점수에서 빠진 슬러그 공개 문구 — 조용히 빼지 않는다.

    두 발행문(성적표·오늘의 기록) 모두에서 쓴다. 슬러그를 빼고 두 개만
    보여주면 성적표/기록이 완전한 것처럼 보인다.
    """
    return [f"※ {_slug_name(slug)}는 종합 점수에 아직 안 들어감 — "
            f"{MISSING_REASON.get(slug, '기록 없음')}"
            for slug in missing_slugs]


# **이 지평에서 실제로 채점된 날** 의 구성이다. 로그에 쌓인 일수가 아니다 —
# 21·63일 지평은 선행 구간이 아직 안 지난 최근 기록을 통째로 버리므로, 로그로
# 세면 실기록이 표본에 들어간 것처럼 보인다. 실측 2026-08-20: 21·63일 지평의
# 실기록 채점일은 0 인데 로그 기준으로는 18일이었다. 이 채널이 파는 것이
# "예측 기록과 사후 채점" 인 이상, 예측한 적 없는 날로만 채운 성적을 그렇게
# 부르면 안 된다.
BACKFILL_NOTE = (
    "※ 이 지평에서 채점된 {total}일 중 {backfill}일은 과거 봉으로 되돌려 "
    "계산한 재구성분입니다 (실기록 {live}일). 재구성분은 오늘 살아남은 종목만 "
    "담고 있어 생존자 편향이 남아 있고, IC 가 실제보다 좋게 나옵니다."
)


# 판정선을 문장으로 옮긴 것. t 만 찍고 끝내면 그 숫자가 선을 넘었는지 구독자가
# 알 방법이 없다 — 실측 2026-08-22 기준 네 지평의 t 는 -0.15 / -0.20 / +0.22 /
# -0.23 으로 전부 판정선의 10분의 1 근처인데, 발행문은 "t=0.224 · 유효표본 47.3"
# 으로 끝나고 옆줄에는 "IC 적중률 56.2%" 가 붙어 있었다. 화면에는 '판정' 컬럼과
# 문턱 캡션이 둘 다 있고 발행문에만 없었다 — 가드가 한 경로에만 걸린 자리이자,
# 정확한 쪽이 안에 남고 모호한 쪽이 바깥으로 나간 자리다.
# 이 채널의 유일한 차별화가 "못 찾으면 못 찾았다고 쓰는 것" 인 이상, 빠뜨린
# 문장 하나가 곧 분식이다.
DECISION_LINE = "  → 판정선(|t| ≥ {t:.1f} · 유효표본 ≥ {n}) {verdict}"
DECISION_PASS = "통과"
DECISION_FAIL = "미달 — 예측력을 아직 찾지 못했습니다"


def decision_line(t_stat, effective_n):
    """판정선 대비 결과 한 줄. 화면의 '판정' 컬럼과 같은 두 조건을 쓴다."""
    passed = (t_stat is not None
              and abs(t_stat) >= DECIDE_T_THRESHOLD
              and float(effective_n) >= DECIDE_MIN_EFFECTIVE_N)
    return DECISION_LINE.format(t=DECIDE_T_THRESHOLD, n=DECIDE_MIN_EFFECTIVE_N,
                                verdict=DECISION_PASS if passed else DECISION_FAIL)


def build_scorecard_message(horizon, stats, missing_slugs, sample_mix=None,
                            *, combine_slugs):
    """N거래일 지평 성적표. stats 는 score_analysts() 의 반환값.

    지평은 **거래일**이다 — build_forward_returns 가 봉을 센다. 화면은 이미
    "21거래일 앞선 수익률 기준" 이라고 정확히 쓰고 있었는데 발행문만 "일"
    이었다. 실측(패널 1,607봉): 21거래일 = 달력 중위 30일, 63거래일 = 91일
    (최대 97일). 63일 지평은 44% 빠르게 채점되는 것처럼 적히고 있었다.

    sample_mix — **이 지평에서 실제로 채점된 날**의 {"live", "backfill"} 구성
    (analyst_scorecard.scored_dates 로 센다). 로그에 쌓인 일수를 넣으면 안 된다
    — 21·63일 지평은 최근 기록을 통째로 버리므로 실기록이 0 일 때도 18일이라고
    적히고, 그러면 이 채널이 "예측 기록과 사후 채점을 공개한다" 고 해 놓고
    예측한 적 없는 날만으로 만든 성적을 그렇게 부르게 된다.
    """
    lines = [f"📊 {horizon}거래일 지평 성적표", ""]

    for slug in publishable(stats):
        s = stats[slug]
        effective_n = float(s.get("effective_n") or 0)
        lines.append(f"*{_slug_name(slug)}*")
        lines.append(f"  평균 IC {s['mean_ic']:+.4f} · IC 적중률 {s['hit_rate']:.1f}%")

        t_stat = s.get("t_stat")
        if effective_n < MIN_EFFECTIVE_N or t_stat is None:
            lines.append(
                f"  판정 표본 n={s['n']} (유효 {effective_n:.1f}) — 통계적 판단 불가")
        else:
            lines.append(f"  t={t_stat} · 유효표본 {effective_n:.1f}")
        lines.append(decision_line(t_stat, effective_n))
        lines.append("")

    lines.extend(_missing_slug_lines(missing_slugs))
    if sample_mix and sample_mix.get("backfill"):
        live = sample_mix.get("live", 0)
        lines.append(BACKFILL_NOTE.format(
            total=sample_mix["backfill"] + live,
            backfill=sample_mix["backfill"], live=live))

    lines.append("")
    lines.append(combine_note(combine_slugs))
    lines.append(HIT_RATE_NOTE)
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_record_message(date_str, regime, top_by_slug, missing_slugs,
                         tie_notes=None, dropped=0, *, horizons, combine_slugs):
    """오늘의 예측 기록. top_by_slug 는 {slug: [(ticker, score), ...]}.

    horizons — 이 기록이 채점될 지평. 문장에 박아 두면 지평이 늘어도 안 따라
    온다. 실제로 1일 지평이 HORIZONS 에 들어간 뒤에도 이 문장은 "5·21·63일"
    이라고 나갔고, 그 사이 1일 성적표가 8회 발행됐다(2026-08-11~19).
    기본값을 두지 않는 이유도 같다 — 기본값은 하드코딩이 숨을 자리다.
    **거래일**이라고 쓴다 — 채점은 봉을 센다. 63거래일은 달력으로 중위 91일
    (최대 97일)이라, "63일 뒤" 는 44% 빠르게 채점되는 것처럼 읽힌다.

    combine_slugs — 종합 점수를 만든 슬러그. 같은 이유로 기본값이 없다.

    missing_slugs 는 build_scorecard_message 와 같은 이유로 필요하다 —
    이 메시지는 매 영업일 나가고 구독자가 실제로 보는 것은 이쪽이다.
    슬러그를 조용히 빼고 두 개만 보여주면 기록이 완전한 것처럼 보인다.

    tie_notes({slug: 잘린 동점 종목 수})가 있으면 그 사실을 함께 적는다.
    경계 점수에서 잘린 종목이 있으면 보이는 목록은 순위가 아니라 동점
    무리의 임의 부분집합이다. 밝히지 않으면 순위가 아닌 것을 순위처럼
    보여주게 된다.

    dropped 는 한쪽 점수가 없어 종합에서 빠진 종목 수다. 0 이 아니면
    밝힌다 — 종목이 조용히 사라지면 순위가 무엇을 대상으로 매겨졌는지
    알 수 없다.
    """
    tie_notes = tie_notes or {}
    lines = [f"🧬 {date_str} 예측 기록 (국면: {regime})", ""]

    for slug in publishable(top_by_slug):
        entries = top_by_slug[slug]
        if not entries:
            continue
        lines.append(f"*{_slug_name(slug)}* 상위 {len(entries)}")
        for ticker, score in entries:
            lines.append(f"  {ticker} {score:.1f}")
        cut = tie_notes.get(slug, 0)
        if cut:
            lines.append(f"  ↳ {entries[-1][1]:.1f}점 동점 {cut}종목이 더 "
                         f"있습니다 — 티커순으로 잘랐습니다")
        lines.append("")

    lines.extend(_missing_slug_lines(missing_slugs))
    if dropped:
        lines.append(f"※ {dropped}종목은 한쪽 점수가 없어 종합에서 빠졌습니다")

    lines.append("")
    lines.append(combine_note(combine_slugs))
    lines.append("이 기록은 "
                 + "·".join(str(h) for h in horizons) + "거래일 뒤 채점됩니다.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
