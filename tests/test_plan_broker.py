"""가상 장부가 트레이드 플랜을 백테스트와 **같은 규칙**으로 돌리는가.

이 파일이 지키는 건 하나다: 장부의 R 과 백테스트의 R 이 같은 자로 잰 값이어야
한다는 것. 규칙이 한 칸이라도 갈라지면 6.4년 OOS 로 잰 +0.66R 을 장부 성적과
비교할 수 없고, 그러면 가상 브로커를 갈아끼운 이유가 사라진다.

그래서 체결·청산 판정을 순수 함수로 떼어내 결정적으로 잰다 — 네트워크 없음.
(백테스트 쪽 규칙은 modules/trade_plan_backtest._simulate_outcome)
"""

import numpy as np
import pytest

import paper_trade_runner_toss as runner
from modules import trade_plan_backtest as bt
from modules import virtual_broker as vb


def bars(*rows) -> list[tuple]:
    """(시, 고, 저, 종) 목록 → 날짜가 붙은 일봉 튜플. 날짜는 순서만 의미 있다."""
    return [(f"2026-09-{i + 1:02d}", *r) for i, r in enumerate(rows)]


# ── 지정가 진입 ────────────────────────────────────────────────────────
def test_limit_entry_fills_on_the_first_bar_that_touches_the_zone():
    # 저가가 지정가에 닿은 첫 봉에서 체결. 그 전 봉은 위에서만 놀았다.
    filled = vb.scan_limit_fill(
        bars((105, 106, 102, 104),      # 저가 102 > 지정가 100 → 안 닿음
             (103, 104,  99, 101)),     # 저가  99 <= 100 → 체결
        limit_price=100.0)
    assert filled == {"status": "filled", "date": "2026-09-02", "price": 100.0}


def test_gap_down_fills_at_the_open_not_at_the_limit():
    # 갭하락으로 진입 구간을 뛰어넘고 열리면 실제로 받는 값은 시가다.
    # 지정가로 적으면 장부가 현실보다 좋아진다.
    filled = vb.scan_limit_fill(bars((94, 96, 90, 95)), limit_price=100.0)
    assert filled["price"] == 94.0


def test_limit_entry_expires_after_the_fill_window():
    never = bars(*[(105, 106, 102, 104)] * vb.LIMIT_FILL_WINDOW)
    assert vb.scan_limit_fill(never, limit_price=100.0)["status"] == "expired"

    # 아직 창이 안 찼으면 폐기가 아니라 대기다 — 다음 실행에서 다시 본다.
    assert vb.scan_limit_fill(never[:-1], limit_price=100.0)["status"] == "waiting"


# ── 라인 청산 ──────────────────────────────────────────────────────────
def test_exit_takes_the_stop_when_one_bar_hits_both():
    # 일봉만으로는 손절과 목표 중 무엇이 먼저였는지 알 수 없다. 백테스트와
    # 같이 보수적으로 손절을 잡는다. 반대로 하면 장부 성적이 위로 새어나간다.
    hit = vb.scan_plan_exit(bars((100, 120, 85, 110)), stop=90.0, target=115.0)
    assert hit["outcome"] == "loss"


def test_exit_prices_follow_the_gap_not_the_line():
    gapped_down = vb.scan_plan_exit(bars((85, 88, 84, 87)), stop=90.0, target=115.0)
    assert gapped_down == {"outcome": "loss", "date": "2026-09-01", "price": 85.0}

    gapped_up = vb.scan_plan_exit(bars((120, 122, 119, 121)), stop=90.0, target=115.0)
    assert gapped_up == {"outcome": "win", "date": "2026-09-01", "price": 120.0}


def test_position_times_out_at_the_open_after_the_hold_window():
    quiet = bars(*[(100, 101, 99, 100)] * (vb.PLAN_HOLD_WINDOW + 1))
    timed_out = vb.scan_plan_exit(quiet, stop=90.0, target=115.0)
    assert timed_out["outcome"] == "timeout"
    assert timed_out["price"] == 100.0            # 창 다음 봉의 시가로 청산

    # 창이 안 찼으면 아직 결판나지 않았다 — None 이어야 포지션이 살아 남는다.
    assert vb.scan_plan_exit(quiet[:-1], stop=90.0, target=115.0) is None


