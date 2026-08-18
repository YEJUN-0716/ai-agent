"""
트레이드 플랜 검증(백테스트) — 셋업이 목표/손절 중 뭘 먼저 쳤나
==================================================================
`trade_plan.build_trade_plan` 이 만든 라인이 **실제로 통했는지** 과거 데이터로
채점한다. 포트폴리오 수익률이 아니라 **셋업 단위 결과**를 본다:

    각 시점 유효 플랜 → 진입 구간에 가격이 닿았나(체결) → 닿았다면 손절과
    목표 중 무엇을 먼저 쳤나 → 방향별 체결률·승률·평균 R·기대값(R).

R 은 위험 1단위 기준 손익. 둘 다 같은 봉이면 **손절 우선(보수적)**, 홀드
기간 내 미결이면 timeout(0).

**산 값은 구간 중간값이 아니라 걸 수 있는 지정가다.** 러너는 구간 상단에
지정가를 걸고(`paper_trade_runner_toss.py:667`) 갭이 나면 그날 시가에
채워진다 — `placeable_r` 이 그 값으로 R 을 다시 낸다. 2026-08-12 측정에서
중간값 가정이 일봉 OOS +0.489R 중 +0.467R 을 만들고 있었다
(`docs/measurements/2026-08-12-entry-rule-daily.md`). 그래서 손절이 -1.0 이
아니다 — 플랜보다 비싸게 샀으면 그만큼 더 잃는다.

**판 값도 같은 규칙이다 (2026-08-16).** 손절가·목표가를 그대로 적으면 갭을
지나간 날 시장에 없던 가격에 파는 것이다. 손절은 아래로 갭하면 그날 시가
(더 나쁘다), 목표는 위로 갭하면 그날 시가(더 좋다) — `virtual_broker.
scan_plan_exit` 이 이미 쓰던 자다. 이걸 안 걸었을 때 손절 아래에서 체결된
건들이 `loss` 라벨로 **+1.36R** 을 적고 있었다.

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
    sessions: np.ndarray | None = None, opens: np.ndarray | None = None,
    entry_ref: float | None = None,
) -> dict:
    """
    start_idx 다음 봉부터 진입 체결을 찾고, 체결되면 손절/목표를 시뮬레이션.

    반환: {"outcome": "win"|"loss"|"timeout"|"eod"|"nofill", "r": float,
           "fill_idx": int|None, "exit_idx": int|None}
      long  체결: 이후 봉의 Low  <= entry_high (되돌림 진입)
      short 체결: 이후 봉의 High >= entry_low

    sessions 를 주면 **당일 청산 단타**로 시뮬레이션한다 (opens 도 함께 필요).
    세션 마지막 봉에서는 손절·목표를 보지 않고 그 봉의 **시가**로 턴다 —
    러너가 15:45 ET 에 시장가를 내는 것과 같은 규칙이어야, 여기서 잰 숫자가
    러너를 대표한다. 마감 종가로 재면 러너가 못 내는 15분치가 섞인다.
    """
    n = len(highs)
    intraday = sessions is not None
    if intraday and opens is None:
        raise ValueError("sessions 를 주면 opens 도 필요합니다 (EOD 청산가).")

    def _is_session_end(k: int) -> bool:
        return intraday and (k + 1 >= n or sessions[k + 1] != sessions[k])

    # 단타는 같은 세션 안에서만 체결을 찾는다. 세션 마지막 봉은 뺀다 —
    # 거기서 체결되면 같은 봉에서 바로 청산해야 하는데 봉 안의 순서를 모른다.
    fill_idx = None
    for j in range(start_idx + 1, min(start_idx + 1 + fill_window, n)):
        if intraday and (sessions[j] != sessions[start_idx] or _is_session_end(j)):
            break
        if direction == "long" and lows[j] <= entry_high:
            fill_idx = j
            break
        if direction == "short" and highs[j] >= entry_low:
            fill_idx = j
            break
    if fill_idx is None:
        return {"outcome": "nofill", "r": 0.0, "fill_idx": None, "exit_idx": None}

    # EOD 청산 R 은 **플랜의 entry_ref** 로 재야 한다. rr 이 그 기준으로
    # 계산돼 있어서, 다른 기준(예: 구간 중간값)을 쓰면 목표가에 딱 닿아 턴
    # 트레이드의 R 이 rr 과 안 맞는다. 안 주면 구간 중간값으로 대신한다.
    if entry_ref is None:
        entry_ref = (entry_low + entry_high) / 2
    for k in range(fill_idx, min(fill_idx + hold_window, n)):
        if intraday and _is_session_end(k):
            exit_px = float(opens[k])
            if direction == "long":
                r = (exit_px - entry_ref) / (entry_ref - stop)
            else:
                r = (entry_ref - exit_px) / (stop - entry_ref)
            return {"outcome": "eod", "r": round(float(r), 4),
                    "fill_idx": fill_idx, "exit_idx": k}
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


def placeable_r(res: dict, plan: dict, limit: float, opens: np.ndarray,
                plan_risk: float, *, self_basis: bool = False) -> dict:
    """지정가에 **실제로 채워지는 값**으로 R 을 다시 낸다.

    체결 조건(롱=구간 상단 터치)은 그대로다 — 바뀌는 것은 산 값뿐이다.
    지정가 아래로 갭이 나면 지정가가 아니라 그날 시가에 채워진다
    (`virtual_broker.scan_limit_fill` 이 이미 min(시가, 지정가) 를 쓴다).
    승패는 손절·목표가 정하므로 안 바뀐다 — R 만 다시 낸다.

    분모는 기본이 **플랜 위험**(|entry_ref − stop|) 이다: 러너가 그 값으로
    사이징한다(`paper_trade_runner_toss.plan_position_size`). self_basis 는
    라인 하나에 걸고 그 라인 기준으로 사이징하는 경우(D·E)에만 쓴다.
    """
    j = res["fill_idx"]
    if j is None:                            # 미체결 — 산 적이 없다
        return {**res, "fill_price": float("nan"),
                "risk_pct": res.get("risk_pct", float("nan"))}
    long = plan["direction"] == "long"
    fill = min(opens[j], limit) if long else max(opens[j], limit)
    if res["outcome"] in ("timeout", "skip"):
        # 미결은 원 백테스트가 0 으로 센다. 산 값은 남긴다 — 체결됐으니
        # 거래비용은 실제로 나갔고, 비용 환산에 그 값이 필요하다.
        return {**res, "r": 0.0, "fill_price": float(fill),
                "exit_price": float(fill),
                "risk_pct": res.get("risk_pct", float("nan"))}
    if self_basis:
        # 갭으로 더 좋게 샀으면 위험폭도 그만큼 좁아진다 — 비용이 몇 R 인지가
        # 같이 바뀌므로 risk_pct 도 체결가 기준으로 다시 낸다.
        plan_risk = (fill - plan["stop"]) if long else (plan["stop"] - fill)
        if not (plan_risk > 0):
            return {**res, "outcome": "skip", "r": 0.0,
                    "fill_price": float(fill), "risk_pct": float("nan")}
    # 청산가도 **걸 수 있는 값**이어야 한다 — 진입에 적용한 규칙 그대로다.
    # 손절가·목표가를 그대로 적으면 갭을 지나간 날 시장에 없던 가격이 된다.
    # `virtual_broker.scan_plan_exit` 이 이미 쓰는 규칙과 같은 자다.
    #   손절: 아래로 갭하면 손절가에 못 판다 → 그날 시가 (더 나쁘다)
    #   목표: 위로 갭하면 지정가 매도가 시가에 채워진다 → 그날 시가 (더 좋다)
    ex_open = opens[res["exit_idx"]]
    if res["outcome"] == "loss":
        exit_px = min(ex_open, plan["stop"]) if long else max(ex_open, plan["stop"])
    elif res["outcome"] == "win":
        exit_px = max(ex_open, plan["targets"][0]) if long else \
            min(ex_open, plan["targets"][0])
    else:                                    # eod — 세션 마지막 봉 시가
        exit_px = ex_open
    r = (exit_px - fill) if long else (fill - exit_px)
    return {**res, "r": float(r / plan_risk), "fill_price": float(fill),
            "exit_price": float(exit_px),
            "risk_pct": (plan_risk / fill * 100.0) if self_basis else
                        res.get("risk_pct", float("nan"))}


def _stats(trades: list[dict]) -> dict:
    """트레이드 목록 → 체결률·승률·평균R·기대값 집계."""
    filled = [t for t in trades if t["outcome"] in ("win", "loss", "timeout", "eod")]
    wins = [t for t in filled if t["outcome"] == "win"]
    losses = [t for t in filled if t["outcome"] == "loss"]
    resolved = wins + losses
    return {
        "setups": len(trades),
        "filled": len(filled),
        "nofill": len(trades) - len(filled),
        "wins": len(wins),
        "losses": len(losses),
        # 뺄셈으로 세면 안 된다 — EOD 청산이 전부 timeout 으로 잡힌다.
        "timeouts": len([t for t in trades if t["outcome"] == "timeout"]),
        "eod_exits": len([t for t in trades if t["outcome"] == "eod"]),
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
    sessions: np.ndarray | None = None, scale: int = 1,
) -> dict:
    """
    한 종목 OHLCV 전 구간을 걸어가며 유효 플랜을 시뮬레이션한다.

    겹치는 트레이드를 막으려고, 한 셋업이 종료된 뒤 cooldown 봉만큼 건너뛴다.
    sessions 를 주면 당일 청산 단타로 시뮬레이션한다 (`_simulate_outcome` 참고).
    반환: {"all": stats, "long": stats, "short": stats, "trades": [...]}
    """
    # 창이 scale 배로 넓어지면 워밍업도 그만큼 필요하다. 호출자가 min_history 를
    # 직접 준 경우에는 건드리지 않는다.
    if min_history == MIN_BARS and scale != 1:
        min_history = MIN_BARS * scale

    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    # 갭 체결가에 항상 필요하다 — 단타(EOD 청산)만 쓰던 값이 아니다.
    opens = df["Open"].to_numpy(dtype=float)
    n = len(df)
    trades: list[dict] = []

    i = min_history
    while i < n - 1:
        plan = build_trade_plan(df.iloc[: i + 1], min_rr=min_rr, scale=scale)
        if not plan["valid"]:
            i += 1
            continue
        res = _simulate_outcome(
            highs, lows, i, plan["direction"],
            plan["entry"]["low"], plan["entry"]["high"],
            plan["stop"], plan["targets"][0], plan["rr"][0],
            fill_window=fill_window, hold_window=hold_window,
            sessions=sessions, opens=opens, entry_ref=plan["entry"]["ref"],
        )
        # 백테스트는 구간 상단만 닿아도 체결로 치면서 산 값은 구간 중간으로
        # 적었다 — 시장에 없던 가격이다. 러너가 실제로 거는 지정가(구간 상단,
        # 갭이면 시가)로 R 을 다시 낸다.
        long = plan["direction"] == "long"
        limit = plan["entry"]["high"] if long else plan["entry"]["low"]
        res = placeable_r(res, plan, limit, opens,
                          abs(plan["entry"]["ref"] - plan["stop"]))
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
