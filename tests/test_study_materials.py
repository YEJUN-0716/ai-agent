from pathlib import Path

import pytest

from assistant.config import Settings
from tools.study_materials import MAX_PDF_BYTES, StudyError, list_new


@pytest.fixture
def settings(tmp_path) -> Settings:
    inbox = tmp_path / "자료넣는곳"
    inbox.mkdir()
    (inbox / "처리완료").mkdir()
    vault = tmp_path / "볼트"
    (vault / "학업").mkdir(parents=True)
    data = tmp_path / "assistant"
    data.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=tmp_path,
        assistant_data_dir=data,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
        study_inbox=inbox,
        obsidian_vault=vault,
    )


def test_lists_waiting_pdfs(settings):
    """자료넣는곳의 PDF를 찾아낸다."""
    # Arrange
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["논문.pdf"]


def test_skips_already_processed(settings):
    """처리완료 폴더는 건너뛴다. 안 그러면 매번 다시 정리한다."""
    # Arrange
    (settings.study_inbox / "새자료.pdf").write_bytes(b"%PDF-1.4 fake")
    (settings.study_done_dir / "지난자료.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["새자료.pdf"]


def test_skips_files_that_are_not_pdf(settings):
    """PDF가 아닌 파일은 목록에 넣지 않는다."""
    # Arrange
    (settings.study_inbox / "메모.txt").write_text("안녕", encoding="utf-8")
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["논문.pdf"]


def test_marks_oversized_files_instead_of_hiding_them(settings):
    """32MB 초과도 목록에는 넣고 표시만 한다.

    목록에서 빼버리면 사장님은 자기가 넣은 파일이 왜 안 보이는지 알 수 없다.
    """
    # Arrange
    (settings.study_inbox / "큰자료.pdf").write_bytes(b"x" * (MAX_PDF_BYTES + 1))

    # Act
    waiting = list_new(settings)

    # Assert
    assert waiting[0]["too_large"] is True


def test_lists_alphabetically_for_a_stable_order(settings):
    """순서가 실행마다 바뀌면 목록을 믿기 어렵다."""
    # Arrange
    for name in ("다.pdf", "가.pdf", "나.pdf"):
        (settings.study_inbox / name).write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["가.pdf", "나.pdf", "다.pdf"]
