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
from tools import (
    assistant_notes,
    obsidian_search,
    stock_reader,
    study_materials,
    study_reader,
    virtual_trade,
)

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
- 숫자를 보고할 때는 그 데이터가 **언제 것인지** 함께 말합니다. 도구가
  as_of를 주면 그 시점을, sync_note가 있으면 최신이 아닐 수 있다는 사실을
  반드시 전합니다. 지난 숫자를 현재처럼 말하면 안 됩니다.
- 관심종목과 메모는 사장님을 대신해 기록할 수 있습니다.
- **도구 이름·필드명을 사장님께 말하지 마십시오.** get_virtual_portfolio,
  pulled, as_of 같은 내부 용어 대신 뜻을 우리말로 풀어 씁니다.
  ("pulled: false" → "최신 데이터를 아직 못 받아왔습니다")
- **'대기 중'은 두 종류입니다. 섞지 마십시오.**
  (a) 가상 브로커가 이미 걸어둔 예약 — 다음 거래일 시가에 **자동 체결**됩니다.
      사장님이 하실 일이 없습니다. "브로커에서 진행하라"고 하지 마십시오.
  (b) 당신이 request_trade로 올린 제안 — 사장님의 승인 명령이 있어야 나갑니다.
  (a)를 (b)처럼 말하면 사장님이 하지 않아도 될 일을 찾게 됩니다.
- 매매는 제안만 할 수 있습니다. 당신에게는 실행 권한이 없습니다.
  request_trade로 제안하면 사장님이 직접 승인해야 주문이 나갑니다.
  "승인했다고 치고 실행하겠다" 같은 말은 하지 마십시오. 불가능합니다.
- 실제 돈으로 하는 주문은 이 시스템에 존재하지 않습니다.

학업 자료에 대해:
- 자료를 정리하려면 먼저 list_new_materials로 무엇이 기다리는지 봅니다.
- **정리는 비용이 듭니다(자료 1건에 수천 원).** 사장님이 정리를 요청했을 때만
  summarize_new_material을 씁니다. 물어보지 않았는데 미리 정리하지 마십시오.
- 질문에 답할 때는 **먼저 list_materials와 볼트 검색으로 답해 보십시오.**
  거기 없는 세부 내용일 때만 ask_material로 원본을 펼칩니다.
- 원본 PDF는 지우지 않습니다. 사장님이 직접 정리하십니다.

옵시디언 노트에 대해:
- search_notes(검색)와 read_note(읽기)는 **공짜입니다.** 이 PC의 파일을 읽을 뿐입니다.
  사장님이 예전에 적어둔 것이나 정리해 둔 자료의 내용은 여기서 먼저 찾으십시오.
  비용이 드는 ask_material은 볼트에서 못 찾았을 때만 씁니다.
