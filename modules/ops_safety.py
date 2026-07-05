"""
운영 안전성: 킬스위치 · 포지션 대사 · 알림 디스패처
=================================================================
실전 자동매매에서 가장 무서운 건 무한 손실 루프다.
이 모듈은 세 가지 안전망을 제공한다:

1. KillSwitch — 당일 손실 임계치 초과 시 거래 차단
2. reconcile_positions — 의도 포지션 vs 브로커 실제 포지션 불일치 탐지
3. AlertDispatcher — 텔레그램/슬랙 메시지 발송 + 최근 알림 내역 보관
"""
import json
from collections import deque
from datetime import date, datetime, timezone

import requests


# ─────────────────────────────────────────────
# 1) KillSwitch
# ─────────────────────────────────────────────
class KillSwitch:
    """
    당일 손실이 max_daily_loss_pct를 넘거나, 연속 오류가 max_errors를 넘으면
    is_active() == True → 이 상태에서 주문을 내보내지 않도록 거래 루프에서 체크.
    """
    def __init__(self, max_daily_loss_pct: float = 3.0, max_errors: int = 5,
                 max_single_order_pct: float = 5.0):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_errors = max_errors
        self.max_single_order_pct = max_single_order_pct
        self._day_start_equity = None
        self._errors_today = 0
        self._triggered = False
        self._trigger_reason = ""
        self._today = date.today()

    def _reset_if_new_day(self):
        today = date.today()
        if today != self._today:
            self._today = today
            self._errors_today = 0
            if not self._triggered:
                self._triggered = False
                self._trigger_reason = ""

    def set_day_start_equity(self, equity: float):
        self._reset_if_new_day()
        self._day_start_equity = float(equity)

    def record_success(self):
        self._reset_if_new_day()

    def record_error(self):
        self._reset_if_new_day()
        self._errors_today += 1
        if self._errors_today >= self.max_errors:
            self._triggered = True
            self._trigger_reason = f"연속 오류 {self._errors_today}회 도달"

    def check_daily_loss(self, current_equity: float) -> bool:
        """손실 임계치 초과 여부. True면 주문 차단해야 함."""
        self._reset_if_new_day()
        if self._triggered:
            return True
        if self._day_start_equity and self._day_start_equity > 0:
            loss_pct = (current_equity / self._day_start_equity - 1) * 100
            if loss_pct <= -abs(self.max_daily_loss_pct):
                self._triggered = True
                self._trigger_reason = f"당일 손실 {loss_pct:.2f}% — 임계치 {self.max_daily_loss_pct}% 초과"
        return self._triggered

    def check_order_size(self, order_value: float, portfolio_equity: float) -> bool:
        """단일 주문이 포트폴리오 대비 max_single_order_pct 초과면 True(차단)."""
        if portfolio_equity <= 0:
            return False
        order_pct = abs(order_value) / portfolio_equity * 100
        return order_pct > self.max_single_order_pct

    def is_active(self) -> bool:
        return self._triggered

    def status(self) -> dict:
        return {
            'triggered': self._triggered,
            'reason': self._trigger_reason,
            'errors_today': self._errors_today,
            'day_start_equity': self._day_start_equity,
        }

    def reset(self):
        self._triggered = False
        self._trigger_reason = ""
        self._errors_today = 0


# ─────────────────────────────────────────────
# 2) 포지션 대사
# ─────────────────────────────────────────────
def reconcile_positions(intended: dict, broker: list,
                         tolerance_shares: float = 0.001) -> dict:
    """
    intended: {'AAPL': 10.0, 'NVDA': 5.0, ...} — 앱이 생각하는 보유량
    broker: Alpaca get_positions() 반환값 리스트
    tolerance_shares: 이 이하 차이는 허용(반올림 오차 등)

    반환:
        matched: 일치 종목
        mismatches: 불일치 종목 (즉각 확인 필요)
        missing_in_broker: 앱엔 있는데 브로커엔 없음 (미체결 or 앱 버그)
        extra_in_broker: 브로커엔 있는데 앱 모름 (수동 주문 등)
    """
    broker_map = {pos['symbol']: float(pos.get('qty', 0)) for pos in broker}
    matched, mismatches, missing_in_broker, extra_in_broker = [], [], [], []

    for symbol, intended_qty in intended.items():
        broker_qty = broker_map.get(symbol, 0.0)
        diff = abs(intended_qty - broker_qty)
        if diff <= tolerance_shares:
            matched.append({'symbol': symbol, 'qty': intended_qty})
        else:
            mismatches.append({
                'symbol': symbol, 'intended': intended_qty,
                'broker': broker_qty, 'diff': round(diff, 4),
            })
        if symbol not in broker_map and intended_qty > tolerance_shares:
            missing_in_broker.append(symbol)

    for symbol in broker_map:
        if symbol not in intended:
            extra_in_broker.append({'symbol': symbol, 'broker_qty': broker_map[symbol]})

    return {
        'ok': len(mismatches) == 0 and len(missing_in_broker) == 0,
        'matched': matched,
        'mismatches': mismatches,
        'missing_in_broker': missing_in_broker,
        'extra_in_broker': extra_in_broker,
    }


# ─────────────────────────────────────────────
# 3) AlertDispatcher
# ─────────────────────────────────────────────
class AlertDispatcher:
    """
    텔레그램으로 알림을 보내고, 최근 N건의 알림 내역을 메모리에 보관.
    level: 'info' | 'warning' | 'critical'
    """
    EMOJI = {'info': 'ℹ️', 'warning': '⚠️', 'critical': '🚨'}

    def __init__(self, telegram_token: str = "", telegram_chat_id: str = "",
                 max_history: int = 100):
        self._token = telegram_token
        self._chat_id = telegram_chat_id
        self._history = deque(maxlen=max_history)

    def send(self, message: str, level: str = 'info') -> dict:
        emoji = self.EMOJI.get(level, 'ℹ️')
        full_msg = f"{emoji} [{level.upper()}] {message}"
        ts = datetime.now(timezone.utc).isoformat()
        record = {'ts': ts, 'level': level, 'message': message, 'sent': False, 'error': None}
        if self._token and self._chat_id:
            try:
                url = f"https://api.telegram.org/bot{self._token}/sendMessage"
                resp = requests.post(url, json={'chat_id': self._chat_id, 'text': full_msg},
                                     timeout=10)
                resp.raise_for_status()
                record['sent'] = True
            except Exception as e:
                record['error'] = str(e)
        self._history.append(record)
        return record

    def recent_alerts(self, n: int = 20) -> list:
        return list(self._history)[-n:]
