# AI 업무 비서 챗봇 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 텔레그램과 PC 웹 양쪽에서 쓰는 개인 업무 비서 챗봇을 만들고, 1단계 도구로 주식 분석 질의·기록·가상매매 제안을 붙인다.

**Architecture:** 자택 PC에 파이썬 서버 하나를 띄운다. `assistant/brain.py`가 Claude API 도구 호출 루프를 돌리는 유일한 두뇌이고, 텔레그램·웹은 입출력 창구일 뿐이다. 도구는 `tools/` 밑의 평범한 파이썬 함수이며, stock-analyzer와의 접점은 `tools/stock_reader.py` 한 파일로 격리한다.

**Tech Stack:** Python 3.11+, `anthropic` SDK (`claude-opus-5`, beta tool runner), `python-telegram-bot` (폴링), FastAPI + uvicorn, SQLite (표준 라이브러리), pytest.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-07-29-ai-assistant-design.md` — 충돌 시 설계 문서가 우선한다.
- 모델 ID는 정확히 `claude-opus-5`. 날짜 접미사를 붙이지 않는다.
- `temperature` / `top_p` / `top_k` / `budget_tokens`를 절대 넘기지 않는다 — `claude-opus-5`에서 400 오류.
- 시크릿은 `.env`에만. 코드·테스트·문서·커밋 메시지에 실제 키를 넣지 않는다.
- **stock-analyzer 저장소에 쓰기 금지.** 유일한 예외는 `modules/virtual_broker.py`가 공개한 `place_notional_buy` / `place_market_sell` 호출이며, 이것도 사용자 승인 후 채널 계층에서만 실행한다.
- **비서(모델)에게 매매 실행 도구를 주지 않는다.** 모델은 제안까지만 할 수 있다.
- 실제 증권 주문 도구는 만들지 않는다.
- 모든 쓰기 행동은 `data/assistant/audit.log`에 기록한다.
- 시각은 KST(UTC+9), ISO 8601 형식. 날짜만 필요할 땐 `YYYY-MM-DD`.
- 타입 힌트를 모든 함수 시그니처에 붙인다 (PEP 8).
- 테스트는 pytest. 커버리지 80% 이상.
- 테스트는 진짜 stock-analyzer 데이터에 의존하지 않는다 — `tmp_path` 픽스처로 가짜 파일을 만든다.

---

### Task 1: 설정과 프로젝트 뼈대

**Files:**
- Create: `assistant/__init__.py`
- Create: `assistant/config.py`
- Create: `channels/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`
- Modify: `requirements.txt`
- Modify: `.env.example`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces: `assistant.config.Settings` (frozen dataclass), `assistant.config.load_settings() -> Settings`, `assistant.config.ConfigError`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config.py`:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'assistant'`

- [ ] **Step 3: 최소 구현을 쓴다**

빈 `assistant/__init__.py`, `channels/__init__.py`, `tests/__init__.py`를 만든다.

`assistant/config.py`:

```python
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
```

`requirements.txt`를 다음으로 교체:

```
python-dotenv
anthropic
python-telegram-bot
fastapi
uvicorn
pytest
pytest-cov
```

`.env.example` 끝에 추가:

```
# ── AI 업무 비서 (server.py) ──────────────────────────────────────────
# ANTHROPIC_API_KEY=
# TELEGRAM_BOT_TOKEN=
# 내 텔레그램 chat_id. 쉼표로 여러 개. 여기 없는 사람은 봇이 무시한다.
# TELEGRAM_ALLOWED_CHAT_IDS=
# STOCK_ANALYZER_PATH=C:\Users\1aass\stock-analyzer
# ASSISTANT_DATA_DIR=data/assistant
# 생각 깊이. 비용을 더 줄이려면 low. (low|medium|high|xhigh|max)
# ASSISTANT_EFFORT=medium
# ASSISTANT_WEB_HOST=127.0.0.1
# ASSISTANT_WEB_PORT=8765
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pip install -r requirements.txt && pytest tests/test_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add assistant/ channels/ tests/ requirements.txt .env.example
git commit -m "feat: 비서 설정 로더와 프로젝트 뼈대"
```

---

### Task 2: 주식 데이터 읽기 도구

**Files:**
- Create: `tools/stock_reader.py`
- Create: `tests/test_stock_reader.py`

**Interfaces:**
- Consumes: `assistant.config.Settings` (`stock_analyzer_path`)
- Produces: `tools.stock_reader.StockDataError`, 그리고 `Settings`를 받는 함수 4개 —
  `get_virtual_portfolio(settings) -> dict`,
  `get_equity_history(settings, limit: int = 30) -> list[dict]`,
  `get_recent_signals(settings, limit: int = 10) -> list[dict]`,
  `get_analyst_scores(settings, limit: int = 5) -> list[dict]`

실제 파일 형태(확인 완료):
- `equity_log.json` → `{"records": [...]}`
- `signal_log.json` → `{"signals": [{"symbol","action","entry_date","entry_price","score","rsi","return_pct", ...}]}`
- `virtual_portfolio.json` → `{"cash_krw","positions","pending","realized_pnl_krw","trades"}` (아직 없을 수 있음)
- `data/analyst_log/<연도>.jsonl` → 한 줄당 `{"date","regime","scores":{"AAPL":{"chart":73.8,"ict":100.0}}}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_stock_reader.py`:

```python
import json

import pytest

from assistant.config import Settings
from tools.stock_reader import (
    StockDataError,
    get_analyst_scores,
    get_equity_history,
    get_recent_signals,
    get_virtual_portfolio,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    stock_dir = tmp_path / "stock-analyzer"
    (stock_dir / "data" / "analyst_log").mkdir(parents=True)
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=stock_dir,
        assistant_data_dir=tmp_path / "assistant",
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
    )


def test_returns_empty_portfolio_when_file_absent(settings):
    # Arrange — virtual_portfolio.json을 만들지 않는다 (첫 실행 전 상태)

    # Act
    result = get_virtual_portfolio(settings)

    # Assert
    assert result["positions"] == {}
    assert result["started"] is False


def test_reads_positions_from_portfolio_file(settings):
    # Arrange
    (settings.stock_analyzer_path / "virtual_portfolio.json").write_text(
        json.dumps({
            "cash_krw": 9_000_000,
            "positions": {"AAPL": {"qty": 3, "avg_price_usd": 330.0,
                                   "entry_date": "2026-07-20"}},
            "pending": [],
            "realized_pnl_krw": 120_000.0,
            "trades": [],
        }),
        encoding="utf-8",
    )

    # Act
    result = get_virtual_portfolio(settings)

    # Assert
    assert result["started"] is True
    assert result["cash_krw"] == 9_000_000
    assert result["positions"]["AAPL"]["qty"] == 3
    assert result["realized_pnl_krw"] == 120_000.0


def test_raises_readable_error_on_corrupt_json(settings):
    # Arrange
    (settings.stock_analyzer_path / "virtual_portfolio.json").write_text(
        "{ 망가진 파일", encoding="utf-8"
    )

    # Act / Assert
    with pytest.raises(StockDataError, match="virtual_portfolio.json"):
        get_virtual_portfolio(settings)


def test_returns_most_recent_signals_first(settings):
    # Arrange
    (settings.stock_analyzer_path / "signal_log.json").write_text(
        json.dumps({"signals": [
            {"symbol": "AMAT", "action": "매수", "entry_date": "2026-07-08",
             "entry_price": 570.5, "score": 68.7, "rsi": 50.2,
             "return_pct": None},
            {"symbol": "AAPL", "action": "🟢 매수", "entry_date": "2026-07-17",
             "entry_price": 333.74, "score": 61.0, "rsi": 72.0,
             "return_pct": 4.2},
        ]}),
        encoding="utf-8",
    )

    # Act
    result = get_recent_signals(settings, limit=1)

    # Assert
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


def test_returns_empty_list_when_signal_log_absent(settings):
    # Act
    result = get_recent_signals(settings)

    # Assert
    assert result == []


