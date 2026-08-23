"""비서 설정 — .env를 읽어 검증된 Settings로 만든다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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

# 웹 창구엔 보낸 사람 번호가 없다. 창구 공통 로직이 번호를 요구하므로
# 이 값 하나를 쓴다 (허용 검사는 루프백 바인딩 + Host 검증이 한다).
WEB_CHAT_ID = 0

STUDY_INBOX_NAME = "자료넣는곳"

# 자료넣는곳 안의 이 폴더는 '이미 정리한 것'이다. 새 자료를 훑을 때 건너뛴다.
STUDY_DONE_NAME = "처리완료"
# 볼트 안에서 학업 자료 정리가 쌓이는 폴더.
STUDY_NOTES_NAME = "학업"

# 비서가 사장님 지시로 노트를 쓰는 폴더. 볼트 안에서 쓰기가 허용된 유일한 곳이다.
# 사고가 나도 이 폴더 하나만 보면 되도록 가둬 둔다.
ASSISTANT_NOTES_NAME = "비서"


class ConfigError(RuntimeError):
    """설정이 잘못됐을 때. 메시지는 사람이 읽고 바로 고칠 수 있어야 한다."""


@dataclass(frozen=True)
class Settings:
    # 비밀값은 repr에서 뺀다 — 로그·traceback에 settings가 실려도 키가 안 샌다.
    anthropic_api_key: str = field(repr=False)
    telegram_bot_token: str = field(repr=False)
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
    # 디스코드는 선택이다. 토큰이 비어 있으면 그 창구를 열지 않는다.
    discord_bot_token: str = field(default="", repr=False)
    discord_allowed_user_ids: frozenset[int] = frozenset()

    def allowed_ids(self, channel: str) -> frozenset[int]:
        """그 창구에서 답해도 되는 사람들. 모르는 창구면 아무도 없다."""
        return {
            "telegram": self.telegram_allowed_chat_ids,
            "discord": self.discord_allowed_user_ids,
            # 웹은 루프백에만 열려 있고 Host 검증까지 붙어 있다 — 접속한 사람이
            # 곧 사장님이라 명단이 없다. 대신 이 고정 번호 하나로 통과시킨다.
            "web": frozenset({WEB_CHAT_ID}),
        }.get(channel, frozenset())

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
    def assistant_notes_dir(self) -> Path:
        """비서가 노트를 쓸 수 있는 유일한 폴더."""
        return self.obsidian_vault / ASSISTANT_NOTES_NAME

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


def _parse_ids(name: str, raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(
                f"{name}에 숫자가 아닌 값이 있습니다: {chunk!r}"
            ) from exc
    if not ids:
        raise ConfigError(
            f"{name}가 비어 있습니다. 허용할 ID를 최소 하나 적어야 합니다."
        )
    return frozenset(ids)


def _optional_channel(token_name: str, ids_name: str) -> tuple[str, frozenset[int]]:
    """메신저 창구 한 곳의 토큰과 명단. 토큰이 비어 있으면 그 창구는 안 연다."""
    token = os.environ.get(token_name, "").strip()
    if not token:
        return "", frozenset()
    return token, _parse_ids(ids_name, _require(ids_name))


def _parse_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(
            f"{name} 값이 숫자가 아닙니다: {value!r}"
        ) from exc
    # 여기를 쓰는 둘(포트, 대화 기록 개수) 다 1 이상이어야 한다. 특히
    # history_limit 은 음수면 SQLite 가 LIMIT -1 = 무제한으로 읽어, 제한을
    # 거는 대신 저장된 대화 전부를 매 요청에 실어 보낸다.
    if parsed < 1:
        raise ConfigError(f"{name} 값은 1 이상이어야 합니다: {parsed}")
    return parsed


def load_settings() -> Settings:
    """환경변수를 읽어 검증된 설정을 만든다. 잘못됐으면 ConfigError."""
    api_key = _require("ANTHROPIC_API_KEY")
    # 메신저 창구는 둘 다 선택이다. 토큰을 비우면 그 창구를 열지 않는다.
    # 다만 토큰만 넣고 명단을 비워두면 봇이 접속만 하고 아무에게도 답하지
    # 않는다 — 조용히 고장나게 두지 않고 여기서 막는다.
    bot_token, chat_ids = _optional_channel(
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS"
    )
    discord_token, discord_ids = _optional_channel(
        "DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_USER_IDS"
    )

    # 2026-08-19 두 저장소를 합치면서 stock-analyzer 는 이 저장소의 하위
    # 폴더가 됐다. 그래서 기본값이 있고, 환경변수는 덮어쓰기용으로 남는다.
    stock_path = Path(
        os.environ.get("STOCK_ANALYZER_PATH", "").strip()
        or Path(__file__).resolve().parent.parent / "stock-analyzer"
    ).expanduser()
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
        discord_bot_token=discord_token,
        discord_allowed_user_ids=discord_ids,
    )
