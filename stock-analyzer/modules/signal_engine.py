"""
시스템 트레이딩 신호 엔진 — 규칙 기반 매수/매도/리밸런싱 판정
=============================================================
app.py 에서 추출했다. Streamlit·yfinance 의존성이 없다.

**가격 조회와 지표 계산을 인자로 주입받는다.** app.py 가 이 모듈을 import
하므로 반대 방향 import 는 순환이 된다. 주입 방식이면 순환도 없고, 테스트가
합성 가격 프레임을 그대로 밀어 넣을 수 있다.

판정 임계값은 전부 모듈 상수로 올려 두었다 — app.py 안에 매직넘버로 흩어져
있을 때는 "RSI 70이 어디서 왔는지" 추적이 안 됐다. 값을 바꾸면
tests/test_system_signals.py 가 잡는다.
"""
from datetime import datetime, timedelta

import numpy as np

# ── 팩터 점수 임계 ──────────────────────────────────────────────────
TOP_FACTOR_SCORE = 75      # 이 이상이면 '최상위' — 추세 확인 없이도 매수 후보
STRONG_FACTOR_SCORE = 50   # 이 이상이면 조건부 매수까지는 허용

# ── RSI 임계 ────────────────────────────────────────────────────────
OVERSOLD_RSI = 35
OVERBOUGHT_RSI = 70
# 강한 상승추세에서는 과매수 임계를 완화한다. 모멘텀 종목은 RSI 70을 오래
# 유지하며 오르기 때문에, 기본 임계로는 정작 잘 가는 종목을 계속 걸러낸다.
OVERBOUGHT_RSI_STRONG_TREND = 80

# ── 추세 강도 (ADX) ────────────────────────────────────────────────
STRONG_TREND_ADX = 25
ADX_FALLBACK = 20          # ADX가 NaN(데이터 부족)이면 '약한 추세'로 간주

# ── 거래량 확인 ────────────────────────────────────────────────────
VOLUME_CONFIRM_RATIO = 0.8   # 20일 평균 대비 이 비율 이상이면 신호 신뢰
VOLUME_LOOKBACK = 20

# ── 비중 조절 ──────────────────────────────────────────────────────
CONDITIONAL_BUY_WEIGHT = 0.7   # 조건부 매수는 목표 비중의 70%만
TRIM_WEIGHT = 0.5              # 하락추세 비중축소는 절반

# ── 데이터 요건 ────────────────────────────────────────────────────
PRICE_LOOKBACK_DAYS = 120
MIN_BARS = 30              # 이보다 짧으면 지표를 믿을 수 없어 건너뛴다
MA_SHORT = 20
MA_LONG = 60

# ── 리밸런싱 주기 ──────────────────────────────────────────────────
REBALANCE_CYCLE_DAYS = 20


