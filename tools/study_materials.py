"""학업 자료의 파일 쪽 일 — 받은 목록, 노트 쓰기, 원본 이동, 목록 파일.

여기서는 네트워크를 쓰지 않는다. Claude에게 PDF를 보여주는 일은
tools/study_reader.py 가 맡는다. 나눠두면 이 파일의 테스트가 빠르고
결정적이며, API 없이도 전부 돈다.
"""

from __future__ import annotations

from assistant.config import Settings

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
