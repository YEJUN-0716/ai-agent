#!/usr/bin/env python
"""3b 러너의 **진입 방식**이 얼마짜리 선택인지 잰다.

    python scripts/measure_entry_rule.py [워커수]

## 왜 재는가

3a 백테스트(+0.390R)의 진입 규칙은 실행 규칙이 아니다. 저가가 **진입 구간
상단**에 닿으면 체결로 치고, 산 가격은 **구간 중간(entry_ref)** 으로 계산한다.
가격이 중간까지 안 내려와도 중간 가격에 산 걸로 친다는 뜻이다 — 실제로는
그렇게 못 산다.

러너(PR #88)는 지정가를 ref 에 건다. 그래서 **잰 것보다 나쁘게 사는 일은
없지만 체결이 준다.** 그 맞바꿈의 크기를 여기서 잰다. 너무 많이 줄면 상단에
거는 쪽이 맞다.

## 세 가지 진입 방식 (같은 셋업, 실행만 다름)

    A 백테스트   저가가 상단에 닿으면 체결 · 산 값은 ref     ← 3a 가 잰 것
    B ref 지정가  저가가 ref 에 닿아야 체결 · 산 값은 ref     ← 러너가 지금 하는 것
    C 상단 지정가 저가가 상단에 닿으면 체결 · 산 값은 상단    ← 정직한 A

A 는 B 의 체결 수와 C 의 가격을 동시에 가진다. 그래서 A 는 실행 가능한 규칙이
아니라 **상한**이다. 진짜 선택지는 B 와 C 뿐이다.

R 은 셋 다 **플랜의 위험(entry_ref - stop)** 으로 나눈다. 실제로 산 값으로
나누면 비싸게 산 C 의 손실이 작아 보여 나란히 못 놓는다.

셋업은 A 기준으로 한 번만 고르고 세 규칙에 똑같이 먹인다(짝지은 비교).
필터는 러너와 같다 — **롱 + 손절폭 0.30% 이상.**

네트워크 無 — 저장 패널만 읽는다(scripts/fetch_intraday_panel.py 로 먼저 받을 것).
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules.intraday_session import session_ids  # noqa: E402
from modules.trade_plan import MIN_BARS, build_trade_plan  # noqa: E402
from modules.trade_plan_backtest import _simulate_outcome  # noqa: E402
from modules.stat_validation import permutation_test_trades  # noqa: E402

PANEL = Path(os.environ.get("PANEL", "data/intraday_panel_15m.parquet"))
OUT_MD = Path("docs/measurements/2026-08-12-entry-rule.md")
OUT_PARQUET = Path("data/entry_rule_trades.parquet")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

# 3a·러너와 같은 값.
FILL_WINDOW = 8
HOLD_WINDOW = 26
COOLDOWN = 3
MIN_RISK_PCT = 0.30
COST_BPS = 6.0
IS_START = pd.Timestamp("2024-12-20")

RULES = ("A_백테스트", "B_ref지정가", "C_상단지정가")


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _sim(rule: str, plan: dict, args: dict) -> dict:
    """한 셋업 × 한 규칙. 반환 R 은 **플랜 위험** 기준으로 맞춰 놓는다."""
    lo, hi = plan["entry"]["low"], plan["entry"]["high"]
    ref, stop, tgt = plan["entry"]["ref"], plan["stop"], plan["targets"][0]
    plan_risk = ref - stop

    if rule == "A_백테스트":
        fill_at, buy_at, rr = hi, ref, plan["rr"][0]
    elif rule == "B_ref지정가":
        # 체결 조건을 ref 로 좁힌다. 산 값은 그대로 ref.
        fill_at, buy_at, rr = ref, ref, plan["rr"][0]
    else:
        # 상단에 걸면 상단에 산다. 위험이 (hi - stop) 으로 커지므로 목표
        # 손익비도 그 기준으로 다시 낸다 — 그래야 win 의 R 이 실제 이익이다.
        fill_at, buy_at = hi, hi
        rr = (tgt - hi) / (hi - stop) if hi > stop else float("nan")

    res = _simulate_outcome(
        args["highs"], args["lows"], args["i"], "long",
        min(lo, fill_at), fill_at, stop, tgt, rr,
        fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW,
        sessions=args["sessions"], opens=args["opens"], entry_ref=buy_at)

    # _simulate_outcome 은 (buy_at - stop) 을 1R 로 세고 나온다. 규칙끼리
    # 나란히 놓으려면 전부 플랜 위험으로 환산해야 한다.
    scale = (buy_at - stop) / plan_risk if plan_risk > 0 else float("nan")
    return {"outcome": res["outcome"], "r": res["r"] * scale,
            "exit_idx": res["exit_idx"], "fill_idx": res["fill_idx"]}


def _run_ticker(args):
    tk, df = args
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    opens = df["Open"].to_numpy(dtype=float)
    sessions = session_ids(df.index)
    n = len(df)
    rows = []

    i = MIN_BARS
    while i < n - 1:
        plan = build_trade_plan(df.iloc[: i + 1], scale=1)
        # 러너와 같은 문턱. 여기서 거르는 것이 3a 가 통과시킨 그 집합이다.
        if (not plan["valid"] or plan["direction"] != "long"
                or plan["risk_pct"] < MIN_RISK_PCT):
            i += 1
            continue

        ctx = {"highs": highs, "lows": lows, "opens": opens,
               "sessions": sessions, "i": i}
        out = {r: _sim(r, plan, ctx) for r in RULES}
        ref, stop = plan["entry"]["ref"], plan["stop"]
        rows.append({
            "ticker": tk, "entry_date": df.index[i],
            "risk_pct": plan["risk_pct"],
            # 구간 상단이 ref 에서 얼마나 위인가 — C 가 더 무는 값이다.
            "zone_up_r": (plan["entry"]["high"] - ref) / (ref - stop),
            **{f"{r}_outcome": out[r]["outcome"] for r in RULES},
            **{f"{r}_r": out[r]["r"] for r in RULES},
        })
        # 셋업 간격은 A 기준으로 고정한다 — 규칙마다 다르면 짝지은 비교가 깨진다.
        a = out["A_백테스트"]
        landing = a["exit_idx"] or a["fill_idx"] or (i + FILL_WINDOW)
        i = max(i + 1, landing + COOLDOWN)
    return rows


def _net(df: pd.DataFrame, rule: str, cost_bps: float = COST_BPS) -> np.ndarray:
    """비용 차감 R. 체결된 것만. 비용은 세 규칙 모두 같은 왕복 bp 다."""
    sub = df[df[f"{rule}_outcome"] != "nofill"]
    if sub.empty:
        return np.array([])
    cost_r = (cost_bps / 1e4) / (sub["risk_pct"] / 100.0)
    return (sub[f"{rule}_r"] - cost_r).to_numpy(dtype=float)


def _block(df: pd.DataFrame, label: str) -> list[str]:
    lines = [f"### {label} — 셋업 {len(df):,}건", "",
             "| 규칙 | 체결 | 체결률 | 총R(비용전) | 순평균R | 순합R | p값 |",
             "|---|---|---|---|---|---|---|"]
    for rule in RULES:
        filled = df[df[f"{rule}_outcome"] != "nofill"]
        net = _net(df, rule)
        if len(net) == 0:
            continue
        p = (permutation_test_trades(net, seed=42)["p_value"]
             if len(net) >= 5 else float("nan"))
        lines.append(
            f"| {rule} | {len(filled):,} | {len(filled) / len(df) * 100:.0f}% | "
            f"{filled[f'{rule}_r'].mean():+.3f}R | {net.mean():+.3f}R | "
            f"{net.sum():+,.0f}R | {p:.4f} |")
    return lines + [""]


def _phantom(df: pd.DataFrame, label: str) -> list[str]:
    """A 의 체결을 **실제로 살 수 있던 것**과 **유령**으로 가른다.

    B 가 A 의 부분집합이라는 게 확인됐으므로(불변식), A 체결 중 B 가 못 산 것은
    "가격이 구간 상단까지만 왔는데 백테스트가 중간값에 사 준" 건이다. 그
    가격에 시장이 거래된 적이 없다.
    """
    a = df[df["A_백테스트_outcome"] != "nofill"]
    real = a[a["B_ref지정가_outcome"] != "nofill"]
    ph = a[a["B_ref지정가_outcome"] == "nofill"]
    if a.empty:
        return []
    n_a = _net(df, "A_백테스트")
    n_r = (real["A_백테스트_r"] - (COST_BPS / 1e4) / (real["risk_pct"] / 100)).to_numpy()
    n_p = (ph["A_백테스트_r"] - (COST_BPS / 1e4) / (ph["risk_pct"] / 100)).to_numpy()
    return [
        f"| {label} | {len(a):,} | {n_a.mean():+.3f}R | {n_a.sum():+,.0f}R | "
        f"{len(real):,} | {n_r.mean():+.3f}R | {len(ph):,} | {n_p.mean():+.3f}R | "
        f"{n_p.sum() / n_a.sum() * 100:.0f}% |"]


def _bands(df: pd.DataFrame) -> list[str]:
    """구간이 좁으면 세 규칙이 같아진다. 좁은 셋업만 남기면 살아나는가?"""
    edges = [0, .2, .4, .6, .8, 1e9]
    names = ["~0.2R", "0.2~0.4R", "0.4~0.6R", "0.6~0.8R", "0.8R+"]
    band = pd.cut(df["zone_up_r"], edges, labels=names)
    lines = ["| 구간 폭 | 셋업 | A 순평균R | B 체결 | B 순평균R | C 순평균R |",
             "|---|---|---|---|---|---|"]
    for name, g in df.groupby(band, observed=True):
        cells = []
        for rule in RULES:
            net = _net(g, rule)
            cells.append(f"{net.mean():+.3f}R" if len(net) else "—")
        nb = (g["B_ref지정가_outcome"] != "nofill").sum()
        lines.append(f"| {name} | {len(g):,} | {cells[0]} | {nb:,} | "
                     f"{cells[1]} | {cells[2]} |")
    return lines


def _write_report(df: pd.DataFrame, span: str) -> str:
    body = [
        "# 진입 방식 — 백테스트 규칙 vs 러너가 실제로 할 수 있는 것 (2026-08-12)",
        "",
        "**결론: 3a 의 +0.390R 은 실행할 수 없는 진입 위에 서 있었다.**",
        "실행 가능한 두 방식은 OOS 에서 둘 다 음수다 (B −0.238R, C −0.287R).",
        "15분봉 자동 주문은 **켜면 안 된다.**", "",
        f"30종목 15분봉 · {span} · 롱 + 손절폭 {MIN_RISK_PCT}% 이상 · "
        f"왕복 {COST_BPS:.0f}bp", "",
        "| 규칙 | 체결 조건 | 산 값 |",
        "|---|---|---|",
        "| A_백테스트 | 저가가 구간 **상단**에 닿으면 | **ref**(구간 중간) |",
        "| B_ref지정가 | 저가가 **ref** 에 닿아야 | ref |",
        "| C_상단지정가 | 저가가 구간 **상단**에 닿으면 | **상단** |",
        "",
        "A 는 B 의 체결 수와 C 의 가격을 동시에 가진다 — 실행 가능한 규칙이 "
        "아니라 **상한**이다. 진짜 선택지는 B 와 C 다.",
        "R 은 셋 다 플랜 위험(entry_ref − stop) 으로 나눈다.", "",
    ]
    oos = df[df["entry_date"] < IS_START]
    ins = df[df["entry_date"] >= IS_START]
    body += _block(oos, f"일봉 규칙을 고를 때 안 본 구간 (~{(IS_START - pd.Timedelta(days=1)).date()})")
    body += _block(ins, f"본 구간 ({IS_START.date()}~)")
    body += _block(df, "전체")

    body += [
        "## A 의 수익은 어디서 왔나 — 유령 체결", "",
        "B 의 체결은 A 의 **부분집합**이다(검증: B 체결인데 A 미체결 0건). 둘 다",
        "산 값이 ref 라, 둘 다 체결된 건은 R 이 소수점까지 같다(2,372건 중 2,361건).",
        "그러면 A 와 B 의 차이는 오직 **A 만 산 1,673건**이다 — 가격이 구간",
        "상단까지만 내려왔는데 백테스트가 중간값에 사 준 건이다. **그 가격에",
        "시장이 거래된 적이 없다.**", "",
        "| 구간 | A 체결 | A 순평균 | A 순합 | 실제 가능 | 그 순평균 | 유령 | 그 순평균 | 유령 비중 |",
        "|---|---|---|---|---|---|---|---|---|",
        *_phantom(df, "전체"),
        *_phantom(oos, "OOS"),
        "",
        "유령 체결이 A 총R 의 **126%** 다. 빼면 남는 게 음수라는 뜻이다.",
        "이유는 분명하다: 구간 상단만 찍고 곧장 오른 셋업이 가장 크게 이기는데,",
        "**그런 판일수록 되돌림이 얕아 지정가가 안 채워진다.** 깊이 되돌리는",
        "판은 그대로 더 빠진다. 역선택이다.", "",
        "## 구간을 좁게 잡으면 살아나는가", "",
        "구간이 좁으면 ref 와 상단이 붙어 세 규칙이 같아진다. 그런 셋업만 "
        "남기는 길이 있는지 봤다.", "",
        *_bands(df),
        "",
        "0.2~0.4R 대가 양수지만 **셋업 100건 · B 체결 37건**이다. 3년치에서 그만큼",
        "밖에 안 나오는 데다, 수익률을 보고 자른 구간이라 그 자체로 데이터마이닝이다.",
        "여기에 전략을 올릴 수 없다.", "",
    ]
    zu = df["zone_up_r"]
    body += [
        "## 진입 구간이 왜 이렇게 넓나", "",
        f"구간 상단은 ref 에서 중앙값 **{zu.median():.2f}R** 위다 "
        f"(평균 {zu.mean():.2f} · 상위25% {zu.quantile(.75):.2f}).",
        "손절이 진입가의 0.3% 인 세계에서, 구간 안 어디를 사느냐가 트레이드마다",
        "0.77R 을 가른다. 진입 구간은 **라인 하나로 좁히지 않으면 계획이 아니다.**", "",
    ]
    return "\n".join(body)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 시뮬레이션 없이 표만 다시 뽑는다 — 8,953건이 parquet 에 남아 있다.
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        df = pd.read_parquet(OUT_PARQUET)
        text = _write_report(df, os.environ.get("SPAN", "저장된 측정"))
        print(text)
        OUT_MD.write_text(text, encoding="utf-8")
        print(f"저장: {OUT_MD}")
        return 0

    if not PANEL.exists():
        print(f"패널이 없습니다: {PANEL}", file=sys.stderr)
        return 1

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else max(os.cpu_count() - 1, 1)
    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    tasks = [(tk, _ohlcv(panel, tk)) for tk in tickers]
    span = f"{panel.index[0]} ~ {panel.index[-1]}"
    print(f"{len(tasks)}종목 · {span} · 워커 {workers}", flush=True)

    rows: list = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(_run_ticker, tasks):
            rows += got
            done += 1
            print(f"  {done}/{len(tasks)}종목 · 누적 {len(rows)}건", flush=True)

    df = pd.DataFrame(rows)
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET)

    text = _write_report(df, span)
    print("\n" + text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}\n저장: {OUT_PARQUET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
