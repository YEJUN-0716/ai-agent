"""`/floor` 세션 러너 — 판을 차리고(prep), 세션이 쓴 걸 검사해 리포트로 남긴다(save).

`-p` 러너(`claude_runner.py`)는 호출 하나가 프로세스 하나였다. 알고리즘 13명이면
프로세스가 13번 뜨고, 그때마다 시세와 앞선 브리핑을 처음부터 다시 넘긴다.
여기는 그 자리를 세션 하나로 바꾼다 — 프로세스 0개, 시세 조회 1회.

    python -m floor.cli prep 하이닉스 scalp
    python -m floor.cli save .tmp/floor/2026-08-05-SKHYNIX-1530.json .tmp/floor/briefings.json

**왜 둘로 나눴나.** 세션이 발언할 때마다 시세를 직접 조회하면 13명이 서로 다른
가격을 보게 된다. prep 이 한 번 찍어 상태 파일에 얼려 두고 save 가 그 얼린 값으로
리포트를 쓴다 — 화면 경로가 스냅샷 하나를 13명에게 돌리는 것과 같은 규칙이다.

**검사는 `-p` 러너와 같은 함수를 쓴다**(`claude_runner.parse_briefing`). 세션이
썼다는 이유로 느슨해지는 자리는 없다. 모드에 없는 판정, 빠진 숫자, 어긋난 순서는
여기서도 똑같이 거절당한다 — 고쳐서 통과시키면 판정이 아니라 창작이 된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from floor import claude_runner, market, plan_gate, report
from floor.agents import BY_KEY, Mode, UnknownMode, resolve_mode
from floor.demo import Briefing, Context, Verdict
from floor.market import Snapshot, Symbol, UnknownSymbol

# 판 상태는 버려도 되는 중간물이다. 남길 값은 리포트뿐이라 reports/ 와 자리를 나눈다.
STATE_DIR = Path(__file__).resolve().parent.parent / ".tmp" / "floor"

# 스냅샷을 JSON 으로 눕혔다 세울 때 튜플로 되돌려야 하는 자리.
_TUPLE_FIELDS = ("closes", "ma20", "ma50", "headlines", "board_rows")

SnapshotFn = Callable[[Symbol], Snapshot]


class FloorError(RuntimeError):
    """세션 러너가 판을 접을 때. 메시지가 그대로 사장님 화면에 뜬다."""


# 판이 깨지는 경로는 여럿이지만 사장님이 볼 건 한 줄짜리 이유뿐이다.
_FAILURES = (
    FloorError,
    claude_runner.RunnerError,
    report.ReportError,
    UnknownSymbol,
    UnknownMode,
)


# ── 판 상태 얼리고 녹이기 ─────────────────────────────────


def _rel(path: Path) -> str:
    """명령줄에 붙일 경로. 윈도우 역슬래시는 bash 에서 이스케이프로 먹힌다."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _write_state(
    state_dir: Path,
    mode: Mode,
    snapshot: Snapshot,
    retro: tuple[str, ...],
    now: datetime,
) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{report.report_name(snapshot.symbol.key, now)[:-3]}.json"
    body = {
        "mode": mode.key,
        "now": now.isoformat(),
        "retro": list(retro),
        "snapshot": asdict(snapshot),
    }
    try:
        path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        raise FloorError(f"판 상태를 저장하지 못했습니다: {exc}") from exc
    return path


def _snapshot_from(data: dict) -> Snapshot:
    fields = dict(data)
    fields["symbol"] = Symbol(**fields["symbol"])
    for key in _TUPLE_FIELDS:
        fields[key] = tuple(fields[key])
    # 봉은 튜플의 튜플이라 한 겹 더 되돌린다.
    fields["bars"] = tuple(tuple(bar) for bar in fields.get("bars", ()))
    return Snapshot(**fields)


