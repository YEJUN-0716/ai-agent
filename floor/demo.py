"""데모 모드 — 클로드를 부르지 않고 전 과정을 재생한다.

이게 개발 수단이다. 화면·연출·리포트 저장까지 전부 여기서 검증하고,
클로드 호출은 2단계에서 이 러너를 갈아끼우기만 하면 된다. 구독 사용량을
쓰지 않으므로 UI를 몇 번을 돌려도 공짜다.

브리핑 본문은 가이드가 정한 네 토막을 지킨다:
    관찰한 수치 인용 → 해석 → 반대 시나리오 → 판단이 바뀌는 트리거
"""

from __future__ import annotations

from dataclasses import dataclass

from floor.agents import Mode
from floor.market import KIND_KR, Snapshot

# 격리 20배는 약 5% 역행이면 증거금이 전액 청산된다. GUARD 는 손절 폭이
# 이 버퍼 안에 들어오는지만 본다 — 빈말이 아니라 산수다.
LIQUIDATION_PCT = 5.0


@dataclass(frozen=True)
class Verdict:
    action: str
    confidence: int
    entry: float
    stop: float
    target: float
    size_pct: float
    rationale: str
    pm_status: str | None = None
    pm_comment: str = ""


@dataclass(frozen=True)
class Briefing:
    agent: str
    bubble: str
    body: str
    verdict: Verdict | None = None


@dataclass(frozen=True)
class Context:
    """러너가 브리핑 하나를 쓰는 데 필요한 전부."""

    snapshot: Snapshot
    mode: Mode
    turn: int
    prior: tuple[Briefing, ...]

    def find(self, agent: str) -> Briefing | None:
        for item in reversed(self.prior):
            if item.agent == agent:
                return item
        return None


def format_body(observed: str, reading: str, counter: str, trigger: str) -> str:
    """브리핑 본문 네 토막. 데모와 실전이 같은 모양이어야 리포트가 한 종류로 남는다."""
    return (
        f"**관찰** {observed}\n\n"
        f"**해석** {reading}\n\n"
        f"**반대 시나리오** {counter}\n\n"
        f"**판단이 바뀌는 트리거** {trigger}"
    )


def _fmt(value: float, snap: Snapshot) -> str:
    if snap.symbol.currency == "KRW":
        return f"{value:,.0f}원"
    return f"${value:,.2f}"


def _bias(snap: Snapshot) -> int:
    """+1 상방 / -1 하방. 차트와 어긋나지 않게 이동평균으로 정한다."""
    ma = snap.ma20[-1]
    return 1 if ma is not None and snap.price >= ma else -1


def _vol(snap: Snapshot, mode: Mode) -> float:
    """판정 기준 시장의 변동성.

    스캘핑·공격은 원화 차트가 아니라 무기한 차트를 본다. 정규장 폭락일에
    원화 변동성이 부풀려져 멀쩡한 셋업이 청산 위험으로 오기각되기 때문이다.
    """
    if mode.key in ("scalp", "attack"):
        return snap.perp_vol_pct
    return snap.krw_vol_pct if snap.symbol.kind == KIND_KR else snap.perp_vol_pct


def _plan(snap: Snapshot, mode: Mode) -> tuple[float, float, float]:
    """진입·손절·목표. 손절은 기준 변동성의 1.2배, 목표는 2.4배(=2R)."""
    direction = _bias(snap)
    vol = _vol(snap, mode)
    entry = snap.price
    stop = entry * (1 - direction * vol * 1.2 / 100)
    target = entry * (1 + direction * vol * 2.4 / 100)
    return round(entry, 2), round(stop, 2), round(target, 2)


