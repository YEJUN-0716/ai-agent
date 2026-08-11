#!/usr/bin/env python
"""3a.5 — 페이퍼 계좌 슬리피지 실측.

    set -a && . ./.env && set +a
    python scripts/measure_paper_slippage.py [종목수] [손절대기분]

왜 재는가
---------
15분봉 백테스트는 **손절가에 정확히 체결된다**고 가정한다(3a, PR #84).
그 규칙의 손익분기가 왕복 5bp 라, 실제 체결이 몇 bp 밀리는지 모르는 채로
자동 주문을 켤 수 없다. 여기서 재는 건 딱 세 다리다:

  entry       — 진입 지정가(현재 호가 ask 에 건 marketable limit) vs 제출 시 mid
  stop_exit   — 손절 스톱 체결가 vs 손절가          ← 백테스트가 0 이라 가정한 것
  market_exit — 15:45 시장가 청산 체결가 vs 제출 시 mid

한 종목당 1주씩 사고, 현재가 바로 아래에 스톱을 걸어 **몇 분 안에 터지게**
한다. 손절폭을 실제 전략값(0.30%)으로 잡으면 하루에 몇 건 못 잰다. 트리거된
뒤에는 어차피 시장가라, 밀리는 폭은 스톱을 얼마나 멀리 뒀느냐와 무관하다.

읽을 때 감안할 것 (측정의 천장)
-------------------------------
- **페이퍼 체결은 시뮬레이션이다.** 호가에 맞춰 즉시 채워 주고 대기열도
  시장충격도 없다. 그러니 여기 나오는 숫자는 **하한**이다. 이걸로도 5bp 를
  넘으면 그 규칙은 실계좌에서 확실히 죽는다. 통과해도 "죽지는 않았다"까지다.
- **벤치마크 mid 는 IEX 호가**다. 체결은 통합호가(NBBO) 기준이라 IEX 스프레드가
  더 넓다 → 비용이 과대 계상되는 쪽으로 틀린다(안전한 방향).
- 개장 직후는 하루 중 스프레드가 가장 넓다. 실제 청산 시각(15:45)은 이보다
  좁으므로, 개장 무렵 측정치는 보수적이다.

결과는 `data/paper_slippage.jsonl` 에 **덧붙인다**. 장중 시세는 지나가면
다시 못 만든다 — 여러 날 돌려 표본을 쌓는 파일이다.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import alpaca_trading as at  # noqa: E402
from modules.alpaca_data import latest_quotes  # noqa: E402

OUT = Path(os.environ.get("OUT", "data/paper_slippage.jsonl"))

# 3a 유니버스와 같은 목록 (scripts/fetch_intraday_panel.UNIVERSE).
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JPM", "V",
    "JNJ", "UNH", "XOM", "PG", "HD", "MA", "ABBV", "MRK", "KO", "PEP",
    "COST", "AVGO", "LLY", "WMT", "MCD", "CRM", "ADBE", "CSCO", "ACN", "TMO",
]

# 스톱을 현재 호가 아래 이만큼에 건다. 실제 손절폭(0.30%)이 아니라 **빨리
# 터지라고** 좁게 잡은 값이다 — 위 독스트링 참조.
STOP_PCT = float(os.environ.get("STOP_PCT", "0.10")) / 100
# 3a 필터가 통과시킨 최소 손절폭. bp 를 R 로 옮길 때 쓴다.
STOP_R_PCT = float(os.environ.get("STOP_R_PCT", "0.30")) / 100

ENTRY_TIMEOUT_SEC = 90     # 지정가가 이 안에 안 채워지면 취소하고 '미체결'로 센다
POLL_SEC = 10


# ── 순수 계산 ──────────────────────────────────────────────────────────
def slip_bp(side: str, ref: float, fill: float) -> float:
    """기준가 대비 체결가가 **불리한 쪽으로** 밀린 정도(bp). 양수 = 비용.

    매수는 비싸게 사면 비용, 매도는 싸게 팔면 비용이다.
    """
    if ref <= 0 or fill <= 0:
        raise ValueError(f"가격이 0 이하입니다: ref={ref} fill={fill}")
    diff = (fill - ref) if side == "buy" else (ref - fill)
    return diff / ref * 1e4


def summarize(rows: list) -> dict:
    """다리별 중앙값·평균 슬리피지(bp) + 왕복 비용을 R 로 환산.

    중앙값을 앞세우는 이유: 체결 한 건이 크게 튀는 게 정상이라 평균만 보면
    표본 몇 개가 판정을 뒤집는다. 둘 다 낸다.
    """
    out = {"n": len(rows), "legs": {}}
    for leg in ("entry", "stop_exit", "market_exit"):
        vals = [r["slip_bp"] for r in rows if r.get("leg") == leg and r.get("slip_bp") is not None]
        if not vals:
            continue
        out["legs"][leg] = {
            "n":      len(vals),
            "median": statistics.median(vals),
            "mean":   statistics.fmean(vals),
            "p90":    sorted(vals)[max(0, int(len(vals) * 0.9) - 1)],
            "max":    max(vals),
        }
    entry = out["legs"].get("entry", {}).get("median")
    for exit_leg in ("stop_exit", "market_exit"):
        ex = out["legs"].get(exit_leg, {}).get("median")
        if entry is None or ex is None:
            continue
        rt_bp = entry + ex
        out[f"round_trip_{exit_leg}"] = {
            "bp": rt_bp,
            # 1R = 손절폭. 0.30% 손절이면 1bp = 0.0333R.
            "R":  rt_bp / 1e4 / STOP_R_PCT,
        }
    return out


# ── 실측 ───────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _market_open() -> bool:
    resp = at._request_with_retry("GET", f"{at.base_url()}/v2/clock",
                                  headers=at._headers(), timeout=10)
    resp.raise_for_status()
    return bool(resp.json().get("is_open"))


def _fill_price(res: dict):
    p = (res or {}).get("filled_avg_price")
    return float(p) if p not in (None, "") else None


def _record(rows: list, **kw) -> None:
    rows.append({"ts": _now(), **kw})


def run(tickers: list, stop_wait_min: float) -> list:
    rows: list = []

    # 1) 진입 — 현재 ask 에 지정가를 건다(즉시 체결되는 marketable limit).
    quotes = latest_quotes(tickers)
    pending = {}
    for sym in tickers:
        q = quotes.get(sym)
        if not q:
            print(f"  {sym}: 호가 없음 — 건너뜀")
            continue
        order = at.place_limit_buy(sym, 1, round(q["ask"], 2))
        pending[sym] = {"order_id": order["id"], "quote": q, "t0": time.time()}
        print(f"  {sym}: 지정가 매수 1주 @ ${q['ask']:.2f} "
              f"(스프레드 {q['spread_bp']:.1f}bp)")

    held = {}
    for sym, p in pending.items():
        res = at.wait_for_fill(p["order_id"], timeout=ENTRY_TIMEOUT_SEC)
        fill = _fill_price(res)
        if res.get("status") != "filled" or fill is None:
            at.cancel_order(p["order_id"])
            _record(rows, symbol=sym, leg="entry", status=res.get("status", "?"),
                    ref_price=p["quote"]["mid"], fill_price=None, slip_bp=None,
                    spread_bp=p["quote"]["spread_bp"], latency_s=None)
            print(f"  {sym}: 진입 미체결({res.get('status')}) — 취소")
            continue
        _record(rows, symbol=sym, leg="entry", status="filled",
                ref_price=p["quote"]["mid"], fill_price=fill,
                slip_bp=slip_bp("buy", p["quote"]["mid"], fill),
                spread_bp=p["quote"]["spread_bp"],
                latency_s=round(time.time() - p["t0"], 1))
        held[sym] = fill

    if not held:
        return rows

    # 2) 손절 스톱 — 현재 매수호가 바로 아래. 몇 분 안에 터지라고 좁게 건다.
    quotes = latest_quotes(list(held))
    stops = {}
    for sym in list(held):
        q = quotes.get(sym)
        if not q:
            continue
        stop_price = round(q["bid"] * (1 - STOP_PCT), 2)
        # 반올림이 스톱을 현재가 위로 올리면 브로커가 거절한다.
        stop_price = min(stop_price, round(q["bid"] - 0.01, 2))
        if stop_price <= 0:
            continue
        order = at.place_stop_sell(sym, 1, stop_price)
        stops[sym] = {"order_id": order["id"], "stop_price": stop_price,
                      "spread_bp": q["spread_bp"], "t0": time.time()}
        print(f"  {sym}: 스톱 매도 @ ${stop_price:.2f} (호가 {q['bid']:.2f})")

    # 3) 트리거 대기 — 남은 건 시장가로 청산한다(15:45 청산 다리).
    deadline = time.time() + stop_wait_min * 60
    while stops and time.time() < deadline:
        time.sleep(POLL_SEC)
        for sym in list(stops):
            s = stops[sym]
            res = at.wait_for_fill(s["order_id"], timeout=0)
            if res.get("status") != "filled":
                continue
            fill = _fill_price(res)
            _record(rows, symbol=sym, leg="stop_exit", status="filled",
                    ref_price=s["stop_price"], fill_price=fill,
                    slip_bp=slip_bp("sell", s["stop_price"], fill) if fill else None,
                    spread_bp=s["spread_bp"],
                    latency_s=round(time.time() - s["t0"], 1))
            print(f"  {sym}: 스톱 체결 ${fill} (손절가 ${s['stop_price']:.2f})")
            del stops[sym], held[sym]

    # 4) 남은 포지션 시장가 청산.
    quotes = latest_quotes(list(held)) if held else {}
    for sym in list(held):
        if sym in stops:
            at.cancel_order(stops[sym]["order_id"])
        q = quotes.get(sym)
        t0 = time.time()
        order = at.place_market_sell(sym, 1)
        res = at.wait_for_fill(order["id"], timeout=60)
        fill = _fill_price(res)
        _record(rows, symbol=sym, leg="market_exit", status=res.get("status", "?"),
                ref_price=(q or {}).get("mid"), fill_price=fill,
                slip_bp=(slip_bp("sell", q["mid"], fill) if q and fill else None),
                spread_bp=(q or {}).get("spread_bp"),
                latency_s=round(time.time() - t0, 1))
        print(f"  {sym}: 시장가 청산 ${fill}")

    return rows


def main() -> int:
    try:
        # 윈도우 기본 콘솔(cp949)이 em-dash 를 못 찍어 진단 메시지가 깨진다.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(UNIVERSE)
    stop_wait_min = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

    if not os.environ.get("ALPACA_API_KEY"):
        print("ALPACA_API_KEY 가 없습니다. `set -a && . ./.env && set +a` 먼저.",
              file=sys.stderr)
        return 1
    # 실계좌에서 돌면 진짜 돈으로 30번 사고 판다. 측정은 페이퍼에서만 한다.
    if not at.is_paper():
        print("실계좌(ALPACA_PAPER=false)에서는 실행하지 않습니다.", file=sys.stderr)
        return 1
    if not _market_open():
        print("정규장이 아닙니다 — 호가도 체결도 못 잽니다.", file=sys.stderr)
        return 1

    tickers = UNIVERSE[:n]
    print(f"{len(tickers)}종목 · 스톱 {STOP_PCT * 100:.2f}% 아래 · "
          f"대기 {stop_wait_min:.0f}분", flush=True)

    rows = run(tickers, stop_wait_min)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    s = summarize(rows)
    print(f"\n=== 이번 실행 {s['n']}건 → {OUT} ===")
    for leg, v in s["legs"].items():
        print(f"  {leg:12s} n={v['n']:3d}  중앙값 {v['median']:+.2f}bp  "
              f"평균 {v['mean']:+.2f}bp  p90 {v['p90']:+.2f}bp  최대 {v['max']:+.2f}bp")
    for key in ("round_trip_stop_exit", "round_trip_market_exit"):
        if key in s:
            print(f"  {key}: {s[key]['bp']:+.2f}bp = {s[key]['R']:+.3f}R "
                  f"(손절폭 {STOP_R_PCT * 100:.2f}% 기준)")
    print("  3a 손익분기: 왕복 5bp. 페이퍼 체결은 시뮬레이션이라 이건 하한이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
