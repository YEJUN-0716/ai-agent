# 학업 자료 요약·분석 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사장님이 바탕화면 폴더에 넣은 PDF를 비서가 읽고(그림·수식 포함) 요약해 옵시디언 볼트에 노트로 남기고, 그 뒤 그 자료에 대해 질문에 답한다.

**Architecture:** 파일 다루는 일과 Claude에게 PDF를 보여주는 일을 두 파일로 나눈다. `tools/study_materials.py`는 네트워크를 쓰지 않고 폴더·노트·목록만 다룬다(테스트가 빠르고 결정적이다). `tools/study_reader.py`만 Claude API를 부르며 PDF를 `document` 블록으로 첨부한다. 비서에게 노출되는 도구 4개는 이 둘을 조합할 뿐이다.

**Tech Stack:** Python 3.14, `anthropic` SDK (`claude-opus-5`), pytest. **새 의존성 없음** — PDF 라이브러리를 쓰지 않는다.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-07-30-study-materials-design.md` — 충돌 시 설계 문서가 우선한다.
- 모델 ID는 정확히 `claude-opus-5`. 날짜 접미사를 붙이지 않는다.
- `temperature` / `top_p` / `top_k` / `budget_tokens`를 절대 넘기지 않는다 — `claude-opus-5`에서 400 오류.
- **PDF에서 글자를 뽑는 라이브러리를 쓰지 않는다.** `pypdf`·`PyPDF2`·`pdfplumber`·`fitz` 모두 금지. 그림과 수식이 사라진다. PDF는 통째로 API에 넘긴다.
- **원본 PDF를 삭제하지 않는다.** 옮기기만 한다. 지우는 것은 사장님 몫이다.
- 시크릿은 `.env`에만. 코드·테스트·문서·커밋 메시지에 실제 키를 넣지 않는다.
- JSON 목록 파일은 `tools/assistant_notes.py`의 `load_json_list` / `save_json_list`를 쓴다. 손상 파일 정책과 원자적 쓰기를 새로 만들지 않는다.
- 모든 쓰기 행동은 `record_audit(settings, action, detail)`으로 기록한다.
- 한국어 주석·메시지. 콘솔이 cp949일 수 있으므로 `—`(U+2014) 같은 문자를 `print`로 내보내지 않는다 (`logging`은 무방).

## 설계에서 바뀐 것 (계획 단계 확정)

| 설계 문서 | 계획 | 이유 |
|---|---|---|
| 도구 5개 (`read_new_material` + `save_material_note` 분리) | 도구 4개 (`summarize_new_material`로 합침) | `tool_result`에는 `document` 블록을 넣을 수 없다 — 텍스트와 이미지만 된다. 그래서 도구가 **자기 API 호출**로 PDF를 첨부한다 |
| `materials.json`에 `pages` | `pages` 없음 | 미리 세려면 PDF 라이브러리가 필요한데 금지했다. 600쪽 초과는 API가 알려준다 |

## File Structure

| 파일 | 책임 |
|---|---|
| `assistant/config.py` (수정) | 자료 폴더 두 개 설정 |
| `tools/study_materials.py` (신규) | 받은 목록, 노트 쓰기, 원본 이동, 목록 파일. **네트워크 없음** |
| `tools/study_reader.py` (신규) | PDF를 `document` 블록으로 붙여 Claude에게 묻는다 |
| `assistant/brain.py` (수정) | 도구 4개 등록 + 시스템 프롬프트 |
| `tests/test_study_materials.py` (신규) | 파일 조작 — API 없이 돈다 |
| `tests/test_study_reader.py` (신규) | 가짜 client로 요청 모양 검증 |
| `tests/test_study_seam.py` (신규) | **진짜 PDF**로 문서 블록이 실제로 통하는지 |

---

### Task 1: 자료 폴더 설정

**Files:**
- Modify: `assistant/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 기존 `Settings` 동결 데이터클래스, `ConfigError`
- Produces: `Settings.study_inbox: Path`, `Settings.obsidian_vault: Path`,
  프로퍼티 `study_done_dir: Path`, `study_notes_dir: Path`, `materials_path: Path`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config.py` 위쪽에 헬퍼를 만든다 (기존 테스트들이 각자 반복하던 공통부):

```python
def _set_required_env(monkeypatch, tmp_path) -> None:
    """필수 항목만 채운다. 선택 항목은 각 테스트가 알아서."""
    stock = tmp_path / "stock-analyzer"
    stock.mkdir(exist_ok=True)
    vault = tmp_path / "볼트"
    vault.mkdir(exist_ok=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1")
    monkeypatch.setenv("STOCK_ANALYZER_PATH", str(stock))
    monkeypatch.setenv("ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    for name in ("ASSISTANT_MODEL", "ASSISTANT_EFFORT", "ASSISTANT_WEB_HOST",
                 "ASSISTANT_WEB_PORT", "ASSISTANT_HISTORY_LIMIT", "STUDY_INBOX"):
        monkeypatch.delenv(name, raising=False)
```

그리고 파일 끝에 테스트 세 개:

```python
def test_study_folders_come_from_env(tmp_path, monkeypatch):
    """자료 폴더와 볼트 경로를 .env로 바꿀 수 있어야 한다."""
    # Arrange
    inbox = tmp_path / "받는곳"
    vault = tmp_path / "볼트"
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STUDY_INBOX", str(inbox))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))

    # Act
    settings = load_settings()

    # Assert
    assert settings.study_inbox == inbox
    assert settings.study_done_dir == inbox / "처리완료"
    assert settings.study_notes_dir == vault / "학업"
    assert settings.materials_path == settings.assistant_data_dir / "materials.json"


def test_missing_vault_is_reported_in_plain_korean(tmp_path, monkeypatch):
    """볼트 경로가 없으면 시작할 때 막고, 무엇을 고쳐야 하는지 알려준다."""
    # Arrange
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)

    # Act / Assert
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "OBSIDIAN_VAULT" in str(exc.value)


