"""
옵시디언 브리지 — 에이전트 ↔ 옵시디언 볼트 양방향 연결
========================================================
옵시디언 볼트는 결국 마크다운(.md) 폴더다. 이 도구는 그 폴더를 읽고 쓴다.
API·서버 필요 없음. 표준 라이브러리만 쓴다.

명령
  init   볼트 폴더 + 하위 구조 + Home/Watchlist 노트 생성 (이미 있으면 보존)
  push   우리 → 옵시디언: 에이전트 메모리 + stock-analyzer 결과를 볼트로 복사/렌더
  pull   옵시디언 → 우리: Watchlist.md 에서 티커를 뽑아 JSON 으로 출력
  sync   무인 갱신: stock-analyzer 를 원격에 맞춘 뒤 push (작업 스케줄러용)

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
from datetime import datetime, timedelta
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
               f"- [[{STOCK_SUB}/Scorecard|애널리스트 성적표 — 표본 현황]] — 판정까지 얼마나 왔나\n"
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


def _push_scorecard() -> int:
    """애널리스트 성적표가 어디까지 왔는지 — 표본 상태만, 네트워크 없이.

    IC·t 값은 여기서 다시 계산하지 않는다. 계산에 276종목 2년치 다운로드가
    필요하기도 하고, 무엇보다 **같은 숫자를 두 곳에서 내면 언젠가 갈라진다.**
    실제 판정은 앱 화면과 텔레그램 채널이 낸다 — 이 노트는 "표본이 어디까지
    찼나" 만 말하고 그쪽을 가리킨다.
    """
    dest = VAULT / STOCK_SUB / "Scorecard.md"
    sys.path.insert(0, str(STOCK_DIR))
    try:
        from modules import analyst_log, analyst_scorecard, publish_log, scorecard_message
    except Exception as e:
        print(f"[건너뜀] 성적표 모듈 로드 실패: {e}")
        return 0

    cwd = os.getcwd()
    try:
        os.chdir(STOCK_DIR)          # 로그 경로가 저장소 기준 상대경로다
        days = analyst_log.load_scoring_days()
        mix = analyst_log.sample_mix()
        published = {h: publish_log.last_published_n(h)
                     for h in analyst_scorecard.HORIZONS}
    except Exception as e:
        print(f"[건너뜀] 성적표 기록 읽기 실패: {e}")
        return 0
    finally:
        os.chdir(cwd)

    if not days:
        print("[건너뜀] 성적표 기록이 비어 있음")
        return 0

    # 슬러그마다 시계가 다르다 — quant·verdict 는 나중에 붙었다.
    first_seen: dict[str, str] = {}
    for day in days:
        for row in day.get("scores", {}).values():
            for slug in row:
                first_seen.setdefault(slug, day["date"])
    tickers = {t for d in days for t in d.get("scores", {})}

    lines = [
        "# 🎓 애널리스트 성적표 — 표본 현황", "", MANAGED_TAG, "",
        f"갱신: {datetime.now():%Y-%m-%d %H:%M}", "",
        "## 표본", "",
        f"- 전체 **{len(days)}일** ({days[0]['date']} ~ {days[-1]['date']}) · "
        f"{len(tickers)}종목",
        f"- 실기록 {mix['live']}일 + 과거 재구성(백필) {mix['backfill']}일",
    ]
    if mix["backfill"]:
        lines.append("- ⚠️ 재구성분은 오늘 살아남은 종목으로 과거를 재므로 "
                     "**생존자 편향**이 남아 있다 — IC 가 실제보다 좋게 나온다.")
    lines += ["", "## 애널리스트별 기록 시작일", "",
              "| 애널리스트 | 첫 기록 |", "|---|---|"]
    for slug, date in sorted(first_seen.items(), key=lambda kv: kv[1]):
        lines.append(f"| {scorecard_message.SLUG_NAMES.get(slug, slug)} | {date} |")

    lines += ["", "## 지평별 마지막 발행", "",
              "| 지평 | 마지막 발행 표본 n |", "|---|---|"]
    for horizon, n in published.items():
        lines.append(f"| {horizon}일 | {'—' if n is None else n} |")

    lines += [
        "", "## 문턱 두 개", "",
        f"- **발행** — 유효표본 ≥ {scorecard_message.MIN_EFFECTIVE_N} "
        "(텔레그램 성적표에 숫자를 싣는 선)",
        f"- **사용가능** — 유효표본 ≥ {analyst_scorecard.DECIDE_MIN_EFFECTIVE_N} "
        f"이고 |t| ≥ {analyst_scorecard.DECIDE_T_THRESHOLD} "
        "(앱 화면 '판정' 칸이 '가능'이 되는 선)",
        "",
        "> 겉보기 표본이 아니라 **유효표본**으로 잰다. 매일 기록하면 예측 구간이 "
        "서로 겹쳐 표본이 실제보다 많아 보인다. 1일 지평만 겹치지 않아 "
        "유효표본 = 겉보기 n 이다.",
        "",
        "> 날짜가 차는 것과 판정이 나는 것은 다르다 — |t| ≥ 2 가 함께 걸려 있어, "
        "알파가 없으면 표본이 아무리 쌓여도 '아직 불가'로 남는다.",
        "",
        f"실제 IC·t 값은 앱 화면(🎓 AI 애널리스트 성적표)과 텔레그램 채널에 있다. "
        f"이 노트는 표본 상태만 옮긴다.",
    ]
    _write(dest, "\n".join(lines) + "\n")
    print(f"성적표 표본 {len(days)}일 → {dest}")
    return len(days)


def cmd_push() -> int:
    VAULT.mkdir(parents=True, exist_ok=True)
    _push_memory()
    _push_measurements()
    _push_signals()
    _push_scorecard()
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


def _fp(ticker: str, value: float) -> str:
    """KRX 는 ₩ 정수, 그 외 $ 소수 2자리."""
    if ticker.endswith((".KS", ".KQ")):
        return f"₩{value:,.0f}"
    return f"${value:.2f}"


def cmd_analyze() -> int:
    """Watchlist 종목을 읽어 트레이드 플랜을 계산하고 볼트에 '관심종목 분석' 노트로 쓴다.

    stock-analyzer 의 가격 로더(price_panel)와 build_trade_plan 을 재사용한다 —
    같은 파이썬 환경에서 실행해야 import 가 된다. 가격 조회는 네트워크를 탄다.
    """
    wl = VAULT / "Watchlist.md"
    if not wl.exists():
        print(f"Watchlist.md 없음: {wl}")
        return 1
    tickers = _parse_tickers(wl.read_text(encoding="utf-8"))
    if not tickers:
        print("Watchlist 에 종목이 없습니다.")
        return 1

    sys.path.insert(0, str(STOCK_DIR))
    try:
        from modules import price_panel
        from modules.trade_plan import MIN_BARS, build_trade_plan
    except Exception as e:
        print(f"[오류] stock-analyzer 모듈 로드 실패 — 같은 파이썬 환경에서 실행하세요: {e}")
        return 1

    end = datetime.now()
    try:
        _, ohlcv = price_panel.load_panel(tickers, end - timedelta(days=420), end)
    except Exception as e:
        print(f"[오류] 가격 데이터 로드 실패: {e}")
        return 1
    ohlcv = ohlcv or {}

    dir_ko = {"long": "🟢 롱", "short": "🔴 숏", "none": "—"}
    rows = ["| 종목 | 방향 | 확신도 | 진입 구간 | 손절 | 목표1 (R:R) | 상태 |",
            "|---|---|---|---|---|---|---|"]
    for tk in tickers:
        df = ohlcv.get(tk)
        if df is None or len(df) < MIN_BARS:
            rows.append(f"| {tk} | — | — | | | | 데이터 부족 |")
            continue
        try:
            p = build_trade_plan(df)
        except Exception as e:
            rows.append(f"| {tk} | — | — | | | | 오류: {e} |")
            continue
        d = dir_ko.get(p["direction"], "—")
        if p["valid"]:
            e0 = p["entry"]
            rows.append(
                f"| {tk} | {d} | {p['confidence']} "
                f"| {_fp(tk, e0['low'])}~{_fp(tk, e0['high'])} | {_fp(tk, p['stop'])} "
                f"| {_fp(tk, p['targets'][0])} (R:R {p['rr'][0]:.1f}) | ✅ 유효 |")
        else:
            rows.append(f"| {tk} | {d} | {p.get('confidence','—')} | | | | {p['reason_invalid']} |")

    note = (f"# 🔍 관심종목 분석\n\n{MANAGED_TAG}\n\n"
            f"갱신: {datetime.now():%Y-%m-%d %H:%M} · {len(tickers)}종목\n\n"
            + "\n".join(rows) + "\n\n"
            "> 자동주문 아님 · 분석용. 숏은 하락추세 + 중간확신 이상만 유효로 잡힙니다.\n")
    dest = VAULT / "관심종목 분석.md"
    _write(dest, note)
    print(f"분석 {len(tickers)}종목 → {dest}")
    return 0


# ── sync: 무인 갱신 ─────────────────────────────────────────────────
def _refresh_stock_repo() -> None:
    """stock-analyzer 를 원격에 맞춘다 — push 가 옛날 파일을 옮기지 않게.

    성적표 기록은 GitHub Actions 러너가 만들어 origin/main 에 커밋한다.
    이 PC 의 사본을 당겨오지 않으면 볼트는 마지막으로 당긴 날에 멈춘다.

    **작업 중일 때는 건드리지 않는다.** 브랜치가 main 이 아니거나 수정 중인
    파일이 있으면 그냥 넘어간다 — 무인 잡이 사람의 작업 트리를 움직이면
    안 된다. 그런 날은 볼트가 조금 옛것이 되지만, 그게 훨씬 싸다.
    """
    import subprocess

    def git(*args):
        return subprocess.run(("git", "-C", str(STOCK_DIR)) + args,
                              capture_output=True, text=True, timeout=120)

    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        dirty = bool(git("status", "--porcelain").stdout.strip())
        if branch != "main" or dirty:
            print(f"[건너뜀] 작업 중이라 pull 생략 (branch={branch}, dirty={dirty})")
            return
        out = git("pull", "--ff-only")
        print(f"pull: {(out.stdout or out.stderr).strip().splitlines()[-1:]}")
    except Exception as e:
        print(f"[건너뜀] pull 실패 — 있는 파일로 진행: {e}")


def cmd_sync() -> int:
    """작업 스케줄러가 부르는 진입점 — 원격 갱신 + push 한 번."""
    print(f"── sync {datetime.now():%Y-%m-%d %H:%M:%S} ──")
    _refresh_stock_repo()
    return cmd_push()


COMMANDS = {"init": cmd_init, "push": cmd_push, "pull": cmd_pull,
            "analyze": cmd_analyze, "sync": cmd_sync}


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
