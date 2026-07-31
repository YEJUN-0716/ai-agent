import pytest

from assistant.brain import (
    MAX_TOOL_ITERATIONS,
    SYSTEM_PROMPT,
    Brain,
    BrainError,
)
from assistant.config import Settings
from assistant.memory import init_db, load_history


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


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [FakeBlock(text)]


class FakeRunner:
    def __init__(self, text: str) -> None:
        self._text = text

    def __iter__(self):
        yield FakeMessage(self._text)


class FakeClient:
    """anthropic 클라이언트를 대신한다. 호출 인자를 기록해 둔다."""

    def __init__(self, reply: str = "안녕하세요") -> None:
        self.reply = reply
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def tool_runner(self, **kwargs):
                outer.calls.append(kwargs)
                return FakeRunner(outer.reply)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


def test_answer_is_returned_and_stored(settings):
    # Arrange
    init_db(settings.db_path)
    brain = Brain(settings, client=FakeClient(reply="가상 자본은 1,012만원입니다"))

    # Act
    answer = brain.ask("가상 브로커 얼마야?", channel="telegram")

    # Assert
    assert answer == "가상 자본은 1,012만원입니다"
    assert load_history(settings.db_path, limit=10) == [
        {"role": "user", "content": "가상 브로커 얼마야?"},
        {"role": "assistant", "content": "가상 자본은 1,012만원입니다"},
    ]


def test_previous_turns_are_sent_to_the_model(settings):
    # Arrange
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)
    brain.ask("첫 질문", channel="web")

    # Act
    brain.ask("두 번째 질문", channel="web")

    # Assert — 두 번째 호출에는 앞선 대화가 순서대로 실려 있다
    messages = client.calls[1]["messages"]
    assert [m["content"] for m in messages] == [
        "첫 질문",
        "안녕하세요",
        "두 번째 질문",
    ]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_model_and_effort_come_from_settings(settings):
    # Arrange
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["effort"] == "medium"
    assert call["thinking"] == {"type": "adaptive"}


def test_forbidden_sampling_parameters_are_never_sent(settings):
    # Arrange — claude-opus-5는 이 값들을 받으면 400을 낸다
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    call = client.calls[0]
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert forbidden not in call


def test_study_tools_are_available(settings):
    """네 가지 도구가 비서에게 보여야 한다."""
    # Arrange / Act
    names = Brain(settings, client=object()).tool_names()

    # Assert
    for tool in ("list_new_materials", "summarize_new_material",
                 "list_materials", "ask_material"):
        assert tool in names


def test_model_cannot_delete_materials(settings):
    """지우는 도구는 주지 않는다. 원본 삭제는 사장님 몫이다."""
    # Arrange / Act
    names = Brain(settings, client=object()).tool_names()

    # Assert
    assert not any("delete" in n or "remove_material" in n for n in names)


def test_model_is_never_given_a_trade_execution_tool(settings):
    # Arrange — 이 프로젝트의 핵심 안전장치
    brain = Brain(settings, client=FakeClient())

    # Act
    names = brain.tool_names()

    # Assert — 제안은 있고 실행은 없다
    assert "request_trade" in names
    assert "approve_request" not in names
    assert "reject_request" not in names
    assert not any(
        "approve" in n or "execute" in n or "order" in n for n in names
    )


def test_tools_actually_reach_the_model(settings):
    # Arrange — 도구 목록이 비어 있으면 위 안전장치 테스트가 공허해진다
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    assert len(client.calls[0]["tools"]) == len(brain.tool_names())
    assert len(brain.tool_names()) >= 10


def test_system_prompt_is_cached(settings):
    # Arrange
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    system = client.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_system_prompt_tells_the_model_it_cannot_execute_trades():
    # Arrange / Act — 도구를 안 주는 것과 별개로, 모델이 "승인한 셈 치고"
    # 말하지 않도록 프롬프트에도 못 박아야 한다

    # Assert
    assert "실행 권한이 없습니다" in SYSTEM_PROMPT


def test_empty_reply_gets_a_readable_fallback(settings):
    # Arrange
    init_db(settings.db_path)
    brain = Brain(settings, client=FakeClient(reply=""))

    # Act
    answer = brain.ask("질문", channel="web")

    # Assert — 빈 답을 그대로 내보내지 않는다
    assert "답변을 만들지 못했습니다" in answer


class FailingRunner:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __iter__(self):
        raise self._exc
        yield  # pragma: no cover — 제너레이터로 만들기 위한 줄


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        outer = self

        class _Messages:
            def tool_runner(self, **kwargs):
                return FailingRunner(outer._exc)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


def test_credit_exhaustion_is_explained_in_plain_korean(settings):
    # Arrange — 사장님이 처음 실행했을 때 실제로 만난 오류
    init_db(settings.db_path)
    raw = RuntimeError(
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': 'Your credit balance is too low "
        "to access the Anthropic API. Please go to Plans & Billing to upgrade "
        "or purchase credits.'}}"
    )
    brain = Brain(settings, client=FailingClient(raw))

    # Act
    with pytest.raises(BrainError) as caught:
        brain.ask("가상 브로커 얼마야?", channel="telegram")

    # Assert — 무엇을 어디서 해야 하는지가 담기고, 개발자용 원문은 빠진다
    message = str(caught.value)
    assert "크레딧" in message
    assert "Plans & Billing" in message
    assert "Error code: 400" not in message


def test_unknown_failure_keeps_the_original_text(settings):
    # Arrange — 모르는 오류를 그럴듯하게 지어내지 않는다
    init_db(settings.db_path)
    brain = Brain(settings, client=FailingClient(RuntimeError("디스크 폭발")))

    # Act
    with pytest.raises(BrainError) as caught:
        brain.ask("질문", channel="web")

    # Assert
    assert "디스크 폭발" in str(caught.value)


class EndlessRunner:
    """도구를 무한히 호출하며 맴도는 모델을 흉내낸다."""

    def __init__(self) -> None:
        self.yielded = 0

    def __iter__(self):
        while True:
            self.yielded += 1
            yield FakeMessage("계속 도구를 쓰는 중")


class EndlessClient:
    def __init__(self) -> None:
        self.runner = EndlessRunner()
        outer = self

        class _Messages:
            def tool_runner(self, **kwargs):
                return outer.runner

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


def test_runaway_tool_loop_is_cut_off(settings):
    # Arrange — 상한이 없으면 여기서 영원히 돌며 비용만 쌓인다
    init_db(settings.db_path)
    client = EndlessClient()
    brain = Brain(settings, client=client)

    # Act
    answer = brain.ask("질문", channel="web")

    # Assert — 멈추고, 왜 멈췄는지 사장님께 알린다
    assert client.runner.yielded == MAX_TOOL_ITERATIONS
    assert "멈췄습니다" in answer


def test_history_respects_the_configured_limit(settings):
    # Arrange — 오래된 대화가 무한정 실리면 비용이 커진다
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)
    for i in range(25):
        brain.ask(f"질문 {i}", channel="web")

    # Act
    last_call = client.calls[-1]

    # Assert — 과거 history_limit개 + 이번 질문 1개가 상한
    assert len(last_call["messages"]) <= settings.history_limit + 1
