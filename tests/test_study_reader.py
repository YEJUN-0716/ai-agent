import base64
import json

import pytest

from assistant.config import Settings
from tools.study_materials import MAX_PDF_BYTES, StudyError
from tools.study_reader import ask_pdf, summarize_pdf


class FakeMessages:
    """요청을 붙잡아 두고 정해진 답을 돌려준다."""

    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        reply = self.reply_text

        class Block:
            type = "text"
            text = reply

        class Response:
            content = [Block()]

        return Response()


class FakeClient:
    def __init__(self, reply_text: str) -> None:
        self.messages = FakeMessages(reply_text)


SUMMARY_JSON = json.dumps(
    {"title": "제목", "one_line": "한 줄", "note_body": "본문"}
)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "논문.pdf"
    path.write_bytes(b"%PDF-1.4 fake content")
    return path


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=tmp_path,
        assistant_data_dir=tmp_path,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
        study_inbox=tmp_path,
        obsidian_vault=tmp_path,
    )


def test_pdf_is_attached_as_a_document_block(settings, pdf):
    """PDF를 문서 블록으로 붙여야 그림과 수식까지 본다.

    글자만 뽑아 텍스트로 넣으면 그림이 사라진다. 이 테스트가 그것을 막는다.
    """
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    document = client.messages.last_kwargs["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(document["source"]["data"]) == pdf.read_bytes()


def test_document_comes_before_the_question(settings, pdf):
    """문서를 텍스트보다 앞에 두라는 것이 API 권고사항이다."""
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    blocks = client.messages.last_kwargs["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["document", "text"]


def test_base64_has_no_newlines(settings, pdf):
    """줄바꿈이 섞이면 API가 거절한다."""
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    data = client.messages.last_kwargs["messages"][0]["content"][0]["source"]["data"]
    assert "\n" not in data


def test_forbidden_parameters_are_never_sent(settings, pdf):
    """claude-opus-5에서 400을 내는 항목들."""
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert forbidden not in client.messages.last_kwargs


def test_summary_comes_back_as_three_parts(settings, pdf):
    # Arrange
    client = FakeClient(json.dumps({
        "title": "어떤 논문",
        "one_line": "요지는 이렇다",
        "note_body": "## 핵심\n- 하나",
    }))

    # Act
    result = summarize_pdf(settings, pdf, client=client)

    # Assert
    assert result["title"] == "어떤 논문"
    assert result["one_line"] == "요지는 이렇다"
    assert "## 핵심" in result["note_body"]


def test_json_inside_a_code_fence_is_still_read(settings, pdf):
    """모델이 ```json 울타리를 칠 때가 있다."""
    # Arrange
    client = FakeClient(f"```json\n{SUMMARY_JSON}\n```")

    # Act
    result = summarize_pdf(settings, pdf, client=client)

    # Assert
    assert result["title"] == "제목"


def test_oversized_pdf_is_refused_before_spending_money(settings, tmp_path):
    """한도 초과는 보내기 전에 막는다. 보내고 400을 받으면 요금만 나간다."""
    # Arrange
    big = tmp_path / "큰자료.pdf"
    big.write_bytes(b"x" * (MAX_PDF_BYTES + 1))
    client = FakeClient("{}")

    # Act / Assert
    with pytest.raises(StudyError) as exc:
        summarize_pdf(settings, big, client=client)
    assert "32" in str(exc.value)
    assert client.messages.last_kwargs is None


def test_a_reply_that_is_not_json_is_reported_not_guessed(settings, pdf):
    """모델이 형식을 어기면 지어내지 말고 실패로 알린다."""
    # Arrange
    client = FakeClient("음... 요약을 못 하겠습니다")

    # Act / Assert
    with pytest.raises(StudyError):
        summarize_pdf(settings, pdf, client=client)


def test_ask_pdf_puts_the_question_after_the_document(settings, pdf):
    # Arrange
    client = FakeClient("3장의 예시는 이러이러합니다")

    # Act
    answer = ask_pdf(settings, pdf, "3장 예시가 뭐였지?", client=client)

    # Assert
    blocks = client.messages.last_kwargs["messages"][0]["content"]
    assert blocks[0]["type"] == "document"
    assert "3장 예시가 뭐였지?" in blocks[1]["text"]
    assert answer == "3장의 예시는 이러이러합니다"
