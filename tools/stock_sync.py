"""stock-analyzer 폴더를 최신으로 당겨온다.

왜 필요한가: 가상 브로커는 자택 러너의 작업 폴더에서 돌고, 결과는 깃허브
저장소로 올라간다. 비서가 읽는 곳은 사장님 PC의 stock-analyzer 사본이다.
당겨오지 않으면 비서가 며칠 지난 숫자를 '현재 잔고'라고 답한다 —
조용히 틀린 답이 가장 나쁘다.

읽기 전에 한 번 당기되, 질문마다 네트워크를 때리지 않도록 간격을 둔다.
당기기에 실패해도 읽기를 막지 않는다. 대신 언제 것인지 함께 알려준다.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

from assistant.config import Settings

KST = timezone(timedelta(hours=9))

# 이 시간 안에 이미 당겼으면 다시 당기지 않는다.
REFRESH_INTERVAL_SECONDS = 300
GIT_TIMEOUT_SECONDS = 30

# 프로세스가 사는 동안만 기억한다. 서버를 껐다 켜면 다시 당긴다.
_last_pull_at: datetime | None = None


def _run_git(settings: Settings, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=settings.stock_analyzer_path,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def _last_commit_time(settings: Settings) -> str | None:
    """마지막 커밋 시각. 데이터가 언제 것인지 알려주기 위해 쓴다."""
    try:
        result = _run_git(settings, "log", "-1", "--format=%cI")
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def reset_throttle() -> None:
    """간격 제한을 초기화한다. 테스트 전용."""
    global _last_pull_at
    _last_pull_at = None


def refresh(settings: Settings, force: bool = False) -> dict:
    """stock-analyzer 사본을 최신으로 맞춘다.

    `--ff-only`라서 사장님이 그 폴더에서 따로 작업 중이면 당기지 않고
    그 사실을 알린다. 남의 작업을 덮어쓰지 않는다.

    Returns:
        pulled: 이번에 실제로 당겼는지
        as_of: 데이터의 마지막 커밋 시각 (ISO 8601). 모르면 None
        note: 사용자에게 알려야 할 문제. 없으면 None
    """
    global _last_pull_at

    now = datetime.now(KST)
    if (
        not force
        and _last_pull_at is not None
        and (now - _last_pull_at).total_seconds() < REFRESH_INTERVAL_SECONDS
    ):
        return {
            "pulled": False,
            "as_of": _last_commit_time(settings),
            "note": None,
        }

    try:
        result = _run_git(settings, "pull", "--ff-only", "origin", "main")
    except FileNotFoundError:
        return {
            "pulled": False,
            "as_of": _last_commit_time(settings),
            "note": "git이 설치돼 있지 않아 최신 데이터를 받아오지 못했습니다.",
        }
    except subprocess.TimeoutExpired:
        return {
            "pulled": False,
            "as_of": _last_commit_time(settings),
            "note": "stock-analyzer 최신화가 30초 안에 끝나지 않았습니다.",
        }
    except OSError as exc:
        return {
            "pulled": False,
            "as_of": _last_commit_time(settings),
            "note": f"stock-analyzer 최신화에 실패했습니다: {exc}",
        }

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip().splitlines()
        return {
            "pulled": False,
            "as_of": _last_commit_time(settings),
            "note": (
                "stock-analyzer 폴더를 최신으로 만들지 못했습니다. "
                "아래 숫자는 지난 데이터일 수 있습니다. "
                f"({reason[-1] if reason else '이유 불명'})"
            ),
        }

    _last_pull_at = now
    return {"pulled": True, "as_of": _last_commit_time(settings), "note": None}
