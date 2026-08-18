"""텔레그램 전송 계층. 답을 만드는 일은 ChatChannel이 한다."""

from __future__ import annotations

from channels.chat import ChatChannel

# 텔레그램 한 메시지 한도는 4096자다.
TELEGRAM_LIMIT = 4096


def build_application(channel: ChatChannel, token: str):
    """텔레그램 폴링 애플리케이션을 만든다."""
    import asyncio

    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        ContextTypes,
        MessageHandler,
        filters,
    )

    from channels.chat import split_for_limit

    async def on_message(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if update.message is None or update.message.text is None:
            return
        reply = await asyncio.to_thread(
            channel.handle_text, update.message.chat_id, update.message.text
        )
        if reply is None:
            return
        for part in split_for_limit(reply, TELEGRAM_LIMIT):
            await update.message.reply_text(part)

    application = ApplicationBuilder().token(token).build()
    # 한글 명령(/승인)은 텔레그램이 command 엔티티로 인식하지 않아
    # 일반 텍스트로 들어온다. 두 경로 모두 같은 처리로 보낸다.
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), on_message)
    )
    application.add_handler(MessageHandler(filters.COMMAND, on_message))
    return application
