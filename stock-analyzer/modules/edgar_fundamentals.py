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
# 캐시 나이 상한(일). 캐시 파일이 있으면 나이를 안 보던 옛 동작은, 러너가 캐시를
# 계속 물려받는 구조와 만나면 "재무가 어느 시점에 멈춰 있는지 아무도 모르는" 상태가
# 된다(실측 2026-08-22: 로컬 캐시가 7/21 것이라 261종목 중 224종목의 2분기 공시가
# 없었다). IC 는 주간 배치라 7일이면 다음 회차에 새 공시가 반드시 들어온다.
CACHE_MAX_AGE_DAYS = 7

# 손익·현금흐름 태그 — 전부 duration(기간) 팩트. 분기 필터 + Q4 유도 기계를 공유한다.
TAG_CHAINS = {
    "revenue":          ["RevenueFromContractWithCustomerExcludingAssessedTax",
                         "Revenues", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income":       ["NetIncomeLoss"],
    # 영업현금흐름 — FCF = OCF - CapEx 계산용
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    # 설비투자(CapEx) — 지출이라 양수로 보고됨 → FCF에서 차감
    "capex":            ["PaymentsToAcquirePropertyPlantAndEquipment",
                         "PaymentsForCapitalImprovements"],
    # 아래 셋은 안전성·F-Score 재료다. assemble_income 에는 안 들어간다 —
    # 열은 _INCOME_COLS 가 정한다. 필요한 쪽이 _assemble_metric 으로 직접 부른다.
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating",
                         "InterestExpenseDebt", "InterestIncomeExpenseNet"],
    "cost_of_revenue":  ["CostOfGoodsAndServicesSold", "CostOfRevenue",
                         "CostOfGoodsSold", "CostOfServices"],
    "gross_profit":     ["GrossProfit"],
}
_INCOME_COLS = ["revenue", "operating_income", "net_income",
                "operating_cash_flow", "capex"]

# 같은 줄을 **다른 정의**로 내는 발행사가 있다 — GS 의 매출은 `Revenues` 가 아니라
# `RevenuesNetOfInterestExpense`, ITW 의 순이익은 `NetIncomeLoss` 가 아니라
# `ProfitLoss`, ADP 의 CapEx 는 `PaymentsToAcquireOtherPropertyPlantAndEquipment` 다.
#
# TAG_CHAINS 는 **시대 승계** 태그라(구 SalesRevenueNet → 신 RevenueFromContract…)
# 합쳐도 같은 정의지만, 아래는 정의 자체가 다르다 — 합치면 소수주주지분·이자수익만큼
# 값이 튀어 있지도 않은 YoY 점프가 생긴다. **종목마다 하나만 고른다**(_assemble_metric).
#
# 은행의 총이자수익(InterestAndDividendIncomeOperating)은 일부러 뺐다 — 분기 수로는
# 이기지만 순영업수익과 다른 줄이라, 커버리지를 벌면서 정의를 떨어뜨린다.
ALT_TAGS = {
    "revenue":    ["RevenuesNetOfInterestExpense",
                   "RegulatedAndUnregulatedOperatingRevenue",
                   "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "capex":      ["PaymentsToAcquireOtherPropertyPlantAndEquipment",
                   "PaymentsToAcquireProductiveAssets",
                   "PaymentsToAcquireMachineryAndEquipment"],
}
# 고르는 자는 세 단계다: (1) 아직 살아 있는가 (2) 최근 분기 수 (3) 전체 분기 수.
# 셋 다 필요하다 — 2018년에 끊긴 태그가 총량으로 이기고(HAL·TJX), 2023년에 끊긴
# 태그는 '2019년 이후 분기 수'로도 이긴다(MRK CapEx: 죽은 태그 18 vs 산 태그 14).
# 동수일 때 전체로 갈라야 짧은 태그가 20년 이력을 통째로 버리지 않는다(BBY).
TAG_PICK_SINCE = "2019-01-01"
LIVE_TAG_DAYS = 400   # 후보 중 가장 최근 분기말에서 이만큼 안이면 '살아 있다'
# 현금흐름표는 10-Q에 **누적(YTD)** 으로 실린다 — Q2는 6개월치, Q3는 9개월치다.
# 분기 필터(80~100일)에 걸리는 건 Q1뿐이라 그대로 쓰면 AAPL 영업현금흐름이
# 20년에 18행(연 1행)으로 줄어 TTM이 성립하지 않는다. 이 둘만 _ytd_quarters로
# 같은 회계연도끼리 차분해 분기값을 만든다.
YTD_TAGS = ("operating_cash_flow", "capex")
YTD_MAX_DAYS = 380
SHARES_TAGS = ["WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"]
# 자본총계 — ROE = net_income_TTM / equity 계산용. instant(시점) 팩트라
# duration 필터에 안 걸리므로 별도 조립기(_assemble_instant)를 쓴다.
EQUITY_TAGS = ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]

