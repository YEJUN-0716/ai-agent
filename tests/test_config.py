import pytest

from assistant.config import ConfigError, load_settings


def _base_env(tmp_path):
    stock_dir = tmp_path / "stock-analyzer"
    stock_dir.mkdir()
    vault = tmp_path / "볼트"
    vault.mkdir()
    return {
        "ANTHROPIC_API_KEY": "sk-test-key",
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_ALLOWED_CHAT_IDS": "111, 222",
        "STOCK_ANALYZER_PATH": str(stock_dir),
        "ASSISTANT_DATA_DIR": str(tmp_path / "data" / "assistant"),
        "OBSIDIAN_VAULT": str(vault),
        "STUDY_INBOX": str(tmp_path / "자료넣는곳"),
    }


def test_loads_settings_from_environment(tmp_path, monkeypatch):
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    # Isolate from any real .env file
    monkeypatch.delenv("ASSISTANT_MODEL", raising=False)
    monkeypatch.delenv("ASSISTANT_EFFORT", raising=False)
    monkeypatch.delenv("ASSISTANT_WEB_HOST", raising=False)
    monkeypatch.delenv("ASSISTANT_WEB_PORT", raising=False)
    monkeypatch.delenv("ASSISTANT_HISTORY_LIMIT", raising=False)

    # Act
    settings = load_settings()

    # Assert
    assert settings.anthropic_api_key == "sk-test-key"
    assert settings.telegram_allowed_chat_ids == frozenset({111, 222})
    assert settings.model == "claude-opus-5"
    assert settings.effort == "medium"
    assert settings.history_limit == 40
    assert settings.assistant_data_dir.exists()


def test_messenger_channels_are_optional(tmp_path, monkeypatch):
    """토큰을 비우면 그 창구는 안 연다. 서버가 못 뜨면 안 된다."""
    # Arrange — 텔레그램을 끄고 디스코드만 켠 상태
    env = _base_env(tmp_path)
    del env["TELEGRAM_BOT_TOKEN"]
    del env["TELEGRAM_ALLOWED_CHAT_IDS"]
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "d-token")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "999")

    # Act
    settings = load_settings()

    # Assert
    assert settings.telegram_bot_token == ""
    assert settings.telegram_allowed_chat_ids == frozenset()
    assert settings.discord_allowed_user_ids == frozenset({999})


def test_channel_with_a_token_but_no_allowlist_is_rejected(tmp_path, monkeypatch):
    """토큰만 넣고 명단을 비우면 봇이 접속만 하고 아무에게도 답하지 않는다."""
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "d-token")
    monkeypatch.delenv("DISCORD_ALLOWED_USER_IDS", raising=False)

    # Act / Assert
    with pytest.raises(ConfigError, match="DISCORD_ALLOWED_USER_IDS"):
        load_settings()


def test_raises_when_api_key_missing(tmp_path, monkeypatch):
    # Arrange
    env = _base_env(tmp_path)
    del env["ANTHROPIC_API_KEY"]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Act / Assert
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings()


def test_raises_when_stock_analyzer_path_does_not_exist(tmp_path, monkeypatch):
    # Arrange
    env = _base_env(tmp_path)
    env["STOCK_ANALYZER_PATH"] = str(tmp_path / "nope")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Act / Assert
    with pytest.raises(ConfigError, match="STOCK_ANALYZER_PATH"):
        load_settings()


def test_non_loopback_web_host_is_rejected_at_startup(tmp_path, monkeypatch):
    # Arrange — 외부에 웹을 여는 것은 설계에서 제외했다. 예전에는 이 값을
    # 통과시킨 뒤 모든 요청이 400이 나서 왜 안 되는지 알 수 없었다.
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ASSISTANT_WEB_HOST", "0.0.0.0")

    # Act / Assert
    with pytest.raises(ConfigError, match="ASSISTANT_WEB_HOST"):
        load_settings()


def test_loopback_web_host_is_accepted(tmp_path, monkeypatch):
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ASSISTANT_WEB_HOST", "localhost")

    # Act
    settings = load_settings()

    # Assert
    assert settings.web_host == "localhost"


def test_effort_can_be_overridden(tmp_path, monkeypatch):
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    # Isolate from any real .env file (except ASSISTANT_EFFORT which we test)
    monkeypatch.delenv("ASSISTANT_MODEL", raising=False)
    monkeypatch.delenv("ASSISTANT_WEB_HOST", raising=False)
    monkeypatch.delenv("ASSISTANT_WEB_PORT", raising=False)
    monkeypatch.delenv("ASSISTANT_HISTORY_LIMIT", raising=False)
    monkeypatch.setenv("ASSISTANT_EFFORT", "low")

    # Act
    settings = load_settings()

    # Assert
    assert settings.effort == "low"


def test_raises_when_web_port_is_not_numeric(tmp_path, monkeypatch):
    # Arrange
    env = _base_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ASSISTANT_MODEL", raising=False)
    monkeypatch.delenv("ASSISTANT_EFFORT", raising=False)
    monkeypatch.delenv("ASSISTANT_WEB_HOST", raising=False)
    monkeypatch.delenv("ASSISTANT_HISTORY_LIMIT", raising=False)
    monkeypatch.setenv("ASSISTANT_WEB_PORT", "not-a-number")

    # Act / Assert
    with pytest.raises(ConfigError, match="ASSISTANT_WEB_PORT"):
        load_settings()


def test_study_folders_come_from_env(tmp_path, monkeypatch):
    """자료 폴더와 볼트 경로를 .env로 바꿀 수 있어야 한다."""
    # Arrange
    env = _base_env(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Act
    settings = load_settings()

    # Assert
    assert settings.study_inbox == tmp_path / "자료넣는곳"
    assert settings.study_done_dir == tmp_path / "자료넣는곳" / "처리완료"
    assert settings.study_notes_dir == tmp_path / "볼트" / "학업"
    assert settings.materials_path == settings.assistant_data_dir / "materials.json"


def test_missing_vault_is_reported_in_plain_korean(tmp_path, monkeypatch):
    """볼트 경로가 없으면 시작할 때 막고, 무엇을 고쳐야 하는지 알려준다."""
    # Arrange
    env = _base_env(tmp_path)
    del env["OBSIDIAN_VAULT"]
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Act / Assert
    with pytest.raises(ConfigError, match="OBSIDIAN_VAULT"):
        load_settings()


def test_vault_folder_must_exist(tmp_path, monkeypatch):
    """오타로 없는 폴더를 적으면 조용히 새로 만들지 않고 알린다."""
    # Arrange
    env = _base_env(tmp_path)
    env["OBSIDIAN_VAULT"] = str(tmp_path / "없는볼트")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Act / Assert
    with pytest.raises(ConfigError, match="OBSIDIAN_VAULT"):
        load_settings()


def test_study_inbox_is_created_if_missing(tmp_path, monkeypatch):
    """받는 곳은 비서가 소유하는 폴더다. 없으면 만들어 준다."""
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)

    # Act
    settings = load_settings()

    # Assert
    assert settings.study_inbox.is_dir()


def test_secrets_are_not_in_repr(tmp_path, monkeypatch):
    """로그나 traceback에 settings가 실려도 키가 새면 안 된다."""
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-secret")
    monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", "333")

    # Act
    text = repr(load_settings())

    # Assert
    for secret in ("sk-test-key", "123:abc", "discord-secret"):
        assert secret not in text
