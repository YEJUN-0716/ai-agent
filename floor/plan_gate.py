"""ACE·PM 판정을 stock-analyzer 의 검증된 플랜 엔진에 통과시키는 마지막 관문.

방향은 플로어(총괄 판정)가 정하고, **라인과 거부권은 엔진이 갖는다.**
`build_trade_plan` 에는 이 용도의 문이 이미 나 있다 — direction 을 주입받아도
게이트(숏 레짐 필터 · 저확신 숏 억제 · 손익비 하한)는 그대로 걸린다.

두 저장소의 유일한 이음매다. stock-analyzer 구조가 바뀌면 이 파일만 고친다.

**엔진을 못 부르면 통과가 아니라 "검사 못 함"으로 끝난다.** 검사기가 자리를
비웠다고 해서 매매가 승인된 것은 아니다 — 화면에도 그렇게 적힌다.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

STATUS_PASS = "pass"  # 엔진이 유효한 셋업으로 인정
STATUS_REJECT = "reject"  # 엔진이 기각 — 이 매매는 하지 않는다
STATUS_SKIP = "skip"  # 검사 자체를 못 함 (통과 아님)

# 플로어 판정 → 엔진 방향. 관망(HOLD/PASS)은 검증할 매매가 없다.
_DIRECTIONS = {"BUY": "long", "LONG": "long", "SELL": "short", "SHORT": "short"}

# build_trade_plan 이 내부에서 예외를 삼키고 되돌려주는 사유들.
# 이건 "기각"이 아니라 "검사 실패"라서 구분해야 한다.
_ENGINE_FAILED = ("트레이드 플랜 오류", "데이터 부족")


@dataclass(frozen=True)
class GateResult:
    """검증 결과 한 장. 화면·리포트가 이것만 보고 그린다."""

    status: str
    headline: str
    direction: str = "none"
    entry_low: float = 0.0
    entry_high: float = 0.0
    stop: float = 0.0
    targets: tuple[float, ...] = ()
    rr: tuple[float | None, ...] = ()
    confidence: str = ""
    signals: tuple[str, ...] = ()
    min_rr: float = 0.0
    # 어느 차트로 쟀는지. 스윙을 15분봉으로, 단타를 일봉으로 재면 그건 검증이
    # 아니라 오판이라 결과에 항상 붙여 보낸다.
    basis: str = ""
    # 위 라인들의 단위. 무기한 15분봉으로 잰 한국 종목은 원화가 아니라 USDT 다.
    currency: str = "USD"

    @property
    def blocked(self) -> bool:
        return self.status == STATUS_REJECT


class GateUnavailable(RuntimeError):
    """엔진을 못 불렀다. 메시지를 그대로 화면에 보여준다."""


def stock_analyzer_dir() -> Path:
    """stock-analyzer 사본 위치. 비서(assistant/config.py)와 같은 환경변수를 쓴다."""
    raw = os.environ.get("STOCK_ANALYZER_PATH")
    return Path(raw).expanduser() if raw else Path.home() / "stock-analyzer"


def _load():
    """(build_trade_plan, MIN_BARS, DEFAULT_MIN_RR, pandas) — 못 부르면 예외."""
    root = stock_analyzer_dir()
    if not root.is_dir():
        raise GateUnavailable(
            f"stock-analyzer 폴더가 없습니다: {root}. STOCK_ANALYZER_PATH 를 확인하세요."
        )
    path = str(root)
    # 맨 앞이 아니라 뒤에 붙이고, 끝나면 뗀다. `modules` 는 아주 흔한 이름이라
    # 경로를 남겨두면 이 프로세스의 다른 import 가 stock-analyzer 쪽으로 샐 수 있다.
    added = path not in sys.path
    if added:
        sys.path.append(path)
    try:
        import pandas as pd

        from modules.trade_plan import DEFAULT_MIN_RR, MIN_BARS, build_trade_plan
    except Exception as exc:  # noqa: BLE001 - 원인을 그대로 사장님께 보여준다
        raise GateUnavailable(
            f"플랜 엔진을 불러오지 못했습니다 ({exc}). "
            "stock-analyzer 와 같은 파이썬 환경에서 실행해야 합니다."
        ) from exc
    finally:
        if added and path in sys.path:
            sys.path.remove(path)
    return build_trade_plan, MIN_BARS, DEFAULT_MIN_RR, pd


def check(
    bars: tuple[tuple[float, float, float, float], ...],
    action: str,
    *,
    basis: str = "일봉",
    currency: str = "USD",
) -> GateResult:
    """판정 방향을 엔진에 넣고 통과 여부를 받는다.

    bars 는 (시, 고, 저, 종) 완결 봉. 엔진은 시간축을 묻지 않는다 — MIN_BARS·ATR·
    레짐 필터가 전부 봉 **개수** 기준이라 일봉이든 15분봉이든 같은 자로 잰다.
    어느 차트를 넣었는지는 호출한 쪽만 알므로 basis·currency 로 받아 결과에 붙인다.

    방향만 넘기고 진입·손절·목표·손익비는 전부 엔진이 계산한다 — 플로어가 부른
    숫자는 여기서 쓰지 않는다.
    """
    where = {"basis": basis, "currency": currency}
    direction = _DIRECTIONS.get(action.strip().upper())
    if direction is None:
        return GateResult(
            STATUS_SKIP, f"{action} — 관망이라 검증할 매매가 없습니다.", **where
        )

    try:
        build_trade_plan, min_bars, default_min_rr, pd = _load()
    except GateUnavailable as exc:
        return GateResult(STATUS_SKIP, f"검사 못 함 — {exc}", **where)

    if len(bars) < min_bars:
        return GateResult(
            STATUS_SKIP,
            f"검사 못 함 — {basis} {len(bars)}개뿐입니다 (최소 {min_bars}개 필요).",
            **where,
        )

    # build_trade_plan 은 자기 안에서 예외를 삼키지만 DataFrame 조립과 호출 자체는
    # 그 밖이다. 판다스 버전이 어긋나 여기서 터지면 판 전체가 죽는다 — 관문 하나
    # 때문에 판정을 잃지 않도록 이 자리도 "검사 못 함"으로 떨어뜨린다.
    try:
        df = pd.DataFrame(list(bars), columns=["Open", "High", "Low", "Close"])
        plan = build_trade_plan(df, direction=direction)
    except Exception as exc:  # noqa: BLE001 - 원인을 그대로 화면에 보여준다
        return GateResult(STATUS_SKIP, f"검사 못 함 — 플랜 엔진 호출 실패: {exc}", **where)

    reason = plan.get("reason_invalid", "")
    if reason.startswith(_ENGINE_FAILED):
        # 엔진이 넘어진 것과 엔진이 기각한 것은 다르다. 넘어진 건 통과가 아니다.
        return GateResult(STATUS_SKIP, f"검사 못 함 — {reason}", **where)

    entry = plan.get("entry") or {}
    rr = tuple(plan.get("rr") or ())
    common = {
        "direction": plan.get("direction", direction),
        "entry_low": entry.get("low", 0.0),
        "entry_high": entry.get("high", 0.0),
        "stop": plan.get("stop", 0.0),
        "targets": tuple(plan.get("targets") or ()),
        "rr": rr,
        "confidence": plan.get("confidence", ""),
        "signals": tuple(plan.get("signals") or ()),
        "min_rr": default_min_rr,
        **where,
    }

    if plan.get("valid"):
        t1 = rr[0] if rr and rr[0] is not None else None
        got = f"손익비 T1 {t1:.2f}" if t1 is not None else "손익비 확인"
        return GateResult(STATUS_PASS, f"{got} · 기준 {default_min_rr} 통과", **common)

    return GateResult(STATUS_REJECT, reason or "유효한 셋업이 아닙니다.", **common)


# 15분봉으로 도는 모드. 여기 없는 모드에 15분봉을 먹이면 시간축이 어긋난다.
INTRADAY_MODES = ("scalp", "attack")


def for_mode(mode_key: str, snapshot, action: str) -> GateResult:
    """그 모드가 실제로 보는 차트의 봉으로 검증한다.

    algo 는 일봉 스윙, scalp·attack 은 15분봉 단타다. 스캘핑의 판정 기준 시장이
    무기한이므로 15분봉도 무기한에서 오고, 그래서 라인 단위가 USDT 다.

    **공격 모드도 통과한다.** 관망이 금지라 방향은 반드시 고르지만, 고른 방향이
    매매 가능한 셋업인지는 여기서 걸린다 — '연출'이라는 이유로 거부권을 면제하면
    거부권이 아니다. 화면과 리포트에는 연출이라는 표기가 그대로 남는다.
    """
    if mode_key == "algo":
        return check(
            snapshot.bars, action, basis="일봉", currency=snapshot.symbol.currency
        )
    if mode_key in INTRADAY_MODES:
        return check(
            snapshot.bars_15m,
            action,
            basis=snapshot.bars_15m_from or "15분봉",
            # 15분봉은 무기한(코인·한국종목)이거나 달러 표기 야후(미국주식)다.
            currency="USD",
        )
    # 모르는 모드를 조용히 통과시키느니 검사 안 됐다고 적는다.
    return GateResult(STATUS_SKIP, f"검사 못 함 — {mode_key} 모드의 시간축을 모릅니다.")