def test_vault_folder_must_exist(tmp_path, monkeypatch):
    """오타로 없는 폴더를 적으면 조용히 새로 만들지 않고 알린다."""
    # Arrange
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "없는볼트"))

    # Act / Assert
    with pytest.raises(ConfigError):
        load_settings()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_config.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'study_inbox'`

- [ ] **Step 3: 최소 구현**

`assistant/config.py` 상수 부분에 더한다:

```python
DEFAULT_STUDY_INBOX = "~/Desktop/자료넣는곳"

# 자료넣는곳 안의 이 폴더는 '이미 정리한 것'이다. 새 자료를 훑을 때 건너뛴다.
STUDY_DONE_NAME = "처리완료"
# 볼트 안에서 비서가 노트를 쓰는 유일한 폴더. 사장님의 다른 노트를 건드리지 않는다.
STUDY_NOTES_NAME = "학업"
```

`Settings`에 필드 두 개 (기존 필드 뒤에):

```python
    study_inbox: Path
    obsidian_vault: Path
```

프로퍼티 세 개:

```python
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
```

`load_settings()`의 `return Settings(...)` 직전에:

```python
    vault = Path(_require("OBSIDIAN_VAULT")).expanduser()
    if not vault.is_dir():
        raise ConfigError(
            f"OBSIDIAN_VAULT가 가리키는 폴더가 없습니다: {vault}. "
            "옵시디언에서 볼트 폴더를 확인해 .env에 정확한 경로를 적어주세요."
        )

    # 받는 곳은 없으면 만든다. 볼트와 달리 비서가 소유하는 폴더다.
    study_inbox = Path(
        os.environ.get("STUDY_INBOX", DEFAULT_STUDY_INBOX)
    ).expanduser()
    study_inbox.mkdir(parents=True, exist_ok=True)
```

`Settings(...)` 호출에 두 줄:

```python
        study_inbox=study_inbox,
        obsidian_vault=vault,
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_config.py -q`
Expected: PASS

기존 테스트가 `OBSIDIAN_VAULT` 없이 `load_settings()`를 부르면 이제 실패한다.
그 테스트들도 `_set_required_env`를 쓰도록 고친다.

- [ ] **Step 5: `.env.example`을 고친다**

10행의 주석 처리된 `OBSIDIAN_VAULT`를 실제 항목으로 올리고 새 항목을 붙인다:

```
# 옵시디언 볼트 폴더 (필수). 비서는 이 안의 "학업" 폴더에만 노트를 씁니다.
OBSIDIAN_VAULT=C:\Users\1aass\OneDrive\Desktop\ObsidianVault

# 학업 자료를 넣는 폴더 (선택). 비우면 바탕화면의 "자료넣는곳"을 씁니다.
# STUDY_INBOX=C:\Users\1aass\OneDrive\Desktop\자료넣는곳
```

- [ ] **Step 6: 커밋**

```bash
git add assistant/config.py tests/test_config.py .env.example
git commit -m "feat: 학업 자료 폴더와 옵시디언 볼트 설정"
```

---

### Task 2: 받은 자료 목록

**Files:**
- Create: `tools/study_materials.py`
- Test: `tests/test_study_materials.py`

**Interfaces:**
- Consumes: `Settings.study_inbox`, `Settings.study_done_dir`
- Produces: `MAX_PDF_BYTES: int`, `class StudyError(RuntimeError)`,
  `list_new(settings: Settings) -> list[dict]` — 각 항목은
  `{"filename": str, "size_bytes": int, "too_large": bool}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_study_materials.py`:

```python
from pathlib import Path

import pytest

from assistant.config import Settings
from tools.study_materials import MAX_PDF_BYTES, StudyError, list_new


@pytest.fixture
def settings(tmp_path) -> Settings:
    inbox = tmp_path / "자료넣는곳"
    inbox.mkdir()
    (inbox / "처리완료").mkdir()
    vault = tmp_path / "볼트"
    (vault / "학업").mkdir(parents=True)
    data = tmp_path / "assistant"
    data.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=tmp_path,
        assistant_data_dir=data,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
        study_inbox=inbox,
        obsidian_vault=vault,
    )


def test_lists_waiting_pdfs(settings):
    """자료넣는곳의 PDF를 찾아낸다."""
    # Arrange
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["논문.pdf"]