def _action(snap: Snapshot, mode: Mode) -> tuple[str, int]:
    """모드가 허용한 판정 중 하나와 확신도."""
    up = _bias(snap) > 0
    strength = abs(snap.change_pct) + snap.fear_greed / 20

    if mode.key == "attack":
        # 근거가 부족해도 방향을 강제로 고르게 만든 모드다. 그래서 확신도가
        # 50%대에 머문다 — 근거가 충분해서 나온 숫자가 아니다.
        return ("LONG" if up else "SHORT", 50 + int(strength) % 9)

    confident = strength > 3.2
    if mode.key == "scalp":
        if not confident:
            return "PASS", 44 + int(strength * 3) % 8
        return ("LONG" if up else "SHORT", 58 + int(strength * 4) % 18)

    if not confident:
        return "HOLD", 46 + int(strength * 3) % 10
    return ("BUY" if up else "SELL", 60 + int(strength * 4) % 21)


def demo_runner(agent: str, ctx: Context) -> Briefing:
    """준비된 응답 하나를 돌려준다. 클로드를 부르지 않는다."""
    writer = _WRITERS.get(agent)
    if writer is None:
        raise KeyError(f"데모 응답이 없는 에이전트입니다: {agent}")
    return writer(ctx)


def _taro(ctx: Context) -> Briefing:
    s, vol = ctx.snapshot, _vol(ctx.snapshot, ctx.mode)
    up = _bias(s) > 0
    ma20 = s.ma20[-1] or s.price
    side = "위" if up else "아래"
    span = max(s.high20 - s.low20, 1e-9)
    return Briefing(
        "TARO",
        f"MA20 {side}, 기준 변동성 {vol:.2f}%",
        format_body(
            f"종가 {_fmt(s.price, s)}, MA20 {_fmt(ma20, s)}, 20봉 고 {_fmt(s.high20, s)} "
            f"저 {_fmt(s.low20, s)}. {ctx.mode.basis} 기준 변동성 {vol:.2f}%.",
            f"가격이 MA20 {side}에 있고 20봉 레인지의 "
            f"{(s.price - s.low20) / span * 100:.0f}% 지점이다. "
            f"{'추세 추종' if up else '되돌림 경계'} 구간으로 읽는다.",
            f"레인지 상·하단을 못 뚫으면 {_fmt(s.low20, s)}–{_fmt(s.high20, s)} 박스가 "
            "이어진다. 그 경우 방향 베팅은 수수료만 낸다.",
            f"종가가 {_fmt(s.high20 if up else s.low20, s)}를 넘어 마감하면 판단을 뒤집는다.",
        ),
    )


def _diana(ctx: Context) -> Briefing:
    s = ctx.snapshot
    return Briefing(
        "DIANA",
        "밸류에이션 중립, 수급이 변수",
        format_body(
            f"{s.symbol.label} 현재가 {_fmt(s.price, s)}, 최근 60봉 고점 대비 "
            f"{(s.price / max(s.closes) - 1) * 100:.1f}%, 저점 대비 "
            f"{(s.price / min(s.closes) - 1) * 100:+.1f}%.",
            "밸류에이션만으로는 방향이 안 나온다. 레인지 중단이라 기본적 요인은 "
            "이번 판정에서 가중치가 낮다.",
            "실적·공급 이슈가 새로 나오면 기술적 그림과 무관하게 레인지가 통째로 이동한다.",
            "분기 실적 발표 또는 공급 계약 공시가 나오면 이 브리핑은 폐기한다.",
        ),
    )


def _nova(ctx: Context) -> Briefing:
    s = ctx.snapshot
    lines = "\n".join(f"- {h}" for h in s.headlines)
    return Briefing(
        "NOVA",
        "헤드라인 혼조 — 방향성 없음",
        format_body(
            f"최신 헤드라인 {len(s.headlines)}건:\n{lines}",
            "상향 리포트와 변동성 확대 기사가 같이 나온다. 뉴스만으로는 한쪽으로 "
            "몰리지 않는 상태다.",
            "악재 하나가 크게 터지면 헤드라인 혼조는 즉시 일방으로 정렬된다.",
            "같은 방향 헤드라인이 3건 이상 연속되면 뉴스 가중치를 올린다.",
        ),
    )


