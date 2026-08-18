"""가상 브로커 — 주문이 제 날짜에 체결되고, 없는 돈을 예약하지 않는가.

2026-07-31 에 두 증상이 같이 터졌다. 7/30 예약분이 이틀째 체결되지 않았고,
예약 합계 1,852만원이 현금 1,000만원을 넘었다. 원인은 하나로 이어져 있다 —
주문 날짜를 한국 달력으로 찍어 체결이 하루씩 밀렸고, 체결이 안 되니 현금이
그대로 남아 러너가 매일 같은 돈으로 주문을 새로 냈다.
"""

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from modules import virtual_broker as vb


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """상태 파일을 임시 폴더로 돌리고 환율을 1,000원으로 고정한 브로커."""
    monkeypatch.setattr(vb, "STATE_FILE", str(tmp_path / "virtual_portfolio.json"))
    monkeypatch.setattr(vb, "_FX", 1000.0)
    return vb


def test_order_date_follows_us_session_not_korean_calendar(broker):
    # 러너는 21:30 UTC 에 돈다 = 뉴욕 17:30(장 마감 직후), 서울은 이미 다음 날
    # 06:30. 주문은 신호가 나온 7/30 장으로 찍혀야 7/31 시가에 체결된다.
    # 서울 날짜(7/31)로 찍으면 체결이 8/3 시가로 밀린다.
    after_us_close = datetime(2026, 7, 30, 21, 30, tzinfo=timezone.utc)
    assert broker.market_date(after_us_close).isoformat() == "2026-07-30"


def test_reservation_cannot_exceed_available_cash(broker):
    # 현금 1,000만. 600만을 예약하면 남은 가용은 400만이다.
    broker.place_notional_buy("AAA", 6000.0, market="US")
    assert broker.available_krw(broker.load_state()) == pytest.approx(4_000_000)
    assert broker.get_account()["buying_power"] == pytest.approx(4_000_000)

    with pytest.raises(ValueError, match="가용 현금"):
        broker.place_notional_buy("BBB", 6000.0, market="US")

    # 거부된 주문은 대기열에 남지 않는다.
    assert len(broker.load_state()["pending"]) == 1


def test_account_view_carries_every_field_the_daily_report_prints(broker, monkeypatch):
    """일일 보고가 읽는 칸이 전부 있어야 한다.

    보고서는 BROKER 값에 따라 토스와 가상 장부를 갈아끼운다. 두 모듈이 같은
    칸을 내놓는다는 전제인데, 확인하는 곳이 없으면 이름 하나가 어긋난 날
    보고서가 조용히 0원을 찍는다. 저장소 이음매에서 이미 겪은 사고다.
    """
    monkeypatch.setattr(broker, "last_close_price", lambda symbol: 120.0)
    state = broker.load_state()
    state["positions"] = {
        "AAA": {"qty": 10, "avg_price_usd": 100.0, "entry_date": "2026-07-31"}
    }
    broker.save_state(state)

    position = broker.get_positions()[0]
    assert {"symbol", "qty", "avg_entry_price",
            "current_price", "unrealized_pl"} <= position.keys()
    assert float(position["unrealized_pl"]) == pytest.approx(200.0)  # (120-100)*10

    account = broker.get_account()
    # 평가액은 주가 × 수량 × 환율. 현금은 그대로.
    assert account["equity"] == pytest.approx(10_000_000 + 10 * 120.0 * 1000.0)
    assert account["buying_power"] == pytest.approx(10_000_000)


def test_price_ignores_the_empty_row_of_a_day_not_yet_traded(broker, monkeypatch):
    # 장 열리기 전에는 오늘 행이 종가 없이 먼저 생긴다. 그 행을 집으면 NaN 이
    # 평가액을 타고 올라가 총자산이 통째로 nan 이 된다. 실제로 그렇게 찍혔다.
    frame = pd.DataFrame(
        {"Close": [100.0, 110.0, float("nan")]},
        index=pd.to_datetime(["2026-07-30", "2026-07-31", "2026-08-03"]),
    )
    monkeypatch.setattr(broker, "_fetch_ohlc", lambda symbol, start, end: frame)

    assert broker.last_close_price("AAA") == 110.0


def test_pending_buy_fills_at_next_open_and_spends_cash(broker, monkeypatch):
    broker.place_notional_buy("AAA", 1000.0, market="US")   # 100만원어치
    # qty 를 안 넘긴 주문은 주문 dict 도 예전 모양 그대로다.
    assert "qty" not in broker.load_state()["pending"][0]
    monkeypatch.setattr(
        broker, "next_open_price", lambda symbol, after: (100.0, "2026-07-31")
    )

    state = broker.settle_pending(broker.load_state(), 1000.0)

    # 1주 10만원 → 100만원으로 10주.
    assert state["positions"]["AAA"]["qty"] == 10
    assert state["cash_krw"] == pytest.approx(9_000_000)
    assert state["pending"] == []
    assert state["trades"][0]["side"] == "buy"


def test_qty_order_fills_exactly_that_many_shares(broker, monkeypatch):
    # 예상가 $110 로 3주를 주문했는데 시가가 $100 로 열렸다. 금액만 넘겼다면
    # 33만원으로 3주가 아니라 3주 값보다 많은 수량을 샀을 것이다.
    broker.place_notional_buy("AAA", 330.0, market="US", qty=3)
    monkeypatch.setattr(
        broker, "next_open_price", lambda symbol, after: (100.0, "2026-07-31")
    )

    state = broker.settle_pending(broker.load_state(), 1000.0)

    assert state["positions"]["AAA"]["qty"] == 3
    assert state["cash_krw"] == pytest.approx(10_000_000 - 3 * 100_000)


def test_qty_order_is_skipped_when_cash_is_short(broker, monkeypatch):
    state = broker.load_state()
    state["cash_krw"] = 250_000.0
    broker.save_state(state)
    broker.place_notional_buy("AAA", 200.0, market="US", qty=2)

    monkeypatch.setattr(
        broker, "next_open_price", lambda symbol, after: (200.0, "2026-07-31")
    )
    state = broker.settle_pending(broker.load_state(), 1000.0)

    # 2주 = 40만원 > 현금 25만원 → 한 주도 안 산다(부분 체결 없음).
    assert state["positions"] == {}
    assert state["cash_krw"] == pytest.approx(250_000)


def test_old_portfolio_file_opens_and_keeps_index_meta(broker, tmp_path):
    """index_meta 가 없는 예전 장부도 그대로 열리고, 새 칸은 저장 후 살아남는다.

    load_state 는 _empty_state 에 없는 키를 조용히 버린다. 칸을 먼저 안 만들면
    러너가 "이번 달 입금했다"를 저장해도 다음 실행에서 사라져 두 번 입금한다.
    """
    with open(broker.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"cash_krw": 1_234_567.0, "positions": {}, "pending": [],
                   "realized_pnl_krw": 0.0, "trades": []}, f)

    state = broker.load_state()
    assert state["cash_krw"] == pytest.approx(1_234_567)
    assert state["index_meta"] == {}

    state["index_meta"]["last_deposit_month"] = "2026-09"
    broker.save_state(state)
    assert broker.load_state()["index_meta"]["last_deposit_month"] == "2026-09"
