"""학업 자료의 파일 쪽 일 — 받은 목록, 노트 쓰기, 원본 이동, 목록 파일.

여기서는 네트워크를 쓰지 않는다. Claude에게 PDF를 보여주는 일은
tools/study_reader.py 가 맡는다. 나눠두면 이 파일의 테스트가 빠르고
결정적이며, API 없이도 전부 돈다.
"""

from __future__ import annotations

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

# 요청 하나의 상한. Claude API 문서 기준 32MB.
MAX_PDF_BYTES = 32 * 1024 * 1024


class StudyError(RuntimeError):
    """자료를 다루지 못했을 때. 메시지는 사장님께 그대로 보여준다."""


def list_new(settings: Settings) -> list[dict]:
    """정리를 기다리는 PDF 목록.

    처리완료 폴더는 건너뛴다 — 그 안은 이미 정리한 것이고, 자료넣는곳
    **안**에 있어서 그냥 훑으면 매번 다시 정리 대상이 된다.

    너무 큰 파일도 목록에는 넣고 표시만 한다. 목록에서 빼버리면 사장님은
    자기가 넣은 파일이 왜 안 보이는지 알 방법이 없다.
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
