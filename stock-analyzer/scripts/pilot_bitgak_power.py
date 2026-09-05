#!/usr/bin/env python
"""빗각 채널 — **재기 전에 검출력만 잰다.** (명세 2단계)

    python scripts/pilot_bitgak_power.py [워커수]
    python scripts/pilot_bitgak_power.py selftest

명세: `docs/bitgak-spec.md` (1단계 완료, A/B/C 봉인). 여기서 묻는 건 하나다 —
**이 규칙을 이 저장소의 자로 잴 수 있는가.** 자산배분(MDE 1.98%p vs 효과
0.7%p)·펀딩캐리·8-K 가 이 자리에서 닫혔다.

## 눈가림 — 진짜 팔의 성적은 여기서 안 잰다

`run()` 은 진짜 작도의 **발동 횟수만** 쓴다. 기대값·승률은 계산은 되지만
집계도 출력도 하지 않는다(`_counts_only`). 분산(방해 모수)은 **위약 팔**에서
채운다 — 무작위 3점으로 그린 같은 기하학이다. 재면 사전 등록 전에 결과를
본 것이 되므로, PEAD 의 `mde_pp` 와 같은 장치를 R 단위로 옮긴 것이다.

## 왜 R 이 이 노선의 자인가

손절이 **한 칸 아래 선**, 익절이 **한 칸 위 선**이므로(명세 3.5) 채널 폭이
곧 R 이다. 선이 평행이라 위험과 보상이 같은 폭 — 즉 **구조적으로 1:1 이고
승률이 전부**다. 종목마다 채널 폭이 달라도 R 로 나누면 같은 자에 올라간다.
비교 대상은 이 저장소가 OOS 로 양수를 본 유일한 값 **+0.14R**(트레이드
기하학)이다. 그보다 MDE 가 크면 이 노선은 여기서 닫힌다.

## 명세가 안 정한 값 — 정찰에서 고정한 것 3개

봉인된 A/B/C 말고, 기계화하다 보니 명세에 숫자가 없는 자리가 셋 나왔다.
그리드 서치는 금지이므로 각각 하나로 못박고 여기 적는다. **바꾸려면 새
사전 등록으로 간다.**

  D. 매물대 창 = **252봉**, 50빈. 명세 B 는 "볼륨프로파일 최대 구간"까지만
     정하고 창을 안 정했다. 전 구간 POC 는 거의 안 깨져 ③이 사실상 사라진다.
  E. P2 선택 = **가장 최근에 확정된 변곡점**. 원문 11강이 "현재" 관점으로
     작도하므로 최신 점을 잡는다. 후보 중 고르는 규칙을 새로 만들지 않는다.
  F. 익절선은 **12선 밖으로 나가도 된다**(k=6.0). 명세가 level 0 진입을 막은
     이유는 "손절 걸 자리가 없다"이지 익절이 아니다. 사다리는 등차라 k=6.0 은
     새 선이 아니라 같은 사다리의 다음 칸이다.

## 함정

- **저저고/고고저 둘 다 그린다.** 원문 11강의 비트코인 장기 채널은 역사적
  **고점**을 포함한다. 그래서 사다리를 k 순서가 아니라 **가격 순서**로 놓고
  "한 칸 아래"를 정의한다. 고고저는 offset 이 음수라 k 순서로 잡으면 손절이
  진입 위로 올라가 롱이 숏이 된다. (selftest ②)
- **손절·익절선은 기울어져 있다.** 진입 후에도 매 봉 선이 움직이므로 r 이
  정확히 −1.00 이 아니다. 이건 결함이 아니라 채널의 정의다.
- **위약은 하한이다.** 위약도 같은 스윙 풀에서 뽑으므로 진짜 채널과 겹치는
  구간이 생긴다. 겹치는 만큼 분산이 닮아 MDE 가 작게 나온다 — 게이트를
  통과해도 그건 "잴 수 있다"의 하한이지 보증이 아니다(PEAD 설계서 0절과
  같은 구조).
- **미국주식과 크립토를 합쳐서 재지 않는다.** 원문 3강 5원칙(크립토는
  오버슈팅·페이크무빙이 많다). 표도 따로 낸다.
- 거래비용은 안 넣었다. 정찰은 "잴 수 있느냐"만 보고, 비용은 수준을 옮기지
  분산을 거의 안 바꾼다. 사전 등록 단계에서 넣는다.
"""
from __future__ import annotations

