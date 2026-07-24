"""
modules/trade_plan_backtest.py 동작 고정 테스트.

핵심은 순수 시뮬레이터 `_simulate_outcome` 를 손으로 짠 가격 경로로 전수
검증하는 것 — 승/패/미체결/타임아웃과 R 값이 결정적으로 나와야 한다.
`backtest_trade_plans` 는 합성 프레임에서 스키마·무오류만 확인한다(방향은
build_trade_plan 이 정하므로 봉 단위로 강제하기 어렵다).

네트워크 無.
"""
import numpy as np
import pandas as pd

from modules import trade_plan_backtest as bt


def _arr(*vals):
    return np.array(vals, dtype=float)


# ── 순수 시뮬레이터 ────────────────────────────────────────────────
def test_sim_long_win():
    # 진입 95~97, 손절 93, 목표 102(R:R 2). j=1 저가 96 체결 → k=2 고가 103 목표
    highs = _arr(100, 98, 103, 104, 104)
    lows = _arr(99, 96, 99, 100, 100)
    r = bt._simulate_outcome(highs, lows, 0, "long", 95, 97, 93, 102, 2.0,
                             fill_window=5, hold_window=5)
    assert r["outcome"] == "win"
    assert r["r"] == 2.0
    assert r["fill_idx"] == 1 and r["exit_idx"] == 2


def test_sim_long_loss():
    # j=1 저가 96 체결 → k=2 저가 92 손절 먼저
    highs = _arr(100, 98, 98, 98)
    lows = _arr(99, 96, 92, 92)
    r = bt._simulate_outcome(highs, lows, 0, "long", 95, 97, 93, 102, 2.0,
                             fill_window=5, hold_window=5)
    assert r["outcome"] == "loss"
    assert r["r"] == -1.0


def test_sim_short_win():
    # 진입 103~105, 손절 107, 목표 98(R:R 2). j=1 고가 104 체결 → k=2 저가 97 목표
    highs = _arr(100, 104, 104, 104)
    lows = _arr(99, 102, 97, 97)
    r = bt._simulate_outcome(highs, lows, 0, "short", 103, 105, 107, 98, 2.0,
                             fill_window=5, hold_window=5)
    assert r["outcome"] == "win"
    assert r["r"] == 2.0


def test_sim_same_bar_stop_first():
    # 체결 봉에서 손절·목표가 같은 봉에 걸리면 손절 우선(보수적)
    highs = _arr(100, 103)   # k=1 고가 103 >= 목표 102
    lows = _arr(99, 92)      # k=1 저가 92 <= 손절 93
    r = bt._simulate_outcome(highs, lows, 0, "long", 95, 97, 93, 102, 2.0,
                             fill_window=3, hold_window=3)
    assert r["outcome"] == "loss"


def test_sim_nofill():
    # 가격이 진입 구간(<=97)에 한 번도 안 닿음
    highs = _arr(100, 101, 102)
    lows = _arr(99.5, 100, 101)
    r = bt._simulate_outcome(highs, lows, 0, "long", 95, 97, 93, 102, 2.0,
                             fill_window=3, hold_window=3)
    assert r["outcome"] == "nofill"
    assert r["fill_idx"] is None


def test_sim_timeout():
    # 체결됐지만 손절(93)·목표(110) 어느 쪽도 홀드 기간 내 미도달
    highs = _arr(100, 98, 99, 99)
    lows = _arr(99, 96, 97, 97)
    r = bt._simulate_outcome(highs, lows, 0, "long", 95, 97, 93, 110, 3.0,
                             fill_window=3, hold_window=3)
    assert r["outcome"] == "timeout"
    assert r["r"] == 0.0


# ── 집계 ───────────────────────────────────────────────────────────
def test_stats_win_rate_and_expectancy():
    trades = [
        {"direction": "long", "outcome": "win", "r": 2.0},
        {"direction": "long", "outcome": "loss", "r": -1.0},
        {"direction": "long", "outcome": "timeout", "r": 0.0},
        {"direction": "long", "outcome": "nofill", "r": 0.0},
    ]
    s = bt._stats(trades)
    assert s["setups"] == 4
    assert s["filled"] == 3          # nofill 제외
    assert s["nofill"] == 1
    assert s["wins"] == 1 and s["losses"] == 1 and s["timeouts"] == 1
    assert s["win_rate"] == 0.5      # 결판난 2건 중 1승
    assert s["expectancy_r"] == 0.5  # (2 + -1)/2
    assert abs(s["avg_r"] - (2 - 1 + 0) / 3) < 1e-9   # timeout 포함 3건


# ── 통합: 합성 프레임에서 스키마·무오류 ────────────────────────────
def _oscillating(n=160, level=100.0):
    i = np.arange(n)
    close = level + 4.0 * np.sin(i / 3.0)
    open_ = close - 0.3 * np.sin(i / 3.0)
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": np.full(n, 1e6)})


def test_backtest_runs_and_schema():
    out = bt.backtest_trade_plans(_oscillating(), fill_window=10, hold_window=15)
    for side in ("all", "long", "short"):
        assert side in out
        for key in ("setups", "filled", "wins", "losses", "win_rate", "expectancy_r"):
            assert key in out[side]
    assert isinstance(out["trades"], list)
    # 집계 정합성: all.setups == long.setups + short.setups
    assert out["all"]["setups"] == out["long"]["setups"] + out["short"]["setups"]
