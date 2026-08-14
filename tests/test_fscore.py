"""app.calc_piotroski_fscore — 8항목이고, 모르는 항목은 0점이 아니다.

여기서 못 박는 것은 둘이다.

1. **분모는 8.** F8(매출총이익률 개선)은 재료가 없어서 뺐다 — EDGAR 279종목에서
   GrossProfit 33.0% · CostOfRevenue 55.6% 로, edgar_fundamentals.MIN_COVERAGE(70%)
   아래다. 항목 수를 종목마다 바꾸지 않는다.
2. **재료가 없으면 None.** 예전 코드는 결측 항목을 '❓' 로 찍으면서 점수는 안 주고
   분모는 9 로 뒀다. 그러면 은행·리츠가 부실해서가 아니라 태그가 없어서 F-Score 가
   낮아진다. 여기서 '0점(전부 실패)'과 '모름(None)'이 구별되는지 본다.

네트워크를 타지 않는다 — yf.Ticker 를 통째로 가짜로 바꾼다.
"""
import pandas as pd
import pytest

import app


def _stmt(rows):
    """index=계정명, 열 0=최근기 · 열 1=직전기. yfinance 재무제표와 같은 모양."""
    return pd.DataFrame({0: [v[0] for v in rows.values()],
                         1: [v[1] for v in rows.values()]}, index=list(rows))


def _fake_ticker(*, roa=0.10, drop=(), bad=False):
    """전 항목 통과하는 제조업체. drop 에 넣은 계정은 아예 없는 것으로 만든다."""
    fin = {'Net Income':    (100, 80),
           'Total Revenue': (900, 800),
           'Gross Profit':  (300, 250)}      # 있어도 점수에 안 들어가야 한다
    bal = {'Total Assets':            (1000, 1000),
           'Long Term Debt':          (100, 150) if not bad else (200, 150),
           'Total Current Assets':    (500, 400),
           'Total Current Liabilities': (200, 200),
           'Ordinary Shares Number':  (1000, 1000)}
    cf  = {'Operating Cash Flow': (200, 180)}
    for k in drop:
        fin.pop(k, None); bal.pop(k, None); cf.pop(k, None)

    class T:
        info = {'returnOnAssets': roa}
        financials    = _stmt(fin)
        balance_sheet = _stmt(bal)
        cashflow      = _stmt(cf)
    return lambda _tk: T()


@pytest.fixture
def patch_yf(monkeypatch):
    def _set(**kw):
        monkeypatch.setattr(app.yf, 'Ticker', _fake_ticker(**kw))
    return _set


def test_full_materials_scores_out_of_eight(patch_yf):
    patch_yf()
    score, sig = app.calc_piotroski_fscore('AAA')
    assert score == 8, sig
    assert len(sig) == app.FSCORE_ITEMS == 8
    assert all(v == '✅' for v in sig.values()), sig
    # F8 은 아예 항목이 아니다 — 매출총이익이 있어도 9점이 되지 않는다.
    assert not any(k.startswith('F8') for k in sig)


def test_missing_item_is_none_not_zero(patch_yf):
    """은행형: 유동자산/부채를 안 낸다 → 그 항목은 ❓, 점수는 None."""
    patch_yf(drop=('Total Current Assets', 'Total Current Liabilities'))
    score, sig = app.calc_piotroski_fscore('BANK')
    assert score is None, '재료 결측을 점수로 냈다 — 모름이 실패로 읽힌다'
    assert sig['F6 유동성개선'] == '❓'
    # 나머지 항목은 그대로 보여준다(화면에서 왜 못 냈는지 읽히도록).
    assert sig['F1 ROA>0'] == '✅'


def test_zero_is_not_none(patch_yf):
    """전 항목 실패는 0점이고 None 이 아니다 — 둘이 섞이면 이 수정이 무의미하다."""
    patch_yf(roa=-0.05, bad=True)
    score, sig = app.calc_piotroski_fscore('BAD')
    assert score is not None and score < 8
    assert sig['F1 ROA>0'] == '❌' and sig['F5 레버리지감소'] == '❌'


def test_roa_zero_is_a_value_not_a_gap(patch_yf):
    """ROA 0.0 은 결측이 아니다 — `if roa` 로 거르면 0% 회사가 모름으로 샌다."""
    patch_yf(roa=0.0)
    score, sig = app.calc_piotroski_fscore('FLAT')
    assert score == 7, sig
    assert sig['F1 ROA>0'] == '❌'


def test_none_becomes_neutral_in_fundamental_score(monkeypatch, patch_yf):
    """fs=None 이면 fundamental_score 는 F-Score 자리에 50(중립)을 넣고,
    'F-Score값'은 None 으로 남긴다 — 화면이 N/A 를 띄울 수 있어야 한다."""
    patch_yf(drop=('Total Current Assets',))
    monkeypatch.setattr(app, 'calc_piotroski_fscore',
                        lambda tk: (None, {'F6 유동성개선': '❓'}))
    _, det = app.fundamental_score('BANK')
    assert det['F-Score'] == 50.0
    assert det['F-Score값'] is None
