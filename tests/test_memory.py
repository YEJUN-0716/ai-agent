from assistant.memory import append_message, clear_history, init_db, load_history


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
