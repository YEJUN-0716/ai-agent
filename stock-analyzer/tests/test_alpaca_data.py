"""시세 모듈이 **기존 가격 패널과 같은 모양**을 내는가, 그리고 무료 플랜의
한도를 미리 잡는가.

이 파일이 지키는 건 셋이다.

1. 3단계가 `price_panel.load_intraday` 를 이 모듈로 갈아끼운다. (prices,
   ohlcv) 두 칸과 Open/High/Low/Close/Volume 컬럼, **tz 없는 UTC 인덱스**가
   어긋나면 지표 코드가 조용히 어긋나거나 TypeError 로 죽는다.
2. 무료 플랜은 웹소켓 30종목·계정당 연결 1개다. 넘겨서 서버에 끊기는 대신
   부르는 쪽에서 막는다.
3. 키가 틀렸을 때 재접속 루프를 돌면 안 된다.

네트워크 없음. requests 와 웹소켓을 통째로 가짜로 끼운다.
"""
import json

import pandas as pd
import pytest

from modules import alpaca_data as ad


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _bar(t, c, v=100):
    return {"t": t, "o": c - 1, "h": c + 1, "l": c - 2, "c": c, "v": v}


@pytest.fixture
def api(monkeypatch):
    """REST 를 가로챈다. `.sent` 에 마지막 요청 params 가 남는다."""
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.delenv("ALPACA_FEED", raising=False)

    box = {"sent": [], "replies": []}

    def fake_request(method, url, **kwargs):
        box["sent"].append({"url": url, "params": dict(kwargs.get("params") or {}),
                            "headers": kwargs.get("headers")})
        return box["replies"].pop(0)

    monkeypatch.setattr(ad, "_request_with_retry", fake_request)
    return box


# ── REST 분봉 ──────────────────────────────────────────────────────────
def test_bars_frame_matches_price_panel_shape(api):
    api["replies"] = [FakeResponse({"bars": {"AAPL": [
        _bar("2026-08-07T13:30:00Z", 200), _bar("2026-08-07T13:45:00Z", 202)]}})]

    df = ad.get_bars(["AAPL"], timeframe="15Min")["AAPL"]

    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.tz is None, "가격 패널은 tz 없는 UTC 축을 쓴다"
    assert df.index[0] == pd.Timestamp("2026-08-07 13:30:00")
    assert df["Close"].tolist() == [200, 202]


def test_bars_default_feed_is_free_iex(api):
    api["replies"] = [FakeResponse({"bars": {}})]
    ad.get_bars(["AAPL"])
    # sip 을 기본으로 두면 무료 계정에서 최근 15분이 통째로 빈다
    assert api["sent"][0]["params"]["feed"] == "iex"
    assert api["sent"][0]["params"]["adjustment"] == "all"


def test_bars_follows_pagination(api):
    api["replies"] = [
        FakeResponse({"bars": {"AAPL": [_bar("2026-08-07T13:30:00Z", 200)]},
                      "next_page_token": "p2"}),
        FakeResponse({"bars": {"AAPL": [_bar("2026-08-07T13:31:00Z", 201)]}}),
    ]
    df = ad.get_bars(["AAPL"], timeframe="1Min")["AAPL"]
    assert len(df) == 2
    assert api["sent"][1]["params"]["page_token"] == "p2"


def test_bars_dedupes_overlapping_pages(api):
    same = "2026-08-07T13:30:00Z"
    api["replies"] = [
        FakeResponse({"bars": {"AAPL": [_bar(same, 200)]}, "next_page_token": "p2"}),
        FakeResponse({"bars": {"AAPL": [_bar(same, 205)]}}),
    ]
    df = ad.get_bars(["AAPL"], timeframe="1Min")["AAPL"]
    assert len(df) == 1 and df["Close"].iloc[0] == 205


def test_krx_ticker_rejected(api):
    with pytest.raises(ValueError, match="미국 주식"):
        ad.get_bars(["005930.KS"])


def test_load_intraday_returns_price_panel_pair(api):
    api["replies"] = [FakeResponse({"bars": {
        "AAPL": [_bar("2026-08-07T13:30:00Z", 200), _bar("2026-08-07T13:45:00Z", 202)],
        "NVDA": [_bar("2026-08-07T13:30:00Z", 100)],
    }})]

    prices, ohlcv = ad.load_intraday(["AAPL", "NVDA"], min_bars=2)

    assert set(prices) == {"AAPL"}, "봉이 모자란 종목은 빠진다"
    assert isinstance(prices["AAPL"], pd.Series)
    assert "High" in ohlcv["AAPL"].columns


