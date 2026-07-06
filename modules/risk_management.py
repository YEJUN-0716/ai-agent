"""
① 포지션 사이징 & 리스크 관리
=================================================================
기존 run_backtest()는 신호가 뜨면 "전액 매수 / 전액 매도"(all-in/all-out)
방식이었다. 이 모듈은:

1. 변동성 타겟팅(volatility targeting) — 종목 변동성에 반비례해서 비중 조절
2. 신호 강도 비례 사이징 — 신호 65점과 95점을 다르게 취급
3. Kelly 공식 기반 사이징 — app.py의 kelly_fraction()과 동일 공식, 연동 가능하게 재구현
4. 드로다운 서킷브레이커 — MDD가 임계치를 넘으면 익스포저 자동 축소
5. 위 요소를 모두 반영한 run_backtest_sized() — run_backtest를 확장

app.py 연동 방법: run_backtest() 대신 run_backtest_sized()를 옵션으로 호출.
"""
import numpy as np
import pandas as pd


# ─────────────────────────────────────────────
# 1) 변동성 타겟팅
# ─────────────────────────────────────────────
def volatility_target_multiplier(returns: pd.Series, target_vol: float = 0.15,
                                  lookback: int = 20, min_mult: float = 0.2,
                                  max_mult: float = 1.5) -> pd.Series:
    """
    최근 lookback일 실현변동성(연율화) 대비 목표변동성 비율로 포지션 배율을 계산.
    변동성이 목표보다 높으면 비중을 줄이고, 낮으면 (상한 내에서) 늘린다.

    반환: returns와 같은 인덱스를 가진 배율(Series). 1.0 = 기본 100% 비중.
    """
    realized_vol = returns.rolling(lookback).std() * np.sqrt(252)
    mult = (target_vol / realized_vol.replace(0, np.nan)).clip(min_mult, max_mult)
    return mult.fillna(1.0)


# ─────────────────────────────────────────────
# 2) 신호 강도 비례 사이징
# ─────────────────────────────────────────────
def signal_strength_weight(signal: float, buy_th: float, max_signal: float = 100.0) -> float:
    """
    신호가 buy_th를 갓 넘겼으면 작게, max_signal에 가까우면 크게 진입.
    buy_th~max_signal 구간을 0.3~1.0 비중으로 선형 매핑.
    """
    if signal <= buy_th:
        return 0.0
    span = max(max_signal - buy_th, 1e-9)
    frac = min((signal - buy_th) / span, 1.0)
    return 0.3 + 0.7 * frac


# ─────────────────────────────────────────────
# 3) Kelly 공식 (app.py의 kelly_fraction과 동일 로직)
# ─────────────────────────────────────────────
def kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float,
                    half_kelly: bool = True, cap: float = 0.25) -> float:
    avg_loss_pct = abs(avg_loss_pct)  # 손실은 절댓값으로 처리
    if avg_loss_pct == 0 or win_rate <= 0:
        return 0.0
    b = avg_win_pct / avg_loss_pct
    f = (win_rate * b - (1 - win_rate)) / b
    f = max(0.0, f)
    if half_kelly:
        f *= 0.5
    return min(f, cap)


# ─────────────────────────────────────────────
# 4) 드로다운 서킷브레이커
# ─────────────────────────────────────────────
class DrawdownCircuitBreaker:
    """
    현재 낙폭(peak 대비)이 threshold를 넘으면 exposure_mult를 reduction으로 낮추고,
    recovery_pct만큼 회복하면 원상복귀.
    """
    def __init__(self, threshold_pct: float = -15.0, reduction: float = 0.5,
                 recovery_pct: float = -5.0):
        self.threshold_pct = threshold_pct
        self.reduction = reduction
        self.recovery_pct = recovery_pct
        self.peak = -np.inf
        self.triggered = False

    def update(self, equity: float) -> float:
        """equity(현재 자산가치)를 입력받아 현재 적용할 exposure 배율을 반환."""
        self.peak = max(self.peak, equity)
        dd_pct = (equity / self.peak - 1) * 100 if self.peak > 0 else 0.0
        if not self.triggered and dd_pct <= self.threshold_pct:
            self.triggered = True
        elif self.triggered and dd_pct >= self.recovery_pct:
            self.triggered = False
        return self.reduction if self.triggered else 1.0


