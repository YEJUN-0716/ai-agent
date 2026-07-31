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

# 웹 창구는 이 PC 안에서만 연다. 설계 단계에서 외부 통로를 열지 않기로 했고
# 웹 계층의 Host 검증도 이 목록에 맞춰져 있다. 다른 값을 넣으면 서버는 뜨지만
# 모든 요청이 400이 되므로, 조용히 고장나게 두지 말고 시작할 때 알린다.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

STUDY_INBOX_NAME = "자료넣는곳"

# 자료넣는곳 안의 이 폴더는 '이미 정리한 것'이다. 새 자료를 훑을 때 건너뛴다.
STUDY_DONE_NAME = "처리완료"
# 볼트 안에서 비서가 노트를 쓰는 유일한 폴더. 사장님의 다른 노트는 건드리지 않는다.
STUDY_NOTES_NAME = "학업"


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
    study_inbox: Path
    obsidian_vault: Path

    @property
    def db_path(self) -> Path:
        return self.assistant_data_dir / "conversations.db"

    @property
    def audit_log_path(self) -> Path:
        return self.assistant_data_dir / "audit.log"

    @property
    def study_done_dir(self) -> Path:
        """정리를 마친 원본 PDF를 옮겨두는 곳. 지우지 않고 쌓아둔다."""
        return self.study_inbox / STUDY_DONE_NAME

    @property
    def study_notes_dir(self) -> Path:
        return self.obsidian_vault / STUDY_NOTES_NAME

    @property
    def materials_path(self) -> Path:
        return self.assistant_data_dir / "materials.json"


def _default_desktop() -> Path:
    """사장님이 실제로 보는 바탕화면.

    윈도우에서 OneDrive를 켜면 바탕화면이 ~/OneDrive/Desktop 으로 옮겨간다.
    그때 ~/Desktop 은 남아 있어도 화면에 안 보이는 껍데기라, 거기에 폴더를
    만들면 "만들었다는데 안 보인다"가 된다. OneDrive 쪽을 먼저 본다.
    """
    onedrive = Path.home() / "OneDrive" / "Desktop"
    return onedrive if onedrive.is_dir() else Path.home() / "Desktop"


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


def _parse_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(
            f"{name} 값이 숫자가 아닙니다: {value!r}"
        ) from exc


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

    web_host = os.environ.get("ASSISTANT_WEB_HOST", DEFAULT_WEB_HOST).strip()
    if web_host not in LOOPBACK_HOSTS:
        raise ConfigError(
            f"ASSISTANT_WEB_HOST는 이 PC 안에서만 여는 주소여야 합니다: "
            f"{web_host!r}는 쓸 수 없습니다. "
            f"가능한 값: {', '.join(sorted(LOOPBACK_HOSTS))}. "
            "휴대폰에서 쓰시려면 웹이 아니라 텔레그램을 이용하세요 — "
            "외부에 웹을 여는 것은 설계에서 제외했습니다."
        )

    effort = os.environ.get("ASSISTANT_EFFORT", DEFAULT_EFFORT).strip()
    if effort not in VALID_EFFORTS:
        raise ConfigError(
            f"ASSISTANT_EFFORT 값이 잘못됐습니다: {effort!r}. "
            f"가능한 값: {', '.join(sorted(VALID_EFFORTS))}"
        )

    vault = Path(_require("OBSIDIAN_VAULT")).expanduser()
    if not vault.is_dir():
        raise ConfigError(
            f"OBSIDIAN_VAULT가 가리키는 폴더가 없습니다: {vault}. "
            "옵시디언에서 볼트 폴더를 확인해 .env에 정확한 경로를 적어주세요."
        )

    # 받는 곳은 없으면 만든다. 볼트와 달리 비서가 소유하는 폴더다.
    configured = os.environ.get("STUDY_INBOX", "").strip()
    study_inbox = (
        Path(configured).expanduser() if configured
        else _default_desktop() / STUDY_INBOX_NAME
    )
    study_inbox.mkdir(parents=True, exist_ok=True)

    return Settings(
        anthropic_api_key=api_key,
        telegram_bot_token=bot_token,
        telegram_allowed_chat_ids=chat_ids,
        stock_analyzer_path=stock_path,
        assistant_data_dir=data_dir,
        model=os.environ.get("ASSISTANT_MODEL", DEFAULT_MODEL).strip(),
        effort=effort,
        web_host=web_host,
        web_port=_parse_int("ASSISTANT_WEB_PORT", DEFAULT_WEB_PORT),
        history_limit=_parse_int("ASSISTANT_HISTORY_LIMIT", DEFAULT_HISTORY_LIMIT),
        study_inbox=study_inbox,
        obsidian_vault=vault,
    )
