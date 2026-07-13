"""
OpenDART 재무 데이터 모듈
=================================================================
한국 상장 종목의 매출·영업이익·자본 데이터를 DART 공시에서 수집.
yfinance가 KRX 종목 재무 정보를 제공하지 못하는 문제를 보완.

환경변수:
  DART_API_KEY  — https://opendart.fss.or.kr 에서 무료 발급

일별 파일 캐시(dart_fund_cache.json)로 API 호출 최소화.
corp_code 매핑(dart_corp_map.json)은 7일마다 갱신.
"""
import io
import json
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import requests

_DART_BASE      = "https://opendart.fss.or.kr/api"
_DART_KEY       = os.environ.get("DART_API_KEY", "")
_BASE_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_CORP_MAP_FILE  = os.path.join(_BASE_DIR, "dart_corp_map.json")
_FUND_CACHE_FILE = os.path.join(_BASE_DIR, "dart_fund_cache.json")

_corp_map: dict = {}


def _load_corp_map() -> dict:
    """stock_code(6자리) → dart corp_code 매핑. 파일 캐시 7일."""
    global _corp_map
    if _corp_map:
        return _corp_map

    if os.path.exists(_CORP_MAP_FILE):
        if time.time() - os.path.getmtime(_CORP_MAP_FILE) < 7 * 86400:
            with open(_CORP_MAP_FILE, encoding="utf-8") as f:
                _corp_map = json.load(f)
            print(f"  [DART] corp_map 캐시 ({len(_corp_map)}개)")
            return _corp_map

    print("  [DART] corp_code.xml 다운로드 중...")
    resp = requests.get(
        f"{_DART_BASE}/corpCode.xml",
        params={"crtfc_key": _DART_KEY},
        timeout=60,
    )
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_name = next(n for n in zf.namelist() if n.upper().endswith(".XML"))
        with zf.open(xml_name) as f:
            tree = ET.parse(f)

    mapping: dict = {}
    for item in tree.getroot().findall(".//list"):
        corp_code  = (item.findtext("corp_code") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        if corp_code and stock_code:
            mapping[stock_code] = corp_code

    _corp_map = mapping
    with open(_CORP_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    print(f"  [DART] corp_map 갱신 ({len(mapping)}개 종목)")
    return _corp_map


def _report_period() -> tuple:
    """가장 최근 공시된 사업보고서 기준 (year, reprt_code)."""
    now = datetime.now()
    # 사업보고서는 결산 후 90일 이내 — 4월 말까지는 전전년도, 5월부터 전년도
    year = now.year - 1
    return str(year), "11011"  # 11011 = 사업보고서


def fetch_krx_fundamentals(tickers: list) -> dict:
    """
    KRX 종목(.KS/.KQ) 재무지표 일괄 조회.

    반환: {
        "005930.KS": {
            "margin":     float | None,   # 영업이익률 (%)
            "net_income": float | None,   # 당기순이익 (원)
            "equity":     float | None,   # 자본총계 (원)
        }, ...
    }

    - 하루 1회 파일 캐시 사용 (API 호출 최소화)
    - DART_API_KEY 없으면 빈 dict 반환
    """
    if not _DART_KEY:
        return {}

    today = datetime.now().strftime("%Y-%m-%d")
    cache: dict = {}
    if os.path.exists(_FUND_CACHE_FILE):
        try:
            with open(_FUND_CACHE_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("date") == today:
                cache = raw.get("data", {})
        except Exception:
            pass

    missing = [tk for tk in tickers if tk not in cache]
    if not missing:
        return {tk: cache.get(tk, {}) for tk in tickers}

    corp_map = _load_corp_map()
    corp_to_tk: dict[str, str] = {}
    corp_codes: list[str] = []
    for tk in missing:
        sc = tk.split(".")[0]          # "005930.KS" → "005930"
        cc = corp_map.get(sc)
        if cc:
            corp_codes.append(cc)
            corp_to_tk[cc] = tk

    year, reprt_code = _report_period()
    all_items: list[dict] = []

    # 20개씩 배치 요청
    for i in range(0, len(corp_codes), 20):
        batch = corp_codes[i : i + 20]
        try:
            resp = requests.get(
                f"{_DART_BASE}/fnlttMultiAcnt.json",
                params={
                    "crtfc_key":  _DART_KEY,
                    "bsns_year":  year,
                    "reprt_code": reprt_code,
                    "corp_code":  ",".join(batch),
                },
                timeout=30,
            )
            data = resp.json()
            if data.get("status") == "000":
                all_items.extend(data.get("list", []))
            else:
                print(f"  [DART] API 오류: {data.get('message')} (배치 {i//20+1})")
        except Exception as e:
            print(f"  [DART] 요청 실패 (배치 {i//20+1}): {e}")

    # 회사별 계정 집계
    company: dict[str, dict] = {}
    for item in all_items:
        cc   = item.get("corp_code", "")
        acct = item.get("account_nm", "")
        raw_amt = (item.get("thstrm_amount") or "0").replace(",", "")
        try:
            amt = float(raw_amt)
        except ValueError:
            continue
        d = company.setdefault(cc, {})
        # 매출액
        if any(k in acct for k in ("수익(매출액)", "매출액")) and "원가" not in acct:
            d.setdefault("revenue", amt)
        # 영업이익
        elif "영업이익" in acct and "손실" not in acct:
            d.setdefault("op_income", amt)
        # 당기순이익 (비지배주주 제외)
        elif "당기순이익" in acct and "지배" not in acct:
            d.setdefault("net_income", amt)
        # 자본총계
        elif "자본총계" in acct:
            d.setdefault("equity", amt)

    # 결과 조립 + 캐시 저장
    for cc, tk in corp_to_tk.items():
        d   = company.get(cc, {})
        rev = d.get("revenue") or 0
        op  = d.get("op_income") or 0
        cache[tk] = {
            "margin":     round(op / rev * 100, 2) if rev > 0 else None,
            "net_income": d.get("net_income"),
            "equity":     d.get("equity"),
        }

    # corp_map에 없는 종목 → 빈 dict 캐시
    for tk in missing:
        cache.setdefault(tk, {})

    with open(_FUND_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": today, "data": cache}, f, ensure_ascii=False, indent=2)

    n_margin = sum(1 for v in cache.values() if v.get("margin") is not None)
    print(f"  [DART] {n_margin}/{len(missing)}개 영업이익률 수집 완료")
    return {tk: cache.get(tk, {}) for tk in tickers}
