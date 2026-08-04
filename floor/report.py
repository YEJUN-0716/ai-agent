"""리포트 저장과 과거 판정 조회.

저장한 리포트는 보관용이 아니라 다음 판단의 입력이다. 같은 종목을 다시 돌리면
지난 판정과 그 이후 가격 흐름을 회고해 판단에 반영한다 — 그래서 읽기도 여기 있다.

파일 이름: reports/2026-08-04-SKHYNIX-1528.md
머리말 6줄만 기계가 읽고, 나머지는 사람이 읽는 마크다운이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from floor.demo import Briefing, Verdict
from floor.market import KST, Snapshot

_HEADER_KEYS = ("symbol", "mode", "action", "confidence", "price", "at")
# 파일 이름을 URL·경로에 그대로 쓰므로, 만든 규칙에서 벗어난 이름은 아예 안 읽는다.
_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Z0-9.\-]+-\d{4}\.md$")


class ReportError(RuntimeError):
    """리포트를 읽거나 쓰지 못했을 때. 메시지를 그대로 사용자에게 보여준다."""


@dataclass(frozen=True)
class PastCall:
    """지난 판정 한 건. 회고에 쓴다."""

    name: str
    symbol: str
    mode: str
    action: str
    confidence: int
    price: float
    at: str


def now_kst() -> datetime:
    return datetime.now(KST)


def report_name(symbol_key: str, now: datetime) -> str:
    return f"{now:%Y-%m-%d}-{symbol_key}-{now:%H%M}.md"


def _header(snapshot: Snapshot, mode_key: str, verdict: Verdict, now: datetime) -> str:
    values = {
        "symbol": snapshot.symbol.key,
        "mode": mode_key,
        "action": verdict.action,
        "confidence": verdict.confidence,
        "price": snapshot.price,
        "at": now.isoformat(),
    }
    lines = "\n".join(f"{key}: {values[key]}" for key in _HEADER_KEYS)
    return f"---\n{lines}\n---\n"


def render(
    snapshot: Snapshot,
    mode_key: str,
    mode_label: str,
    basis: str,
    briefings: tuple[Briefing, ...],
    verdict: Verdict,
    retro: tuple[str, ...],
    now: datetime,
    source: str,
) -> str:
    s = snapshot
    parts = [
        _header(s, mode_key, verdict, now),
        f"\n# {s.symbol.label} · {mode_label} · {verdict.action}\n",
        f"- 시각 {now:%Y-%m-%d %H:%M} KST",
        f"- 판정 기준 시장 **{basis}**",
        f"- 현재가 {s.price} ({s.change_pct:+.2f}%)",
        f"- 시세 출처 `{source}`"
        + ("  ← 합성값입니다. 실측 아님." if source == "demo" else ""),
    ]
    if s.board_rows:
        parts.append("\n## 멀티 거래소 전광판\n")
        parts.append("| 거래소 | 무기한(USDT) | 펀딩 | KRX 괴리 |")
        parts.append("|---|---|---|---|")
        for row in s.board_rows:
            funding = (
                "추정치 — 직접 조회 불가"
                if row["estimated"]
                else f"{row['funding_pct']:+.4f}%"
            )
            parts.append(
                f"| {row['exchange']} | {row['perp_usdt']} | {funding} "
                f"| {row['gap_pct']:+.2f}% |"
            )

    if retro:
        parts.append("\n## 과거 판정 회고\n")
        parts.extend(f"- {line}" for line in retro)

    parts.append("\n## 에이전트 브리핑\n")
    for item in briefings:
        parts.append(f"### {item.agent} — {item.bubble}\n")
        parts.append(item.body + "\n")

    parts.append("\n## 최종 판정\n")
    parts.append(f"- 액션 **{verdict.action}** · 확신도 {verdict.confidence}%")
    parts.append(f"- 진입 {verdict.entry} · 손절 {verdict.stop} · 목표 {verdict.target}")
    parts.append(f"- 권장 비중 {verdict.size_pct}%")
    if verdict.pm_status:
        parts.append(f"- PM {verdict.pm_status} — {verdict.pm_comment}")
    parts.append(f"- 근거 {verdict.rationale}")
    parts.append(
        "\n---\nAI 시뮬레이션입니다. 투자 조언이 아니며 실제 주문은 이뤄지지 않았습니다.\n"
    )
    return "\n".join(parts)


def save(directory: Path, name: str, text: str) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"리포트를 저장하지 못했습니다: {exc}") from exc
    return path


def _parse_header(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    found: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, _, value = line.partition(":")
        found[key.strip()] = value.strip()
    return found if all(k in found for k in _HEADER_KEYS) else None


def listing(directory: Path) -> tuple[PastCall, ...]:
    """최근 순 전체 목록. 머리말이 깨진 파일은 조용히 건너뛴다.

    목록 한 줄이 깨졌다고 /reports 페이지 전체가 죽으면 안 된다. 대신 회고에
    쓰는 값은 정상인 파일에서만 나오므로 잘못된 값이 섞이지는 않는다.
    """
    if not directory.exists():
        return ()
    out: list[PastCall] = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        if not _NAME_RE.match(path.name):
            continue
        try:
            header = _parse_header(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if header is None:
            continue
        try:
            out.append(
                PastCall(
                    name=path.name,
                    symbol=header["symbol"],
                    mode=header["mode"],
                    action=header["action"],
                    confidence=int(header["confidence"]),
                    price=float(header["price"]),
                    at=header["at"],
                )
            )
        except ValueError:
            continue
    return tuple(out)


def read(directory: Path, name: str) -> str:
    """리포트 한 건의 원문.

    이름을 URL 에서 받으므로 형식 검사를 통과한 것만 연다. 통과해도 경로를
    조합한 뒤 부모 디렉터리를 다시 확인한다 — 검사 하나가 뚫려도 밖으로 못 나간다.
    """
    if not _NAME_RE.match(name):
        raise ReportError(f"리포트 이름 형식이 아닙니다: {name!r}")
    path = (directory / name).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise ReportError(f"그런 리포트가 없습니다: {name}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportError(f"리포트를 읽지 못했습니다: {exc}") from exc


def retrospect(
    directory: Path, symbol_key: str, price_now: float, limit: int = 3
) -> tuple[str, ...]:
    """같은 종목의 지난 판정과 그 이후 가격 흐름. 다음 판단의 입력이 된다."""
    past = [c for c in listing(directory) if c.symbol == symbol_key][:limit]
    lines = []
    for call in past:
        moved = (price_now / call.price - 1) * 100 if call.price else 0.0
        when = call.at[:16].replace("T", " ")
        lines.append(
            f"{when} · {call.mode} · {call.action}(확신도 {call.confidence}%) "
            f"→ 이후 {moved:+.2f}%"
        )
    return tuple(lines)
