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
import json
import os
import sys
import warnings
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

try:
    import pandas_ta as _pta
    _PTA_AVAILABLE = True
except ImportError:
    _pta = None
    _PTA_AVAILABLE = False

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
TRAIL_STOP_PCT = float(os.environ.get("TRAIL_STOP_PCT", "10"))  # 고점 대비 트레일링 스톱 % (0이면 비활성)
BUY_SCORE_MIN  = float(os.environ.get("BUY_SCORE_MIN", "60"))  # 최소 매수 점수 (10~90, 미달 시 관망)

# ── 레짐별 팩터 가중치 (5-팩터: 모멘텀·저변동·가치·퀄리티) ──────────
REGIME_WEIGHTS = {
    "bull":    {"mom_3m": 0.35, "mom_1m": 0.25, "low_vol": 0.15, "value": 0.15, "quality": 0.10},
    "neutral": {"mom_3m": 0.25, "mom_1m": 0.20, "low_vol": 0.20, "value": 0.20, "quality": 0.15},
    "bear":    {"mom_3m": 0.10, "mom_1m": 0.05, "low_vol": 0.40, "value": 0.25, "quality": 0.20},
}
TARGET_VOL_PCT = 0.15   # 변동성 기반 사이징: 목표 연율화 변동성 15%

PEAK_FILE        = "peak_prices.json"   # 고점 추적 파일 (리포에 커밋됨)
SIGNAL_LOG_FILE  = "signal_log.json"   # 시그널 적중률 추적 파일 (리포에 커밋됨)
SIGNAL_HOLD_DAYS = 21                  # 결과 판정까지 대기 캘린더 일수

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


# ── 시장 레짐 감지 ─────────────────────────────────────────────────
def get_market_regime() -> tuple:
    """
    SPY 200일 이동평균 + VIX로 시장 레짐 감지.
    - bull    : SPY ≥ 200MA AND VIX < 25
    - bear    : SPY < 97% of 200MA OR VIX > 30
    - neutral : 그 사이
    Returns (regime: str, details: dict)
    """
    spy_ratio, vix_cur = 1.0, 20.0
    try:
        spy = yf.download("SPY", period="1y", progress=False, auto_adjust=True)
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.droplevel(1)
        c = spy["Close"].dropna()
        if len(c) >= 200:
            spy_ratio = float(c.iloc[-1]) / float(c.rolling(200).mean().iloc[-1])
    except Exception as e:
        print(f"  [레짐] SPY 오류: {e}")

    try:
        vix = yf.download("^VIX", period="5d", progress=False, auto_adjust=True)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.droplevel(1)
        vc = vix["Close"].dropna()
        if not vc.empty:
            vix_cur = float(vc.iloc[-1])
    except Exception as e:
        print(f"  [레짐] VIX 오류: {e}")

    if spy_ratio >= 1.0 and vix_cur < 25:
        regime = "bull"
    elif spy_ratio < 0.97 or vix_cur > 30:
        regime = "bear"
    else:
        regime = "neutral"

    return regime, {"spy_ratio": round(spy_ratio, 3), "vix": round(vix_cur, 1)}


# ── 팩터 스코어 (순수 pandas/numpy) ──────────────────────────────
def _rsi(close: pd.Series, period: int = 14) -> float:
    if _PTA_AVAILABLE:
        try:
            result = _pta.rsi(close, length=period)
            if result is not None and not result.empty:
                val = result.iloc[-1]
                return float(val) if pd.notna(val) else 50.0
        except Exception:
            pass
    # fallback
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


def _fetch_fundamentals(tickers: list) -> dict:
    """P/E·영업이익률 수집. 반환: {ticker: {"pe": float, "margin": float}}"""
    result = {}
    for tk in tickers:
        try:
            info = yf.Ticker(tk).info
            pe_raw     = info.get("trailingPE")
            if pe_raw is None:
                pe_raw = info.get("forwardPE")
            margin_raw = info.get("operatingMargins")
            pe     = min(float(pe_raw), 200.0) if pe_raw is not None and pe_raw > 0 else np.nan
            margin = float(margin_raw) * 100   if margin_raw is not None else np.nan
            result[tk] = {"pe": pe, "margin": margin}
        except Exception:
            result[tk] = {"pe": np.nan, "margin": np.nan}
    return result


