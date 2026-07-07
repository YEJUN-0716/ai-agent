"""
페이퍼 트레이딩 자동 실행 스크립트
=================================================================
app.py와 완전 독립 — yfinance + pandas만 사용.
GitHub Actions cron으로 매일 미국 장마감 후 자동 실행.

환경변수:
  ALPACA_API_KEY        Alpaca Paper API Key  (필수)
  ALPACA_SECRET_KEY     Alpaca Paper Secret   (필수)
  TELEGRAM_TOKEN        텔레그램 봇 토큰      (선택)
  TELEGRAM_CHAT_ID      텔레그램 채팅 ID      (선택)
  UNIVERSE              유니버스 이름 (기본: 'S&P 500 대형 30')
  TOP_N                 매수 상위 N개 (기본: 5)
  CAPITAL_PER_TRADE     종목당 투입금 USD (기본: 1000)
  MAX_POSITIONS         최대 동시 포지션 수 (기본: 10)
  DRY_RUN               true면 주문 안 냄 (기본: false)
"""
import os
import sys
import warnings
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

# ── 설정 ────────────────────────────────────────────────────────
ALPACA_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
UNIVERSE_NAME  = os.environ.get("UNIVERSE", "S&P 500 대형 30")
TOP_N          = int(os.environ.get("TOP_N", "5"))
CAPITAL_USD    = float(os.environ.get("CAPITAL_PER_TRADE", "1000"))
MAX_POSITIONS  = int(os.environ.get("MAX_POSITIONS", "10"))
DRY_RUN        = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
STOP_LOSS_PCT  = float(os.environ.get("STOP_LOSS_PCT", "5"))   # 손절 % (0이면 스톱 없음)
BUY_SCORE_MIN  = float(os.environ.get("BUY_SCORE_MIN", "60"))  # 최소 매수 점수 (10~90, 미달 시 관망)

PAPER_BASE = "https://paper-api.alpaca.markets"

# ── 유니버스 ────────────────────────────────────────────────────
UNIVERSE_PRESETS = {
    "S&P 500 대형 30": [
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","BRK-B","JPM","V",
        "JNJ","UNH","XOM","PG","HD","MA","ABBV","MRK","KO","PEP",
        "COST","AVGO","LLY","WMT","MCD","CRM","ADBE","CSCO","ACN","TMO",
    ],
    "NASDAQ 기술주 20": [
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AVGO","ADBE","CRM",
        "AMD","INTC","QCOM","NFLX","PYPL","INTU","AMAT","MU","LRCX","SNPS",
    ],
    "반도체 15": [
        "NVDA","AMD","INTC","TSM","ASML","QCOM","AVGO","MU","LRCX","AMAT",
        "MRVL","ON","NXPI","TXN","KLAC",
    ],
    "배당 귀족 15": [
        "JNJ","PG","KO","PEP","MMM","EMR","ABT","ADP","AFL","SHW",
        "GD","ITW","ED","WMT","MCD",
    ],
}


# ── 팩터 스코어 (순수 pandas/numpy) ──────────────────────────────
def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - 100 / (1 + rs)
    if rsi_series.empty:
        return 50.0
    val = rsi_series.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def _momentum(close: pd.Series) -> dict:
    ret = {}
    for label, days in [("1M", 21), ("3M", 63), ("6M", 126)]:
        if len(close) >= days + 1:
            ret[label] = float((close.iloc[-1] / close.iloc[-days] - 1) * 100)
        else:
            ret[label] = 0.0
    return ret


