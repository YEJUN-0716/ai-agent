import pytest
from fastapi.testclient import TestClient

from assistant.config import Settings
from channels.web import create_app
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


def test_chat_page_is_served(settings):
    # Arrange
    client = TestClient(create_app(settings, FakeBrain()))

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert "<textarea" in response.text


def test_chat_endpoint_reaches_the_brain(settings):
    # Arrange
    brain = FakeBrain()
    client = TestClient(create_app(settings, brain))

    # Act
    response = client.post("/chat", json={"message": "관심종목 보여줘"})

    # Assert — 채널 이름이 web으로 기록돼야 대화 흐름이 맞는다
    assert response.status_code == 200
    assert response.json()["reply"] == "답변: 관심종목 보여줘"
    assert brain.asked == [("관심종목 보여줘", "web")]


def test_whitespace_only_message_is_rejected(settings):
    # Arrange
    brain = FakeBrain()
    client = TestClient(create_app(settings, brain))

    # Act
    response = client.post("/chat", json={"message": "   "})

    # Assert — 빈 질문에 API 비용을 쓰지 않는다
    assert response.status_code == 400
    assert brain.asked == []


def test_brain_failure_returns_readable_error(settings):
    # Arrange
    class BrokenBrain:
        def ask(self, question: str, channel: str) -> str:
            raise RuntimeError("API 한도 초과")

    client = TestClient(create_app(settings, BrokenBrain()))

    # Act
    response = client.post("/chat", json={"message": "질문"})

    # Assert — 500으로 죽지 않고 이유를 그대로 전한다
    assert response.status_code == 200
    assert "API 한도 초과" in response.json()["reply"]


def test_pending_endpoint_lists_proposals(settings):
    # Arrange
    client = TestClient(create_app(settings, FakeBrain()))
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.get("/pending")

    # Assert
    assert response.json()["pending"][0]["symbol"] == "AAPL"


def test_approve_endpoint_executes_the_trade(settings):
    # Arrange
    executor = FakeExecutor()
    client = TestClient(
        create_app(settings, FakeBrain(), trade_executor=executor)
    )
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.post(
        "/approve", json={"request_id": proposal["request_id"]}
    )

    # Assert
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert "예약했습니다" in response.json()["reply"]
    assert list_pending_requests(settings) == []


def test_approve_with_unknown_id_does_not_execute(settings):
    # Arrange
    executor = FakeExecutor()
    client = TestClient(
        create_app(settings, FakeBrain(), trade_executor=executor)
    )

    # Act
    response = client.post("/approve", json={"request_id": "없는아이디"})

    # Assert
    assert executor.calls == []
    assert "찾지 못했습니다" in response.json()["reply"]


def test_chat_endpoint_cannot_execute_a_trade(settings):
    # Arrange — 대화 경로로는 절대 주문이 나가면 안 된다
    executor = FakeExecutor()
    client = TestClient(
        create_app(settings, FakeBrain(), trade_executor=executor)
    )
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act — 승인처럼 들리는 말을 채팅으로 보낸다
    client.post("/chat", json={"message": "방금 그거 승인해서 바로 사줘"})

    # Assert — 브로커는 불리지 않고 제안은 그대로 대기 중이다
    assert executor.calls == []
    assert len(list_pending_requests(settings)) == 1
