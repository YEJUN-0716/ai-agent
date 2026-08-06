"""성적표 공개 채널 발행기 — 채점 후 발행. 기록은 하지 않는다.

기록기(signal_worker --record-only)와 이 워커는 워크플로가 다르다. 기록이
발송에 묶여 있어서, 알파가 없어 매수 알림을 끄자 성적표 재료까지 같이
끊긴 것이 이 분리의 이유다.

성적을 파일에 쌓지 않는다. analyst_log 와 가격이 유일한 진실이고 성적은
그것의 함수다. 매번 전체를 다시 계산한다 — 기록이 하루 한 줄이라 비용이
무시할 수준이다. 저장하는 것은 발행 이력뿐이다.
"""
import os
import sys
from datetime import datetime, timedelta

import requests

from modules import (analyst_log, analyst_scorecard, price_panel,
                     publish_log, scorecard_message)

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHANNEL_ID = os.environ.get("TELEGRAM_PUBLIC_CHANNEL_ID", "")

# 가격 패널의 시작점은 고정 일수가 아니라 기록의 첫 날짜에서 역산한다
# (건별 이유는 main() 참고). 이 여유분은 첫 기록일 이전으로 얼마나 더
# 당겨올지를 정한다 — 연휴 등으로 그 날짜 자체에 종가가 없어도
# "그 날짜 이하 마지막 종가"를 찾을 수 있게, 있을 법한 연휴보다 넉넉히 둔다.
WARMUP_DAYS = 14

# 발행 노출 종목 수. 채점은 전 종목으로 하고 노출만 줄인다.
TOP_N = 5

# 아직 기록하지 않는 슬러그 — 조용히 빼지 않고 발행문에 사유를 밝힌다.
MISSING_SLUGS = ["quant"]

# 종합 점수에 들어가는 애널리스트와, 그 결과를 담을 슬러그 이름.
#
# 단순 평균(50:50)인 이유: 실측 성적(IC) 표본이 아직 없다(첫 5일 판정이
# 2026-07-30 도래, n=1). 유일한 대안인 analyst_weights.load_analyst_weights()
# 는 실측이 아니라 ic_weights.json 의 팩터 IC 를 대리 지표로 쓰는데, 그
# 팩터들이 바로 |ICIR| < 0.1 로 판정돼 시그널 봇을 끄게 만든 것들이다.
# 근거 없는 가중치보다 균등이 정직하다. IC 가 쌓이면 그때 근거를 갖고 바꾼다.
# 산식은 analyst_scorecard 가 소유한다 — 스캘핑 성적표도 같은 규칙으로
# 종합해야 하므로, 규칙이 두 벌이 되면 언젠가 갈라진다.
COMBINE_SLUGS = analyst_scorecard.COMBINE_SLUGS
COMBINED_SLUG = analyst_scorecard.COMBINED_SLUG
combined_day = analyst_scorecard.combined_day
combined_days = analyst_scorecard.combined_days


def dropped_ticker_count(day, combined):
    """종합에서 빠진 종목 수 — 한쪽 점수가 없어 제외된 것.

    0 이 아니면 발행문에 밝힌다. 종목이 조용히 사라지면 순위가 무엇으로
    매겨졌는지 알 수 없게 된다.
    """
    return len(day.get("scores", {})) - len(combined.get("scores", {}))


def send_tg(msg):
    """공개 채널로 발송한다. 개인 채팅(TELEGRAM_CHAT_ID)과 섞지 않는다.

    requests 의 연결 오류 메시지에는 요청 URL 전체(봇 토큰 포함)가 그대로
    박힌다. 이 저장소는 공개이고 워크플로 로그도 공개로 읽히므로, 그
    예외를 그대로 print 하면 토큰이 로그에 남는다. GitHub 의 시크릿
    마스킹은 2차 방어일 뿐 유일한 방어여선 안 된다 — 예외 타입과 짧은
    메시지만 남기고 원문은 버린다.
    """
    if not TG_TOKEN or not TG_CHANNEL_ID:
        print("[TG] 환경변수 없음 — 발송 생략")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHANNEL_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code == 400 and "parse entities" in resp.text:
            resp = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHANNEL_ID, "text": msg},
                timeout=10,
            )
    except requests.exceptions.RequestException as e:
        print(f"[TG 오류] 요청 실패 — {type(e).__name__}")
        return False

    ok = resp.status_code == 200
    print("[TG] 발송 성공" if ok else f"[TG 오류] {resp.text}")
    return ok


def new_horizons(stats_by_horizon, root=publish_log.LOG_DIRNAME):
    """표본이 늘어난 지평만 돌려준다 — 같은 판정을 두 번 보내지 않는다."""
    out = []
    for horizon in sorted(stats_by_horizon):
        stats = stats_by_horizon[horizon]
        n = max((s.get("n", 0) for s in stats.values()), default=0)
        if n <= 0:
            continue
        last = publish_log.last_published_n(horizon, root=root)
        if last is None or n > last:
            out.append(horizon)
    return out


def top_by_slug(day, limit=TOP_N):
    """그날 기록에서 슬러그별 상위 종목 — [(ticker, score), ...]."""
    buckets = {}
    for ticker, per_analyst in day.get("scores", {}).items():
        for slug, score in per_analyst.items():
            buckets.setdefault(slug, []).append((ticker, float(score)))

    # (-score, ticker): 점수만으로 정렬하면 동점일 때 dict 삽입 순서에 기대게
    # 된다. ict 는 100.0 에서 자주 여러 종목이 동점(예: 2026-07-23, 19종목)
    # 이라 티커를 2차 키로 둬야 같은 로그가 항상 같은 목록을 낸다.
    return {slug: sorted(rows, key=lambda r: (-r[1], r[0]))[:limit]
            for slug, rows in buckets.items()}


