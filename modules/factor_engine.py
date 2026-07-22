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

# 가치·퀄리티 원점수 배합의 단일 진실 공급원 (app.py 의 두 구현과 공유).
# 의존성 없는 순수 산술 모듈이라 방어적 import 대상이 아니다.
from modules.factor_formulas import (
    ACCRUAL_NEUTRAL as _ACCRUAL_NEUTRAL,
    book_yield as _book_yield,
    earnings_yield as _earnings_yield,
    quality_raw as _quality_raw,
    value_raw as _value_raw,
)

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
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
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
    """
    P/E·PBR·이익률 수집. 반환: {ticker: {"pe": float, "pb": float, "margin": float}}

    KRX 종목(.KS/.KQ): OpenDART에서 영업이익률을 가져오고, P/E·P/B는 yfinance로 보완.
    US 종목: yfinance 전용.

    yfinance에서 trailingPE/operatingMargins가 없는 경우:
    forwardPE → priceToBook → profitMargins 순 폴백.

    주의: walk-forward IC 분석에서 look-ahead bias를 방지하려면
    point_in_time_fundamentals()를 사용하세요.
    """
    # DART 일괄 사전 조회 (KRX 종목만, 실패해도 yfinance 폴백)
    dart_data: dict = {}
    krx_tickers = [tk for tk in tickers if tk.endswith((".KS", ".KQ"))]
    if krx_tickers:
        try:
            from modules.dart_fundamentals import fetch_krx_fundamentals
            dart_data = fetch_krx_fundamentals(krx_tickers)
        except Exception as e:
            print(f"  [DART] 재무 사전 조회 실패 (yfinance 폴백): {e}")

    # yf.Ticker().info 병렬 조회 (순차 대비 ~10배 빠름)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_info(tk: str) -> tuple:
        try:
            return tk, yf.Ticker(tk).info
        except Exception:
            return tk, {}

    with ThreadPoolExecutor(max_workers=10) as pool:
        infos = dict(f.result() for f in as_completed(pool.submit(_fetch_info, tk) for tk in tickers))

    result = {}
    for tk in tickers:
        is_krx    = tk.endswith((".KS", ".KQ"))
        dart_margin = dart_data.get(tk, {}).get("margin")
        info      = infos.get(tk, {})
        try:
            # P/E — trailing 우선, None일 때만 forward 폴백 (0.0은 trailing로 유지)
            pe_raw = info.get("trailingPE")
            if pe_raw is None:
                pe_raw = info.get("forwardPE")
            pe = min(float(pe_raw), 200.0) if pe_raw is not None and float(pe_raw) > 0 else np.nan

            # PBR
            pb_raw = info.get("priceToBook")
            pb = min(float(pb_raw), 50.0) if pb_raw and float(pb_raw) > 0 else np.nan

            # 이익률 — KRX: DART 우선 / 없으면 yfinance 폴백
            if is_krx and dart_margin is not None:
                margin = float(dart_margin)
            else:
                margin_raw = info.get("operatingMargins")
                if margin_raw is None:
                    margin_raw = info.get("profitMargins")
                margin = float(margin_raw) * 100 if margin_raw is not None else np.nan

            # ROE (app.py calc_factor_scores와 동일 소스: yfinance returnOnEquity)
            roe_raw = info.get("returnOnEquity")
            roe = round(float(roe_raw) * 100, 2) if roe_raw is not None else np.nan

            # FCF 수익률 + 발생액 품질 (app.py calc_factor_scores의 P3-A/P3-B와 동일 공식)
            fcf  = info.get("freeCashflow")
            mcap = info.get("marketCap")
            ni_v = info.get("netIncomeToCommon")
            fcf_yield = fcf / mcap * 100 if fcf is not None and mcap else np.nan
            if fcf is not None and ni_v is not None and ni_v > 0 and fcf > 0:
                accrual_q = float(np.clip((fcf / ni_v) * 50, 0, 100))
            elif fcf is not None and ni_v is not None and ni_v > 0 and fcf <= 0:
                accrual_q = 0.0  # GAAP 흑자 + 현금 소각 → 최저 품질
            else:
                accrual_q = np.nan

            result[tk] = {"pe": pe, "pb": pb, "margin": margin,
                          "roe": roe, "fcf_yield": fcf_yield, "accrual_q": accrual_q}
        except Exception:
            result[tk] = {
                "pe": np.nan, "pb": np.nan,
                "margin": float(dart_margin) if dart_margin is not None else np.nan,
                "roe": np.nan, "fcf_yield": np.nan, "accrual_q": np.nan,
            }
    return result


