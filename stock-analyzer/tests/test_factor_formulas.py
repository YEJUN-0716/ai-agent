"""
factor_formulas 계수 고정 + 세 엔진 드리프트 방지 테스트.

목적이 두 가지다:
  1. 가치·퀄리티 배합 계수를 못 박는다. 계수를 바꾸면 여기가 깨지므로,
     "왜 바꿨는지" 없이는 IC 히스토리와 백테스트 결과가 조용히 무효화되지 않는다.
  2. 배합을 계산하는 모듈들(factor_scoring.py / factor_engine.py)이 실제로 이
     모듈을 쓰고 있는지(= 복붙 사본이 되살아나지 않았는지) 소스에서 확인한다.
     인라인 사본 금지는 app.py 까지 넓게 건다 — 계산이 거기로 되돌아오는 것
     자체를 막는 게 목적이다.
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

# 배합을 실제로 계산하는 모듈들 — 반드시 공유 모듈을 import 해야 한다.
# app.py 는 여기 없다. 점수 계산이 factor_scoring.py 로 넘어가면서 app.py 는
# 배합을 더 이상 직접 다루지 않기 때문이다 (조회 루프만 남았다).
# factor_engine.py 도 2026-08-19 에 빠졌다 — 배합을 쓰던 calc_factor_scores 가
# 죽은 채로 남아 있다가 삭제돼서, 이제 그 파일은 레짐 감지와 PIT 재무만 한다.
IMPORT_GUARD_TARGETS = ["modules/factor_scoring.py"]

# 인라인 사본 금지는 app.py 까지 포함해 더 넓게 건다 — 계산이 app.py 로
# 되돌아오는 것 자체를 막는 것이 이 가드의 목적이다.
INLINE_GUARD_TARGETS = ["app.py"] + IMPORT_GUARD_TARGETS

# 과거 세 곳에 복붙돼 있던 배합 리터럴. 다시 나타나면 = 공유 모듈 우회.
# value_raw / quality_raw 대입문 안에 계수가 직접 박힌 경우만 잡는다 —
# 무관한 기술적 지표 배합(예: ma*0.40 + rsi*0.30 + macd*0.30)까지 걸면 안 된다.
LEGACY_VALUE_BLEND = re.compile(r"""value_raw["']?\s*[:=][^\n]*0\.40""")
LEGACY_QUALITY_BLEND = re.compile(r"""quality_raw["']?\s*[:=][^\n]*0\.45""")


@pytest.mark.parametrize("relpath", IMPORT_GUARD_TARGETS)
def test_engines_import_shared_formulas(relpath):
    source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    assert "from modules.factor_formulas import" in source, (
        f"{relpath} 가 factor_formulas 를 import 하지 않는다 — 배합이 다시 복제됐을 수 있다."
    )


@pytest.mark.parametrize("relpath", INLINE_GUARD_TARGETS)
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


# ── 6. 가격 팩터 정의 (모멘텀 · 변동성) ─────────────────────────────
#
# 가치·퀄리티와 달리 가격 팩터는 네 곳이 **서로 다른 구간**으로 계산한다.
# 여기서 하는 일은 그 차이를 없애는 게 아니라 **한 함수의 인자로 드러내는**
# 것이다 — 어느 정의가 맞는지는 IC 실측 없이 정할 수 없기 때문이다.
# 아래 테스트는 공유 함수가 각 호출부의 기존 수식을 **그대로** 재현하는지
# 확인한다. 통합은 그 다음 문제다.

def _ramp_series(n, start=100.0, step=0.7):
    """단조 증가 가격 시계열 — 구간을 한 봉만 잘못 잡아도 값이 달라진다."""
    return pd.Series([start + step * i for i in range(n)])


def _wiggly_series(n, start=100.0):
    """등락이 섞인 시계열 — 변동성이 0이면 검증이 의미를 잃는다."""
    return pd.Series([start + 5 * ((i * 7) % 11) + 0.3 * i for i in range(n)])


def test_momentum_pct_measures_between_two_bars():
    close = _ramp_series(300)
    # skip=0 이면 마지막 봉 기준, lookback 만큼 이전 봉과 비교한다.
    expected = (close.iloc[-1] / close.iloc[-64] - 1) * 100
    assert ff.momentum_pct(close, 64) == pytest.approx(expected)


def test_momentum_pct_skips_recent_bars():
    """12-1 모멘텀: 최근 한 달을 건너뛰고 그 이전 구간을 잰다."""
    close = _ramp_series(300)
    expected = (close.iloc[-21] / close.iloc[-252] - 1) * 100
    assert ff.momentum_pct(close, 252, skip_bars=21) == pytest.approx(expected)


def test_momentum_pct_returns_zero_when_history_too_short():
    """1년치가 없으면 짧은 구간으로 추정하지 않는다 — 0 을 돌려준다."""
    assert ff.momentum_pct(_ramp_series(100), 252, skip_bars=21) == 0.0
    assert ff.momentum_pct(_ramp_series(63), 64) == 0.0


def test_annualized_vol_pct_uses_last_n_daily_returns():
    close = _wiggly_series(300)
    daily = close.pct_change().dropna().tail(21)
    expected = daily.std() * (252 ** 0.5) * 100
    assert ff.annualized_vol_pct(close, 21) == pytest.approx(expected)