def calc_factor_scores(tickers: list, regime: str = "neutral") -> pd.DataFrame:
    """
    각 티커의 5-팩터 점수(0~100) 계산.
    팩터: 모멘텀(3M/1M) + 저변동성 + 가치(저PER) + 퀄리티(영업이익률)
    가중치는 레짐(bull/neutral/bear)에 따라 동적 조정.
    """
    W = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["neutral"])

    end   = datetime.now()
    start = end - timedelta(days=200)
    rows  = []
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
            vol   = float(close.pct_change().tail(20).std() * np.sqrt(252) * 100)
            rows.append({
                "ticker":  tk,
                "mom_1m":  mom.get("1M", 0),
                "mom_3m":  mom.get("3M", 0),
                "rsi":     rsi,
                "vol_ann": vol,
                "price":   float(close.iloc[-1]),
            })
        except Exception as e:
            print(f"  [{tk}] 데이터 오류: {e}")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # 기초체력 (P/E, 영업이익률) 수집
    print("  기초체력 데이터 수집 중...")
    fund = _fetch_fundamentals([r["ticker"] for r in rows])
    df["pe"]     = df["ticker"].map(lambda t: fund.get(t, {}).get("pe", np.nan))
    df["margin"] = df["ticker"].map(lambda t: fund.get(t, {}).get("margin", np.nan))

    # Z-score 정규화
    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)

    # 저변동성 (낮을수록 좋음)
    mu_v, sigma_v = df["vol_ann"].mean(), df["vol_ann"].std()
    df["z_low_vol"] = -(df["vol_ann"] - mu_v) / (sigma_v + 1e-9)

    # 가치 (저 P/E → 고점수, NaN이면 중립 0으로)
    pe_valid = df["pe"].dropna()
    if len(pe_valid) >= 3:
        mu_pe, sigma_pe = pe_valid.mean(), pe_valid.std()
        df["z_value"] = (-(df["pe"] - mu_pe) / (sigma_pe + 1e-9)).fillna(0.0)
    else:
        df["z_value"] = 0.0

    # 퀄리티 (높은 영업이익률 → 고점수)
    m_valid = df["margin"].dropna()
    if len(m_valid) >= 3:
        mu_m, sigma_m = m_valid.mean(), m_valid.std()
        df["z_quality"] = ((df["margin"] - mu_m) / (sigma_m + 1e-9)).fillna(0.0)
    else:
        df["z_quality"] = 0.0

    df["composite_z"] = (
        df["z_mom_3m"]  * W["mom_3m"]
        + df["z_mom_1m"]  * W["mom_1m"]
        + df["z_low_vol"] * W["low_vol"]
        + df["z_value"]   * W["value"]
        + df["z_quality"] * W["quality"]
    )
    cmin, cmax = df["composite_z"].min(), df["composite_z"].max()
    df["composite"] = (df["composite_z"] - cmin) / (cmax - cmin + 1e-9) * 80 + 10
    return df.sort_values("composite", ascending=False).reset_index(drop=True)


