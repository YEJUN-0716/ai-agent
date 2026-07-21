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

# company_tickers.json이 잘못된 CIK를 주는 종목의 수동 교정.
# XOM은 2115436(수수료신고 ffd만)이 아니라 34088(us-gaap 438태그)이다.
# 다른 종목은 us-gaap 검증에서 실패로 잡혀 coverage에 드러난다 — 발견 시 여기 추가.
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
        out[f["end"]] = (f["filed"], float(f["val"]))  # dedup으로 end는 이미 유일
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
