"""
페이퍼 트레이딩 자동 실행 스크립트
=================================================================
GitHub Actions cron으로 매일 미국 장마감 후 자동 실행.
팩터 시그널을 Alpaca Paper Trading 계좌에 실제 주문으로 연결한다.

사용하는 환경변수:
  ALPACA_API_KEY        Alpaca Paper API Key  (필수)
  ALPACA_SECRET_KEY     Alpaca Paper Secret   (필수)
  TELEGRAM_TOKEN        텔레그램 봇 토큰      (선택)
  TELEGRAM_CHAT_ID      텔레그램 채팅 ID      (선택)
  UNIVERSE              유니버스 이름 (기본: 'S&P 500 대형 30')
  TOP_N                 매수 상위 N개 (기본: 5)
  CAPITAL_PER_TRADE     종목당 투입금 USD    (기본: 1000)
  MAX_POSITIONS         최대 동시 포지션 수  (기본: 10)
  DRY_RUN               true 면 주문 안 냄   (기본: false)

⚠️  PAPER_BASE_URL만 사용 — 실거래 api.alpaca.markets 절대 사용 금지
"""
import os
import sys
import json
import warnings
from datetime import datetime, timezone

import requests

warnings.filterwarnings("ignore")

# ── 설정 ────────────────────────────────────────────────────────
ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
UNIVERSE_NAME = os.environ.get("UNIVERSE", "S&P 500 대형 30")
TOP_N         = int(os.environ.get("TOP_N", "5"))
CAPITAL_USD   = float(os.environ.get("CAPITAL_PER_TRADE", "1000"))
MAX_POSITIONS = int(os.environ.get("MAX_POSITIONS", "10"))
DRY_RUN       = os.environ.get("DRY_RUN", "false").strip().lower() == "true"

PAPER_BASE = "https://paper-api.alpaca.markets"


