"""러너의 시그널 채점 — signal_log.json 에 실제로 값을 쓰는 경로.

화면(app.py)에는 채점 테스트가 4개 있었는데 이쪽엔 0개였고, 그래서 화면만
21거래일로 고쳐지고 러너는 달력 21일 + '도는 날 종가' 로 남아 있었다.
파일에 쓰는 쪽이 프로덕션이다 — 여기에 잠근다.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paper_trade_runner_toss as runner  # noqa: E402
from modules import signal_scorecard  # noqa: E402


def _closes(n, start='2026-07-01'):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(np.arange(100.0, 100.0 + n), index=idx)


def test_runner_scores_on_the_21st_bar_not_the_day_it_runs():
    """봉이 더 쌓여도 답이 안 변해야 한다 — 변하면 '러너가 도는 날'이 채점일이다."""
    sig = {'symbol': 'AAA', 'entry_date': '2026-07-01', 'entry_price': 100.0,
           'return_pct': None}
    out30 = runner.resolve_signal_outcomes([dict(sig)], {'AAA': _closes(30)})[0]
    out90 = runner.resolve_signal_outcomes([dict(sig)], {'AAA': _closes(90)})[0]
    assert out30['return_pct'] == out90['return_pct'] == pytest.approx(21.0)
    assert out30['outcome_date'] == out90['outcome_date']


def test_runner_waits_when_21_bars_have_not_passed():
    """달력 21일이 지났어도 봉이 15개면 채점하지 않는다.

    실측(2026-08-22): 채점된 11건 중 5건이 이 자리에서 조기 확정됐다.
    """
    sig = {'symbol': 'AAA', 'entry_date': '2026-07-01', 'entry_price': 100.0,
           'return_pct': None}
    out = runner.resolve_signal_outcomes([sig], {'AAA': _closes(16)})[0]
    assert out['return_pct'] is None
    assert out.get('outcome_date') is None


def test_runner_never_rescores_a_finished_signal():
    sig = {'symbol': 'AAA', 'entry_date': '2026-07-01', 'entry_price': 100.0,
           'return_pct': -3.0, 'outcome_date': '2026-07-30'}
    out = runner.resolve_signal_outcomes([sig], {'AAA': _closes(90)})[0]
    assert out['return_pct'] == -3.0 and out['outcome_date'] == '2026-07-30'


def test_runner_and_screen_share_one_rule():
    """사본이 다시 갈리지 않게 잠근다 — 러너·화면 둘 다 이 함수를 부른다."""
    import app
    assert app.score_signal is signal_scorecard.score_signal
    src = open(runner.__file__, encoding='utf-8').read()
    assert 'signal_scorecard.score_signal' in src
    # 달력일 게이트가 되살아나면 여기서 걸린다.
    assert 'SIGNAL_HOLD_DAYS' not in src


# ── 러너 성적표와 백테스트가 같은 자를 쓰는가 ─────────────────────

def _ledger(outcome, r):
    return {"plan": True, "side": "sell", "outcome": outcome, "r_realized": r}


def _bt(outcome, r):
    return {"outcome": outcome, "r": r, "direction": "long"}


def test_runner_stats_match_backtest_definitions():
    """같은 트레이드 집합이면 승률·기대값·평균이 세 자리까지 같아야 한다.

    예전에는 러너가 승률 분모에 timeout 을 넣고 백테스트는 뺐다. 이름
    `avg_r` 도 백테스트에서는 '체결 전체' 인데 러너에서는 '결판 평균'
    이었다. 두 자가 갈리면 "장부가 백테스트 근처에 있나" 를 물을 수 없다.
    """
    from modules.trade_plan_backtest import _stats

    cases = [("win", 2.0), ("win", 1.5), ("loss", -1.4), ("loss", -1.0),
             ("timeout", 0.8), ("timeout", -0.3)]
    mine = runner.plan_trade_summary([_ledger(o, r) for o, r in cases])
    theirs = _stats([_bt(o, r) for o, r in cases])

    assert mine["win_rate"] == pytest.approx(theirs["win_rate"] * 100, abs=0.05)
    assert mine["expectancy_r"] == pytest.approx(theirs["expectancy_r"], abs=5e-4)
    assert mine["avg_r"] == pytest.approx(theirs["avg_r"], abs=5e-4)


def test_timeout_is_kept_out_of_win_rate_but_counted_at_its_real_r():
    """timeout 은 승률 분모에서 빠지고, 평균R 에는 실제로 받은 R 로 들어간다."""
    s = runner.plan_trade_summary([
        _ledger("win", 2.0), _ledger("loss", -1.0), _ledger("timeout", 1.9)])
    assert s["n_decided"] == 2 and s["n_timeout"] == 1
    assert s["win_rate"] == pytest.approx(50.0)          # 1/2, 1/3 이 아니다
    assert s["avg_r"] == pytest.approx((2.0 - 1.0 + 1.9) / 3, abs=5e-4)
    assert "avg_r_realized" not in s        # 자가 둘로 갈라지면 여기서 걸린다