def _vibe(ctx: Context) -> Briefing:
    s = ctx.snapshot
    fg = s.fear_greed
    label = "공포" if fg < 40 else ("탐욕" if fg > 60 else "중립")
    return Briefing(
        "VIBE",
        f"공포탐욕 {fg} · {label}",
        format_body(
            f"공포탐욕 지수 {fg}({label}), 당일 등락 {s.change_pct:+.2f}%.",
            f"{label} 구간이다. "
            + ("과열 되돌림 위험을 얹는다." if fg > 60 else "역발상 매수 여지가 있다."),
            "지수는 후행한다. 가격이 먼저 돌면 심리는 따라오므로 단독 근거로 쓰면 늦는다.",
            "지수가 하루 만에 15포인트 이상 움직이면 심리 판단을 다시 잡는다.",
        ),
    )


def _bull(ctx: Context) -> Briefing:
    s, taro = ctx.snapshot, ctx.find("TARO")
    rebut = ctx.find("BEAR")
    head = f"BEAR의 '{rebut.bubble}' 주장에 반박한다. " if rebut else ""
    return Briefing(
        "BULL",
        f"{ctx.turn}턴 — 상승 근거 우위",
        format_body(
            f"{head}TARO 관찰: {taro.bubble if taro else '기술적 중립'}. "
            f"20봉 저점 {_fmt(s.low20, s)}이 두 번 지켜졌다.",
            "저점이 유지되는 동안은 하방보다 상방 여유가 크다. 손절을 저점 아래로 "
            "두면 손실은 제한되고 목표는 레인지 상단까지 열린다.",
            "저점이 깨지면 이 논거는 그 자리에서 무효다. 그때는 내 계획이 아니라 "
            "BEAR의 계획이 맞는 것이다.",
            f"{_fmt(s.low20, s)} 종가 이탈.",
        ),
    )


def _bear(ctx: Context) -> Briefing:
    s, rebut = ctx.snapshot, ctx.find("BULL")
    head = f"BULL이 든 '{rebut.bubble}'는 레인지 안 움직임일 뿐이다. " if rebut else ""
    return Briefing(
        "BEAR",
        f"{ctx.turn}턴 — 상단 저항 미돌파",
        format_body(
            f"{head}20봉 고점 {_fmt(s.high20, s)}을 아직 못 뚫었고 "
            f"기준 변동성이 {_vol(s, ctx.mode):.2f}%다.",
            "저점 방어는 매수 신호가 아니라 아직 결정이 안 났다는 뜻이다. "
            "상단 미돌파 상태의 매수는 레인지 상단에 물릴 확률이 높다.",
            f"거래량을 실어 {_fmt(s.high20, s)}을 돌파하면 내 반대 논거는 사라진다.",
            f"{_fmt(s.high20, s)} 돌파 후 되돌림 없이 유지.",
        ),
    )


def _blitz(ctx: Context) -> Briefing:
    s = ctx.snapshot
    entry, stop, target = _plan(s, ctx.mode)
    up = _bias(s) > 0
    return Briefing(
        "BLITZ",
        f"{'롱' if up else '숏'} 트리거 {_fmt(entry, s)}",
        format_body(
            f"15분봉 기준. 진입 {_fmt(entry, s)} · 무효화 {_fmt(stop, s)} · "
            f"목표 {_fmt(target, s)}. 기준 시장은 {ctx.mode.basis}.",
            f"손절까지 {abs(entry - stop) / entry * 100:.2f}%, 목표까지 "
            f"{abs(target - entry) / entry * 100:.2f}% — 손익비 2R이다.",
            "레인지 안에서 톱질이 나면 진입 트리거만 반복 체결되고 손절이 쌓인다.",
            "같은 트리거로 두 번 연속 손절나면 이 셋업은 접는다.",
        ),
    )


