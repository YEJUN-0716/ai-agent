"""화면이 사실을 말하는가 — app.py 7덩어리 리뷰에서 나온 다섯 자리.

전부 "코드는 도는데 화면·백테스트가 다른 사실을 말한다" 는 모양이라 예외로는
안 잡힌다. 여기서 잠그는 것:

  1. 백테스트 벡터 점수 == 라이브 스칼라 점수 (`iloc[-N]` vs `shift(N)` 한 봉)
  2. 워크플로 크론은 파일에서 읽는다 (주석 처리 = 꺼진 것)
  3. 리스크 화면의 Sharpe 키는 하드코딩하지 않는다
  4. 반대 의견은 **반대한 사람**을 가리킨다
  5. 시그널 채점은 화면 여는 날이 아니라 봉이 정한다
"""

import numpy as np
import pandas as pd
import pytest

import app


# ── 1. 백테스트 점수 == 라이브 점수 ────────────────────────────────

def _ohlcv(n=320, seed=7):
    """지표 9개가 전부 살아나는 길이의 합성 봉. 추세·조정·횡보를 섞는다 —
    한 방향으로만 가면 다이버전스·크로스 분기가 한 번도 안 밟힌다."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1.1, n) + np.sin(np.arange(n) / 17) * 1.3
    close = 100 + np.cumsum(steps)
    close = np.maximum(close, 5.0)
    high = close + np.abs(rng.normal(0, 0.6, n))
    low = close - np.abs(rng.normal(0, 0.6, n))
    vol = rng.integers(5_000, 50_000, n).astype(float)
    idx = pd.bdate_range('2024-01-01', periods=n)
    return pd.DataFrame({'Open': close, 'High': high, 'Low': low,
                         'Close': close, 'Volume': vol}, index=idx)


@pytest.mark.parametrize('seed', [1, 7, 42])
def test_bt_signals_full_matches_technical_score(seed):
    """벡터 경로의 마지막 봉은 스칼라 경로와 **같은 점수**여야 한다.

    안 맞던 시절 실측: 20종목 4,000봉에서 중위 0.47점·최대 9.35점 차이,
    임계값 58/42 에서 매수·매도·관망이 갈리는 봉 5.53%. 원인은 전부
    `p.iloc[-20]`(19봉 전) vs `p.shift(20)`(20봉 전) 한 봉이었다.
    """
    df = _ohlcv(seed=seed)
    vec = app.bt_signals_full(df)
    # 봉마다 스칼라를 다시 도는 건 비싸다 — 뒤쪽 40봉만 본다(창은 최대 120봉).
    for i in range(len(df) - 40, len(df)):
        scalar, _ = app.technical_score(df.iloc[:i + 1])
        assert float(vec.iloc[i]) == pytest.approx(scalar, abs=1e-9), (
            f"{df.index[i].date()} 벡터 {float(vec.iloc[i]):.4f} vs 스칼라 {scalar:.4f}")


def test_ma_cross_bonus_needs_trend():
    """ADX 가 약하면 크로스 보너스가 없다 — 스칼라는 `and has_trend` 밖으로
    안 나가는데 벡터만 `~gc_bonus` 로 +5 를 얹고 있었다."""
    src = __import__('inspect').getsource(app.bt_signals_full)
    assert 'gc_weak = gc & has_trend_bt' in src
    assert 'dc_weak = dc & has_trend_bt' in src


# ── 2. 워크플로 크론은 파일이 답한다 ──────────────────────────────

def test_workflow_cron_reads_live_schedule(tmp_path, monkeypatch):
    """켜짐/꺼짐을 파일에서 읽는다.

    화면이 이 둘을 정확히 **반대로** 말하고 있었다: 시그널 알림은 2026-07-20
    부터 주석 처리돼 있는데 "활성", 가상 장부 매매는 평일마다 돌면서 장부에
    31건을 기록하는 중인데 "크론 비활성화" 였다.

    예전엔 이 테스트가 실제 파일로 signal-alerts 가 꺼져 있음을 단언했다.
    그건 **운영 상태를 테스트에 박아 둔 것**이라, 알림을 되살리는 날 코드가
    멀쩡한데 CI 가 깨진다. 읽는 능력만 잠그고 상태는 픽스처로 만든다.
    """
    monkeypatch.setattr(app, '_WORKFLOW_DIR', str(tmp_path))
    (tmp_path / 'on.yml').write_text(
        'on:\n  schedule:\n    - cron: "30 21 * * 1-5"\n', encoding='utf-8')
    (tmp_path / 'off.yml').write_text(
        'on:\n  # schedule:\n  #   - cron: "30 22 * * 1-5"\n  workflow_dispatch:\n',
        encoding='utf-8')
    assert app.workflow_cron('on.yml') == ['30 21 * * 1-5']
    assert app.workflow_cron('off.yml') == [], '주석 처리는 꺼진 것이다'


def test_real_workflow_dir_is_readable():
    """실제 워크플로 디렉터리를 읽을 수 있어야 한다 — 경로가 어긋나면 화면이
    전부 '워크플로 파일 없음' 으로 떨어진다. 크론이 켜졌는지는 안 본다."""
    assert app.workflow_cron('paper-trade-us.yml') is not None


def test_workflow_cron_distinguishes_missing_from_disabled():
    """'꺼져 있다'(빈 목록)와 '그런 파일이 없다'(None)는 다른 답이다."""
    assert app.workflow_cron('아무것도아닌.yml') is None


def test_execution_mode_never_hardcodes_schedule(tmp_path, monkeypatch):
    """크론을 껐다 켜면 카드가 따라와야 한다."""
    monkeypatch.setattr(app, '_WORKFLOW_DIR', str(tmp_path))
    (tmp_path / 'paper-trade-us.yml').write_text(
        'on:\n  schedule:\n    - cron: "30 21 * * 1-5"\n', encoding='utf-8')
    (tmp_path / 'signal-alerts.yml').write_text(
        'on:\n  # schedule:\n  #   - cron: "30 22 * * 1-5"\n  workflow_dispatch:\n',
        encoding='utf-8')
    on = ' '.join(app.execution_mode_status()['reasons'])
    assert '30 21 * * 1-5' in on and '크론 활성' in on
    assert '수동 실행' in on          # 시그널 알림 쪽

    (tmp_path / 'paper-trade-us.yml').write_text(
        'on:\n  workflow_dispatch:\n', encoding='utf-8')
    off = ' '.join(app.execution_mode_status()['reasons'])
    assert '30 21' not in off
    assert '자동 매매 없음' in off


# ── 3. 리스크 화면의 Sharpe 키 ────────────────────────────────────

def test_sharpe_key_is_not_hardcoded():
    """`calc_risk_metrics` 는 그날 ^IRX 로 키를 만든다 — 화면이 특정 금리를
    박아 두면 get 이 늘 기본값 0 을 집는다. 실제로 2026-07-16 이후 26거래일
    내내 모든 종목이 'Sharpe 0.00 저조' 로 떴다(^IRX 3.66~3.81)."""
    # 주석에는 옛 키가 사유로 남아 있으므로 주석을 걷어내고 본다.
    src = '\n'.join(line.split('#')[0] for line
                    in __import__('inspect').getsource(app.main).splitlines())
    assert 'Sharpe (RF 4.5%)' not in src
    assert "k.startswith('Sharpe')" in src


# ── 4. 반대 의견은 반대한 사람을 가리킨다 ─────────────────────────

def _rep(name, score):
    return {'name': name, 'icon': '·', 'score': score, 'weight': 33.3,
            'role': 'directional', 'verdict': app._team_verdict(score),
            'reasons': [], 'detail': {}}


def test_dissent_names_the_dissenter():
    """한 명만 다르면 그 한 명을 짚는다. 예전 코드는 **동의하는** 사람을 골라서
    이 경우 아무 말도 안 했다 — 기록 실측으로 전체 판정의 66.4% 가 여기다."""
    mgr = app.manager_consolidate([_rep('가', 55), _rep('나', 55), _rep('다', 20)])
    assert mgr['verdict'] == '중립'
    assert mgr['dissent'] is not None
    assert '다' in mgr['dissent'] and '만' in mgr['dissent']


def test_dissent_silent_when_everyone_agrees():
    mgr = app.manager_consolidate([_rep('가', 72), _rep('나', 75), _rep('다', 78)])
    assert mgr['dissent'] is None


def test_dissent_does_not_say_only_when_all_disagree():
    """전원이 다르면 '…만' 은 거짓말이다 — 예전 코드가 유일하게 문구를 띄우던
    0.73% 의 경우가 하필 여기였고, 거기서 첫 번째 사람을 지목했다."""
    reports = [_rep('가', 80), _rep('나', 20), _rep('다', 20)]
    mgr = app.manager_consolidate(reports)
    dissenters = [r for r in reports if r['verdict'] != mgr['verdict']]
    if len(dissenters) == len(reports):
        assert '만' not in mgr['dissent']
        assert '전원' in mgr['dissent']


# ── 5. 시그널 채점은 봉이 정한다 ──────────────────────────────────

def _closes(n, start='2026-07-01'):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(np.arange(100.0, 100.0 + n), index=idx)


def test_score_signal_uses_the_21st_bar_not_today():
    """21거래일째 종가로 잰다. 봉이 더 쌓여도 답이 안 변해야 한다 —
    변하면 '화면을 연 날'이 채점일이라는 뜻이다."""
    entry = '2026-07-01'
    short = _closes(30)
    long_ = _closes(90)
    assert app.score_signal(short, entry, 100.0) == app.score_signal(long_, entry, 100.0)


def test_score_signal_counts_trading_days_not_calendar_days():
    """달력 21일은 15거래일쯤이다. 21거래일째 봉을 집는지 값으로 확인한다."""
    entry = '2026-07-01'
    closes = _closes(60)          # 진입 다음 거래일부터 101, 102, ... 로 오른다
    # 진입일(첫 봉)을 뺀 21번째 봉 = 100 + 21 = 121
    scored = app.score_signal(closes, entry, 100.0)
    assert scored['return_pct'] == pytest.approx(21.0)
    # 값과 함께 **그 값이 어느 봉의 것인지**도 온다 — 날짜를 부르는 쪽이
    # 채우면 채점 시점이 경로마다 갈린다(러너는 '도는 날', 화면은 '여는 날').
    assert scored['outcome_price'] == pytest.approx(121.0)
    assert scored['outcome_date'] == closes.index[21].strftime('%Y-%m-%d')


def test_score_signal_waits_when_bars_are_short():
    """봉이 모자라면 '아직 모른다'(None). 현재가로 때우지 않는다."""
    assert app.score_signal(_closes(10), '2026-07-01', 100.0) is None


def test_score_signal_rejects_bad_entry_price():
    assert app.score_signal(_closes(60), '2026-07-01', 0.0) is None
    assert app.score_signal(_closes(60), '2026-07-01', None) is None
