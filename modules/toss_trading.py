"""
토스증권 오픈 트레이딩 API 래퍼 (paper_trade_runner_toss.py 전용)
=================================================================
공식 문서: https://openapi.tossinvest.com  (정확한 엔드포인트는 docs 참고)

환경변수:
  TOSS_CLIENT_ID        토스증권 오픈 API Client ID
  TOSS_CLIENT_SECRET    토스증권 오픈 API Client Secret
  TOSS_ACCOUNT_SEQ      계좌 식별자(account_seq)
"""
import os
import threading
import time
from datetime import datetime, timezone

import requests

# ──────────────────────────────────────────────────────────────────────
# 기본 설정 (실제 API 도메인 확인 후 수정)
# ──────────────────────────────────────────────────────────────────────
_BASE = os.environ.get("TOSS_API_BASE", "https://openapi.tossinvest.com")

# 국내/해외 거래소 코드 매핑
_EXCHANGE_CODE: dict[str, str] = {
    "KRX":   "KRX",   # 코스피 · 코스닥
    "KOSPI": "KRX",
    "KOSDAQ": "KRX",
    "US":    "NYSE",  # 미국 — 실제 값은 docs에서 확인 (NYSE / NASDAQ / ARCA 등)
    "NYSE":  "NYSE",
    "NASDAQ":"NASDAQ",
}

# ──────────────────────────────────────────────────────────────────────
# OAuth 토큰 캐싱 (프로세스 내 재사용)
# ──────────────────────────────────────────────────────────────────────
_token_lock  = threading.Lock()
_token_cache: dict[str, str | float] = {}   # {"token": str, "expires_at": float}


def _get_token(client_id: str, client_secret: str) -> str:
    """OAuth2 Bearer 토큰 발급 (만료 60초 전에 갱신)."""
    with _token_lock:
        now = time.time()
        if _token_cache.get("token") and _token_cache.get("expires_at", 0) > now + 60:
            return str(_token_cache["token"])

        resp = requests.post(
            f"{_BASE}/oauth2/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # 토스 API가 반환하는 필드명은 docs에서 확인 후 아래 키를 맞춰 수정
        access_token = data.get("access_token") or data.get("accessToken", "")
        expires_in   = int(data.get("expires_in", 86400))

        _token_cache["token"]      = access_token
        _token_cache["expires_at"] = now + expires_in
        return access_token


def _headers(client_id: str, client_secret: str, account_seq: str) -> dict:
    """모든 API 요청에 공통으로 붙이는 헤더를 반환."""
    token = _get_token(client_id, client_secret)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json; charset=utf-8",
        "account-seq":   account_seq,    # 실제 헤더 이름은 docs 확인 필요
    }


# ──────────────────────────────────────────────────────────────────────
# 계좌 / 포지션 조회
# ──────────────────────────────────────────────────────────────────────

