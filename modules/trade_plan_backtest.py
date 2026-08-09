"""
트레이드 플랜 검증(백테스트) — 셋업이 목표/손절 중 뭘 먼저 쳤나
==================================================================
`trade_plan.build_trade_plan` 이 만든 라인이 **실제로 통했는지** 과거 데이터로
채점한다. 포트폴리오 수익률이 아니라 **셋업 단위 결과**를 본다:

    각 시점 유효 플랜 → 진입 구간에 가격이 닿았나(체결) → 닿았다면 손절과
    목표 중 무엇을 먼저 쳤나 → 방향별 체결률·승률·평균 R·기대값(R).

R 은 위험 1단위 기준 손익. 목표를 먼저 치면 +R:R, 손절을 먼저 치면 -1.0,
둘 다 같은 봉이면 **손절 우선(보수적)**, 홀드 기간 내 미결이면 timeout(0).

`_simulate_outcome` 는 명시적 플랜 좌표만 받는 순수 함수라 결정적으로
테스트한다. `backtest_trade_plans` 는 매 봉 build_trade_plan 을 재계산하므로
느리다(오프라인 측정 전용 — ic_weight_updater 와 같은 성격). 네트워크 無.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.trade_plan import DEFAULT_MIN_RR, MIN_BARS, build_trade_plan

DEFAULT_FILL_WINDOW = 20   # 이 봉 수 안에 진입 구간에 안 닿으면 미체결
DEFAULT_HOLD_WINDOW = 40   # 체결 후 이 봉 수 안에 손절/목표 안 나면 timeout
DEFAULT_COOLDOWN = 3       # 한 트레이드 종료 후 다음 셋업까지 최소 간격


def _simulate_outcome(
    highs: np.ndarray, lows: np.ndarray, start_idx: int, direction: str,
    entry_low: float, entry_high: float, stop: float, target: float, rr: float,
    *, fill_window: int = DEFAULT_FILL_WINDOW, hold_window: int = DEFAULT_HOLD_WINDOW,
) -> dict:
    """
    start_idx 다음 봉부터 진입 체결을 찾고, 체결되면 손절/목표를 시뮬레이션.

    반환: {"outcome": "win"|"loss"|"timeout"|"nofill", "r": float,
           "fill_idx": int|None, "exit_idx": int|None}
      long  체결: 이후 봉의 Low  <= entry_high (되돌림 진입)
      short 체결: 이후 봉의 High >= entry_low
    """
    n = len(highs)
    fill_idx = None
    for j in range(start_idx + 1, min(start_idx + 1 + fill_window, n)):
        if direction == "long" and lows[j] <= entry_high:
            fill_idx = j
            break
        if direction == "short" and highs[j] >= entry_low:
            fill_idx = j
            break
    if fill_idx is None:
        return {"outcome": "nofill", "r": 0.0, "fill_idx": None, "exit_idx": None}

    for k in range(fill_idx, min(fill_idx + hold_window, n)):
        if direction == "long":
            hit_stop = lows[k] <= stop
            hit_tgt = highs[k] >= target
        else:
            hit_stop = highs[k] >= stop
            hit_tgt = lows[k] <= target
        if hit_stop:                       # 같은 봉에 둘 다면 손절 우선 (보수적)
            return {"outcome": "loss", "r": -1.0, "fill_idx": fill_idx, "exit_idx": k}
        if hit_tgt:
            return {"outcome": "win", "r": float(rr), "fill_idx": fill_idx, "exit_idx": k}
    return {"outcome": "timeout", "r": 0.0, "fill_idx": fill_idx, "exit_idx": None}


def _stats(trades: list[dict]) -> dict:
    """트레이드 목록 → 체결률·승률·평균R·기대값 집계."""
    filled = [t for t in trades if t["outcome"] in ("win", "loss", "timeout")]
    wins = [t for t in filled if t["outcome"] == "win"]
    losses = [t for t in filled if t["outcome"] == "loss"]
    resolved = wins + losses
    return {
        "setups": len(trades),
        "filled": len(filled),
        "nofill": len(trades) - len(filled),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(filled) - len(resolved),
        "win_rate": (len(wins) / len(resolved)) if resolved else float("nan"),
        # avg_r: timeout 을 0 으로 포함한 체결 트레이드 평균 (실현 기대 R)
        "avg_r": (sum(t["r"] for t in filled) / len(filled)) if filled else float("nan"),
        # expectancy_r: 결판난 트레이드만의 기대값
        "expectancy_r": (sum(t["r"] for t in resolved) / len(resolved)) if resolved else float("nan"),
    }


def backtest_trade_plans(
    df: pd.DataFrame, *, min_rr: float = DEFAULT_MIN_RR,
    fill_window: int = DEFAULT_FILL_WINDOW, hold_window: int = DEFAULT_HOLD_WINDOW,
    cooldown: int = DEFAULT_COOLDOWN, min_history: int = MIN_BARS,
) -> dict:
    """
    한 종목 OHLCV 전 구간을 걸어가며 유효 플랜을 시뮬레이션한다.

    겹치는 트레이드를 막으려고, 한 셋업이 종료된 뒤 cooldown 봉만큼 건너뛴다.
    반환: {"all": stats, "long": stats, "short": stats, "trades": [...]}
    """
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    n = len(df)
    trades: list[dict] = []

    i = min_history
    while i < n - 1:
        plan = build_trade_plan(df.iloc[: i + 1], min_rr=min_rr)
        if not plan["valid"]:
            i += 1
            continue
        res = _simulate_outcome(
            highs, lows, i, plan["direction"],
            plan["entry"]["low"], plan["entry"]["high"],
            plan["stop"], plan["targets"][0], plan["rr"][0],
            fill_window=fill_window, hold_window=hold_window,
        )
        # 가격 좌표도 함께 남긴다. R 은 위험 1단위 기준이라 그 자체로는
        # 거래비용을 못 잰다 — 같은 +1R 이어도 손절이 1% 떨어져 있으면
        # 수수료가 5% 떨어진 경우의 다섯 배를 먹는다. 비용을 R 로 바꾸려면
        # 위험이 가격의 몇 %였는지가 있어야 한다.
        trades.append({
            "idx": i, "direction": plan["direction"],
            "confidence": plan["confidence"],
            "entry_ref": plan["entry"]["ref"],
            "stop_price": plan["stop"],
            "target_price": plan["targets"][0],
            **res,
        })
        landing = res["exit_idx"] or res["fill_idx"] or (i + fill_window)
        i = max(i + 1, landing + cooldown)

    return {
        "all": _stats(trades),
        "long": _stats([t for t in trades if t["direction"] == "long"]),
        "short": _stats([t for t in trades if t["direction"] == "short"]),
        "trades": trades,
    }
