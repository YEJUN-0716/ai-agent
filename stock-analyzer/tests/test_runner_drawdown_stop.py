"""드로다운 스톱과 일별 리포트의 '당일 수익률' — 5덩어리(러너/워커) 리뷰.

두 함수 다 벽시계·입금 때문에 조용히 틀리던 자리다. 고친 규칙을 잠근다.
"""
import daily_report_toss as rpt
import paper_trade_runner_toss as runner


def _rec(date, equity, deposited=0.0):
    return {"date": date, "equity": equity, "deposited": deposited}


# ── 낙폭 판정: 입금을 뺀 곡선으로 잰다 ────────────────────────────────
def test_deposit_does_not_reset_the_peak():
    """증자한 날은 원본 곡선에서 신고가지만, 매매는 고점 대비 빠져 있었다.

    실측 재현(2026-08-18, 9천만원 증자): 원본 equity 로는 0.00%,
    입금을 빼면 −1.85%. 스톱은 뒤쪽을 봐야 한다.
    """
    records = [_rec("2026-08-14", 10_335_439), _rec("2026-08-17", 10_222_601)]
    # 오늘: 9천만 입금 + 매매로 고점 대비 하락
    blocked, dd = runner.check_portfolio_drawdown(
        100_144_188, records, deposited_now=90_000_000)
    assert not blocked
    assert -2.0 < dd < -1.5, dd          # 원본으로 재면 여기가 0.00 이 된다


def test_deposit_day_crash_still_trips_the_stop():
    """증자한 날 매매가 20% 빠졌으면 막아야 한다 — 원본 곡선은 못 잡는다."""
    records = [_rec("2026-08-14", 10_000_000)]
    equity_now = (10_000_000 * 0.80) + 90_000_000
    blocked, dd = runner.check_portfolio_drawdown(
        equity_now, records, deposited_now=90_000_000)
    assert blocked and dd < -15, (blocked, dd)
    # 같은 상황을 입금 정보 없이 재면(예전 동작) 신고가라 안 막힌다
    assert runner.check_portfolio_drawdown(equity_now, records)[0] is False


def test_plain_loss_without_deposit_is_unchanged():
    records = [_rec("2026-08-14", 10_000_000), _rec("2026-08-17", 9_000_000)]
    blocked, dd = runner.check_portfolio_drawdown(8_000_000, records)
    assert blocked and round(dd, 2) == -20.0


def test_no_records_or_disabled_never_blocks():
    assert runner.check_portfolio_drawdown(1, []) == (False, 0.0)


# ── 일별 리포트: 마지막 기록일의 수익률은 벽시계로 끄지 않는다 ────────
def test_last_ret_survives_a_stale_wall_clock():
    """크론이 자정 UTC 를 넘겨 돌아도 당일 수익률이 사라지면 안 된다.

    예전에는 records[-1]["date"] == 오늘(UTC) 일 때만 켜서, 30분만 밀리면
    러너가 멀쩡히 돈 날에 "오늘 페이퍼트레이드 미실행" 이 떴다.
    """
    perf = rpt.calc_perf([_rec("2026-08-18", 10_000_000),
                          _rec("2026-08-19", 10_100_000)])
    assert perf["last_ret"] == 1.0
    assert perf["last_date"] == "2026-08-19"


def test_last_ret_excludes_the_deposit():
    perf = rpt.calc_perf([_rec("2026-08-17", 10_000_000),
                          _rec("2026-08-18", 100_000_000, deposited=90_000_000)])
    assert perf["last_ret"] == 0.0, perf     # 입금만으로는 수익이 0
