#!/usr/bin/env python
"""빗각 채널 8단계 — **페이퍼 배관 번인** 러너. (성과를 안 잰다)

    python scripts/run_bitgak_paper.py clean [--yes]      # §2.3 계좌 위생
    python scripts/run_bitgak_paper.py selftest           # §11.4 신규 8종
    python scripts/run_bitgak_paper.py gate [--days 60]   # §2.5 도구 게이트
    python scripts/run_bitgak_paper.py probe              # §3.1 스톱·지정가 경로
    python scripts/run_bitgak_paper.py scan               # 15:30~15:45, 주문 안 냄
    python scripts/run_bitgak_paper.py submit [--dry-run] # 같은 회차, MOC 제출
    python scripts/run_bitgak_paper.py settle             # 16:15 이후
    python scripts/run_bitgak_paper.py note --kind code --text "..."   # §3.5 개입
    python scripts/run_bitgak_paper.py report [--from YYYY-MM-DD]      # §1 판정선

사전 등록: `docs/superpowers/specs/2026-09-05-bitgak-stage8-design.md` (§11 그대로)

## 이 파일이 묻는 것 하나

> 7단계가 잰 규칙이 요구하는 주문 네 종류가, **하네스와 같은 선 위에서**,
> 제시간에, 규칙이 센 것과 1:1 로, 틀린 사건 없이 실제로 나가는가.

성과가 아니다. 기대값도 아니다(§4). **성립하는가**다. 이 파일은 평균 R 을
계산하지 않는다 — 계산하는 순간 사전 등록이 깨진다.

## 규칙 코드를 다시 안 짠다

발동 판정·채널선·사다리는 전부 `pilot_bitgak_power` 것을 import 한다. 유동성
컷은 `measure_bitgak._liq_mask` 그대로다. 러너가 규칙을 다시 짜면 §2.5 도구
게이트가 통과할 수 없고, 통과해도 그건 7단계가 잰 규칙이 아니다.

`lines_at()` 만 새로 짰다 — `scan()` 의 **한 봉짜리 판**이다. 그런데 그 한 봉에서
"오늘의 채널"을 새로 고르면 **하네스와 다른 선**이 나온다: scan() 은 보유 중인
구간을 통째로 건너뛰므로 그 사이의 채널 갱신 봉을 **안 본다**. 그래서 러너도
`_carried_channel()` 로 그 방문 순서를 되짚어 하네스가 그날 들고 있던 채널을
쓴다 — §8 갈래대로 **러너를 하네스에 맞춘다. 하네스는 안 고친다.**
(처음엔 매 봉 새로 고르면 같은 답이 난다고 봤다. 25종목 게이트에서 170건이
어긋났다 — 도구 게이트가 실제로 잡아낸 첫 결함이다.)

## 함정 셋

- **시세 피드는 sip 여야 한다.** 판정 행 패널이 `feed_name="sip"` 으로 지어졌다
  (`scripts/fetch_smallcap_panel.py`). iex 일봉의 거래량은 통합 대비 몇 %라
  ADV 500만 컷이 **다른 컷**이 된다. 게이트는 저장 패널 위에서 도니까 이걸
  못 잡는다 — 그래서 코드에 박아 둔다.
- **정정은 새 주문 id 를 만든다.** `alpaca_trading.replace_order` docstring 참조.
  장부가 id 를 갱신 안 하면 다음 날 정정이 죽은 주문을 때리고, 상호 취소가
  살아 있는 다리를 못 찾는다.
- **두 다리를 전량으로 동시에 거는 걸 브로커가 막을 수 있다.** J-2 스톱과 J-3
  지정가를 각각 보유 수량 전량으로 내면 매도 예약이 보유량의 2배가 된다.
  §11.1 이 OCO 를 명시적으로 안 쓰기로 했으므로 **있는 그대로 두 주문을 낸다** —
  막히면 그건 결함이 아니라 ① 의 관측이고 거절 사유 `insufficient_qty` 로
  분류된다.
  ponytail: 이 자리가 막히면 업그레이드 경로는 OCO 인데, 그건 백테스트가 잰 적
  없는 체결 규칙이라 새 사전 등록 사안이다(§11.1).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from modules import alpaca_trading as at  # noqa: E402
from modules.alpaca_data import get_bars, latest_quotes  # noqa: E402
from modules.ict_analysis import find_swing_points  # noqa: E402
from measure_bitgak import _liq_mask  # noqa: E402  — 유동성 컷도 그대로 쓴다
from pilot_bitgak_power import (  # noqa: E402  — 규칙은 하네스 것을 그대로
    HOLD, LARGECAP_PANEL, LARGECAP_UNIVERSE, LEVELS, MIN_LEN, MIN_RISK_PCT,
    POC_WIN, SWING_L, _channel, _level, _ohlcv, _qualify, _triple, scan,
    shape_at,
)

_ET = ZoneInfo("America/New_York")

# ── §2.2 번인 운영 파라미터 — 봉인. 결과를 보고 안 바꾼다 ──────────────
PREFIX       = "bitgak8-"
PROBE_PREFIX = "bitgak8probe-"
SHAPES       = ("breakout", "support")   # 자유도 I — I-a + I-b 둘 다
LIQ          = 5e6                       # 진입일 직전 20일 중위 거래대금
MIN_PX       = 5.0
NOTIONAL     = 1000.0                    # 트레이드당 명목 목표(달러)
MAX_NEW      = 5                         # 하루 신규 진입 상한
WINDOW       = (15, 30), (15, 50)        # 제출 창 (ET) — 15:50 이 브로커 컷오프
SETTLE_FROM  = (16, 15)                  # 체결 확정은 이 시각 이후
MIN_SUBMITS  = 15                        # §1 표본 문턱 — 못 넘으면 「미측정」
GATE_TOL     = 1e-6                      # §2.5 절대오차

LEDGER = Path("data/bitgak_paper_ledger.jsonl")
PROBE  = Path("data/bitgak_paper_probe.json")
GATE   = Path("data/bitgak_paper_gate.json")

# 장부 한 줄의 칸. **행 모양을 바꾸면 그 행을 읽는 자리를 전부 세어야 한다**
# (7단계 정오표 ⑤ · PR #218 이 같은 병으로 물린 자리). selftest ⑧ 은 합성 장부를
# 지어 report() 를 통째로 돌린다 — 읽는 자리가 스키마에 없는 칸을 참조하면 거기서
# 깨지고, 쓰는 자리가 스키마 밖 칸을 넣으면 `_append` 가 막는다.
_SCHEMA = {
    "scan":   ("date", "kind", "ts", "et", "universe_n", "eligible_n",
               "triggers", "selected", "holding", "note"),
    "submit": ("date", "kind", "ts", "et", "dry_run", "idempotent_skip",
               "submitted", "blocked", "rejected"),
    "settle": ("date", "kind", "ts", "et", "fills", "opened", "closed",
               "amended", "amend_failed", "canceled", "oversell",
               "leg_ok", "leg_total", "close_gap_bp", "positions"),
    "clean":  ("date", "kind", "ts", "et", "closed_positions", "canceled_orders"),
    "note":   ("date", "kind", "ts", "et", "cause", "text"),
}
_CAUSES = ("runner", "quote", "broker", "code", "boss")   # §3.5 개입 분류


# ── 시각 · 달력 ────────────────────────────────────────────────────────
def _et_now() -> datetime:
    return datetime.now(_ET)


def _in_window(now: datetime) -> bool:
    """15:30 ≤ 지금 < 15:50 ET. 크론 넷 중 창 밖 회차는 여기서 조용히 죽는다."""
    (h0, m0), (h1, m1) = WINDOW
    return (h0, m0) <= (now.hour, now.minute) < (h1, m1)


def _sessions(start, end) -> list[str]:
    """Alpaca 거래일 달력. 휴장일은 cron 이 모른다 — 스크립트가 판정한다."""
    resp = at._request_with_retry(
        "GET", f"{at.base_url()}/v2/calendar", headers=at._headers(),
        params={"start": str(start), "end": str(end)}, timeout=15)
    resp.raise_for_status()
    return [str(d["date"]) for d in resp.json()]


def _is_trading_day(d) -> bool:
    return str(d) in _sessions(d, d)


def _bars_since(entry_date: str, today: str) -> int:
    """진입 봉에서 오늘까지 **거래일** 수. 하네스의 `x − e` 와 같은 축이다."""
    return max(len(_sessions(entry_date, today)) - 1, 0)


# ── 안전 게이트 ────────────────────────────────────────────────────────
def _require_paper() -> None:
    """실계좌 키로는 이 번인이 한 줄도 안 돈다 (§12).

    9단계 전까지 실계좌는 이 저장소의 어떤 코드도 안 건드린다. 키를 잘못 넣은
    날 진짜 주문이 나가는 걸 막는 게 이 함수다.

    사전 등록 §12 는 `.env` 를 Alpaca 공식 이름(APCA_*)으로 적었고 이 저장소
    모듈은 ALPACA_* 를 읽는다. 이름을 한쪽으로 통일하는 대신 여기서 받아 준다 —
    다른 러너가 쓰는 이름을 8단계 사정으로 바꾸지 않는다.
    """
    for src, dst in (("APCA_API_KEY_ID", "ALPACA_API_KEY"),
                     ("APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY")):
        if os.environ.get(src) and not os.environ.get(dst):
            os.environ[dst] = os.environ[src]
    if not at.is_paper():
        raise SystemExit("실계좌가 열려 있습니다 — 8단계 번인은 페이퍼에서만 돕니다. "
                         "ALPACA_PAPER 를 지우거나 true 로 두세요.")


# ── 장부 ───────────────────────────────────────────────────────────────
def _append(row: dict) -> dict:
    fields = _SCHEMA[row["kind"]]
    missing, extra = set(fields) - set(row), set(row) - set(fields)
    if missing or extra:
        raise ValueError(f"장부 행 모양이 스키마와 다릅니다 ({row['kind']}): "
                         f"빠짐={sorted(missing)} 남음={sorted(extra)}")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _row(kind: str, now: datetime, **kw) -> dict:
    """한 줄 append 다 — 이미 적은 거래일 줄을 다시 열지 않는다.

    사전 등록 §11.2 는 "거래일 한 줄"이라 적었다. 실제로는 국면(scan·submit·
    settle)마다 붙인다: 적은 줄을 다시 여는 순간 러너가 중간에 죽은 날의 기록이
    통째로 날아간다. 읽는 쪽(`report`)이 날짜로 묶으므로 재현은 같다.
    """
    return {"date": now.strftime("%Y-%m-%d"), "kind": kind,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "et": now.strftime("%H:%M:%S"), **kw}


def _rows(since: str = "") -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if not since or r["date"] >= since:
                out.append(r)
    return out


def _open_positions(rows: list[dict]) -> list[dict]:
    """마지막 settle 행이 들고 있는 포지션 표. 되감기를 안 한다 — 그 행이 곧 상태다."""
    for r in reversed(rows):
        if r["kind"] == "settle":
            return r["positions"]
    return []


# ── 규칙 — 하네스의 한 봉짜리 판 ───────────────────────────────────────
def _carried_channel(df: pd.DataFrame, sw, qual, trades: list, i: int):
    """봉 i 에서 `scan()` 이 **들고 있던** 채널. 오늘 새로 고른 것과 다를 수 있다.

    scan() 은 채널을 바꿔야 할 봉(`change`)에서만 다시 고르고, 보유 중에는 봉을
    통째로 건너뛴다(`i = x + 1`) — **건너뛴 구간의 change 봉은 안 본다.** 그래서
    "오늘 새로 고른 채널"은 하네스가 그날 쓴 채널이 아니다.

    건너뛴 구간은 scan() 이 낸 트레이드의 (진입, 청산] 이다. 규칙을 다시 짠 게
    아니라 **scan() 자신의 출력으로 그 루프의 방문 순서를 되짚는다.**

    반환은 `(채널, 3점)` — 3점은 규칙에 안 쓰이고 차트가 변곡점을 찍는 재료다
    (`modules/bitgak_chart.py`). 채널이 없으면 둘 다 None.
    """
    n = len(df)
    change = {int(v) for v in np.r_[sw["idx"].values + SWING_L,
                                    qual[np.isfinite(qual)]] if v < n}
    resume = {t["idx"]: t["exit"] + 1 for t in trades}
    ch, pts, v = None, None, max(MIN_LEN, POC_WIN + SWING_L)
    while v <= i:
        if ch is None or v in change:
            t3 = _triple(sw, qual, v)
            ch = _channel(t3) if t3 else None
            pts = t3 if ch else None
        v = resume.get(v, v + 1)
    return ch, pts


def lines_at(df: pd.DataFrame, shapes: tuple = SHAPES,
             trades: list | None = None, sw=None, qual=None, ch=None) -> dict | None:
    """마지막 봉에서 발동했는가, 그리고 **그날의 채널선 셋**.

    산수는 전부 `pilot_bitgak_power` 함수를 부른다. 채널은 `_carried_channel` 이
    하네스와 같은 것을 집어 준다. 반환값의 `slope/offset/base/k/k_stop` 은 다리를
    매일 다시 그리는 재료다 — scan() 의 청산 루프가 **진입 시점 채널**을 그대로
    쓰므로 러너도 진입 때 고정해서 들고 간다.

    `trades` 를 주면 그 스캔을 재사용한다(게이트가 종목당 한 번만 스캔하려고 쓴다).
    `sw`/`qual`/`ch` 도 같은 이유의 손잡이다 — 이미 센 것을 다시 안 세게만 하고,
    안 주면 여기서 똑같이 짓는다. 규칙은 어느 쪽이든 같다.
    """
    i = len(df) - 1
    if i < max(MIN_LEN, POC_WIN + SWING_L):
        return None
    if sw is None:
        sw = find_swing_points(df, lookback=SWING_L)
    if len(sw) < 3:
        return None
    if qual is None:
        qual = _qualify(df, sw)
    if trades is None:
        trades = scan(df, shapes=shapes, fill="gap", entry="close")
    if ch is None:
        ch, _ = _carried_channel(df, sw, qual, trades, i)
    if ch is None:
        return None

    cl, lo = df["Close"].values, df["Low"].values
    rung = sorted(LEVELS, key=lambda k: _level(ch, k, i))
    hit = None
    for pos, k in enumerate(rung):
        if pos == 0:            # 아래에 선이 없다 = 손절 못 건다
            continue
        shape = shape_at(cl[i - 1], cl[i], lo[i],
                         _level(ch, k, i - 1), _level(ch, k, i))
        if shape in shapes:     # 가장 **높은** 발동 선을 취한다
            hit = (k, rung[pos - 1], shape)
    if hit is None:
        return None
    k, k_stop, shape = hit

    px    = float(cl[i])
    entry = _level(ch, k, i)
    stop  = _level(ch, k_stop, i)
    risk  = px - stop
    if risk <= 0 or risk / px < MIN_RISK_PCT:
        return None
    slope, x1, y1, offset = ch
    return {
        "shape": shape, "px": px,
        "entry_lvl":  float(entry),
        "stop_lvl":   float(stop),
        "target_lvl": float(_level(ch, k + (k - k_stop), i)),
        # scan() 이 트레이드마다 적는 두 값을 **같은 식·같은 dtype 으로** 다시 센다.
        # 도구 게이트가 이걸 맞대 본다 — 자세한 이유는 `_gate_job`.
        "margin":   float((cl[i] - entry) / cl[i]),
        "risk_pct": float((cl[i] - stop) / cl[i]),
        "slope": float(slope), "offset": float(offset),
        "base":  float(y1 + slope * (i - x1)),
        "k": float(k), "k_stop": float(k_stop),
    }


def legs_on(p: dict, n: int) -> tuple[float, float]:
    """진입 후 n 거래일째의 (손절선, 익절선).

    사다리는 등차라 익절 k+step = 2k − k_stop 이다. 즉 **익절선 = 2×진입선 −
    손절선** — 새 자유도가 아니라 같은 사다리의 산수다. 선이 기울어져 있어서
    n 이 하루 늘 때마다 두 값이 slope 만큼 같이 움직인다(§3.4).
    """
    base = p["base"] + p["slope"] * n
    return (round(base + p["k_stop"] * p["offset"], 2),
            round(base + (2 * p["k"] - p["k_stop"]) * p["offset"], 2))


# ── 시세 ───────────────────────────────────────────────────────────────
def _last_snapshot() -> pd.DataFrame:
    u = pd.read_parquet(LARGECAP_UNIVERSE, columns=["date", "ticker", "asset_id"])
    return u.loc[u["date"] == u["date"].max()]


def universe() -> list[str]:
    """판정 행 유니버스의 **가장 최근 기준일** 구성종목 — 티커(주문용)."""
    return sorted(_last_snapshot()["ticker"].dropna().unique().tolist())


def universe_ids() -> list[str]:
    """같은 구성종목을 **asset_id**(`cik:TICKER`)로. 저장 패널의 칸 이름이 이거다.

    티커로 맞추면 안 된다 — 재활용 티커는 지금 주인의 이력을 준다
    ([[fscore-smallcap-prereg]]). cik 이 붙은 id 가 그 사고를 막는 자리다.
    """
    return sorted(_last_snapshot()["asset_id"].dropna().unique().tolist())


def daily_bars(symbols: list[str], calendar_days: int = 1100,
               chunk: int = 100) -> dict[str, pd.DataFrame]:
    """조정 일봉. **sip 고정** — 판정 행 패널과 같은 피드여야 컷이 같은 컷이다."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=calendar_days)
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), chunk):
        out.update(get_bars(symbols[i:i + chunk], timeframe="1Day",
                            start=start, end=end, feed_name="sip",
                            adjustment="all", max_pages=100))
    return out