# ─────────────────────────────────────────────
# 5) 사이징을 반영한 백테스트
# ─────────────────────────────────────────────
def run_backtest_sized(df: pd.DataFrame, bt_signals_full_fn, buy_th: float = 65,
                        sell_th: float = 45, initial_capital: float = 10_000_000,
                        commission: float = 0.0005, slippage: float = 0.0003,
                        use_vol_target: bool = True, target_vol: float = 0.20,
                        use_signal_sizing: bool = True,
                        use_circuit_breaker: bool = True,
                        dd_threshold: float = -15.0, dd_reduction: float = 0.5):
    """
    look-ahead bias가 제거된 D+1 시가 체결 로직 위에 사이징을 추가한 버전.
    bt_signals_full_fn: app.py의 bt_signals_full 함수를 그대로 주입받아 사용
                        (모듈 간 의존성을 낮추기 위해 함수 자체를 인자로 받음).
    """
    sigs = bt_signals_full_fn(df)
    prices = df['Close'].values
    opens = df['Open'].values if 'Open' in df.columns else None
    dates = df.index
    n = len(df)
    daily_ret_for_vol = df['Close'].pct_change()

    vol_mult_series = (volatility_target_multiplier(daily_ret_for_vol, target_vol)
                        if use_vol_target else pd.Series(1.0, index=df.index))

    capital = float(initial_capital)
    shares = 0.0
    in_pos = False
    entry_val = 0.0
    equity = np.full(n, float(initial_capital))
    trades = []
    pending = None
    pending_target_frac = 1.0
    cb = DrawdownCircuitBreaker(dd_threshold, dd_reduction) if use_circuit_breaker else None

    for i in range(20, n):
        px = float(prices[i])
        exec_px = float(opens[i]) if opens is not None and not np.isnan(opens[i]) else px

        if pending == 'buy' and not in_pos:
            target_frac = pending_target_frac
            if cb is not None:
                target_frac *= cb.update(capital)
            invest_amt = capital * target_frac
            buy_px = exec_px * (1 + slippage)
            fee = invest_amt * commission
            shares = (invest_amt - fee) / buy_px
            entry_val = invest_amt
            capital -= invest_amt
            in_pos = True
            trades.append({'날짜': dates[i], '구분': '🟢 매수', '비중': f"{target_frac*100:.0f}%", '수익률': ''})
        elif pending == 'sell' and in_pos:
            sell_px = exec_px * (1 - slippage)
            gross = shares * sell_px
            fee = gross * commission
            net = gross - fee
            pnl = (net / entry_val - 1) * 100
            capital += net
            shares = 0.0
            in_pos = False
            trades.append({'날짜': dates[i], '구분': '🔴 매도', '비중': '-', '수익률': f"{pnl:+.2f}%"})
        pending = None

        sig = float(sigs.iloc[i])
        if not in_pos and sig > buy_th:
            pending = 'buy'
            frac = signal_strength_weight(sig, buy_th) if use_signal_sizing else 1.0
            frac *= float(vol_mult_series.iloc[i]) if use_vol_target else 1.0
            pending_target_frac = float(np.clip(frac, 0.0, 1.0))
        elif in_pos and sig < sell_th:
            pending = 'sell'

        equity[i] = capital + shares * px

    final_v = float(equity[-1])
    tot_ret = (final_v - initial_capital) / initial_capital * 100
    eq_s = pd.Series(equity).replace(0, np.nan).ffill()
    roll_max = eq_s.expanding().max()
    mdd = float(((eq_s - roll_max) / roll_max * 100).min())
    daily_ret = eq_s.pct_change().dropna().iloc[20:]  # 워밍업 0수익률 20개 제외
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0

    metrics = {
        'total_return': round(tot_ret, 1),
        'mdd': round(mdd, 1),
        'sharpe': round(sharpe, 2),
        'n_trades': sum(1 for t in trades if '매도' in t['구분']),
    }
    eq_df = pd.DataFrame({'날짜': dates, '전략': equity})
    return metrics, eq_df, pd.DataFrame(trades) if trades else pd.DataFrame()
