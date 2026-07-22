"""
팩터 점수 산출 — 순수 계산부
================================
app.py::calc_factor_scores 에서 추출했다. Streamlit·yfinance 의존성이 없고,
네트워크도 타지 않는다. 가격 프레임과 재무 dict 를 **받아서** 점수를 낸다.

signal_engine 때와 달리 조회 함수를 주입받지 않는다. 이 함수의 I/O 는
종목별 순차 다운로드 + 레이트리밋 슬립 + 진행률 표시라, 콜백 다섯 개를
받아 봐야 지저분함이 자리만 옮길 뿐이다. 대신 **경계를 다르게 그었다** —
I/O 루프는 app.py 에 남기고, 조용히 틀어질 수 있는 계산만 여기로 옮긴다.

임계값과 가중치는 전부 모듈 상수다. app.py 안에 매직넘버로 흩어져 있을 때는
"low_vol 0.08 이 어디서 왔는지" 추적이 안 됐다.

tests/test_factor_scores.py 가 여기 공식을 전부 고정한다.
"""
import numpy as np
import pandas as pd

from modules.factor_formulas import (
    accrual_quality,
    book_yield,
    earnings_yield,
    quality_raw,
    value_raw,
)

# ── 데이터 요건 ────────────────────────────────────────────────────
MIN_BARS = 30              # 이보다 짧으면 지표를 믿을 수 없어 종목을 버린다
VOLUME_WINDOW = 20         # 유동성 필터용 평균 거래량 구간

# ── 모멘텀 (P1-A: skip-1M) ─────────────────────────────────────────
# 최근 1개월은 단기 역전(short-term reversal)이 지배해 모멘텀 신호를 오염시킨다.
# 그래서 12개월 수익률에서 마지막 1개월을 잘라낸 2~12개월 구간을 쓴다.
MOMENTUM_SKIP_DAYS = 21
MOMENTUM_LOOKBACK_DAYS = 252

# ── 변동성 (P2-A) ──────────────────────────────────────────────────
# 전 구간 표준편차를 쓰면 몇 년 전 급등락이 현재 점수를 계속 끌고 다닌다.
VOL_WINDOW = 252
TRADING_DAYS_PER_YEAR = 252
LOW_VOL_BASE = 100.0       # low_vol 점수 = 100 - 연변동성(%), 0 하한

# ── 정규화 (z-score → 점수) ────────────────────────────────────────
WINSOR_LOW, WINSOR_HIGH = 0.01, 0.99   # 극단값 하나가 랭킹을 독점하지 못하게
SCORE_MEAN = 50.0
SCORE_PER_SIGMA = 15.0                 # ±2σ 가 20/80 이 되도록
SCORE_MIN, SCORE_MAX = 20.0, 80.0

# ── 기본 팩터 가중치 ───────────────────────────────────────────────
# P1-B: low_vol 축소 (IC ICIR = -0.199). 순서도 의미가 있다 — composite 계산이
# 이 dict 를 그대로 순회한다.
DEFAULT_FACTOR_WEIGHTS = {
    'momentum': 0.35,
    'value': 0.25,
    'quality': 0.32,
    'low_vol': 0.08,
}

# P2-C: 주간 IC 가중치는 기본값을 갈아엎지 않고 절반만 섞는다. IC 추정치는
# 표본 잡음이 커서, 통째로 대체하면 한 주의 노이즈가 다음 주 포트폴리오를 흔든다.
IC_BLEND_WEIGHT = 0.5

# 선택적 추가 팩터. 각 가중치는 합성점수에서 고정 몫을 떼어 가고,
# 나머지를 기본 4팩터가 나눠 갖는다.
EXTRA_FACTOR_WEIGHTS = (
    ('analyst_raw', 0.10),
    ('short_raw', 0.05),
    ('eps_surprise_raw', 0.10),
    ('ict_raw', 0.10),
)

# ── 추가 팩터 스케일 ───────────────────────────────────────────────
ANALYST_BEST, ANALYST_WORST = 1.0, 5.0   # yfinance 추천지표: 1=강력매수
SHORT_RATIO_SCALE = 8.0                  # 공매도비율 12.5 이상이면 0점
EPS_SURPRISE_QUARTERS = 4
EPS_ESTIMATE_FLOOR = 0.01                # 0 근처 추정치로 나눠 폭발하는 것 방지

NAME_MAX_LEN = 20
KRX_SUFFIXES = ('.KS', '.KQ')

BASE_RAW_COLUMNS = ('momentum_raw', 'value_raw', 'quality_raw', 'low_vol_raw')