# ── scan · submit ──────────────────────────────────────────────────────
def _coid(kind: str, day: str, symbol: str) -> str:
    """`bitgak8-` 접두사가 붙은 주문 id (§2.3).

    진입·타임아웃은 **그날 날짜가 앞**이라 `bitgak8-{오늘}-` 조회 하나로 멱등을
    본다. 다리(stop/limit)는 매일 정정하며 새 id 를 받으므로 날짜를 뒤에 붙여
    그 조회에 안 걸리게 한다 — 안 그러면 어제 걸어 둔 다리가 오늘 제출을 막는다.
    """
    if kind in ("entry", "timeout"):
        return f"{PREFIX}{day}-{symbol}-{kind}"
    return f"{PREFIX}{symbol}-{kind}-{day}"


def plan(now: datetime, holding: list[str]) -> dict:
    """그날 15:50 의 **결정 집합**. ③ 대조가 이것과 실제 주문을 맞춘다."""
    syms = universe()
    frames = daily_bars(syms)
    triggers, eligible = [], 0
    for sym, df in sorted(frames.items()):
        df = df.dropna()
        if len(df) < MIN_LEN + 1:
            continue
        if not bool(_liq_mask(df, LIQ, MIN_PX)[-1]):   # 컷도 하네스 것 그대로
            continue
        eligible += 1
        sig = lines_at(df)
        if sig is not None:
            triggers.append({"symbol": sym, **sig})

    day = now.strftime("%Y-%m-%d")
    # 선정은 결정적이고 **여유·성과와 무관**하다(§2.2). 여유가 큰 것만 고르면
    # ②·③ 이 쉬워지는 쪽으로 표본이 쏠린다 — 배관을 재는 자가 유리한 날만 본다.
    pool = [t for t in triggers if t["symbol"] not in holding]  # 종목당 동시 1포지션
    pool.sort(key=lambda t: zlib.crc32(f"{day}:{t['symbol']}".encode()))
    return {"universe_n": len(syms), "eligible_n": eligible,
            "triggers": triggers, "selected": pool[:MAX_NEW]}