def generate_signals(factor_df: pd.DataFrame, top_n: int = 5,
                     min_score: float = BUY_SCORE_MIN,
                     regime: str = "neutral") -> list:
    """팩터 스코어 기반 매수/매도 시그널 생성.

    top_n    : 매수 후보 상한 (점수 상위 N개 안에서만 고려)
    min_score: 이 점수 미만이면 top_n 안이어도 관망 처리 (10~90 척도)
    regime   : bear이면 매수 시그널 전면 억제 (하락장 손실 컷)
    """
    if factor_df.empty:
        return []
    buy_set  = set(factor_df.head(top_n)["ticker"])
    sell_set = set(factor_df.tail(max(len(factor_df) // 4, 1))["ticker"])
    signals  = []
    for _, row in factor_df.iterrows():
        tk      = row["ticker"]
        score   = float(row["composite"])
        rsi     = float(row["rsi"])
        price   = float(row["price"])
        vol_ann = float(row.get("vol_ann", 20.0))
        # bear 레짐에서는 매수 억제 (매도/관망만)
        can_buy = tk in buy_set and rsi < 75 and score >= min_score and regime != "bear"
        if can_buy:
            action = "매수"
        elif tk in sell_set or rsi > 80:
            action = "매도"
        else:
            action = "관망"
        signals.append({
            "ticker":  tk, "action": action,
            "score":   round(score, 1), "rsi": round(rsi, 1),
            "price":   price, "vol_ann": round(vol_ann, 1),
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


# ── 시그널 추적 로그 ─────────────────────────────────────────────
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
    """
    entry_date + SIGNAL_HOLD_DAYS 이상 경과한 미결 시그널의 결과를 기입.
    prices_cache: {symbol: float(현재가)} — 이미 수집된 가격 활용.
    """
    today = datetime.now(timezone.utc).date()
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
                raw = yf.download(sym, period="5d", progress=False, auto_adjust=True)
                if not raw.empty:
                    cur = float(raw["Close"].iloc[-1])
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
    wins  = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
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
    진입 직후에는 평단이 곧 고점이므로 일반 손절과 동일하게 작동.
    수익 구간에서는 고점이 올라가며 스톱선도 함께 올라감.
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

        # 고점 = max(저장된 고점, 평단, 현재가)
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

    # 0. 시장 레짐 감지
    print("시장 레짐 감지 중...")
    regime, regime_info = get_market_regime()
    regime_emoji = {"bull": "🐂", "neutral": "😐", "bear": "🐻"}.get(regime, "😐")
    print(f"  레짐: {regime_emoji} {regime.upper()}  "
          f"SPY/200MA={regime_info['spy_ratio']:.3f}  VIX={regime_info['vix']:.1f}")
    if regime == "bear":
        print("  ⚠️  Bear 레짐 — 신규 매수 억제 모드")

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

    # 2-1. 트레일링 스톱 체크 (고점 대비, peak_prices.json에 고점 추적)
    sell_results, sell_done = [], set()
    peaks = load_peak_prices()
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
    # 순서 유지하며 중복 제거
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
    print(f"  상위 5개: {factor_df.head(5)['ticker'].tolist()}")

    # 5. 시그널 생성
    signals = generate_signals(factor_df, TOP_N, regime=regime)
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
        if buying_power < CAPITAL_USD * 0.5:
            print(f"  [매수 스킵] 매수여력 부족")
            break
        # 변동성 기반 포지션 사이징: 목표 연율화 변동성 15%
        asset_vol = sig.get("vol_ann", 20.0) / 100
        if asset_vol > 0:
            _size = CAPITAL_USD * TARGET_VOL_PCT / asset_vol
            _size = max(CAPITAL_USD * 0.5, min(CAPITAL_USD * 2.0, _size))
            _size = round(_size, 2)
        else:
            _size = CAPITAL_USD
        if buying_power < _size * 0.9:
            print(f"  [매수 스킵] {sym} 매수여력 부족 (필요 ${_size:,.0f}, 가용 ${buying_power:,.0f})")
            continue
        print(f"  [매수] {sym} ${_size:,.0f}  "
              f"(스코어 {sig['score']}, RSI {sig['rsi']}, 변동성 {sig.get('vol_ann', 0):.1f}%)")
        try:
            res = place_buy(sym, _size)
            buy_results.append({"symbol": sym, "notional": _size, "ok": True})
            buying_power -= _size
            n_bought += 1
        except Exception as e:
            print(f"  [매수 오류] {sym}: {e}")
            buy_results.append({"symbol": sym, "error": str(e)})

    # 8. 요약 & 텔레그램
    # 매도 종목 peak 제거 후 저장
    for sym in sell_done:
        peaks.pop(sym, None)
    save_peak_prices(peaks)

    # 시그널 로그: 결과 판정 → 신규 매수 로깅
    sig_log = load_signal_log()
    prices_cache = {
        _to_alpaca_sym(s["ticker"]): float(s.get("price", 0))
        for s in signals if s.get("price")
    }
    sig_log = resolve_signal_outcomes(sig_log, prices_cache)
    sig_log = append_signals_to_log(
        [{"symbol": _to_alpaca_sym(s["ticker"]), "action": s["action"],
          "price": s.get("price", 0), "score": s.get("score", 0),
          "rsi": s.get("rsi", 50)} for s in buy_sigs],
        sig_log
    )
    save_signal_log(sig_log)
    sl_summary = signal_log_summary(sig_log)

    n_sells = sum(1 for r in sell_results if r.get("ok"))
    n_buys  = sum(1 for r in buy_results  if r.get("ok"))
    n_errs  = sum(1 for r in sell_results + buy_results if "error" in r)
    _trail_sells = [r["symbol"] for r in sell_results
                    if r.get("ok") and "트레일링" in r.get("reason", "")]

    lines = [
        f"*페이퍼 트레이딩 실행* `{run_ts}`",
        f"{'[DRY-RUN]' if DRY_RUN else '[실제 주문]'}  자산 `${equity_now:,.0f}`",
        f"{regime_emoji} 레짐: *{regime.upper()}*  SPY/MA={regime_info['spy_ratio']:.3f}  VIX={regime_info['vix']:.1f}",
        f"트레일링 스톱: {TRAIL_STOP_PCT:.0f}% (고점 대비)",
        "",
        f"*매도* {n_sells}건: " + ", ".join(r["symbol"] for r in sell_results if r.get("ok")),
        f"*매수* {n_buys}건: " + ", ".join(r["symbol"] for r in buy_results if r.get("ok")),
    ]
    if _trail_sells:
        lines.append(f"트레일링 스톱 발동: {', '.join(_trail_sells)}")
    if n_errs:
        lines.append(f"오류 {n_errs}건 — Actions 로그 확인")

    top5 = factor_df.head(5)[["ticker","composite","rsi"]].to_dict("records")
    lines.append("\n*팩터 상위 5개*")
    for r in top5:
        lines.append(f"  {r['ticker']}: 스코어 {r['composite']:.0f}, RSI {r['rsi']:.0f}")

    if sl_summary["n"] >= 5:
        lines.append(
            f"\n*시그널 적중률* ({sl_summary['n']}건)\n"
            f"  승률 {sl_summary['win_rate']:.0f}%  "
            f"평균수익 {sl_summary['avg_return']:+.1f}%  "
            f"평균수익거래 {sl_summary['avg_win']:+.1f}%  "
            f"평균손실거래 {sl_summary['avg_loss']:+.1f}%"
        )

    msg = "\n".join(lines)
    print("\n" + "─"*45)
    print(msg)
    print("─"*45)
    send_tg(msg)
    print(f"\n완료. 매도 {n_sells}건 / 매수 {n_buys}건 / 오류 {n_errs}건")


if __name__ == "__main__":
    main()