# ── 정규화 ──────────────────────────────────────────────────────────

def zscore_to_score(series):
    """Z-score → 20~80 점수. 평균 50, ±2σ 가 20/80. 1~99% 윈저라이징.

    표준편차가 0이면(전 종목 동일) z-score 가 정의되지 않는다. NaN 을
    흘려보내면 composite 전체가 NaN 이 되므로 중립 50점으로 되돌린다.
    """
    lo, hi = series.quantile(WINSOR_LOW), series.quantile(WINSOR_HIGH)
    series = series.clip(lo, hi)
    m, s = series.mean(), series.std()
    if pd.isna(s) or s < 1e-9:
        return pd.Series(SCORE_MEAN, index=series.index)
    z = (series - m) / s
    return (z * SCORE_PER_SIGMA + SCORE_MEAN).clip(SCORE_MIN, SCORE_MAX).fillna(SCORE_MEAN)


# ── 가격 기반 팩터 ──────────────────────────────────────────────────

def clean_price_frame(df, min_avg_volume=0):
    """스캔에 쓸 수 있는 프레임인지 판정하고 정리한다. 못 쓰면 None.

    None 을 돌려주는 세 경우 — 프레임이 비었거나, 30봉 미만이거나,
    유동성 필터에 걸렸거나. 호출부는 셋을 구분하지 않고 '실패' 로 묶는다.
    """
    if df is None or df.empty:
        return None
    df = df.dropna(subset=['Close'])
    if len(df) < MIN_BARS:
        return None
    if min_avg_volume > 0:
        avg_vol = (float(df['Volume'].tail(VOLUME_WINDOW).mean())
                   if 'Volume' in df.columns else 0)
        if avg_vol < min_avg_volume:
            return None
    return df


def price_factors(df):
    """clean_price_frame 을 통과한 프레임 → 가격 기반 원점수."""
    close = df['Close']
    if len(df) >= MOMENTUM_LOOKBACK_DAYS:
        momentum = (float(close.iloc[-MOMENTUM_SKIP_DAYS])
                    / float(close.iloc[-MOMENTUM_LOOKBACK_DAYS]) - 1) * 100
    else:
        # 1년치가 없으면 추정하지 않는다. 짧은 구간 모멘텀은 잡음이다.
        momentum = 0

    daily_ret = close.pct_change().dropna()
    annual_vol = (float(daily_ret.tail(VOL_WINDOW).std())
                  * np.sqrt(TRADING_DAYS_PER_YEAR) * 100)
    return {
        'price': float(close.iloc[-1]),
        'momentum_raw': round(momentum, 2),
        'low_vol_raw': round(max(LOW_VOL_BASE - annual_vol, 0), 2),
        'vol': round(annual_vol, 1),
    }


# ── 재무 기반 팩터 ──────────────────────────────────────────────────

def _dart_filled(ticker, roe_v, pm_v, dart_row):
    """KRX 종목의 빈 ROE·이익률을 DART 값으로 메운다.

    yfinance 는 KRX 재무를 자주 비운다. 이미 값이 있으면 덮어쓰지 않는다 —
    DART margin 은 영업이익률이라 순이익률의 근사치로만 쓸 수 있기 때문이다.
    """
    if not ticker.endswith(KRX_SUFFIXES) or (roe_v and pm_v):
        return roe_v, pm_v
    dd = dart_row or {}
    if not roe_v and dd.get('net_income') and dd.get('equity'):
        try:
            roe_v = round(dd['net_income'] / dd['equity'] * 100, 2)
        except ZeroDivisionError:
            pass
    if not pm_v and dd.get('margin') is not None:
        pm_v = dd['margin']
    return roe_v, pm_v


