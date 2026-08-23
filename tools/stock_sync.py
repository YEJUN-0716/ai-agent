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

# as_of 를 재는 대상. 비서가 실제로 읽는 파일들이다(stock_reader 참고).
_DATA_PATHS = (
    "virtual_portfolio.json",
    "equity_log.json",
    "signal_log.json",
    "data",
)

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
    """마지막 커밋 시각. 데이터가 언제 것인지 알려주기 위해 쓴다.

    **읽는 데이터 파일로 한정한다.** 한정하지 않으면 이 값은 코드 커밋 시각이
    된다 — 2026-08-19 저장소를 합치면서 stock-analyzer 가 하위 폴더가 됐고,
    그 뒤로 데이터는 그대로인데 as_of 만 앞서 나갔다(실측 중위 3.6h, 최대
    16.2h). 폴더로 한정해도 같은 문제다. 코드 커밋도 그 폴더 안이다.
    """
    try:
        result = _run_git(settings, "log", "-1", "--format=%cI", "--", *_DATA_PATHS)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def working_tree_busy(path) -> str | None:
    """사람이 그 폴더에서 작업 중이면 이유, 아니면 None.

    **무인 잡이 사람의 작업 트리를 움직이면 안 된다.** 브랜치가 main 이 아니거나
    수정 중인 파일이 있으면 당기지 않는다 — 그런 날은 데이터가 조금 옛것이
    되지만, 남의 작업을 흔드는 것보다 훨씬 싸다.

    2026-08-19 저장소를 합친 뒤로는 당기는 대상이 **비서 자기 소스**이기도 하다.
    가드는 obsidian_bridge 쪽 사본에만 있었고 5분마다 도는 이쪽엔 없었다.

    git 이 없거나 느리면 예외를 그대로 올린다 — 부르는 쪽이 이미 다룬다.
    """
    def git(*args) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        return f"작업 중(브랜치 {branch or '알 수 없음'})이라 최신화를 건너뛰었습니다."
    if git("status", "--porcelain"):
        return "작업 중(수정된 파일 있음)이라 최신화를 건너뛰었습니다."
    return None


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
        busy = working_tree_busy(settings.stock_analyzer_path)
        if busy:
            return {
                "pulled": False,
                "as_of": _last_commit_time(settings),
                "note": f"{busy} 아래 숫자는 지난 데이터일 수 있습니다.",
            }
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