# 대차대조표(instant) 재료 — 안전성(D/E·유동비율)·F-Score(ROA·레버리지·유동성).
# **여기서는 분기 수로 고르지 않는다. 목록 순서가 정의 우선순위다.**
# 자본총계를 손익처럼 분기 수로 고르면 두 태그를 다 내는 230종목 중 152종목이
# 소수주주지분 포함 태그로 넘어간다(실측). ROE 분자는 지배주주 순이익이라
# 분모만 바뀌면 그건 커버리지가 아니라 어긋남이다. 손익 쪽 ALT_TAGS 와 규칙이
# 다른 이유는 거기엔 우선순위가 없기 때문이다 — GS 는 `Revenues` 자체가 없다.
BALANCE_TAGS = {
    "equity":              EQUITY_TAGS,
    "assets":              ["Assets"],
    "current_assets":      ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "long_term_debt":      ["LongTermDebtNoncurrent", "LongTermDebt",
                            "LongTermDebtAndCapitalLeaseObligations"],
    "short_term_debt":     ["DebtCurrent", "ShortTermBorrowings",
                            "LongTermDebtCurrent", "OtherShortTermBorrowings"],
}
# 조립기는 여기 두지 않는다 — 대차대조표를 쓰는 쪽(scripts/measure_fscore.py)이
# 회계 시점(end) 인덱스를 원해서 `_assemble_instant` 를 직접 부른다. filed 인덱스
# 판(옛 `assemble_balance`)은 부르는 코드가 없어 걷어냈다. 필요해지면 git 에 있다.

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
    """
    companyfacts JSON. 429/503과 네트워크 예외는 지수 백오프 3회 재시도.
    최종 실패 시 None을 반환해 호출자가 그 종목만 제외하도록 한다
    (276개 순차 요청 중 하나의 예외로 IC 전체가 죽지 않게).
    """
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=SEC_HEADERS, timeout=60)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
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


