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

# MODE=daily 로 **일봉**에도 같은 칼을 댄다. 15분봉에서 죽은 그 가정
# (`_simulate_outcome`)은 봉 길이와 무관하게 같은 함수다 — 프로덕션이 실제로
# 돌리는 일봉 규칙이 성한지가 더 급하다.
MODE = os.environ.get("MODE", "intraday").strip().lower()
DAILY = MODE == "daily"

if DAILY:
    # scripts/measure_trade_plan_oos.py 와 같은 값이라야 그 +0.58R 을 대표한다.
    PANEL = Path(os.environ.get("PANEL", "data/price_panel_v1.parquet"))
    FILL_WINDOW, HOLD_WINDOW, MIN_LEN = 15, 30, 120
    COST_BPS = 40.0          # 왕복. 편도 20bp = 한국 증권사 미국주식 실제 자리
    SUFFIX = "-daily"
else:
    PANEL = Path(os.environ.get("PANEL", "data/intraday_panel_15m.parquet"))
    FILL_WINDOW, HOLD_WINDOW, MIN_LEN = 8, 26, MIN_BARS   # 3a·러너와 같은 값
    COST_BPS = 6.0
    SUFFIX = ""

OUT_MD = Path(f"docs/measurements/2026-08-12-entry-rule{SUFFIX}.md")
OUT_PARQUET = Path(f"data/entry_rule_trades{SUFFIX}.parquet")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

COOLDOWN = 3
MIN_RISK_PCT = 0.30
IS_START = pd.Timestamp("2024-12-20")

RULES = ("A_백테스트", "B_ref지정가", "C_상단지정가", "B_갭반영", "C_갭반영",
         "D_하단지정가", "D_하단갭반영", "E_시장가")
# ── 라인 하나로 좁힌 규칙 (2026-08-13 추가) ────────────────────────────────
# 위 A~C 는 전부 **구간**을 전제로 한 변형이고, R 을 짝지어 보려고 분모를 플랜
# 위험으로 고정해 뒀다. 실제 러너는 라인 하나에 걸고 **그 라인 기준 위험**으로
# 사이징한다 — 그래서 D·E 는 산 값으로 R 을 낸다(self basis). 비용도 그 위험폭
# 으로 나눠야 맞다(손절이 좁을수록 6bp 가 R 을 크게 먹는다).
#
#   D 하단 지정가  구간 **하단**에 걸어 가장 깊은 되돌림만 산다. 손절은 그
#                  하단 바로 아래(ATR 0.25배)라 위험폭이 제일 좁다.
#   E 시장가       되돌림을 **안 기다린다** — 다음 봉 시가에 무조건 산다.
#                  역선택(이기는 판일수록 안 되돌린다)이 원인이라면 이 쪽만
#                  살아남는다. 체결률 100%, 대신 제일 비싸게 사고 위험이 넓다.
_SELF_BASIS = ("D_하단지정가", "D_하단갭반영", "E_시장가")
# 갭 반영판 = 같은 지정가인데 **체결가만** 실제로 가능한 값으로 바꾼 것.
# 지정가 아래로 갭 하락하면 지정가가 아니라 그날 시가에 채워진다
# (`virtual_broker.scan_limit_fill` 이 실제로 min(시가, 지정가) 를 쓴다).
# 체결 봉·승패는 손절/목표가 정하므로 안 바뀐다 — R 만 다시 낸다.
_GAP_OF = {"B_갭반영": "B_ref지정가", "C_갭반영": "C_상단지정가",
           "D_하단갭반영": "D_하단지정가"}
