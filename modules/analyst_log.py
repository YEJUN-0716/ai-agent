"""애널리스트 점수 일별 기록 — data/analyst_log/YYYY.jsonl.

한 줄에 하루치. 연도별로 파일을 나눠 한 파일이 무한히 커지지 않게 한다.
점수는 소수 1자리로 절삭한다 — 0.01 점 차이는 성적에 아무 의미가 없고
연 3MB 와 30MB 를 가른다.

값이 없는 애널리스트는 **키를 뺀다.** 중립값(50)으로 채우면 '계산 불가'가
'중립 판단'으로 성적에 섞인다 — ic_weights 의 12-1 모멘텀에 적용한 것과
같은 규칙이다.
"""
import json
import os

LOG_DIRNAME = os.path.join("data", "analyst_log")
SCORE_DECIMALS = 1


def _year_path(root, date_str):
    return os.path.join(str(root), f"{date_str[:4]}.jsonl")


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in (line.strip() for line in f) if ln]


def append_day(date_str, regime, scores, root=LOG_DIRNAME):
    """하루치 점수를 기록한다. 같은 날짜가 이미 있으면 대체한다.

    같은 날 스캔이 두 번 돌아도 줄이 겹치지 않아야 한다 — 겹치면 그 날이
    성적에 두 번 계산돼 표본이 부풀려진다.
    """
    path = _year_path(root, date_str)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    trimmed = {}
    for ticker, per_analyst in scores.items():
        row = {slug: round(float(v), SCORE_DECIMALS)
               for slug, v in per_analyst.items() if v is not None}
        if row:
            trimmed[ticker] = row

    record = {"date": date_str, "regime": regime, "scores": trimmed}

    kept = []
    for line in _read_lines(path):
        try:
            if json.loads(line).get("date") == date_str:
                continue
        except ValueError:
            continue          # 깨진 줄은 버린다 — 되살릴 방법이 없다
        kept.append(line)
    kept.append(json.dumps(record, ensure_ascii=False))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")


def load_days(root=LOG_DIRNAME, since=None):
    """기록을 날짜 오름차순으로 읽는다. since(YYYY-MM-DD) 이상만."""
    root = str(root)
    if not os.path.isdir(root):
        return []

    days = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".jsonl"):
            continue
        for line in _read_lines(os.path.join(root, name)):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if since and rec.get("date", "") < since:
                continue
            days.append(rec)

    return sorted(days, key=lambda d: d.get("date", ""))