def _read_cached(path: str):
    """캐시 파일의 dict. 없거나 손상됐으면 None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # 손상 시 호출부가 재수집한다


def _is_stale(path: str, max_age_days) -> bool:
    """캐시 파일이 max_age_days 보다 오래됐는가. None 이면 늙지 않는다."""
    if max_age_days is None:
        return False
    try:
        return (time.time() - os.path.getmtime(path)) > max_age_days * 86400
    except OSError:
        return True


def load_raw(ticker: str, cache_dir: str = None, cik: int = None,
             max_age_days: int = None):
    """
    ticker의 us-gaap 팩트 dict를 반환한다. 없거나 무효면 None.
    유효할 때만 디스크에 캐시한다 (무효는 다음 실행에서 재시도 가능하도록).

    `cik`을 주면 티커 조회를 건너뛴다. **상장폐지 종목에는 필수다** — `get_cik`은
    SEC의 *현재* 매핑이라 죽은 티커는 없거나, 더 나쁘게는 그 티커를 물려받은
    다른 회사를 준다(BBBY: 886158 파산 → 1130713 재상장).

    `max_age_days`: 캐시가 이보다 오래됐으면 다시 받는다. **기본은 None(안 늙음)
    이다** — 사전등록 측정(quant_pit 백필 등)은 같은 입력으로 다시 돌 수 있어야
    하므로 캐시를 마음대로 갱신하면 안 된다. 갱신이 필요한 쪽(IC 진입점
    `fetch_*_history`)만 `CACHE_MAX_AGE_DAYS` 를 넘긴다. 파일이 있는데 갱신에
    실패하면 **낡은 캐시를 그대로 돌려준다** — 네트워크 한 번에 종목이 통째로
    빠져 커버리지가 흔들리는 것보다 낫다.
    """
    cache_dir = cache_dir or RAW_DIR
    if cik is None:
        cik = get_cik(ticker)
    if cik is None:
        return None

    path   = os.path.join(cache_dir, f"CIK{cik:010d}.json")
    cached = _read_cached(path)
    if cached is not None and not _is_stale(path, max_age_days):
        return cached

    time.sleep(RATE_SLEEP)
    raw = _fetch_companyfacts(cik)
    ug  = _valid_us_gaap(raw)
    if ug is None:
        return cached

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
    """
    대체 목록의 모든 태그를 병합해 반환한다.
    발행사가 연도별로 태그를 바꾸는 경우(구 SalesRevenueNet/Revenues →
    신 RevenueFromContractWithCustomerExcludingAssessedTax, 2017~2018 전환)가
    많아, 첫 태그만 쓰면 과거 이력이 조용히 잘린다. 겹치는 (start,end)는
    상위 _dedup_earliest가 최초 공시 기준으로 정리한다.
    """
    merged = []
    for tag in tag_chain:
        node = us_gaap.get(tag)
        if node and unit in node.get("units", {}):
            merged.extend(node["units"][unit])
    return merged


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
    연간 팩트마다 그 안에 든 분기가 정확히 3개이면
    Q4 = 연간 - (3개 분기 합)으로 유도한다.
    Q4.start = 셋 중 가장 늦은 end, Q4.end = 연간 end, Q4.filed = 연간 filed.
    3개가 아니면 유도하지 않는다 (추측 금지).

    Q4가 3개월 팩트로 이미 있어도 그게 **연간보다 늦게** 공시됐으면 유도한다.
    CAT의 2024 4분기가 그렇다 — 10-K(2025-02-14)에 연간만 실리고 그 분기 팩트는
    14개월 뒤 비교표시 8-K(2026-03-26)에 처음 나온다. 그대로 두면 2025년 내내
    TTM에 구멍이 난다. 두 후보 중 무엇을 쓸지는 _assemble_tag가 최초 공시로 고른다.
    """
    tol = timedelta(days=CONTAINMENT_TOL_DAYS)
    derived = []
    for a in annuals:
        As = date.fromisoformat(a["start"])
        Ae = date.fromisoformat(a["end"])
        inside = [q for q in quarters
                  if date.fromisoformat(q["start"]) >= As - tol
                  and date.fromisoformat(q["end"]) <= Ae + tol]
        is_q4 = lambda q: abs((date.fromisoformat(q["end"]) - Ae).days) <= CONTAINMENT_TOL_DAYS
        existing = [q for q in inside if is_q4(q)]
        if existing and min(q["filed"] for q in existing) <= a["filed"]:
            continue  # Q4가 이미 같거나 더 이르게 공시돼 있다 — 보고된 값을 쓴다
        inside = [q for q in inside if not is_q4(q)]
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
    """한 손익 태그를 {end_str: (filed_str, val)}로 조립. 분기 + 유도 Q4.

    같은 분기말이 두 번 나오면 **최초 공시**를 남긴다. _dedup_earliest는 (start,end)
    기준이라 유도 Q4에는 성립하지 않는다 — 다음 해 10-K가 비교표시로 같은 연도를
    다시 싣고 그 start가 하루 어긋나면 Q4가 한 번 더 유도되고 filed가 **1년 뒤**로
    밀린다(CAT의 2024-12-31 분기가 filed 2026-03-26으로 찍혀 2025년 내내 TTM에
    구멍이 났다). 시점 데이터에서는 최초 공시가 사실이다.
    """
    raw = _facts_for_chain(us_gaap, tag_chain, "USD")
    if not raw:
        return {}
    quarters = _dedup_earliest(_quarter_facts(raw), key=lambda f: (f["start"], f["end"]))
    annuals  = _dedup_earliest(_annual_facts(raw),  key=lambda f: (f["start"], f["end"]))
    allq = _derive_q4(quarters, annuals)
    out = {}
    for f in sorted(allq, key=lambda f: f["filed"]):
        out.setdefault(f["end"], (f["filed"], float(f["val"])))
    return out


