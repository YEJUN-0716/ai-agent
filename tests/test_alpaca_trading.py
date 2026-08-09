"""Alpaca 래퍼가 다른 브로커와 **같은 모양**을 내는가, 그리고 단위를 안 섞는가.

이 파일이 지키는 건 두 가지다.

1. 러너는 브로커를 import 한 줄로 갈아끼운다. 토스·가상 브로커와 반환 칸
   이름이 하나라도 어긋나면 보고서가 조용히 0원을 찍는다.
2. **금액 단위.** 토스는 시장에 따라 원/달러가 갈렸고 가상 브로커는 원으로
   환산해 적었다. Alpaca 는 전부 달러다. 이 경계에서 2026-07-30 하루에 반대
   방향 사고가 두 건 났다 — 623달러가 623원이 되고, 50만원이 7억원이 됐다.

네트워크 없음. requests 를 통째로 가짜로 바꿔 끼운다.
"""

import json

import pytest
import requests

from modules import alpaca_trading as al
from paper_trade_runner_toss import order_accepted


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

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
    """모든 요청을 가로채는 가짜 API. `.sent` 에 마지막 요청이 남는다."""
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)

    box = {"sent": None, "reply": FakeResponse({})}

    def fake_request(method, url, **kwargs):
        box["sent"] = {"method": method, "url": url, **kwargs}
        return box["reply"]

    monkeypatch.setattr(al.requests, "request", fake_request)
    return box


# ── 안전장치 ───────────────────────────────────────────────────────────
def test_paper_account_is_the_default(monkeypatch):
    # 실계좌를 기본으로 두면 키를 잘못 넣은 날 진짜 주문이 나간다.
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    assert al.is_paper()
    monkeypatch.setenv("ALPACA_PAPER", "false")
    assert not al.is_paper()
    # 오타·빈 값은 페이퍼로 남는다 — 실계좌는 명시해야만 열린다.
    monkeypatch.setenv("ALPACA_PAPER", "")
    assert al.is_paper()


def test_missing_keys_fail_loudly(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="ALPACA_API_KEY"):
        al.get_account()


def test_krx_order_is_refused_not_silently_sent(api):
    # 유니버스에 .KS 가 하나 남아 있으면 Alpaca 로 흘러든다. 조용히 보내면
    # 알 수 없는 심볼로 거절되거나, 더 나쁘게는 동명의 미국 종목을 산다.
    with pytest.raises(ValueError, match="미국 주식 전용"):
        al.place_notional_buy("005930", 1000.0, market="KRX")
    assert api["sent"] is None


# ── 계좌 · 포지션 모양 ─────────────────────────────────────────────────
def test_account_carries_every_field_the_runner_reads(api):
    api["reply"] = FakeResponse({
        "equity": "10123.45", "buying_power": "5000.00",
        "account_blocked": False, "trading_blocked": False,
    })
    acct = al.get_account()
    # 러너가 읽는 칸. 이름이 어긋나면 자산 0원 → 드로다운 −100% 오진단이다.
    assert acct["equity"] == pytest.approx(10123.45)
    assert acct["buying_power"] == pytest.approx(5000.0)
    assert acct["account_blocked"] is False and acct["trading_blocked"] is False


def test_account_failure_raises_instead_of_reporting_zero(api):
    api["reply"] = FakeResponse({"message": "forbidden"}, status_code=403)
    # 0 을 돌려주면 킬스위치가 급락으로 오해하고 헛발질한다.
    with pytest.raises(requests.HTTPError):
        al.get_account()


def test_positions_match_the_other_brokers_shape(api):
    api["reply"] = FakeResponse([{
        "symbol": "AAPL", "qty": "10", "avg_entry_price": "150.5",
        "current_price": "160.0", "unrealized_pl": "95.0",
    }])
    pos = al.get_positions()[0]
    assert set(pos) >= {"symbol", "qty", "avg_entry_price",
                        "current_price", "unrealized_pl"}
    # 값은 문자열이다 — 토스·가상 브로커가 그렇고, 러너가 float() 로 받는다.
    assert pos["qty"] == "10" and pos["current_price"] == "160.0"