def test_annualized_vol_pct_window_changes_the_answer():
    """구간 길이는 실제로 값을 바꾼다 — 21봉과 252봉은 다른 팩터다."""
    close = _wiggly_series(300)
    assert ff.annualized_vol_pct(close, 21) != pytest.approx(
        ff.annualized_vol_pct(close, 252)
    )


# 네 호출부가 지금 쓰는 구간. (호출부, lookback, skip) — 통합 전 실측 근거가
# 없으므로 값을 맞추지 않고 그대로 보존한다. 3단계에서 이 표가 한 줄로 줄어야
# 이중화가 끝난 것이다.
LEGACY_MOMENTUM_CALLSITES = [
    ("factor_scoring.price_factors",        252, 21),   # 12-1 모멘텀 (프로덕션 스캔)
    ("factor_validator._calc_momentum_vol",  63,  0),   # 한 봉 짧다 (per_factor 와 불일치)
    ("factor_validator._calc_per_factor",    64,  0),
]


@pytest.mark.parametrize("label,lookback,skip", LEGACY_MOMENTUM_CALLSITES)
def test_shared_momentum_reproduces_each_callsite(label, lookback, skip):
    """공유 함수가 각 호출부의 기존 수식과 소수점까지 같은 값을 낸다."""
    close = _wiggly_series(300)
    end = close.iloc[-skip] if skip else close.iloc[-1]
    legacy = float((end / close.iloc[-lookback] - 1) * 100)
    assert ff.momentum_pct(close, lookback, skip_bars=skip) == pytest.approx(legacy)


LEGACY_VOL_CALLSITES = [
    ("factor_scoring.price_factors", 252),   # 프로덕션 스캔
    ("factor_validator", 21),                # IC 를 측정하는 구간
]


@pytest.mark.parametrize("label,window", LEGACY_VOL_CALLSITES)
def test_shared_vol_reproduces_each_callsite(label, window):
    close = _wiggly_series(300)
    legacy = float(close.pct_change().dropna().tail(window).std() * (252 ** 0.5) * 100)
    assert ff.annualized_vol_pct(close, window) == pytest.approx(legacy)


# ── 7. 호출부가 실제로 넘기는 구간 고정 ─────────────────────────────
#
# 공유 함수가 옳아도 호출부가 인자를 잘못 넘기면 팩터가 조용히 바뀐다.
# factor_scoring 쪽은 test_factor_scores.py / test_sectoral_scores.py 가 이미
# 덮고 있지만, factor_validator 에는 동작 테스트가 하나도 없었다 — IC 를
# 만드는 코드인데도 그랬다. 여기서 구간만 못 박는다.

def _price_panel(n_tickers=6, n_bars=300):
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    return {
        chr(ord("A") + i): pd.Series(
            [100 + (i + 1) * 0.3 * j + 5 * ((j * 7) % 11) for j in range(n_bars)],
            index=idx,
        )
        for i in range(n_tickers)
    }, idx


def test_ic_factor_windows_are_pinned():
    """IC 가 재는 구간을 고정한다 — mom_3m=64봉, mom_1m=22봉, 변동성=21봉.

    이 숫자가 바뀌면 ic_weights.json 의 IC 히스토리와 비교가 끊긴다.
    프로덕션 스캔(12-1 모멘텀·252봉)과 다르다는 사실 자체도 여기 박아 둔다.
    """
    from modules import factor_validator as fv

    prices, idx = _price_panel()
    got = fv._calc_per_factor_zscores(prices, idx[-1])

    raw_mom = pd.Series({tk: ff.momentum_pct(s, 64) for tk, s in prices.items()})
    expected_z = (raw_mom - raw_mom.mean()) / (raw_mom.std() + 1e-9)
    for tk in prices:
        assert got[tk]["mom_3m"] == pytest.approx(expected_z[tk])

    raw_vol = pd.Series({tk: ff.annualized_vol_pct(s, 21) for tk, s in prices.items()})
    expected_vol_z = -(raw_vol - raw_vol.mean()) / (raw_vol.std() + 1e-9)
    for tk in prices:
        assert got[tk]["low_vol"] == pytest.approx(expected_vol_z[tk])


def test_ic_momentum_window_differs_from_production_scan():
    """같은 가격으로도 IC 구간과 프로덕션 구간은 다른 값을 낸다.

    두 정의가 실제로 갈라져 있다는 증거 — 합칠 때 이 테스트가 바뀌어야 한다.
    """
    close = _wiggly_series(300)
    ic_side = ff.momentum_pct(close, 64)
    production = ff.momentum_pct(close, 252, skip_bars=21)
    assert ic_side != pytest.approx(production)


def test_price_factor_definitions_have_one_owner():
    """가격 팩터 수식이 호출부에 다시 인라인으로 나타나면 실패시킨다."""
    inline_momentum = re.compile(r"iloc\[-\d+\]\s*/\s*[^\n]*iloc\[-\d+\]")
    inline_vol = re.compile(r"pct_change\(\)[^\n]*std\(\)[^\n]*sqrt")
    for relpath in ("modules/factor_scoring.py", "modules/factor_engine.py",
                    "modules/factor_validator.py"):
        source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert not inline_momentum.search(source), (
            f"{relpath} 에 모멘텀 수식이 인라인으로 남아 있다 — "
            f"factor_formulas.momentum_pct() 를 쓸 것."
        )
        assert not inline_vol.search(source), (
            f"{relpath} 에 연변동성 수식이 인라인으로 남아 있다 — "
            f"factor_formulas.annualized_vol_pct() 를 쓸 것."
        )
