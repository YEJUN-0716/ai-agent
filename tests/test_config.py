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
