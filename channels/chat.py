"""창구 공통 로직. 메시지 한 줄을 받아 답 한 줄을 만든다.

텔레그램이든 디스코드든 하는 일은 같다. 다른 것은 "누가 보냈나"를 확인하는
명단과 대화 기록에 남길 이름뿐이라, 그 둘만 name으로 갈라 쓴다.

매매 승인은 여기서 직접 실행한다 — 비서(모델)는 실행 도구를 갖고 있지
않으므로, 사장님이 명령을 쳤을 때 이 계층이 브로커를 부른다.
"""

from __future__ import annotations

from assistant.config import Settings
from tools import virtual_trade

APPROVE_COMMANDS = ("/승인", "/approve")
REJECT_COMMANDS = ("/거절", "/reject")
HELP_COMMANDS = ("/도움", "/help", "/start")

_HELP = """무엇이든 물어보세요.

  가상 브로커 지금 얼마야?
  최근 시그널 5개 보여줘
  엔비디아 관심종목에 넣어줘
  애플에 메모 남겨줘: 가이던스 확인 후 재검토

매매는 두 단계입니다. 제가 제안하면 사장님이 승인해야 나갑니다.
  /승인            — 대기 중인 제안 보기
  /승인 <번호>     — 승인해서 주문 넣기
  /거절 <번호>     — 제안 버리기"""


class ChatChannel:
    """메시지 하나를 받아 답 한 줄을 만든다.

    메신저 라이브러리 없이 테스트할 수 있도록 handle_text를 순수 함수처럼
    만들고, 전송 계층(telegram_bot / discord_bot)은 그것을 연결만 한다.
    """

    def __init__(
        self,
        settings: Settings,
        brain,
        trade_executor=None,
        name: str = "telegram",
    ) -> None:
        self._settings = settings
        self._brain = brain
        self._executor = trade_executor
        self._name = name

    def _is_allowed(self, chat_id: int) -> bool:
        return chat_id in self._settings.allowed_ids(self._name)

    def _pending_summary(self) -> str:
        pending = virtual_trade.list_pending_requests(self._settings)
        if not pending:
            return "승인을 기다리는 매매 제안이 없습니다."
        lines = ["승인 대기 중인 제안:"]
        for item in pending:
            if item["side"] == "buy":
                what = f"{item['symbol']} {item['amount_krw']:,.0f}원어치 매수"
            else:
                what = f"{item['symbol']} {item['qty']}주 매도"
            lines.append(f"  {item['request_id']} — {what}")
        lines.append("승인하려면 '/승인 <번호>', 취소하려면 '/거절 <번호>'")
        return "\n".join(lines)

    def _approve(self, argument: str) -> str:
        if not argument:
            return self._pending_summary()
        try:
            result = virtual_trade.approve_request(
                self._settings, argument, executor=self._executor
            )
        except virtual_trade.TradeError as exc:
            return str(exc)
        return f"{result['summary']}를 예약했습니다. 다음 거래일 시가에 체결됩니다."

    def _reject(self, argument: str) -> str:
        if not argument:
            return self._pending_summary()
        try:
            result = virtual_trade.reject_request(self._settings, argument)
        except virtual_trade.TradeError as exc:
            return str(exc)
        return f"{result['summary']} 제안을 취소했습니다."

    def handle_text(self, chat_id: int, text: str) -> str | None:
        """메시지 하나를 처리한다. 허용되지 않은 사람이면 None."""
        if not self._is_allowed(chat_id):
            return None

        stripped = text.strip()
        head, _, argument = stripped.partition(" ")
        argument = argument.strip()

        if head in HELP_COMMANDS:
            return _HELP
        if head in APPROVE_COMMANDS:
            return self._approve(argument)
        if head in REJECT_COMMANDS:
            return self._reject(argument)

        try:
            return self._brain.ask(stripped, channel=self._name)
        except Exception as exc:  # noqa: BLE001 — 사용자에게 그대로 알린다
            return f"처리 중 문제가 생겼습니다: {exc}"


def split_for_limit(text: str, limit: int) -> list[str]:
    """긴 답을 메신저가 받는 길이로 자른다.

    주식 리포트 한 장은 디스코드 한도(2000자)를 쉽게 넘긴다. 자르지 않으면
    전송 자체가 거절돼 답이 통째로 사라진다. 되도록 줄 경계에서 자른다.
    """
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:
            # 줄바꿈 없이 긴 덩어리는 그냥 자른다.
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]
        else:
            # 경계의 줄바꿈 하나만 먹는다. 빈 줄까지 지우면 표가 무너진다.
            chunks.append(remaining[:cut])
            remaining = remaining[cut + 1:]
    chunks.append(remaining)
    return chunks