# ── 주문 ───────────────────────────────────────────────────────────────
def test_notional_buy_sends_dollars_and_never_converts(api):
    api["reply"] = FakeResponse({"id": "o1", "status": "accepted"})
    al.place_notional_buy("AAPL", 1370.0)

    body = api["sent"]["json"]
    # 1,370 이 그대로 나가야 한다. 환율이 곱해지면 192만이 되고, 나뉘면 0.97달러다.
    assert body["notional"] == "1370.0"
    assert body["side"] == "buy" and body["type"] == "market"
    # notional 주문은 시장가·당일만 허용된다. gtc 로 보내면 통째로 거절된다.
    assert body["time_in_force"] == "day"
    assert "qty" not in body


def test_limit_buy_holds_until_canceled_and_needs_whole_shares(api):
    api["reply"] = FakeResponse({"id": "o2", "status": "new"})
    al.place_limit_buy("NVDA", qty=7, limit_price=223.63)

    body = api["sent"]["json"]
    assert body["type"] == "limit" and body["limit_price"] == "223.63"
    # 진입 구간에 닿을 때까지 며칠이고 살아 있어야 한다. day 면 당일 만료다.
    assert body["time_in_force"] == "gtc"
    assert body["qty"] == "7"

    with pytest.raises(ValueError, match="1주 미만"):
        al.place_limit_buy("NVDA", qty=0, limit_price=223.63)


def test_rejection_reason_survives_the_exception(api):
    api["reply"] = FakeResponse(
        {"message": "insufficient buying power"}, status_code=403)
    with pytest.raises(requests.HTTPError, match="insufficient buying power"):
        al.place_notional_buy("AAPL", 1370.0)


def test_dry_run_sends_nothing(api):
    for call in (lambda: al.place_notional_buy("AAPL", 100.0, dry_run=True),
                 lambda: al.place_limit_buy("AAPL", 1, 100.0, dry_run=True),
                 lambda: al.place_market_sell("AAPL", 1, dry_run=True)):
        assert call()["status"] == "dry_run"
    assert api["sent"] is None


def test_fractional_sell_survives_the_round_trip(api):
    # notional 매수는 소수점 주식을 만든다. int() 로 자르면 잔량이 남아
    # 포지션이 안 닫히고, 다음 실행에서 유령 보유로 잡힌다.
    api["reply"] = FakeResponse({"id": "o3", "status": "accepted"})
    al.place_market_sell("AAPL", "6.8421")
    assert api["sent"]["json"]["qty"] == "6.8421"


# ── 체결 확인 ──────────────────────────────────────────────────────────
def test_fill_returns_price_when_terminal(api):
    api["reply"] = FakeResponse({
        "status": "filled", "filled_avg_price": "150.25", "filled_qty": "10",
    })
    fill = al.wait_for_fill("o1")
    assert fill["status"] == "filled"
    assert fill["filled_avg_price"] == "150.25"


def test_open_order_times_out_without_claiming_a_price(monkeypatch, api):
    api["reply"] = FakeResponse({"status": "new"})     # 지정가 대기 중
    monkeypatch.setattr(al.time, "sleep", lambda s: None)
    fill = al.wait_for_fill("o2", timeout=0)
    # 타임아웃은 '미체결'이 아니라 '알 수 없음'이다. 체결가를 지어내면 안 된다.
    assert fill["status"] == "timeout"
    assert fill["filled_avg_price"] is None


def test_expired_order_releases_its_slot_and_cash():
    # Alpaca 에만 있는 상태다. 러너가 이걸 '살아 있는 주문'으로 세면 자리와
    # 현금이 영영 안 풀려 상한이 통째로 꺼진다.
    assert order_accepted("expired") is False
    assert order_accepted("rejected") is False
    # GTC 주문이 오늘만 끝난 것 — 내일 다시 체결될 수 있으니 자리는 잡아 둔다.
    assert order_accepted("done_for_day") is True
    assert order_accepted("new") is True
