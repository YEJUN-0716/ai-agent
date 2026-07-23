"""
팩터 유효성 검증 모듈
============================
IC (Information Coefficient) / ICIR / 퀸타일 분석

팩터 점수가 실제 미래 수익률을 얼마나 잘 예측하는지 수치로 검증.
- IC  : 스피어만 상관계수(팩터 점수 vs 실제 수익률), −1~+1
- ICIR: IC 평균 / IC 표준편차 — 팩터 일관성 (0.5 이상이면 신뢰 가능)
- 퀸타일: 상위 20%(Q5) vs 하위 20%(Q1) 누적 수익률 비교

PIT(Point-in-Time) 재무 데이터:
  walk-forward IC 분석에서 look-ahead bias 방지를 위해
  as_of_date 기준으로 알 수 있는 재무 데이터만 사용.
  edgar_fundamentals.fetch_quarterly_fundamentals_history()의 인덱스는
  EDGAR의 실제 공시일(filed)이라 <= as_of_date 필터만으로
  미래 데이터를 차단할 수 있다.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from scipy.stats import spearmanr

from modules.factor_engine import point_in_time_fundamentals as _pit_fundamentals
from modules.factor_formulas import (
    annualized_vol_pct as _annualized_vol_pct,
    momentum_pct as _momentum_pct,
)
from modules.edgar_fundamentals import (
    fetch_quarterly_fundamentals_history as _fetch_fin_hist,
    fetch_shares_history                 as _fetch_shares_hist,
    fetch_equity_history                 as _fetch_equity_hist,
)


def _zscore_col(df, col, sign=1.0):
    """col의 z-score(부호 적용). 유효값 3개 미만이면 전부 0(팩터 기여 없음)."""
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    valid = df[col].dropna()
    if len(valid) < 3:
        return pd.Series(0.0, index=df.index)
    mu, sigma = valid.mean(), valid.std()
    return (sign * (df[col] - mu) / (sigma + 1e-9)).fillna(0.0)


def _add_value_quality_z(df):
    """value/quality 팩터 z-score를 app.py 라이브 배합과 동일 비율로 블렌드.

    value  = 저PER(EP) + FCF수익률  (라이브 EP0.40/BP0.30/FCF0.30에서 PIT에 없는
             BP를 빼고 EP0.571/FCF0.429로 재정규화)
    quality = ROE0.45 + 영업이익률0.35 + 발생액0.20
    커버리지 미달로 특정 지표가 전부 NaN이면 그 성분 z=0 → 나머지로 랭킹.
    IC는 rank 기반이라 성분별 상수 스케일은 팩터 자체 IC에 영향 없음.
    """
    z_ep  = _zscore_col(df, "pe", sign=-1.0)       # 낮은 PER = 높은 value
    z_fcf = _zscore_col(df, "fcf_yield", sign=1.0)
    df["z_value"] = z_ep * 0.571 + z_fcf * 0.429

    z_roe = _zscore_col(df, "roe", sign=1.0)
    z_mgn = _zscore_col(df, "margin", sign=1.0)
    z_acc = _zscore_col(df, "accrual_q", sign=1.0)
    df["z_quality"] = z_roe * 0.45 + z_mgn * 0.35 + z_acc * 0.20
    return df
from modules.price_panel import load_panel

try:
    from modules.ict_analysis import ict_factor_score as _ict_factor_score
    _ICT_AVAILABLE = True
except Exception:
    _ict_factor_score = None
    _ICT_AVAILABLE = False

try:
    from modules.bulls_signals import bulls_raw_score as _bulls_score
    _BULLS_AVAILABLE = True
except Exception:
    _bulls_score = None
    _BULLS_AVAILABLE = False

# 6-팩터 고정 가중치 (IC 검증용, 레짐 미분류)
_IC_WEIGHTS = {
    "mom_3m":  0.27,
    "mom_1m":  0.18,
    "low_vol": 0.18,
    "value":   0.135,
    "quality": 0.135,
    "ict":     0.10,
}


def _ict_raw_score(ohlcv_dict: dict, tk: str, as_of_date) -> float:
    """ICT 팩터 점수 — look-ahead 없이 as_of_date 이전 OHLCV만 사용."""
    if not _ICT_AVAILABLE or tk not in ohlcv_dict:
        return 50.0
    try:
        sub = ohlcv_dict[tk]
        sub = sub[sub.index <= pd.Timestamp(as_of_date)]
        if len(sub) < 60:
            return 50.0
        return float(_ict_factor_score(sub))
    except Exception:
        return 50.0


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI 시계열 (다이버전스 입력용)."""
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _bulls_raw_score(ohlcv_dict: dict, tk: str, as_of_date) -> float:
    """불스해적단 공용 백본 팩터 원점수 — look-ahead 없이 as_of_date 이전만 사용.

    ★ 측정 전용. 라이브 composite/가중치에 편입되지 않으며 IC 검증 목적으로만 계산.
    """
    if not _BULLS_AVAILABLE or tk not in ohlcv_dict:
        return 0.0
    try:
        sub = ohlcv_dict[tk]
        sub = sub[sub.index <= pd.Timestamp(as_of_date)]
        if len(sub) < 60:
            return 0.0
        rsi = _rsi_series(sub["Close"])
        return float(_bulls_score(sub, rsi=rsi))
    except Exception:
        return 0.0


