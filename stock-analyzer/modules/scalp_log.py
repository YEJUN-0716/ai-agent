"""스캘핑(15분봉) 성적표의 재료 — 점수 기록 위치와 선행수익률 저장소.

점수 기록은 일봉과 형식이 같아 `analyst_log` 를 루트만 바꿔 그대로 쓴다
(SCORE_DIRNAME). 이 모듈이 따로 가진 것은 **선행수익률**이다.

왜 수익률을 저장하는가 — 일봉 성적표는 아무 성적도 저장하지 않는다.
기록과 가격이 유일한 진실이고 성적은 매번 다시 계산한다(scorecard_worker
참고). 15분봉에는 그 방식이 통하지 않는다. yfinance 가 15분봉을 60일치만
주므로 두 달이 지나면 기록 시점의 봉 자체가 사라지고, 다시 계산하면 오래된
날들이 조용히 빠진다. 표본이 늘지 않아 성적표가 멈추는데 워크플로는 초록이다
— 이 저장소가 이미 두 번 겪은 고장의 형태다. 다시 만들 수 없는 값은 만들어진
그 순간에 남긴다.

디렉터리를 analyst_log 와 나눈 이유: `load_days()` 는 자기 디렉터리의 .jsonl
을 파일명과 무관하게 전부 읽는다. 한 폴더에 두면 수익률 줄이 점수 기록으로
읽힌다 (publish_log 를 따로 둔 것과 같은 이유).
"""
import json
import os

# 점수 기록 — analyst_log.append_day / load_days 에 root 로 넘긴다.
SCORE_DIRNAME = os.path.join("data", "scalp_log")
RETURNS_DIRNAME = os.path.join("data", "scalp_returns")

# 수익률은 %. 0.0001% 차이는 성적에 의미가 없다.
RETURN_DECIMALS = 4


def _year_path(root, date_str):
    return os.path.join(str(root), f"{date_str[:4]}.jsonl")


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in (line.strip() for line in f) if ln]


def append_returns(date_str, horizon, returns, root=RETURNS_DIRNAME):
    """기록일의 horizon 봉 선행수익률(%)을 남긴다 — {ticker: pct}.

    같은 (날짜, 지평)이 이미 있으면 대체한다. 겹치면 그 날이 성적에 두 번
    계산돼 표본이 부풀려진다 — analyst_log.append_day 와 같은 규칙이다.
    """
    path = _year_path(root, date_str)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    horizon = int(horizon)
    trimmed = {ticker: round(float(pct), RETURN_DECIMALS)
               for ticker, pct in returns.items() if pct is not None}
    record = {"date": date_str, "horizon": horizon, "returns": trimmed}

    kept = []
    for line in _read_lines(path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue          # 깨진 줄은 버린다 — 되살릴 방법이 없다
        if rec.get("date") == date_str and _as_int(rec.get("horizon")) == horizon:
            continue
        kept.append(line)
    kept.append(json.dumps(record, ensure_ascii=False))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_returns(root=RETURNS_DIRNAME):
    """{horizon: {date: {ticker: pct}}} — analyst_scorecard 가 받는 모양 그대로.

    지평별로 갈라 주므로 호출자는 `load_returns().get(26, {})` 을 그대로
    score_analysts 의 forward_returns 자리에 넣을 수 있다.
    """
    root = str(root)
    if not os.path.isdir(root):
        return {}

    out = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".jsonl"):
            continue
        for line in _read_lines(os.path.join(root, name)):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            horizon = _as_int(rec.get("horizon"))
            rets = rec.get("returns") or {}
            if horizon is None or not rets:
                continue
            out.setdefault(horizon, {})[rec.get("date", "")] = rets

    return out
