"""
팩터 원점수(raw) 공식 — 세 구현의 단일 진실 공급원(single source of truth)
=========================================================================
가치·퀄리티 원점수의 배합은 아래 세 곳에서 **동일해야 한다**:
  - app.py::calc_factor_scores          (UI + signal_worker.py)
  - app.py::calc_factor_scores_sectoral (섹터 중립 랭킹)
  - modules/factor_engine.py::calc_factor_scores (페이퍼트레이드 + factor_validator)

과거에는 같은 수식을 세 곳에 복붙해 두고 주석으로만 "드리프트 해소"라고
적어 뒀는데, 한쪽만 고치면 UI와 백테스트 결과가 조용히 갈렸다.

이 모듈이 그 배합을 소유한다. 계수를 바꾸려면 여기서만 바꾸면 되고,
tests/test_factor_formulas.py 가 계수를 고정해 두었으므로 무단 변경은
CI에서 실패한다.

순수 산술만 쓰므로 스칼라와 pandas Series 양쪽에서 동일하게 동작한다
(app.py 는 종목별 스칼라로, factor_engine.py 는 Series로 호출한다).
의존성 없음 — Streamlit·yfinance·pandas 모두 import 하지 않는다.
"""

# ── 가치 팩터 배합 (P3-A) ──────────────────────────────────────────
# EP(이익수익률) 40% + BP(장부수익률) 30% + FCF수익률 30%
VALUE_EP_WEIGHT = 0.40
VALUE_BP_WEIGHT = 0.30
VALUE_FCF_WEIGHT = 0.30

# ── 퀄리티 팩터 배합 (P3-B) ────────────────────────────────────────
# ROE 45% + 이익률 35% + 발생액 품질 20%
QUALITY_ROE_WEIGHT = 0.45
QUALITY_MARGIN_WEIGHT = 0.35
QUALITY_ACCRUAL_WEIGHT = 0.20

# 발생액 품질 기본값 — 재무 데이터가 없거나 순이익이 음수라 판정 불가일 때.
# 중립값(50)을 써서 데이터 결측이 페널티로 작용하지 않게 한다.
ACCRUAL_NEUTRAL = 50.0
ACCRUAL_MIN = 0.0
ACCRUAL_MAX = 100.0
# FCF/NI 비율을 0~100 척도로 옮기는 배율 (비율 1.0 = 중립 50점).
ACCRUAL_SCALE = 50.0


def _is_missing(x):
    """None 과 NaN 을 함께 걸러낸다.

    yfinance 의 .info 는 값이 없을 때 None 이 아니라 float('nan') 을 주는
    경우가 있다. NaN 은 비교가 전부 False 라 `x <= 0` 검사를 그냥 통과해
    버리므로, 여기서 명시적으로 잡지 않으면 원점수 전체가 NaN 으로 오염된다.
    (NaN != NaN 은 파이썬에서 참 — numpy 의존 없이 판정하려고 이 형태를 쓴다.)
    """
    return x is None or x != x


def earnings_yield(per):
    """PER → 이익수익률(%). PER이 없거나 0 이하(적자)면 0."""
    if _is_missing(per) or per <= 0:
        return 0.0
    return 100.0 / per


def book_yield(pbr):
    """PBR → 장부수익률(%). PBR이 없거나 0 이하면 0."""
    if _is_missing(pbr) or pbr <= 0:
        return 0.0
    return 100.0 / pbr


def accrual_quality(fcf, net_income):
    """
    발생액 품질(0~100) — 회계이익이 실제 현금으로 뒷받침되는 정도.

    FCF/NI 비율이 높을수록 이익의 질이 좋다. 판정 규칙:
      - FCF 또는 NI 결측, 혹은 NI <= 0  → 중립값 50 (판정 불가)
      - NI > 0 이고 FCF <= 0            → 0 (GAAP 흑자인데 현금 소각 = 최저 품질)
      - NI > 0 이고 FCF > 0             → (FCF/NI) * 50 을 0~100으로 클립
    """
    if _is_missing(fcf) or _is_missing(net_income) or net_income <= 0:
        return ACCRUAL_NEUTRAL
    if fcf <= 0:
        return ACCRUAL_MIN
    scaled = (fcf / net_income) * ACCRUAL_SCALE
    return min(max(scaled, ACCRUAL_MIN), ACCRUAL_MAX)


def value_raw(ep, bp, fcf_yield):
    """가치 원점수 = EP·40% + BP·30% + FCF수익률·30%.

    fcf_yield 는 음수(현금 소각)도 그대로 반영해 페널티로 작용시킨다.
    스칼라·Series 모두 허용 — 호출부에서 결측을 0으로 채워 넘길 것.
    """
    return (ep * VALUE_EP_WEIGHT
            + bp * VALUE_BP_WEIGHT
            + fcf_yield * VALUE_FCF_WEIGHT)


def quality_raw(roe, margin, accrual_q):
    """퀄리티 원점수 = ROE·45% + 이익률·35% + 발생액품질·20%.

    roe·margin 은 % 단위. 스칼라·Series 모두 허용 — 호출부에서 결측을
    roe/margin 은 0, accrual_q 는 ACCRUAL_NEUTRAL 로 채워 넘길 것.
    """
    return (roe * QUALITY_ROE_WEIGHT
            + margin * QUALITY_MARGIN_WEIGHT
            + accrual_q * QUALITY_ACCRUAL_WEIGHT)
