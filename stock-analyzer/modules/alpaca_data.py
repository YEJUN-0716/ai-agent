"""
Alpaca 시세 모듈 — 분봉 이력(REST) + 실시간 스트림(웹소켓)
=================================================================
`modules/alpaca_trading.py` 가 체결이면, 이쪽은 시세다. 인증 헤더와 재시도
로직은 그 모듈 것을 그대로 쓴다(같은 벤더, 같은 키).

⚠️ 시세 API 는 페이퍼/실계좌가 갈리지 않는다
---------------------------------------------
체결은 `paper-api` / `api` 로 도메인이 갈리지만 시세는 `data.alpaca.markets`
하나다. 페이퍼 계좌로도 진짜 시세를 받는다.

무료(Basic) 플랜의 실제 제약 — 여기서 설계가 갈린다
--------------------------------------------------
- **실시간은 IEX 거래소만.** 전 거래소 통합(SIP)은 월 $99. IEX 는 미국 주식
  거래량의 일부만 지나가므로, 거래가 뜸한 종목은 분봉이 통째로 비는 구간이
  생긴다. 유동성 큰 종목만 골라 쓰는 전제 위에서만 안전하다.
- **SIP 는 최근 15분을 못 준다.** `feed="sip"` 로 과거를 조회하면 지금부터
  15분 전까지가 비어 있다. `feed="iex"` 는 그 구멍이 없다(실시간).
  → 기본값을 iex 로 두는 이유다.
- **웹소켓 구독은 30 종목까지.** 단타 유니버스를 그 위로 키우면 조용히
  거절당하는 게 아니라 연결 자체가 끊긴다.
- **계정당 웹소켓 연결 1개.** 러너와 앱이 동시에 붙으면 나중에 붙은 쪽이
  앞의 것을 걷어찬다. 5단계 데몬을 붙일 때 스트림 소유자를 한 곳으로 정할 것.
- REST 200 회/분.

`ALPACA_FEED=sip` 로 유료 전환하면 위 제약이 전부 풀린다. 코드는 안 바뀐다.

분봉을 얻는 길이 두 개다 — 뭘 쓸 것인가
--------------------------------------
`load_intraday()` (REST 폴링) 와 `stream_bars()` (웹소켓) 는 **같은 봉**을 준다.
IEX 피드에서는 REST 도 실시간이라, 1분 주기 판단이면 REST 한 줄이면 끝난다
(30 종목을 한 번의 호출로 받는다 = 200회/분 한도의 0.5%).
웹소켓은 **1분을 못 기다릴 때** — 봉 중간의 손절 감시 같은 것 — 값을 한다.
3단계에서 어느 쪽으로 갈지 정한다.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

# 인증 헤더·재시도는 체결 모듈 것을 재사용한다. 같은 키 쌍이고, 백오프 규칙이
# 두 벌로 갈라지면 한쪽만 고치는 날이 온다.
from modules.alpaca_trading import _headers, _request_with_retry

_DATA_BASE   = "https://data.alpaca.markets"
_STREAM_BASE = "wss://stream.data.alpaca.markets/v2"

# 무료 플랜 한도. 넘으면 서버가 연결을 끊으므로 미리 잡는다.
_FREE_STREAM_SYMBOL_CAP = 30

# yfinance 패널과 같은 칸 이름. 기존 지표 코드(ict_analysis, trade_plan)가
# "Close"/"High" 를 그대로 찾으므로 여기서 맞춰 둔다.
_BAR_FIELDS = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}


def feed() -> str:
    """기본 iex(무료). `ALPACA_FEED=sip` 로 유료 통합 피드 전환."""
    return os.environ.get("ALPACA_FEED", "iex").strip().lower()


def _iso(dt: datetime) -> str:
    """Alpaca 는 RFC-3339 를 받는다. naive 는 UTC 로 간주한다."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 분봉 이력 (REST) ───────────────────────────────────────────────────
