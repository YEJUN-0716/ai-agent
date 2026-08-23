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
        study_inbox=tmp_path,
        obsidian_vault=tmp_path,
    )


def make_client(app) -> TestClient:
    """실제 접속과 같은 호스트로 테스트 클라이언트를 만든다.

    기본값('testserver')으로 두면 Host 검증에 걸린다. 그 검증은
    DNS 재바인딩을 막는 장치이므로 테스트를 위해 느슨하게 하지 않고,
    테스트 쪽이 진짜 주소를 쓰게 한다.
    """
    return TestClient(app, base_url="http://127.0.0.1:8765")


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
    client = make_client(create_app(settings, FakeBrain()))

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert "<textarea" in response.text


def test_chat_endpoint_reaches_the_brain(settings):
    # Arrange
    brain = FakeBrain()
    client = make_client(create_app(settings, brain))

    # Act
    response = client.post("/chat", json={"message": "관심종목 보여줘"})

    # Assert — 채널 이름이 web으로 기록돼야 대화 흐름이 맞는다
    assert response.status_code == 200
    assert response.json()["reply"] == "답변: 관심종목 보여줘"
    assert brain.asked == [("관심종목 보여줘", "web")]


def test_whitespace_only_message_is_rejected(settings):
    # Arrange
    brain = FakeBrain()
    client = make_client(create_app(settings, brain))

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

    client = make_client(create_app(settings, BrokenBrain()))

    # Act
    response = client.post("/chat", json={"message": "질문"})

    # Assert — 500으로 죽지 않고 이유를 그대로 전한다
    assert response.status_code == 200
    assert "API 한도 초과" in response.json()["reply"]


def test_pending_endpoint_lists_proposals(settings):
    # Arrange
    client = make_client(create_app(settings, FakeBrain()))
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.get("/pending")

    # Assert
    assert response.json()["pending"][0]["symbol"] == "AAPL"


def test_approve_endpoint_executes_the_trade(settings):
    # Arrange
    executor = FakeExecutor()
    client = make_client(
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
    client = make_client(
        create_app(settings, FakeBrain(), trade_executor=executor)
    )

    # Act
    response = client.post("/approve", json={"request_id": "없는아이디"})

    # Assert
    assert executor.calls == []
    assert "찾지 못했습니다" in response.json()["reply"]


def test_request_from_a_foreign_hostname_is_refused(settings):
    # Arrange — DNS 재바인딩으로 악성 사이트가 127.0.0.1의 승인 엔드포인트를
    # 대신 호출하는 상황. 브라우저는 공격자 도메인을 Host에 실어 보낸다.
    executor = FakeExecutor()
    client = make_client(
        create_app(settings, FakeBrain(), trade_executor=executor)
    )
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.post(
        "/approve",
        json={"request_id": proposal["request_id"]},
        headers={"Host": "evil.example.com"},
    )

    # Assert — 거부되고, 주문은 나가지 않고, 제안은 그대로 남는다
    assert response.status_code == 400
    assert executor.calls == []
    assert len(list_pending_requests(settings)) == 1


def test_chat_from_a_foreign_hostname_is_refused(settings):
    # Arrange — 대화 엔드포인트도 같은 경로로 남용될 수 있다 (비용·정보 유출)
    brain = FakeBrain()
    client = make_client(create_app(settings, brain))

    # Act
    response = client.post(
        "/chat",
        json={"message": "관심종목 보여줘"},
        headers={"Host": "evil.example.com"},
    )

    # Assert
    assert response.status_code == 400
    assert brain.asked == []


def test_localhost_hostname_is_accepted(settings):
    # Arrange — 정상 접속이 막히면 안 된다
    client = make_client(create_app(settings, FakeBrain()))

    # Act
    response = client.post(
        "/chat",
        json={"message": "질문"},
        headers={"Host": "localhost:8765"},
    )

    # Assert
    assert response.status_code == 200


def test_chat_endpoint_cannot_execute_a_trade(settings):
    # Arrange — 대화 경로로는 절대 주문이 나가면 안 된다
    executor = FakeExecutor()
    client = make_client(
        create_app(settings, FakeBrain(), trade_executor=executor)
    )
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act — 승인처럼 들리는 말을 채팅으로 보낸다
    client.post("/chat", json={"message": "방금 그거 승인해서 바로 사줘"})

    # Assert — 브로커는 불리지 않고 제안은 그대로 대기 중이다
    assert executor.calls == []
    assert len(list_pending_requests(settings)) == 1


def test_approve_command_in_chat_actually_approves(settings):
    # Arrange — 안내문(_HELP)이 '/승인 <번호>' 가 먹는다고 말한다. 웹에서만
    # 그 말이 거짓이었다 — /chat 이 창구를 안 거치고 두뇌로 직행했다.
    brain = FakeBrain()
    executor = FakeExecutor()
    client = make_client(create_app(settings, brain, trade_executor=executor))
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.post(
        "/chat", json={"message": f"/승인 {proposal['request_id']}"}
    )

    # Assert — 주문이 나가고, 질문으로 흘러가지 않는다
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert "예약했습니다" in response.json()["reply"]
    assert brain.asked == []
    assert list_pending_requests(settings) == []


def test_help_command_in_chat_is_not_a_question(settings):
    # Arrange
    brain = FakeBrain()
    client = make_client(create_app(settings, brain))

    # Act
    response = client.post("/chat", json={"message": "/도움"})

    # Assert — 창구가 답한다. 두뇌를 부르면 API 값만 쓰고 안내문도 아니다
    assert "/승인" in response.json()["reply"]
    assert brain.asked == []
