# EDGAR PIT 재무 데이터 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SEC EDGAR에서 20년치 분기 재무를 확보해 `ic_weights.json`에서 value·quality 팩터의 IC를 처음으로 측정 가능하게 만든다.

**Architecture:** 신규 `modules/edgar_fundamentals.py`가 EDGAR `companyfacts`를 종목별로 받아 로컬 캐시하고, "분기당 정확히 1행" 계약을 지키는 분기 패널로 조립한다. `factor_engine.py`의 yfinance 기반 두 함수를 이 모듈로 교체한다. `point_in_time_fundamentals`와 IC 계산 로직은 손대지 않는다.

**Tech Stack:** Python 3.12, requests, pandas, pytest. 신규 의존성 없음 (requests·pyarrow는 이미 있음).

## Global Constraints

- Python **3.12**.
- 원본 캐시 경로: `data/edgar_raw/CIK{cik:010d}.json`. `data/`는 이미 gitignore.
- 캐시는 원본 JSON 단일 캐시. parquet 없음. 매 실행 원본에서 재조립(CPU상 저렴).
- `fetch_quarterly_fundamentals_history(tickers, ...)` 반환: `{ticker: pd.DataFrame(index=filed날짜, columns=[revenue, operating_income, net_income])}`.
- `fetch_shares_history(tickers, ...)` 반환: `{ticker: pd.Series(index=filed날짜, values=희석주식수)}`.
- 이 두 반환 형태는 기존 소비자(`point_in_time_fundamentals`)와 동일해야 한다 — 하위 로직 불변.
- **"분기당 1행" 계약**: 출력 DataFrame은 (1) 같은 (start,end) 조합 중복 없음, (2) 손익 행 기간 80~100일, (3) 인덱스(filed) 단조 비감소(non-decreasing).
- **중복 제거는 최초 공시 기준** (같은 (start,end)에 여러 filed면 가장 이른 것). 정정 공시는 무시 — look-ahead 차단.
- 조용한 실패 금지. 개별 티커 실패는 수집해 실행 끝에 출력. `except Exception: pass` 새로 쓰지 않는다.
- CIK 검증: `companyfacts` 응답에 `us-gaap` 네임스페이스 없으면 실패 처리 (XOM 함정).
- 신규 코드 주석·로그·docstring은 한국어.
- 테스트는 네트워크를 타지 않는다. `requests`와 `_load_raw`는 목으로 대체.
- **stockholders_equity·PIT p/b는 이번 범위 밖** (다음 작업). value는 pe, quality는 margin으로 측정된다.

## 상수 (Task 1에서 정의, 이후 참조)

```python
SEC_HEADERS   = {"User-Agent": "stock-analyzer research contact@example.com"}
RAW_DIR       = "data/edgar_raw"
RATE_SLEEP    = 0.12          # SEC 초당 10요청 제한 → 약 8/s
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 80, 100
ANNUAL_MIN_DAYS,  ANNUAL_MAX_DAYS  = 350, 380
CONTAINMENT_TOL_DAYS = 10
MIN_COVERAGE  = 0.70

TAG_CHAINS = {
    "revenue":          ["RevenueFromContractWithCustomerExcludingAssessedTax",
                         "Revenues", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income":       ["NetIncomeLoss"],
}
SHARES_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"]

# company_tickers.json이 잘못된 CIK를 주는 종목의 수동 교정.
# XOM은 2115436(수수료신고 ffd만)이 아니라 34088(us-gaap 438태그)이다.
# 다른 종목은 us-gaap 검증에서 실패로 잡혀 coverage에 드러난다 — 발견 시 여기 추가.
CIK_OVERRIDES = {"XOM": 34088}
```

---

### Task 1: 원본 수집 계층 (CIK 매핑 · fetch · 검증 · 캐시)

**Files:**
- Create: `modules/edgar_fundamentals.py`
- Create: `tests/test_edgar_fundamentals.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `get_cik(ticker: str) -> int | None`
  - `_fetch_companyfacts(cik: int) -> dict | None` (429/503 재시도)
  - `_valid_us_gaap(facts_json: dict | None) -> dict | None`
  - `load_raw(ticker: str, cache_dir: str = None) -> dict | None` — us-gaap dict 또는 None. 유효할 때만 디스크 캐시.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_edgar_fundamentals.py`:

```python
"""edgar_fundamentals 테스트. 네트워크를 타지 않고 requests를 목으로 대체한다."""
import json
import pandas as pd
import pytest

from modules import edgar_fundamentals as ef


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_valid_us_gaap_rejects_ffd_only():
    """us-gaap 네임스페이스가 없으면 None (XOM 함정)."""
    assert ef._valid_us_gaap({"facts": {"ffd": {}}}) is None
    assert ef._valid_us_gaap({"facts": {"us-gaap": {"X": 1}}}) == {"X": 1}
    assert ef._valid_us_gaap(None) is None


def test_load_raw_caches_valid_only(tmp_path, monkeypatch):
    """유효 응답은 디스크에 캐시하고, 두 번째 호출은 네트워크를 안 탄다."""
    calls = []
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": []}}}}}

    def fake_get(url, **kw):
        calls.append(url)
        if "company_tickers" in url:
            return _Resp(200, {"0": {"ticker": "AAPL", "cik_str": 320193}})
        return _Resp(200, payload)

    monkeypatch.setattr(ef.requests, "get", fake_get)
    monkeypatch.setattr(ef.time, "sleep", lambda *_: None)

    ug = ef.load_raw("AAPL", cache_dir=str(tmp_path))
    assert ug == payload["facts"]["us-gaap"]
    n_after_first = len(calls)

    ef.load_raw("AAPL", cache_dir=str(tmp_path))
    # 티커맵 재사용 + 디스크 캐시 → companyfacts 재요청 없음
    assert not any("companyfacts" in u for u in calls[n_after_first:])
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.edgar_fundamentals'`

- [ ] **Step 3: 모듈 뼈대 + 수집 계층 구현**

`modules/edgar_fundamentals.py`:

```python
"""
EDGAR PIT 재무 데이터
=====================
SEC EDGAR companyfacts에서 20년치 분기 재무를 받아 로컬 캐시하고,
"분기당 정확히 1행" 계약을 지키는 분기 패널로 조립한다.
factor_engine.py의 yfinance 기반 fetch_*_history를 대체한다.

인덱스 의미 주의: 기존 yfinance 경로는 "분기말 + 45일" 추정치를
인덱스로 썼으나, 이 모듈은 EDGAR의 실제 공시일(filed)을 쓴다.
소비 측(`<= as_of` 필터)은 그대로 동작하지만 값의 의미가 다르다.
"""
import os
import json
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

SEC_HEADERS   = {"User-Agent": "stock-analyzer research contact@example.com"}
RAW_DIR       = "data/edgar_raw"
RATE_SLEEP    = 0.12
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 80, 100
ANNUAL_MIN_DAYS,  ANNUAL_MAX_DAYS  = 350, 380
CONTAINMENT_TOL_DAYS = 10
MIN_COVERAGE  = 0.70

TAG_CHAINS = {
    "revenue":          ["RevenueFromContractWithCustomerExcludingAssessedTax",
                         "Revenues", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income":       ["NetIncomeLoss"],
}
SHARES_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"]

CIK_OVERRIDES = {"XOM": 34088}

_cik_map = None


def get_cik(ticker: str):
    """티커 → CIK. CIK_OVERRIDES 우선, 없으면 SEC 매핑."""
    if ticker in CIK_OVERRIDES:
        return CIK_OVERRIDES[ticker]
    global _cik_map
    if _cik_map is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers=SEC_HEADERS, timeout=30)
        r.raise_for_status()
        _cik_map = {v["ticker"]: int(v["cik_str"]) for v in r.json().values()}
    return _cik_map.get(ticker)


def _fetch_companyfacts(cik: int):
    """companyfacts JSON. 429/503은 지수 백오프 3회 재시도. 실패 시 None."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    for attempt in range(3):
        r = requests.get(url, headers=SEC_HEADERS, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 503):
            time.sleep(2 ** attempt)
            continue
        return None
    return None


def _valid_us_gaap(facts_json):
    """us-gaap 네임스페이스가 있으면 그 dict, 없으면 None (XOM 함정 방어)."""
    facts = (facts_json or {}).get("facts", {})
    return facts.get("us-gaap")


def load_raw(ticker: str, cache_dir: str = None):
    """
    ticker의 us-gaap 팩트 dict를 반환한다. 없거나 무효면 None.
    유효할 때만 디스크에 캐시한다 (무효는 다음 실행에서 재시도 가능하도록).
    """
    cache_dir = cache_dir or RAW_DIR
    cik = get_cik(ticker)
    if cik is None:
        return None

    path = os.path.join(cache_dir, f"CIK{cik:010d}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass  # 손상 시 아래에서 재수집

    time.sleep(RATE_SLEEP)
    raw = _fetch_companyfacts(cik)
    ug  = _valid_us_gaap(raw)
    if ug is None:
        return None

    os.makedirs(cache_dir, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ug, f)
    os.replace(tmp, path)
    return ug
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -v`
Expected: `3 passed`

- [ ] **Step 5: 커밋**

```bash
git add modules/edgar_fundamentals.py tests/test_edgar_fundamentals.py
git commit -m "feat: edgar_fundamentals 수집 계층

CIK 매핑(XOM 교정 포함), companyfacts fetch(429 재시도),
us-gaap 검증, 원본 JSON 디스크 캐시."
```