_LIMIT_OF = {"B_ref지정가": "ref", "C_상단지정가": "aggressive",
             "D_하단지정가": "far"}


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _sim(rule: str, plan: dict, args: dict) -> dict:
    """한 셋업 × 한 규칙. 반환 R 은 **플랜 위험** 기준으로 맞춰 놓는다.

    숏은 부호가 전부 뒤집힌다. 공격적 체결선이 롱은 구간 **상단**, 숏은 구간
    **하단**이다 — 되돌림을 덜 기다리는 쪽이 어디냐가 방향마다 반대다.
    """
    lo, hi = plan["entry"]["low"], plan["entry"]["high"]
    ref, stop, tgt = plan["entry"]["ref"], plan["stop"], plan["targets"][0]
    long = plan["direction"] == "long"
    aggressive = hi if long else lo          # 백테스트가 체결로 치는 선
    far = lo if long else hi                 # 되돌림을 끝까지 기다리는 선
    plan_risk = (ref - stop) if long else (stop - ref)

    if rule in _SELF_BASIS:
        return _sim_single_line(rule, plan, args, far)

    if rule == "A_백테스트":
        fill_at, buy_at, rr = aggressive, ref, plan["rr"][0]
    elif rule == "B_ref지정가":
        # 체결 조건을 ref 로 좁힌다. 산(판) 값은 그대로 ref.
        fill_at, buy_at, rr = ref, ref, plan["rr"][0]
    else:
        # 공격적 선에 걸면 그 값에 산다(판다). 위험이 커지므로 목표 손익비도
        # 그 기준으로 다시 낸다 — 그래야 win 의 R 이 실제 이익이다.
        fill_at = buy_at = aggressive
        risk_c = (buy_at - stop) if long else (stop - buy_at)
        rr = ((tgt - buy_at) if long else (buy_at - tgt)) / risk_c if risk_c > 0 \
            else float("nan")

    # 체결 조건은 롱이면 lows <= entry_high, 숏이면 highs >= entry_low 다.
    e_lo = min(lo, fill_at) if long else fill_at
    e_hi = fill_at if long else max(hi, fill_at)
    res = _simulate_outcome(
        args["highs"], args["lows"], args["i"], plan["direction"],
        e_lo, e_hi, stop, tgt, rr,
        fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW,
        sessions=args["sessions"], opens=args["opens"], entry_ref=buy_at)

    # _simulate_outcome 은 |buy_at - stop| 을 1R 로 세고 나온다. 규칙끼리
    # 나란히 놓으려면 전부 플랜 위험으로 환산해야 한다.
    risk_used = (buy_at - stop) if long else (stop - buy_at)
    scale = risk_used / plan_risk if plan_risk > 0 else float("nan")
    return {"outcome": res["outcome"], "r": res["r"] * scale,
            "exit_idx": res["exit_idx"], "fill_idx": res["fill_idx"]}


def _sim_single_line(rule: str, plan: dict, args: dict, far: float) -> dict:
    """라인 하나에 걸고 **그 라인 기준**으로 R 을 낸다.

    A~C 와 달리 분모가 플랜 위험이 아니라 산 값의 위험이다 — 러너가 실제로
    사이징하는 값이고, 비용이 몇 R 인지도 이 위험폭이 정한다. 그래서 손절폭
    문턱(MIN_RISK_PCT)도 **이 라인 기준**으로 다시 건다. 통과 못 하면 "skip":
    체결이 안 된 게 아니라 그 라인에서는 애초에 거는 셋업이 아니라는 뜻이다.
    """
    stop, tgt = plan["stop"], plan["targets"][0]
    long = plan["direction"] == "long"
    i, opens = args["i"], args["all_opens"]

    if rule == "E_시장가":
        # 되돌림을 안 기다린다 = 다음 봉 시가. 체결 조건을 무한으로 열어 두면
        # `_simulate_outcome` 이 다음 봉에서 곧바로 체결로 잡는다(세션 마지막
        # 봉이면 nofill — 러너도 그 봉에서는 새로 못 산다).
        if i + 1 >= len(opens):
            return {"outcome": "skip", "r": 0.0, "risk_pct": float("nan"),
                    "fill_idx": None, "exit_idx": None}
        line = float(opens[i + 1])
        e_lo = line if long else float("-inf")
        e_hi = float("inf") if long else line
    else:
        line = far
        e_lo = e_hi = line

    risk = (line - stop) if long else (stop - line)
    rr = ((tgt - line) if long else (line - tgt)) / risk if risk > 0 else float("nan")
    risk_pct = risk / line * 100.0 if line > 0 else float("nan")
    # 손절폭 문턱은 러너와 같은 이유로 건다(비용 산수). 목표가 이미 등 뒤면
    # 거래가 아니다 — rr<=0 은 걸러 낸다.
    if not (risk > 0) or not (rr > 0) or (not DAILY and risk_pct < MIN_RISK_PCT):
        return {"outcome": "skip", "r": 0.0, "risk_pct": risk_pct,
                "fill_idx": None, "exit_idx": None}

    res = _simulate_outcome(
        args["highs"], args["lows"], i, plan["direction"],
        e_lo, e_hi, stop, tgt, rr,
        fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW,
        sessions=args["sessions"], opens=args["opens"], entry_ref=line)
    # `_simulate_outcome` 이 이미 |line - stop| 을 1R 로 센다 — 환산 불필요.
    return {**res, "risk_pct": risk_pct, "line": line}


