# AI 애널리스트 성적표 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 애널리스트별 판단을 매일 자동으로 기록하고, 실제 수익률과 대조해 실측 성적표를 만든다. 가중치 자동 반영은 하지 않는다(표시만).

**Architecture:** 점수 산식을 `app.py` 에서 순수 모듈로 추출 → 매일 스캔이 전 유니버스에 대해 점수를 JSONL 로 기록 → 채점기가 5/21/63일 선행수익률과 대조해 IC 를 내되 겹치는 창을 Newey–West 로 보정 → 화면에 표시.

**Tech Stack:** Python 3.14, pandas, numpy, scipy.stats.spearmanr, pytest

**Spec:** `docs/superpowers/specs/2026-07-23-analyst-scorecard-design.md`

## Global Constraints

- 새 모듈은 `streamlit`·`yfinance` 를 import 하지 않는다. 입력은 호출부가 넘긴다.
- 계산 불가는 **키를 뺀다.** 중립값(50)으로 채우지 않는다 — '계산 불가'가 '중립 판단'으로 성적에 섞인다.
- 기록 실패가 일일 스캔을 죽이면 안 된다. 예외를 잡아 경고만 남기고 계속한다.
- 애널리스트 키는 안정 슬러그(`chart`/`quant`/`ict`). 표시 이름(한글)은 기록에 쓰지 않는다.
- 가중치를 바꾸는 코드를 쓰지 않는다. 이번 범위는 기록·채점·표시까지다.
- 커밋 메시지는 한국어, conventional commits 접두사.

## 범위 조정 — `quant` 는 Phase 1 에서 기록하지 않는다

스펙은 방향성 3인을 기록한다고 했으나, 배선 지점을 확인한 결과 비용이 갈린다.

- `chart`(기술점수+모멘텀), `ict` — OHLCV 만 필요. `price_panel` 이 이미 로드하므로 **추가 네트워크 0**.
- `quant` — `fundamental_score(ticker)` 가 종목별 `yf.Ticker(tk).info` 를 부른다. 스캔이 내부적으로 이미 부르지만 반환하지 않아, 재사용하려면 `calc_factor_scores` 의 반환 계약을 바꿔야 한다. 그대로 두고 다시 부르면 276회 추가 조회다.

`quant` 는 어차피 과거 재현이 불가(스펙 2절)하므로 기록 시작이 늦어도 손실이 대칭적이지 않다.
**Phase 1 은 `chart`/`ict` 로 시작해 시계를 먼저 돌리고**, `quant` 는 스캔 반환 계약을 정리하는 별도 작업으로 뺀다. 기록 포맷은 3인을 수용하므로 나중에 키만 추가하면 된다.

## File Structure

| 파일 | 책임 |
|---|---|
| `modules/analyst_team.py` (신규) | 방향성 애널리스트 점수 산식 — 순수 함수 |
| `modules/analyst_log.py` (신규) | JSONL 기록 append / 조회 |
| `modules/analyst_scorecard.py` (신규) | 채점 — IC, 적중률, Newey–West 표준오차 |
| `app.py` (수정) | 추출된 산식을 호출하도록 교체 + 성적표 표시 |
| `signal_worker.py` (수정) | 매일 기록 호출 |
| `modules/analyst_weights.py` (수정) | `production_weights` 우선 읽기 |
| `tests/test_analyst_team.py` (신규) | 추출 동작 무변경 고정 |
| `tests/test_analyst_log.py` (신규) | 기록 왕복·결측 처리 |
| `tests/test_analyst_scorecard.py` (신규) | 채점·겹침 보정 |

---

### Task 1: 애널리스트 점수 산식 추출

`app.py:3679-3707` 의 `ict_crt_analyst`·`technical_momentum_analyst` 는 점수 계산과
보고서 포맷팅이 섞여 있다. 자동 경로는 점수만 필요하므로 **점수 산식만** 순수 함수로
분리하고, 기존 함수는 그것을 호출해 보고서를 만든다.

**Files:**
- Create: `modules/analyst_team.py`
- Modify: `app.py:3679-3707`
- Test: `tests/test_analyst_team.py`

**Interfaces:**
- Produces:
  - `ANALYST_SLUGS = ("chart", "quant", "ict")`
  - `chart_score(technical_score: float, momentum_score: float) -> float`
  - `ict_score(base: float, adjustment: float) -> float`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_analyst_team.py