import os
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from modules.ict_analysis import find_swing_points  # noqa: E402

US_PANEL = Path("data/price_panel_v1.parquet")
CRYPTO_PANEL = Path("data/crypto_panel.parquet")

# 5단계 — 생존자 편향 없는 대형주 패널(`python scripts/pilot_bitgak_power.py largecap`).
# 4단계의 293종목은 **지금 상장돼 있는** 이름만 손으로 적은 목록이라, 죽은 회사가
# 하나도 없다. 이쪽은 웨이백 SEC 스냅샷으로 지은 월별 상위 1,000 이다.
# 창 시작 2017-09-01 은 그 스냅샷의 첫 기준일(2017-08-31) 다음 날이고, 그보다
# 앞은 "그날 뭐가 상장돼 있었나"를 복원할 재료가 없다(실측: 웨이백 CDX 에
# company_tickers.json 이 2017-08-28 이전에 없다. Alpaca 봉도 2016-01-04 이 바닥).
LARGECAP_PANEL    = Path("data/largecap_ohlcv_panel.parquet")
LARGECAP_UNIVERSE = Path("data/largecap_universe.parquet")
LARGECAP_START    = pd.Timestamp("2017-09-01")
CRYPTO = ["BTC-USD", "ETH-USD", "XRP-USD", "LTC-USD", "BCH-USD", "ADA-USD",
          "DOGE-USD", "LINK-USD", "XLM-USD", "SOL-USD", "AVAX-USD", "DOT-USD"]
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

SWING_L = 5            # 자유도 A — 봉인
TREND_MOVE = 0.35      # 원문 3강 8원칙, 유일한 숫자
POC_WIN, POC_BINS = 252, 50   # 자유도 D — 정찰에서 고정
LEVELS = tuple(0.5 * i for i in range(12))   # 0 ~ 5.5, 12선 (사장님 결정)
HOLD = 40              # 러너 실설정(`trade_plan_backtest.DEFAULT_HOLD_WINDOW`)
MIN_LEN = 400
MIN_RISK_PCT = 0.001   # 채널 폭이 0.1% 미만이면 자가 안 되는 트레이드 — 버린다
REPS, SEED = 2000, 20260904
YARDSTICK = 0.14       # 트레이드 기하학 OOS 기대값 — 이 노선이 넘어야 할 값


# ── 작도 ────────────────────────────────────────────────────────
def _poc_zone(df: pd.DataFrame, j: int) -> tuple[float, float]:
    """j 로 끝나는 252봉의 볼륨프로파일 최대 구간 [하단, 상단] (자유도 B·D)."""
    lo_i = max(0, j - POC_WIN + 1)
    h, l = df["High"].values[lo_i:j + 1], df["Low"].values[lo_i:j + 1]
    c, v = df["Close"].values[lo_i:j + 1], df["Volume"].values[lo_i:j + 1]
    top, bot = float(h.max()), float(l.min())
    if not np.isfinite(top) or top <= bot:
        return float("nan"), float("nan")
    edges = np.linspace(bot, top, POC_BINS + 1)
    typ = (h + l + c) / 3.0
    b = np.clip(np.digitize(typ, edges) - 1, 0, POC_BINS - 1)
    vol = np.bincount(b, weights=np.nan_to_num(v), minlength=POC_BINS)
    k = int(vol.argmax())
    return float(edges[k]), float(edges[k + 1])


