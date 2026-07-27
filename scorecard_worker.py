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

# 선행수익률 계산에 필요한 과거 구간. 최장 지평(63봉) + 여유.
PANEL_DAYS = 400

# 발행 노출 종목 수. 채점은 전 종목으로 하고 노출만 줄인다.
TOP_N = 5

# 아직 기록하지 않는 슬러그 — 조용히 빼지 않고 발행문에 사유를 밝힌다.
MISSING_SLUGS = ["quant"]


def send_tg(msg):
    """공개 채널로 발송한다. 개인 채팅(TELEGRAM_CHAT_ID)과 섞지 않는다."""
    if not TG_TOKEN or not TG_CHANNEL_ID:
        print("[TG] 환경변수 없음 — 발송 생략")
        return False

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

    return {slug: sorted(rows, key=lambda r: r[1], reverse=True)[:limit]
            for slug, rows in buckets.items()}


def main():
    days = analyst_log.load_days()
    if not days:
        print("기록이 없다 — 발행할 것이 없다.", file=sys.stderr)
        return 1

    latest = days[-1]
    if not send_tg(scorecard_message.build_record_message(
            latest.get("date", ""), latest.get("regime", "unknown"),
            top_by_slug(latest))):
        print("오늘의 기록 발송 실패", file=sys.stderr)
        return 1

    tickers = sorted({t for d in days for t in d.get("scores", {})})
    end = datetime.now()
    try:
        prices, _ = price_panel.load_panel(
            tickers, end - timedelta(days=PANEL_DAYS), end)
    except Exception as e:
        print(f"가격 패널 로드 실패 — 채점 불가: {e}", file=sys.stderr)
        return 1

    dates = [d["date"] for d in days]
    stats_by_horizon = {}
    for horizon in analyst_scorecard.HORIZONS:
        fwd = analyst_scorecard.build_forward_returns(prices, dates, horizon)
        stats_by_horizon[horizon] = analyst_scorecard.score_analysts(
            days, fwd, horizon)

    today = datetime.now().strftime("%Y-%m-%d")
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
