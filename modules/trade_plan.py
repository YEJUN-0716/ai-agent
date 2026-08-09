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
# T1 손익비가 이 미만이면 유효 셋업이 아니다 — 화면에도 텔레그램에도 추천으로
# 나가지 않는다. 1:2 는 지시받은 기준선이다(2026-08-05): 이기는 판에서 잃는 판의
# 두 배를 벌지 못하면, 승률이 반이어도 남는 게 없다. 이 숫자를 올리면 셋업 수가
# 줄고 셋업당 기대값이 오른다 — 바꿀 때마다 scripts/measure_trade_plan.py 로 잰다.
DEFAULT_MIN_RR = 2.0
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

    grade, risk_pct = cost_grade(entry_ref, stop)
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
        # 실행 등급 — 이 계획이 수수료·슬리피지를 얼마나 견디나. confidence 와
        # 달리 **결과와 연결된 것이 측정된** 유일한 등급이다.
        "cost_grade": grade,
        "risk_pct": round(risk_pct, 2),
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


def _confidence(magnitude: float) -> str:
    """ICT 구조가 방향에 동의한 정도. **결과를 예측하지 않는다.**

    이름이 '확신도' 라 "맞을 확률" 로 읽히지만 그런 뜻이 아니다. 2026-08-10
    측정에서 롱만 놓고 보면 등급별 기대값이 구별되지 않았다:

        high   +0.601R  [+0.456, +0.740]      (날짜 블록 부트스트랩 95%)
        medium +0.643R  [+0.570, +0.712]
        low    +0.656R  [+0.564, +0.756]
        low - high = +0.058R  95% [-0.113, +0.229]  ← 0 을 포함

    한동안 "확신도가 거꾸로다" 로 보였던 건 롱/숏 구성비 때문이었다 — low 는
    저확신 숏 억제 규칙 탓에 100% 롱이고 high 는 34% 가 숏이다. 숏이 나쁘니
    숏 비중이 높은 쪽이 나빠 보였을 뿐이다.

    그래서 이 값은 **구조 설명**으로만 쓴다. 실행 가치를 가르는 등급은
    cost_grade 가 낸다.

    (예전 시그니처는 confluence 도 받았지만 한 번도 쓰지 않았다.)
    """
    if magnitude >= CONF_HIGH_TH:
        return "high"
    if magnitude >= CONF_MED_TH:
        return "medium"
    return "low"


# 손절이 진입가에서 몇 % 떨어져 있나 — 실행 등급의 유일한 재료.
#
# 2026-08-10 거래비용 측정(13,336건)에서 위험폭 4분위의 **총 기대값은 거의
# 같았다**(+0.54~+0.60R). 다른 건 비용 내성뿐이었다:
#
#     Q4 손절 >2.34%   손익분기 편도 88.8bp
#     Q3     >1.75%              53.3bp
#     Q2     >1.34%              41.7bp
#     Q1     <=1.34%             29.6bp   ← 실제 비용 구간(편도 10~35bp) 안
#
# 결과를 보고 고르는 게 아니라 기하를 보고 고르는 등급이라, 흔한 백테스트
# 필터보다 데이터마이닝 위험이 낮다. 경계를 옮기려면 측정을 다시 할 것.
COST_GRADE_BANDS = ((2.34, "A"), (1.75, "B"), (1.34, "C"))
COST_GRADE_BREAKEVEN_BP = {"A": 88.8, "B": 53.3, "C": 41.7, "D": 29.6}


