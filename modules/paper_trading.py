"""
③ 실전 자동매매(페이퍼 트레이딩) 연동
=================================================================
generate_system_signals()가 만든 매수/매도 액션을 Alpaca 계좌에
주문으로 전송하고, 체결 결과를 로컬 로그(JSON Lines)에 저장.

⚠️  ALPACA_MODE 환경변수:
    paper (기본값) → https://paper-api.alpaca.markets  (페이퍼 트레이딩)
    live           → https://api.alpaca.markets         (실거래 ⚠️ 진짜 돈!)

모든 주문 함수는 기본값 dry_run=True — 명시적으로 False를 넘겨야 실제
계좌에 주문이 나감 (설계상 안전장치).
"""
import json
import os
import time
from datetime import datetime, timezone

import requests

_PAPER_URL = "https://paper-api.alpaca.markets"
_LIVE_URL  = "https://api.alpaca.markets"


def _base_url() -> str:
    """ALPACA_MODE 환경변수로 엔드포인트 결정. 기본=paper."""
    mode = os.environ.get("ALPACA_MODE", "paper").strip().lower()
    if mode == "live":
        return _LIVE_URL
    return _PAPER_URL


def _headers(key: str, secret: str) -> dict:
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_account(key: str, secret: str) -> dict:
    r = requests.get(f"{_base_url()}/v2/account", headers=_headers(key, secret), timeout=10)
    r.raise_for_status()
    return r.json()


def get_positions(key: str, secret: str) -> list:
    r = requests.get(f"{_base_url()}/v2/positions", headers=_headers(key, secret), timeout=10)
    r.raise_for_status()
    return r.json()


def submit_paper_order(symbol: str, qty: float, side: str, key: str, secret: str,
                        order_type: str = "market", time_in_force: str = "day",
                        dry_run: bool = True) -> dict:
    """
    side: 'buy' | 'sell'
    dry_run=True(기본값)면 주문을 넣지 않고 "이렇게 나갈 예정"만 반환.
    """
    payload = {
        "symbol": symbol,
        "qty": str(round(qty, 4)),
        "side": side,
        "type": order_type,
        "time_in_force": time_in_force,
    }
    if dry_run:
        return {"dry_run": True, "would_submit": payload}

    r = requests.post(f"{_base_url()}/v2/orders", headers=_headers(key, secret),
                       json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def get_order_fill(order_id: str, key: str, secret: str) -> dict:
    r = requests.get(f"{_base_url()}/v2/orders/{order_id}",
                     headers=_headers(key, secret), timeout=10)
    r.raise_for_status()
    return r.json()


def wait_for_fill(order_id: str, key: str, secret: str,
                   max_wait: int = 60, interval: int = 5) -> dict:
    """
    주문 체결 대기 (polling).
    filled / cancelled / rejected / expired 중 하나가 될 때까지 기다림.
    max_wait 초 내에 체결 완료 안 되면 {"status": "timeout"} 반환.
    """
    TERMINAL = {"filled", "cancelled", "rejected", "expired"}
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            order = get_order_fill(order_id, key, secret)
            if order.get("status") in TERMINAL:
                return order
        except Exception:
            pass
        time.sleep(interval)
    return {"status": "timeout", "order_id": order_id}


def sync_signals_to_orders(actions: list, key: str, secret: str,
                            capital_per_trade: dict = None,
                            dry_run: bool = True,
                            log_path: str = "paper_trade_log.jsonl") -> list:
    """
    generate_system_signals() actions 리스트를 받아 실제 주문으로 변환.
    capital_per_trade: {'AAPL': 10, ...} — 종목별 수량(주) 기준.
    반환: 주문 결과 리스트. log_path에 JSON Lines 형식으로 append 저장.
    """
    results = []
    for act in actions:
        tk = act['ticker']
        action = act['action']
        if '조건부' in action:
            results.append({'ticker': tk, 'skipped': True, 'reason': '조건부 신호 — 즉시 체결 불가'})
            continue
        if '매수' not in action and '매도' not in action:
            continue

        side = 'buy' if '매수' in action else 'sell'
        alloc = (capital_per_trade or {}).get(tk)
        if not alloc:
            results.append({'ticker': tk, 'skipped': True, 'reason': 'capital_per_trade 미지정'})
            continue

        try:
            qty = alloc or 0
            order = submit_paper_order(tk, qty, side, key, secret, dry_run=dry_run)
            record = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'ticker': tk, 'side': side, 'qty': qty,
                'reason': act.get('reason', ''), 'dry_run': dry_run,
                'order_response': order,
            }
        except requests.exceptions.RequestException as e:
            record = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'ticker': tk, 'side': side, 'error': str(e), 'dry_run': dry_run,
            }
        results.append(record)
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        except OSError:
            pass

    return results


def place_notional_buy(symbol: str, notional_usd: float, key: str, secret: str,
                        dry_run: bool = True) -> dict:
    """notional 시장가 매수 (분수 주식 지원).
    submit_paper_order의 qty 방식과 달리 달러 금액 기준으로 주문.
    """
    payload = {
        "symbol": symbol,
        "notional": str(round(notional_usd, 2)),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    if dry_run:
        return {"dry_run": True, "would_submit": payload}
    r = requests.post(f"{_base_url()}/v2/orders", headers=_headers(key, secret),
                       json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def place_market_sell(symbol: str, qty: str, key: str, secret: str,
                       dry_run: bool = True) -> dict:
    """시장가 매도."""
    payload = {
        "symbol": symbol,
        "qty": qty,
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
    }
    if dry_run:
        return {"dry_run": True, "would_submit": payload}
    r = requests.post(f"{_base_url()}/v2/orders", headers=_headers(key, secret),
                       json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def compare_assumed_vs_actual_slippage(signal_price: float, filled_avg_price: float,
                                        side: str,
                                        assumed_slippage_pct: float = 0.03) -> dict:
    """
    백테스트 가정 슬리피지 vs 실제 Alpaca 체결 슬리피지 비교.
    양수 = 비용 발생 방향.
    """
    if not signal_price:
        return {'signal_price': signal_price, 'actual_slippage_pct': 0.0,
                'assumed_slippage_pct': assumed_slippage_pct, 'difference_pct': 0.0}
    raw_slip_pct = (filled_avg_price / signal_price - 1) * 100
    if side == 'sell':
        raw_slip_pct = -raw_slip_pct
    return {
        'signal_price': signal_price,
        'filled_avg_price': filled_avg_price,
        'actual_slippage_pct': round(raw_slip_pct, 4),
        'assumed_slippage_pct': assumed_slippage_pct,
        'gap_pct': round(raw_slip_pct - assumed_slippage_pct, 4),
        'note': ('실제 슬리피지가 백테스트 가정보다 크면 백테스트 성과가 실전에서 '
                 '재현되지 않을 가능성이 큼 — 가정치를 상향 조정할 것을 권장'),
    }
