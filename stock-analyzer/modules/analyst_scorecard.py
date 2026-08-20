"""애널리스트 성적 채점 — 순위 예측력(IC)과 겹침 보정 표준오차.

매일 기록하고 21일 뒤 수익률로 채점하면 관측치의 선행 구간이 서로 겹친다.
겹친 관측을 독립으로 세면 표본이 실제보다 20배 많아 보이고, "n=250,
유의함" 이라는 잘못된 결론이 나온다. Newey–West 로 자기상관을 반영해
표준오차를 키운다 — 실제 정보량만큼만 인정하는 장치다.

파일도 네트워크도 모른다. 숫자만 받는다.
"""
import numpy as np
from scipy.stats import spearmanr

# 1일: **겹치는 구간이 없어 유효표본 = 겉보기 n.** 다른 지평은 매일 기록하면
#      예측 구간이 서로 겹쳐 유효표본이 n 의 0.3배(5일)~0.03배(63일)로 깎이는데,
#      1일은 lag=0 이라 newey_west_se 가 단순 표준오차와 같아진다. 스윙 판정을
#      직접 재진 못하지만 "이 점수에 단면 정보가 있긴 한가" 에 가장 먼저 답한다.
# 5일: 그 다음으로 빨리 쌓인다.
# 21일: 팩터 IC 와 같은 기준이라 나란히 비교된다.
# 63일: 느린 신호용.
HORIZONS = (1, 5, 21, 63)

# 단면 상관을 낼 최소 종목 수. 이보다 적으면 그 날은 버린다.
MIN_TICKERS_PER_DAY = 5

# "이 애널리스트를 써도 되나" 의 판정선. 겉보기 n 이 아니라 **유효표본**으로
# 잰다 — 매일 기록하면 예측 구간이 겹쳐 겉보기 n 은 쉽게 커진다.
#
# 발행선(scorecard_message.MIN_EFFECTIVE_N=10)과 다른 질문이다. 그쪽은
# "숫자를 보여줘도 되나", 이쪽은 "그 숫자로 가중치를 옮겨도 되나" 다.
#
# app.py 가 아니라 여기 있는 이유: 판정선을 읽으려고 Streamlit 을 띄워야
# 하면 성적표 상태를 밖에서(브리지·워커·스크립트) 말할 수 없다.
DECIDE_MIN_EFFECTIVE_N = 30
DECIDE_T_THRESHOLD = 2.0

# ── 스캘핑(15분봉) 지평 ──────────────────────────────────────────────
# 정규장 6.5시간 = 15분봉 26개 (미국·한국 실측 동일).
BARS_PER_SESSION_15M = 26

# 26봉(1거래일)·78봉(3거래일). 스캘핑 판정은 "당일~며칠" 을 보므로
# 5·21·63 일은 이 판정을 재는 자가 아니다.
SCALP_HORIZONS_BARS = (26, 78)

# 총괄 판정 문턱. app._team_verdict 가 verdict_of 에 위임하므로 화면과
# 성적표가 같은 선을 쓴다 — 두 곳에 숫자를 적으면 언젠가 갈라진다.
VERDICT_BUY_AT = 65.0
VERDICT_SELL_AT = 40.0

# 종합 점수에 들어가는 애널리스트와 그 결과를 담을 슬러그.
# 단순 평균인 이유는 scorecard_worker 를 참고 — 실측 IC 표본이 아직 없다.
COMBINE_SLUGS = ("chart", "ict")
COMBINED_SLUG = "combined"


def verdict_of(score):
    """점수 → 매수/중립/매도. 화면의 총괄 판정과 같은 문턱."""
    if score >= VERDICT_BUY_AT:
        return '매수'
    return '매도' if score <= VERDICT_SELL_AT else '중립'


