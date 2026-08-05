"""트레이더 라인 — 손절이 진입 근거가 사라지는 자리에 찍히는가.

매수/매도 라인만 있을 때는 "얼마에 사서 얼마에 판다"까지만 말할 수 있었다.
손절이 없으면 틀렸을 때 얼마를 잃는지 모르고, 그러면 손익비도 못 낸다 —
방향이 맞아도 장기적으로 남는 게 없는 매매를 걸러낼 수가 없다.

손절 자리는 매수 라인(최근접 지지) 아래의 다음 지지다. 거기까지 밀렸으면
"이 지지에서 반등한다"는 진입 근거 자체가 사라진 것이다. 다만 지지선은
정확히 그 가격에서 반등하지 않고 아래를 한 번 찍고 올라오는 일이 흔해서
ATR의 절반만큼 여유를 둔다.
"""

import pandas as pd
import pytest

import app


@pytest.fixture
def flat_df():
    """ATR이 정확히 2.0이 되는 30일치 봉 — 손절 폭을 손으로 검산할 수 있게."""
    n = 30
    return pd.DataFrame({
        'Close':  [100.0] * n,
        'High':   [101.0] * n,   # TR = max(H-L, |H-prevC|, |L-prevC|) = max(2, 1, 1)
        'Low':    [99.0] * n,
        'Volume': [1_000_000] * n,
    })


MANAGER = {'verdict': '중립', 'agreement': 60}


def _levels(monkeypatch, levels):
    monkeypatch.setattr(app, 'find_sr_levels', lambda c, h, l: levels)


def test_atr_measures_the_stocks_own_daily_swing(flat_df):
    assert app.calc_atr(flat_df) == pytest.approx(2.0)


def test_stop_sits_below_the_next_support_with_room(flat_df, monkeypatch):
    # 지지 95(매수 라인)와 90. 95가 깨지면 다음 방어선은 90이고,
    # 손절은 그 아래로 ATR 절반(1.0)만큼 더 내려간 자리여야 한다.
    _levels(monkeypatch, [{'level': 95.0, 'above': False},
                          {'level': 90.0, 'above': False},
                          {'level': 110.0, 'above': True}])

    t = app.trader_signal_lines(flat_df, MANAGER)

    assert t['buy_line'] == 95.0
    assert t['stop_line'] == pytest.approx(89.0)
    assert t['stop_line'] < t['buy_line']          # 손절이 매수보다 위면 계획이 아니다
    assert t['stop_dist'] == pytest.approx(-11.0)  # 현재가 100 기준


def test_stop_falls_back_to_atr_when_no_support_below(flat_df, monkeypatch):
    # 아래에 기댈 지지가 없으면 구조가 아니라 변동성으로 잰다 — 매수 라인 -1.5 ATR.
    _levels(monkeypatch, [{'level': 95.0, 'above': False},
                          {'level': 110.0, 'above': True}])

    t = app.trader_signal_lines(flat_df, MANAGER)

    assert t['stop_line'] == pytest.approx(92.0)
    assert '지지 없음' in t['stop_note']


def test_risk_reward_comes_from_the_three_lines(flat_df, monkeypatch):
    # 매수 95 → 매도 110 은 +15, 매수 95 → 손절 89 는 -6. 손익비 2.5:1.
    _levels(monkeypatch, [{'level': 95.0, 'above': False},
                          {'level': 90.0, 'above': False},
                          {'level': 110.0, 'above': True}])

    assert app.trader_signal_lines(flat_df, MANAGER)['rr'] == pytest.approx(2.5)


def test_stop_never_goes_below_zero(flat_df, monkeypatch):
    # 동전주에서 그냥 빼면 손절이 음수가 된다 — 화면에 "-0.30에 손절"이 찍힌다.
    _levels(monkeypatch, [{'level': 0.5, 'above': False},
                          {'level': 0.1, 'above': False},
                          {'level': 2.0, 'above': True}])

    t = app.trader_signal_lines(flat_df, MANAGER)

    assert t['stop_line'] == 0.0
    assert t['rr'] is None or t['rr'] > 0