def _classify(msg: str) -> str:
    """거절 사유. **분류 안 된 거절이 한 건이라도 남으면 ① 은 통과가 아니다.**"""
    m = msg.lower()
    if "insufficient buying power" in m or "buying_power" in m:
        return "buying_power"
    if "insufficient qty" in m or "held_for_orders" in m:
        return "insufficient_qty"   # 두 다리를 전량으로 동시에 건 자리(모듈 함정 3)
    if "cls" in m or "on_close" in m or "on-close" in m or "closing auction" in m:
        return "moc_unsupported"
    if "fractional" in m or "qty" in m or "quantity" in m:
        return "qty"
    if "not tradable" in m or "halt" in m or "asset" in m:
        return "asset"
    if "cutoff" in m or "too late" in m or "market is closed" in m:
        return "late"               # ② 로 넘어간다
    return "unclassified"


def _today_orders(day: str) -> list[dict]:
    """오늘 낸 진입·타임아웃 주문. 크론 넷의 멱등은 이 조회 하나로 선다."""
    resp = at._request_with_retry(
        "GET", f"{at.base_url()}/v2/orders", headers=at._headers(),
        params={"status": "all", "limit": 500,
                "after": f"{day[:4]}-{day[4:6]}-{day[6:]}T00:00:00Z"}, timeout=20)
    resp.raise_for_status()
    return [o for o in resp.json()
            if str(o.get("client_order_id", "")).startswith(f"{PREFIX}{day}-")]


def _log_scan(now: datetime, p: dict, holding: list[str], note: str) -> None:
    _append(_row("scan", now, universe_n=p["universe_n"], eligible_n=p["eligible_n"],
                 triggers=[t["symbol"] for t in p["triggers"]],
                 selected=[t["symbol"] for t in p["selected"]],
                 holding=holding, note=note))
    print(f"[scan] {now:%Y-%m-%d %H:%M} ET 유니버스 {p['universe_n']} · "
          f"컷 통과 {p['eligible_n']} · 발동 {len(p['triggers'])} · "
          f"선정 {[t['symbol'] for t in p['selected']]}")