def sessions_for_bars(bars):
    """분봉 지평을 '기록 몇 개가 겹치는지' 로 바꾼다 — Newey–West lag 용.

    기록은 하루 한 번이므로 26봉(1거래일) 지평은 겹치는 기록이 없고 78봉은
    3개가 겹친다. 여기에 봉 수를 그대로 넘기면 lag(25·77)이 표본 수보다 커져
    표준오차가 부풀고 유효표본이 0 에 붙는다 — 봉 종류가 바뀌면 창과 기준을
    함께 옮겨야 한다는 규칙이 채점 쪽에도 그대로 적용된다.
    """
    return max(int(round(int(bars) / BARS_PER_SESSION_15M)), 1)


def combined_day(day, slugs=COMBINE_SLUGS, into=COMBINED_SLUG):
    """하루치 기록을 종합 점수 한 줄로 바꾼다 — scores 가 {티커: {into: x}}.

    slugs 를 **모두** 가진 종목만 넣는다. 한쪽이 없다고 중립값 50 으로 채우면
    '계산 불가'가 '중립 판단'으로 성적에 섞인다 — analyst_log 가 값 없는
    슬러그의 키를 아예 빼는 것과 같은 규칙이다.

    한쪽만 있는 점수를 그대로 쓰는 것도 안 된다. 다른 종목은 두 축의 평균인데
    그 종목만 한 축이면 같은 자로 잰 값이 아니다.
    """
    out = {}
    for ticker, per_analyst in day.get("scores", {}).items():
        vals = [per_analyst[slug] for slug in slugs if slug in per_analyst]
        if len(vals) != len(slugs):
            continue
        out[ticker] = {into: sum(float(v) for v in vals) / len(vals)}

    combined = {"date": day.get("date", ""), "regime": day.get("regime", ""),
                "scores": out}
    if day.get("asof"):
        combined["asof"] = day["asof"]
    return combined


def combined_days(days, slugs=COMBINE_SLUGS, into=COMBINED_SLUG):
    """기록 전체를 종합 점수로 바꾼다. 남는 종목이 없는 날은 버린다.

    빈 날을 남기면 표본으로 세지지는 않지만 dates 목록만 길어져 가격 패널을
    쓸데없이 넓게 잡는다.
    """
    converted = [combined_day(day, slugs, into) for day in days]
    return [day for day in converted if day["scores"]]


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


def build_forward_returns(prices, dates, horizon):
    """기록된 날짜별 선행수익률 — {date: {ticker: pct}}.

    prices : {ticker: 종가 Series (DatetimeIndex)}
    dates  : 채점할 날짜 문자열 목록 ("YYYY-MM-DD")

    horizon 거래봉 뒤의 가격과 비교한다. 아직 미래가 안 온 날짜(선행 구간이
    데이터 끝을 넘는 경우)는 **그 종목을 넣지 않는다** — 마지막 가격으로
    대신하면 최근 기록이 전부 수익률 0 근처로 눌려 성적이 왜곡된다.
    """
    import pandas as pd

    out = {}
    for date_str in dates:
        as_of = pd.Timestamp(date_str)
        row = {}
        for ticker, series in prices.items():
            if series is None or len(series) == 0:
                continue
            past = series[series.index <= as_of]
            if len(past) == 0:
                continue

            start_pos = len(past) - 1
            end_pos = start_pos + int(horizon)
            if end_pos >= len(series):
                continue            # 아직 미래가 오지 않았다

            cur = float(series.iloc[start_pos])
            fwd = float(series.iloc[end_pos])
            if cur > 0:
                row[ticker] = (fwd / cur - 1.0) * 100.0

        if row:
            out[date_str] = row

    return out


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

        # 표본이 lag 의 두 배에 못 미치면 Newey–West 는 겹침을 못 잰다. 그때
        # 추정된 표준오차는 통상 표준오차보다 **작게** 나오기도 하는데, 아래
        # min(..., n) 이 그걸 "유효표본 = 전체" 로 바꿔 t 를 그대로 통과시킨다
        # — 추정기가 무너진 바로 그 순간에 가장 확신에 찬 숫자가 나온다.
        #
        # 실제로 겪었다: 국면별로 쪼개 bear 20일 × 63일 지평을 재니 유효표본
        # 20.0 · t -20.6 이 나왔다. lag(62)이 표본(20)보다 크니 계산이 성립할
        # 수 없는 자리인데 화면에는 '판정 가능' 으로 보일 값이었다.
        #
        # 못 재면 못 잰다고 한다 — se=None 이면 t_stat 도 None 이 되고,
        # 화면과 발행문은 이미 그걸 "통계적 판단 불가" 로 읽는다.
        if n <= 2 * lag:
            out[slug] = {
                "mean_ic":     round(mean_ic, 4),
                "se":          None,
                "t_stat":      None,
                "n":           n,
                "effective_n": 0.0,
                "hit_rate":    round(float((arr > 0).mean()) * 100, 1),
            }
            continue

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


