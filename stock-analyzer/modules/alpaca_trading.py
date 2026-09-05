"""
Alpaca 트레이딩 API 래퍼
=================================================================
REST v2 (https://docs.alpaca.markets). 키 인증(헤더 2줄) — 토스처럼 OAuth
토큰을 돌리지 않는다.

`modules/toss_trading.py` · `modules/virtual_broker.py` 와 **같은 시그니처**를
낸다. 러너 입장에서는 import 한 줄만 갈아끼우면 되고 전략 코드는 안 갈라진다.

기본은 **페이퍼 계좌**다(`ALPACA_PAPER=false` 로 실계좌 전환). 실계좌를 기본으로
두면 키를 잘못 넣은 날 진짜 주문이 나간다.

⚠️ 금액 단위는 **항상 USD** 다
-----------------------------
가상 브로커는 받은 달러를 원으로 환산해 장부에 적었다. Alpaca 는 미국 주식 전용이라 환산이 없다 —
`notional` 도 `equity` 도 `buying_power` 도 전부 달러다.

이 경계에서 이미 두 번 사고가 났다(2026-07-30: 623달러가 623원으로 기록돼 매수
0건 / 같은 날 반대로 50만원이 7억원). 원화를 들고 있는 호출자는 **넘기기 전에
직접 나눠야 한다.** 러너는 아직 자산을 원화로 다루므로, 러너를 이 모듈로
갈아끼울 때 환산 지점을 반드시 같이 손볼 것.
"""
import os
import time
import uuid

import requests

# ── 기본 설정 ──────────────────────────────────────────────────────────
_PAPER_BASE = "https://paper-api.alpaca.markets"
_LIVE_BASE  = "https://api.alpaca.markets"


def base_url() -> str:
    """페이퍼가 기본. 실계좌는 ALPACA_PAPER=false 를 명시해야 열린다."""
    paper = os.environ.get("ALPACA_PAPER", "true").strip().lower() != "false"
    return _PAPER_BASE if paper else _LIVE_BASE


def is_paper() -> bool:
    return base_url() == _PAPER_BASE


# 재시도 대상 HTTP 상태 (일시적 오류만) — 429 rate limit, 5xx 서버 오류
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES  = 3
_BACKOFF_BASE = 1.5   # 초; 지수 백오프 (1.5, 3.0, 6.0 ...)


# ponytail: toss_trading._request_with_retry 와 같은 모양이다. 토스 모듈이
# 폐기되면 사본이 하나만 남으므로 지금 공용 헬퍼로 빼지 않는다.
def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """네트워크·일시적 오류에 지수 백오프로 재시도하는 requests 래퍼.

    4xx(429 제외)는 즉시 반환한다 — 서버가 응답을 준 거절은 다시 보내봐야
    같은 답이고, 주문 POST 를 재시도하면 중복 체결 위험이 생긴다.
    """
    last_exc = None
    resp = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                print(f"  [alpaca] {resp.status_code} → {wait:.1f}s 후 재시도 "
                      f"({attempt + 1}/{_MAX_RETRIES})")
                time.sleep(wait)
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                print(f"  [alpaca] 네트워크 오류({type(e).__name__}) → {wait:.1f}s 후 "
                      f"재시도 ({attempt + 1}/{_MAX_RETRIES})")
                time.sleep(wait)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return resp


def _headers(api_key: str = "", secret_key: str = "") -> dict:
    """인증 헤더. 빈 인자는 환경변수로 채운다.

    토스 래퍼가 (client_id, client_secret, account_seq) 를 받으므로 러너가 그
    자리에 그대로 넘긴다. 앞의 둘이 Alpaca 키 쌍이고 account_seq 는 쓰이지
    않는다 — Alpaca 는 키 하나가 계좌 하나다.
    """
    key    = api_key    or os.environ.get("ALPACA_API_KEY", "")
    secret = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise ValueError(
            "Alpaca 키가 없습니다 — ALPACA_API_KEY / ALPACA_SECRET_KEY 를 "
            "환경변수나 인자로 넘기세요.")
    return {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type":        "application/json",
    }


