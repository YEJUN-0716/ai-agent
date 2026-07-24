"""
ICT 트레이드 플랜 생성기 — 방향 + 진입/손절/목표 라인
========================================================
`ict_analysis.py` 가 감지한 구조(Order Block / FVG / 스윙 / Premium·Discount)를
**실제 매매 라인**으로 조립한다. 종목 하나를 넣으면 다음을 돌려준다:

    방향(long / short / none) · 진입 구간 · 손절가 · 목표가(T1, T2)
    · 목표별 손익비(R:R) · 확신도 · 근거 목록

설계 원칙
--------
- **양방향.** 약세 구조면 숏 플랜을, 강세 구조면 롱 플랜을 만든다. 방향은
  `calc_ict_adjustment` 의 순(net) 점수 부호로 정한다 (자동주문은 하지 않는다 —
  이 모듈은 라인만 계산한다).
- **기하(라인 계산)와 감지(구조 탐지)를 분리한다.** R:R·정렬·유효성 판정은 순수
  함수 `_assemble_plan` 에 몰아넣어 결정적으로 테스트한다. `build_trade_plan` 은
  ict_analysis 로 구조를 찾아 그 좌표를 `_assemble_plan` 에 넘길 뿐이다.
- **네트워크·Streamlit 의존성 없음.** OHLCV DataFrame 만 받는다.

손절은 구조 무효화 지점(진입 구간 반대편) + ATR 완충으로 잡고, 목표는 반대편
유동성(스윙 고/저점, Premium·Discount 극단)으로 잡는다. 임계값은 전부 모듈
상수로 올려 두었다 — 바꾸면 tests/test_trade_plan.py 가 잡는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.ict_analysis import (
    calc_ict_adjustment,
    find_fvg,
    find_order_blocks,
    find_swing_points,
    premium_discount,
)

# ── 방향 판정 임계 ──────────────────────────────────────────────────
BIAS_TH = 10          # |ICT 조정점수| 가 이 이상이어야 방향성 인정 (미만이면 none)
CONF_HIGH_TH = 25     # 확신도 high 경계
CONF_MED_TH = 12      # 확신도 medium 경계

# ── 숏 레짐 필터 ────────────────────────────────────────────────────
# 숏은 종목이 실제로 약세일 때만 허용한다. 미국주식은 장기 상승 드리프트가
# 있어 강세장에서 숏을 치면 흐름을 거스른다 (유니버스 실측: 숏 +0.37R vs 롱
# +0.71R). 게이트: 현재가 < REGIME_MA 이평 AND 이평이 상승 중이 아님.
REGIME_MA = 50
REGIME_SLOPE_LOOKBACK = 20

# ── 라인 계산 ──────────────────────────────────────────────────────
DEFAULT_MIN_RR = 1.5      # T1 손익비가 이 미만이면 유효 셋업 아님
ATR_WINDOW = 14
ATR_STOP_BUFFER = 0.25    # 손절 완충 = 구조 극단 너머 ATR 의 이 배수
NEAR_ZONE_PCT = 0.03      # 진입 구조는 현재가 ±3% 안쪽만 (추격 방지)
FALLBACK_BUFFER_PCT = 0.005  # ATR 을 못 구할 때 완충 = 현재가의 0.5%

MIN_BARS = 60             # 이보다 짧으면 구조를 믿을 수 없다


def _atr(df: pd.DataFrame, window: int = ATR_WINDOW) -> float:
    """마지막 ATR(평균 실질 범위). 계산 불가면 현재가의 FALLBACK_BUFFER_PCT."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(tr.rolling(window).mean().iloc[-1])
    if not np.isfinite(atr) or atr <= 0:
        atr = float(close.iloc[-1]) * FALLBACK_BUFFER_PCT
    return atr


def _rr(direction: str, entry_ref: float, stop: float, target: float) -> float:
    """목표 하나의 손익비. 위험(진입-손절)이 0 이하이거나 방향이 어긋나면 nan."""
    if direction == "long":
        risk = entry_ref - stop
        reward = target - entry_ref
    else:  # short
        risk = stop - entry_ref
        reward = entry_ref - target
    if risk <= 0 or reward <= 0:
        return float("nan")
    return reward / risk


