"""주문을 '자리 차지'로 세는 기준과 '체결됨'으로 세는 기준은 다르다.

2026-07-31 에 이 둘을 buy_rec["ok"] 하나로 묶은 대가를 치렀다. 가상 주문은
다음 거래일 시가에 체결되므로 주문 직후엔 영원히 "체결 확인 안 됨"이다.
그 상태로 카운터가 멈춰 5종목 한도에 12종목이 예약됐다.

그렇다고 예약을 '체결됨'으로 세면 반대쪽이 무너진다. 체결가가 아직 없어서
성적표에 어제 종가가 진입가로 박힌다 — 체결 확인 검사가 막으려던 그 일이다.
그래서 기준을 둘로 나눴고, 이 파일이 그 경계를 지킨다.
"""

import pytest

from paper_trade_runner_toss import order_accepted


@pytest.mark.parametrize("status", ["pending_next_open", "timeout", "filled", None])
def test_live_order_holds_a_slot_and_the_cash(status):
    # 아직 살아 있는 주문은 매수여력과 보유 한도를 차지한다.
    # pending_next_open(가상 예약)과 timeout(체결 여부 미확인)도 마찬가지다.
    assert order_accepted(status)


@pytest.mark.parametrize("status", ["canceled", "rejected", "replaced",
                                    "cancel_rejected", "replace_rejected"])
def test_definitively_failed_order_frees_the_slot(status):
    # 브로커가 확실히 거부한 주문만 자리를 돌려준다.
    assert not order_accepted(status)
