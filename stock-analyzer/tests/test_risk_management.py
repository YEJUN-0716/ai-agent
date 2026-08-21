"""사이징 백테스트가 내는 **표와 곡선이 같은 값을 말하는가**.

거래별 수익률 표는 사장님이 실제로 보는 숫자다. 자산곡선과 한 다리라도 어긋나면
"이 전략 몇 % 벌었나"에 답이 두 개가 된다 — 이 저장소가 반복해서 당한 실패다.

네트워크 없음. 신호 함수를 주입받는 구조라 결정적인 신호를 그냥 넣는다.
"""

import numpy as np
import pandas as pd
import pytest

from modules.risk_management import run_backtest_sized


def frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    return pd.DataFrame({"Open": closes, "Close": closes}, index=idx)


def signals_that_buy_at(buy_i: int, sell_i: int):
    """buy_i 에 매수 신호, sell_i 에 매도 신호. 체결은 D+1 시가다."""
    def fn(df):
        s = pd.Series(50.0, index=df.index)
        s.iloc[buy_i] = 90.0
        s.iloc[sell_i] = 10.0
        return s
    return fn


def test_trade_return_matches_the_equity_curve():
    """원가는 실제로 나간 돈이다. 수수료를 빼고 잡으면 표만 좋아 보인다.

    예전엔 entry_val = invest_amt - fee 라, 거래별 수익률이 매수 수수료 한 다리만큼
    낙관 편향됐다. 자산곡선은 invest_amt 를 뺐으므로 둘이 안 맞았다.
    """
    # 30일 내내 100원, 매수·매도 사이에도 가격 변화 없음 → 진짜 수익률은
    # 수수료·슬리피지만큼 **마이너스**여야 한다.
    px = [100.0] * 30
    metrics, eq, trades = run_backtest_sized(
        frame(px), signals_that_buy_at(21, 24),
        use_vol_target=False, use_signal_sizing=False, use_circuit_breaker=False)

    assert metrics["n_trades"] == 1
    shown = float(trades[trades["구분"].str.contains("매도")]["수익률"].iloc[0].rstrip("%"))
    assert shown < 0, f"가격이 안 움직였는데 수익률이 {shown:+.2f}% — 비용이 사라졌다"

    # 표의 수익률과 곡선의 총수익률이 같은 값을 말해야 한다 (전액 투입 1회전).
    assert shown == pytest.approx(metrics["total_return"], abs=0.05)


def test_no_signal_leaves_the_capital_untouched():
    px = [100.0] * 30
    metrics, eq, trades = run_backtest_sized(
        frame(px), lambda df: pd.Series(50.0, index=df.index))
    assert trades.empty and metrics["total_return"] == 0.0
    assert np.allclose(eq["전략"], 10_000_000)


def test_app_uses_the_module_kelly():
    """app.py 가 자기 사본을 다시 만들면 여기서 잡힌다.

    사본은 `avg_loss_pct <= 0 → 0.0` 이었는데 호출부가 넘기는 평균 손실은 늘
    음수라(손실 거래 수익률의 평균), 리스크팀의 "권장 비중(Half-Kelly)"이
    손실 거래가 하나라도 있는 모든 종목에서 0.0% 로 찍혔다.
    """
    import app
    assert app.kelly_fraction(0.55, 8.0, -4.0) == pytest.approx(0.1625)
    assert app.kelly_fraction(0.55, 8.0, 4.0) == pytest.approx(0.1625)
