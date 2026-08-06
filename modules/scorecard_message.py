"""발행 메시지 조립 — 숫자를 문장으로 바꾼다. 계산도 네트워크도 없다.

성적을 좋게 보이게 하는 장치를 두지 않는다. 음수 IC 는 음수로 쓰고, 표본이
모자라면 모자라다고 쓴다. 이 채널의 유일한 차별화가 그것이다.

매수·매도·목표가 표현을 쓰지 않는다 — 점수와 순위, 사후 채점만 발행한다.
"""

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
}

# 퀀트+재무는 2026-08-07 부터 기록한다(signal_worker.record_analyst_scores).
# 다만 이 채널의 종합 점수는 여전히 차트+ICT 단순평균이다 — 셋을 섞을 가중치를
# 정할 실측 IC 표본이 아직 없다(근거 없는 가중치보다 균등이 정직하다는 결정).
# 앱 화면의 총괄 판정은 ic_weights 기반 블렌드라 이 채널과 다른 값이다.
MISSING_REASON = {"quant": "실측 IC 표본이 없어 가중치 근거가 아직 없음"}

# 종합 점수가 무엇의 평균인지 밝힌다. 합성 방식을 감추면 순위의 의미를
# 알 수 없고, 나중에 가중치를 바꿨을 때 구독자가 알아챌 방법도 없다.
COMBINE_NOTE = ("종합 점수는 차트+파동+모멘텀과 ICT+CRT 의 단순 평균입니다 "
                "— 두 점수가 모두 있는 종목만 순위에 넣습니다.")


def _slug_name(slug):
    return SLUG_NAMES.get(slug, slug)


def _missing_slug_lines(missing_slugs):
    """종합 점수에서 빠진 슬러그 공개 문구 — 조용히 빼지 않는다.

    두 발행문(성적표·오늘의 기록) 모두에서 쓴다. 슬러그를 빼고 두 개만
    보여주면 성적표/기록이 완전한 것처럼 보인다.
    """
    return [f"※ {_slug_name(slug)}는 종합 점수에 아직 안 들어감 — "
            f"{MISSING_REASON.get(slug, '기록 없음')}"
            for slug in missing_slugs]


def build_scorecard_message(horizon, stats, missing_slugs):
    """N일 지평 성적표. stats 는 score_analysts() 의 반환값."""
    lines = [f"📊 {horizon}일 지평 성적표", ""]

    for slug in sorted(stats):
        s = stats[slug]
        effective_n = float(s.get("effective_n") or 0)
        lines.append(f"*{_slug_name(slug)}*")
        lines.append(f"  평균 IC {s['mean_ic']:+.4f} · 적중률 {s['hit_rate']:.1f}%")

        t_stat = s.get("t_stat")
        if effective_n < MIN_EFFECTIVE_N or t_stat is None:
            lines.append(
                f"  판정 표본 n={s['n']} (유효 {effective_n:.1f}) — 통계적 판단 불가")
        else:
            lines.append(f"  t={t_stat} · 유효표본 {effective_n:.1f}")
        lines.append("")

    lines.extend(_missing_slug_lines(missing_slugs))

    lines.append("")
    lines.append(COMBINE_NOTE)
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_record_message(date_str, regime, top_by_slug, missing_slugs,
                         tie_notes=None, dropped=0):
    """오늘의 예측 기록. top_by_slug 는 {slug: [(ticker, score), ...]}.

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

    for slug in sorted(top_by_slug):
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
    lines.append(COMBINE_NOTE)
    lines.append("이 기록은 5·21·63일 뒤 채점됩니다.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
