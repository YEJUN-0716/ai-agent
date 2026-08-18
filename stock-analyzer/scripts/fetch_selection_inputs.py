#!/usr/bin/env python
"""선택 계층 재생에 필요한 입력 3개를 받아 온다 (네트워크 有, 한 번만).

    python scripts/fetch_selection_inputs.py

사전 등록: `docs/superpowers/specs/2026-08-18-selection-layer-design.md` §3

## 왜 저장 패널(price_panel_v1)에 안 채우고 새 파일을 만드나

유니버스 61종목 중 저장 패널엔 46종목뿐이다. 빠진 15에 **AAL·SYK 가 있고
지금 대기 주문에 들어 있다** — 안 메우면 표본이 한 방향으로 치우친다.

그런데 그 15개를 price_panel_v1 에 **덧붙이면** 안 된다. 그 파일 전체를 도는
측정들(`measure_trade_plan_oos`, `measure_entry_rule`)이 279종목 기준으로
봉인돼 있어, 294종목이 되면 같은 명령이 다른 숫자를 뱉는다. 봉인된 입력을
조용히 바꾸는 짓이다.

대신 61종목을 **한 번에 새로 받아** 이 측정 전용 패널로 둔다. 46 은 옛 판,
15 는 새 판으로 섞는 것보다 낫다 — 수정주가 리비전이 한 판으로 통일된다.

## 받는 것

1. `data/selection_panel.parquet`  61종목 OHLCV (2020-03-31 ~ 2026-08-14)
2. `data/selection_regime.parquet` SPY 200MA 비율 + VIX → 날짜별 레짐
   (`factor_engine.get_market_regime` 과 **같은 문턱**. 200MA 를 첫날부터
   채우려고 1년 앞서 받는다.)
3. `data/selection_sectors.json`   섹터 한도용 티커→섹터
   (`paper_trade_runner_toss._get_sector` 와 같은 출처: yfinance info)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

from modules.price_panel import load_panel  # noqa: E402

PANEL_START = pd.Timestamp("2020-03-31")   # price_panel_v1 첫날과 같게
PANEL_END = pd.Timestamp("2026-08-14")     # price_panel_v1 마지막날과 같게
REGIME_START = PANEL_START - pd.Timedelta(days=400)   # 200MA 워밍업

OUT_PANEL = Path("data/selection_panel.parquet")
OUT_REGIME = Path("data/selection_regime.parquet")
OUT_SECTORS = Path("data/selection_sectors.json")
REF_PANEL = Path("data/price_panel_v1.parquet")

# 러너 워크플로가 실제로 넘기는 5개 프리셋 (.github/workflows/paper-trade-us.yml)
PRESETS = ["미국 중소형 30", "미국 금융주 15", "미국 가치배당주 20",
           "미국 AI·빅테크 15", "미국 헬스케어 15"]


def universe() -> list[str]:
    """러너 프리셋에서 고유 티커. 러너를 import 하면 부작용이 없지만,
    프리셋 딕셔너리만 필요하므로 소스에서 그 블록만 떼어 읽는다."""
    src = Path("paper_trade_runner_toss.py").read_text(encoding="utf-8")
    m = re.search(r"UNIVERSE_PRESETS = \{.*?\n\}\n", src, re.S)
    ns: dict = {}
    exec(m.group(0), ns)  # noqa: S102 - 우리 저장소 소스다
    presets = ns["UNIVERSE_PRESETS"]
    out: list[str] = []
    for name in PRESETS:
        out += presets[name]
    return sorted(dict.fromkeys(out))


def fetch_regime() -> pd.DataFrame:
    """SPY 200MA 비율과 VIX 종가 → 날짜별 레짐.

    문턱은 `factor_engine.get_market_regime` 과 같다:
      bull  SPY ≥ 200MA and VIX < 25 / bear  SPY < 200MA×0.97 or VIX > 30
    """
    raw = yf.download(["SPY", "^VIX"], start=REGIME_START, end=PANEL_END + pd.Timedelta(days=1),
                      progress=False, auto_adjust=True, group_by="column")
    close = raw["Close"]
    spy, vix = close["SPY"].dropna(), close["^VIX"].dropna()
    ratio = spy / spy.rolling(200).mean()
    df = pd.DataFrame({"spy_ratio": ratio, "vix": vix}).dropna()
    df["regime"] = "neutral"
    df.loc[(df.spy_ratio >= 1.0) & (df.vix < 25), "regime"] = "bull"
    df.loc[(df.spy_ratio < 0.97) | (df.vix > 30), "regime"] = "bear"
    return df


def fetch_sectors(tickers: list[str]) -> dict:
    """티커→섹터. 러너의 `_get_sector` 와 같은 출처(yfinance info)."""
    out = {}
    for i, tk in enumerate(tickers, 1):
        try:
            out[tk] = yf.Ticker(tk).info.get("sector") or "Unknown"
        except Exception as e:
            print(f"  [섹터 실패] {tk}: {e}")
            out[tk] = "Unknown"
        if i % 10 == 0:
            print(f"  섹터 {i}/{len(tickers)}", flush=True)
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    tickers = universe()
    print(f"유니버스 {len(tickers)}종목 — 패널 받는 중 "
          f"({PANEL_START.date()} ~ {PANEL_END.date()})", flush=True)
    # yfinance 의 end 는 배타적이다 — PANEL_END 그대로 주면 그 날 봉이 빠진다.
    load_panel(tickers, PANEL_START, PANEL_END + pd.Timedelta(days=1),
               cache_path=str(OUT_PANEL))

    panel = pd.read_parquet(OUT_PANEL)
    got = sorted({t for _, t in panel.columns})
    miss = [t for t in tickers if t not in got]
    print(f"패널: {len(got)}종목 · {len(panel)}거래일 "
          f"({panel.index.min().date()} ~ {panel.index.max().date()})")
    if miss:
        print(f"  ⚠️ 끝내 못 받은 종목 {len(miss)}: {', '.join(miss)}")

    # 옛 판과의 리비전 차이를 눈으로 확인한다. 조용히 다른 값이면 두 패널로
    # 낸 숫자를 나란히 못 놓는다 — 벌어지는 종목이 있으면 여기서 보인다.
    if REF_PANEL.exists():
        ref = pd.read_parquet(REF_PANEL)
        shared = [t for t in got if t in {c for _, c in ref.columns}]
        worst = []
        for tk in shared:
            a = panel[("Close", tk)].dropna()
            b = ref[("Close", tk)].dropna()
            idx = a.index.intersection(b.index)
            if len(idx) > 100:
                worst.append((float(((a[idx] / b[idx]) - 1).abs().max() * 100), tk))
        worst.sort(reverse=True)
        print(f"  옛 패널({len(shared)}종목 공통) 대비 종가 최대 편차: "
              + ", ".join(f"{tk} {d:.2f}%" for d, tk in worst[:3]))

    print("레짐(SPY·VIX) 받는 중...", flush=True)
    regime = fetch_regime()
    regime.to_parquet(OUT_REGIME)
    print(f"레짐: {len(regime)}일 · " + " ".join(
        f"{k}={v}" for k, v in regime.loc[str(PANEL_START.date()):]
        ["regime"].value_counts().items()))

    print("섹터 받는 중...", flush=True)
    sectors = fetch_sectors(got)
    OUT_SECTORS.write_text(json.dumps(sectors, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    counts = pd.Series(sectors).value_counts()
    print("섹터: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"\n기록: {OUT_PANEL}, {OUT_REGIME}, {OUT_SECTORS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
