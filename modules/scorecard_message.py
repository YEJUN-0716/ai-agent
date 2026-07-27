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
    "chart": "차트+파동+모멘텀",
    "quant": "퀀트+재무",
    "ict":   "ICT+CRT",
}

MISSING_REASON = {"quant": "일별 펀더멘털 수집 미구축"}


def _slug_name(slug):
    return SLUG_NAMES.get(slug, slug)


def build_scorecard_message(horizon, stats, missing_slugs):
    """N일 지평 성적표. stats 는 score_analysts() 의 반환값."""
    lines = [f"📊 {horizon}일 지평 성적표", ""]

    for slug in sorted(stats):
        s = stats[slug]
        effective_n = float(s.get("effective_n") or 0)
        lines.append(f"*{_slug_name(slug)}*")
        lines.append(f"  평균 IC {s['mean_ic']:+.4f} · 적중률 {s['hit_rate']:.1f}%")

        if effective_n < MIN_EFFECTIVE_N:
            lines.append(
                f"  판정 표본 n={s['n']} (유효 {effective_n:.1f}) — 통계적 판단 불가")
        else:
            lines.append(f"  t={s['t_stat']} · 유효표본 {effective_n:.1f}")
        lines.append("")

    for slug in missing_slugs:
        reason = MISSING_REASON.get(slug, "기록 없음")
        lines.append(f"※ {_slug_name(slug)}는 아직 기록하지 않음 — {reason}")

    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_record_message(date_str, regime, top_by_slug):
    """오늘의 예측 기록. top_by_slug 는 {slug: [(ticker, score), ...]}."""
    lines = [f"🧬 {date_str} 예측 기록 (국면: {regime})", ""]

    for slug in sorted(top_by_slug):
        entries = top_by_slug[slug]
        if not entries:
            continue
        lines.append(f"*{_slug_name(slug)}* 상위 {len(entries)}")
        for ticker, score in entries:
            lines.append(f"  {ticker} {score:.1f}")
        lines.append("")

    lines.append("이 기록은 5·21·63일 뒤 채점됩니다.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