def _ttm(recent_q, col):
    """recent_q(분기 4행)에서 col의 TTM 합. 컬럼 없거나 전부 NaN이면 NaN."""
    if col in recent_q.columns and recent_q[col].notna().any():
        return float(recent_q[col].sum())
    return np.nan


def point_in_time_fundamentals(tk: str, as_of_date, price: float,
                                 fin_hist: dict, shares_hist: dict,
                                 equity_hist: dict = None) -> dict:
    """
    as_of_date 기준 look-ahead-free 재무 지표 계산.

    전제 계약 (edgar_fundamentals.assemble_income이 보장):
      - fin_hist[tk]는 분기당 정확히 1행 (중복 기간 없음)
      - 인덱스는 실제 공시일(filed), 단조 비감소
      - 따라서 tail(4)가 서로 다른 4개 분기 = TTM으로 유효하다
    이 계약이 깨지면 (예: 중복 분기, 연간 행 혼입) TTM이 조용히 부풀려진다.

    equity_hist(선택): 자본총계 Series(index=filed). 있으면 ROE를 계산한다.

    반환: {"pe", "margin", "roe", "fcf_yield", "accrual_q"} (미측정은 NaN)
    """
    out = {"pe": np.nan, "margin": np.nan, "roe": np.nan,
           "fcf_yield": np.nan, "accrual_q": np.nan}
    try:
        df = fin_hist.get(tk, pd.DataFrame())
        if df.empty:
            return out
        past = df[df.index <= pd.Timestamp(as_of_date)]
        if past.empty:
            return out

        recent_q = past.tail(4)
        net_ttm   = _ttm(recent_q, "net_income")
        rev_ttm   = _ttm(recent_q, "revenue")
        op_ttm    = _ttm(recent_q, "operating_income")
        ocf_ttm   = _ttm(recent_q, "operating_cash_flow")
        capex_ttm = _ttm(recent_q, "capex")

        sh = shares_hist.get(tk, pd.Series(dtype=float))
        if not sh.empty:
            sh_past  = sh[sh.index <= pd.Timestamp(as_of_date)]
            n_shares = float(sh_past.iloc[-1]) if not sh_past.empty else float(sh.iloc[-1])
        else:
            n_shares = np.nan

        # PER (EPS>0일 때만)
        if pd.notna(n_shares) and n_shares > 0 and pd.notna(net_ttm) and price > 0:
            eps = net_ttm / n_shares
            if eps > 0:
                out["pe"] = min(price / eps, 200.0)

        # 영업이익률
        if pd.notna(rev_ttm) and rev_ttm > 0 and pd.notna(op_ttm):
            out["margin"] = (op_ttm / rev_ttm) * 100

        # ROE = net_ttm / 자본총계 (as_of 이전 최신 공시)
        if equity_hist is not None:
            eq = equity_hist.get(tk, pd.Series(dtype=float))
            if not eq.empty:
                eq_past = eq[eq.index <= pd.Timestamp(as_of_date)]
                equity_v = float(eq_past.iloc[-1]) if not eq_past.empty else np.nan
                if pd.notna(equity_v) and equity_v > 0 and pd.notna(net_ttm):
                    out["roe"] = (net_ttm / equity_v) * 100

        # FCF = OCF - CapEx (CapEx는 지출이라 양수 보고 → 차감)
        if pd.notna(ocf_ttm) and pd.notna(capex_ttm):
            fcf_ttm = ocf_ttm - capex_ttm
            # FCF 수익률 = FCF_TTM / 시가총액 (시총 = price × 주식수)
            if pd.notna(n_shares) and n_shares > 0 and price > 0:
                mcap = price * n_shares
                if mcap > 0:
                    out["fcf_yield"] = fcf_ttm / mcap * 100
            # 발생액 품질 (app.py 라이브 경로와 동일 공식)
            if pd.notna(net_ttm) and net_ttm > 0:
                out["accrual_q"] = (float(np.clip((fcf_ttm / net_ttm) * 50, 0, 100))
                                    if fcf_ttm > 0 else 0.0)
    except Exception:
        pass
    return out


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
    from concurrent.futures import ThreadPoolExecutor, as_completed

    W = _load_regime_weights().get(regime, REGIME_WEIGHTS["neutral"])
    end   = datetime.now()
    start = end - timedelta(days=200)

    def _fetch_ticker(tk: str):
        try:
            raw = yf.download(tk, start=start, end=end, progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            if raw.empty or len(raw) < 60:
                return None
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
            return {
                "ticker":  tk,
                "mom_1m":  mom.get("1M", 0),
                "mom_3m":  mom.get("3M", 0),
                "rsi":     rsi,
                "vol_ann": vol,
                "ict_raw": ict_raw,
                "price":   float(close.iloc[-1]),
            }
        except Exception as e:
            print(f"  [{tk}] 데이터 오류: {e}")
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_fetch_ticker, tk): tk for tk in tickers}
        for fut in as_completed(futs):
            result = fut.result()
            if result is not None:
                rows.append(result)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    print("  기초체력 데이터 수집 중...")
    fund = fetch_fundamentals([r["ticker"] for r in rows])
    df["pe"]        = df["ticker"].map(lambda t: fund.get(t, {}).get("pe",        np.nan))
    df["pb"]        = df["ticker"].map(lambda t: fund.get(t, {}).get("pb",        np.nan))
    df["margin"]    = df["ticker"].map(lambda t: fund.get(t, {}).get("margin",    np.nan))
    df["roe"]       = df["ticker"].map(lambda t: fund.get(t, {}).get("roe",       np.nan))
    df["fcf_yield"] = df["ticker"].map(lambda t: fund.get(t, {}).get("fcf_yield", np.nan))
    df["accrual_q"] = df["ticker"].map(lambda t: fund.get(t, {}).get("accrual_q", np.nan))

    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)

    mu_v, sigma_v = df["vol_ann"].mean(), df["vol_ann"].std()
    df["z_low_vol"] = -(df["vol_ann"] - mu_v) / (sigma_v + 1e-9)

    mu_i, sigma_i = df["ict_raw"].mean(), df["ict_raw"].std()
    df["z_ict"] = (df["ict_raw"] - mu_i) / (sigma_i + 1e-9)

    # 가치·퀄리티 배합은 factor_formulas 가 소유한다 (app.py 의 두 구현과 공유).
    # 계수를 바꾸려면 modules/factor_formulas.py 에서만 바꿀 것 — 여기서 고치면
    # UI 랭킹과 백테스트 결과가 조용히 갈린다.
    ep = df["pe"].apply(_earnings_yield)
    bp = df["pb"].apply(_book_yield)
    fcf_y = df["fcf_yield"].fillna(0.0)
    df["value_raw"] = _value_raw(ep, bp, fcf_y)
    mu_val, sigma_val = df["value_raw"].mean(), df["value_raw"].std()
    df["z_value"] = (df["value_raw"] - mu_val) / (sigma_val + 1e-9)

    roe_v     = df["roe"].fillna(0.0)
    margin_v  = df["margin"].fillna(0.0)
    accrual_v = df["accrual_q"].fillna(_ACCRUAL_NEUTRAL)
    df["quality_raw"] = _quality_raw(roe_v, margin_v, accrual_v)
    mu_q, sigma_q = df["quality_raw"].mean(), df["quality_raw"].std()
    df["z_quality"] = (df["quality_raw"] - mu_q) / (sigma_q + 1e-9)

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


def fetch_returns_matrix(tickers: list, lookback_days: int = 60) -> pd.DataFrame:
    """
    상관관계 기반 포지션 사이징용 일별 수익률 행렬.
    매수 후보 종목에만 적용 (전체 유니버스가 아닌 실제 후보군에 한해 사용).

    반환: pd.DataFrame (index=날짜, columns=티커, values=일별 수익률)
    """
    end   = datetime.now()
    start = end - timedelta(days=lookback_days + 10)
    ret_dict = {}
    for tk in tickers:
        try:
            yahoo_sym = tk.replace(".", "-")
            raw = yf.download(yahoo_sym, start=start, end=end,
                              progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.droplevel(1)
            if not raw.empty and "Close" in raw.columns and len(raw) >= 10:
                ret_dict[tk] = raw["Close"].dropna().pct_change().dropna()
        except Exception:
            pass
    if not ret_dict:
        return pd.DataFrame()
    return pd.DataFrame(ret_dict).dropna(how="all").tail(lookback_days)


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