def _qualify(df: pd.DataFrame, sw: pd.DataFrame) -> np.ndarray:
    """스윙마다 "몇 번 봉에서 변곡점으로 **확정**되는가". 안 되면 무한대.

    ① 역사적 고/저점  — 그 자리에서 바로(스윙 확정 봉 j+L)
    ② 추세 변곡       — 이후 반대방향 35% 이동이 나온 봉
    ③ 돌파 변곡       — 스윙이 매물대 안이고, 이후 종가가 그 위를 벗어난 봉

    전부 **그 봉까지의 정보만** 쓴다. selftest ③ 이 매 실행 확인한다.
    """
    hi, lo, cl = df["High"].values, df["Low"].values, df["Close"].values
    run_max, run_min = np.maximum.accumulate(hi), np.minimum.accumulate(lo)
    out = np.full(len(sw), np.inf)
    for r, (j, price, kind) in enumerate(zip(sw["idx"], sw["price"], sw["type"])):
        j = int(j)
        cand = [j + SWING_L]  if (price >= run_max[j] if kind == "H"
                                  else price <= run_min[j]) else []
        # ② — 스윙 이후 반대방향 TREND_MOVE 이동
        if kind == "L":
            hit = np.flatnonzero(hi[j:] >= price * (1 + TREND_MOVE))
        else:
            hit = np.flatnonzero(lo[j:] <= price * (1 - TREND_MOVE))
        if hit.size:
            cand.append(j + int(hit[0]))
        # ③ — 매물대 안의 점이 나중에 위로 돌파된다
        zlo, zhi = _poc_zone(df, j)
        if zlo == zlo and zlo <= price <= zhi:
            brk = np.flatnonzero(cl[j + 1:] > zhi)
            if brk.size:
                cand.append(j + 1 + int(brk[0]))
        if cand:
            out[r] = max(min(cand), j + SWING_L)   # 스윙 자체가 확정되기 전은 없다
    return out


def _triple(sw: pd.DataFrame, qual: np.ndarray, i: int, rng=None):
    """봉 i 시점의 3점. `rng` 가 있으면 **위약** — 변곡점 판정을 안 쓰고 무작위.

    진짜: P2 = 가장 최근 확정 변곡점(자유도 E) → P1 = 그와 같은 종류의 직전
    확정 변곡점(사이에 반대 종류 스윙이 있어야 인정 = 저저고/고고저) →
    P3 = 그 사이 반대 종류 중 가장 극단(저저고면 최고점).
    """
    idxs, prices, kinds = sw["idx"].values, sw["price"].values, sw["type"].values
    seen = np.flatnonzero(idxs + SWING_L <= i)            # 확정된 스윙 전부
    if seen.size < 3:
        return None
    pool = seen if rng is not None else seen[qual[seen] <= i]
    if pool.size < 2:
        return None

    def between(a: int, b: int, kind: str):
        m = seen[(idxs[seen] > idxs[a]) & (idxs[seen] < idxs[b]) & (kinds[seen] != kind)]
        return m

    order = rng.permutation(pool) if rng is not None else pool[::-1]
    for p2 in order:
        kind = kinds[p2]
        same = [p for p in (pool if rng is None else seen)
                if kinds[p] == kind and idxs[p] < idxs[p2]]
        for p1 in (rng.permutation(same) if rng is not None else same[::-1]):
            mid = between(p1, p2, kind)
            if not mid.size:
                continue
            if rng is not None:
                p3 = mid[rng.integers(len(mid))]
            else:
                p3 = mid[prices[mid].argmax() if kind == "L" else prices[mid].argmin()]
            return (int(idxs[p1]), float(prices[p1]), int(idxs[p2]), float(prices[p2]),
                    int(idxs[p3]), float(prices[p3]))
    return None


def _channel(t):
    """3점 → (slope, x1, y1, offset). 명세 3.1 그대로."""
    x1, y1, x2, y2, x3, y3 = t
    if x2 == x1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    offset = y3 - (y1 + slope * (x3 - x1))
    return None if offset == 0 else (slope, x1, y1, offset)


def _level(ch, k: float, x: int) -> float:
    slope, x1, y1, offset = ch
    return y1 + slope * (x - x1) + k * offset