def _read_state(path: Path) -> tuple[Mode, Snapshot, tuple[str, ...], datetime]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FloorError(f"판 상태를 읽지 못했습니다: {exc}") from exc
    try:
        return (
            resolve_mode(data["mode"]),
            _snapshot_from(data["snapshot"]),
            tuple(data["retro"]),
            datetime.fromisoformat(data["now"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # 얼린 판을 반쯤 녹여 쓰느니 다시 차린다. 절반짜리 시세로 낸 판정은 판정이 아니다.
        raise FloorError(f"판 상태가 깨졌습니다({exc}) — prep 부터 다시 돌리세요") from exc


# ── prep — 판 차리기 ──────────────────────────────────────


def _pack(
    mode: Mode,
    snapshot: Snapshot,
    retro: tuple[str, ...],
    state_path: Path,
    now: datetime,
) -> str:
    """세션이 전원을 연기하는 데 필요한 전부. 여기 없는 숫자는 지어낸 숫자다."""
    order = mode.order
    base = Context(snapshot=snapshot, mode=mode, turn=0, prior=())
    rule_sets = [
        claude_runner.rules(key, Context(snapshot=snapshot, mode=mode, turn=turn, prior=()))
        for key, turn in order
    ]
    # 전원이 공유하는 규칙은 한 번만 적고, 자리마다 다른 것만 그 줄에 붙인다.
    common = [rule for rule in rule_sets[0] if all(rule in rs for rs in rule_sets)]

    lines = [
        f"# 판 — {snapshot.symbol.label} · {mode.label}",
        "",
        f"- 상태 파일 `{_rel(state_path)}` ← save 에 그대로 넘긴다",
        f"- 판정 기준 시장 **{mode.basis}**",
        f"- 낼 수 있는 판정 {' / '.join(mode.actions)}",
        f"- 발언 {len(order)}회 — 전부 이 세션이 연기한다 (클로드 프로세스 0개)",
        f"- {now:%Y-%m-%d %H:%M} KST · {mode.note}",
        "",
        "## 시세 — 인용은 여기서만",
        "",
        claude_runner.facts(snapshot, base),
        "",
        "## 과거 판정 회고",
        "",
    ]
    lines += [f"- {line}" for line in retro] or ["- 이 종목의 지난 판정 없음"]

    lines += ["", "## 전원이 지킬 것", ""]
    lines += [f"- {rule}" for rule in common]

    lines += ["", "## 발언 순서 — 이대로, 하나도 빼지 않는다", ""]
    for index, ((key, turn), agent_rules) in enumerate(zip(order, rule_sets), start=1):
        who = BY_KEY[key]
        turn_note = f" · 토론 {turn}번째 발언" if turn else ""
        lines.append(f"{index}. **{key}** {who.name} — {who.role}{turn_note}")
        lines.append(f"   - 보는 것: {who.watches}")
        lines += [f"   - {rule}" for rule in agent_rules if rule not in common]

    verdict_agents = [a for a in claude_runner.VERDICT_AGENTS if a in mode.roster]
    lines += [
        "",
        "## 출력 형식",
        "",
        "브리핑 하나가 JSON 객체 하나다. 위 순서 그대로 배열에 담아 파일로 쓴다.",
        "`agent` 에는 위 목록의 키를 그대로 적는다 — 어긋나면 save 가 거절한다.",
        "",
        "```json",
        '{"agent": "%s", %s' % (order[0][0], claude_runner.shape(order[0][0], base)[1:]),
        "```",
        "",
        f"판정을 내는 {' · '.join(verdict_agents)} 는 verdict 를 붙인다:",
        "",
        "```json",
        *(
            '{"agent": "%s", %s' % (key, claude_runner.shape(key, base)[1:])
            for key in verdict_agents
        ),
        "```",
        "",
        "## 다 쓰면",
        "",
        "```",
        f"python -m floor.cli save {_rel(state_path)} <브리핑 파일>",
        "```",
        "",
        "거절당하면 거절당한 항목만 고쳐 다시 돌린다. 검사를 피해 가지 않는다.",
    ]
    return "\n".join(lines)


def prep(
    symbol_raw: str,
    mode_raw: str | None = None,
    *,
    reports_dir: Path = report.REPORTS_DIR,
    snapshot_fn: SnapshotFn = market.live_snapshot,
    state_dir: Path = STATE_DIR,
    now: datetime | None = None,
) -> tuple[str, Path]:
    """시세를 한 번 찍어 얼리고, 세션이 읽을 브리핑 팩을 만든다."""
    symbol = market.resolve_symbol(symbol_raw)
    mode = resolve_mode(mode_raw)
    now = now or report.now_kst()
    try:
        snapshot = snapshot_fn(symbol)
    except Exception as exc:  # 시세를 못 얻으면 그 판은 끝이다. 합성으로 메우지 않는다.
        raise FloorError(f"시세를 가져오지 못했습니다: {exc}") from exc

    retro = report.retrospect(reports_dir, symbol.key, snapshot.price)
    state_path = _write_state(state_dir, mode, snapshot, retro, now)
    return _pack(mode, snapshot, retro, state_path, now), state_path


# ── save — 검사하고 남기기 ────────────────────────────────


def _read_briefings(path: Path) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FloorError(f"브리핑 파일을 읽지 못했습니다: {exc}") from exc
    if not isinstance(payload, list):
        raise FloorError("브리핑 파일은 JSON 배열 하나여야 합니다")
    return payload


def save(
    state_path: Path,
    briefings_path: Path,
    *,
    reports_dir: Path = report.REPORTS_DIR,
) -> tuple[Path, Verdict]:
    """세션이 쓴 브리핑을 `-p` 러너와 같은 기준으로 검사하고 리포트로 남긴다."""
    mode, snapshot, retro, now = _read_state(state_path)
    payload = _read_briefings(briefings_path)
    order = mode.order

    if len(payload) != len(order):
        # 개수가 곧 판정의 근거다. 12명이 낸 판정을 13명이 낸 판정으로 남길 수 없다.
        raise FloorError(
            f"브리핑이 {len(payload)}개입니다 — {mode.label} 모드는 {len(order)}개입니다. "
            "순서를 건너뛰거나 합치지 않습니다."
        )

    briefings: list[Briefing] = []
    for index, (item, (key, turn)) in enumerate(zip(payload, order), start=1):
        if not isinstance(item, dict):
            raise FloorError(f"{index}번째 브리핑이 JSON 객체가 아닙니다")
        wrote = str(item.get("agent", "")).strip().upper()
        if wrote != key:
            raise FloorError(
                f"{index}번째는 {key} 차례인데 {wrote or '이름 없는 항목'} 이 왔습니다 — "
                "발언 순서가 어긋났습니다"
            )
        ctx = Context(snapshot=snapshot, mode=mode, turn=turn, prior=tuple(briefings))
        briefings.append(claude_runner.parse_briefing(item, key, ctx))

    final = next((b.verdict for b in reversed(briefings) if b.verdict is not None), None)
    if final is None:
        raise FloorError("판정을 낸 에이전트가 없습니다.")

    # 화면과 같은 관문을 세션에서도 통과해야 한다. 여기서 빠지면 `/floor` 로 돈 판만
    # 검증 없이 리포트에 남는다.
    gate = plan_gate.check(snapshot.bars, final.action) if mode.key == "algo" else None

    text = report.render(
        snapshot=snapshot,
        mode_key=mode.key,
        mode_label=mode.label,
        basis=mode.basis,
        briefings=tuple(briefings),
        verdict=final,
        retro=retro,
        now=now,
        source=snapshot.source,
        gate=gate,
    )
    path = report.save(reports_dir, report.report_name(snapshot.symbol.key, now), text)
    return path, final, gate


# ── 명령줄 ────────────────────────────────────────────────


def split_args(words: list[str]) -> tuple[str, str]:
    """마지막 토큰이 모드면 떼어낸다. `SK 하이닉스 scalp` 처럼 띄어 쓴 종목도 받는다."""
    if len(words) > 1:
        try:
            resolve_mode(words[-1])
        except UnknownMode:
            pass
        else:
            return " ".join(words[:-1]), words[-1]
    return " ".join(words), "algo"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # 윈도우 cp949 에서 한글이 깨진다
    parser = argparse.ArgumentParser(
        prog="python -m floor.cli", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ready = sub.add_parser("prep", help="시세를 찍어 얼리고 브리핑 팩을 출력한다")
    ready.add_argument("words", nargs="+", metavar="종목 [모드]")

    done = sub.add_parser("save", help="세션이 쓴 브리핑을 검사해 리포트로 남긴다")
    done.add_argument("state", type=Path, help="prep 이 만든 상태 파일")
    done.add_argument("briefings", type=Path, help="브리핑 JSON 배열 파일")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "prep":
            text, _ = prep(*split_args(args.words))
            print(text)
        else:
            path, verdict, gate = save(args.state, args.briefings)
            print(f"리포트 저장 — {_rel(path)}")
            print(
                f"판정 {verdict.action} · 확신도 {verdict.confidence}% · "
                f"진입 {verdict.entry} · 손절 {verdict.stop} · 목표 {verdict.target} · "
                f"비중 {verdict.size_pct}%"
            )
            if gate is not None:
                print(f"검증 관문 {gate.status.upper()} — {gate.headline}")
                if gate.blocked:
                    print("→ 이 매매는 하지 않습니다. 위 숫자는 실행 대상이 아닙니다.")
    except _FAILURES as exc:
        print(f"실패 — {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