def _guard(ctx: Context) -> Briefing:
    s = ctx.snapshot
    entry, stop, _ = _plan(s, ctx.mode)
    stop_pct = abs(entry - stop) / entry * 100
    buffer = LIQUIDATION_PCT - stop_pct
    ok = buffer > 1.5
    size = round(min(20.0, max(3.0, buffer * 4)), 1)
    return Briefing(
        "GUARD",
        f"청산 버퍼 {buffer:.2f}%p · 비중 {size}%",
        format_body(
            f"격리 20배에서 청산은 약 {LIQUIDATION_PCT}% 역행. 이 계획의 손절 폭은 "
            f"{stop_pct:.2f}%이므로 버퍼는 {buffer:.2f}%p다. "
            f"{ctx.mode.basis} 변동성 {_vol(s, ctx.mode):.2f}% 기준.",
            (
                f"버퍼가 남으니 비중 {size}%까지 허용한다. 손절은 지정가로 강제한다."
                if ok
                else "버퍼가 얇다. 한 번의 스파이크에 손절과 청산이 동시에 걸린다."
            ),
            "원화 차트로 재면 변동성이 두 배 이상 부풀려져 이 셋업이 통째로 기각된다. "
            "그래서 기준 시장을 무기한으로 고정했다.",
            f"버퍼가 1.5%p 아래로 내려가면 {'진입 금지' if ok else '이미 진입 금지'}다.",
        ),
    )


def _risky(ctx: Context) -> Briefing:
    ace = ctx.find("ACE")
    plan = ace.verdict if ace and ace.verdict else None
    return Briefing(
        "RISKY",
        "계획이 과도하게 방어적",
        format_body(
            f"ACE 계획: {plan.action if plan else '미정'} · 확신도 "
            f"{plan.confidence if plan else 0}% · 비중 {plan.size_pct if plan else 0}%.",
            "손절이 명확한 계획에서 비중을 깎으면 기대값만 깎인다. 손실은 손절이 "
            "막지 비중이 막는 게 아니다.",
            "다만 갭이 나는 종목이면 손절이 그 가격에 안 잡힌다. 그때는 비중이 "
            "유일한 방어다.",
            "야간 갭 이력이 확인되면 내 주장을 철회한다.",
        ),
    )


def _safe(ctx: Context) -> Briefing:
    s, ace = ctx.snapshot, ctx.find("ACE")
    plan = ace.verdict if ace and ace.verdict else None
    return Briefing(
        "SAFE",
        "비중 축소·손절 상향 요구",
        format_body(
            f"최악 시나리오: 진입 직후 {_fmt(s.low20, s)} 이탈. 그 경우 손실은 "
            f"계획 손절 {_fmt(plan.stop, s) if plan else '미정'}에서 멈추지 않을 수 있다.",
            "꼬리위험은 평균 변동성에 안 잡힌다. 비중을 절반으로 줄이면 같은 사건에서 "
            "살아남아 다음 기회를 잡는다.",
            "RISKY 말대로 매번 반으로 줄이면 이기는 판에서도 반만 먹는다.",
            "20봉 저점이 세 번 이상 지켜진 이력이면 비중 축소 요구를 낮춘다.",
        ),
    )


def _neutral(ctx: Context) -> Briefing:
    ace = ctx.find("ACE")
    plan = ace.verdict if ace and ace.verdict else None
    size = round((plan.size_pct if plan else 10.0) * 0.7, 1)
    return Briefing(
        "NEUTRAL",
        f"조건부 승인 — 비중 {size}%",
        format_body(
            "RISKY는 기대값, SAFE는 생존을 본다. 둘 다 맞고 충돌 지점은 비중 하나다.",
            f"손절이 지정가로 걸린다는 전제에서만 RISKY가 맞다. 그 전제를 계획에 "
            f"박아 넣고 비중은 {size}%로 절충한다.",
            "지정가 손절이 체결 안 되는 시장이면 이 절충안은 SAFE 쪽으로 붙어야 한다.",
            "체결 실패가 한 번이라도 관측되면 비중 상한을 다시 내린다.",
        ),
    )


