"""비서와 stock-analyzer가 맞닿는 지점을 진짜로 통과시켜 보는 테스트.

다른 테스트는 전부 가짜 브로커를 주입한다. 그래서 두 저장소를 잇는 계약
— 함수 이름, 인자 순서, **금액의 단위** — 은 아무도 확인하지 않았다.
2026-07-30에 그 틈으로 결함 두 개가 106 그린인 채로 머지됐다:

  * 원화 50만원을 그대로 넘겨 장부에 7억원으로 적혔다 (환율 1,400배).
  * 주문이 저장된 뒤 print가 터져 "실패"로 처리되고 제안이 되살아났다.
    안내대로 재승인하면 같은 주문이 두 번 나갔다.

둘 다 가짜 브로커로는 절대 잡을 수 없다. 이 파일만이 진짜 모듈을 부른다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from assistant.config import Settings
from tools import virtual_trade
from tools.virtual_trade import VirtualBrokerExecutor, approve_request, request_trade

STOCK_ANALYZER = Path(r"C:\Users\1aass\stock-analyzer")

pytestmark = pytest.mark.skipif(
    not (STOCK_ANALYZER / "modules" / "virtual_broker.py").exists(),
    reason=f"stock-analyzer를 찾지 못했습니다: {STOCK_ANALYZER}",
)


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """진짜 virtual_broker를, 장부만 임시 파일로 바꿔서 쓴다."""
    monkeypatch.setenv("VIRTUAL_PORTFOLIO_FILE", str(tmp_path / "portfolio.json"))
    if str(STOCK_ANALYZER) not in sys.path:
        sys.path.append(str(STOCK_ANALYZER))

    # 상태 파일 경로를 모듈 상수(STATE_FILE)로 한 번만 읽으므로,
    # 환경변수를 바꿨으면 다시 불러와야 반영된다.
    for name in ("modules.virtual_broker", "modules"):
        sys.modules.pop(name, None)
    from modules import virtual_broker

    return virtual_broker


@pytest.fixture
def settings(tmp_path) -> Settings:
    data_dir = tmp_path / "assistant"
    data_dir.mkdir()
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=STOCK_ANALYZER,
        assistant_data_dir=data_dir,
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
        study_inbox=tmp_path,
        obsidian_vault=tmp_path,
    )


def _orders() -> list[dict]:
    path = Path(os.environ["VIRTUAL_PORTFOLIO_FILE"])
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["pending"]


def test_buy_amount_reaches_the_ledger_in_won(settings, broker):
    """사장님이 말한 원화 금액이 장부에 같은 원화 금액으로 적혀야 한다.

    place_notional_buy 는 미국 종목의 금액을 달러로 해석해 환율을 곱한다.
    비서가 환산하지 않고 원화를 넘기면 1,400배로 부풀어, 체결 시점에
    현금 부족으로 조용히 폐기된다 — 사장님은 예약됐다고 들은 채로.
    """
    # Arrange
    asked_krw = 500_000

    # Act
    VirtualBrokerExecutor(settings).buy("AAPL", asked_krw)

    # Assert — 오차는 환율 왕복의 부동소수점 수준(1원 미만)만 허용한다
    orders = _orders()
    assert len(orders) == 1
    assert orders[0]["notional_krw"] == pytest.approx(asked_krw, abs=1.0)


def test_buy_does_not_exceed_the_account(settings, broker):
    """계좌보다 작은 금액을 시켰으면 장부에도 계좌보다 작게 적혀야 한다.

    단위가 어긋나면 이 조건이 먼저 깨진다. 배수를 직접 세지 않아도
    '체결 가능한 주문인가'라는 실제로 중요한 성질을 잡아낸다.
    """
    # Arrange / Act
    VirtualBrokerExecutor(settings).buy("AAPL", 500_000)

    # Assert
    assert _orders()[0]["notional_krw"] <= broker.INITIAL_CAPITAL_KRW


def test_approval_survives_a_console_that_cannot_print_korean(
    settings, broker, monkeypatch
):
    """한글을 못 찍는 콘솔에서도 승인은 성공해야 한다.

    브로커는 주문을 저장한 **뒤** 진행 상황을 print한다. cp949 콘솔에서는
    그 줄의 '—'(U+2014)가 UnicodeEncodeError를 낸다. 저장이 끝난 뒤 터지므로
    주문은 이미 나갔는데 approve_request는 실패로 보고 제안을 되살렸다.
    사장님이 안내대로 재승인하면 주문이 두 번 나간다.
    """
    # Arrange — cp949 콘솔을 흉내 낸다
    class Cp949Stdout:
        encoding = "cp949"

        def write(self, text: str) -> int:
            text.encode("cp949")  # 한글·대시에서 터진다
            return len(text)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(sys, "stdout", Cp949Stdout())
    request_id = request_trade(
        settings, "buy", "AAPL", amount_krw=500_000
    )["request_id"]

    # Act
    result = approve_request(settings, request_id)

    # Assert — 주문은 한 건, 대기 목록은 비어 있어야 한다
    assert result["executed"] is True
    assert len(_orders()) == 1
    assert virtual_trade.list_pending_requests(settings) == []


def test_one_approval_places_exactly_one_order(settings, broker):
    """승인 한 번에 주문은 정확히 한 건. 두 번째 승인은 거절돼야 한다."""
    # Arrange
    request_id = request_trade(
        settings, "buy", "AAPL", amount_krw=500_000
    )["request_id"]

    # Act
    approve_request(settings, request_id)
    with pytest.raises(virtual_trade.TradeError):
        approve_request(settings, request_id)

    # Assert
    assert len(_orders()) == 1
