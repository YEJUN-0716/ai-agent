"""방향성 애널리스트의 점수 산식 — 화면과 자동 기록이 공유하는 단일 진실 공급원.

app.py 의 애널리스트 함수는 점수 계산과 보고서 포맷팅이 붙어 있었다. 자동
경로(signal_worker)는 점수만 필요한데 포맷팅까지 끌고 오면 Streamlit 의존이
따라온다. 여기서는 산식만 갖는다 — streamlit·yfinance 를 import 하지 않는다.

슬러그(chart/quant/ict)는 기록 파일의 키다. 표시 이름(한글)이 바뀌어도 과거
기록과의 연결이 끊기지 않도록 여기서 고정한다.
"""

ANALYST_SLUGS = ("chart", "quant", "ict")

# 차트 애널리스트: 기술점수 70% + 모멘텀점수 30% (app.py 원본 비율)
CHART_TECHNICAL_WEIGHT = 0.7
CHART_MOMENTUM_WEIGHT = 0.3

SCORE_MIN = 0.0
SCORE_MAX = 100.0


def chart_score(technical_score, momentum_score):
    """차트+파동+모멘텀 점수 = 기술점수 70% + 모멘텀점수 30%."""
    return (float(technical_score) * CHART_TECHNICAL_WEIGHT
            + float(momentum_score) * CHART_MOMENTUM_WEIGHT)


def ict_score(base, adjustment):
    """ICT+CRT 점수 = 구조 점수 + CRT/FVG/OB 조정, 0~100 으로 자름."""
    return min(max(float(base) + float(adjustment), SCORE_MIN), SCORE_MAX)
