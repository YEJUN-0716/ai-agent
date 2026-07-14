"""
토스증권 오픈 트레이딩 API 래퍼
=================================================================
OpenAPI 스펙 v1.2.2 기반 (https://openapi.tossinvest.com)
OAuth2 Client Credentials Grant / Bearer 토큰 방식

계좌 관련 모든 API는 X-Tossinvest-Account 헤더 필수.
토큰은 client당 1개만 유효 (재발급 시 이전 토큰 즉시 무효화).
"""
import math
import os
import threading
import time

import requests

# ── 기본 설정 ──────────────────────────────────────────────────────────
_BASE = "https://openapi.tossinvest.com"

# ── OAuth2 토큰 캐싱 ───────────────────────────────────────────────────
_token_lock  = threading.Lock()
# client_id를 키로 하여 여러 credentials 동시 사용 시 토큰 오염 방지
_token_cache: dict[str, dict] = {}   # {client_id: {"token": str, "expires_at": float}}


def _get_token(client_id: str, client_secret: str) -> str:
    """OAuth2 Client Credentials Grant 토큰 발급 (만료 60초 전 갱신)."""
    with _token_lock:
        now   = time.time()
        entry = _token_cache.get(client_id, {})
        if entry.get("token") and entry.get("expires_at", 0) > now + 60:
            return str(entry["token"])

        resp = requests.post(
            f"{_BASE}/oauth2/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        _token_cache[client_id] = {
            "token":      data["access_token"],
            "expires_at": now + int(data.get("expires_in", 86400)),
        }
        return str(_token_cache[client_id]["token"])


def _headers(client_id: str, client_secret: str, account_seq: str) -> dict:
    """모든 계좌 API 요청에 공통으로 붙이는 헤더."""
    return {
        "Authorization":        f"Bearer {_get_token(client_id, client_secret)}",
        "Content-Type":         "application/json; charset=utf-8",
        "X-Tossinvest-Account": account_seq,
    }


def _auth_only_headers(client_id: str, client_secret: str) -> dict:
    """계좌 헤더 불필요한 API (시세 등) 용 헤더."""
    return {
        "Authorization": f"Bearer {_get_token(client_id, client_secret)}",
    }