def shape_at(prev_close, close, low, lvl_prev, lvl, tol: float = 0.0) -> str | None:
    """한 봉·한 선의 진입 형태 (7단계 자유도 I). 안 걸리면 None.

    **두 형태는 상호 배타다** — 직전 종가 조건이 정반대(`<=` vs `>`)라 한 봉·한
    선에서 둘 다 참일 수 없다. 그래서 우선순위 규칙이 없다. 이 배타성이 깨지면
    "가장 높은 발동 선" 규칙이 형태에 따라 다른 답을 내므로, selftest 가 격자로
    매번 확인한다.
    """
    # `tol` 은 **판정에 안 쓰는 보고용 손잡이**다(7단계 §5.4 "반대 방향"). 종가가
    # 선 아래 tol 안쪽에서 끝난 봉까지 세어, 15:50 에 선 위였다면 실제로 샀을
    # 트레이드가 몇 건인지 본다. 기본 0.0 이면 규칙 그대로다.
    if close <= lvl - tol * abs(close):
        return None
    if prev_close <= lvl_prev:
        return "breakout"          # I-a — 선 아래 있다가 위로 넘겨 마감
    if low <= lvl:
        return "support"           # I-b — 선 위에 있다가 선까지 눌렸다가 지켜냈다
    return None                    # 선 위에서 하루 종일 놀았다


def fill_px(outcome: str, op_x: float, lvl: float, fill: str) -> float:
    """청산 체결가 (7단계 자유도 J). `ideal` 은 6단계 — 선에 정확히 체결.

    `gap` 은 걸 수 있는 주문이다: 스톱은 갭이면 시가에 터지고(J-2, 불리),
    지정가는 유리한 갭을 그대로 받는다(J-3). 사다리는 가격 순이라 이 하네스의
    트레이드는 전부 롱 모양이다(손절이 진입 아래) — 그래서 min/max 가 각각
    "나쁜 쪽"과 "좋은 쪽"이다.
    """
    if fill != "gap":
        return lvl
    return min(op_x, lvl) if outcome == "loss" else max(op_x, lvl)


