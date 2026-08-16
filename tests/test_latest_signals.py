"""[오늘] 화면이 읽는 latest_signals.json — 기록 계약과 신선도 판정.

이 두 가지가 깨지면 화면이 조용히 틀린 걸 보여준다:
- 워커가 액션 dict 를 변환해서 적으면 카드가 빈칸으로 뜬다(액션 원소는 그대로 적는다)
- 신선도 판정이 틀리면 어제 액션이 오늘 것처럼 보인다
"""
import json
from datetime import datetime, timedelta

import app
import signal_worker


def test_save_latest_signals_keeps_action_dicts_intact(tmp_path):
    actions = [{'ticker': 'AAPL', 'action': '🟢 매수', 'weight': '4.0%', 'price': '$333.74',
                'alloc': '$400', 'qty': '1.20주', 'reason': '팩터 61점', 'priority': 'HIGH',
                'mom': '+8.3%'}]
    rebal = {'next_rebal': '3일 후', 'buy_count': 1, 'sell_count': 0, 'hold_count': 0}
    path = tmp_path / "latest_signals.json"

    signal_worker.save_latest_signals(actions, rebal, 'S&P 500 전체 (500종목)', 10000, path=path)

    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['actions'] == actions          # 변환 없음
    assert saved['rebal'] == rebal
    assert saved['universe'] == 'S&P 500 전체 (500종목)'
    assert saved['capital'] == 10000
    # 시각은 타임존이 붙어야 한다 — 없으면 신선도 계산이 서버 위치에 따라 흔들린다
    assert datetime.fromisoformat(saved['generated_at']).tzinfo is not None


def test_freshness_flags_stale_batch():
    now = datetime(2026, 8, 17, 15, 0).astimezone()      # 월요일 오후

    two_h = app._signals_freshness((now - timedelta(hours=2)).isoformat(), now=now)
    assert two_h['when'] == '2시간 전'
    assert not two_h['stale'] and not two_h['too_old']

    # 금요일 배치를 월요일에 보고 있다 — 마지막 개장일(월)보다 오래됐으므로 경고
    old = app._signals_freshness((now - timedelta(days=3)).isoformat(), now=now)
    assert old['stale'] and not old['too_old']

    ancient = app._signals_freshness((now - timedelta(days=9)).isoformat(), now=now)
    assert ancient['too_old']

    assert app._signals_freshness('없는 시각') is None


def test_actions_sorted_sells_before_buys_within_priority():
    actions = [
        {'ticker': 'BUY1', 'action': '🟢 매수', 'priority': 'HIGH'},
        {'ticker': 'SELL1', 'action': '🔴 매도', 'priority': 'HIGH'},
        {'ticker': 'WAIT1', 'action': '🟡 대기', 'priority': 'LOW'},
        {'ticker': 'TRIM1', 'action': '🟠 비중축소', 'priority': 'NORMAL'},
    ]
    live, quiet = app._sort_actions(actions)
    assert [a['ticker'] for a in live] == ['SELL1', 'BUY1', 'TRIM1']
    assert [a['ticker'] for a in quiet] == ['WAIT1']