def _ytd_quarters(us_gaap: dict, tag_chain: list) -> dict:
    """누적 공시 태그 → {end_str: (filed_str, 그 분기 값)}. 현금흐름표 전용.

    같은 회계연도(start가 같은 것)끼리 모아 앞 누적을 뺀다. 10-K의 연간 팩트도
    start가 같으므로 같은 그룹에 들어가 Q4 = FY − Q3누적으로 저절로 떨어진다.
    """
    raw = [f for f in _facts_for_chain(us_gaap, tag_chain, "USD")
           if 0 < _duration_days(f) <= YTD_MAX_DAYS]
    facts = _dedup_earliest(raw, key=lambda f: (f["start"], f["end"]))
    by_year = {}
    for f in facts:
        by_year.setdefault(f["start"], []).append(f)
    out = {}
    for start, group in by_year.items():
        group.sort(key=lambda f: f["end"])
        prev_end, prev_val = date.fromisoformat(start), 0.0
        for f in group:
            end = date.fromisoformat(f["end"])
            # 앞 누적이 빠진 자리에서 빼면 두 분기 합이 한 분기로 잡힌다.
            # 직전 것과 90일 간격일 때만 분기로 인정한다.
            if QUARTER_MIN_DAYS <= (end - prev_end).days <= QUARTER_MAX_DAYS:
                got = (f["filed"], float(f["val"]) - prev_val)
                if f["end"] not in out or got[0] < out[f["end"]][0]:
                    out[f["end"]] = got
            prev_end, prev_val = end, float(f["val"])
    return out


def _assemble_metric(us_gaap: dict, name: str) -> dict:
    """지표 하나를 {end_str: (filed_str, val)}로. 현금흐름은 누적 차분 경로를 탄다.

    ALT_TAGS 에 대체 정의가 있으면 **조립 후** 분기 수로 하나만 고른다.
    원시 사실 수로 고르면 연간 공시가 없어 Q4가 유도되지 않는 태그가 뽑히고,
    그 종목은 해마다 한 분기가 비어 TTM 이 영영 안 선다(CAT).
    """
    build = _ytd_quarters if name in YTD_TAGS else _assemble_tag
    cands = [build(us_gaap, TAG_CHAINS[name])]
    cands += [build(us_gaap, [t]) for t in ALT_TAGS.get(name, []) if t in us_gaap]
    ends = [max(d) for d in cands if d]
    if not ends:
        return {}
    alive = (date.fromisoformat(max(ends)) - timedelta(days=LIVE_TAG_DAYS)).isoformat()
    return max(cands, key=lambda d: (bool(d) and max(d) >= alive,
                                     sum(1 for e in d if e >= TAG_PICK_SINCE), len(d)))


def assemble_income(us_gaap: dict) -> pd.DataFrame:
    """
    us-gaap → 분기 손익·현금흐름 DataFrame
    (index=filed, cols=[end, revenue, operating_income, net_income, operating_cash_flow, capex]).

    `end`(분기말)을 열로 싣는다. 소비 측이 tail(4)를 TTM 으로 쓰는데, 분기가
    빠진 구간에서는 그 4행이 1년이 아니다 — 열이 없으면 소비 측이 그걸 알 방법이
    없다(factor_engine.point_in_time_fundamentals).
    같은 분기의 태그들은 같은 공시(filed)에서 오므로 end 기준으로 정렬한다.
    현금흐름 태그는 누적(YTD) 공시라 _ytd_quarters로 따로 조립한다.

    행의 filed는 그 분기 태그들의 **가장 늦은** 공시일이다. 마지막 조각이 나오기
    전에는 그 행 전체를 알 수 없다 — min을 쓰면 look-ahead다.

    행의 순서는 **분기말(end)** 이다. filed 로 세우면 안 된다 — 옛 분기가 뒤늦게
    처음 공시되는 일이 흔해서(NVDA 의 2020-04-26 분기가 2021-05-26 에 처음 나온다)
    filed 순 목록의 마지막 4행이 최근 4분기가 아니게 된다. 소비 측
    `point_in_time_fundamentals` 는 `df[df.index <= as_of].tail(4)` 를 TTM 으로
    쓰는데, 그 마스크는 순서를 안 건드리므로 end 순이면 tail(4)가 곧 최근 4분기다.
    실측(5년 창·261종목): filed 순이면 PIT 관측점의 3.10%(39종목)에서 TTM 창이
    어긋나고 ROE 가 중위 9.5% 틀렸다. `_filed_series`(자본총계·주식수)와
    `quant_pit.quarterly` 가 이미 같은 이유로 회계 시점을 기준으로 세운다.

    인덱스(filed)는 그래서 단조가 아니다 — `<= as_of` **마스크**로만 소비할 것.
    `.loc[:as_of]` 슬라이스는 정렬된 인덱스를 요구하므로 쓰면 안 된다.
    """
    cols = {name: _assemble_metric(us_gaap, name) for name in _INCOME_COLS}
    ends = sorted(set().union(*[set(c) for c in cols.values()])) if any(cols.values()) else []
    if not ends:
        return pd.DataFrame()

    rows = []
    for end in ends:
        fileds = [cols[n][end][0] for n in cols if end in cols[n]]
        row = {"filed": pd.Timestamp(max(fileds)), "end": pd.Timestamp(end)}
        for n in cols:
            row[n] = cols[n][end][1] if end in cols[n] else np.nan
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("end").set_index("filed")
    return df[["end"] + _INCOME_COLS].dropna(how="all", subset=_INCOME_COLS)


