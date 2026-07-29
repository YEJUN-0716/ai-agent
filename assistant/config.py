"""비서 설정 — .env를 읽어 검증된 Settings로 만든다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"
DEFAULT_HISTORY_LIMIT = 40
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765
VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class ConfigError(RuntimeError):
    """설정이 잘못됐을 때. 메시지는 사람이 읽고 바로 고칠 수 있어야 한다."""


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    telegram_bot_token: str
    telegram_allowed_chat_ids: frozenset[int]
    stock_analyzer_path: Path
    assistant_data_dir: Path
    model: str
    effort: str
    web_host: str
    web_port: int
    history_limit: int

    @property
    def db_path(self) -> Path:
        return self.assistant_data_dir / "conversations.db"

    @property
    def audit_log_path(self) -> Path:
        return self.assistant_data_dir / "audit.log"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name}가 비어 있습니다. .env 파일에 값을 채워 넣으세요."
        )
    return value


def _parse_chat_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_CHAT_IDS에 숫자가 아닌 값이 있습니다: {chunk!r}"
            ) from exc
    if not ids:
        raise ConfigError(
            "TELEGRAM_ALLOWED_CHAT_IDS가 비어 있습니다. "
            "허용할 텔레그램 chat_id를 최소 하나 적어야 합니다."
        )
    return frozenset(ids)


def load_settings() -> Settings:
    """환경변수를 읽어 검증된 설정을 만든다. 잘못됐으면 ConfigError."""
    api_key = _require("ANTHROPIC_API_KEY")
    bot_token = _require("TELEGRAM_BOT_TOKEN")
    chat_ids = _parse_chat_ids(_require("TELEGRAM_ALLOWED_CHAT_IDS"))

    stock_path = Path(_require("STOCK_ANALYZER_PATH")).expanduser()
    if not stock_path.is_dir():
        raise ConfigError(
            f"STOCK_ANALYZER_PATH가 가리키는 폴더가 없습니다: {stock_path}"
        )

    data_dir = Path(
        os.environ.get("ASSISTANT_DATA_DIR", "data/assistant")
    ).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    effort = os.environ.get("ASSISTANT_EFFORT", DEFAULT_EFFORT).strip()
    if effort not in VALID_EFFORTS:
        raise ConfigError(
            f"ASSISTANT_EFFORT 값이 잘못됐습니다: {effort!r}. "
            f"가능한 값: {', '.join(sorted(VALID_EFFORTS))}"
        )

    return Settings(
        anthropic_api_key=api_key,
        telegram_bot_token=bot_token,
        telegram_allowed_chat_ids=chat_ids,
        stock_analyzer_path=stock_path,
        assistant_data_dir=data_dir,
        model=os.environ.get("ASSISTANT_MODEL", DEFAULT_MODEL).strip(),
        effort=effort,
        web_host=os.environ.get("ASSISTANT_WEB_HOST", DEFAULT_WEB_HOST).strip(),
        web_port=int(os.environ.get("ASSISTANT_WEB_PORT", DEFAULT_WEB_PORT)),
        history_limit=int(
            os.environ.get("ASSISTANT_HISTORY_LIMIT", DEFAULT_HISTORY_LIMIT)
        ),
    )
