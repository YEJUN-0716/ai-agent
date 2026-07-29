"""업무 비서 실행 진입점. 텔레그램 폴링과 웹 서버를 함께 띄운다.

    python server.py

사용법은 workflows/ai-assistant.md 를 보세요.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from assistant.brain import Brain
from assistant.config import ConfigError, Settings, load_settings
from assistant.memory import init_db
from channels.telegram_bot import TelegramChannel
from channels.web import create_app

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("assistant")


async def _run_web(settings: Settings, brain: Brain) -> None:
    config = uvicorn.Config(
        create_app(settings, brain),
        host=settings.web_host,
        port=settings.web_port,
        log_level="warning",
    )
    await uvicorn.Server(config).serve()


async def _run_telegram(channel: TelegramChannel) -> None:
    application = channel.build_application()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def _main() -> None:
    settings = load_settings()
    init_db(settings.db_path)
    brain = Brain(settings)
    channel = TelegramChannel(settings, brain)

    log.info("웹 채팅: http://%s:%s", settings.web_host, settings.web_port)
    log.info(
        "텔레그램 폴링 시작 (허용 chat_id: %s)",
        ", ".join(str(i) for i in sorted(settings.telegram_allowed_chat_ids)),
    )

    # 한쪽이 죽어도 다른 쪽은 계속 돈다. 어느 쪽이 왜 죽었는지는 남긴다.
    results = await asyncio.gather(
        _run_web(settings, brain),
        _run_telegram(channel),
        return_exceptions=True,
    )
    for name, result in zip(("웹 서버", "텔레그램"), results):
        if isinstance(result, BaseException):
            log.error("%s가 멈췄습니다: %s", name, result)


def main() -> None:
    try:
        asyncio.run(_main())
    except ConfigError as exc:
        raise SystemExit(f"설정 오류: {exc}") from exc
    except KeyboardInterrupt:
        log.info("종료합니다.")


if __name__ == "__main__":
    main()