# ── 웹소켓 스트림 ──────────────────────────────────────────────────────
class FakeWS:
    """정해진 프레임을 순서대로 뱉고 끝나면 끊긴 척한다."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent   = []
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self):
        if not self.frames:
            raise ConnectionError("closed")
        return json.dumps(self.frames.pop(0))

    def close(self):
        self.closed = True


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.delenv("ALPACA_FEED", raising=False)


def test_stream_authenticates_then_subscribes(keys):
    ws = FakeWS([[{"T": "success", "msg": "connected"}]])
    stream = ad.stream_bars(["AAPL", "NVDA"], reconnect=False, _ws_factory=lambda url: ws)
    with pytest.raises(ConnectionError):
        next(stream)

    assert ws.sent[0]["action"] == "auth"
    assert ws.sent[0]["key"] == "key"
    assert ws.sent[1] == {"action": "subscribe", "bars": ["AAPL", "NVDA"]}


def test_stream_bar_matches_frame_columns(keys):
    ws = FakeWS([[{"T": "success", "msg": "authenticated"},
                  {"T": "b", "S": "AAPL", "t": "2026-08-07T13:30:00Z",
                   "o": 199, "h": 201, "l": 198, "c": 200, "v": 1234}]])
    bar = next(ad.stream_bars(["AAPL"], reconnect=False, _ws_factory=lambda u: ws))

    assert bar["symbol"] == "AAPL"
    assert bar["t"] == pd.Timestamp("2026-08-07 13:30:00")  # tz 없는 UTC
    assert (bar["Open"], bar["High"], bar["Low"], bar["Close"]) == (199, 201, 198, 200)
    assert bar["Volume"] == 1234 and isinstance(bar["Volume"], int)


def test_stream_free_plan_symbol_cap(keys):
    with pytest.raises(ValueError, match="30종목"):
        next(ad.stream_bars([f"S{i}" for i in range(31)], _ws_factory=lambda u: None))


def test_stream_paid_feed_has_no_cap(keys, monkeypatch):
    monkeypatch.setenv("ALPACA_FEED", "sip")
    seen = {}

    def factory(url):
        seen["url"] = url
        return FakeWS([[{"T": "success", "msg": "connected"}]])

    with pytest.raises(ConnectionError):
        next(ad.stream_bars([f"S{i}" for i in range(31)], reconnect=False,
                            _ws_factory=factory))
    assert seen["url"].endswith("/sip"), "피드가 URL 에 실려야 한다"


@pytest.mark.parametrize("code", [402, 406])
def test_stream_fatal_errors_do_not_reconnect(keys, code):
    """키 오류·연결 한도는 기다린다고 낫지 않는다. 재시도가 차단을 부른다."""
    calls = {"n": 0}

    def factory(url):
        calls["n"] += 1
        return FakeWS([[{"T": "error", "code": code, "msg": "nope"}]])

    with pytest.raises(ad.StreamFatalError):
        next(ad.stream_bars(["AAPL"], reconnect=True, _ws_factory=factory))
    assert calls["n"] == 1


def test_stream_reconnects_after_drop(keys, monkeypatch):
    """끊긴 뒤 다시 붙고 **다시 구독한다** — 재구독을 빠뜨리면 조용히 아무 봉도
    안 온다."""
    conns = []

    def factory(url):
        # 첫 연결은 곧장 끊기고, 두 번째가 봉을 준다
        frames = ([] if not conns else
                  [[{"T": "b", "S": "AAPL", "t": "2026-08-07T13:30:00Z",
                     "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]])
        ws = FakeWS(frames)
        conns.append(ws)
        return ws

    monkeypatch.setattr(ad.time, "sleep", lambda *_: None)   # 백오프 대기 건너뛴다

    bar = next(ad.stream_bars(["AAPL"], _ws_factory=factory))
    assert bar["symbol"] == "AAPL"
    assert len(conns) == 2
    assert conns[1].sent[1]["action"] == "subscribe"
    assert conns[0].closed, "끊긴 연결은 닫는다"


def test_an_empty_frame_is_a_drop_not_a_pause(keys, monkeypatch):
    """상대가 끊으면 recv() 는 빈 프레임을 계속 준다.

    그걸 건너뛰기만 하면 제너레이터가 CPU 를 태우며 영원히 돌고 재접속이 한 번도
    안 걸린다. 끊김으로 올려야 stream_bars 가 다시 붙는다.
    """
    class DeadWS(FakeWS):
        def recv(self):
            return ""

    monkeypatch.setattr(ad.time, "sleep", lambda s: None)
    with pytest.raises(ConnectionError):
        next(ad.stream_bars(["AAPL"], reconnect=False, _ws_factory=lambda u: DeadWS([])))

    # reconnect=True 면 조용히 도는 대신 실제로 다시 붙는다.
    tries = []

    def factory(url):
        tries.append(url)
        if len(tries) == 1:
            return DeadWS([])
        return FakeWS([[{"T": "b", "S": "AAPL", "t": "2026-08-07T13:30:00Z",
                         "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]])

    assert next(ad.stream_bars(["AAPL"], _ws_factory=factory))["symbol"] == "AAPL"
    assert len(tries) == 2
