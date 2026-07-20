"""
분석 유니버스 정의
==================
티커 목록이 app.py·paper_trade_runner*.py·ic_weight_updater.py에
흩어져 있던 것을 여기로 모은다.

출처는 app.py의 'S&P 500 전체' 프리셋. 이름과 달리 실제로는 290종목이었고,
그중 yfinance가 데이터를 주지 않는 14종목을 제거해 확정했다.
제거 사유는 상장폐지·피인수(ATVI/PXD/KSU/MRO/HES 등)와 데이터 공백(MMC 등)이
섞여 있다. 사유와 무관하게 가격 데이터가 없으면 팩터 분석에 넣을 수 없다.
확정 근거: scripts/audit_universe.py

주의: 이 목록은 *현재* 상장 종목이다. 과거 시점 구성종목이 아니므로
과거 데이터 분석에는 생존자 편향이 있다 (survivorship_check 참조).
"""

SP500 = [
    # 정보기술
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "INTU", "AMD", "QCOM",
    "TXN", "AMAT", "LRCX", "KLAC", "MU", "SNPS", "CDNS", "FTNT", "EPAM",
    "HPQ", "HPE", "NTAP", "WDC", "STX", "KEYS", "TRMB", "CTSH", "ACN",
    "IBM", "IT", "FFIV", "PAYC", "GDDY", "GPN", "FIS", "FISV", "PAYX", "ADP",
    # 커뮤니케이션
    "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "EA",
    "TTWO", "OMC", "LYV", "WBD", "FOX", "NWSA", "MTCH",
    # 임의소비재
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "MAR",
    "HLT", "YUM", "ORLY", "AZO", "ULTA", "ROST", "DG", "DLTR", "BBY", "KMX",
    "PHM", "DHI", "LEN", "NVR", "TOL", "F", "GM", "APTV", "BWA", "LEA",
    # 필수소비재
    "WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "KMB",
    "GIS", "SJM", "CAG", "HRL", "MKC", "CHD", "CLX", "EL", "COTY",
    # 헬스케어
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "AMGN",
    "GILD", "VRTX", "REGN", "BIIB", "ILMN", "IQV", "IDXX", "WAT", "MTD", "A",
    "HCA", "CI", "ELV", "HUM", "CNC", "MOH", "DVA", "HSIC", "ZBH", "STE",
    # 금융
    "BRK-B", "JPM", "BAC", "WFC", "GS", "MS", "BLK", "AXP", "C", "USB",
    "TFC", "PNC", "SCHW", "COF", "MTB", "FITB", "HBAN", "RF", "CFG", "KEY",
    "CB", "AON", "MET", "PRU", "AFL", "AIG", "PGR", "TRV", "ALL",
    "BX", "KKR", "APO", "CG", "ARES", "TROW", "IVZ", "BEN", "AMG", "NTRS",
    # 산업재
    "GE", "HON", "CAT", "UPS", "BA", "RTX", "LMT", "NOC", "GD", "TDG",
    "ITW", "EMR", "ETN", "PH", "ROK", "DOV", "IR", "XYL", "GNRC",
    "UNP", "CSX", "NSC", "WAB", "FDX", "CHRW", "EXPD", "GWW", "FAST",
    # 에너지
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "VLO", "DVN",
    "HAL", "BKR", "FANG", "OXY", "APA", "EQT", "RRC",
    # 소재
    "LIN", "APD", "SHW", "ECL", "PPG", "NEM", "FCX", "NUE", "STLD", "RS",
    "ALB", "MOS", "CF", "FMC", "IFF", "RPM", "SON", "PKG", "IP",
    # 유틸리티
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PEG", "XEL", "ED",
    "ETR", "FE", "EIX", "WEC", "ES", "AWK", "CMS", "NI", "LNT", "EVRG",
    # 부동산
    "AMT", "PLD", "CCI", "EQIX", "PSA", "O", "WELL", "SPG", "DLR", "EQR",
    "AVB", "MAA", "UDR", "CPT", "ESS", "HST", "REG", "FRT", "BXP", "VTR",
]

# 중복 방지 — 프리셋에 같은 티커가 두 번 등장할 여지가 있었다
SP500 = list(dict.fromkeys(SP500))
