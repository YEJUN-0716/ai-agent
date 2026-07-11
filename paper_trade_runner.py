"""
페이퍼 트레이딩 자동 실행 스크립트
=================================================================
GitHub Actions cron으로 매일 미국 장마감 후 자동 실행.
팩터 스코어·시장 레짐 로직은 modules/factor_engine.py 참조.
Alpaca 주문 함수는 modules/paper_trading.py 참조.

환경변수:
  ALPACA_API_KEY        Alpaca API Key                (필수)
  ALPACA_SECRET_KEY     Alpaca Secret                 (필수)
  ALPACA_MODE           paper(기본) 또는 live ⚠️      (선택)
  TELEGRAM_TOKEN        텔레그램 봇 토큰              (선택)
  TELEGRAM_CHAT_ID      텔레그램 채팅 ID              (선택)
  UNIVERSE              유니버스 이름                  (기본: 'S&P 500 대형 30')
  TOP_N                 매수 상위 N개                  (기본: 5)
  CAPITAL_PER_TRADE     종목당 투입금 USD              (기본: 1000)
  MAX_POSITIONS         최대 동시 포지션 수 (bull 기준) (기본: 10)
  NEUTRAL_MAX_POSITIONS neutral 레짐 최대 포지션 수    (기본: MAX_POSITIONS*0.7)
  BEAR_MAX_POSITIONS    bear 레짐 최대 포지션 수       (기본: MAX_POSITIONS*0.4)
  DRY_RUN               true면 주문 안 냄              (기본: false)
  TRAIL_STOP_PCT        트레일링 스톱 %                (기본: 10)
  BUY_SCORE_MIN         최소 매수 점수                 (기본: 60)
  PORTFOLIO_DD_STOP_PCT 포트폴리오 드로다운 한도 %    (기본: 15, 0이면 비활성)
  MAX_SECTOR_POSITIONS  동일 섹터 최대 보유 종목 수    (기본: 2)
"""
import json
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from modules.factor_engine import (
    REGIME_WEIGHTS, TARGET_VOL_PCT,
    get_market_regime, calc_factor_scores, generate_signals,
    fetch_returns_matrix,
)
from modules.portfolio_allocator import (
    correlation_penalty_scale,
    risk_parity_position_scale,
    portfolio_correlation_report,
)
from modules.paper_trading import (
    place_notional_buy as _pm_notional_buy,
    place_market_sell  as _pm_market_sell,
    wait_for_fill      as _pm_wait_fill,
)

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
# 레짐별 최대 포지션 수 — bear에서 노출을 자동 축소해 하락 충격 완충
_REGIME_MAX_POS: dict[str, int] = {
    "bull":    MAX_POSITIONS,
    "neutral": int(os.environ.get("NEUTRAL_MAX_POSITIONS",
                                  str(max(1, round(MAX_POSITIONS * 0.7))))),
    "bear":    int(os.environ.get("BEAR_MAX_POSITIONS",
                                  str(max(1, round(MAX_POSITIONS * 0.4))))),
}
DRY_RUN        = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
TRAIL_STOP_PCT = float(os.environ.get("TRAIL_STOP_PCT", "10"))
BUY_SCORE_MIN  = float(os.environ.get("BUY_SCORE_MIN", "60"))
PORTFOLIO_DD_STOP_PCT = float(os.environ.get("PORTFOLIO_DD_STOP_PCT", "15"))
MAX_SECTOR_POSITIONS  = int(os.environ.get("MAX_SECTOR_POSITIONS", "2"))
# 상관관계 기반 포지션 사이징 방식: "vol_corr"(기본) 또는 "risk_parity"
SIZING_METHOD = os.environ.get("SIZING_METHOD", "vol_corr")

_ALPACA_MODE = os.environ.get("ALPACA_MODE", "paper").strip().lower()
_ALPACA_BASE = ("https://api.alpaca.markets" if _ALPACA_MODE == "live"
                else "https://paper-api.alpaca.markets")

PEAK_FILE        = "peak_prices.json"
SIGNAL_LOG_FILE  = "signal_log.json"
EQUITY_LOG_FILE  = "equity_log.json"
SIGNAL_HOLD_DAYS = 21

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