import pytest
from modules import analyst_team as at


def test_chart_score_is_technical_70_momentum_30():
    assert at.chart_score(80.0, 40.0) == pytest.approx(80 * 0.7 + 40 * 0.3)


def test_ict_score_clips_to_0_100():
    assert at.ict_score(95.0, 20.0) == 100.0
    assert at.ict_score(5.0, -20.0) == 0.0
    assert at.ict_score(50.0, 5.0) == 55.0


def test_slugs_are_stable():
    assert at.ANALYST_SLUGS == ("chart", "quant", "ict")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_analyst_team.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.analyst_team'`

- [ ] **Step 3: 모듈을 만든다**

```python
# modules/analyst_team.py
"""방향성 애널리스트의 점수 산식 — 화면과 자동 기록이 공유하는 단일 진실 공급원.

app.py 의 애널리스트 함수는 점수 계산과 보고서 포맷팅이 붙어 있었다. 자동
경로(signal_worker)는 점수만 필요한데 포맷팅까지 끌고 오면 Streamlit 의존이
따라온다. 여기서는 산식만 갖는다 — streamlit·yfinance 를 import 하지 않는다.
"""

ANALYST_SLUGS = ("chart", "quant", "ict")

CHART_TECHNICAL_WEIGHT = 0.7
CHART_MOMENTUM_WEIGHT = 0.3

SCORE_MIN = 0.0
SCORE_MAX = 100.0


def chart_score(technical_score, momentum_score):
    """차트+파동+모멘텀 점수 = 기술점수 70% + 모멘텀점수 30%."""
    return (float(technical_score) * CHART_TECHNICAL_WEIGHT
            + float(momentum_score) * CHART_MOMENTUM_WEIGHT)


def ict_score(base, adjustment):
    """ICT+CRT 점수 = 구조 점수 + CRT/FVG/OB 조정, 0~100 로 자름."""
    return min(max(float(base) + float(adjustment), SCORE_MIN), SCORE_MAX)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_analyst_team.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: app.py 가 새 모듈을 쓰게 바꾼다**

`app.py:3685` 의 `score = float(np.clip(base + adj_info['adjustment'], 0, 100))` 를
`score = _analyst_team.ict_score(base, adj_info['adjustment'])` 로,
`app.py:3695` 의 `score = t_score * 0.7 + mom_score * 0.3` 를
`score = _analyst_team.chart_score(t_score, mom_score)` 로 바꾼다.
파일 상단 import 블록에 `from modules import analyst_team as _analyst_team` 를 추가한다.

- [ ] **Step 6: 전체 스위트로 무변경을 확인한다**

Run: `python -m pytest -q`
Expected: PASS — 기존 테스트 전부 통과 (점수가 바뀌면 `test_factor_scores.py` 가 잡는다)

- [ ] **Step 7: 커밋**

```bash
git add modules/analyst_team.py tests/test_analyst_team.py app.py
git commit -m "refactor: 애널리스트 점수 산식을 analyst_team 모듈로 추출"
```

---

### Task 2: 기록 저장소

**Files:**
- Create: `modules/analyst_log.py`
- Test: `tests/test_analyst_log.py`

**Interfaces:**
- Consumes: `analyst_team.ANALYST_SLUGS`
- Produces:
  - `append_day(date_str: str, regime: str, scores: dict, root: str|Path) -> None`
  - `load_days(root: str|Path, since: str|None = None) -> list[dict]`
  - `LOG_DIRNAME = "data/analyst_log"`

`scores` 는 `{ticker: {slug: float}}`. 값이 없는 슬러그는 **키가 없어야** 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_analyst_log.py
import pytest
from modules import analyst_log as al


def test_round_trip(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.14, "ict": 48.5}}, root=tmp_path)
    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert days[0]["date"] == "2026-07-23"
    assert days[0]["regime"] == "bull"
    # 소수 1자리로 절삭된다
    assert days[0]["scores"]["AAPL"]["chart"] == 62.1


def test_missing_slug_stays_missing(tmp_path):
    """계산 불가는 키를 뺀다 — 50 으로 채우면 '중립 판단'으로 섞인다."""
    al.append_day("2026-07-23", "bull", {"AAPL": {"chart": 62.1}}, root=tmp_path)
    day = al.load_days(tmp_path)[0]
    assert "ict" not in day["scores"]["AAPL"]


def test_days_are_sorted_across_years(tmp_path):
    al.append_day("2027-01-05", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-12-31", "bear", {"A": {"chart": 2.0}}, root=tmp_path)
    assert [d["date"] for d in al.load_days(tmp_path)] == ["2026-12-31", "2027-01-05"]


def test_since_filter(tmp_path):
    al.append_day("2026-07-01", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 2.0}}, root=tmp_path)
    assert len(al.load_days(tmp_path, since="2026-07-10")) == 1


def test_duplicate_date_is_replaced(tmp_path):
    """같은 날 두 번 돌아도 줄이 겹치지 않는다."""
    al.append_day("2026-07-23", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 9.0}}, root=tmp_path)
    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert days[0]["scores"]["A"]["chart"] == 9.0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_analyst_log.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.analyst_log'`