def test_skips_already_processed(settings):
    """처리완료 폴더는 건너뛴다. 안 그러면 매번 다시 정리한다."""
    # Arrange
    (settings.study_inbox / "새자료.pdf").write_bytes(b"%PDF-1.4 fake")
    (settings.study_done_dir / "지난자료.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["새자료.pdf"]


def test_skips_files_that_are_not_pdf(settings):
    # Arrange
    (settings.study_inbox / "메모.txt").write_text("안녕", encoding="utf-8")
    (settings.study_inbox / "논문.pdf").write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["논문.pdf"]


def test_marks_oversized_files_instead_of_hiding_them(settings):
    """32MB 초과도 목록에는 넣고 표시만 한다.

    목록에서 빼버리면 사장님은 자기가 넣은 파일이 왜 안 보이는지 알 수 없다.
    """
    # Arrange
    (settings.study_inbox / "큰자료.pdf").write_bytes(b"x" * (MAX_PDF_BYTES + 1))

    # Act
    waiting = list_new(settings)

    # Assert
    assert waiting[0]["too_large"] is True


def test_lists_alphabetically_for_a_stable_order(settings):
    """순서가 실행마다 바뀌면 목록을 믿기 어렵다."""
    # Arrange
    for name in ("다.pdf", "가.pdf", "나.pdf"):
        (settings.study_inbox / name).write_bytes(b"%PDF-1.4 fake")

    # Act
    waiting = list_new(settings)

    # Assert
    assert [item["filename"] for item in waiting] == ["가.pdf", "나.pdf", "다.pdf"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_materials.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.study_materials'`

- [ ] **Step 3: 최소 구현**

`tools/study_materials.py`:

```python
"""학업 자료의 파일 쪽 일 — 받은 목록, 노트 쓰기, 원본 이동, 목록 파일.

여기서는 네트워크를 쓰지 않는다. Claude에게 PDF를 보여주는 일은
tools/study_reader.py 가 맡는다. 나눠두면 이 파일의 테스트가 빠르고
결정적이며, API 없이도 전부 돈다.
"""

from __future__ import annotations

from pathlib import Path

from assistant.config import Settings

# 요청 하나의 상한. Claude API 문서 기준 32MB.
MAX_PDF_BYTES = 32 * 1024 * 1024


class StudyError(RuntimeError):
    """자료를 다루지 못했을 때. 메시지는 사장님께 그대로 보여준다."""


def list_new(settings: Settings) -> list[dict]:
    """정리를 기다리는 PDF 목록.

    처리완료 폴더는 건너뛴다 — 그 안은 이미 정리한 것이고, 자료넣는곳
    **안**에 있어서 그냥 훑으면 매번 다시 정리 대상이 된다.
    """
    inbox = settings.study_inbox
    if not inbox.is_dir():
        return []

    items: list[dict] = []
    for path in sorted(inbox.iterdir()):
        if path.is_dir() or path.suffix.lower() != ".pdf":
            continue
        size = path.stat().st_size
        items.append({
            "filename": path.name,
            "size_bytes": size,
            "too_large": size > MAX_PDF_BYTES,
        })
    return items
```

`inbox.iterdir()`는 한 겹만 본다. 처리완료는 하위 폴더이므로 `path.is_dir()`에서
걸러지고, 그 안의 파일은 애초에 순회 대상이 아니다.

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_materials.py -q`
Expected: PASS (5개)

- [ ] **Step 5: 커밋**

```bash
git add tools/study_materials.py tests/test_study_materials.py
git commit -m "feat: 정리 대기 중인 학업 자료 목록"
```

---

### Task 3: 노트 저장 — 셋 다 되거나 셋 다 안 되거나

**Files:**
- Modify: `tools/study_materials.py`
- Test: `tests/test_study_materials.py`

**Interfaces:**
- Consumes: Task 2의 `StudyError`; `tools.assistant_notes`의
  `load_json_list(settings, path)`, `save_json_list(path, items)`,
  `record_audit(settings, action, detail)`, `now_kst()`
- Produces:
  `save_material(settings, filename: str, title: str, one_line: str, note_body: str) -> dict`
  — 반환 `{"material_id": str, "note_path": str, "moved_to": str}`;
  `list_materials(settings) -> list[dict]`;
  `material_path(settings, material_id: str) -> Path`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_study_materials.py`에 붙인다:

```python
from tools.study_materials import list_materials, material_path, save_material


def _put_pdf(settings, name="논문.pdf"):
    path = settings.study_inbox / name
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_saving_writes_note_moves_original_and_records_index(settings):
    """세 가지가 한 덩어리로 일어난다."""
    # Arrange
    _put_pdf(settings)

    # Act
    result = save_material(
        settings, "논문.pdf", "논문제목", "한 줄 요약", "## 핵심\n내용"
    )

    # Assert
    note = Path(result["note_path"])
    assert note.exists()
    assert "논문제목" in note.read_text(encoding="utf-8")
    assert not (settings.study_inbox / "논문.pdf").exists()
    assert (settings.study_done_dir / "논문.pdf").exists()
    assert [m["title"] for m in list_materials(settings)] == ["논문제목"]


def test_original_is_moved_not_deleted(settings):
    """원본은 절대 사라지지 않는다. 지우는 것은 사장님 몫이다."""
    # Arrange
    _put_pdf(settings)
    original = (settings.study_inbox / "논문.pdf").read_bytes()

    # Act
    save_material(settings, "논문.pdf", "제목", "한 줄", "본문")

    # Assert
    assert (settings.study_done_dir / "논문.pdf").read_bytes() == original


def test_note_name_collision_does_not_overwrite(settings):
    """같은 날 같은 제목이어도 앞의 노트를 덮어쓰지 않는다."""
    # Arrange
    _put_pdf(settings, "첫번째.pdf")
    _put_pdf(settings, "두번째.pdf")

    # Act
    first = save_material(settings, "첫번째.pdf", "같은제목", "하나", "본문A")
    second = save_material(settings, "두번째.pdf", "같은제목", "둘", "본문B")

    # Assert
    assert first["note_path"] != second["note_path"]
    assert "본문A" in Path(first["note_path"]).read_text(encoding="utf-8")
    assert "본문B" in Path(second["note_path"]).read_text(encoding="utf-8")


def test_nothing_is_left_behind_when_the_move_fails(settings, monkeypatch):
    """원본을 못 옮기면 노트도 목록도 남기지 않는다.

    반쪽만 남으면 다음 실행이 같은 자료를 또 정리해 노트가 둘이 된다.
    """
    # Arrange
    _put_pdf(settings)
    import tools.study_materials as mod

    def refuse_move(src, dst):
        raise OSError("옮기지 못했습니다")

    monkeypatch.setattr(mod.shutil, "move", refuse_move)

    # Act
    with pytest.raises(StudyError):
        save_material(settings, "논문.pdf", "제목", "한 줄", "본문")

    # Assert
    assert (settings.study_inbox / "논문.pdf").exists()
    assert list(settings.study_notes_dir.glob("*.md")) == []
    assert list_materials(settings) == []


def test_missing_source_is_reported_clearly(settings):
    # Act / Assert
    with pytest.raises(StudyError) as exc:
        save_material(settings, "없는파일.pdf", "제목", "한 줄", "본문")
    assert "없는파일.pdf" in str(exc.value)


def test_material_path_finds_the_archived_original(settings):
    """나중에 세부 질문을 받으면 보관된 원본을 찾아야 한다."""
    # Arrange
    _put_pdf(settings)
    result = save_material(settings, "논문.pdf", "제목", "한 줄", "본문")

    # Act
    found = material_path(settings, result["material_id"])

    # Assert
    assert found == settings.study_done_dir / "논문.pdf"


def test_material_path_rejects_an_unknown_id(settings):
    # Act / Assert
    with pytest.raises(StudyError):
        material_path(settings, "없는번호")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_materials.py -q`
Expected: FAIL — `ImportError: cannot import name 'save_material'`

- [ ] **Step 3: 최소 구현**

`tools/study_materials.py` 위쪽 import를 고친다:

```python
import shutil
import uuid
from pathlib import Path

from assistant.config import Settings
from tools.assistant_notes import (
    load_json_list,
    now_kst,
    record_audit,
    save_json_list,
)
```

그리고 함수들을 더한다:

```python
def list_materials(settings: Settings) -> list[dict]:
    """정리를 마친 자료 목록. 제목과 한 줄 요약으로 무엇을 펼칠지 고른다."""
    return load_json_list(settings, settings.materials_path)


def material_path(settings: Settings, material_id: str) -> Path:
    """보관된 원본 PDF의 경로. 세부 질문에 답할 때 쓴다."""
    for item in list_materials(settings):
        if item.get("material_id") == material_id:
            path = settings.study_done_dir / Path(item["source_file"]).name
            if not path.exists():
                raise StudyError(
                    f"원본 파일을 찾지 못했습니다: {path.name}. "
                    "처리완료 폴더에서 옮기거나 지우셨는지 확인해 주세요."
                )
            return path
    raise StudyError(f"그런 번호의 자료가 없습니다: {material_id}")


def _free_note_path(settings: Settings, title: str) -> Path:
    """겹치지 않는 노트 경로. 기존 노트를 덮어쓰지 않는다."""
    stamp = now_kst().date().isoformat()
    safe = "".join(c for c in title if c not in '\\/:*?"<>|').strip() or "제목없음"
    base = settings.study_notes_dir / f"{stamp}-{safe}.md"
    if not base.exists():
        return base
    for n in range(2, 100):
        candidate = base.with_name(f"{stamp}-{safe}-{n}.md")
        if not candidate.exists():
            return candidate
    raise StudyError(f"같은 이름의 노트가 너무 많습니다: {safe}")


def _note_text(material_id: str, source: str, title: str,
               one_line: str, body: str) -> str:
    return (
        "---\n"
        f"source: {source}\n"
        f"ingested: {now_kst().date().isoformat()}\n"
        f"material_id: {material_id}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"## 한 줄 요약\n{one_line}\n\n"
        f"{body}\n\n"
        "---\n"
        "원본 PDF는 처리완료 폴더에 있습니다. 세부 내용은 비서에게 물어보세요.\n"
    )


def save_material(settings: Settings, filename: str, title: str,
                  one_line: str, note_body: str) -> dict:
    """노트를 쓰고, 원본을 옮기고, 목록에 남긴다 — 셋 다 되거나 셋 다 안 되거나.

    반쪽만 남으면 다음 실행이 같은 자료를 다시 정리해 노트가 둘이 된다.
    그래서 어느 단계가 실패하든 앞선 단계를 되돌린다.
    """
    source = settings.study_inbox / filename
    if not source.exists():
        raise StudyError(
            f"{filename}을 자료넣는곳에서 찾지 못했습니다. "
            "이미 정리했거나 파일 이름이 바뀌었을 수 있습니다."
        )

    settings.study_notes_dir.mkdir(parents=True, exist_ok=True)
    settings.study_done_dir.mkdir(parents=True, exist_ok=True)

    material_id = uuid.uuid4().hex[:8]
    note = _free_note_path(settings, title)
    entry = {
        "material_id": material_id,
        "title": title,
        "one_line": one_line,
        "source_file": f"처리완료/{filename}",
        "ingested_at": now_kst().isoformat(timespec="seconds"),
        "note_path": str(note),
    }

    note.write_text(
        _note_text(material_id, filename, title, one_line, note_body),
        encoding="utf-8",
    )

    items = list_materials(settings)
    try:
        items.append(entry)
        save_json_list(settings.materials_path, items)
    except OSError as exc:
        note.unlink(missing_ok=True)
        raise StudyError(f"자료 목록에 기록하지 못했습니다: {exc}") from exc

    try:
        shutil.move(str(source), str(settings.study_done_dir / filename))
    except OSError as exc:
        # 원본이 그대로 남아야 다시 시도할 수 있다. 노트와 목록을 되돌린다.
        note.unlink(missing_ok=True)
        save_json_list(settings.materials_path, items[:-1])
        raise StudyError(
            f"{filename} 원본을 처리완료로 옮기지 못했습니다: {exc}. "
            "자료는 그대로 두었으니 다시 시도할 수 있습니다."
        ) from exc

    record_audit(settings, "material_add", f"{material_id} {title}")
    return {
        "material_id": material_id,
        "note_path": str(note),
        "moved_to": str(settings.study_done_dir / filename),
    }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_materials.py -q`
Expected: PASS (12개)

- [ ] **Step 5: 되돌리기가 진짜 도는지 확인한다**

`save_material`의 `note.unlink(missing_ok=True)` 두 줄을 잠시 지우고 돌린다.
`test_nothing_is_left_behind_when_the_move_fails`가 **실패해야 한다.** 실패하지
않으면 그 테스트는 아무것도 지키지 않는 것이니 고친다. 확인 후 되돌린다.

- [ ] **Step 6: 커밋**

```bash
git add tools/study_materials.py tests/test_study_materials.py
git commit -m "feat: 학업 자료 노트 저장 (노트·원본·목록을 한 덩어리로)"
```

---

### Task 4: PDF를 비서에게 보여주기

**Files:**
- Create: `tools/study_reader.py`
- Test: `tests/test_study_reader.py`

**Interfaces:**
- Consumes: `Settings.model`, `Settings.effort`, `Settings.anthropic_api_key`;
  Task 2의 `MAX_PDF_BYTES`, `StudyError`
- Produces:
  `summarize_pdf(settings, pdf_path: Path, client=None) -> dict`
  — 반환 `{"title": str, "one_line": str, "note_body": str}`;
  `ask_pdf(settings, pdf_path: Path, question: str, client=None) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_study_reader.py`:

```python
import base64
import json
from pathlib import Path

import pytest

from assistant.config import Settings
from tools.study_materials import MAX_PDF_BYTES, StudyError
from tools.study_reader import ask_pdf, summarize_pdf


class FakeMessages:
    """요청을 붙잡아 두고 정해진 답을 돌려준다."""

    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        reply = self.reply_text

        class Block:
            type = "text"
            text = reply

        class Response:
            content = [Block()]

        return Response()


class FakeClient:
    def __init__(self, reply_text: str) -> None:
        self.messages = FakeMessages(reply_text)


SUMMARY_JSON = json.dumps(
    {"title": "제목", "one_line": "한 줄", "note_body": "본문"}
)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "논문.pdf"
    path.write_bytes(b"%PDF-1.4 fake content")
    return path


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        anthropic_api_key="k", telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=tmp_path, assistant_data_dir=tmp_path,
        model="claude-opus-5", effort="medium",
        web_host="127.0.0.1", web_port=8765, history_limit=40,
        study_inbox=tmp_path, obsidian_vault=tmp_path,
    )


def test_pdf_is_attached_as_a_document_block(settings, pdf):
    """PDF를 문서 블록으로 붙여야 그림과 수식까지 본다.

    글자만 뽑아 텍스트로 넣으면 그림이 사라진다. 이 테스트가 그것을 막는다.
    """
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    document = client.messages.last_kwargs["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(document["source"]["data"]) == pdf.read_bytes()


def test_document_comes_before_the_question(settings, pdf):
    """문서를 텍스트보다 앞에 두라는 것이 API 권고사항이다."""
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    blocks = client.messages.last_kwargs["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["document", "text"]


def test_base64_has_no_newlines(settings, pdf):
    """줄바꿈이 섞이면 API가 거절한다."""
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    data = client.messages.last_kwargs["messages"][0]["content"][0]["source"]["data"]
    assert "\n" not in data


def test_forbidden_parameters_are_never_sent(settings, pdf):
    """claude-opus-5에서 400을 내는 항목들."""
    # Arrange
    client = FakeClient(SUMMARY_JSON)

    # Act
    summarize_pdf(settings, pdf, client=client)

    # Assert
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert forbidden not in client.messages.last_kwargs


def test_summary_comes_back_as_three_parts(settings, pdf):
    # Arrange
    client = FakeClient(json.dumps({
        "title": "어떤 논문", "one_line": "요지는 이렇다",
        "note_body": "## 핵심\n- 하나",
    }))

    # Act
    result = summarize_pdf(settings, pdf, client=client)

    # Assert
    assert result["title"] == "어떤 논문"
    assert result["one_line"] == "요지는 이렇다"
    assert "## 핵심" in result["note_body"]


def test_json_inside_a_code_fence_is_still_read(settings, pdf):
    """모델이 ```json 울타리를 칠 때가 있다."""
    # Arrange
    client = FakeClient(f"```json\n{SUMMARY_JSON}\n```")

    # Act
    result = summarize_pdf(settings, pdf, client=client)

    # Assert
    assert result["title"] == "제목"


def test_oversized_pdf_is_refused_before_spending_money(settings, tmp_path):
    """한도 초과는 보내기 전에 막는다. 보내고 400을 받으면 요금만 나간다."""
    # Arrange
    big = tmp_path / "큰자료.pdf"
    big.write_bytes(b"x" * (MAX_PDF_BYTES + 1))
    client = FakeClient("{}")

    # Act / Assert
    with pytest.raises(StudyError) as exc:
        summarize_pdf(settings, big, client=client)
    assert "32" in str(exc.value)
    assert client.messages.last_kwargs is None


def test_a_reply_that_is_not_json_is_reported_not_guessed(settings, pdf):
    """모델이 형식을 어기면 지어내지 말고 실패로 알린다."""
    # Arrange
    client = FakeClient("음... 요약을 못 하겠습니다")

    # Act / Assert
    with pytest.raises(StudyError):
        summarize_pdf(settings, pdf, client=client)


def test_ask_pdf_puts_the_question_after_the_document(settings, pdf):
    # Arrange
    client = FakeClient("3장의 예시는 이러이러합니다")

    # Act
    answer = ask_pdf(settings, pdf, "3장 예시가 뭐였지?", client=client)

    # Assert
    blocks = client.messages.last_kwargs["messages"][0]["content"]
    assert blocks[0]["type"] == "document"
    assert "3장 예시가 뭐였지?" in blocks[1]["text"]
    assert answer == "3장의 예시는 이러이러합니다"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_reader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.study_reader'`

- [ ] **Step 3: 최소 구현**

`tools/study_reader.py`:

```python
"""PDF를 Claude에게 보여주고 요약·답변을 받아온다.

왜 도구가 직접 API를 부르는가:
  tool_result 에는 문서 블록을 넣을 수 없다 (텍스트와 이미지만 된다).
  그래서 "PDF를 비서에게 건네준다"를 도구 반환값으로는 할 수 없다.
  대신 이 도구가 자기 요청을 만들어 PDF를 붙이고, 받은 답을 글로 돌려준다.

왜 글자를 뽑지 않는가:
  API는 각 페이지를 이미지로 바꿔 텍스트와 함께 모델에 준다. 그림·수식·표를
  본다. pypdf 같은 것으로 글자만 뽑으면 그게 전부 사라진다.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic

from assistant.config import Settings
from tools.study_materials import MAX_PDF_BYTES, StudyError

MAX_TOKENS = 8000

_SUMMARY_PROMPT = """이 자료를 사장님이 나중에 다시 읽을 수 있게 정리해 주세요.

반드시 아래 형태의 JSON만 출력하세요. 다른 말을 붙이지 마세요.

{
  "title": "자료 제목 (파일 이름이 아니라 내용상의 제목)",
  "one_line": "이 자료가 무슨 얘기인지 한 문장",
  "note_body": "마크다운 본문"
}

note_body 에는 다음 두 부분을 넣으세요.

## 핵심
- 요점을 항목으로. 원본을 다시 안 열어도 될 만큼 담되, 옮겨 적지는 마세요.

## 쉬운 말로
어려운 대목을 풀어서 설명합니다. 그림이나 수식이 있으면 그것이 무엇을
말하는지도 우리말로 적어주세요.
"""


def _client(settings: Settings, client):
    return client or anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _document_block(pdf_path: Path) -> dict:
    """PDF를 문서 블록으로. 줄바꿈 없는 base64여야 API가 받는다."""
    if not pdf_path.exists():
        raise StudyError(f"파일을 찾지 못했습니다: {pdf_path.name}")

    size = pdf_path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise StudyError(
            f"{pdf_path.name}은 너무 큽니다 ({size / 1024 / 1024:.0f}MB). "
            "한 번에 보낼 수 있는 크기는 32MB입니다. 자료를 나눠서 넣어주세요."
        )

    encoded = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": encoded,
        },
    }


