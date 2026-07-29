import pytest

from assistant.config import ConfigError, load_settings


def _base_env(tmp_path):
    stock_dir = tmp_path / "stock-analyzer"
    stock_dir.mkdir()
    return {
        "ANTHROPIC_API_KEY": "sk-test-key",
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "TELEGRAM_ALLOWED_CHAT_IDS": "111, 222",
        "STOCK_ANALYZER_PATH": str(stock_dir),
        "ASSISTANT_DATA_DIR": str(tmp_path / "data" / "assistant"),
    }


def test_loads_settings_from_environment(tmp_path, monkeypatch):
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)

    # Act
    settings = load_settings()

    # Assert
    assert settings.anthropic_api_key == "sk-test-key"
    assert settings.telegram_allowed_chat_ids == frozenset({111, 222})
    assert settings.model == "claude-opus-5"
    assert settings.effort == "medium"
    assert settings.history_limit == 40
    assert settings.assistant_data_dir.exists()


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


def test_effort_can_be_overridden(tmp_path, monkeypatch):
    # Arrange
    for key, value in _base_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ASSISTANT_EFFORT", "low")

    # Act
    settings = load_settings()

    # Assert
    assert settings.effort == "low"