- [ ] **Step 3: 구현한다**

```python
# modules/analyst_log.py
"""애널리스트 점수 일별 기록 — data/analyst_log/YYYY.jsonl.

한 줄에 하루치. 연도별로 파일을 나눠 한 파일이 무한히 커지지 않게 한다.
점수는 소수 1자리로 절삭한다 — 0.01 점의 차이는 성적에 아무 의미가 없고
연 3MB 와 30MB 를 가른다.

값이 없는 애널리스트는 **키를 뺀다.** 중립값으로 채우면 '계산 불가'가
'중립 판단'으로 성적에 섞인다 (ic_weights 12-1 모멘텀과 같은 규칙).
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
    """하루치 점수를 기록한다. 같은 날짜가 이미 있으면 대체한다."""
    path = _year_path(root, date_str)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    trimmed = {}
    for ticker, per_analyst in scores.items():
        row = {slug: round(float(v), SCORE_DECIMALS)
               for slug, v in per_analyst.items() if v is not None}
        if row:
            trimmed[ticker] = row

    record = {"date": date_str, "regime": regime, "scores": trimmed}

    kept = [ln for ln in _read_lines(path)
            if json.loads(ln).get("date") != date_str]
    kept.append(json.dumps(record, ensure_ascii=False))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")


def load_days(root=LOG_DIRNAME, since=None):
    """기록을 날짜 오름차순으로 읽는다. since 이상만."""
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_analyst_log.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add modules/analyst_log.py tests/test_analyst_log.py
git commit -m "feat: 애널리스트 점수 일별 기록 저장소"
```

---

### Task 3: 채점기 — IC 와 겹침 보정 표준오차

**Files:**
- Create: `modules/analyst_scorecard.py`
- Test: `tests/test_analyst_scorecard.py`

**Interfaces:**
- Produces:
  - `HORIZONS = (5, 21, 63)`
  - `newey_west_se(values: list[float], lag: int) -> float`
  - `score_analysts(days: list[dict], forward_returns: dict, horizon: int) -> dict`

`forward_returns` 는 `{date_str: {ticker: pct}}`. 반환은
`{slug: {"mean_ic", "se", "t_stat", "n", "effective_n", "hit_rate"}}`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_analyst_scorecard.py
import numpy as np
import pytest
from modules import analyst_scorecard as sc


def test_independent_samples_match_plain_se():
    """겹침이 없으면(lag=0) 통상 표준오차와 같아야 한다."""
    rng = np.random.default_rng(0)
    vals = rng.normal(0.02, 0.15, 200).tolist()
    plain = float(np.std(vals, ddof=1) / np.sqrt(len(vals)))
    assert sc.newey_west_se(vals, lag=0) == pytest.approx(plain, rel=1e-9)


def test_overlap_inflates_se():
    """양의 자기상관이 있으면 표준오차가 커진다 — 표본을 과대평가하지 않는다."""
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.1, 300)
    overlapped = np.convolve(base, np.ones(20) / 20, mode="same").tolist()
    plain = float(np.std(overlapped, ddof=1) / np.sqrt(len(overlapped)))
    assert sc.newey_west_se(overlapped, lag=19) > plain * 1.5


def test_se_is_never_negative():
    """Newey-West 는 표본에 따라 음수 분산이 나올 수 있다 — 0 아래로 못 간다."""
    vals = [0.1, -0.1] * 30
    assert sc.newey_west_se(vals, lag=19) >= 0.0


