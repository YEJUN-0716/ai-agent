"""
옵시디언 브리지 — 에이전트 결과를 볼트에 쓴다 (단방향)
========================================================
옵시디언 볼트는 결국 마크다운(.md) 폴더다. 이 도구는 그 폴더에 쓴다.
API·서버 필요 없음.

**볼트는 읽는 곳이다.** 관심종목을 볼트에서 읽어 오던 pull/analyze 는 뺐다
(2026-08-11) — 관심종목 입력은 비서(텔레그램·디스코드)가 `data/assistant/`
에서 관리한다. 같은 목록을 두 곳에서 받으면 어느 쪽이 진짜인지 모르게 된다.

명령
  init   볼트 폴더 + 하위 구조 + Home 노트 생성 (이미 있으면 보존)
  push   우리 → 옵시디언: 에이전트 메모리 + stock-analyzer 결과를 볼트로 복사/렌더
  sync   무인 갱신: stock-analyzer 를 원격에 맞춘 뒤 push (작업 스케줄러용)

경로는 환경변수로 바꾼다 (없으면 아래 기본값):
  OBSIDIAN_VAULT   볼트 폴더            기본: ~/OneDrive/Desktop/ObsidianVault
  STOCK_DIR        stock-analyzer 폴더  기본: ~/stock-analyzer
  MEMORY_DIR       에이전트 메모리 폴더  기본: 이 프로젝트의 .claude 메모리

사용:  python tools/obsidian_bridge.py <init|push|sync>
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
               f"- [[{STOCK_SUB}/Signals|Stock 신호]] · 측정 리포트는 {STOCK_SUB}/Measurements 폴더\n"
               f"- [[{STOCK_SUB}/Scorecard|애널리스트 성적표 — 표본 현황]] — 판정까지 얼마나 왔나\n"
               f"- [[{STOCK_SUB}/Alpaca|Alpaca 체결 기록]] — 실제 매수·매도와 계좌 잔고\n"
               f"- [[{MEMORY_SUB}/MEMORY|에이전트 메모리]]\n\n"
               "`python tools/obsidian_bridge.py push` 로 최신화됩니다.\n")

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


def _alpaca_keys() -> tuple[str, str]:
    """Alpaca 키. 환경변수가 없으면 stock-analyzer 의 .env 에서 읽는다.

    비밀값 사본을 하나로 둔다 — 두 곳에 두면 로테이션한 날 한쪽만 바뀐다.
    작업 스케줄러가 부르는 .cmd 는 .env 를 읽지 않으므로 이 경로가 유일하다.
    """
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if key and secret:
        return key, secret
    env = STOCK_DIR / ".env"
    if not env.exists():
        return "", ""
    found = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("ALPACA_") and "=" in line:
            k, v = line.split("=", 1)
            found[k.strip()] = v.strip()
    return found.get("ALPACA_API_KEY", ""), found.get("ALPACA_SECRET_KEY", "")


def _kst(iso: str) -> str:
    """Alpaca 의 UTC 타임스탬프 → 한국시간 표시.

    나노초 9자리가 붙어 오는 경우가 있어 fromisoformat 이 죽는다. 표시용이라
    초까지만 잘라 쓴다.
    """
    if not iso:
        return "—"
    try:
        dt = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return f"{dt + timedelta(hours=9):%m-%d %H:%M}"
    except ValueError:
        return iso[:16]


def _push_alpaca() -> int:
    """Alpaca 계좌의 **체결 기록**을 노트로 남긴다.

    장부를 따로 만들지 않는다 — 진짜 기록은 브로커에 있다. 우리가 옆에서
    받아 적으면 주문이 거절되거나 부분체결된 날 두 장부가 갈라지고, 그때
    어느 쪽이 맞는지 알 방법이 없다.

    키가 없거나 네트워크가 죽어도 push 전체를 막지 않는다 — 다른 노트는
    로컬 파일이라 멀쩡히 갱신된다.
    """
    dest = VAULT / STOCK_SUB / "Alpaca.md"
    key, secret = _alpaca_keys()
    if not key or not secret:
        print(f"[건너뜀] Alpaca 키 없음 (환경변수 또는 {STOCK_DIR / '.env'})")
        return 0

    sys.path.insert(0, str(STOCK_DIR))
    try:
        from modules import alpaca_trading as at
        account = at.get_account(key, secret)
        positions = at.get_positions(key, secret)
        resp = at._request_with_retry(
            "GET", f"{at.base_url()}/v2/orders",
            headers=at._headers(key, secret),
            params={"status": "closed", "limit": 100, "direction": "desc"},
            timeout=20)
        resp.raise_for_status()
        orders = [o for o in resp.json() if str(o.get("status")) == "filled"]
    except Exception as e:
        print(f"[건너뜀] Alpaca 조회 실패: {e}")
        return 0

    mode = "페이퍼(모의)" if at.is_paper() else "⚠️ 실계좌"
    lines = [f"# 💵 Alpaca 체결 기록", "", MANAGED_TAG, "",
             f"갱신: {datetime.now():%Y-%m-%d %H:%M} · {mode} · 금액 단위 **USD**", "",
             "## 계좌", "",
             f"- 평가액: **${account['equity']:,.2f}**",
             f"- 매수여력: ${account['buying_power']:,.2f}",
             f"- 보유 종목: {len(positions)}개", ""]

    if positions:
        lines += ["## 보유 포지션", "",
                  "| 종목 | 수량 | 평균 매입가 | 현재가 | 평가손익 |", "|---|---|---|---|---|"]
        for p in positions:
            pl = float(p["unrealized_pl"] or 0)
            lines.append(f"| {p['symbol']} | {p['qty']} | ${float(p['avg_entry_price']):,.2f} "
                         f"| ${float(p['current_price']):,.2f} | {pl:+,.2f} |")
        lines.append("")

    lines += [f"## 최근 체결 ({len(orders)}건)", "",
              "| 체결시각(KST) | 종목 | 매매 | 수량 | 체결가 | 금액 | 주문유형 |",
              "|---|---|---|---|---|---|---|"]
    for o in orders:
        qty = float(o.get("filled_qty") or 0)
        price = float(o.get("filled_avg_price") or 0)
        side = "🔵 매수" if o.get("side") == "buy" else "🔴 매도"
        lines.append(f"| {_kst(o.get('filled_at', ''))} | {o.get('symbol', '')} | {side} "
                     f"| {qty:g} | ${price:,.2f} | ${qty * price:,.2f} | {o.get('type', '')} |")
    if not orders:
        lines.append("| — | 아직 체결이 없습니다 | | | | | |")

    _write(dest, "\n".join(lines) + "\n")
    print(f"Alpaca 체결 {len(orders)}건 · 보유 {len(positions)}종목 → {dest}")
    return len(orders)


def cmd_push() -> int:
    VAULT.mkdir(parents=True, exist_ok=True)
    _push_memory()
    _push_measurements()
    _push_signals()
    _push_scorecard()
    _push_alpaca()
    print("push 완료.")
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


COMMANDS = {"init": cmd_init, "push": cmd_push, "sync": cmd_sync}


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