def test_reads_equity_records(settings):
    # Arrange
    (settings.stock_analyzer_path / "equity_log.json").write_text(
        json.dumps({"records": [
            {"date": "2026-07-27", "equity_krw": 10_050_000},
            {"date": "2026-07-28", "equity_krw": 10_120_000},
        ]}),
        encoding="utf-8",
    )

    # Act
    result = get_equity_history(settings, limit=1)

    # Assert
    assert result == [{"date": "2026-07-28", "equity_krw": 10_120_000}]


def test_reads_analyst_scores_newest_first(settings):
    # Arrange
    log = settings.stock_analyzer_path / "data" / "analyst_log" / "2026.jsonl"
    log.write_text(
        json.dumps({"date": "2026-07-22", "regime": "bull",
                    "scores": {"AAPL": {"chart": 50.0, "ict": 60.0}}}) + "\n"
        + json.dumps({"date": "2026-07-23", "regime": "bull",
                      "scores": {"AAPL": {"chart": 73.8, "ict": 100.0}}}) + "\n",
        encoding="utf-8",
    )

    # Act
    result = get_analyst_scores(settings, limit=1)

    # Assert
    assert result[0]["date"] == "2026-07-23"
    assert result[0]["scores"]["AAPL"]["chart"] == 73.8


def test_skips_corrupt_lines_in_analyst_log(settings):
    # Arrange — 한 줄이 망가져도 나머지는 읽혀야 한다
    log = settings.stock_analyzer_path / "data" / "analyst_log" / "2026.jsonl"
    log.write_text(
        "{ 망가진 줄\n"
        + json.dumps({"date": "2026-07-23", "regime": "bull", "scores": {}})
        + "\n",
        encoding="utf-8",
    )

    # Act
    result = get_analyst_scores(settings)

    # Assert
    assert len(result) == 1
    assert result[0]["date"] == "2026-07-23"
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_stock_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.stock_reader'`

- [ ] **Step 3: 최소 구현을 쓴다**

`tools/stock_reader.py`:

```python
"""stock-analyzer 결과를 읽는 유일한 창구. 절대 쓰지 않는다.

stock-analyzer 구조가 바뀌면 이 파일만 고치면 된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assistant.config import Settings


class StockDataError(RuntimeError):
    """주식 데이터를 읽지 못했을 때. 메시지는 사용자에게 그대로 보여준다."""


def _read_json(path: Path) -> Any | None:
    """파일이 없으면 None, 망가졌으면 StockDataError."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise StockDataError(
            f"{path.name} 파일을 읽지 못했습니다: {exc}"
        ) from exc


def get_virtual_portfolio(settings: Settings) -> dict:
    """가상 브로커 보유 현황. 아직 한 번도 안 돌았으면 started=False."""
    data = _read_json(settings.stock_analyzer_path / "virtual_portfolio.json")
    if data is None:
        return {
            "started": False,
            "cash_krw": None,
            "positions": {},
            "pending": [],
            "realized_pnl_krw": 0.0,
            "note": "가상 브로커가 아직 한 번도 실행되지 않았습니다.",
        }
    return {
        "started": True,
        "cash_krw": data.get("cash_krw"),
        "positions": data.get("positions", {}),
        "pending": data.get("pending", []),
        "realized_pnl_krw": data.get("realized_pnl_krw", 0.0),
    }


def get_equity_history(settings: Settings, limit: int = 30) -> list[dict]:
    """가상 브로커 자본 곡선. 최신 것부터 limit개."""
    data = _read_json(settings.stock_analyzer_path / "equity_log.json")
    if data is None:
        return []
    records = data.get("records", [])
    return records[-limit:][::-1]


def get_recent_signals(settings: Settings, limit: int = 10) -> list[dict]:
    """매매 시그널 기록. 최신 것부터 limit개."""
    data = _read_json(settings.stock_analyzer_path / "signal_log.json")
    if data is None:
        return []
    signals = data.get("signals", [])
    return signals[-limit:][::-1]


def get_analyst_scores(settings: Settings, limit: int = 5) -> list[dict]:
    """애널리스트 팀의 종목별 점수 기록. 최신 날짜부터 limit개."""
    log_dir = settings.stock_analyzer_path / "data" / "analyst_log"
    if not log_dir.is_dir():
        return []

    entries: list[dict] = []
    for path in sorted(log_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise StockDataError(
                f"{path.name} 파일을 읽지 못했습니다: {exc}"
            ) from exc
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # 한 줄이 망가져도 나머지는 살린다.
                continue

    entries.sort(key=lambda e: e.get("date", ""))
    return entries[-limit:][::-1]
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_stock_reader.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add tools/stock_reader.py tests/test_stock_reader.py
git commit -m "feat: stock-analyzer 결과 읽기 도구"
```

---

### Task 3: 비서 소유 관심종목·메모와 감사 기록

**Files:**
- Create: `tools/assistant_notes.py`
- Create: `tests/test_assistant_notes.py`

**Interfaces:**
- Consumes: `assistant.config.Settings` (`assistant_data_dir`, `audit_log_path`)
- Produces:
  `tools.assistant_notes.now_kst() -> datetime`,
  `record_audit(settings, action: str, detail: str) -> None`,
  `read_audit_log(settings, limit: int = 20) -> list[str]`,
  `add_watchlist(settings, symbol: str, reason: str = "") -> dict`,
  `remove_watchlist(settings, symbol: str) -> dict`,
  `list_watchlist(settings) -> list[dict]`,
  `add_note(settings, symbol: str, note: str) -> dict`,
  `list_notes(settings, symbol: str | None = None, limit: int = 20) -> list[dict]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_assistant_notes.py`:

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_assistant_notes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.assistant_notes'`

- [ ] **Step 3: 최소 구현을 쓴다**

`tools/assistant_notes.py`:

```python
"""비서가 소유하는 관심종목·메모, 그리고 모든 쓰기의 감사 기록.

stock-analyzer에는 저장된 관심종목 목록이 없다 (매일 점수로 자동 산출).
그래서 비서가 자기 파일을 가진다. stock-analyzer 폴더에는 쓰지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assistant.config import Settings

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def _load_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 비서 소유 파일이 망가지면 빈 목록으로 새로 시작한다.
        return []
    return data if isinstance(data, list) else []


def _save_list(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("종목 코드가 비어 있습니다.")
    return cleaned


def record_audit(settings: Settings, action: str, detail: str) -> None:
    """모든 쓰기 행동을 한 줄씩 남긴다."""
    path = settings.audit_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{now_kst().isoformat(timespec='seconds')} | {action} | {detail}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def read_audit_log(settings: Settings, limit: int = 20) -> list[str]:
    """감사 기록을 최신순으로 limit줄."""
    path = settings.audit_log_path
    if not path.exists():
        return []
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return lines[-limit:][::-1]


def _watchlist_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "watchlist.json"


def _notes_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "notes.json"


def add_watchlist(settings: Settings, symbol: str, reason: str = "") -> dict:
    """관심종목에 추가한다. 이미 있으면 그대로 둔다."""
    sym = _normalize_symbol(symbol)
    path = _watchlist_path(settings)
    items = _load_list(path)

    if any(item.get("symbol") == sym for item in items):
        return {"symbol": sym, "already_present": True}

    entry = {
        "symbol": sym,
        "added_at": now_kst().date().isoformat(),
        "reason": reason.strip(),
    }
    items.append(entry)
    _save_list(path, items)
    record_audit(settings, "watchlist_add", sym)
    return {"symbol": sym, "already_present": False, "entry": entry}


def remove_watchlist(settings: Settings, symbol: str) -> dict:
    """관심종목에서 뺀다."""
    sym = _normalize_symbol(symbol)
    path = _watchlist_path(settings)
    items = _load_list(path)
    remaining = [item for item in items if item.get("symbol") != sym]

    if len(remaining) == len(items):
        return {"symbol": sym, "removed": False}

    _save_list(path, remaining)
    record_audit(settings, "watchlist_remove", sym)
    return {"symbol": sym, "removed": True}


def list_watchlist(settings: Settings) -> list[dict]:
    """관심종목 전체."""
    return _load_list(_watchlist_path(settings))


def add_note(settings: Settings, symbol: str, note: str) -> dict:
    """종목에 대한 내 판단·메모를 남긴다."""
    sym = _normalize_symbol(symbol)
    text = note.strip()
    if not text:
        raise ValueError("메모 내용이 비어 있습니다.")

    path = _notes_path(settings)
    items = _load_list(path)
    entry = {
        "symbol": sym,
        "note": text,
        "created_at": now_kst().isoformat(timespec="seconds"),
    }
    items.append(entry)
    _save_list(path, items)
    record_audit(settings, "note_add", f"{sym}: {text[:60]}")
    return entry


def list_notes(
    settings: Settings, symbol: str | None = None, limit: int = 20
) -> list[dict]:
    """메모를 최신순으로. symbol을 주면 그 종목만."""
    items = _load_list(_notes_path(settings))
    if symbol:
        sym = _normalize_symbol(symbol)
        items = [item for item in items if item.get("symbol") == sym]
    return items[-limit:][::-1]
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_assistant_notes.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add tools/assistant_notes.py tests/test_assistant_notes.py
git commit -m "feat: 비서 소유 관심종목·메모와 감사 기록"
```

