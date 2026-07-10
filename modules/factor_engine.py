"""
팩터 엔진 — 5-팩터 모델 (모멘텀·저변동·가치·퀄리티) + 시장 레짐 감지
=========================================================
paper_trade_runner.py 와 factor_validator.py 가 공유하는 순수 pandas/numpy 구현.
Streamlit 의존성 없음.
"""
import json
import os
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import pandas_ta as _pta
    _PTA_AVAILABLE = True
except ImportError:
    _pta = None
    _PTA_AVAILABLE = False

try:
    from modules.ict_analysis import ict_factor_score as _ict_factor_score
    _ICT_AVAILABLE = True
except Exception:
    _ict_factor_score = None
    _ICT_AVAILABLE = False

warnings.filterwarnings("ignore")

# ── 레짐별 팩터 가중치 ──────────────────────────────────────────────
# ICT에 10% 배분(기존 5개 팩터는 0.9배로 축소해 합=1.0 유지).
# 이 10%는 IC가 검증되기 전까지의 '약한 prior'일 뿐 — ic_weight_updater.py가
# 매주 ICT의 실측 mean_IC로 이 가중치를 다시 조정한다 (IC가 낮으면 자동으로 축소됨).
REGIME_WEIGHTS = {
    "bull":    {"mom_3m": 0.315, "mom_1m": 0.225, "low_vol": 0.135, "value": 0.135, "quality": 0.09, "ict": 0.10},
    "neutral": {"mom_3m": 0.225, "mom_1m": 0.180, "low_vol": 0.180, "value": 0.180, "quality": 0.135, "ict": 0.10},
    "bear":    {"mom_3m": 0.090, "mom_1m": 0.045, "low_vol": 0.360, "value": 0.225, "quality": 0.18, "ict": 0.10},
}
TARGET_VOL_PCT = 0.15   # 변동성 기반 사이징: 목표 연율화 변동성


def get_market_regime() -> tuple:
    """
    SPY 200일 이동평균 + VIX로 시장 레짐 감지.
    - bull    : SPY ≥ 200MA AND VIX < 25
    - bear    : SPY < 97% of 200MA OR VIX > 30
    - neutral : 그 사이
    Returns (regime: str, details: dict)
    """
    # 기본값: neutral 레짐이 되도록 설정 (네트워크 실패 시 안전한 폴백)
    spy_ratio, vix_cur = 0.99, 25.0
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


def _rsi(close: pd.Series, period: int = 14) -> float:
    if _PTA_AVAILABLE:
        try:
            result = _pta.rsi(close, length=period)
            if result is not None and not result.empty:
                val = result.iloc[-1]
                return float(val) if pd.notna(val) else 50.0
        except Exception:
            pass
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
            ret[label] = float((close.iloc[-1] / close.iloc[-(days + 1)] - 1) * 100)
        else:
            ret[label] = 0.0
    return ret


def fetch_fundamentals(tickers: list) -> dict:
    """P/E·영업이익률 수집. 반환: {ticker: {"pe": float, "margin": float}}"""
    result = {}
    for tk in tickers:
        try:
            info = yf.Ticker(tk).info
            pe_raw = info.get("trailingPE")
            if pe_raw is None:
                pe_raw = info.get("forwardPE")
            margin_raw = info.get("operatingMargins")
            pe     = min(float(pe_raw), 200.0) if pe_raw is not None and pe_raw > 0 else np.nan
            margin = float(margin_raw) * 100   if margin_raw is not None else np.nan
            result[tk] = {"pe": pe, "margin": margin}
        except Exception:
            result[tk] = {"pe": np.nan, "margin": np.nan}
    return result


_IC_WEIGHT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ic_weights.json")


