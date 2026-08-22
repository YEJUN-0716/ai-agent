"""방향성 애널리스트의 점수 산식 — 화면과 자동 기록이 공유하는 단일 진실 공급원.

app.py 의 애널리스트 함수는 점수 계산과 보고서 포맷팅이 붙어 있었다. 자동
경로(signal_worker)는 점수만 필요한데 포맷팅까지 끌고 오면 Streamlit 의존이
따라온다. 여기서는 산식만 갖는다 — streamlit·yfinance 를 import 하지 않는다.

슬러그(chart/quant/ict)는 기록 파일의 키다. 표시 이름(한글)이 바뀌어도 과거
기록과의 연결이 끊기지 않도록 여기서 고정한다.
"""

ANALYST_SLUGS = ("chart", "quant", "ict")

# 총괄 판정(화면 상단 VERDICT)을 만드는 방향성 3인. 매크로·백테스트·리스크는
# 역할이 달라 방향성 블렌드에 안 들어간다(app.manager_consolidate 참고).
DIRECTIONAL_SLUGS = ("chart", "quant", "ict")

# 블렌드 결과를 기록에 남길 때 쓰는 키. 재료(3인 점수)를 나중에 다시 섞지 않고
# 이 값을 그대로 남기는 이유는 가중치가 변하기 때문이다 — ic_weights.json 을
# 매주 갱신하고 국면별로도 다르다. 나중에 섞으면 그날 화면에 뜬 값과 달라진다.
VERDICT_SLUG = "verdict"

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


def blend_score(scores, weights):
    """가중평균 — 화면 총괄 점수와 기록이 공유하는 단일 산식.

    app.manager_consolidate 가 이 함수에 위임한다. 같은 식을 두 곳에 적으면
    언젠가 갈라지고, 그러면 성적표가 화면과 다른 자로 재게 된다 — 판정
    문턱(analyst_scorecard.verdict_of)을 한 곳이 소유하는 것과 같은 이유다.

    가중치 합이 0 이면 동일가중으로 떨어진다. 가중치를 못 읽었다는 이유로
    판정 자체를 버리지는 않는다 — 판정이 없는 쪽이 훨씬 비싸다.
    """
    vals = [float(s) for s in scores]
    if not vals:
        return None
    ws = [float(w) for w in weights]
    total = sum(ws)
    if total <= 0:
        return sum(vals) / len(vals)
    return sum(v * w for v, w in zip(vals, ws)) / total


def verdict_score(per_analyst, weights_by_slug):
    """기록 한 종목의 3인 점수 → 그 시점의 화면 총괄 점수. 못 내면 None.

    3인이 **모두** 있어야 낸다. 빠진 자리를 중립 50 으로 채우면 '계산 불가'가
    '중립 판단'으로 성적에 섞이고, 두 명만 섞은 값은 애초에 화면이 낸 판정이
    아니다 — analyst_scorecard.combined_day 와 같은 규칙이다.
    """
    if any(per_analyst.get(slug) is None for slug in DIRECTIONAL_SLUGS):
        return None
    weights_by_slug = weights_by_slug or {}
    return blend_score([per_analyst[slug] for slug in DIRECTIONAL_SLUGS],
                       [weights_by_slug.get(slug, 0.0) for slug in DIRECTIONAL_SLUGS])


def fundamental_unavailable(detail) -> bool:
    """`app.fundamental_score` 의 detail 이 '재무를 못 받았다'를 말하는가.

    야후가 조회를 막으면 예외 없이 빈 dict 가 온다. 그대로 계산하면 모든
    항목이 None 이라 점수가 50 근처(실측 50.55)에 붙는다 — '못 받았다'가
    '중립 판단'으로 조용히 흘러간다.

    이 판별이 signal_worker 안에만 있어서 **기록은 그 종목의 quant 를 빼는데
    화면은 50.55 를 총괄 블렌드에 그대로 넣고** 있었다. 기록 3,307 종목일로
    재보니 총괄 라벨이 17.8%(bull 국면 가중치) 뒤집히고, 퀀트 비중이 66% 인
    bear 국면이면 21.7% 다. 같은 질문에 두 곳이 답하고 있었으므로 여기로
    옮긴다 — verdict_score 가 "3인이 모두 있어야 낸다"고 못 박은 것과 한 쌍이다.
    """
    detail = detail or {}
    return bool(detail.get('데이터없음') or detail.get('오류'))