def _assemble_plan(
    direction: str,
    current: float,
    entry_low: float,
    entry_high: float,
    stop: float,
    targets: list[float],
    *,
    min_rr: float = DEFAULT_MIN_RR,
) -> dict:
    """
    순수 기하: 좌표를 받아 entry_ref·R:R·정렬·유효성을 계산한다.

    유효(valid) 조건
      long : stop < entry_low <= entry_high, 모든 목표 > entry_ref, T1 R:R >= min_rr
      short: entry_low <= entry_high < stop, 모든 목표 < entry_ref, T1 R:R >= min_rr
    """
    entry_low, entry_high = min(entry_low, entry_high), max(entry_low, entry_high)
    entry_ref = (entry_low + entry_high) / 2.0
    targets = [float(t) for t in targets]
    rr = [_rr(direction, entry_ref, stop, t) for t in targets]

    if direction == "long":
        ordering_ok = stop < entry_low and all(t > entry_ref for t in targets)
    elif direction == "short":
        ordering_ok = stop > entry_high and all(t < entry_ref for t in targets)
    else:
        ordering_ok = False

    t1_rr = rr[0] if rr else float("nan")
    valid = bool(ordering_ok and np.isfinite(t1_rr) and t1_rr >= min_rr)

    reason_invalid = ""
    if not ordering_ok:
        reason_invalid = "진입/손절/목표 정렬 불가 (구조가 방향과 어긋남)"
    elif not (np.isfinite(t1_rr) and t1_rr >= min_rr):
        reason_invalid = f"손익비 부족 (T1 R:R {t1_rr:.2f} < {min_rr})"

    return {
        "direction": direction,
        "current": round(current, 2),
        "entry": {
            "low": round(entry_low, 2),
            "high": round(entry_high, 2),
            "ref": round(entry_ref, 2),
        },
        "stop": round(stop, 2),
        "targets": [round(t, 2) for t in targets],
        "rr": [round(r, 2) if np.isfinite(r) else None for r in rr],
        "valid": valid,
        "reason_invalid": reason_invalid,
    }


def _pick_long_entry(df: pd.DataFrame, cur: float) -> tuple[float, float, str]:
    """롱 진입 구간: 현재가 아래 가장 가까운 미체결 강세 구조 → 폴백은 Discount."""
    obs = find_order_blocks(df, lookback=80, min_move_pct=1.0)
    cands = [o for o in obs
             if o["type"] == "bull" and not o["mitigated"] and o["top"] <= cur * (1 + NEAR_ZONE_PCT)]
    if cands:
        ob = max(cands, key=lambda o: o["top"])          # 현재가에 가장 가까운(높은) 지지 OB
        return ob["bottom"], ob["top"], "Bullish OB 지지"

    fvgs = [f for f in find_fvg(df, lookback=80, min_gap_pct=0.03)
            if f["type"] == "bull" and not f["filled"] and f["top"] <= cur * (1 + NEAR_ZONE_PCT)]
    if fvgs:
        f = max(fvgs, key=lambda f: f["top"])
        return f["bottom"], f["top"], "Bullish FVG 지지"

    pdd = premium_discount(df, lookback=60)
    return pdd["low"], min(pdd["mid"], cur), "Discount 되돌림"


def _pick_short_entry(df: pd.DataFrame, cur: float) -> tuple[float, float, str]:
    """숏 진입 구간: 현재가 위 가장 가까운 미체결 약세 구조 → 폴백은 Premium."""
    obs = find_order_blocks(df, lookback=80, min_move_pct=1.0)
    cands = [o for o in obs
             if o["type"] == "bear" and not o["mitigated"] and o["bottom"] >= cur * (1 - NEAR_ZONE_PCT)]
    if cands:
        ob = min(cands, key=lambda o: o["bottom"])        # 현재가에 가장 가까운(낮은) 저항 OB
        return ob["bottom"], ob["top"], "Bearish OB 저항"

    fvgs = [f for f in find_fvg(df, lookback=80, min_gap_pct=0.03)
            if f["type"] == "bear" and not f["filled"] and f["bottom"] >= cur * (1 - NEAR_ZONE_PCT)]
    if fvgs:
        f = min(fvgs, key=lambda f: f["bottom"])
        return f["bottom"], f["top"], "Bearish FVG 저항"

    pdd = premium_discount(df, lookback=60)
    return max(pdd["mid"], cur), pdd["high"], "Premium 되돌림"


def _long_targets(df: pd.DataFrame, cur: float, entry_ref: float) -> list[float]:
    """롱 목표: 위쪽 가장 가까운 스윙 고점 → Premium 고가 → 측정된 연장."""
    swings = find_swing_points(df.tail(80), lookback=5)
    highs = sorted(p for p in swings[swings["type"] == "H"]["price"].tolist() if p > cur)
    pdd = premium_discount(df, lookback=60)
    t1 = highs[0] if highs else pdd["high"]
    if t1 <= entry_ref:
        t1 = pdd["high"]
    t2 = pdd["high"] if pdd["high"] > t1 else t1 + (t1 - entry_ref)
    return [t1, t2]


def _short_targets(df: pd.DataFrame, cur: float, entry_ref: float) -> list[float]:
    """숏 목표: 아래쪽 가장 가까운 스윙 저점 → Discount 저가 → 측정된 연장."""
    swings = find_swing_points(df.tail(80), lookback=5)
    lows = sorted((p for p in swings[swings["type"] == "L"]["price"].tolist() if p < cur), reverse=True)
    pdd = premium_discount(df, lookback=60)
    t1 = lows[0] if lows else pdd["low"]
    if t1 >= entry_ref:
        t1 = pdd["low"]
    t2 = pdd["low"] if pdd["low"] < t1 else t1 - (entry_ref - t1)
    return [t1, t2]


def _confidence(magnitude: float, confluence: int) -> str:
    if magnitude >= CONF_HIGH_TH:
        return "high"
    if magnitude >= CONF_MED_TH:
        return "medium"
    return "low"