def _gap_adjust(res: dict, plan: dict, limit: float, opens, plan_risk: float,
                *, self_basis: bool = False) -> dict:
    """지정가 아래로 갭이 났으면 **그날 시가**에 채워진다 — 그 체결가로 R 재계산.

    승패는 손절·목표가 정하므로 안 바뀐다. 바뀌는 것은 얼마에 샀느냐뿐이라
    R 만 다시 낸다 (timeout 은 원 백테스트가 0 으로 세므로 그대로 0).
    """
    j = res["fill_idx"]
    if j is None or res["outcome"] in ("nofill", "timeout", "skip"):
        return {"outcome": res["outcome"], "r": res["r"] if j is None else 0.0,
                "risk_pct": res.get("risk_pct", float("nan"))}
    long = plan["direction"] == "long"
    fill = min(opens[j], limit) if long else max(opens[j], limit)
    if self_basis:
        # 갭으로 더 좋게 샀으면 위험폭도 그만큼 좁아진다 — 비용이 몇 R 인지가
        # 같이 바뀌므로 risk_pct 도 체결가 기준으로 다시 낸다.
        plan_risk = (fill - plan["stop"]) if long else (plan["stop"] - fill)
        if not (plan_risk > 0):
            return {"outcome": "skip", "r": 0.0, "risk_pct": float("nan")}
    if res["outcome"] == "loss":
        exit_px = plan["stop"]
    elif res["outcome"] == "win":
        exit_px = plan["targets"][0]
    else:                                    # eod — 세션 마지막 봉 시가
        exit_px = opens[res["exit_idx"]]
    r = (exit_px - fill) if long else (fill - exit_px)
    return {"outcome": res["outcome"], "r": r / plan_risk,
            "risk_pct": (plan_risk / fill * 100.0) if self_basis else
                        res.get("risk_pct", float("nan"))}