def _load_regime_weights() -> dict:
    """
    ic_weights.json이 있으면 IC 기반 조정 가중치를 사용, 없으면 기본 REGIME_WEIGHTS.
    ic_weight_updater.py가 매주 생성하는 파일.

    레짐 이름(bull/neutral/bear)뿐 아니라 각 레짐 안의 팩터 키 구성까지 검증한다.
    팩터가 추가/제거되어 스키마가 바뀐 경우(예: ict 팩터 신규 추가) 저장된 파일은
    구버전 스키마이므로 무시하고 새 기본 REGIME_WEIGHTS로 폴백 →
    그래야만 ict 팩터가 ic_weight_updater.py 재실행 전까지 0으로 묻히지 않는다.
    """
    try:
        if os.path.exists(_IC_WEIGHT_FILE):
            with open(_IC_WEIGHT_FILE) as f:
                data = json.load(f)
            rw = data.get("regime_weights", {})
            if all(r in rw for r in REGIME_WEIGHTS):
                schema_ok = all(
                    set(rw[r].keys()) == set(REGIME_WEIGHTS[r].keys())
                    for r in REGIME_WEIGHTS
                )
                if schema_ok:
                    return rw
    except Exception:
        pass
    return REGIME_WEIGHTS


def calc_factor_scores(tickers: list, regime: str = "neutral") -> pd.DataFrame:
    """
    각 티커의 5-팩터 점수(10~90) 계산.
    팩터: 모멘텀(3M/1M) + 저변동성 + 가치(저PER) + 퀄리티(영업이익률)
    가중치는 레짐(bull/neutral/bear) + ic_weights.json(있으면)에 따라 동적 조정.
    """
    W = _load_regime_weights().get(regime, REGIME_WEIGHTS["neutral"])
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
            ict_raw = 50.0
            if _ICT_AVAILABLE:
                try:
                    ict_raw = float(_ict_factor_score(raw))
                except Exception:
                    ict_raw = 50.0
            rows.append({
                "ticker":  tk,
                "mom_1m":  mom.get("1M", 0),
                "mom_3m":  mom.get("3M", 0),
                "rsi":     rsi,
                "vol_ann": vol,
                "ict_raw": ict_raw,
                "price":   float(close.iloc[-1]),
            })
        except Exception as e:
            print(f"  [{tk}] 데이터 오류: {e}")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    print("  기초체력 데이터 수집 중...")
    fund = fetch_fundamentals([r["ticker"] for r in rows])
    df["pe"]     = df["ticker"].map(lambda t: fund.get(t, {}).get("pe", np.nan))
    df["margin"] = df["ticker"].map(lambda t: fund.get(t, {}).get("margin", np.nan))

    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)

    mu_v, sigma_v = df["vol_ann"].mean(), df["vol_ann"].std()
    df["z_low_vol"] = -(df["vol_ann"] - mu_v) / (sigma_v + 1e-9)

    mu_i, sigma_i = df["ict_raw"].mean(), df["ict_raw"].std()
    df["z_ict"] = (df["ict_raw"] - mu_i) / (sigma_i + 1e-9)

    pe_valid = df["pe"].dropna()
    if len(pe_valid) >= 3:
        mu_pe, sigma_pe = pe_valid.mean(), pe_valid.std()
        df["z_value"] = (-(df["pe"] - mu_pe) / (sigma_pe + 1e-9)).fillna(0.0)
    else:
        df["z_value"] = 0.0

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
        + df["z_ict"]     * W.get("ict", 0.0)
    )
    cmin, cmax = df["composite_z"].min(), df["composite_z"].max()
    df["composite"] = (df["composite_z"] - cmin) / (cmax - cmin + 1e-9) * 80 + 10
    return df.sort_values("composite", ascending=False).reset_index(drop=True)


def generate_signals(factor_df: pd.DataFrame, top_n: int = 5,
                     min_score: float = 60.0,
                     regime: str = "neutral") -> list:
    """
    팩터 스코어 기반 매수/매도 시그널 생성.

    top_n    : 매수 후보 상한 (점수 상위 N개 안에서만 고려)
    min_score: 이 점수 미만이면 top_n 안이어도 관망 처리
    regime   : bear이면 매수 시그널 전면 억제
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
