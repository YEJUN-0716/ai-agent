#!/usr/bin/env python
"""일회성 데이터 수술 — 2026-07-23~08-07 기록의 날짜를 실제 장 날짜로 되돌린다.

**한 번만 돌린다.** 이미 돌린 뒤에 또 돌리면 아무것도 안 바뀐다(멱등).
스크립트를 남기는 이유는 기록을 손으로 고친 흔적을 되짚을 수 있게 하기
위해서다 — 되짚을 수 없는 데이터 수술은 안 한 것만 못하다.

## 무슨 일이 있었나

signal_worker 가 기록 날짜를 `datetime.now()` — 러너의 UTC 벽시계 — 로 찍었다.
크론은 23:00 UTC 지만 GitHub 은 이걸 최대 한 시간 넘게 밀어, 실제 실행은
23:57~01:41 UTC 사이에 흩어졌다. 자정을 넘긴 날은 **그 장의 기록이 다음
날짜로** 저장됐다.

증거는 기록 안에 있다. 15분봉 줄 하나가 `date=2026-08-07` 인데
`asof=2026-08-06T19:45` 다 — 봉은 8/6 인데 날짜는 8/7 이다. 그리고 일봉
기록에 토요일(2026-07-25·08-01)이 두 줄 있다. 미국 장은 토요일에 안 연다.

아래 표는 실행 이력(gh run list)의 시작 시각 + 소요 시간으로 각 줄이
**어느 실행에서 나왔는지** 를 맞춘 것이다. 00:0x UTC 에 돈 실행은 그 시점에
존재하지도 않는 그날 봉을 담을 수 없다 — 담긴 것은 언제나 직전 장이다.

## 잃은 것

같은 날짜로 두 장이 겹치면 append_day 가 앞의 것을 대체했다. 그래서
**2026-07-27 과 08-05 장은 사라졌다.** 되살릴 수 없다 — 차트·ICT 점수는
과거 봉으로 재계산할 수 있지만 퀀트는 그날 조회한 yfinance .info 라
시점 데이터고, 지금 다시 받으면 다른 값이 나온다.

2026-08-06 일봉 줄(5종목)은 수동 workflow_dispatch 스모크 테스트
(UNIVERSE=AAPL,MSFT,NVDA,AMZN,GOOGL)다. 이게 같은 날짜의 276종목 기록을
덮어써서 08-05 장을 지웠다. 테스트 줄은 버린다.
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 현재 날짜 → 실제 장 날짜. None 은 줄을 버린다는 뜻.
DAILY = {
    # 7/23 11:30 ET 수동 실행(장중 스냅샷). 같은 장의 장마감 기록이 아래
    # '2026-07-24' 줄로 남아 있어 중복이다 — 장마감 쪽을 남긴다.
    "2026-07-23": None,
    "2026-07-24": "2026-07-23",   # 실행 07-23T23:59 UTC = 19:59 ET 07/23
    "2026-07-25": "2026-07-24",   # 실행 07-25T00:02 UTC = 20:02 ET 07/24 (토요일 줄)
    "2026-07-28": "2026-07-28",   # 실행 07-28T23:57 — 같은 날짜의 07/27 장을 덮었다
    "2026-07-29": "2026-07-29",
    "2026-07-31": "2026-07-30",   # 실행 07-31T00:00 UTC = 20:00 ET 07/30
    "2026-08-01": "2026-07-31",   # 실행 08-01T00:01 UTC (토요일 줄)
    "2026-08-04": "2026-08-03",
    "2026-08-05": "2026-08-04",
    "2026-08-06": None,           # 5종목 스모크 테스트 — 08/05 장을 덮어썼다
    "2026-08-07": "2026-08-06",   # 실행 08-07T01:41 UTC = 21:41 ET 08/06
}

# 15분봉은 asof 가 기준봉을 들고 있어 추측이 필요 없다.
SCALP = {
    "2026-08-06": None,           # asof 16:15 UTC = 12:15 ET, 장중 5종목 테스트
    "2026-08-07": "2026-08-06",   # asof 2026-08-06T19:45 = 15:45 ET 08/06
}

PATHS = [
    (os.path.join(ROOT, "data", "analyst_log", "2026.jsonl"), DAILY),
    (os.path.join(ROOT, "data", "scalp_log", "2026.jsonl"), SCALP),
]

# 발행 이력의 log_date 도 같이 옮긴다. 안 옮기면 마지막 발행 날짜가 로그의
# 마지막 날짜보다 앞서서, 이미 보낸 기록을 텔레그램에 한 번 더 보낸다.
PUBLISH_PATH = os.path.join(ROOT, "data", "publish_log", "published_2026.jsonl")


def _load(path):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _save(path, records):
    with io.open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# 수술 여부를 가르는 표식. 수술 뒤에는 어느 로그에도 이 날짜가 남지 않는다
# (마지막 줄이 08-06 으로 내려간다). 표를 두 번 적용하면 '2026-07-23': None
# 이 멀쩡한 기록을 지우므로, 멱등성은 선택이 아니라 필수다.
ALREADY_DONE_IF_ABSENT = "2026-08-07"


def _needs_repair(records):
    return any(r.get("date") == ALREADY_DONE_IF_ABSENT for r in records)


def repair(path, mapping):
    records = _load(path)
    if not records:
        return f"{path}: 비어 있음, 건너뜀"
    if not _needs_repair(records):
        return f"{path}: 이미 수술됨, 건너뜀"

    out = []
    for rec in records:
        date = rec.get("date")
        if date not in mapping:
            out.append(rec)
            continue
        new = mapping[date]
        if new is None:
            continue
        rec["date"] = new
        out.append(rec)

    out.sort(key=lambda r: r.get("date", ""))
    dates = [r["date"] for r in out]
    if len(set(dates)) != len(dates):
        raise SystemExit(f"{path}: 수술 후 날짜가 겹친다. 표를 다시 본다: {dates}")

    _save(path, out)
    return f"{path}: {len(records)}줄 → {len(out)}줄 · {dates}"


def repair_publish_log():
    records = _load(PUBLISH_PATH)
    if not records:
        return f"{PUBLISH_PATH}: 비어 있음, 건너뜀"
    if not any(r.get("log_date") == ALREADY_DONE_IF_ABSENT for r in records):
        return f"{PUBLISH_PATH}: 이미 수술됨, 건너뜀"

    changed = 0
    for rec in records:
        new = DAILY.get(rec.get("log_date"), rec.get("log_date"))
        if new is not None and new != rec.get("log_date"):
            rec["log_date"] = new
            changed += 1
    if changed:
        _save(PUBLISH_PATH, records)
    return f"{PUBLISH_PATH}: log_date {changed}건 이동"


if __name__ == "__main__":
    for path, mapping in PATHS:
        print(repair(path, mapping))
    print(repair_publish_log())
    print("\n사라진 장: 2026-07-27, 2026-08-05 (되살릴 수 없음)")
    sys.exit(0)