def cut_tie_counts(day, top):
    """표시에서 잘려나간 동점 종목 수 — {slug: n}. 잘린 게 없으면 키가 없다.

    상위 N 으로 자를 때 경계 점수가 그 아래로도 이어지면, 보이는 목록은
    순위가 아니라 동점 무리에서 임의로 고른 부분집합이다. ict 는 100.0 에서
    자주 포화되고(2026-07-23 기준 19종목) 그때 "상위 5" 는 19개 중 5개를
    티커순으로 자른 것에 지나지 않는다.

    몇 개가 같은 점수로 잘렸는지 밝히지 않으면 순위가 아닌 것을 순위처럼
    보여주게 된다 — 음수 IC 를 숨기는 것과 같은 종류의 분식이다.
    """
    out = {}
    scores = day.get("scores", {})
    for slug, rows in top.items():
        if not rows:
            continue
        cutoff = rows[-1][1]
        total_at_cutoff = sum(
            1 for per_analyst in scores.values()
            if slug in per_analyst and float(per_analyst[slug]) == cutoff)
        shown_at_cutoff = sum(1 for _, score in rows if score == cutoff)
        if total_at_cutoff > shown_at_cutoff:
            out[slug] = total_at_cutoff - shown_at_cutoff
    return out


def main():
    days = analyst_log.load_days()
    if not days:
        print("기록이 없다 — 발행할 것이 없다.", file=sys.stderr)
        return 1

    today = datetime.now().strftime("%Y-%m-%d")
    latest = days[-1]
    log_date = latest.get("date", "")

    # 오늘의 기록은 로그 날짜로 중복을 막는다 — n 이 없으므로 new_horizons
    # 의 표본 비교 방식을 못 쓴다. 수동 workflow_dispatch 로 스모크 테스트할
    # 때마다 재발송되던 것, 그리고 기록기가 밀리거나 실패한 날 어제 날짜의
    # 종목을 다시 내보내던 것 둘 다 이걸로 막는다.
    if publish_log.last_published_record_date() == log_date:
        print(f"오늘의 기록 이미 발행됨 (log_date={log_date}) — 발송 생략")
    else:
        latest_combined = combined_day(latest)
        top = top_by_slug(latest_combined)
        if not send_tg(scorecard_message.build_record_message(
                log_date, latest.get("regime", "unknown"),
                top, MISSING_SLUGS, cut_tie_counts(latest_combined, top),
                dropped_ticker_count(latest, latest_combined))):
            print("오늘의 기록 발송 실패", file=sys.stderr)
            return 1
        publish_log.record_published_record(today, log_date)
        print(f"오늘의 기록 발행 (log_date={log_date})")

    # 채점도 종합 점수로 한다. score_analysts 는 scores 안의 슬러그를 그대로
    # 집계하므로, 변환한 기록을 넘기면 {"combined": {...}} 하나만 돌아온다 —
    # 채점 산식은 건드릴 필요가 없다.
    days = combined_days(days)
    if not days:
        print("종합 점수를 낼 수 있는 기록이 없다.", file=sys.stderr)
        return 1

    tickers = sorted({t for d in days for t in d.get("scores", {})})
    dates = [d["date"] for d in days]

    # 고정 일수(예전 PANEL_DAYS=400) 대신 기록의 첫 날짜에서 역산한다.
    # 고정폭이면 로그가 그 폭을 넘어서는 순간 오래된 기록일이 패널
    # 밖으로 밀려나 build_forward_returns 가 그 날들을 조용히 버리고,
    # n 이 늘지 않아(줄지도 모르고) 성적표 발행이 영구히 멈춘다 —
    # 그린 워크플로 위에서 티 안 나게.
    earliest = datetime.strptime(dates[0], "%Y-%m-%d")
    end = datetime.now()
    try:
        # min_trading_days 를 낮추는 이유: 기본값 80거래일은 400일 패널에서
        # 신규상장 종목을 걸러내려고 만든 값이다. 여기 구간은 기록 첫날에서
        # 역산하므로 기록이 쌓이기 전에는 영업일 수십 일밖에 안 되고, 그러면
        # 전 종목이 문턱에 걸려 "확보율 0/276" 으로 매일 죽는다 (2026-07-28).
        # 채점에 필요한 것은 기록일 종가와 그 며칠 뒤 종가뿐이고, 선행 구간이
        # 모자란 종목은 build_forward_returns 가 알아서 뺀다.
        prices, _ = price_panel.load_panel(
            tickers, earliest - timedelta(days=WARMUP_DAYS), end,
            min_trading_days=price_panel.MIN_TRADING_DAYS_SCORING)
    except Exception as e:
        print(f"가격 패널 로드 실패 — 채점 불가: {e}", file=sys.stderr)
        return 1

    stats_by_horizon = {}
    for horizon in analyst_scorecard.HORIZONS:
        fwd = analyst_scorecard.build_forward_returns(prices, dates, horizon)
        stats_by_horizon[horizon] = analyst_scorecard.score_analysts(
            days, fwd, horizon)

    for horizon in new_horizons(stats_by_horizon):
        stats = stats_by_horizon[horizon]
        if not send_tg(scorecard_message.build_scorecard_message(
                horizon, stats, MISSING_SLUGS)):
            print(f"{horizon}일 성적표 발송 실패", file=sys.stderr)
            return 1
        n = max(s.get("n", 0) for s in stats.values())
        publish_log.record_published(today, horizon, n)
        print(f"{horizon}일 성적표 발행 (n={n})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
