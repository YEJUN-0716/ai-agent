"""실전 러너 — 브리핑 한 장을 `claude -p` 로 받아온다.

데모 러너와 자리가 같다. `pipeline.run(runner=...)` 에 이걸 넣으면 준비된 응답
대신 진짜 클로드가 쓴다. 나머지 순서·화면·리포트는 그대로다.

호출 한 번이 프로세스 하나다. 알고리즘 모드면 13번 뜬다 — 느린 게 정상이다.
급하면 `/floor` 슬래시커맨드(`floor/cli.py`)가 같은 판을 세션 하나로 돈다.

프롬프트 조각(`facts`·`rules`·`shape`)과 응답 검사(`parse_briefing`)는 그 세션
러너와 공유한다. 화면으로 돌리든 세션으로 돌리든 같은 기준을 통과한 것만 리포트가
된다 — 한쪽에만 예외를 뚫으면 두 경로의 판정이 갈린다.

**과금 주의.** `ANTHROPIC_API_KEY` 가 환경에 있으면 구독이 아니라 API 로 돈이
나간다. 확인에 기대지 않고 자식 프로세스 환경에서 매번 벗겨서 넘긴다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from floor.agents import BY_KEY, ROOM_ANALYSTS, ROOM_RESEARCH
from floor.demo import Briefing, Context, Verdict, format_body
from floor.market import Snapshot

# 한 번 부르는 데 이보다 오래 걸리면 뭔가 잘못된 거다.
TIMEOUT_SEC = 240

# 이 둘만 판정을 낸다. 나머지는 브리핑만 쓴다.
VERDICT_AGENTS = ("ACE", "PM")

# 매매 레벨(진입가·손절가·목표가)을 부를 수 있는 방.
#
# 애널리스트 팀과 리서치룸은 관측과 논거를 내는 자리다. 여기서 "손절 3% 이내 롱
# 진입 성립" 같은 문장이 나오면 리포트 한 장에 추천가가 여러 벌 실려, 어느 숫자가
# 실행 대상인지 흐려진다. 리스크 위원회는 이미 나온 계획을 검증·수정하는 자리라
# 레벨을 만지지 않으면 일이 안 되므로 여기 넣지 않는다.
QUIET_ROOMS = (ROOM_ANALYSTS, ROOM_RESEARCH)
PM_STATUSES = ("승인", "수정승인", "기각")

SYSTEM_PROMPT = (
    "너는 트레이딩 플로어의 애널리스트다. 요청받은 역할 한 명만 연기한다. "
    "숫자는 주어진 시세에서만 인용하고 없는 값을 지어내지 않는다. "
    "출력은 JSON 객체 하나뿐이다. 코드펜스·설명·인사말을 붙이지 않는다."
)

_FIELDS = ("observed", "reading", "counter", "trigger")


class RunnerError(RuntimeError):
    """실전 호출이 실패했을 때. 메시지가 그대로 화면에 뜬다."""


def _claude() -> str:
    """윈도우에서는 claude 가 .cmd 라 실행 파일 경로를 찾아 써야 한다."""
    path = shutil.which("claude")
    if path is None:
        raise RunnerError(
            "claude CLI 를 찾지 못했습니다. 설치했는지, PATH 에 있는지 확인하세요."
        )
    return path


def _env() -> dict[str, str]:
    """API 키를 뺀 환경. 이 한 줄이 구독과 종량과금을 가른다."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def facts(snap: Snapshot, ctx: Context) -> str:
    """에이전트가 인용해도 되는 숫자 전부. 여기 없는 값은 지어낸 값이다."""
    unit = "원" if snap.symbol.currency == "KRW" else "달러"
    lines = [
        f"종목: {snap.symbol.label} ({snap.symbol.key}) · 표시통화 {unit}",
        f"현재가: {snap.price} ({snap.change_pct:+.2f}%)",
        f"20봉 고점 {snap.high20} · 저점 {snap.low20} · MA20 {snap.ma20[-1]} "
        f"· MA50 {snap.ma50[-1]}",
        f"판정 기준 시장: {ctx.mode.basis}",
        f"기준 변동성(무기한) {snap.perp_vol_pct}% · 원화 정규장 {snap.krw_vol_pct}%",
        f"공포탐욕 {snap.fear_greed} · 정규장 개장 {'예' if snap.market_open else '아니오'}",
        f"시세 출처: {snap.source}",
    ]
    if snap.fx_krw:
        lines.append(f"환율 {snap.fx_krw}원/달러")
    for row in snap.board_rows:
        mark = " (추정 — 직접 조회 불가)" if row["estimated"] else ""
        lines.append(
            f"무기한 {row['exchange']}: {row['perp_usdt']} USDT · "
            f"KRX 괴리 {row['gap_pct']:+.2f}%{mark}"
        )
    if snap.headlines:
        lines.append("헤드라인:")
        lines.extend(f"  - {head}" for head in snap.headlines)
    else:
        lines.append("헤드라인: 조회 실패 — 뉴스 없이 판단한다.")
    return "\n".join(lines)


