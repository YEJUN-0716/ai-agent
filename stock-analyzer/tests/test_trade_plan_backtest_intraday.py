import numpy as np

from modules.trade_plan_backtest import _simulate_outcome, _stats

# 세션 2개 × 4봉. 인덱스 0~3 이 첫날, 4~7 이 둘째 날.
SESSIONS = np.array([1, 1, 1, 1, 2, 2, 2, 2])


def test_eod_exit_uses_open_of_last_bar_of_session():
    # 롱 진입 100, 손절 90 (위험 10), 목표 130. 손절·목표 둘 다 안 닿고
    # 세션 마지막 봉(idx 3)의 시가 105 에 털린다 → r = (105-100)/10 = +0.5
    highs = np.array([100.0, 101, 102, 106, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 98, 104, 100, 100, 100, 100])
    opens = np.array([100.0, 100, 100, 105, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=98.0, entry_high=100.0, stop=90.0, target=130.0, rr=3.0,
        entry_ref=100.0, sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "eod"
    assert res["r"] == 0.5
    assert res["exit_idx"] == 3


def test_stop_before_eod_still_wins_out():
    # idx 2 에서 손절(90)을 친다 → EOD 까지 가지 않는다.
    highs = np.array([100.0, 101, 102, 106, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 89, 104, 100, 100, 100, 100])
    opens = np.array([100.0, 100, 100, 105, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=98.0, entry_high=100.0, stop=90.0, target=130.0, rr=3.0,
        sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "loss"
    assert res["r"] == -1.0


def test_no_fill_when_entry_not_touched_before_session_end():
    # 진입 구간 89~90 에 첫 세션 동안 안 닿는다 → 다음 세션으로 넘어가지 않는다.
    highs = np.array([100.0, 101, 102, 103, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 98, 97, 85, 85, 85, 85])
    opens = np.array([100.0, 100, 100, 100, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=89.0, entry_high=90.0, stop=80.0, target=120.0, rr=3.0,
        sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "nofill"
    assert res["fill_idx"] is None


def test_short_eod_exit_r_sign():
    # 숏 진입 100, 손절 110 (위험 10), 목표 70. EOD 시가 95 → r = (100-95)/10 = +0.5
    highs = np.array([100.0, 101, 102, 96, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 98, 94, 100, 100, 100, 100])
    opens = np.array([100.0, 100, 102, 95, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "short",
        entry_low=100.0, entry_high=102.0, stop=110.0, target=70.0, rr=3.0,
        entry_ref=100.0, sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "eod"
    assert res["r"] == 0.5


def test_sessions_none_keeps_old_timeout_behaviour():
    # 세션을 안 주면 예전 그대로 — 홀드 창 안에 아무것도 안 닿으면 timeout, R=0.
    highs = np.array([100.0, 101, 102, 103])
    lows = np.array([100.0, 99, 98, 97])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=98.0, entry_high=100.0, stop=80.0, target=130.0, rr=3.0)
    assert res["outcome"] == "timeout"
    assert res["r"] == 0.0


def test_stats_counts_eod_apart_from_timeout():
    # timeouts 를 뺄셈으로 세면 EOD 청산이 전부 timeout 으로 잡힌다.
    trades = [
        {"outcome": "win", "r": 2.0},
        {"outcome": "loss", "r": -1.0},
        {"outcome": "eod", "r": 0.4},
        {"outcome": "timeout", "r": 0.0},
        {"outcome": "nofill", "r": 0.0},
    ]
    s = _stats(trades)
    assert s["filled"] == 4
    assert s["nofill"] == 1
    assert s["timeouts"] == 1
    assert s["eod_exits"] == 1
    # avg_r 은 체결 4건 평균 — EOD 손익이 섞여 들어가야 한다.
    assert abs(s["avg_r"] - (2.0 - 1.0 + 0.4 + 0.0) / 4) < 1e-9