def cmd_scan() -> int:
    """드라이런 관측용. 주문을 안 내므로 ③ 판정 대조에는 안 들어간다."""
    now = _et_now()
    holding = [q["symbol"] for q in _open_positions(_rows())]
    _log_scan(now, plan(now, holding), holding, "scan-only")
    return 0


def cmd_submit(dry_run: bool = False) -> int:
    now = _et_now()
    if not _in_window(now):
        print(f"[submit] 창 밖({now:%H:%M} ET) — 아무것도 안 합니다.")
        return 0
    if not _is_trading_day(now.date()):
        print(f"[submit] 휴장일({now:%Y-%m-%d}) — 아무것도 안 합니다.")
        return 0

    day = now.strftime("%Y%m%d")
    if not dry_run and _today_orders(day):
        # 크론 넷의 전제. 이게 없으면 대책이 아니라 **중복 주문 기계**다(§3.2).
        _append(_row("submit", now, dry_run=False, idempotent_skip=True,
                     submitted=[], blocked=[], rejected=[]))
        print("[submit] 오늘 주문이 이미 있습니다 — 멱등 종료.")
        return 0

    positions = _open_positions(_rows())
    holding = [q["symbol"] for q in positions]
    p = plan(now, holding)
    # 스캔과 제출이 **같은 계산**에서 나온다. 두 번 스캔하면 15:50 결정 집합이
    # 회차마다 갈라져 ③ 대조가 무엇에 대조하는지 알 수 없게 된다.
    _log_scan(now, p, holding, "with-submit")

    buying_power = 0.0 if dry_run else at.get_account()["buying_power"]
    submitted, blocked, rejected = [], [], []

    for t in p["selected"]:
        qty = max(1, math.floor(NOTIONAL / t["px"]))
        cost = qty * t["px"]
        if not dry_run and cost > buying_power:
            # 사전 등록된 예외(§3.3) — 누락으로 안 세되 **건수를 반드시 보고한다**.
            blocked.append({"symbol": t["symbol"], "qty": qty, "need": round(cost, 2),
                            "have": round(buying_power, 2), "cause": "buying_power"})
            continue
        try:
            o = at.place_moc_buy(t["symbol"], qty, dry_run=dry_run,
                                 client_order_id=_coid("entry", day, t["symbol"]))
            buying_power -= cost
            submitted.append({
                "symbol": t["symbol"], "leg": "entry", "qty": qty,
                "order_id": o["id"], "status": o["status"],
                "px_at_submit": t["px"], "shape": t["shape"],
                "channel": {kk: t[kk] for kk in ("slope", "offset", "base", "k",
                                                 "k_stop", "entry_lvl", "stop_lvl",
                                                 "target_lvl")}})
        except Exception as e:
            rejected.append({"symbol": t["symbol"], "leg": "entry",
                             "cause": _classify(str(e)), "msg": str(e)[:300]})

    # 타임아웃 MOC 은 **같은 창**에서 나간다 — 청산이 40봉째 종가라 그날 내야 한다.
    # 그래서 보유 봉수 계산이 진입 스캔과 같은 회차에 들어간다(§3.5).
    today = now.strftime("%Y-%m-%d")
    for q in positions:
        if _bars_since(q["entry_date"], today) < HOLD:
            continue
        try:
            o = at.place_moc_sell(q["symbol"], q["qty"], dry_run=dry_run,
                                  client_order_id=_coid("timeout", day, q["symbol"]))
            submitted.append({"symbol": q["symbol"], "leg": "timeout", "qty": q["qty"],
                              "order_id": o["id"], "status": o["status"],
                              "px_at_submit": None, "shape": None, "channel": None})
        except Exception as e:
            rejected.append({"symbol": q["symbol"], "leg": "timeout",
                             "cause": _classify(str(e)), "msg": str(e)[:300]})

    _append(_row("submit", now, dry_run=dry_run, idempotent_skip=False,
                 submitted=submitted, blocked=blocked, rejected=rejected))
    print(f"[submit] {now:%H:%M} ET 제출 {len(submitted)} · 매수여력 막힘 "
          f"{len(blocked)} · 거절 {len(rejected)}{' (DRY RUN)' if dry_run else ''}")
    return 0


