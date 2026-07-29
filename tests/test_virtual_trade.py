import pytest

from assistant.config import Settings
from tools.assistant_notes import read_audit_log
from tools.virtual_trade import (
    TradeError,
    approve_request,
    list_pending_requests,
    reject_request,
    request_trade,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    stock_dir = tmp_path / "stock-analyzer"
    stock_dir.mkdir()
    data_dir = tmp_path / "assistant"
    data_dir.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=stock_dir,
        assistant_data_dir=data_dir,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
    )


class FakeExecutor:
    """진짜 가상 브로커 대신 호출을 기록만 한다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def buy(self, symbol: str, amount_krw: float) -> dict:
        self.calls.append(("buy", symbol, amount_krw))
        return {"ok": True, "id": "virtual-buy-1"}

    def sell(self, symbol: str, qty: int) -> dict:
        self.calls.append(("sell", symbol, qty))
        return {"ok": True, "id": "virtual-sell-1"}


def test_request_does_not_execute_anything(settings):
    # Arrange — request_trade는 executor를 아예 받지 않는다. 그러니
    # "브로커가 안 불렸다"는 브로커가 남기는 흔적이 없다는 것으로 증명한다.

    # Act — 제안만 한다
    result = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Assert — 가상 브로커 상태 파일이 생기지 않았다 (주문이 안 나갔다)
    assert not (settings.stock_analyzer_path / "virtual_portfolio.json").exists()
    assert result["status"] == "confirmation_required"
    assert result["request_id"]
    assert len(list_pending_requests(settings)) == 1


def test_request_trade_signature_has_no_executor():
    # Arrange — 실행 경로가 제안 함수에 존재하지 않아야 한다
    import inspect

    # Act
    params = inspect.signature(request_trade).parameters

    # Assert
    assert "executor" not in params


def test_approval_executes_buy_through_broker(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "aapl", amount_krw=1_000_000)

    # Act
    result = approve_request(settings, request["request_id"], executor=executor)

    # Assert
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert result["executed"] is True
    assert list_pending_requests(settings) == []


def test_approval_executes_sell_through_broker(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "sell", "AAPL", qty=3)

    # Act
    approve_request(settings, request["request_id"], executor=executor)

    # Assert
    assert executor.calls == [("sell", "AAPL", 3)]


def test_unknown_request_id_is_rejected(settings):
    # Arrange
    executor = FakeExecutor()

    # Act / Assert
    with pytest.raises(TradeError, match="찾지 못했습니다"):
        approve_request(settings, "없는-아이디", executor=executor)
    assert executor.calls == []


def test_request_cannot_be_approved_twice(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)
    approve_request(settings, request["request_id"], executor=executor)

    # Act / Assert
    with pytest.raises(TradeError, match="찾지 못했습니다"):
        approve_request(settings, request["request_id"], executor=executor)
    assert len(executor.calls) == 1


def test_rejected_request_never_executes(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    result = reject_request(settings, request["request_id"])

    # Assert
    assert result["rejected"] is True
    assert list_pending_requests(settings) == []
    with pytest.raises(TradeError):
        approve_request(settings, request["request_id"], executor=executor)
    assert executor.calls == []


def test_buy_without_amount_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="금액"):
        request_trade(settings, "buy", "AAPL")


def test_sell_without_qty_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="수량"):
        request_trade(settings, "sell", "AAPL")


def test_non_positive_amount_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="0보다"):
        request_trade(settings, "buy", "AAPL", amount_krw=0)


def test_unknown_side_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="buy"):
        request_trade(settings, "short", "AAPL", amount_krw=1_000_000)


def test_request_and_approval_are_both_audited(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    approve_request(settings, request["request_id"], executor=executor)

    # Assert
    actions = [line.split("|")[1].strip() for line in read_audit_log(settings)]
    assert actions == ["trade_approve", "trade_request"]
