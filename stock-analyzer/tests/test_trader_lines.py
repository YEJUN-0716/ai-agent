"""트레이더 라인 — 총괄이 방향을 정하고, 검증된 플랜 엔진이 라인을 잡는다.

예전에는 이 함수가 지지/저항 최근접 레벨로 직접 라인을 그렸다. 그러면 화면이
**언제나** 세 라인을 그린다 — 손익비가 0.6:1 이어도, 상승장 한복판에서 숏이어도.
`modules/trade_plan.py` 에는 이미 같은 계산이 게이트(손익비 하한·숏 레짐·저확신
숏 억제)와 백테스트까지 붙은 채로 있었으므로, 여기서는 그걸 호출하고 방향만
총괄 판정으로 주입한다.

그래서 이 파일이 지키는 것은 **이음매**다: 판정이 방향으로 옮겨졌는가, 플랜의
숫자가 카드 키로 옳게 옮겨졌는가, 게이트가 걸렸을 때 화면이 라인 대신 사유를
받는가. 라인 기하 자체는 tests/test_trade_plan.py 가 본다 — 두 번 보지 않는다.
"""

import pandas as pd
import pytest

import app
from modules import trade_plan as tp


@pytest.fixture
def flat_df():
    """현재가 100 인 봉. 플랜 엔진을 가짜로 갈아끼우므로 내용은 중요하지 않다."""
    n = 30
    return pd.DataFrame({
        'Open':   [100.0] * n,
        'Close':  [100.0] * n,
        'High':   [101.0] * n,
        'Low':    [99.0] * n,
        'Volume': [1_000_000] * n,
    })


LONG    = {'verdict': '매수', 'agreement': 80}
SHORT   = {'verdict': '매도', 'agreement': 80}
NEUTRAL = {'verdict': '중립', 'agreement': 60}


def _plan(**over):
    """유효한 롱 플랜 하나 — 진입 95(94~96), 손절 89, 목표 110/120, R:R 2.5."""
    base = {
        'direction': 'long', 'bias_score': 20, 'confidence': 'high', 'confluence': 3,
        'current': 100.0, 'entry': {'low': 94.0, 'high': 96.0, 'ref': 95.0},
        'stop': 89.0, 'targets': [110.0, 120.0], 'rr': [2.5, 5.0],
        'valid': True, 'reason_invalid': '', 'signals': ['진입 근거: Bullish OB 지지'],
    }
    base.update(over)
    # 등급·실행여부는 손으로 적지 않고 진짜 함수로 낸다 — 손으로 적으면
    # 문턱이 바뀔 때마다 이 픽스처가 조용히 실물과 어긋난다.
    grade, risk_pct = tp.cost_grade(base['entry']['ref'], base['stop'])
    actionable, why_not = tp._actionable(
        base['valid'], base['direction'], grade, base['reason_invalid'])
    base.setdefault('cost_grade', grade)
    base.setdefault('risk_pct', round(risk_pct, 2))
    base.setdefault('actionable', actionable)
    base.setdefault('reason_not_actionable', why_not)
    return base


def _no_lines(**over):
    """게이트에 막혀 라인 자체가 없는 플랜 (숏 레짐 보류 등)."""
    return _plan(entry={'low': 0.0, 'high': 0.0, 'ref': 0.0}, stop=0.0,
                 targets=[], rr=[], valid=False, **over)


def _engine(monkeypatch, plan, capture=None):
    """플랜 엔진을 가짜로 교체. capture 를 주면 호출 인자를 담아 준다."""
    def fake(df, **kwargs):
        if capture is not None:
            capture.update(kwargs)
        return plan
    monkeypatch.setattr(tp, 'build_trade_plan', fake)


# ── 방향: 총괄 판정이 정한다 ──────────────────────────────────────

def test_buy_verdict_asks_the_engine_for_a_long(flat_df, monkeypatch):
    got = {}
    _engine(monkeypatch, _plan(), got)

    t = app.trader_signal_lines(flat_df, LONG)

    assert got['direction'] == 'long'
    assert t['direction'] == 'long'


def test_sell_verdict_asks_the_engine_for_a_short(flat_df, monkeypatch):
    """매도 판정에 롱 계획을 그려 주면 화면이 총괄과 반대되는 말을 한다."""
    got = {}
    _engine(monkeypatch, _plan(direction='short'), got)

    t = app.trader_signal_lines(flat_df, SHORT)

    assert got['direction'] == 'short'
    assert t['direction'] == 'short'


def test_neutral_verdict_lets_the_structure_decide(flat_df, monkeypatch):
    """총괄이 방향을 안 냈으면 억지로 롱 참고선을 그리지 않고 구조에 맡긴다."""
    got = {}
    _engine(monkeypatch, _plan(), got)

    app.trader_signal_lines(flat_df, NEUTRAL)

    assert got['direction'] is None


# ── 매핑: 플랜의 숫자가 카드 키로 옳게 옮겨지는가 ────────────────

def test_plan_numbers_map_onto_the_card_keys(flat_df, monkeypatch):
    _engine(monkeypatch, _plan())

    t = app.trader_signal_lines(flat_df, LONG)

    assert t['entry_line'] == 95.0        # 진입 구간의 기준값
    assert t['target_line'] == 110.0      # T1 (T2 는 카드에 안 쓴다)
    assert t['stop_line'] == 89.0
    assert t['rr'] == 2.5
    assert t['valid'] is True


def test_distances_are_measured_from_the_current_price(flat_df, monkeypatch):
    _engine(monkeypatch, _plan())

    t = app.trader_signal_lines(flat_df, LONG)

    assert t['entry_dist'] == pytest.approx(-5.0)    # 95 vs 현재가 100
    assert t['target_dist'] == pytest.approx(10.0)
    assert t['stop_dist'] == pytest.approx(-11.0)


