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
    """시장 구조 이탈(BOS) 및 성격 변화(CHoCH) 감지 — **깨진 시각순**.

    소비자 셋(`ict_factor_score` · `calc_ict_adjustment` · 차트 주석)이 전부
    `events[-1]` 을 "가장 최근 구조 전환" 으로 읽는다. 그런데 이 함수는 스윙
    **쌍 순서**로 훑으므로, 오래된 스윙이 뒤늦게 깨지면 그 이벤트가 목록 뒤로
    가지 않는다 — 나중 쌍의 돌파가 먼저 일어났어도 목록에서는 뒤에 온다.
    그래서 마지막에 한 번 세워서 돌려준다. 소비자마다 세우면 언젠가 한 곳이
    안 따라온다.

    (실측 2026-08-22, 저장 패널 20종목 × 30봉: 정렬 전 `events[-1]` 이 최신
    이벤트와 다른 봉이 10.3%, 그중 **방향이 정반대인 봉이 4.67%** 였다.
    그 봉에서 ICT 애널리스트 점수가 평균 49.4점 움직이고, 실기록 3,032건
    기준 그중 15.8% 는 총괄 판정 라벨까지 뒤집힌다.)
    """
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
    return sorted(events, key=lambda e: e["idx"])


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


def overlay_trade_plan(fig: go.Figure, plan: dict | None) -> go.Figure:
    """트레이드 플랜(진입 구간·손절·목표) 라인을 기존 차트 fig 에 얹는다.

    plan 은 modules.trade_plan.build_trade_plan 의 반환 dict. 방향이 없거나
    진입가가 비어 있으면 아무것도 그리지 않는다.
    """
    if not plan or plan.get("direction") not in ("long", "short"):
        return fig
    entry = plan.get("entry") or {}
    if not entry.get("ref"):
        return fig

    dir_ko = "롱" if plan["direction"] == "long" else "숏"
    fig.add_hrect(
        y0=entry["low"], y1=entry["high"],
        fillcolor="rgba(120,144,156,0.18)", line_color="#90a4ae", line_width=1,
        annotation_text=f"진입 {dir_ko} {entry['low']:.2f}~{entry['high']:.2f}",
        annotation_position="left", annotation_font=dict(size=9, color="#b0bec5"),
    )
    fig.add_hline(
        y=plan["stop"], line_dash="dash", line_color="#ef5350", line_width=1.2,
        annotation_text=f"손절 {plan['stop']:.2f}", annotation_position="right",
        annotation_font=dict(size=9, color="#ef5350"),
    )
    for i, (t, rr) in enumerate(zip(plan.get("targets", []), plan.get("rr", [])), start=1):
        rr_s = f" (R:R {rr:.1f})" if rr else ""
        fig.add_hline(
            y=t, line_dash="dot", line_color="#26a69a", line_width=1.2,
            annotation_text=f"목표{i} {t:.2f}{rr_s}", annotation_position="right",
            annotation_font=dict(size=9, color="#26a69a"),
        )
    return fig


def plot_ict_chart(df: pd.DataFrame, n_candles: int = 80, ticker: str = "",
                   plan: dict | None = None) -> go.Figure:
    """ICT 오버레이 캔들스틱 차트 반환. plan 을 주면 진입/손절/목표선도 그린다."""
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
    overlay_trade_plan(fig, plan)
    return fig