# ── 계좌 · 포지션 ──────────────────────────────────────────────────────
def get_account(client_id: str = "", client_secret: str = "",
                account_seq: str = "") -> dict:
    """계좌 요약. **단위는 USD.**

    GET /v2/account → equity, buying_power, account_blocked, trading_blocked

    토스 래퍼는 조회 실패를 0 으로 삼켰다(자산·매수여력을 따로 부르므로 한쪽만
    죽어도 나머지는 쓸 수 있었다). Alpaca 는 한 번의 호출이라 삼킬 게 없다 —
    실패하면 올린다. 자산 0 원으로 조용히 진행하면 드로다운 −100% 로 오진해
    킬스위치가 헛발질한다.
    """
    resp = _request_with_retry("GET", f"{base_url()}/v2/account",
                               headers=_headers(client_id, client_secret), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {
        "equity":          float(data.get("equity", 0) or 0),
        "buying_power":    float(data.get("buying_power", 0) or 0),
        "account_blocked": bool(data.get("account_blocked", False)),
        "trading_blocked": bool(data.get("trading_blocked", False)),
        "_raw":            data,
    }


def get_positions(client_id: str = "", client_secret: str = "",
                  account_seq: str = "") -> list:
    """보유 종목. 값은 문자열로 낸다 — 토스·가상 브로커와 같은 모양이라야
    러너의 포지션 처리 코드가 갈라지지 않는다."""
    resp = _request_with_retry("GET", f"{base_url()}/v2/positions",
                               headers=_headers(client_id, client_secret), timeout=15)
    resp.raise_for_status()
    return [{
        "symbol":          str(p.get("symbol", "")),
        "qty":             str(p.get("qty", "0")),
        "avg_entry_price": str(p.get("avg_entry_price", "0")),
        "current_price":   str(p.get("current_price", "0")),
        "unrealized_pl":   str(p.get("unrealized_pl", "0")),
        "_raw":            p,
    } for p in resp.json()]


def _find_by_client_order_id(client_order_id: str, headers: dict) -> dict | None:
    """그 id 로 이미 들어간 주문. 없거나 조회에 실패하면 None."""
    try:
        r = requests.get(f"{base_url()}/v2/orders:by_client_order_id",
                         headers=headers, params={"client_order_id": client_order_id},
                         timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# ── 주문 ───────────────────────────────────────────────────────────────
def _submit(body: dict, api_key: str, secret_key: str) -> dict:
    """주문 하나를 보낸다. **재시도해도 두 번 체결되지 않는다.**

    _request_with_retry 는 429·5xx·네트워크 오류에 같은 POST 를 다시 보낸다.
    응답만 유실되고 서버는 주문을 받은 경우, 재시도가 진짜 주문을 하나 더
    만든다 — 돈이 두 배로 나간다. 그래서 호출마다 client_order_id 를 한 번
    만들어 붙인다. Alpaca 는 같은 id 를 두 번 받으면 거절하므로, 재시도는
    중복 체결 대신 거절을 받는다.

    거절받은 뒤에는 그 id 로 조회해 **이미 들어간 주문**을 돌려준다. 안 그러면
    실제로 체결된 주문을 러너가 실패로 기록해 장부가 실제와 갈라진다.
    """
    body = {**body, "client_order_id": body.get("client_order_id") or uuid.uuid4().hex}
    hdrs = _headers(api_key, secret_key)
    resp = _request_with_retry("POST", f"{base_url()}/v2/orders",
                               headers=hdrs, json=body, timeout=15)
    if resp.status_code >= 400:
        existing = _find_by_client_order_id(body["client_order_id"], hdrs)
        if existing is not None:
            print(f"  [alpaca] 재시도가 중복으로 거절됨 — 먼저 들어간 주문을 씁니다 "
                  f"({existing.get('id')})")
            data = existing
            return {"id": str(data.get("id", "")), "status": str(data.get("status", "")),
                    "_raw": data}
        # 거절 사유가 본문에 있다. raise_for_status 만 부르면 그게 사라져서
        # "왜 안 샀나"를 로그만 보고는 알 수 없다.
        raise requests.HTTPError(
            f"Alpaca 주문 거절 HTTP {resp.status_code}: {resp.text}", response=resp)
    data = resp.json()
    return {
        "id":     str(data.get("id", "")),
        "status": str(data.get("status", "")),
        "_raw":   data,
    }


def place_notional_buy(symbol: str, notional: float,
                       client_id: str = "", client_secret: str = "",
                       account_seq: str = "",
                       dry_run: bool = False,
                       meta: dict | None = None) -> dict:
    """금액(**달러**) 기반 시장가 매수.

    토스는 수량 지정만 돼서 현재가로 수량을 역산하고 1주 미만이면 못 샀다.
    Alpaca 는 notional 주문과 소수점 매매를 지원하므로 금액을 그대로 보낸다 —
    "주가가 투입금보다 비싸서 스킵"이 사라진다.

    notional 주문은 시장가·당일(day)만 된다. 그래서 time_in_force 를 고정한다.

    meta(주문 근거)는 받기만 하고 버린다. 실체결은 즉시 일어나 러너가 그 자리에서
    성적표에 적으므로 주문에 실어 나를 필요가 없다. 다음 거래일에 체결되는 가상
    브로커만 그게 필요하다 — 러너가 브로커별로 갈라지지 않게 인자만 맞춰 둔다.
    """
    if symbol.endswith((".KS", ".KQ")):
        raise ValueError(f"Alpaca 는 미국 주식 전용입니다 — {symbol} 주문은 받지 않습니다.")
    if notional <= 0:
        raise ValueError(f"{symbol} 주문 금액이 0 이하입니다: {notional}")

    if dry_run:
        print(f"  [DRY_RUN] 매수: {symbol} ${notional:,.2f} (시장가)")
        return {"id": "dry_run", "symbol": symbol, "notional": notional,
                "status": "dry_run"}

    return _submit({
        "symbol":        symbol,
        "notional":      str(round(float(notional), 2)),
        "side":          "buy",
        "type":          "market",
        "time_in_force": "day",
    }, client_id, client_secret)


def place_limit_buy(symbol: str, qty: int, limit_price: float,
                    client_id: str = "", client_secret: str = "",
                    account_seq: str = "",
                    dry_run: bool = False,
                    extended_hours: bool = False, tif: str = "gtc") -> dict:
    """진입 구간 **지정가** 매수 (기본 GTC — 취소할 때까지 살아 있다).

    단타 러너는 `tif="day"` 로 건다. GTC 로 두면 러너가 하드킬당했을 때 주문이
    밤을 넘겨, 다음 날 아침에 어제 계획으로 체결된다 — 손절도 안 걸린 채로.

    토스에 없어서 트레이드 플랜을 실계좌로 못 돌리던 주문이다. 다만 이건
    **진입만** 건다 — 손절·목표 라인은 아직 안 붙는다. 라인까지 브로커에
    맡기려면 bracket 주문이 필요한데, 그건 백테스트가 잰 적 없는 체결 규칙이라
    따로 정하고 재야 한다. 그때까지 청산은 러너가 판정한다.

    지정가는 소수점 매매가 안 되므로 수량은 정수다.
    """
    qty = int(qty)
    if qty < 1:
        raise ValueError(f"{symbol} 지정가 주문 수량이 1주 미만입니다 (소수점 매매 불가).")
    if limit_price <= 0:
        raise ValueError(f"{symbol} 지정가가 0 이하입니다: {limit_price}")

    if dry_run:
        print(f"  [DRY_RUN] 지정가 매수: {symbol} {qty}주 @ ${limit_price:,.2f}")
        return {"id": "dry_run", "symbol": symbol, "qty": qty,
                "limit_price": limit_price, "status": "dry_run"}

    return _submit({
        "symbol":          symbol,
        "qty":             str(qty),
        "side":            "buy",
        "type":            "limit",
        "limit_price":     str(round(float(limit_price), 2)),
        "time_in_force":   tif,
        "extended_hours":  bool(extended_hours),
    }, client_id, client_secret)


def place_market_sell(symbol: str, qty,
                      client_id: str = "", client_secret: str = "",
                      account_seq: str = "",
                      dry_run: bool = False) -> dict:
    """시장가 매도. qty 는 소수점도 받는다 (notional 매수분 전량 청산용)."""
    qty_f = float(str(qty))
    if qty_f <= 0:
        raise ValueError(f"매도 수량 0: {symbol}")

    if dry_run:
        print(f"  [DRY_RUN] 매도: {symbol} {qty_f:g}주")
        return {"id": "dry_run", "symbol": symbol, "qty": qty_f, "status": "dry_run"}

    return _submit({
        "symbol":        symbol,
        "qty":           str(qty_f),
        "side":          "sell",
        "type":          "market",
        "time_in_force": "day",
    }, client_id, client_secret)


def place_stop_sell(symbol: str, qty: int, stop_price: float,
                    client_id: str = "", client_secret: str = "",
                    account_seq: str = "",
                    dry_run: bool = False, tif: str = "day",
                    client_order_id: str = "") -> dict:
    """손절 **스톱** 매도. 가격이 stop_price 를 건드리면 시장가로 전환된다.

    백테스트는 손절가에 정확히 체결된다고 가정한다. 실제로는 트리거된 뒤
    시장가라, 체결가는 손절가보다 **낮게** 나온다(= 슬리피지). 그 차이를
    재려고 만든 주문이다. 손익분기가 왕복 5bp 라 모르고 켤 수 없다.

    당일(day)로 건다 — 단타는 15:45 에 전량 청산하므로 스톱이 밤을 넘길 일이
    없고, GTC 로 두면 다음 날 갭에 엉뚱하게 터진다.

    `tif` 기본값이 그 "day" 다 — **기존 호출자의 body 는 한 글자도 안 바뀐다.**
    빗각 8단계는 40봉을 들고 가므로 `tif="gtc"` 로 건다(스윙은 밤을 넘겨야 한다).
    """
    qty = int(qty)
    if qty < 1:
        raise ValueError(f"{symbol} 스톱 매도 수량이 1주 미만입니다 (소수점 매매 불가).")
    if stop_price <= 0:
        raise ValueError(f"{symbol} 손절가가 0 이하입니다: {stop_price}")

    if dry_run:
        print(f"  [DRY_RUN] 스톱 매도: {symbol} {qty}주 @ ${stop_price:,.2f}")
        return {"id": "dry_run", "symbol": symbol, "qty": qty,
                "stop_price": stop_price, "status": "dry_run"}

    body = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          "sell",
        "type":          "stop",
        "stop_price":    str(round(float(stop_price), 2)),
        "time_in_force": tif,
    }
    if client_order_id:
        body["client_order_id"] = client_order_id
    return _submit(body, client_id, client_secret)


def place_limit_sell(symbol: str, qty: int, limit_price: float,
                     client_id: str = "", client_secret: str = "",
                     account_seq: str = "",
                     dry_run: bool = False, tif: str = "gtc",
                     client_order_id: str = "") -> dict:
    """익절 **지정가** 매도. `place_limit_buy` 의 매도판이다.

    `place_limit_buy` 는 매수만 걸었다 — 그 docstring 이 "손절·목표 라인은 아직
    안 붙는다"고 적어 둔 자리가 여기다. 목표선 다리(J-3)가 이 주문이다.

    기본 GTC: 목표선은 며칠~몇 주 뒤에 닿는다. day 로 걸면 매일 만료돼 밤사이
    갭업이 그냥 지나간다.
    """
    qty = int(qty)
    if qty < 1:
        raise ValueError(f"{symbol} 지정가 매도 수량이 1주 미만입니다 (소수점 매매 불가).")
    if limit_price <= 0:
        raise ValueError(f"{symbol} 목표가가 0 이하입니다: {limit_price}")

    if dry_run:
        print(f"  [DRY_RUN] 지정가 매도: {symbol} {qty}주 @ ${limit_price:,.2f}")
        return {"id": "dry_run", "symbol": symbol, "qty": qty,
                "limit_price": limit_price, "status": "dry_run"}

    body = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          "sell",
        "type":          "limit",
        "limit_price":   str(round(float(limit_price), 2)),
        "time_in_force": tif,
    }
    if client_order_id:
        body["client_order_id"] = client_order_id
    return _submit(body, client_id, client_secret)


