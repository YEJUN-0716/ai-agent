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


def test_add_note_normalizes_symbol_with_whitespace(settings):
    # Act — lowercase and whitespace-padded symbol
    add_note(settings, "  aapl  ", "테스트 메모")

    # Assert — symbol is stored normalized as "AAPL"
    result = list_notes(settings)
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["note"] == "테스트 메모"


def test_duplicate_add_watchlist_no_audit_entry(settings):
    # Arrange
    add_watchlist(settings, "AAPL", reason="첫 번째")
    initial_log = read_audit_log(settings)
    initial_count = len(initial_log)

    # Act — add same symbol again
    add_watchlist(settings, "AAPL", reason="두 번째")

    # Assert — audit log should not have a new entry for duplicate
    final_log = read_audit_log(settings)
    assert len(final_log) == initial_count


def test_remove_absent_symbol_no_audit_entry(settings):
    # Arrange
    initial_log = read_audit_log(settings)
    initial_count = len(initial_log)

    # Act — remove symbol that was never added
    remove_watchlist(settings, "TSLA")

    # Assert — audit log should not have a new entry for absent symbol
    final_log = read_audit_log(settings)
    assert len(final_log) == initial_count


def test_corrupt_watchlist_file_is_preserved(settings):
    # Arrange — create a corrupt watchlist.json
    watchlist_path = settings.assistant_data_dir / "watchlist.json"
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist_path.write_text("{ invalid json", encoding="utf-8")

    # Act — try to load the corrupt file
    result = list_watchlist(settings)

    # Assert — returns empty list, corrupt file is backed up, audit entry exists
    assert result == []
    corrupt_files = list(watchlist_path.parent.glob("watchlist.json.*.corrupt"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_text(encoding="utf-8") == "{ invalid json"

    audit_lines = read_audit_log(settings)
    assert any("file_corrupt" in line for line in audit_lines)


def test_missing_notes_file_no_backup_no_audit(settings):
    # Arrange — notes.json does not exist
    notes_path = settings.assistant_data_dir / "notes.json"
    assert not notes_path.exists()
    initial_log = read_audit_log(settings)
    initial_count = len(initial_log)

    # Act — try to load missing notes file
    result = list_notes(settings)

    # Assert — returns empty list, no backup file created, no audit entry
    assert result == []
    corrupt_files = list(notes_path.parent.glob("notes.json.*.corrupt"))
    assert len(corrupt_files) == 0
    final_log = read_audit_log(settings)
    assert len(final_log) == initial_count
