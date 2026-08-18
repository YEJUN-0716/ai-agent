"""가상 브로커 매매 — 제안과 실행을 분리한다.

비서(모델)에게는 request_trade만 준다. 실행 함수(approve_request)는
모델의 도구 목록에 넣지 않고, 사용자가 승인 명령을 쳤을 때 채널이 직접 부른다.
실제 돈이 오가는 주문 도구는 이 파일에 없고, 앞으로도 만들지 않는다.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Protocol

from assistant.config import Settings
from tools.assistant_notes import (
    load_json_list,
    now_kst,
    record_audit,
    save_json_list,
)

VALID_SIDES = ("buy", "sell")

# 대기 목록 파일의 읽기-수정-쓰기를 감싼다.
#
# 텔레그램과 웹이 한 프로세스에서 동시에 돌고, 웹은 요청을 스레드로 처리한다.
# 잠금이 없으면 두 승인이 같은 제안을 각자 "내가 뺐다"고 판단해 브로커를 두 번
# 부른다 — 사장님이 한 번 승인한 매매가 두 건의 주문이 된다. 실제로 재현됐다.
_PENDING_LOCK = threading.Lock()

# 브로커 호출을 한 번에 하나씩만 보낸다.
#
# virtual_broker는 주문마다 상태 파일을 통째로 읽고-고치고-쓴다. 잠금이 없으면
# 동시에 들어온 두 주문 중 하나가 흔적 없이 사라진다. 그리고 아래에서 stdout을
# 잠시 바꿔치기하므로, 그 구간이 겹치지 않게 하는 역할도 겸한다.
_BROKER_LOCK = threading.Lock()

log = logging.getLogger("assistant.trade")


def _run_broker(func, *args, **kwargs) -> dict:
    """브로커 함수를 부른다. 브로커가 찍는 글자가 거래를 망치지 못하게 한다.

    virtual_broker는 주문을 **저장한 뒤** 진행 상황을 print한다. 한국어 윈도우
    콘솔(cp949)은 그 줄의 '—'(U+2014)를 인코딩하지 못해 UnicodeEncodeError를
    낸다. 저장이 끝난 뒤 터지므로 주문은 이미 나갔는데 호출자는 실패로 본다.
    실제로 2026-07-30에 재현됐다 — 승인 한 번에 주문이 나가고도 제안이 되살아나,
    안내대로 재승인하면 같은 주문이 두 건이 됐다.

    그래서 브로커의 출력을 메모리 버퍼로 돌린다. 버퍼는 어떤 문자든 받으므로
    콘솔 인코딩과 무관해지고, 내용은 로그로 남겨 잃지 않는다.
    """
    buffer = io.StringIO()
    with _BROKER_LOCK:
        with contextlib.redirect_stdout(buffer):
            result = func(*args, **kwargs)

    for line in buffer.getvalue().splitlines():
        if line.strip():
            log.info("%s", line.strip())
    return result


class TradeError(RuntimeError):
    """매매 요청·승인이 거부됐을 때. 메시지는 사용자에게 그대로 보여준다."""


# 원/달러는 하루에도 움직이지만 초 단위로 볼 값은 아니다. 한 시간 재사용한다.
_FX_TTL_SEC = 3600
_fx_cache: tuple[float, float] | None = None  # (환율, 받은 시각)


def _inject_fx(broker) -> None:
    """실시간 원/달러를 브로커에 넣는다. 못 받으면 브로커 기본값을 그대로 둔다.

    브로커의 _FX 기본값은 1,400 고정이다. 러너(app.py)는 시작할 때
    set_fx()로 실시간 환율을 넣는데, 비서는 별도 프로세스라 아무도 넣어주지
    않는다. 그대로 두면 평가금액과 매수 환산이 실제 환율만큼 어긋난다.
    """
    global _fx_cache
    now = time.monotonic()
    if _fx_cache is None or now - _fx_cache[1] > _FX_TTL_SEC:
        try:
            from modules.fx import fetch_krw_per_usd  # noqa: PLC0415

            _fx_cache = (fetch_krw_per_usd(fallback=broker.krw_per_usd()), now)
        except Exception as exc:  # noqa: BLE001 — 환율을 못 받아도 멈추지 않는다
            log.warning("환율을 받지 못해 브로커 기본값을 씁니다: %s", exc)
            return
    broker.set_fx(_fx_cache[0])


def broker_module(settings: Settings):
    """stock-analyzer의 가상 브로커를 이 프로세스에서 쓸 수 있게 불러온다.

    부르기 전에 두 가지를 맞춰야 한다. 둘 다 안 맞으면 조용히 틀린다.

    1. **장부 파일 경로.** 브로커는 STATE_FILE을 import 시점에 환경변수로 한 번만
       읽고, 없으면 상대경로 "virtual_portfolio.json"을 쓴다. 비서는
       stock-analyzer 밖에서 도는 별도 프로세스라 그대로 두면 비서 폴더에 빈
       장부가 새로 생긴다. 승인한 주문이 진짜 포트폴리오에 닿지 않는데
       사장님은 "예약했습니다"라는 답만 받는다.
    2. **원/달러.** _inject_fx 참고.
    """
    path = str(settings.stock_analyzer_path)
    if path not in sys.path:
        # 맨 앞이 아니라 뒤에 붙인다. stock-analyzer 루트에는 app.py 같은
        # 흔한 이름의 최상위 모듈이 있어서, 앞에 넣으면 이후 같은 이름의
        # import가 의도치 않게 그쪽으로 잡힌다.
        sys.path.append(path)

    # setdefault다. 테스트는 임시 장부를 가리키도록 미리 넣어두는데,
    # 여기서 덮어쓰면 테스트가 진짜 장부에 주문을 쓰게 된다.
    os.environ.setdefault(
        "VIRTUAL_PORTFOLIO_FILE",
        str(settings.stock_analyzer_path / "virtual_portfolio.json"),
    )

    try:
        from modules import virtual_broker  # noqa: PLC0415
    except ImportError as exc:
        raise TradeError(
            "stock-analyzer의 가상 브로커를 불러오지 못했습니다: "
            f"{exc}. STOCK_ANALYZER_PATH 설정을 확인하세요."
        ) from exc

    _inject_fx(virtual_broker)
    return virtual_broker


class TradeExecutor(Protocol):
    def buy(self, symbol: str, amount_krw: float) -> dict: ...
    def sell(self, symbol: str, qty: int) -> dict: ...


class VirtualBrokerExecutor:
    """stock-analyzer의 가상 브로커를 부른다. 상태 파일에 직접 손대지 않는다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _module(self):
        return broker_module(self._settings)

    def buy(self, symbol: str, amount_krw: float) -> dict:
        """원화 금액으로 매수를 예약한다.

        place_notional_buy 의 금액 단위는 시장을 따른다 — 미국 종목은 달러다.
        비서는 늘 원화로 말하므로 여기서 환산해서 넘긴다. 환산을 빼먹으면
        환율 배수(약 1,400배)만큼 부풀어, 체결 시점에 현금 부족으로 조용히
        폐기된다. 사장님은 "예약했습니다"라는 답만 받는다.
        """
        broker = self._module()
        amount_usd = amount_krw / broker.krw_per_usd()
        return _run_broker(
            broker.place_notional_buy, symbol, amount_usd, market="US"
        )

    def sell(self, symbol: str, qty: int) -> dict:
        return _run_broker(self._module().place_market_sell, symbol, qty)


def _pending_path(settings: Settings) -> Path:
    return settings.assistant_data_dir / "pending_trades.json"


def _load_pending(settings: Settings) -> list[dict]:
    """대기 중인 제안을 읽는다.

    손상 파일 처리는 프로젝트 공통 정책(load_json_list)을 그대로 쓴다 —
    망가진 파일은 조용히 버리지 않고 보관하고 감사 기록에 남긴다.
    """
    return load_json_list(settings, _pending_path(settings))


def _save_pending(settings: Settings, items: list[dict]) -> None:
    """대기 목록을 저장한다. 쓰기 방식은 프로젝트 공통 정책을 따른다."""
    save_json_list(_pending_path(settings), items)


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
            f"제안은 되살려 두었습니다. 다시 시도하려면 '/승인 {request_id}'. "
            "다만 주문이 이미 들어간 뒤에 실패했을 가능성이 있으니, "
            "재승인 전에 보유 현황을 먼저 확인해 주세요."
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