def _run_ticker(args):
    tk, df = args
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    all_opens = df["Open"].to_numpy(dtype=float)
    # 일봉은 스윙이라 세션 청산이 없다. 주면 마지막 봉마다 시가로 털어 버린다.
    opens = None if DAILY else df["Open"].to_numpy(dtype=float)
    sessions = None if DAILY else session_ids(df.index)
    n = len(df)
    rows = []

    i = MIN_LEN
    while i < n - 1:
        plan = build_trade_plan(df.iloc[: i + 1], scale=1)
        # 분봉은 러너 문턱(롱 + 손절폭 0.30%)에서 자른다 — 3a 가 통과시킨 집합.
        # 일봉은 valid 면 다 담고 방향·등급은 행에 적어 리포트에서 슬라이스한다.
        if not plan["valid"] or (not DAILY and (
                plan["direction"] != "long" or plan["risk_pct"] < MIN_RISK_PCT)):
            i += 1
            continue

        ctx = {"highs": highs, "lows": lows, "opens": opens,
               "all_opens": all_opens, "sessions": sessions, "i": i}
        out = {r: _sim(r, plan, ctx) for r in RULES if r not in _GAP_OF}
        ref, stop = plan["entry"]["ref"], plan["stop"]
        long = plan["direction"] == "long"
        risk = (ref - stop) if long else (stop - ref)
        aggressive = plan["entry"]["high"] if long else plan["entry"]["low"]
        far = plan["entry"]["low"] if long else plan["entry"]["high"]
        lines = {"ref": ref, "aggressive": aggressive, "far": far}
        for gap_rule, base in _GAP_OF.items():
            out[gap_rule] = _gap_adjust(
                out[base], plan, lines[_LIMIT_OF[base]], all_opens, risk,
                self_basis=base in _SELF_BASIS)
        rows.append({
            "ticker": tk, "entry_date": df.index[i],
            "direction": plan["direction"], "cost_grade": plan["cost_grade"],
            "actionable": bool(plan["actionable"]),
            "risk_pct": plan["risk_pct"],
            # 공격적 체결선이 ref 에서 얼마나 떨어져 있나 — C 가 더 무는 값이다.
            "zone_up_r": abs(aggressive - ref) / risk if risk > 0 else float("nan"),
            **{f"{r}_outcome": out[r]["outcome"] for r in RULES},
            **{f"{r}_r": out[r]["r"] for r in RULES},
            # 라인 하나 규칙은 손절폭이 플랜과 다르다 → 비용 R 을 낼 때 쓴다.
            **{f"{r}_risk_pct": out[r].get("risk_pct", float("nan"))
               for r in _SELF_BASIS},
        })
        # 셋업 간격은 A 기준으로 고정한다 — 규칙마다 다르면 짝지은 비교가 깨진다.
        a = out["A_백테스트"]
        landing = a["exit_idx"] or a["fill_idx"] or (i + FILL_WINDOW)
        i = max(i + 1, landing + COOLDOWN)
    return rows


