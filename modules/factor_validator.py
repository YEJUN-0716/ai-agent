"""
팩터 유효성 검증 모듈
============================
IC (Information Coefficient) / ICIR / 퀸타일 분석

팩터 점수가 실제 미래 수익률을 얼마나 잘 예측하는지 수치로 검증.
- IC  : 스피어만 상관계수(팩터 점수 vs 실제 수익률), −1~+1
- ICIR: IC 평균 / IC 표준편차 — 팩터 일관성 (0.5 이상이면 신뢰 가능)
- 퀸타일: 상위 20%(Q5) vs 하위 20%(Q1) 누적 수익률 비교
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from scipy.stats import spearmanr
import yfinance as yf

from modules.factor_engine import fetch_fundamentals as _fetch_fundamentals_once

# 5-팩터 고정 가중치 (IC 검증용, 레짐 미분류)
_IC_WEIGHTS = {"mom_3m": 0.30, "mom_1m": 0.20, "low_vol": 0.20, "value": 0.15, "quality": 0.15}


def _calc_momentum_vol_scores(prices_dict: dict, as_of_date,
                               fundamentals: dict = None) -> dict:
    """
    as_of_date 기준으로 각 티커의 5-팩터 점수 계산.
    look-ahead bias 없음 (가격 데이터는 as_of_date 이전만 사용).
    fundamentals: {ticker: {"pe": float, "margin": float}} — 현재 기준 상수값 허용
    반환: {ticker: composite_score}
    """
    rows = []
    for tk, close in prices_dict.items():
        sub = close[close.index <= as_of_date]
        if len(sub) < 64:
            continue
        mom_3m = float((sub.iloc[-1] / sub.iloc[-63] - 1) * 100)
        mom_1m = float((sub.iloc[-1] / sub.iloc[-21] - 1) * 100) if len(sub) >= 22 else 0.0
        vol_21 = float(sub.pct_change().iloc[-21:].std() * np.sqrt(252) * 100) if len(sub) >= 22 else 100.0
        fund   = (fundamentals or {}).get(tk, {})
        rows.append({
            "ticker": tk,
            "mom_3m": mom_3m, "mom_1m": mom_1m, "vol": vol_21,
            "pe":     fund.get("pe", np.nan),
            "margin": fund.get("margin", np.nan),
        })

    if len(rows) < 4:
        return {}

    df = pd.DataFrame(rows).set_index("ticker")

    # 모멘텀·변동성 Z-score
    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)
    mu, sigma = df["vol"].mean(), df["vol"].std()
    df["z_low_vol"] = -(df["vol"] - mu) / (sigma + 1e-9)

    # 가치 (저 P/E → 고점수)
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

    W = _IC_WEIGHTS
    df["composite_z"] = (
        W["mom_3m"]  * df["z_mom_3m"]
        + W["mom_1m"]  * df["z_mom_1m"]
        + W["low_vol"] * df["z_low_vol"]
        + W["value"]   * df["z_value"]
        + W["quality"] * df["z_quality"]
    )
    mn, mx = df["composite_z"].min(), df["composite_z"].max()
    df["score"] = 20 + (df["composite_z"] - mn) / (mx - mn + 1e-9) * 60
    return df["score"].to_dict()


def run_ic_analysis(
    tickers: list,
    lookback_years: int = 2,
    rebal_days: int = 21,
    forward_days: int = 21,
    progress_cb=None,
) -> tuple:
    """
    Walk-forward IC 분석.

    Parameters
    ----------
    tickers       : 분석할 티커 리스트
    lookback_years: 분석 기간 (년)
    rebal_days    : 리밸런싱 간격 (거래일)
    forward_days  : IC 계산용 예측 기간 (거래일)
    progress_cb   : 진행률 콜백 (0~1 float를 받는 callable, 선택)

    Returns
    -------
    ic_df             : pd.DataFrame [date, ic, n_tickers]
    quintile_cum      : dict {Q1~Q5: pd.Series(cum_return%, indexed by date)}
    summary           : dict {mean_ic, std_ic, icir, t_stat, pct_positive, n_periods}
    long_short_cum    : pd.Series Q5-Q1 롱쇼트 누적 수익률
    """
    end   = datetime.now()
    start = end - timedelta(days=lookback_years * 365 + 90)

    # ── 가격 데이터 수집 ─────────────────────────────────────────
    prices_dict = {}
    for i, tk in enumerate(tickers):
        if progress_cb:
            progress_cb(i / len(tickers) * 0.45)
        try:
            raw = yf.download(tk, start=start, end=end, progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw = raw.xs(tk, axis=1, level=1) if tk in raw.columns.get_level_values(1) else raw
                raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
            if not raw.empty and "Close" in raw.columns and len(raw) >= 80:
                prices_dict[tk] = raw["Close"].dropna()
        except Exception:
            pass

    if len(prices_dict) < 5:
        return pd.DataFrame(), {}, {}, pd.Series(dtype=float)

    # ── 기초체력 데이터 수집 (1회, walk-forward 전체에 상수) ────────
    if progress_cb:
        progress_cb(0.5)
    fundamentals = _fetch_fundamentals_once(list(prices_dict.keys()))

    # ── 공통 거래일 인덱스 ───────────────────────────────────────
    all_closes = pd.DataFrame(prices_dict).dropna(how="all")
    dates      = all_closes.index
    min_start  = 65 + forward_days

    rebal_indices = list(range(min_start, len(dates) - forward_days, rebal_days))
    if not rebal_indices:
        return pd.DataFrame(), {}, {}, pd.Series(dtype=float)

    # ── Walk-forward ─────────────────────────────────────────────
    ic_records = []
    q_returns  = {f"Q{i}": [] for i in range(1, 6)}

    for step_i, idx in enumerate(rebal_indices):
        if progress_cb:
            progress_cb(0.55 + step_i / len(rebal_indices) * 0.45)

        as_of = dates[idx]
        scores = _calc_momentum_vol_scores(prices_dict, as_of, fundamentals=fundamentals)
        if len(scores) < 5:
            continue

        fwd_date = dates[min(idx + forward_days, len(dates) - 1)]
        returns  = {}
        for tk, score in scores.items():
            cur_vals = prices_dict[tk][prices_dict[tk].index <= as_of]
            fwd_vals = prices_dict[tk][prices_dict[tk].index <= fwd_date]
            if len(cur_vals) > 0 and len(fwd_vals) > 0:
                cp = float(cur_vals.iloc[-1])
                fp = float(fwd_vals.iloc[-1])
                returns[tk] = (fp / cp - 1) * 100 if cp > 0 else np.nan

        common = [t for t in scores if t in returns and not np.isnan(returns[t])]
        if len(common) < 5:
            continue

        x = np.array([scores[t] for t in common])
        y = np.array([returns[t] for t in common])
        ic, _ = spearmanr(x, y)
        ic_records.append({"date": as_of, "ic": ic, "n_tickers": len(common)})

        # 퀸타일 (Q1=저점수, Q5=고점수)
        sorted_t  = sorted(common, key=lambda t: scores[t])
        n         = len(sorted_t)
        q_size    = max(1, n // 5)
        quintiles = [
            sorted_t[:q_size],
            sorted_t[q_size:2*q_size],
            sorted_t[2*q_size:3*q_size],
            sorted_t[3*q_size:4*q_size],
            sorted_t[4*q_size:],
        ]
        for qi, q_tickers in enumerate(quintiles):
            q_ret = float(np.mean([returns[t] for t in q_tickers if t in returns]))
            q_returns[f"Q{qi+1}"].append({"date": as_of, "return": q_ret})

    if not ic_records:
        return pd.DataFrame(), {}, {}, pd.Series(dtype=float)

    # ── IC 집계 ──────────────────────────────────────────────────
    ic_df   = pd.DataFrame(ic_records)
    ic_vals = ic_df["ic"].dropna()

    mean_ic = float(ic_vals.mean())
    std_ic  = float(ic_vals.std())
    icir    = mean_ic / (std_ic + 1e-9)
    t_stat  = icir * float(np.sqrt(len(ic_vals)))

    summary = {
        "mean_ic":     round(mean_ic, 4),
        "std_ic":      round(std_ic, 4),
        "icir":        round(icir, 3),
        "t_stat":      round(t_stat, 2),
        "pct_positive": round(float((ic_vals > 0).mean() * 100), 1),
        "n_periods":   int(len(ic_vals)),
    }

    # ── 퀸타일 누적 수익률 ────────────────────────────────────────
    quintile_cum = {}
    for q_name, records in q_returns.items():
        if records:
            df_q = pd.DataFrame(records).set_index("date").sort_index()
            df_q["cum"] = (1 + df_q["return"] / 100).cumprod() * 100 - 100
            quintile_cum[q_name] = df_q["cum"]

    ls_cum = pd.Series(dtype=float)
    if "Q5" in quintile_cum and "Q1" in quintile_cum:
        q5 = pd.DataFrame(q_returns["Q5"]).set_index("date").sort_index()["return"]
        q1 = pd.DataFrame(q_returns["Q1"]).set_index("date").sort_index()["return"]
        ls = (q5 - q1).dropna()
        ls_cum = (1 + ls / 100).cumprod() * 100 - 100

    return ic_df, quintile_cum, summary, ls_cum
