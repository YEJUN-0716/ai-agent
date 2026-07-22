"""
factor_formulas 계수 고정 + 세 엔진 드리프트 방지 테스트.

목적이 두 가지다:
  1. 가치·퀄리티 배합 계수를 못 박는다. 계수를 바꾸면 여기가 깨지므로,
     "왜 바꿨는지" 없이는 IC 히스토리와 백테스트 결과가 조용히 무효화되지 않는다.
  2. app.py / factor_engine.py 가 실제로 이 모듈을 쓰고 있는지(= 복붙 사본이
     되살아나지 않았는지) 소스 레벨에서 확인한다.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from modules import factor_formulas as ff

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 1. 계수 고정 ────────────────────────────────────────────────────

def test_value_weights_sum_to_one():
    total = ff.VALUE_EP_WEIGHT + ff.VALUE_BP_WEIGHT + ff.VALUE_FCF_WEIGHT
    assert total == pytest.approx(1.0)


def test_quality_weights_sum_to_one():
    total = (ff.QUALITY_ROE_WEIGHT + ff.QUALITY_MARGIN_WEIGHT
             + ff.QUALITY_ACCRUAL_WEIGHT)
    assert total == pytest.approx(1.0)


def test_value_weights_are_pinned():
    """P3-A 배합: EP 40% / BP 30% / FCF 30%."""
    assert (ff.VALUE_EP_WEIGHT, ff.VALUE_BP_WEIGHT, ff.VALUE_FCF_WEIGHT) == (0.40, 0.30, 0.30)


def test_quality_weights_are_pinned():
    """P3-B 배합: ROE 45% / 이익률 35% / 발생액품질 20%."""
    assert (ff.QUALITY_ROE_WEIGHT, ff.QUALITY_MARGIN_WEIGHT,
            ff.QUALITY_ACCRUAL_WEIGHT) == (0.45, 0.35, 0.20)


# ── 2. 수익률 변환 ──────────────────────────────────────────────────

def test_earnings_yield_inverts_per():
    assert ff.earnings_yield(20.0) == pytest.approx(5.0)


def test_book_yield_inverts_pbr():
    assert ff.book_yield(4.0) == pytest.approx(25.0)


@pytest.mark.parametrize("bad", [None, 0, -12.5, float("nan")])
def test_yields_return_zero_for_missing_or_negative(bad):
    """적자(음수 PER)·결측·NaN 은 0점 — NaN 이 새어 나가면 안 된다."""
    assert ff.earnings_yield(bad) == 0.0
    assert ff.book_yield(bad) == 0.0


# ── 3. 발생액 품질 ──────────────────────────────────────────────────

def test_accrual_quality_neutral_when_ratio_is_one():
    """FCF == NI 면 중립 50점."""
    assert ff.accrual_quality(100.0, 100.0) == pytest.approx(50.0)


def test_accrual_quality_zero_when_burning_cash_despite_profit():
    """GAAP 흑자 + 음수 FCF = 최저 품질."""
    assert ff.accrual_quality(-50.0, 100.0) == 0.0


def test_accrual_quality_clipped_at_hundred():
    """FCF 가 NI 의 3배여도 100 을 넘지 않는다."""
    assert ff.accrual_quality(300.0, 100.0) == 100.0


@pytest.mark.parametrize("fcf,ni", [
    (None, 100.0),
    (100.0, None),
    (100.0, 0.0),
    (100.0, -80.0),       # 적자 → 비율에 의미 없음
    (float("nan"), 100.0),
    (100.0, float("nan")),
])
def test_accrual_quality_defaults_to_neutral_when_undecidable(fcf, ni):
    """판정 불가(결측·NaN·적자)는 페널티가 아니라 중립값."""
    assert ff.accrual_quality(fcf, ni) == ff.ACCRUAL_NEUTRAL


# ── 4. 원점수 배합 ──────────────────────────────────────────────────

def test_value_raw_blends_by_weights():
    # EP 10, BP 20, FCF 5 → 10*.4 + 20*.3 + 5*.3 = 4 + 6 + 1.5 = 11.5
    assert ff.value_raw(10.0, 20.0, 5.0) == pytest.approx(11.5)


def test_value_raw_penalizes_negative_fcf_yield():
    """현금 소각(음수 FCF수익률)은 클리핑되지 않고 점수를 끌어내려야 한다."""
    burning = ff.value_raw(10.0, 20.0, -5.0)
    healthy = ff.value_raw(10.0, 20.0, 5.0)
    assert burning < healthy
    assert burning == pytest.approx(8.5)


def test_quality_raw_blends_by_weights():
    # ROE 20, 마진 10, 발생액 50 → 20*.45 + 10*.35 + 50*.2 = 9 + 3.5 + 10 = 22.5
    assert ff.quality_raw(20.0, 10.0, 50.0) == pytest.approx(22.5)


def test_blend_functions_work_on_series():
    """factor_engine 은 Series 로 호출한다 — 스칼라와 같은 결과가 나와야 한다."""
    ep = pd.Series([10.0, 0.0])
    bp = pd.Series([20.0, 0.0])
    fcf = pd.Series([5.0, 0.0])
    result = ff.value_raw(ep, bp, fcf)
    assert isinstance(result, pd.Series)
    assert result.tolist() == pytest.approx([11.5, 0.0])


# ── 5. 드리프트 방지 — 복붙 사본이 되살아났는지 소스에서 확인 ──────

DRIFT_GUARD_TARGETS = ["app.py", "modules/factor_engine.py"]

# 과거 세 곳에 복붙돼 있던 배합 리터럴. 다시 나타나면 = 공유 모듈 우회.
# value_raw / quality_raw 대입문 안에 계수가 직접 박힌 경우만 잡는다 —
# 무관한 기술적 지표 배합(예: ma*0.40 + rsi*0.30 + macd*0.30)까지 걸면 안 된다.
LEGACY_VALUE_BLEND = re.compile(r"""value_raw["']?\s*[:=][^\n]*0\.40""")
LEGACY_QUALITY_BLEND = re.compile(r"""quality_raw["']?\s*[:=][^\n]*0\.45""")


@pytest.mark.parametrize("relpath", DRIFT_GUARD_TARGETS)
def test_engines_import_shared_formulas(relpath):
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert "from modules.factor_formulas import" in source, (
        f"{relpath} 가 factor_formulas 를 import 하지 않는다 — 배합이 다시 복제됐을 수 있다."
    )


@pytest.mark.parametrize("relpath", DRIFT_GUARD_TARGETS)
def test_no_inline_blend_literals_remain(relpath):
    """배합 계수를 인라인으로 다시 써 넣으면 실패시킨다."""
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert not LEGACY_VALUE_BLEND.search(source), (
        f"{relpath} 에 가치 배합(0.40/0.30/0.30)이 인라인으로 남아 있다 — "
        f"factor_formulas.value_raw() 를 쓸 것."
    )
    assert not LEGACY_QUALITY_BLEND.search(source), (
        f"{relpath} 에 퀄리티 배합(0.45/0.35/0.20)이 인라인으로 남아 있다 — "
        f"factor_formulas.quality_raw() 를 쓸 것."
    )
