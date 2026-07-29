"""텔레그램 창구. 허용된 chat_id에만 답한다.

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


class TelegramChannel:
    """텔레그램 메시지 하나를 받아 답 한 줄을 만든다.

    텔레그램 라이브러리 없이 테스트할 수 있도록 handle_text를 순수
    함수처럼 만들고, build_application은 그것을 연결만 한다.
    """

    def __init__(self, settings: Settings, brain, trade_executor=None) -> None:
        self._settings = settings
        self._brain = brain
        self._executor = trade_executor

    def _is_allowed(self, chat_id: int) -> bool:
        return chat_id in self._settings.telegram_allowed_chat_ids

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
            return self._brain.ask(stripped, channel="telegram")
        except Exception as exc:  # noqa: BLE001 — 사용자에게 그대로 알린다
            return f"처리 중 문제가 생겼습니다: {exc}"

    def build_application(self):
        """텔레그램 폴링 애플리케이션을 만든다."""
        import asyncio

        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            ContextTypes,
            MessageHandler,
            filters,
        )

        async def on_message(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            if update.message is None or update.message.text is None:
                return
            reply = await asyncio.to_thread(
                self.handle_text, update.message.chat_id, update.message.text
            )
            if reply is not None:
                await update.message.reply_text(reply)

        application = (
            ApplicationBuilder().token(self._settings.telegram_bot_token).build()
        )
        # 한글 명령(/승인)은 텔레그램이 command 엔티티로 인식하지 않아
        # 일반 텍스트로 들어온다. 두 경로 모두 같은 처리로 보낸다.
        application.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), on_message)
        )
        application.add_handler(MessageHandler(filters.COMMAND, on_message))
        return application