# ── settle ────────────────────────────────────────────────────────────
def _order(order_id: str) -> dict:
    resp = at._request_with_retry("GET", f"{at.base_url()}/v2/orders/{order_id}",
                                  headers=at._headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def _official_close(symbol: str, day: str) -> float | None:
    """공식 종가. MOC 체결가와의 차이는 **판정 아님** — 9단계로 넘기는 관측치다(§8.1)."""
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        df = get_bars([symbol], timeframe="1Day", start=start,
                      end=datetime.now(timezone.utc), feed_name="sip",
                      adjustment="all").get(symbol)
        return float(df["Close"].iloc[0]) if df is not None and len(df) else None
    except Exception:
        return None


def cmd_settle(dry_run: bool = False) -> int:
    now = _et_now()
    today = now.strftime("%Y-%m-%d")
    if not _is_trading_day(now.date()):
        print(f"[settle] 휴장일({today}) — 아무것도 안 합니다.")
        return 0
    if (now.hour, now.minute) < SETTLE_FROM:
        print(f"[settle] 아직 {now:%H:%M} ET — 16:15 이후에 돕니다.")
        return 0

    rows = _rows()
    positions = {q["symbol"]: dict(q) for q in _open_positions(rows)}
    day = now.strftime("%Y%m%d")

    # 1) 오늘 낸 MOC 의 체결 확정 → 장부 기록
    fills, opened, gaps = [], [], []
    for r in rows:
        if r["kind"] != "submit" or r["date"] != today or r["dry_run"]:
            continue
        for s in r["submitted"]:
            info = _order(s["order_id"])
            status = str(info.get("status", ""))
            px  = info.get("filled_avg_price")
            qty = int(float(info.get("filled_qty") or 0))
            fills.append({"symbol": s["symbol"], "leg": s["leg"], "status": status,
                          "qty": qty,
                          "fill_price": float(px) if px not in (None, "") else None})
            if status != "filled" or qty < 1:
                continue
            close = _official_close(s["symbol"], today)
            if close and px:
                gaps.append({"symbol": s["symbol"], "leg": s["leg"],
                             "bp": round((float(px) - close) / close * 1e4, 2)})
            if s["leg"] == "entry":
                positions[s["symbol"]] = {
                    "symbol": s["symbol"], "entry_date": today, "qty": qty,
                    "entry_fill": float(px), "stop_id": "", "limit_id": "",
                    "stop_px": None, "target_px": None, **s["channel"]}
                opened.append(s["symbol"])

    # 2) 상호 취소 — 브로커 포지션에서 사라진 종목의 **남은 다리**를 지운다.
    #    한쪽이 체결됐는데 반대쪽을 안 지우면 없는 주식을 판다. 실계좌에서는
    #    **돈이 새는 종류**라, 성립 검사를 실계좌 앞에 두는 이유가 이 줄이다(§3.4).
    held = {p["symbol"]: int(float(p["qty"])) for p in at.get_positions()}
    closed, canceled = [], []
    for sym in list(positions):
        if held.get(sym, 0) >= 1:
            continue
        for leg in ("stop_id", "limit_id"):
            oid = positions[sym][leg]
            if oid and at.cancel_order(oid):
                canceled.append({"symbol": sym, "leg": leg, "order_id": oid})
        closed.append(sym)
        positions.pop(sym)

    # 3) 다리 등록·정정 — 채널선이 기울어져 매 거래일 두 값이 바뀐다(§3.4).
    amended, amend_failed, oversell = [], [], []
    leg_ok = leg_total = 0
    for sym, q in positions.items():
        stop_px, target_px = legs_on(q, _bars_since(q["entry_date"], today))
        broker_qty = held.get(sym, 0)
        if q["qty"] > broker_qty:
            # 보유보다 많이 팔려는 주문은 **절대** 안 낸다(④ 초과 매도 0건).
            oversell.append({"symbol": sym, "want": q["qty"], "have": broker_qty})
            continue
        leg_total += 1
        for leg, key, place, price in (
                ("stop",  "stop_id",  at.place_stop_sell,  stop_px),
                ("limit", "limit_id", at.place_limit_sell, target_px)):
            try:
                if q[key]:
                    kw = {"stop_price": price} if leg == "stop" else {"limit_price": price}
                    o = at.replace_order(q[key], dry_run=dry_run,
                                         client_order_id=_coid(leg, day, sym), **kw)
                    amended.append({"symbol": sym, "leg": leg, "price": price})
                else:
                    o = place(sym, q["qty"], price, dry_run=dry_run, tif="gtc",
                              client_order_id=_coid(leg, day, sym))
                q[key] = o["id"]        # 정정은 **새 id** 를 만든다
            except Exception as e:
                amend_failed.append({"symbol": sym, "leg": leg,
                                     "cause": _classify(str(e)), "msg": str(e)[:300]})
        q["stop_px"], q["target_px"] = stop_px, target_px
        if q["stop_id"] and q["limit_id"]:
            leg_ok += 1

    _append(_row("settle", now, fills=fills, opened=opened, closed=closed,
                 amended=amended, amend_failed=amend_failed, canceled=canceled,
                 oversell=oversell, leg_ok=leg_ok, leg_total=leg_total,
                 close_gap_bp=gaps, positions=list(positions.values())))
    print(f"[settle] 체결확인 {len(fills)} · 신규 {len(opened)} · 청산 {len(closed)} · "
          f"다리 {leg_ok}/{leg_total} · 정정 {len(amended)} · 실패 {len(amend_failed)}")
    return 0


# ── clean · probe · note ───────────────────────────────────────────────
def cmd_clean(yes: bool = False) -> int:
    """§2.3 — 번인 전에 계좌를 비운다.

    남의 포지션 10종목은 **매수여력을 먹고**, 빗각 유니버스와 겹치면 「종목당
    동시 1포지션」과 충돌해 ③ 대조가 노이즈가 아니라 **오염**이 된다.
    """
    pos = at.get_positions()
    print(f"페이퍼 계좌 보유 {len(pos)}종목: {[p['symbol'] for p in pos]}")
    if not yes and input("전량 시장가 청산 + 미체결 전량 취소 — 진행할까요? (yes): ") != "yes":
        print("취소했습니다.")
        return 1
    r1 = at._request_with_retry("DELETE", f"{at.base_url()}/v2/positions",
                                headers=at._headers(),
                                params={"cancel_orders": "true"}, timeout=30)
    r2 = at._request_with_retry("DELETE", f"{at.base_url()}/v2/orders",
                                headers=at._headers(), timeout=30)
    _append(_row("clean", _et_now(), closed_positions=[p["symbol"] for p in pos],
                 canceled_orders=r2.status_code in (200, 207)))
    print(f"청산 {r1.status_code} · 주문취소 {r2.status_code}")
    return 0 if r1.status_code in (200, 207) else 1


def cmd_probe(symbol: str = "AAPL", wait_min: float = 10.0,
              dry_run: bool = False) -> int:
    """§3.1 — 스톱·지정가 **체결 경로**만 밟는다. 체결 **가격**은 안 본다(§4).

    5 거래일 안에 손절선·익절선에 실제로 닿는다는 보장이 없어서 번인과 분리해
    돌린다. 접두사가 `bitgak8probe-` 라 장부에도 ③ 대조에도 안 들어간다.
    """
    q = latest_quotes([symbol]).get(symbol)
    if not q:
        raise SystemExit(f"{symbol} 호가를 못 받았습니다 — 장중에 돌리세요.")

    out = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "symbol": symbol, "legs": {}}
    for leg in ("limit", "stop"):
        buy = at.place_notional_buy(symbol, round(q["ask"] * 1.02, 2), dry_run=dry_run)
        got = at.wait_for_fill(buy["id"], timeout=90)
        if not dry_run and got.get("status") != "filled":
            out["legs"][leg] = {"status": f"entry_{got.get('status')}", "order_id": ""}
            continue
        px = latest_quotes([symbol]).get(symbol, q)
        # 지정가 매도는 **매수호가 아래**에 건다. "현재가 바로 위"에 걸면 몇 분
        # 안에 안 터질 수 있고, 여기서 보는 건 가격이 아니라 경로다(§3.1).
        cid = f"{PROBE_PREFIX}{leg}-{int(time.time())}"
        if leg == "limit":
            o = at.place_limit_sell(symbol, 1, round(px["bid"] - 0.01, 2),
                                    dry_run=dry_run, tif="day", client_order_id=cid)
        else:
            o = at.place_stop_sell(symbol, 1, round(px["bid"] * 0.999 - 0.01, 2),
                                   dry_run=dry_run, tif="day", client_order_id=cid)
        res = at.wait_for_fill(o["id"], timeout=int(wait_min * 60))
        out["legs"][leg] = {"status": res.get("status"), "order_id": o["id"]}
        if res.get("status") != "filled":
            at.cancel_order(o["id"])
            at.place_market_sell(symbol, 1, dry_run=dry_run)
        print(f"  프로브 {leg}: {res.get('status')}")

    PROBE.parent.mkdir(parents=True, exist_ok=True)
    PROBE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_note(cause: str, text: str) -> int:
    """§3.5 — 개입을 분류해 남긴다. **분류가 곧 9단계 숙제 목록이다.**"""
    if cause not in _CAUSES:
        raise SystemExit(f"--kind 는 {'|'.join(_CAUSES)} 중 하나입니다.")
    _append(_row("note", _et_now(), cause=cause, text=text))
    print(f"[note] {cause}: {text}")
    return 0