# ── 스캔 ────────────────────────────────────────────────────────
def scan(df: pd.DataFrame, seed: int | None = None,
         shapes: tuple[str, ...] = ("breakout",),
         fill: str = "ideal", entry: str = "close",
         tol: float = 0.0) -> list[dict]:
    """한 종목 전 구간. `seed` 가 있으면 위약 팔.

    진입: 종가가 채널선 하나를 **위로 넘겨 마감**. 손절 = 가격 기준 한 칸 아래
    선(없으면 진입 안 함 — 명세의 level 0 규칙을 방향 무관하게 옮긴 것),
    익절 = 한 칸 위 선(자유도 F). 종목당 동시 1포지션.

    **기본값 조합 `("breakout",)·ideal·close` 는 4·6단계와 글자 그대로 같은
    경로다.** 7단계 사전 등록이 연 자유도 셋만 인자로 나왔다:

      shapes  I-a `breakout` = 돌파 후 안착 (`cl[i]>선` 이고 `cl[i-1]<=선`)
              I-b `support`  = 지지 확인   (`cl[i]>선`, `cl[i-1]>선`, `lo[i]<=선`)
              직전 종가 조건이 정반대라 **상호 배타** — 우선순위 규칙이 없다.
      fill    J  `ideal` = 6단계(손절 정확히 s, 익절 정확히 tg)
                 `gap`   = 걸 수 있는 주문 — 손절 min(Open,s), 익절 max(Open,tg)
      entry   J-1 `close`(MOC) / `nextopen` = 다음 거래일 시가(참고 F)

    사전 등록: `docs/superpowers/specs/2026-09-05-bitgak-stage7-design.md` §2.
    """
    sw = find_swing_points(df, lookback=SWING_L)
    if len(sw) < 3:
        return []
    qual = _qualify(df, sw)
    hi, lo, cl = df["High"].values, df["Low"].values, df["Close"].values
    op = df["Open"].values
    n = len(df)
    # 채널은 풀이 바뀔 때만 바뀐다 — 매 봉 다시 고르지 않는다(속도가 아니라 정의).
    change = set(int(v) for v in np.r_[sw["idx"].values + SWING_L,
                                       qual[np.isfinite(qual)]] if v < n)

    trades: list[dict] = []
    ch = None
    i = max(MIN_LEN, POC_WIN + SWING_L)
    while i < n:
        if ch is None or i in change:
            rng = np.random.default_rng([seed, i]) if seed is not None else None
            t = _triple(sw, qual, i, rng)
            ch = _channel(t) if t else None
        if ch is None:
            i += 1
            continue
        # 가격 순 사다리 — 고고저는 offset 이 음수라 k 순서와 뒤집힌다.
        rung = sorted(LEVELS, key=lambda k: _level(ch, k, i))
        hit = None
        for pos, k in enumerate(rung):
            if pos == 0:                       # 아래에 선이 없다 = 손절 못 건다
                continue
            shape = shape_at(cl[i - 1], cl[i], lo[i],
                             _level(ch, k, i - 1), _level(ch, k, i), tol)
            if shape in shapes:                # 가장 **높은** 발동 선을 취한다
                hit = (pos, k, rung[pos - 1], shape)
        if hit is None:
            i += 1
            continue
        pos, k, k_stop, shape = hit
        step = k - k_stop                      # 사다리 한 칸(부호 포함)
        # 진입 봉 e — MOC 는 신호 봉 그 자리, 참고 F 는 다음 거래일 시가다.
        # 손절선은 **신호일(i) 것**을 쓴다(사전 등록 §8 참고 F 규약).
        if entry == "nextopen":
            if i + 1 > n - 1:                  # 다음 거래일이 없다 — 버린다
                i += 1
                continue
            e, px = i + 1, op[i + 1]
        else:
            e, px = i, cl[i]
        risk = px - _level(ch, k_stop, i)
        if risk <= 0 or risk / px < MIN_RISK_PCT:
            i += 1
            continue
        # `r_ideal` 은 같은 트레이드를 6단계 식(정확히 s / 정확히 tg)으로 센 값.
        # 갭이 R 을 얼마나 먹었는지 **짝지어** 보고하려고 같이 들고 간다
        # (사전 등록 §8 "같이 보고하는 것"). 체결 규약은 진입 결정을 안 바꾸므로
        # 두 값은 언제나 같은 트레이드의 두 청산가다.
        out, r, ri, x = "timeout", 0.0, 0.0, min(e + HOLD, n - 1)
        for x in range(e + 1, min(e + HOLD, n - 1) + 1):
            s, tg = _level(ch, k_stop, x), _level(ch, k + step, x)
            if lo[x] <= s:                     # 같은 봉에 둘 다면 손절(보수적)
                out, ri = "loss", (s - px) / risk
                r = (fill_px("loss", op[x], s, fill) - px) / risk
                break
            if hi[x] >= tg:
                out, ri = "win", (tg - px) / risk
                r = (fill_px("win", op[x], tg, fill) - px) / risk
                break
        if out == "timeout":
            r = ri = (cl[x] - px) / risk       # 타임아웃은 MOC — 갭이 없다
        trades.append({"idx": i, "exit": x, "outcome": out, "r": float(r),
                       "r_ideal": float(ri),
                       "risk_pct": float(risk / px), "hold": x - e,
                       "shape": shape,
                       # 여유 — 종가가 발동선을 얼마나 넘겼나. 7단계 「시점」
                       # 판정선의 한쪽 조각이다(사전 등록 §5.4). 새 자료가 아니라
                       # 저장 패널에서 그냥 나오는 값이라 여기서 같이 들고 간다.
                       "margin": float((cl[i] - _level(ch, k, i)) / cl[i])})
        i = x + 1                              # 보유 중엔 새 진입 무시
    return trades


def _counts_only(trades: list[dict]) -> dict:
    """**진짜 팔에서 여기까지만 나간다.** r 은 집계하지 않는다 — 눈가림."""
    return {"n": len(trades),
            "resolved": sum(t["outcome"] in ("win", "loss") for t in trades),
            "timeout": sum(t["outcome"] == "timeout" for t in trades),
            # 결과가 아니라 기하학이다 — 채널 한 칸이 주가의 몇 %인가(사이징),
            # 몇 봉 만에 결판나는가(보유창 40봉이 적절한가).
            "risk": [t["risk_pct"] for t in trades],
            "hold": [t["hold"] for t in trades]}