def _text_of(message) -> str:
    return "".join(
        block.text for block in message.content
        if getattr(block, "type", "") == "text"
    )


def _explain(exc: Exception) -> str:
    """API 오류를 사장님이 읽고 조치할 수 있는 말로."""
    text = str(exc)
    if "credit balance is too low" in text:
        return ("Claude API 크레딧이 떨어졌습니다. "
                "console.anthropic.com → Plans & Billing에서 충전해 주세요.")
    if "too many pages" in text or "600" in text:
        return "쪽수가 너무 많습니다. 한 번에 600쪽까지만 읽을 수 있습니다."
    if "encrypted" in text or "password" in text:
        return "암호가 걸린 PDF는 읽을 수 없습니다. 암호를 푼 뒤 다시 넣어주세요."
    return f"자료를 읽는 중 문제가 생겼습니다: {text}"


def _ask(settings: Settings, pdf_path: Path, question: str, client) -> str:
    document = _document_block(pdf_path)   # 보내기 전에 크기를 막는다
    try:
        message = _client(settings, client).messages.create(
            model=settings.model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": settings.effort},
            messages=[{
                "role": "user",
                # 문서를 텍스트보다 앞에 두는 것이 API 권고사항이다.
                "content": [document, {"type": "text", "text": question}],
            }],
        )
    except Exception as exc:   # noqa: BLE001 — 사장님께 옮겨 전한다
        raise StudyError(_explain(exc)) from exc
    return _text_of(message).strip()


