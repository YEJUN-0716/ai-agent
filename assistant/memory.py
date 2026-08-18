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


def append_exchange(
    db_path: Path, channel: str, question: str, answer: str
) -> None:
    """질문과 답을 한 덩어리로 남긴다.

    두 번에 나눠 저장하면 그 사이에 프로세스가 죽었을 때 짝이 없는 질문 한 줄이
    영구히 남는다. 그러면 이후 load_history가 assistant 메시지로 시작할 수 있고,
    API는 첫 메시지가 user가 아니면 거절한다 — 비서가 통째로 먹통이 된다.
    한 트랜잭션으로 묶어 '둘 다 남거나 둘 다 안 남거나'로 만든다.
    """
    now = datetime.now(KST).isoformat(timespec="seconds")
    with closing(_connect(db_path)) as conn:
        conn.executemany(
            "INSERT INTO messages (channel, role, content, created_at)"
            " VALUES (?, ?, ?, ?)",
            [
                (channel, "user", question, now),
                (channel, "assistant", answer, now),
            ],
        )
        conn.commit()


def load_history(db_path: Path, limit: int) -> list[dict]:
    """최근 limit개를 오래된 것부터 반환한다.

    API에 그대로 실을 수 있는 형태 `[{"role": ..., "content": ...}]`.
    최신 것을 남겨야 하므로 DESC로 자른 뒤 뒤집는다.

    맨 앞의 assistant 메시지는 버린다. API는 첫 메시지가 반드시 user여야 하고,
    아니면 400으로 거절한다 — 사장님 눈에는 비서가 통째로 고장난 것으로 보이고
    DB를 직접 손대기 전에는 풀리지 않는다. 실제로 걸리는 경로가 둘 있다:
    limit이 홀수일 때, 그리고 저장 도중 죽어 짝이 없는 줄이 남았을 때.
    잘린 창의 앞머리를 버리는 것뿐이라 대화 흐름은 그대로다.
    """
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    history = [
        {"role": role, "content": content} for role, content in reversed(rows)
    ]
    first_user = next(
        (i for i, m in enumerate(history) if m["role"] == "user"), len(history)
    )
    return history[first_user:]


def clear_history(db_path: Path) -> int:
    """전체 삭제. 지운 개수를 반환한다."""
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM messages")
        conn.commit()
        return cursor.rowcount
