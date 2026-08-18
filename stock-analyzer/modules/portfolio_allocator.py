"""
포트폴리오 할당 & 전략 결합
=================================================================
여러 개의 독립 전략(또는 독립 알파)를 어떻게 섞을지 결정하는 모듈.
Mean-Variance(MV)는 공분산 추정 오차가 극단적 비중을 만드는 문제가 있어
실전에서는 역변동성/리스크패리티 방식이 더 강건하다.
"""
import numpy as np
import pandas as pd


def compute_strategy_correlation(strategy_returns: pd.DataFrame) -> pd.DataFrame:
    """전략들 사이의 상관행렬. 0.7 이상이면 사실상 중복 전략일 가능성."""
    return strategy_returns.corr()


def inverse_vol_weights(strategy_returns: pd.DataFrame, lookback: int = 60) -> pd.Series:
    """
    역변동성 비중: 변동성이 낮은 전략에 더 많이 배분.
    lookback일치 실현변동성(연율화 전 일변동성) 기준.
    """
    vol = strategy_returns.tail(lookback).std()
    inv = 1.0 / vol.replace(0, np.nan)
    weights = (inv / inv.sum()).fillna(0.0)
    if weights.sum() == 0:
        n = len(weights)
        return pd.Series(1.0 / n if n > 0 else 0.0, index=weights.index)
    return weights


def risk_parity_weights(strategy_returns: pd.DataFrame,
                         max_iter: int = 500, tol: float = 1e-10) -> pd.Series:
    """
    리스크 패리티 비중: 각 전략의 리스크 기여분을 균등하게 맞춤.
    공분산 기반 반복법(iterative 접근) — 수렴하지 않으면 역변동성으로 폴백.
    """
    cov = strategy_returns.cov().values
    n = len(strategy_returns.columns)
    w = np.ones(n) / n

    for _ in range(max_iter):
        port_var = w @ cov @ w
        if port_var <= 0:
            break
        risk_contrib = w * (cov @ w) / port_var
        target = np.ones(n) / n
        gradient = 2 * (risk_contrib - target)
        w = w - 0.01 * gradient
        w = np.maximum(w, 1e-8)
        w = w / w.sum()
        if np.max(np.abs(gradient)) < tol:
            break

    result = pd.Series(w, index=strategy_returns.columns)
    if not np.isfinite(result).all():
        return inverse_vol_weights(strategy_returns)
    return result / result.sum()


def combine_strategies(strategy_equity_curves: dict,
                        weights: pd.Series,
                        initial_capital: float = 10_000_000) -> pd.DataFrame:
    """
    각 전략의 equity curve(절대값)를 수익률로 바꿔 가중합산 후
    다시 절대 equity 값으로 복원.

    strategy_equity_curves: {'전략A': pd.Series(날짜별 자산), '전략B': ...}
    weights: pd.Series({'전략A': 0.6, '전략B': 0.4})
    """
    if not strategy_equity_curves:
        return pd.DataFrame({'combined': pd.Series(dtype=float)})
    ret_df = pd.DataFrame({k: v.pct_change() for k, v in strategy_equity_curves.items()})
    ret_df.iloc[0] = 0.0
    combined_ret = (ret_df * weights).sum(axis=1)
    combined_equity = (1 + combined_ret).cumprod() * initial_capital
    return pd.DataFrame({
        'combined': combined_equity,
        **strategy_equity_curves,
    })


def correlation_penalty_scale(returns_df: pd.DataFrame,
                               min_scale: float = 0.5,
                               max_scale: float = 1.0,
                               corr_threshold: float = 0.3) -> pd.Series:
    """
    개별 종목이 다른 종목들과 얼마나 상관되는지에 따라 포지션 사이즈를 조정.
    핵심: 평균이 아닌 최대 쌍별 상관관계를 사용 — 집중 베타를 놓치지 않기 위함.

    반환: pd.Series {ticker: scale} — 1.0=축소 없음, min_scale=최대 축소
    """
    if returns_df.empty or len(returns_df.columns) < 2:
        return pd.Series(1.0, index=returns_df.columns)

    corr    = returns_df.corr()
    tickers = list(corr.columns)
    scales  = {}
    for tk in tickers:
        other_corrs = [abs(corr.loc[tk, other]) for other in tickers if other != tk]
        max_corr    = max(other_corrs) if other_corrs else 0.0
        if max_corr <= corr_threshold:
            scales[tk] = max_scale
        else:
            t = (max_corr - corr_threshold) / (1.0 - corr_threshold + 1e-9)
            scales[tk] = max_scale - t * (max_scale - min_scale)
    return pd.Series(scales)


