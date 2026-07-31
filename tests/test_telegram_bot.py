import pytest

from assistant.config import Settings
from channels.telegram_bot import TelegramChannel
from tools.virtual_trade import list_pending_requests, request_trade


@pytest.fixture
def settings(tmp_path) -> Settings:
    stock_dir = tmp_path / "stock-analyzer"
    stock_dir.mkdir()
    data_dir = tmp_path / "assistant"
    data_dir.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({111}),
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


class FakeBrain:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def ask(self, question: str, channel: str) -> str:
        self.asked.append((question, channel))
        return f"답변: {question}"


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def buy(self, symbol: str, amount_krw: float) -> dict:
        self.calls.append(("buy", symbol, amount_krw))
        return {"ok": True}

    def sell(self, symbol: str, qty: int) -> dict:
        self.calls.append(("sell", symbol, qty))
        return {"ok": True}


def test_message_from_stranger_is_ignored(settings):
    # Arrange
    brain = FakeBrain()
    channel = TelegramChannel(settings, brain)

    # Act
    reply = channel.handle_text(chat_id=999, text="안녕")

    # Assert — 비서를 부르지도 않는다 (토큰도 쓰지 않는다)
    assert reply is None
    assert brain.asked == []


def test_message_from_owner_reaches_the_brain(settings):
    # Arrange
    brain = FakeBrain()
    channel = TelegramChannel(settings, brain)

    # Act
    reply = channel.handle_text(chat_id=111, text="가상 브로커 얼마야?")

    # Assert
    assert reply == "답변: 가상 브로커 얼마야?"
    assert brain.asked == [("가상 브로커 얼마야?", "telegram")]


def test_approve_command_executes_pending_trade(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(
        chat_id=111, text=f"/승인 {proposal['request_id']}"
    )

    # Assert
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert "예약했습니다" in reply
    assert list_pending_requests(settings) == []


def test_approve_command_from_stranger_does_nothing(settings):
    # Arrange — 남이 승인 번호를 알아내도 실행되면 안 된다
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(
        chat_id=999, text=f"/승인 {proposal['request_id']}"
    )

    # Assert
    assert reply is None
    assert executor.calls == []
    assert len(list_pending_requests(settings)) == 1


def test_approve_with_unknown_id_explains_the_problem(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)

    # Act
    reply = channel.handle_text(chat_id=111, text="/승인 없는아이디")

    # Assert
    assert "찾지 못했습니다" in reply
    assert executor.calls == []


def test_approve_without_id_shows_pending_list(settings):
    # Arrange
    channel = TelegramChannel(
        settings, FakeBrain(), trade_executor=FakeExecutor()
    )
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(chat_id=111, text="/승인")

    # Assert
    assert "AAPL" in reply
    assert "1,000,000원어치 매수" in reply


def test_pending_list_says_so_when_nothing_waits(settings):
    # Arrange
    channel = TelegramChannel(settings, FakeBrain())

    # Act
    reply = channel.handle_text(chat_id=111, text="/승인")

    # Assert
    assert "없습니다" in reply


def test_reject_command_discards_the_proposal(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(
        chat_id=111, text=f"/거절 {proposal['request_id']}"
    )

    # Assert
    assert "취소했습니다" in reply
    assert executor.calls == []
    assert list_pending_requests(settings) == []


def test_english_approve_command_also_works(settings):
    # Arrange — 텔레그램이 한글 명령을 command로 인식하지 않을 때를 대비
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "sell", "AAPL", qty=3)

    # Act
    reply = channel.handle_text(
        chat_id=111, text=f"/approve {proposal['request_id']}"
    )

    # Assert
    assert executor.calls == [("sell", "AAPL", 3)]
    assert "예약했습니다" in reply


def test_help_command_does_not_reach_the_brain(settings):
    # Arrange — 도움말에 API 비용을 쓸 이유가 없다
    brain = FakeBrain()
    channel = TelegramChannel(settings, brain)

    # Act
    reply = channel.handle_text(chat_id=111, text="/start")

    # Assert
    assert "/승인" in reply
    assert brain.asked == []


def test_brain_failure_is_reported_not_swallowed(settings):
    # Arrange
    class BrokenBrain:
        def ask(self, question: str, channel: str) -> str:
            raise RuntimeError("API 한도 초과")

    channel = TelegramChannel(settings, BrokenBrain())

    # Act
    reply = channel.handle_text(chat_id=111, text="질문")

    # Assert — 조용히 삼키지 않고 이유를 그대로 전한다
    assert "API 한도 초과" in reply