def _calc_momentum_vol_scores(prices_dict: dict, as_of_date,
                               ohlcv_dict: dict = None,
                               fin_hist: dict = None,
                               shares_hist: dict = None,
                               equity_hist: dict = None) -> dict:
    """
    as_of_date 기준으로 각 티커의 6-팩터 점수 계산.
    look-ahead bias 없음 (가격·재무 데이터는 as_of_date 이전만 사용).

    반환: {ticker: composite_score}
    """
    rows = []
    for tk, close in prices_dict.items():
        sub = close[close.index <= as_of_date]
        if len(sub) < 64:
            continue
        cur_price = float(sub.iloc[-1])
        # 구간이 아래 _calc_per_factor_zscores 와 한 봉씩 어긋나 있다(63 vs 64,
        # 21 vs 22). 실측 IC 를 무효화하지 않으려고 그대로 뒀다 — 3단계에서
        # 정의를 확정할 때 함께 맞춘다.
        mom_3m = _momentum_pct(sub, 63)
        mom_1m = _momentum_pct(sub, 21) if len(sub) >= 22 else 0.0
        vol_21 = _annualized_vol_pct(sub, 21) if len(sub) >= 22 else 100.0

        if fin_hist is not None:
            fund = _pit_fundamentals(tk, as_of_date, cur_price,
                                     fin_hist, shares_hist or {}, equity_hist)
        else:
            fund = {}

        ict_score = _ict_raw_score(ohlcv_dict or {}, tk, as_of_date)

        rows.append({
            "ticker": tk,
            "mom_3m": mom_3m, "mom_1m": mom_1m, "vol": vol_21,
            "pe":        fund.get("pe", np.nan),
            "margin":    fund.get("margin", np.nan),
            "roe":       fund.get("roe", np.nan),
            "fcf_yield": fund.get("fcf_yield", np.nan),
            "accrual_q": fund.get("accrual_q", np.nan),
            "ict":    ict_score,
        })

    if len(rows) < 4:
        return {}

    df = pd.DataFrame(rows).set_index("ticker")

    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)
    mu, sigma = df["vol"].mean(), df["vol"].std()
    df["z_low_vol"] = -(df["vol"] - mu) / (sigma + 1e-9)

    _add_value_quality_z(df)

    mu_i, sigma_i = df["ict"].mean(), df["ict"].std()
    df["z_ict"] = (df["ict"] - mu_i) / (sigma_i + 1e-9)

    W = _IC_WEIGHTS
    df["composite_z"] = (
        W["mom_3m"]  * df["z_mom_3m"]
        + W["mom_1m"]  * df["z_mom_1m"]
        + W["low_vol"] * df["z_low_vol"]
        + W["value"]   * df["z_value"]
        + W["quality"] * df["z_quality"]
        + W.get("ict", 0.0) * df["z_ict"]
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

    if progress_cb:
        progress_cb(0.10)
    prices_dict, ohlcv_dict = load_panel(tickers, start, end)
    if progress_cb:
        progress_cb(0.40)

    if len(prices_dict) < 5:
        return pd.DataFrame(), {}, {}, pd.Series(dtype=float)

    if progress_cb:
        progress_cb(0.45)
    fin_hist    = _fetch_fin_hist(list(prices_dict.keys()))
    shares_hist = _fetch_shares_hist(list(prices_dict.keys()))
    equity_hist = _fetch_equity_hist(list(prices_dict.keys()))

    all_closes    = pd.DataFrame(prices_dict).dropna(how="all")
    dates         = all_closes.index
    min_start     = 65 + forward_days
    rebal_indices = list(range(min_start, len(dates) - forward_days, rebal_days))
    if not rebal_indices:
        return pd.DataFrame(), {}, {}, pd.Series(dtype=float)

    ic_records = []
    q_returns  = {f"Q{i}": [] for i in range(1, 6)}

    for step_i, idx in enumerate(rebal_indices):
        if progress_cb:
            progress_cb(0.50 + step_i / len(rebal_indices) * 0.50)

        as_of  = dates[idx]
        scores = _calc_momentum_vol_scores(
            prices_dict, as_of,
            ohlcv_dict=ohlcv_dict,
            fin_hist=fin_hist,
            shares_hist=shares_hist,
            equity_hist=equity_hist,
        )
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

    ic_df   = pd.DataFrame(ic_records)
    ic_vals = ic_df["ic"].dropna()

    mean_ic = float(ic_vals.mean())
    std_ic  = float(ic_vals.std())
    icir    = mean_ic / (std_ic + 1e-9)
    t_stat  = icir * float(np.sqrt(len(ic_vals)))

    summary = {
        "mean_ic":      round(mean_ic, 4),
        "std_ic":       round(std_ic, 4),
        "icir":         round(icir, 3),
        "t_stat":       round(t_stat, 2),
        "pct_positive": round(float((ic_vals > 0).mean() * 100), 1),
        "n_periods":    int(len(ic_vals)),
    }

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


# ── 팩터별 IC 분석 (가중치 자동 조정용) ───────────────────────────

def _calc_per_factor_zscores(prices_dict: dict, as_of_date,
                              ohlcv_dict: dict = None,
                              fin_hist: dict = None,
                              shares_hist: dict = None,
                              equity_hist: dict = None) -> dict:
    """
    as_of_date 기준 각 티커의 팩터별 z-score (6-팩터).

    반환: {ticker: {"mom_3m": z, "mom_1m": z, "low_vol": z,
                    "value": z, "quality": z, "ict": z}}
    """
    rows = []
    for tk, close in prices_dict.items():
        sub = close[close.index <= as_of_date]
        if len(sub) < 65:
            continue
        cur_price = float(sub.iloc[-1])
        # 여기서 재는 mom_3m/mom_1m/low_vol 이 곧 ic_weights.json 의 IC 다.
        # 프로덕션 스캔은 이와 다른 구간(12-1, 252봉)을 쓴다 — 팩터 정의를
        # 바꾸면 저장된 IC 히스토리와의 비교가 끊긴다.
        mom_3m = _momentum_pct(sub, 64)
        mom_1m = _momentum_pct(sub, 22) if len(sub) >= 22 else 0.0
        vol_21 = _annualized_vol_pct(sub, 21)

        if fin_hist is not None:
            fund = _pit_fundamentals(tk, as_of_date, cur_price,
                                     fin_hist, shares_hist or {}, equity_hist)
        else:
            fund = {}

        ict_score = _ict_raw_score(ohlcv_dict or {}, tk, as_of_date)
        bulls_score = _bulls_raw_score(ohlcv_dict or {}, tk, as_of_date)

        rows.append({
            "ticker": tk,
            "mom_3m": mom_3m, "mom_1m": mom_1m, "vol": vol_21,
            "pe":        fund.get("pe", np.nan),
            "margin":    fund.get("margin", np.nan),
            "roe":       fund.get("roe", np.nan),
            "fcf_yield": fund.get("fcf_yield", np.nan),
            "accrual_q": fund.get("accrual_q", np.nan),
            "ict":    ict_score,
            "bulls":  bulls_score,
        })

    if len(rows) < 5:
        return {}

    df = pd.DataFrame(rows).set_index("ticker")

    for col in ("mom_3m", "mom_1m"):
        mu, sigma = df[col].mean(), df[col].std()
        df[f"z_{col}"] = (df[col] - mu) / (sigma + 1e-9)

    mu, sigma = df["vol"].mean(), df["vol"].std()
    df["z_low_vol"] = -(df["vol"] - mu) / (sigma + 1e-9)

    _add_value_quality_z(df)

    mu_i, sigma_i = df["ict"].mean(), df["ict"].std()
    df["z_ict"] = (df["ict"] - mu_i) / (sigma_i + 1e-9)

    mu_b, sigma_b = df["bulls"].mean(), df["bulls"].std()
    df["z_bulls"] = (df["bulls"] - mu_b) / (sigma_b + 1e-9)

    result = {}
    for tk in df.index:
        result[tk] = {
            "mom_3m":  float(df.loc[tk, "z_mom_3m"]),
            "mom_1m":  float(df.loc[tk, "z_mom_1m"]),
            "low_vol": float(df.loc[tk, "z_low_vol"]),
            "value":   float(df.loc[tk, "z_value"]),
            "quality": float(df.loc[tk, "z_quality"]),
            "ict":     float(df.loc[tk, "z_ict"]),
            "bulls":   float(df.loc[tk, "z_bulls"]),
        }
    return result


def run_per_factor_ic_analysis(
    tickers: list,
    lookback_years: int = 2,
    rebal_days: int = 21,
    forward_days: int = 21,
    progress_cb=None,
) -> dict:
    """
    팩터별 walk-forward IC 분석.
    ic_weight_updater.py 가 호출하여 ic_weights.json을 생성할 때 사용.

    Returns
    -------
    {factor: {"mean_ic": float, "std_ic": float, "icir": float,
              "pct_positive": float, "n": int}}
    """
    end   = datetime.now()
    start = end - timedelta(days=lookback_years * 365 + 90)

    if progress_cb:
        progress_cb(0.10)
    prices_dict, ohlcv_dict = load_panel(tickers, start, end)
    if progress_cb:
        progress_cb(0.40)

    if len(prices_dict) < 5:
        return {}

    if progress_cb:
        progress_cb(0.45)
    fin_hist    = _fetch_fin_hist(list(prices_dict.keys()))
    shares_hist = _fetch_shares_hist(list(prices_dict.keys()))
    equity_hist = _fetch_equity_hist(list(prices_dict.keys()))

    all_closes    = pd.DataFrame(prices_dict).dropna(how="all")
    dates         = all_closes.index
    min_start     = 65 + forward_days
    rebal_indices = list(range(min_start, len(dates) - forward_days, rebal_days))
    if not rebal_indices:
        return {}

    FACTORS    = ["mom_3m", "mom_1m", "low_vol", "value", "quality", "ict", "bulls"]
    factor_ics = {f: [] for f in FACTORS}

    for step_i, idx in enumerate(rebal_indices):
        if progress_cb:
            progress_cb(0.50 + step_i / len(rebal_indices) * 0.50)

        as_of         = dates[idx]
        factor_scores = _calc_per_factor_zscores(
            prices_dict, as_of,
            ohlcv_dict=ohlcv_dict,
            fin_hist=fin_hist,
            shares_hist=shares_hist,
            equity_hist=equity_hist,
        )
        if len(factor_scores) < 5:
            continue

        fwd_date = dates[min(idx + forward_days, len(dates) - 1)]
        returns  = {}
        for tk in factor_scores:
            cur_vals = prices_dict[tk][prices_dict[tk].index <= as_of]
            fwd_vals = prices_dict[tk][prices_dict[tk].index <= fwd_date]
            if len(cur_vals) > 0 and len(fwd_vals) > 0:
                cp, fp = float(cur_vals.iloc[-1]), float(fwd_vals.iloc[-1])
                returns[tk] = (fp / cp - 1) * 100 if cp > 0 else np.nan

        common = [t for t in factor_scores if t in returns and not np.isnan(returns[t])]
        if len(common) < 5:
            continue

        y = np.array([returns[t] for t in common])
        for factor in FACTORS:
            x   = np.array([factor_scores[t].get(factor, 0.0) for t in common])
            ic, _ = spearmanr(x, y)
            if not np.isnan(ic):
                factor_ics[factor].append(float(ic))

    result = {}
    for factor, ics in factor_ics.items():
        if not ics:
            result[factor] = {"mean_ic": 0.0, "std_ic": 0.0, "icir": 0.0,
                               "pct_positive": 0.0, "n": 0}
            continue
        arr     = np.array(ics)
        mean_ic = float(arr.mean())
        std_ic  = float(arr.std())
        result[factor] = {
            "mean_ic":      round(mean_ic, 4),
            "std_ic":       round(std_ic, 4),
            "icir":         round(mean_ic / (std_ic + 1e-9), 3),
            "pct_positive": round(float((arr > 0).mean() * 100), 1),
            "n":            len(ics),
        }
    return result


def run_out_of_sample_validation(
    tickers: list,
    lookback_years: int = 5,
    test_fraction: float = 0.25,
    rebal_days: int = 21,
    forward_days: int = 21,
    progress_cb=None,
) -> dict:
    """
    OOS(Out-of-Sample) 검증.
    학습 기간(1 - test_fraction)에서 팩터 IC 계산 후
    검증 기간(test_fraction)에서 동일 가중치를 테스트하여 과적합을 탐지.

    Returns
    -------
    {
      "train_period":          {"start": str, "end": str},
      "test_period":           {"start": str, "end": str},
      "train_factor_ic":       {factor: stats},
      "derived_test_weights":  {factor: weight},
      "test_composite_ic":     float,
      "train_composite_ic":    float,
      "overfitting_flag":      bool,
    }
    """
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_years * 365 + 90)

    total_days = (end_dt - start_dt).days
    test_days  = int(total_days * test_fraction)
    train_end  = end_dt - timedelta(days=test_days)

    train_start_str = start_dt.strftime("%Y-%m-%d")
    train_end_str   = train_end.strftime("%Y-%m-%d")
    test_start_str  = (train_end + timedelta(days=1)).strftime("%Y-%m-%d")
    test_end_str    = end_dt.strftime("%Y-%m-%d")

    if progress_cb:
        progress_cb(0.05)

    prices_dict, ohlcv_dict = load_panel(tickers, train_start_str, test_end_str)
    if progress_cb:
        progress_cb(0.35)

    if len(prices_dict) < 5:
        return {}

    if progress_cb:
        progress_cb(0.35)
    fin_hist    = _fetch_fin_hist(list(prices_dict.keys()))
    shares_hist = _fetch_shares_hist(list(prices_dict.keys()))
    equity_hist = _fetch_equity_hist(list(prices_dict.keys()))

    all_closes = pd.DataFrame(prices_dict).dropna(how="all")
    dates      = all_closes.index
    FACTORS    = ["mom_3m", "mom_1m", "low_vol", "value", "quality", "ict"]
    min_idx    = 65 + forward_days

    def _walk_forward_ic(date_range, prog_start, prog_end):
        factor_ics_local = {f: [] for f in FACTORS}
        rebal_idx_list   = list(range(min_idx, len(date_range) - forward_days, rebal_days))

        for step_i, idx in enumerate(rebal_idx_list):
            if progress_cb:
                pct = prog_start + (step_i / max(len(rebal_idx_list), 1)) * (prog_end - prog_start)
                progress_cb(pct)

            as_of = date_range[idx]
            factor_scores = _calc_per_factor_zscores(
                prices_dict, as_of,
                ohlcv_dict=ohlcv_dict,
                fin_hist=fin_hist,
                shares_hist=shares_hist,
                equity_hist=equity_hist,
            )
            if len(factor_scores) < 5:
                continue

            fwd_date = date_range[min(idx + forward_days, len(date_range) - 1)]
            returns  = {}
            for tk in factor_scores:
                cv = prices_dict[tk][prices_dict[tk].index <= as_of]
                fv = prices_dict[tk][prices_dict[tk].index <= fwd_date]
                if len(cv) > 0 and len(fv) > 0:
                    cp, fp = float(cv.iloc[-1]), float(fv.iloc[-1])
                    returns[tk] = (fp / cp - 1) * 100 if cp > 0 else np.nan

            common = [t for t in factor_scores if t in returns and not np.isnan(returns[t])]
            if len(common) < 5:
                continue
            y = np.array([returns[t] for t in common])
            for factor in FACTORS:
                x  = np.array([factor_scores[t].get(factor, 0.0) for t in common])
                ic, _ = spearmanr(x, y)
                if not np.isnan(ic):
                    factor_ics_local[factor].append(float(ic))

        out = {}
        for factor, ics in factor_ics_local.items():
            if not ics:
                out[factor] = {"mean_ic": 0.0, "std_ic": 0.0, "icir": 0.0,
                               "pct_positive": 0.0, "n": 0}
                continue
            arr = np.array(ics)
            out[factor] = {
                "mean_ic":      round(float(arr.mean()), 4),
                "std_ic":       round(float(arr.std()), 4),
                "icir":         round(float(arr.mean()) / (float(arr.std()) + 1e-9), 3),
                "pct_positive": round(float((arr > 0).mean() * 100), 1),
                "n":            len(ics),
            }
        return out

    train_dates = dates[dates <= pd.Timestamp(train_end_str)]
    test_dates  = dates[dates >  pd.Timestamp(train_end_str)]

    if progress_cb:
        progress_cb(0.40)

    train_factor_ic = _walk_forward_ic(train_dates, 0.40, 0.70)

    if progress_cb:
        progress_cb(0.70)

    IC_FLOOR = 0.005
    raw_w    = {f: max(s.get("mean_ic", IC_FLOOR), IC_FLOOR)
                for f, s in train_factor_ic.items()}
    total_w  = sum(raw_w.values())
    derived_weights = {f: round(v / total_w, 4) for f, v in raw_w.items()}

    test_factor_ic = _walk_forward_ic(test_dates, 0.70, 0.98)

    if progress_cb:
        progress_cb(0.98)

    train_composite_ic = sum(
        derived_weights.get(f, 0.0) * train_factor_ic.get(f, {}).get("mean_ic", 0.0)
        for f in FACTORS
    )
    test_composite_ic = sum(
        derived_weights.get(f, 0.0) * test_factor_ic.get(f, {}).get("mean_ic", 0.0)
        for f in FACTORS
    )

    overfitting = (train_composite_ic > 0.01
                   and test_composite_ic < train_composite_ic * 0.5)

    if progress_cb:
        progress_cb(1.0)

    return {
        "train_period":         {"start": train_start_str, "end": train_end_str},
        "test_period":          {"start": test_start_str,  "end": test_end_str},
        "train_factor_ic":      train_factor_ic,
        "derived_test_weights": derived_weights,
        "test_composite_ic":    round(test_composite_ic, 4),
        "train_composite_ic":   round(train_composite_ic, 4),
        "overfitting_flag":     overfitting,
    }
