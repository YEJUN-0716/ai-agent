"""
시그널 디케이 분석 (IC 감쇠 & 최적 보유기간)
=================================================================
IC(Information Coefficient) = 시그널과 미래 수익률 사이의 상관계수.
보유기간(holding period)을 늘릴수록 IC가 얼마나 빠르게 떨어지는지 보면
"이 신호가 며칠 앞을 예측하는가"를 정량화할 수 있다.

IC가 절반으로 줄어드는 반감기가 짧다 = 단기 신호.
반감기가 길다 = 중장기 신호.
"""
import numpy as np
import pandas as pd
from scipy import stats


def compute_signal_ic_decay(signal: pd.Series, close: pd.Series,
                              horizons: list, method: str = 'spearman') -> pd.DataFrame:
    """
    horizons: [1, 5, 10, 20, 60] 등 일수 목록
    method: 'spearman'(순위 상관, 이상치 강건) or 'pearson'
    반환: horizon별 IC(상관계수), t통계량, p값
    """
    rows = []
    for h in horizons:
        fwd_ret = close.shift(-h) / close - 1
        df_tmp = pd.concat([signal, fwd_ret], axis=1).dropna()
        df_tmp.columns = ['signal', 'fwd']
        if len(df_tmp) < 10:
            rows.append({'horizon': h, 'IC': np.nan, 't_stat': np.nan, 'p_value': np.nan, 'n': len(df_tmp)})
            continue
        if method == 'spearman':
            corr, pval = stats.spearmanr(df_tmp['signal'], df_tmp['fwd'])
        else:
            corr, pval = stats.pearsonr(df_tmp['signal'], df_tmp['fwd'])
        n = len(df_tmp)
        t_stat = corr * np.sqrt(n - 2) / np.sqrt(max(1 - corr ** 2, 1e-12))
        rows.append({'horizon': h, 'IC': round(float(corr), 4),
                     't_stat': round(float(t_stat), 3), 'p_value': round(float(pval), 4), 'n': n})
    return pd.DataFrame(rows).set_index('horizon')


def compute_signal_ic_decay_marginal(signal: pd.Series, close: pd.Series,
                                      horizons: list, method: str = 'spearman') -> pd.DataFrame:
    """
    누적 IC가 아닌 구간별(marginal) IC.
    horizon[i]에서 horizon[i-1]까지의 수익률과의 상관.
    → "20일→60일 구간에서도 신호가 유효한가?"를 판단.
    """
    rows = []
    horizons_sorted = sorted(horizons)
    prev = 0
    for h in horizons_sorted:
        fwd_ret = (close.shift(-h) / close.shift(-prev) - 1) if prev > 0 else (close.shift(-h) / close - 1)
        df_tmp = pd.concat([signal, fwd_ret], axis=1).dropna()
        df_tmp.columns = ['signal', 'fwd']
        if len(df_tmp) < 10:
            rows.append({'horizon': h, 'prev': prev, 'marginal_IC': np.nan})
            prev = h
            continue
        if method == 'spearman':
            corr, pval = stats.spearmanr(df_tmp['signal'], df_tmp['fwd'])
        else:
            corr, pval = stats.pearsonr(df_tmp['signal'], df_tmp['fwd'])
        rows.append({'horizon': h, 'prev': prev, 'marginal_IC': round(float(corr), 4),
                     'p_value': round(float(pval), 4)})
        prev = h
    return pd.DataFrame(rows).set_index('horizon')


def estimate_half_life(ic_decay_df: pd.DataFrame) -> dict:
    """
    IC vs horizon의 지수감쇠 피팅: IC(h) ≈ IC(0) * exp(-λh)
    반감기 = ln(2) / λ 일.
    """
    df = ic_decay_df[['IC']].dropna()
    if len(df) < 3:
        return {'half_life_days': None, 'note': '데이터 부족'}

    horizons = np.array(df.index, dtype=float)
    ics = np.abs(df['IC'].values)

    try:
        from scipy.optimize import curve_fit
        def exp_decay(h, ic0, lam):
            return ic0 * np.exp(-lam * h)
        popt, _ = curve_fit(exp_decay, horizons, ics, p0=[ics[0], 0.05],
                            bounds=([0, 1e-9], [1.0, 10.0]), maxfev=2000)
        lam = popt[1]
        half_life = float(np.log(2) / lam) if lam > 0 else None
    except Exception:
        half_life = None

    return {
        'half_life_days': round(half_life, 1) if half_life else None,
        'note': (f"IC 반감기 약 {half_life:.1f}일 → "
                 f"{'단기(5일 이하) 신호' if half_life and half_life <= 5 else '중단기(5~20일) 신호' if half_life and half_life <= 20 else '장기 신호 또는 피팅 실패'}")
        if half_life else "반감기 추정 실패 (비단조 감쇠 또는 데이터 부족)"
    }


def find_optimal_holding_period(ic_decay_df: pd.DataFrame,
                                 significance_threshold: float = 0.05) -> dict:
    """
    p_value < significance_threshold를 만족하는 가장 긴 horizon을 최적 보유기간으로 본다.
    """
    df = ic_decay_df.dropna()
    if 'p_value' not in df.columns:
        return {'optimal_horizon': None, 'note': 'p_value 컬럼 없음'}
    sig = df[df['p_value'] < significance_threshold]
    if sig.empty:
        return {'optimal_horizon': None, 'note': f'유의수준 {significance_threshold} 이하의 horizon 없음'}
    optimal = int(sig.index.max())
    return {
        'optimal_horizon': optimal,
        'note': f"유의한 IC가 마지막으로 관찰된 horizon = {optimal}일 → 최적 보유기간 기준"
    }


def signal_autocorrelation(signal: pd.Series, lags: list) -> pd.DataFrame:
    """
    신호 자기상관 — 신호가 얼마나 오래 지속되는지(persistence) 측정.
    autocorr이 높다 = 신호가 느리게 바뀐다 = 포트폴리오 회전율 낮음.
    """
    rows = []
    for lag in lags:
        ac = float(signal.autocorr(lag=lag))
        rows.append({'lag': lag, 'autocorr': round(ac, 4)})
    return pd.DataFrame(rows).set_index('lag')


def full_decay_analysis(signal: pd.Series, close: pd.Series,
                         horizons: list) -> dict:
    """모든 분석을 한 번에 실행하는 래퍼."""
    ic_df = compute_signal_ic_decay(signal, close, horizons)
    marginal_df = compute_signal_ic_decay_marginal(signal, close, horizons)
    half_life = estimate_half_life(ic_df)
    optimal = find_optimal_holding_period(ic_df)
    autocorr = signal_autocorrelation(signal, lags=[1, 5, 10, 20])
    return {
        'ic_decay': ic_df,
        'marginal_ic': marginal_df,
        'half_life': half_life,
        'optimal_holding_period': optimal,
        'signal_autocorrelation': autocorr,
    }
