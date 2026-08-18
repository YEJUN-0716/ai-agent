"""
생존자 편향 점검 모듈
=================================================================
IC 분석은 현재 상장된 종목만 대상으로 하여, 과거에 파산하거나
상장폐지된 종목을 제외합니다. 이 모듈은 해당 편향의 존재와
규모를 문서화하여 IC 분석 결과 해석 시 참고할 수 있게 합니다.

이 모듈은 데이터를 수정하지 않습니다 — 편향을 정량화하여
분석 결과에 메타데이터로 첨부할 뿐입니다.
"""
from datetime import datetime

KNOWN_DELISTED_LARGE_CAPS = [
    {"ticker": "LEH",  "name": "Lehman Brothers Holdings",       "event": "파산",
     "date": "2008-09-15", "note": "글로벌 금융위기 상징 — 시가총액 약 $600B에서 0으로"},
    {"ticker": "BSC",  "name": "Bear Stearns",                    "event": "강제 매각",
     "date": "2008-03-14", "note": "JP모건에 주당 $2에 강제 매각 (최고가 대비 -99%)"},
    {"ticker": "WM",   "name": "Washington Mutual",               "event": "파산",
     "date": "2008-09-25", "note": "미국 최대 저축기관 파산"},
    {"ticker": "ENE",  "name": "Enron",                           "event": "회계사기/파산",
     "date": "2001-12-02", "note": "회계 부정 사건 — 시가총액 $70B에서 0으로"},
    {"ticker": "WCG",  "name": "WorldCom",                        "event": "회계사기/파산",
     "date": "2002-07-21", "note": "Enron 다음 규모의 회계 부정 파산"},
    {"ticker": "C",    "name": "Citigroup (2009 구제금융)",        "event": "주가 붕괴",
     "date": "2009-03-05", "note": "주가 $55→$0.97, 현재 상장 유지이나 주주가치 99% 소멸"},
    {"ticker": "AIG",  "name": "AIG (2008 구제금융)",              "event": "정부 구제금융",
     "date": "2008-09-16", "note": "주가 $70→$0.33, 현재 상장 유지이나 주주가치 99% 소멸"},
    {"ticker": "FRC",  "name": "First Republic Bank",             "event": "파산/FDIC 인수",
     "date": "2023-05-01", "note": "SVB 사태 여파 — JP모건에 매각 (2023년 5월)"},
    {"ticker": "SIVB", "name": "Silicon Valley Bank (SVB)",       "event": "파산/FDIC 인수",
     "date": "2023-03-10", "note": "스타트업 생태계 최대 규모 은행 파산 (2023년 3월)"},
    {"ticker": "SBNY", "name": "Signature Bank",                  "event": "파산/FDIC 인수",
     "date": "2023-03-12", "note": "SVB 사태 이틀 후 파산"},
    {"ticker": "GE",   "name": "General Electric (구조조정)",      "event": "주가 -80% 후 분할",
     "date": "2021-11-09", "note": "다우지수 120년 구성원에서 퇴출(2018), 3개사로 분할"},
    {"ticker": "HTZ",  "name": "Hertz Global Holdings",           "event": "파산/재상장",
     "date": "2020-05-22", "note": "코로나로 파산 → 2021년 재상장 (기존 주주가치 소멸)"},
    {"ticker": "SHLDQ","name": "Sears Holdings",                  "event": "파산",
     "date": "2018-10-15", "note": "미국 최대 소매업체, 전자상거래에 밀려 파산"},
    {"ticker": "BBBYQ","name": "Bed Bath & Beyond",               "event": "파산",
     "date": "2023-04-23", "note": "소매업 몰락 — 밈주식 거품 이후 파산"},
]


def survivorship_bias_warning(universe_size: int = None) -> str:
    """IC 분석 결과에 첨부할 생존자 편향 경고 문자열 반환."""
    lines = [
        "⚠️  생존자 편향(Survivorship Bias) 주의",
        "이 IC 분석은 *현재 상장된* 종목만을 대상으로 합니다.",
        "과거에 파산하거나 상장폐지된 종목은 제외되어 IC가 과대 추정될 수 있습니다.",
    ]
    if universe_size is not None:
        lines.append(f"분석 유니버스: {universe_size}개 (현재 상장 종목만 포함)")
    lines.append(
        f"알려진 상장폐지/구제금융 대형주 사례: {len(KNOWN_DELISTED_LARGE_CAPS)}건 (목록 참고)"
    )
    return "\n".join(lines)


def known_failures_in_period(start_date: str, end_date: str) -> list:
    """
    분석 기간(start_date ~ end_date) 내에 발생한 알려진 실패 사례 목록 반환.
    날짜 형식: "YYYY-MM-DD"
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end   = datetime.strptime(end_date,   "%Y-%m-%d")
    except ValueError:
        return []

    failures = []
    for item in KNOWN_DELISTED_LARGE_CAPS:
        try:
            event_date = datetime.strptime(item["date"], "%Y-%m-%d")
            if start <= event_date <= end:
                failures.append({
                    "ticker": item["ticker"],
                    "name":   item["name"],
                    "event":  item["event"],
                    "date":   item["date"],
                    "note":   item["note"],
                })
        except ValueError:
            continue
    return failures


def annotate_ic_result(ic_result: dict, universe_size: int,
                        start_date: str = None, end_date: str = None) -> dict:
    """
    IC 분석 결과 dict에 생존자 편향 메타데이터를 추가하여 반환.
    ic_result: run_per_factor_ic_analysis() 또는 run_out_of_sample_validation() 결과
    """
    warning  = survivorship_bias_warning(universe_size=universe_size)
    failures = []
    if start_date and end_date:
        failures = known_failures_in_period(start_date, end_date)

    annotated = dict(ic_result)
    annotated["survivorship_bias"] = {
        "warning":              warning,
        "known_failures_count": len(failures),
        "known_failures":       failures,
        "note": (
            "IC가 실제보다 낙관적으로 추정될 수 있습니다. "
            "생존자 편향을 완전히 제거하려면 CRSP 등 "
            "상장폐지 이력 데이터가 필요합니다."
        ),
    }
    return annotated