def calc_factor_scores(tickers: list) -> pd.DataFrame:
    """각 티커의 모멘텀·RSI·변동성으로 팩터 점수(0~100) 계산."""
    end = datetime.now()
    start = end - timedelta(days=200)
    rows = []
    for tk in tickers:
        try:
            raw = yf.download(tk, start=start, end=end, progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            if raw.empty or len(raw) < 60:
                continue
            close = raw["Close"].dropna()
            mom   = _momentum(close)
            rsi   = _rsi(close)
            vol   = float(close.pct_change().tail(20).std() * np.sqrt(252) * 100)  # 연율화 변동성
            rows.append({
                "ticker":   tk,
                "mom_1m":   mom.get("1M", 0),
                "mom_3m":   mom.get("3M", 0),
                "rsi":      rsi,
                "vol_ann":  vol,
                "price":    float(close.iloc[-1]),
            })
        except Exception as e:
            print(f"  [{tk}] 데이터 오류: {e}")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    WEIGHTS = {"mom_3m": 0.40, "mom_1m": 0.30, "low_vol": 0.30}

    # Z-score 정규화
    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)

    # 저변동성 점수 (변동성 낮을수록 좋음)
    mu_v, sigma_v = df["vol_ann"].mean(), df["vol_ann"].std()
    df["z_low_vol"] = -(df["vol_ann"] - mu_v) / (sigma_v + 1e-9)

    df["composite_z"] = (
        df["z_mom_3m"]  * WEIGHTS["mom_3m"]
        + df["z_mom_1m"]  * WEIGHTS["mom_1m"]
        + df["z_low_vol"] * WEIGHTS["low_vol"]
    )
    # 0~100으로 변환
    cmin, cmax = df["composite_z"].min(), df["composite_z"].max()
    df["composite"] = (df["composite_z"] - cmin) / (cmax - cmin + 1e-9) * 80 + 10
    return df.sort_values("composite", ascending=False).reset_index(drop=True)


