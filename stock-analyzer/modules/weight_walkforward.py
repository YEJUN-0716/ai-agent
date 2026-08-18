"""팩터 가중치 워크포워드 검증 — 백테스트가 과최적화인지 가리는 장치.

문제
----
지금 ic_weights.json 의 가중치는 **5년 전체 IC 를 보고** 정해진다. 그 가중치로
같은 5년을 백테스트하면 미래를 알고 짠 조합으로 과거를 채점하는 셈이라 성과가
부풀려진다(look-ahead bias).

이 모듈이 하는 일
----------------
각 시점 t 에서 **t 이전에 관측된 IC 만으로** 가중치를 정하고, 그 가중치를
t 시점 팩터 IC 에 적용해 채점한다(확장 윈도우). 그렇게 얻은 OOS 계열을,
전체 구간으로 가중치를 정했을 때의 IS 계열과 나란히 비교한다 — 둘의 차이가
곧 과최적화의 크기다.

입력은 순수 자료구조라 네트워크·파일 접근이 없다. 측정 스크립트가 데이터를
모아서 넘기고, 이 모듈은 계산만 한다.
"""

import math

DEFAULT_MIN_PERIODS = 12      # 가중치를 처음 정하기까지 필요한 최소 관측 수(월 단위로 1년)
DEFAULT_FLOOR = 0.03          # 개별 팩터 최소 비중 — 한 팩터에 전부 몰리는 것을 막는다


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ic_weights(ic_rows, floor=DEFAULT_FLOOR):
    """관측된 IC 행들 → 가중치 dict.

    ic_rows: [{factor: ic, ...}, ...] 기간별 IC 관측.
    평균 IC 의 양수 부분에 비례해 배분하되 floor 로 하한을 두고 합이 1 이 되게
    정규화한다. 평균 IC 가 전부 0 이하면 균등 배분으로 물러선다 — 음의 IC 에
    베팅하지 않는다.
    """
    if not ic_rows:
        return {}
    factors = sorted({f for row in ic_rows for f in row})
    means = {f: _mean([row[f] for row in ic_rows if f in row]) for f in factors}
    raw = {f: max(means[f], 0.0) for f in factors}
    if sum(raw.values()) <= 0:
        return {f: 1.0 / len(factors) for f in factors}
    total = sum(raw.values())
    w = {f: max(raw[f] / total, floor) for f in factors}
    s = sum(w.values())
    return {f: v / s for f, v in w.items()}


def composite_ic(factor_ics, weights):
    """한 기간의 팩터별 IC + 가중치 → 합성 IC.

    합성 점수의 IC 를 엄밀히 구하려면 팩터 간 상관이 필요하지만, z-score 가중합
    점수의 IC 는 실무상 가중평균 IC 로 근사한다 — 이 모듈의 목적은 절대 수준이
    아니라 IS/OOS **차이**를 보는 것이라 이 근사로 충분하다."""
    common = [f for f in factor_ics if f in weights]
    if not common:
        return 0.0
    return sum(factor_ics[f] * weights[f] for f in common)


def summarize(ic_series):
    """IC 계열 → 평균·표준편차·ICIR·t값·양의 IC 비율."""
    n = len(ic_series)
    if n == 0:
        return {'n': 0, 'mean_ic': 0.0, 'std_ic': 0.0, 'icir': 0.0,
                't_stat': 0.0, 'pct_positive': 0.0}
    m, sd = _mean(ic_series), _stdev(ic_series)
    icir = (m / sd) if sd > 0 else 0.0
    return {
        'n': n,
        'mean_ic': m,
        'std_ic': sd,
        'icir': icir,
        't_stat': icir * math.sqrt(n),
        'pct_positive': len([x for x in ic_series if x > 0]) / n * 100,
    }


def verdict(s_oos, s_is):
    """OOS 결과를 한 문장으로 — 백테스트 수치를 그대로 믿지 않게 막는 장치."""
    if s_oos['n'] == 0:
        return "검증 구간 없음 — 관측 기간이 min_periods 보다 짧습니다."
    gap = s_is['mean_ic'] - s_oos['mean_ic']
    if s_oos['mean_ic'] <= 0:
        return (f"OOS 평균 IC {s_oos['mean_ic']:+.4f} — 과거로 정한 가중치가 미래 구간에서 "
                f"예측력을 내지 못했습니다 (IS {s_is['mean_ic']:+.4f}, 과최적화 폭 {gap:+.4f}).")
    if abs(s_oos['t_stat']) < 2.0:
        return (f"OOS 평균 IC {s_oos['mean_ic']:+.4f} (t={s_oos['t_stat']:.2f}) — 방향은 양수지만 "
                f"우연과 구분되지 않습니다. 과최적화 폭 {gap:+.4f}.")
    return (f"OOS 평균 IC {s_oos['mean_ic']:+.4f} (t={s_oos['t_stat']:.2f}) — 검증 구간에서도 "
            f"예측력이 유지됐습니다. 과최적화 폭 {gap:+.4f}.")


def walk_forward(periods, min_periods=DEFAULT_MIN_PERIODS, floor=DEFAULT_FLOOR):
    """확장 윈도우 워크포워드.

    periods: 시간 순으로 정렬된 [{'date': ..., 'ics': {factor: ic}}, ...]
    반환: oos / in_sample 계열과 요약. overfit_gap 이 클수록 백테스트가 부풀려진 것이다.
    """
    oos, in_sample, used = [], [], []
    full_weights = ic_weights([p['ics'] for p in periods], floor=floor)

    for i, p in enumerate(periods):
        if i < min_periods:
            continue                      # 가중치를 정할 과거가 아직 부족한 구간은 건너뛴다
        w = ic_weights([q['ics'] for q in periods[:i]], floor=floor)
        oos.append(composite_ic(p['ics'], w))
        in_sample.append(composite_ic(p['ics'], full_weights))
        used.append({'date': p.get('date'), 'weights': w})

    s_oos, s_is = summarize(oos), summarize(in_sample)
    return {
        'oos': oos,
        'in_sample': in_sample,
        'weights_by_period': used,
        'full_sample_weights': full_weights,
        'summary': {
            'oos': s_oos,
            'in_sample': s_is,
            'overfit_gap': s_is['mean_ic'] - s_oos['mean_ic'],
            'verdict': verdict(s_oos, s_is),
        },
    }
