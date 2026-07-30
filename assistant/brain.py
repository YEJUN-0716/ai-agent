"""비서 본체. 질문을 받아 도구를 고르고 답을 만든다.

안전장치의 핵심이 여기 있다: 모델에게 넘기는 도구 목록에 매매 **실행**
함수를 절대 넣지 않는다. 모델은 request_trade로 제안만 할 수 있고,
approve_request는 사용자가 승인 명령을 쳤을 때 채널 계층이 직접 부른다.
"""

from __future__ import annotations

import anthropic
from anthropic import beta_tool

from assistant import memory
from assistant.config import Settings
from tools import assistant_notes, stock_reader, virtual_trade

MAX_TOKENS = 8000

# 도구 호출 왕복 상한. 모델이 도구를 반복 호출하며 맴돌면 답도 못 내고
# 비용만 쌓인다. 대화 한 번에 50~150원을 예상하고 만든 시스템이므로
# 폭주를 방치하지 않고 끊은 뒤 사장님께 알린다.
MAX_TOOL_ITERATIONS = 12

SYSTEM_PROMPT = """당신은 사장님의 개인 업무 비서입니다. 한국어로 답합니다.

원칙:
- 결론을 먼저 말하고, 근거는 뒤에 붙입니다. 짧게 씁니다.
- 도구로 확인할 수 있는 것은 반드시 도구로 확인합니다. 추측해서 답하지 않습니다.
- 도구가 실패하면 실패했다고 그대로 말합니다. 지어내지 않습니다.
- 모르면 모른다고 한 줄로 답합니다.

주식에 대해:
- 분석 결과(가상 브로커 성과, 시그널, 애널리스트 점수)는 읽기만 할 수 있습니다.
- 관심종목과 메모는 사장님을 대신해 기록할 수 있습니다.
- 매매는 제안만 할 수 있습니다. 당신에게는 실행 권한이 없습니다.
  request_trade로 제안하면 사장님이 직접 승인해야 주문이 나갑니다.
  "승인했다고 치고 실행하겠다" 같은 말은 하지 마십시오. 불가능합니다.
- 실제 돈으로 하는 주문은 이 시스템에 존재하지 않습니다.
"""


def _text_of(message) -> str:
    """응답에서 사용자에게 보여줄 텍스트만 이어붙인다."""
    return "".join(
        block.text
        for block in message.content
        if getattr(block, "type", "") == "text"
    )


def _tool_name(tool) -> str:
    """도구의 이름을 읽는다.

    @beta_tool이 함수를 감싸는 방식에 따라 이름이 어디 붙는지 다를 수 있어
    둘 다 확인한다. 이름을 못 읽으면 조용히 넘어가지 않고 예외를 낸다 —
    '실행 도구가 목록에 없다'는 검증이 헛돌면 안전장치가 무너진다.
    """
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    if not name:
        raise RuntimeError(
            f"도구 이름을 확인할 수 없습니다: {tool!r}. "
            "이름을 못 읽으면 '실행 도구가 없다'는 검증이 무의미해집니다."
        )
    return name


