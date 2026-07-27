"""시그널 성적표 집계 검증.

핵심은 "표본이 적을 때 함부로 결론 내지 않는다"는 성질이다 — 승률이 100%여도
n 이 작으면 판정은 '표본 부족'이어야 한다.
"""

import math

from modules import signal_scorecard as sc


def _sig(ret=None, score=None, bench=None):
    s = {'symbol': 'X', 'entry_date': '2026-01-01', 'return_pct': ret}
    if score is not None:
        s['score'] = score
    if bench is not None:
        s['benchmark_return_pct'] = bench
    return s


# ── 기본 집계 ────────────────────────────────────────────────────

def test_counts_only_evaluated_signals_as_sample():
    """미평가(return_pct=None) 시그널은 표본에서 빠지고 pending 으로만 센다."""
    out = sc.summarize_signals([_sig(5.0), _sig(-2.0), _sig(None), _sig(None)])
    assert out['n'] == 2
    assert out['pending'] == 2


def test_hit_rate_and_expectancy():
    out = sc.summarize_signals([_sig(10.0), _sig(-5.0), _sig(3.0), _sig(-2.0)])
    assert out['hit_rate'] == 50.0
    assert math.isclose(out['expectancy'], 1.5)
    assert math.isclose(out['avg_win'], 6.5)
    assert math.isclose(out['avg_loss'], -3.5)


def test_profit_factor_is_gross_win_over_gross_loss():
    out = sc.summarize_signals([_sig(6.0), _sig(4.0), _sig(-5.0)])
    assert math.isclose(out['profit_factor'], 2.0)


def test_profit_factor_infinite_when_no_losses():
    out = sc.summarize_signals([_sig(3.0), _sig(1.0)])
    assert out['profit_factor'] == float('inf')


def test_returns_zeroed_summary_for_empty_input():
    out = sc.summarize_signals([])
    assert out['n'] == 0
    assert out['expectancy'] == 0.0
    assert '표본 없음' in out['verdict']


# ── 최대낙폭 ─────────────────────────────────────────────────────

def test_max_drawdown_is_zero_when_only_gains():
    assert sc.max_drawdown([5.0, 3.0, 1.0]) == 0.0


def test_max_drawdown_measures_peak_to_trough():
    # +10% 로 고점 → -20% → -10% : 고점 대비 최저 = 0.8*0.9 - 1 = -28%
    mdd = sc.max_drawdown([10.0, -20.0, -10.0])
    assert math.isclose(mdd, -28.0, abs_tol=0.01)


def test_max_drawdown_handles_empty():
    assert sc.max_drawdown([]) == 0.0


# ── 판정 (과대 해석 방지 장치) ───────────────────────────────────

def test_small_sample_never_claims_significance_even_when_all_wins():
    """전승이어도 표본이 작으면 '표본 부족' 이라고 말해야 한다."""
    out = sc.summarize_signals([_sig(5.0) for _ in range(5)])
    assert out['hit_rate'] == 100.0
    assert '표본 부족' in out['verdict']


def test_large_but_noisy_sample_is_reported_as_not_significant():
    rets = [3.0 if i % 2 == 0 else -3.0 for i in range(40)]   # 기대값 ~0
    out = sc.summarize_signals([_sig(r) for r in rets])
    assert out['n'] == 40
    assert abs(out['t_stat']) < sc.SIGNIFICANT_T
    assert '유의하지 않음' in out['verdict']


def test_consistent_edge_on_large_sample_is_reported_as_significant():
    rets = [2.0 if i % 5 else -1.0 for i in range(50)]        # 꾸준한 양의 기대값
    out = sc.summarize_signals([_sig(r) for r in rets])
    assert out['t_stat'] > sc.SIGNIFICANT_T
    assert '유의한 양의 기대값' in out['verdict']


def test_losing_system_is_called_out():
    rets = [-2.0 if i % 5 else 1.0 for i in range(50)]
    out = sc.summarize_signals([_sig(r) for r in rets])
    assert out['expectancy'] < 0
    assert '유의한 음의 기대값' in out['verdict']


# ── 벤치마크 대비 ────────────────────────────────────────────────

def test_alpha_is_excess_over_benchmark():
    out = sc.summarize_signals([_sig(5.0, bench=3.0), _sig(1.0, bench=2.0)])
    assert math.isclose(out['expectancy'], 3.0)
    assert math.isclose(out['alpha_vs_benchmark'], 0.5)   # 3.0 - 2.5


def test_alpha_is_none_when_benchmark_partially_missing():
    """일부만 벤치마크가 있으면 비교가 왜곡되므로 계산하지 않는다."""
    out = sc.summarize_signals([_sig(5.0, bench=3.0), _sig(1.0)])
    assert out['alpha_vs_benchmark'] is None


# ── 점수 구간별 단조성 ───────────────────────────────────────────

def test_score_buckets_split_by_edges():
    signals = [_sig(1.0, score=55), _sig(2.0, score=65),
               _sig(-1.0, score=75), _sig(4.0, score=85)]
    buckets = {b['bucket']: b for b in sc.by_score_bucket(signals)}
    assert buckets['0-59']['n'] == 1
    assert buckets['60-69']['n'] == 1
    assert buckets['70-79']['hit_rate'] == 0.0
    assert math.isclose(buckets['80-100']['avg_return'], 4.0)


def test_score_buckets_ignore_signals_without_score():
    buckets = sc.by_score_bucket([_sig(1.0), _sig(2.0, score=85)])
    assert sum(b['n'] for b in buckets) == 1
