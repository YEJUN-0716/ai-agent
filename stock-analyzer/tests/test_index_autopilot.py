"""인덱스 자동운용 규칙 — 안 팔고, 정수주로만 사고, 없는 돈을 안 쓰는가.

이 규칙에는 매도가 없다. 그래서 한 번 잘못 산 비중은 되돌릴 방법이 없고,
현금을 초과해 주문하면 다음 달 적립금을 미리 당겨쓴 셈이 된다. 여기서 막지
않으면 12개월 뒤에야 알게 된다.
"""

import pytest

from modules import index_autopilot
from modules.index_autopilot import TARGETS, plan_orders

# 계획 시점의 대략적인 실제 가격. 1주 값이 청크 문제를 만드는 비율이 핵심이다.
PRICES = {"ITOT": 120.0, "AGG": 98.0, "GLDM": 87.0}
MONTHLY = 714.0  # 월 100만원 ≈ $714


def test_demo_self_check():
    index_autopilot.demo()


def test_never_sells():
    # AGG 를 목표보다 훨씬 많이 들고 있어도 매도(음수 수량) 주문은 안 나온다.
    orders = plan_orders({"AGG": 100}, PRICES, MONTHLY)
    assert orders
    assert all(o["qty"] > 0 for o in orders)
    assert "AGG" not in [o["ticker"] for o in orders]


def test_integer_shares_only():
    orders = plan_orders({}, PRICES, MONTHLY)
    assert all(isinstance(o["qty"], int) for o in orders)


def test_never_exceeds_cash():
    for cash in (0.0, 87.0, 500.0, MONTHLY, 12345.67):
        orders = plan_orders({"ITOT": 3, "GLDM": 1}, PRICES, cash)
        assert sum(o["qty"] * o["est_price"] for o in orders) <= cash


def test_largest_gap_first():
    # 빈 장부면 비중이 가장 큰 ITOT 의 부족분이 1위다.
    assert plan_orders({}, PRICES, MONTHLY)[0]["ticker"] == "ITOT"


def test_skips_when_share_costs_more_than_cash():
    # GLDM 목표 $71 < 1주 $87 → 이번 달은 안 사고 이월한다.
    orders = plan_orders({}, PRICES, MONTHLY)
    assert "GLDM" not in [o["ticker"] for o in orders]
    spent = sum(o["qty"] * o["est_price"] for o in orders)
    assert MONTHLY - spent > 0


def test_crash_makes_asset_first_priority():
    # 균형 잡힌 장부에서 ITOT 만 반토막 → ITOT 이 우선순위 1위가 된다.
    holdings = {"ITOT": 42, "AGG": 15, "GLDM": 8}
    balanced = plan_orders(holdings, PRICES, MONTHLY)
    crashed = plan_orders(holdings, {**PRICES, "ITOT": 60.0}, MONTHLY)
    assert crashed[0]["ticker"] == "ITOT"
    assert crashed[0]["qty"] > balanced[0]["qty"]


def test_targets_must_sum_to_one():
    with pytest.raises(ValueError):
        plan_orders({}, PRICES, MONTHLY, {"ITOT": 0.7, "AGG": 0.2})


def test_zero_cash_makes_no_orders():
    assert plan_orders({"ITOT": 10}, PRICES, 0.0) == []


def test_rejects_missing_or_broken_price():
    # 가격을 못 받아온 달에 조용히 건너뛰면 그 자산만 영원히 안 사진다.
    with pytest.raises(ValueError):
        plan_orders({}, {**PRICES, "GLDM": 0.0}, MONTHLY)
    with pytest.raises(ValueError):
        plan_orders({}, {"ITOT": 120.0, "AGG": 98.0}, MONTHLY)


def test_targets_default_is_the_designed_mix():
    assert TARGETS == {"ITOT": 0.70, "AGG": 0.20, "GLDM": 0.10}


# ── 갭 버퍼는 한 곳이 소유한다 ────────────────────────────────────

def test_gap_buffer_is_applied_inside_plan_orders():
    """부르는 쪽이 아니라 이 함수가 뺀다.

    러너는 빼고 측정 스크립트(measure_index_autopilot)는 안 빼서, "정수주
    마찰 연 −0.03%p" 가 실전과 다른 규칙으로 잰 값이었다.
    """
    prices = {"ITOT": 100.0, "AGG": 100.0, "GLDM": 100.0}
    full = sum(o["qty"] for o in index_autopilot.plan_orders({}, prices, 10_000.0,
                                                gap_buffer_bp=0.0))
    buffered = sum(o["qty"] for o in index_autopilot.plan_orders({}, prices, 10_000.0))
    assert index_autopilot.GAP_BUFFER_BP > 0
    assert buffered < full


def test_runner_and_measurement_use_the_same_buffer():
    """둘 다 index_autopilot 의 상수를 쓴다 — 사본을 만들면 여기서 걸린다."""
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ('index_runner.py', 'scripts/measure_index_autopilot.py'):
        src = open(os.path.join(root, rel), encoding='utf-8').read()
        assert not re.search(r'^GAP_BUFFER_BP\s*=\s*[0-9]', src, re.M), (
            f'{rel} 이 버퍼 값을 자기 숫자로 들고 있다 — 두 규칙이 갈린다')