def _ace(ctx: Context) -> Briefing:
    s = ctx.snapshot
    action, confidence = _action(s, ctx.mode)
    entry, stop, target = _plan(s, ctx.mode)
    stop_pct = abs(entry - stop) / entry * 100
    size = min(20.0, max(3.0, (LIQUIDATION_PCT - stop_pct) * 4))
    flat = action in ("HOLD", "PASS")
    rationale = f"{ctx.mode.basis} 기준. 애널리스트 관찰과 토론을 종합해 {action} 판정. " + (
        "근거가 한쪽으로 정렬되지 않아 관망한다."
        if flat
        else f"손절 {stop_pct:.2f}% 대비 목표 "
        f"{abs(target - entry) / entry * 100:.2f}%로 2R 확보."
    )
    verdict = Verdict(
        action=action,
        confidence=confidence,
        entry=entry,
        stop=stop,
        target=target,
        size_pct=0.0 if flat else round(size, 1),
        rationale=rationale,
    )
    return Briefing(
        "ACE",
        f"{action} · 확신도 {confidence}%",
        format_body(
            f"판정 {action}, 확신도 {confidence}%. 진입 {_fmt(entry, s)} · "
            f"손절 {_fmt(stop, s)} · 목표 {_fmt(target, s)} · 비중 {verdict.size_pct}%.",
            rationale,
            "확신도가 60% 아래면 방향보다 무효화 레벨이 중요하다. 손절이 계획의 본체다.",
            f"{_fmt(stop, s)} 도달 시 판정 종료. 재진입은 새 셋업으로만.",
        ),
        verdict=verdict,
    )


def _pm(ctx: Context) -> Briefing:
    ace = ctx.find("ACE")
    if ace is None or ace.verdict is None:
        raise ValueError("PM 은 ACE 판정 없이 심사할 수 없습니다.")
    plan = ace.verdict
    neutral = ctx.find("NEUTRAL")

    if plan.confidence < 50:
        status, size = "기각", 0.0
        comment = "확신도가 동전 던지기 수준이다. 이번 판은 건너뛴다."
    elif neutral is not None and plan.size_pct > 0:
        status = "수정승인"
        size = round(plan.size_pct * 0.7, 1)
        comment = (
            f"방향은 승인, 비중만 {plan.size_pct}% → {size}%로 줄인다. 지정가 손절 필수."
        )
    else:
        status, size = "승인", plan.size_pct
        comment = "계획 그대로 승인한다."

    final = Verdict(
        action="HOLD" if status == "기각" else plan.action,
        confidence=plan.confidence,
        entry=plan.entry,
        stop=plan.stop,
        target=plan.target,
        size_pct=size,
        rationale=plan.rationale,
        pm_status=status,
        pm_comment=comment,
    )
    return Briefing(
        "PM",
        f"{status} · 비중 {size}%",
        format_body(
            f"ACE 계획 {plan.action} 확신도 {plan.confidence}% 비중 "
            f"{plan.size_pct}%를 심사했다.",
            comment,
            "내가 승인해도 시장은 계획대로 안 움직인다. 이건 판정이지 예측이 아니다.",
            "손절이 체결되면 그것으로 종료. 물타기 없음.",
        ),
        verdict=final,
    )


_WRITERS = {
    "TARO": _taro,
    "DIANA": _diana,
    "NOVA": _nova,
    "VIBE": _vibe,
    "BULL": _bull,
    "BEAR": _bear,
    "BLITZ": _blitz,
    "GUARD": _guard,
    "RISKY": _risky,
    "SAFE": _safe,
    "NEUTRAL": _neutral,
    "ACE": _ace,
    "PM": _pm,
}