---

### Task 2: 팩트 추출 · 기간 분류 · 태그 대체

**Files:**
- Modify: `modules/edgar_fundamentals.py`
- Modify: `tests/test_edgar_fundamentals.py`

**Interfaces:**
- Consumes: Task 1
- Produces:
  - `_duration_days(fact: dict) -> int`
  - `_facts_for_chain(us_gaap: dict, tag_chain: list, unit: str) -> list`
  - `_quarter_facts(raw: list) -> list` (80~100일)
  - `_annual_facts(raw: list) -> list` (350~380일)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_edgar_fundamentals.py` 끝에 추가:

```python
def _fact(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def test_facts_for_chain_uses_first_present_tag():
    """대체 목록에서 먼저 존재하는 태그를 쓴다."""
    ug = {"Revenues": {"units": {"USD": [_fact("2020-01-01", "2020-03-31", 5, "2020-05-01")]}}}
    got = ef._facts_for_chain(ug, ef.TAG_CHAINS["revenue"], "USD")
    assert len(got) == 1 and got[0]["val"] == 5


def test_facts_for_chain_empty_when_no_tag():
    assert ef._facts_for_chain({}, ["Nope"], "USD") == []


def test_quarter_and_annual_classification():
    """기간 길이로 분기(80~100)와 연간(350~380)을 가른다. 9개월(YTD)은 둘 다 아님."""
    raw = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),  # 90일 분기
        _fact("2020-01-01", "2020-09-30", 3, "2020-11-01"),  # 273일 (YTD) — 제외
        _fact("2020-01-01", "2020-12-31", 4, "2021-02-01"),  # 365일 연간
    ]
    q = ef._quarter_facts(raw)
    a = ef._annual_facts(raw)
    assert [f["val"] for f in q] == [1]
    assert [f["val"] for f in a] == [4]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -k "chain or classification" -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_facts_for_chain'`

- [ ] **Step 3: 구현 추가**

`modules/edgar_fundamentals.py`의 `load_raw` 아래에 추가:

```python
def _duration_days(fact: dict) -> int:
    """팩트의 기간 길이(일). 시점 데이터(start 없음)는 -1."""
    if "start" not in fact:
        return -1
    return (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days


def _facts_for_chain(us_gaap: dict, tag_chain: list, unit: str) -> list:
    """대체 목록에서 먼저 존재하는 태그의 팩트 리스트를 반환."""
    for tag in tag_chain:
        node = us_gaap.get(tag)
        if node and unit in node.get("units", {}):
            return node["units"][unit]
    return []


def _quarter_facts(raw: list) -> list:
    """기간 80~100일인 팩트만 (진짜 분기)."""
    return [f for f in raw
            if QUARTER_MIN_DAYS <= _duration_days(f) <= QUARTER_MAX_DAYS]


def _annual_facts(raw: list) -> list:
    """기간 350~380일인 팩트만 (연간)."""
    return [f for f in raw
            if ANNUAL_MIN_DAYS <= _duration_days(f) <= ANNUAL_MAX_DAYS]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -v`
Expected: `6 passed`

- [ ] **Step 5: 커밋**

```bash
git add modules/edgar_fundamentals.py tests/test_edgar_fundamentals.py
git commit -m "feat: edgar 팩트 추출·기간 분류·태그 대체

기간 길이로 분기(80~100일)와 연간(350~380일)을 가른다.
YTD 누적(6·9개월)은 어느 쪽도 아니므로 자동 제외."
```

---

### Task 3: 분기 조립 — 중복 제거 · Q4 유도 · 계약

여기가 핵심이다. `point_in_time_fundamentals`의 `tail(4)`가 옳으려면 이 조립이 계약을 지켜야 한다.

**Files:**
- Modify: `modules/edgar_fundamentals.py`
- Modify: `tests/test_edgar_fundamentals.py`

**Interfaces:**
- Consumes: Task 2
- Produces:
  - `_dedup_earliest(facts: list, key) -> list`
  - `_derive_q4(quarters: list, annuals: list) -> list`
  - `_assemble_tag(us_gaap: dict, tag_chain: list) -> dict` — `{end_str: (filed_str, val_float)}`
  - `assemble_income(us_gaap: dict) -> pd.DataFrame` — index=filed, cols=[revenue, operating_income, net_income]
  - `assemble_shares(us_gaap: dict) -> pd.Series` — index=filed, 희석주식수

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_edgar_fundamentals.py` 끝에 추가:

```python
def test_dedup_keeps_earliest_filed():
    """같은 (start,end)가 여러 filed로 오면 가장 이른 것만 남긴다."""
    facts = [
        _fact("2020-01-01", "2020-03-31", 10, "2020-05-01"),
        _fact("2020-01-01", "2020-03-31", 11, "2021-05-01"),  # 이듬해 비교 재게시
    ]
    out = ef._dedup_earliest(facts, key=lambda f: (f["start"], f["end"]))
    assert len(out) == 1 and out[0]["filed"] == "2020-05-01"


