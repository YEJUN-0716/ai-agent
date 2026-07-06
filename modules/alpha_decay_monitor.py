"""
알파 디케이 모니터
=================================================================
백테스트에서 좋았던 전략도 시장에 퍼지거나 구조가 바뀌면 성과가 떨어진다.
이 모듈은 실전(live) 수익률 시계열을 백테스트 모수와 비교해서
언제부터 알파가 사라지고 있는지 통계적으로 감지한다.

사용 예:
    from modules.alpha_decay_monitor import detect_alpha_decay
    result = detect_alpha_decay(live_daily_returns, bt_mean, bt_std)
"""
import numpy as np
import pandas as pd
from scipy import stats


def rolling_performance_vs_baseline(live_returns: pd.Series,
                                     backtest_daily_mean: float,
                                     backtest_daily_std: float,
                                     window: int = 60) -> pd.DataFrame:
    """
    rolling window 기간 동안의 실전 평균수익률/변동성을 백테스트 모수와 비교.
    반환: 날짜별 {live_mean, live_std, bt_mean, bt_std, z_score, p_value} DataFrame
    """
    roll_mean = live_returns.rolling(window).mean()
    roll_std = live_returns.rolling(window).std()

    if backtest_daily_std <= 0:
        return pd.DataFrame(columns=['live_mean', 'live_std', 'bt_mean', 'bt_std', 'z_score', 'p_value'])
    z_scores = (roll_mean - backtest_daily_mean) / (backtest_daily_std / np.sqrt(window))
    p_values = z_scores.apply(lambda z: float(stats.norm.cdf(z)) if np.isfinite(z) else np.nan)

    return pd.DataFrame({
        'live_mean': roll_mean,
        'live_std': roll_std,
        'bt_mean': backtest_daily_mean,
        'bt_std': backtest_daily_std,
        'z_score': z_scores,
        'p_value': p_values,
    })


def detect_alpha_decay(live_returns: pd.Series,
                        backtest_daily_mean: float,
                        backtest_daily_std: float,
                        window: int = 60,
                        z_threshold: float = -2.0) -> dict:
    """
    최근 window일 실전 수익률이 백테스트 평균보다
    z_threshold 표준편차 이상 낮으면 알파 디케이로 판정.
    """
    perf = rolling_performance_vs_baseline(live_returns, backtest_daily_mean, backtest_daily_std, window)
    latest = perf.dropna().iloc[-1] if not perf.dropna().empty else None

    if latest is None:
        return {'detected': False, 'reason': '데이터 부족 (window보다 live 데이터가 짧음)'}

    z = float(latest['z_score'])
    detected = z <= z_threshold
    return {
        'detected': detected,
        'latest_z_score': round(z, 3),
        'latest_live_mean_daily': round(float(latest['live_mean']), 6),
        'backtest_daily_mean': round(backtest_daily_mean, 6),
        'window_days': window,
        'reason': (f"최근 {window}일 평균 일수익률({latest['live_mean']:.4%})이 "
                   f"백테스트 평균({backtest_daily_mean:.4%}) 대비 z={z:.2f}로 "
                   f"{'임계치 이하 → 알파 디케이 감지됨' if detected else '정상 범위'}")
    }


def cusum_change_detection(returns: pd.Series, target_mean: float,
                             threshold_std: float = 8.0,
                             allowance_std: float = 0.5) -> dict:
    """
    CUSUM(누적합) 변화점 탐지.
    평균이 목표치 아래로 얼마나 오래 / 얼마나 크게 지속되는지 본다.
    threshold_std: 표준편차 몇 배를 초과하면 알람 (기본 8σ, 보수적 설정)
    """
    _std = float(returns.std())
    if not np.isfinite(_std) or _std <= 0:
        return {
            'n_alarms': 0, 'alarms': [], 'final_cusum': 0.0,
            'threshold': 0.0, 'note': '데이터 부족 또는 변동성 없음 (CUSUM 계산 불가)',
        }
    sigma = _std
    k = allowance_std * sigma
    S_neg = 0.0
    cusum_vals, alarms = [], []

    for i, r in enumerate(returns):
        S_neg = min(0.0, S_neg + r - target_mean + k)
        cusum_vals.append(S_neg)
        if S_neg <= -threshold_std * sigma:
            alarms.append({'index': i, 'date': returns.index[i] if hasattr(returns.index[i], 'isoformat') else str(returns.index[i]),
                           'cusum': round(S_neg, 6)})
            S_neg = 0.0

    return {
        'n_alarms': len(alarms),
        'alarms': alarms,
        'final_cusum': round(cusum_vals[-1] if cusum_vals else 0.0, 6),
        'threshold': round(-threshold_std * sigma, 6),
        'note': f"알람 {len(alarms)}회 발생 — 성과 구조 변화 가능성 높음" if alarms else "CUSUM 이상 없음",
    }


def two_sample_ttest_backtest_vs_live(backtest_returns: pd.Series,
                                       live_returns: pd.Series) -> dict:
    """
    백테스트 수익률 vs 실전 수익률의 평균 차이가 통계적으로 유의한지 t검정.
    p < 0.05이면서 live_mean < bt_mean이면 실질적 성과 저하로 해석.
    """
    if len(backtest_returns) < 10 or len(live_returns) < 10:
        return {'error': '데이터 부족 (각 10개 이상 필요)'}

    t_stat, p_val = stats.ttest_ind(backtest_returns.dropna(), live_returns.dropna(),
                                     alternative='greater')
    bt_mean = float(backtest_returns.mean())
    live_mean = float(live_returns.mean())
    return {
        't_statistic': round(float(t_stat), 4),
        'p_value': round(float(p_val), 4),
        'backtest_daily_mean': round(bt_mean, 6),
        'live_daily_mean': round(live_mean, 6),
        'significant': bool(p_val < 0.05),
        'interpretation': (
            '백테스트 > 실전 수익률 차이가 통계적으로 유의함 — 알파 디케이 강한 증거' if (p_val < 0.05 and bt_mean > live_mean)
            else '통계적으로 유의한 성과 차이 없음 (또는 실전이 더 좋음)')
    }