# ── §2.5 도구 게이트 ───────────────────────────────────────────────────
def _gate_job(args):
    """저장 패널 위에서 **하네스 선값 ↔ 러너 선값**. 달력을 하나도 안 쓴다.

    scan() 은 선값을 직접 안 내지만 `margin`·`risk_pct` 로 정확히 되살아난다:
    진입선 = 종가×(1−margin), 손절선 = 종가×(1−risk_pct). 익절선은 사다리가
    등차라 2×진입선 − 손절선이다(`legs_on` 과 같은 산수).
    """
    tk, df, days = args
    cl = df["Close"].values.astype("float64")
    first = max(len(df) - days, 0)
    bad, worst, n = [], 0.0, 0
    trades = scan(df, shapes=SHAPES, fill="gap", entry="close")
    for t in trades:
        i = t["idx"]
        if i < first:
            continue
        n += 1
        sig = lines_at(df.iloc[:i + 1], trades=trades)
        if sig is None:
            bad.append({"ticker": tk, "idx": int(i), "why": "runner_no_signal"})
            continue
        if sig["shape"] != t["shape"]:
            bad.append({"ticker": tk, "idx": int(i), "why": "shape",
                        "harness": t["shape"], "runner": sig["shape"]})
            continue
        # 달러로 옮긴 오차. **선값을 margin 에서 되살리지 않는다** — 되살리면
        # 자가 러너보다 무디다: 패널이 float32 라 `cl[i] - 선값` 이 NumPy 2 의
        # 약한 스칼라 규칙(NEP 50)대로 float32 로 내려앉고, margin 은 그 순간
        # 상대오차 1e-7 을 먹는다(159달러에서 약 2e-5). 그건 하네스가 원래
        # 그렇게 적은 값이라 러너가 아무리 맞아도 1e-6 을 못 넘는다. 그래서
        # **하네스가 실제로 적은 두 값을 같은 식으로 다시 세서 맞댄다** —
        # 채널이 같으면 비트까지 같고, 다르면 벌어진다.
        err = cl[i] * max(abs(sig["margin"] - t["margin"]),
                          abs(sig["risk_pct"] - t["risk_pct"]))
        # 익절선은 사다리가 등차라는 산수다 — 그 산수가 맞는지는 여기서 본다.
        err = max(err, abs(sig["target_lvl"]
                           - (2 * sig["entry_lvl"] - sig["stop_lvl"])))
        worst = max(worst, err)
        if err > GATE_TOL:
            bad.append({"ticker": tk, "idx": int(i), "why": "level", "err": float(err)})
    return tk, n, worst, bad


def cmd_gate(days: int = 60, workers: int = 0) -> int:
    from concurrent.futures import ProcessPoolExecutor
    panel = pd.read_parquet(LARGECAP_PANEL)
    keep = set(universe_ids())
    tasks = []
    for tk in sorted({t for _, t in panel.columns}):
        if tk not in keep:
            continue
        df = _ohlcv(panel, tk)
        if len(df) >= MIN_LEN:
            tasks.append((tk, df, days))
    workers = workers or max((os.cpu_count() or 2) - 1, 1)
    print(f"[gate] {len(tasks)}종목 × 최근 {days}봉 · 워커 {workers}", flush=True)

    checked, worst, bad = 0, 0.0, []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for _tk, n, w, b in pool.map(_gate_job, tasks):
            checked += n
            worst = max(worst, w)
            bad += b
    res = {"days": days, "symbols": len(tasks), "checked": checked,
           "max_abs_err": worst, "tol": GATE_TOL, "n_mismatch": len(bad),
           "mismatches": bad[:200], "ok": not bad}
    GATE.parent.mkdir(parents=True, exist_ok=True)
    GATE.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gate] 대조 {checked}건 · 최대오차 {worst:.3e} · 불일치 {len(bad)}건 → "
          f"{'통과' if not bad else '실패 — 진행 안 함'}")
    return 0 if not bad else 1


# ── §1 판정 ───────────────────────────────────────────────────────────
def report(rows: list[dict], probe: dict | None = None,
           gate: dict | None = None) -> dict:
    """판정선 다섯 + 표본 문턱. **성과는 한 줄도 안 센다**(§4).

    비율 문턱이 하나도 없다는 게 이 함수의 모양이다 — 5일로 98% 를 말하면 그건
    신뢰구간이 아니라 희망이고, 비율은 9단계 몫이다(§1).
    """
    subs  = [r for r in rows if r["kind"] == "submit" and not r["dry_run"]]
    setts = [r for r in rows if r["kind"] == "settle"]
    notes = [r for r in rows if r["kind"] == "note"]
    days  = sorted({r["date"] for r in subs})

    # ① 주문 — 네 종류가 각각 최소 1건 성립 · 분류 안 된 거절 0건
    filled = {"entry": 0, "timeout": 0, "stop": 0, "limit": 0}
    for r in setts:
        for f in r["fills"]:
            if f["status"] == "filled" and f["leg"] in filled:
                filled[f["leg"]] += 1
    for leg in ("stop", "limit"):
        if (probe or {}).get("legs", {}).get(leg, {}).get("status") == "filled":
            filled[leg] += 1        # 프로브도 성립으로 센다(§3.1)
    causes: dict[str, int] = {}
    for x in ([y for r in subs for y in r["rejected"]]
              + [y for r in setts for y in r["amend_failed"]]):
        causes[x["cause"]] = causes.get(x["cause"], 0) + 1

    # ② 마감 — 15:50 ET 전 제출, 중복 0건
    (h1, m1) = WINDOW[1]
    late = [r["date"] for r in subs
            if r["submitted"] and (int(r["et"][:2]), int(r["et"][3:5])) >= (h1, m1)]
    dup = []
    for d in days:
        seen: dict[tuple, int] = {}
        for r in subs:
            if r["date"] == d:
                for s in r["submitted"]:
                    key = (s["symbol"], s["leg"])
                    seen[key] = seen.get(key, 0) + 1
        dup += [f"{d}:{k[0]}:{k[1]}" for k, v in seen.items() if v > 1]

    # ③ 대조 — 「15:50 스캔·선정」 대 「실제 제출」. 양방향 불일치 0건.
    #    매수여력 부족만 사전 등록된 예외이고, 그 외 모든 누락은 불일치다(§3.3).
    ghost, missing, blocked_n = [], [], 0
    for d in days:
        sel = set()
        for r in rows:
            if r["kind"] == "scan" and r["date"] == d and r["note"] == "with-submit":
                sel |= set(r["selected"])
        sent, blk = set(), set()
        for r in subs:
            if r["date"] == d:
                sent |= {s["symbol"] for s in r["submitted"] if s["leg"] == "entry"}
                blk  |= {b["symbol"] for b in r["blocked"]}
        blocked_n += len(blk)
        ghost   += [f"{d}:{s}" for s in sorted(sent - sel)]
        missing += [f"{d}:{s}" for s in sorted(sel - sent - blk)]

    # ④ 유지 — 포지션-일 100%, 초과 매도 0건
    leg_ok = sum(r["leg_ok"] for r in setts)
    leg_total = sum(r["leg_total"] for r in setts)
    oversell = sum(len(r["oversell"]) for r in setts)

    # ⑤ 운영 — 무개입 완주 ≥4/5, 코드 결함 개입 0건
    code_notes = [n for n in notes if n["cause"] == "code"]
    touched = {n["date"] for n in notes}

    n_entry = sum(len([s for s in r["submitted"] if s["leg"] == "entry"])
                  for r in subs)
    out = {
        "days": days,
        "sample":   {"entry_submitted": n_entry, "threshold": MIN_SUBMITS,
                     "ok": n_entry >= MIN_SUBMITS},
        "gate":     {"ok": bool((gate or {}).get("ok")),
                     "max_abs_err": (gate or {}).get("max_abs_err"),
                     "n_mismatch": (gate or {}).get("n_mismatch")},
        "1_orders": {"filled": filled, "causes": causes,
                     "unclassified": causes.get("unclassified", 0),
                     "ok": all(v >= 1 for v in filled.values())
                           and not causes.get("unclassified")},
        "2_cutoff": {"late_days": late, "duplicates": dup,
                     "ok": not late and not dup},
        "3_match":  {"ghost": ghost, "missing": missing,
                     "blocked_buying_power": blocked_n,
                     "ok": not ghost and not missing},
        "4_legs":   {"leg_ok": leg_ok, "leg_total": leg_total, "oversell": oversell,
                     "ok": leg_total > 0 and leg_ok == leg_total and oversell == 0},
        "5_ops":    {"clean_days": len([d for d in days if d not in touched]),
                     "of": len(days), "code_interventions": len(code_notes),
                     "interventions": [[n["date"], n["cause"], n["text"]]
                                       for n in notes],
                     "ok": len([d for d in days if d not in touched]) >= 4
                           and not code_notes},
        # 판정 아님 — 9단계에 넘기는 관측치(§8.1). 슬리피지·기대값은 여기 없다(§4).
        "observed": {"close_gap_bp": [g for r in setts for g in r["close_gap_bp"]],
                     "amends": sum(len(r["amended"]) for r in setts),
                     "amend_failed": sum(len(r["amend_failed"]) for r in setts),
                     "cron_et": [[r["date"], r["et"], r["idempotent_skip"]]
                                 for r in subs]},
    }
    lines = ("gate", "1_orders", "2_cutoff", "3_match", "4_legs", "5_ops")
    out["verdict"] = ("미측정" if not out["sample"]["ok"]
                      else "배관 성립" if all(out[k]["ok"] for k in lines)
                      else "배관 실패")
    return out