def test_derive_q4_when_absent():
    """Q1~Q3 분기 + 연간이 있고 Q4가 없으면 Q4 = FY - (Q1+Q2+Q3)."""
    quarters = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 2, "2020-08-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
    ]
    annuals = [_fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K")]
    out = ef._derive_q4(quarters, annuals)
    q4 = [f for f in out if f["end"] == "2020-12-31"]
    assert len(q4) == 1
    assert q4[0]["val"] == 10 - (1 + 2 + 3)   # 4
    assert q4[0]["filed"] == "2021-02-01"


def test_no_derive_when_q4_already_present():
    """Q4가 이미 3개월 팩트로 있으면 유도하지 않는다."""
    quarters = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 2, "2020-08-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
        _fact("2020-10-01", "2020-12-31", 4, "2021-02-01"),
    ]
    annuals = [_fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K")]
    out = ef._derive_q4(quarters, annuals)
    q4 = [f for f in out if f["end"] == "2020-12-31"]
    assert len(q4) == 1 and q4[0]["val"] == 4   # 원본 유지, 유도 안 함


def test_no_derive_when_quarter_missing():
    """Q1~Q3 중 하나라도 없으면 Q4를 만들지 않는다 — 추측 금지."""
    quarters = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
    ]
    annuals = [_fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K")]
    out = ef._derive_q4(quarters, annuals)
    assert not [f for f in out if f["end"] == "2020-12-31"]


def _income_ug():
    """Q1~Q3 분기 + 연간을 세 손익 태그 모두에 담은 us-gaap 목."""
    def series(base):
        return {"units": {"USD": [
            _fact("2020-01-01", "2020-03-31", base + 1, "2020-05-01"),
            _fact("2020-04-01", "2020-06-30", base + 2, "2020-08-01"),
            _fact("2020-07-01", "2020-09-30", base + 3, "2020-11-01"),
            _fact("2020-01-01", "2020-12-31", base + 10, "2021-02-01", form="10-K"),
        ]}}
    return {
        "RevenueFromContractWithCustomerExcludingAssessedTax": series(100),
        "OperatingIncomeLoss": series(10),
        "NetIncomeLoss": series(0),
    }


def test_assemble_income_contract():
    """조립 결과가 '분기당 1행' 계약을 지킨다."""
    df = ef.assemble_income(_income_ug())
    # 4개 분기(Q1~Q3 + 유도 Q4)
    assert len(df) == 4
    assert list(df.columns) == ["revenue", "operating_income", "net_income"]
    # 인덱스(filed) 단조 비감소
    assert df.index.is_monotonic_increasing
    # net_income TTM = 1+2+3+4(유도) = 10
    assert df["net_income"].sum() == 0 + 1 + 2 + 3 + (10 - 6)


def test_assemble_shares_no_q4_derivation():
    """주식수는 분기 팩트만 쓰고 Q4를 유도하지 않는다 (합산 대상이 아님)."""
    ug = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        _fact("2020-01-01", "2020-03-31", 1000, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 1010, "2020-08-01"),
    ]}}}
    s = ef.assemble_shares(ug)
    assert list(s.values) == [1000, 1010]
    assert s.index.is_monotonic_increasing
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -k "dedup or derive or assemble" -v`
Expected: FAIL — `AttributeError: ... '_dedup_earliest'`

- [ ] **Step 3: 조립 로직 구현**

`modules/edgar_fundamentals.py`의 `_annual_facts` 아래에 추가:

```python
def _dedup_earliest(facts: list, key) -> list:
    """key가 같은 팩트 중 가장 이른 filed만 남긴다 (최초 공시 기준)."""
    best = {}
    for f in facts:
        k = key(f)
        if k not in best or f["filed"] < best[k]["filed"]:
            best[k] = f
    return list(best.values())


def _derive_q4(quarters: list, annuals: list) -> list:
    """
    연간 팩트마다 그 안에 든 분기가 정확히 3개이고 Q4가 없으면
    Q4 = 연간 - (3개 분기 합)으로 유도한다.
    Q4.start = 셋 중 가장 늦은 end, Q4.end = 연간 end, Q4.filed = 연간 filed.
    3개가 아니거나 Q4가 이미 있으면 유도하지 않는다 (추측 금지).
    """
    tol = timedelta(days=CONTAINMENT_TOL_DAYS)
    derived = []
    for a in annuals:
        As = date.fromisoformat(a["start"])
        Ae = date.fromisoformat(a["end"])
        inside = [q for q in quarters
                  if date.fromisoformat(q["start"]) >= As - tol
                  and date.fromisoformat(q["end"]) <= Ae + tol]
        if any(abs((date.fromisoformat(q["end"]) - Ae).days) <= CONTAINMENT_TOL_DAYS
               for q in inside):
            continue  # Q4 이미 존재
        if len(inside) == 3:
            q4_start = max(date.fromisoformat(q["end"]) for q in inside)
            derived.append({
                "start": q4_start.isoformat(),
                "end":   a["end"],
                "filed": a["filed"],
                "val":   a["val"] - sum(q["val"] for q in inside),
                "form":  "DERIVED",
            })
    return quarters + derived