# ── 현재가 조회 (주문 수량 계산용) ─────────────────────────────────────
def _get_price(symbol: str, client_id: str, client_secret: str) -> float:
    """GET /api/v1/prices 로 현재가(lastPrice) 조회. 실패 시 0.0 반환."""
    try:
        resp = requests.get(
            f"{_BASE}/api/v1/prices",
            headers=_auth_only_headers(client_id, client_secret),
            params={"symbols": symbol},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("result", [])
        if items:
            return float(items[0].get("lastPrice", 0))
    except Exception as e:
        print(f"  [toss] 현재가 조회 실패 {symbol}: {e}")
    return 0.0


# ── 계좌 / 자산 조회 ───────────────────────────────────────────────────

def get_account(client_id: str, client_secret: str, account_seq: str) -> dict:
    """
    계좌 요약(총 자산평가액, 매수가능금액) 반환.

    반환 구조:
      {
        "equity":          float,  # KRW 자산평가액 (GET /api/v1/holdings → result.marketValue.amount.krw)
        "buying_power":    float,  # 매수가능금액 (GET /api/v1/buying-power?currency=KRW → result.cashBuyingPower)
        "account_blocked": bool,
        "trading_blocked": bool,
      }
    """
    hdrs = _headers(client_id, client_secret, account_seq)

    # 1) 보유 주식 평가금액
    stock_value = 0.0
    try:
        resp = requests.get(f"{_BASE}/api/v1/holdings", headers=hdrs, timeout=15)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        krw_val = result.get("marketValue", {}).get("amount", {}).get("krw", "0")
        stock_value = float(krw_val or 0)
    except Exception as e:
        print(f"  [toss] 자산 조회 실패: {e}")

    # 2) 매수 가능 금액 (현금) — GET /api/v1/buying-power?currency=KRW
    buying_power = 0.0
    try:
        resp = requests.get(
            f"{_BASE}/api/v1/buying-power",
            headers=hdrs,
            params={"currency": "KRW"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        buying_power = float(result.get("cashBuyingPower", 0))
    except Exception as e:
        print(f"  [toss] 매수가능금액 조회 실패: {e}")

    # equity = 주식 평가금액 + 현금 (현금 제외 시 포지션 없으면 0이 되어 드로다운 -100% 오진단)
    equity = stock_value + buying_power

    return {
        "equity":          equity,
        "buying_power":    buying_power,
        "account_blocked": False,
        "trading_blocked": False,
    }


def get_positions(client_id: str, client_secret: str, account_seq: str) -> list:
    """
    현재 보유 종목 목록 반환.

    GET /api/v1/holdings
    응답: result.items[] 각 항목:
      symbol, quantity, averagePurchasePrice, lastPrice, profitLoss.amount

    각 반환 항목:
      {
        "symbol":          str,
        "qty":             str,   # 보유 수량
        "avg_entry_price": str,   # 평균 매입가
        "current_price":   str,   # 현재가
        "unrealized_pl":   str,   # 평가손익
      }
    """
    hdrs = _headers(client_id, client_secret, account_seq)
    resp = requests.get(f"{_BASE}/api/v1/holdings", headers=hdrs, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("result", {}).get("items", [])

    positions = []
    for h in items:
        positions.append({
            "symbol":          str(h.get("symbol", "")),
            "qty":             str(h.get("quantity", "0")),
            "avg_entry_price": str(h.get("averagePurchasePrice", "0")),
            "current_price":   str(h.get("lastPrice", "0")),
            "unrealized_pl":   str(h.get("profitLoss", {}).get("amount", "0")),
            "_raw":            h,
        })
    return positions


# ── 주문 ───────────────────────────────────────────────────────────────

def place_notional_buy(
    symbol:        str,
    notional:      float,
    client_id:     str,
    client_secret: str,
    account_seq:   str,
    market:        str  = "KRX",
    dry_run:       bool = False,
) -> dict:
    """
    금액(notional) 기반 시장가 매수.
    토스 API는 수량 지정 방식이므로 현재가로 수량을 역산 후 주문.

    POST /api/v1/orders
    Body: { symbol, side:"BUY", orderType:"MARKET", quantity:"10" }
    quantity는 API 스펙상 문자열(decimal string)로 전송.
    응답: result.orderId
    """
    price = _get_price(symbol, client_id, client_secret)
    if price <= 0:
        raise ValueError(f"현재가 조회 실패: {symbol}")

    qty = int(math.floor(notional / price))
    if qty < 1:
        raise ValueError(
            f"매수 수량 부족: {symbol} 현재가 {price:,.0f}, 투자금 {notional:,.0f} → {qty}주"
        )

    if dry_run:
        print(f"  [DRY_RUN] 매수: {symbol} {qty}주 @ {price:,.0f}  "
              f"(금액 {notional:,.0f}, 시장={market})")
        return {"id": "dry_run", "symbol": symbol, "qty": qty,
                "price": price, "notional": notional, "status": "dry_run"}

    hdrs = _headers(client_id, client_secret, account_seq)
    body = {
        "symbol":    symbol,
        "side":      "BUY",
        "orderType": "MARKET",
        "quantity":  str(qty),   # 스펙: decimal string
    }

    resp = requests.post(f"{_BASE}/api/v1/orders", headers=hdrs, json=body, timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    return {
        "id":     str(result.get("orderId", "")),
        "status": "placed",
        "qty":    qty,
        "price":  price,
        "_raw":   result,
    }


def place_market_sell(
    symbol:        str,
    qty,
    client_id:     str,
    client_secret: str,
    account_seq:   str,
    market:        str  = "KRX",
    dry_run:       bool = False,
) -> dict:
    """
    시장가 매도.

    POST /api/v1/orders
    Body: { symbol, side:"SELL", orderType:"MARKET", quantity:"10" }
    quantity는 API 스펙상 문자열(decimal string)로 전송.
    응답: result.orderId
    """
    qty_int = int(float(str(qty)))
    if qty_int < 1:
        raise ValueError(f"매도 수량 0: {symbol}")

    if dry_run:
        print(f"  [DRY_RUN] 매도: {symbol} {qty_int}주  (시장={market})")
        return {"id": "dry_run", "symbol": symbol, "qty": qty_int,
                "market": market, "status": "dry_run"}

    hdrs = _headers(client_id, client_secret, account_seq)
    body = {
        "symbol":    symbol,
        "side":      "SELL",
        "orderType": "MARKET",
        "quantity":  str(qty_int),   # 스펙: decimal string
    }

    resp = requests.post(f"{_BASE}/api/v1/orders", headers=hdrs, json=body, timeout=15)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    return {
        "id":     str(result.get("orderId", "")),
        "status": "placed",
        "qty":    qty_int,
        "_raw":   result,
    }


# ── 체결 확인 ──────────────────────────────────────────────────────────
_FILL_TIMEOUT_SEC  = 60
_FILL_POLL_SEC     = 5
# OrderStatus enum (lowercase 비교): FILLED, CANCELED, REJECTED, REPLACED, CANCEL_REJECTED, REPLACE_REJECTED
_TERMINAL_STATUSES = {
    "filled", "canceled", "rejected", "replaced",
    "cancel_rejected", "replace_rejected",
}


def wait_for_fill(
    order_id:      str,
    client_id:     str,
    client_secret: str,
    account_seq:   str,
) -> dict:
    """
    주문 체결 확인 (폴링, 최대 _FILL_TIMEOUT_SEC 초).

    GET /api/v1/orders/{orderId}
    응답: result.status, result.execution.averageFilledPrice, result.execution.filledQuantity
    """
    if order_id == "dry_run":
        return {"status": "dry_run", "filled_avg_price": None}

    hdrs     = _headers(client_id, client_secret, account_seq)
    deadline = time.time() + _FILL_TIMEOUT_SEC

    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{_BASE}/api/v1/orders/{order_id}",
                headers=hdrs,
                timeout=10,
            )
            resp.raise_for_status()
            data      = resp.json().get("result", {})
            status    = str(data.get("status", "")).lower()

            if status in _TERMINAL_STATUSES:
                execution = data.get("execution", {})
                return {
                    "status":           status,
                    "filled_avg_price": execution.get("averageFilledPrice"),
                    "filled_qty":       execution.get("filledQuantity"),
                    "_raw":             data,
                }
        except Exception as e:
            print(f"  [toss] 체결 확인 오류 (계속 시도): {e}")

        time.sleep(_FILL_POLL_SEC)

    return {"status": "timeout", "order_id": order_id}
