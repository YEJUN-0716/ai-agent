"""가계부 반복 항목 자동 입력 — 매달 1일, 없으면 넣는다.

노션에는 "매달 반복되는 행" 이 없다. 2026-09-02 에 강지 구독(월 990원)을 넣으면서
2027-01 까지 4줄을 손으로 박았는데, 그 뒤로는 아무도 안 만든다. 손으로 채우는 표는
언젠가 반드시 빈다 — 그날 가계부는 틀린 채로 조용히 돌아간다.

**화면은 노션이 그리고, 반복 노동만 여기서 한다.** 자체 가계부 앱을 만들지 않은
이유는 같은 날 비서를 폐기한 이유와 같다 — 상시 켜둬야 하는 프로그램은 꺼진다.
노션은 남이 24시간 돌려주는 서버다.

**두 번 넣지 않는다.** 넣기 전에 그 달에 같은 이름이 있는지 먼저 묻는다. 크론이
두 번 돌든, 사장님이 이미 손으로 적었든, 결과는 같다.

사용법
  python tools/notion_recurring.py            # 이번 달 것을 채운다
  python tools/notion_recurring.py --dry-run  # 무엇을 넣을지만 보여준다

환경변수: NOTION_TOKEN (내부 통합 토큰. 통합을 「살림」 페이지에 연결해야 보인다)
"""

from __future__ import annotations

import calendar
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"
가계부 = "ae679f91-f6b6-4e87-b049-6ab0180dc68f"  # 데이터 소스 ID

# 매달 들어가는 것들. 새 구독이 생기면 여기 한 줄 추가한다.
# `일` 은 결제일 — 그 달에 없는 날짜(2월 30일)면 말일로 당긴다.
RECURRING = [
    {"항목": "강지 구독", "금액": 990, "분류": "문화", "구분": "지출",
     "일": 2, "메모": "월 구독료 (자동 입력)"},
]


def 결제일(year: int, month: int, day: int) -> date:
    """그 달에 없는 날이면 말일로 당긴다 — 31일 결제가 2월에 사라지지 않게."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _call(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def 이미_있나(항목: str, 첫날: date, 말일: date) -> bool:
    res = _call(f"data_sources/{가계부}/query", {"filter": {"and": [
        {"property": "항목", "title": {"equals": 항목}},
        {"property": "날짜", "date": {"on_or_after": 첫날.isoformat()}},
        {"property": "날짜", "date": {"on_or_before": 말일.isoformat()}},
    ]}})
    return bool(res.get("results"))


def 넣기(item: dict, 날짜: date) -> None:
    _call("pages", {
        "parent": {"type": "data_source_id", "data_source_id": 가계부},
        "properties": {
            "항목": {"title": [{"text": {"content": item["항목"]}}]},
            "금액": {"number": item["금액"]},
            "날짜": {"date": {"start": 날짜.isoformat()}},
            "분류": {"select": {"name": item["분류"]}},
            "구분": {"select": {"name": item["구분"]}},
            "메모": {"rich_text": [{"text": {"content": item["메모"]}}]},
        },
    })


def main() -> int:
    if not os.environ.get("NOTION_TOKEN"):
        print("NOTION_TOKEN 이 없다. 노션 내부 통합 토큰을 환경변수(러너는 시크릿)에 넣어야 한다.",
              file=sys.stderr)
        return 1

    오늘 = date.today()
    첫날 = date(오늘.year, 오늘.month, 1)
    말일 = date(오늘.year, 오늘.month, calendar.monthrange(오늘.year, 오늘.month)[1])
    dry = "--dry-run" in sys.argv

    for item in RECURRING:
        날짜 = 결제일(오늘.year, 오늘.month, item["일"])
        if 이미_있나(item["항목"], 첫날, 말일):
            print(f"건너뜀 {item['항목']} — {오늘:%Y-%m} 에 이미 있다")
            continue
        if dry:
            print(f"[예정] {item['항목']} {item['금액']}원 → {날짜}")
            continue
        넣기(item, 날짜)
        print(f"추가 {item['항목']} {item['금액']}원 → {날짜}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        # 노션은 실패 이유를 본문에 담아 보낸다. 삼키면 원인 찾는 데 며칠 걸린다
        # (토스 403 이 그렇게 12일을 먹었다).
        print(f"노션 API {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        raise SystemExit(1)