def _split_candidates(tickers, factor_df, top_n):
    """팩터 랭킹 → (매수 후보, 매도 후보).

    매수는 상위 top_n, 매도는 하위 1/3. 팩터 테이블이 없으면 입력 순서
    앞 top_n개를 매수 후보로 쓰고 매도 후보는 비운다 (근거 없이 팔지 않는다).
    """
    if factor_df is None or factor_df.empty:
        return tickers[:top_n], []
    buy = factor_df.head(top_n)['ticker'].tolist()
    sell = factor_df.tail(max(len(factor_df) // 3, 1))['ticker'].tolist()
    return buy, sell


def _factor_score(factor_df, ticker):
    """해당 종목의 composite 점수. 테이블에 없으면 0."""
    if factor_df is None or factor_df.empty:
        return 0
    row = factor_df[factor_df['ticker'] == ticker]
    return float(row['composite'].iloc[0]) if not row.empty else 0


def _next_rebalance_days(today=None):
    """다음 리밸런싱까지 남은 일수 (연중 일수 기준 20일 주기)."""
    today = today or datetime.now()
    return (REBALANCE_CYCLE_DAYS
            - today.timetuple().tm_yday % REBALANCE_CYCLE_DAYS) % REBALANCE_CYCLE_DAYS


def generate_system_signals(tickers, *, fetch_prices, calc_rsi, calc_momentum, calc_adx,
                            factor_df=None, weights=None, top_n=5, capital=10000):
    """
    규칙 기반 매수/매도/리밸런싱 시그널을 만든다.

    주입 인자:
      fetch_prices(ticker, start, end) -> OHLCV DataFrame (빈 프레임이면 건너뜀)
      calc_rsi(close_series) -> RSI Series
      calc_momentum(df) -> {'3M': ..., ...}
      calc_adx(high, low, close) -> (adx, pdi, ndi) Series 3개

    반환: (actions, rebal_info)
      actions    — 종목별 판정 dict 리스트 (signal_worker 가 텔레그램 메시지로 렌더)
      rebal_info — 다음 리밸런싱 시점 + 매수/매도/보유 건수 집계
    """
    actions = []
    end = datetime.now()
    buy_candidates, sell_candidates = _split_candidates(tickers, factor_df, top_n)

    for tk in tickers:
        try:
            df = fetch_prices(tk, end - timedelta(days=PRICE_LOOKBACK_DAYS), end)
            if df.empty or len(df) < MIN_BARS:
                continue
            df = df.dropna(subset=['Close'])
            p = df['Close']
            cp = float(p.iloc[-1])

            rsi = float(calc_rsi(p).iloc[-1])
            ma_short = float(p.rolling(MA_SHORT).mean().iloc[-1])
            ma_long = float(p.rolling(MA_LONG).mean().iloc[-1])
            mom_3m = calc_momentum(df).get('3M', 0) or 0

            in_buy = tk in buy_candidates
            in_sell = tk in sell_candidates
            trend_up = cp > ma_short > ma_long
            trend_dn = cp < ma_short < ma_long

            # 거래량 확인 — 평균 대비 부족하면 신호 신뢰도가 낮다
            avg_volume = float(df['Volume'].rolling(VOLUME_LOOKBACK).mean().iloc[-1])
            vr = float(df['Volume'].iloc[-1]) / (avg_volume + 1e-9)
            vol_confirm = vr >= VOLUME_CONFIRM_RATIO

            adx_s, pdi_s, ndi_s = calc_adx(df['High'], df['Low'], p)
            adx_v = float(adx_s.iloc[-1])
            if np.isnan(adx_v):
                adx_v = ADX_FALLBACK
            strong_trend_up = (trend_up and adx_v > STRONG_TREND_ADX
                               and float(pdi_s.iloc[-1]) > float(ndi_s.iloc[-1]))

            overbought_th = OVERBOUGHT_RSI_STRONG_TREND if strong_trend_up else OVERBOUGHT_RSI
            oversold = rsi < OVERSOLD_RSI
            overbought = rsi > overbought_th

            target_w = weights.get(tk, 0) if weights else (1.0 / top_n if in_buy else 0)
            f_score_v = _factor_score(factor_df, tk)
            is_top_factor = f_score_v >= TOP_FACTOR_SCORE
            is_strong_factor = f_score_v >= STRONG_FACTOR_SCORE

            fp = f"${cp:.2f}"

            def _make_action(action, tw, reason, priority):
                alloc = capital * tw
                qty = alloc / cp if cp > 0 else 0
                qty_str = f"{qty:,.2f}주"
                alloc_str = f"${alloc:,.0f}"
                return {'ticker': tk, 'action': action, 'weight': f"{tw*100:.1f}%",
                        'price': fp, 'alloc': alloc_str, 'qty': qty_str,
                        'reason': reason, 'priority': priority, 'mom': f"{mom_3m:+.1f}%"}

            # 분기 순서가 곧 우선순위다. 위쪽 조건이 먼저 걸리므로
            # '팩터 최상위' 판정이 '과매도 반등' 판정을 앞선다.
            if in_buy and is_top_factor and not overbought and vol_confirm:
                actions.append(_make_action('🟢 매수', target_w,
                    f"팩터 {f_score_v:.0f}점 (최상위) — 거래량 확인 (RSI {rsi:.0f})", 'HIGH'))
            elif in_buy and is_top_factor and not overbought and not vol_confirm:
                actions.append(_make_action('🟡 조건부 매수', target_w * CONDITIONAL_BUY_WEIGHT,
                    f"팩터 {f_score_v:.0f}점 (최상위) — 거래량 부족({vr:.1f}×), 소량 선진입", 'NORMAL'))
            elif in_buy and (trend_up or oversold) and not overbought:
                vol_note = '' if vol_confirm else f' | 거래량 주의({vr:.1f}×)'
                actions.append(_make_action('🟢 매수', target_w,
                    f"팩터 {f_score_v:.0f}점 + {'과매도 반등' if oversold else '상승추세'} (RSI {rsi:.0f}){vol_note}",
                    'HIGH' if oversold else 'NORMAL'))
            elif in_buy and is_strong_factor and not overbought:
                actions.append(_make_action('🟡 조건부 매수', target_w * CONDITIONAL_BUY_WEIGHT,
                    f"팩터 {f_score_v:.0f}점 — 추세 확인 시 비중 확대 (RSI {rsi:.0f})", 'NORMAL'))
            elif in_buy:
                actions.append(_make_action('🟡 대기', target_w,
                    f"팩터 {f_score_v:.0f}점, 추세·팩터 모두 약함 (RSI {rsi:.0f})", 'LOW'))
            elif in_sell or (trend_dn and overbought):
                actions.append(_make_action('🔴 매도', 0,
                    f"팩터 {f_score_v:.0f}점 하위{'+ 하락추세' if trend_dn else ''} (RSI {rsi:.0f})", 'HIGH'))
            elif trend_dn:
                actions.append(_make_action('🟠 비중축소', target_w * TRIM_WEIGHT,
                    f"하락추세 (RSI {rsi:.0f})", 'NORMAL'))
            else:
                actions.append(_make_action('⚪ 관망', target_w,
                    f"팩터 {f_score_v:.0f}점 — 뚜렷한 방향 없음 (RSI {rsi:.0f})", 'LOW'))
        except Exception:
            # 한 종목이 죽어도 배치 전체를 멈추지 않는다 — signal_worker 는
            # 수백 종목을 한 번에 돌린다.
            continue

    rebal_days = _next_rebalance_days()
    rebal_info = {
        'next_rebal': '오늘 리밸런싱' if rebal_days == 0 else f"{rebal_days}일 후",
        'buy_count': sum(1 for a in actions if '매수' in a['action']),
        'sell_count': sum(1 for a in actions if '매도' in a['action'] or '축소' in a['action']),
        'hold_count': sum(1 for a in actions if '관망' in a['action'] or '대기' in a['action']),
    }
    return actions, rebal_info
