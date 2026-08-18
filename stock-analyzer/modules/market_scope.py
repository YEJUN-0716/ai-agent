"""
시장 판별과 시장별 벤치마크
================================
"이 유니버스는 어느 시장인가" 를 한 곳에서 답한다. Streamlit·yfinance
의존성이 없고 네트워크도 타지 않는다 — 티커 문자열만 보고 판정한다.

이게 필요해진 이유: 국면 판정(get_market_regime)이 SPY 를 기준으로만 돌았다.
한국 유니버스를 스캔해도 미국 시장이 강세면 'bull' 이 나오고, 그 국면으로
ic_weights.json 에서 팩터 가중치를 골랐다. IC 가중치가 실전 스캔에 연결되기
전에는 영향이 제한적이었지만, 연결된 뒤로는 **한국 종목의 팩터 배분을 미국
시장 상태가 정하는** 상태가 됐다.

여기 있는 ETF·지수 티커는 전부 실제 조회로 확인했다 (2026-07 기준, 각 121봉).
추측으로 넣으면 yfinance 가 조용히 빈 프레임을 주고, UI 는 "데이터 없음" 이
아니라 그냥 빈 화면을 보여준다.
"""
US = 'US'
KRX = 'KRX'

KRX_SUFFIXES = ('.KS', '.KQ')

# 국면 판정 기준 지수. KOSPI 종합지수는 KOSDAQ 종목에도 대표성이 있다 —
# 두 시장은 같은 거시 충격에 함께 움직인다.
REGIME_BENCHMARK = {
    US: 'SPY',
    KRX: '^KS11',    # KOSPI Composite Index
}

# 섹터 상대강도 비교용 ETF. yfinance 가 돌려주는 영문 섹터명이 키다.
# 매핑이 확실하지 않은 섹터는 **비워 둔다** — 엉뚱한 ETF 와 비교시키느니
# "비교 대상 없음" 이 정직하다.
SECTOR_ETF = {
    US: {
        'Technology':             'XLK',
        'Consumer Cyclical':      'XLY',
        'Financial Services':     'XLF',
        'Healthcare':             'XLV',
        'Consumer Defensive':     'XLP',
        'Communication Services': 'XLC',
        'Industrials':            'XLI',
        'Basic Materials':        'XLB',
        'Energy':                 'XLE',
        'Real Estate':            'XLRE',
        'Utilities':              'XLU',
    },
    KRX: {
        'Technology':             '139260.KS',   # TIGER 200 IT
        'Financial Services':     '091170.KS',   # KODEX 은행
        'Healthcare':             '266420.KS',   # KODEX 헬스케어
        'Consumer Cyclical':      '139290.KS',   # TIGER 200 경기소비재
        'Consumer Defensive':     '227560.KS',   # TIGER 200 생활소비재
        'Industrials':            '139230.KS',   # TIGER 200 중공업
        'Communication Services': '315270.KS',   # TIGER 200 커뮤니케이션서비스
        # 한국은 에너지와 화학이 한 ETF 로 묶여 있다. 둘 다 같은 곳을 가리킨다.
        'Basic Materials':        '139250.KS',   # TIGER 200 에너지화학
        'Energy':                 '139250.KS',
        # Real Estate·Utilities 는 대표성 있는 KRX 섹터 ETF 를 확인하지 못했다.
        # 건설 ETF 를 부동산에 갖다 붙이는 식의 근사는 하지 않는다.
    },
}


def market_of_ticker(ticker):
    """티커 하나의 시장. .KS/.KQ 면 KRX, 나머지는 US."""
    return KRX if str(ticker).endswith(KRX_SUFFIXES) else US


def market_of(tickers):
    """유니버스의 시장. 섞여 있으면 다수결.

    혼합 유니버스에 정답은 없다 — 국면은 "지금 어느 시장의 조류를 타는가" 라
    하나만 고를 수밖에 없다. 다수결이면 한국 15종목 + 미국 1종목 같은
    구성이 판정을 뒤집지 않는다. 동수면 US 로 둔다 (기존 동작).
    """
    tickers = list(tickers or [])
    if not tickers:
        return US
    krx_count = sum(1 for t in tickers if market_of_ticker(t) == KRX)
    return KRX if krx_count * 2 > len(tickers) else US


def regime_benchmark(tickers):
    """이 유니버스의 국면을 재는 데 쓸 지수 티커."""
    return REGIME_BENCHMARK[market_of(tickers)]


def sector_etf(sector, market=US):
    """섹터 상대강도 비교용 ETF. 매핑이 없으면 빈 문자열."""
    return SECTOR_ETF.get(market, {}).get(sector, '')


def sector_etf_for_ticker(sector, ticker):
    """종목이 속한 시장의 섹터 ETF."""
    return sector_etf(sector, market_of_ticker(ticker))