def _prior(ctx: Context) -> str:
    if not ctx.prior:
        return "(없음 — 네가 첫 발언이다)"
    return "\n\n".join(f"[{b.agent}] {b.bubble}\n{b.body}" for b in ctx.prior)


def shape(agent: str, ctx: Context) -> str:
    """이 에이전트가 내야 하는 JSON 모양. 판정을 내는 둘만 verdict 를 붙인다."""
    base = (
        '{"bubble": "말풍선 한 줄, 40자 이내", '
        '"observed": "인용한 수치", "reading": "해석", '
        '"counter": "반대 시나리오", "trigger": "판단이 바뀌는 조건"'
    )
    if agent not in VERDICT_AGENTS:
        return base + "}"

    actions = " | ".join(ctx.mode.actions)
    verdict = (
        ', "verdict": {"action": "' + actions + '", '
        '"confidence": 정수 0-100, "entry": 숫자, "stop": 숫자, '
        '"target": 숫자, "size_pct": 숫자, "rationale": "판정 근거 두세 문장"'
    )
    if agent == "PM":
        verdict += ', "pm_status": "' + " | ".join(PM_STATUSES) + '", "pm_comment": "한 줄"'
    return base + verdict + "}}"


def rules(agent: str, ctx: Context) -> list[str]:
    """이 에이전트가 지켜야 할 것. 세션 러너도 같은 목록을 받아 쓴다."""
    out = [
        f"판정 기준 시장은 {ctx.mode.basis} 다. 이 시장의 변동성으로 계산한다.",
    ]
    # 매수·매도를 권하는 건 트레이딩 본부 몫이다. 분석 방에 판정 목록을 쥐여주면
    # 관측 대신 추천을 적어 와, 한 화면에 추천이 여러 개 뜬다.
    if agent in VERDICT_AGENTS:
        out.append(f"이 모드에서 낼 수 있는 판정은 {' / '.join(ctx.mode.actions)} 뿐이다.")
    else:
        out.append(
            "판정은 트레이딩 본부만 낸다. 매수·매도·진입을 권하지 말고 "
            "관측·해석·반대 시나리오·판단이 바뀌는 조건만 적는다."
        )
    if BY_KEY[agent].room in QUIET_ROOMS:
        out.append(
            "진입가·손절가·목표가·비중 같은 매매 레벨은 부르지 않는다. 가격은 "
            "관측과 해석의 근거로만 인용하고, 트리거는 '이러면 진입'이 아니라 "
            "'이러면 내 해석이 뒤집힌다'로 적는다."
        )
    if ctx.mode.key in ("scalp", "attack"):
        out.append(
            "원화 정규장 변동성은 폭락일에 몇 배로 부풀려진다. 그 값으로 손절 폭을 "
            "재면 멀쩡한 셋업이 청산 위험으로 오기각되므로 쓰지 않는다."
        )
    if ctx.mode.key == "attack" and agent in VERDICT_AGENTS:
        out.append("관망은 금지다. 근거가 약해도 방향을 고르고 확신도를 낮게 적는다.")
    if agent in VERDICT_AGENTS:
        # 관망에 0 을 적으면 화면에 "손절 0" 으로 뜬다. 안 들어간다는 뜻이지
        # 손절이 없다는 뜻이 아니므로, 감시 레벨을 적게 하고 비중만 0 으로 둔다.
        out.append(
            "관망 판정이어도 entry·stop·target 에는 0 이 아니라 '들어간다면 여기'인 "
            "감시 레벨을 적는다. 비중(size_pct)만 0 으로 둔다."
        )
    if agent == "GUARD":
        out.append("격리 20배 기준 약 5% 역행이면 청산이다. 손절 폭이 그 안인지 본다.")
    if ctx.turn:
        out.append(f"{ctx.turn}번째 발언이다. 상대 주장을 인용해 직접 반박한다.")
    return out


def _prompt(agent: str, ctx: Context) -> str:
    who = BY_KEY[agent]
    return "\n".join(
        [
            f"역할: {who.name} — {who.role} ({who.key})",
            f"네가 보는 것: {who.watches}",
            f"모드: {ctx.mode.label} — {ctx.mode.note}",
            "",
            "규칙:",
            *(f"- {rule}" for rule in rules(agent, ctx)),
            "",
            "시세:",
            facts(ctx.snapshot, ctx),
            "",
            "앞선 브리핑:",
            _prior(ctx),
            "",
            "다음 JSON 하나만 출력한다:",
            shape(agent, ctx),
        ]
    )