def _assemble_tag(us_gaap: dict, tag_chain: list) -> dict:
    """한 손익 태그를 {end_str: (filed_str, val)}로 조립. 분기 + 유도 Q4."""
    raw = _facts_for_chain(us_gaap, tag_chain, "USD")
    if not raw:
        return {}
    quarters = _dedup_earliest(_quarter_facts(raw), key=lambda f: (f["start"], f["end"]))
    annuals  = _dedup_earliest(_annual_facts(raw),  key=lambda f: (f["start"], f["end"]))
    allq = _derive_q4(quarters, annuals)
    out = {}
    for f in sorted(allq, key=lambda f: f["filed"]):
        out[f["end"]] = (f["filed"], float(f["val"]))  # 같은 end면 늦은 filed가 덮되, dedup으로 이미 유일
    return out


def assemble_income(us_gaap: dict) -> pd.DataFrame:
    """
    us-gaap → 분기 손익 DataFrame (index=filed, cols=[revenue, operating_income, net_income]).
    같은 분기의 세 태그는 같은 공시(filed)에서 오므로 end 기준으로 정렬한다.
    """
    cols = {name: _assemble_tag(us_gaap, chain) for name, chain in TAG_CHAINS.items()}
    ends = sorted(set().union(*[set(c) for c in cols.values()])) if any(cols.values()) else []
    if not ends:
        return pd.DataFrame()

    rows = []
    for end in ends:
        fileds = [cols[n][end][0] for n in cols if end in cols[n]]
        row = {"filed": pd.Timestamp(min(fileds))}
        for n in cols:
            row[n] = cols[n][end][1] if end in cols[n] else np.nan
        rows.append(row)

    df = pd.DataFrame(rows).set_index("filed").sort_index()
    return df[["revenue", "operating_income", "net_income"]].dropna(how="all")


def assemble_shares(us_gaap: dict) -> pd.Series:
    """us-gaap → 희석주식수 Series (index=filed). 분기 팩트만, Q4 유도 없음."""
    raw = _facts_for_chain(us_gaap, SHARES_TAGS, "shares")
    if not raw:
        return pd.Series(dtype=float)
    quarters = _dedup_earliest(_quarter_facts(raw), key=lambda f: (f["start"], f["end"]))
    data = {}
    for f in sorted(quarters, key=lambda f: f["filed"]):
        data[pd.Timestamp(f["filed"])] = float(f["val"])
    return pd.Series(data).sort_index()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -v`
Expected: `13 passed`

- [ ] **Step 5: 커밋**

```bash
git add modules/edgar_fundamentals.py tests/test_edgar_fundamentals.py
git commit -m "feat: edgar 분기 조립 — 중복 제거·Q4 유도·계약

point_in_time_fundamentals의 tail(4)가 요구하는 '분기당 1행' 계약을
데이터 생성 측에서 보장한다. 최초 공시 기준 중복 제거, Q4는
FY-(Q1+Q2+Q3)로 유도(3개 다 있고 Q4 부재일 때만)."
```

---

### Task 4: 공개 API + 커버리지 보고

**Files:**
- Modify: `modules/edgar_fundamentals.py`
- Modify: `tests/test_edgar_fundamentals.py`

**Interfaces:**
- Consumes: Task 1·3
- Produces:
  - `fetch_quarterly_fundamentals_history(tickers, reporting_lag_days=None) -> dict`
  - `fetch_shares_history(tickers, start=None) -> dict`
  - `last_coverage() -> dict` — `{"requested", "resolved", "failed", "metric_coverage"}`

`reporting_lag_days`·`start` 인자는 기존 시그니처 호환용으로 받되 무시한다 (EDGAR는 실제 filed를 준다).

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_edgar_fundamentals.py` 끝에 추가:

```python
def test_public_api_uses_disk_cache_once(monkeypatch):
    """두 공개 함수가 load_raw를 통해 종목당 한 번만 원본을 읽는다."""
    ug = _income_ug()
    ug["WeightedAverageNumberOfDilutedSharesOutstanding"] = {"units": {"shares": [
        _fact("2020-01-01", "2020-03-31", 1000, "2020-05-01"),
    ]}}
    seen = []

    def fake_load_raw(tk, cache_dir=None):
        seen.append(tk)
        return ug if tk == "AAPL" else None

    monkeypatch.setattr(ef, "load_raw", fake_load_raw)

    fin = ef.fetch_quarterly_fundamentals_history(["AAPL", "DEAD"])
    assert not fin["AAPL"].empty
    assert fin["DEAD"].empty
    cov = ef.last_coverage()
    assert cov["requested"] == 2
    assert cov["resolved"] == 1
    assert cov["failed"] == ["DEAD"]
    assert 0.0 <= cov["metric_coverage"]["net_income"] <= 1.0


def test_fetch_shares_history_returns_series(monkeypatch):
    ug = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        _fact("2020-01-01", "2020-03-31", 1000, "2020-05-01"),
    ]}}}
    monkeypatch.setattr(ef, "load_raw", lambda tk, cache_dir=None: ug)
    out = ef.fetch_shares_history(["AAPL"])
    assert isinstance(out["AAPL"], pd.Series)
    assert out["AAPL"].iloc[-1] == 1000
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -k "public_api or shares_history" -v`
Expected: FAIL — `AttributeError: ... 'fetch_quarterly_fundamentals_history'`

- [ ] **Step 3: 공개 API 구현**

`modules/edgar_fundamentals.py` 끝에 추가:

```python
_last_coverage = {"requested": 0, "resolved": 0, "failed": [], "metric_coverage": {}}


def last_coverage() -> dict:
    """직전 fetch_quarterly_fundamentals_history의 커버리지."""
    return dict(_last_coverage)


def fetch_quarterly_fundamentals_history(tickers: list, reporting_lag_days=None) -> dict:
    """
    분기 손익 이력. 반환 {ticker: DataFrame(index=filed, cols=[revenue,operating_income,net_income])}.
    reporting_lag_days는 기존 시그니처 호환용 — EDGAR는 실제 filed를 주므로 무시.
    """
    global _last_coverage
    result, failed = {}, []
    metric_hit = {m: 0 for m in TAG_CHAINS}

    for tk in tickers:
        ug = load_raw(tk)
        if ug is None:
            failed.append(tk)
            result[tk] = pd.DataFrame()
            continue
        df = assemble_income(ug)
        result[tk] = df
        if df.empty:
            failed.append(tk)
        else:
            for m in TAG_CHAINS:
                if m in df.columns and df[m].notna().any():
                    metric_hit[m] += 1

    n = len(tickers) or 1
    _last_coverage = {
        "requested": len(tickers),
        "resolved":  len(tickers) - len(failed),
        "failed":    failed,
        "metric_coverage": {m: round(c / n, 3) for m, c in metric_hit.items()},
    }

    print(f"[edgar] 확보 {_last_coverage['resolved']}/{len(tickers)}종목")
    if failed:
        print(f"[edgar] 실패 {len(failed)}종목: {', '.join(failed[:20])}"
              + (" ..." if len(failed) > 20 else ""))
    for m, frac in _last_coverage["metric_coverage"].items():
        flag = "  <-- 70% 미만, 편향 위험" if frac < MIN_COVERAGE else ""
        print(f"[edgar] {m} 커버리지 {frac:.0%}{flag}")

    return result


def fetch_shares_history(tickers: list, start=None) -> dict:
    """희석주식수 이력. 반환 {ticker: Series(index=filed)}. start는 호환용, 무시."""
    result = {}
    for tk in tickers:
        ug = load_raw(tk)
        result[tk] = assemble_shares(ug) if ug is not None else pd.Series(dtype=float)
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_edgar_fundamentals.py -v`
Expected: `15 passed`

- [ ] **Step 5: 커밋**

```bash
git add modules/edgar_fundamentals.py tests/test_edgar_fundamentals.py
git commit -m "feat: edgar 공개 API + 커버리지 보고

fetch_quarterly_fundamentals_history / fetch_shares_history를
기존 시그니처 호환으로 제공. 지표별 커버리지를 계산해 70% 미만이면
편향 위험을 경고한다 (태그 표준화는 대형주에 유리 → 저커버리지는
대형주 편향 표본)."
```

---

### Task 5: factor_validator 배선 교체 + 계약 문서화

**Files:**
- Modify: `modules/factor_validator.py:25-28` (import 블록)
- Modify: `modules/factor_engine.py` (yfinance 기반 두 history 함수 제거, `point_in_time_fundamentals` docstring에 계약 명시)

**Interfaces:**
- Consumes: Task 4의 `fetch_quarterly_fundamentals_history`, `fetch_shares_history`
- Produces: 없음

- [ ] **Step 1: import 출처 교체**

`modules/factor_validator.py`의 import 블록을 찾는다:

```python
from modules.factor_engine import (
    fetch_quarterly_fundamentals_history as _fetch_fin_hist,
    fetch_shares_history                 as _fetch_shares_hist,
    point_in_time_fundamentals           as _pit_fundamentals,
)
```

이렇게 바꾼다 (재무 이력은 EDGAR, PIT 계산은 factor_engine 그대로):

```python
from modules.factor_engine import point_in_time_fundamentals as _pit_fundamentals
from modules.edgar_fundamentals import (
    fetch_quarterly_fundamentals_history as _fetch_fin_hist,
    fetch_shares_history                 as _fetch_shares_hist,
)
```

- [ ] **Step 2: factor_engine의 yfinance history 함수 제거 확인**

먼저 이 두 함수를 다른 곳이 쓰는지 확인한다.

Run: `grep -rn "fetch_quarterly_fundamentals_history\|fetch_shares_history" --include=*.py | grep -v test | grep -v edgar_fundamentals`
Expected: `factor_engine.py`의 정의부만 나온다 (factor_validator는 Step 1에서 edgar로 옮겼으므로 더 이상 안 나옴). app.py 등 다른 소비자가 나오면 중단하고 사람에게 보고.

- [ ] **Step 3: factor_engine에서 두 함수 삭제**

`modules/factor_engine.py`의 `fetch_quarterly_fundamentals_history`와 `fetch_shares_history` 함수 전체(184~242행 부근)를 삭제한다. `import yfinance`는 라이브 경로(`fetch_fundamentals`)가 계속 쓰므로 남긴다.

- [ ] **Step 4: point_in_time_fundamentals docstring에 계약 명시**

`modules/factor_engine.py`의 `point_in_time_fundamentals` docstring을 아래로 교체:

```python
    """
    as_of_date 기준 look-ahead-free 재무 지표 계산.

    전제 계약 (edgar_fundamentals.assemble_income이 보장):
      - fin_hist[tk]는 분기당 정확히 1행 (중복 기간 없음)
      - 인덱스는 실제 공시일(filed), 단조 비감소
      - 따라서 tail(4)가 서로 다른 4개 분기 = TTM으로 유효하다
    이 계약이 깨지면 (예: 중복 분기, 연간 행 혼입) TTM이 조용히 부풀려진다.

    반환: {"pe": float, "margin": float}
    """
```

- [ ] **Step 5: 소규모 실동작 확인 (네트워크 사용)**

Run:

```bash
python -c "
from modules.factor_validator import run_per_factor_ic_analysis
r = run_per_factor_ic_analysis(['AAPL','MSFT','NVDA','GOOGL','AMZN','META','JPM','XOM','JNJ','PG'], lookback_years=2)
for f, s in r.items():
    print(f, 'n=', s['n'], 'mean_ic=', s['mean_ic'])
"
```

Expected: `[edgar] 확보 .../10종목` 출력 후, **`value`와 `quality`의 `n`이 0이 아님** (기존엔 n=0이었다). XOM이 CIK 교정 덕에 확보되는지도 확인. 첫 실행은 원본 다운로드로 다소 걸리고, 두 번째는 디스크 캐시로 빠르다.

- [ ] **Step 6: 전체 테스트**

Run: `python -m pytest tests/ -q`
Expected: 기존 19 + edgar 15 = `34 passed`

- [ ] **Step 7: 커밋**

```bash
git add modules/factor_validator.py modules/factor_engine.py
git commit -m "refactor: PIT 재무를 yfinance에서 EDGAR로 교체

factor_validator가 edgar_fundamentals를 쓰도록 배선 교체.
factor_engine의 yfinance 기반 history 함수 2개 제거(라이브 경로는 유지).
point_in_time_fundamentals에 tail(4)의 전제 계약을 명시.

value/quality가 처음으로 측정 가능해진다 (yfinance 5분기 한계 해소)."
```

---

### Task 6: 워크플로 캐시 연동

**Files:**
- Modify: `.github/workflows/ic-update.yml`

**Interfaces:**
- Consumes: Task 1의 `RAW_DIR` (`data/edgar_raw/`)
- Produces: 없음

- [ ] **Step 1: EDGAR 원본 캐시 스텝 추가**

`.github/workflows/ic-update.yml`의 "가격 패널 캐시 복원" 스텝 뒤에 삽입:

```yaml
      - name: EDGAR 원본 캐시 복원
        uses: actions/cache@v4
        with:
          path: data/edgar_raw/
          # 원본 JSON은 크고(약 1.4GB) 재수집이 비싸다. run_id로 저장,
          # restore-keys로 직전 것 복원. 주간 실행이라 7일 경계에서
          # 만료될 수 있으나, 만료 시 edgar_fundamentals가 재수집하므로
          # 실패하지 않고 느려질 뿐이다.
          key: edgar-raw-v1-${{ github.run_id }}
          restore-keys: |
            edgar-raw-v1-
```