def get_bars(symbols, timeframe: str = "1Min", start=None, end=None,
             api_key: str = "", secret_key: str = "",
             feed_name: str = "", max_pages: int = 20,
             adjustment: str = "all") -> dict:
    """여러 종목의 봉을 한 번에 받는다 → {티커: DataFrame}.

    GET /v2/stocks/bars. timeframe 은 "1Min"/"5Min"/"15Min"/"1Hour"/"1Day".

    yfinance 의 60일 벽이 없다 — Alpaca 는 2016년치 분봉까지 준다. 15분봉
    채점을 만료 전에 서둘러 하던 제약([[scalp-scorecard-clock]])이 여기서
    풀린다.

    페이지네이션은 next_page_token 을 따라간다. max_pages 는 안전장치다 —
    잘못된 start 로 몇 년치를 긁어 오다 러너가 멈추는 것보다, 잘린 걸 알고
    다시 부르는 편이 낫다.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        return {}
    if any(s.endswith(".KS") or s.endswith(".KQ") for s in symbols):
        raise ValueError("Alpaca 는 미국 주식 전용입니다 — KRX 티커는 받지 않습니다.")

    end   = end   or datetime.now(timezone.utc)
    start = start or (end - timedelta(days=5))

    params = {
        "symbols":    ",".join(symbols),
        "timeframe":  timeframe,
        "start":      _iso(start),
        "end":        _iso(end),
        "limit":      10000,
        # 기본은 분할·배당 반영. 안 하면 분할일의 갭이 신호가 된다.
        # 시가총액을 계산할 때만 "raw" — 조정 종가에 당시 주식수를 곱하면
        # 나중에 분할한 회사의 과거 시총이 틀린다.
        "adjustment": adjustment,
        "feed":       feed_name or feed(),
    }
    hdrs = _headers(api_key, secret_key)

    collected: dict[str, list] = {}
    for _ in range(max_pages):
        resp = _request_with_retry("GET", f"{_DATA_BASE}/v2/stocks/bars",
                                   headers=hdrs, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        for sym, rows in (payload.get("bars") or {}).items():
            collected.setdefault(sym, []).extend(rows)
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    else:
        print(f"  [alpaca-data] {max_pages}페이지에서 끊었습니다 — 구간이 너무 깁니다.")

    return {sym: _to_frame(rows) for sym, rows in collected.items() if rows}


def latest_quotes(symbols, api_key: str = "", secret_key: str = "",
                  feed_name: str = "") -> dict:
    """현재 호가 → {티커: {"bid", "ask", "mid", "spread_bp", "ts"}}.

    GET /v2/stocks/quotes/latest. 30종목을 한 번에 받는다.

    ⚠️ 무료 플랜은 **IEX 호가**다. 체결은 전 거래소 통합(NBBO) 기준으로
    일어나므로 여기 스프레드가 실제보다 넓게 나온다. 슬리피지를 이 mid 대비로
    재면 **보수적(비용 과대)** 이다 — 그 방향이면 판단이 안전한 쪽으로 틀린다.

    호가가 한쪽만 있거나 0 인 종목은 뺀다(IEX 에 그 순간 호가가 없는 경우).
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        return {}

    resp = _request_with_retry(
        "GET", f"{_DATA_BASE}/v2/stocks/quotes/latest",
        headers=_headers(api_key, secret_key),
        params={"symbols": ",".join(symbols), "feed": feed_name or feed()},
        timeout=15)
    resp.raise_for_status()

    out = {}
    for sym, q in (resp.json().get("quotes") or {}).items():
        bid, ask = float(q.get("bp", 0) or 0), float(q.get("ap", 0) or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2
        out[sym] = {"bid": bid, "ask": ask, "mid": mid,
                    "spread_bp": (ask - bid) / mid * 1e4, "ts": q.get("t")}
    return out


def latest_trades(symbols, api_key: str = "", secret_key: str = "",
                  feed_name: str = "") -> dict:
    """마지막 **체결가** → {티커: price}. GET /v2/stocks/trades/latest.

    목표가 도달 판정에 쓴다. 호가(`latest_quotes`)를 쓰면 안 된다 — 무료 IEX
    호가는 2026-08-11 측정에서 30건 중 19건이 스프레드 20bp 초과였고 JPM 은
    622bp 로 찍혔다. 그 mid 로 목표를 재면 오지 않은 목표에 팔거나 온 목표를
    놓친다. 체결가는 실제로 일어난 거래라 그 왜곡이 없다.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        return {}

    resp = _request_with_retry(
        "GET", f"{_DATA_BASE}/v2/stocks/trades/latest",
        headers=_headers(api_key, secret_key),
        params={"symbols": ",".join(symbols), "feed": feed_name or feed()},
        timeout=15)
    resp.raise_for_status()
    out = {}
    for sym, t in (resp.json().get("trades") or {}).items():
        px = float(t.get("p", 0) or 0)
        if px > 0:
            out[sym] = px
    return out


def _to_frame(rows: list) -> pd.DataFrame:
    """Alpaca 봉 리스트 → OHLCV DataFrame.

    인덱스는 **UTC 기준 naive** 다. 가격 패널이 이 축을 쓴다 — 거래소마다
    tz 가 섞이면 '몇 봉 뒤'를 재는 쪽이 TypeError 로 죽거나 조용히 어긋난다.
    """
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True, format="ISO8601")
    df = df.set_index("t").sort_index()
    df.index = df.index.tz_localize(None)
    df.index.name = None
    df = df[[c for c in _BAR_FIELDS if c in df.columns]].rename(columns=_BAR_FIELDS)
    # 같은 봉이 두 페이지에 걸쳐 오는 경우가 있다.
    return df[~df.index.duplicated(keep="last")]


def load_intraday(tickers: list, timeframe: str = "15Min", days: int = 60,
                  min_bars: int = 1, api_key: str = "", secret_key: str = "") -> tuple:
    """`price_panel.load_intraday` 와 **같은 모양**: (prices, ohlcv).

    3단계에서 import 한 줄만 갈아끼우면 되도록 반환 형태를 맞춰 둔다.
    yfinance 쪽은 60일이 물리적 한계지만 여기서는 days 를 그냥 늘리면 된다.
    """
    frames = get_bars(tickers, timeframe=timeframe,
                      start=datetime.now(timezone.utc) - timedelta(days=days),
                      api_key=api_key, secret_key=secret_key)
    prices, ohlcv = {}, {}
    for tk, df in frames.items():
        df = df.dropna(subset=["Close"])
        if len(df) < min_bars:
            continue
        prices[tk] = df["Close"]
        ohlcv[tk]  = df
    return prices, ohlcv


# ── 실시간 스트림 (웹소켓) ─────────────────────────────────────────────
# 재접속하면 안 되는 오류들. 키가 틀렸거나 이미 다른 연결이 붙어 있는 상태에서
# 무한 재시도하면 서버 쪽 차단만 부른다.
_FATAL_STREAM_CODES = {
    401: "인증 전에 다른 메시지를 보냈습니다",
    402: "키가 틀렸습니다 (ALPACA_API_KEY / ALPACA_SECRET_KEY 확인)",
    403: "이미 인증된 연결입니다",
    404: "인증 시간 초과",
    406: "연결 한도 초과 — 이 계정으로 이미 다른 스트림이 붙어 있습니다",
    409: "이 구독은 플랜에 포함되지 않습니다 (SIP 는 유료)",
}


def stream_bars(symbols, channels=("bars",), api_key: str = "", secret_key: str = "",
                feed_name: str = "", reconnect: bool = True, _ws_factory=None):
    """분봉이 마감될 때마다 하나씩 내주는 제너레이터.

        for bar in stream_bars(["AAPL", "NVDA"]):
            print(bar["symbol"], bar["Close"])

    각 봉은 `_to_frame` 과 같은 칸 이름을 쓴다(Open/High/Low/Close/Volume +
    symbol, t). 그래서 받은 봉을 이력 DataFrame 에 그대로 이어 붙일 수 있다.

    끊기면 다시 붙고 다시 구독한다. 다만 **키 오류나 연결 한도(위 표)는
    올린다** — 그건 기다린다고 낫는 게 아니고, 재시도가 차단을 부른다.

    ponytail: 봉 채널만 정규화한다. trades/quotes 를 구독하면 원문 dict 가
    그대로 나온다 — 3단계가 봉으로 판단한다고 정해질 때까지 형태를 못 박지
    않는다.
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        raise ValueError("구독할 종목이 없습니다.")

    fd = feed_name or feed()
    if fd == "iex" and len(symbols) > _FREE_STREAM_SYMBOL_CAP:
        raise ValueError(
            f"무료 플랜 웹소켓은 {_FREE_STREAM_SYMBOL_CAP}종목까지입니다 "
            f"(요청 {len(symbols)}). 종목을 줄이거나 ALPACA_FEED=sip 로 전환하세요.")

    key    = api_key    or os.environ.get("ALPACA_API_KEY", "")
    secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise ValueError("Alpaca 키가 없습니다 — ALPACA_API_KEY / ALPACA_SECRET_KEY")

    sub = {"action": "subscribe"}
    for ch in channels:
        sub[ch] = symbols

    backoff = 1.5
    while True:
        ws = None
        try:
            ws = (_ws_factory or _connect)(f"{_STREAM_BASE}/{fd}")
            _send(ws, {"action": "auth", "key": key, "secret": secret})
            _send(ws, sub)
            backoff = 1.5   # 한 번 붙었으면 다음 끊김은 처음부터 센다
            for msg in _messages(ws):
                if msg.get("T") == "b":
                    yield _normalize_bar(msg)
                elif msg.get("T") == "error":
                    _raise_stream_error(msg)
                elif msg.get("T") in ("success", "subscription"):
                    continue
                else:
                    yield msg
        except StreamFatalError:
            raise
        except Exception as e:                      # noqa: BLE001 — 끊김은 종류가 많다
            if not reconnect:
                raise
            print(f"  [alpaca-data] 스트림 끊김({type(e).__name__}: {e}) → "
                  f"{backoff:.1f}s 후 재접속")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:                   # noqa: BLE001
                    pass


class StreamFatalError(RuntimeError):
    """재접속으로 나아지지 않는 스트림 오류 (키·연결 한도·플랜)."""


def _raise_stream_error(msg: dict):
    code = int(msg.get("code", 0) or 0)
    text = _FATAL_STREAM_CODES.get(code, msg.get("msg", "알 수 없는 오류"))
    if code in _FATAL_STREAM_CODES:
        raise StreamFatalError(f"Alpaca 스트림 오류 {code}: {text}")
    raise RuntimeError(f"Alpaca 스트림 오류 {code}: {msg.get('msg', '')}")


def _connect(url: str):
    # 지연 import — 시세 이력(REST)만 쓰는 호출자는 이 의존성 없이도 돌아간다.
    import websocket
    return websocket.create_connection(url, timeout=60)


def _send(ws, payload: dict):
    ws.send(json.dumps(payload))


def _messages(ws):
    """웹소켓 프레임 하나가 메시지 여러 개를 담은 배열로 온다.

    상대가 끊으면 recv() 는 빈 프레임을 계속 돌려준다. 그걸 건너뛰기만 하면
    제너레이터가 CPU 를 태우며 영원히 돌고, stream_bars 의 재접속은 예외가 없어
    한 번도 안 걸린다. 끊김을 끊김으로 올려야 다시 붙는다.
    """
    while True:
        raw = ws.recv()
        if not raw:
            raise ConnectionError("웹소켓이 빈 프레임을 반환했습니다 — 연결 끊김")
        data = json.loads(raw)
        for msg in (data if isinstance(data, list) else [data]):
            yield msg


def _normalize_bar(msg: dict) -> dict:
    out = {"symbol": msg.get("S", ""),
           "t": pd.to_datetime(msg["t"], utc=True, format="ISO8601").tz_localize(None)}
    for src, dst in _BAR_FIELDS.items():
        out[dst] = float(msg.get(src, 0) or 0)
    out["Volume"] = int(out["Volume"])
    return out
