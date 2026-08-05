"""트레이더 라인 — 총괄 판정이 정한 방향으로 진입·목표·손절이 잡히는가.

매수/매도 라인만 있을 때는 "얼마에 사서 얼마에 판다"까지만 말할 수 있었다.
손절이 없으면 틀렸을 때 얼마를 잃는지 모르고, 그러면 손익비도 못 낸다 —
방향이 맞아도 장기적으로 남는 게 없는 매매를 걸러낼 수가 없다.

방향도 계획의 일부다. 총괄이 매도를 냈는데 롱 계획을 그려 주면 화면이 총괄과
반대되는 매매를 지시하게 된다. 그래서 매수→롱, 매도→숏, 중립→롱 기준 참고선.

손절 자리는 진입 너머의 다음 지지/저항이다. 거기까지 갔으면 "이 자리에서
되돌린다"는 진입 근거 자체가 사라진 것이다. 다만 지지·저항은 정확히 그
가격에서 돌지 않고 살짝 넘겼다 돌아오는 일이 흔해서 ATR의 절반을 더 밀어낸다.
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


LONG    = {'verdict': '매수', 'agreement': 80}
SHORT   = {'verdict': '매도', 'agreement': 80}
NEUTRAL = {'verdict': '중립', 'agreement': 60}

# 지지 95·90, 저항 110·120. 현재가 100.
FULL_SR = [{'level': 95.0, 'above': False}, {'level': 90.0, 'above': False},
           {'level': 110.0, 'above': True}, {'level': 120.0, 'above': True}]


def _levels(monkeypatch, levels):
    monkeypatch.setattr(app, 'find_sr_levels', lambda c, h, l: levels)


def test_atr_measures_the_stocks_own_daily_swing(flat_df):
    assert app.calc_atr(flat_df) == pytest.approx(2.0)


# ── 롱 (총괄 매수) ────────────────────────────────────────────────

def test_buy_verdict_plans_a_long(flat_df, monkeypatch):
    _levels(monkeypatch, FULL_SR)

    t = app.trader_signal_lines(flat_df, LONG)

    assert t['direction'] == 'long'
    assert t['entry_line'] == 95.0     # 최근접 지지에서 산다
    assert t['target_line'] == 110.0   # 최근접 저항에서 판다
    assert t['stop_line'] == pytest.approx(89.0)   # 다음 지지 90 − 0.5 ATR
    assert t['stop_line'] < t['entry_line'] < t['target_line']


def test_long_stop_falls_back_to_atr_when_no_support_below(flat_df, monkeypatch):
    # 아래에 기댈 지지가 없으면 구조가 아니라 변동성으로 잰다 — 진입가 -1.5 ATR.
    _levels(monkeypatch, [{'level': 95.0, 'above': False},
                          {'level': 110.0, 'above': True}])

    t = app.trader_signal_lines(flat_df, LONG)

    assert t['stop_line'] == pytest.approx(92.0)
    assert '지지 없음' in t['stop_note']


def test_long_risk_reward_comes_from_the_three_lines(flat_df, monkeypatch):
    # 진입 95 → 목표 110 은 +15, 진입 95 → 손절 89 는 -6. 손익비 2.5:1.
    _levels(monkeypatch, FULL_SR)

    assert app.trader_signal_lines(flat_df, LONG)['rr'] == pytest.approx(2.5)


# ── 숏 (총괄 매도) ────────────────────────────────────────────────

def test_sell_verdict_plans_a_short_not_a_long(flat_df, monkeypatch):
    """매도 판정에 롱 계획을 그려 주면 화면이 총괄과 반대되는 말을 한다."""
    _levels(monkeypatch, FULL_SR)

    t = app.trader_signal_lines(flat_df, SHORT)

    assert t['direction'] == 'short'
    assert t['entry_line'] == 110.0    # 최근접 저항에서 판다(공매도)
    assert t['target_line'] == 95.0    # 최근접 지지에서 되산다
    assert t['stop_line'] == pytest.approx(121.0)  # 다음 저항 120 + 0.5 ATR
    assert t['target_line'] < t['entry_line'] < t['stop_line']   # 롱과 정확히 반대


def test_short_stop_falls_back_to_atr_when_no_resistance_above(flat_df, monkeypatch):
    _levels(monkeypatch, [{'level': 95.0, 'above': False},
                          {'level': 110.0, 'above': True}])

    t = app.trader_signal_lines(flat_df, SHORT)

    assert t['stop_line'] == pytest.approx(113.0)   # 진입 110 + 1.5 ATR
    assert '저항 없음' in t['stop_note']


def test_short_risk_reward_uses_the_short_direction(flat_df, monkeypatch):
    # 진입 110 → 목표 95 는 +15(숏이니 하락이 이익), 손절 121 까지는 -11.
    _levels(monkeypatch, FULL_SR)

    assert app.trader_signal_lines(flat_df, SHORT)['rr'] == pytest.approx(15 / 11)


# ── 중립·경계 ────────────────────────────────────────────────────

def test_neutral_verdict_keeps_the_long_reference(flat_df, monkeypatch):
    # 방향이 없으면 참고선이라도 하나는 있어야 한다. 롱 기준으로 두고 화면이 밝힌다.
    _levels(monkeypatch, FULL_SR)

    assert app.trader_signal_lines(flat_df, NEUTRAL)['direction'] == 'long'


def test_long_stop_never_goes_below_zero(flat_df, monkeypatch):
    # 동전주에서 그냥 빼면 손절이 음수가 된다 — 화면에 "-0.30에 손절"이 찍힌다.
    _levels(monkeypatch, [{'level': 0.5, 'above': False},
                          {'level': 0.1, 'above': False},
                          {'level': 2.0, 'above': True}])

    t = app.trader_signal_lines(flat_df, LONG)

    assert t['stop_line'] == 0.0
    assert t['rr'] is None or t['rr'] > 0