def summarize_pdf(settings: Settings, pdf_path: Path, client=None) -> dict:
    """자료를 읽고 제목·한 줄 요약·본문을 돌려준다."""
    answer = _ask(settings, pdf_path, _SUMMARY_PROMPT, client)

    cleaned = answer.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        data = json.loads(cleaned)
        return {
            "title": str(data["title"]).strip(),
            "one_line": str(data["one_line"]).strip(),
            "note_body": str(data["note_body"]).strip(),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StudyError(
            f"{pdf_path.name} 요약을 정리된 형태로 받지 못했습니다. "
            "다시 시도해 주세요."
        ) from exc


def ask_pdf(settings: Settings, pdf_path: Path, question: str,
            client=None) -> str:
    """보관된 원본을 펼쳐 세부 질문에 답한다."""
    return _ask(settings, pdf_path, question, client)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_reader.py -q`
Expected: PASS (9개)

- [ ] **Step 5: 커밋**

```bash
git add tools/study_reader.py tests/test_study_reader.py
git commit -m "feat: PDF를 문서 블록으로 붙여 요약·질문"
```

---

### Task 5: 진짜 PDF로 이음매 확인

**Files:**
- Create: `tests/test_study_seam.py`
- Create: `tests/fixtures/그림과수식.pdf`

**Interfaces:**
- Consumes: Task 4의 `summarize_pdf`
- Produces: 없음 (테스트 전용)

가짜 client만으로는 "우리가 만든 요청을 API가 실제로 받아주는가"를 확인할 수 없다.
2026-07-30에 비서-브로커 이음매에서 똑같은 이유로 결함 2건이 106 그린인 채 머지됐다.
이 파일이 그 자리를 지킨다.

- [ ] **Step 1: 그림과 수식이 든 PDF를 만든다**

글자를 뽑는 라이브러리는 금지지만 **테스트용 PDF를 만드는 것**은 다르다.
`matplotlib`은 stock-analyzer에 이미 깔려 있다. 한 번 만들어 커밋하면 끝이다.

```bash
PYTHONUTF8=1 python - <<'PY'
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3, 4], [1, 4, 9, 16], marker="o")
ax.set_title("Growth of x squared")
# 수식 — 글자 추출로는 절대 안 나오는 것
ax.text(1.2, 12, r"$E = mc^2$", fontsize=22)
ax.text(1.2, 8, r"$\int_0^1 x^2 dx = \frac{1}{3}$", fontsize=18)
out = Path("tests/fixtures"); out.mkdir(parents=True, exist_ok=True)
fig.savefig(out / "그림과수식.pdf", format="pdf")
print("만들었습니다:", (out / "그림과수식.pdf").stat().st_size, "바이트")
PY
```

- [ ] **Step 2: 이음매 테스트를 쓴다**

`tests/test_study_seam.py`:

```python
"""우리가 만든 요청을 Claude API가 실제로 받아주는지 확인한다.

다른 테스트는 전부 가짜 client를 쓴다. 그래서 문서 블록의 모양이 틀려도,
금지된 항목을 넣어도 아무도 잡지 못한다. 이 파일만이 진짜로 보낸다.

돈이 든다 (1회 수백 원). 기본으로는 건너뛰고, 확인하고 싶을 때만 켠다:

    RUN_PDF_SEAM=1 PYTHONUTF8=1 python -m pytest tests/test_study_seam.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from assistant.config import load_settings
from tools.study_reader import summarize_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "그림과수식.pdf"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PDF_SEAM") != "1",
    reason="진짜 API를 부르고 요금이 듭니다. RUN_PDF_SEAM=1 로 켜세요.",
)


def test_api_accepts_our_document_block():
    """요청 모양이 맞는지. 400이 나면 여기서 걸린다."""
    # Arrange
    settings = load_settings()

    # Act
    result = summarize_pdf(settings, FIXTURE)

    # Assert
    assert result["title"]
    assert result["one_line"]
    assert result["note_body"]


def test_the_model_actually_sees_the_picture_and_the_formula():
    """글자 추출로는 절대 못 얻는 것을 읽어냈는지.

    이 PDF의 수식과 그래프는 그림으로만 존재한다. 요약에 그 내용이
    나온다면 API가 페이지를 이미지로도 보고 있다는 뜻이다.
    """
    # Arrange
    settings = load_settings()

    # Act
    result = summarize_pdf(settings, FIXTURE)
    whole = (result["one_line"] + result["note_body"]).lower()

    # Assert
    assert any(word in whole for word in
               ("mc", "제곱", "적분", "그래프", "곡선", "수식")), whole
```

- [ ] **Step 3: 실제로 돌려본다**

Run: `RUN_PDF_SEAM=1 PYTHONUTF8=1 python -m pytest tests/test_study_seam.py -q -s`
Expected: PASS 2개. 실패하면 요청 모양이 틀린 것이니 Task 4로 돌아간다.

두 번째 테스트가 통과하는 것이 이 기능 전체의 근거다. 실패하면 설계 전제가
무너진 것이므로 **진행하지 말고 보고한다.**

- [ ] **Step 4: 건너뛰기가 도는지 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_study_seam.py -q`
Expected: 2 skipped (요금이 나가지 않는다)

- [ ] **Step 5: 커밋**

```bash
git add tests/test_study_seam.py tests/fixtures/그림과수식.pdf
git commit -m "test: 진짜 PDF로 문서 블록 이음매 확인"
```

---

### Task 6: 비서에 도구 붙이기

**Files:**
- Modify: `assistant/brain.py`
- Test: `tests/test_brain.py`

**Interfaces:**
- Consumes: Task 2~4의 `list_new`, `save_material`, `list_materials`,
  `material_path`, `StudyError`, `summarize_pdf`, `ask_pdf`
- Produces: 도구 4개 — `list_new_materials()`, `summarize_new_material(filename)`,
  `list_materials()`, `ask_material(material_id, question)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_brain.py`의 `settings` 픽스처에 `study_inbox` / `obsidian_vault`
두 줄을 더한다 (Task 1에서 필수 필드가 됐다). 그리고 테스트 둘:

```python
def test_study_tools_are_available(settings):
    """네 가지 도구가 비서에게 보여야 한다."""
    # Arrange / Act
    names = Brain(settings, client=object()).tool_names()

    # Assert
    for tool in ("list_new_materials", "summarize_new_material",
                 "list_materials", "ask_material"):
        assert tool in names


def test_model_cannot_delete_materials(settings):
    """지우는 도구는 주지 않는다. 원본 삭제는 사장님 몫이다."""
    # Arrange / Act
    names = Brain(settings, client=object()).tool_names()

    # Assert
    assert not any("delete" in n or "remove_material" in n for n in names)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest tests/test_brain.py -q`
Expected: FAIL — `assert 'list_new_materials' in names`

- [ ] **Step 3: 최소 구현**

`assistant/brain.py`의 import를 고친다:

```python
from tools import (
    assistant_notes,
    stock_reader,
    study_materials,
    study_reader,
    virtual_trade,
)
```

`_build_tools()` 안, `request_trade` 앞에 네 개를 더한다:

```python
        @beta_tool
        def list_new_materials() -> str:
            """정리를 기다리는 학업 자료(PDF) 목록을 확인한다."""
            return str(study_materials.list_new(settings))

        @beta_tool
        def summarize_new_material(filename: str) -> str:
            """PDF 한 개를 읽고 요약해 옵시디언 볼트에 노트로 저장한다.

            자료를 읽는 데 비용이 든다. 사장님이 정리를 요청했을 때만 쓴다.

            Args:
                filename: 자료넣는곳에 있는 파일 이름 (예: 논문.pdf).
            """
            try:
                summary = study_reader.summarize_pdf(
                    settings, settings.study_inbox / filename
                )
                saved = study_materials.save_material(
                    settings, filename,
                    summary["title"], summary["one_line"], summary["note_body"],
                )
                return str({**saved, "title": summary["title"]})
            except study_materials.StudyError as exc:
                return f"정리하지 못했습니다: {exc}"

        @beta_tool
        def list_materials() -> str:
            """정리해 둔 학업 자료 목록을 조회한다. 제목과 한 줄 요약이 나온다."""
            return str(study_materials.list_materials(settings))

        @beta_tool
        def ask_material(material_id: str, question: str) -> str:
            """보관된 원본 PDF를 펼쳐 세부 질문에 답한다.

            요약 노트로 답할 수 없을 때만 쓴다 — 원본을 다시 읽으므로 비용이 든다.

            Args:
                material_id: list_materials로 확인한 자료 번호.
                question: 원본에서 확인할 내용.
            """
            try:
                path = study_materials.material_path(settings, material_id)
                return study_reader.ask_pdf(settings, path, question)
            except study_materials.StudyError as exc:
                return f"확인하지 못했습니다: {exc}"
```

반환 목록에 네 이름을 더한다 (`request_trade` 앞).

- [ ] **Step 4: 시스템 프롬프트에 안내를 더한다**

`SYSTEM_PROMPT`의 "주식에 대해:" 블록 뒤에 붙인다:

```
학업 자료에 대해:
- 자료를 정리하려면 먼저 list_new_materials로 무엇이 기다리는지 봅니다.
- **정리는 비용이 듭니다(자료 1건에 수천 원).** 사장님이 정리를 요청했을 때만
  summarize_new_material을 씁니다. 물어보지 않았는데 미리 정리하지 마십시오.
- 질문에 답할 때는 **먼저 list_materials와 요약 노트로 답해 보십시오.**
  거기 없는 세부 내용일 때만 ask_material로 원본을 펼칩니다.
- 원본 PDF는 지우지 않습니다. 사장님이 직접 정리하십니다.
```

- [ ] **Step 5: 통과를 확인한다**

Run: `PYTHONUTF8=1 python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add assistant/brain.py tests/test_brain.py
git commit -m "feat: 학업 자료 도구 4개를 비서에 연결"
```

---

### Task 7: 워크플로 문서와 전체 검증

**Files:**
- Modify: `workflows/ai-assistant.md`
- Modify: `docs/superpowers/specs/2026-07-30-study-materials-design.md`

**Interfaces:**
- Consumes: Task 1~6 전부
- Produces: 없음 (문서)

- [ ] **Step 1: 워크플로에 사용법을 더한다**

`## 쓰는 법` 절의 예시 목록에 세 줄을 더한다:

```
  새로 넣은 자료 정리해줘
  그 논문 3장 예시가 뭐였지?
  정리해둔 자료 목록 보여줘
```

`## 고장났을 때` 표에 세 줄을 더한다:

```
| `설정 오류: OBSIDIAN_VAULT...` | `.env`의 볼트 경로. 옵시디언에서 볼트 폴더 위치를 확인한다 |
| 자료를 넣었는데 안 보임 | 자료넣는곳 **바로 아래**에 있는지. 하위 폴더는 안 본다. `.pdf`가 맞는지 |
| "너무 큽니다 / 쪽수가 많습니다" | 한 번에 32MB·600쪽까지. 자료를 나눠서 넣는다 |
```

- [ ] **Step 2: 설계 문서에 계획 단계 변경을 반영한다**

`## 4. 비서가 쓸 도구` 표를 도구 4개로 고치고, `materials.json` 예시에서 `pages`를
지운다. 그 자리에 한 줄만 남긴다:

```
`tool_result`에는 문서 블록을 넣을 수 없어 도구가 직접 API를 부른다.
`pages`는 미리 세려면 PDF 라이브러리가 필요해 뺐다 (계획서 참고).
```

- [ ] **Step 3: 전체 테스트**

Run: `PYTHONUTF8=1 python -m pytest -q`
Expected: 전부 PASS (기존 114 + 새 28 안팎)

- [ ] **Step 4: 뮤테이션 확인**

아래 넷을 하나씩 되돌려 테스트가 잡는지 본다. 하나라도 못 잡으면 그 테스트는
아무것도 지키지 않는 것이다.

| 되돌릴 것 | 잡아야 할 테스트 |
|---|---|
| `_document_block`의 `"type": "document"` → `"text"` | `test_pdf_is_attached_as_a_document_block` |
| `save_material`의 되돌리기 두 줄 삭제 | `test_nothing_is_left_behind_when_the_move_fails` |
| `list_new`의 `path.is_dir()` 건너뛰기 삭제 | `test_skips_already_processed` |
| `_document_block`의 크기 검사 삭제 | `test_oversized_pdf_is_refused_before_spending_money` |

- [ ] **Step 5: 실제로 한 번 써 본다**

`자료넣는곳`에 진짜 PDF를 하나 넣고 서버를 띄운 뒤 텔레그램에서 "새로 넣은 거
정리해줘"라고 한다. 확인할 것:

- 볼트 `학업` 폴더에 노트가 생겼는가
- 노트에 그림·수식 내용이 반영됐는가
- 원본이 `처리완료`로 옮겨졌고 **지워지지 않았는가**
- 이어서 세부 질문을 하면 답하는가

- [ ] **Step 6: 커밋**

```bash
git add workflows/ai-assistant.md docs/superpowers/specs/2026-07-30-study-materials-design.md
git commit -m "docs: 학업 자료 사용법과 고장 대처"
```

---

## 완료 기준

- [ ] `pytest` 전부 통과
- [ ] 뮤테이션 4건 모두 잡힘
- [ ] `RUN_PDF_SEAM=1`로 진짜 PDF 이음매 테스트 통과 — **모델이 그림·수식을 읽어냄**
- [ ] 실제 PDF 한 건을 텔레그램으로 정리해 노트가 생김
- [ ] 원본 PDF가 처리완료에 그대로 있음
- [ ] 새 파이썬 의존성 0개
