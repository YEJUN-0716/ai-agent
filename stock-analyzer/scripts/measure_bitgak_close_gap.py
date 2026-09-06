#!/usr/bin/env python
"""빗각 7단계 — **자 검사(§5.3)와 「시점」 판정선(§5.4).**

    python scripts/measure_bitgak_close_gap.py gate    # 도구 게이트 — 패널의 시가·저가 칸
    python scripts/measure_bitgak_close_gap.py         # δ 와 거짓 진입 비율

사전 등록: `docs/superpowers/specs/2026-09-05-bitgak-stage7-design.md`

## 이 파일이 묻는 것 하나 — 종가 진입이 15:50 에 걸리는가

MOC 는 공식 종가에 그대로 체결된다. **가격은 문제가 아니다.** 문제는 시점이다:
MOC 마감이 15:50 ET 이라 그 시각에 "종가가 선 위에서 마감할 것"을 미리 걸어야
한다. 15:50 엔 참이었는데 종가에 거짓이 되면 규칙이 안 센 트레이드를 실제로
사게 된다 — **거짓 진입**이다.

    거짓 진입 비율 = 여유가 δ 중위값보다 작은 발동의 비율
      여유  (cl[i] − L_k(i)) / cl[i]      ← 저장 패널. 발동 전부. 새 자료 0
      δ     |P(15:50) − 마지막 프린트| / 마지막 프린트   ← Alpaca 분봉, 1,000 종목-일

**δ 의 두 끝을 둘 다 IEX 프린트로 잡는다.** 15:50 값은 IEX 프린트인데 종가만
통합호가(패널)에서 가져오면, 재는 게 막판 이동이 아니라 **두 자의 차이**가 된다.
2026-08-11 에 이 저장소가 정확히 그 함정을 밟았다 — 체결가는 멀쩡했고 기준선만
엉뚱한 곳에 있었다(`paper-slippage-measured`). 그래서 사전 등록 §9 가 "δ 는 IEX
마지막 프린트다"라고 미리 적어 뒀다. 조정 계수도 같은 날 안에서 상쇄된다.

**호가(quote)는 안 쓴다.** 여기서 쓰는 건 체결 프린트(trade)다 — 무료 IEX 호가가
못 쓸 물건인 것과 별개다(사전 등록 §5.2).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from measure_bitgak import CACHE, _cache_key, _liq_mask  # noqa: E402
from pilot_bitgak_power import (  # noqa: E402
    LARGECAP_PANEL, LARGECAP_START, LARGECAP_UNIVERSE, MIN_LEN, _ohlcv, member_masks,
)

SEED = 20260905                 # 사전 등록 §5.4 — 봉인
N_SAMPLE = 1000                 # 종목-일
LIQ, MIN_PX = 5e6, 5.0          # 판정 행 유니버스 (§6)
GATE_TK = ["AAPL", "MSFT", "JPM"]
GATE_OPEN_BP, GATE_LOW_BP = 100.0, 300.0     # §5.3
DELTA_CACHE = Path("data/bitgak_delta_1550.json")   # API 재호출을 막는다(gitignore)
# 무료 IEX 분봉의 벽 — 실측(2020-07-15 0봉 / 2020-08-14 18봉). 사전 등록이 못 본
# 자리다: 표본은 봉인된 대로 9년에서 뽑되, **δ 가 실제로 나온 건 이 날 이후뿐**이라
# 그 시대의 발동만 따로 세서 같이 보고한다(판정선은 안 고친다).
IEX_WALL = pd.Timestamp("2020-08-01").toordinal()
VERDICT_OPTS = {"shapes": ("breakout", "support"), "fill": "gap", "liq": 5e6}


# ── §5.3 도구 게이트 ────────────────────────────────────────────
def gate(panel: pd.DataFrame) -> bool:
    """AAPL·MSFT·JPM 의 시가 갭과 저가 깊이 중위값. 하나라도 넘으면 진행 안 함.

    성과 문턱이 아니라 **자 검사**다: 100bp 는 "조정 사고나 칸이 밀린 자료"를
    잡는 값이고, 저가 쪽 0 은 "Low 칸이 안 채워졌다"를 잡는 값이다. 지지 확인이
    저가를 처음으로 판정에 쓰므로 이번에 같이 검사한다.
    """
    tks = {t.split(":")[-1]: t for _, t in panel.columns}
    ok = True
    print(f"### 도구 게이트 (§5.3) — {LARGECAP_START.date()}~ 저장 패널")
    for sym in GATE_TK:
        d = _ohlcv(panel, tks[sym])
        d = d[d.index >= LARGECAP_START]
        gap = (d["Open"] - d["Close"].shift(1)).abs() / d["Close"]
        deep = (d["Close"] - d["Low"]) / d["Close"]
        g, w = float(gap.median() * 1e4), float(deep.median() * 1e4)
        good = g < GATE_OPEN_BP and 0.0 < w < GATE_LOW_BP
        ok &= good
        print(f"  {sym:5s} n={len(d):5d}  |시가-전일종가|/종가 중위 {g:6.1f}bp "
              f"(< {GATE_OPEN_BP:.0f})   (종가-저가)/종가 중위 {w:6.1f}bp "
              f"(0 < . < {GATE_LOW_BP:.0f})  -> {'O' if good else 'X'}")
    print(f"  ▶ 게이트 {'통과 — 진행한다' if ok else '실패 — 패널을 고치고 다시 온다'}")
    return ok


# ── 판정 행 유니버스 ────────────────────────────────────────────
def eligible(panel: pd.DataFrame) -> list[tuple[str, pd.Timestamp]]:
    """판정 행이 진입할 수 있었던 (종목, 날짜) 전부 — 구성종목 마스크 ∧ 유동성 컷.

    **발동 여부와 무관하게** 뽑는다(사전 등록 §5.4). 발동일만 뽑으면 막판에
    움직인 날로 쏠린다 — δ 가 그 쏠림을 그대로 물려받는다.
    """
    frames = {tk: d for tk in sorted({t for _, t in panel.columns})
              if len(d := _ohlcv(panel, tk)) >= MIN_LEN}
    masks = member_masks(LARGECAP_UNIVERSE, {t: d.index for t, d in frames.items()},
                         LARGECAP_START)
    out = []
    for tk, m in masks.items():
        d = frames[tk]
        keep = m & _liq_mask(d, LIQ, MIN_PX)
        out += [(tk, ts) for ts in d.index[keep]]
    return out


# ── §5.4 δ — 막판 이동 ──────────────────────────────────────────
def _last_at_or_before(df: pd.DataFrame, when) -> float | None:
    sub = df.loc[:when]
    return float(sub["Close"].iloc[-1]) if len(sub) else None


def deltas(pairs: list[tuple[str, pd.Timestamp]]) -> dict:
    """{"종목|날짜": δ} — Alpaca 분봉(IEX). 받은 건 캐시에 남겨 다시 안 부른다."""
    from modules.alpaca_data import get_bars

    if not os.environ.get("ALPACA_API_KEY"):
        # 키가 없으면 매 날짜가 예외로 떨어지고 표본이 통째로 None 이 된다 —
        # 실패가 아니라 **빈 표본으로 보이는 정지**다. 여기서 멈춘다.
        sys.exit("ALPACA_API_KEY 가 없습니다. `set -a && . ./.env && set +a` 먼저.")

    got = (json.loads(DELTA_CACHE.read_text(encoding="utf-8"))
           if DELTA_CACHE.exists() else {})
    by_date: dict[pd.Timestamp, list[str]] = {}
    for tk, ts in pairs:
        if f"{tk}|{ts.date()}" not in got:
            by_date.setdefault(ts, []).append(tk)
    print(f"  δ 캐시 {len(got)}건 · 새로 받을 날짜 {len(by_date)}개", flush=True)

    for n, (ts, tks) in enumerate(sorted(by_date.items()), 1):
        # 15:40~16:05 ET 를 UTC 로. 서머타임은 tz 변환에 맡긴다 — 손으로 4/5시간을
        # 더하면 3월과 11월에 한 시간씩 틀린다.
        et = ts.tz_localize("America/New_York")
        to_utc = lambda d: (et + d).tz_convert("UTC").tz_localize(None)
        start, end = to_utc(timedelta(hours=15, minutes=40)), to_utc(timedelta(hours=16, minutes=5))
        cut, close_t = to_utc(timedelta(hours=15, minutes=50)), to_utc(timedelta(hours=16))
        syms = {tk.split(":")[-1]: tk for tk in tks}
        try:
            bars = get_bars(list(syms), timeframe="1Min", start=start, end=end)
        except Exception as e:                       # 하루가 빠져도 표본만 준다
            print(f"    [{ts.date()}] {type(e).__name__}: {e}", flush=True)
            bars = {}
        for sym, tk in syms.items():
            d = bars.get(sym)
            key = f"{tk}|{ts.date()}"
            if d is None or d.empty:
                got[key] = None                      # IEX 에 그날 프린트가 없다
                continue
            p50 = _last_at_or_before(d, cut)         # 15:50 까지의 마지막 프린트
            pcl = _last_at_or_before(d, close_t)     # 16:00 까지의 마지막 프린트
            got[key] = (abs(p50 - pcl) / pcl if p50 and pcl and pcl > 0 else None)
        if n % 25 == 0 or n == len(by_date):
            DELTA_CACHE.write_text(json.dumps(got), encoding="utf-8")
            print(f"    ..{n}/{len(by_date)}일", flush=True)
    DELTA_CACHE.write_text(json.dumps(got), encoding="utf-8")
    return got


def margins() -> dict:
    """판정 행 스캔의 여유 분포 — 캐시에서 읽는다(`measure_bitgak.py` 가 채운다)."""
    key = _cache_key("largecap", **VERDICT_OPTS)
    cached = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if key not in cached:
        sys.exit(f"판정 행 스캔이 캐시에 없다 — 먼저 돌려라:\n"
                 f"  python scripts/measure_bitgak.py largecap "
                 f"--shapes both --fill gap --liq 5e6\n  (키 {key})")
    real = cached[key][0]
    out: dict[str, list] = {"breakout": [], "support": [], "recent": []}
    for rows in real.values():
        for row in rows:
            out[row[5]].append(row[7])
            if row[1] >= IEX_WALL:          # δ 를 실제로 잰 시대의 발동만
                out["recent"].append(row[7])
    return out


# ── 지연 결정 (2026-09-06) — cut 을 앞으로 옮기면 δ 가 얼마나 커지나 ──────
# 왜: 무료 플랜의 sip 은 최근 15분을 안 준다(`modules/alpaca_data` 머리말). 러너가
# 15:50 에 보는 값이 실제로는 ~15:34 값이 된다. **자는 그대로 두고 자리만 옮겨서**
# δ 와 거짓 진입이 얼마가 되는지 잰다 — 사전 등록 §7.2 갈래가 그 비율로 정해진다.
DELAY_CACHE = Path("data/bitgak_delta_cuts.json")     # gitignore
DEFAULT_CUTS = ("15:50", "15:40", "15:34", "15:30")


def _td(hhmm: str) -> timedelta:
    h, m = hhmm.split(":")
    return timedelta(hours=int(h), minutes=int(m))


def deltas_multi(pairs: list[tuple[str, pd.Timestamp]],
                 cuts: tuple[str, ...]) -> dict:
    """{"종목|날짜": {cut: δ}} — 분봉 한 번으로 여러 cut 을 잰다.

    **산식은 `deltas()` 그대로다.** cut 다리는 `[cut−10분, cut]` 의 마지막
    프린트, 종가 다리는 언제나 `[15:40, 16:00]` 의 마지막 프린트 — 종가 다리를
    cut 에 따라 움직이면 재는 게 "막판 이동"이 아니라 두 창의 차이가 된다.
    cut=15:50 행이 기존 측정(δ 중위 14.9bp)을 재현하는지가 **자 검사**다.
    """
    from modules.alpaca_data import get_bars

    if not os.environ.get("ALPACA_API_KEY"):
        sys.exit("ALPACA_API_KEY 가 없습니다. `set -a && . ./.env && set +a` 먼저.")

    got = (json.loads(DELAY_CACHE.read_text(encoding="utf-8"))
           if DELAY_CACHE.exists() else {})
    by_date: dict[pd.Timestamp, list[str]] = {}
    for tk, ts in pairs:
        k = f"{tk}|{ts.date()}"
        if k not in got or any(c not in got[k] for c in cuts):
            by_date.setdefault(ts, []).append(tk)
    print(f"  δ 캐시 {len(got)}건 · 새로 받을 날짜 {len(by_date)}개", flush=True)

    first = min(_td(c) for c in cuts) - timedelta(minutes=10)
    for n, (ts, tks) in enumerate(sorted(by_date.items()), 1):
        et = ts.tz_localize("America/New_York")
        to_utc = lambda d: (et + d).tz_convert("UTC").tz_localize(None)
        start, end = to_utc(first), to_utc(timedelta(hours=16, minutes=5))
        syms = {tk.split(":")[-1]: tk for tk in tks}
        try:
            bars = get_bars(list(syms), timeframe="1Min", start=start, end=end)
        except Exception as e:                       # 하루가 빠져도 표본만 준다
            print(f"    [{ts.date()}] {type(e).__name__}: {e}", flush=True)
            bars = {}
        for sym, tk in syms.items():
            d = bars.get(sym)
            key = f"{tk}|{ts.date()}"
            row = got.setdefault(key, {})
            if d is None or d.empty:
                row.update({c: None for c in cuts})
                continue
            # 종가 다리 — 모든 cut 이 같은 것을 쓴다(기존 측정과 같은 창)
            pcl = _last_at_or_before(d.loc[to_utc(timedelta(hours=15, minutes=40)):],
                                     to_utc(timedelta(hours=16)))
            for c in cuts:
                cut = to_utc(_td(c))
                p = _last_at_or_before(d.loc[cut - timedelta(minutes=10):], cut)
                row[c] = (abs(p - pcl) / pcl if p and pcl and pcl > 0 else None)
        if n % 25 == 0 or n == len(by_date):
            DELAY_CACHE.write_text(json.dumps(got), encoding="utf-8")
            print(f"    ..{n}/{len(by_date)}일", flush=True)
    DELAY_CACHE.write_text(json.dumps(got), encoding="utf-8")
    return got


def run_delay(cuts: tuple[str, ...] = DEFAULT_CUTS) -> None:
    """cut 별 δ 와 거짓 진입 비율. **표본·시드·여유 분포는 7단계 그대로.**"""
    panel = pd.read_parquet(LARGECAP_PANEL)
    if not gate(panel):
        sys.exit(1)

    pairs = eligible(panel)
    rng = np.random.default_rng(SEED)               # 봉인 — 같은 1,000건
    pick = [pairs[j] for j in rng.choice(len(pairs), size=N_SAMPLE, replace=False)]
    print(f"\n### δ vs 결정 시각 — {len(pairs):,} 종목-일에서 {N_SAMPLE}건 "
          f"(시드 {SEED}, 7단계와 같은 표본)")
    got = deltas_multi(pick, cuts)

    m = margins()
    allm = np.asarray(m["breakout"] + m["support"])
    recent = np.asarray(m["recent"])
    print(f"\n### 결과 — 여유 중위 {np.median(allm) * 1e4:.1f}bp, 발동 {allm.size:,}건")
    print(f"{'cut':>6} {'표본':>9} {'δ중위':>9} {'δ90':>9} "
          f"{'거짓진입':>9} {'(2020-08~)':>11}  갈래")
    for c in cuts:
        vals = [v for tk, ts in pick
                if (v := (got.get(f"{tk}|{ts.date()}") or {}).get(c)) is not None]
        if len(vals) < N_SAMPLE // 2:
            print(f"{c:>6} {len(vals):>4}/{N_SAMPLE}  표본 절반 미달 — 분포가 아니라 사고다")
            continue
        d50, d90 = float(np.median(vals)), float(np.percentile(vals, 90))
        rate, rate_r = float((allm < d50).mean()), float((recent < d50).mean())
        branch = ("유지" if rate <= 0.10 else
                  "유지 + 참고 F 대조" if rate <= 0.25 else "참고 F 로 이동 (죽음)")
        print(f"{c:>6} {len(vals):>4}/{N_SAMPLE} {d50 * 1e4:8.1f}bp {d90 * 1e4:8.1f}bp "
              f"{rate * 100:8.1f}% {rate_r * 100:10.1f}%  {branch}")
    print("\n갈래는 사전 등록 §7.2. **25% 초과는 참고 F 로 옮기라는 뜻인데 참고 F 가"
          "\n7단계에서 죽었다(② 하한 −0.000) — 그 칸에 떨어지면 노선이 닫힌다.**")


def run() -> None:
    panel = pd.read_parquet(LARGECAP_PANEL)
    if not gate(panel):
        sys.exit(1)

    pairs = eligible(panel)
    rng = np.random.default_rng(SEED)
    pick = [pairs[j] for j in rng.choice(len(pairs), size=N_SAMPLE, replace=False)]
    print(f"\n### δ — 판정 행 유니버스 {len(pairs):,} 종목-일에서 "
          f"{N_SAMPLE}건 (시드 {SEED})")
    got = deltas(pick)
    vals = [v for tk, ts in pick if (v := got.get(f"{tk}|{ts.date()}")) is not None]
    if len(vals) < N_SAMPLE // 2:
        sys.exit(f"δ 표본이 {len(vals)}/{N_SAMPLE} 뿐이다 — 절반도 못 받았으면 "
                 f"이건 분포가 아니라 사고다. 로그를 보고 다시 온다.")
    d50, d90 = float(np.median(vals)), float(np.percentile(vals, 90))
    print(f"  받은 표본 {len(vals)}/{N_SAMPLE}  ({len(vals) / N_SAMPLE * 100:.1f}%)")
    print(f"  δ 중위 {d50 * 1e4:.1f}bp · 90분위 {d90 * 1e4:.1f}bp  "
          f"(판정에는 중위만 쓴다)")

    m = margins()
    allm = m["breakout"] + m["support"]
    print(f"\n### 거짓 진입 비율 (§5.4) — 여유 < δ 중위 {d50 * 1e4:.1f}bp")
    for name, arr in (("I-a 돌파", m["breakout"]), ("I-b 지지", m["support"]),
                      ("합침 = 판정선", allm),
                      ("참고 2020-08~", m["recent"])):
        if not arr:
            continue
        a = np.asarray(arr)
        print(f"  {name:12s} n={a.size:6d}  여유 중위 {np.median(a) * 1e4:7.1f}bp  "
              f"거짓 진입 {float((a < d50).mean()) * 100:5.1f}%")
    rate = float((np.asarray(allm) < d50).mean())
    branch = ("10% 이하 → 종가 진입 유지, 판정 행 그대로" if rate <= 0.10 else
              "10~25% → 판정은 그대로, 참고 F 를 나란히 놓고 보류 판단"
              if rate <= 0.25 else "25% 초과 → 판정을 참고 F 로 옮긴다")
    print(f"  ▶ {rate * 100:.1f}% — {branch}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "gate":
        sys.exit(0 if gate(pd.read_parquet(LARGECAP_PANEL)) else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "delay":
        cuts = tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else DEFAULT_CUTS
        sys.exit(run_delay(cuts))
    run()