# ── 통계 ────────────────────────────────────────────────────────
def _boot_se(by_tk: dict, reps=REPS, seed=SEED) -> float:
    """평균 R 의 종목 부트스트랩 SE. 날짜 군집은 안 잡으므로 MDE 는 하한이다."""
    keys = list(by_tk)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        pick = [by_tk[keys[j]] for j in rng.integers(len(keys), size=len(keys))]
        c = np.concatenate(pick)
        if c.size:
            vals.append(c.mean())
    return float(np.std(vals, ddof=1)) if len(vals) > 1 else float("nan")


def mde_r(placebo_by_tk: dict, n_real: int) -> float:
    """진짜 팔이 위약과 갈리려면 최소 몇 R 이어야 하는가 (양측 5% · 검출력 80%).

    **점추정을 반환하지 않는다.** 분산은 위약 팔에서만 채우고 진짜 팔에서는
    표본 수 하나만 받는다 — `measure_pead.mde_pp` 와 같은 눈가림 장치다.
    진짜 팔의 SE 는 같은 군집 구조를 가정해 `sqrt(n_위약/n_진짜)` 로 옮긴다
    (selftest ④ 가 이 축척을 확인한다).
    """
    n_p = sum(v.size for v in placebo_by_tk.values())
    if not n_p or not n_real:
        return float("nan")
    se_p = _boot_se(placebo_by_tk)
    se_r = se_p * np.sqrt(n_p / n_real)
    return float(2.8 * np.sqrt(se_p ** 2 + se_r ** 2))


# ── 실행 ────────────────────────────────────────────────────────
def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _job(args):
    tk, df, mask = args
    # `hash()` 는 프로세스마다 씨앗이 달라 워커를 나누면 재현이 안 된다 — crc32 로 고정.
    seed = zlib.crc32(tk.encode()) % 10 ** 6
    keep = (lambda ts: ts) if mask is None else (lambda ts: [t for t in ts if mask[t["idx"]]])
    return (tk, _counts_only(keep(scan(df))),
            np.array([t["r"] for t in keep(scan(df, seed=seed))]))


def member_masks(universe: Path, index_by_tk: dict, start) -> dict:
    """{종목: 진입 허용 마스크} — 그 달에 유니버스 구성종목이었던 날만 True.

    합집합(한 번이라도 상위 1,000 이었던 종목)의 **전 이력**을 쓰면 미래를 본다:
    2024년에 상위 1,000 에 들어온 종목은 "그 사이에 커졌다"는 이유로 뽑힌 것이라,
    그 종목의 2018년 트레이드를 세는 순간 승자만 골라 센 게 된다.

    스캔은 전 이력 위에서 돈다(매물대 252봉 예열이 창 앞을 먹는다). 거르는 건
    **진입일**뿐이다 — 규칙 A~F 는 손도 안 댄다.
    """
    u = pd.read_parquet(universe)
    udates = pd.DatetimeIndex(sorted(u["date"].unique()))
    flags = {}
    for aid, g in u.groupby("asset_id", sort=False):
        f = np.zeros(len(udates), dtype=bool)
        f[udates.get_indexer(pd.DatetimeIndex(g["date"].unique()))] = True
        flags[aid] = f
    out = {}
    for tk, idx in index_by_tk.items():
        f = flags.get(tk)
        if f is None:
            continue
        pos = np.searchsorted(udates.to_numpy(), idx.to_numpy(), side="right") - 1
        m = (pos >= 0) & f[np.clip(pos, 0, len(udates) - 1)] & np.asarray(idx >= start)
        if m.any():
            out[tk] = m
    return out


