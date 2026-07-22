"""
불스해적단(블랙펄 제1함대) 매매기법 — 공용 백본 유틸
=====================================================
강의 11강 전체를 관통하는 재사용 유틸을 순수 함수로 모은 모듈.

설계 원칙 (bulls-pirate-course-notes 메모리 기준):
  1) 거래량 동반 = 진짜 / 없으면 페이크          → volume_ratio / volume_confirmed
  2) 상위 추세 레짐이 오실레이터·밴드 신호의 전제  → adx / trend_regime  (9·10·11강 상위 게이트)
  3) 다이버전스는 RSI/MACD/스토캐 공용             → detect_divergence   (2·8·9·10강)
  4) 크로스는 구간필터와 함께                      → cross_events        (7·8·10강)
  5) 볼린저 파생값으로 터치/밴드워크/스퀴즈 정량화 → bollinger_features  (11강)
  6) 박스권 + 거래량 종가 돌파 + measured move     → detect_box_breakout (1강)

★ 중요: 이 모듈은 "검증 가능한 팩터"를 계산할 뿐, **라이브 시그널에 자동 편입하지 않는다.**
  bulls_raw_score() 의 가중치는 IC 검증 전 플레이스홀더이며, factor_validator 의
  run_per_factor_ic_analysis 로 예측력(IC)을 확인한 뒤에만 신호에 편입한다.
  (검증 없이 규칙을 하드코딩했던 signal-alerts 함정을 반복하지 않기 위함.)

입력 규약: OHLCV DataFrame — 대문자 컬럼 Open/High/Low/Close/Volume, DatetimeIndex.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV_COLS = ("Open", "High", "Low", "Close", "Volume")


# ── 내부 유틸 ────────────────────────────────────────────────────
def _require_cols(df: pd.DataFrame, cols=("High", "Low", "Close")) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing} (필요: {cols})")


def _rma(s: pd.Series, period: int) -> pd.Series:
    """Wilder 평활(RMA) = ewm(alpha=1/period)."""
    return s.ewm(alpha=1.0 / period, adjust=False).mean()


def _local_swings(values: np.ndarray, lookback: int = 5) -> list[tuple[int, float, str]]:
    """1-D 배열에서 좌우 lookback 기준 국소 고점(H)/저점(L) 탐지.

    반환: [(idx, value, 'H'|'L'), ...]  (시간순)
    """
    n = len(values)
    out: list[tuple[int, float, str]] = []
    for i in range(lookback, n - lookback):
        window_l = values[i - lookback:i]
        window_r = values[i + 1:i + lookback + 1]
        v = values[i]
        if v > window_l.max() and v > window_r.max():
            out.append((i, float(v), "H"))
        elif v < window_l.min() and v < window_r.min():
            out.append((i, float(v), "L"))
    return out


def _f(x) -> float | None:
    """NaN 안전 float 변환."""
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ── 1. 거래량 필터 (8-4, 전 강의 공통) ───────────────────────────
def volume_ratio(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """당일 거래량 / 거래량 window일 이동평균. 값 > 1 = 평균 이상."""
    _require_cols(df, ("Volume",))
    vol = df["Volume"].astype(float)
    sma = vol.rolling(window, min_periods=max(2, window // 2)).mean()
    return vol / sma.replace(0, np.nan)


def volume_confirmed(df: pd.DataFrame, window: int = 20, k: float = 1.0) -> bool:
    """마지막 봉의 거래량이 window일 평균의 k배 이상인가 (진짜 돌파 판정)."""
    r = volume_ratio(df, window)
    last = r.iloc[-1] if len(r) else np.nan
    return bool(np.isfinite(last) and last >= k)


# ── 2. ADX / 추세·횡보 레짐 게이트 (9·10·11강 상위 게이트) ────────
def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder ADX / +DI / -DI. 반환 컬럼: adx, plus_di, minus_di."""
    _require_cols(df, ("High", "Low", "Close"))
    high, low, close = df["High"].astype(float), df["Low"].astype(float), df["Close"].astype(float)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr = _rma(tr, period)
    plus_di = 100 * _rma(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100 * _rma(minus_dm, period) / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_line = _rma(dx.fillna(0.0), period)

    return pd.DataFrame({"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di})


def trend_regime(df: pd.DataFrame, period: int = 14, adx_trend: float = 20.0) -> str:
    """추세/횡보 레짐 판정 — 오실레이터·밴드 신호의 상위 게이트.

    ADX >= adx_trend 이면 추세장(방향은 +DI vs -DI), 아니면 횡보장(range).
    반환: "trend_up" | "trend_down" | "range"
    """
    a = adx(df, period)
    if a.empty or not np.isfinite(a["adx"].iloc[-1]):
        return "range"
    strength = a["adx"].iloc[-1]
    if strength < adx_trend:
        return "range"
    return "trend_up" if a["plus_di"].iloc[-1] >= a["minus_di"].iloc[-1] else "trend_down"


# ── 3. 볼린저밴드 파생값 (11강) ──────────────────────────────────
def bollinger_features(df: pd.DataFrame, window: int = 20, num_std: float = 2.0,
                       squeeze_lookback: int = 120, walk_bars: int = 2) -> dict:
    """볼린저 파생값으로 터치/밴드워크/스퀴즈를 정량화.

    반환 dict:
      mid, upper, lower  : 마지막 봉 밴드 값
      pctB               : (Close-lower)/(upper-lower)  — 0=하단,1=상단
      bandwidth          : (upper-lower)/mid            — 변동성 폭
      squeeze            : bandwidth 가 squeeze_lookback 내 최저 부근인가(에너지 응축)
      bandwalk_up/down   : 최근 walk_bars 봉이 밴드 밖(추세장 밴드워크)인가
      touch_upper/lower  : 마지막 봉이 밴드 상/하단 터치·이탈인가
    """
    _require_cols(df, ("Close",))
    close = df["Close"].astype(float)
    mid = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std

    span = (upper - lower).replace(0, np.nan)
    pct_b = (close - lower) / span
    bandwidth = (upper - lower) / mid.replace(0, np.nan)

    bw_last = bandwidth.iloc[-1]
    bw_window = bandwidth.tail(squeeze_lookback).dropna()
    squeeze = bool(
        np.isfinite(bw_last) and len(bw_window) >= window
        and bw_last <= bw_window.quantile(0.10)
    )

    recent_b = pct_b.tail(walk_bars)
    bandwalk_up = bool(len(recent_b) == walk_bars and (recent_b > 1.0).all())
    bandwalk_down = bool(len(recent_b) == walk_bars and (recent_b < 0.0).all())

    b_last = pct_b.iloc[-1]
    return {
        "mid": _f(mid.iloc[-1]),
        "upper": _f(upper.iloc[-1]),
        "lower": _f(lower.iloc[-1]),
        "pctB": _f(b_last),
        "bandwidth": _f(bw_last),
        "squeeze": squeeze,
        "bandwalk_up": bandwalk_up,
        "bandwalk_down": bandwalk_down,
        "touch_upper": bool(np.isfinite(b_last) and b_last >= 1.0),
        "touch_lower": bool(np.isfinite(b_last) and b_last <= 0.0),
    }


# ── 4. 공용 다이버전스 (2·8·9·10강) ──────────────────────────────
def detect_divergence(price: pd.Series, osc: pd.Series, lookback: int = 60,
                      swing_lookback: int = 5) -> dict:
    """가격 vs 임의 오실레이터(RSI/MACD/스토캐) 다이버전스 — 정규 4종 + 히든.

    정규(추세 전환):
      regular_bull : 가격 저점↓ + 지표 저점↑  → 반등 준비
      regular_bear : 가격 고점↑ + 지표 고점↓  → 하락 준비
    히든(추세 지속):
      hidden_bull  : 가격 저점↑ + 지표 저점↓  → 상승 지속
      hidden_bear  : 가격 고점↓ + 지표 고점↑  → 하락 지속

    가장 최근 완성된 스윙 쌍 기준. 반환: 위 4개 bool dict.
    """
    out = {"regular_bull": False, "regular_bear": False,
           "hidden_bull": False, "hidden_bear": False}
    if price is None or osc is None:
        return out

    p = price.dropna().tail(lookback)
    o = osc.reindex(p.index).astype(float)
    joined = pd.concat([p.astype(float), o], axis=1).dropna()
    if len(joined) < swing_lookback * 2 + 3:
        return out
    pv = joined.iloc[:, 0].values
    ov = joined.iloc[:, 1].values

    p_sw = _local_swings(pv, swing_lookback)
    p_highs = [(i, v) for i, v, t in p_sw if t == "H"]
    p_lows = [(i, v) for i, v, t in p_sw if t == "L"]

    def _osc_at(idx: int) -> float:
        return float(ov[idx])

    # 저점 비교 → bull 계열
    if len(p_lows) >= 2:
        (i1, pl1), (i2, pl2) = p_lows[-2], p_lows[-1]
        ol1, ol2 = _osc_at(i1), _osc_at(i2)
        if pl2 < pl1 and ol2 > ol1:
            out["regular_bull"] = True
        elif pl2 > pl1 and ol2 < ol1:
            out["hidden_bull"] = True

    # 고점 비교 → bear 계열
    if len(p_highs) >= 2:
        (j1, ph1), (j2, ph2) = p_highs[-2], p_highs[-1]
        oh1, oh2 = _osc_at(j1), _osc_at(j2)
        if ph2 > ph1 and oh2 < oh1:
            out["regular_bear"] = True
        elif ph2 < ph1 and oh2 > oh1:
            out["hidden_bear"] = True

    return out


# ── 5. 크로스 이벤트 + 구간 필터 (7·8·10강) ──────────────────────
def cross_events(fast: pd.Series, slow: pd.Series,
                 zone_low: float | None = None,
                 zone_high: float | None = None) -> dict:
    """두 라인의 골든/데드크로스 + 구간 필터.

    - 이평선 GC/DC(7강), MACD/시그널 크로스(8강), 스토캐 %K/%D 크로스(10강) 공용.
    - 구간필터: 스토캐처럼 "20 이하 GC = 강한 매수 / 80 이상 DC = 핵심 매도" 판정용.
      fast 의 크로스 시점 값이 zone_low 이하면 golden_in_zone, zone_high 이상이면 dead_in_zone.

    반환: golden, dead, golden_in_zone, dead_in_zone (bool)
    """
    out = {"golden": False, "dead": False,
           "golden_in_zone": False, "dead_in_zone": False}
    f = fast.astype(float)
    s = slow.astype(float)
    joined = pd.concat([f, s], axis=1).dropna()
    if len(joined) < 2:
        return out
    diff = joined.iloc[:, 0] - joined.iloc[:, 1]
    prev, cur = diff.iloc[-2], diff.iloc[-1]
    fast_now = float(joined.iloc[-1, 0])

    if prev <= 0 < cur:
        out["golden"] = True
        if zone_low is not None and fast_now <= zone_low:
            out["golden_in_zone"] = True
    elif prev >= 0 > cur:
        out["dead"] = True
        if zone_high is not None and fast_now >= zone_high:
            out["dead_in_zone"] = True
    return out


# ── 6. 박스권 + 거래량 종가 돌파 (1강) ───────────────────────────
def detect_box_breakout(df: pd.DataFrame, lookback: int = 60, swing_lookback: int = 3,
                        tol_pct: float = 1.5, min_touches: int = 2,
                        vol_window: int = 20, vol_k: float = 1.0,
                        fib_ext: float = 1.618) -> dict:
    """박스권 감지 → 거래량 동반 종가 돌파 → measured-move 목표/손절 + 페이크아웃.

    반환 dict:
      in_box            : 최근이 박스권(2회 이상 지지·저항 반복)인가
      box_high/box_low  : 박스 상·하단
      breakout_up/down  : 마지막 종가가 박스 상/하단을 종가 기준 돌파했는가
      vol_confirmed     : 돌파에 거래량(vol_window MA 대비 vol_k배) 동반했는가
      fakeout           : 돌파 방향인데 거래량 미동반(페이크아웃 위험)인가
      target            : measured move 목표가(박스높이 × fib 확장), 손절 stop
    """
    _require_cols(df, OHLCV_COLS)
    out = {
        "in_box": False, "box_high": None, "box_low": None,
        "breakout_up": False, "breakout_down": False,
        "vol_confirmed": False, "fakeout": False,
        "target": None, "stop": None,
    }
    sub = df.tail(lookback)
    if len(sub) < swing_lookback * 2 + 3:
        return out

    highs = _local_swings(sub["High"].astype(float).values, swing_lookback)
    lows = _local_swings(sub["Low"].astype(float).values, swing_lookback)
    res_touches = [v for _, v, t in highs if t == "H"]
    sup_touches = [v for _, v, t in lows if t == "L"]
    if len(res_touches) < min_touches or len(sup_touches) < min_touches:
        return out

    box_high = float(np.median(res_touches))
    box_low = float(np.median(sup_touches))
    if box_high <= box_low:
        return out

    # 저항 터치들이 box_high 근처, 지지 터치들이 box_low 근처로 tol_pct 내 뭉쳐야 박스 확정
    res_ok = all(abs(v - box_high) / box_high * 100 <= tol_pct for v in res_touches[-min_touches:])
    sup_ok = all(abs(v - box_low) / box_low * 100 <= tol_pct for v in sup_touches[-min_touches:])
    out["in_box"] = bool(res_ok and sup_ok)
    out["box_high"], out["box_low"] = box_high, box_low

    close = float(sub["Close"].iloc[-1])
    box_height = box_high - box_low
    vol_ok = volume_confirmed(df, vol_window, vol_k)
    out["vol_confirmed"] = vol_ok

    if out["in_box"] and close > box_high:
        out["breakout_up"] = True
        out["target"] = box_high + box_height * fib_ext
        out["stop"] = box_high  # 돌파선 종가 재이탈 시 손절
        out["fakeout"] = not vol_ok
    elif out["in_box"] and close < box_low:
        out["breakout_down"] = True
        out["target"] = box_low - box_height * fib_ext
        out["stop"] = box_low
        out["fakeout"] = not vol_ok
    return out


# ── 7. 팩터 합성 (IC 검증 전 플레이스홀더) ───────────────────────
def bulls_raw_score(df: pd.DataFrame, rsi: pd.Series | None = None) -> float:
    """공용 백본 신호를 단일 스칼라로 합성 — factor_validator IC 검증용 원점수.

    ★ 가중치는 IC 검증 전 플레이스홀더. run_per_factor_ic_analysis 로 예측력을
      확인하기 전에는 라이브 시그널에 편입하지 않는다. (bulls-pirate-course-notes)

    합성 규칙(불스해적단 공통 테마 반영):
      - 상위 레짐 게이트: 추세장이면 추세방향 신호 가중, 횡보장이면 평균회귀 가중
      - 거래량 동반 돌파는 가산, 미동반(페이크아웃)은 감산
      - 다이버전스/밴드 위치는 소폭 가감
    반환: float (양수=강세, 음수=약세). 데이터 부족 시 0.0.
    """
    try:
        _require_cols(df, OHLCV_COLS)
    except ValueError:
        return 0.0
    if len(df) < 30:
        return 0.0

    score = 0.0
    regime = trend_regime(df)
    boll = bollinger_features(df)
    box = detect_box_breakout(df)

    # 거래량 동반 박스 돌파 (1강 핵심)
    if box["breakout_up"]:
        score += 1.0 if box["vol_confirmed"] else -0.5
    if box["breakout_down"]:
        score -= 1.0 if box["vol_confirmed"] else 0.5  # 거래량 미동반 하락돌파 = 페이크 → 반등 여지

    # 레짐별 볼린저 해석 (11강 메타규칙: 같은 상단터치라도 추세/횡보 상반)
    if regime == "trend_up" and boll["bandwalk_up"]:
        score += 0.7                       # 추세장 상단 밴드워크 = 추세 지속 매수
    elif regime == "range" and boll["touch_upper"]:
        score -= 0.5                       # 횡보장 상단 터치 = 과열 매도
    elif regime == "range" and boll["touch_lower"]:
        score += 0.5                       # 횡보장 하단 터치 = 과매도 매수
    if boll["squeeze"]:
        score += 0.2                       # 스퀴즈 = 방향 대기(약한 가점)

    # RSI 다이버전스 (오실레이터 주어졌을 때만)
    if rsi is not None:
        div = detect_divergence(df["Close"], rsi)
        score += 0.5 * (div["regular_bull"] + div["hidden_bull"])
        score -= 0.5 * (div["regular_bear"] + div["hidden_bear"])

    return float(score)
