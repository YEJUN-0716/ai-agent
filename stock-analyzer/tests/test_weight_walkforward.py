"""가중치 워크포워드 검증.

가장 중요한 성질: **미래를 보지 않는다.** t 시점 가중치는 t 이전 관측만으로
정해져야 하고, 그래서 국면이 뒤집히는 데이터에서는 전체구간 가중치(IS)보다
성적이 나빠야 정상이다 — 그 차이가 과최적화 폭이다.
"""

import math

from modules import weight_walkforward as wf


def _periods(rows):
    return [{'date': f"2026-{i // 12 + 1:02d}-{i % 12 + 1:02d}", 'ics': r}
            for i, r in enumerate(rows)]


# ── 가중치 산출 ──────────────────────────────────────────────────

def test_weights_sum_to_one():
    w = wf.ic_weights([{'a': 0.05, 'b': 0.01}, {'a': 0.03, 'b': 0.02}])
    assert math.isclose(sum(w.values()), 1.0)


def test_higher_mean_ic_gets_more_weight():
    w = wf.ic_weights([{'a': 0.10, 'b': 0.01}])
    assert w['a'] > w['b']


def test_negative_ic_factor_is_floored_not_shorted():
    """음의 IC 팩터는 하한까지만 줄이고 마이너스 비중을 주지 않는다."""
    w = wf.ic_weights([{'a': 0.10, 'b': -0.08}])
    assert w['b'] > 0
    assert math.isclose(w['b'], wf.DEFAULT_FLOOR, abs_tol=0.01)


def test_all_negative_falls_back_to_equal_weights():
    w = wf.ic_weights([{'a': -0.02, 'b': -0.05}])
    assert math.isclose(w['a'], w['b'])


def test_empty_input_gives_empty_weights():
    assert wf.ic_weights([]) == {}


# ── 합성 IC ──────────────────────────────────────────────────────

def test_composite_ic_is_weighted_average():
    assert math.isclose(wf.composite_ic({'a': 0.10, 'b': 0.00}, {'a': 0.5, 'b': 0.5}), 0.05)


def test_composite_ic_ignores_factors_without_weight():
    assert math.isclose(wf.composite_ic({'a': 0.10, 'zzz': 9.9}, {'a': 1.0}), 0.10)


# ── 워크포워드 (미래 미참조) ─────────────────────────────────────

def test_skips_periods_before_min_periods():
    out = wf.walk_forward(_periods([{'a': 0.01, 'b': 0.01} for _ in range(20)]), min_periods=12)
    assert out['summary']['oos']['n'] == 8          # 20 - 12
    assert len(out['weights_by_period']) == 8


def test_returns_no_oos_when_history_too_short():
    out = wf.walk_forward(_periods([{'a': 0.01} for _ in range(5)]), min_periods=12)
    assert out['summary']['oos']['n'] == 0
    assert '검증 구간 없음' in out['summary']['verdict']


def test_weights_at_time_t_use_only_past_observations():
    """앞 12기간은 a 만 좋고 이후엔 b 만 좋은 데이터.
    13번째 기간의 가중치는 아직 a 쪽이어야 한다 — 미래(b 우세)를 알면 안 된다."""
    rows = [{'a': 0.10, 'b': -0.05} for _ in range(12)] + \
           [{'a': -0.05, 'b': 0.10} for _ in range(12)]
    out = wf.walk_forward(_periods(rows), min_periods=12)
    first_w = out['weights_by_period'][0]['weights']
    assert first_w['a'] > first_w['b']


def test_overfit_gap_is_positive_when_regime_flips():
    """국면이 뒤집히면 전체구간 가중치(미래 포함)가 워크포워드보다 유리하다 —
    이 차이가 곧 백테스트가 부풀려지는 크기다."""
    rows = [{'a': 0.10, 'b': -0.05} for _ in range(12)] + \
           [{'a': -0.05, 'b': 0.10} for _ in range(12)]
    out = wf.walk_forward(_periods(rows), min_periods=12)
    assert out['summary']['overfit_gap'] > 0


def test_stable_factor_keeps_edge_out_of_sample():
    out = wf.walk_forward(_periods([{'a': 0.08, 'b': 0.0} for _ in range(30)]), min_periods=12)
    assert out['summary']['oos']['mean_ic'] > 0
    assert math.isclose(out['summary']['overfit_gap'], 0.0, abs_tol=1e-9)


# ── 요약 통계 ────────────────────────────────────────────────────

def test_summary_reports_zero_for_empty_series():
    s = wf.summarize([])
    assert s['n'] == 0 and s['t_stat'] == 0.0


def test_t_stat_scales_with_sample_size():
    short = wf.summarize([0.02, 0.01, 0.03] * 4)
    long = wf.summarize([0.02, 0.01, 0.03] * 20)
    assert long['t_stat'] > short['t_stat']


def test_verdict_flags_zero_or_negative_oos():
    out = wf.walk_forward(_periods([{'a': 0.0, 'b': 0.0} for _ in range(20)]), min_periods=12)
    assert '예측력을 내지 못했' in out['summary']['verdict']
