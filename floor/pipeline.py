"""진행 순서를 실제로 돌리고, 화면이 받아 쓸 사건을 순서대로 내보낸다.

러너를 인자로 받는 이유: 데모(준비된 응답)와 실전(claude -p)이 이 자리만 다르다.
나머지 순서·이벤트·리포트 저장은 두 모드가 똑같이 쓴다.

내보내는 사건
    meta     이번 판의 모드·에이전트 배치 (화면을 그리기 위한 첫 신호)
    log      콘솔 한 줄 (데이터 수집 → 헤드라인 → 과거 판정 회고)
    quote    전광판에 올릴 시세
    phase    방 하나가 일을 시작함
    thinking 에이전트가 생각 중
    agent    브리핑 완성 (말풍선 + 전문)
    verdict  최종 판정
    saved    리포트 저장됨
    done     끝
    error    실패 — 여기서 스트림이 끝난다
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from floor import plan_gate, report
from floor.agents import BY_KEY, MODES, UnknownMode, resolve_mode
from floor.demo import Briefing, Context, Verdict, demo_runner
from floor.market import Snapshot, Symbol, UnknownSymbol, demo_snapshot, resolve_symbol

Runner = Callable[[str, Context], Briefing]
SnapshotFn = Callable[[Symbol], Snapshot]


def describe_modes() -> list[dict]:
    """화면이 모드 버튼을 그리는 데 쓰는 설명. 호출 횟수를 같이 보낸다."""
    return [
        {
            "key": m.key,
            "label": m.label,
            "calls": m.calls,
            "actions": list(m.actions),
            "basis": m.basis,
            "note": m.note,
            "roster": list(m.roster),
        }
        for m in MODES.values()
    ]


def describe_agents() -> list[dict]:
    return [asdict(a) for a in BY_KEY.values()]


def _quote_event(snap: Snapshot) -> dict:
    return {
        "type": "quote",
        "symbol": snap.symbol.key,
        "label": snap.symbol.label,
        "currency": snap.symbol.currency,
        "price": snap.price,
        "change_pct": snap.change_pct,
        "closes": list(snap.closes),
        "ma20": list(snap.ma20),
        "ma50": list(snap.ma50),
        "high20": snap.high20,
        "low20": snap.low20,
        "market_open": snap.market_open,
        "board": list(snap.board_rows),
        "fx_krw": snap.fx_krw,
        "fear_greed": snap.fear_greed,
        "perp_vol_pct": snap.perp_vol_pct,
        "krw_vol_pct": snap.krw_vol_pct,
        "source": snap.source,
    }


def _verdict_event(verdict: Verdict, basis: str) -> dict:
    payload = asdict(verdict)
    payload.update({"type": "verdict", "basis": basis})
    return payload


def run(
    symbol_raw: str,
    mode_raw: str,
    *,
    reports_dir: Path,
    runner: Runner = demo_runner,
    snapshot_fn: SnapshotFn = demo_snapshot,
    now: datetime | None = None,
) -> Iterator[dict]:
    """한 판을 끝까지 돌린다. 사건을 만드는 즉시 내보낸다."""
    try:
        symbol = resolve_symbol(symbol_raw)
        mode = resolve_mode(mode_raw)
    except (UnknownSymbol, UnknownMode) as exc:
        yield {"type": "error", "text": str(exc)}
        return

    now = now or report.now_kst()
    yield {
        "type": "meta",
        "symbol": symbol.key,
        "label": symbol.label,
        "mode": mode.key,
        "mode_label": mode.label,
        "basis": mode.basis,
        "note": mode.note,
        "calls": mode.calls,
        "roster": list(mode.roster),
        "started_at": now.isoformat(),
    }

    try:
        snapshot = snapshot_fn(symbol)
    except Exception as exc:  # 시세를 못 얻으면 그 판은 끝이다
        yield {"type": "error", "text": f"시세를 가져오지 못했습니다: {exc}"}
        return

    yield _quote_event(snapshot)
    yield {"type": "log", "track": "system", "text": f"■ 데이터 수집 — {symbol.label}"}
    yield {
        "type": "log",
        "track": "system",
        "text": f"  현재가 {snapshot.price} ({snapshot.change_pct:+.2f}%) · "
        f"20봉 {snapshot.low20}–{snapshot.high20} · 출처 {snapshot.source}",
    }
    yield {
        "type": "log",
        "track": "system",
        # 스캘핑이 왜 원화 차트를 안 보는지 화면에 매번 남긴다.
        "text": f"  판정 기준 시장 {mode.basis} · 무기한 변동성 "
        f"{snapshot.perp_vol_pct:.2f}% vs 원화 {snapshot.krw_vol_pct:.2f}%",
    }
    if snapshot.source == "demo":
        yield {
            "type": "log",
            "track": "system",
            "text": "  ⚠ 데모 모드 — 위 숫자는 합성값입니다. 실측 시세가 아닙니다.",
        }

    yield {"type": "log", "track": "system", "text": "■ 뉴스 헤드라인"}
    for line in snapshot.headlines:
        yield {"type": "log", "track": "system", "text": f"  - {line}"}
    if not snapshot.headlines:
        # 뉴스가 없는 판과 뉴스를 못 가져온 판을 화면에서 구분해 준다.
        yield {
            "type": "log",
            "track": "system",
            "text": "  - 헤드라인을 가져오지 못했습니다. 뉴스 없이 판단합니다.",
        }

    retro = report.retrospect(reports_dir, symbol.key, snapshot.price)
    yield {"type": "log", "track": "system", "text": "■ 과거 판정 회고"}
    if retro:
        for line in retro:
            yield {"type": "log", "track": "system", "text": f"  - {line}"}
    else:
        yield {"type": "log", "track": "system", "text": "  - 이 종목의 지난 판정 없음"}

    briefings: list[Briefing] = []
    for index, step in enumerate(mode.steps):
        yield {
            "type": "phase",
            "step": index,
            "room": BY_KEY[step.agents[0]].room,
            "label": step.label,
            "kind": step.kind,
            "agents": list(step.agents),
        }
        for key, turn in step.order():
            agent = BY_KEY[key]
            yield {"type": "thinking", "agent": key, "room": agent.room, "turn": turn}
            ctx = Context(
                snapshot=snapshot, mode=mode, turn=turn, prior=tuple(briefings)
            )
            try:
                brief = runner(key, ctx)
            except Exception as exc:
                yield {"type": "error", "text": f"{key} 분석 실패: {exc}"}
                return
            briefings.append(brief)
            yield {
                "type": "agent",
                "agent": key,
                "name": agent.name,
                "role": agent.role,
                "room": agent.room,
                "track": "debate" if step.kind == "debate" else "agent",
                "turn": turn,
                "bubble": brief.bubble,
                "body": brief.body,
            }

    final = next((b.verdict for b in reversed(briefings) if b.verdict is not None), None)
    if final is None:
        yield {"type": "error", "text": "판정을 낸 에이전트가 없습니다."}
        return

    yield _verdict_event(final, mode.basis)

    # 검증 관문. 방향은 플로어가 정했고, 그 매매가 가능한지는 엔진이 정한다.
    # 모드마다 보는 차트가 다르므로 시간축은 for_mode 가 고른다 — algo 는 일봉,
    # scalp·attack 은 15분봉이다. 공격 모드도 예외가 아니다.
    gate = plan_gate.for_mode(mode.key, snapshot, final.action)
    yield {"type": "gate", **asdict(gate), "blocked": gate.blocked}

    text = report.render(
        snapshot=snapshot,
        mode_key=mode.key,
        mode_label=mode.label,
        basis=mode.basis,
        briefings=tuple(briefings),
        verdict=final,
        gate=gate,
        retro=retro,
        now=now,
        source=snapshot.source,
    )
    name = report.report_name(symbol.key, now)
    try:
        report.save(reports_dir, name, text)
    except report.ReportError as exc:
        # 판정은 이미 화면에 떴다. 저장 실패로 그걸 지우지 말고 사실만 알린다.
        yield {"type": "error", "text": str(exc)}
        return

    yield {"type": "saved", "name": name, "url": f"/reports/{name}"}
    yield {"type": "done"}
