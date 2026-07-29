"""가상 브로커 매매 — 제안과 실행을 분리한다.

비서(모델)에게는 request_trade만 준다. 실행 함수(approve_request)는
모델의 도구 목록에 넣지 않고, 사용자가 승인 명령을 쳤을 때 채널이 직접 부른다.
실제 돈이 오가는 주문 도구는 이 파일에 없고, 앞으로도 만들지 않는다.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Protocol

from assistant.config import Settings
from tools.assistant_notes import load_json_list, now_kst, record_audit

VALID_SIDES = ("buy", "sell")

# 대기 목록 파일의 읽기-수정-쓰기를 감싼다.
#
# 텔레그램과 웹이 한 프로세스에서 동시에 돌고, 웹은 요청을 스레드로 처리한다.
# 잠금이 없으면 두 승인이 같은 제안을 각자 "내가 뺐다"고 판단해 브로커를 두 번
# 부른다 — 사장님이 한 번 승인한 매매가 두 건의 주문이 된다. 실제로 재현됐다.
_PENDING_LOCK = threading.Lock()


class TradeError(RuntimeError):
    """매매 요청·승인이 거부됐을 때. 메시지는 사용자에게 그대로 보여준다."""


class TradeExecutor(Protocol):
    def buy(self, symbol: str, amount_krw: float) -> dict: ...
    def sell(self, symbol: str, qty: int) -> dict: ...


class VirtualBrokerExecutor:
    """stock-analyzer의 가상 브로커를 부른다. 상태 파일에 직접 손대지 않는다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _module(self):
        path = str(self._settings.stock_analyzer_path)
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            from modules import virtual_broker  # noqa: PLC0415
        except ImportError as exc:
            raise TradeError(
                "stock-analyzer의 가상 브로커를 불러오지 못했습니다: "
                f"{exc}. STOCK_ANALYZER_PATH 설정을 확인하세요."
            ) from exc
        return virtual_broker

    def buy(self, symbol: str, amount_krw: float) -> dict:
        return self._module().place_notional_buy(symbol, amount_krw)

    def sell(self, symbol: str, qty: int) -> dict:
        return self._module().place_market_sell(symbol, qty)


def _pending_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "pending_trades.json"


def _load_pending(settings: Settings) -> list[dict]:
    """대기 중인 제안을 읽는다.

    손상 파일 처리는 프로젝트 공통 정책(load_json_list)을 그대로 쓴다 —
    망가진 파일은 조용히 버리지 않고 보관하고 감사 기록에 남긴다.
    """
    return load_json_list(settings, _pending_path(settings))


def _save_pending(settings: Settings, items: list[dict]) -> None:
    path = _pending_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _describe(request: dict) -> str:
    if request["side"] == "buy":
        return f"{request['symbol']} {request['amount_krw']:,.0f}원어치 매수"
    return f"{request['symbol']} {request['qty']}주 매도"


def request_trade(
    settings: Settings,
    side: str,
    symbol: str,
    amount_krw: float | None = None,
    qty: int | None = None,
) -> dict:
    """매매를 제안한다. 실행하지 않는다 — 사용자 승인이 있어야 나간다."""
    side = side.strip().lower()
    if side not in VALID_SIDES:
        raise TradeError(f"side는 buy 또는 sell이어야 합니다: {side!r}")

    sym = symbol.strip().upper()
    if not sym:
        raise TradeError("종목 코드가 비어 있습니다.")

    if side == "buy":
        if amount_krw is None:
            raise TradeError("매수하려면 금액(amount_krw)이 필요합니다.")
        if float(amount_krw) <= 0:
            raise TradeError("금액은 0보다 커야 합니다.")
    else:
        if qty is None:
            raise TradeError("매도하려면 수량(qty)이 필요합니다.")
        if int(qty) <= 0:
            raise TradeError("수량은 0보다 커야 합니다.")

    request = {
        "request_id": uuid.uuid4().hex[:8],
        "side": side,
        "symbol": sym,
        "amount_krw": float(amount_krw) if amount_krw is not None else None,
        "qty": int(qty) if qty is not None else None,
        "requested_at": now_kst().isoformat(timespec="seconds"),
    }

    with _PENDING_LOCK:
        items = _load_pending(settings)
        items.append(request)
        _save_pending(settings, items)
    record_audit(
        settings, "trade_request", f"{request['request_id']} {_describe(request)}"
    )

    return {
        "status": "confirmation_required",
        "request_id": request["request_id"],
        "summary": _describe(request),
        "message": (
            f"{_describe(request)}를 예약하려면 승인이 필요합니다. "
            f"'/승인 {request['request_id']}'라고 답해주세요. "
            "제가 직접 실행할 수는 없습니다."
        ),
    }