def test_realized_r_divides_by_plan_risk_not_by_the_fill():
    # 분모는 러너가 사이징한 값(entry_ref − stop)이다. 백테스트와 같은 자.
    # 구간 상단(101)에 채워지면 실제 주당 위험이 계획(5)보다 넓으므로
    # 손절은 −1.00R 이 아니라 **−1.2R** 이다. 이게 실제로 잃는 돈이다.
    assert vb.realized_r(101.0, 95.0, 95.0, entry_ref=100.0) == pytest.approx(-1.2)
    assert vb.realized_r(101.0, 95.0, 110.0, entry_ref=100.0) == pytest.approx(1.8)
    # 갭하락으로 더 싸게(98) 샀으면 같은 손절가라도 덜 잃는다.
    assert vb.realized_r(98.0, 95.0, 95.0, entry_ref=100.0) == pytest.approx(-0.6)
    # entry_ref 가 없는 옛 플랜은 체결가로 되돌아간다(하위호환).
    assert vb.realized_r(98.0, 95.0, 95.0) == pytest.approx(-1.0)


def test_ledger_and_backtest_price_the_same_gap_identically():
    """두 자가 갈라지는 걸 잡는 유일한 테스트.

    2026-08-16 에 실제로 갈라져 있었다 — 장부는 갭을 지나간 손절을 시가로
    적었는데 백테스트는 손절가로 적어, 손절 아래에서 체결된 건에 **+1.36R**
    을 기록했다. 새 규칙을 한쪽에만 넣으면 여기서 죽는다.
    """
    plan = {"direction": "long", "stop": 93.0, "targets": [102.0],
            "entry": {"low": 95.0, "high": 97.0, "ref": 96.0}}
    limit, plan_risk = 97.0, 3.0

    for label, (o, h, low, c) in {
        "손절 아래로 갭": (90.0, 91.0, 89.0, 90.0),
        "손절을 장중에 이탈": (96.0, 96.5, 92.0, 92.5),
        "목표 위로 갭": (108.0, 109.0, 107.0, 108.5),
    }.items():
        entry_bar = (96.0, 97.5, 95.0, 96.5)      # 지정가 97 에 체결되는 봉
        ledger_fill = vb.scan_limit_fill([("d0", *entry_bar)], limit)
        exit_hit = vb.scan_plan_exit([("d0", *entry_bar), ("d1", o, h, low, c)],
                                     plan["stop"], plan["targets"][0])
        ledger_r = vb.realized_r(ledger_fill["price"], plan["stop"],
                                 exit_hit["price"], entry_ref=plan["entry"]["ref"])

        opens = np.array([100.0, entry_bar[0], o])
        got = bt.placeable_r({"outcome": exit_hit["outcome"], "r": 0.0,
                              "fill_idx": 1, "exit_idx": 2},
                             plan, limit, opens, plan_risk)

        assert got["fill_price"] == ledger_fill["price"], label
        assert got["exit_price"] == exit_hit["price"], label
        assert got["r"] == pytest.approx(ledger_r), label


# ── 사이징 ─────────────────────────────────────────────────────────────
def test_size_risks_a_fixed_share_of_capital_not_a_fixed_amount():
    # 자본 1,000만, 위험 0.5% = 5만원. 주당 위험 (100−95)×1,000 = 5,000원 → 10주.
    qty = runner.plan_position_size(10_000_000, entry_ref=100.0, stop=95.0,
                                    fx=1000.0, risk_pct=0.5, max_pos_pct=100)
    assert qty == 10


def test_position_cap_binds_when_the_stop_is_tight():
    # 손절이 1% 밖에 안 떨어져 있으면 위험 기준만으로는 50주(자본의 50%)가 된다.
    # 이 시스템의 손절은 실제로 1.3~3% 라 상한이 없으면 한 종목이 장부를 삼킨다.
    qty = runner.plan_position_size(10_000_000, entry_ref=100.0, stop=99.0,
                                    fx=1000.0, risk_pct=0.5, max_pos_pct=15)
    assert qty == 15                       # 15% / (100 × 1,000원) = 15주


def test_size_is_zero_when_one_share_is_unaffordable():
    # 소수점 거래 불가. 0.4주는 0주다 — 1주로 올림하면 위험이 계획보다 커진다.
    assert runner.plan_position_size(1_000_000, entry_ref=100.0, stop=95.0,
                                     fx=1400.0, risk_pct=0.5) == 0


