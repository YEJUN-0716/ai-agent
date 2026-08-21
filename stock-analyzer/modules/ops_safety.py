"""
운영 안전성: 킬스위치 · 포지션 대사
=================================================================
실전 자동매매에서 가장 무서운 건 무한 손실 루프다.
이 모듈은 두 가지 안전망을 제공한다:

1. KillSwitch — 당일 손실 임계치 초과 시 거래 차단
2. reconcile_positions — 의도 포지션 vs 브로커 실제 포지션 불일치 탐지

알림 발송은 여기 없다. 텔레그램 발송 함수는 러너·보고서·성적표가 각자
`send_tg` 로 들고 있고, 그 사본들은 예외 원문에 봇 토큰이 섞이지 않도록
`type(e).__name__` 만 찍는다(tests/test_telegram_token_not_logged.py).
여기 있던 AlertDispatcher 는 부르는 코드가 한 곳도 없는 채로 `str(e)` 를
기록에 담고 있어 2026-08-21 리뷰에서 삭제했다 — 필요해지면 git 에 있다.
"""
from datetime import date


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
            self._day_start_equity = None  # 당일 기준가 재설정 필요
            if self._triggered:
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
            return True  # 자산 0 이하 → 모든 주문 차단
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
        elif symbol not in broker_map and intended_qty > tolerance_shares:
            missing_in_broker.append(symbol)
        else:
            mismatches.append({
                'symbol': symbol, 'intended': intended_qty,
                'broker': broker_qty, 'diff': round(diff, 4),
            })

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