# ── Alpaca 헬퍼 ──────────────────────────────────────────────────
def _h():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def alpaca_get(path, params=None):
    r = requests.get(f"{PAPER_BASE}{path}", headers=_h(), params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def alpaca_post(path, body):
    r = requests.post(f"{PAPER_BASE}{path}", headers=_h(), json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def alpaca_delete(path):
    r = requests.delete(f"{PAPER_BASE}{path}", headers=_h(), timeout=15)
    r.raise_for_status()


def _to_alpaca_sym(ticker: str) -> str | None:
    """yfinance 티커 → Alpaca 심볼. 한국 주식은 None 반환(불가)."""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return None
    return ticker.replace("-", ".").upper()


# ── 텔레그램 ────────────────────────────────────────────────────
def send_tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG 오류] {e}", file=sys.stderr)


# ── 주문 실행 ────────────────────────────────────────────────────
def place_buy(symbol: str, notional_usd: float) -> dict:
    """notional 주문(달러 기준) → 알파카 분수주식 지원."""
    body = {
        "symbol": symbol,
        "notional": str(round(notional_usd, 2)),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    if DRY_RUN:
        return {"dry_run": True, "body": body}
    return alpaca_post("/v2/orders", body)


def place_sell(symbol: str, qty: str) -> dict:
    body = {
        "symbol": symbol,
        "qty": qty,
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
    }
    if DRY_RUN:
        return {"dry_run": True, "body": body}
    return alpaca_post("/v2/orders", body)


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*55}")
    print(f"  페이퍼 트레이딩 실행  {run_ts}")
    print(f"  DRY_RUN={DRY_RUN}  UNIVERSE={UNIVERSE_NAME}  TOP_N={TOP_N}")
    print(f"  CAPITAL_PER_TRADE=${CAPITAL_USD}  MAX_POS={MAX_POSITIONS}")
    print(f"{'='*55}\n")

    if not ALPACA_KEY or not ALPACA_SECRET:
        print("[오류] ALPACA_API_KEY / ALPACA_SECRET_KEY 환경변수가 없습니다.")
        sys.exit(1)

    # 1. 계정 상태 확인
    try:
        acct = alpaca_get("/v2/account")
    except Exception as e:
        print(f"[오류] Alpaca 계정 조회 실패: {e}")
        sys.exit(1)

    equity_now = float(acct.get("equity", 0))
    buying_power = float(acct.get("buying_power", 0))
    print(f"계정 현재 자산: ${equity_now:,.2f}  매수여력: ${buying_power:,.2f}")

    # trading_blocked 또는 account_blocked 상태면 중단
    if acct.get("trading_blocked") or acct.get("account_blocked"):
        msg = f"⛔ 계정이 잠겨 있습니다 (trading_blocked). 실행 중단."
        print(msg)
        send_tg(msg)
        sys.exit(0)

    # 2. 현재 보유 포지션
    try:
        positions = alpaca_get("/v2/positions")
    except Exception as e:
        print(f"[오류] 포지션 조회 실패: {e}")
        positions = []

    held = {p["symbol"]: p for p in positions}
    print(f"현재 보유 포지션 {len(held)}개: {list(held.keys())}")

    # 3. app.py 핵심 함수 임포트 (ScriptRunContext 경고는 정상)
    print("\napp.py 로딩 중 (경고 메시지는 무시)...")
    try:
        import app as core
    except Exception as e:
        print(f"[오류] app.py 임포트 실패: {e}")
        sys.exit(1)

    # 4. 유니버스 결정
    universe_tickers_raw = core.UNIVERSE_PRESETS.get(UNIVERSE_NAME)
    if not universe_tickers_raw:
        # 환경변수에 쉼표 구분 티커 직접 지정한 경우
        universe_tickers_raw = [t.strip().upper() for t in UNIVERSE_NAME.split(",") if t.strip()]
    if not universe_tickers_raw:
        print(f"[오류] 유니버스 '{UNIVERSE_NAME}'을 찾을 수 없습니다.")
        sys.exit(1)

    # 한국 주식 제외 (Alpaca에서 거래 불가)
    universe_tickers = [t for t in universe_tickers_raw
                        if not t.endswith(".KS") and not t.endswith(".KQ")]
    print(f"\n유니버스: {UNIVERSE_NAME} → {len(universe_tickers)}개 티커")

    # 5. 팩터 스코어 계산
    print("팩터 스코어 계산 중... (약 1~3분 소요)")
    try:
        factor_df = core.calc_factor_scores(universe_tickers)
    except Exception as e:
        print(f"[경고] 팩터 스코어 실패: {e} → 섹터 중립 방식 시도")
        try:
            factor_df = core.calc_factor_scores_sectoral(universe_tickers)
        except Exception as e2:
            print(f"[오류] 팩터 스코어 모두 실패: {e2}")
            factor_df = None

    # 6. 시스템 시그널 생성
    print("시스템 시그널 생성 중...")
    try:
        actions, rebal_info = core.generate_system_signals(
            universe_tickers, factor_df=factor_df, top_n=TOP_N, capital=CAPITAL_USD * MAX_POSITIONS
        )
    except Exception as e:
        print(f"[오류] 시그널 생성 실패: {e}")
        sys.exit(1)

    print(f"시그널: 매수 {rebal_info['buy_count']}건, 매도 {rebal_info['sell_count']}건, 관망 {rebal_info['hold_count']}건")

    # 7. 매도 처리 (보유 중이면서 매도 시그널)
    sell_results = []
    sell_tickers_done = set()
    sell_actions = [a for a in actions if "매도" in a["action"] and "조건부" not in a["action"]]

    for act in sell_actions:
        tk = act["ticker"]
        sym = _to_alpaca_sym(tk)
        if sym and sym in held:
            qty = held[sym].get("qty", "0")
            print(f"  [매도] {sym}  {qty}주")
            try:
                res = place_sell(sym, qty)
                sell_results.append({"symbol": sym, "side": "sell", "qty": qty, "result": res})
                sell_tickers_done.add(sym)
            except Exception as e:
                print(f"  [매도 오류] {sym}: {e}")
                sell_results.append({"symbol": sym, "side": "sell", "error": str(e)})

    # 8. 매수 처리
    remaining_slots = MAX_POSITIONS - (len(held) - len(sell_tickers_done))
    buy_actions = [a for a in actions
                   if a["action"].strip() == "🟢 매수"
                   and _to_alpaca_sym(a["ticker"]) not in held]
    buy_actions = buy_actions[:max(0, remaining_slots)]

    buy_results = []
    for act in buy_actions:
        tk = act["ticker"]
        sym = _to_alpaca_sym(tk)
        if not sym:
            continue
        if buying_power < CAPITAL_USD * 0.9:
            print(f"  [매수 스킵] 매수여력 부족 (${buying_power:.0f} < ${CAPITAL_USD:.0f})")
            break
        print(f"  [매수] {sym}  ${CAPITAL_USD:,.0f}")
        try:
            res = place_buy(sym, CAPITAL_USD)
            buy_results.append({"symbol": sym, "side": "buy", "notional": CAPITAL_USD, "result": res})
            buying_power -= CAPITAL_USD
        except Exception as e:
            print(f"  [매수 오류] {sym}: {e}")
            buy_results.append({"symbol": sym, "side": "buy", "error": str(e)})

    # 9. 결과 요약 & 텔레그램 발송
    n_sells = len([r for r in sell_results if "error" not in r])
    n_buys  = len([r for r in buy_results  if "error" not in r])
    n_errs  = len([r for r in sell_results + buy_results if "error" in r])

    lines = [
        f"📊 *페이퍼 트레이딩 실행* `{run_ts}`",
        f"{'🧪 DRY-RUN 모드' if DRY_RUN else '✅ 실제 주문 제출'}",
        f"계좌 자산: `${equity_now:,.0f}`  매수여력: `${buying_power:,.0f}`",
        "",
        f"*매도 완료* {n_sells}건",
    ]
    for r in sell_results:
        mark = "✅" if "error" not in r else "❌"
        lines.append(f"  {mark} {r['symbol']} {r.get('qty','')}주")
    lines.append(f"\n*매수 완료* {n_buys}건")
    for r in buy_results:
        mark = "✅" if "error" not in r else "❌"
        lines.append(f"  {mark} {r['symbol']} `${r.get('notional', 0):,.0f}`")
    if n_errs:
        lines.append(f"\n⚠️ 오류 {n_errs}건 발생 — GitHub Actions 로그 확인")
    lines.append(f"\n_유니버스: {UNIVERSE_NAME} | TOP\\_N: {TOP_N}_")

    tg_msg = "\n".join(lines)
    print("\n" + "─"*40)
    print(tg_msg)
    print("─"*40)
    send_tg(tg_msg)

    print(f"\n완료. 매도 {n_sells}건 / 매수 {n_buys}건 / 오류 {n_errs}건")


if __name__ == "__main__":
    main()
