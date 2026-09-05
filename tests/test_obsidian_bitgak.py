"""빗각 번인 노트가 붙잡아야 하는 두 가지 — 종료코드와 공허한 체크.

1. **종료코드**: 번인 중 판정은 「미측정」이고 러너는 exit 1 을 낸다. 그걸
   실패로 읽으면 5일 내내 노트가 안 생긴다 — 그런데 push 는 성공으로 찍힌다
   (옛 STOCK_DIR 사고와 같은 침묵).
2. **공허한 체크**: 하루도 안 돌면 "위반 0건"이라 ②③ 이 ok 로 나온다. 참이지만
   시작도 안 한 번인을 3/5 통과로 읽게 만든다.

네트워크 없음 — 러너를 stdout 만 흉내내는 스크립트로 갈아 끼운다.
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

REPORT = {
    "days": [], "verdict": "미측정",
    "sample": {"entry_submitted": 0, "threshold": 15, "ok": False},
    "gate": {"ok": True, "max_abs_err": 9e-13, "n_mismatch": 0},
    "1_orders": {"filled": {"entry": 0, "timeout": 0, "stop": 0, "limit": 0},
                 "causes": {}, "unclassified": 0, "ok": False},
    "2_cutoff": {"late_days": [], "duplicates": [], "ok": True},
    "3_match": {"ghost": [], "missing": [], "blocked_buying_power": 0, "ok": True},
    "4_legs": {"leg_ok": 0, "leg_total": 0, "oversell": 0, "ok": False},
    "5_ops": {"clean_days": 0, "of": 0, "code_interventions": 0,
              "interventions": [], "ok": False},
    "observed": {"close_gap_bp": [], "amends": 0, "amend_failed": 0, "cron_et": []},
}


def _bridge(monkeypatch, tmp_path, report):
    """러너 자리에 stdout 만 흉내내는 가짜를 놓고 볼트를 tmp 로 돌린다."""
    stock = tmp_path / "stock-analyzer"
    (stock / "scripts").mkdir(parents=True)
    src = ["import sys",
           f"sys.stdout.write({json.dumps(json.dumps(report, ensure_ascii=False))})",
           "print()", "print('판정: 미측정')",
           "sys.exit(1)"]          # ← 번인 중 러너의 정상 종료코드
    (stock / "scripts" / "run_bitgak_paper.py").write_text(
        chr(10).join(src), encoding="utf-8")
    monkeypatch.setenv("STOCK_DIR", str(stock))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "vault"))
    import obsidian_bridge
    b = importlib.reload(obsidian_bridge)
    (b.VAULT / b.STOCK_SUB).mkdir(parents=True, exist_ok=True)
    return b


def test_exit_1_still_writes_the_note(monkeypatch, tmp_path):
    b = _bridge(monkeypatch, tmp_path, REPORT)
    home = []
    b._push_bitgak(home)
    note = (b.VAULT / b.STOCK_SUB / "Bitgak Burn-in.md").read_text(encoding="utf-8")
    assert "미측정" in note and "0/5일" in home[0]


def test_no_days_means_no_checkmarks(monkeypatch, tmp_path):
    b = _bridge(monkeypatch, tmp_path, REPORT)
    b._push_bitgak(home := [])
    note = (b.VAULT / b.STOCK_SUB / "Bitgak Burn-in.md").read_text(encoding="utf-8")
    body = note.split("## 판정선", 1)[1]
    # 도구 게이트는 오프라인이라 이미 통과다. 나머지 다섯은 아직 아무것도 아니다.
    assert body.count("✅") == 1, body
    assert home


def test_a_real_day_restores_the_checkmarks(monkeypatch, tmp_path):
    rep = json.loads(json.dumps(REPORT))
    rep["days"] = ["2026-09-08"]
    b = _bridge(monkeypatch, tmp_path, rep)
    b._push_bitgak([])
    note = (b.VAULT / b.STOCK_SUB / "Bitgak Burn-in.md").read_text(encoding="utf-8")
    assert note.split("## 판정선", 1)[1].count("✅") == 3   # gate + ② + ③


def test_missing_runner_is_skipped_not_crashed(monkeypatch, tmp_path):
    b = _bridge(monkeypatch, tmp_path, REPORT)
    (b.STOCK_DIR / "scripts" / "run_bitgak_paper.py").unlink()
    assert b._push_bitgak([]) == 0
