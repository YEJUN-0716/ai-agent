"""킬스위치의 하루 손실 판정은 입금을 손익으로 세면 안 된다.

2026-08-18 증자 9천만원 때 원본 자산끼리 비교하면 하루 변화가 +879.6% 로
읽힌다(실제 −0.77%). 그 날 일일손실 가드는 통째로 눈을 감고, 반대로 출금한
날은 거짓 발동해 신규 매수를 전면 차단한다. 고점 대비를 보는
check_portfolio_drawdown 은 2026-08-20 에 고쳤는데 세 줄 아래 이 자리는
안 따라왔었다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paper_trade_runner_toss as runner  # noqa: E402
from modules.ops_safety import KillSwitch  # noqa: E402


def test_deposit_is_not_counted_as_profit():
    records = [{"equity": 10_222_601.0, "deposited": 0.0}]
    # 실제 실측값: 다음 날 자산 100,144,188 중 9천만원이 입금이다.
    adj = runner.deposit_adjusted_equity(100_144_188.0, records, 90_000_000.0)
    assert round((adj / records[-1]["equity"] - 1) * 100, 2) == -0.77


def test_withdrawal_is_not_counted_as_loss():
    """출금이 거짓 발동을 만들면 신규 매수가 전면 차단된다."""
    records = [{"equity": 10_000_000.0, "deposited": 5_000_000.0}]
    adj = runner.deposit_adjusted_equity(9_500_000.0, records, 4_500_000.0)
    assert adj == 10_000_000.0


def test_real_loss_still_triggers():
    """입금을 뺀 뒤에도 진짜 하락은 그대로 잡혀야 한다."""
    records = [{"equity": 10_000_000.0, "deposited": 0.0}]
    adj = runner.deposit_adjusted_equity(9_600_000.0, records, 0.0)
    ks = KillSwitch(max_daily_loss_pct=3.0)
    ks.set_day_start_equity(records[-1]["equity"])
    assert ks.check_daily_loss(adj) is True


def test_no_records_passes_equity_through():
    assert runner.deposit_adjusted_equity(123.0, [], 0.0) == 123.0
