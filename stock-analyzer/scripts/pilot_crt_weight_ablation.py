#!/usr/bin/env python
"""CRT Phase2 ±20 이 제 몫을 하는가 — 트레이드 플랜에서 이 항만 뽑아 본다.

    python scripts/pilot_crt_weight_ablation.py [워커수]

## 왜 재나

2026-09-03 레인지 정의 감사(`docs/measurements/2026-09-03-crt-range-definition.md`)는
현행 정의와 정본 정의가 **다른 신호**라는 것까지만 답했다. 어느 쪽이 나은지는
못 갈랐고 — 두 정의 다 다음날 수익 예측에서 MDE 를 못 넘었다 — 그래서 남은
질문을 이렇게 적어 뒀다:

    진짜 질문은 정의가 아니라 가중치다. CRT Phase2 는 ICT 조정 ±30 중 ±20 인데,
    두 정의 다 이 자로는 0 과 구분이 안 된다. "±20 이 제 몫을 하는가"는 여기서
    답이 안 되고, composite 에서 이 항을 빼고 재는 다른 측정이 필요하다.

이 스크립트가 그 측정이다. 자는 다음날 수익이 아니라 **R** 이다 — 이 항의
프로덕션 소비자가 `build_trade_plan` 이고, 거기서 ±20 은 세 곳을 건드린다:

  1. 방향 게이트   |adj| >= BIAS_TH(10)         → CRT 혼자서도 방향을 만든다
  2. 저확신 숏 컷   conf_mag >= CONF_MED_TH(12)  → CRT 혼자서도 숏 컷을 통과한다
  3. 확신도 등급    high/medium/low (표시용)

즉 ±20 은 "점수를 조금 기울이는" 항이 아니라 **혼자서 트레이드를 만들어 내는**
항이다. 숏 기대값이 OOS −0.21R 인 걸 생각하면 부호가 어느 쪽이든 클 수 있다.

## 두 팔

    A(현행)   프로덕션 그대로
    B(제거)   `detect_crt_setup` 이 항상 setup=None — ±20 만 사라진다

B 는 프로덕션 파일을 안 고친다. 측정 프로세스 안에서만 그 함수를 갈아 끼운다.
`trade_plan` 은 CRT 의 **가격 좌표를 쓰지 않는다**(진입·손절·목표는 FVG/OB/구조가
낸다). 그래서 이 절제는 기하학을 안 건드리고 점수만 지운다 — selftest 두 개가
그걸 매 실행 확인한다(조정값이 정확히 ±20 만 다른가, 방향이 같으면 좌표가 같은가).

## 무엇을 못 재나 (미리 적는다)

- **두 팔의 트레이드 집합은 완전히 짝지어지지 않는다.** 백테스트는 한 트레이드가
  끝난 뒤 cooldown 만큼 건너뛰므로, 한쪽에서 셋업이 사라지면 그 뒤 스캔 위치도
  갈린다. 그래서 "A 에만 있는 트레이드"는 **거의** ±20 탓이지만 전부는 아니다.
  팔 전체 기대값 차이는 이 함정과 무관하다 — 그게 헤드라인이다.
- SE 는 **종목 부트스트랩**으로 낸다. 두 팔이 같은 재표본을 받으므로 공유 구조가
  자동으로 상쇄된다(두 팔을 독립으로 보고 재면 SE 가 과대해져 "차이 없음"이 공짜로
  나온다 — 여기선 그 반대 방향의 함정이다). 다만 날짜 군집(같은 날 여러 종목이
  함께 발동)은 안 잡는다. 차이 통계라 대부분 상쇄되지만 남는 만큼 MDE 는 하한이다.
- 거래비용은 안 넣었다. 두 팔이 같은 비용을 받으므로 차이에는 거의 안 남는다.

네트워크 無 — 저장 패널만 읽는다.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from modules import trade_plan_backtest as bt  # noqa: E402
from measure_trade_plan_oos import (  # noqa: E402  — 같은 자를 쓴다
    FILL_WINDOW, HOLD_WINDOW, IS_START, MIN_LEN, PANEL, _block, _ohlcv,
)

REPS = 2000
SEED = 20260904
NO_CRT = {"setup": None, "crt_high": 0.0, "crt_low": 0.0,
          "crt_mid": 0.0, "swept_erl": None, "phase": None}


def _patch_no_crt():
    """B 팔 — CRT 항만 지운다. `calc_ict_adjustment` 가 모듈 전역으로 이 이름을
    찾으므로 여기만 갈아 끼우면 조정값에서 ±20 이 빠진다."""
    import modules.ict_analysis as ict
    ict.detect_crt_setup = lambda df, period=3: dict(NO_CRT)


# ── 팔 실행 ────────────────────────────────────────────────────
def _run(args):
    arm, tk, df = args
    if arm == "B":
        _patch_no_crt()
    out = bt.backtest_trade_plans(df, fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW)
    trades = out["trades"]
    if arm == "A":
        # 트레이드마다 그날 CRT 가 켜져 있었나 — 프로덕션 함수를 그대로 다시
        # 먹인다(재구현하지 않는다). 백테스트가 본 것과 같은 슬라이스다.
        from modules.ict_analysis import detect_crt_setup
        for t in trades:
            t["crt"] = detect_crt_setup(df.iloc[: t["idx"] + 1])["setup"]
    for t in trades:
        t["ticker"] = tk
        t["entry_date"] = df.index[t["idx"]]
    return trades


# ── 통계 ───────────────────────────────────────────────────────
def _resolved(trades):
    """결판난 것만. 기대값은 기존 측정과 같은 정의(win/loss 평균 R)를 쓴다."""
    return [t for t in trades if t["outcome"] in ("win", "loss")]


def _exp(trades):
    r = [t["r"] for t in _resolved(trades)]
    return float(np.mean(r)) if r else float("nan")


def _by_ticker(trades):
    d = defaultdict(list)
    for t in _resolved(trades):
        d[t["ticker"]].append(t["r"])
    return {k: np.asarray(v, float) for k, v in d.items()}


def _mean_of(arrs):
    if not arrs:
        return float("nan")
    c = np.concatenate(arrs)
    return float(c.mean()) if c.size else float("nan")


def _boot_se(g1, g2, tickers, reps=REPS, seed=SEED):
    """두 집단 평균차의 종목 부트스트랩 SE. **같은 재표본**을 둘 다 받는다."""
    rng = np.random.default_rng(seed)
    idx = np.arange(len(tickers))
    vals = []
    for _ in range(reps):
        pick = [tickers[j] for j in rng.choice(idx, size=len(idx), replace=True)]
        v = (_mean_of([g1[tk] for tk in pick if tk in g1])
             - _mean_of([g2[tk] for tk in pick if tk in g2]))
        if v == v:
            vals.append(v)
    return float(np.std(vals, ddof=1)) if len(vals) > 1 else float("nan")


def _cmp(label, left, right, tickers, tail=""):
    """left − right 기대값 차이와 MDE(2.8·SE, 양측 5% · 검출력 80%)."""
    d = _exp(left) - _exp(right)
    mde = 2.8 * _boot_se(_by_ticker(left), _by_ticker(right), tickers)
    mark = "O" if abs(d) > mde else "X"
    return (f"  {label:22} {_exp(left):+.3f}R  vs {_exp(right):+.3f}R  "
            f"차이 {d:+.3f}R  MDE {mde:.3f}R  {mark}  "
            f"n={len(_resolved(left))}/{len(_resolved(right))}{tail}")


# ── selftest ───────────────────────────────────────────────────
def selftest(panel, tickers):
    """B 팔이 **±20 만** 지웠나. 이게 깨지면 아래 표 전체가 무의미하다."""
    import modules.ict_analysis as ict

    orig = ict.detect_crt_setup
    checked = fired = clipped = 0
    try:
        for tk in tickers[:40]:
            df = _ohlcv(panel, tk)
            if len(df) < 200:
                continue
            for i in range(200, len(df), 37):
                cut = df.iloc[: i + 1]
                ict.detect_crt_setup = orig
                a = ict.calc_ict_adjustment(cut)
                setup = orig(cut)["setup"]
                _patch_no_crt()
                b = ict.calc_ict_adjustment(cut)
                want = {"bullish": 20, "bearish": -20}.get(setup, 0)
                got = a["adjustment"] - b["adjustment"]
                # 조정값은 ±30 으로 잘린다. 그래서 CRT 가 켜져도 **실제로 전달되는
                # 몫은 20 보다 작을 수 있다** — 다른 항이 이미 캡에 닿아 있으면
                # 남은 자리만 받는다. 자르기가 안 물린 표본에서는 정확히 ±20.
                cap = abs(a["adjustment"]) == 30 or abs(b["adjustment"]) == 30
                if cap:
                    clipped += bool(want)
                    assert 0 <= got * np.sign(want or 1) <= abs(want), \
                        f"{tk} {i}: 잘림 표본인데 전달분 {got} 이 [0,{want}] 밖"
                else:
                    assert got == want, f"{tk} {i}: {a['adjustment']} − {b['adjustment']} != {want}"
                assert [s for s in a["signals"] if "CRT" not in s] == b["signals"], \
                    f"{tk} {i}: CRT 말고 다른 신호가 달라졌다"
                checked += 1
                fired += bool(setup)
    finally:
        ict.detect_crt_setup = orig

    assert checked > 500, f"표본이 너무 적다 ({checked})"
    assert fired > 0, "CRT 가 한 번도 안 켜졌다 — 자가 안 물린 것"
    print(f"selftest ①: {checked}표본(CRT 발동 {fired}, 그중 ±30 캡에 잘린 것 "
          f"{clipped}) — 절제가 CRT 항만 지운다 OK", flush=True)


def geometry_check(panel, tickers):
    """CRT 발동일에도 방향이 같으면 가격 좌표는 같아야 한다."""
    import modules.ict_analysis as ict
    from modules.trade_plan import build_trade_plan

    orig = ict.detect_crt_setup
    same = 0
    try:
        for tk in tickers[:60]:
            df = _ohlcv(panel, tk)
            for i in range(200, len(df), 53):
                cut = df.iloc[: i + 1]
                ict.detect_crt_setup = orig
                if not orig(cut)["setup"]:
                    continue
                pa = build_trade_plan(cut)
                _patch_no_crt()
                pb = build_trade_plan(cut)
                ict.detect_crt_setup = orig
                if pa["valid"] and pb["valid"] and pa["direction"] == pb["direction"]:
                    assert (round(pa["entry"]["ref"], 4) == round(pb["entry"]["ref"], 4)
                            and round(pa["stop"], 4) == round(pb["stop"], 4)), \
                        f"{tk} {i}: 방향이 같은데 좌표가 다르다 — 절제가 기하학을 건드렸다"
                    same += 1
    finally:
        ict.detect_crt_setup = orig
    assert same > 0, "방향이 같은 CRT 발동일이 하나도 없다 — 자가 안 물린 것"
    print(f"selftest ②: 방향이 같은 CRT 발동일 {same}건, 진입·손절 좌표 동일 OK",
          flush=True)


# ── main ───────────────────────────────────────────────────────
def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else max((os.cpu_count() or 2) - 1, 1)
    panel = pd.read_parquet(PANEL)

    tasks = []
    for tk in sorted({t for _, t in panel.columns}):
        df = _ohlcv(panel, tk)
        if len(df) >= MIN_LEN:
            tasks.append((tk, df))
    tk_list = [t for t, _ in tasks]
    span = f"{tasks[0][1].index[0].date()} ~ {tasks[0][1].index[-1].date()}"
    print(f"{len(tasks)}종목 · {span} · 워커 {workers}", flush=True)

    selftest(panel, tk_list)
    geometry_check(panel, tk_list)

    arms = {}
    for arm in ("A", "B"):
        got: list[dict] = []
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for tr in pool.map(_run, [(arm, tk, df) for tk, df in tasks]):
                got += tr
                done += 1
                if done % 50 == 0 or done == len(tasks):
                    print(f"  {arm} {done}/{len(tasks)}종목 · 누적 {len(got)}건", flush=True)
        arms[arm] = got

    A, B = arms["A"], arms["B"]
    oosA = [t for t in A if t["entry_date"] < IS_START]
    oosB = [t for t in B if t["entry_date"] < IS_START]

    body = ["  ── 팔별 전체 (OOS = 규칙을 정할 때 안 본 구간) ──",
            _block("A 현행 OOS", oosA),
            _block("B ±20 제거 OOS", oosB),
            "",
            "  ── 기대값 차이 (A − B, 종목 부트스트랩 MDE) ──",
            _cmp("OOS 전체", oosA, oosB, tk_list),
            _cmp("OOS 롱", [t for t in oosA if t["direction"] == "long"],
                 [t for t in oosB if t["direction"] == "long"], tk_list),
            _cmp("OOS 숏", [t for t in oosA if t["direction"] == "short"],
                 [t for t in oosB if t["direction"] == "short"], tk_list),
            _cmp("전 구간", A, B, tk_list),
            ""]

    keyA = {(t["ticker"], t["idx"]) for t in A}
    keyB = {(t["ticker"], t["idx"]) for t in B}
    onlyA = [t for t in A if (t["ticker"], t["idx"]) not in keyB]
    onlyB = [t for t in B if (t["ticker"], t["idx"]) not in keyA]
    sharedA = [t for t in A if (t["ticker"], t["idx"]) in keyB]
    sharedB = [t for t in B if (t["ticker"], t["idx"]) in keyA]
    body += ["  ── ±20 이 만든/막던 트레이드 (한쪽에만 있는 셋업) ──",
             _cmp("A 에만 vs 공통", onlyA, sharedA, tk_list,
                  f"  셋업 {len(onlyA)}/{len(sharedA)}"),
             _cmp("B 에만 vs 공통", onlyB, sharedB, tk_list,
                  f"  셋업 {len(onlyB)}/{len(sharedB)}"),
             ""]

    def _agree(t):
        return ((t["crt"] == "bullish" and t["direction"] == "long")
                or (t["crt"] == "bearish" and t["direction"] == "short"))

    agree = [t for t in A if t["crt"] and _agree(t)]
    against = [t for t in A if t["crt"] and not _agree(t)]
    none = [t for t in A if not t["crt"]]
    body += ["  ── A 팔 안에서 CRT 가 켜진 트레이드인가 (기준선 = CRT 없음) ──",
             _cmp("CRT 동의 vs 없음", agree, none, tk_list, f"  셋업 {len(agree)}"),
             _cmp("CRT 반대 vs 없음", against, none, tk_list, f"  셋업 {len(against)}")]

    print("\n" + "\n".join(body))
    print("\nMDE 를 못 넘은 줄(X)은 방향을 읽지 않는다.")
    print("표는 손으로 docs/measurements/ 에 옮긴다 — 이 스크립트는 문서를 쓰지 않는다.\n"
          "산문과 숫자를 한 파일이 반쯤 자동으로 채우면 다시 돌렸을 때 둘이 갈린다.")


if __name__ == "__main__":
    main()
