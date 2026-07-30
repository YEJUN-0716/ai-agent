"""내 텔레그램 chat_id를 찾아준다.

브라우저로 getUpdates 주소를 직접 여는 대신 이걸 쓴다.

사용:
    python tools/telegram_chat_id.py

.env의 TELEGRAM_BOT_TOKEN을 읽어 쓴다. 토큰은 화면에 찍지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_SECONDS = 15


def _call(token: str, method: str) -> dict:
    """텔레그램 API를 부른다. 오류 메시지에 토큰이 섞이지 않게 감싼다."""
    url = API.format(token=token, method=method)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SystemExit(
                "봇 토큰이 잘못됐습니다. .env의 TELEGRAM_BOT_TOKEN을 확인하세요.\n"
                "@BotFather에게 /mybots → 봇 선택 → API Token 으로 다시 볼 수 있습니다."
            ) from exc
        raise SystemExit(
            f"텔레그램 서버가 거절했습니다 (HTTP {exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"텔레그램에 접속하지 못했습니다: {exc.reason}. 인터넷 연결을 확인하세요."
        ) from exc


def find_chat_ids(token: str) -> list[dict]:
    """최근 메시지를 보낸 사람들의 chat_id를 중복 없이 모은다."""
    data = _call(token, "getUpdates")
    if not data.get("ok"):
        raise SystemExit(
            f"텔레그램이 오류를 돌려줬습니다: {data.get('description')}"
        )

    found: dict[int, dict] = {}
    for update in data.get("result", []):
        # 일반 메시지든 수정된 메시지든 채널 글이든 chat 정보는 같은 자리에 있다.
        for key in ("message", "edited_message", "channel_post"):
            chat = update.get(key, {}).get("chat")
            if not chat:
                continue
            found[chat["id"]] = {
                "chat_id": chat["id"],
                "name": chat.get("first_name") or chat.get("title") or "(이름 없음)",
                "type": chat.get("type", "?"),
            }
    return list(found.values())


def main() -> None:
    # 윈도우 기본 콘솔은 cp949라 한글 안내문이 깨진다. UTF-8로 맞춘다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass  # 리다이렉트 등으로 못 바꿔도 동작 자체는 문제없다.

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            ".env의 TELEGRAM_BOT_TOKEN이 비어 있습니다.\n"
            "텔레그램에서 @BotFather → /newbot 으로 봇을 만들고 토큰을 넣으세요."
        )

    # 웹훅이 걸려 있으면 getUpdates가 항상 비어 있다. 가장 흔한 원인이다.
    hook = _call(token, "getWebhookInfo").get("result", {})
    if hook.get("url"):
        raise SystemExit(
            "이 봇에 웹훅이 걸려 있어 메시지를 가져올 수 없습니다.\n"
            "브라우저에서 아래 주소를 열어 웹훅을 지운 뒤 다시 실행하세요:\n"
            "  https://api.telegram.org/bot<토큰>/deleteWebhook"
        )

    chats = find_chat_ids(token)
    if not chats:
        raise SystemExit(
            "아직 받은 메시지가 없습니다.\n"
            "1. 텔레그램에서 방금 만든 봇을 찾아 대화를 엽니다\n"
            "2. 아무 말이나 보냅니다 (예: 안녕)\n"
            "3. 이 명령을 다시 실행합니다"
        )

    print("찾았습니다. 아래 숫자를 .env의 TELEGRAM_ALLOWED_CHAT_IDS= 뒤에 넣으세요.\n")
    for chat in chats:
        print(f"  {chat['chat_id']}   <- {chat['name']} ({chat['type']})")
    if len(chats) > 1:
        print("\n여러 개면 사장님 본인 것만 넣으세요. 쉼표로 여러 개도 됩니다.")


if __name__ == "__main__":
    main()