---

### Task 4: 가상 매매 — 제안과 승인 분리

**핵심 안전장치.** 비서(모델)는 매매를 **제안만** 할 수 있다. 실행 함수는 모델의 도구 목록에 절대 넣지 않고, 사용자가 `/승인` 명령을 쳤을 때 채널 계층이 직접 부른다.

**Files:**
- Create: `tools/virtual_trade.py`
- Create: `tests/test_virtual_trade.py`

**Interfaces:**
- Consumes: `assistant.config.Settings`, `tools.assistant_notes.record_audit`, `tools.assistant_notes.now_kst`
- Produces:
  `tools.virtual_trade.TradeError`,
  `request_trade(settings, side: str, symbol: str, amount_krw: float | None = None, qty: int | None = None) -> dict` ← 모델에게 주는 유일한 매매 도구,
  `list_pending_requests(settings) -> list[dict]`,
  `approve_request(settings, request_id: str, executor=None) -> dict` ← 채널 전용,
  `reject_request(settings, request_id: str) -> dict` ← 채널 전용

`executor`는 테스트에서 갈아끼우기 위한 인자다. 기본값 `None`이면 stock-analyzer의
`modules/virtual_broker`를 불러 `place_notional_buy` / `place_market_sell`을 호출한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_virtual_trade.py`:

```python
import pytest