# ── MOC (market-on-close) ──────────────────────────────────────────────
# `time_in_force: "cls"` 가 마감 경매 주문이다. 이 두 글자가 틀리면 Alpaca 는
# **당일 시장가**로 받아 장중에 체결한다 — 규칙(종가 진입)이 아닌 매매가 된다.
# 조용히 틀리는 종류라 selftest ① 이 body 를 글자 그대로 검사한다.
#
# 컷오프는 15:50 ET. 그 뒤 제출은 거절된다(러너가 시각을 직접 검사하는 이유).
def _moc(symbol: str, qty: int, side: str, api_key: str, secret_key: str,
         dry_run: bool, client_order_id: str) -> dict:
    qty = int(qty)
    if qty < 1:
        raise ValueError(f"{symbol} MOC 수량이 1주 미만입니다 (소수점 매매 불가).")
    if dry_run:
        print(f"  [DRY_RUN] MOC {side}: {symbol} {qty}주")
        return {"id": "dry_run", "symbol": symbol, "qty": qty, "status": "dry_run"}
    body = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          side,
        "type":          "market",
        "time_in_force": "cls",
    }
    if client_order_id:
        body["client_order_id"] = client_order_id
    return _submit(body, api_key, secret_key)


def place_moc_buy(symbol: str, qty: int,
                  client_id: str = "", client_secret: str = "",
                  account_seq: str = "",
                  dry_run: bool = False, client_order_id: str = "") -> dict:
    """진입 **MOC** 매수 (J-1) — 당일 공식 종가로 체결된다."""
    return _moc(symbol, qty, "buy", client_id, client_secret, dry_run, client_order_id)


