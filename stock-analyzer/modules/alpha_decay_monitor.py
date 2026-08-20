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