def portfolio_correlation_report(returns_df: pd.DataFrame,
                                  corr_threshold: float = 0.7) -> dict:
    """
    진단용: 상관관계 임계치를 초과하는 페어 목록 및 평균 상관관계 반환.
    """
    if returns_df.empty or len(returns_df.columns) < 2:
        return {"high_corr_pairs": [], "avg_pairwise_corr": 0.0}

    corr      = returns_df.corr()
    tickers   = list(corr.columns)
    pairs     = []
    corr_vals = []
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            c = float(corr.iloc[i, j])
            corr_vals.append(abs(c))
            if abs(c) >= corr_threshold:
                pairs.append({
                    "ticker_a":    tickers[i],
                    "ticker_b":    tickers[j],
                    "correlation": round(c, 3),
                })
    return {
        "high_corr_pairs":    sorted(pairs, key=lambda x: abs(x["correlation"]), reverse=True),
        "avg_pairwise_corr":  round(float(np.mean(corr_vals)), 3) if corr_vals else 0.0,
    }


def risk_parity_position_scale(returns_df: pd.DataFrame,
                                min_scale: float = 0.3,
                                max_scale: float = 2.0) -> pd.Series:
    """
    공분산 기반 리스크 패리티로 개별 종목 포지션 사이즈를 결정.
    반환: 균등 비중 대비 상대적 스케일 (1.0 = 균등 비중)
    """
    if returns_df.empty or len(returns_df.columns) < 2:
        return pd.Series(1.0, index=returns_df.columns)

    cov = returns_df.cov().values
    n   = cov.shape[0]
    w   = np.ones(n) / n

    for _ in range(300):
        port_var = w @ cov @ w
        if port_var <= 0:
            break
        risk_contrib = w * (cov @ w) / port_var
        target       = np.ones(n) / n
        grad         = 2 * (risk_contrib - target)
        w            = w - 0.01 * grad
        w            = np.maximum(w, 1e-8)
        w            = w / w.sum()
        if np.max(np.abs(grad)) < 1e-9:
            break

    if not np.isfinite(w).all():
        w = np.ones(n) / n

    eq_weight = 1.0 / n
    scales    = np.clip(w / eq_weight, min_scale, max_scale)
    return pd.Series(scales, index=returns_df.columns)


def diversification_report(strategy_returns: pd.DataFrame, weights: pd.Series) -> dict:
    """
    포트폴리오 분산화 효과 정량화.
    Diversification Ratio = 가중평균 개별변동성 / 포트폴리오 변동성
    (> 1이면 분산화 효과가 있음, 클수록 좋음)
    """
    cov = strategy_returns.cov()
    w = weights.reindex(strategy_returns.columns).fillna(0).values
    individual_vols = np.sqrt(np.diag(cov.values))
    port_vol = float(np.sqrt(w @ cov.values @ w))
    weighted_avg_vol = float(np.dot(w, individual_vols))
    div_ratio = weighted_avg_vol / port_vol if port_vol > 0 else 1.0
    corr = compute_strategy_correlation(strategy_returns)
    high_corr_pairs = []
    cols = list(strategy_returns.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            c = float(corr.iloc[i, j])
            if c > 0.7:
                high_corr_pairs.append({'전략A': cols[i], '전략B': cols[j], '상관': round(c, 3)})

    return {
        'diversification_ratio': round(div_ratio, 3),
        'portfolio_annualized_vol_pct': round(port_vol * np.sqrt(252) * 100, 2),
        'high_correlation_pairs': high_corr_pairs,
        'note': (f"분산화 비율 {div_ratio:.2f} — {'분산화 효과 있음' if div_ratio > 1.05 else '전략들이 유사하게 움직여 분산화 효과 미미'}")
    }
