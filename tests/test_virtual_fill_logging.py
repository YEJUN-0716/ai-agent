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


def test_unknown_score_is_left_empty_not_faked(signal_log):
    # 점수·RSI 는 주문 시점의 값이라 체결 시점엔 알 수 없다. 0 이나 오늘 점수로
    # 채우면 점수 구간별 분석이 없는 근거로 오염된다.
    runner.record_virtual_fills([
        {"date": "2026-07-31", "symbol": "AAA", "side": "buy",
         "qty": 10, "price_usd": 100.0},
    ])

    entry = _entries(signal_log)[0]
    assert entry["score"] is None
    assert entry["rsi"] is None


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
