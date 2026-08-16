"""옵시디언 Alpaca 노트가 붙잡아야 하는 두 가지 — 시각과 키 경로.

1. **시각**: Alpaca 는 UTC 로 답하고 사장님은 한국시간으로 읽는다. 여기서
   9시간이 빠지면 "새벽 2시에 매수"처럼 보여서 기록을 못 믿게 된다.
2. **키**: 작업 스케줄러가 부르는 .cmd 는 .env 를 읽지 않는다. 그래서
   stock-analyzer 의 .env 를 직접 읽는 경로가 무인 갱신의 유일한 통로다.
   이게 조용히 끊기면 노트만 안 생기고 push 는 성공했다고 찍힌다.

네트워크 없음.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))


def _bridge(monkeypatch, stock_dir=None):
    """STOCK_DIR 은 import 시점에 굳는다 — 바꾸려면 다시 읽어야 한다."""
    if stock_dir is not None:
        monkeypatch.setenv("STOCK_DIR", str(stock_dir))
    import obsidian_bridge
    return importlib.reload(obsidian_bridge)


# ── 시각 ───────────────────────────────────────────────────────────────
def test_utc_fill_time_becomes_kst(monkeypatch):
    b = _bridge(monkeypatch)
    # 09:30 ET 개장 = 13:30 UTC = 22:30 KST (같은 날 밤).
    assert b._kst("2026-08-11T13:30:00Z") == "08-11 22:30"


def test_nanosecond_timestamps_do_not_crash(monkeypatch):
    # Alpaca 는 나노초 9자리를 붙여 보낸다. fromisoformat 이 여기서 죽는다.
    b = _bridge(monkeypatch)
    assert b._kst("2026-08-11T13:30:00.123456789Z") == "08-11 22:30"


def test_missing_time_is_a_dash(monkeypatch):
    b = _bridge(monkeypatch)
    assert b._kst("") == "—"
    assert b._kst("깨진값") == "깨진값"[:16]


# ── 키 ─────────────────────────────────────────────────────────────────
def test_keys_come_from_stock_analyzer_env_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "# 주석\nALPACA_API_KEY=PK123\nALPACA_SECRET_KEY=sec456\n", encoding="utf-8")
    b = _bridge(monkeypatch, tmp_path)
    assert b._alpaca_keys() == ("PK123", "sec456")


def test_environment_wins_over_the_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ALPACA_API_KEY=old\nALPACA_SECRET_KEY=old\n",
                                   encoding="utf-8")
    monkeypatch.setenv("ALPACA_API_KEY", "new")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "newsec")
    b = _bridge(monkeypatch, tmp_path)
    assert b._alpaca_keys() == ("new", "newsec")


def test_no_keys_anywhere_is_empty_not_an_error(monkeypatch, tmp_path):
    # 키가 없으면 그 노트만 건너뛴다. push 전체가 죽으면 메모리·성적표까지 멈춘다.
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    b = _bridge(monkeypatch, tmp_path / "없는폴더")
    assert b._alpaca_keys() == ("", "")
    assert b._push_alpaca() == 0


@pytest.fixture(autouse=True)
def _restore_module(monkeypatch):
    """다른 테스트가 기본 경로의 브리지를 보게 되돌린다."""
    yield
    monkeypatch.undo()
    import obsidian_bridge
    importlib.reload(obsidian_bridge)


# ── 판정 ───────────────────────────────────────────────────────────────
# 목록 노트가 리포트를 열지 않고 결과를 보여주는 근거. 여기가 틀리면
# 사장님이 실패한 측정을 통과로 읽는다 — 실제로 한 번 그렇게 났다.
def test_verdict_reads_only_the_heading(monkeypatch):
    """판정표 머리글 '통과선' 이 실패를 통과로 뒤집으면 안 된다."""
    b = _bridge(monkeypatch)
    report = (
        "# F-Score 롱숏\n\n"
        "## 판정: **실패** (①X AND ②X)\n\n"
        "| | 무엇 | 통과선 | 실측 |\n"
        "|---|---|---|---|\n"
        "| ① | 상위 분위 초과수익 | t ≥ +2 | t=-0.4 |\n"
    )
    assert b._verdict(report) == "❌ 실패"


def test_verdict_labels(monkeypatch):
    b = _bridge(monkeypatch)
    assert b._verdict("## 판정: **통과** (①O AND ②O)\n") == "✅ 통과"
    assert b._verdict("## 판정: **검출력 부족 — 미측정**\n") == "⚪ 미측정"
    # 서술로 적은 초기 리포트는 한 낱말로 줄이지 않는다.
    assert b._verdict("## 판정\n\n**두 항목 모두 구분 불가.** 통과 근거 없음.\n") == "📄 서술형"
    assert b._verdict("# 비용 민감도\n\n표만 있는 리포트.\n") == "—"
