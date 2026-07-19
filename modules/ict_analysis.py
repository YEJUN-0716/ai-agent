"""
ICT (Inner Circle Trader) 분석 모듈
=====================================
Fair Value Gap / Order Block / BOS·CHoCH / Premium·Discount
퀀트 팩터 점수 + Plotly 시각화 제공
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go


# ── 스윙 고점·저점 ──────────────────────────────────────────────
def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """lookback개 캔들 양쪽 기준으로 스윙 고점(H)·저점(L) 감지."""
    highs = df["High"].values
    lows  = df["Low"].values
    n = len(df)
    records = []
    for i in range(lookback, n - lookback):
        if highs[i] > highs[i - lookback:i].max() and highs[i] > highs[i + 1:i + lookback + 1].max():
            records.append({"idx": i, "date": df.index[i], "price": highs[i], "type": "H"})
        if lows[i] < lows[i - lookback:i].min() and lows[i] < lows[i + 1:i + lookback + 1].min():
            records.append({"idx": i, "date": df.index[i], "price": lows[i], "type": "L"})
    return pd.DataFrame(records) if records else pd.DataFrame(columns=["idx", "date", "price", "type"])


# ── Fair Value Gap ──────────────────────────────────────────────
def find_fvg(df: pd.DataFrame, lookback: int = 100, min_gap_pct: float = 0.05) -> list:
    """
    FVG(가격 불균형) 감지.
    Bullish FVG: candle[i-2].high < candle[i].low
    Bearish FVG: candle[i-2].low  > candle[i].high
    """
    sub = df.tail(lookback).reset_index()
    date_col = sub.columns[0]   # reset_index 후 원래 인덱스 컬럼명 (Date/Datetime/index)
    cur = float(sub["Close"].iloc[-1])
    fvgs = []
    for i in range(2, len(sub)):
        h2, l2 = float(sub["High"].iloc[i - 2]), float(sub["Low"].iloc[i - 2])
        li, hi = float(sub["Low"].iloc[i]),      float(sub["High"].iloc[i])

        if h2 < li:                              # Bullish FVG
            gap_pct = (li - h2) / cur * 100
            if gap_pct >= min_gap_pct:
                filled = float(sub["Low"].iloc[i:].min()) <= h2
                fvgs.append({"type": "bull", "top": li, "bottom": h2,
                              "date": sub[date_col].iloc[i - 1],
                              "filled": filled, "gap_pct": round(gap_pct, 2)})

        elif l2 > hi:                            # Bearish FVG
            gap_pct = (l2 - hi) / cur * 100
            if gap_pct >= min_gap_pct:
                filled = float(sub["High"].iloc[i:].max()) >= l2
                fvgs.append({"type": "bear", "top": l2, "bottom": hi,
                              "date": sub[date_col].iloc[i - 1],
                              "filled": filled, "gap_pct": round(gap_pct, 2)})
    return fvgs


# ── Order Block ──────────────────────────────────────────────────
def find_order_blocks(df: pd.DataFrame, lookback: int = 100, min_move_pct: float = 1.0) -> list:
    """
    Order Block 감지.
    Bullish OB: 상승 임펄스 직전 마지막 하락 캔들
    Bearish OB: 하락 임펄스 직전 마지막 상승 캔들
    """
    sub = df.tail(lookback).reset_index()
    date_col = sub.columns[0]   # reset_index 후 원래 인덱스 컬럼명 (Date/Datetime/index)
    obs = []
    for i in range(1, len(sub) - 1):
        move = (float(sub["Close"].iloc[i + 1]) / float(sub["Close"].iloc[i]) - 1) * 100
        is_bear_candle = sub["Close"].iloc[i] < sub["Open"].iloc[i]
        is_bull_candle = sub["Close"].iloc[i] > sub["Open"].iloc[i]

        if is_bear_candle and move >= min_move_pct:          # Bullish OB
            mitigated = float(sub["Low"].iloc[i + 1:].min()) <= float(sub["Low"].iloc[i])
            obs.append({"type": "bull",
                        "top": float(sub["High"].iloc[i]), "bottom": float(sub["Low"].iloc[i]),
                        "date": sub[date_col].iloc[i], "mitigated": mitigated})
        elif is_bull_candle and move <= -min_move_pct:       # Bearish OB
            mitigated = float(sub["High"].iloc[i + 1:].max()) >= float(sub["High"].iloc[i])
            obs.append({"type": "bear",
                        "top": float(sub["High"].iloc[i]), "bottom": float(sub["Low"].iloc[i]),
                        "date": sub[date_col].iloc[i], "mitigated": mitigated})
    return obs


# ── BOS / CHoCH ──────────────────────────────────────────────────
def find_bos_choch(df: pd.DataFrame, swings: pd.DataFrame) -> list:
    """시장 구조 이탈(BOS) 및 성격 변화(CHoCH) 감지."""
    if swings.empty or len(swings) < 2:
        return []
    closes = df["Close"].values
    dates  = df.index
    events = []
    swing_list = swings.sort_values("idx").to_dict("records")

    for j in range(1, len(swing_list)):
        prev, curr = swing_list[j - 1], swing_list[j]
        after = curr["idx"] + 1
        if after >= len(closes):
            continue

        if prev["type"] == "H":
            for k in range(after, len(closes)):
                if closes[k] > prev["price"]:
                    label = "BOS_bull" if curr["type"] == "L" else "CHoCH_bull"
                    events.append({"type": label, "price": prev["price"],
                                   "date": dates[k], "idx": k})
                    break
        elif prev["type"] == "L":
            for k in range(after, len(closes)):
                if closes[k] < prev["price"]:
                    label = "BOS_bear" if curr["type"] == "H" else "CHoCH_bear"
                    events.append({"type": label, "price": prev["price"],
                                   "date": dates[k], "idx": k})
                    break
    return events


# ── Premium / Discount ──────────────────────────────────────────
def premium_discount(df: pd.DataFrame, lookback: int = 60) -> dict:
    """최근 lookback일 고·저가 기준 50% 균형선 및 현재 구간 반환."""
    sub  = df.tail(lookback)
    high = float(sub["High"].max())
    low  = float(sub["Low"].min())
    mid  = (high + low) / 2
    cur  = float(df["Close"].iloc[-1])
    pos  = (cur - low) / (high - low) * 100 if high > low else 50.0
    return {"high": high, "low": low, "mid": mid, "current": cur,
            "zone": "premium" if cur > mid else "discount",
            "position_pct": round(pos, 1)}


# ── 퀀트 팩터 점수 (0~100) ────────────────────────────────────────
def ict_factor_score(df: pd.DataFrame) -> float:
    """
    ICT 구조 기반 팩터 점수 (0~100).
    - 미충족 강세 FVG가 현재가 아래에 많을수록 점수 상승 (지지 구조)
    - 최근 BOS가 상승 방향이면 점수 상승
    - 디스카운트 구간에 있으면 점수 상승 (매력적인 진입가)
    """
    if df.empty or len(df) < 30:
        return 50.0

    score = 50.0
    cur = float(df["Close"].iloc[-1])

    try:
        fvgs = find_fvg(df, lookback=80, min_gap_pct=0.05)
        bull_below = sum(1 for f in fvgs if f["type"] == "bull" and not f["filled"] and f["top"] < cur)
        bear_above = sum(1 for f in fvgs if f["type"] == "bear" and not f["filled"] and f["bottom"] > cur)
        score += float(np.clip((bull_below - bear_above) * 6, -20, 20))
    except Exception:
        pass

    try:
        swings = find_swing_points(df, lookback=5)
        events = find_bos_choch(df, swings)
        if events:
            last = events[-1]["type"]
            score += 15.0 if "bull" in last else -15.0
    except Exception:
        pass

    try:
        pd_info = premium_discount(df, lookback=60)
        score += (50 - pd_info["position_pct"]) * 0.2
    except Exception:
        pass

    return float(np.clip(score, 10, 90))


# ── Plotly 차트 ──────────────────────────────────────────────────
_TV_BG    = "#131722"
_TV_PAPER = "#131722"
_TV_GRID  = "#1e2530"
_TV_TEXT  = "#9598a1"


def plot_ict_chart(df: pd.DataFrame, n_candles: int = 80, ticker: str = "") -> go.Figure:
    """ICT 오버레이 캔들스틱 차트 반환."""
    sub = df.tail(n_candles).copy()
    fig = go.Figure()

    # ── 캔들스틱 ──────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=sub.index,
        open=sub["Open"], high=sub["High"],
        low=sub["Low"],   close=sub["Close"],
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        name="Price",
    ))

    # ── FVG 박스 (미충족만) ───────────────────────────────────
    fvgs = find_fvg(df, lookback=n_candles + 20, min_gap_pct=0.05)
    for fvg in fvgs:
        if fvg["filled"]:
            continue
        if fvg["type"] == "bull":
            fc, lc, lbl = "rgba(38,166,154,0.15)", "#26a69a", f"FVG↑ {fvg['gap_pct']:.2f}%"
        else:
            fc, lc, lbl = "rgba(239,83,80,0.15)",  "#ef5350", f"FVG↓ {fvg['gap_pct']:.2f}%"
        fig.add_hrect(
            y0=fvg["bottom"], y1=fvg["top"],
            fillcolor=fc, line_color=lc, line_width=1,
            annotation_text=lbl,
            annotation_position="right",
            annotation_font=dict(size=9, color=lc),
        )

    # ── Order Block 박스 (미충족 최근 5개) ───────────────────
    obs = find_order_blocks(df, lookback=n_candles + 20, min_move_pct=1.0)
    for ob in [o for o in obs if not o["mitigated"]][-5:]:
        if ob["type"] == "bull":
            fc, lc, lbl = "rgba(33,150,243,0.18)", "#2196F3", "Bull OB"
        else:
            fc, lc, lbl = "rgba(255,152,0,0.18)",  "#FF9800", "Bear OB"
        fig.add_hrect(
            y0=ob["bottom"], y1=ob["top"],
            fillcolor=fc, line_color=lc, line_width=1, line_dash="dot",
            annotation_text=lbl,
            annotation_position="left",
            annotation_font=dict(size=9, color=lc),
        )

    # ── 스윙 고점·저점 마커 ────────────────────────────────────
    swings = find_swing_points(sub, lookback=5)
    if not swings.empty:
        for sw_type, sym, color, pos in [
            ("H", "triangle-down", "#ef5350", "top center"),
            ("L", "triangle-up",   "#26a69a", "bottom center"),
        ]:
            sw = swings[swings["type"] == sw_type]
            if not sw.empty:
                label = "SH" if sw_type == "H" else "SL"
                fig.add_trace(go.Scatter(
                    x=sw["date"], y=sw["price"],
                    mode="markers+text",
                    marker=dict(symbol=sym, size=9, color=color),
                    text=[label] * len(sw), textposition=pos,
                    textfont=dict(size=8, color=color),
                    name=label, showlegend=False,
                ))

        # ── BOS / CHoCH 어노테이션 ─────────────────────────
        events = find_bos_choch(sub, swings)
        for ev in events[-5:]:
            is_bull = "bull" in ev["type"]
            lbl = ev["type"].replace("_", " ").upper()
            col = "#26a69a" if is_bull else "#ef5350"
            fig.add_annotation(
                x=ev["date"], y=ev["price"],
                text=lbl, showarrow=True, arrowhead=2,
                arrowcolor=col, font=dict(size=9, color=col),
                bgcolor="rgba(0,0,0,0.55)", bordercolor=col,
                ay=-30 if is_bull else 30,
            )

    # ── 프리미엄·디스카운트 중간선 ────────────────────────────
    pd_info = premium_discount(sub, lookback=min(60, len(sub)))
    fig.add_hline(
        y=pd_info["mid"], line_dash="dash", line_color="#9c27b0", line_width=1,
        annotation_text=(f"EQ {pd_info['mid']:.2f}  "
                         f"({pd_info['zone'].upper()} {pd_info['position_pct']:.0f}%)"),
        annotation_font=dict(color="#9c27b0", size=9),
        annotation_position="right",
    )

    fig.update_layout(
        title=dict(text=f"{ticker}  ICT 분석", font=dict(size=13, color=_TV_TEXT)),
        height=500,
        plot_bgcolor=_TV_BG, paper_bgcolor=_TV_PAPER,
        xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor=_TV_GRID, showgrid=True, color=_TV_TEXT),
        yaxis=dict(gridcolor=_TV_GRID, showgrid=True, color=_TV_TEXT),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=_TV_TEXT)),
        margin=dict(l=0, r=160, t=40, b=0),
    )
    return fig


# ── CRT (Candle Range Theory) ───────────────────────────────────
def detect_crt_setup(df: pd.DataFrame, period: int = 3) -> dict:
    """
    CRT Phase 2 감지 (매수·매도 진입 단계).
    최근 period 캔들을 HTF CRT Range(ERL High / ERL Low)로 설정,
    오늘(마감 후 마지막) 캔들이 ERL을 스윕 후 Range 안으로 되돌아왔는지 확인.

    Phase 2 Bullish: 오늘 캔들 저가 < CRT Low AND 종가 > CRT Low
                     → 내일 IRL(Range 내부)로 복귀 후 반대 ERL(High) 전달 예상
    Phase 2 Bearish: 오늘 캔들 고가 > CRT High AND 종가 < CRT High
                     → 내일 IRL로 복귀 후 반대 ERL(Low) 전달 예상

    반환:
      setup       "bullish" | "bearish" | None
      crt_high    float — CRT Range 고가(ERL High)
      crt_low     float — CRT Range 저가(ERL Low)
      crt_mid     float — Range 중간(IRL 기준)
      swept_erl   float | None — 스윕된 ERL 가격
      phase       2 | None
    """
    result = {"setup": None, "crt_high": 0.0, "crt_low": 0.0,
              "crt_mid": 0.0, "swept_erl": None, "phase": None}

    if len(df) < period + 2:
        return result

    # 기준 Range: 오늘 제외 직전 period 캔들
    ref      = df.iloc[-(period + 1):-1]
    crt_high = float(ref["High"].max())
    crt_low  = float(ref["Low"].min())
    result["crt_high"] = crt_high
    result["crt_low"]  = crt_low
    result["crt_mid"]  = (crt_high + crt_low) / 2

    today = df.iloc[-1]
    t_low, t_high, t_close = float(today["Low"]), float(today["High"]), float(today["Close"])

    crt_mid = result["crt_mid"]
    if t_low < crt_low and crt_mid < t_close < crt_high:
        result.update({"setup": "bullish", "swept_erl": crt_low, "phase": 2})
    elif t_high > crt_high and crt_low < t_close < crt_mid:
        result.update({"setup": "bearish", "swept_erl": crt_high, "phase": 2})

    return result


# ── 자동매매용 ICT 진입 품질 조정 점수 ──────────────────────────────
def calc_ict_adjustment(df: pd.DataFrame) -> dict:
    """
    팩터 composite 점수에 가산·감산할 ICT/CRT 기반 조정값 반환.
    범위: -30 ~ +30

    가산 (불리시 신호):
      +20  CRT Phase2 Bullish  — ERL Low 스윕 후 반전 (내일 상승 전달 예상)
      +15  Bullish FVG 내 현재가 — 기관 미체결 지지 구간
      +10  Bullish OB 근처     — 기관 매수 포지션 구간
      +10  최근 BOS/CHoCH Bullish — 시장 구조 전환
      +5   Discount 구간 하단  — 저평가 진입가

    감산 (베어리시 신호):
      -20  CRT Phase2 Bearish  — ERL High 스윕 후 반전 (내일 하락 전달 예상)
      -15  Bearish FVG 내 현재가 — 기관 미체결 저항 구간
      -10  Bearish OB 근처     — 기관 매도 포지션 구간
      -10  최근 BOS/CHoCH Bearish — 시장 구조 하락 전환
      -8   Premium 구간 상단   — 과도 연장, 추격 위험

    반환: {"adjustment": int, "signals": list[str], "crt": dict}
    """
    if df.empty or len(df) < 60:
        return {"adjustment": 0, "signals": [], "crt": {}}

    try:
        cur     = float(df["Close"].iloc[-1])
        adj     = 0
        signals = []

        # 1. CRT Phase 2
        crt = detect_crt_setup(df)
        if crt["setup"] == "bullish":
            adj += 20
            signals.append(f"CRT Bullish Phase2 (${crt['swept_erl']:.2f} ERL 스윕→반전)")
        elif crt["setup"] == "bearish":
            adj -= 20
            signals.append(f"CRT Bearish Phase2 (${crt['swept_erl']:.2f} ERL 스윕→반전)")

        # 2. FVG — 미충족 갭과 현재가 위치
        fvgs = find_fvg(df, lookback=60, min_gap_pct=0.03)
        for f in fvgs:
            if f["filled"]:
                continue
            if f["type"] == "bull" and f["bottom"] <= cur <= f["top"]:
                adj += 15
                signals.append(f"Bullish FVG 지지 (${f['bottom']:.2f}~${f['top']:.2f})")
                break
        for f in fvgs:
            if f["filled"]:
                continue
            if f["type"] == "bear" and f["bottom"] <= cur <= f["top"]:
                adj -= 15
                signals.append(f"Bearish FVG 저항 (${f['bottom']:.2f}~${f['top']:.2f})")
                break

        # 3. Order Block — 미충족 OB와 현재가 위치 (유형별 첫 번째 OB만 반영)
        obs = find_order_blocks(df, lookback=60, min_move_pct=1.0)
        unmitigated = [o for o in obs if not o["mitigated"]]
        for ob in unmitigated:
            zone_l = ob["bottom"] * 0.98
            zone_h = ob["top"]    * 1.02
            if ob["type"] == "bull" and zone_l <= cur <= zone_h:
                adj += 10
                signals.append(f"Bullish OB 지지 (${ob['bottom']:.2f}~${ob['top']:.2f})")
                break
        for ob in unmitigated:
            zone_l = ob["bottom"] * 0.98
            zone_h = ob["top"]    * 1.02
            if ob["type"] == "bear" and zone_l <= cur <= zone_h:
                adj -= 10
                signals.append(f"Bearish OB 저항 (${ob['bottom']:.2f}~${ob['top']:.2f})")
                break

        # 4. BOS / CHoCH 최근 방향
        swings = find_swing_points(df.tail(80), lookback=5)
        events = find_bos_choch(df.tail(80), swings)
        if events:
            last = events[-1]["type"]
            if "bull" in last:
                adj += 10
                signals.append(f"최근 {last.upper()} (불리시 구조 전환)")
            elif "bear" in last:
                adj -= 10
                signals.append(f"최근 {last.upper()} (베어리시 구조 전환)")

        # 5. Premium / Discount
        pd_info = premium_discount(df, lookback=60)
        if pd_info["zone"] == "discount" and pd_info["position_pct"] < 30:
            adj += 5
            signals.append(f"Discount 하단 ({pd_info['position_pct']:.0f}%) — 저평가 진입")
        elif pd_info["zone"] == "premium" and pd_info["position_pct"] > 80:
            adj -= 8
            signals.append(f"Premium 상단 ({pd_info['position_pct']:.0f}%) — 추격 위험")

        return {
            "adjustment": max(-30, min(30, adj)),
            "signals":    signals,
            "crt":        crt,
        }

    except Exception as e:
        return {"adjustment": 0, "signals": [f"ICT 오류: {e}"], "crt": {}}


# ── 빗각채널 (Pitched / Diagonal Channel) ───────────────────────
def find_trend_channel(df: pd.DataFrame, lookback: int = 80, swing_lookback: int = 5) -> dict | None:
    """
    스윙 고·저점 기반 자동 평행채널 감지.
    고점/저점 각각 회귀선 → 평균 기울기로 평행 강제.
    반환: upper/lower/mid 라인 좌표 + direction/width_pct/position_pct/zone
    """
    if len(df) < max(lookback // 2, swing_lookback * 3):
        return None

    sub = df.tail(lookback)
    swings = find_swing_points(sub, lookback=swing_lookback)

    if swings.empty:
        return None

    highs = swings[swings["type"] == "H"]
    lows  = swings[swings["type"] == "L"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    h_slope, _ = np.polyfit(highs["idx"].values, highs["price"].values, 1)
    l_slope, _ = np.polyfit(lows["idx"].values,  lows["price"].values,  1)
    slope = (h_slope + l_slope) / 2

    # 평행 채널: 기울기 고정 후 스윙 포인트를 감싸도록 상하 shift
    x_all  = np.arange(len(sub))
    h_off  = float(np.max(highs["price"].values - slope * highs["idx"].values))
    l_off  = float(np.min(lows["price"].values  - slope * lows["idx"].values))

    upper_y = slope * x_all + h_off
    lower_y = slope * x_all + l_off
    mid_y   = (upper_y + lower_y) / 2

    dates  = list(sub.index)
    cur    = float(sub["Close"].iloc[-1])
    width  = upper_y[-1] - lower_y[-1]
    pos    = (cur - lower_y[-1]) / width * 100 if width > 0 else 50.0
    slope_norm = slope / (float(sub["Close"].mean()) + 1e-9) * 100  # 일별 % 기울기

    return {
        "slope":        float(slope),
        "direction":    "bullish" if slope_norm > 0.05 else "bearish" if slope_norm < -0.05 else "sideways",
        "width_pct":    round(width / lower_y[-1] * 100, 2) if lower_y[-1] > 0 else 0.0,
        "position_pct": round(pos, 1),
        "zone":         "상단" if pos > 66 else "하단" if pos < 33 else "중간",
        "upper":        {"x": dates, "y": upper_y.tolist()},
        "lower":        {"x": dates, "y": lower_y.tolist()},
        "mid":          {"x": dates, "y": mid_y.tolist()},
    }


def plot_channel_chart(df: pd.DataFrame, n_candles: int = 80,
                       swing_lookback: int = 5, ticker: str = "") -> go.Figure:
    """빗각채널 오버레이 캔들스틱 차트 반환."""
    sub = df.tail(n_candles).copy()
    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=sub.index,
        open=sub["Open"], high=sub["High"],
        low=sub["Low"],   close=sub["Close"],
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        name="Price",
    ))

    ch = find_trend_channel(df, lookback=n_candles, swing_lookback=swing_lookback)
    if ch:
        dir_ko  = {"bullish": "상승", "bearish": "하락", "sideways": "횡보"}[ch["direction"]]
        dir_col = {"bullish": "#26a69a", "bearish": "#ef5350", "sideways": "#ff9800"}[ch["direction"]]
        fill_rgba = (
            "rgba(38,166,154,0.06)"  if ch["direction"] == "bullish" else
            "rgba(239,83,80,0.06)"   if ch["direction"] == "bearish" else
            "rgba(255,152,0,0.06)"
        )

        # 하단선 (투명 기준)
        fig.add_trace(go.Scatter(
            x=ch["lower"]["x"], y=ch["lower"]["y"],
            mode="lines", line=dict(color=dir_col, width=1.5, dash="dash"),
            name="채널 하단", showlegend=True,
        ))
        # 상단선 (fill to lower)
        fig.add_trace(go.Scatter(
            x=ch["upper"]["x"], y=ch["upper"]["y"],
            mode="lines", line=dict(color=dir_col, width=1.5, dash="dash"),
            fill="tonexty", fillcolor=fill_rgba,
            name=f"채널 상단 ({dir_ko})", showlegend=True,
        ))
        # 중간선
        fig.add_trace(go.Scatter(
            x=ch["mid"]["x"], y=ch["mid"]["y"],
            mode="lines", line=dict(color=dir_col, width=1, dash="dot"),
            name="채널 중간선", showlegend=False,
        ))

        # 스윙 마커
        swings = find_swing_points(sub, lookback=swing_lookback)
        if not swings.empty:
            for sw_type, sym, col, pos in [
                ("H", "triangle-down", "#ef5350", "top center"),
                ("L", "triangle-up",   "#26a69a", "bottom center"),
            ]:
                sw = swings[swings["type"] == sw_type]
                if not sw.empty:
                    fig.add_trace(go.Scatter(
                        x=sw["date"], y=sw["price"],
                        mode="markers",
                        marker=dict(symbol=sym, size=8, color=col),
                        name="SH" if sw_type == "H" else "SL",
                        showlegend=False,
                    ))

        # 현재 위치 어노테이션
        last_x = ch["upper"]["x"][-1]
        fig.add_annotation(
            x=last_x, y=ch["upper"]["y"][-1],
            text="상단", showarrow=False,
            font=dict(size=9, color=dir_col), xanchor="left", xshift=5,
        )
        fig.add_annotation(
            x=last_x, y=ch["lower"]["y"][-1],
            text="하단", showarrow=False,
            font=dict(size=9, color=dir_col), xanchor="left", xshift=5,
        )

    fig.update_layout(
        title=dict(text=f"{ticker}  빗각채널", font=dict(size=13, color=_TV_TEXT)),
        height=480,
        plot_bgcolor=_TV_BG, paper_bgcolor=_TV_PAPER,
        xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor=_TV_GRID, showgrid=True, color=_TV_TEXT),
        yaxis=dict(gridcolor=_TV_GRID, showgrid=True, color=_TV_TEXT),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=_TV_TEXT)),
        margin=dict(l=0, r=80, t=40, b=0),
    )
    return fig
