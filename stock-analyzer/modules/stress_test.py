"""
역사적 시나리오 & 합성 충격 스트레스 테스트
=================================================================
백테스트는 보통 "평균적인 시장 환경"에서의 성과를 보여준다.
이 모듈은 과거 주요 급락 이벤트 구간만 잘라서 전략을 돌려보거나
(역사적 시나리오), 가상으로 -20% 충격을 equity curve에 가해본다.
"""
import pandas as pd


KNOWN_STRESS_PERIODS = {
    'covid_crash_2020': {
        'label': 'COVID 폭락 (2020)',
        'start': '2020-02-01',
        'end': '2020-04-30',
        'desc': 'S&P500 -34%, VIX 85 (2020.02.19~03.23 저점)',
    },
    'rate_hike_2022': {
        'label': '연준 금리 인상 사이클 (2022)',
        'start': '2022-01-01',
        'end': '2022-12-31',
        'desc': '나스닥 -33%, 10년 국채 4.2%까지 상승. 성장주 밸류에이션 압축',
    },
    'svb_crisis_2023': {
        'label': 'SVB 파산 + 은행 위기 (2023)',
        'start': '2023-03-01',
        'end': '2023-04-30',
        'desc': '실리콘밸리뱅크 파산(03.10), 크레디트스위스 강제 합병(03.19). 금융섹터 급락',
    },
    'aug_2024_selloff': {
        'label': '엔 캐리 청산 (2024.08)',
        'start': '2024-07-20',
        'end': '2024-08-20',
        'desc': '일본 BOJ 금리 인상 → 엔 캐리 트레이드 청산 → VIX 65 급등 (08.05)',
    },
}


def replay_historical_scenario(run_backtest_fn, full_df: pd.DataFrame,
                                scenario_key: str, buffer_days: int = 30,
                                **backtest_kwargs) -> dict:
    """
    특정 스트레스 기간의 데이터만 슬라이스해서 전략을 다시 돌린다.
    buffer_days: 신호 워밍업을 위해 시작일 앞에 여유를 줌.
    run_backtest_fn: app.py의 run_backtest를 그대로 주입 (의존성 주입 패턴).
    backtest_kwargs: run_backtest에 전달할 추가 인자들.
    """
    if scenario_key not in KNOWN_STRESS_PERIODS:
        return {'error': f"알 수 없는 시나리오: {scenario_key}. 사용 가능: {list(KNOWN_STRESS_PERIODS)}"}

    sp = KNOWN_STRESS_PERIODS[scenario_key]
    start = pd.Timestamp(sp['start']) - pd.Timedelta(days=buffer_days)
    end = pd.Timestamp(sp['end'])

    slice_df = full_df[(full_df.index >= start) & (full_df.index <= end)].copy()
    if len(slice_df) < 30:
        return {'error': f"시나리오 기간 데이터 부족 ({len(slice_df)}행). 전체 데이터 범위를 확인하세요."}

    try:
        metrics, equity_df, trades_df = run_backtest_fn(slice_df, **backtest_kwargs)
    except Exception as e:
        return {'error': f"백테스트 실행 오류: {e}"}

    return {
        'scenario': sp['label'],
        'desc': sp['desc'],
        'period': f"{sp['start']} ~ {sp['end']}",
        'n_rows': len(slice_df),
        'metrics': metrics,
        'equity_df': equity_df,
        'trades_df': trades_df,
    }


def run_all_scenarios(run_backtest_fn, full_df: pd.DataFrame, **backtest_kwargs) -> list:
    """모든 known 시나리오에 대해 replay_historical_scenario를 실행."""
    results = []
    for key in KNOWN_STRESS_PERIODS:
        res = replay_historical_scenario(run_backtest_fn, full_df, key, **backtest_kwargs)
        results.append({'scenario_key': key, **res})
    return results


def synthetic_shock_test(equity_curve: pd.Series,
                          shock_pct: float = -20.0,
                          shock_day: int = None,
                          circuit_breaker: float = None) -> dict:
    """
    주어진 equity curve에 하루 만에 shock_pct% 하락을 가상으로 적용하고
    이후 회복 과정을 시뮬레이션.
    shock_day: None이면 중간 지점, 정수면 해당 인덱스.
    circuit_breaker: 이 MDD 수준에서 포지션을 강제 청산(현금화)하는 규칙을 모의.
    """
    eq = equity_curve.copy().reset_index(drop=True)
    n = len(eq)
    day = shock_day if shock_day is not None else n // 2

    if day >= n:
        return {'error': 'shock_day가 equity curve 길이를 초과'}

    shocked = eq.copy()
    shocked[day:] = shocked[day:] * (1 + shock_pct / 100)

    if circuit_breaker is not None:
        peak = shocked[:day].max() if day > 0 else shocked.iloc[0]
        for i in range(day, n):
            if shocked[i] / peak - 1 <= circuit_breaker / 100:
                shocked[i:] = shocked[i]
                break

    roll_max = shocked.expanding().max()
    mdd = float(((shocked - roll_max) / roll_max * 100).min())
    final_ret = float((shocked.iloc[-1] / shocked.iloc[0] - 1) * 100)

    return {
        'shock_pct': shock_pct,
        'shock_day': day,
        'post_shock_mdd_pct': round(mdd, 2),
        'final_return_pct': round(final_ret, 2),
        'circuit_breaker': circuit_breaker,
        'shocked_equity': shocked,
        'note': (f"{shock_day}일에 {shock_pct:+.0f}% 충격 적용 후 MDD {mdd:.1f}%, 최종수익률 {final_ret:.1f}%"
                 + (f" (서킷브레이커 {circuit_breaker}% 적용)" if circuit_breaker else ""))
    }


def max_consecutive_loss_days(daily_returns: pd.Series) -> dict:
    """연속 손실 일수 최대 기록."""
    max_streak, current = 0, 0
    for r in daily_returns:
        if r < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return {
        'max_consecutive_loss_days': max_streak,
        'note': f"최대 {max_streak}일 연속 손실 기록"
    }
