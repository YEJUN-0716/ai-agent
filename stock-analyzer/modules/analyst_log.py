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

# 과거 봉으로 되돌려 계산한 점수. **실기록이 아니다** — 그래서 파일을 가른다.
# 섞어 두면 "그날 화면에 뜬 점수" 와 "나중에 재구성한 점수" 가 한 파일 안에서
# 구별되지 않고, 성적표가 무엇을 잰 건지 아무도 말할 수 없게 된다.
# 재구성이 왜 가능한지·어디까지 같은지는 scripts/backfill_analyst_log.py 참고.
BACKFILL_DIRNAME = os.path.join("data", "analyst_log_backfill")

SCORE_DECIMALS = 1


def _year_path(root, date_str):
    return os.path.join(str(root), f"{date_str[:4]}.jsonl")


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in (line.strip() for line in f) if ln]


def trim_scores(scores):
    """기록에 남길 모양으로 다듬는다 — 소수 1자리 절삭, 값 없는 슬러그 제거."""
    trimmed = {}
    for ticker, per_analyst in scores.items():
        row = {slug: round(float(v), SCORE_DECIMALS)
               for slug, v in per_analyst.items() if v is not None}
        if row:
            trimmed[ticker] = row
    return trimmed


def write_days(records, root):
    """여러 날치를 한 번에 쓴다 — 백필처럼 수백 일을 만들 때.

    append_day 를 반복하면 매번 파일을 통째로 다시 쓴다. 하루 줄이 10KB 를
    넘으므로 500일이면 수 GB 의 쓰기가 된다. 여기는 연도별로 한 번씩만 쓴다.
    """
    by_year = {}
    for date_str, regime, scores in records:
        row = trim_scores(scores)
        if row:
            by_year.setdefault(date_str[:4], []).append(
                {"date": date_str, "regime": regime, "scores": row})

    for year, rows in by_year.items():
        path = _year_path(root, f"{year}-01-01")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        rows.sort(key=lambda r: r["date"])
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return sum(len(rows) for rows in by_year.values())


def append_day(date_str, regime, scores, root=LOG_DIRNAME, asof=None):
    """하루치 점수를 기록한다. 같은 날짜가 이미 있으면 대체한다.

    같은 날 스캔이 두 번 돌아도 줄이 겹치지 않아야 한다 — 겹치면 그 날이
    성적에 두 번 계산돼 표본이 부풀려진다.

    **더 적은 종목으로는 대체하지 않는다.** 대체는 "다시 재서 갱신" 이지
    "덜 잰 것으로 덮어쓰기" 가 아니다. 2026-08-06 에 5종목짜리 스모크
    테스트(workflow_dispatch, UNIVERSE=AAPL,MSFT,...)가 같은 날 276종목
    기록을 통째로 지웠고, 그 장은 되살릴 수 없다. yfinance 가 절반만
    내려주는 날의 재실행도 같은 모양으로 기록을 깎는다.

    반환값 — 기록했으면 True, 더 큰 기록을 지키느라 건너뛰었으면 False.

    asof — 기준 봉의 시각(ISO 문자열). 일봉 기록은 날짜만으로 그 날 종가를
    찾을 수 있어 비워 둔다. 분봉 기록(scalp_log.SCORE_DIRNAME)은 하루에 봉이
    26개라 "그 날의 어느 봉이었나" 를 남기지 않으면 몇 봉 뒤를 셀 기준이 없다.
    """
    path = _year_path(root, date_str)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    trimmed = trim_scores(scores)
    record = {"date": date_str, "regime": regime, "scores": trimmed}
    if asof:
        record["asof"] = str(asof)

    kept = []
    for line in _read_lines(path):
        try:
            existing = json.loads(line)
        except ValueError:
            continue          # 깨진 줄은 버린다 — 되살릴 방법이 없다
        if existing.get("date") == date_str:
            if len(existing.get("scores", {})) > len(trimmed):
                return False
            continue
        kept.append(line)
    kept.append(json.dumps(record, ensure_ascii=False))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    return True


def load_scoring_days(since=None):
    """채점에 쓰는 전체 표본 — 백필 재구성 + 실기록. 날짜 오름차순.

    같은 날짜가 양쪽에 있으면 **실기록을 남긴다.** 백필은 과거 봉으로 되돌려
    계산한 값이고, 야후가 배당·분할로 수정주가를 갱신하면 그날 화면에 뜬
    값과 미세하게 어긋난다 — 실제로 뜬 쪽이 사실이다.

    표본이 무엇으로 이뤄졌는지는 따로 센다 — 채점에 실제로 들어간 날은
    scored_mix(), 로그에 쌓인 날은 sample_mix(). 이 함수가 "몇 개인가" 와
    "무엇인가" 를 함께 답하면 화면이 둘을 구별할 수 없다.
    """
    live = load_days(LOG_DIRNAME, since=since)
    live_dates = {d.get("date") for d in live}
    merged = [d for d in load_days(BACKFILL_DIRNAME, since=since)
              if d.get("date") not in live_dates] + live
    return sorted(merged, key=lambda d: d.get("date", ""))


def sample_mix(since=None):
    """**로그에 쌓인** 일수 — {"live": 실기록, "backfill": 백필}.

    성적표 표본과 다르다. 채점은 선행 구간이 지난 날만 쓰므로, 21·63일 지평은
    최근 실기록을 통째로 버린다(실측 2026-08-20: 로그 실기록 18일, 두 지평의
    실기록 채점일 0일). 성적표 옆에 붙일 숫자는 scored_mix() 로 센다.
    """
    live = load_days(LOG_DIRNAME, since=since)
    live_dates = {d.get("date") for d in live}
    backfill = [d for d in load_days(BACKFILL_DIRNAME, since=since)
                if d.get("date") not in live_dates]
    return {"live": len(live), "backfill": len(backfill)}


def scored_mix(dates):
    """**실제로 채점된** 날짜 목록 → {"live", "backfill"}.

    dates 는 analyst_scorecard.scored_dates() 가 돌려준 것. 이걸 안 쓰고
    sample_mix() 를 성적표 옆에 붙이면, 실기록이 하나도 안 들어간 지평에도
    "실기록 18일" 이 적힌다 — 이 채널이 파는 "사후 채점" 이 거짓이 되는 자리다.
    """
    live_dates = {d.get("date") for d in load_days(LOG_DIRNAME)}
    live = sum(1 for d in dates if d in live_dates)
    return {"live": live, "backfill": len(dates) - live}


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