def _extract(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RunnerError(f"JSON 을 찾지 못했습니다: {text[:120]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RunnerError(f"JSON 을 읽지 못했습니다: {exc}") from exc
    if not isinstance(data, dict):
        raise RunnerError("JSON 객체가 아닙니다")
    return data


def _verdict(data: dict, agent: str, ctx: Context) -> Verdict:
    raw = data.get("verdict")
    if not isinstance(raw, dict):
        raise RunnerError(f"{agent} 가 판정(verdict)을 내지 않았습니다")

    action = str(raw.get("action", "")).upper()
    if action not in ctx.mode.actions:
        # 모드가 허용 안 한 판정은 고쳐서 통과시키지 않는다. 그건 판정이 아니다.
        raise RunnerError(
            f"{agent} 판정 {action!r} 은 {ctx.mode.label} 모드에 없습니다 — "
            f"가능한 값: {' / '.join(ctx.mode.actions)}"
        )
    try:
        confidence = int(raw["confidence"])
        numbers = {key: float(raw[key]) for key in ("entry", "stop", "target", "size_pct")}
    except (KeyError, TypeError, ValueError) as exc:
        raise RunnerError(f"{agent} 판정에 숫자가 빠졌습니다: {exc}") from exc
    if not 0 <= confidence <= 100:
        raise RunnerError(f"{agent} 확신도가 0-100 밖입니다: {confidence}")

    status = raw.get("pm_status")
    if agent == "PM" and status not in PM_STATUSES:
        raise RunnerError(f"PM 결재가 {' / '.join(PM_STATUSES)} 가 아닙니다: {status!r}")

    return Verdict(
        action=action,
        confidence=confidence,
        entry=round(numbers["entry"], 2),
        stop=round(numbers["stop"], 2),
        target=round(numbers["target"], 2),
        size_pct=round(numbers["size_pct"], 1),
        rationale=str(raw.get("rationale", "")).strip(),
        pm_status=status if agent == "PM" else None,
        pm_comment=str(raw.get("pm_comment", "")).strip(),
    )


def parse_briefing(data: dict, agent: str, ctx: Context) -> Briefing:
    """JSON 한 덩이를 브리핑으로. 검사에 걸리면 메우지 않고 거절한다.

    `-p` 러너와 `/floor` 세션 러너가 둘 다 이 문을 지난다.
    """
    missing = [key for key in _FIELDS if not str(data.get(key, "")).strip()]
    if missing:
        raise RunnerError(f"{agent} 브리핑에 {' · '.join(missing)} 가 없습니다")
    return Briefing(
        agent=agent,
        bubble=str(data.get("bubble") or "").strip()[:60] or agent,
        body=format_body(*(str(data[key]).strip() for key in _FIELDS)),
        verdict=_verdict(data, agent, ctx) if agent in VERDICT_AGENTS else None,
    )


def _call(prompt: str) -> str:
    command = [
        _claude(),
        "-p",
        "--system-prompt",
        SYSTEM_PROMPT,
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disallowed-tools",
        "Bash",
        "Edit",
        "Write",
        "Task",
    ]
    try:
        done = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SEC,
            env=_env(),
            # 프로젝트 밖에서 돌린다. 안에서 돌리면 이 저장소의 CLAUDE.md 가
            # 13번 다 딸려 들어가 요금만 늘고 페르소나도 흐려진다.
            cwd=tempfile.gettempdir(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"{TIMEOUT_SEC}초 안에 답하지 않았습니다") from exc
    except OSError as exc:
        raise RunnerError(f"claude 를 실행하지 못했습니다: {exc}") from exc

    if done.returncode != 0:
        raise RunnerError(
            f"claude 가 실패했습니다(코드 {done.returncode}): {done.stderr.strip()[:200]}"
        )
    return done.stdout


def claude_runner(agent: str, ctx: Context) -> Briefing:
    """브리핑 한 장. 형식이 깨지면 한 번만 다시 부른다."""
    if agent not in BY_KEY:
        raise RunnerError(f"모르는 에이전트입니다: {agent}")

    prompt = _prompt(agent, ctx)
    for attempt in (1, 2):
        try:
            return parse_briefing(_extract(_call(prompt)), agent, ctx)
        except RunnerError:
            # 형식이 한 번 깨지는 건 흔하다. 두 번째도 깨지면 판을 접는다 —
            # 여기서 대충 메워 넘기면 판정이 아니라 창작이 된다.
            if attempt == 2:
                raise
            prompt = (
                _prompt(agent, ctx)
                + "\n\n앞선 응답이 형식을 어겼다. JSON 객체 하나만 출력한다."
            )
    raise AssertionError("도달할 수 없는 자리")