def crypto_panel() -> pd.DataFrame:
    if CRYPTO_PANEL.exists():
        return pd.read_parquet(CRYPTO_PANEL)
    import yfinance as yf
    got = yf.download(CRYPTO, start="2010-01-01", auto_adjust=True, progress=False)
    got = got[[c for c in got.columns if c[0] in FIELDS]]
    got.to_parquet(CRYPTO_PANEL)
    return got


def table(panel: pd.DataFrame, label: str, workers: int,
          universe: Path = None, start=None) -> None:
    frames = {}
    for tk in sorted({t for _, t in panel.columns}):
        df = _ohlcv(panel, tk)
        if len(df) >= MIN_LEN:
            frames[tk] = df
    masks = ({} if universe is None
             else member_masks(universe, {t: d.index for t, d in frames.items()}, start))
    tasks = [(tk, df, None if universe is None else masks.get(tk))
             for tk, df in frames.items() if universe is None or tk in masks]
    if not tasks:
        print(f"\n### {label} — 쓸 수 있는 종목이 없다")
        return
    if universe is None:
        span_lo = min(d.index[0] for _, d, _ in tasks)
        span_hi = max(d.index[-1] for _, d, _ in tasks)
        years = sum(len(d) for _, d, _ in tasks) / 252.0
    else:   # 노출은 이력 전체가 아니라 **구성종목이었던 날**만 센다
        span_lo = min(d.index[m][0] for _, d, m in tasks)
        span_hi = max(d.index[m][-1] for _, d, m in tasks)
        years = sum(int(m.sum()) for _, _, m in tasks) / 252.0

    real = {"n": 0, "resolved": 0, "timeout": 0}
    risk, hold, hit, placebo = [], [], 0, {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for tk, c, pr in pool.map(_job, tasks):
            for k in real:
                real[k] += c[k]
            risk += c["risk"]
            hold += c["hold"]
            hit += bool(c["n"])
            if pr.size:
                placebo[tk] = pr

    n_p = sum(v.size for v in placebo.values())
    mde = mde_r(placebo, real["resolved"])
    print(f"\n### {label} — {len(tasks)}종목 · {span_lo.date()}~{span_hi.date()} "
          f"(종목-연 합계 {years:.0f})")
    print(f"  진짜 작도 발동   {real['n']:6d}건 "
          f"(결판 {real['resolved']}, 타임아웃 {real['timeout']}) "
          f"= 종목-연당 {real['n'] / years:.2f}회")
    print(f"  위약(무작위 3점) {n_p:6d}건 — 분산은 여기서만 채운다")
    print(f"  MDE  {mde:.3f}R   (자 {YARDSTICK:+.2f}R → "
          f"{'통과: 이 자로 잴 수 있다' if mde < YARDSTICK else '실패: 못 잰다'})")
    if risk:
        print(f"  채널 한 칸  중위 {np.median(risk) * 100:.2f}% (주가 대비) · "
              f"결판까지 중위 {np.median(hold):.0f}봉 · 발동 종목 {hit}/{len(tasks)}")
    print("  ※ 진짜 팔의 기대값·승률은 계산도 출력도 안 했다(눈가림).")


def run() -> None:
    args = [a for a in sys.argv[1:] if a != "largecap"]
    workers = int(args[0]) if args else max((os.cpu_count() or 2) - 1, 1)
    print(f"워커 {workers} · 손절/익절 한 칸 · 보유 {HOLD}봉 · 스윙 L={SWING_L}", flush=True)
    if "largecap" in sys.argv[1:]:      # 5단계 — 편향 없는 패널만 잰다(크립토는 닫혔다)
        table(pd.read_parquet(LARGECAP_PANEL), "미국주식 (PIT 대형주 · 5단계)",
              workers, universe=LARGECAP_UNIVERSE, start=LARGECAP_START)
        return
    table(pd.read_parquet(US_PANEL), "미국주식 (대형주 패널)", workers)
    table(crypto_panel(), "크립토 (yfinance 일봉)", workers)


# ── selftest ────────────────────────────────────────────────────
def _synth(n=900, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.02, n)))
    h, l = c * (1 + abs(rng.normal(0, 0.01, n))), c * (1 - abs(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"Open": c, "High": h, "Low": l, "Close": c,
                         "Volume": rng.integers(1e5, 1e6, n).astype(float)},
                        index=pd.bdate_range("2018-01-01", periods=n))


