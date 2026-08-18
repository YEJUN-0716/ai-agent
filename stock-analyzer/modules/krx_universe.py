"""
KRX 유니버스 — KOSPI/KOSDAQ 시가총액 상위 N종목
=================================================
한국 유니버스가 그동안 15종목 하드코딩이었다. 종목이 상장폐지되거나 순위가
바뀌어도 목록은 그대로였고, 늘리려면 사람이 손으로 티커를 적어야 했다.

FinanceDataReader 로 실제 상장 목록을 받아 시가총액 순으로 자른다. FDR 은
이미 requirements 에 있고 app.py 가 한국 주가 조회에 쓰고 있다 — 새 의존성이
아니다.

`KOSPI 100`, `KOSDAQ 50` 같은 이름을 UNIVERSE 환경변수에 그대로 넣을 수 있다.
프리셋 dict 에 넣지 않는 이유는 목록이 매번 달라지기 때문이다. 고정 프리셋은
"오늘의 상위 100" 을 표현할 수 없다.

**일별 파일 캐시.** FDR 상장목록 조회는 수 초 걸리고 하루에 여러 번 바뀌지
않는다. GitHub Actions 가 스캔마다 새로 받을 이유가 없다.
"""
import json
import os
import re
import time

# KOSPI 는 .KS, KOSDAQ 은 .KQ 접미사를 붙여야 yfinance 가 알아듣는다.
MARKET_SUFFIX = {
    'KOSPI': '.KS',
    'KOSDAQ': '.KQ',
}

# "KOSPI 100", "kosdaq 50" — 공백 하나, 대소문자 무시.
UNIVERSE_PATTERN = re.compile(r'^\s*(KOSPI|KOSDAQ)\s+(\d+)\s*$', re.IGNORECASE)

# FDR 버전마다 컬럼명이 다르다. 앞에서부터 있는 것을 쓴다.
_CODE_COLUMNS = ('Code', 'Symbol')
_MARCAP_COLUMNS = ('Marcap', 'MarketCap', 'Market Cap')

# KRX 종목코드 6번째 자리가 0이면 보통주, 5·7·9 등은 우선주다.
# 우선주는 유동성이 얇고 의결권이 없어 PER·PBR 이 보통주와 체계적으로 다르다.
# 걸러내지 않으면 삼성전자 같은 종목이 보통주+우선주로 두 번 들어와, 팩터
# 랭킹이 사실상 같은 회사에 두 자리를 내준다. (실측: KOSPI 시총 상위 20 중
# 005935 삼성전자우 1건)
COMMON_STOCK_LAST_DIGIT = '0'

MAX_TICKERS = 1000          # 그 이상은 스캔이 몇 시간 단위가 된다
CACHE_TTL_SECONDS = 86400   # 하루
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'krx_listing_cache.json')


def is_common_stock(code):
    """보통주 여부. 우선주(끝자리 5·7·9 등)를 유니버스에서 뺀다."""
    return str(code).endswith(COMMON_STOCK_LAST_DIGIT)


def _pick_column(columns, candidates):
    for name in candidates:
        if name in columns:
            return name
    return None


def _fetch_listing(market):
    """FDR 상장목록 → 시가총액 내림차순 보통주 종목코드 리스트.

    FDR import 를 함수 안에서 한다. 모듈 import 만으로 무거운 의존성을
    끌어오면 테스트와 headless 스크립트가 다 느려진다.
    """
    import FinanceDataReader as fdr

    df = fdr.StockListing(market)
    if df is None or df.empty:
        return []

    code_col = _pick_column(df.columns, _CODE_COLUMNS)
    if code_col is None:
        raise ValueError(f"FDR {market} 목록에 종목코드 컬럼이 없다: {list(df.columns)}")

    marcap_col = _pick_column(df.columns, _MARCAP_COLUMNS)
    if marcap_col is not None:
        df = df.dropna(subset=[marcap_col]).sort_values(marcap_col, ascending=False)
    # 시총 컬럼이 없는 FDR 버전이면 원래 순서를 쓴다. 대체로 시총순이지만
    # 보장은 없다 — 그때는 "상위 N" 이 근사치라는 뜻이다.

    codes = [str(c).zfill(6) for c in df[code_col].tolist()]
    return [c for c in codes if is_common_stock(c)]


def _load_cache():
    try:
        if time.time() - os.path.getmtime(_CACHE_FILE) < CACHE_TTL_SECONDS:
            with open(_CACHE_FILE, encoding='utf-8') as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(cache):
    try:
        with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except OSError:
        pass   # 캐시는 있으면 좋은 것 — 못 써도 스캔은 굴러가야 한다


def listing_codes(market, use_cache=True):
    """해당 시장의 종목코드 목록 (시가총액 내림차순, 6자리 문자열)."""
    market = market.upper()
    if market not in MARKET_SUFFIX:
        raise ValueError(f"지원하지 않는 시장: {market}")

    cache = _load_cache() if use_cache else {}
    if market in cache:
        return cache[market]

    codes = _fetch_listing(market)
    if codes and use_cache:
        cache[market] = codes
        _save_cache(cache)
    return codes


def top_tickers(market, count, use_cache=True):
    """시가총액 상위 N종목을 yfinance 티커로. 예: ('KOSPI', 3) → ['005930.KS', ...]"""
    market = market.upper()
    suffix = MARKET_SUFFIX[market]
    codes = listing_codes(market, use_cache=use_cache)
    return [f"{code}{suffix}" for code in codes[:count]]


def resolve(name, use_cache=True):
    """'KOSPI 100' 같은 이름 → 티커 목록. 형식이 아니면 None.

    None 을 돌려주는 게 중요하다 — 호출부가 "이 이름은 내 것이 아니다" 를
    알고 다음 해석기(쉼표구분 티커 등)로 넘어갈 수 있어야 한다.
    """
    if not isinstance(name, str):
        return None
    matched = UNIVERSE_PATTERN.match(name)
    if not matched:
        return None
    market = matched.group(1).upper()
    count = min(int(matched.group(2)), MAX_TICKERS)
    if count <= 0:
        return None
    return top_tickers(market, count, use_cache=use_cache)
