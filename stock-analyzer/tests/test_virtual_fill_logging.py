"""가상 체결이 성적표에 오르는가 — 진짜 체결가와 진짜 체결일로.

가상 매매는 성적표에 한 건도 오르지 않고 있었다. 체결이 주문 시점이 아니라
다음 거래일 시가에, 그것도 러너의 매수 경로 밖(settle_pending)에서 일어나기
때문이다. 지금 돌고 있는 유일한 매매가 성적에 안 잡히는 상태였다.

메우는 방법도 중요하다. 어제 종가나 오늘 다시 계산한 점수로 채우면 숫자는
차지만 근거 없는 값이 성적표에 박힌다. 채울 수 있는 것만 채우고 모르는
것은 비워 둔다.
"""

import json
from datetime import datetime, timezone

import pytest

import paper_trade_runner_toss as runner
from modules import virtual_broker as vb


@pytest.fixture
def signal_log(tmp_path, monkeypatch):
    path = tmp_path / "signal_log.json"
    monkeypatch.setattr(runner, "SIGNAL_LOG_FILE", str(path))
    return path


def _entries(path):
    return json.loads(path.read_text())["signals"]


def test_fill_is_logged_at_its_own_price_and_date(signal_log):
    # settle_pending 이 남긴 체결 기록 그대로를 넘긴다.
    n = runner.record_virtual_fills([
        {"date": "2026-07-31", "symbol": "AAA", "side": "buy",
         "qty": 10, "price_usd": 100.0, "amount_krw": 1_000_000},
    ])

    assert n == 1
    entry = _entries(signal_log)[0]
    assert entry["entry_price"] == 100.0        # 신호가가 아니라 체결가
    assert entry["entry_date"] == "2026-07-31"  # 기록한 날이 아니라 체결일
    assert entry["id"] == "AAA-2026-07-31"


def test_order_score_travels_from_order_to_the_scorecard(signal_log, tmp_path,
                                                          monkeypatch):
    """주문 → 다음 거래일 체결 → 성적표까지 점수가 살아서 건너오는가.

    점수는 주문 시점에만 존재하고 체결은 다음 거래일에 일어난다. 그 사이를
    잇는 고리가 하나라도 끊기면(브로커가 meta 를 안 싣거나, 체결 기록에 안
    옮기거나, 성적표가 안 읽거나) 점수 칸이 조용히 비고 가상 체결이 점수
    구간별 분석에서 통째로 빠진다 — 2026-08-04 까지 실제로 그 상태였다.
    """
    monkeypatch.setattr(vb, "STATE_FILE", str(tmp_path / "virtual_portfolio.json"))
    monkeypatch.setattr(vb, "_FX", 1000.0)
    monkeypatch.setattr(vb, "next_open_price",
                        lambda symbol, after: (100.0, "2026-07-31"))

    vb.place_notional_buy("AAA", 1000.0, meta={"score": 82, "rsi": 61})
    state = vb.settle_pending(vb.load_state(), 1000.0)

    runner.record_virtual_fills(state["trades"])

    entry = _entries(signal_log)[0]
    assert entry["score"] == 82
    assert entry["rsi"] == 61
    assert entry["entry_price"] == 100.0        # 신호가가 아니라 체결가


def test_unknown_score_is_left_empty_not_faked(signal_log):
    # 근거를 안 실은 주문(예전 예약분, 비서를 통한 수동 매수)은 점수를 모른다.
    # 0 이나 오늘 다시 계산한 점수로 채우면 점수 구간별 분석이 없는 근거로 오염된다.
    runner.record_virtual_fills([
        {"date": "2026-07-31", "symbol": "AAA", "side": "buy",
         "qty": 10, "price_usd": 100.0},
    ])

    entry = _entries(signal_log)[0]
    assert entry["score"] is None
    assert entry["rsi"] is None


def test_order_carries_the_published_verdict_as_an_observation():
    """트레이드 플랜 주문에 그날 공개돼 있던 총괄 판정이 실리는가.

    2026-08-10 팩터 → 트레이드 플랜 전환 때 `place_limit_entry` 에 meta 를 안
    실어서, 전환 이후 체결은 성적표 점수 칸이 통째로 비었다(실측 21건 중 6건만
    점수 있음, 전부 전환 전 건). 고리가 셋인데(주문→체결→성적표) 위 두 테스트가
    아래 둘을 잡고, 이 테스트가 첫 고리를 잡는다.

    실리는 값은 **주문 근거가 아니라 관측 기록**이다 — score_role 이 그걸 박는다.
    """
    scores = {"AAA": {"chart": 61.0, "quant": 58.0, "ict": 70.0, "verdict": 63.4}}

    meta = runner.order_meta(scores, "2026-08-12", "AAA")
    assert meta["score"] == 63.4               # by_score_bucket 이 읽는 키
    assert meta["score_role"] == "observed"    # 주문이 쓴 값이 아니다
    assert meta["analyst"]["ict"] == 70.0      # 3팀 원점수도 같이 남는다
    assert meta["analyst_asof"] == "2026-08-12"


def test_ticker_without_a_verdict_gets_no_faked_score():
    # 러너 유니버스(95종목)와 애널리스트 기록 유니버스(S&P 500)는 다르다.
    # 없는 종목을 중립 50 으로 채우면 '기록 없음'이 '중립 판단'으로 성적에 섞인다.
    assert runner.order_meta({}, None, "AAA") == {}
    assert runner.order_meta({"AAA": {"chart": 61.0}}, "2026-08-12", "AAA") == {}


def test_sell_fills_do_not_enter_the_log(signal_log):
    # 성적표는 진입만 기록하고 결과는 resolve_signal_outcomes 가 채운다.
    n = runner.record_virtual_fills([
        {"date": "2026-07-31", "symbol": "AAA", "side": "sell",
         "qty": 10, "price_usd": 120.0},
    ])

    assert n == 0
    assert not signal_log.exists()


def test_same_fill_is_not_logged_twice(signal_log):
    fill = [{"date": "2026-07-31", "symbol": "AAA", "side": "buy",
             "qty": 10, "price_usd": 100.0}]

    runner.record_virtual_fills(fill)
    runner.record_virtual_fills(fill)

    assert len(_entries(signal_log)) == 1


def test_ordinary_signals_still_use_today(signal_log):
    # entry_date 를 안 주는 기존 호출부(주문 시점 기록)는 동작이 그대로여야 한다.
    log = runner.append_signals_to_log(
        [{"symbol": "BBB", "action": "매수", "price": 50.0, "score": 70, "rsi": 55}],
        [],
    )

    assert log[0]["entry_date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert log[0]["score"] == 70