def get_account(client_id: str, client_secret: str, account_seq: str) -> dict:
    """
    계좌 요약(자산, 매수여력, 잠금 여부) 반환.
    예시 반환 구조:
      {
        "equity":           123456789.0,
        "buying_power":     9876543.0,
        "account_blocked":  False,
        "trading_blocked":  False,
      }
    """
    hdrs = _headers(client_id, client_secret, account_seq)
    resp = requests.get(
        f"{_BASE}/v1/accounts/{account_seq}",   # ← 실제 경로 docs 확인
        headers=hdrs,
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()

    # 토스 API 응답 필드명을 표준화 (docs 확인 후 키 이름 수정)
    return {
        "equity":          float(raw.get("totalEquity") or raw.get("total_equity", 0)),
        "buying_power":    float(raw.get("availableBuyingPower") or raw.get("available_buying_power", 0)),
        "account_blocked": bool(raw.get("accountBlocked") or raw.get("account_blocked", False)),
        "trading_blocked": bool(raw.get("tradingBlocked") or raw.get("trading_blocked", False)),
        "_raw":            raw,
    }


def get_positions(client_id: str, client_secret: str, account_seq: str) -> list:
    """
    현재 보유 포지션 목록 반환.
    각 항목 구조:
      {
        "symbol":           "005930",        # 종목코드 (접미사 제외)
        "qty":              "100",
        "avg_entry_price":  "70000",
        "current_price":    "72000",
        "unrealized_pl":    "200000",
      }
    """
    hdrs = _headers(client_id, client_secret, account_seq)
    resp = requests.get(
        f"{_BASE}/v1/accounts/{account_seq}/positions",  # ← docs 확인
        headers=hdrs,
        timeout=15,
    )
    resp.raise_for_status()
    raw  = resp.json()

    positions_raw = raw.get("positions") or raw.get("items") or (raw if isinstance(raw, list) else [])
    result = []
    for p in positions_raw:
        result.append({
            "symbol":          str(p.get("symbol") or p.get("ticker") or p.get("stockCode", "")),
            "qty":             str(p.get("quantity") or p.get("qty", "0")),
            "avg_entry_price": str(p.get("avgEntryPrice") or p.get("avg_entry_price", "0")),
            "current_price":   str(p.get("currentPrice") or p.get("current_price", "0")),
            "unrealized_pl":   str(p.get("unrealizedPl") or p.get("unrealized_pl", "0")),
            "_raw":            p,
        })
    return result


# ──────────────────────────────────────────────────────────────────────
# 주문 실행
# ──────────────────────────────────────────────────────────────────────

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
    - 국내(market='KRX'): 금액을 현재가로 나눠 주수 계산 후 시장가 주문
    - 해외(market='US'):  notional 그대로 금액 매수 (broker가 분수주 지원 시)

    반환: {"id": "order_id", "status": "placed", ...}
    """
    exchange = _EXCHANGE_CODE.get(market, market)

    if dry_run:
        print(f"  [DRY_RUN] 매수: {symbol}  {notional:,.0f}원  시장={exchange}")
        return {"id": "dry_run", "symbol": symbol, "notional": notional,
                "market": exchange, "status": "dry_run"}

    hdrs = _headers(client_id, client_secret, account_seq)
    body: dict = {
        "accountSeq":  account_seq,
        "symbol":      symbol,
        "orderType":   "MARKET",          # 시장가  (docs 확인: "MKT" / "MARKET")
        "side":        "BUY",
        "notionalKrw": notional,          # 금액 기반 (필드명 docs에서 확인)
        "market":      exchange,
    }

    # 해외 주문은 USD 필드로 분리될 수 있음 — docs 확인 필요
    if market not in ("KRX", "KOSPI", "KOSDAQ"):
        body.pop("notionalKrw", None)
        body["notionalUsd"] = notional

    resp = requests.post(
        f"{_BASE}/v1/orders",             # ← 실제 경로 docs 확인
        headers=hdrs,
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "id":     str(data.get("orderId") or data.get("order_id", "")),
        "status": str(data.get("status", "placed")),
        "_raw":   data,
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
    시장가 전량(또는 qty 지정) 매도.
    qty: int 또는 str(숫자).
    """
    exchange = _EXCHANGE_CODE.get(market, market)
    qty_str  = str(int(float(str(qty))))

    if dry_run:
        print(f"  [DRY_RUN] 매도: {symbol} {qty_str}주  시장={exchange}")
        return {"id": "dry_run", "symbol": symbol, "qty": qty_str,
                "market": exchange, "status": "dry_run"}

    hdrs = _headers(client_id, client_secret, account_seq)
    body = {
        "accountSeq": account_seq,
        "symbol":     symbol,
        "orderType":  "MARKET",
        "side":       "SELL",
        "quantity":   int(qty_str),
        "market":     exchange,
    }

    resp = requests.post(
        f"{_BASE}/v1/orders",
        headers=hdrs,
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "id":     str(data.get("orderId") or data.get("order_id", "")),
        "status": str(data.get("status", "placed")),
        "_raw":   data,
    }


# ──────────────────────────────────────────────────────────────────────
# 체결 확인
# ──────────────────────────────────────────────────────────────────────
_FILL_TIMEOUT_SEC  = 30
_FILL_POLL_SEC     = 3
_TERMINAL_STATUSES = {"filled", "cancelled", "rejected", "expired"}


def wait_for_fill(
    order_id:      str,
    client_id:     str,
    client_secret: str,
    account_seq:   str,
) -> dict:
    """
    주문이 terminal 상태(체결·취소·거부)가 될 때까지 폴링.
    _FILL_TIMEOUT_SEC 초 초과 시 {"status": "timeout"} 반환.
    DRY_RUN에서 넘어온 "dry_run" order_id는 즉시 반환.
    """
    if order_id == "dry_run":
        return {"status": "dry_run", "filled_avg_price": None}

    hdrs     = _headers(client_id, client_secret, account_seq)
    deadline = time.time() + _FILL_TIMEOUT_SEC

    while time.time() < deadline:
        resp = requests.get(
            f"{_BASE}/v1/orders/{order_id}",  # ← docs 확인
            headers=hdrs,
            timeout=10,
        )
        resp.raise_for_status()
        data   = resp.json()
        status = str(data.get("status", "")).lower()

        if status in _TERMINAL_STATUSES:
            return {
                "status":           status,
                "filled_avg_price": data.get("filledAvgPrice") or data.get("filled_avg_price"),
                "filled_qty":       data.get("filledQty") or data.get("filled_qty"),
                "_raw":             data,
            }
        time.sleep(_FILL_POLL_SEC)

    return {"status": "timeout", "order_id": order_id}
