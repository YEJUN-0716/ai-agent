"""산출물 날짜 점검 — "성공했는데 아무것도 안 만든" 잡을 잡는다.

2026-09-02, 같은 모양의 사고를 하루에 셋 발견했다. 옵시디언 동기화는 매시간
`exit 0` 으로 성공하면서 2주 동안 옛 폴더를 읽고 있었고, 비서는 프로세스가 아예
없었고, 자체 러너는 돌면서 아무도 안 불렀다. **종료코드는 셋 다 정상이었다.**

그래서 잡의 성공 여부가 아니라 **산출물이 언제 갱신됐는지**를 본다. 잡이 죽든,
경로가 낡든, 조용히 빈손으로 끝나든 — 결과가 안 나오면 똑같이 걸린다.

날짜는 파일 mtime 이 아니라 `git log` 에서 읽는다. 체크아웃하면 mtime 은 전부
"방금"이 되기 때문이다. 그래서 러너에서는 `fetch-depth: 0` 이 필요하다.

사용법
  python tools/healthcheck.py              # 표 출력, 낡은 게 있으면 exit 1
  python tools/healthcheck.py --telegram   # 낡은 게 있을 때만 텔레그램 발송

환경변수: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (없으면 발송만 생략)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

# (경로, 최대 나이(일), 누가 만드는지)
#
# 나이 기준은 **주기보다 넉넉하게** 잡는다. 평일 잡은 금요일에 만든 걸 월요일
# 아침까지 들고 있어야 하고, 연휴도 있다. 좁게 잡으면 늑대가 하도 울어서 아무도
# 안 보게 된다 — 이 점검의 유일한 실패 방식이다.
CHECKS = [
    ("stock-analyzer/equity_log.json",        4,  "paper-trade-us (평일)"),
    ("stock-analyzer/signal_log.json",        4,  "paper-trade-us (평일)"),
    ("stock-analyzer/virtual_portfolio.json", 4,  "paper-trade-us (평일)"),
    ("stock-analyzer/data/analyst_log",       4,  "analyst-log (평일)"),
    ("stock-analyzer/data/publish_log",       4,  "scorecard-publish (평일)"),
    ("stock-analyzer/ic_weights.json",        10, "ic-update (주간)"),
    ("stock-analyzer/index_portfolio.json",   40, "index-autopilot (매월 1~8일)"),
    ("data/heartbeat.json",                   3,  "옵시디언 동기화 (자택 PC, 매시간)"),
]
# peak_prices.json 은 일부러 뺐다 — 신고가가 없으면 정상적으로 안 바뀐다.
# "안 바뀌는 게 정상"인 파일은 이 방식으로 감시할 수 없다.


def last_commit_at(path: str) -> datetime | None:
    """그 경로를 마지막으로 건드린 커밋 시각. 이력이 없으면 None."""
    out = subprocess.run(
        ("git", "log", "-1", "--format=%cI", "--", path),
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return datetime.fromisoformat(out) if out else None


def audit(now: datetime, ages: dict[str, datetime | None]) -> list[tuple]:
    """(경로, 나이(일), 기준, 잡) — 기준을 넘겼거나 이력이 없는 것만."""
    bad = []
    for path, max_days, job in CHECKS:
        at = ages.get(path)
        if at is None:
            bad.append((path, None, max_days, job))
            continue
        days = (now - at).total_seconds() / 86400
        if days > max_days:
            bad.append((path, days, max_days, job))
    return bad


def _line(path: str, days: float | None, max_days: int, job: str) -> str:
    aged = "이력 없음" if days is None else f"{days:.1f}일째 그대로"
    return f"· {path} — {aged} (기준 {max_days}일 · {job})"


def send_telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        print("[TG] 토큰/챗ID 없음 → 발송 생략")
        return
    body = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"[TG] 발송 {r.status}")


def main() -> int:
    now = datetime.now(timezone.utc)
    ages = {path: last_commit_at(path) for path, _, _ in CHECKS}
    bad = audit(now, ages)

    for path, _, _ in CHECKS:
        at = ages[path]
        mark = "✗" if any(b[0] == path for b in bad) else "✓"
        print(f"{mark} {path:<42} {at.isoformat() if at else '이력 없음'}")

    if not bad:
        print("\n전부 최신.")
        return 0

    text = f"⚠️ 산출물 점검 — 낡은 것 {len(bad)}건\n\n" + "\n".join(_line(*b) for b in bad)
    print("\n" + text)
    if "--telegram" in sys.argv:
        send_telegram(text)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
