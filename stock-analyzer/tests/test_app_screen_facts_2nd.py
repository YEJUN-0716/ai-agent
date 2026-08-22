"""화면이 사실을 말하는가 — app.py **2차** 리뷰에서 나온 네 자리.

1차(test_app_screen_facts)와 같은 모양이지만 재료가 다르다. 이번 넷은 전부
"규칙은 저장소 어딘가에 이미 있는데 화면 경로만 그 규칙을 안 지킨다" 였다.

  ① 재무 조회 실패를 중립 50 으로 섞지 않는다 (기록은 이미 안 섞고 있었다)
  ② [오늘] 빈 화면이 '아직'과 '꺼져 있다'를 가른다 (workflow_cron 은 이미 있었다)
  ③ 국면 배지·국면 조정 점수는 가중치와 **같은 자**를 쓴다 (스트립은 이미 그랬다)
  ④ 포지션 대사는 진짜 장부와 비교한다 (빈 리스트를 넘기고 있었다)
"""

import json

import pandas as pd

import app
from modules import analyst_team


# ── ① 재무를 못 받으면 총괄 판정을 내지 않는다 ────────────────────

def _rep(name, score, role='directional'):
    return app.build_analyst_report(name, '·', score, [], role=role)


def test_fundamental_unavailable_is_one_rule():
    """판별을 두 곳에 적으면 갈린다 — 실제로 signal_worker 에만 있었다."""
    assert analyst_team.fundamental_unavailable({'데이터없음': True})
    assert analyst_team.fundamental_unavailable({'오류': 'HTTP 429'})
    assert not analyst_team.fundamental_unavailable({'업종': 'Technology'})


def test_quant_card_shows_no_score_when_fundamentals_fail():
    """야후가 막히면 fundamental_score 는 50.55 를 돌려준다. 그 숫자가 카드에
    '퀀트+재무 50점 · 중립' 으로 뜨면 못 받은 것이 중립 판단으로 읽힌다."""
    rep = app.quant_fundamental_analyst(50.55, {'데이터없음': True, '업종': 'N/A'})
    assert rep['unavailable'] is True
    assert rep['score'] is None
    assert rep['verdict'] == '판정 불가'
    assert any('점수 없음' in r for r in rep['reasons'])


def test_verdict_is_withheld_when_a_directional_analyst_has_no_score():
    """방향성 3인이 모두 있어야 판정을 낸다 — analyst_team.verdict_score 와 같은 규칙.

    기록 3,307 종목일로 재보니, 실패를 50.55 로 채우면 총괄 라벨이 17.8%
    (bull 가중치) 뒤집혔다. 퀀트 비중이 66% 인 bear 국면이면 21.7% 다.
    """
    reports = [_rep('차트+파동+모멘텀', 80.0),
               app.quant_fundamental_analyst(50.55, {'데이터없음': True}),
               _rep('ICT+CRT', 75.0)]
    mgr = app.manager_consolidate(reports)
    assert mgr['total_score'] is None
    assert mgr['verdict'] is None
    assert '퀀트+재무' in mgr['unavailable']
    assert '판정 불가' in mgr['consensus']


def test_verdict_is_normal_when_all_three_have_scores():
    reports = [_rep('차트+파동+모멘텀', 80.0), _rep('퀀트+재무', 70.0), _rep('ICT+CRT', 75.0)]
    mgr = app.manager_consolidate(reports)
    assert mgr['total_score'] is not None
    assert mgr['verdict'] in ('매수', '중립', '매도')
    assert mgr['unavailable'] == []


def test_score_less_report_never_renders_a_number():
    """숫자 자리를 비우는 것까지 잠근다 — 카드가 score 를 그대로 찍으면 죽거나 50 을 낸다."""
    rep = app.quant_fundamental_analyst(50.55, {'오류': 'boom'})
    assert app._normalize_report(rep)['headline'] == '판정 불가 — 점수 없음'
    color, blink = app._module_accent(rep)
    assert blink is False, '점수가 없으면 매도 경보로 점멸시키지 않는다'


# ── ② [오늘] 빈 화면: '아직'과 '꺼져 있다'는 다른 말 ───────────────

def test_today_empty_state_distinguishes_disabled_from_pending(tmp_path, monkeypatch):
    """이 패널이 읽는 파일을 쓰는 잡은 2026-07-20 부터 꺼져 있었는데 화면은
    '아직 오늘 배치가 안 돌았습니다' 라고만 했다. 켜짐/꺼짐/없음 셋을 가른다."""
    monkeypatch.setattr(app, '_WORKFLOW_DIR', str(tmp_path))
    monkeypatch.setattr(app, '_load_latest_signals', lambda: None)

    said = []
    monkeypatch.setattr(app.st, 'markdown', lambda html, **k: said.append(html))
    monkeypatch.setattr(app.st, 'button', lambda *a, **k: False)

    (tmp_path / 'signal-alerts.yml').write_text(
        'on:\n  # schedule:\n  #   - cron: "30 22 * * 1-5"\n  workflow_dispatch:\n',
        encoding='utf-8')
    app.render_today_actions()
    assert '꺼져 있습니다' in said[-1]

    (tmp_path / 'signal-alerts.yml').write_text(
        'on:\n  schedule:\n    - cron: "30 22 * * 1-5"\n', encoding='utf-8')
    app.render_today_actions()
    assert '아직 오늘 배치가 안 돌았습니다' in said[-1]

    (tmp_path / 'signal-alerts.yml').unlink()
    app.render_today_actions()
    assert '찾지 못했습니다' in said[-1]


