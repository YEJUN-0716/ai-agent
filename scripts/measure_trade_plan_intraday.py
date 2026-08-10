#!/usr/bin/env python
"""트레이드 플랜을 **15분봉에서** 잰다 — 3단계가 성립하는지의 시험.

    python scripts/measure_trade_plan_intraday.py [워커수]

## 왜 필요한가

트레이드 플랜의 기대값 +0.69R 은 **일봉에서 잰 숫자**다. 15분봉에서 같은
규칙이 통한다는 근거는 없다. 이 저장소는 수익률 예측을 네 번 시도해 네 번
실패했고 통과한 것은 트레이드 기하학 하나뿐이다. 측정 없이 자동 주문을
붙이는 것이 피해야 할 실패 방식이다.

## 무엇을 재는가

설정 A (scale=1)   창을 봉 그대로. "5시간짜리 추세"로 해석한다.
설정 B (scale=26)  창 × 26. 15분봉에서 일봉과 같은 실제 시간을 본다.

둘 다 **당일 청산**이다 — 세션 마지막 봉(15:45 ET) 시가에 턴다. 3b 러너와
같은 규칙이라야 여기서 잰 숫자가 러너를 대표한다.

## 얼마나 걸리나 (2026-08-10 실측, 1종목 1년)

    설정 A    77초
    설정 B  1,123초   ← 창이 26배라 스윙 탐지가 2,080봉을 훑는다

30종목 3년이면 A 는 워커 12개로 ~15분, B 는 ~3시간이다. 종목이나 연수를
줄이면 빨라지지만 표본이 그만큼 준다 — 이 단계의 목적이 "충분한 표본에서
재는 것"이라 줄이지 않았다. 배경으로 돌려 놓고 기다린다.

네트워크 無 — 저장 패널만 읽는다 (scripts/fetch_intraday_panel.py 로 먼저 받을 것).
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules import trade_plan_backtest as bt  # noqa: E402
from modules.intraday_session import session_ids  # noqa: E402
from modules.stat_validation import permutation_test_trades  # noqa: E402

# 패널·설정·산출물 이름을 환경변수로 연다 — 기간이나 설정을 달리한 측정을
# 나란히 돌려 놓고 비교하려면 서로 덮어쓰지 않아야 한다.
#   PANEL  다른 패널 파일        ONLY  설정 하나만 (예: A_봉그대로)
#   TAG    산출물 이름에 붙일 꼬리표
PANEL = Path(os.environ.get("PANEL", "data/intraday_panel_15m.parquet"))
_TAG = os.environ.get("TAG", "")
OUT_JSON = Path(f"data/trade_plan_intraday_result{_TAG}.json")
OUT_TXT = Path(f"docs/measurements/2026-08-10-trade-plan-intraday{_TAG}.txt")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

# 당일 안에서 진입을 기다리는 최대 봉 수(2시간)와 보유 상한(하루).
# 세션 경계가 어차피 더 짧게 자르므로 상한 역할만 한다.
FILL_WINDOW = 8
HOLD_WINDOW = 26

# 15분봉 하루 정규장 봉 수. 설정 B 의 배수.
BARS_PER_DAY = 26

# 숏 레짐필터·저확신 컷을 고를 때 본 구간의 시작 (일봉 측정 기준).
IS_START = pd.Timestamp("2024-12-20")

# 왕복 거래비용(bp). 대형주 15분봉 스프레드 + 슬리피지 가정.
#
# **이 숫자가 결론을 정한다.** 15분봉에서 이 규칙의 손절폭은 가격의
# 0.15~0.26% 다. 왕복 6bp = 0.06% 를 R 로 바꾸면 트레이드당 0.4R 이고,
# 총기대값이 +0.35R 이라 비용이 수익 전부를 먹는다. 손익분기가 5bp 근처라
# 가정 하나로 판정이 뒤집힌다 — 그래서 하나만 쓰지 않고 훑는다.
COST_BPS = 6.0
COST_SWEEP = (0.0, 1.0, 2.0, 4.0, 6.0, 10.0)

SETTINGS = {"A_봉그대로": 1, "B_일수환산": BARS_PER_DAY}
if os.environ.get("ONLY"):
    SETTINGS = {k: v for k, v in SETTINGS.items() if k in os.environ["ONLY"].split(",")}


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _run_ticker(args):
    """한 종목 × 한 설정 — 진입 시각을 붙인 트레이드 목록."""
    tk, df, scale = args
    out = bt.backtest_trade_plans(
        df, fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW,
        sessions=session_ids(df.index), scale=scale)
    for t in out["trades"]:
        t["ticker"] = tk
        t["entry_date"] = df.index[t["idx"]]
        # 비용을 R 로 바꾸려면 위험이 가격의 몇 %였는지가 필요하다.
        ref, stop = t["entry_ref"], t["stop_price"]
        t["risk_pct"] = abs(ref - stop) / ref if ref else float("nan")
    return out["trades"]


def _net_r(trades: list[dict], cost_bps: float = COST_BPS) -> list[float]:
    """비용 차감 R. 체결된 트레이드만.

    R 은 위험 1단위 기준이라 그 자체로는 비용을 못 잰다. 손절이 가격의
    risk_pct 만큼 떨어져 있으면 왕복 cost_bps 는 cost_bps/risk_pct 만큼의
    R 이다 — 손절이 촘촘할수록 같은 스프레드가 더 크게 먹는다.
    """
    out = []
    for t in trades:
        if t["outcome"] == "nofill":
            continue
        rp = t["risk_pct"]
        cost_r = (cost_bps / 10000.0) / rp if rp and rp == rp else float("nan")
        out.append(t["r"] - cost_r)
    return out


def _cost_sweep(trades: list[dict]) -> list[str]:
    """비용 가정별 순 기대값과 순열검정 p. 판정이 어디서 뒤집히는지 본다."""
    lines = ["  ── 비용 민감도 (OOS) ──",
             "    왕복bp    순평균R    p값      n"]
    for bps in COST_SWEEP:
        net = [r for r in _net_r(trades, bps) if r == r]
        if not net:
            continue
        arr = np.array(net)
        p = (permutation_test_trades(arr, seed=42)["p_value"]
             if len(arr) >= 5 else float("nan"))
        lines.append(f"    {bps:5.0f}    {arr.mean():+7.3f}   {p:6.4f}  {len(arr):5d}")
    return lines


def _fmt(s: dict) -> str:
    wr, ex, ar = s["win_rate"], s["expectancy_r"], s["avg_r"]
    wr = " nan" if wr != wr else f"{wr * 100:4.0f}%"
    ex = "  nan" if ex != ex else f"{ex:+5.2f}R"
    ar = "  nan" if ar != ar else f"{ar:+5.2f}R"
    return (f"setups={s['setups']:5d}  filled={s['filled']:5d}  "
            f"W/L={s['wins']:4d}/{s['losses']:4d}  eod={s.get('eod_exits', 0):4d}  "
            f"winrate={wr}  expectancy={ex}  avg={ar}")


def _block(label: str, trades: list[dict]) -> str:
    lines = [f"  {label:22} {_fmt(bt._stats(trades))}"]
    net = [r for r in _net_r(trades) if r == r]
    if net:
        lines.append(f"    └ 비용차감({COST_BPS:.0f}bp)     "
                     f"평균 {np.mean(net):+5.2f}R  (n={len(net)})")
    for direction, dlab in (("long", "롱"), ("short", "숏")):
        sub = [t for t in trades if t["direction"] == direction]
        if sub:
            lines.append(f"    └ {dlab:20} {_fmt(bt._stats(sub))}")
    return "\n".join(lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not PANEL.exists():
        print(f"패널이 없습니다: {PANEL}\n"
              f"먼저: python scripts/fetch_intraday_panel.py", file=sys.stderr)
        return 1

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else max(os.cpu_count() - 1, 1)
    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    frames = {tk: _ohlcv(panel, tk) for tk in tickers}

    span = f"{panel.index[0]} ~ {panel.index[-1]}"
    print(f"{len(frames)}종목 · {len(panel):,}봉 · {span} · 워커 {workers}\n",
          flush=True)

    result: dict = {"span": span, "tickers": len(frames), "cost_bps": COST_BPS,
                    "fill_window": FILL_WINDOW, "hold_window": HOLD_WINDOW,
                    "settings": {}}
    body: list[str] = [f"15분봉 트레이드 플랜 측정 · {len(frames)}종목 · {span}",
                       f"당일 청산(15:45 ET 시가) · 비용 {COST_BPS:.0f}bp 왕복", ""]

    for name, scale in SETTINGS.items():
        # 워밍업이 scale 배로 커진다. 봉이 모자란 종목은 뺀다.
        need = 60 * scale + FILL_WINDOW + HOLD_WINDOW
        tasks = [(tk, df, scale) for tk, df in frames.items() if len(df) > need]
        print(f"── 설정 {name} (scale={scale}) · {len(tasks)}종목 ──", flush=True)

        trades: list[dict] = []
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for got in pool.map(_run_ticker, tasks):
                trades += got
                done += 1
                if done % 5 == 0 or done == len(tasks):
                    print(f"  {done}/{len(tasks)}종목 · 누적 {len(trades)}건",
                          flush=True)

        oos = [t for t in trades if t["entry_date"] < IS_START]
        ins = [t for t in trades if t["entry_date"] >= IS_START]

        body += [
            f"── 설정 {name} (scale={scale}) ──",
            f"  ── 일봉 규칙을 고를 때 안 본 구간 (~{(IS_START - pd.Timedelta(days=1)).date()}) ──",
            _block("전체", oos),
            "",
            f"  ── 본 구간 ({IS_START.date()}~) ──",
            _block("전체", ins),
            "",
            "  ── 연도별 ──",
        ]
        for year in sorted({t["entry_date"].year for t in trades}):
            body.append(_block(str(year), [t for t in trades
                                           if t["entry_date"].year == year]))

        # 트레이드를 남긴다 — 비용 가정을 바꿔 다시 보려고 2시간짜리
        # 백테스트를 또 돌리는 일이 없도록.
        tr_path = Path(f"data/intraday_trades_{name}{_TAG}.parquet")
        pd.DataFrame([{k: t[k] for k in
                       ("ticker", "entry_date", "direction", "confidence",
                        "outcome", "r", "risk_pct", "entry_ref", "stop_price")}
                      for t in trades]).to_parquet(tr_path)
        print(f"  트레이드 저장: {tr_path}", flush=True)

        body += ["", *_cost_sweep(oos)]

        net_oos = [r for r in _net_r(oos) if r == r]
        perm = None
        if len(net_oos) >= 5:
            perm = permutation_test_trades(np.array(net_oos), seed=42)
            body += ["", f"  순열검정(OOS, 비용차감): p={perm['p_value']:.4f} "
                         f"{'유의' if perm['is_significant_95pct'] else '우연과 구분 안 됨'}"]
        body.append("")

        result["settings"][name] = {
            "scale": scale,
            "tickers": len(tasks),
            "oos": bt._stats(oos),
            "is": bt._stats(ins),
            "oos_net_avg_r": float(np.mean(net_oos)) if net_oos else None,
            "oos_net_n": len(net_oos),
            "permutation": perm,
        }

    text = "\n".join(body)
    print("\n" + text)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text(text, encoding="utf-8")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                   default=str), encoding="utf-8")
    print(f"\n저장: {OUT_TXT}\n저장: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
