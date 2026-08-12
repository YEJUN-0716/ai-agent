#!/usr/bin/env python
"""3b — 15분봉 단타 러너 (Alpaca 페이퍼) · **지금은 막혀 있다**

⛔ 2026-08-12 측정(`docs/measurements/2026-08-12-entry-rule.md`)으로 자동 주문을
막았다. 3a 의 +0.390R 은 **실행할 수 없는 진입** 위에 서 있었다 — 가격이 진입
구간 상단까지만 왔는데 백테스트가 구간 중간값에 사 준다. 시장에 없던 가격이고,
그 유령 체결이 총R 의 126% 다. 실제로 걸 수 있는 두 방식(구간 중간 지정가 /
구간 상단 지정가)은 OOS 에서 각각 −0.238R, −0.287R 이다.

역선택이 원인이다. 크게 이기는 셋업일수록 되돌림이 얕아 지정가가 안 채워지고,
깊이 되돌리는 셋업은 그대로 더 빠진다. 진입 구간 상단이 ref 에서 중앙값 0.77R
위라, 구간 안 어디를 사느냐가 트레이드마다 R 을 통째로 가른다.

**아래 코드는 그대로 쓸 수 있다** — 막힌 것은 진입 규칙이지 주문 처리가 아니다.
진입을 라인 하나로 좁혀 다시 재고, 양수가 나오면 `RUN_KNOWN_NEGATIVE` 게이트를
지우면 된다.

    set -a && . ./.env && set +a
    python intraday_runner.py            # 장중 상시 실행 (마감 15분 전 전량 청산)
    DRY_RUN=true python intraday_runner.py   # 주문 안 냄 (신호만 찍어 본다)

무엇을 하는가
------------
정규장 15분봉이 마감될 때마다 유니버스 30종목의 트레이드 플랜을 다시 그리고,
**3a 가 통과시킨 조합만** 진입 지정가로 건다. 체결되면 손절 스톱을 브로커에
걸고, 목표는 러너가 1분 폴링으로 본다. 마감 15분 전에 전량 시장가 청산한다.

3a 가 통과시킨 조합 = **롱 + 손절폭 0.30% 이상** (OOS 6bp 에서 +0.390R,
p<0.0001, n=4,539). 손절폭이 그보다 좁으면 왕복 6bp 가 0.4R 을 먹어 총기대값이
통째로 사라진다 — 이 필터는 수익률이 아니라 비용 산수에서 나왔다.

`trade_plan.actionable` 을 쓰면 **안 된다.** 그 플래그의 등급 경계(A 2.34% /
B 1.75%)는 일봉에서 잰 것이고, 15분봉 손절폭 중앙값은 0.16% 라 전부 D 로
떨어진다. 같은 질문("비용을 견디나")이지만 답이 봉 길이마다 다르다 — 그래서
예외를 뚫지 않고 분봉 전용 문턱(MIN_RISK_PCT)을 따로 둔다.

백테스트와 다른 점 (알고 하는 것)
--------------------------------
- **진입 지정가를 구간 상단이 아니라 entry_ref(구간 중간)에 건다.** 백테스트는
  저가가 구간 상단에 닿으면 체결로 치고 R 은 ref 기준으로 잰다. 상단에 걸면
  체결 수는 맞지만 ref 보다 비싸게 사서 R 이 조용히 깎인다. ref 에 걸면
  체결률은 낮아지되 **잰 것보다 나쁘게 사는 일이 없다.** 체결률 차이는
  로그에서 비교할 수 있다(entry_order vs entry_fill).
- **목표 청산은 1분 폴링이라 목표가를 스쳐 지나가면 그 뒤 값에 판다.**
  백테스트는 목표가 정확히 체결된다고 본다. 이 다리도 측정 대상이다.
- 손절은 브로커 스톱이다. 봉 마감까지 기다리면 15분치 초과 손실이 섞인다.

환경변수
  MIN_RISK_PCT        진입 최소 손절폭 %        (기본 0.30 — 3a 필터)
  RISK_PCT_PER_TRADE  1R = 자본의 몇 %          (기본 0.5)
  MAX_POSITION_PCT    한 종목 명목가 상한 %     (기본 10)
  MAX_POSITIONS       동시 보유 상한            (기본 8)
  MAX_DAILY_LOSS_R    누적 -R 이 이만큼이면 그날 종료 (기본 6)
  DRY_RUN             true 면 주문 전송 안 함
  RUNNER_LOG          로그 파일 (기본 data/intraday_runner.jsonl)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from modules import alpaca_trading as at
from modules.alpaca_data import latest_trades, load_intraday
from modules.intraday_session import regular_hours
from modules.trade_plan import MIN_BARS, build_trade_plan

LOG = Path(os.environ.get("RUNNER_LOG", "data/intraday_runner.jsonl"))

# 3a 유니버스와 같은 목록 (scripts/fetch_intraday_panel.UNIVERSE).
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JPM", "V",
    "JNJ", "UNH", "XOM", "PG", "HD", "MA", "ABBV", "MRK", "KO", "PEP",
    "COST", "AVGO", "LLY", "WMT", "MCD", "CRM", "ADBE", "CSCO", "ACN", "TMO",
]

MIN_RISK_PCT     = float(os.environ.get("MIN_RISK_PCT", "0.30"))
RISK_PCT         = float(os.environ.get("RISK_PCT_PER_TRADE", "0.5"))
MAX_POSITION_PCT = float(os.environ.get("MAX_POSITION_PCT", "10"))
MAX_POSITIONS    = int(os.environ.get("MAX_POSITIONS", "8"))
MAX_DAILY_LOSS_R = float(os.environ.get("MAX_DAILY_LOSS_R", "6"))
DRY_RUN          = os.environ.get("DRY_RUN", "").strip().lower() == "true"

BAR_MIN      = 15    # 봉 길이(분)
FILL_WINDOW  = 8     # 이 봉 수 안에 안 채워지면 지정가 취소 (백테스트와 같은 값)
COOLDOWN     = 3     # 트레이드 종료 후 같은 종목을 다시 잡기까지 (봉)
POLL_SEC     = 60    # REST 1분 폴링. 웹소켓은 안 쓴다 (계정당 연결 1개 제약)
EOD_FLAT_MIN = 15    # 마감 이 분 전 전량 청산 = 백테스트의 15:45 청산
HISTORY_DAYS = 14    # 플랜 창(80봉)을 채우고도 남는 최소 길이

_TERMINAL = {"canceled", "expired", "rejected", "replaced", "done_for_day"}


# ── 순수 계산 (테스트가 잡는 부분) ────────────────────────────────────
def position_size(equity: float, entry_ref: float, stop: float) -> int:
    """주문 수량. 위험 기준과 명목가 상한 중 **작은 쪽**.

    15분봉 손절은 진입가에서 0.3% 밖에 안 떨어져 있어서 위험 기준만 쓰면
    자본의 0.5% 를 걸겠다는 주문이 명목가 167% 짜리가 된다. 실제로는 명목가
    상한이 수량을 정하고, 그래서 트레이드당 실질 위험은 0.5% 보다 훨씬 작다 —
    이 단계의 목적이 달러 수익이 아니라 R 통계와 슬리피지를 쌓는 것이라 그래도 된다.
    """
    risk_per_share = entry_ref - stop
    if risk_per_share <= 0 or entry_ref <= 0 or equity <= 0:
        return 0
    by_risk     = (equity * RISK_PCT / 100.0) / risk_per_share
    by_notional = (equity * MAX_POSITION_PCT / 100.0) / entry_ref
    return int(min(by_risk, by_notional))


def entry_reason(plan: dict) -> str:
    """진입 대상이 아니면 사유, 대상이면 빈 문자열.

    `plan["actionable"]` 을 쓰지 않는 이유는 모듈 독스트링 참조.
    """
    if not plan.get("valid"):
        return plan.get("reason_invalid") or "유효 셋업 아님"
    if plan.get("direction") != "long":
        return "숏은 비용 후 기대값이 0 근처 — 관찰만"
    if plan.get("risk_pct", 0.0) < MIN_RISK_PCT:
        return f"손절폭 {plan['risk_pct']:.2f}% < {MIN_RISK_PCT:.2f}% — 비용에 먹힌다"
    return ""


def scan_due(now, last_bar) -> bool:
    """이번 분에 신호를 다시 그릴 차례인가.

    15분 경계마다 한 번, 경계에서 1분 이상 지난 뒤에. 마감 **직후**의 봉은
    데이터가 아직 안 실려 있는 일이 있어 한 박자 기다린다.
    """
    return now.floor(f"{BAR_MIN}min") != last_bar and now.minute % BAR_MIN >= 1


def bar_cutoff(now):
    """마지막으로 **마감된** 봉의 시작 시각 (UTC naive).

    Alpaca 봉은 **시작 시각**으로 라벨된다. 13:46 에는 13:45 봉이 진행 중이고
    마감된 마지막 봉은 13:30 이다. 여기서 한 칸 틀리면 미완성 봉의 고/저로
    구조를 잡아, 백테스트가 본 적 없는 신호가 조용히 생긴다.
    """
    return now.floor(f"{BAR_MIN}min").tz_localize(None) - timedelta(minutes=BAR_MIN)


def trade_r(entry_ref: float, stop: float, fill: float, exit_px: float) -> float:
    """실현 R. 위험 1단위는 **플랜의** entry_ref-stop 이다.

    실제 체결가로 위험을 재면 잘 산 트레이드일수록 R 이 커져, 백테스트가 낸
    숫자와 나란히 못 놓는다. 잘 산 만큼은 분자에서 이미 이득으로 잡힌다.
    """
    risk = entry_ref - stop
    return (exit_px - fill) / risk if risk > 0 else 0.0


# ── 로그 ───────────────────────────────────────────────────────────────
def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _log(event: str, **kw) -> None:
    """한 건 생길 때마다 **즉시** 붙인다. 장중 기록은 지나가면 못 만든다."""
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "event": event, **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    detail = " ".join(f"{k}={v}" for k, v in kw.items())
    print(f"[{row['ts'][11:19]}] {event:14s} {detail}", flush=True)


# ── 브로커 ─────────────────────────────────────────────────────────────
def _clock() -> dict:
    r = at._request_with_retry("GET", f"{at.base_url()}/v2/clock",
                               headers=at._headers(), timeout=10)
    r.raise_for_status()
    d = r.json()
    return {"is_open": bool(d.get("is_open")),
            "now": pd.Timestamp(d["timestamp"]).tz_convert("UTC"),
            "next_open": pd.Timestamp(d["next_open"]).tz_convert("UTC"),
            "next_close": pd.Timestamp(d["next_close"]).tz_convert("UTC")}


def _order(order_id: str) -> dict:
    r = at._request_with_retry("GET", f"{at.base_url()}/v2/orders/{order_id}",
                               headers=at._headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _sweep() -> None:
    """남은 주문·포지션을 브로커 쪽에서 통째로 지운다 — 마지막 안전망.

    러너가 세는 것과 계좌가 어긋난 경우(부분체결 조각, 놓친 주문)에도 밤을
    넘기지 않게. 단타 러너가 포지션을 들고 자면 그건 다른 전략이 된다.
    """
    if DRY_RUN:
        return
    r = at._request_with_retry("DELETE", f"{at.base_url()}/v2/positions",
                               headers=at._headers(),
                               params={"cancel_orders": "true"}, timeout=30)
    if r.status_code not in (200, 207):
        _log("sweep_error", status=r.status_code, body=r.text[:200])


# ── 상태 전이 ──────────────────────────────────────────────────────────
def _adopt(st: dict, tk: str, p: dict, o: dict, now) -> bool:
    """체결된(또는 부분체결된) 진입을 포지션으로 받고 손절 스톱을 건다.

    부분체결을 흘리면 **스톱 없는 포지션**이 남는다. 취소 경로에서도 반드시
    여기를 지나가야 하는 이유다.
    """
    qty = int(float(o.get("filled_qty") or 0))
    if qty < 1:
        return False
    fill = float(o.get("filled_avg_price") or 0) or p["entry_ref"]
    stop_order = at.place_stop_sell(tk, qty, p["stop"], dry_run=DRY_RUN)
    st["held"][tk] = {**p, "qty": qty, "fill": fill, "filled_at": now,
                      "stop_order_id": stop_order["id"]}
    st["pending"].pop(tk, None)
    _log("entry_fill", symbol=tk, qty=qty, fill=fill, ref=p["entry_ref"],
         stop=p["stop"], target=p["target"], risk_pct=p["risk_pct"])
    return True


def _close(st: dict, tk: str, h: dict, exit_px: float, reason: str, now) -> None:
    r = trade_r(h["entry_ref"], h["stop"], h["fill"], exit_px)
    st["day_r"] += r
    st["cooldown"][tk] = now + timedelta(minutes=COOLDOWN * BAR_MIN)
    st["held"].pop(tk, None)
    # 손절 다리는 손절가 대비 몇 bp 밀렸는지가 그대로 남는다 — 3a.5 가 잰 그 값이고,
    # 변동성 큰 날의 표본이 여기서 쌓인다.
    slip_bp = ((h["stop"] - exit_px) / h["stop"] * 1e4) if reason == "stop" else None
    _log("exit", symbol=tk, reason=reason, qty=h["qty"], fill=h["fill"],
         exit_price=exit_px, stop=h["stop"], target=h["target"],
         r=round(r, 3), slip_bp=(round(slip_bp, 2) if slip_bp is not None else None),
         day_r=round(st["day_r"], 2))


def _market_exit(st: dict, tk: str, h: dict, reason: str, now,
                 fallback_px: float | None = None) -> None:
    """스톱을 걷고 시장가로 턴다. 걷는 사이 스톱이 터졌으면 그쪽으로 센다."""
    if not DRY_RUN and not at.cancel_order(h["stop_order_id"]):
        o = _order(h["stop_order_id"])
        if o.get("status") == "filled":
            _close(st, tk, h, float(o["filled_avg_price"]), "stop", now)
            return
    if DRY_RUN:
        _close(st, tk, h, fallback_px or h["fill"], reason, now)
        return
    res = at.wait_for_fill(at.place_market_sell(tk, h["qty"])["id"], timeout=60)
    px = res.get("filled_avg_price")
    _close(st, tk, h, float(px) if px else (fallback_px or h["fill"]), reason, now)


def _reconcile(st: dict, now) -> None:
    """진입 지정가 → 포지션 → 청산. 매 분 돈다."""
    for tk, p in list(st["pending"].items()):
        if DRY_RUN:
            continue
        o = _order(p["order_id"])
        status = str(o.get("status", "")).lower()
        if status == "filled":
            _adopt(st, tk, p, o, now)
        elif status in _TERMINAL:
            if not _adopt(st, tk, p, o, now):     # 부분체결분 구제
                st["pending"].pop(tk, None)
                _log("entry_dead", symbol=tk, status=status)
        elif now - p["submitted"] > timedelta(minutes=FILL_WINDOW * BAR_MIN):
            at.cancel_order(p["order_id"])
            o = _order(p["order_id"])
            if not _adopt(st, tk, p, o, now):
                st["pending"].pop(tk, None)
                _log("entry_expired", symbol=tk, ref=p["entry_ref"])

    if not st["held"] or DRY_RUN:
        return
    prices = latest_trades(list(st["held"]))
    for tk, h in list(st["held"].items()):
        o = _order(h["stop_order_id"])
        if str(o.get("status", "")).lower() == "filled":
            _close(st, tk, h, float(o["filled_avg_price"]), "stop", now)
            continue
        px = prices.get(tk)
        if px is not None and px >= h["target"]:
            _market_exit(st, tk, h, "target", now, fallback_px=px)


def _scan(st: dict, equity: float, bar_close, now) -> None:
    """마감된 마지막 봉으로 플랜을 다시 그리고 진입 지정가를 건다."""
    _, ohlcv = load_intraday(UNIVERSE, "15Min", days=HISTORY_DAYS, min_bars=MIN_BARS)
    seen = skipped = 0
    for tk in UNIVERSE:
        if len(st["held"]) + len(st["pending"]) >= MAX_POSITIONS:
            break
        if tk in st["held"] or tk in st["pending"]:
            continue
        if st["cooldown"].get(tk, now) > now:
            continue
        df = ohlcv.get(tk)
        if df is None:
            continue
        # 진행 중인 봉은 버린다. 미완성 봉의 고/저로 구조를 잡으면 백테스트가
        # 본 적 없는 신호가 생긴다.
        df = regular_hours(df)
        df = df[df.index <= bar_close]
        if len(df) < MIN_BARS:
            continue
        seen += 1
        plan = build_trade_plan(df, scale=1)
        if entry_reason(plan):
            skipped += 1
            continue
        ref, stop = plan["entry"]["ref"], plan["stop"]
        qty = position_size(equity, ref, stop)
        if qty < 1:
            skipped += 1
            continue
        # day 로 건다 — 러너가 하드킬당해도 주문이 밤을 넘기지 않게.
        order = at.place_limit_buy(tk, qty, ref, dry_run=DRY_RUN, tif="day")
        st["pending"][tk] = {"order_id": order["id"], "entry_ref": ref,
                             "stop": stop, "target": plan["targets"][0],
                             "risk_pct": plan["risk_pct"], "qty": qty,
                             "submitted": now}
        _log("entry_order", symbol=tk, qty=qty, ref=ref, stop=stop,
             target=plan["targets"][0], risk_pct=plan["risk_pct"],
             rr=plan["rr"][0], conf=plan["confidence"])
    _log("scan", bar=str(bar_close), checked=seen, skipped=skipped,
         held=len(st["held"]), pending=len(st["pending"]))


def _shutdown(st: dict, halt: str, now) -> None:
    """보유 전량 청산 → 대기 주문 취소 → 브로커 쪽 안전망.

    하루의 끝은 **반드시** 여기를 지나간다. 청산이 한 종목 실패해도 나머지를
    계속 털어야 하므로 종목마다 따로 감싼다 — 한 건 때문에 나머지가 밤을
    넘기는 것이 최악이다.
    """
    for tk, h in list(st["held"].items()):
        try:
            _market_exit(st, tk, h, "eod" if halt == "eod" else "halt", now)
        except Exception as e:
            _log("error", detail=f"청산 실패 {tk}: {e}")
    for tk, p in list(st["pending"].items()):
        try:
            if not DRY_RUN:
                at.cancel_order(p["order_id"])
        except Exception as e:
            _log("error", detail=f"취소 실패 {tk}: {e}")
        st["pending"].pop(tk, None)
    _sweep()
    _log("stop", reason=halt, day_r=round(st["day_r"], 2))


# ── 진입점 ─────────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not os.environ.get("ALPACA_API_KEY"):
        print("ALPACA_API_KEY 가 없습니다. `set -a && . ./.env && set +a` 먼저.",
              file=sys.stderr)
        return 1
    if not at.is_paper() and os.environ.get("ALLOW_LIVE") != "true":
        print("실계좌에서는 안 돕니다 — 3b 는 페이퍼 검증 단계입니다 "
              "(정말이면 ALLOW_LIVE=true).", file=sys.stderr)
        return 1
    # 2026-08-12 측정으로 막아 둔다. 자세한 건 모듈 독스트링 맨 위.
    if os.environ.get("RUN_KNOWN_NEGATIVE") != "true":
        print("정지 — 이 규칙은 실행 가능한 형태로는 기대값이 음수입니다.\n"
              "  docs/measurements/2026-08-12-entry-rule.md\n"
              "  3a 의 +0.390R 은 시장에 없던 가격에 사는 진입 위에 있었습니다."
              " 그 유령 체결이 수익의 126% 입니다.\n"
              "  진입 규칙을 고쳐 다시 재기 전에는 켜지 않습니다 "
              "(그래도 돌리려면 RUN_KNOWN_NEGATIVE=true).", file=sys.stderr)
        return 1

    clk = _clock()
    if not clk["is_open"]:
        wait = (clk["next_open"] - clk["now"]).total_seconds()
        if wait > 2 * 3600:
            print(f"정규장이 아닙니다. 다음 개장 {clk['next_open']} "
                  f"({wait / 3600:.1f}시간 뒤)", file=sys.stderr)
            return 1
        print(f"개장 대기 {wait / 60:.0f}분 → {clk['next_open']}", flush=True)
        time.sleep(wait + 5)
        clk = _clock()

    flatten_at = clk["next_close"] - timedelta(minutes=EOD_FLAT_MIN)
    equity = at.get_account()["equity"]
    st = {"pending": {}, "held": {}, "cooldown": {}, "day_r": 0.0}
    _log("start", equity=equity, flatten_at=str(flatten_at), dry_run=DRY_RUN,
         min_risk_pct=MIN_RISK_PCT, max_positions=MAX_POSITIONS)

    last_bar = None
    halt = ""
    while True:
        now = _now()
        if now >= flatten_at:
            halt = "eod"
            break
        try:
            _reconcile(st, now)
            if st["day_r"] <= -MAX_DAILY_LOSS_R:
                halt = "daily_loss"
                break
            if scan_due(now, last_bar):
                _scan(st, equity, bar_cutoff(now), now)
                last_bar = now.floor(f"{BAR_MIN}min")
        except Exception as e:                     # 6시간 반짜리 프로세스다.
            _log("error", detail=f"{type(e).__name__}: {e}")
        time.sleep(POLL_SEC)

    _shutdown(st, halt, _now())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
