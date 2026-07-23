"""애널리스트 성적 채점 — 순위 예측력(IC)과 겹침 보정 표준오차.

매일 기록하고 21일 뒤 수익률로 채점하면 관측치의 선행 구간이 서로 겹친다.
겹친 관측을 독립으로 세면 표본이 실제보다 20배 많아 보이고, "n=250,
유의함" 이라는 잘못된 결론이 나온다. Newey–West 로 자기상관을 반영해
표준오차를 키운다 — 실제 정보량만큼만 인정하는 장치다.

파일도 네트워크도 모른다. 숫자만 받는다.
"""
import numpy as np
from scipy.stats import spearmanr

# 5일: 표본이 연 50개씩 쌓여 가장 먼저 판정 가능.
# 21일: 팩터 IC 와 같은 기준이라 나란히 비교된다.
# 63일: 느린 신호용.
HORIZONS = (5, 21, 63)

# 단면 상관을 낼 최소 종목 수. 이보다 적으면 그 날은 버린다.
MIN_TICKERS_PER_DAY = 5


def newey_west_se(values, lag):
    """평균의 Newey–West 표준오차. lag=0 이면 통상 표준오차와 같다.

    Bartlett 커널로 lag 까지의 자기공분산을 더한다. 겹치는 선행 구간이
    양의 자기상관을 만들므로 합이 커지고, 표준오차도 그만큼 커진다.
    """
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n < 2:
        return float("nan")

    dev = arr - arr.mean()
    # ddof=1 로 나눠 lag=0 일 때 통상 표준오차와 정확히 일치시킨다.
    total = float(dev @ dev) / (n - 1)

    for k in range(1, min(int(lag), n - 1) + 1):
        gamma_k = float(dev[k:] @ dev[:-k]) / (n - 1)
        weight = 1.0 - k / (lag + 1.0)
        total += 2.0 * weight * gamma_k

    # 표본에 따라 음수가 나올 수 있다 — 표준오차가 허수가 되면 안 된다.
    if total <= 0:
        return 0.0
    return float(np.sqrt(total / n))


def _daily_ic(day_scores, returns, slug):
    """하루치 단면 IC. 유효 종목이 모자라면 None.

    그 애널리스트의 점수가 **있는 종목만** 쓴다. 없는 종목을 중립값으로
    채우면 계산 불가가 예측력 없음으로 섞인다.
    """
    pairs = []
    for ticker, per_analyst in day_scores.items():
        if slug not in per_analyst:
            continue
        ret = returns.get(ticker)
        if ret is None or (isinstance(ret, float) and np.isnan(ret)):
            continue
        pairs.append((per_analyst[slug], ret))

    if len(pairs) < MIN_TICKERS_PER_DAY:
        return None

    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    # 점수가 전부 같으면 순위가 정의되지 않는다.
    if len(np.unique(x)) < 2:
        return None

    ic, _ = spearmanr(x, y)
    return None if np.isnan(ic) else float(ic)


def score_analysts(days, forward_returns, horizon):
    """애널리스트별 IC 통계.

    days             : [{"date", "scores": {ticker: {slug: score}}}]
    forward_returns  : {date: {ticker: pct}}

    반환: {slug: {mean_ic, se, t_stat, n, effective_n, hit_rate}}
      n           — 겉보기 표본(채점된 날 수)
      effective_n — 겹침을 반영한 유효 표본. 판정은 이쪽으로 한다.
    """
    slugs = sorted({s for d in days
                    for per_analyst in d.get("scores", {}).values()
                    for s in per_analyst})
    lag = max(int(horizon) - 1, 0)

    out = {}
    for slug in slugs:
        ics = []
        for day in days:
            rets = forward_returns.get(day.get("date"))
            if not rets:
                continue
            ic = _daily_ic(day.get("scores", {}), rets, slug)
            if ic is not None:
                ics.append(ic)

        if not ics:
            continue

        arr = np.asarray(ics, dtype=float)
        n = len(arr)
        mean_ic = float(arr.mean())
        se = newey_west_se(ics, lag)
        plain_se = float(arr.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")

        # 유효 표본: 겹침 보정으로 표준오차가 커진 만큼 표본이 줄어든 것으로 읽는다.
        if se and se > 0 and not np.isnan(se) and not np.isnan(plain_se):
            effective_n = min(n * (plain_se / se) ** 2, float(n))
        else:
            effective_n = float(n)

        out[slug] = {
            "mean_ic":     round(mean_ic, 4),
            "se":          round(se, 4) if not np.isnan(se) else None,
            "t_stat":      round(mean_ic / se, 3) if se else None,
            "n":           n,
            "effective_n": round(effective_n, 1),
            "hit_rate":    round(float((arr > 0).mean()) * 100, 1),
        }

    return out
