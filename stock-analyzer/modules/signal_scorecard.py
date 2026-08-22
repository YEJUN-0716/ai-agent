"""시그널 성적표 — 평가가 끝난 시그널로 실제 성과를 집계한다.

signal_log.json 에 쌓이는 시그널은 발생 21일 뒤 return_pct 가 채워진다.
이 모듈은 그 완료분만 모아서 "이 시스템을 따라갔다면 어땠는가"를 계산한다.

설계 원칙 두 가지:
1. **표본이 부족하면 부족하다고 말한다.** 승률 70% 도 n=3 이면 아무 의미가 없다.
   verdict 가 표본 수와 t 검정을 근거로 판정을 문장으로 돌려준다.
2. **벤치마크 대비를 같이 낸다.** 강세장에서는 아무 종목이나 사도 오른다.
   초과수익이 없으면 적중률이 높아도 시스템의 기여는 0 이다.
"""

import math

import pandas as pd

# 판정 기준 — 표본 30건 미만은 통계로 다루지 않는다는 퀀트 관행
MIN_SAMPLE = 30
SIGNIFICANT_T = 2.0

# 채점 지평 — 진입 다음 봉부터 세어 이 **거래일째** 종가로 잰다.
SIGNAL_HORIZON_BARS = 21


def score_signal(closes, entry_date, entry_price, horizon=SIGNAL_HORIZON_BARS):
    """진입 후 `horizon` **거래일째** 종가로 채점. 봉이 모자라면 None.

    반환 {"return_pct", "outcome_date", "outcome_price"} — 값과 **그 값이 어느
    봉의 것인지**를 같이 준다. 날짜를 따로 채우게 두면 채점 시점이 부르는
    쪽마다 달라진다.

    예전엔 "달력 21일이 지났으면 **지금 현재가**" 로 쟀다. 그러면 재는 날이
    채점일이 된다 — 화면은 여는 날, 러너는 도는 날. **채점 시점은 부르는
    쪽이 아니라 봉이 정한다.**

    이 규칙의 사본이 두 벌이었다(app.py 화면 / paper_trade_runner_toss 러너).
    화면만 고쳐져 있었고, `signal_log.json` 에 실제로 값을 쓰는 건 매일 도는
    러너였다 — 채점된 11건 전부가 21봉이 아니라 15~16봉에서 찍혔고 그중 5건은
    아직 21봉이 안 찬 건이었다(2026-08-22 실측). 그래서 규칙을 여기 한 곳에
    두고 양쪽이 부른다.

    Streamlit·네트워크를 모른다 — 산술을 테스트로 잠글 수 있어야 한다.
    """
    if not entry_price or float(entry_price) <= 0 or closes is None or len(closes) == 0:
        return None
    idx = pd.to_datetime(closes.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    # 진입일 당일은 이미 체결된 값이라 세지 않는다.
    after = pd.Series(closes.values, index=idx).dropna()
    after = after[after.index > pd.Timestamp(entry_date)]
    if len(after) < horizon:
        return None
    bar = after.index[horizon - 1]
    price = float(after.iloc[horizon - 1])
    return {"return_pct": round((price / float(entry_price) - 1) * 100, 2),
            "outcome_date": bar.strftime("%Y-%m-%d"),
            "outcome_price": round(price, 2)}


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def max_drawdown(returns_pct):
    """시그널을 순서대로 1회씩 따라갔을 때 누적 곡선의 최대낙폭(%, 음수).

    각 시그널을 독립 1회 베팅으로 보고 복리 누적한다 — 여러 개를 동시에 들고 갔을 때의
    실제 낙폭과는 다르지만, 시스템의 연속 손실 크기를 조작 여지 없이 보여준다."""
    if not returns_pct:
        return 0.0
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in returns_pct:
        equity *= (1 + r / 100)
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    return mdd * 100


def verdict(n, expectancy, t_stat):
    """표본 수와 유의성을 근거로 한 문장 판정 — 숫자를 과대 해석하지 않게 막는 장치."""
    if n == 0:
        return "표본 없음 — 평가가 끝난 시그널이 아직 없습니다."
    if n < MIN_SAMPLE:
        return (f"표본 부족 (n={n} < {MIN_SAMPLE}) — 승률·기대값은 참고용이며 "
                f"통계적 결론을 내릴 수 없습니다.")
    if abs(t_stat) < SIGNIFICANT_T:
        return (f"유의하지 않음 (n={n}, t={t_stat:.2f}) — 기대값 {expectancy:+.2f}% 는 "
                f"우연과 구분되지 않습니다.")
    if expectancy > 0:
        return (f"유의한 양의 기대값 (n={n}, t={t_stat:.2f}, {expectancy:+.2f}%) — "
                f"다만 표본 기간의 시장 국면 편향은 따로 확인해야 합니다.")
    return (f"유의한 음의 기대값 (n={n}, t={t_stat:.2f}, {expectancy:+.2f}%) — "
            f"현재 규칙을 그대로 따르면 손실이 누적됩니다.")


def summarize_signals(signals, benchmark_key='benchmark_return_pct'):
    """완료된 시그널 목록 → 성과 요약 dict.

    signals        : signal_log.json 의 'signals' 리스트. return_pct 가 None 이면 미평가로 제외.
    benchmark_key  : 같은 기간 벤치마크 수익률이 기록돼 있으면 초과수익도 계산한다.
                     완료 시그널 전부에 값이 있을 때만 계산한다(일부만 있으면 비교가 왜곡된다).
    """
    done = [s for s in signals if s.get('return_pct') is not None]
    pending = [s for s in signals if s.get('return_pct') is None]
    rets = [float(s['return_pct']) for s in done]

    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    expectancy = _mean(rets)          # 1회 진입당 평균 기대 수익률(%)
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    profit_factor = ((gross_win / gross_loss) if gross_loss > 0
                     else (float('inf') if gross_win else 0.0))

    sd = _stdev(rets)
    t_stat = (expectancy / (sd / math.sqrt(n))) if n > 1 and sd > 0 else 0.0

    bench = [float(s[benchmark_key]) for s in done if s.get(benchmark_key) is not None]
    alpha = (expectancy - _mean(bench)) if (n and len(bench) == n) else None

    return {
        'n': n,
        'pending': len(pending),
        'hit_rate': (len(wins) / n * 100) if n else 0.0,
        'expectancy': expectancy,
        'avg_win': _mean(wins),
        'avg_loss': _mean(losses),
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown(rets),
        'stdev': sd,
        't_stat': t_stat,
        'alpha_vs_benchmark': alpha,
        'verdict': verdict(n, expectancy, t_stat),
    }


def by_score_bucket(signals, edges=(0, 60, 70, 80, 101)):
    """점수 구간별 성과 — 점수가 높을수록 실제로 더 잘 맞는지(단조성) 확인용.
    단조성이 깨지면 점수 자체에 예측력이 없다는 신호다."""
    done = [s for s in signals
            if s.get('return_pct') is not None and s.get('score') is not None]
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        rets = [float(s['return_pct']) for s in done if lo <= float(s['score']) < hi]
        out.append({
            'bucket': f"{lo}-{hi - 1}",
            'n': len(rets),
            'hit_rate': (len([r for r in rets if r > 0]) / len(rets) * 100) if rets else 0.0,
            'avg_return': _mean(rets),
        })
    return out