def cmd_report(since: str = "") -> int:
    probe = json.loads(PROBE.read_text(encoding="utf-8")) if PROBE.exists() else None
    gate  = json.loads(GATE.read_text(encoding="utf-8")) if GATE.exists() else None
    out = report(_rows(since), probe, gate)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n판정: {out['verdict']}   "
          "(통과해도 노선은 「보류」다 — 8단계는 실전 투입을 승인하지 않는다, §5)")
    return 0 if out["verdict"] == "배관 성립" else 1


# ── selftest — §11.4 신규 8종 ─────────────────────────────────────────
class _FakeBroker:
    """네트워크 없는 브로커. `sent` 에 주문 body 가 그대로 쌓인다."""

    def __init__(self):
        self.sent, self.canceled, self.positions = [], [], []

    def submit(self, body, *a):
        self.sent.append(body)
        return {"id": f"o{len(self.sent)}", "status": "accepted", "_raw": body}

    def cancel(self, oid, *a, **k):
        self.canceled.append(oid)
        return True

    def get_positions(self, *a, **k):
        return self.positions


def _pos_row(**kw) -> dict:
    base = {"symbol": "AAPL", "entry_date": "2026-09-01", "qty": 5,
            "entry_fill": 200.0, "stop_id": "", "limit_id": "",
            "stop_px": None, "target_px": None,
            "slope": 0.1, "offset": 1.0, "base": 100.0, "k": 1.0, "k_stop": 0.5}
    return {**base, **kw}


