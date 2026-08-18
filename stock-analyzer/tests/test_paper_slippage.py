"""3a.5 슬리피지 측정의 산수 — 부호와 R 환산.

이 파일이 지키는 건 하나다: **비용은 언제나 양수**여야 한다. 매수는 비싸게
사면 비용, 매도는 싸게 팔면 비용이라 부호가 다리마다 뒤집힌다. 여기서 한 번
뒤집히면 "왕복 −3bp" 같은 숫자가 나와 손익분기 판정이 통째로 거짓이 된다.

스톱 매도 주문 본문도 같이 잰다 — 백테스트가 손절가 정확 체결을 가정하므로
그 가정을 재는 주문이 엉뚱한 타입으로 나가면 측정 자체가 무의미해진다.

네트워크 없음.
"""
import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from modules import alpaca_trading as al  # noqa: E402
from measure_paper_slippage import slip_bp, summarize  # noqa: E402


# ── 부호 ───────────────────────────────────────────────────────────────
def test_buying_above_mid_is_a_cost():
    # mid 100 에서 100.05 에 샀으면 5bp 손해다.
    assert slip_bp("buy", 100.0, 100.05) == pytest.approx(5.0)


def test_selling_below_the_stop_is_a_cost():
    # 손절가 100 인데 99.95 에 체결됐으면 5bp 손해다. 백테스트는 이걸 0 으로 뒀다.
    assert slip_bp("sell", 100.0, 99.95) == pytest.approx(5.0)


def test_favourable_fills_are_negative():
    # 스톱이 손절가보다 좋게 채워질 수도 있다(갭업 트리거). 비용의 반대라 음수다.
    assert slip_bp("sell", 100.0, 100.02) == pytest.approx(-2.0)
    assert slip_bp("buy", 100.0, 99.98) == pytest.approx(-2.0)


def test_zero_price_is_rejected():
    # 체결가 None 을 0 으로 흘려보내면 -10000bp 가 표본에 섞인다.
    with pytest.raises(ValueError):
        slip_bp("buy", 100.0, 0)


# ── 요약 ───────────────────────────────────────────────────────────────
def _row(leg, bp):
    return {"leg": leg, "slip_bp": bp}


def test_round_trip_adds_entry_and_exit_in_R():
    rows = [_row("entry", 2.0), _row("entry", 4.0),
            _row("stop_exit", 6.0), _row("stop_exit", 8.0)]
    s = summarize(rows)
    assert s["legs"]["entry"]["median"] == pytest.approx(3.0)
    assert s["legs"]["stop_exit"]["median"] == pytest.approx(7.0)
    # 왕복 10bp = 0.10%. 손절폭 0.30% 면 1R 의 3분의 1이다.
    assert s["round_trip_stop_exit"]["bp"] == pytest.approx(10.0)
    assert s["round_trip_stop_exit"]["R"] == pytest.approx(1 / 3, abs=1e-3)


def test_missing_leg_produces_no_round_trip():
    # 진입만 재고 청산을 못 잰 실행에서 왕복 비용을 지어내면 안 된다.
    s = summarize([_row("entry", 2.0)])
    assert "round_trip_stop_exit" not in s
    assert "round_trip_market_exit" not in s


def test_unfilled_rows_do_not_count_as_zero_slippage():
    # 미체결은 slip_bp=None 으로 남는다. 0 으로 세면 비용이 낮게 보인다.
    rows = [_row("entry", 6.0), {"leg": "entry", "slip_bp": None}]
    s = summarize(rows)
    assert s["legs"]["entry"]["n"] == 1
    assert s["legs"]["entry"]["median"] == pytest.approx(6.0)


# ── 스톱 주문 ──────────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code, self.headers = payload, status_code, {}

    @property
    def text(self):
        return json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    box = {"sent": None, "reply": FakeResponse({"id": "o1", "status": "new"})}

    def fake_request(method, url, **kwargs):
        box["sent"] = {"method": method, "url": url, **kwargs}
        return box["reply"]

    monkeypatch.setattr(al.requests, "request", fake_request)
    return box


def test_stop_sell_goes_out_as_a_day_stop(api):
    al.place_stop_sell("AAPL", 1, 231.456)
    body = api["sent"]["json"]
    assert body["type"] == "stop"
    assert body["side"] == "sell"
    assert body["stop_price"] == "231.46"
    # GTC 로 두면 밤을 넘겨 다음 날 갭에 터진다. 단타는 당일 청산이다.
    assert body["time_in_force"] == "day"


def test_stop_sell_rejects_fractional_and_zero(api):
    with pytest.raises(ValueError):
        al.place_stop_sell("AAPL", 0, 100.0)
    with pytest.raises(ValueError):
        al.place_stop_sell("AAPL", 1, 0)
    assert api["sent"] is None      # 거절은 네트워크를 타지 않는다


def test_paper_only_guard_exists():
    # 측정 스크립트는 실계좌에서 돌면 진짜 돈으로 30번 사고 판다.
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "measure_paper_slippage.py"), encoding="utf-8").read()
    assert "at.is_paper()" in src


# ── 호가 ───────────────────────────────────────────────────────────────
def test_one_sided_quotes_are_dropped(monkeypatch):
    from modules import alpaca_data as ad
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setattr(ad, "_request_with_retry", lambda *a, **k: FakeResponse({
        "quotes": {
            "AAPL": {"bp": 100.0, "ap": 100.02, "t": "x"},
            "MRK":  {"bp": 0, "ap": 55.0, "t": "x"},      # IEX 에 매수호가 없음
            "KO":   {"bp": 60.0, "ap": 59.0, "t": "x"},   # 뒤집힌 호가
        }}))
    q = ad.latest_quotes(["AAPL", "MRK", "KO"])
    assert set(q) == {"AAPL"}
    assert q["AAPL"]["mid"] == pytest.approx(100.01)
    assert q["AAPL"]["spread_bp"] == pytest.approx(2.0, abs=0.01)