# ── Alpaca 헬퍼 (환경변수 바인딩) ─────────────────────────────────
def _h():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def alpaca_get(path, params=None):
    r = requests.get(f"{_ALPACA_BASE}{path}", headers=_h(), params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def alpaca_post(path, body):
    r = requests.post(f"{_ALPACA_BASE}{path}", headers=_h(), json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def place_buy(symbol: str, notional_usd: float) -> dict:
    return _pm_notional_buy(symbol, notional_usd, ALPACA_KEY, ALPACA_SECRET, dry_run=DRY_RUN)


def place_sell(symbol: str, qty: str) -> dict:
    return _pm_market_sell(symbol, qty, ALPACA_KEY, ALPACA_SECRET, dry_run=DRY_RUN)


# ── 고점·시그널 로그 ─────────────────────────────────────────────
def load_peak_prices() -> dict:
    if os.path.exists(PEAK_FILE):
        try:
            with open(PEAK_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_peak_prices(peaks: dict) -> None:
    with open(PEAK_FILE, "w") as f:
        json.dump(peaks, f, indent=2, sort_keys=True)


def load_signal_log() -> list:
    if os.path.exists(SIGNAL_LOG_FILE):
        try:
            with open(SIGNAL_LOG_FILE) as f:
                return json.load(f).get("signals", [])
        except Exception:
            return []
    return []


def save_signal_log(signals: list) -> None:
    with open(SIGNAL_LOG_FILE, "w") as f:
        json.dump({"signals": signals[-300:]}, f, indent=2)


# ── 자산 로그 (P&L 추적) ─────────────────────────────────────────
def load_equity_log() -> list:
    if os.path.exists(EQUITY_LOG_FILE):
        try:
            with open(EQUITY_LOG_FILE) as f:
                return json.load(f).get("records", [])
        except Exception:
            return []
    return []


def save_equity_log(records: list) -> None:
    with open(EQUITY_LOG_FILE, "w") as f:
        json.dump({"records": records[-504:]}, f, indent=2)  # 최대 2년(504 거래일)


def append_equity_log(records: list, equity: float, spy_price: float | None,
                       positions: list) -> list:
    """오늘 날짜 항목이 없으면 추가, 이미 있으면 업데이트."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
    entry = {
        "date":           today,
        "equity":         round(equity, 2),
        "spy_price":      round(spy_price, 2) if spy_price else None,
        "unrealized_pnl": round(unrealized, 2),
        "n_positions":    len(positions),
    }
    if records and records[-1]["date"] == today:
        records[-1] = entry
    else:
        records.append(entry)
    return records


def calc_performance_metrics(records: list) -> dict:
    """샤프 비율, 최대 드로다운, 총 수익률, SPY 대비 알파 계산."""
    if len(records) < 5:
        return {}
    equities   = [r["equity"]    for r in records]
    spy_prices = [r.get("spy_price") for r in records]

    # 일별 수익률
    port_rets = [(equities[i] / equities[i-1] - 1) for i in range(1, len(equities))]
    mean_r, std_r = float(np.mean(port_rets)), float(np.std(port_rets))
    sharpe = (mean_r / (std_r + 1e-9)) * np.sqrt(252) if std_r > 0 else 0.0

    # 최대 드로다운
    peak_e, max_dd = equities[0], 0.0
    for e in equities:
        peak_e = max(peak_e, e)
        max_dd = min(max_dd, (e / peak_e - 1) * 100)

    # 총 수익률
    total_ret = (equities[-1] / equities[0] - 1) * 100

    # SPY 수익률 (유효 데이터만)
    spy_valid = [p for p in spy_prices if p]
    spy_ret   = (spy_valid[-1] / spy_valid[0] - 1) * 100 if len(spy_valid) >= 2 else 0.0

    return {
        "total_return": round(total_ret, 2),
        "spy_return":   round(spy_ret, 2),
        "alpha":        round(total_ret - spy_ret, 2),
        "sharpe":       round(sharpe, 2),
        "max_dd":       round(max_dd, 2),
        "n_days":       len(records),
    }


def check_portfolio_drawdown(equity_now: float, records: list) -> tuple[bool, float]:
    """계좌 고점 대비 드로다운이 PORTFOLIO_DD_STOP_PCT 초과하면 (True, dd%) 반환."""
    if PORTFOLIO_DD_STOP_PCT <= 0 or not records:
        return False, 0.0
    peak = max(r["equity"] for r in records)
    dd   = (equity_now / peak - 1) * 100
    return dd < -PORTFOLIO_DD_STOP_PCT, round(dd, 2)


# ── 섹터 조회 ────────────────────────────────────────────────────
_sector_cache: dict[str, str] = {}


def _get_sector(ticker: str) -> str:
    """yfinance로 섹터 조회 (결과 캐싱). 실패하면 'Unknown'."""
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    try:
        info   = yf.Ticker(ticker).info
        sector = info.get("sector") or "Unknown"
    except Exception:
        sector = "Unknown"
    _sector_cache[ticker] = sector
    return sector


# ── 포지션 reconciliation ─────────────────────────────────────────
def reconcile_positions(peaks: dict, actual_positions: list,
                         alert_fn) -> dict:
    """
    peak_prices.json(시스템 기록) vs 실제 Alpaca 포지션 불일치 감지 및 자동 수정.
    - 시스템O/계좌X: 외부 매도 → peak 항목 삭제
    - 시스템X/계좌O: 외부 매수 → 현재가로 peak 초기화
    """
    actual_syms   = {p["symbol"] for p in actual_positions}
    peak_syms     = set(peaks.keys())
    ghost         = peak_syms - actual_syms   # 시스템엔 있는데 계좌엔 없음
    orphan        = actual_syms - peak_syms   # 계좌엔 있는데 시스템엔 없음

    if not ghost and not orphan:
        return peaks

    pos_map = {p["symbol"]: p for p in actual_positions}

    # Ghost: 시스템엔 있는데 Alpaca 계좌엔 없음 → 외부 매도 가능성 → TG 알림
    if ghost:
        msgs = ["⚠️ *포지션 불일치* (외부 매도 의심)"]
        for sym in ghost:
            msgs.append(f"  `{sym}` 시스템O → Alpaca X (peak 삭제)")
            peaks.pop(sym, None)
        msg = "\n".join(msgs)
        print(msg)
        alert_fn(msg)

    # Orphan: Alpaca엔 있는데 시스템 기록엔 없음 → peak 초기화 (알림 생략)
    # 첫 실행 또는 수동 매수 시 모든 포지션이 orphan으로 잡혀 TG 스팸 방지
    for sym in orphan:
        cur_price = float(pos_map[sym].get("current_price") or
                          pos_map[sym].get("avg_entry_price") or 0)
        if cur_price > 0:
            peaks[sym] = cur_price
            print(f"  [peak 초기화] {sym} @ ${cur_price:.2f}")

    return peaks


# ── 시그널 로그 ──────────────────────────────────────────────────
def append_signals_to_log(new_signals: list, existing_log: list) -> list:
    """매수 시그널을 로그에 추가 (같은 날 중복 제외)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing_ids = {s["id"] for s in existing_log}
    for sig in new_signals:
        if sig.get("action") != "매수":
            continue
        sig_id = f"{sig['symbol']}-{today}"
        if sig_id in existing_ids:
            continue
        existing_log.append({
            "id":           sig_id,
            "symbol":       sig["symbol"],
            "action":       sig["action"],
            "entry_date":   today,
            "entry_price":  sig.get("price", 0.0),
            "score":        sig.get("score", 0.0),
            "rsi":          sig.get("rsi", 50.0),
            "outcome_date":  None,
            "outcome_price": None,
            "return_pct":    None,
        })
    return existing_log


def resolve_signal_outcomes(signal_log: list, prices_cache: dict) -> list:
    """entry_date + SIGNAL_HOLD_DAYS 이상 경과한 미결 시그널의 결과를 기입."""
    today   = datetime.now(timezone.utc).date()
    updated = []
    for sig in signal_log:
        if sig.get("return_pct") is not None:
            updated.append(sig)
            continue
        try:
            entry_date = datetime.strptime(sig["entry_date"], "%Y-%m-%d").date()
        except Exception:
            updated.append(sig)
            continue
        if (today - entry_date).days < SIGNAL_HOLD_DAYS:
            updated.append(sig)
            continue
        sym = sig["symbol"]
        cur = prices_cache.get(sym)
        if cur is None:
            try:
                yahoo_sym = sym.replace(".", "-")
                raw = yf.download(yahoo_sym, period="5d", progress=False, auto_adjust=True)
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.droplevel(1)
                if not raw.empty:
                    cur = float(raw["Close"].dropna().iloc[-1])
                    prices_cache[sym] = cur
            except Exception:
                pass
        if cur and float(sig.get("entry_price", 0)) > 0:
            ret = (cur / float(sig["entry_price"]) - 1) * 100
            sig["outcome_price"] = round(cur, 2)
            sig["outcome_date"]  = str(today)
            sig["return_pct"]    = round(ret, 2)
        updated.append(sig)
    return updated


def signal_log_summary(signal_log: list) -> dict:
    """완료된 시그널의 적중률·평균 수익률 요약."""
    done = [s for s in signal_log if s.get("return_pct") is not None]
    if not done:
        return {"n": 0, "win_rate": 0.0, "avg_return": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    returns = [s["return_pct"] for s in done]
    wins    = [r for r in returns if r > 0]
    losses  = [r for r in returns if r <= 0]
    return {
        "n":          len(done),
        "win_rate":   round(len(wins) / len(done) * 100, 1),
        "avg_return": round(float(np.mean(returns)), 2),
        "avg_win":    round(float(np.mean(wins)),    2) if wins   else 0.0,
        "avg_loss":   round(float(np.mean(losses)),  2) if losses else 0.0,
    }


def check_trailing_stops(positions: list, trail_pct: float = TRAIL_STOP_PCT,
                          peaks: dict = None) -> tuple:
    """고점 대비 trail_pct% 이하로 떨어지면 시장가 매도 (트레일링 스톱).
    반환: (results, 업데이트된 peaks)
    """
    if trail_pct <= 0 or not positions:
        return [], peaks or {}

    if peaks is None:
        peaks = {}

    results = []
    for pos in positions:
        sym       = pos.get("symbol", "")
        qty       = pos.get("qty", "0")
        avg_price = float(pos.get("avg_entry_price", 0) or 0)
        cur_price = float(pos.get("current_price",   0) or 0)
        if avg_price <= 0 or cur_price <= 0 or float(qty) <= 0:
            continue

        prev_peak  = float(peaks.get(sym, avg_price))
        new_peak   = max(prev_peak, avg_price, cur_price)
        peaks[sym] = new_peak

        trail_line = new_peak * (1 - trail_pct / 100)
        gain_pct   = (cur_price / avg_price - 1) * 100

        if cur_price < trail_line:
            print(f"  [트레일링 스톱] {sym}  고점 ${new_peak:.2f} → 현재 ${cur_price:.2f}"
                  f"  ({trail_pct:.0f}% 이상 하락)  손익 {gain_pct:+.1f}%")
            if DRY_RUN:
                results.append({"symbol": sym, "dry_run": True, "gain_pct": gain_pct})
            else:
                try:
                    place_sell(sym, qty)
                    results.append({"symbol": sym, "ok": True, "gain_pct": gain_pct})
                    peaks.pop(sym, None)
                except Exception as e:
                    print(f"  [트레일링 스톱 오류] {sym}: {e}")
                    results.append({"symbol": sym, "error": str(e)})
        else:
            room = (cur_price / trail_line - 1) * 100
            print(f"  {sym}  현재 ${cur_price:.2f}  고점 ${new_peak:.2f}"
                  f"  손익 {gain_pct:+.1f}%  스톱까지 {room:.1f}% 여유")

    return results, peaks


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


# ── ICT/CRT 병렬 분석 ────────────────────────────────────────────
def _calc_ict_batch(tickers: list[str]) -> dict[str, dict]:
    """
    상위 후보 티커의 ICT/CRT 조정 점수를 병렬로 계산.
    반환: {ticker: {"adjustment": int, "signals": list, "crt": dict}}
    """
    from modules.ict_analysis import calc_ict_adjustment

    def _analyze(tk: str) -> tuple[str, dict]:
        try:
            yahoo_sym = tk.replace(".", "-")
            raw = yf.download(yahoo_sym, period="3mo", progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            raw = raw.dropna(subset=["Open", "High", "Low", "Close"])
            if len(raw) < 60:
                return tk, {"adjustment": 0, "signals": [], "crt": {}}
            return tk, calc_ict_adjustment(raw)
        except Exception as e:
            return tk, {"adjustment": 0, "signals": [f"오류: {e}"], "crt": {}}

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_analyze, t): t for t in tickers}
        for fut in as_completed(futs):
            tk = futs[fut]
            try:
                _, res = fut.result()
                results[tk] = res
            except (Exception, SystemExit):
                results[tk] = {"adjustment": 0, "signals": [], "crt": {}}
    return results


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    run_ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode_tag = f"[{_ALPACA_MODE.upper()}]"
    print(f"\n{'='*60}")
    print(f"  페이퍼 트레이딩 실행  {run_ts}  {mode_tag}")
    print(f"  DRY_RUN={DRY_RUN}  UNIVERSE={UNIVERSE_NAME}  TOP_N={TOP_N}")
    print(f"  CAPITAL_PER_TRADE=${CAPITAL_USD}  MAX_POS={MAX_POSITIONS}")
    print(f"  DD_STOP={PORTFOLIO_DD_STOP_PCT}%  MAX_SECTOR={MAX_SECTOR_POSITIONS}")
    print(f"{'='*60}\n")

    if not ALPACA_KEY or not ALPACA_SECRET:
        print("[오류] ALPACA_API_KEY / ALPACA_SECRET_KEY 환경변수가 없습니다.")
        sys.exit(1)

    # 0. 시장 레짐 감지
    print("시장 레짐 감지 중...")
    regime, regime_info = get_market_regime()
    regime_emoji = {"bull": "🐂", "neutral": "😐", "bear": "🐻"}.get(regime, "😐")
    print(f"  레짐: {regime_emoji} {regime.upper()}  "
          f"SPY/200MA={regime_info['spy_ratio']:.3f}  VIX={regime_info['vix']:.1f}")
    effective_max_pos = _REGIME_MAX_POS.get(regime, MAX_POSITIONS)
    if effective_max_pos < MAX_POSITIONS:
        print(f"  ⚠️  {regime.upper()} 레짐 — MAX_POSITIONS {MAX_POSITIONS} → {effective_max_pos} (노출 축소)")

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

    # 1-1. 자산 로그 로드 & 포트폴리오 드로다운 체크 ①
    equity_log   = load_equity_log()
    dd_blocked, dd_pct = check_portfolio_drawdown(equity_now, equity_log)
    if dd_blocked:
        warn = (f"⚠️ 포트폴리오 드로다운 *{dd_pct:.1f}%* "
                f"(한도: -{PORTFOLIO_DD_STOP_PCT:.0f}%) — 신규 매수 중단")
        print(warn); send_tg(warn)

    # 1-2. SPY 현재가 수집 (성과 추적용) ④
    spy_price = None
    try:
        _spy = yf.download("SPY", period="5d", progress=False, auto_adjust=True)
        if isinstance(_spy.columns, pd.MultiIndex):
            _spy.columns = _spy.columns.droplevel(1)
        if not _spy.empty:
            spy_price = float(_spy["Close"].dropna().iloc[-1])
    except Exception:
        pass

    # 2. 현재 보유 포지션
    try:
        positions = alpaca_get("/v2/positions")
    except Exception as e:
        print(f"[경고] 포지션 조회 실패: {e}")
        positions = []

    held = {p["symbol"]: p for p in positions}
    print(f"현재 보유 {len(held)}개: {list(held.keys())}")

    # 2-1. peak_prices 로드 & 포지션 reconciliation ⑦
    peaks = load_peak_prices()
    if positions:
        peaks = reconcile_positions(peaks, positions, send_tg)

    # 2-2. 트레일링 스톱 체크
    sell_results, sell_done = [], set()
    if positions and TRAIL_STOP_PCT > 0:
        print(f"\n트레일링 스톱 점검 ({TRAIL_STOP_PCT:.0f}%)...")
        _stop_fired, peaks = check_trailing_stops(positions, peaks=peaks)
        for r in _stop_fired:
            if r.get("ok") or r.get("dry_run"):
                sell_results.append({"symbol": r["symbol"], "ok": True,
                                     "reason": f"트레일링 스톱 {r.get('gain_pct', 0):+.1f}%"})
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
    seen: set[str] = set()
    tickers = [t for t in raw_tickers
               if not t.endswith(".KS") and not t.endswith(".KQ")
               and t not in seen and not seen.add(t)]  # type: ignore[func-returns-value]
    print(f"\n유니버스: {UNIVERSE_NAME} → {len(tickers)}개 (중복 제거 후)")

    # 4. 팩터 스코어
    print(f"팩터 스코어 계산 중... 레짐={regime.upper()} (2~4분)")
    factor_df = calc_factor_scores(tickers, regime=regime)
    if factor_df.empty:
        print("[경고] 팩터 스코어 계산 실패 — 오늘 실행 건너뜀")
        send_tg("페이퍼 트레이딩: 팩터 스코어 계산 실패, 오늘 실행 건너뜀")
        sys.exit(0)
    print(f"  팩터 상위 5개: {factor_df.head(5)['ticker'].tolist()}")

    # 4-1. ICT/CRT 진입 품질 보정 (상위 후보에 한해 병렬 분석)
    ict_top_n = min(max(TOP_N * 3, 15), len(factor_df))
    print(f"ICT/CRT 분석 중 (상위 {ict_top_n}개)...")
    ict_data: dict[str, dict] = {}
    try:
        ict_data = _calc_ict_batch(factor_df.head(ict_top_n)["ticker"].tolist())
    except Exception as _ict_err:
        print(f"[경고] ICT 배치 분석 실패 — 조정 없이 계속: {_ict_err}")

    # ICT 시그널 참고용 로그 (composite 점수에는 반영 안 함 — factor_engine에서 이미 처리)
    for _tk, _res in ict_data.items():
        _sigs = _res.get("signals", [])
        if _sigs:
            _sig_str = " / ".join(_sigs[:2])
            print(f"  ICT 참고 [{_tk}] {_sig_str}")
    print(f"  팩터 상위 5개: {factor_df.head(5)['ticker'].tolist()}")

    # 5. 시그널 생성
    signals = generate_signals(factor_df, TOP_N, min_score=BUY_SCORE_MIN, regime=regime)
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
                place_sell(sym, qty)
                sell_results.append({"symbol": sym, "qty": qty, "ok": True})
                sell_done.add(sym)
            except Exception as e:
                print(f"  [매도 오류] {sym}: {e}")
                sell_results.append({"symbol": sym, "error": str(e)})

    # 매도 완료 후 buying_power 갱신
    if sell_done and not DRY_RUN:
        try:
            _acct2 = alpaca_get("/v2/account")
            buying_power = float(_acct2.get("buying_power", buying_power))
            print(f"매도 후 갱신 매수여력: ${buying_power:,.2f}")
        except Exception as _e:
            print(f"[경고] 매도 후 buying_power 갱신 실패: {_e}")

    # 4-2. 상관관계 기반 포지션 사이징 계산
    corr_scale = pd.Series(dtype=float)
    try:
        _corr_universe = sorted(set([s["ticker"] for s in buy_sigs]) | set(held))
        if len(_corr_universe) >= 2:
            _returns_mat = fetch_returns_matrix(_corr_universe, lookback_days=60)
            if not _returns_mat.empty:
                if SIZING_METHOD == "risk_parity":
                    corr_scale = risk_parity_position_scale(_returns_mat)
                else:
                    corr_scale = correlation_penalty_scale(_returns_mat)
                _corr_report = portfolio_correlation_report(_returns_mat, corr_threshold=0.7)
                if _corr_report["high_corr_pairs"]:
                    for _pair in _corr_report["high_corr_pairs"][:3]:
                        print(f"  [상관 경고] {_pair['ticker_a']}↔{_pair['ticker_b']}: "
                              f"{_pair['correlation']:.2f}")
    except Exception as _corr_err:
        print(f"  [경고] 상관관계 계산 실패 (사이징 축소 없이 진행): {_corr_err}")

    # 7. 매수 (드로다운 스톱 ① / 섹터 제한 ② / 체결 확인 ③)
    remaining  = effective_max_pos - (len(held) - len(sell_done))
    buy_results = []
    n_bought    = 0
    skipped_sector = []

    # C4: cold cache 시 첫 반복에서 len(held) 건의 yfinance 호출이 몰리는 문제 방지
    for _s in held:
        _get_sector(_s)

    # C1: 이번 실행에서 이미 매수한 섹터 카운트 (snapshot인 held와 별개로 추적)
    bought_sectors: dict[str, int] = {}

    for sig in buy_sigs:
        if n_bought >= max(0, remaining):
            break
        if dd_blocked:
            print(f"  [드로다운 스톱] 신규 매수 전면 중단 (dd={dd_pct:.1f}%)")
            break

        sym = _to_alpaca_sym(sig["ticker"])
        if not sym or sym in held:
            continue

        # ② 섹터 집중도 제한
        sym_sector = _get_sector(sym)
        held_in_sector = (
            sum(1 for s in held if s not in sell_done and _get_sector(s) == sym_sector)
            + bought_sectors.get(sym_sector, 0)  # C1: 이번 실행 내 이미 매수한 동일 섹터 수
        )
        if held_in_sector >= MAX_SECTOR_POSITIONS:
            print(f"  [섹터 제한] {sym} ({sym_sector}) 이미 {held_in_sector}개 — 스킵")
            skipped_sector.append(f"{sym}({sym_sector})")
            continue

        if buying_power < CAPITAL_USD * 0.5:
            print(f"  [매수 스킵] 매수여력 부족")
            break

        # 변동성 기반 포지션 사이징
        asset_vol = sig.get("vol_ann", 20.0) / 100
        if asset_vol > 0:
            _size = CAPITAL_USD * TARGET_VOL_PCT / asset_vol
            _size = max(CAPITAL_USD * 0.5, min(CAPITAL_USD * 2.0, _size))
        else:
            _size = CAPITAL_USD

        # 상관관계 페널티 스케일 적용
        _scale = float(corr_scale.get(sig["ticker"], 1.0))
        _size  = round(_size * _scale, 2)

        if buying_power < _size * 0.9:
            print(f"  [매수 스킵] {sym} 매수여력 부족 (필요 ${_size:,.0f}, 가용 ${buying_power:,.0f})")
            continue

        _ict     = ict_data.get(sig["ticker"], {})
        _ict_adj = _ict.get("adjustment", 0)
        _ict_sig = " / ".join(_ict.get("signals", [])[:2])
        print(f"  [매수] {sym} ${_size:,.0f}  "
              f"(스코어 {sig['score']}, ICT {_ict_adj:+d}, RSI {sig['rsi']},"
              f" 변동성 {sig.get('vol_ann', 0):.1f}%, 섹터 {sym_sector})")
        if _ict_sig:
            print(f"    ▶ ICT: {_ict_sig}")
        try:
            result = place_buy(sym, _size)
            buy_rec = {
                "symbol":  sym, "ticker": sig["ticker"], "notional": _size, "ok": True,
                "price":   sig.get("price", 0),
                "score":   sig.get("score", 0),
                "rsi":     sig.get("rsi", 50),
                "sector":  sym_sector,
            }

            # ③ 체결 확인 (실거래 모드에서만)
            if not DRY_RUN and isinstance(result, dict) and result.get("id"):
                fill = _pm_wait_fill(result["id"], ALPACA_KEY, ALPACA_SECRET)
                fill_status = fill.get("status", "unknown")
                buy_rec["fill_status"] = fill_status
                if fill_status == "filled":
                    buy_rec["fill_price"] = float(fill.get("filled_avg_price") or sig.get("price", 0))
                    print(f"    체결 확인 ✅  ${buy_rec['fill_price']:.2f}")
                else:
                    print(f"    ⚠️ 미체결: {fill_status}")
                    buy_rec["ok"] = False

            buy_results.append(buy_rec)
            # cancelled/rejected/expired = 실제 체결 없음 → 슬롯/자금 차감 불필요
            if buy_rec.get("fill_status") not in {"cancelled", "rejected", "expired"}:
                buying_power -= _size
                n_bought += 1
                bought_sectors[sym_sector] = bought_sectors.get(sym_sector, 0) + 1
        except Exception as e:
            print(f"  [매수 오류] {sym}: {e}")
            buy_results.append({"symbol": sym, "error": str(e)})

    # 8. 사후 처리
    # DRY_RUN에서는 실제 매도가 없으므로 고점 기록을 지우지 않음
    if not DRY_RUN:
        for sym in sell_done:
            peaks.pop(sym, None)
    save_peak_prices(peaks)

    sig_log = load_signal_log()
    prices_cache = {
        _to_alpaca_sym(s["ticker"]): float(s.get("price", 0))
        for s in signals if s.get("price")
    }
    sig_log = resolve_signal_outcomes(sig_log, prices_cache)
    sig_log = append_signals_to_log(
        [{"symbol": r["symbol"], "action": "매수",
          "price": r.get("fill_price", r.get("price", 0)),
          "score": r.get("score", 0),
          "rsi":   r.get("rsi", 50)} for r in buy_results if r.get("ok")],
        sig_log
    )
    save_signal_log(sig_log)
    sl_summary = signal_log_summary(sig_log)

    # ④ 자산 로그 업데이트
    equity_log = append_equity_log(equity_log, equity_now, spy_price, positions)
    save_equity_log(equity_log)
    perf = calc_performance_metrics(equity_log)

    # 9. 요약 & 텔레그램
    n_sells   = sum(1 for r in sell_results if r.get("ok"))
    n_buys    = sum(1 for r in buy_results  if r.get("ok"))
    n_errs    = sum(1 for r in sell_results + buy_results if "error" in r)
    n_pending = sum(1 for r in buy_results
                    if not r.get("ok") and r.get("fill_status") in {"timeout", "unknown"})
    _trail_sells = [r["symbol"] for r in sell_results
                    if r.get("ok") and "트레일링" in r.get("reason", "")]

    lines = [
        f"*페이퍼 트레이딩* `{run_ts}` {mode_tag}",
        f"{'[DRY-RUN]' if DRY_RUN else '[실제 주문]'}  자산 `${equity_now:,.0f}`",
        f"{regime_emoji} 레짐: *{regime.upper()}*  SPY/MA={regime_info['spy_ratio']:.3f}  VIX={regime_info['vix']:.1f}"
        + (f"  (MAX_POS→{effective_max_pos})" if effective_max_pos < MAX_POSITIONS else ""),
        "",
        f"*매도* {n_sells}건: " + (", ".join(r["symbol"] for r in sell_results if r.get("ok")) or "없음"),
        f"*매수* {n_buys}건: " + (", ".join(r["symbol"] for r in buy_results  if r.get("ok")) or "없음"),
    ]
    if _trail_sells:
        lines.append(f"트레일링 스톱 발동: {', '.join(_trail_sells)}")
    if skipped_sector:
        lines.append(f"섹터 제한 스킵: {', '.join(skipped_sector)}")
    if dd_blocked:
        lines.append(f"⛔ 드로다운 *{dd_pct:.1f}%* → 신규 매수 중단")
    if n_errs:
        lines.append(f"오류 {n_errs}건 — Actions 로그 확인")
    if n_pending:
        lines.append(f"⏳ 미확인 주문 {n_pending}건 (timeout/미체결) — Alpaca에서 직접 확인 필요")

    top5 = factor_df.head(5)[["ticker", "composite", "rsi"]].to_dict("records")
    lines.append("\n*팩터 상위 5개*")
    for r in top5:
        tk       = r["ticker"]
        ict_sigs = ict_data.get(tk, {}).get("signals", [])
        ict_tag  = f" (ICT: {ict_sigs[0][:20]})" if ict_sigs else ""
        lines.append(f"  {tk}: 스코어 {r['composite']:.0f}{ict_tag}, RSI {r['rsi']:.0f}")

    # 매수 종목 ICT 시그널 하이라이트
    ict_highlights = []
    for r in buy_results:
        if not r.get("ok"):
            continue
        tk  = r.get("ticker", r.get("symbol", ""))
        res = ict_data.get(tk, {})
        if res.get("signals"):
            ict_highlights.append(f"  `{tk}` {res['signals'][0]}")
    if ict_highlights:
        lines.append("\n*ICT 시그널*")
        lines.extend(ict_highlights[:4])

    # ⑤ 성과 지표
    if perf:
        lines.append(
            f"\n*성과 ({perf['n_days']}일)*\n"
            f"  수익률 {perf['total_return']:+.1f}%  SPY {perf['spy_return']:+.1f}%  "
            f"알파 {perf['alpha']:+.1f}%\n"
            f"  샤프 {perf['sharpe']:.2f}  최대낙폭 {perf['max_dd']:.1f}%"
        )

    if sl_summary["n"] >= 5:
        lines.append(
            f"\n*시그널 적중률* ({sl_summary['n']}건)\n"
            f"  승률 {sl_summary['win_rate']:.0f}%  "
            f"평균수익 {sl_summary['avg_return']:+.1f}%  "
            f"평균수익거래 {sl_summary['avg_win']:+.1f}%  "
            f"평균손실거래 {sl_summary['avg_loss']:+.1f}%"
        )

    msg = "\n".join(lines)
    print("\n" + "─"*50)
    print(msg)
    print("─"*50)
    send_tg(msg)
    print(f"\n완료. 매도 {n_sells}건 / 매수 {n_buys}건 / 오류 {n_errs}건")


if __name__ == "__main__":
    main()
