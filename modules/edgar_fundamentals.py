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
