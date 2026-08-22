import subprocess

import pytest

from assistant.config import Settings
from tools import stock_sync


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
        study_inbox=tmp_path,
        obsidian_vault=tmp_path,
    )


@pytest.fixture(autouse=True)
def fresh_throttle():
    # 간격 제한은 모듈 전역이라 테스트끼리 새어나간다. 매번 초기화한다.
    stock_sync.reset_throttle()
    yield
    stock_sync.reset_throttle()


class FakeGit:
    """git 호출을 가로채 기록한다."""

    def __init__(self, pull_returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._pull_returncode = pull_returncode
        self._stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if "pull" in cmd:
            return subprocess.CompletedProcess(
                cmd, self._pull_returncode, stdout="", stderr=self._stderr
            )
        # git log -1 --format=%cI
        return subprocess.CompletedProcess(
            cmd, 0, stdout="2026-07-30T07:29:00+09:00\n", stderr=""
        )


def test_pulls_and_reports_data_age(settings, monkeypatch):
    # Arrange
    git = FakeGit()
    monkeypatch.setattr(subprocess, "run", git)

    # Act
    result = stock_sync.refresh(settings)

    # Assert
    assert result["pulled"] is True
    assert result["as_of"] == "2026-07-30T07:29:00+09:00"
    assert result["note"] is None
    assert any("pull" in call for call in git.calls)


def test_second_call_within_the_window_does_not_pull_again(settings, monkeypatch):
    # Arrange — 질문마다 네트워크를 때리면 느리고 비싸다
    git = FakeGit()
    monkeypatch.setattr(subprocess, "run", git)
    stock_sync.refresh(settings)

    # Act
    second = stock_sync.refresh(settings)

    # Assert
    assert sum(1 for call in git.calls if "pull" in call) == 1
    assert second["pulled"] is False


def test_force_ignores_the_window(settings, monkeypatch):
    # Arrange
    git = FakeGit()
    monkeypatch.setattr(subprocess, "run", git)
    stock_sync.refresh(settings)

    # Act
    stock_sync.refresh(settings, force=True)

    # Assert
    assert sum(1 for call in git.calls if "pull" in call) == 2


def test_pull_failure_does_not_raise_but_says_why(settings, monkeypatch):
    # Arrange — 사장님이 그 폴더에서 따로 작업 중인 상황
    git = FakeGit(
        pull_returncode=1,
        stderr="fatal: Not possible to fast-forward, aborting.",
    )
    monkeypatch.setattr(subprocess, "run", git)

    # Act — 실패해도 예외를 던져 읽기를 막지 않는다
    result = stock_sync.refresh(settings)

    # Assert — 대신 왜 최신이 아닌지 반드시 알린다
    assert result["pulled"] is False
    assert result["note"] is not None
    assert "지난 데이터일 수 있습니다" in result["note"]
    assert "fast-forward" in result["note"]


def test_pull_uses_ff_only_so_local_work_is_never_clobbered(settings, monkeypatch):
    # Arrange
    git = FakeGit()
    monkeypatch.setattr(subprocess, "run", git)

    # Act
    stock_sync.refresh(settings)

    # Assert
    pull_call = next(call for call in git.calls if "pull" in call)
    assert "--ff-only" in pull_call


def test_as_of_measures_the_data_not_the_code(settings, monkeypatch):
    # Arrange — 저장소를 합친 뒤로 코드 커밋이 데이터보다 늘 최신이다.
    #           경로 한정이 빠지면 as_of 가 코드 커밋 시각을 가리킨다.
    git = FakeGit()
    monkeypatch.setattr(subprocess, "run", git)

    # Act
    stock_sync.refresh(settings)

    # Assert
    log_call = next(call for call in git.calls if "log" in call)
    assert "--" in log_call
    assert log_call[log_call.index("--") + 1:] == list(stock_sync._DATA_PATHS)


def test_missing_git_is_reported_not_swallowed(settings, monkeypatch):
    # Arrange
    def boom(cmd, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)

    # Act
    result = stock_sync.refresh(settings)

    # Assert
    assert result["pulled"] is False
    assert "git이 설치돼 있지 않아" in result["note"]


def test_timeout_is_reported_not_swallowed(settings, monkeypatch):
    # Arrange — 네트워크가 죽으면 비서가 통째로 멈추면 안 된다
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(subprocess, "run", hang)

    # Act
    result = stock_sync.refresh(settings)

    # Assert
    assert result["pulled"] is False
    assert "30초" in result["note"]
