"""업무 비서 실행 진입점. 텔레그램 폴링과 웹 서버를 함께 띄운다.

    python server.py

사용법은 workflows/ai-assistant.md 를 보세요.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from assistant.brain import Brain
from assistant.config import ConfigError, Settings, load_settings
from assistant.memory import init_db
from channels import discord_bot, telegram_bot
from channels.chat import ChatChannel
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


async def _run_telegram(channel: ChatChannel, token: str) -> None:
    application = telegram_bot.build_application(channel, token)
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def _supervise(name: str, coro) -> None:
    """창구 하나를 지켜본다. 멈추면 그 자리에서 이유를 남긴다.

    창구는 정상이면 끝나지 않는다. 따라서 여기 도달했다는 것 자체가 문제다.
    """
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 어떤 이유든 사장님께 알린다
        log.error("%s가 멈췄습니다: %s", name, exc)
    else:
        log.error("%s가 예기치 않게 종료됐습니다.", name)


async def _main() -> None:
    settings = load_settings()
    init_db(settings.db_path)
    brain = Brain(settings)
    telegram = ChatChannel(settings, brain, name="telegram")

    log.info("웹 채팅: http://%s:%s", settings.web_host, settings.web_port)
    log.info(
        "텔레그램 폴링 시작 (허용 chat_id: %s)",
        ", ".join(str(i) for i in sorted(settings.telegram_allowed_chat_ids)),
    )

    # 한쪽이 죽어도 다른 쪽은 계속 돈다. 다만 죽은 사실은 즉시 알려야 한다 —
    # gather가 끝난 뒤에 로그를 남기면, 텔레그램이 영원히 대기하는 구조라
    # 그 로그는 영원히 출력되지 않는다.
    tasks = [
        _supervise("웹 서버", _run_web(settings, brain)),
        _supervise(
            "텔레그램", _run_telegram(telegram, settings.telegram_bot_token)
        ),
    ]

    if settings.discord_bot_token:
        discord_channel = ChatChannel(settings, brain, name="discord")
        log.info(
            "디스코드 접속 (허용 user_id: %s)",
            ", ".join(str(i) for i in sorted(settings.discord_allowed_user_ids)),
        )
        tasks.append(
            _supervise(
                "디스코드",
                discord_bot.run(discord_channel, settings.discord_bot_token),
            )
        )

    await asyncio.gather(*tasks)


def _force_utf8_console() -> None:
    """콘솔 출력을 UTF-8로 맞춘다. 실패해도 글자만 깨질 뿐 죽지 않게 한다.

    한국어 윈도우의 기본 콘솔은 cp949라, 로그에 흔한 '—'나 이모지 한 글자가
    UnicodeEncodeError를 낸다. 로그 한 줄 때문에 비서가 멈추면 안 된다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main() -> None:
    _force_utf8_console()
    try:
        asyncio.run(_main())
    except ConfigError as exc:
        raise SystemExit(f"설정 오류: {exc}") from exc
    except KeyboardInterrupt:
        log.info("종료합니다.")


if __name__ == "__main__":
    main()
