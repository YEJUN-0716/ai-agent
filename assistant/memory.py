"""대화 기록. 텔레그램과 웹이 같은 기록을 공유한다.

폰에서 물어본 걸 PC에서 이어서 물을 수 있도록 채널은 기록해 두되
읽을 때는 하나의 흐름으로 돌려준다.
"""

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


def append_message(db_path: Path, channel: str, role: str, content: str) -> None:
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
    """최근 limit개를 오래된 것부터 반환한다.

    API에 그대로 실을 수 있는 형태 `[{"role": ..., "content": ...}]`.
    최신 것을 남겨야 하므로 DESC로 자른 뒤 뒤집는다.
    """
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