def place_moc_sell(symbol: str, qty: int,
                   client_id: str = "", client_secret: str = "",
                   account_seq: str = "",
                   dry_run: bool = False, client_order_id: str = "") -> dict:
    """타임아웃 **MOC** 매도 (J-4) — 40봉째 종가 청산."""
    return _moc(symbol, qty, "sell", client_id, client_secret, dry_run, client_order_id)


def replace_order(order_id: str, limit_price: float = None,
                  stop_price: float = None, qty: int = None,
                  client_id: str = "", client_secret: str = "",
                  dry_run: bool = False, client_order_id: str = "") -> dict:
    """대기 중인 주문의 **가격만** 바꾼다. PATCH /v2/orders/{id}.

    채널선이 기울어져 있어 손절·목표 다리 값이 매 거래일 바뀐다. 취소 후
    재제출로 하면 그 사이 몇 초 동안 **다리 없는 포지션**이 생긴다 — 정정이
    그 창을 없앤다.

    ⚠️ Alpaca 는 정정을 "옛 주문 replaced + **새 주문 생성**"으로 처리한다.
    반환되는 id 는 **새 id** 이고 옛 id 는 죽는다. 호출자가 id 를 갱신하지 않으면
    다음 날 정정이 죽은 주문을 때리고, 상호 취소가 살아 있는 다리를 못 찾는다.
    """
    if dry_run:
        print(f"  [DRY_RUN] 주문 정정: {order_id} limit={limit_price} stop={stop_price}")
        return {"id": order_id, "status": "dry_run"}

    body = {}
    if limit_price is not None:
        body["limit_price"] = str(round(float(limit_price), 2))
    if stop_price is not None:
        body["stop_price"] = str(round(float(stop_price), 2))
    if qty is not None:
        body["qty"] = str(int(qty))
    if client_order_id:
        body["client_order_id"] = client_order_id
    if not body:
        raise ValueError("정정할 값이 하나도 없습니다.")

    resp = _request_with_retry("PATCH", f"{base_url()}/v2/orders/{order_id}",
                               headers=_headers(client_id, client_secret),
                               json=body, timeout=15)
    if resp.status_code >= 400:
        raise requests.HTTPError(
            f"Alpaca 주문 정정 거절 HTTP {resp.status_code}: {resp.text}", response=resp)
    data = resp.json()
    return {"id": str(data.get("id", "")), "status": str(data.get("status", "")),
            "_raw": data}