def _filled(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """체결된 것만. "skip" 은 그 라인에서 애초에 안 거는 셋업이라 함께 뺀다."""
    return df[~df[f"{rule}_outcome"].isin(("nofill", "skip"))]


def _net(df: pd.DataFrame, rule: str, cost_bps: float = COST_BPS) -> np.ndarray:
    """비용 차감 R. 체결된 것만.

    비용 bp 는 모든 규칙이 같지만 **몇 R 인지는 손절폭이 정한다.** 라인 하나
    규칙(D·E)은 산 값 기준 위험폭이 플랜과 다르므로 그 열을 쓴다.
    """
    sub = _filled(df, rule)
    if sub.empty:
        return np.array([])
    col = f"{rule}_risk_pct" if f"{rule}_risk_pct" in sub.columns else "risk_pct"
    cost_r = (cost_bps / 1e4) / (sub[col] / 100.0)
    return (sub[f"{rule}_r"] - cost_r).to_numpy(dtype=float)


def _block(df: pd.DataFrame, label: str) -> list[str]:
    lines = [f"### {label} — 셋업 {len(df):,}건", "",
             "| 규칙 | 체결 | 체결률 | 총R(비용전) | 순평균R | 순합R | p값 |",
             "|---|---|---|---|---|---|---|"]
    for rule in RULES:
        filled = _filled(df, rule)
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


def _invariants(df: pd.DataFrame) -> tuple[int, int, int]:
    """(B체결∧A미체결, A·B 둘 다 체결, 그중 R 이 같은 것).

    첫 값이 0 이라야 "A − B = 유령" 이라는 분해가 성립한다. 셋째는 둘 다 같은
    값에 사므로 같은 봉에 체결되면 R 이 소수점까지 같아야 한다는 확인이다.
    """
    fa = df["A_백테스트_outcome"] != "nofill"
    fb = df["B_ref지정가_outcome"] != "nofill"
    both = df[fa & fb]
    same = np.isclose(both["A_백테스트_r"], both["B_ref지정가_r"]).sum()
    return int((fb & ~fa).sum()), len(both), int(same)


def _bands(df: pd.DataFrame) -> list[str]:
    """구간이 좁으면 세 규칙이 같아진다. 좁은 셋업만 남기면 살아나는가?"""
    edges = [0, .2, .4, .6, .8, 1e9]
    names = ["~0.2R", "0.2~0.4R", "0.4~0.6R", "0.6~0.8R", "0.8R+"]
    band = pd.cut(df["zone_up_r"], edges, labels=names)
    shown = ("A_백테스트", "B_갭반영", "C_갭반영")
    lines = ["| 구간 폭 | 셋업 | A 순평균R | B 체결 | B갭 순평균R | C갭 순평균R |",
             "|---|---|---|---|---|---|"]
    for name, g in df.groupby(band, observed=True):
        cells = []
        for rule in shown:
            net = _net(g, rule)
            cells.append(f"{net.mean():+.3f}R" if len(net) else "—")
        nb = (g["B_ref지정가_outcome"] != "nofill").sum()
        lines.append(f"| {name} | {len(g):,} | {cells[0]} | {nb:,} | "
                     f"{cells[1]} | {cells[2]} |")
    return lines


def _single_lines(df: pd.DataFrame) -> list[str]:
    """라인 하나로 좁혔을 때 손절폭이 어떻게 변하나 — 비용 R 을 가르는 값이다.

    구간 하단에 걸면 손절이 바로 아래(ATR 0.25배)라 위험폭이 확 좁아지고,
    6bp 가 R 을 훨씬 크게 먹는다. 시장가는 반대로 넓어져 비용은 싸지지만
    목표까지의 손익비가 그만큼 줄어든다. 둘 다 **공짜가 아니다.**
    """
    lines = ["## 라인 하나로 좁히면 손절폭이 이렇게 바뀐다", "",
             f"| 규칙 | 거는 셋업 | skip | 손절폭 중앙 | 비용 {COST_BPS:.0f}bp 가 몇 R |",
             "|---|---|---|---|---|"]
    for rule in _SELF_BASIS:
        col = f"{rule}_risk_pct"
        if col not in df.columns:
            continue
        sub = _filled(df, rule)
        n_skip = int((df[f"{rule}_outcome"] == "skip").sum())
        rp = sub[col].median() if not sub.empty else float("nan")
        lines.append(f"| {rule} | {len(df) - n_skip:,} | {n_skip:,} | "
                     f"{rp:.3f}% | {(COST_BPS / 1e4) / (rp / 100):.3f}R |")
    plan_rp = df["risk_pct"].median()
    lines += ["", f"플랜(entry_ref) 기준 손절폭 중앙은 {plan_rp:.3f}% "
              f"= 비용 {(COST_BPS / 1e4) / (plan_rp / 100):.3f}R 이다.", ""]
    return lines


def _write_report(df: pd.DataFrame, span: str) -> str:
    if DAILY:
        head = [
            "# 진입 방식 — **일봉** 트레이드 플랜에 같은 칼을 댄다 (2026-08-12)",
            "",
            "15분봉(3a)이 체결 가정에서 죽었다"
            "(`2026-08-12-entry-rule.md`). 같은 `_simulate_outcome` 이",
            "일봉도 돌린다 — 프로덕션(가상 브로커·화면 판정·텔레그램·성적표)이",
            "딛고 선 **+0.58R** 이 성한지 본다.", "",
            "**결론: 마이너스는 아니지만 0 이다.** 프로덕션이 실제로 거는 것",
            "(actionable = 롱 + 등급 A/B)의 OOS 순평균이 +0.489R → **+0.022R**",
            "(p=0.26). 비용 전으로 봐도 +0.657R → +0.189R 이라 **수수료 가정과",
            "무관하게 이익의 70% 가 살 수 없는 가격에서 나오고 있었다.**",
            "프로덕션은 `plan[\"entry\"][\"high\"]` 에 지정가를 건다"
            "(`paper_trade_runner_toss.py:684`) — 아래 C 다.", "",
            "## 2026-08-13 추가 — 일봉도 라인 하나로 좁혀 봤다 (D·E)", "",
            "15분봉 쪽(`2026-08-12-entry-rule.md`)에서 D·E 를 채웠는데, D 는 "
            "손절폭 0.30% 문턱에 8,953 중 8,756 이 걸려 빠져 **사실상 못 쟀다.** "
            "일봉엔 그 문턱이 없다(러너가 안 건다) — 그래서 D 를 처음으로 "
            "제대로 잰다. **여기서도 양수는 없다.**", "",
            "- **걸 수 있는 것 중 최선은 여전히 C갭 +0.022R 이다** — 프로덕션이 "
            "이미 하고 있는 것. 라인을 옮겨서 나아지는 자리가 없다.",
            "- **D_하단은 비용이 죽인다.** actionable OOS 비용 전 +0.209R 인데 "
            "순평균은 **−0.555R** 이다. 구간 하단에 걸면 손절이 바로 아래"
            "(ATR 0.25배)라 손절폭 중앙이 0.60% 로 좁아지고, 왕복 40bp 가 "
            "**0.66R** 을 먹는다(플랜 기준 1.74% · 0.23R). 15분봉은 문턱이 "
            "막아서 못 갔고 일봉은 문턱이 없어 갔는데, 막힌 이유는 같다 — "
            "[[intraday-cost-dominates-r]] 이 일봉 하단 라인에도 그대로 걸린다.",
            "- **E_시장가는 되돌림을 안 기다려도 0 이다.** 체결률 96%(n=5,165) "
            "로 유령이 잡던 셋업을 거의 다 잡는데 비용 전 **+0.050R**, 순평균 "
            "−0.081R. 15분봉과 같은 결론이 일봉에서도 나왔다: 유령 수익은 "
            "**어떤 셋업을 골랐나가 아니라 싸게 산 값**이었다.",
            "- 손절폭이 D 0.60% · 플랜 1.74% · E 5.07% 로 라인마다 8배 벌어진다. "
            "**같은 40bp 가 0.66R 도 되고 0.08R 도 된다** — 라인을 바꾸면 "
            "비용 산수가 같이 바뀐다는 뜻이라, 비용 전 숫자만 보고 라인을 "
            "고르면 안 된다.", "",
            "**판정은 안 바뀐다.** 프로덕션(C)이 걸 수 있는 것 중 최선이고 그 "
            "값이 0 이다. 다음에 손볼 것은 주문 방식이 아니라 신호다.", "",
            f"279종목 일봉 · {span} · 왕복 {COST_BPS:.0f}bp(편도 20bp)", "",
        ]
    else:
        head = [
            "# 진입 방식 — 백테스트 규칙 vs 러너가 실제로 할 수 있는 것 (2026-08-12)",
            "",
            "**결론: 3a 의 +0.390R 은 실행할 수 없는 진입 위에 서 있었다.**",
            "실제로 걸 수 있는 주문(갭반영)은 OOS 에서 가장 좋은 것이 **−0.072R** "
            "(p=0.98) 이다. 비용 전으로 봐도 +0.071R 로 0 근처다.",
            "15분봉 자동 주문은 **켜면 안 된다.**", "",
            "## 2026-08-13 추가 — 라인 하나로 좁혀 봤다 (D·E)", "",
            "위 결론이 남긴 숙제가 \"진입을 라인 하나로 좁혀 다시 재라\" 였다. "
            "안 잰 라인 둘을 채웠다 — 구간 **하단**(D, 가장 깊은 되돌림)과 "
            "**되돌림을 안 기다리는 시장가**(E). **양수는 없다.**", "",
            "- **E_시장가가 걸 수 있는 것 중 가장 낫다: OOS 순평균 −0.036R** "
            "(p=0.998, 체결률 90%, n=2,853). B갭 −0.165R · C갭 −0.072R 보다 "
            "좋지만 0 이다.",
            "- **비용 문제가 아니다.** E 는 비용 전으로도 OOS +0.022R · 전체 "
            "+0.008R 이다. 손절폭이 중앙 1.38% 로 넓어 6bp 가 0.044R 밖에 "
            "안 먹는데도 남는 게 없다 — 넓은 손절이 [[intraday-cost-dominates-r]] "
            "의 비용 문턱을 통과시켜 줬지만, 통과한 자리에 기대값이 없다.",
            "- **유령 수익의 정체가 갈렸다: \"어떤 셋업을 골랐나\"가 아니라 "
            "\"싸게 산 값\" 이었다.** 역선택(이기는 판은 안 되돌린다)이 원인이면 "
            "되돌림을 안 기다리는 E 가 그 판들을 되찾아야 한다. E 는 유령이 "
            "잡던 셋업을 90% 다 잡고도 0 이다. 그 +1.373R 은 셋업 선택이 아니라 "
            "**시장에 없던 할인가**가 만든 값이었다.",
            "- **D_하단은 구조적으로 죽는다.** 손절이 구간 하단 바로 아래"
            "(ATR 0.25배)라 8,953 셋업 중 8,756 건이 손절폭 0.30% 문턱에 걸려 "
            "빠지고, 남은 197 건 중 체결은 32 건이다. OOS 는 7 건 · −0.194R. "
            "양수(+0.835R)는 본 구간 25 건에서 나온 값이라 근거가 못 된다.", "",
            "**게이트(`RUN_KNOWN_NEGATIVE`)는 유지한다.** 막힌 것이 진입 *실행* "
            "이 아니라 **셋업 자체**임이 이번에 갈렸다 — 어느 라인에 걸어도 0 "
            "이면 다음에 손볼 것은 주문 방식이 아니라 신호다.", "",
            f"30종목 15분봉 · {span} · 롱 + 손절폭 {MIN_RISK_PCT}% 이상 · "
            f"왕복 {COST_BPS:.0f}bp", "",
        ]
    body = [
        *head,
        "| 규칙 | 체결 조건 | 산 값 |",
        "|---|---|---|",
        "| A_백테스트 | 저가가 **공격적 선**(롱=구간 상단)에 닿으면 | **ref**(구간 중간) |",
        "| B_ref지정가 | 저가가 **ref** 에 닿아야 | ref |",
        "| C_상단지정가 | 저가가 공격적 선에 닿으면 | 공격적 선 |",
        "| B_갭반영 / C_갭반영 | 같음 | **min(그날 시가, 지정가)** |",
        "| D_하단지정가 | 저가가 **구간 하단**에 닿아야 | 구간 하단 |",
        "| D_하단갭반영 | 같음 | **min(그날 시가, 구간 하단)** |",
        "| E_시장가 | 무조건 (되돌림 안 기다림) | **다음 봉 시가** |",
        "",
        "A 는 B 의 체결 수와 C 의 가격을 동시에 가진다 — 실행 가능한 규칙이 "
        "아니라 **상한**이다.",
        "**갭반영판이 실제로 걸 수 있는 주문이다.** 지정가 아래로 갭이 나면 "
        "지정가가 아니라 그날 시가에 채워진다 — `virtual_broker.scan_limit_fill` "
        "이 이미 그렇게 센다. 무보정 B·C 는 그만큼 불리하게 잡힌 값이라 같이 둔다.",
        "A~C 의 R 은 플랜 위험(|entry_ref − stop|) 으로 나눈다 — 짝지어 보려고 "
        "분모를 고정한 것이다. **D·E 는 산 값 기준(|체결가 − stop|)** 이다: "
        "러너가 실제로 그 값으로 사이징하고, 비용이 몇 R 인지도 그 위험폭이 "
        "정한다. " + ("일봉은 러너가 손절폭 문턱을 안 걸어 D·E 도 안 건다 — "
         "**skip** 은 손절이 체결가 위로 가거나(갭) 목표가 이미 등 뒤인 판뿐이다."
         if DAILY else
         "그래서 손절폭 문턱도 D·E 는 그 라인 기준으로 다시 걸고, 통과 못 한 "
         "셋업은 미체결이 아니라 **skip**(그 라인에서는 안 거는 판)으로 빠진다.")
        + " 아래 체결률의 분모는 전체 셋업이다.", "",
    ]
    oos = df[df["entry_date"] < IS_START]
    ins = df[df["entry_date"] >= IS_START]
    body += _block(oos, f"규칙을 고를 때 안 본 구간 (~{(IS_START - pd.Timedelta(days=1)).date()})")
    body += _block(ins, f"본 구간 ({IS_START.date()}~)")
    body += _block(df, "전체")

    if DAILY:
        # 프로덕션이 실제로 거는 것만 따로 본다. 전체 +0.58R 은 숏·저등급까지
        # 다 넣은 숫자고, `actionable` (롱 + 실행등급 A/B) 이 화면·텔레그램·
        # 가상 브로커가 따르는 문턱이다.
        body += ["## 프로덕션이 실제로 거는 것만 (actionable = 롱 + 등급 A/B)", ""]
        act = df[df["actionable"]]
        body += _block(act[act["entry_date"] < IS_START],
                       "actionable · 안 본 구간")
        body += _block(act, "actionable · 전체")
        body += ["## 방향별 (전체)", ""]
        for d, lab in (("long", "롱"), ("short", "숏")):
            sub = df[df["direction"] == d]
            if not sub.empty:
                body += _block(sub, lab)

    inv, both, same = _invariants(df)
    n_a = int((df["A_백테스트_outcome"] != "nofill").sum())
    n_b = int((df["B_ref지정가_outcome"] != "nofill").sum())
    body += [
        "## A 의 수익은 어디서 왔나 — 유령 체결", "",
        f"B 의 체결은 A 의 **부분집합**이다(검증: B 체결인데 A 미체결 {inv}건).",
        f"둘 다 산 값이 ref 라, 둘 다 체결된 건은 R 이 소수점까지 같다"
        f"({both:,}건 중 {same:,}건). 그러면 A 와 B 의 차이는 오직 **A 만 산 "
        f"{n_a - n_b:,}건**이다 — 가격이 공격적 체결선까지만 왔는데 백테스트가",
        "구간 중간값에 사 준 건이다. **그 가격에 시장이 거래된 적이 없다.**", "",
        "| 구간 | A 체결 | A 순평균 | A 순합 | 실제 가능 | 그 순평균 | 유령 | 그 순평균 | 유령 비중 |",
        "|---|---|---|---|---|---|---|---|---|",
        *_phantom(df, "전체"),
        *_phantom(oos, "OOS"),
        *(_phantom(df[df["actionable"]], "actionable") if DAILY else []),
        "",
        "비중이 100% 를 넘으면 **유령을 빼면 남는 게 음수**라는 뜻이다.",
        "역선택이다: 공격적 체결선만 찍고 곧장 간 셋업이 가장 크게 이기는데,",
        "**그런 판일수록 되돌림이 얕아 지정가가 안 채워진다.** 깊이 되돌리는",
        "판은 그대로 더 간다.", "",
        "## 구간을 좁게 잡으면 살아나는가", "",
        "구간이 좁으면 ref 와 상단이 붙어 세 규칙이 같아진다. 그런 셋업만 "
        "남기는 길이 있는지 봤다.", "",
        *_bands(df),
        "",
        "좁은 대가 양수로 나와도 **표본이 몇 건인지** 먼저 볼 것. 수익률을 보고",
        "자른 구간이라 그 자체가 데이터마이닝이다.", "",
    ]
    body += _single_lines(df)
    zu = df["zone_up_r"]
    body += [
        "## 진입 구간이 얼마나 넓나", "",
        f"공격적 체결선은 ref 에서 중앙값 **{zu.median():.2f}R** 떨어져 있다 "
        f"(평균 {zu.mean():.2f} · 상위25% {zu.quantile(.75):.2f}).",
        "이 값이 구간 안 어디서 사느냐가 트레이드마다 가르는 크기다.",
        "진입 구간은 **라인 하나로 좁히지 않으면 계획이 아니다** — 그래서 "
        "좁혀서 다시 쟀고(D·E), 어느 라인도 양수가 아니다. 구간 폭은 "
        "**A 가 왜 그렇게 좋아 보였는지**를 설명하는 값이지, 고치면 되는 "
        "결함이 아니었다.", "",
        f"생성: `MODE={MODE} python scripts/measure_entry_rule.py`", "",
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
    tasks = [(tk, d) for tk in tickers
             if len(d := _ohlcv(panel, tk)) > MIN_LEN + FILL_WINDOW + HOLD_WINDOW]
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