def test_entry_basis_is_carried_to_the_screen(flat_df, monkeypatch):
    # "왜 여기가 진입인가"가 사라지면 화면이 숫자만 남은 점괘가 된다.
    _engine(monkeypatch, _plan())

    assert 'Bullish OB 지지' in app.trader_signal_lines(flat_df, LONG)['entry_note']


# ── 게이트: 못 할 매매는 못 한다고 말한다 ────────────────────────

def test_low_rr_setup_is_held_not_recommended(flat_df, monkeypatch):
    """예전에는 손익비 0.6:1 도 그냥 그렸다. 이제는 화면이 보류라고 말한다."""
    _engine(monkeypatch, _plan(valid=False, rr=[0.6, 1.0],
                               reason_invalid='손익비 부족 (T1 R:R 0.60 < 1.5)'))

    t = app.trader_signal_lines(flat_df, LONG)

    assert t['valid'] is False
    assert '관찰' in t['stance']
    assert '손익비 부족' in t['reason_invalid']
    assert t['entry_line'] == 95.0        # 참고용 라인은 남긴다


def test_vetoed_short_draws_no_lines_at_all(flat_df, monkeypatch):
    """레짐에 막힌 숏은 라인이 없다 — 0.00 을 그리느니 사유를 보여준다."""
    _engine(monkeypatch, _no_lines(direction='short',
                                   reason_invalid='상위추세 상승/횡보 — 숏 보류'))

    t = app.trader_signal_lines(flat_df, SHORT)

    assert t['entry_line'] is None
    assert t['target_line'] is None and t['stop_line'] is None
    assert t['entry_dist'] is None
    assert t['rr'] is None
    assert '숏 보류' in t['reason_invalid']


def test_real_engine_refuses_a_too_short_frame(flat_df):
    """가짜 없이 진짜 엔진과 붙여 보는 한 판 — 30봉이면 구조를 믿을 수 없다."""
    t = app.trader_signal_lines(flat_df, LONG)

    assert t['entry_line'] is None
    assert t['valid'] is False
    assert '데이터 부족' in t['reason_invalid']


# ── 화면 표시: 추천이라는 말은 게이트를 통과한 계획에만 붙는다 ──────

MGR = {'total_score': 62.0, 'consensus': '매수 우위', 'agreement': 80, 'verdict': '매수',
       'strongest_opinion': '모멘텀팀 +12', 'dissent': '', 'macro_note': '',
       'confidence_note': ''}


def _card_html(flat_df, monkeypatch, plan, with_trader=True):
    """카드를 그려 보고 HTML 을 받아 온다 — 화면에 실제로 무슨 말이 찍히는지."""
    _engine(monkeypatch, plan)
    trader = app.trader_signal_lines(flat_df, MGR)
    out = []
    monkeypatch.setattr(app.st, 'markdown', lambda html, **kw: out.append(html))
    monkeypatch.setattr(app.st, 'caption', lambda *a, **kw: None)
    app.render_verdict_cards({'manager': MGR, 'trader': trader, 'is_krw': False},
                             with_trader=with_trader)
    return ''.join(out)


def test_workspace_report_shows_the_verdict_without_trade_prices(flat_df, monkeypatch):
    """애널리스트 팀 리포트는 총괄까지만. 매매추천가는 최상단 총괄 자리에만 둔다."""
    html = _card_html(flat_df, monkeypatch, _plan(), with_trader=False)

    assert '총괄 종합 보고서' in html
    for banned in ('진입', '목표', '손절', '추천'):
        assert banned not in html, banned


def test_gate_badge_on_a_setup_that_clears_the_gate(flat_df, monkeypatch):
    """딱지는 **통과했다는 사실**만 말한다.

    2026-08-12 까지는 "✅ 추천" 이었다. 그 말이 약속하는 초과수익이 측정에서
    안 나왔다 — 걸 수 있는 진입·청산으로 재면 편도 20bp 후 +0.06R 이다
    (2026-08-16 재측정). 게이트는 남기되 문구는 게이트가 하는 일에 맞춘다.
    """
    html = _card_html(flat_df, monkeypatch, _plan())

    assert '실행 문턱 통과' in html
    assert '추천' not in html          # 다시 기어들어오면 여기서 잡는다
    # 수치는 MEASURED_EDGE_NOTE 한 곳에만 산다 — 여기 베껴 적으면 또 갈린다.
    from modules.trade_plan import MEASURED_EDGE_NOTE
    assert MEASURED_EDGE_NOTE in html  # 근거 수치를 같이 낸다


def test_no_recommendation_when_the_reward_does_not_cover_the_risk(flat_df, monkeypatch):
    """1:2 에 못 미치는 계획은 딱지가 아니라 보류로 나간다."""
    html = _card_html(flat_df, monkeypatch, _plan(
        valid=False, rr=[1.8, 3.0], reason_invalid='손익비 부족 (T1 R:R 1.80 < 2.0)'))

    assert '실행 문턱 통과' not in html
    assert '관찰만' in html and '손익비 부족' in html


def test_kelly_note_from_the_risk_team_is_still_quoted(flat_df, monkeypatch):
    _engine(monkeypatch, _plan())
    risk = {'reasons': ['변동성 높음', 'Half-Kelly 권장 비중 3.2%']}

    t = app.trader_signal_lines(flat_df, LONG, risk)

    assert t['position_note'] == 'Half-Kelly 권장 비중 3.2%'