def _instant_facts(raw: list) -> list:
    """시점(instant) 팩트만 — start가 없는 대차대조표 항목(자본총계 등)."""
    return [f for f in raw if "start" not in f and "end" in f]


def _assemble_instant(us_gaap: dict, tags: list) -> dict:
    """시점 팩트를 {end_str: (filed_str, val)}로. **목록 순서가 정의 우선순위다.**

    최근(TAG_PICK_SINCE 이후) 값이 있는 **첫** 태그를 쓴다. 하나도 없으면 값이
    있는 첫 태그로 떨어진다. 태그를 합치지 않는 이유는 손익과 같다 — 지배주주
    자본과 소수주주지분 포함 자본이 한 이력 안에서 섞이면 없는 증자가 보인다.
    """
    fallback = {}
    for t in tags:
        instants = _dedup_earliest(_instant_facts(_facts_for_chain(us_gaap, [t], "USD")),
                                   key=lambda f: f["end"])
        d = {f["end"]: (f["filed"], float(f["val"])) for f in instants}
        if any(e >= TAG_PICK_SINCE for e in d):
            return d
        fallback = fallback or d
    return fallback


def _filed_series(d: dict) -> pd.Series:
    """{end: (filed, val)} → filed 인덱스 Series. **같은 filed 를 합치지 않는다.**

    한 공시가 대차대조표 시점을 여럿 싣는 일이 흔하다(첫 공시·비교표시). filed 를
    dict 키로 쓰면 그중 하나만 남는데, 그 하나가 가장 늦은 시점이라는 보장이 없다
    — 실측으로 자본총계 관측 16,984개 중 739개(4.4%)가 이렇게 사라졌고, 남은 값이
    **더 큰 쪽**이라 AMD 2011-02-18 의 자본총계가 1.01B 대신 3.23B(2007년 값)였다.

    (filed, end) 순으로 세우므로 같은 filed 안에서는 가장 늦은 시점이 마지막에
    온다 — 소비 측 `eq[eq.index <= as_of].iloc[-1]` 이 그걸 집는다.
    assemble_income 이 중복 filed 를 그냥 두는 것과 같은 규칙이다.

    이미 아는 시점보다 **옛 시점이 뒤늦게 처음 공시되는** 경우는 뺀다(MTCH 의
    2020-12-31 주식수가 2022-02-24 에 처음 나온다). 남겨 두면 소비 측이 집는
    마지막 값이 1년 전 시점으로 되돌아간다.
    """
    if not d:
        return pd.Series(dtype=float)
    rows, seen_end = [], ""
    for filed, end, val in sorted((f, e, v) for e, (f, v) in d.items()):
        if end <= seen_end:
            continue
        seen_end = end
        rows.append((filed, val))
    return pd.Series([v for _, v in rows],
                     index=pd.to_datetime([f for f, _ in rows]))


def assemble_equity(us_gaap: dict) -> pd.Series:
    """
    us-gaap → 자본총계 Series (index=filed). 대차대조표는 instant 팩트라
    duration 필터가 아니라 _instant_facts로 뽑고, 같은 시점(end)은 최초 공시만 남긴다.
    filed 인덱스라 <= as_of 필터로 look-ahead 없이 소비할 수 있다.
    """
    return _filed_series(_assemble_instant(us_gaap, EQUITY_TAGS))


