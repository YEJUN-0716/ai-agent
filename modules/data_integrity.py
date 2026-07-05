"""
데이터 무결성 검증
=================================================================
백테스트가 아무리 로직이 맞아도 입력 데이터가 틀리면 결과 전체가 무의미하다.
이 모듈은 OHLCV 데이터를 넣으면 흔한 데이터 오류 패턴을 자동으로 잡아낸다.
"""
import numpy as np
import pandas as pd


def check_ohlc_sanity(df: pd.DataFrame) -> dict:
    """OHLC 관계가 물리적으로 말이 되는지 점검."""
    issues = []
    n = len(df)

    bad_high = df[(df['High'] < df['Low']) | (df['High'] < df['Open']) | (df['High'] < df['Close'])]
    if len(bad_high) > 0:
        issues.append(f"High가 다른 가격보다 낮은 날 {len(bad_high)}건 (예: {list(bad_high.index[:3])})")

    bad_low = df[(df['Low'] > df['High']) | (df['Low'] > df['Open']) | (df['Low'] > df['Close'])]
    if len(bad_low) > 0:
        issues.append(f"Low가 다른 가격보다 높은 날 {len(bad_low)}건 (예: {list(bad_low.index[:3])})")

    non_positive = df[(df[['Open', 'High', 'Low', 'Close']] <= 0).any(axis=1)]
    if len(non_positive) > 0:
        issues.append(f"가격이 0 이하인 날 {len(non_positive)}건")

    if 'Volume' in df.columns:
        neg_vol = df[df['Volume'] < 0]
        if len(neg_vol) > 0:
            issues.append(f"거래량이 음수인 날 {len(neg_vol)}건")

    return {
        'n_rows': n,
        'n_issues': len(issues),
        'issues': issues,
        'is_clean': len(issues) == 0,
    }


def detect_suspicious_jumps(df: pd.DataFrame, threshold_pct: float = 30.0) -> dict:
    """하루 만에 threshold_pct% 이상 튀는 구간을 찾는다."""
    ret = df['Close'].pct_change() * 100
    jumps = ret[ret.abs() > threshold_pct]
    return {
        'n_jumps': len(jumps),
        'jumps': [{'날짜': str(d), '등락률': round(float(v), 1)} for d, v in jumps.items()],
        'note': (f"{threshold_pct}% 이상 급변 {len(jumps)}건 발견. 실제 뉴스(증자발행, 합병 등)로 "
                 f"설명되는지 확인 필요. 특히 액면분할/병합 날짜와 겹치면 수정주가 처리가 "
                 f"제대로 안 됐을 가능성이 큼.") if len(jumps) > 0 else "이상 급변 없음.",
    }


def check_split_adjustment(df: pd.DataFrame, known_splits: list = None) -> dict:
    """
    known_splits: [{'date': '2022-08-25', 'ratio': 20}, ...] 형태로 알려진
    액면분할 이벤트를 넣으면, 그 날짜 전후로 가격이 ratio만큼 연속으로
    조정되어 있는지(= 이미 수정주가가 반영됐는지) 확인한다.
    """
    if not known_splits:
        return {'checked': False, 'note': 'known_splits가 없어 검증을 건너뜀'}

    results = []
    for sp in known_splits:
        d = pd.Timestamp(sp['date'])
        ratio = sp['ratio']
        before = df[df.index < d]['Close']
        after = df[df.index >= d]['Close']
        if len(before) < 5 or len(after) < 5:
            results.append({'date': sp['date'], 'checked': False, 'note': '분할 전후 데이터 부족'})
            continue
        avg_before = before.iloc[-5:].mean()
        avg_after = after.iloc[:5].mean()
        implied_ratio = avg_before / avg_after
        is_adjusted = abs(implied_ratio - 1.0) < 0.15
        results.append({
            'date': sp['date'], 'expected_ratio': ratio,
            'implied_ratio_from_price': round(float(implied_ratio), 2),
            'appears_adjusted': is_adjusted,
            'note': ('수정주가 반영된 것으로 보임' if is_adjusted else
                     f'미조정 가능성 있음 → 분할 전후 가격이 {implied_ratio:.1f}배 차이나는데 '
                     f'조정됐다면 1에 가까워야 함. auto_adjust=True로 다시 받아볼 것')
        })
    return {'checked': True, 'results': results}


def survivorship_bias_report(current_universe: list) -> dict:
    """현재 사용하는 종목 리스트가 생존편향 위험이 있음을 명시적으로 리포트."""
    return {
        'n_tickers': len(current_universe),
        'warning': (
            f"현재 {len(current_universe)}개 종목은 모두 '지금 시점에 존재하는' 종목입니다. "
            "과거 이 기간 동안 상장폐지/인수합병/급락 후 정리매매된 종목은 "
            "이 리스트에 없으므로, 백테스트 성과가 실제보다 좋게 나올 수 있습니다 "
            "(생존편향). 완전한 해결에는 유료 블룸버그 데이터(예: CRSP)가 필요합니다."
        ),
    }


def full_data_integrity_check(df: pd.DataFrame, ticker: str = "", known_splits: list = None) -> dict:
    """전 검증들을 한 번에 실행해서 종합 리포트를 만든다."""
    ohlc = check_ohlc_sanity(df)
    jumps = detect_suspicious_jumps(df)
    splits = check_split_adjustment(df, known_splits)
    overall_ok = ohlc['is_clean'] and jumps['n_jumps'] == 0
    return {
        'ticker': ticker,
        'overall_ok': overall_ok,
        'ohlc_sanity': ohlc,
        'suspicious_jumps': jumps,
        'split_adjustment': splits,
    }