from assistant.config import Settings
from tools.assistant_notes import read_audit_log
from tools.virtual_trade import (
    TradeError,
    approve_request,
    list_pending_requests,
    reject_request,
    request_trade,
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


class FakeExecutor:
    """진짜 가상 브로커 대신 호출을 기록만 한다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def buy(self, symbol: str, amount_krw: float) -> dict:
        self.calls.append(("buy", symbol, amount_krw))
        return {"ok": True, "id": "virtual-buy-1"}

    def sell(self, symbol: str, qty: int) -> dict:
        self.calls.append(("sell", symbol, qty))
        return {"ok": True, "id": "virtual-sell-1"}


def test_request_does_not_execute_anything(settings):
    # Arrange — request_trade는 executor를 아예 받지 않는다. 그러니
    # "브로커가 안 불렸다"는 브로커가 남기는 흔적이 없다는 것으로 증명한다.

    # Act — 제안만 한다
    result = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Assert — 가상 브로커 상태 파일이 생기지 않았다 (주문이 안 나갔다)
    assert not (settings.stock_analyzer_path / "virtual_portfolio.json").exists()
    assert result["status"] == "confirmation_required"
    assert result["request_id"]
    assert len(list_pending_requests(settings)) == 1


def test_request_trade_signature_has_no_executor():
    # Arrange — 실행 경로가 제안 함수에 존재하지 않아야 한다
    import inspect

    # Act
    params = inspect.signature(request_trade).parameters

    # Assert
    assert "executor" not in params


def test_approval_executes_buy_through_broker(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "aapl", amount_krw=1_000_000)

    # Act
    result = approve_request(settings, request["request_id"], executor=executor)

    # Assert
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert result["executed"] is True
    assert list_pending_requests(settings) == []


def test_approval_executes_sell_through_broker(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "sell", "AAPL", qty=3)

    # Act
    approve_request(settings, request["request_id"], executor=executor)

    # Assert
    assert executor.calls == [("sell", "AAPL", 3)]


def test_unknown_request_id_is_rejected(settings):
    # Arrange
    executor = FakeExecutor()

    # Act / Assert
    with pytest.raises(TradeError, match="찾지 못했습니다"):
        approve_request(settings, "없는-아이디", executor=executor)
    assert executor.calls == []


def test_request_cannot_be_approved_twice(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)
    approve_request(settings, request["request_id"], executor=executor)

    # Act / Assert
    with pytest.raises(TradeError, match="찾지 못했습니다"):
        approve_request(settings, request["request_id"], executor=executor)
    assert len(executor.calls) == 1


def test_rejected_request_never_executes(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    result = reject_request(settings, request["request_id"])

    # Assert
    assert result["rejected"] is True
    assert list_pending_requests(settings) == []
    with pytest.raises(TradeError):
        approve_request(settings, request["request_id"], executor=executor)
    assert executor.calls == []


def test_buy_without_amount_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="금액"):
        request_trade(settings, "buy", "AAPL")


def test_sell_without_qty_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="수량"):
        request_trade(settings, "sell", "AAPL")


def test_non_positive_amount_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="0보다"):
        request_trade(settings, "buy", "AAPL", amount_krw=0)


def test_unknown_side_is_rejected(settings):
    # Act / Assert
    with pytest.raises(TradeError, match="buy"):
        request_trade(settings, "short", "AAPL", amount_krw=1_000_000)


def test_request_and_approval_are_both_audited(settings):
    # Arrange
    executor = FakeExecutor()
    request = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    approve_request(settings, request["request_id"], executor=executor)

    # Assert
    actions = [line.split("|")[1].strip() for line in read_audit_log(settings)]
    assert actions == ["trade_approve", "trade_request"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_virtual_trade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.virtual_trade'`

- [ ] **Step 3: 최소 구현을 쓴다**

`tools/virtual_trade.py`:

```python
"""가상 브로커 매매 — 제안과 실행을 분리한다.

비서(모델)에게는 request_trade만 준다. 실행 함수(approve_request)는
모델의 도구 목록에 넣지 않고, 사용자가 승인 명령을 쳤을 때 채널이 직접 부른다.
실제 돈이 오가는 주문 도구는 이 파일에 없고, 앞으로도 만들지 않는다.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Protocol

from assistant.config import Settings
from tools.assistant_notes import now_kst, record_audit

VALID_SIDES = ("buy", "sell")


class TradeError(RuntimeError):
    """매매 요청·승인이 거부됐을 때. 메시지는 사용자에게 그대로 보여준다."""


class TradeExecutor(Protocol):
    def buy(self, symbol: str, amount_krw: float) -> dict: ...
    def sell(self, symbol: str, qty: int) -> dict: ...


class VirtualBrokerExecutor:
    """stock-analyzer의 가상 브로커를 부른다. 상태 파일에 직접 손대지 않는다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _module(self):
        path = str(self._settings.stock_analyzer_path)
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            from modules import virtual_broker  # noqa: PLC0415
        except ImportError as exc:
            raise TradeError(
                "stock-analyzer의 가상 브로커를 불러오지 못했습니다: "
                f"{exc}. STOCK_ANALYZER_PATH 설정을 확인하세요."
            ) from exc
        return virtual_broker

    def buy(self, symbol: str, amount_krw: float) -> dict:
        return self._module().place_notional_buy(symbol, amount_krw)

    def sell(self, symbol: str, qty: int) -> dict:
        return self._module().place_market_sell(symbol, qty)


def _pending_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "pending_trades.json"


def _load_pending(settings: Settings) -> list[dict]:
    path = _pending_path(settings)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_pending(settings: Settings, items: list[dict]) -> None:
    path = _pending_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _describe(request: dict) -> str:
    if request["side"] == "buy":
        return f"{request['symbol']} {request['amount_krw']:,.0f}원어치 매수"
    return f"{request['symbol']} {request['qty']}주 매도"


def request_trade(
    settings: Settings,
    side: str,
    symbol: str,
    amount_krw: float | None = None,
    qty: int | None = None,
) -> dict:
    """매매를 제안한다. 실행하지 않는다 — 사용자 승인이 있어야 나간다."""
    side = side.strip().lower()
    if side not in VALID_SIDES:
        raise TradeError(f"side는 buy 또는 sell이어야 합니다: {side!r}")

    sym = symbol.strip().upper()
    if not sym:
        raise TradeError("종목 코드가 비어 있습니다.")

    if side == "buy":
        if amount_krw is None:
            raise TradeError("매수하려면 금액(amount_krw)이 필요합니다.")
        if float(amount_krw) <= 0:
            raise TradeError("금액은 0보다 커야 합니다.")
    else:
        if qty is None:
            raise TradeError("매도하려면 수량(qty)이 필요합니다.")
        if int(qty) <= 0:
            raise TradeError("수량은 0보다 커야 합니다.")

    request = {
        "request_id": uuid.uuid4().hex[:8],
        "side": side,
        "symbol": sym,
        "amount_krw": float(amount_krw) if amount_krw is not None else None,
        "qty": int(qty) if qty is not None else None,
        "requested_at": now_kst().isoformat(timespec="seconds"),
    }

    items = _load_pending(settings)
    items.append(request)
    _save_pending(settings, items)
    record_audit(
        settings, "trade_request", f"{request['request_id']} {_describe(request)}"
    )

    return {
        "status": "confirmation_required",
        "request_id": request["request_id"],
        "summary": _describe(request),
        "message": (
            f"{_describe(request)}를 예약하려면 승인이 필요합니다. "
            f"'/승인 {request['request_id']}'라고 답해주세요. "
            "제가 직접 실행할 수는 없습니다."
        ),
    }


def list_pending_requests(settings: Settings) -> list[dict]:
    """승인 대기 중인 매매 제안."""
    return _load_pending(settings)


def _pop_request(settings: Settings, request_id: str) -> dict:
    items = _load_pending(settings)
    for index, item in enumerate(items):
        if item["request_id"] == request_id:
            items.pop(index)
            _save_pending(settings, items)
            return item
    raise TradeError(
        f"승인 대기 중인 요청 {request_id}를 찾지 못했습니다. "
        "이미 처리됐거나 잘못된 번호입니다."
    )


def approve_request(
    settings: Settings, request_id: str, executor: TradeExecutor | None = None
) -> dict:
    """사용자 승인 후 실제로 가상 브로커에 주문을 넣는다.

    채널 계층 전용. 모델의 도구 목록에 넣지 말 것.
    """
    request = _pop_request(settings, request_id)
    broker = executor or VirtualBrokerExecutor(settings)

    if request["side"] == "buy":
        result = broker.buy(request["symbol"], request["amount_krw"])
    else:
        result = broker.sell(request["symbol"], request["qty"])

    record_audit(
        settings, "trade_approve", f"{request_id} {_describe(request)}"
    )
    return {
        "executed": True,
        "request_id": request_id,
        "summary": _describe(request),
        "broker_result": result,
    }


def reject_request(settings: Settings, request_id: str) -> dict:
    """제안을 버린다. 채널 계층 전용."""
    request = _pop_request(settings, request_id)
    record_audit(settings, "trade_reject", f"{request_id} {_describe(request)}")
    return {"rejected": True, "request_id": request_id,
            "summary": _describe(request)}
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_virtual_trade.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: 커밋**

```bash
git add tools/virtual_trade.py tests/test_virtual_trade.py
git commit -m "feat: 가상 매매 제안·승인 분리 (모델에 실행 도구 미제공)"
```

---

### Task 5: 대화 기억

**Files:**
- Create: `assistant/memory.py`
- Create: `tests/test_memory.py`

**Interfaces:**
- Consumes: `pathlib.Path`
- Produces:
  `assistant.memory.init_db(db_path: Path) -> None`,
  `append_message(db_path: Path, channel: str, role: str, content: str) -> None`,
  `load_history(db_path: Path, limit: int) -> list[dict]` → `[{"role": ..., "content": ...}]` 오래된 것부터,
  `clear_history(db_path: Path) -> int`

설계 문서는 API compaction을 언급했지만, `claude-opus-5`의 컨텍스트는 100만 토큰이고
1인용 대화는 근처에도 못 간다. 최근 `history_limit`개만 싣는 단순한 방식으로 충분하므로
베타 기능을 하나 덜 쌓는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_memory.py`:

```python
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


def test_limit_keeps_the_most_recent_messages(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)
    for i in range(5):
        append_message(db, "web", "user", f"질문 {i}")

    # Act
    history = load_history(db, limit=2)

    # Assert
    assert [m["content"] for m in history] == ["질문 3", "질문 4"]


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


def test_init_db_is_safe_to_call_twice(tmp_path):
    # Arrange
    db = tmp_path / "conversations.db"
    init_db(db)
    append_message(db, "web", "user", "남아 있어야 한다")

    # Act
    init_db(db)

    # Assert
    assert len(load_history(db, limit=10)) == 1


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
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'assistant.memory'`

- [ ] **Step 3: 최소 구현을 쓴다**

`assistant/memory.py`:

```python
"""대화 기록. 텔레그램과 웹이 같은 기록을 공유한다."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def init_db(db_path: Path) -> None:
    """테이블을 만든다. 이미 있으면 아무것도 하지 않는다."""
    with closing(_connect(db_path)) as conn:
        conn.execute(_SCHEMA)
        conn.commit()


def append_message(
    db_path: Path, channel: str, role: str, content: str
) -> None:
    """대화 한 줄을 남긴다."""
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO messages (channel, role, content, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                channel,
                role,
                content,
                datetime.now(KST).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()


def load_history(db_path: Path, limit: int) -> list[dict]:
    """최근 limit개를 오래된 것부터 반환한다 (API에 그대로 실을 수 있는 형태)."""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def clear_history(db_path: Path) -> int:
    """전체 삭제. 지운 개수를 반환한다."""
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM messages")
        conn.commit()
        return cursor.rowcount
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_memory.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add assistant/memory.py tests/test_memory.py
git commit -m "feat: 대화 기록 저장소 (텔레그램·웹 공유)"
```

---

### Task 6: 비서 본체 (Claude API 도구 루프)

**Files:**
- Create: `assistant/brain.py`
- Create: `tests/test_brain.py`

**Interfaces:**
- Consumes: `assistant.config.Settings`, `assistant.memory`, `tools.stock_reader`, `tools.assistant_notes`, `tools.virtual_trade.request_trade`
- Produces:
  `assistant.brain.Brain(settings, client=None)`,
  `Brain.ask(question: str, channel: str) -> str`,
  `Brain.tool_names() -> list[str]`,
  `assistant.brain.SYSTEM_PROMPT: str`

주의: `Brain.tool_names()`에 `approve_request` / `reject_request`가 **절대** 들어가면 안 된다.
테스트가 이걸 지킨다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_brain.py`:

```python
import pytest

from assistant.brain import Brain
from assistant.config import Settings
from assistant.memory import init_db, load_history


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


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [FakeBlock(text)]


class FakeRunner:
    def __init__(self, text: str) -> None:
        self._text = text

    def __iter__(self):
        yield FakeMessage(self._text)


class FakeClient:
    """anthropic 클라이언트를 대신한다. 호출 인자를 기록한다."""

    def __init__(self, reply: str = "안녕하세요") -> None:
        self.reply = reply
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def tool_runner(self, **kwargs):
                outer.calls.append(kwargs)
                return FakeRunner(outer.reply)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()


def test_answer_is_returned_and_stored(settings):
    # Arrange
    init_db(settings.db_path)
    brain = Brain(settings, client=FakeClient(reply="가상 자본은 1,012만원입니다"))

    # Act
    answer = brain.ask("가상 브로커 얼마야?", channel="telegram")

    # Assert
    assert answer == "가상 자본은 1,012만원입니다"
    history = load_history(settings.db_path, limit=10)
    assert history == [
        {"role": "user", "content": "가상 브로커 얼마야?"},
        {"role": "assistant", "content": "가상 자본은 1,012만원입니다"},
    ]


def test_previous_turns_are_sent_to_the_model(settings):
    # Arrange
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)
    brain.ask("첫 질문", channel="web")

    # Act
    brain.ask("두 번째 질문", channel="web")

    # Assert — 두 번째 호출에는 앞선 대화가 실려 있다
    messages = client.calls[1]["messages"]
    assert [m["content"] for m in messages[:2]] == ["첫 질문", "안녕하세요"]
    assert messages[-1]["content"] == "두 번째 질문"


def test_model_and_effort_come_from_settings(settings):
    # Arrange
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["effort"] == "medium"
    assert call["thinking"] == {"type": "adaptive"}


def test_forbidden_sampling_parameters_are_never_sent(settings):
    # Arrange — claude-opus-5는 이 값들을 받으면 400을 낸다
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    call = client.calls[0]
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert forbidden not in call


def test_model_is_never_given_a_trade_execution_tool(settings):
    # Arrange
    brain = Brain(settings, client=FakeClient())

    # Act
    names = brain.tool_names()

    # Assert — 제안은 있고 실행은 없다
    assert "request_trade" in names
    assert "approve_request" not in names
    assert "reject_request" not in names
    assert not any("approve" in n or "execute" in n or "order" in n
                   for n in names)


def test_system_prompt_is_cached(settings):
    # Arrange
    init_db(settings.db_path)
    client = FakeClient()
    brain = Brain(settings, client=client)

    # Act
    brain.ask("질문", channel="web")

    # Assert
    system = client.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_empty_reply_gets_a_readable_fallback(settings):
    # Arrange
    init_db(settings.db_path)
    brain = Brain(settings, client=FakeClient(reply=""))

    # Act
    answer = brain.ask("질문", channel="web")

    # Assert
    assert "답변을 만들지 못했습니다" in answer
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_brain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'assistant.brain'`

- [ ] **Step 3: 최소 구현을 쓴다**

`assistant/brain.py`:

```python
"""비서 본체. 질문을 받아 도구를 고르고 답을 만든다."""

from __future__ import annotations

import anthropic
from anthropic import beta_tool

from assistant import memory
from assistant.config import Settings
from tools import assistant_notes, stock_reader, virtual_trade

MAX_TOKENS = 8000

SYSTEM_PROMPT = """당신은 사장님의 개인 업무 비서입니다. 한국어로 답합니다.

원칙:
- 결론을 먼저 말하고, 근거는 뒤에 붙입니다. 짧게 씁니다.
- 도구로 확인할 수 있는 것은 반드시 도구로 확인합니다. 추측해서 답하지 않습니다.
- 도구가 실패하면 실패했다고 그대로 말합니다. 지어내지 않습니다.
- 모르면 모른다고 한 줄로 답합니다.

주식에 대해:
- 분석 결과(가상 브로커 성과, 시그널, 애널리스트 점수)는 읽기만 할 수 있습니다.
- 관심종목과 메모는 사장님을 대신해 기록할 수 있습니다.
- 매매는 제안만 할 수 있습니다. 당신에게는 실행 권한이 없습니다.
  request_trade로 제안하면 사장님이 직접 승인해야 주문이 나갑니다.
  "승인했다고 치고 실행하겠다" 같은 말은 하지 마십시오. 불가능합니다.
- 실제 돈으로 하는 주문은 이 시스템에 존재하지 않습니다.
"""


def _text_of(message) -> str:
    return "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )


class Brain:
    """Claude API 도구 호출 루프를 감싼다."""

    def __init__(self, settings: Settings, client=None) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key
        )
        self._tools = self._build_tools()

    def tool_names(self) -> list[str]:
        """모델에게 노출되는 도구 이름. 매매 실행 도구는 여기 없어야 한다.

        @beta_tool이 함수를 감싸는 방식에 따라 이름이 어디 붙는지 다를 수 있어
        둘 다 확인한다. 이름을 못 찾으면 빈 문자열이 아니라 예외를 내야
        안전장치 테스트가 헛돌지 않는다.
        """
        names: list[str] = []
        for tool in self._tools:
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if not name:
                raise RuntimeError(
                    f"도구 이름을 확인할 수 없습니다: {tool!r}. "
                    "이름을 못 읽으면 '실행 도구가 없다'는 검증이 무의미해집니다."
                )
            names.append(name)
        return names

    def _build_tools(self) -> list:
        settings = self._settings

        @beta_tool
        def get_virtual_portfolio() -> str:
            """가상 브로커의 현재 보유 종목과 현금, 실현 손익을 조회한다."""
            return str(stock_reader.get_virtual_portfolio(settings))

        @beta_tool
        def get_equity_history(limit: int = 30) -> str:
            """가상 브로커 자본 곡선을 최신순으로 조회한다.

            Args:
                limit: 가져올 기록 개수.
            """
            return str(stock_reader.get_equity_history(settings, limit))

        @beta_tool
        def get_recent_signals(limit: int = 10) -> str:
            """최근 매매 시그널을 최신순으로 조회한다.

            Args:
                limit: 가져올 시그널 개수.
            """
            return str(stock_reader.get_recent_signals(settings, limit))

        @beta_tool
        def get_analyst_scores(limit: int = 5) -> str:
            """애널리스트 팀의 날짜별 종목 점수를 최신순으로 조회한다.

            Args:
                limit: 가져올 날짜 개수.
            """
            return str(stock_reader.get_analyst_scores(settings, limit))

        @beta_tool
        def list_watchlist() -> str:
            """사장님의 관심종목 목록을 조회한다."""
            return str(assistant_notes.list_watchlist(settings))

        @beta_tool
        def add_watchlist(symbol: str, reason: str = "") -> str:
            """관심종목에 종목을 추가한다.

            Args:
                symbol: 종목 코드 (예: AAPL).
                reason: 추가하는 이유. 비워도 된다.
            """
            return str(assistant_notes.add_watchlist(settings, symbol, reason))

        @beta_tool
        def remove_watchlist(symbol: str) -> str:
            """관심종목에서 종목을 뺀다.

            Args:
                symbol: 종목 코드 (예: AAPL).
            """
            return str(assistant_notes.remove_watchlist(settings, symbol))

        @beta_tool
        def add_note(symbol: str, note: str) -> str:
            """종목에 대한 사장님의 판단이나 메모를 기록한다.

            Args:
                symbol: 종목 코드 (예: AAPL).
                note: 남길 메모 내용.
            """
            return str(assistant_notes.add_note(settings, symbol, note))

        @beta_tool
        def list_notes(symbol: str = "", limit: int = 20) -> str:
            """기록해 둔 메모를 최신순으로 조회한다.

            Args:
                symbol: 특정 종목만 보려면 종목 코드. 비우면 전체.
                limit: 가져올 메모 개수.
            """
            return str(
                assistant_notes.list_notes(settings, symbol or None, limit)
            )

        @beta_tool
        def read_audit_log(limit: int = 20) -> str:
            """언제 무엇을 바꿨는지 기록을 최신순으로 조회한다.

            Args:
                limit: 가져올 줄 수.
            """
            return str(assistant_notes.read_audit_log(settings, limit))

        @beta_tool
        def request_trade(
            side: str, symbol: str, amount_krw: float = 0, qty: int = 0
        ) -> str:
            """가상 브로커 매매를 제안한다. 실행되지 않는다 — 사장님 승인이 필요하다.

            Args:
                side: buy 또는 sell.
                symbol: 종목 코드 (예: AAPL).
                amount_krw: 매수 금액(원). 매수일 때만.
                qty: 매도 수량(주). 매도일 때만.
            """
            try:
                return str(
                    virtual_trade.request_trade(
                        settings,
                        side,
                        symbol,
                        amount_krw=amount_krw or None,
                        qty=qty or None,
                    )
                )
            except virtual_trade.TradeError as exc:
                return f"제안하지 못했습니다: {exc}"

        @beta_tool
        def list_pending_requests() -> str:
            """승인을 기다리는 매매 제안 목록을 조회한다."""
            return str(virtual_trade.list_pending_requests(settings))

        return [
            get_virtual_portfolio,
            get_equity_history,
            get_recent_signals,
            get_analyst_scores,
            list_watchlist,
            add_watchlist,
            remove_watchlist,
            add_note,
            list_notes,
            read_audit_log,
            request_trade,
            list_pending_requests,
        ]

    def ask(self, question: str, channel: str) -> str:
        """질문에 답한다. 질문과 답을 대화 기록에 남긴다."""
        settings = self._settings
        memory.init_db(settings.db_path)

        history = memory.load_history(settings.db_path, settings.history_limit)
        messages = history + [{"role": "user", "content": question}]

        runner = self._client.beta.messages.tool_runner(
            model=settings.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.effort},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=self._tools,
            messages=messages,
        )

        answer = ""
        for message in runner:
            text = _text_of(message)
            if text.strip():
                answer = text

        answer = answer.strip() or "답변을 만들지 못했습니다. 다시 물어봐 주세요."

        memory.append_message(settings.db_path, channel, "user", question)
        memory.append_message(settings.db_path, channel, "assistant", answer)
        return answer
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_brain.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add assistant/brain.py tests/test_brain.py
git commit -m "feat: 비서 본체 — Claude API 도구 호출 루프"
```

---

### Task 7: 텔레그램 창구

**Files:**
- Create: `channels/telegram_bot.py`
- Create: `tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `assistant.config.Settings`, `assistant.brain.Brain`, `tools.virtual_trade`
- Produces:
  `channels.telegram_bot.TelegramChannel(settings, brain, trade_executor=None)`,
  `TelegramChannel.handle_text(chat_id: int, text: str) -> str | None` (허용 안 된 chat_id면 `None`),
  `TelegramChannel.build_application()` → `python-telegram-bot`의 `Application`

`handle_text`는 순수 함수처럼 만들어 텔레그램 라이브러리 없이 테스트한다.
`build_application`은 그 함수를 텔레그램에 연결만 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_telegram_bot.py`:

```python
import pytest

from assistant.config import Settings
from channels.telegram_bot import TelegramChannel
from tools.virtual_trade import list_pending_requests, request_trade


@pytest.fixture
def settings(tmp_path) -> Settings:
    stock_dir = tmp_path / "stock-analyzer"
    stock_dir.mkdir()
    data_dir = tmp_path / "assistant"
    data_dir.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({111}),
        stock_analyzer_path=stock_dir,
        assistant_data_dir=data_dir,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
    )


class FakeBrain:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def ask(self, question: str, channel: str) -> str:
        self.asked.append((question, channel))
        return f"답변: {question}"


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def buy(self, symbol: str, amount_krw: float) -> dict:
        self.calls.append(("buy", symbol, amount_krw))
        return {"ok": True}

    def sell(self, symbol: str, qty: int) -> dict:
        self.calls.append(("sell", symbol, qty))
        return {"ok": True}


def test_message_from_stranger_is_ignored(settings):
    # Arrange
    brain = FakeBrain()
    channel = TelegramChannel(settings, brain)

    # Act
    reply = channel.handle_text(chat_id=999, text="안녕")

    # Assert — 비서는 부르지도 않는다
    assert reply is None
    assert brain.asked == []


def test_message_from_owner_reaches_the_brain(settings):
    # Arrange
    brain = FakeBrain()
    channel = TelegramChannel(settings, brain)

    # Act
    reply = channel.handle_text(chat_id=111, text="가상 브로커 얼마야?")

    # Assert
    assert reply == "답변: 가상 브로커 얼마야?"
    assert brain.asked == [("가상 브로커 얼마야?", "telegram")]


def test_approve_command_executes_pending_trade(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(
        chat_id=111, text=f"/승인 {proposal['request_id']}"
    )

    # Assert
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert "예약했습니다" in reply
    assert list_pending_requests(settings) == []


def test_approve_command_from_stranger_does_nothing(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(
        chat_id=999, text=f"/승인 {proposal['request_id']}"
    )

    # Assert
    assert reply is None
    assert executor.calls == []
    assert len(list_pending_requests(settings)) == 1


def test_approve_with_unknown_id_explains_the_problem(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)

    # Act
    reply = channel.handle_text(chat_id=111, text="/승인 없는아이디")

    # Assert
    assert "찾지 못했습니다" in reply
    assert executor.calls == []


def test_approve_without_id_shows_pending_list(settings):
    # Arrange
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=FakeExecutor())
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(chat_id=111, text="/승인")

    # Assert
    assert "AAPL" in reply


def test_reject_command_discards_the_proposal(settings):
    # Arrange
    executor = FakeExecutor()
    channel = TelegramChannel(settings, FakeBrain(), trade_executor=executor)
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    reply = channel.handle_text(
        chat_id=111, text=f"/거절 {proposal['request_id']}"
    )

    # Assert
    assert "취소했습니다" in reply
    assert executor.calls == []
    assert list_pending_requests(settings) == []


def test_brain_failure_is_reported_not_swallowed(settings):
    # Arrange
    class BrokenBrain:
        def ask(self, question: str, channel: str) -> str:
            raise RuntimeError("API 한도 초과")

    channel = TelegramChannel(settings, BrokenBrain())

    # Act
    reply = channel.handle_text(chat_id=111, text="질문")

    # Assert
    assert "API 한도 초과" in reply
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'channels.telegram_bot'`

- [ ] **Step 3: 최소 구현을 쓴다**

`channels/telegram_bot.py`:

```python
"""텔레그램 창구. 허용된 chat_id에만 답한다.

매매 승인은 여기서 직접 실행한다 — 비서(모델)는 실행 도구를 갖고 있지 않다.
"""

from __future__ import annotations

from assistant.config import Settings
from tools import virtual_trade

APPROVE_COMMANDS = ("/승인", "/approve")
REJECT_COMMANDS = ("/거절", "/reject")


class TelegramChannel:
    def __init__(self, settings: Settings, brain, trade_executor=None) -> None:
        self._settings = settings
        self._brain = brain
        self._executor = trade_executor

    def _is_allowed(self, chat_id: int) -> bool:
        return chat_id in self._settings.telegram_allowed_chat_ids

    def _pending_summary(self) -> str:
        pending = virtual_trade.list_pending_requests(self._settings)
        if not pending:
            return "승인을 기다리는 매매 제안이 없습니다."
        lines = ["승인 대기 중인 제안:"]
        for item in pending:
            if item["side"] == "buy":
                what = f"{item['symbol']} {item['amount_krw']:,.0f}원어치 매수"
            else:
                what = f"{item['symbol']} {item['qty']}주 매도"
            lines.append(f"  {item['request_id']} — {what}")
        lines.append("승인하려면 '/승인 <번호>', 취소하려면 '/거절 <번호>'")
        return "\n".join(lines)

    def _approve(self, argument: str) -> str:
        if not argument:
            return self._pending_summary()
        try:
            result = virtual_trade.approve_request(
                self._settings, argument, executor=self._executor
            )
        except virtual_trade.TradeError as exc:
            return str(exc)
        return f"{result['summary']}를 예약했습니다. 다음 거래일 시가에 체결됩니다."

    def _reject(self, argument: str) -> str:
        if not argument:
            return self._pending_summary()
        try:
            result = virtual_trade.reject_request(self._settings, argument)
        except virtual_trade.TradeError as exc:
            return str(exc)
        return f"{result['summary']} 제안을 취소했습니다."

    def handle_text(self, chat_id: int, text: str) -> str | None:
        """메시지 하나를 처리한다. 허용되지 않은 사람이면 None."""
        if not self._is_allowed(chat_id):
            return None

        stripped = text.strip()
        head, _, argument = stripped.partition(" ")
        argument = argument.strip()

        if head in APPROVE_COMMANDS:
            return self._approve(argument)
        if head in REJECT_COMMANDS:
            return self._reject(argument)

        try:
            return self._brain.ask(stripped, channel="telegram")
        except Exception as exc:  # noqa: BLE001 — 사용자에게 그대로 알린다
            return f"처리 중 문제가 생겼습니다: {exc}"

    def build_application(self):
        """텔레그램 폴링 애플리케이션을 만든다."""
        import asyncio

        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            ContextTypes,
            MessageHandler,
            filters,
        )

        async def on_message(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            if update.message is None or update.message.text is None:
                return
            chat_id = update.message.chat_id
            reply = await asyncio.to_thread(
                self.handle_text, chat_id, update.message.text
            )
            if reply is not None:
                await update.message.reply_text(reply)

        application = ApplicationBuilder().token(
            self._settings.telegram_bot_token
        ).build()
        application.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), on_message)
        )
        application.add_handler(MessageHandler(filters.COMMAND, on_message))
        return application
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add channels/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: 텔레그램 창구 (chat_id 화이트리스트, 매매 승인 명령)"
```

---

### Task 8: 웹 창구와 실행 진입점

**Files:**
- Create: `channels/web.py`
- Create: `server.py`
- Create: `tests/test_web.py`

**Interfaces:**
- Consumes: `assistant.config.Settings`, `assistant.brain.Brain`, `tools.virtual_trade`
- Produces:
  `channels.web.create_app(settings, brain, trade_executor=None) -> FastAPI`,
  엔드포인트 `GET /` (채팅 HTML), `POST /chat` `{"message": str}` → `{"reply": str}`,
  `GET /pending` → `{"pending": [...]}`, `POST /approve` `{"request_id": str}` → `{"reply": str}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_web.py`:

```python
import pytest
from fastapi.testclient import TestClient

from assistant.config import Settings
from channels.web import create_app
from tools.virtual_trade import list_pending_requests, request_trade


@pytest.fixture
def settings(tmp_path) -> Settings:
    stock_dir = tmp_path / "stock-analyzer"
    stock_dir.mkdir()
    data_dir = tmp_path / "assistant"
    data_dir.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({111}),
        stock_analyzer_path=stock_dir,
        assistant_data_dir=data_dir,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
    )


class FakeBrain:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def ask(self, question: str, channel: str) -> str:
        self.asked.append((question, channel))
        return f"답변: {question}"


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def buy(self, symbol: str, amount_krw: float) -> dict:
        self.calls.append(("buy", symbol, amount_krw))
        return {"ok": True}

    def sell(self, symbol: str, qty: int) -> dict:
        self.calls.append(("sell", symbol, qty))
        return {"ok": True}


def test_chat_page_is_served(settings):
    # Arrange
    client = TestClient(create_app(settings, FakeBrain()))

    # Act
    response = client.get("/")

    # Assert
    assert response.status_code == 200
    assert "<textarea" in response.text


def test_chat_endpoint_reaches_the_brain(settings):
    # Arrange
    brain = FakeBrain()
    client = TestClient(create_app(settings, brain))

    # Act
    response = client.post("/chat", json={"message": "관심종목 보여줘"})

    # Assert
    assert response.status_code == 200
    assert response.json()["reply"] == "답변: 관심종목 보여줘"
    assert brain.asked == [("관심종목 보여줘", "web")]


def test_empty_message_is_rejected(settings):
    # Arrange
    client = TestClient(create_app(settings, FakeBrain()))

    # Act
    response = client.post("/chat", json={"message": "   "})

    # Assert
    assert response.status_code == 400


def test_brain_failure_returns_readable_error(settings):
    # Arrange
    class BrokenBrain:
        def ask(self, question: str, channel: str) -> str:
            raise RuntimeError("API 한도 초과")

    client = TestClient(create_app(settings, BrokenBrain()))

    # Act
    response = client.post("/chat", json={"message": "질문"})

    # Assert
    assert response.status_code == 200
    assert "API 한도 초과" in response.json()["reply"]


def test_pending_endpoint_lists_proposals(settings):
    # Arrange
    client = TestClient(create_app(settings, FakeBrain()))
    request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.get("/pending")

    # Assert
    assert response.json()["pending"][0]["symbol"] == "AAPL"


def test_approve_endpoint_executes_the_trade(settings):
    # Arrange
    executor = FakeExecutor()
    client = TestClient(create_app(settings, FakeBrain(), trade_executor=executor))
    proposal = request_trade(settings, "buy", "AAPL", amount_krw=1_000_000)

    # Act
    response = client.post(
        "/approve", json={"request_id": proposal["request_id"]}
    )

    # Assert
    assert executor.calls == [("buy", "AAPL", 1_000_000.0)]
    assert "예약했습니다" in response.json()["reply"]
    assert list_pending_requests(settings) == []


def test_approve_with_unknown_id_does_not_execute(settings):
    # Arrange
    executor = FakeExecutor()
    client = TestClient(create_app(settings, FakeBrain(), trade_executor=executor))

    # Act
    response = client.post("/approve", json={"request_id": "없는아이디"})

    # Assert
    assert executor.calls == []
    assert "찾지 못했습니다" in response.json()["reply"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `pytest tests/test_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'channels.web'`

- [ ] **Step 3: 최소 구현을 쓴다**

`channels/web.py`:

```python
"""PC 브라우저 채팅 창구. localhost에만 바인딩한다."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from assistant.config import Settings
from tools import virtual_trade

_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>업무 비서</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}
 #log{border:1px solid #ddd;border-radius:8px;padding:1rem;height:60vh;overflow:auto;
      white-space:pre-wrap}
 .me{color:#0b5}.bot{color:#222}
 form{display:flex;gap:.5rem;margin-top:1rem}
 textarea{flex:1;padding:.6rem;font:inherit;height:3.2rem}
 button{padding:.6rem 1.2rem;font:inherit}
</style></head><body>
<h1>업무 비서</h1>
<div id="log"></div>
<form id="f"><textarea id="m" placeholder="무엇이든 물어보세요"></textarea>
<button>보내기</button></form>
<script>
const log=document.getElementById('log');
function add(cls,text){const d=document.createElement('div');
 d.className=cls;d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;}
document.getElementById('f').onsubmit=async e=>{
 e.preventDefault();const box=document.getElementById('m');
 const text=box.value.trim();if(!text)return;
 add('me','나: '+text);box.value='';add('bot','비서: …');
 const r=await fetch('/chat',{method:'POST',
   headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
 const data=await r.json();
 log.lastChild.textContent='비서: '+(data.reply||data.detail||'응답 없음');
};
</script></body></html>
"""


class ChatRequest(BaseModel):
    message: str


class ApproveRequest(BaseModel):
    request_id: str


def create_app(settings: Settings, brain, trade_executor=None) -> FastAPI:
    app = FastAPI(title="업무 비서")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _PAGE

    @app.post("/chat")
    async def chat(payload: ChatRequest) -> dict:
        question = payload.message.strip()
        if not question:
            raise HTTPException(status_code=400, detail="메시지가 비어 있습니다.")
        try:
            reply = await asyncio.to_thread(brain.ask, question, "web")
        except Exception as exc:  # noqa: BLE001 — 사용자에게 그대로 알린다
            return {"reply": f"처리 중 문제가 생겼습니다: {exc}"}
        return {"reply": reply}

    @app.get("/pending")
    async def pending() -> dict:
        return {"pending": virtual_trade.list_pending_requests(settings)}

    @app.post("/approve")
    async def approve(payload: ApproveRequest) -> dict:
        try:
            result = virtual_trade.approve_request(
                settings, payload.request_id, executor=trade_executor
            )
        except virtual_trade.TradeError as exc:
            return {"reply": str(exc)}
        return {
            "reply": f"{result['summary']}를 예약했습니다. "
                     "다음 거래일 시가에 체결됩니다."
        }

    return app
```

`server.py`:

```python
"""업무 비서 실행 진입점. 텔레그램 폴링과 웹 서버를 함께 띄운다.

    python server.py
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from assistant.brain import Brain
from assistant.config import ConfigError, load_settings
from assistant.memory import init_db
from channels.telegram_bot import TelegramChannel
from channels.web import create_app

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("assistant")


async def _run_web(settings, brain) -> None:
    config = uvicorn.Config(
        create_app(settings, brain),
        host=settings.web_host,
        port=settings.web_port,
        log_level="warning",
    )
    await uvicorn.Server(config).serve()


async def _run_telegram(channel: TelegramChannel) -> None:
    application = channel.build_application()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def _main() -> None:
    settings = load_settings()
    init_db(settings.db_path)
    brain = Brain(settings)
    channel = TelegramChannel(settings, brain)

    log.info("웹 채팅: http://%s:%s", settings.web_host, settings.web_port)
    log.info("텔레그램 폴링 시작 (허용 chat_id: %s)",
             ", ".join(str(i) for i in sorted(settings.telegram_allowed_chat_ids)))

    # 한쪽이 죽어도 다른 쪽은 계속 돈다.
    await asyncio.gather(
        _run_web(settings, brain),
        _run_telegram(channel),
        return_exceptions=True,
    )


def main() -> None:
    try:
        asyncio.run(_main())
    except ConfigError as exc:
        raise SystemExit(f"설정 오류: {exc}") from exc
    except KeyboardInterrupt:
        log.info("종료합니다.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest tests/test_web.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add channels/web.py server.py tests/test_web.py
git commit -m "feat: 웹 채팅 창구와 실행 진입점"
```

---

### Task 9: 워크플로 문서와 전체 검증

**Files:**
- Create: `workflows/ai-assistant.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1~8 전부
- Produces: 없음 (문서)

- [ ] **Step 1: 전체 테스트와 커버리지를 확인한다**

Run: `pytest --cov=assistant --cov=channels --cov=tools --cov-report=term-missing`
Expected: 전부 PASS, 커버리지 80% 이상. 80% 미만이면 빠진 분기에 테스트를 더한 뒤 진행한다.

- [ ] **Step 2: 안전장치가 실제로 작동하는지 확인한다**

Run: `pytest -k "trade or stranger or forbidden" -v`
Expected: PASS. 특히 다음 셋이 통과해야 한다.
- `test_model_is_never_given_a_trade_execution_tool`
- `test_request_does_not_execute_anything`
- `test_message_from_stranger_is_ignored`

- [ ] **Step 3: 워크플로 문서를 쓴다**

`workflows/ai-assistant.md`:

````markdown
# AI 업무 비서

## 목표

텔레그램(휴대폰)과 PC 브라우저에서 같은 비서에게 말을 건다.
비서는 주식 분석 결과를 읽고, 관심종목·메모를 기록하고, 가상 매매를 제안한다.

## 준비

1. `.env.example`을 `.env`로 복사하고 다음을 채운다.
   - `ANTHROPIC_API_KEY` — Claude API 키
   - `TELEGRAM_BOT_TOKEN` — @BotFather에게 받은 토큰
   - `TELEGRAM_ALLOWED_CHAT_IDS` — 내 chat_id. 봇에게 아무 말이나 보낸 뒤
     `https://api.telegram.org/bot<토큰>/getUpdates`를 열면 보인다.
   - `STOCK_ANALYZER_PATH` — stock-analyzer 폴더 경로
2. `pip install -r requirements.txt`

## 실행

```
python server.py
```

- 웹 채팅: http://127.0.0.1:8765
- 텔레그램: 봇에게 그냥 말을 건다

## 쓰는 법

평소처럼 물어보면 된다.

- "가상 브로커 지금 얼마야?"
- "최근 시그널 5개 보여줘"
- "엔비디아 관심종목에 넣어줘, 실적 발표 대기중이라고"
- "애플에 대해 메모 남겨줘: 가이던스 확인 후 재검토"
- "지난주에 내가 뭘 바꿨지?"

## 매매는 두 단계다

비서는 매매를 **제안만** 한다. 실행 권한이 없다.

1. "애플 100만원어치 사는 걸로 해줘" → 비서가 제안하고 승인 번호를 준다
2. `/승인 <번호>` → 그때 실제로 가상 브로커에 주문이 들어간다

취소는 `/거절 <번호>`. 대기 목록은 `/승인`만 치면 나온다.
실제 돈으로 하는 주문 기능은 이 시스템에 없다.

## 도구를 하나 더 붙이려면

1. `tools/`에 파이썬 파일을 하나 만든다. 함수는 `Settings`를 첫 인자로 받는다.
2. `tests/`에 그 파일의 테스트를 쓴다. 진짜 데이터에 의존하지 않는다.
3. `assistant/brain.py`의 `_build_tools()`에 `@beta_tool` 함수를 더한다.
   도크스트링이 곧 비서가 읽는 설명서다 — 언제 쓰는 도구인지 명확히 쓴다.
4. 되돌릴 수 없는 행동이라면 실행 함수를 도구 목록에 넣지 말고,
   `tools/virtual_trade.py`처럼 제안과 승인을 분리한다.

## 고장났을 때

| 증상 | 확인할 것 |
|---|---|
| `설정 오류: ...` | `.env`의 해당 항목 |
| 텔레그램이 답을 안 함 | `TELEGRAM_ALLOWED_CHAT_IDS`에 내 chat_id가 있는지 |
| "주식 데이터를 읽지 못했습니다" | `STOCK_ANALYZER_PATH`, 그리고 그 폴더의 파일 상태 |
| 비용이 많이 나옴 | `.env`의 `ASSISTANT_EFFORT`를 `low`로 |

## 아직 없는 것

옵시디언 검색, 학업 자료 요약, 일정 관리, 카카오톡 창구.
설계는 `docs/superpowers/specs/2026-07-29-ai-assistant-design.md`에 있다.
````

`README.md`의 파일 구조 설명에 다음 줄을 더한다.

```markdown
- `server.py` — AI 업무 비서 실행 (사용법: `workflows/ai-assistant.md`)
```

- [ ] **Step 4: 실제로 한 번 띄워본다**

Run: `python server.py`
Expected: 설정 오류 없이 뜨고, 브라우저에서 http://127.0.0.1:8765 가 열리고,
텔레그램 봇이 내 메시지에 답한다. 확인 후 Ctrl+C.

여기서 처음으로 진짜 API 비용이 발생한다. 질문 두어 개로 확인하고 끝낸다.

- [ ] **Step 5: 커밋**

```bash
git add workflows/ai-assistant.md README.md
git commit -m "docs: AI 업무 비서 워크플로 문서"
```

---

## 완료 후

브랜치 `feat/ai-assistant-design`에서 PR을 올린다.

```bash
git push -u origin feat/ai-assistant-design
gh pr create --fill
```

## 다음 단계 (별도 계획)

1. 옵시디언 검색·기록 도구 — 기존 `tools/obsidian_bridge.py`를 감싼다
2. 학업 자료 요약·분석 도구 — 파일 업로드 경로 설계 필요
3. 일정·할일 관리 도구 — 구글 OAuth 필요
4. 카카오톡 창구 — 공개 HTTPS 통로를 열기로 결정할 경우에만
