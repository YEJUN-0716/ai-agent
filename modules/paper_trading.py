"""
③ 실전 자동매매(페이퍼 트레이딩) 연동
=================================================================
generate_system_signals()가 만든 매수/매도 액션을 Alpaca 페이퍼 계좌에
주문으로 전송하고, 체결 결과를 로컬 로그(JSON Lines)에 저장.

⚠️ 반드시 Paper Trading 엔드포인트만 사용:
    PAPER_BASE_URL = "https://paper-api.alpaca.markets"
    (실거래 api.alpaca.markets 로 절대 바꾸지 말 것)
모든 주문 함수는 기본값 dry_run=True — 명시적으로 False를 넘겨야 실제
페이퍼 계좌에 주문이 나감 (설계상 안전장치).
"""
import json
from datetime import datetime, timezone

import requests

PAPER_BASE_URL = "https://paper-api.alpaca.markets"


def _headers(key: str, secret: str) -> dict:
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def get_account(key: str, secret: str) -> dict:
    r = requests.get(f"{PAPER_BASE_URL}/v2/account", headers=_headers(key, secret), timeout=10)
    r.raise_for_status()
    return r.json()


def get_positions(key: str, secret: str) -> list:
    r = requests.get(f"{PAPER_BASE_URL}/v2/positions", headers=_headers(key, secret), timeout=10)
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

    r = requests.post(f"{PAPER_BASE_URL}/v2/orders", headers=_headers(key, secret),
                       json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def get_order_fill(order_id: str, key: str, secret: str) -> dict:
    r = requests.get(f"{PAPER_BASE_URL}/v2/orders/{order_id}",
                     headers=_headers(key, secret), timeout=10)
    r.raise_for_status()
    return r.json()


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
        if '매수' not in action and '매도' not in action:
            continue

        side = 'buy' if '매수' in action else 'sell'
        alloc = (capital_per_trade or {}).get(tk)
        if side == 'buy' and not alloc:
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


def compare_assumed_vs_actual_slippage(signal_price: float, filled_avg_price: float,
                                        side: str,
                                        assumed_slippage_pct: float = 0.03) -> dict:
    """
    백테스트 가정 슬리피지 vs 실제 Alpaca 체결 슬리피지 비교.
    양수 = 불리(비용 발생), 음수 = 유리 (매수/매도 방향 무관하게 통일).
    """
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