def _short_trend_ok(df: pd.DataFrame) -> tuple[bool, str]:
    """
    숏 레짐 게이트: 종목이 실제 하락 국면일 때만 True.
      현재가 < REGIME_MA 이평  AND  이평이 상승 중이 아님(기울기 <= 0)
    레짐을 확인할 데이터가 부족하면 **막는다**(보수적으로, 확인 안 되면 숏 보류).
    """
    close = df["Close"]
    if len(close) < REGIME_MA + REGIME_SLOPE_LOOKBACK:
        return False, "레짐 확인 불가 (데이터 부족) — 숏 보류"
    ma = close.rolling(REGIME_MA).mean()
    cur = float(close.iloc[-1])
    ma_now = float(ma.iloc[-1])
    ma_past = float(ma.iloc[-1 - REGIME_SLOPE_LOOKBACK])
    if cur < ma_now and ma_now <= ma_past:
        return True, ""
    return False, f"상위추세 상승/횡보 — 숏 보류 (현재 {cur:.2f} vs MA{REGIME_MA} {ma_now:.2f})"


def build_trade_plan(df: pd.DataFrame, *, min_rr: float = DEFAULT_MIN_RR,
                     short_trend_filter: bool = True) -> dict:
    """
    ICT 구조 기반 양방향 트레이드 플랜.

    반환 dict
      direction        "long" | "short" | "none"
      bias_score       ICT 순 조정점수 (-30~+30)
      confidence       "high" | "medium" | "low"
      confluence       방향에 동의한 구조 신호 개수
      current          현재가
      entry            {"low","high","ref"}  진입 구간
      stop             손절가
      targets          [T1, T2]
      rr               [T1 R:R, T2 R:R]  (계산 불가면 None)
      valid            매매 가능한 셋업인지 (정렬 OK + T1 R:R >= min_rr)
      reason_invalid   valid=False 사유
      signals          근거(사람이 읽는 문장) 목록
    """
    empty = {
        "direction": "none", "bias_score": 0, "confidence": "low", "confluence": 0,
        "current": float(df["Close"].iloc[-1]) if len(df) else 0.0,
        "entry": {"low": 0.0, "high": 0.0, "ref": 0.0},
        "stop": 0.0, "targets": [], "rr": [], "valid": False,
        "reason_invalid": "데이터 부족", "signals": [],
    }
    if df is None or df.empty or len(df) < MIN_BARS:
        return empty

    try:
        cur = float(df["Close"].iloc[-1])
        adj_info = calc_ict_adjustment(df)
        adj = int(adj_info.get("adjustment", 0))
        signals = list(adj_info.get("signals", []))

        if adj >= BIAS_TH:
            direction = "long"
        elif adj <= -BIAS_TH:
            direction = "short"
        else:
            out = dict(empty)
            out.update({"current": round(cur, 2), "bias_score": adj, "signals": signals,
                        "reason_invalid": f"뚜렷한 방향성 없음 (ICT {adj:+d}, |{adj}|<{BIAS_TH})"})
            return out

        def _short_veto(reason: str) -> dict:
            out = dict(empty)
            out.update({"direction": "short", "current": round(cur, 2),
                        "bias_score": adj, "signals": signals,
                        "confidence": _confidence(abs(adj), len(signals)),
                        "confluence": len(signals), "reason_invalid": reason})
            return out

        # 저확신 숏 억제 — low 확신도 숏은 기대값이 낮다 (실측 +0.16R). 숏은
        # medium 이상(|ICT| >= CONF_MED_TH)만 허용한다. 롱은 제한 없음.
        if direction == "short" and abs(adj) < CONF_MED_TH:
            return _short_veto(f"저확신 숏 억제 (|ICT| {abs(adj)} < {CONF_MED_TH})")

        # 숏 레짐 게이트 — 종목이 약세가 아니면 숏 보류 (라인은 계산하지 않음)
        if direction == "short" and short_trend_filter:
            ok, why = _short_trend_ok(df)
            if not ok:
                return _short_veto(why)

        atr = _atr(df)
        if direction == "long":
            entry_low, entry_high, struct = _pick_long_entry(df, cur)
            stop = entry_low - ATR_STOP_BUFFER * atr
            targets = _long_targets(df, cur, (entry_low + entry_high) / 2.0)
        else:
            entry_low, entry_high, struct = _pick_short_entry(df, cur)
            stop = entry_high + ATR_STOP_BUFFER * atr
            targets = _short_targets(df, cur, (entry_low + entry_high) / 2.0)

        plan = _assemble_plan(direction, cur, entry_low, entry_high, stop, targets, min_rr=min_rr)
        plan["bias_score"] = adj
        plan["confidence"] = _confidence(abs(adj), len(signals))
        plan["confluence"] = len(signals)
        plan["signals"] = [f"진입 근거: {struct}"] + signals
        return plan

    except Exception as e:  # 한 종목이 죽어도 스캔 전체를 멈추지 않는다
        out = dict(empty)
        out["reason_invalid"] = f"트레이드 플랜 오류: {e}"
        return out
