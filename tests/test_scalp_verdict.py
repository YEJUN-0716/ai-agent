"""스캘핑판(15분봉) 총괄 판정 — 창과 기준이 봉 종류를 따라가는가.

`calc_momentum` 은 창을 **봉 개수**로 잡는다(63봉=3개월). 그래서 15분봉을 그대로
먹여도 터지지 않고, 대신 '3개월 모멘텀'이 이틀 반이 된다 — 점수 기준이 "3개월에
+30%면 90점"이므로 이틀 반 수익률은 언제나 50점 근처에 붙고, 죽은 숫자가 30%
가중치로 총괄에 섞인다. 이 파일이 지키는 것은 그 함정이다:

  1. 스캘핑 모멘텀은 창(1일·3일·1주)과 기준(±1.5/3/5%)이 함께 스캘핑 규모다
  2. 15분봉을 못 받으면 **일봉으로 대체하지 않고** 예외를 올린다
  3. 조립된 스캘핑 판정은 방향성 3인만 쓰고, 퀀트+재무는 일봉 보고서를 그대로 받는다

라인 기하와 총괄 블렌드 자체는 test_trade_plan.py·test_analyst_team.py 가 본다.
"""

import numpy as np
import pandas as pd
import pytest

import app

BARS_1D = app.BARS_PER_DAY_15M


def _df(closes):
    """종가만 의미 있는 15분봉. 고가/저가는 지표가 NaN 을 안 내게만 벌려 둔다."""
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({
        'Open': c, 'Close': c, 'High': c * 1.002, 'Low': c * 0.998,
        'Volume': [1_000_000] * len(c),
    })


def _flat_then(pct, bars=BARS_1D * 5 + 1):
    """1주 전부터 지금까지 pct% 오른(내린) 봉 — 세 창 모두 같은 방향이 된다."""
    return _df(np.linspace(100.0, 100.0 * (1 + pct / 100), bars))


def _quant_report():
    return app.build_analyst_report('퀀트+재무', '💰', 55.0, ['ROE 20.0%'], {})


def test_intraday_momentum_uses_intraday_windows():
    m = app.calc_momentum_intraday(_flat_then(5.0))
    assert set(m) == {'score', '1D', '3D', '1W'}
    assert m['1W'] == pytest.approx(5.0, abs=0.05)
    # 창이 짧을수록 최근 구간만 잡으므로 상승폭이 작다
    assert m['1D'] < m['3D'] < m['1W']


def test_intraday_momentum_scale_is_scalping_sized():
    """일봉 기준(±30%)이 아니라 스캘핑 기준(±1.5/3/5%)으로 점수가 갈려야 한다.

    이 단조성이 깨지면 15분봉 모멘텀이 다시 50점에 붙어 죽은 숫자가 된다.
    """
    up, flat, down = (app.calc_momentum_intraday(_flat_then(p))['score']
                      for p in (6.0, 0.0, -6.0))
    assert up > 70 and down < 30
    assert down < flat < up


def test_intraday_momentum_survives_short_history():
    """1주치가 없는 종목은 없는 창을 None 으로 두고 남은 창으로만 채점한다."""
    m = app.calc_momentum_intraday(_flat_then(2.0, bars=BARS_1D * 2))
    assert m['1D'] is not None and m['1W'] is None
    assert 0 < m['score'] <= 100


def test_scalp_verdict_refuses_to_fall_back_to_daily(monkeypatch):
    """15분봉이 없으면 예외 — 조용히 일봉을 쓰면 화면이 스윙 판정을
    '스캘핑'이라고 적어 내보낸다."""
    monkeypatch.setattr(app, 'download_stock', lambda *a, **k: _df([100.0] * 5))
    with pytest.raises(ValueError):
        app.build_scalp_verdict('AAPL', _quant_report())

    monkeypatch.setattr(app, 'download_stock', lambda *a, **k: pd.DataFrame())
    with pytest.raises(ValueError):
        app.build_scalp_verdict('AAPL', _quant_report())


def test_scalp_verdict_reuses_daily_quant_report(monkeypatch):
    monkeypatch.setattr(app, 'download_stock',
                        lambda *a, **k: _flat_then(4.0, bars=BARS_1D * 12))
    quant = _quant_report()
    out = app.build_scalp_verdict('AAPL', quant)

    assert [r['name'] for r in out['reports']] == ['차트+파동+모멘텀', '퀀트+재무', 'ICT+CRT']
    assert out['reports'][1] is quant          # 재무제표는 봉과 무관 — 일봉 값 그대로
    assert out['manager']['verdict'] in ('매수', '매도', '중립')
    assert 'direction' in out['trader']
    assert out['momentum']['1D'] is not None

    # 모멘텀 근거 문구가 '3개월'이라고 적히면 이틀치를 석 달로 파는 것이다
    reasons = ' '.join(out['reports'][0]['reasons'])
    assert '3개월' not in reasons and '1일 모멘텀' in reasons