def generate_signals(factor_df: pd.DataFrame, top_n: int = 5,
                     min_score: float = BUY_SCORE_MIN) -> list:
    """팩터 스코어 기반 매수/매도 시그널 생성.

    top_n: 매수 후보 상한 (점수 상위 N개 안에서만 고려)
    min_score: 이 점수 미만이면 top_n 안이어도 관망 처리 (10~90 척도)
    """
    if factor_df.empty:
        return []
    buy_set  = set(factor_df.head(top_n)["ticker"])
    sell_set = set(factor_df.tail(max(len(factor_df) // 4, 1))["ticker"])
    signals  = []
    for _, row in factor_df.iterrows():
        tk    = row["ticker"]
        score = float(row["composite"])
        rsi   = float(row["rsi"])
        price = float(row["price"])
        if tk in buy_set and rsi < 75 and score >= min_score:
            action = "매수"
        elif tk in sell_set or rsi > 80:
            action = "매도"
        else:
            action = "관망"
        signals.append({
            "ticker": tk, "action": action,
            "score":  round(score, 1), "rsi": round(rsi, 1),
            "price":  price,
        })
    return signals


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


def place_buy(symbol: str, notional_usd: float) -> dict:
    """notional 시장가 매수. 분수 주식은 stop/bracket 미지원이므로 소프트웨어 손절 사용."""
    body = {"symbol": symbol, "notional": str(round(notional_usd, 2)),
            "side": "buy", "type": "market", "time_in_force": "day"}
    return {"dry_run": True, "body": body} if DRY_RUN else alpaca_post("/v2/orders", body)


def check_and_apply_stops(positions: list, stop_pct: float = STOP_LOSS_PCT) -> list:
    """현재가가 평단 대비 stop_pct% 이하로 떨어진 포지션을 즉시 시장가 매도.
    Alpaca 분수 주식은 stop order 미지원 → 크론 실행마다 소프트웨어로 체크.
    """
    if stop_pct <= 0 or not positions:
        return []
    results = []
    for pos in positions:
        sym       = pos.get("symbol", "")
        qty       = pos.get("qty", "0")
        avg_price = float(pos.get("avg_entry_price", 0) or 0)
        cur_price = float(pos.get("current_price",   0) or 0)
        if avg_price <= 0 or cur_price <= 0 or float(qty) <= 0:
            continue
        stop_line = avg_price * (1 - stop_pct / 100)
        if cur_price < stop_line:
            loss_pct = (cur_price / avg_price - 1) * 100
            print(f"  [손절 발동] {sym}  {loss_pct:+.1f}%  "
                  f"현재 ${cur_price:.2f} < 손절선 ${stop_line:.2f}")
            if DRY_RUN:
                results.append({"symbol": sym, "dry_run": True, "loss_pct": loss_pct})
            else:
                try:
                    place_sell(sym, qty)
                    results.append({"symbol": sym, "ok": True, "loss_pct": loss_pct})
                except Exception as e:
                    print(f"  [손절 매도 오류] {sym}: {e}")
                    results.append({"symbol": sym, "error": str(e)})
        else:
            room = (cur_price / stop_line - 1) * 100
            print(f"  {sym}  현재 ${cur_price:.2f}  손절선까지 {room:.1f}% 여유")
    return results


def place_sell(symbol: str, qty: str) -> dict:
    body = {"symbol": symbol, "qty": qty,
            "side": "sell", "type": "market", "time_in_force": "day"}
    return {"dry_run": True, "body": body} if DRY_RUN else alpaca_post("/v2/orders", body)


def _to_alpaca_sym(ticker: str):
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return None
    return ticker.replace("-", ".").upper()


# ── 텔레그램 ────────────────────────────────────────────────────
def send_tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 없음 — 발송 생략")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code == 200:
            print("[TG] 발송 성공 ✅")
        else:
            print(f"[TG 오류] HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[TG 오류] {e}", file=sys.stderr)


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

    # 1. Alpaca 계정 확인
    try:
        acct = alpaca_get("/v2/account")
    except Exception as e:
        print(f"[오류] Alpaca 계정 조회 실패: {e}")
        sys.exit(1)

    equity_now   = float(acct.get("equity", 0))
    buying_power = float(acct.get("buying_power", 0))
    print(f"계정 자산: ${equity_now:,.2f}  매수여력: ${buying_power:,.2f}")

    if acct.get("trading_blocked") or acct.get("account_blocked"):
        msg = "계정이 잠겨 있습니다 — 실행 중단."
        print(msg); send_tg(msg); sys.exit(0)

    # 2. 현재 보유 포지션
    try:
        positions = alpaca_get("/v2/positions")
    except Exception as e:
        print(f"[경고] 포지션 조회 실패: {e}")
        positions = []

    held = {p["symbol"]: p for p in positions}
    print(f"현재 보유 {len(held)}개: {list(held.keys())}")

    # 2-1. 소프트웨어 손절 체크 (분수 주식은 Alpaca stop order 미지원)
    sell_results, sell_done = [], set()
    if positions and STOP_LOSS_PCT > 0:
        print(f"\n손절 기준 점검 ({STOP_LOSS_PCT:.0f}%)...")
        _stop_fired = check_and_apply_stops(positions)
        for r in _stop_fired:
            if r.get("ok") or r.get("dry_run"):
                sell_results.append({"symbol": r["symbol"], "ok": True,
                                     "reason": f"손절 {r.get('loss_pct', 0):+.1f}%"})
                sell_done.add(r["symbol"])

    # 3. 유니버스 (';'로 여러 프리셋 합치기 가능, 중복 제거)
    raw_tickers: list[str] = []
    for _name in UNIVERSE_NAME.split(";"):
        _name = _name.strip()
        _preset = UNIVERSE_PRESETS.get(_name)
        if _preset:
            raw_tickers.extend(_preset)
        else:
            raw_tickers.extend(t.strip().upper() for t in _name.split(",") if t.strip())
    # 순서 유지하며 중복 제거
    seen: set[str] = set()
    tickers = [t for t in raw_tickers
               if not t.endswith(".KS") and not t.endswith(".KQ")
               and t not in seen and not seen.add(t)]  # type: ignore[func-returns-value]
    print(f"\n유니버스: {UNIVERSE_NAME} → {len(tickers)}개 (중복 제거 후)")

    # 4. 팩터 스코어
    print("팩터 스코어 계산 중... (1~3분)")
    factor_df = calc_factor_scores(tickers)
    if factor_df.empty:
        print("[경고] 팩터 스코어 계산 실패 — 오늘 실행 건너뜀")
        send_tg("페이퍼 트레이딩: 팩터 스코어 계산 실패, 오늘 실행 건너뜀")
        sys.exit(0)
    print(f"  상위 5개: {factor_df.head(5)['ticker'].tolist()}")

    # 5. 시그널 생성
    signals = generate_signals(factor_df, TOP_N)
    buy_sigs  = [s for s in signals if s["action"] == "매수"]
    sell_sigs = [s for s in signals if s["action"] == "매도"]
    print(f"시그널: 매수 {len(buy_sigs)}건, 매도 {len(sell_sigs)}건")

    # 6. 매도 (팩터 시그널)
    for sig in sell_sigs:
        sym = _to_alpaca_sym(sig["ticker"])
        if sym and sym in held and sym not in sell_done:
            qty = held[sym].get("qty", "0")
            print(f"  [매도] {sym} {qty}주")
            try:
                res = place_sell(sym, qty)
                sell_results.append({"symbol": sym, "qty": qty, "ok": True})
                sell_done.add(sym)
            except Exception as e:
                print(f"  [매도 오류] {sym}: {e}")
                sell_results.append({"symbol": sym, "error": str(e)})

    # 매도 완료 후 buying_power 갱신 (매도 대금 반영)
    if sell_done and not DRY_RUN:
        try:
            _acct2 = alpaca_get("/v2/account")
            buying_power = float(_acct2.get("buying_power", buying_power))
            print(f"매도 후 갱신 매수여력: ${buying_power:,.2f}")
        except Exception as _e:
            print(f"[경고] 매도 후 buying_power 갱신 실패: {_e}")

    # 7. 매수
    remaining = MAX_POSITIONS - (len(held) - len(sell_done))
    buy_results = []
    n_bought = 0
    for sig in buy_sigs:
        if n_bought >= max(0, remaining):
            break
        sym = _to_alpaca_sym(sig["ticker"])
        if not sym or sym in held:
            continue
        if buying_power < CAPITAL_USD * 0.9:
            print(f"  [매수 스킵] 매수여력 부족")
            break
        print(f"  [매수] {sym} ${CAPITAL_USD:,.0f}  (스코어 {sig['score']}, RSI {sig['rsi']})")
        try:
            res = place_buy(sym, CAPITAL_USD)
            buy_results.append({"symbol": sym, "notional": CAPITAL_USD, "ok": True})
            buying_power -= CAPITAL_USD
            n_bought += 1
        except Exception as e:
            print(f"  [매수 오류] {sym}: {e}")
            buy_results.append({"symbol": sym, "error": str(e)})

    # 8. 요약 & 텔레그램
    n_sells = sum(1 for r in sell_results if r.get("ok"))
    n_buys  = sum(1 for r in buy_results  if r.get("ok"))
    n_errs  = sum(1 for r in sell_results + buy_results if "error" in r)
    _stop_sells = [r["symbol"] for r in sell_results if r.get("ok") and "손절" in r.get("reason", "")]

    lines = [
        f"*페이퍼 트레이딩 실행* `{run_ts}`",
        f"{'[DRY-RUN]' if DRY_RUN else '[실제 주문]'}  자산 `${equity_now:,.0f}`",
        f"손절 기준: {STOP_LOSS_PCT:.0f}% (소프트웨어 — 매일 장마감 후 체크)",
        "",
        f"*매도* {n_sells}건: " + ", ".join(r["symbol"] for r in sell_results if r.get("ok")),
        f"*매수* {n_buys}건: " + ", ".join(r["symbol"] for r in buy_results if r.get("ok")),
    ]
    if _stop_sells:
        lines.append(f"손절 발동: {', '.join(_stop_sells)}")
    if n_errs:
        lines.append(f"오류 {n_errs}건 — Actions 로그 확인")

    top5 = factor_df.head(5)[["ticker","composite","rsi"]].to_dict("records")
    lines.append("\n*팩터 상위 5개*")
    for r in top5:
        lines.append(f"  {r['ticker']}: 스코어 {r['composite']:.0f}, RSI {r['rsi']:.0f}")

    msg = "\n".join(lines)
    print("\n" + "─"*45)
    print(msg)
    print("─"*45)
    send_tg(msg)
    print(f"\n완료. 매도 {n_sells}건 / 매수 {n_buys}건 / 오류 {n_errs}건")


if __name__ == "__main__":
    main()