def list_pending_requests(settings: Settings) -> list[dict]:
    """승인 대기 중인 매매 제안."""
    return _load_pending(settings)


def _restore_request(settings: Settings, request: dict) -> None:
    """꺼낸 제안을 대기 목록에 되돌려 놓는다. 실행이 실패했을 때만 쓴다."""
    with _PENDING_LOCK:
        items = _load_pending(settings)
        if any(
            item.get("request_id") == request["request_id"] for item in items
        ):
            return
        items.append(request)
        _save_pending(settings, items)


def _pop_request(settings: Settings, request_id: str) -> dict:
    """제안을 대기 목록에서 꺼낸다. 꺼내는 데 성공한 쪽만 실행 자격을 갖는다.

    찾기·제거·저장이 한 덩어리로 일어나야 한다. 잠금 없이 하면 동시에 들어온
    두 승인이 같은 항목을 각자 찾아 각자 주문을 넣는다.
    """
    with _PENDING_LOCK:
        items = _load_pending(settings)
        for index, item in enumerate(items):
            if item.get("request_id") == request_id:
                items.pop(index)
                _save_pending(settings, items)
                return item
    raise TradeError(
        f"승인 대기 중인 요청 {request_id}를 찾지 못했습니다. "
        "이미 처리됐거나 잘못된 번호입니다."
    )


def approve_request(
    settings: Settings, request_id: str, executor: TradeExecutor | None = None
) -> dict:
    """사용자 승인 후 실제로 가상 브로커에 주문을 넣는다.

    채널 계층 전용. 모델의 도구 목록에 넣지 말 것.
    """
    request = _pop_request(settings, request_id)
    broker = executor or VirtualBrokerExecutor(settings)

    try:
        if request["side"] == "buy":
            result = broker.buy(request["symbol"], request["amount_krw"])
        else:
            result = broker.sell(request["symbol"], request["qty"])
    except Exception as exc:
        # 제안은 이미 대기 목록에서 빠진 뒤다. 여기서 그냥 터지면 사장님이
        # 승인한 제안이 흔적 없이 사라진다. 되돌려놓고 기록을 남긴 뒤 알린다.
        _restore_request(settings, request)
        record_audit(
            settings,
            "trade_approve_failed",
            f"{request_id} {_describe(request)} — 브로커 실패: {exc}",
        )
        raise TradeError(
            f"{_describe(request)} 주문을 넣지 못했습니다: {exc}. "
            f"제안은 그대로 두었으니 '/승인 {request_id}'로 다시 시도할 수 있습니다."
        ) from exc

    record_audit(
        settings, "trade_approve", f"{request_id} {_describe(request)}"
    )
    return {
        "executed": True,
        "request_id": request_id,
        "summary": _describe(request),
        "broker_result": result,
    }


def reject_request(settings: Settings, request_id: str) -> dict:
    """제안을 버린다. 채널 계층 전용."""
    request = _pop_request(settings, request_id)
    record_audit(settings, "trade_reject", f"{request_id} {_describe(request)}")
    return {"rejected": True, "request_id": request_id,
            "summary": _describe(request)}
