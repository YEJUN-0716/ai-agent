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


# ── 걸 수 있는 체결가 ──────────────────────────────────────────────
#
# 유령 체결: 구간 **상단** 터치를 체결로 치면서 산 값은 구간 **중간**
# (entry_ref)으로 적는 것. 시장에 없던 가격이다. 2026-08-12 측정에서 일봉
# OOS 이익의 70% 가 여기서 나왔다(`docs/measurements/2026-08-12-entry-rule-daily.md`).

def test_placeable_fill_is_the_limit_not_the_zone_mid():
    plan = {"direction": "long", "stop": 93.0, "targets": [102.0],
            "entry": {"low": 95.0, "high": 97.0, "ref": 96.0}}
    opens = _arr(100, 99, 98)                 # 갭 없음 → 지정가 97 에 채워진다
    res = {"outcome": "loss", "r": -1.0, "fill_idx": 1, "exit_idx": 2}
    got = bt.placeable_r(res, plan, 97.0, opens, plan_risk=3.0)
    assert got["fill_price"] == 97.0
    # 플랜보다 1 비싸게 샀으므로 손절은 -1.0 이 아니라 -4/3 이다
    assert abs(got["r"] - (93.0 - 97.0) / 3.0) < 1e-9


def test_placeable_fill_uses_open_on_gap_down():
    plan = {"direction": "long", "stop": 93.0, "targets": [102.0],
            "entry": {"low": 95.0, "high": 97.0, "ref": 96.0}}
    opens = _arr(100, 94, 98)                 # 지정가 아래로 갭 → 시가에 채워진다
    got = bt.placeable_r({"outcome": "win", "r": 2.0, "fill_idx": 1, "exit_idx": 2},
                         plan, 97.0, opens, plan_risk=3.0)
    assert got["fill_price"] == 94.0
    assert abs(got["r"] - (102.0 - 94.0) / 3.0) < 1e-9


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


# ── 비용 환산에 필요한 가격 좌표 ─────────────────────────────────────
#
# R 은 위험 1단위 기준이라 그 자체로는 거래비용을 못 잰다. 손절이 진입가에서
# 몇 % 떨어져 있었는지가 있어야 수수료를 R 로 바꿀 수 있다.

def test_trades_carry_price_coordinates():
    import numpy as np
    import pandas as pd

    n = 200
    close = pd.Series([100 + 0.3 * i + 4 * np.sin(i / 8) for i in range(n)],
                      index=pd.bdate_range("2025-01-01", periods=n))
    df = pd.DataFrame({
        "Open": close * 0.995, "High": close * 1.02,
        "Low": close * 0.98, "Close": close,
        "Volume": pd.Series([1_000_000] * n, index=close.index),
    })

    trades = bt.backtest_trade_plans(df)["trades"]
    assert trades, "이 패널에서 셋업이 하나도 안 나오면 테스트가 무의미하다"

    for t in trades:
        assert t["entry_ref"] > 0 and t["stop_price"] > 0
        # 위험폭이 0 이면 비용을 R 로 나눌 수 없다 — 유효 플랜이면 항상 양수다
        assert abs(t["entry_ref"] - t["stop_price"]) > 0
        if t["direction"] == "long":
            assert t["stop_price"] < t["entry_ref"] < t["target_price"]
        else:
            assert t["target_price"] < t["entry_ref"] < t["stop_price"]


def test_backtest_scores_on_the_placeable_fill():
    """`backtest_trade_plans` 가 실제로 체결가로 채점하는가 (유령 재발 방지)."""
    trades = bt.backtest_trade_plans(_oscillating(), fill_window=10,
                                     hold_window=15)["trades"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    assert losses, "손절 트레이드가 없으면 이 테스트가 무의미하다"
    for t in losses:
        risk = abs(t["entry_ref"] - t["stop_price"])
        want = ((t["stop_price"] - t["fill_price"]) if t["direction"] == "long"
                else (t["fill_price"] - t["stop_price"])) / risk
        assert abs(t["r"] - want) < 1e-9
    # 구간 중간값으로 채점하면 손절이 전부 정확히 -1.0 이다
    assert any(abs(t["r"] + 1.0) > 1e-9 for t in losses)