def assemble_shares(us_gaap: dict) -> pd.Series:
    """us-gaap → 희석주식수 Series (index=filed). 분기 팩트만, Q4 유도 없음.

    자본총계와 같은 이유로 _filed_series 를 쓴다 — 한 공시가 분기를 여럿 실으면
    filed 를 키로 쓰던 옛 코드가 그중 하나만 남겼다(실측 영향은 0.02%로 작았다).
    """
    raw = _facts_for_chain(us_gaap, SHARES_TAGS, "shares")
    if not raw:
        return pd.Series(dtype=float)
    quarters = _dedup_earliest(_quarter_facts(raw), key=lambda f: (f["start"], f["end"]))
    d = {}
    for f in sorted(quarters, key=lambda f: f["filed"]):
        d.setdefault(f["end"], (f["filed"], float(f["val"])))
    return _filed_series(d)


_last_coverage = {"requested": 0, "resolved": 0, "failed": [], "metric_coverage": {}}


def last_coverage() -> dict:
    """직전 fetch_quarterly_fundamentals_history의 커버리지."""
    return dict(_last_coverage)


def fetch_quarterly_fundamentals_history(tickers: list, reporting_lag_days=None) -> dict:
    """
    분기 손익 이력. 반환 {ticker: DataFrame(index=filed, cols=[end,revenue,operating_income,net_income,...])}.
    reporting_lag_days는 기존 시그니처 호환용 — EDGAR는 실제 filed를 주므로 무시.
    """
    global _last_coverage
    result, failed = {}, []
    metric_hit = {m: 0 for m in _INCOME_COLS}

    for tk in tickers:
        ug = load_raw(tk, max_age_days=CACHE_MAX_AGE_DAYS)
        if ug is None:
            failed.append(tk)
            result[tk] = pd.DataFrame()
            continue
        df = assemble_income(ug)
        result[tk] = df
        if df.empty:
            failed.append(tk)
        else:
            for m in _INCOME_COLS:
                if m in df.columns and df[m].notna().any():
                    metric_hit[m] += 1

    n = len(tickers) or 1
    metric_coverage = {m: round(c / n, 3) for m, c in metric_hit.items()}
    _last_coverage = {
        "requested": len(tickers),
        "resolved":  len(tickers) - len(failed),
        "failed":    failed,
        "metric_coverage": metric_coverage,
    }

    print(f"[edgar] 확보 {_last_coverage['resolved']}/{len(tickers)}종목")
    if failed:
        print(f"[edgar] 실패 {len(failed)}종목: {', '.join(failed[:20])}"
              + (" ..." if len(failed) > 20 else ""))
    for m, frac in metric_coverage.items():
        flag = f"  <-- {MIN_COVERAGE:.0%} 미만, 제외" if frac < MIN_COVERAGE else ""
        print(f"[edgar] {m} 커버리지 {frac:.0%}{flag}")

    # 커버리지 미달 지표는 전 종목에서 제외한다. 편향된 부분집합으로 IC를
    # 계산하지 않고 해당 팩터를 n=0(미측정)으로 떨어뜨려, ic_weight_updater가
    # unavailable로 처리하게 한다 (부분 표본으로 자신 있게 틀리느니 "모른다").
    low = [m for m, frac in metric_coverage.items() if frac < MIN_COVERAGE]
    if low:
        print(f"[edgar] 커버리지 미달 지표 IC 제외: {low}")
        for df in result.values():
            if not df.empty:
                for m in low:
                    if m in df.columns:
                        df[m] = np.nan

    return result


def fetch_shares_history(tickers: list, start=None) -> dict:
    """희석주식수 이력. 반환 {ticker: Series(index=filed)}. start는 호환용, 무시."""
    result = {}
    for tk in tickers:
        ug = load_raw(tk, max_age_days=CACHE_MAX_AGE_DAYS)
        result[tk] = assemble_shares(ug) if ug is not None else pd.Series(dtype=float)
    return result


def fetch_equity_history(tickers: list, start=None) -> dict:
    """자본총계 이력. 반환 {ticker: Series(index=filed)}. start는 호환용, 무시.
    ROE = net_income_TTM / equity 계산에 point_in_time_fundamentals가 사용."""
    result = {}
    for tk in tickers:
        ug = load_raw(tk, max_age_days=CACHE_MAX_AGE_DAYS)
        result[tk] = assemble_equity(ug) if ug is not None else pd.Series(dtype=float)
    return result