def selftest() -> None:
    import tempfile

    fake = _FakeBroker()
    saved_at = {k: getattr(at, k) for k in
                ("_submit", "cancel_order", "get_positions", "get_account")}
    saved_g = {k: globals()[k] for k in
               ("plan", "_today_orders", "_is_trading_day", "_in_window",
                "_bars_since", "_et_now", "LEDGER")}
    at._submit, at.cancel_order, at.get_positions = (
        fake.submit, fake.cancel, fake.get_positions)
    try:
        # ① MOC body 가 정확히 market + cls. 오타 하나면 Alpaca 가 day 시장가로
        #    받아 **장중에 체결**된다 — 규칙이 아닌 매매가 된다.
        at.place_moc_buy("AAPL", 3, client_order_id="bitgak8-20260908-AAPL-entry")
        assert fake.sent[-1] == {"symbol": "AAPL", "qty": "3", "side": "buy",
                                 "type": "market", "time_in_force": "cls",
                                 "client_order_id": "bitgak8-20260908-AAPL-entry"}, \
            fake.sent[-1]
        at.place_moc_sell("AAPL", 3)
        assert fake.sent[-1]["side"] == "sell"
        assert fake.sent[-1]["time_in_force"] == "cls"

        # ② place_stop_sell 기본 호출이 지금과 **글자 그대로** 같은 body(회귀 방지).
        at.place_stop_sell("AAPL", 1, 100.0)
        assert fake.sent[-1] == {"symbol": "AAPL", "qty": "1", "side": "sell",
                                 "type": "stop", "stop_price": "100.0",
                                 "time_in_force": "day"}, fake.sent[-1]
        at.place_stop_sell("AAPL", 1, 100.0, tif="gtc")
        assert fake.sent[-1]["time_in_force"] == "gtc"

        # ④ client_order_id 접두사 — 진입/타임아웃은 날짜가 앞, 다리는 뒤,
        #    프로브는 아예 다른 접두사라 오늘 조회·③ 대조에서 빠진다.
        assert _coid("entry", "20260908", "AAPL") == "bitgak8-20260908-AAPL-entry"
        assert _coid("stop", "20260908", "AAPL") == "bitgak8-AAPL-stop-20260908"
        assert _coid("stop", "20260908", "AAPL").startswith(PREFIX)
        assert not _coid("stop", "20260908", "AAPL").startswith(f"{PREFIX}20260908-")
        assert not f"{PROBE_PREFIX}limit-1".startswith(f"{PREFIX}20260908-")

        # ⑥ 정정값 = 그날 하네스 선값 (§2.5 도구 게이트의 단위 테스트판).
        ch = _channel((10, 100.0, 60, 130.0, 30, 145.0))
        slope, x1, y1, offset = ch
        p = {"slope": slope, "offset": offset, "base": y1 + slope * (70 - x1),
             "k": 1.0, "k_stop": 0.5}
        for n in (0, 1, 7, 40):
            stop, target = legs_on(p, n)
            assert abs(stop - round(_level(ch, 0.5, 70 + n), 2)) < 1e-9, n
            assert abs(target - round(_level(ch, 1.5, 70 + n), 2)) < 1e-9, n
        assert legs_on(p, 1) != legs_on(p, 0), "기울어진 선인데 하루 지나도 그대로다"

        sel = [{"symbol": "AAPL", "px": 200.0, "shape": "breakout",
                "slope": 0.1, "offset": 1.0, "base": 100.0, "k": 1.0, "k_stop": 0.5,
                "entry_lvl": 199.0, "stop_lvl": 190.0, "target_lvl": 208.0}]
        globals()["plan"] = lambda now, holding: {
            "universe_n": 1, "eligible_n": 1, "triggers": sel,
            "selected": [s for s in sel if s["symbol"] not in holding]}
        globals()["_is_trading_day"] = lambda d: True
        globals()["_in_window"] = lambda now: True
        globals()["_bars_since"] = lambda a, b: 0
        at.get_account = lambda *a, **k: {"buying_power": 1e6}

        with tempfile.TemporaryDirectory() as td:
            # ③ 멱등 — 같은 날 submit 을 네 번 불러도 주문은 **한 번만** 나간다.
            #    크론 넷의 전제이고 ②「중복 0건」의 단위 테스트판이다.
            globals()["LEDGER"] = Path(td) / "idem.jsonl"
            live: list[str] = []
            globals()["_today_orders"] = lambda day: [{"client_order_id": c}
                                                      for c in live]
            for _ in range(4):
                cmd_submit(dry_run=False)
                live = [b.get("client_order_id", "") for b in fake.sent
                        if str(b.get("client_order_id", "")).startswith(
                            f"{PREFIX}{_et_now():%Y%m%d}-")]
            want = _coid("entry", f"{_et_now():%Y%m%d}", "AAPL")
            entries = [b for b in fake.sent if b.get("client_order_id") == want]
            assert len(entries) == 1, [b.get("client_order_id") for b in fake.sent]
            assert sum(r["kind"] == "submit" and r["idempotent_skip"]
                       for r in _rows()) == 3

            # ⑦ --dry-run 은 브로커를 한 번도 안 부른다 — 페이퍼로 주문이 새는 걸 막는다.
            globals()["LEDGER"] = Path(td) / "dry.jsonl"
            before = len(fake.sent)

            def _boom(*a, **k):
                raise AssertionError("dry-run 인데 브로커를 불렀다")

            at._submit, at.get_account = _boom, _boom
            cmd_submit(dry_run=True)
            at._submit, at.get_account = fake.submit, (lambda *a, **k: {"buying_power": 1e6})
            assert len(fake.sent) == before

            # ⑤ 다리 상호 취소 — 한쪽이 체결되면 반대쪽이 취소되고 초과 매도 0.
            globals()["LEDGER"] = Path(td) / "settle.jsonl"
            globals()["_et_now"] = lambda: datetime(2026, 9, 2, 16, 30, tzinfo=_ET)
            _append(_row("settle", _et_now(), fills=[], opened=[], closed=[],
                         amended=[], amend_failed=[], canceled=[], oversell=[],
                         leg_ok=1, leg_total=1, close_gap_bp=[],
                         positions=[_pos_row(stop_id="s1", limit_id="l1")]))
            fake.positions = []          # 스톱이 채워져 포지션이 사라졌다
            cmd_settle()
            last = _rows()[-1]
            assert sorted(fake.canceled) == ["l1", "s1"], fake.canceled
            assert last["closed"] == ["AAPL"] and last["positions"] == []
            assert last["oversell"] == []

            # 보유보다 많이 팔려는 자리는 주문 자체를 안 낸다.
            globals()["LEDGER"] = Path(td) / "over.jsonl"
            _append(_row("settle", _et_now(), fills=[], opened=[], closed=[],
                         amended=[], amend_failed=[], canceled=[], oversell=[],
                         leg_ok=1, leg_total=1, close_gap_bp=[],
                         positions=[_pos_row()]))
            fake.positions = [{"symbol": "AAPL", "qty": "2"}]
            n_before = len(fake.sent)
            cmd_settle()
            assert len(fake.sent) == n_before, "보유 2주인데 5주 매도가 나갔다"
            assert _rows()[-1]["oversell"] == [{"symbol": "AAPL", "want": 5, "have": 2}]

            # 다리가 붙는 정상 경로: 스톱·지정가 두 주문이 그날 선값으로 나간다.
            globals()["LEDGER"] = Path(td) / "legs.jsonl"
            _append(_row("settle", _et_now(), fills=[], opened=[], closed=[],
                         amended=[], amend_failed=[], canceled=[], oversell=[],
                         leg_ok=0, leg_total=1, close_gap_bp=[],
                         positions=[_pos_row()]))
            fake.positions = [{"symbol": "AAPL", "qty": "5"}]
            n_before = len(fake.sent)
            cmd_settle()
            legs = fake.sent[n_before:]
            assert [b["type"] for b in legs] == ["stop", "limit"], legs
            assert all(b["time_in_force"] == "gtc" for b in legs), legs
            want_stop, want_target = legs_on(_pos_row(), 0)
            assert float(legs[0]["stop_price"]) == want_stop
            assert float(legs[1]["limit_price"]) == want_target
            assert _rows()[-1]["leg_ok"] == 1

            # ⑧ 장부 한 줄의 칸 — 읽는 자리를 전부 세었는가.
            #    합성 장부로 report() 를 통째로 돌린다. 읽는 쪽이 스키마에 없는
            #    칸을 참조하면 여기서 KeyError 로 깨진다.
            globals()["LEDGER"] = Path(td) / "full.jsonl"
            now = datetime(2026, 9, 8, 15, 40, tzinfo=_ET)
            _append(_row("scan", now, universe_n=900, eligible_n=700,
                         triggers=["AAPL"], selected=["AAPL"], holding=[],
                         note="with-submit"))
            _append(_row("submit", now, dry_run=False, idempotent_skip=False,
                         submitted=[{"symbol": "AAPL", "leg": "entry", "qty": 5,
                                     "order_id": "o1", "status": "accepted",
                                     "px_at_submit": 200.0, "shape": "breakout",
                                     "channel": {}}],
                         blocked=[], rejected=[]))
            _append(_row("settle", now,
                         fills=[{"symbol": "AAPL", "leg": "entry", "status": "filled",
                                 "qty": 5, "fill_price": 200.0}],
                         opened=["AAPL"], closed=[], amended=[], amend_failed=[],
                         canceled=[], oversell=[], leg_ok=1, leg_total=1,
                         close_gap_bp=[{"symbol": "AAPL", "leg": "entry", "bp": 1.2}],
                         positions=[]))
            _append(_row("note", now, cause="broker", text="시세 지연"))
            _append(_row("clean", now, closed_positions=["OLD"], canceled_orders=True))
            rep = report(_rows(), probe={"legs": {"stop": {"status": "filled"},
                                                  "limit": {"status": "filled"}}},
                         gate={"ok": True, "max_abs_err": 1e-12, "n_mismatch": 0})
            assert rep["3_match"] == {"ghost": [], "missing": [],
                                      "blocked_buying_power": 0, "ok": True}, rep
            assert rep["1_orders"]["filled"] == {"entry": 1, "timeout": 0,
                                                 "stop": 1, "limit": 1}
            assert rep["1_orders"]["ok"] is False       # 타임아웃 MOC 이 아직 0건
            assert rep["verdict"] == "미측정"            # 표본 문턱 15건 미달
            assert rep["5_ops"]["ok"] is False           # 개입이 있는 날

            # 스키마 밖 칸은 쓰는 쪽에서 막힌다.
            try:
                _append({**_row("note", now, cause="code", text="x"), "extra": 1})
                raise AssertionError("스키마 밖 칸이 그냥 들어갔다")
            except ValueError:
                pass
    finally:
        for k, v in saved_at.items():
            setattr(at, k, v)
        globals().update(saved_g)

    print("selftest OK — 신규 8종 전부 통과")


# ── CLI ───────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="빗각 8단계 페이퍼 배관 번인")
    ap.add_argument("mode", choices=["clean", "selftest", "gate", "probe", "scan",
                                     "submit", "settle", "note", "report"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--kind", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--from", dest="since", default="")
    a = ap.parse_args(argv)

    if a.mode == "selftest":
        selftest()
        return 0
    if a.mode == "gate":
        return cmd_gate(a.days, a.workers)
    if a.mode == "report":
        return cmd_report(a.since)

    _require_paper()        # 여기부터는 브로커를 만진다
    return {"clean":  lambda: cmd_clean(a.yes),
            "probe":  lambda: cmd_probe(a.symbol, dry_run=a.dry_run),
            "scan":   lambda: cmd_scan(),
            "submit": lambda: cmd_submit(a.dry_run),
            "settle": lambda: cmd_settle(a.dry_run),
            "note":   lambda: cmd_note(a.kind, a.text)}[a.mode]()


if __name__ == "__main__":
    raise SystemExit(main())
