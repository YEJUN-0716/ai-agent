import sqlite3

import pytest

from assistant import memory
from assistant.memory import (
    append_exchange,
    append_message,
    clear_history,
    init_db,
    load_history,
)


def test_history_never_starts_with_an_assistant_message(tmp_path):
    """API는 첫 메시지가 user가 아니면 거절한다.

    limit이 홀수면 잘린 창이 assistant로 시작한다. 그대로 API에 실으면
    사장님이 무엇을 물어도 오류가 나고, 원인을 알 방법이 없다.
    """
    # Arrange — 질문·답 세 쌍
    db = tmp_path / "conversations.db"
    init_db(db)
    for i in range(3):
        append_exchange(db, "telegram", f"질문{i}", f"답변{i}")

    # Act / Assert — 어떤 limit이어도 user로 시작해야 한다
    for limit in (1, 2, 3, 4, 5, 6, 10):
        history = load_history(db, limit=limit)
        if history:
            assert history[0]["role"] == "user", f"limit={limit}"


def test_history_recovers_from_a_question_saved_without_its_answer(tmp_path):
    """저장 도중 죽어 짝이 없는 줄이 남아도 비서는 계속 답할 수 있어야 한다.

    한 번 어긋나면 그 뒤 모든 대화가 assistant로 시작해, DB를 직접 고치기
    전에는 영구히 먹통이 된다.
    """
    # Arrange — 질문만 남기고 죽은 상태를 만든다
    db = tmp_path / "conversations.db"
    init_db(db)
    append_exchange(db, "telegram", "질문0", "답변0")
    append_message(db, "telegram", "user", "답을 못 받은 질문")
    append_exchange(db, "telegram", "질문1", "답변1")

    # Act
    history = load_history(db, limit=4)

    # Assert
    assert history[0]["role"] == "user"


def test_exchange_is_saved_all_or_nothing(tmp_path, monkeypatch):
    """질문은 들어갔는데 답이 못 들어간 채로 끝나면 안 된다.

    질문 한 줄을 실제로 INSERT한 뒤 죽는 상황을 만든다. 커밋 전이므로
    트랜잭션이 통째로 되감겨야 하고, 짝이 없는 줄이 남으면 안 된다.
    """
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)
    real_connect = memory._connect

    class DiesAfterFirstRow:
        def __init__(self, conn):
            self._conn = conn

        def executemany(self, sql, rows):
            self._conn.execute(sql, rows[0])   # 질문만 들어간다
            raise sqlite3.OperationalError("저장 도중 중단")

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(
        memory, "_connect", lambda path: DiesAfterFirstRow(real_connect(path))
    )

    # Act
    with pytest.raises(sqlite3.OperationalError):
        append_exchange(db, "telegram", "질문", "답변")
    monkeypatch.undo()

    # Assert — 반쪽짜리 기록이 남지 않았다
    assert load_history(db, limit=10) == []


def test_history_is_empty_before_any_message(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)

    # Act
    history = load_history(db, limit=10)

    # Assert
    assert history == []


def test_messages_come_back_oldest_first(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)

    # Act
    append_message(db, "telegram", "user", "첫 질문")
    append_message(db, "telegram", "assistant", "첫 답변")

    # Assert
    history = load_history(db, limit=10)
    assert history == [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답변"},
    ]


def test_limit_keeps_the_most_recent_messages_in_order(tmp_path):
    # Arrange — limit보다 많은 기록을 쌓는다. 항목이 2개 이상이라야
    # "최신 것을 남기고 오래된 순서로 돌려준다"가 실제로 검증된다.
    db = tmp_path / "conversations.db"
    init_db(db)
    for i in range(5):
        append_message(db, "web", "user", f"질문 {i}")

    # Act
    history = load_history(db, limit=2)

    # Assert — 가장 최근 둘을, 오래된 것부터
    assert [m["content"] for m in history] == ["질문 3", "질문 4"]


def test_limit_larger_than_stored_returns_everything(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)
    append_message(db, "web", "user", "하나")
    append_message(db, "web", "assistant", "둘")

    # Act
    history = load_history(db, limit=100)

    # Assert
    assert [m["content"] for m in history] == ["하나", "둘"]


def test_telegram_and_web_share_the_same_history(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)

    # Act — 폰에서 묻고 PC에서 이어 묻는다
    append_message(db, "telegram", "user", "폰에서 한 질문")
    append_message(db, "web", "user", "PC에서 한 질문")

    # Assert
    history = load_history(db, limit=10)
    assert [m["content"] for m in history] == [
        "폰에서 한 질문",
        "PC에서 한 질문",
    ]


def test_roles_survive_the_round_trip(tmp_path):
    # Arrange — role이 뒤섞이면 API가 대화를 잘못 이해한다
    db = tmp_path / "conversations.db"
    init_db(db)

    # Act
    append_message(db, "web", "user", "질문")
    append_message(db, "web", "assistant", "답변")

    # Assert
    history = load_history(db, limit=10)
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_init_db_is_safe_to_call_twice(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)
    append_message(db, "web", "user", "남아 있어야 한다")

    # Act
    init_db(db)

    # Assert
    assert len(load_history(db, limit=10)) == 1


def test_init_db_creates_missing_parent_directory(tmp_path):
    # Arrange — 첫 실행 때 data/assistant/가 아직 없을 수 있다
    db = tmp_path / "없던폴더" / "conversations.db"

    # Act
    init_db(db)

    # Assert
    assert db.exists()


def test_clear_history_removes_everything(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)
    append_message(db, "web", "user", "지울 것")

    # Act
    removed = clear_history(db)

    # Assert
    assert removed == 1
    assert load_history(db, limit=10) == []
