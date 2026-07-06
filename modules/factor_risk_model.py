"""
팩터 리스크 모델 (스타일 분석 + 순수 알파 분리)
=================================================================
전략 수익률을 시장·팩터로 분해해서 "진짜 알파"인지 아니면
단순히 모멘텀/가치/저변동성 팩터를 장기 롱으로 들고 있는 건지 판단한다.

주요 기능:
1. Regression-based style analysis (Sharpe 1992 스타일 분석)
2. Rolling market beta (시장 노출도 시계열)
3. Sector concentration report
4. Factor-pure alpha check (팩터 설명분 제거 후 남는 알파)
"""
import numpy as np
import pandas as pd
from scipy import stats


def regression_style_analysis(strategy_returns: pd.Series,
                                factor_returns: pd.DataFrame,
                                annualization_factor: int = 252) -> dict:
    """
    strategy_returns: 일별 전략 수익률 (Series)
    factor_returns: 일별 팩터 수익률 (DataFrame, 열=팩터명)
        예: {'MKT': spy_returns, 'SMB': smb_returns, 'HML': hml_returns}

    반환: 팩터별 계수, R², annualized alpha, t-통계량
    """
    df = pd.concat([strategy_returns.rename('strategy'), factor_returns], axis=1).dropna()
    if len(df) < 30:
        return {'error': '데이터 부족 (30행 미만)'}

    y = df['strategy'].values
    X = df[factor_returns.columns].values
    X_with_const = np.column_stack([np.ones(len(X)), X])

    try:
        result = np.linalg.lstsq(X_with_const, y, rcond=None)
        coeffs = result[0]
    except np.linalg.LinAlgError:
        return {'error': '회귀분석 실패'}

    alpha_daily = float(coeffs[0])
    betas = {col: round(float(coeffs[i + 1]), 4) for i, col in enumerate(factor_returns.columns)}
    y_hat = X_with_const @ coeffs
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    alpha_annualized = alpha_daily * annualization_factor * 100

    n, k = len(y), len(coeffs)
    if n > k:
        mse = ss_res / (n - k)
        cov_matrix = mse * np.linalg.pinv(X_with_const.T @ X_with_const)
        se_alpha = float(np.sqrt(cov_matrix[0, 0]))
        t_alpha = alpha_daily / se_alpha if se_alpha > 0 else 0.0
        p_alpha = float(2 * stats.t.sf(abs(t_alpha), df=n - k))
    else:
        t_alpha, p_alpha = 0.0, 1.0

    return {
        'alpha_daily': round(alpha_daily, 6),
        'alpha_annualized_pct': round(alpha_annualized, 3),
        't_stat_alpha': round(t_alpha, 3),
        'p_value_alpha': round(p_alpha, 4),
        'significant_alpha': bool(p_alpha < 0.05),
        'betas': betas,
        'r_squared': round(r_squared, 4),
        'interpretation': (
            f"연율화 알파 {alpha_annualized:.2f}%, "
            f"{'통계적으로 유의 (p={p_alpha:.3f})' if p_alpha < 0.05 else '통계적으로 유의하지 않음 (p={p_alpha:.3f})'}. "
            f"R²={r_squared:.2f} — {'팩터로 전략 수익률의 대부분이 설명됨' if r_squared > 0.7 else '독자적 알파 성분 큼'}"
        )
    }


def rolling_market_beta(stock_returns: pd.Series, market_returns: pd.Series,
                         window: int = 60) -> pd.Series:
    """
    rolling window 기준 시장 베타. β > 1이면 시장보다 변동이 크고,
    음수면 역방향. 추세를 보면 시장 노출도가 시간에 따라 어떻게 바뀌는지 확인.
    """
    cov = stock_returns.rolling(window).cov(market_returns)
    var = market_returns.rolling(window).var()
    return (cov / var.replace(0, np.nan)).rename('rolling_beta')


def sector_concentration_report(weights: dict, sectors: dict) -> dict:
    """
    weights: {'AAPL': 0.15, 'MSFT': 0.12, ...}
    sectors: {'AAPL': 'Technology', 'MSFT': 'Technology', 'XOM': 'Energy', ...}

    HHI(허핀달-허시만 지수)로 섹터 집중도 측정.
    HHI < 0.15: 분산, 0.15~0.25: 중간, > 0.25: 집중
    """
    sector_weights: dict[str, float] = {}
    for ticker, w in weights.items():
        sec = sectors.get(ticker, 'Unknown')
        sector_weights[sec] = sector_weights.get(sec, 0.0) + w

    total = sum(sector_weights.values()) or 1.0
    norm = {sec: w / total for sec, w in sector_weights.items()}
    hhi = float(sum(v ** 2 for v in norm.values()))
    top_sector = max(norm, key=norm.get)

    return {
        'sector_weights': {k: round(v * 100, 1) for k, v in sorted(norm.items(), key=lambda x: -x[1])},
        'hhi': round(hhi, 4),
        'top_sector': top_sector,
        'top_sector_weight_pct': round(norm[top_sector] * 100, 1),
        'concentration': ('고집중' if hhi > 0.25 else '중간' if hhi > 0.15 else '분산'),
        'note': f"HHI={hhi:.3f} — 최대 비중 섹터: {top_sector} ({norm[top_sector]*100:.1f}%)"
    }


def factor_pure_alpha_check(strategy_returns: pd.Series,
                              market_returns: pd.Series,
                              factor_returns: pd.DataFrame,
                              weights: dict,
                              sectors: dict) -> dict:
    """
    종합 팩터 리스크 점검 — style analysis + sector concentration + rolling beta를 한 번에.
    """
    style = regression_style_analysis(strategy_returns, factor_returns)
    sector = sector_concentration_report(weights, sectors)
    rb = rolling_market_beta(strategy_returns, market_returns)
    latest_beta = float(rb.dropna().iloc[-1]) if not rb.dropna().empty else None

    _beta_str = f"{latest_beta:.2f}" if latest_beta is not None else "N/A"
    return {
        'style_analysis': style,
        'sector_concentration': sector,
        'latest_rolling_beta': round(latest_beta, 3) if latest_beta is not None else None,
        'summary': (
            f"알파 {style.get('alpha_annualized_pct', 'N/A')}% (연율화), "
            f"R²={style.get('r_squared', 'N/A')}, "
            f"현재 시장베타={_beta_str}, "
            f"섹터집중도={sector['concentration']}"
        )
    }
