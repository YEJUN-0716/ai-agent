"""가상 장부 자기 점검 — 규칙을 하나씩 깨고 실제로 빨개지는지 본다.

이 장부의 예약·체결·만료는 우리가 만든 규칙이라, 진짜 브로커라면 거래소가
거절해 줄 실수를 아무도 안 막아 준다. 점검이 잡는 걸 보이는 게 이 파일의 일이다.
성한 장부가 통과하는지(거짓 경보 없음)도 같이 본다.
"""
from datetime import date

import pytest

from modules import virtual_broker as vb


@pytest.fixture
def healthy():
    """현금 1,000만 → AAPL 10주를 100만원에 사고 5주를 60만원에 판 장부."""
    return {
        "cash_krw": vb.INITIAL_CAPITAL_KRW - 1_000_000 + 600_000,
        "positions": {"AAPL": {"qty": 5, "avg_price_usd": 100.0, "entry_date": "2026-08-01"}},
        "pending": [],
        "realized_pnl_krw": 100_000.0,
        "trades": [
            {"date": "2026-08-01", "symbol": "AAPL", "side": "buy",
             "qty": 10, "amount_krw": 1_000_000},
            {"date": "2026-08-10", "symbol": "AAPL", "side": "sell",
             "qty": 5, "amount_krw": 600_000, "pnl_krw": 100_000},
        ],
        "index_meta": {},
    }


TODAY = date(2026, 8, 17)


def test_healthy_ledger_is_silent(healthy):
    assert vb.check_state(healthy, today=TODAY) == []


def test_reservation_beyond_cash(healthy):
    """2026-07-31 에 실제로 난 사고 — 같은 현금으로 예약이 쌓였다."""
    healthy["pending"] = [
        {"side": "buy", "symbol": "MSFT", "notional_krw": healthy["cash_krw"] * 0.7,
         "placed_date": "2026-08-17"},
        {"side": "buy", "symbol": "AMZN", "notional_krw": healthy["cash_krw"] * 0.7,
         "placed_date": "2026-08-17"},
    ]
    assert any("넘는다" in p for p in vb.check_state(healthy, today=TODAY))


def test_cash_not_matching_trade_history(healthy):
    healthy["cash_krw"] -= 500_000          # 이력에 없는 지출
    problems = vb.check_state(healthy, today=TODAY)
    assert any("거래 이력과 안 맞는다" in p for p in problems)


def test_realized_pnl_not_matching_sells(healthy):
    healthy["realized_pnl_krw"] = 999_999.0
    assert any("실현손익" in p for p in vb.check_state(healthy, today=TODAY))


def test_shares_vanish_when_position_overwritten(healthy):
    """지정가 체결이 기존 포지션을 통째로 덮어쓰면 주식이 조용히 사라진다."""
    healthy["positions"]["AAPL"]["qty"] = 2      # 이력상 5주여야 한다
    assert any("보유 수량이 거래 이력과 안 맞는다" in p
               for p in vb.check_state(healthy, today=TODAY))


def test_selling_more_than_held(healthy):
    healthy["pending"] = [{"side": "sell", "symbol": "AAPL", "qty": 9,
                           "placed_date": "2026-08-17"}]
    assert any("많이 팔려 한다" in p for p in vb.check_state(healthy, today=TODAY))


def test_double_reservation_same_symbol(healthy):
    healthy["pending"] = [
        {"side": "buy", "symbol": "MSFT", "notional_krw": 100_000, "placed_date": "2026-08-17"},
        {"side": "buy", "symbol": "MSFT", "notional_krw": 100_000, "placed_date": "2026-08-17"},
    ]
    assert any("이중 예약" in p for p in vb.check_state(healthy, today=TODAY))


def test_stale_pending_order_means_settlement_stopped(healthy):
    """만료됐어야 할 주문이 남아 있으면 정산 자체가 안 돈다는 신호다."""
    healthy["pending"] = [
        {"side": "buy", "kind": "limit_entry", "symbol": "GM", "qty": 13,
         "limit_price": 81.74, "notional_krw": 1_500_000, "placed_date": "2026-01-02"},
    ]
    assert any("대기 중" in p for p in vb.check_state(healthy, today=TODAY))


def test_live_pending_orders_are_not_flagged(healthy):
    """며칠 전 지정가 주문은 정상 — 거짓 경보를 내면 아무도 안 본다."""
    healthy["pending"] = [
        {"side": "buy", "kind": "limit_entry", "symbol": "GM", "qty": 13,
         "limit_price": 81.74, "notional_krw": 1_500_000, "placed_date": "2026-08-13"},
    ]
    assert vb.check_state(healthy, today=TODAY) == []


def test_malformed_order_is_reported_not_crashed(healthy):
    healthy["pending"] = [
        {"side": "buy", "symbol": "KO", "notional_krw": 0, "placed_date": "2026-08-17"},
        {"side": "hold", "symbol": "KO", "placed_date": "2026-08-17"},
        {"side": "buy", "symbol": "KO", "notional_krw": 1000, "placed_date": "어제"},
    ]
    problems = vb.check_state(healthy, today=TODAY)
    assert any("금액이 0 이하" in p for p in problems)
    assert any("buy/sell 이 아니다" in p for p in problems)
    assert any("placed_date" in p for p in problems)


def test_index_ledger_deposits_are_not_a_violation(healthy):
    """인덱스 장부는 매달 입금이 들어온다 — 그건 어긋남이 아니다."""
    healthy["cash_krw"] += 3_000_000
    healthy["index_meta"] = {"deposited_krw": 3_000_000.0}
    assert vb.check_state(healthy, today=TODAY) == []


def test_index_fees_and_dividends_are_not_a_violation(healthy):
    """인덱스 장부는 매수·매도 밖으로도 현금이 움직인다 — 수수료와 배당.

    둘 다 trades 에 안 남으므로 검산식이 세 주지 않으면 첫 수수료·첫 배당에
    "장부가 깨졌다"는 거짓 경보가 뜬다. 진짜 경보를 못 믿게 되는 자리다.
    """
    healthy["cash_krw"] += 300_000 - 50_000
    healthy["index_meta"] = {"dividends_krw": 300_000.0, "fees_krw": 50_000.0}
    assert vb.check_state(healthy, today=TODAY) == []
