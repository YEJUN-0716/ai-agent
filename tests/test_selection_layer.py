"""선택 계층 재생기의 규칙 셋을 못 박는다.

이 셋이 틀리면 표는 멀쩡해 보이면서 판정만 뒤집힌다:

  1. **대기 주문이 자리를 문다.** 안 물면 20일치 주문이 겹쳐 쌓여 러너 팔이
     기준선에 가까워지고, 선택 계층이 아무것도 안 하는 것처럼 보인다.
  2. **기준선 팔에는 한도가 없다.** 자리·섹터·현금이 조금이라도 걸리면
     '선택 없음'이 아니라 '약한 선택'을 기준선으로 삼게 된다.
  3. **`cash_short` 은 체결 쪽으로 센다.** 값은 구간에 닿았고 못 산 이유가
     우리 현금이다 — 폐기로 세면 백테스트(현금 무제한)와 다른 자가 된다
     (사전 등록 §2, `2026-08-18-fill-fidelity.md`).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import measure_selection_layer as m  # noqa: E402

DAYS = pd.bdate_range("2024-01-01", periods=80)


def _fixture(tickers, limit):
    """모든 봉이 같은 종목들 — 체결 여부는 limit 하나로 정해진다."""
    bars = {tk: [(d, 100.0, 101.0, 99.0, 100.0) for d in DAYS] for tk in tickers}
    closes = pd.DataFrame({tk: pd.Series(100.0, index=DAYS) for tk in tickers})
    plans = pd.DataFrame([{"ticker": tk, "date": DAYS[0], "grade": "A",
                           "current": 100.0, "entry_ref": 100.0, "limit": limit,
                           "stop": 98.0, "target": 104.0, "rr": 2.0,
                           "risk_pct": 2.0} for tk in tickers])
    regime = pd.Series("bull", index=DAYS)
    sectors = {tk: f"S{i}" for i, tk in enumerate(tickers)}   # 섹터 한도는 안 걸리게
    return plans, bars, closes, sectors, regime


def test_pending_order_occupies_a_slot(monkeypatch):
    """자리 1칸에 후보 2종목 — 대기가 자리를 물면 그날 주문은 1건뿐이다."""
    monkeypatch.setattr(m, "REGIME_MAX_POS", {"bull": 1, "neutral": 1, "bear": 1})
    plans, bars, closes, sectors, regime = _fixture(["A", "B"], limit=90.0)
    orders, skips = m.replay(plans, bars, closes, sectors, regime, "runner")

    assert len(orders) == 1, orders
    assert skips["slot"] == 1
    assert orders[0]["outcome"] == "nofill"          # 90 에는 안 닿는다
    # 폐기일까지 자리를 물고 있다 — 20번째 봉이다
    assert orders[0]["event_day"] == DAYS[m.LIMIT_FILL_WINDOW]


def test_baseline_has_no_limits(monkeypatch):
    """같은 상황에서 기준선은 둘 다 주문한다 — 한도가 없어야 기준선이다."""
    monkeypatch.setattr(m, "REGIME_MAX_POS", {"bull": 1, "neutral": 1, "bear": 1})
    plans, bars, closes, sectors, regime = _fixture(["A", "B"], limit=90.0)
    orders, skips = m.replay(plans, bars, closes, sectors, regime, "baseline")

    assert len(orders) == 2, orders
    assert skips == {"slot": 0, "sector": 0, "size": 0, "cash": 0}


def test_cash_short_counts_on_the_fill_side():
    """폐기율 분모엔 들어가고 분자엔 안 들어간다."""
    s = m.rate([{"outcome": "nofill"}, {"outcome": "cash_short"},
                {"outcome": "filled"}, {"outcome": "censored"}])
    assert (s["n"], s["nofill"], s["cash_short"], s["censored"]) == (3, 1, 1, 1)
    assert abs(s["rate"] - 100 / 3) < 1e-9


def test_cash_short_when_ledger_cannot_pay(monkeypatch):
    """값은 닿았는데 현금이 모자라면 폐기가 아니라 cash_short 다.

    대기 주문은 현금을 **묶지 않는다** — 그래서 늦게 체결되는 주문이 먼저
    체결된 주문에 현금을 뺏길 수 있다. 러너 장부에서 실제로 나던 사유다.
    """
    monkeypatch.setattr(m, "plan_position_size", lambda *a, **k: 1)
    monkeypatch.setattr(m, "CAPITAL_KRW", 1.5 * 99.5 * m.FX)   # 딱 1주치 자본

    def bars_for(late: bool):
        # late=True 는 6번째 봉부터만 99.5 에 닿는다 (그전엔 저가 100)
        return [(d, 100.0, 101.0, 99.0 if (not late or i >= 6) else 100.0, 100.0)
                for i, d in enumerate(DAYS)]

    bars = {"LATE": bars_for(True), "FAST": bars_for(False)}
    closes = pd.DataFrame({tk: pd.Series(100.0, index=DAYS) for tk in bars})
    plans = pd.DataFrame([
        {"ticker": "LATE", "date": DAYS[0]},   # 먼저 걸리고 늦게 체결
        {"ticker": "FAST", "date": DAYS[1]},   # 나중에 걸리고 먼저 체결
    ]).assign(grade="A", current=100.0, entry_ref=100.0, limit=99.5,
              stop=98.0, target=104.0, rr=2.0, risk_pct=2.0)
    sectors = {"LATE": "S1", "FAST": "S2"}
    orders, skips = m.replay(plans, bars, closes, sectors,
                             pd.Series("bull", index=DAYS), "runner")

    got = {o["ticker"]: o["outcome"] for o in orders}
    assert got == {"LATE": "cash_short", "FAST": "filled"}, orders
    assert skips["cash"] == 0      # 주문 시점엔 둘 다 여력이 있었다
