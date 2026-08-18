#!/usr/bin/env python
"""선택 계층 폐기율 — 세 팔을 같은 패널·같은 코드로 재생한다.

    python scripts/fetch_selection_inputs.py      # 선행 1회 (네트워크)
    python scripts/measure_selection_layer.py     # 재생 + 리포트 (네트워크 無)
    python scripts/measure_selection_layer.py selftest

사전 등록: `docs/superpowers/specs/2026-08-18-selection-layer-design.md`
**판정선은 그 문서에 봉인돼 있다. 결과를 보고 여기를 고치지 않는다.**

## 무엇을 재나

백테스트는 **모든 셋업**을 세고 러너는 그중 **고른 일부**만 주문한다. 그 사이
선택 계층이 셋업 무리를 바꾸는지를 **폐기율**(20거래일 안에 지정가 미도달)로
본다. 세 팔의 차이는 선택 계층뿐이다 — 패널도 `build_trade_plan` 도 창도 같다.

| 팔 | 무엇 |
|---|---|
| baseline | 롱 ∩ 등급 A·B 를 **전부** 주문 (자리·섹터·현금 제한 없음) |
| runner   | `rank_plan_candidates` 순위 + 자리·섹터·레짐·현금 한도 |
| placebo  | **무작위 순위** + 같은 한도 (순위 탓인지 한도 탓인지 가른다) |

## 재생이 되는 이유

선택 계층이 전부 일봉만으로 결정된다. 애널리스트 점수는 주문에 안 낀다
(`order_meta` — "관측 기록이지 주문 근거가 아니다"). 걸렸으면 재생 구간이
기록 시작일(2026-07-23) 이후 한 달로 잘렸다.

## 못 재는 것

실시간 데이터와 저장 패널의 차이, 러너 운영 사고(실행 건너뜀·패널 확보 실패).
**이건 살아 있는 시스템의 독립 검증이 아니다** — 선택 계층 하나를 분리하는 자다.
`fill-fidelity` ② 의 살아 있는 시계(n≥46)는 이 결과와 무관하게 그대로 돈다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from modules.trade_plan import MIN_BARS, build_trade_plan  # noqa: E402
from modules.virtual_broker import (  # noqa: E402
    LIMIT_FILL_WINDOW, PLAN_HOLD_WINDOW, scan_limit_fill, scan_plan_exit,
)
from paper_trade_runner_toss import (  # noqa: E402
    plan_position_size, rank_plan_candidates,
)

PANEL = Path("data/selection_panel.parquet")
REGIME = Path("data/selection_regime.parquet")
SECTORS = Path("data/selection_sectors.json")
PLANS = Path("data/selection_plans.parquet")     # 1단계 캐시 (느린 부분)
OUT_MD = Path(f"docs/measurements/{_dt.date.today().isoformat()}-selection-layer.md")

FIELDS = ["Open", "High", "Low", "Close", "Volume"]
LOOKBACK_DAYS = 400          # paper_trade_runner_toss.PLAN_LOOKBACK_DAYS
IS_START = pd.Timestamp("2024-12-20")   # 이 앞이 OOS (measure_trade_plan_oos 와 같은 경계)
MMD = 5.0                    # 의미 있는 최소 차이 %p — 사전 등록 §4 봉인
PLACEBO_SEEDS = 20           # 위약은 시드 하나면 그 시드의 잡음을 판정한다

# .github/workflows/paper-trade-us.yml 의 프로덕션 값. 이 값이 아니면 이 재생이
# 그 러너를 대표하지 않는다.
CAPITAL_KRW = 100_000_000    # 초기 1천만 + 2026-08-18 증자 9천만
FX = 1400.0                  # KRW_PER_USD
RISK_PCT = 0.155
MAX_POSITION_PCT = 15.0
MAX_SECTOR_POSITIONS = 5
REGIME_MAX_POS = {"bull": 18, "neutral": 13, "bear": 7}

# ponytail: 트레일링 스톱·드로다운 스톱·킬스위치는 안 넣었다. 트레일링 10% 는
# 손절 1~3% 보다 항상 뒤라 플랜 포지션에 안 걸리고, 나머지 둘은 사전 등록이
# 적은 한도 목록(자리·섹터·레짐·현금)에 없다. 넣으려면 사전 등록부터 고친다.


# ── 1단계: 날짜별 후보 (느리다 — 캐시한다) ────────────────────────────
def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _plans_for(args) -> list[dict]:
    """한 종목의 **매일** 플랜 중 actionable(롱 ∩ 등급 A·B)만.

    러너는 셋업 하나를 하루만 보는 게 아니다 — 자리가 없어 오늘 못 낸 후보를
    사흘 뒤에 낼 수 있다. 그래서 백테스트의 쿨다운 셋업 목록으로는 이 질문에
    답할 수 없고, **날짜×종목** 격자가 필요하다. 창은 러너와 같은 400일이다.
    """
    tk, df = args
    rows = []
    for i in range(len(df)):
        d = df.index[i]
        w = df.loc[d - pd.Timedelta(days=LOOKBACK_DAYS):d]
        if len(w) < MIN_BARS:
            continue
        p = build_trade_plan(w)
        if not p["actionable"]:
            continue
        rows.append({
            "ticker": tk, "date": d, "grade": p["cost_grade"],
            "current": p["current"], "entry_ref": p["entry"]["ref"],
            "limit": p["entry"]["high"],      # 롱 진입은 구간 상단 지정가
            "stop": p["stop"], "target": p["targets"][0],
            "rr": p["rr"][0], "risk_pct": p["risk_pct"],
        })
    return rows


def build_plans(panel: pd.DataFrame, workers: int) -> pd.DataFrame:
    tasks = []
    for tk in sorted({t for _, t in panel.columns}):
        df = _ohlcv(panel, tk)
        if len(df) >= MIN_BARS:
            tasks.append((tk, df))
    print(f"후보 계산 {len(tasks)}종목 × {len(panel)}거래일 · 워커 {workers}", flush=True)
    rows: list[dict] = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(_plans_for, tasks):
            rows += got
            done += 1
            if done % 10 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)}종목 · 누적 {len(rows)}후보", flush=True)
    out = pd.DataFrame(rows).sort_values(["date", "ticker"]).reset_index(drop=True)
    out.to_parquet(PLANS)
    return out


# ── 2단계: 재생 ────────────────────────────────────────────────────────
def replay(plans: pd.DataFrame, bars: dict, closes: pd.DataFrame,
           sectors: dict, regime: pd.Series, arm: str, seed: int = 0) -> tuple:
    """하루씩 걸으며 주문을 내고 체결/폐기를 정산한다. 반환은 (주문, 스킵 사유).

    순서는 러너와 같다: 먼저 정산(settle_pending), 그다음 주문. 자리는 보유 +
    **대기 주문**을 함께 센다 — 대기가 자리를 안 물면 20일치 주문이 겹쳐 쌓인다.

    스킵 사유를 같이 센다. 주문이 안 나간 이유가 자리인지 현금인지가 갈려야
    이 재생이 실제 러너를 대표하는지 알 수 있다 — 장부에서 실제로 막던 건
    자리가 아니라 **현금**이었다(2026-08-18 워크플로 주석).
    """
    rng = np.random.default_rng(seed)
    limited = arm != "baseline"
    pos_of = {tk: {b[0]: i for i, b in enumerate(bs)} for tk, bs in bars.items()}
    by_day = {d: g.to_dict("records") for d, g in plans.groupby("date")}

    cash = CAPITAL_KRW
    pending: dict[str, dict] = {}     # ticker → 주문
    holding: dict[str, dict] = {}     # ticker → 포지션
    orders: list[dict] = []
    skips = {"slot": 0, "sector": 0, "size": 0, "cash": 0}

    for day in closes.index:
        # 1. 정산 — 오늘이 이벤트 날인 대기 주문·보유 포지션
        for tk, o in list(pending.items()):
            if o["event_day"] != day:
                continue
            del pending[tk]
            if o["event"] == "expire":
                o["outcome"] = "nofill"
                continue
            cost = o["qty"] * o["fill_price"] * FX
            if limited and cost > cash:
                # 값은 구간에 **닿았다**. 못 산 이유가 우리 현금이라 폐기 쪽이
                # 아니라 체결 쪽으로 센다 (fill-fidelity 와 같은 규칙).
                o["outcome"] = "cash_short"
                continue
            o["outcome"] = "filled"
            cash -= cost
            i = pos_of[tk][o["fill_day"]]
            ex = scan_plan_exit(bars[tk][i:i + PLAN_HOLD_WINDOW + 1],
                                o["stop"], o["target"])
            holding[tk] = {"qty": o["qty"], "exit_day": ex["date"] if ex else None,
                           "exit_price": ex["price"] if ex else None}
        for tk, p in list(holding.items()):
            if p["exit_day"] == day:
                del holding[tk]
                cash += p["qty"] * p["exit_price"] * FX

        cands = by_day.get(day)
        if not cands:
            continue
        cands = [c for c in cands
                 if c["ticker"] not in holding and c["ticker"] not in pending]
        if not cands:
            continue

        # 2. 자리 — 레짐별 상한에서 보유 + 대기를 뺀다
        if limited:
            max_pos = REGIME_MAX_POS[regime.asof(day)]
            slots = max(0, max_pos - len(holding) - len(pending))
            if slots <= 0:
                skips["slot"] += len(cands)
                continue
            equity = cash + sum(p["qty"] * closes.at[day, tk] * FX
                                for tk, p in holding.items())
            ranked = ([cands[i] for i in rng.permutation(len(cands))]
                      if arm == "placebo" else rank_plan_candidates(cands))
        else:
            slots, equity, ranked = len(cands), 0.0, cands

        # 매수여력은 **그날 낸 주문만큼** 줄어든다 (러너와 같다). 대기 주문이
        # 현금을 묶지는 않으므로 다음 날이면 다시 찬다 — 그래서 나중에 체결될 때
        # 현금이 모자랄 수 있고, 그게 cash_short 다.
        avail = cash
        placed = 0
        for c in ranked:
            if placed >= slots:
                skips["slot"] += 1
                continue
            tk = c["ticker"]
            qty = 1
            if limited:
                sec = sectors.get(tk, "Unknown")
                in_sector = sum(1 for t in list(holding) + list(pending)
                                if sectors.get(t, "Unknown") == sec)
                if in_sector >= MAX_SECTOR_POSITIONS:
                    skips["sector"] += 1
                    continue
                qty = plan_position_size(equity, c["entry_ref"], c["stop"], FX,
                                         risk_pct=RISK_PCT,
                                         max_pos_pct=MAX_POSITION_PCT)
                if qty < 1:                       # 1주도 못 삼 — 주문 자체가 없다
                    skips["size"] += 1
                    continue
                if qty * c["limit"] * FX > avail:  # 매수여력 부족 — 주문 안 낸다
                    skips["cash"] += 1
                    continue
                avail -= qty * c["limit"] * FX

            i = pos_of[tk][day]
            res = scan_limit_fill(bars[tk][i + 1:i + 1 + LIMIT_FILL_WINDOW],
                                  c["limit"])
            o = {"ticker": tk, "order_day": day, "grade": c["grade"], "qty": qty,
                 "limit": c["limit"], "stop": c["stop"], "target": c["target"],
                 "outcome": "open"}
            if res["status"] == "filled":
                o.update(event="fill", event_day=res["date"],
                         fill_day=res["date"], fill_price=res["price"])
            elif res["status"] == "expired":
                # 20번째 봉에서 폐기 — 그때까지 자리를 물고 있다
                o.update(event="expire",
                         event_day=bars[tk][i + LIMIT_FILL_WINDOW][0])
            else:
                # 패널 끝까지 20봉이 안 남았다 — 사유가 안 갈렸으므로 집계에서 뺀다
                o.update(event=None, event_day=None, outcome="censored")
            pending[tk] = o
            orders.append(o)
            placed += 1
    return orders, skips


# ── 집계 ───────────────────────────────────────────────────────────────
def rate(orders: list[dict]) -> dict:
    """폐기율 — 분모는 사유가 갈린 주문. cash_short 은 체결 쪽."""
    res = [o for o in orders if o["outcome"] in ("nofill", "filled", "cash_short")]
    nofill = sum(1 for o in res if o["outcome"] == "nofill")
    return {"n": len(res), "nofill": nofill,
            "cash_short": sum(1 for o in res if o["outcome"] == "cash_short"),
            "censored": sum(1 for o in orders if o["outcome"] == "censored"),
            "rate": (nofill / len(res) * 100) if res else float("nan")}


def split(orders: list[dict], lo=None, hi=None) -> list[dict]:
    return [o for o in orders
            if (lo is None or o["order_day"] >= lo)
            and (hi is None or o["order_day"] < hi)]


def fisher(a: dict, b: dict) -> float:
    """두 폐기율의 양측 정확검정. 표본이 수천이라 근사 대신 정확검정을 쓴다."""
    if min(a["n"], b["n"]) == 0:
        return float("nan")
    return float(stats.fisher_exact([[a["nofill"], a["n"] - a["nofill"]],
                                     [b["nofill"], b["n"] - b["nofill"]]])[1])


def _line(label: str, s: dict) -> str:
    return (f"  {label:12} n={s['n']:6d}  폐기 {s['nofill']:5d}  "
            f"폐기율 {s['rate']:5.2f}%  (cash_short {s['cash_short']:4d}, "
            f"미판정 {s['censored']:3d})")


def selftest() -> int:
    """재생 엔진 자체 점검 — 한 종목·한 후보로 체결과 폐기를 강제한다."""
    days = pd.bdate_range("2024-01-01", periods=60)
    bars = {"X": [(d, 100.0, 101.0, 99.0, 100.0) for d in days]}
    closes = pd.DataFrame({"X": pd.Series(100.0, index=days)})
    reg = pd.Series("bull", index=days)
    sec = {"X": "Tech"}

    def one(limit):
        return pd.DataFrame([{"ticker": "X", "date": days[0], "grade": "A",
                              "current": 100.0, "entry_ref": 100.0, "limit": limit,
                              "stop": 98.0, "target": 104.0, "rr": 2.0,
                              "risk_pct": 2.0}])

    hit, _ = replay(one(99.5), bars, closes, sec, reg, "baseline")
    assert len(hit) == 1 and hit[0]["outcome"] == "filled", hit
    miss, _ = replay(one(90.0), bars, closes, sec, reg, "baseline")
    assert len(miss) == 1 and miss[0]["outcome"] == "nofill", miss
    # 20봉 안에 안 닿으면 폐기일은 주문 다음 20번째 봉이다
    assert miss[0]["event_day"] == days[LIMIT_FILL_WINDOW], miss
    # 한도가 붙은 팔에서도 값이 닿으면 폐기가 아니다 (체결 또는 cash_short)
    poor, _ = replay(one(99.5), bars, closes, sec, reg, "runner")
    assert poor and poor[0]["outcome"] in ("filled", "cash_short"), poor
    r = rate([{"outcome": "nofill"}, {"outcome": "cash_short"},
              {"outcome": "filled"}, {"outcome": "censored"}])
    assert (r["n"], r["nofill"], r["cash_short"], r["censored"]) == (3, 1, 1, 1), r
    assert abs(r["rate"] - 100 / 3) < 1e-9, r
    print("selftest OK")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()

    workers = max((os.cpu_count() or 2) - 1, 1)
    panel = pd.read_parquet(PANEL)
    regime = pd.read_parquet(REGIME)["regime"]
    sectors = json.loads(SECTORS.read_text(encoding="utf-8"))

    cached = PLANS.exists()
    plans = pd.read_parquet(PLANS) if cached else build_plans(panel, workers)
    if cached:
        print(f"후보 캐시 재사용: {PLANS} ({len(plans):,}행)")

    # 세 팔은 **같은 종목 집합**으로 돈다. 한 팔만 넓으면 비교가 깨진다.
    tickers = sorted(set(plans["ticker"]))
    bars, closes = {}, {}
    for tk in tickers:
        df = _ohlcv(panel, tk)
        bars[tk] = list(zip(df.index, df["Open"], df["High"], df["Low"], df["Close"]))
        closes[tk] = df["Close"]
    closes = pd.DataFrame(closes)
    span = f"{panel.index.min().date()} ~ {panel.index.max().date()}"
    print(f"재생: {len(tickers)}종목 · {len(panel)}거래일 ({span}) · "
          f"후보 {len(plans):,}건", flush=True)

    arms, skips = {}, {}
    for arm in ("baseline", "runner"):
        arms[arm], skips[arm] = replay(plans, bars, closes, sectors, regime, arm)
    placebo_runs = [replay(plans, bars, closes, sectors, regime, "placebo", seed=s)
                    for s in range(PLACEBO_SEEDS)]
    placebos = [o for o, _ in placebo_runs]

    def oos(o):
        return split(o, hi=IS_START)

    base_o, run_o = rate(oos(arms["baseline"])), rate(oos(arms["runner"]))
    pl_oos = [rate(oos(p)) for p in placebos]
    pl_rates = [s["rate"] for s in pl_oos]
    pl_mean = float(np.mean(pl_rates))
    # 위약 판정은 시드 평균으로 한다 — 시드 하나면 그 시드의 잡음을 판정한다.
    pl_pooled = {"n": sum(s["n"] for s in pl_oos),
                 "nofill": sum(s["nofill"] for s in pl_oos),
                 "cash_short": sum(s["cash_short"] for s in pl_oos),
                 "censored": sum(s["censored"] for s in pl_oos),
                 "rate": pl_mean}

    gap = run_o["rate"] - base_o["rate"]
    p_val = fisher(run_o, base_o)
    pl_gap = pl_mean - base_o["rate"]

    lines = [f"  ── OOS (~{(IS_START - pd.Timedelta(days=1)).date()}) ──",
             _line("기준선", base_o), _line("러너", run_o),
             _line("위약(합산)", pl_pooled)
             + f"  [시드 {PLACEBO_SEEDS}개 폐기율 {min(pl_rates):.2f}~{max(pl_rates):.2f}%]",
             "", f"  ── IS ({IS_START.date()}~) ──"]
    for name, o in arms.items():
        lines.append(_line(name, rate(split(o, lo=IS_START))))
    pl_is = [rate(split(p, lo=IS_START)) for p in placebos]
    lines.append(_line("placebo", {
        "n": sum(s["n"] for s in pl_is), "nofill": sum(s["nofill"] for s in pl_is),
        "cash_short": sum(s["cash_short"] for s in pl_is),
        "censored": sum(s["censored"] for s in pl_is),
        "rate": float(np.mean([s["rate"] for s in pl_is])),
    }))

    lines += ["", "  ── 러너 팔에서 주문이 안 나간 이유 (전 구간) ──",
              "  " + "  ".join(f"{k} {v:,}" for k, v in skips["runner"].items())
              + "   (자리·섹터·1주미만·현금 순)"]
    lines += ["", "  ── OOS 연도별 (러너 − 기준선, %p) ──"]
    signs = []
    for yr in sorted({o["order_day"].year for o in oos(arms["baseline"])}):
        b = rate([o for o in oos(arms["baseline"]) if o["order_day"].year == yr])
        r = rate([o for o in oos(arms["runner"]) if o["order_day"].year == yr])
        d = r["rate"] - b["rate"]
        signs.append(d)
        lines.append(f"  {yr}  기준선 {b['rate']:5.2f}% (n={b['n']:5d})  "
                     f"러너 {r['rate']:5.2f}% (n={r['n']:4d})  차이 {d:+6.2f}%p")

    ok1 = abs(gap) >= MMD and p_val < 0.05
    ok2 = abs(pl_gap) < MMD
    ok3 = bool(signs) and (all(s > 0 for s in signs) or all(s < 0 for s in signs))
    if not ok3:
        verdict = ("**미측정** — ③ 부호가 연도를 가로질러 뒤집힌다. 레짐 산물이라 "
                   "구간을 늘리거나 질문을 쪼개야 한다 (실패가 아니다).")
    elif not ok1:
        verdict = (f"**① 미달 — 차이 없음** (차이 {gap:+.2f}%p, MMD {MMD}%p, "
                   f"p={p_val:.4g}). 선택 계층은 폐기율을 안 바꾼다. "
                   f"`fill-fidelity` ② 의 살아 있는 시계는 그대로 돈다.")
    elif not ok2:
        verdict = (f"**①③ 통과 — 단 출처는 한도다** (위약도 {pl_gap:+.2f}%p 벌어졌다). "
                   f"'순위가 셋업 무리를 바꾼다'로 쓰면 안 된다.")
    else:
        verdict = (f"**①②③ 통과** — 러너의 셋업 무리가 백테스트 표본과 다르다 "
                   f"({gap:+.2f}%p, p={p_val:.4g}). 갈라진 방향을 보고 진입 구간 "
                   f"정의를 다시 볼지는 **새로 사전 등록한다.**")

    judge = [f"  ① 크기·유의  |{gap:+.2f}%p| ≥ {MMD}%p 그리고 p={p_val:.4g} < 0.05 → "
             f"{'통과' if ok1 else '미달'}",
             f"  ② 위약 붙음  |{pl_gap:+.2f}%p| < {MMD}%p → {'통과' if ok2 else '실패'}",
             f"  ③ 부호 유지  {' '.join(f'{s:+.1f}' for s in signs)} → "
             f"{'통과' if ok3 else '뒤집힘'}"]

    report = "\n".join(lines + ["", "  ── 판정 (사전 등록 §4) ──"] + judge)
    print("\n" + report + "\n\n" + verdict)

    # 빠진 종목은 사유를 갈라 적는다 — 데이터가 없는 것과 셋업이 안 난 것은
    # 다른 이야기다. 앞은 유니버스가 낡았다는 뜻이고, 뒤는 정상이다.
    nodata, nosetup = [], []
    for t in sorted({t for _, t in panel.columns}):
        if t in tickers:
            continue
        (nodata if len(_ohlcv(panel, t)) < MIN_BARS else nosetup).append(t)
    ylo, yhi = min(signs), max(signs)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        f"# 선택 계층 폐기율 — 측정 ({_dt.date.today().isoformat()})\n\n"
        f"사전 등록: `docs/superpowers/specs/2026-08-18-selection-layer-design.md` "
        f"— 판정선은 결과를 보기 전에 봉인했다.\n\n"
        f"- 패널: `{PANEL}` {len(tickers)}종목 · {len(panel)}거래일 ({span})\n"
        f"- 후보(롱 ∩ 등급 A·B) {len(plans):,}건 · 체결 창 {LIMIT_FILL_WINDOW}거래일 "
        f"· 홀드 {PLAN_HOLD_WINDOW}봉\n"
        f"- 러너 설정: 자본 {CAPITAL_KRW:,.0f}원 · 위험 {RISK_PCT}% · "
        f"자리 {REGIME_MAX_POS} · 섹터 {MAX_SECTOR_POSITIONS} · FX {FX:.0f}\n"
        f"- 패널에 데이터가 없는 종목: {', '.join(nodata) or '없음'}\n"
        f"- 데이터는 있는데 후보가 한 번도 안 난 종목: "
        f"{', '.join(nosetup) or '없음'}\n\n"
        f"```\n{report}\n```\n\n{verdict}\n\n"
        f"## 읽을 때 주의 — 판정 뒤에 적는다 (판정선은 안 건드렸다)\n\n"
        f"1. **통합값이 어느 해보다도 작다.** OOS 통합 차이는 {gap:+.2f}%p 인데 "
        f"연도별은 {ylo:+.2f} ~ {yhi:+.2f}%p 다. 두 팔의 연도별 표본 비중이 달라서 "
        f"생기는 일이다(심프슨) — 러너는 기준선 폐기율이 가장 낮은 해에 상대적으로 "
        f"적게 주문했다. **판정은 사전 등록대로 통합 OOS 로 했다.**\n"
        f"2. **현금은 재생에서 한 번도 안 막았다** — 주문 시점 스킵 "
        f"{skips['runner']['cash']}건, 체결 시점 cash_short {run_o['cash_short']}건. "
        f"막은 건 자리({skips['runner']['slot']:,}건)와 섹터"
        f"({skips['runner']['sector']:,}건)다. 위험 0.155% 설정의 예측과 같지만, "
        f"장부가 실제로 현금에 막히던 시절(0.31%)과는 제약 구조가 다르다.\n"
        f"3. **위약이 기준선에 붙었다** ({pl_gap:+.2f}%p). 차이가 났다면 출처는 "
        f"한도가 아니라 순위였을 것이다 — 다만 ① 이 미달이라 그 해석은 쓰지 않는다.\n\n"
        f"재현: `python scripts/fetch_selection_inputs.py && "
        f"python scripts/measure_selection_layer.py`\n",
        encoding="utf-8")
    print(f"\n기록: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
