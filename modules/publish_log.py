"""발행 이력 — data/publish_log/published_YYYY.jsonl.

성적은 저장하지 않는다. analyst_log 와 가격이 유일한 진실이고 성적은
그것의 함수다. 중간 결과를 저장하면 원본과 어긋날 자리만 생긴다.

여기 남기는 것은 "무엇을 이미 보냈는가" 하나뿐이다. 지평별 마지막 표본 수를
비교해, 늘었으면 새로 판정된 날이 생긴 것으로 본다.
"""
import json
import os

# 발행 기록은 data/publish_log 에만 저장된다. analyst_log 와 분리해야 한다.
# analyst_log.load_days() 는 "*.jsonl" 만 필터링하므로 (이름 프리픽스 체크 없음),
# 같은 디렉토리에 published_YYYY.jsonl 을 넣으면 load_days() 가 발행 기록을
# 읽고 "date" 키 없이 정렬해 days[0]['date'] 에서 KeyError 를 낸다.
LOG_DIRNAME = os.path.join("data", "publish_log")
FILE_PREFIX = "published_"


def _year_path(root, year):
    return os.path.join(str(root), f"{FILE_PREFIX}{year}.jsonl")


def _read_all(root):
    root = str(root)
    if not os.path.isdir(root):
        return []

    records = []
    for name in sorted(os.listdir(root)):
        if not (name.startswith(FILE_PREFIX) and name.endswith(".jsonl")):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue      # 깨진 줄은 버린다 — 되살릴 방법이 없다
    return records


def last_published_n(horizon, root=LOG_DIRNAME):
    """그 지평에서 마지막으로 발행한 표본 수. 발행한 적 없으면 None."""
    horizon = int(horizon)
    ns = [r["n"] for r in _read_all(root)
          if r.get("horizon") == horizon and isinstance(r.get("n"), int)]
    return max(ns) if ns else None


def record_published(date_str, horizon, n, root=LOG_DIRNAME):
    """발행 1건을 기록한다. 한 줄이 발행 1건이다."""
    path = _year_path(root, date_str[:4])
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    record = {"published_at": date_str, "horizon": int(horizon), "n": int(n)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