def test_score_analysts_computes_ic_per_slug():
    days = [
        {"date": "2026-01-05", "scores": {
            "A": {"chart": 90.0}, "B": {"chart": 50.0}, "C": {"chart": 10.0},
            "D": {"chart": 70.0}, "E": {"chart": 30.0}}},
    ]
    fwd = {"2026-01-05": {"A": 5.0, "B": 0.0, "C": -5.0, "D": 2.0, "E": -2.0}}
    got = sc.score_analysts(days, fwd, horizon=5)
    assert got["chart"]["mean_ic"] == pytest.approx(1.0)
    assert got["chart"]["n"] == 1


def test_missing_slug_is_excluded_not_zero_filled():
    """점수가 없는 종목은 그 애널리스트 계산에서 빠진다."""
    days = [
        {"date": "2026-01-05", "scores": {
            "A": {"chart": 90.0, "ict": 10.0}, "B": {"chart": 50.0},
            "C": {"chart": 10.0}, "D": {"chart": 70.0}, "E": {"chart": 30.0}}},
    ]
    fwd = {"2026-01-05": {k: 0.0 for k in "ABCDE"}}
    got = sc.score_analysts(days, fwd, horizon=5)
    assert "ict" not in got


def test_effective_n_is_smaller_than_apparent_n():
    """겹치는 창에서는 유효 표본이 겉보기 표본보다 작다."""
    rng = np.random.default_rng(2)
    days, fwd = [], {}
    for i in range(60):
        d = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        days.append({"date": d, "scores": {
            t: {"chart": float(rng.normal(50, 10))} for t in "ABCDE"}})
        fwd[d] = {t: float(rng.normal(0, 3)) for t in "ABCDE"}
    got = sc.score_analysts(days, fwd, horizon=21)
    assert got["chart"]["n"] == 60
    assert got["chart"]["effective_n"] < 60
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_analyst_scorecard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.analyst_scorecard'`

- [ ] **Step 3: 구현한다**

```python
# modules/analyst_scorecard.py
"""애널리스트 성적 채점 — 순위 예측력(IC)과 겹침 보정 표준오차.

매일 기록하고 21일 뒤 수익률로 채점하면 관측치의 선행 구간이 서로 겹친다.
겹친 관측을 독립으로 세면 표본이 실제보다 20배 많아 보이고, "n=250,
유의함" 이라는 잘못된 결론이 나온다. Newey-West 로 자기상관을 반영해
표준오차를 키운다 — 실제 정보량만큼만 인정하는 장치다.

파일도 네트워크도 모른다. 숫자만 받는다.
"""
import numpy as np
from scipy.stats import spearmanr

HORIZONS = (5, 21, 63)

MIN_TICKERS_PER_DAY = 5


def newey_west_se(values, lag):
    """평균의 Newey-West 표준오차. lag=0 이면 통상 표준오차와 같다."""
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n < 2:
        return float("nan")

    dev = arr - arr.mean()
    gamma0 = float(dev @ dev) / (n - 1)
    total = gamma0

    for k in range(1, min(int(lag), n - 1) + 1):
        gamma_k = float(dev[k:] @ dev[:-k]) / (n - 1)
        weight = 1.0 - k / (lag + 1.0)      # Bartlett
        total += 2.0 * weight * gamma_k

    if total <= 0:
        return 0.0
    return float(np.sqrt(total / n))


def _daily_ic(day_scores, returns, slug):
    """하루치 단면 IC. 유효 종목이 모자라면 None."""
    pairs = [(v[slug], returns[t]) for t, v in day_scores.items()
             if slug in v and t in returns and returns[t] is not None
             and not np.isnan(returns[t])]
    if len(pairs) < MIN_TICKERS_PER_DAY:
        return None
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    if len(set(x.tolist())) < 2:
        return None
    ic, _ = spearmanr(x, y)
    return None if np.isnan(ic) else float(ic)


def score_analysts(days, forward_returns, horizon):
    """애널리스트별 IC 통계."""
    slugs = sorted({s for d in days for v in d.get("scores", {}).values()
                    for s in v})
    lag = max(int(horizon) - 1, 0)

    out = {}
    for slug in slugs:
        ics, hits = [], []
        for day in days:
            rets = forward_returns.get(day.get("date"))
            if not rets:
                continue
            ic = _daily_ic(day.get("scores", {}), rets, slug)
            if ic is None:
                continue
            ics.append(ic)
            hits.append(1.0 if ic > 0 else 0.0)

        if not ics:
            continue

        arr = np.asarray(ics, dtype=float)
        mean_ic = float(arr.mean())
        se = newey_west_se(ics, lag)
        plain_se = (float(arr.std(ddof=1) / np.sqrt(len(arr)))
                    if len(arr) > 1 else float("nan"))

        if se and not np.isnan(se) and se > 0 and not np.isnan(plain_se):
            effective_n = len(arr) * (plain_se / se) ** 2
        else:
            effective_n = float(len(arr))

        out[slug] = {
            "mean_ic":     round(mean_ic, 4),
            "se":          round(se, 4) if not np.isnan(se) else None,
            "t_stat":      round(mean_ic / se, 3) if se else None,
            "n":           len(arr),
            "effective_n": round(min(effective_n, len(arr)), 1),
            "hit_rate":    round(float(np.mean(hits)) * 100, 1),
        }

    return out
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_analyst_scorecard.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add modules/analyst_scorecard.py tests/test_analyst_scorecard.py
git commit -m "feat: 애널리스트 채점기 — IC + 겹침 보정 표준오차"
```