def cost_grade(entry_ref: float, stop: float) -> tuple[str, float]:
    """(등급, 위험폭 %) — 손절이 멀수록 수수료·슬리피지를 잘 견딘다."""
    if entry_ref <= 0:
        return "D", 0.0
    risk_pct = abs(entry_ref - stop) / entry_ref * 100.0
    for threshold, grade in COST_GRADE_BANDS:
        if risk_pct > threshold:
            return grade, risk_pct
    return "D", risk_pct


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
                     short_trend_filter: bool = True,
                     direction: str | None = None) -> dict:
    """
    ICT 구조 기반 양방향 트레이드 플랜.

    direction
      None(기본)이면 ICT 순점수의 부호로 방향을 정한다.
      "long" | "short" 를 주면 **방향만** 그것으로 고정한다 — 방향의 주인이
      바깥(총괄 판정)일 때 쓴다. 게이트(숏 레짐·저확신 숏 억제·손익비 하한)는
      그대로 적용된다. 방향을 넘겨받는 것이지 "무조건 그린다"는 뜻이 아니다.

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
    if direction not in (None, "long", "short"):
        raise ValueError(f"direction 은 'long' | 'short' | None 만 됩니다: {direction!r}")

    empty = {
        "direction": "none", "bias_score": 0, "confidence": "low", "confluence": 0,
        "current": float(df["Close"].iloc[-1]) if len(df) else 0.0,
        "entry": {"low": 0.0, "high": 0.0, "ref": 0.0},
        "stop": 0.0, "targets": [], "rr": [], "valid": False,
        "reason_invalid": "데이터 부족", "signals": [],
        # 계획이 없으면 등급도 없다. 빠뜨리면 화면이 KeyError 로 죽는다 —
        # 유효 계획과 무효 계획의 키 모양은 같아야 한다.
        "cost_grade": "D", "risk_pct": 0.0,
    }
    if df is None or df.empty or len(df) < MIN_BARS:
        return empty

    try:
        cur = float(df["Close"].iloc[-1])
        adj_info = calc_ict_adjustment(df)
        adj = int(adj_info.get("adjustment", 0))
        signals = list(adj_info.get("signals", []))

        if direction is None:
            if adj >= BIAS_TH:
                direction = "long"
            elif adj <= -BIAS_TH:
                direction = "short"
            else:
                out = dict(empty)
                out.update({"current": round(cur, 2), "bias_score": adj, "signals": signals,
                            "reason_invalid": f"뚜렷한 방향성 없음 (ICT {adj:+d}, |{adj}|<{BIAS_TH})"})
                return out

        # 구조가 방향에 **동의한** 정도. 반대 부호는 확신의 근거가 될 수 없으므로
        # 0 으로 깎는다. 방향을 ICT 가 정한 경우엔 이 값이 |adj| 와 같아서 아무것도
        # 달라지지 않고, 방향을 주입받은 경우에만 의미가 생긴다.
        conf_mag = max(adj if direction == "long" else -adj, 0)
        if conf_mag == 0 and abs(adj) >= BIAS_TH:
            signals = [f"⚠️ ICT 구조는 반대 방향 ({adj:+d}) — 방향은 바깥 판정을 따름"] + signals

        def _short_veto(reason: str) -> dict:
            out = dict(empty)
            out.update({"direction": "short", "current": round(cur, 2),
                        "bias_score": adj, "signals": signals,
                        "confidence": _confidence(conf_mag),
                        "confluence": len(signals), "reason_invalid": reason})
            return out

        # 저확신 숏 억제 — low 확신도 숏은 기대값이 낮다 (실측 +0.16R). 숏은
        # medium 이상(약세 동의 >= CONF_MED_TH)만 허용한다. 롱은 제한 없음.
        if direction == "short" and conf_mag < CONF_MED_TH:
            return _short_veto(f"저확신 숏 억제 (약세 구조 동의 {conf_mag} < {CONF_MED_TH})")

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
        plan["confidence"] = _confidence(conf_mag)
        plan["confluence"] = len(signals)
        plan["signals"] = [f"진입 근거: {struct}"] + signals
        return plan

    except Exception as e:  # 한 종목이 죽어도 스캔 전체를 멈추지 않는다
        out = dict(empty)
        out["reason_invalid"] = f"트레이드 플랜 오류: {e}"
        return out