# ── 후보 순위 ──────────────────────────────────────────────────────────
def test_grade_a_outranks_b_and_ties_break_on_distance_to_entry():
    ranked = runner.rank_plan_candidates([
        {"ticker": "B_FAR",  "grade": "B", "current": 110.0, "entry_ref": 100.0},
        {"ticker": "A_FAR",  "grade": "A", "current": 110.0, "entry_ref": 100.0},
        {"ticker": "B_NEAR", "grade": "B", "current": 101.0, "entry_ref": 100.0},
    ])
    # 등급 안에서는 기대값이 구별되지 않는다고 측정됐으므로, 안 측정된 기준을
    # 새로 만들지 않고 체결 확률이 높은(진입가에 가까운) 쪽을 앞에 둔다.
    assert [c["ticker"] for c in ranked] == ["A_FAR", "B_NEAR", "B_FAR"]


# ── 장부 통합 ──────────────────────────────────────────────────────────
@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(vb, "STATE_FILE", str(tmp_path / "virtual_portfolio.json"))
    monkeypatch.setattr(vb, "_FX", 1000.0)
    return vb


def test_limit_order_reserves_cash_and_settles_into_a_plan_position(broker, monkeypatch):
    broker.place_limit_entry("AAA", qty=10, limit_price=100.0,
                             plan={"stop": 95.0, "target": 115.0, "rr": 3.0,
                                   "grade": "A", "risk_pct": 5.0})
    # 예약은 체결 전까지 현금을 줄이지 않으므로, 가용 현금에서 빼야 러너가
    # 같은 돈으로 주문을 또 내지 않는다.
    assert broker.available_krw(broker.load_state()) == pytest.approx(9_000_000)

    monkeypatch.setattr(broker, "daily_bars",
                        lambda sym, since, until=None: bars((99, 101, 97, 100)))
    state = broker.settle_pending(broker.load_state(), 1000.0)

    pos = state["positions"]["AAA"]
    assert pos["qty"] == 10 and pos["plan"]["entry_fill"] == 99.0
    assert state["pending"] == []
    assert state["cash_krw"] == pytest.approx(10_000_000 - 10 * 99 * 1000)


def test_stop_out_records_the_r_the_scorecard_needs(broker, monkeypatch):
    state = broker.load_state()
    state["positions"]["AAA"] = {
        "qty": 10, "avg_price_usd": 101.0, "entry_date": "2026-09-01",
        "plan": {"entry_ref": 100.0, "entry_fill": 101.0, "stop": 95.0,
                 "target": 115.0, "rr": 3.0, "grade": "A"},
    }
    broker.save_state(state)
    monkeypatch.setattr(broker, "daily_bars",
                        lambda sym, since, until=None: bars((99, 100, 94, 96)))

    state = broker.settle_pending(broker.load_state(), 1000.0)
    exit_trade = state["trades"][-1]
    assert exit_trade["outcome"] == "loss"
    # 구간 상단 체결(101)이라 손절은 −1.00R 이 아니다. 장부가 −1.00 을 적으면
    # 손실을 과소 기록하는 것이고, 백테스트와 다른 자로 재게 된다.
    assert exit_trade["r_realized"] == pytest.approx(-1.2)
    assert "AAA" not in state["positions"]


def test_manual_market_buy_still_works(broker, monkeypatch):
    """비서(챗봇)의 수동 매수는 kind 없는 옛 주문이다. 하위호환이 깨지면
    저장소 이음매에서 조용히 죽는다 — 이미 한 번 겪은 사고다."""
    broker.place_notional_buy("BBB", 1000.0, market="US")
    monkeypatch.setattr(broker, "next_open_price",
                        lambda sym, after: (50.0, "2026-09-02"))

    state = broker.settle_pending(broker.load_state(), 1000.0)
    assert state["positions"]["BBB"]["qty"] == 20
    assert "plan" not in state["positions"]["BBB"]


def test_summary_counts_unfilled_setups_so_the_win_rate_cannot_inflate():
    # 백테스트에서 셋업의 36% 가 진입구간 미도달로 사라졌다. 체결분만 세면
    # 장부 체결률이 100% 로 보이고 백테스트와 비교가 안 된다.
    perf = runner.plan_trade_summary([
        {"plan": True, "side": "buy"},
        {"plan": True, "outcome": "win",  "r_realized": 3.0},
        {"plan": True, "side": "buy"},
        {"plan": True, "outcome": "loss", "r_realized": -1.0},
        {"plan": True, "outcome": "nofill", "r_realized": 0.0},
        {"side": "buy"},                        # 팩터 시절 매수 — 안 섞인다
    ])
    assert perf["n_setups"] == 3
    assert perf["n_filled"] == 2
    assert perf["n_resolved"] == 2
    assert perf["avg_r"] == pytest.approx(1.0)
    assert perf["win_rate"] == pytest.approx(50.0)
    assert perf["fill_rate"] == pytest.approx(66.7)
