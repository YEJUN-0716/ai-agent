import pytest

from assistant.config import Settings
from tools.assistant_notes import (
    add_note,
    add_watchlist,
    list_notes,
    list_watchlist,
    read_audit_log,
    remove_watchlist,
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


def test_added_symbol_appears_in_watchlist(settings):
    # Act
    add_watchlist(settings, "aapl", reason="실적 발표 대기")

    # Assert — 심볼은 대문자로 정규화된다
    entries = list_watchlist(settings)
    assert len(entries) == 1
    assert entries[0]["symbol"] == "AAPL"
    assert entries[0]["reason"] == "실적 발표 대기"
    assert entries[0]["added_at"].startswith("20")


def test_adding_same_symbol_twice_does_not_duplicate(settings):
    # Arrange
    add_watchlist(settings, "AAPL", reason="첫 번째")

    # Act
    result = add_watchlist(settings, "AAPL", reason="두 번째")

    # Assert
    assert result["already_present"] is True
    assert len(list_watchlist(settings)) == 1


def test_removed_symbol_disappears(settings):
    # Arrange
    add_watchlist(settings, "AAPL")

    # Act
    result = remove_watchlist(settings, "aapl")

    # Assert
    assert result["removed"] is True
    assert list_watchlist(settings) == []


def test_removing_absent_symbol_reports_not_found(settings):
    # Act
    result = remove_watchlist(settings, "TSLA")

    # Assert
    assert result["removed"] is False


def test_notes_can_be_filtered_by_symbol(settings):
    # Arrange
    add_note(settings, "AAPL", "가이던스 확인 후 재검토")
    add_note(settings, "NVDA", "밸류에이션 부담")

    # Act
    result = list_notes(settings, symbol="aapl")

    # Assert
    assert len(result) == 1
    assert result[0]["note"] == "가이던스 확인 후 재검토"


def test_notes_are_returned_newest_first(settings):
    # Arrange
    add_note(settings, "AAPL", "첫 메모")
    add_note(settings, "AAPL", "두 번째 메모")

    # Act
    result = list_notes(settings)

    # Assert
    assert result[0]["note"] == "두 번째 메모"


def test_every_write_lands_in_audit_log(settings):
    # Act
    add_watchlist(settings, "AAPL")
    add_note(settings, "AAPL", "메모")
    remove_watchlist(settings, "AAPL")

    # Assert
    lines = read_audit_log(settings)
    actions = [line.split("|")[1].strip() for line in lines]
    assert actions == ["watchlist_remove", "note_add", "watchlist_add"]


def test_empty_note_is_rejected(settings):
    # Act / Assert
    with pytest.raises(ValueError, match="메모 내용"):
        add_note(settings, "AAPL", "   ")


def test_empty_symbol_is_rejected(settings):
    # Act / Assert
    with pytest.raises(ValueError, match="종목 코드"):
        add_watchlist(settings, "  ")