- 노트는 읽기만 합니다. 고치거나 지우는 권한은 없습니다.
- 검색해서 안 나오면 "볼트에 없다"고 말합니다. 노트에 없는 내용을 지어내지 마십시오.
- 답할 때 어느 노트에서 봤는지 노트 제목을 함께 알려주십시오.
"""


class BrainError(RuntimeError):
    """비서가 답을 만들지 못했을 때. 메시지는 사장님께 그대로 보여준다."""


def _explain(exc: Exception) -> str:
    """API 오류를 사장님이 읽고 바로 조치할 수 있는 말로 바꾼다.

    원문은 영어 개발자용 메시지라 그대로 보여주면 무슨 일인지 알 수 없다.
    무엇을 어디서 해야 하는지까지 적는다.
    """
    text = str(exc)

    if "credit balance is too low" in text:
        return (
            "Claude API 크레딧이 떨어졌습니다. "
            "console.anthropic.com → Plans & Billing에서 충전해 주세요. "
            "(claude.ai 구독료와는 별개의 지갑입니다.)"
        )
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "API 키가 거절당했습니다. .env의 ANTHROPIC_API_KEY를 확인해 주세요. "
            "console.anthropic.com → API keys에서 새로 만들 수 있습니다."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return (
            "이 API 키로는 접근할 수 없습니다. "
            "console.anthropic.com에서 키의 권한과 사용 한도를 확인해 주세요."
        )
    if isinstance(exc, anthropic.RateLimitError):
        return "요청이 잠시 몰렸습니다. 30초쯤 뒤에 다시 물어봐 주세요."
    if isinstance(exc, anthropic.APIConnectionError):
        return "앤트로픽 서버에 접속하지 못했습니다. 인터넷 연결을 확인해 주세요."
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return "앤트로픽 서버가 불안정합니다. 잠시 뒤 다시 물어봐 주세요."

    # 알려진 유형이 아니면 원문을 남긴다. 지어내는 것보다 낫다.
    return f"예상치 못한 문제입니다: {text}"


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
        def get_data_freshness() -> str:
            """주식 데이터가 언제 것인지, 최신화에 문제가 없는지 확인한다."""
            return str(stock_reader.get_data_freshness(settings))

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
        def list_new_materials() -> str:
            """정리를 기다리는 학업 자료(PDF) 목록을 확인한다."""
            return str(study_materials.list_new(settings))

        @beta_tool
        def summarize_new_material(filename: str) -> str:
            """PDF 한 개를 읽고 요약해 옵시디언 볼트에 노트로 저장한다.

            자료를 읽는 데 비용이 든다. 사장님이 정리를 요청했을 때만 쓴다.

            Args:
                filename: 자료넣는곳에 있는 파일 이름 (예: 논문.pdf).
            """
            try:
                summary = study_reader.summarize_pdf(
                    settings, settings.study_inbox / filename
                )
                saved = study_materials.save_material(
                    settings, filename,
                    summary["title"], summary["one_line"], summary["note_body"],
                )
                return str({**saved, "title": summary["title"]})
            except study_materials.StudyError as exc:
                return f"정리하지 못했습니다: {exc}"

        @beta_tool
        def list_materials() -> str:
            """정리해 둔 학업 자료 목록을 조회한다. 제목과 한 줄 요약이 나온다."""
            return str(study_materials.list_materials(settings))

        @beta_tool
        def ask_material(material_id: str, question: str) -> str:
            """보관된 원본 PDF를 펼쳐 세부 질문에 답한다.

            요약 노트로 답할 수 없을 때만 쓴다 — 원본을 다시 읽으므로 비용이 든다.

            Args:
                material_id: list_materials로 확인한 자료 번호.
                question: 원본에서 확인할 내용.
            """
            try:
                path = study_materials.material_path(settings, material_id)
                return study_reader.ask_pdf(settings, path, question)
            except study_materials.StudyError as exc:
                return f"확인하지 못했습니다: {exc}"

        @beta_tool
        def search_notes(query: str, limit: int = 5) -> str:
            """옵시디언 볼트의 노트를 검색한다. 공짜다 — 여기부터 뒤진다.

            사장님이 직접 쓴 노트와 정리해 둔 자료 요약이 모두 들어 있다.

            Args:
                query: 찾을 단어. 여러 개면 띄어쓰기로 구분한다.
                limit: 가져올 노트 개수.
            """
            try:
                return str(obsidian_search.search_notes(settings, query, limit))
            except obsidian_search.SearchError as exc:
                return f"검색하지 못했습니다: {exc}"

        @beta_tool
        def read_note(note_path: str) -> str:
            """볼트의 노트 하나를 통째로 읽는다. 이것도 공짜다.

            Args:
                note_path: search_notes가 알려준 노트 경로.
            """
            try:
                return str(obsidian_search.read_note(settings, note_path))
            except obsidian_search.SearchError as exc:
                return f"읽지 못했습니다: {exc}"

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
            get_data_freshness,
            get_equity_history,
            get_recent_signals,
            get_analyst_scores,
            list_watchlist,
            add_watchlist,
            remove_watchlist,
            add_note,
            list_notes,
            read_audit_log,
            list_new_materials,
            summarize_new_material,
            list_materials,
            ask_material,
            search_notes,
            read_note,
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
        try:
            for turn, message in enumerate(runner, start=1):
                text = _text_of(message)
                if text.strip():
                    answer = text
                if turn >= MAX_TOOL_ITERATIONS:
                    hit_cap = True
                    break
        except Exception as exc:  # noqa: BLE001 — 사장님이 조치할 수 있게 옮긴다
            raise BrainError(_explain(exc)) from exc

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

        # 질문과 답은 반드시 함께 남긴다. 하나만 남으면 다음 대화의 기록이
        # assistant로 시작해 API가 거절한다 — memory.append_exchange 참고.
        memory.append_exchange(settings.db_path, channel, question, answer)
        return answer