# ── CRT (Candle Range Theory) ───────────────────────────────────
def detect_crt_setup(df: pd.DataFrame, period: int = 3) -> dict:
    """
    CRT Phase 2 감지 (매수·매도 진입 단계).
    최근 period 캔들을 HTF CRT Range(ERL High / ERL Low)로 설정,
    오늘(마감 후 마지막) 캔들이 ERL을 스윕 후 Range 안으로 되돌아왔는지 확인.

    Phase 2 Bullish: 오늘 캔들 저가 < CRT Low AND 종가가 Range 중간~High 사이
                     → 내일 IRL(Range 내부)로 복귀 후 반대 ERL(High) 전달 예상
    Phase 2 Bearish: 오늘 캔들 고가 > CRT High AND 종가가 Low~Range 중간 사이
                     → 내일 IRL로 복귀 후 반대 ERL(Low) 전달 예상

    종가 조건은 "Range 안으로 되돌아왔나"가 아니라 **반대편 절반까지 회복했나**
    다 (crt_mid 기준). 스윕 직후 겨우 ERL 위에서 끝난 캔들은 반전으로 안 친다.

    ⚠️ **기준 Range 가 정본 CRT 와 다르다 — 롤링 max/min 이고, 정본은 HTF 캔들 하나다.**
    정본은 캔들이 닫힐 때까지 경계가 고정인데 여기는 매일 미끄러진다. 2026-09-03 에
    두 정의를 대조했더니 **발동률은 비슷한데 발동일이 60% 갈렸다**(Jaccard 40%) —
    파라미터 차이가 아니라 다른 신호다. 다만 **어느 쪽이 나은지는 못 갈랐다**: 다음날
    수익에서 두 정의 네 줄 모두 MDE 를 못 넘었다. 그래서 **고치지 않고 그대로 둔다.**
    바꿔서 나아진다는 증거가 없다. docs/measurements/2026-09-03-crt-range-definition.md

    ⚠️ **period 를 늘려도 "오늘 캔들"은 한 봉 그대로다.** 그래서 이 함수는
    `calc_ict_adjustment(scale=N)` 의 시간 환산이 성립하지 않는 유일한 항이다:
    기준 레인지만 N배로 넓어지고 스윕하는 봉은 그대로라 발동이 사실상 사라진다
    (2026-08-20 실측 발동률 — 일봉 period=3 은 2.99%, 15분봉 period=78 은 0.01%).
    ⚠️ **이 항을 빼도 성적이 갈리지 않는다 — 그래서 그냥 둔다.** 2026-09-04 절제
    (`docs/measurements/2026-09-04-crt-weight-ablation.md`): 트레이드 플랜 백테스트를
    이 항 있는 팔/없는 팔로 돌렸더니 OOS 기대값 +0.115R vs +0.096R, 차이 +0.019R 에
    MDE 0.029R — **못 넘었다.** 갈리려면 표본이 2.3~2.8배 필요하다. 같이 나온 것:
    빼도 셋업이 2.7% 줄 뿐이고(다른 항이 이미 방향 게이트를 넘겨 놓는다), 조정값이
    ±30 으로 잘리는 탓에 **발동의 14% 는 20 을 다 받지도 못한다.**

    ICT 조정점수 ±30 중 ±20 을 차지하는 최대 항이라 영향이 작지 않다. 러너는
    scale=1 이라 지금 손해는 없지만, **일수 환산(scale>1)으로 다시 재는 날에는
    "오늘 캔들"도 scale 봉을 묶어 하나로 만든 뒤 비교해야 한다.**

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
def calc_ict_adjustment(df: pd.DataFrame, *, scale: int = 1) -> dict:
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

    scale — 창(lookback)에 곱하는 배수. 일봉은 1. 15분봉에서 일봉과 같은
    실제 시간을 보려면 26(정규장 하루 = 15분봉 26개). 이 함수의 창은 전부
    **봉 개수**라, scale 없이 분봉에 먹이면 "3개월 구조"가 이틀이 된다.
    """
    if df.empty or len(df) < 60 * scale:
        return {"adjustment": 0, "signals": [], "crt": {}}

    try:
        cur     = float(df["Close"].iloc[-1])
        adj     = 0
        signals = []

        # 1. CRT Phase 2
        crt = detect_crt_setup(df, period=3 * scale)
        if crt["setup"] == "bullish":
            adj += 20
            signals.append(f"CRT Bullish Phase2 (${crt['swept_erl']:.2f} ERL 스윕→반전)")
        elif crt["setup"] == "bearish":
            adj -= 20
            signals.append(f"CRT Bearish Phase2 (${crt['swept_erl']:.2f} ERL 스윕→반전)")

        # 2. FVG — 미충족 갭과 현재가 위치
        fvgs = find_fvg(df, lookback=60 * scale, min_gap_pct=0.03)
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
        obs = find_order_blocks(df, lookback=60 * scale, min_move_pct=1.0)
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
        swings = find_swing_points(df.tail(80 * scale), lookback=5 * scale)
        events = find_bos_choch(df.tail(80 * scale), swings)
        if events:
            last = events[-1]["type"]
            if "bull" in last:
                adj += 10
                signals.append(f"최근 {last.upper()} (불리시 구조 전환)")
            elif "bear" in last:
                adj -= 10
                signals.append(f"최근 {last.upper()} (베어리시 구조 전환)")

        # 5. Premium / Discount
        pd_info = premium_discount(df, lookback=60 * scale)
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
