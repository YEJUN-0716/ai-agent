"""일별 리포트의 매수대기 블록.

매수여력은 예약분을 이미 뺀 값이라, 대기 금액이 보고서에 안 적히면
"총자산은 그대로인데 매수여력만 줄어든" 것처럼 읽힌다.
"""
import daily_report_toss as rpt


def test_no_pending_means_no_block():
    assert rpt.pending_buy_block({"pending": []}) == []
    assert rpt.pending_buy_block({}) == []


def test_sell_orders_do_not_count_as_buy_reservation():
    state = {"pending": [{"side": "sell", "symbol": "AAPL", "qty": 2}]}
    assert rpt.pending_buy_block(state) == []


def test_buy_block_totals_and_orders_by_size():
    state = {"pending": [
        {"side": "buy", "symbol": "MSFT", "qty": 1, "notional_krw": 512_000},
        {"side": "buy", "symbol": "AAPL", "qty": 2, "limit_price": 333.74,
         "notional_krw": 934_000},
        {"side": "buy", "symbol": "KO", "notional_krw": 100_000},   # 시가 주문(수량 없음)
        {"side": "sell", "symbol": "XOM", "qty": 3},
    ]}
    head, *rows = rpt.pending_buy_block(state)

    assert "매수대기 3건" in head and "1,546,000원" in head
    assert [r.split('`')[1] for r in rows] == ['AAPL', 'MSFT', 'KO']   # 금액 큰 순
    assert "2주 @ $333.74" in rows[0]
    assert "시가" in rows[2]                                           # 수량 없는 주문
