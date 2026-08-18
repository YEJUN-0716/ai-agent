import threading
import time

import pytest

from assistant.config import Settings
from tools.assistant_notes import read_audit_log
from tools.virtual_trade import (
    TradeError,
    _run_broker,
    approve_request,
    list_pending_requests,
    reject_request,
    request_trade,
)


def test_broker_calls_never_overlap():
    """브로커 호출은 한 번에 하나씩만 나가야 한다.

    virtual_broker는 주문마다 상태 파일을 통째로 읽고-고치고-쓴다. 두 호출이
    겹치면 나중에 저장한 쪽이 앞선 주문을 덮어써서, 사장님이 승인한 매매가
    흔적 없이 사라진다. _run_broker가 stdout을 잠시 바꿔치기하는 구간이기도
    해서, 겹치면 다른 창구의 출력까지 함께 삼킨다.
    """
    # Arrange — 실행 중인 호출 수를 세는 가짜 브로커 함수
    concurrent: list[int] = []
    peak: list[int] = []

    def slow_broker_call(_symbol):
        concurrent.append(1)
        peak.append(len(concurrent))
        time.sleep(0.01)      # 겹칠 틈을 넉넉히 준다
        concurrent.pop()
        return {"ok": True}

    # Act — 여덟 창구가 동시에 브로커를 부른다
    start = threading.Barrier(8)

    def call() -> None:
        start.wait()
        _run_broker(slow_broker_call, "AAPL")

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert — 어느 순간에도 동시에 실행된 호출은 하나뿐
    assert len(peak) == 8
    assert max(peak) == 1


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
        study_inbox=tmp_path,
        obsidian_vault=tmp_path,
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


class CountingExecutor:
    """호출 횟수만 센다. 동시 호출에도 정확하도록 자체 잠금을 쓴다."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def _count(self) -> dict:
        with self._lock:
            self.calls += 1
        return {"ok": True}

    def buy(self, symbol: str, amount_krw: float) -> dict:
        return self._count()

    def sell(self, symbol: str, qty: int) -> dict:
        return self._count()


def test_simultaneous_approvals_execute_the_order_only_once(settings):
    # Arrange — 폰과 PC에서 같은 제안을 동시에 승인하는 상황.
    # 텔레그램과 웹은 한 프로세스에서 스레드로 동시에 돈다.
    executor = CountingExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)
    start = threading.Barrier(2)
    errors: list[Exception] = []

    def approve() -> None:
        start.wait()
        try:
            approve_request(settings, request["request_id"], executor=executor)
        except TradeError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert — 주문은 딱 한 번 나가고, 진 쪽은 이유를 받는다
    assert executor.calls == 1
    assert len(errors) == 1
    assert "찾지 못했습니다" in str(errors[0])
    assert list_pending_requests(settings) == []


class BrokenExecutor:
    """주문을 넣다가 실패하는 브로커."""

    def buy(self, symbol: str, amount_krw: float) -> dict:
        raise RuntimeError("연결 끊김")

    def sell(self, symbol: str, qty: int) -> dict:
        raise RuntimeError("연결 끊김")


def test_broker_failure_keeps_the_proposal(settings):
    # Arrange — 승인했는데 브로커가 실패하는 상황
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    with pytest.raises(TradeError, match="연결 끊김"):
        approve_request(
            settings, request["request_id"], executor=BrokenExecutor()
        )

    # Assert — 제안이 사라지지 않고 그대로 남아 다시 승인할 수 있다
    pending = list_pending_requests(settings)
    assert len(pending) == 1
    assert pending[0]["request_id"] == request["request_id"]


def test_broker_failure_is_audited(settings):
    # Arrange
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    with pytest.raises(TradeError):
        approve_request(
            settings, request["request_id"], executor=BrokenExecutor()
        )

    # Assert — 실패도 기록에 남는다. 성공한 척하지 않는다.
    actions = [line.split("|")[1].strip() for line in read_audit_log(settings)]
    assert actions == ["trade_approve_failed", "trade_request"]


def test_retry_after_broker_failure_succeeds(settings):
    # Arrange — 실패한 뒤 브로커가 정상으로 돌아왔다
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)
    with pytest.raises(TradeError):
        approve_request(
            settings, request["request_id"], executor=BrokenExecutor()
        )

    # Act — 같은 번호로 다시 승인
    result = approve_request(
        settings, request["request_id"], executor=executor
    )

    # Assert — 이번엔 나간다. 그리고 딱 한 번만 나간다.
    assert result["executed"] is True
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert list_pending_requests(settings) == []


def test_corrupt_pending_file_is_preserved_not_discarded(settings):
    # Arrange — 대기 목록 파일이 망가졌다
    pending_path = settings.assistant_data_dir / "pending_trades.json"
    pending_path.write_text("{ 망가진 파일", encoding="utf-8")

    # Act
    result = list_pending_requests(settings)

    # Assert — 빈 목록으로 넘어가되, 원본은 보관되고 기록이 남는다
    assert result == []
    backups = list(
        settings.assistant_data_dir.glob("pending_trades.json.*.corrupt")
    )
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ 망가진 파일"
    assert not pending_path.exists()
    actions = [line.split("|")[1].strip() for line in read_audit_log(settings)]
    assert "file_corrupt" in actions


def test_request_and_approval_are_both_audited(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    approve_request(settings, request["request_id"], executor=executor)

    # Assert
    actions = [line.split("|")[1].strip() for line in read_audit_log(settings)]
    assert actions == ["trade_approve", "trade_request"]