class Brain:
    """Claude API 도구 호출 루프를 감싼다."""

    def __init__(self, settings: Settings, client=None) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key
        )
        self._tools = self._build_tools()

    def tool_names(self) -> list[str]:
        """모델에게 노출되는 도구 이름. 매매 실행 도구는 여기 없어야 한다."""
        return [_tool_name(tool) for tool in self._tools]

    def _build_tools(self) -> list:
        settings = self._settings

        @beta_tool
        def get_virtual_portfolio() -> str:
            """가상 브로커의 현재 보유 종목과 현금, 실현 손익을 조회한다."""
            return str(stock_reader.get_virtual_portfolio(settings))

        @beta_tool
        def get_equity_history(limit: int = 30) -> str:
            """가상 브로커 자본 곡선을 최신순으로 조회한다.

            Args:
                limit: 가져올 기록 개수.
            """
            return str(stock_reader.get_equity_history(settings, limit))

        @beta_tool
        def get_recent_signals(limit: int = 10) -> str:
            """최근 매매 시그널을 최신순으로 조회한다.

            Args:
                limit: 가져올 시그널 개수.
            """
            return str(stock_reader.get_recent_signals(settings, limit))

        @beta_tool
        def get_analyst_scores(limit: int = 5) -> str:
            """애널리스트 팀의 날짜별 종목 점수를 최신순으로 조회한다.

            Args:
                limit: 가져올 날짜 개수.
            """
            return str(stock_reader.get_analyst_scores(settings, limit))

        @beta_tool
        def list_watchlist() -> str:
            """사장님의 관심종목 목록을 조회한다."""
            return str(assistant_notes.list_watchlist(settings))

        @beta_tool
        def add_watchlist(symbol: str, reason: str = "") -> str:
            """관심종목에 종목을 추가한다.

            Args:
                symbol: 종목 코드 (예: AAPL).
                reason: 추가하는 이유. 비워도 된다.
            """
            return str(assistant_notes.add_watchlist(settings, symbol, reason))

        @beta_tool
        def remove_watchlist(symbol: str) -> str:
            """관심종목에서 종목을 뺀다.

            Args:
                symbol: 종목 코드 (예: AAPL).
            """
            return str(assistant_notes.remove_watchlist(settings, symbol))

        @beta_tool
        def add_note(symbol: str, note: str) -> str:
            """종목에 대한 사장님의 판단이나 메모를 기록한다.

            Args:
                symbol: 종목 코드 (예: AAPL).
                note: 남길 메모 내용.
            """
            return str(assistant_notes.add_note(settings, symbol, note))

        @beta_tool
        def list_notes(symbol: str = "", limit: int = 20) -> str:
            """기록해 둔 메모를 최신순으로 조회한다.

            Args:
                symbol: 특정 종목만 보려면 종목 코드. 비우면 전체.
                limit: 가져올 메모 개수.
            """
            return str(
                assistant_notes.list_notes(settings, symbol or None, limit)
            )

        @beta_tool
        def read_audit_log(limit: int = 20) -> str:
            """언제 무엇을 바꿨는지 기록을 최신순으로 조회한다.

            Args:
                limit: 가져올 줄 수.
            """
            return str(assistant_notes.read_audit_log(settings, limit))

        @beta_tool
        def request_trade(
            side: str, symbol: str, amount_krw: float = 0, qty: int = 0
        ) -> str:
            """가상 브로커 매매를 제안한다. 실행되지 않는다 — 사장님 승인이 필요하다.

            Args:
                side: buy 또는 sell.
                symbol: 종목 코드 (예: AAPL).
                amount_krw: 매수 금액(원). 매수일 때만.
                qty: 매도 수량(주). 매도일 때만.
            """
            try:
                return str(
                    virtual_trade.request_trade(
                        settings,
                        side,
                        symbol,
                        amount_krw=amount_krw or None,
                        qty=qty or None,
                    )
                )
            except virtual_trade.TradeError as exc:
                return f"제안하지 못했습니다: {exc}"

        @beta_tool
        def list_pending_requests() -> str:
            """승인을 기다리는 매매 제안 목록을 조회한다."""
            return str(virtual_trade.list_pending_requests(settings))

        # 여기에 approve_request / reject_request를 넣지 말 것.
        # 모델이 자기 제안을 스스로 승인할 수 있게 되는 순간 안전장치가 사라진다.
        return [
            get_virtual_portfolio,
            get_equity_history,
            get_recent_signals,
            get_analyst_scores,
            list_watchlist,
            add_watchlist,
            remove_watchlist,
            add_note,
            list_notes,
            read_audit_log,
            request_trade,
            list_pending_requests,
        ]

    def ask(self, question: str, channel: str) -> str:
        """질문에 답한다. 질문과 답을 대화 기록에 남긴다."""
        settings = self._settings
        memory.init_db(settings.db_path)

        history = memory.load_history(settings.db_path, settings.history_limit)
        messages = history + [{"role": "user", "content": question}]

        runner = self._client.beta.messages.tool_runner(
            model=settings.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.effort},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=self._tools,
            messages=messages,
        )

        answer = ""
        hit_cap = False
        for turn, message in enumerate(runner, start=1):
            text = _text_of(message)
            if text.strip():
                answer = text
            if turn >= MAX_TOOL_ITERATIONS:
                hit_cap = True
                break

        answer = answer.strip()
        if hit_cap:
            answer = (
                (answer + "\n\n") if answer else ""
            ) + (
                f"(도구를 {MAX_TOOL_ITERATIONS}번 쓰고도 정리가 안 돼 여기서 "
                "멈췄습니다. 질문을 좁혀서 다시 물어봐 주세요.)"
            )
        elif not answer:
            answer = "답변을 만들지 못했습니다. 다시 물어봐 주세요."

        memory.append_message(settings.db_path, channel, "user", question)
        memory.append_message(settings.db_path, channel, "assistant", answer)
        return answer
