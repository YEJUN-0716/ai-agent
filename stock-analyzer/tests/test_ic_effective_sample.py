"""겹치는 창을 독립 표본으로 세지 않는지 — IC t 통계량의 유효표본.

화면은 예측 기간을 10/21/42거래일로 고르게 하는데 리밸런싱 간격은 21일 고정이다.
42를 고르면 이웃 관측이 창의 절반을 공유하므로, n 을 그대로 쓰면 |t| 가 √2 배
부풀고 바로 아래 "|t| ≥ 2.0 → 통계적으로 유의미" 문구가 뜬다.
"""
import math

import pytest

from modules.factor_validator import effective_periods


def test_no_overlap_keeps_every_period():
    assert effective_periods(58, forward_days=21, rebal_days=21) == 58
    # 창이 간격보다 짧으면 관측 사이에 빈틈이 생길 뿐, 표본이 늘지는 않는다.
    assert effective_periods(58, forward_days=10, rebal_days=21) == 58


def test_double_length_window_halves_the_sample():
    assert effective_periods(58, forward_days=42, rebal_days=21) == pytest.approx(29.0)


def test_overlap_shrinks_t_by_sqrt_of_overlap():
    icir, n = 0.30, 58
    t_naive = icir * math.sqrt(n)
    t_fixed = icir * math.sqrt(effective_periods(n, 42, 21))
    assert t_naive / t_fixed == pytest.approx(math.sqrt(2), abs=1e-6)
    # 실측 형태: 겹침을 무시하면 유의, 보정하면 아니다.
    assert t_naive > 2.0 > t_fixed


def test_never_below_two():
    assert effective_periods(3, forward_days=252, rebal_days=21) == 2.0