def selftest() -> None:
    # ① 피보나치 채널 산수 — level 0 은 P1·P2 를, level 1 은 P3 를 지난다.
    ch = _channel((10, 100.0, 60, 130.0, 30, 145.0))
    assert abs(_level(ch, 0, 10) - 100) < 1e-9 and abs(_level(ch, 0, 60) - 130) < 1e-9
    assert abs(_level(ch, 1, 30) - 145) < 1e-9
    assert abs(_level(ch, 0.5, 30) - 128.5) < 1e-9        # base(30)=112, offset=33

    # ② 고고저(offset 음수)에서도 손절이 진입 아래다 — 가격 순 사다리가 하는 일.
    down = _channel((10, 150.0, 60, 120.0, 30, 100.0))
    assert down[3] < 0
    rung = sorted(LEVELS, key=lambda k: _level(down, k, 70))
    assert _level(down, rung[0], 70) < _level(down, rung[1], 70)
    assert rung[0] == 5.5 and rung[-1] == 0.0        # k 순서가 뒤집혀 있다

    # ③ 미래를 안 본다 — 앞부분만 준 df 와 전체 df 의 트레이드가 같아야 한다.
    df = _synth()
    m = 700
    full = [t for t in scan(df) if t["exit"] < m - 1]
    cut = [t for t in scan(df.iloc[:m]) if t["exit"] < m - 1]
    assert full == cut, (len(full), len(cut))
    assert full, "합성 데이터에서 트레이드가 하나도 안 나왔다 — 자가 안 물린 것"

    # ④ MDE 축척: 표본이 4배면 SE 는 절반, MDE 는 sqrt((1+1/4))/sqrt(2) 배.
    rng = np.random.default_rng(1)
    pb = {f"T{i}": rng.normal(0, 1, 50) for i in range(40)}
    n_p = sum(v.size for v in pb.values())
    a, b = mde_r(pb, n_p), mde_r(pb, 4 * n_p)
    assert abs(b / a - np.sqrt(1.25 / 2.0)) < 1e-9, (a, b)

    # ⑤ 손절이 걸린 트레이드의 r 은 −1 근처다(선이 기울어서 정확히 −1 은 아니다).
    losses = [t["r"] for t in scan(_synth(seed=3)) if t["outcome"] == "loss"]
    assert losses and -2.0 < float(np.mean(losses)) < -0.5, losses[:5]

    # ⑥ 구성종목 마스크 — 기준일에 든 달만 True, 창 시작 앞은 무조건 False.
    import tempfile
    u = pd.DataFrame({"date": pd.to_datetime(["2017-08-31", "2017-09-30",
                                              "2017-10-31", "2017-08-31"]),
                      "asset_id": ["1:A", "1:A", "1:A", "2:B"]})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "u.parquet"
        u.to_parquet(p)
        idx = pd.DatetimeIndex(["2017-08-15", "2017-09-15", "2017-10-15",
                                "2017-11-15", "2017-12-15"])
        mk = member_masks(p, {"1:A": idx, "2:B": idx, "3:C": idx},
                          pd.Timestamp("2017-09-01"))
    assert "3:C" not in mk                       # 유니버스에 없는 종목
    # 마지막 기준일은 **앞으로 이어진다** — 2026-07-31 구성이 패널 끝(8월 중순)까지
    # 간다는 뜻이고, 그게 월별 리밸런스의 정의다. 죽은 종목은 그 앞 기준일에서
    # 이미 빠지므로 여기 안 걸린다.
    assert list(mk["1:A"]) == [False, True, True, True, True]    # 창 앞만 False
    assert list(mk["2:B"]) == [False, True, False, False, False]  # 한 달만 구성종목

    print(f"selftest OK — 합성 {len(full)}트레이드, 손절 평균 "
          f"{np.mean(losses):+.2f}R · 마스크 OK")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        run()