def test_app_can_write_the_file_the_today_screen_reads(tmp_path):
    """앱 안에서 시그널을 돌려도 [오늘] 화면이 채워져야 한다.

    이 파일을 쓰는 곳이 러너뿐이었고 그 크론은 꺼져 있었다 — 빈 화면이
    안내하는 탈출구(앱에서 직접 돌리기)를 따라가도 계속 비어 있었다.
    """
    from signal_worker import save_latest_signals
    actions = [{'ticker': 'AAPL', 'action': '매수', 'priority': 'HIGH',
                'price': '$100', 'reason': '테스트'}]
    path = tmp_path / 'latest_signals.json'
    save_latest_signals(actions, {'buy_count': 1}, 'S&P 500 대형 30', 100000, path=path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['actions'] == actions and saved['generated_at']


# ── ③ 국면은 한 자로만 잰다 ───────────────────────────────────────

def test_analysis_regime_uses_the_weight_ruler():
    """스트립은 러너 자(_weight_regime), 분석 블록은 옛 라벨 자를 쥐고 있었다 —
    1,680거래일 중 231일(13.8%) 다른 답, 26일 정반대. 행동을 바꾸는 쪽을 옮겼다."""
    src = __import__('inspect').getsource(app.main)
    body = '\n'.join(ln for ln in src.splitlines() if not ln.lstrip().startswith('#'))
    assert '_regime = _weight_regime()' in body
    assert '_regime, _regime_diff = get_market_regime()' not in body


def test_label_ruler_is_still_available_for_the_record():
    """regime_of 는 지운 게 아니다 — 성적표·백필이 과거 날짜에 붙인 라벨이
    그 자로 찍혀 있어 바꾸면 기록이 안 이어진다."""
    closes = pd.Series(range(1, 261), dtype=float)
    assert app.regime_of(closes)[0] in ('bull', 'bear', 'neutral')


# ── ④ 포지션 대사는 진짜 장부와 비교한다 ──────────────────────────

def test_reconcile_against_empty_broker_is_meaningless():
    """빈 리스트를 실제 포지션으로 넘기면 mismatches 가 비어서 화면에
    '❌ 불일치 0건' 이 뜬다 — 이 모양을 다시 만들지 않게 못 박는다."""
    from modules.ops_safety import reconcile_positions
    blind = reconcile_positions({'AAPL': 10.0}, [])
    assert blind['ok'] is False and blind['mismatches'] == []

    real = reconcile_positions({'AAPL': 10.0}, [{'symbol': 'AAPL', 'qty': 10.0}])
    assert real['ok'] is True and real['matched']


def test_reconcile_widget_reads_the_ledger_positions():
    """위젯이 넘기는 실제 포지션의 모양 — 장부의 positions 를 그대로 쓴다."""
    ledger = {'positions': {'AAPL': {'qty': 3}, 'MSFT': {'qty': 0}}}
    actual = [{'symbol': s, 'qty': float((p or {}).get('qty', 0) or 0)}
              for s, p in (ledger.get('positions') or {}).items()]
    from modules.ops_safety import reconcile_positions
    assert reconcile_positions({'AAPL': 3.0, 'MSFT': 0.0}, actual)['ok'] is True


def test_killswitch_widget_is_gone():
    """러너의 킬스위치와 무관한 세션 위젯이 '거래 허용 상태'를 찍고 있었다."""
    src = __import__('inspect').getsource(app.main)
    assert '_KillSwitch(' not in src
    assert not hasattr(app, '_KillSwitch')


# ── 곁들여: 지표 IC 사본의 lag ────────────────────────────────────

def test_indicator_ic_uses_the_same_lag_as_the_live_score():
    """calc_indicator_ics 는 9개 지표의 세 번째 사본이다. 주석은
    'bt_signals_full/technical_score와 동기화' 인데 OBV 다이버전스만
    옛 lag(shift(20)/shift(5))으로 남아 있었다 — 스칼라 iloc[-N] 은 N-1봉 전."""
    src = __import__('inspect').getsource(app.calc_indicator_ics)
    assert 'obv.shift(4)' in src and 'obv.shift(19)' in src
    assert 'obv.shift(5)' not in src and 'obv.shift(20)' not in src