# ── 체결 확인 ──────────────────────────────────────────────────────────
_FILL_TIMEOUT_SEC = 60
_FILL_POLL_SEC    = 5
# Alpaca OrderStatus 중 더 이상 안 변하는 것들.
# 'expired' 는 토스에 없던 상태다 — 러너의 order_accepted() 가 이걸 '살아 있는
# 주문'으로 세면 자리와 현금이 영영 안 풀린다. 반대로 'done_for_day' 는 GTC
# 주문이 오늘만 끝난 것이라 자리는 계속 잡고 있어야 한다.
_TERMINAL_STATUSES = {
    "filled", "canceled", "expired", "rejected", "replaced", "done_for_day",
}


def wait_for_fill(order_id: str, client_id: str = "", client_secret: str = "",
                  account_seq: str = "", timeout: int = _FILL_TIMEOUT_SEC) -> dict:
    """주문 체결 확인 (폴링).

    타임아웃은 '미체결'이 아니라 **'알 수 없음'** 이다. 지정가 주문은 며칠씩
    안 채워지는 게 정상이므로 여기서 기다려 봐야 소용이 없다 — 호출부는
    timeout 을 성공으로 세면 안 되고, 다음 실행의 포지션 대사에서 맞춘다.
    """
    if order_id == "dry_run":
        return {"status": "dry_run", "filled_avg_price": None}

    hdrs     = _headers(client_id, client_secret)
    deadline = time.time() + timeout
    while True:
        try:
            resp = _request_with_retry(
                "GET", f"{base_url()}/v2/orders/{order_id}", headers=hdrs, timeout=10)
            resp.raise_for_status()
            data   = resp.json()
            status = str(data.get("status", "")).lower()
            if status in _TERMINAL_STATUSES:
                return {
                    "status":           status,
                    "filled_avg_price": data.get("filled_avg_price"),
                    "filled_qty":       data.get("filled_qty"),
                    "_raw":             data,
                }
        except Exception as e:
            print(f"  [alpaca] 체결 확인 오류 (계속 시도): {e}")

        if time.time() >= deadline:
            return {"status": "timeout", "order_id": order_id,
                    "filled_avg_price": None, "filled_qty": None}
        time.sleep(_FILL_POLL_SEC)


def cancel_order(order_id: str, client_id: str = "", client_secret: str = "") -> bool:
    """대기 중인 주문 취소. 이미 종결됐으면 False (오류로 올리지 않는다).

    지정가를 GTC 로 걸므로 취소 경로가 없으면 만료 규칙(20거래일)을 브로커에
    적용할 방법이 없다.
    """
    resp = _request_with_retry("DELETE", f"{base_url()}/v2/orders/{order_id}",
                               headers=_headers(client_id, client_secret), timeout=15)
    return resp.status_code in (200, 204)
