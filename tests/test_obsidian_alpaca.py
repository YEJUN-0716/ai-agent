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


# ── 메모리 목차 ─────────────────────────────────────────────────────────
# 분류를 한 줄 요약의 낱말로 추측하던 판이 실제로 틀렸다 — 이미 닫힌 노선이
# '진행 중'에, 채널 출시가 '측정'에 들어갔다. 이제 파일이 분류를 들고 있다.
def _memory_note(dirpath, slug, title, topic, status, body=""):
    (dirpath / f"{slug}.md").write_text(
        f"---\nname: {slug}\ndescription: \"{title}\"\nmetadata:\n"
        f"  node_type: memory\n  type: project\n  topic: {topic}\n"
        f"  status: {status}\n---\n\n{body}\n", encoding="utf-8")


def test_memory_index_groups_by_frontmatter_not_words(monkeypatch, tmp_path):
    """요약에 '실측'이 있어도 topic 이 운영이면 운영으로 간다."""
    mem, vault = tmp_path / "mem", tmp_path / "vault"
    mem.mkdir(); vault.mkdir()
    _memory_note(mem, "ecc-trial", "ecc 플러그인 끔", "운영", "끝남")
    _memory_note(mem, "pead-prereg", "PEAD 사전 등록", "전략", "닫힘",
                 "앞선 판단은 [[ecc-trial]] 참고.")
    (mem / "MEMORY.md").write_text(
        "- [ecc 플러그인 끔](ecc-trial.md) — 적재량 -45% 실측\n"
        "- [PEAD 사전 등록](pead-prereg.md) — 측정 완료, 판정 실패\n",
        encoding="utf-8")

    monkeypatch.setenv("MEMORY_DIR", str(mem))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    b = _bridge(monkeypatch)
    assert b._push_memory() == 2

    index = (vault / "Agent Memory" / "MEMORY.md").read_text(encoding="utf-8")
    운영 = index.split("시스템 운영")[1].split("##")[0]
    assert "ecc 플러그인 끔" in 운영 and "PEAD" not in 운영
    assert "❌ 닫힘" in index and "✅ 끝남" in index


def test_memory_notes_get_korean_names_and_links_follow(monkeypatch, tmp_path):
    """파일명을 한글로 바꾸면 본문의 [[슬러그]] 링크도 같이 바뀌어야 한다."""
    mem, vault = tmp_path / "mem", tmp_path / "vault"
    mem.mkdir(); vault.mkdir()
    _memory_note(mem, "ecc-trial", "ecc 플러그인 끔", "운영", "끝남")
    _memory_note(mem, "pead-prereg", "PEAD 사전 등록", "전략", "닫힘",
                 "앞선 판단은 [[ecc-trial]] 참고.")
    (mem / "MEMORY.md").write_text(
        "- [ecc 플러그인 끔](ecc-trial.md) — x\n"
        "- [PEAD 사전 등록](pead-prereg.md) — y\n", encoding="utf-8")

    monkeypatch.setenv("MEMORY_DIR", str(mem))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    b = _bridge(monkeypatch)
    b._push_memory()

    dest = vault / "Agent Memory"
    assert (dest / "PEAD 사전 등록.md").exists()
    assert not (dest / "pead-prereg.md").exists()      # 옛 이름은 남지 않는다
    assert "[[ecc 플러그인 끔]]" in (dest / "PEAD 사전 등록.md").read_text(
        encoding="utf-8")


def test_memory_without_topic_is_surfaced_not_guessed(monkeypatch, tmp_path):
    """태그가 없으면 어딘가에 끼워넣지 말고 '미분류'로 드러낸다."""
    mem, vault = tmp_path / "mem", tmp_path / "vault"
    mem.mkdir(); vault.mkdir()
    (mem / "untagged.md").write_text(
        "---\nname: untagged\nmetadata:\n  type: project\n---\n\n본문\n",
        encoding="utf-8")
    (mem / "MEMORY.md").write_text("- [태그 없는 메모](untagged.md) — z\n",
                                   encoding="utf-8")

    monkeypatch.setenv("MEMORY_DIR", str(mem))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    b = _bridge(monkeypatch)
    b._push_memory()
    assert "미분류" in (vault / "Agent Memory" / "MEMORY.md").read_text(
        encoding="utf-8")


def test_pipe_in_summary_does_not_split_the_table(monkeypatch, tmp_path):
    """'|t|≤0.6' 같은 요약이 표 칸을 쪼개면 안 된다."""
    mem, vault = tmp_path / "mem", tmp_path / "vault"
    mem.mkdir(); vault.mkdir()
    _memory_note(mem, "verdict", "총괄 판정", "전략", "돌아감")
    (mem / "MEMORY.md").write_text(
        "- [총괄 판정](verdict.md) — 이미 반증됐다(|t|≤0.6)\n", encoding="utf-8")

    monkeypatch.setenv("MEMORY_DIR", str(mem))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    b = _bridge(monkeypatch)
    b._push_memory()
    row = [l for l in (vault / "Agent Memory" / "MEMORY.md").read_text(
        encoding="utf-8").splitlines() if "총괄 판정" in l][0]
    assert r"\|t\|" in row
    assert row.count("|") - row.count(r"\|") == 4   # 칸 3개 = 칸 구분 파이프 4개
