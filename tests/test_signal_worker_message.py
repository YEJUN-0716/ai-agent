"""
signal_worker.build_message 렌더 고정 테스트.

텔레그램 메시지에 트레이드 플랜 라인(롱)과 '숏 관찰' 섹션이 제대로 붙는지
확인한다. build_message 는 순수 포맷 로직이라 합성 actions/plans 로 검증한다.
"""
import signal_worker as sw
from modules import trade_plan as tp

_REBAL = {"buy_count": 1, "sell_count": 0, "hold_count": 0}


def _buy_action(ticker="AAA"):
    return {"ticker": ticker, "action": "🟢 매수", "priority": "HIGH",
            "price": "$100.00", "alloc": "$1,000", "qty": "10주", "reason": "테스트 매수"}


def _grade(plan):
    """등급·실행여부를 진짜 함수로 채운다 — 손으로 적으면 문턱이 바뀔 때
    픽스처가 조용히 실물과 어긋난다."""
    grade, risk_pct = tp.cost_grade(plan["entry"]["ref"], plan["stop"])
    actionable, why_not = tp._actionable(
        plan["valid"], plan["direction"], grade, plan.get("reason_invalid", ""))
    plan.update(cost_grade=grade, risk_pct=round(risk_pct, 2),
                actionable=actionable, reason_not_actionable=why_not)
    return plan


def _long_plan():
    return _grade({"direction": "long", "valid": True, "confidence": "high",
                   "bias_score": 20, "reason_invalid": "",
                   "entry": {"low": 95.0, "high": 97.0, "ref": 96.0}, "stop": 93.0,
                   "targets": [102.0, 108.0], "rr": [2.0, 4.0]})


def _short_plan():
    return _grade({"direction": "short", "valid": True, "confidence": "medium",
                   "bias_score": -18, "reason_invalid": "",
                   "entry": {"low": 103.0, "high": 105.0, "ref": 104.0}, "stop": 107.0,
                   "targets": [98.0, 92.0], "rr": [2.0, 4.0]})


def test_long_plan_line_attached_to_buy():
    msg = sw.build_message(["AAA"], [_buy_action("AAA")], _REBAL, [],
                           plans={"AAA": _long_plan()})
    assert "📐" in msg
    assert "진입 $95.00~$97.00" in msg
    assert "손절 $93.00" in msg
    assert "목표 $102.00 (R:R 2.0)" in msg


def test_short_watch_section_listed():
    # 숏 플랜은 매수 액션이 없어도 '숏 관찰' 섹션에 뜬다
    msg = sw.build_message(["BBB"], [], _REBAL, [], plans={"BBB": _short_plan()})
    assert "숏 관찰" in msg
    # 확신도가 아니라 실행등급을 싣는다 — 확신도는 결과와 연결된 것이
    # 측정되지 않았고(2026-08-10), 실행등급은 비용 후 기대값을 가른다.
    assert "*BBB* 숏 (실행등급 A · 손절 2.9%)" in msg
    assert "medium" not in msg
    assert "진입 $103.00~$105.00" in msg


def test_krx_won_formatting():
    plan = _long_plan()
    msg = sw.build_message(["005930.KS"], [_buy_action("005930.KS")], _REBAL, [],
                           plans={"005930.KS": plan})
    assert "₩95" in msg and "$" not in msg.split("📐")[1]


def test_no_plan_line_when_invalid():
    invalid = dict(_long_plan(), valid=False)
    msg = sw.build_message(["AAA"], [_buy_action("AAA")], _REBAL, [],
                           plans={"AAA": invalid})
    assert "📐" not in msg
    assert "숏 관찰" not in msg


def test_backwards_compatible_without_plans():
    # plans 인자 없이도 (기존 호출) 정상 동작
    msg = sw.build_message(["AAA"], [_buy_action("AAA")], _REBAL, [])
    assert "*AAA* 🟢 매수" in msg
    assert "📐" not in msg
