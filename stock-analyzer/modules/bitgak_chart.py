"""빗각 채널 차트 — 8단계 러너와 **같은 선**을 그린다.

산수를 다시 안 짠다. 작도(변곡점 확정·3점·피보 채널)는 `pilot_bitgak_power`,
그날의 채널은 `run_bitgak_paper._carried_channel`, 발동 판정은 `lines_at`,
유동성 컷은 `measure_bitgak._liq_mask` 를 그대로 부른다. 여기서 규칙을 한
줄이라도 다시 쓰면 화면이 러너와 다른 신호를 그리게 되고, 그건 예전
`find_trend_channel` 이 "빗각채널"이라는 이름만 같고 다른 물건이었던 자리로
되돌아가는 것이다(`docs/bitgak-spec.md` §1).

**같아야 하는 게 규칙만이 아니다.** `_carried_channel` 은 경로 의존이라
(방문 순서를 되짚는다) 시작 봉이 다르면 다른 채널이 나온다. 그래서 창을
러너와 같은 `CALENDAR_DAYS` 로 고정하고, 시세도 같은 sip 피드를 먼저 쓴다
(`sip_bars`). 키가 없어 yfinance 로 물러선 회차는 `feed` 로 드러난다 —
`_qualify` 의 돌파변곡(③)이 `_poc_zone` 으로 거래량 가중이라, 피드가 다르면
변곡점 3점이 갈릴 수 있기 때문이다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from measure_bitgak import _liq_mask  # noqa: E402
from pilot_bitgak_power import (  # noqa: E402
    LEVELS, MIN_LEN, POC_WIN, SWING_L, _level, _qualify, scan,
)
from run_bitgak_paper import (  # noqa: E402
    LIQ, MIN_PX, SHAPES, _carried_channel, daily_bars, lines_at,
)

from modules.ict_analysis import (  # noqa: E402
    _TV_BG, _TV_GRID, _TV_PAPER, _TV_TEXT, find_swing_points,
)

# 러너 `plan()` 과 같은 문턱·같은 창. 화면 때문에 낮추면 잰 적 없는 규칙이 된다.
MIN_BARS = MIN_LEN + 1
CALENDAR_DAYS = 1100

_UP, _DOWN, _FLAT = "#26a69a", "#ef5350", "#ff9800"


def sip_bars(ticker: str):
    """러너와 같은 sip 일봉. 키가 없거나 피드가 막히면 None (호출부가 물러선다)."""
    try:
        df = daily_bars([ticker], calendar_days=CALENDAR_DAYS).get(ticker)
    except Exception:
        return None
    return df.dropna() if df is not None and not df.empty else None


def bitgak_state(df, feed: str = "yfinance") -> dict | None:
    """마지막 봉 기준 빗각 상태. 못 그리면 `{"error": ...}`."""
    if df is None or len(df) == 0 or "Close" not in df.columns:
        return {"error": "시세 없음"}
    df = df.dropna(subset=["Close"])
    n = len(df)
    if n < MIN_BARS:
        return {"error": f"봉 부족 — {n}봉 (작도에 {MIN_BARS}봉 필요)"}

    sw = find_swing_points(df, lookback=SWING_L)
    if len(sw) < 3:
        return {"error": "스윙 포인트 3개 미만"}

    qual = _qualify(df, sw)
    trades = scan(df, shapes=SHAPES, fill="gap", entry="close")
    ch, pts = _carried_channel(df, sw, qual, trades, n - 1)
    if ch is None:
        return {"error": "변곡점 3개(저저고/고고저)를 못 찾음"}

    i = n - 1
    close = float(df["Close"].iloc[-1])
    # 가격 순 사다리 — 고고저는 offset 이 음수라 k 순서와 뒤집힌다.
    rung = sorted(LEVELS, key=lambda k: _level(ch, k, i))
    ladder = [(k, _level(ch, k, i)) for k in rung]
    below = [(k, y) for k, y in ladder if y <= close]
    above = [(k, y) for k, y in ladder if y > close]

    slope = ch[0]
    slope_pct = slope / (float(df["Close"].tail(POC_WIN).mean()) + 1e-9) * 100

    # 러너 `plan()` 은 이 컷을 통과한 종목만 `lines_at` 에 넣는다. 화면이 컷을
    # 건너뛰면 러너가 절대 주문 안 낼 종목에도 진입·손절·목표가 뜬다.
    eligible = bool(_liq_mask(df, LIQ, MIN_PX)[-1])
    hit = (lines_at(df, shapes=SHAPES, trades=trades, sw=sw, qual=qual, ch=ch)
           if eligible else None)

    return {
        "ch": ch,
        "pts": pts,
        "ladder": ladder,
        "close": close,
        "k_below": below[-1] if below else None,
        "k_above": above[0] if above else None,
        "direction": ("bullish" if slope_pct > 0.01 else
                      "bearish" if slope_pct < -0.01 else "sideways"),
        "slope_pct": slope_pct,
        "width_pct": (ladder[-1][1] - ladder[0][1]) / close * 100,
        "eligible": eligible,
        "hit": hit,
        "n_trades": len(trades),
        "feed": feed,
        "bars": n,
    }


def plot_bitgak_chart(df, state: dict | None, n_candles: int = 120,
                      ticker: str = "") -> go.Figure:
    """캔들 + 빗각 12선(+ 발동 시 진입·손절·목표)."""
    fig = go.Figure()
    ok = df is not None and len(df) and "Close" in df.columns
    if ok:
        df = df.dropna(subset=["Close"])
        n_candles = min(n_candles, len(df))
        x0 = len(df) - n_candles
        sub = df.iloc[x0:]
        fig.add_trace(go.Candlestick(
            x=sub.index, open=sub["Open"], high=sub["High"],
            low=sub["Low"], close=sub["Close"],
            increasing_line_color=_UP, decreasing_line_color=_DOWN, name="Price",
        ))

    if ok and state and not state.get("error"):
        ch = state["ch"]
        col = {"bullish": _UP, "bearish": _DOWN, "sideways": _FLAT}[state["direction"]]
        xs = list(range(x0, len(df)))
        k_hi = state["k_above"][0] if state["k_above"] else None
        k_lo = state["k_below"][0] if state["k_below"] else None
        # 사다리 맨 아래 칸 = 아래에 선이 없어 손절을 못 거는 자리. 고고저 채널은
        # offset 이 음수라 그 자리가 L0 이 아니라 L5.5 다 — k 로 찍으면 반대쪽이
        # 굵어진다. 가격 순으로 정렬된 `ladder` 의 첫 칸을 쓴다.
        k_base = state["ladder"][0][0]

        for k in LEVELS:
            ys = [_level(ch, k, x) for x in xs]
            live = k in (k_hi, k_lo)          # 현재 가격을 감싼 두 칸
            base = k == k_base
            fig.add_trace(go.Scatter(
                x=sub.index, y=ys, mode="lines",
                line=dict(color=col if (live or base) else "rgba(255,255,255,0.22)",
                          width=1.6 if live else 1.2 if base else 0.8,
                          dash="solid" if base else "dot" if live else "dash"),
                name=f"L{k:g}", showlegend=False,
                hovertemplate=f"L{k:g}  %{{y:.2f}}<extra></extra>",
            ))
            fig.add_annotation(
                x=sub.index[-1], y=ys[-1], text=f"{k:g}", showarrow=False,
                font=dict(size=8, color=col if (live or base) else "#6b7280"),
                xanchor="left", xshift=4,
            )

        # 변곡점 3점 — 이 세 점이 채널 전체를 정한다
        if state["pts"]:
            x1, y1, x2, y2, x3, y3 = state["pts"]
            vis = [(x, y, t) for x, y, t in
                   ((x1, y1, "P1"), (x2, y2, "P2"), (x3, y3, "P3")) if x >= x0]
            if vis:
                fig.add_trace(go.Scatter(
                    x=[df.index[x] for x, _, _ in vis],
                    y=[y for _, y, _ in vis],
                    mode="markers+text", text=[t for _, _, t in vis],
                    textposition="top center",
                    textfont=dict(size=9, color="#facc15"),
                    marker=dict(symbol="circle", size=9, color="#facc15",
                                line=dict(width=1, color="#000")),
                    name="변곡점", showlegend=False,
                ))

        hit = state["hit"]
        if hit:
            for lvl, label, lc in (
                (hit["entry_lvl"], f"진입 L{hit['k']:g} ({hit['shape']})", "#2962ff"),
                (hit["stop_lvl"],  f"손절 L{hit['k_stop']:g}", _DOWN),
                (hit["target_lvl"], "목표", _UP),
            ):
                fig.add_hline(y=lvl, line_dash="dashdot", line_color=lc, line_width=1.2,
                              annotation_text=label, annotation_position="left",
                              annotation_font=dict(size=9, color=lc))

    title = f"{ticker}  빗각 채널 (12선)"
    if state and state.get("error"):
        title += f" — {state['error']}"
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=_TV_TEXT)),
        height=520,
        plot_bgcolor=_TV_BG, paper_bgcolor=_TV_PAPER,
        xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor=_TV_GRID, showgrid=True, color=_TV_TEXT),
        yaxis=dict(gridcolor=_TV_GRID, showgrid=True, color=_TV_TEXT),
        showlegend=False,
        margin=dict(l=0, r=40, t=40, b=0),
    )
    return fig