---

### Task 4: 매일 기록 배선

**Files:**
- Modify: `signal_worker.py`
- Test: `tests/test_analyst_log.py` (기록 실패가 스캔을 죽이지 않는지)

**Interfaces:**
- Consumes: `analyst_log.append_day`, `analyst_team.ict_score`
- Produces: `signal_worker.record_analyst_scores(tickers, panel, regime) -> int`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_analyst_log.py 에 추가
def test_recording_failure_does_not_raise(monkeypatch):
    """기록이 깨져도 일일 스캔은 계속돼야 한다."""
    import signal_worker

    def _boom(*a, **k):
        raise RuntimeError("디스크 꽉 참")

    monkeypatch.setattr(signal_worker.analyst_log, "append_day", _boom)
    assert signal_worker.record_analyst_scores([], {}, "bull") == 0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_analyst_log.py -q`
Expected: FAIL — `AttributeError: module 'signal_worker' has no attribute 'record_analyst_scores'`

- [ ] **Step 3: 구현한다**

`signal_worker.py` 상단에 `from modules import analyst_log, analyst_team` 를 넣고:

```python
def record_analyst_scores(tickers, panel, regime):
    """전 유니버스의 ict 점수를 기록한다. 실패해도 스캔은 계속된다.

    quant 는 종목별 yfinance .info 가 필요해 Phase 1 에서 제외했다 —
    스캔의 반환 계약을 정리한 뒤 별도로 붙인다.
    """
    from datetime import datetime

    try:
        from modules.ict_analysis import ict_factor_score, calc_ict_adjustment
    except Exception:
        return 0

    scores = {}
    for tk in tickers:
        df = panel.get(tk)
        if df is None or len(df) < 60:
            continue
        try:
            adj = calc_ict_adjustment(df)
            scores[tk] = {"ict": analyst_team.ict_score(
                ict_factor_score(df), adj["adjustment"])}
        except Exception:
            continue

    if not scores:
        return 0

    try:
        analyst_log.append_day(datetime.now().strftime("%Y-%m-%d"),
                               regime, scores)
    except Exception as e:
        print(f"[경고] 애널리스트 기록 실패 (스캔은 계속): {e}")
        return 0

    return len(scores)
```

`main()` 의 `save_signal_log(actions)` 직전에 호출을 넣는다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest -q`
Expected: PASS — 전체 스위트

- [ ] **Step 5: 커밋**

```bash
git add signal_worker.py tests/test_analyst_log.py
git commit -m "feat: 매일 스캔이 애널리스트 점수를 기록"
```

---

### Task 5: `analyst_weights` 가 프로덕션 블록을 우선 읽게

