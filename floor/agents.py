"""에이전트 13명과 모드별 진행 순서. 데이터만 두고, 실행은 pipeline.py가 한다.

여기 숫자를 고치면 클로드 호출 횟수가 그대로 바뀐다. 모드마다 몇 번 부르는지는
가이드에 적힌 값(algo 13 / scalp·attack 5)이고, 테스트가 그 값을 잠근다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 방 4개. 화면 배치와 1:1로 대응한다.
ROOM_ANALYSTS = "analysts"  # 좌상단 애널리스트 팀
ROOM_RESEARCH = "research"  # 우상단 리서치룸
ROOM_RISK = "risk"  # 좌하단 리스크 위원회 ↔ 스캘핑 데스크
ROOM_DESK = "desk"  # 우하단 트레이딩 본부

# 스캘핑·공격 모드의 판정 기준 시장.
#
# 정규장 원화 차트를 기준으로 삼으면 폭락일에 변동성이 몇 배로 부풀려져
# (실측 예: KRX 3.05% vs 무기한 1.20%) 멀쩡한 셋업이 "청산 위험"으로 오기각된다.
# 취향이 아니라 실측으로 나온 규칙이라 화면과 리포트에 항상 같이 적는다.
BASIS_PERP = "24시간 USDT 무기한"
BASIS_KRW = "KRX 정규장 원화"


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    role: str
    room: str
    watches: str


AGENTS: tuple[Agent, ...] = (
    Agent(
        "TARO",
        "타로",
        "기술적 분석",
        ROOM_ANALYSTS,
        "이동평균·RSI·MACD·ATR·변동성, 캔들 흐름",
    ),
    Agent(
        "DIANA",
        "다이애나",
        "기본적 분석",
        ROOM_ANALYSTS,
        "시가총액·거래대금·공급량·52주 고저",
    ),
    Agent(
        "NOVA",
        "노바",
        "뉴스 분석",
        ROOM_ANALYSTS,
        "최신 헤드라인 (한국 종목은 한국어 뉴스)",
    ),
    Agent(
        "VIBE",
        "바이브",
        "센티먼트",
        ROOM_ANALYSTS,
        "공포·탐욕 지수, 헤드라인 전반의 심리",
    ),
    Agent(
        "BULL",
        "불",
        "매수 논거",
        ROOM_RESEARCH,
        "애널리스트 리포트에서 상승 근거를 뽑아 주장",
    ),
    Agent(
        "BEAR",
        "베어",
        "매도 논거",
        ROOM_RESEARCH,
        "BULL의 주장을 직접 반박하며 하락 근거 제시",
    ),
    Agent(
        "BLITZ",
        "블리츠",
        "스캘퍼",
        ROOM_RISK,
        "15분봉 단타 — 진입 트리거·목표·무효화 레벨",
    ),
    Agent(
        "GUARD",
        "가드",
        "리스크 관리",
        ROOM_RISK,
        "20배 청산 버퍼 검증, 권장 비중, 진입 금지 조건",
    ),
    Agent(
        "RISKY",
        "리스키",
        "공격적 리스크",
        ROOM_RISK,
        "기회비용 관점 — 계획이 과도하게 방어적인지",
    ),
    Agent(
        "SAFE",
        "세이프",
        "보수적 리스크",
        ROOM_RISK,
        "최악 시나리오·꼬리위험 — 비중 축소·손절 상향 요구",
    ),
    Agent(
        "NEUTRAL",
        "뉴트럴",
        "중립적 리스크",
        ROOM_RISK,
        "양측 주장을 가르고 조건부 승인안 제시",
    ),
    Agent(
        "ACE",
        "에이스",
        "수석 트레이더",
        ROOM_DESK,
        "전부 종합해 판정 (확신도·진입·손절·목표)",
    ),
    Agent(
        "PM",
        "피엠",
        "포트폴리오 매니저",
        ROOM_DESK,
        "ACE 계획을 승인·수정·기각하는 최종 관문",
    ),
)

BY_KEY: dict[str, Agent] = {a.key: a for a in AGENTS}


@dataclass(frozen=True)
class Step:
    """파이프라인 한 구간.

    parallel   — 동시에 말한다 (화면에선 말풍선이 같이 뜬다)
    sequential — 앞사람 발언을 읽고 차례로 말한다
    debate     — agents 를 번갈아 turns 번. 한 턴이 호출 한 번이다.
    """

    kind: str
    agents: tuple[str, ...]
    label: str
    turns: int = 0

    @property
    def calls(self) -> int:
        return self.turns if self.kind == "debate" else len(self.agents)

    def order(self) -> tuple[tuple[str, int], ...]:
        """(에이전트 키, 턴 번호) 순서. 턴 번호는 토론이 아니면 0."""
        if self.kind != "debate":
            return tuple((key, 0) for key in self.agents)
        return tuple(
            (self.agents[i % len(self.agents)], i + 1) for i in range(self.turns)
        )


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    steps: tuple[Step, ...]
    actions: tuple[str, ...]
    basis: str
    note: str

    @property
    def calls(self) -> int:
        return sum(step.calls for step in self.steps)

    @property
    def roster(self) -> tuple[str, ...]:
        """이번 모드에서 일하는 에이전트. 나머지는 화면에서 흐릿하게 쉰다."""
        seen: list[str] = []
        for step in self.steps:
            for key in step.agents:
                if key not in seen:
                    seen.append(key)
        return tuple(seen)


ALGO = Mode(
    key="algo",
    label="알고리즘",
    steps=(
        Step("parallel", ("TARO", "DIANA", "NOVA", "VIBE"), "애널리스트 팀"),
        Step("debate", ("BULL", "BEAR"), "리서치 토론", turns=4),
        Step("sequential", ("ACE",), "1차 판정"),
        Step("sequential", ("RISKY", "SAFE", "NEUTRAL"), "리스크 위원회"),
        Step("sequential", ("PM",), "최종 승인"),
    ),
    actions=("BUY", "SELL", "HOLD"),
    basis=BASIS_KRW,
    note="논문 구조 전체. 스윙·중장기 관점. 관망(HOLD) 가능.",
)

SCALP = Mode(
    key="scalp",
    label="스캘핑 20x",
    steps=(
        Step("parallel", ("TARO", "VIBE"), "애널리스트 팀"),
        Step("sequential", ("BLITZ",), "스캘핑 데스크"),
        Step("sequential", ("GUARD",), "청산 버퍼 검증"),
        Step("sequential", ("ACE",), "최종 판정"),
    ),
    actions=("LONG", "SHORT", "PASS"),
    basis=BASIS_PERP,
    note="24시간 USDT 기준 단타. 20배 청산 검증. 관망(PASS) 가능.",
)

ATTACK = Mode(
    key="attack",
    label="공격",
    steps=SCALP.steps,
    actions=("LONG", "SHORT"),
    basis=BASIS_PERP,
    # 확신도 50%대가 나오는 건 근거가 충분해서가 아니라 이 규칙 때문이다.
    # 판정이 아니라 연출이라는 걸 화면에 그대로 띄운다.
    note="관망 금지 — 반드시 방향을 고른다. 판정이 아니라 연출이며 시연·영상용.",
)

MODES: dict[str, Mode] = {m.key: m for m in (ALGO, SCALP, ATTACK)}

# 사장님이 한국어로 부르는 이름도 받는다.
MODE_ALIASES = {"알고리즘": "algo", "스캘핑": "scalp", "공격": "attack"}


class UnknownMode(ValueError):
    """모드 이름을 못 알아들었을 때. 메시지를 그대로 사용자에게 보여준다."""


def resolve_mode(raw: str | None) -> Mode:
    key = (raw or "algo").strip().lower()
    key = MODE_ALIASES.get(key, key)
    if key not in MODES:
        known = " · ".join(MODES)
        raise UnknownMode(f"모르는 모드입니다: {raw!r} — 쓸 수 있는 값: {known}")
    return MODES[key]
