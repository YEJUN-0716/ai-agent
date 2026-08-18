"""디스코드 전송 계층. 답을 만드는 일은 ChatChannel이 한다.

명단은 보낸 사람의 유저 ID로 본다. 그래서 개인 DM이든 서버 채널이든
사장님이 말을 걸면 답하고, 다른 사람이 말하면 조용히 넘어간다.
"""

from __future__ import annotations

import asyncio

from channels.chat import ChatChannel, split_for_limit

# 디스코드 한 메시지 한도는 2000자다.
DISCORD_LIMIT = 2000


async def run(channel: ChatChannel, token: str) -> None:
    """디스코드에 접속해 메시지를 받는다. 정상이면 끝나지 않는다."""
    import discord

    intents = discord.Intents.default()
    # 메시지 내용을 읽으려면 개발자 포털에서 MESSAGE CONTENT INTENT를
    # 켜야 한다. 꺼져 있으면 접속은 되는데 내용이 빈 문자열로 온다.
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_message(message) -> None:
        if message.author.id == client.user.id or not message.content:
            return
        reply = await asyncio.to_thread(
            channel.handle_text, message.author.id, message.content
        )
        if reply is None:
            return
        for part in split_for_limit(reply, DISCORD_LIMIT):
            await message.channel.send(part)

    await client.start(token)