**Files:**
- Modify: `modules/analyst_weights.py`
- Test: `tests/test_analyst_weights.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_analyst_weights.py
import json
import pytest
from modules import analyst_weights as aw


def _write(tmp_path, payload, monkeypatch):
    p = tmp_path / "ic_weights.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(aw, "_IC_WEIGHT_FILE", str(p))


def test_prefers_production_weights(tmp_path, monkeypatch):
    """실전 스캔이 쓰는 블록을 우선 읽는다 — mom_3m 매핑은 후퇴 경로다."""
    _write(tmp_path, {
        "production_weights": {"bull": {"momentum": 0.6, "value": 0.2,
                                        "quality": 0.15, "low_vol": 0.05}},
        "regime_weights": {"bull": {"mom_3m": 0.1, "mom_1m": 0.1, "low_vol": 0.1,
                                    "value": 0.3, "quality": 0.3, "ict": 0.1}},
    }, monkeypatch)

    w = aw.load_analyst_weights("bull")
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["차트+파동+모멘텀"] == pytest.approx(0.65)


def test_falls_back_to_regime_weights(tmp_path, monkeypatch):
    _write(tmp_path, {
        "regime_weights": {"bull": {"mom_3m": 0.2, "mom_1m": 0.1, "low_vol": 0.1,
                                    "value": 0.2, "quality": 0.2, "ict": 0.2}},
    }, monkeypatch)

    w = aw.load_analyst_weights("bull")
    assert w["차트+파동+모멘텀"] == pytest.approx(0.4)


def test_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "_IC_WEIGHT_FILE", str(tmp_path / "nope.json"))
    assert aw.load_analyst_weights("bull") is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_analyst_weights.py -q`
Expected: FAIL — `test_prefers_production_weights` 가 0.65 대신 0.4 를 받는다

- [ ] **Step 3: 구현한다**

`production_weights` 분기를 추가한다. 프로덕션 블록에는 `ict` 키가 없으므로
ICT 애널리스트는 몫이 0 이 되어 정규화에서 빠진다 — 그건 잘못이다.
`ict` 는 프로덕션 4팩터에 없으니 **프로덕션 블록을 쓸 때도 ICT 몫은
`regime_weights` 의 `ict` 를 그대로 가져와 합산**한 뒤 정규화한다.

```python
_PRODUCTION_MAP = {
    "차트+파동+모멘텀": ("momentum", "low_vol"),
    "퀀트+재무":        ("value", "quality"),
}


def _normalize(raw):
    total = sum(raw.values())
    if total < 1e-9:
        return None
    return {k: v / total for k, v in raw.items()}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest -q`
Expected: PASS — 전체 스위트

- [ ] **Step 5: 커밋**

```bash
git add modules/analyst_weights.py tests/test_analyst_weights.py
git commit -m "fix: 애널리스트 가중치가 프로덕션 IC 블록을 우선 읽도록"
```

---

### Task 6: 성적표 표시

**Files:**
- Modify: `app.py` (애널리스트 팀 탭)

- [ ] **Step 1: 표시 함수를 만든다**

기록을 읽어 5/21/63일 성적을 표로 보여준다. 유효 표본이 30 미만이면
"아직 판정 불가 — n 부족" 을 함께 띄운다. 가중치는 건드리지 않는다.

- [ ] **Step 2: 전체 스위트**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add app.py
git commit -m "feat: 애널리스트 성적표 화면 표시"
```

---

## Self-Review

**스펙 커버리지**

| 스펙 요구 | 태스크 |
|---|---|
| 매일 전 유니버스 기록 | Task 4 |
| 5/21/63일 동시 채점 | Task 3 (`HORIZONS`) |
| 겹침 보정 표준오차 | Task 3 (`newey_west_se`) |
| 겉보기 n 과 유효 표본 병기 | Task 3 (`n`, `effective_n`) |
| JSONL 연도별 저장 | Task 2 |
| 안정 슬러그 | Task 1 (`ANALYST_SLUGS`) |
| 산식 추출 (Streamlit 무의존) | Task 1 |
| 계산 불가는 키 제거 | Task 2, Task 3 |
| 기록 실패가 스캔을 안 죽임 | Task 4 |
| `analyst_weights` 프로덕션 블록 | Task 5 |
| 표시만, 가중치 미반영 | Task 6 (전환 게이트 코드 없음) |

**미커버 (의도적)**

- `quant` 기록 — 위 "범위 조정" 참조. 별도 작업.
- 전환 게이트 구현 — 스펙의 비목표. 표본이 쌓인 뒤.
