"""옵시디언 볼트 검색 — 노트를 찾아 읽는다. 읽기 전용.

색인(SQLite FTS·임베딩)을 만들지 않는다. 볼트가 md 수십 개 수준이라
매번 전부 훑어도 눈 깜짝할 새다. 느려지면 그때 색인을 붙인다.

한국어는 조사가 붙는다("어텐션은"). 그래서 단어 단위가 아니라
부분일치로 찾는다.

이 파일은 네트워크도 API도 쓰지 않는다 — 검색은 공짜다.
비서가 유료로 원본 PDF를 펼치기 전에 먼저 여기를 뒤져야 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from assistant.config import Settings

# 볼트 안이지만 사람이 읽는 노트가 아닌 곳. 훑지 않는다.
SKIP_DIRS = frozenset({".obsidian", ".trash", ".git", "__pycache__"})

# 노트 하나에서 읽어들일 상한. 이걸 넘는 파일은 앞부분만 보고 판단한다.
MAX_FILE_BYTES = 512 * 1024

# 아래 셋이 응답 크기를 묶는 장치다. 검색 결과는 그대로 대화에 들어가므로
# 상한이 없으면 볼트가 커질수록 토큰이 샌다.
MAX_LIMIT = 10
SNIPPETS_PER_NOTE = 3
SNIPPET_CHARS = 200

# read_note가 한 번에 돌려줄 본문 상한. 넘으면 잘라내고 잘렸다고 알린다.
MAX_NOTE_CHARS = 20_000


class SearchError(RuntimeError):
    """볼트를 뒤지지 못했을 때. 메시지는 사장님께 그대로 보여준다."""


def _iter_notes(vault: Path) -> Iterator[Path]:
    """볼트 안의 노트를 하나씩. 설정 폴더와 휴지통은 건너뛴다."""
    for path in vault.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.relative_to(vault).parts[:-1]):
            continue
        if path.is_file():
            yield path


def _read(path: Path) -> str:
    """노트 본문. 인코딩이 깨져도 검색은 계속돼야 하므로 대체 문자로 읽는다."""
    data = path.read_bytes()[:MAX_FILE_BYTES]
    return data.decode("utf-8", errors="replace")


def _title(path: Path, text: str) -> str:
    """노트 제목 — 첫 번째 `# 제목`, 없으면 파일 이름."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _rel(vault: Path, path: Path) -> str:
    """비서에게 돌려줄 경로. 볼트 기준 상대경로만 밖으로 낸다."""
    return path.relative_to(vault).as_posix()


def _snippets(text: str, terms: list[str]) -> list[str]:
    """검색어가 걸린 줄들. 어디에 있었는지 사장님이 바로 알아보게."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if any(term in low for term in terms):
            out.append(line[:SNIPPET_CHARS])
            if len(out) >= SNIPPETS_PER_NOTE:
                break
    return out


def _score(text_low: str, title_low: str, terms: list[str]) -> tuple[int, int]:
    """(걸린 검색어 종류 수, 가중 점수). 종류 수가 우선이다.

    검색어를 다 품은 노트가, 한 단어만 여러 번 나오는 노트보다 위여야 한다.
    """
    matched = sum(1 for term in terms if term in text_low)
    if not matched:
        return 0, 0
    title_hits = sum(10 for term in terms if term in title_low)
    occurrences = min(sum(text_low.count(term) for term in terms), 20)
    return matched, matched * 100 + title_hits + occurrences


def search_notes(settings: Settings, query: str, limit: int = 5) -> dict:
    """볼트 전체에서 검색어가 든 노트를 찾는다. 제목과 걸린 줄을 돌려준다."""
    terms = [t for t in query.lower().split() if t]
    if not terms:
        raise SearchError("검색어가 비어 있습니다.")

    vault = settings.obsidian_vault
    if not vault.is_dir():
        raise SearchError(f"볼트 폴더를 찾지 못했습니다: {vault}")

    limit = max(1, min(limit, MAX_LIMIT))

    scored: list[tuple[int, int, dict]] = []
    scanned = 0
    for path in _iter_notes(vault):
        scanned += 1
        try:
            text = _read(path)
        except OSError:
            continue  # 열리지 않는 파일 하나 때문에 검색 전체가 죽지 않는다
        title = _title(path, text)
        matched, score = _score(text.lower(), title.lower(), terms)
        if not matched:
            continue
        scored.append((matched, score, {
            "path": _rel(vault, path),
            "title": title,
            "matched_terms": matched,
            "lines": _snippets(text, terms),
        }))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    matches = [row[2] for row in scored[:limit]]
    return {
        "query": query,
        "scanned_notes": scanned,
        "found": len(scored),
        "matches": matches,
        "note": (
            "볼트에서 찾지 못했습니다." if not matches
            else f"{len(scored)}개 중 상위 {len(matches)}개입니다."
        ),
    }


def resolve_in_vault(settings: Settings, note_path: str) -> Path:
    """볼트 안의 노트 경로로 바꾼다. 볼트 밖이면 거절한다.

    경로는 모델이 정한다. 검증 없이 열면 `../../.env`로 볼트 밖 파일이
    통째로 새어나간다. 심볼릭 링크도 resolve가 실제 위치까지 따라가므로
    볼트 안에 걸어둔 바깥 링크도 여기서 걸린다.
    """
    if not note_path.strip():
        raise SearchError("노트 경로가 비어 있습니다.")

    vault = settings.obsidian_vault.resolve()
    target = (vault / note_path).resolve()
    if not target.is_relative_to(vault):
        raise SearchError(f"볼트 밖의 파일은 열 수 없습니다: {note_path}")
    if target.suffix.lower() != ".md":
        raise SearchError(f"노트(.md)만 열 수 있습니다: {note_path}")
    if not target.is_file():
        raise SearchError(f"그런 노트가 없습니다: {note_path}")
    return target


def read_note(settings: Settings, note_path: str) -> dict:
    """노트 하나를 통째로 읽는다. 검색으로 찾은 경로를 넣는다."""
    target = resolve_in_vault(settings, note_path)
    text = _read(target)
    return {
        "path": _rel(settings.obsidian_vault.resolve(), target),
        "title": _title(target, text),
        "text": text[:MAX_NOTE_CHARS],
        "truncated": len(text) > MAX_NOTE_CHARS,
    }