- [ ] **Step 2: YAML 문법 확인**

Run:

```bash
python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/ic-update.yml', encoding='utf-8'))
steps = [s.get('name') for s in d['jobs']['update-ic-weights']['steps']]
print(steps)
assert 'EDGAR 원본 캐시 복원' in steps
print('OK')
"
```

Expected: 스텝 목록에 `EDGAR 원본 캐시 복원`이 있고 `OK` 출력.

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/ic-update.yml
git commit -m "ci: ic-update에 EDGAR 원본 캐시 연동

actions/cache로 data/edgar_raw/를 실행 간 공유. 만료 시에도
edgar_fundamentals가 재수집하므로 실패하지 않는다."
```

---

### Task 7: 전체 실행과 결과 판정

**Files:**
- Modify: `ic_weights.json` (실행 산출물)

**Interfaces:**
- Consumes: Task 1~6 전부
- Produces: 없음

- [ ] **Step 1: 전체 테스트**

Run: `python -m pytest tests/ -q`
Expected: `34 passed`

- [ ] **Step 2: 276종목 전체 실행**

Run: `PYTHONIOENCODING=utf-8 python ic_weight_updater.py`
Expected: 첫 실행은 EDGAR 원본 다운로드(약 4~5분) + IC 계산. `[edgar]` 커버리지 로그 확인 — net_income·revenue·operating_income이 각각 70% 이상이어야 value/quality를 신뢰할 수 있다.

- [ ] **Step 3: value/quality 측정 확인**

Run:

```bash
PYTHONIOENCODING=utf-8 python -c "
import json
d = json.load(open('ic_weights.json', encoding='utf-8'))
print(f\"{'factor':<10}{'mean_ic':>10}{'icir':>9}{'n':>6}\")
for f, s in d['per_factor_ic'].items():
    print(f\"{f:<10}{s['mean_ic']:>10.4f}{s['icir']:>9.3f}{s['n']:>6}\")
print('unavailable:', d['ic_unavailable_factors'])
"
```

Expected: **`value`와 `quality`의 `n`이 0이 아님** (성공 기준 2). `ic_unavailable_factors`에서 두 팩터가 빠졌는지 확인.

- [ ] **Step 4: 결과 판정**

성공 기준 3이다. 유니버스 확대 때와 같은 기준으로 `ICIR`을 본다:

- `|ICIR| >= 0.5` → 활용 가능한 신호
- `0.2 <= |ICIR| < 0.5` → 약하지만 존재
- `|ICIR| < 0.2` → 유의한 신호 없음

value/quality가 유의하게 나오면 신호원 문제의 상당 부분이 풀린다. 안 나오면 #1(신호원 교체)의 범위가 커진다. **어느 쪽이든 숫자를 좋게 만들려 파라미터를 조정하지 않는다** — 결과를 그대로 보고한다.

커버리지가 70% 미만인 지표가 있으면 그 IC는 대형주 편향 표본이므로 신뢰도를 낮춰 보고한다.

- [ ] **Step 5: 커밋**

```bash
git add ic_weights.json
git commit -m "chore: EDGAR PIT 재무로 value/quality IC 최초 산출

yfinance 5분기 한계로 n=0이던 두 팩터를 EDGAR 20년치로 측정."
```

- [ ] **Step 6: 사용자 보고**

Step 3의 표와 Step 4의 판정을 보고한다. value/quality의 `n`, `ICIR`, 각 지표 커버리지를 명시한다.

---

## 완료 후 남는 것 (이번 범위 밖)

- **stockholders_equity / PIT p/b**: 데이터 계층은 이 모듈로 쉽게 확장 가능하나, 활성화는 `point_in_time_fundamentals`와 `_calc_per_factor_zscores` 수정이 필요. #1(신호원 교체)에서.
- **신규 펀더멘털 팩터**: 총이익률·부채비율·FCF수익률·발생액·자산성장률 등. 원본이 로컬에 있어 추출 코드만 추가하면 된다. #1 대상.
- **커버리지 70% 하드 게이팅**: 현재는 경고만 출력하고 판정은 사람이 한다. 자동 차단은 ic_weight_updater 교차 로직이 필요 — n=0(전면 부재)은 이미 처리되고, 부분 편향은 판단 영역이라 가시화에 그친다.
- **정정 공시**: 최초 공시 기준이라 정정본은 무시. 엄밀 PIT는 인터페이스 재설계 필요.
- **app.py 라이브 경로**: 실시간 재무 조회는 yfinance 유지. 이번엔 백테스트/PIT 경로만 교체.
- **IC_FLOOR 로직 재설계(#3)**: value/quality 실측 IC가 생긴 뒤 착수.