def scored_dates(days, forward_returns, slug):
    """그 슬러그가 **실제로 채점된** 날짜 목록. score_analysts 와 같은 규칙.

    표본이 무엇으로 이뤄졌는지는 로그에 뭐가 있나가 아니라 채점에 뭐가
    들어갔나로 세야 한다. 21·63일 지평은 선행 구간이 아직 안 지난 최근
    기록을 통째로 버리므로, 로그 기준으로 세면 실기록이 표본에 들어간
    것처럼 보인다 — 실측 2026-08-20, 21일 지평의 실기록 채점일은 0 인데
    analyst_log.sample_mix() 는 18일이라고 답했다.

    n 은 score_analysts 가 답한다. 이 함수는 '무엇인가' 만 답한다.
    """
    out = []
    for day in days:
        rets = forward_returns.get(day.get("date"))
        if not rets:
            continue
        if _daily_ic(day.get("scores", {}), rets, slug) is not None:
            out.append(day.get("date"))
    return out


def verdict_hit_rate(days, returns, slug=COMBINED_SLUG):
    """총괄 판정의 방향 적중률 — 화면에 뜬 매수/매도가 실제로 맞았나.

    days    : combined_days() 를 거친 기록 (scores 가 {티커: {slug: 점수}})
    returns : {date: {ticker: pct}} — 그 지평의 선행수익률

    score_analysts 의 hit_rate 와 다른 것을 잰다. 그쪽은 '그 날 순위 예측이
    맞은 방향이었나'(단면 IC 가 양수인 날의 비율)이고, 이쪽은 '매수라고 한
    종목이 실제로 올랐나'(판정 하나하나)다. 사장님이 화면에서 보는 것은
    이쪽이다.

    중립 판정은 분모에서 뺀다 — 맞고 틀림이 정의되지 않는 판정을 세면
    적중률이 언제나 50% 쪽으로 눌린다. 판정을 안 낸 것이 성적을 좋게도
    나쁘게도 만들면 안 된다. 수익률 정확히 0% 는 빗나감으로 센다 —
    방향을 맞히지 못한 것이다.
    """
    hits = buy_n = sell_n = neutral_n = 0

    for day in days:
        rets = returns.get(day.get("date")) or {}
        for ticker, per_analyst in day.get("scores", {}).items():
            score = per_analyst.get(slug)
            ret = rets.get(ticker)
            if score is None or ret is None:
                continue
            ret = float(ret)
            if np.isnan(ret):
                continue

            verdict = verdict_of(float(score))
            if verdict == '중립':
                neutral_n += 1
                continue
            if verdict == '매수':
                buy_n += 1
                hits += 1 if ret > 0 else 0
            else:
                sell_n += 1
                hits += 1 if ret < 0 else 0

    n = buy_n + sell_n
    return {
        "hit_rate":  round(hits / n * 100, 1) if n else None,
        "n":         n,
        "hits":      hits,
        "buy_n":     buy_n,
        "sell_n":    sell_n,
        "neutral_n": neutral_n,
    }