def fundamental_factors(ticker, info, dart_row=None):
    """재무 dict → 밸류·퀄리티 원점수. info 가 비어도 예외 없이 0점 계열을 돌려준다.

    배합 계수는 factor_formulas 소유다 (factor_engine.py 와 공유).
    """
    info = info or {}
    per = info.get('trailingPE') or info.get('forwardPE')
    pbr = info.get('priceToBook')

    roe = info.get('returnOnEquity')
    roe_v = round(roe * 100, 2) if roe is not None else 0
    pm = info.get('profitMargins')
    pm_v = (pm * 100 if pm else 0)

    # shortName 이 None 으로 들어오는 종목이 있다. 기본값 인자만으로는
    # 키가 존재하는 한 None 이 그대로 나와 슬라이싱에서 터진다.
    name = (info.get('shortName') or ticker)[:NAME_MAX_LEN]

    # P3-A: FCF 수익률 — 음수 FCF 도 그대로 반영한다 (현금 소각 페널티)
    fcf = info.get('freeCashflow')
    mcap = info.get('marketCap')
    fcf_yield_pct = fcf / mcap * 100 if (fcf is not None and mcap and mcap > 0) else 0.0
    # P3-B: 발생액 품질
    accrual_q = accrual_quality(fcf, info.get('netIncomeToCommon'))

    roe_v, pm_v = _dart_filled(ticker, roe_v, pm_v, dart_row)

    return {
        'name': name,
        'per': per,
        'pbr': pbr,
        'roe': roe_v,
        'value_raw': round(value_raw(earnings_yield(per), book_yield(pbr),
                                     fcf_yield_pct), 2),
        'quality_raw': round(quality_raw(roe_v, pm_v, accrual_q), 2),
    }


# ── 추가 팩터 스케일 (전부 "낮을수록 좋다" 를 뒤집는다) ─────────────

def analyst_score(recommendation_mean):
    """1=강력매수 ~ 5=강력매도 → 0~100 (높을수록 좋음). 범위 밖이면 None."""
    rec = recommendation_mean
    if not rec or not (ANALYST_BEST <= rec <= ANALYST_WORST):
        return None
    return round((ANALYST_WORST - rec) / (ANALYST_WORST - ANALYST_BEST) * 100, 1)


def short_ratio_score(short_ratio):
    """공매도 비율 → 0~100. 낮을수록 좋으므로 뒤집는다."""
    if short_ratio is None or short_ratio < 0:
        return None
    return round(max(0, 100 - min(short_ratio * SHORT_RATIO_SCALE, 100)), 1)


def eps_surprise_score(earnings_history):
    """최근 4분기 EPS 서프라이즈 평균(%). 계산 불가면 None."""
    eh = earnings_history
    if eh is None or eh.empty:
        return None
    need = {'epsEstimate', 'epsActual'}
    if not need.issubset(eh.columns):
        return None
    recent = eh.dropna(subset=list(need)).tail(EPS_SURPRISE_QUARTERS)
    if recent.empty:
        return None
    surprise = ((recent['epsActual'] - recent['epsEstimate'])
                / recent['epsEstimate'].abs().clip(lower=EPS_ESTIMATE_FLOOR) * 100)
    return round(float(surprise.mean()), 1)


# ── 합성과 랭킹 ─────────────────────────────────────────────────────

def blend_ic_weights(ic_weights):
    """주간 IC 가중치를 기본값과 50:50 으로 섞는다. IC 가 없으면 기본값 그대로."""
    if not ic_weights:
        return dict(DEFAULT_FACTOR_WEIGHTS)
    return {k: d * (1 - IC_BLEND_WEIGHT) + ic_weights.get(k, d) * IC_BLEND_WEIGHT
            for k, d in DEFAULT_FACTOR_WEIGHTS.items()}


def rank_by_composite(rows, factor_weights=None, ic_weights=None):
    """원점수 행들 → 정규화 + 가중 합성 + 랭킹된 DataFrame.

    factor_weights 를 명시하면 그대로 쓰고, None 이면 IC 블렌딩 결과를 쓴다.
    추가 팩터 컬럼이 있으면 각자의 고정 몫을 떼어 가고 나머지를 4팩터가 나눈다.
    """
    rdf = pd.DataFrame(rows)
    for col in BASE_RAW_COLUMNS:
        rdf[col.replace('_raw', '')] = zscore_to_score(rdf[col])

    extra_cols, extra_w = [], []
    for col, w in EXTRA_FACTOR_WEIGHTS:
        if col in rdf.columns:
            fname = col.replace('_raw', '')
            rdf[fname] = zscore_to_score(rdf[col])
            extra_cols.append(fname)
            extra_w.append(w)
    base_w = 1 - sum(extra_w)

    fw = factor_weights if factor_weights is not None else blend_ic_weights(ic_weights)
    fw_sum = sum(fw.values()) or 1.0

    rdf['composite'] = sum(
        rdf[name] * fw.get(name, default) / fw_sum * base_w
        for name, default in DEFAULT_FACTOR_WEIGHTS.items()
    )
    for fname, w in zip(extra_cols, extra_w):
        rdf['composite'] += rdf[fname] * w

    rdf = rdf.sort_values('composite', ascending=False).reset_index(drop=True)
    rdf['rank'] = range(1, len(rdf) + 1)
    return rdf
