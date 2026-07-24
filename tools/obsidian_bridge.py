"""
옵시디언 브리지 — 에이전트 ↔ 옵시디언 볼트 양방향 연결
========================================================
옵시디언 볼트는 결국 마크다운(.md) 폴더다. 이 도구는 그 폴더를 읽고 쓴다.
API·서버 필요 없음. 표준 라이브러리만 쓴다.

명령
  init   볼트 폴더 + 하위 구조 + Home/Watchlist 노트 생성 (이미 있으면 보존)
  push   우리 → 옵시디언: 에이전트 메모리 + stock-analyzer 결과를 볼트로 복사/렌더
  pull   옵시디언 → 우리: Watchlist.md 에서 티커를 뽑아 JSON 으로 출력

경로는 환경변수로 바꾼다 (없으면 아래 기본값):
  OBSIDIAN_VAULT   볼트 폴더            기본: ~/OneDrive/Desktop/ObsidianVault
  STOCK_DIR        stock-analyzer 폴더  기본: ~/stock-analyzer
  MEMORY_DIR       에이전트 메모리 폴더  기본: 이 프로젝트의 .claude 메모리

사용:  python tools/obsidian_bridge.py <init|push|pull>
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()

VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT", HOME / "OneDrive" / "Desktop" / "ObsidianVault"))
STOCK_DIR = Path(os.environ.get("STOCK_DIR", HOME / "stock-analyzer"))
MEMORY_DIR = Path(os.environ.get(
    "MEMORY_DIR",
    HOME / ".claude" / "projects"
    / "c--Users-1aass-OneDrive-Desktop-AI-AGENT" / "memory"))

# 볼트 안 폴더 이름 (사람이 옵시디언에서 볼 이름)
MEMORY_SUB = "Agent Memory"
STOCK_SUB = "Stock Analyzer"

# push 가 관리하는(덮어쓰는) 노트 상단에 붙이는 표식.
MANAGED_TAG = "> [!info] 에이전트가 자동 생성/갱신하는 노트입니다. 직접 고쳐도 다음 push 때 덮어써집니다."


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── init ────────────────────────────────────────────────────────────
def cmd_init() -> int:
    VAULT.mkdir(parents=True, exist_ok=True)
    (VAULT / MEMORY_SUB).mkdir(exist_ok=True)
    (VAULT / STOCK_SUB / "Measurements").mkdir(parents=True, exist_ok=True)

    home = VAULT / "Home.md"
    if not home.exists():
        _write(home,
               "# 🏠 Home\n\n"
               "에이전트와 연결된 볼트입니다.\n\n"
               "- [[Watchlist]] — 관심종목 (내가 쓰면 에이전트가 읽음)\n"
               f"- [[{STOCK_SUB}/Signals|Stock 신호]] · 측정 리포트는 {STOCK_SUB}/Measurements 폴더\n"
               f"- [[{MEMORY_SUB}/MEMORY|에이전트 메모리]]\n\n"
               "`python tools/obsidian_bridge.py push` 로 최신화, `pull` 로 관심종목 읽기.\n")

    wl = VAULT / "Watchlist.md"
    if not wl.exists():
        _write(wl,
               "# 📌 Watchlist\n\n"
               "여기에 관심종목을 적으면 에이전트가 `pull` 로 읽어갑니다.\n"
               "한 줄에 하나, `- TICKER` 형식으로 적으세요. `#` 뒤는 메모.\n\n"
               "- AAPL   # 예시\n- NVDA\n- 005930.KS  # 삼성전자\n")

    print(f"볼트 준비 완료: {VAULT}")
    print("옵시디언에서 이 폴더를 'Open folder as vault' 로 열면 됩니다.")
    return 0


# ── push: 우리 → 옵시디언 ────────────────────────────────────────────
def _push_memory() -> int:
    dest = VAULT / MEMORY_SUB
    dest.mkdir(parents=True, exist_ok=True)
    if not MEMORY_DIR.exists():
        print(f"[건너뜀] 메모리 폴더 없음: {MEMORY_DIR}")
        return 0
    n = 0
    for md in MEMORY_DIR.glob("*.md"):
        shutil.copyfile(md, dest / md.name)
        n += 1
    print(f"메모리 {n}개 → {dest}")
    return n


def _push_measurements() -> int:
    src = STOCK_DIR / "docs" / "measurements"
    dest = VAULT / STOCK_SUB / "Measurements"
    dest.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f"[건너뜀] 측정 폴더 없음: {src}")
        return 0
    n = 0
    for md in src.glob("*.md"):
        shutil.copyfile(md, dest / md.name)
        n += 1
    print(f"측정 리포트 {n}개 → {dest}")
    return n


def _push_signals() -> int:
    log = STOCK_DIR / "signal_log.json"
    dest = VAULT / STOCK_SUB / "Signals.md"
    if not log.exists():
        print(f"[건너뜀] 신호 로그 없음: {log}")
        return 0
    try:
        data = json.loads(log.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[건너뜀] 신호 로그 파싱 실패: {e}")
        return 0

    signals = data.get("signals", [])
    recent = sorted(signals, key=lambda s: s.get("entry_date", ""), reverse=True)[:50]
    lines = [f"# 📈 매수 신호 (최근 {len(recent)}건)", "",
             MANAGED_TAG, "",
             f"갱신: {datetime.now():%Y-%m-%d %H:%M}", "",
             "| 날짜 | 종목 | 액션 | 진입가 | 21일 수익률 |",
             "|---|---|---|---|---|"]
    for s in recent:
        ret = s.get("return_pct")
        ret_s = "—" if ret is None else f"{ret:+.1f}%"
        lines.append(f"| {s.get('entry_date','')} | {s.get('symbol','')} "
                     f"| {s.get('action','')} | {s.get('entry_price','')} | {ret_s} |")
    _write(dest, "\n".join(lines) + "\n")
    print(f"신호 {len(recent)}건 → {dest}")
    return len(recent)


def cmd_push() -> int:
    VAULT.mkdir(parents=True, exist_ok=True)
    _push_memory()
    _push_measurements()
    _push_signals()
    print("push 완료.")
    return 0


# ── pull: 옵시디언 → 우리 ────────────────────────────────────────────
def _parse_tickers(text: str) -> list[str]:
    """'- TICKER  # 메모' 형식에서 티커만 추출. 헤더/빈줄/코드펜스 무시."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        token = line[2:].split("#")[0].strip().strip("[]")   # [[AAPL]] 링크도 허용
        if token and all(c.isalnum() or c in ".-" for c in token) and any(c.isalpha() for c in token):
            out.append(token.upper())
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def cmd_pull() -> int:
    wl = VAULT / "Watchlist.md"
    if not wl.exists():
        print(json.dumps({"error": f"Watchlist.md 없음: {wl}", "tickers": []},
                         ensure_ascii=False))
        return 1
    tickers = _parse_tickers(wl.read_text(encoding="utf-8"))
    print(json.dumps({"tickers": tickers, "count": len(tickers)}, ensure_ascii=False))
    return 0


COMMANDS = {"init": cmd_init, "push": cmd_push, "pull": cmd_pull}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(f"사용법: python tools/obsidian_bridge.py <{'|'.join(COMMANDS)}>")
        return 2
    return COMMANDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
