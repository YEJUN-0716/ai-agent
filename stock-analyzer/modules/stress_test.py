"""
역사적 시나리오 스트레스 테스트
=================================================================
백테스트는 보통 "평균적인 시장 환경"에서의 성과를 보여준다.
이 모듈은 과거 주요 급락 이벤트 구간만 잘라서 전략을 다시 돌려본다.
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

    # 라벨 구간 안의 거래일만 센다. len(slice_df) 는 워밍업 버퍼까지 포함하는데
    # 화면은 그 값을 'period' 옆에 "N거래일" 로 붙여 왔다 — COVID 시나리오는
    # "2020-02-01 ~ 2020-04-30" 옆에 버퍼 20여 거래일이 더해진 수가 찍혔다.
    # 기간을 말하는 숫자와 기간을 말하는 문장이 다른 구간을 가리키면 안 된다.
    labeled = int(((slice_df.index >= pd.Timestamp(sp['start']))
                   & (slice_df.index <= end)).sum())

    return {
        'scenario': sp['label'],
        'desc': sp['desc'],
        'period': f"{sp['start']} ~ {sp['end']}",
        'n_rows': labeled,
        'metrics': metrics,
        'equity_df': equity_df,
        'trades_df': trades_df,
    }
