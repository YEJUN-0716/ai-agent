#!/usr/bin/env python
"""퀀트 재료를 EDGAR 시점 데이터로 바꾸면 총괄 판정이 값을 하나 — 사전 등록대로 한 번 잰다.

    python scripts/measure_quant_pit.py           # ①② 판정 + 위약 대조
    python scripts/measure_quant_pit.py selftest  # 산수 점검 (시장 데이터 無)
    python scripts/measure_quant_pit.py sealed    # 봉인 구간, 판정 뒤 딱 한 번

설계서: `docs/superpowers/specs/2026-08-13-quant-pit-design.md`.
**가중치·재정규화·점수화 문턱·IC 지평은 거기서 못 박았다. 결과를 보고 바꾸면 폐기다.**

## 판정은 AND 두 개다

  ① 단면  `quant_pit` 단독 21일 IC — 날짜 블록 부트스트랩 |t| >= 2
  ② 매매  총괄 판정으로 트레이드 플랜 **방향을 정한** 러너 vs **현행 러너**,
          6bp, 초과 연수익 부트스트랩 95% 하한 > 0

**하나만 통과하면 실패다.** 이 저장소는 ①만 보고 다섯 번 속았다.

②의 기준선이 매수보유가 아닌 이유: 이 가설은 "시장을 이긴다"가 아니라
**"총괄 판정을 붙이면 지금보다 낫다"** 다. 단, 매수보유 줄은 항상 같이 낸다 —
②를 통과해도 매수보유를 못 넘으면 실전 배선은 여전히 안 한다.

## 세 줄을 **같은 기계**로 낸다

현행 러너 줄을 `data/entry_rule_trades-daily.parquet` 에서 읽어오지 않는다.
그 파일은 다른 구간·다른 셋업 간격으로 만들어졌다. 여기서는 같은 창을 같은
루프로 세 번 돌린다 — 방향을 정하는 자만 다르다.

  현행   `build_trade_plan(...)`            ICT 순점수가 방향을 정한다
  판정   `build_trade_plan(..., direction=)` 총괄 판정이 방향을 정한다
  위약   같은 기계에 **날짜 안에서 섞은** 방향

위약 줄이 없으면 ②의 점추정을 읽을 수 없다. PEAD 에서 배운 줄이다.
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# measure_entry_rule 은 import 시점에 MODE 로 상수를 고른다. 일봉으로 **고정**한다
# — 환경변수가 intraday 로 남아 있으면 체결창·비용이 15분봉 값으로 들어온다.
os.environ["MODE"] = "daily"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules import analyst_log, analyst_scorecard as sc, analyst_team  # noqa: E402
from modules import analyst_weights  # noqa: E402
from modules.trade_plan import build_trade_plan  # noqa: E402
from scripts.measure_entry_rule import (  # noqa: E402
    COOLDOWN, FILL_WINDOW, MIN_LEN, _gap_adjust, _sim,
)
from scripts.measure_pead import _block_idx, excess_cagr_ci  # noqa: E402
from scripts.measure_portfolio import (  # noqa: E402
    MAX_POSITIONS, bench_curve, cagr, closes, mdd, simulate,
)

SLUG = "quant_pit"
PANEL = Path(os.environ.get("PANEL", "data/price_panel_v1.parquet"))
OUT_MD = Path("docs/measurements/2026-08-14-quant-pit.md")

# 본 측정. 백필 501일(2024-07-23 ~ 2026-07-22) 중 앞쪽만 쓴다 — 설계서 5절.
START = pd.Timestamp("2024-07-23")
END = pd.Timestamp("2026-02-28")

# 봉인 해제. 판정이 끝난 뒤 딱 한 번, 확인용으로만 연다. 갈려도 판정을 안
# 바꾸므로 **출력 파일을 따로 쓴다** — 같은 파일에 덮으면 판정을 덮는 것과
# 구별이 안 된다(PEAD 와 같은 규칙).
SEALED = "sealed" in sys.argv
if SEALED:
    START = pd.Timestamp("2026-03-01")
    END = pd.Timestamp("2026-07-22")
    OUT_MD = Path("docs/measurements/2026-08-14-quant-pit-sealed.md")

HORIZON = 21          # ①의 지평. 차트·ICT 를 쟀던 그 자(analyst_scorecard.HORIZONS)
COST_BPS = 6.0        # ②의 판정 비용
BLOCK = 20            # 날짜 블록 (자기상관 보존) — measure_pead 와 같은 값
SEED = 20260814

# 방향은 화면이 이미 쓰는 문턱으로 정한다. 여기서 새로 고르면 그 순간 튜닝이다.
BUY_AT, SELL_AT = sc.VERDICT_BUY_AT, sc.VERDICT_SELL_AT

# 연기 테스트 (`LIMIT=5 python scripts/measure_quant_pit.py`). 0 이면 전 종목.
LIMIT = int(os.environ.get("LIMIT", "0"))

FIELDS = ("Open", "High", "Low", "Close", "Volume")
LINES = ("현행(ICT 방향)", "총괄 판정 방향", "위약(방향 섞음)")


# ---------------------------------------------------------------- 총괄 판정

def verdict_frame(days: list) -> pd.DataFrame:
    """[date, ticker, verdict] — 3인 점수가 **다 있는** 자리만.

    빠진 자리를 중립 50 으로 채우면 '계산 불가'가 '중립 판단'으로 섞인다
    (`analyst_team.verdict_score` 와 같은 규칙). 가중치는 그 날 국면의
    ic_weights 배분을 쓴다 — 화면 총괄 판정이 쓰는 그 자다.
    """
    names = analyst_weights.DIRECTIONAL_ANALYSTS      # 차트 / 퀀트 / ICT 순
    slugs = ("chart", SLUG, "ict")
    cache: dict[str, list] = {}
    rows = []
    for day in days:
        regime = day.get("regime", "neutral")
        if regime not in cache:
            w = analyst_weights.load_analyst_weights(regime) or {}
            cache[regime] = [w.get(n, 1.0 / len(names)) for n in names]
        weights = cache[regime]
        for ticker, per in day.get("scores", {}).items():
            vals = [per.get(s) for s in slugs]
            if any(v is None for v in vals):
                continue
            rows.append((day["date"], ticker,
                         analyst_team.blend_score(vals, weights)))
    return pd.DataFrame(rows, columns=["date", "ticker", "verdict"])


def directions(vf: pd.DataFrame) -> pd.Series:
    """총괄 판정 → 방향. 매수/매도 문턱 사이는 **주문을 안 낸다**."""
    v = vf["verdict"].to_numpy(dtype=float)
    out = np.full(len(v), None, dtype=object)
    out[v >= BUY_AT] = "long"
    out[v <= SELL_AT] = "short"
    # None 을 그대로 담으면 pandas 가 NaN 으로 바꿔 버린다 — 소비 측이 전부
    # `pd.notna` 를 쓰도록 두고, 여기서는 값만 넣는다.
    return pd.Series(list(out), index=vf.index, dtype=object)


def placebo(vf: pd.DataFrame, dirs: pd.Series, seed: int = SEED) -> pd.Series:
    """같은 날짜·같은 분포, 종목만 섞은 방향.

    날짜별로 섞는다 — 전체를 섞으면 날짜별 롱/숏 개수까지 달라져서 무엇이
    달라진 건지 못 가른다. 점추정이 이 줄과 구별되지 않으면 신호가 아니라 구성이다.
    """
    rng = np.random.default_rng(seed)
    out = dirs.copy()
    for _, idx in vf.groupby("date").groups.items():
        out.loc[idx] = rng.permutation(dirs.loc[idx].to_numpy(dtype=object))
    return out


def by_ticker(vf: pd.DataFrame, dirs: pd.Series) -> dict:
    """{ticker: {날짜: 방향}} — 방향이 없는 자리는 아예 안 담는다."""
    out: dict[str, dict] = {}
    for (date, ticker), d in zip(zip(vf["date"], vf["ticker"]), dirs):
        if pd.notna(d):
            out.setdefault(ticker, {})[date] = d
    return out


# ---------------------------------------------------------------- 러너 한 줄

def _run_line(task):
    """한 종목 한 줄의 트레이드. dirs=None 이면 현행(ICT 가 방향을 정한다).

    `measure_entry_rule._run_ticker` 의 일봉 루프에서 **실제 러너가 거는 라인
    하나**(C_갭반영)만 남긴 것이다. 셋업 간격(COOLDOWN)은 그 라인의 착지로
    잡는다 — 세 줄이 같은 규칙을 쓰므로 짝지은 비교가 유지된다.
    """
    ticker, df, dirs, lo, hi = task
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    all_opens = df["Open"].to_numpy(dtype=float)
    dates = df.index
    n = len(df)

    rows = []
    i = max(MIN_LEN, int(dates.searchsorted(lo)))
    while i < n - 1:
        day = dates[i]
        if day > hi:
            break
        if dirs is None:
            plan = build_trade_plan(df.iloc[: i + 1], scale=1)
        else:
            want = dirs.get(day.strftime("%Y-%m-%d"))
            if want is None:
                i += 1
                continue
            plan = build_trade_plan(df.iloc[: i + 1], scale=1, direction=want)
        if not plan["valid"]:
            i += 1
            continue

        ctx = {"highs": highs, "lows": lows, "opens": None,
               "all_opens": all_opens, "sessions": None, "i": i}
        res = _sim("C_상단지정가", plan, ctx)
        long = plan["direction"] == "long"
        ref, stop = plan["entry"]["ref"], plan["stop"]
        risk = (ref - stop) if long else (stop - ref)
        limit = plan["entry"]["high"] if long else plan["entry"]["low"]
        gap = _gap_adjust(res, plan, limit, all_opens, risk)

        rows.append({
            "ticker": ticker, "entry_date": day,
            "direction": plan["direction"], "actionable": bool(plan["actionable"]),
            "risk_pct": plan["risk_pct"],
            "C_갭반영_outcome": gap["outcome"], "C_갭반영_r": gap["r"],
            "fill_date": dates[res["fill_idx"]] if res["fill_idx"] is not None else pd.NaT,
            "exit_date": dates[res["exit_idx"]] if res["exit_idx"] is not None else pd.NaT,
            "expire_date": dates[min(i + FILL_WINDOW, n - 1)],
        })
        landing = res["exit_idx"] or res["fill_idx"] or (i + FILL_WINDOW)
        i = max(i + 1, landing + COOLDOWN)
    return rows


def run_lines(panel: pd.DataFrame, dir_maps: list, workers: int) -> list:
    """세 줄을 한 번에 돈다. 반환은 줄마다 트레이드 DataFrame."""
    tickers = sorted({t for _, t in panel.columns})
    if LIMIT:
        tickers = tickers[:LIMIT]      # 연기 테스트용. 판정에는 쓰지 않는다.
    frames = []
    for label, dmap in zip(LINES, dir_maps):
        tasks = []
        for tk in tickers:
            df = pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()
            if len(df) <= MIN_LEN:
                continue
            if dmap is not None and not dmap.get(tk):
                continue                      # 방향이 한 번도 안 난 종목
            tasks.append((tk, df, None if dmap is None else dmap.get(tk, {}), START, END))
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, got in enumerate(pool.map(_run_line, tasks), 1):
                rows.extend(got)
                if i % 50 == 0 or i == len(tasks):
                    print(f"  [{label}] {i}/{len(tasks)}종목", flush=True)
        frames.append(pd.DataFrame(rows))
    return frames


def daily_curve(result: dict, index: pd.DatetimeIndex) -> pd.Series:
    """실현 자본 곡선을 거래일 격자에 올린다. 두 줄을 같은 날짜로 묶어 뽑으려면 필요하다.

    보유 중 평가손익은 안 센다(`measure_portfolio.mdd` 주석과 같은 한계) —
    **세 줄이 같은 방식**이라 비교에는 영향이 없다.
    """
    curve = result["curve"]
    if curve.empty:
        return pd.Series(1.0, index=index)
    joined = curve.reindex(index.union(curve.index)).ffill()
    return joined.reindex(index).ffill().fillna(1.0)


# ---------------------------------------------------------------- ① 단면 IC

def daily_ics(days: list, fwd: dict, slug: str) -> tuple:
    """(날짜, 그날의 단면 IC) — `analyst_scorecard._daily_ic` 를 그대로 쓴다."""
    out = []
    for day in days:
        rets = fwd.get(day.get("date"))
        if not rets:
            continue
        ic = sc._daily_ic(day.get("scores", {}), rets, slug)
        if ic is not None:
            out.append((day["date"], ic))
    return ([d for d, _ in out], np.array([v for _, v in out], dtype=float))


def block_t(values: np.ndarray) -> tuple:
    """날짜 블록 부트스트랩으로 평균의 t. 겹치는 21일 선행 구간을 블록이 흡수한다."""
    if len(values) < BLOCK * 2:
        return float(values.mean()) if len(values) else float("nan"), float("nan")
    idx = _block_idx(len(values), np.random.default_rng(SEED))
    boot = values[idx].mean(axis=1)
    mean, sd = float(values.mean()), float(boot.std())
    return mean, (mean / sd if sd > 0 else float("nan"))


# ---------------------------------------------------------------- 자체검사

def selftest() -> int:
    # 1) 방향 문턱 — 사이는 주문을 안 낸다.
    vf = pd.DataFrame({"date": ["d1"] * 4, "ticker": list("ABCD"),
                       "verdict": [70.0, 65.0, 50.0, 39.0]})
    d = directions(vf)
    assert [x if pd.notna(x) else None for x in d] == ["long", "long", None,
                                                       "short"], list(d)

    # 2) 위약은 날짜 **안에서만** 섞는다 — 날짜별 방향 개수가 보존돼야 한다.
    vf2 = pd.DataFrame({"date": ["d1"] * 3 + ["d2"] * 3, "ticker": list("ABCABC"),
                        "verdict": [70.0, 70.0, 30.0, 30.0, 50.0, 50.0]})
    d2 = directions(vf2)
    p2 = placebo(vf2, d2, seed=1)
    for day in ("d1", "d2"):
        m = vf2["date"] == day
        assert sorted(map(str, d2[m])) == sorted(map(str, p2[m])), day

    # 3) 총괄 판정 = 세 점수의 가중평균이고, 하나라도 없으면 안 낸다.
    days = [{"date": "2025-01-02", "regime": "bull",
             "scores": {"A": {"chart": 60.0, SLUG: 80.0, "ict": 40.0},
                        "B": {"chart": 60.0, "ict": 40.0}}}]
    got = verdict_frame(days)
    assert list(got["ticker"]) == ["A"], "재료가 빠진 종목이 판정에 들어갔다"
    assert 40.0 <= got["verdict"].iloc[0] <= 80.0

    # 4) 자본 곡선을 거래일 격자에 올려도 값이 안 바뀐다(계단 유지).
    idx = pd.bdate_range("2025-01-01", periods=6)
    res = {"curve": pd.Series([1.1, 1.2], index=[idx[1], idx[3]])}
    c = daily_curve(res, idx)
    assert list(c) == [1.0, 1.1, 1.1, 1.2, 1.2, 1.2], list(c)

    # 5) 블록 t — 평균이 0 인 계열은 t 도 0 근처, 상수 계열은 sd=0 이라 nan.
    rng = np.random.default_rng(0)
    m, t = block_t(rng.normal(0, 1, 500))
    assert abs(t) < 3, t
    assert np.isnan(block_t(np.ones(100))[1])

    print("selftest OK")
    return 0


# ---------------------------------------------------------------- 리포트

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    workers = max(os.cpu_count() - 1, 1)

    days_all = analyst_log.load_days(analyst_log.BACKFILL_DIRNAME)
    days = [d for d in days_all
            if START <= pd.Timestamp(d["date"]) <= END]
    if not days:
        print("백필 구간이 비었다 — scripts/backfill_quant_pit.py 를 먼저 돌릴 것.",
              file=sys.stderr)
        return 1
    dates = [d["date"] for d in days]

    panel = pd.read_parquet(PANEL)
    close_all = panel["Close"]
    prices = {tk: close_all[tk].dropna() for tk in close_all.columns}

    # ── ① 단면 ────────────────────────────────────────────────────────────
    fwd = sc.build_forward_returns(prices, dates, HORIZON)
    stats = sc.score_analysts(days, fwd, HORIZON)
    ic_dates, ics = daily_ics(days, fwd, SLUG)
    mean_ic, t_ic = block_t(ics)
    pass1 = abs(t_ic) >= 2

    per_day = [len([1 for per in d["scores"].values() if SLUG in per]) for d in days]

    # ── ② 매매 ────────────────────────────────────────────────────────────
    vf = verdict_frame(days)
    dirs = directions(vf)
    plc = placebo(vf, dirs)
    print(f"총괄 판정 {len(vf):,}건 · 롱 {(dirs == 'long').sum():,} · "
          f"숏 {(dirs == 'short').sum():,} · 무주문 {dirs.isna().sum():,}")

    frames = run_lines(panel, [None, by_ticker(vf, dirs), by_ticker(vf, plc)], workers)

    close = closes(START, END)
    bench = bench_curve(START, END)
    results, curves = [], []
    for label, df in zip(LINES, frames):
        act = df[df["actionable"]] if len(df) else df
        res = simulate(act, COST_BPS) if len(act) else {
            "equity": 1.0, "curve": pd.Series(dtype=float), "taken": 0,
            "skipped_slot": 0, "avg_open": 0.0, "pos_log": []}
        results.append((label, act, res))
        curves.append(daily_curve(res, close.index))

    base_ret = curves[0].pct_change().fillna(0.0).values
    ver_ret = curves[1].pct_change().fillna(0.0).values
    plc_ret = curves[2].pct_change().fillna(0.0).values
    pt, lo, hi = excess_cagr_ci(ver_ret, base_ret)
    p_pt, p_lo, p_hi = excess_cagr_ci(plc_ret, base_ret)
    pass2 = lo > 0

    years = (close.index[-1] - close.index[0]).days / 365.25
    verdict = "통과" if (pass1 and pass2) else "실패"

    body = [
        "# 퀀트 재료 교체 — 봉인 구간 확인" if SEALED else
        "# 퀀트 팀 재료를 EDGAR 시점 데이터로 바꾸면 총괄 판정이 값을 하나 (2026-08-14)",
        "",
        f"구간 {START.date()} ~ {END.date()} · 백필 {len(days)}일 · "
        f"IC 지평 {HORIZON}일 · 판정 비용 {COST_BPS:.0f}bp · 자리 {MAX_POSITIONS}.",
        "사전 등록: `docs/superpowers/specs/2026-08-13-quant-pit-design.md`.",
        "",
    ] + ([
        "> **이 문서는 판정이 아니다.** 본 측정(`2026-08-14-quant-pit.md`)에서 이미 판정이",
        "> 끝났고, 봉인 구간은 그 뒤에 딱 한 번 확인용으로 연 것이다. 같은 통과선을 같은",
        "> 코드로 대 본 값일 뿐, **판정을 바꾸지 않는다**(설계서 5절).",
        "",
    ] if SEALED else []) + [
        f"## 판정: **{verdict}** (①{'O' if pass1 else 'X'} AND ②{'O' if pass2 else 'X'})",
        "",
        "| | 무엇 | 통과선 | 실측 | |",
        "|---|---|---|---|---|",
        f"| ① 단면 | `quant_pit` 단독 {HORIZON}일 IC | 날짜 블록 부트스트랩 \\|t\\| >= 2 | "
        f"평균 IC {mean_ic:+.4f} · t={t_ic:+.2f} (채점일 {len(ics)}) | "
        f"{'O' if pass1 else 'X'} |",
        f"| ② 매매 | 총괄 판정 방향 러너 − 현행 러너 | 초과 연수익 95% 하한 > 0 | "
        f"{pt:+.2f}%p · 95% [{lo:+.2f}, {hi:+.2f}] | {'O' if pass2 else 'X'} |",
        "",
        "**하나만 통과하면 실패다.** ①만 보고 이 저장소는 다섯 번 속았다.",
        "",
        "## 세 줄 + 매수보유",
        "",
        "| 줄 | 잡은 트레이드 | 자리 없어 버림 | 최종 자본 | 연 수익률 | MDD |",
        "|---|---|---|---|---|---|",
    ]

    for (label, act, res), curve in zip(results, curves):
        body.append(
            f"| {label} | {res['taken']:,} | {res['skipped_slot']:,} | "
            f"×{res['equity']:.3f} | **{cagr(res['equity'], years):+.1f}%** | "
            f"{mdd(curve):.1f}% |")
    body += [
        f"| 매수보유 (같은 패널 동일가중) | — | — | ×{float(bench.iloc[-1]):.3f} | "
        f"{cagr(float(bench.iloc[-1]), years):+.1f}% | {mdd(bench):.1f}% |",
        "",
        "매수보유는 통과선이 아니지만 뺄 수 없다 — 현행 러너가 이미 매수보유에 진다는 것이",
        "EP02 의 결론이고(`2026-08-13-portfolio-vs-benchmark.md`), ②를 통과해도 매수보유를",
        "못 넘으면 **실전 배선은 여전히 안 한다.** 이 문장은 재기 전에 썼다(설계서 4절).",
        "",
        "## 위약 대조 — ②의 점추정을 그냥 읽으면 안 되는 이유",
        "",
        f"같은 기계에 **날짜 안에서 방향만 섞은** 총괄 판정을 넣으면 현행 대비 "
        f"{p_pt:+.2f}%p · 95% [{p_lo:+.2f}, {p_hi:+.2f}] 가 나온다.",
        "",
        "판정 줄의 점추정이 이 줄과 구별되지 않으면 그건 신호가 아니라 **구성**이다 —",
        "방향을 바깥에서 정하면 트레이드 수와 롱/숏 비율이 달라지고, 그 차이만으로도",
        "곡선이 움직인다. 이 줄은 판정 기준이 아니라 해석용이다.",
        "",
        "## 애널리스트 셋을 같은 자로",
        "",
        f"| 슬러그 | 평균 IC | t (Newey–West) | 유효표본 | 적중률 |",
        "|---|---|---|---|---|",
    ]
    for slug in ("chart", SLUG, "ict"):
        s = stats.get(slug)
        if not s:
            continue
        t_nw = s.get("t_stat")
        body.append(f"| `{slug}` | {s['mean_ic']:+.4f} | "
                    f"{'—' if t_nw is None else f'{t_nw:+.2f}'} | "
                    f"{s['effective_n']} | {s['hit_rate']:.1f}% |")
    body += [
        "",
        "판정은 위 표의 Newey–West 가 아니라 **①의 날짜 블록 부트스트랩**으로 한다",
        "(사전 등록). 이 표는 차트·ICT 와 나란히 놓고 보라고 내는 것이다.",
        "",
        "## 재료가 얼마나 채워졌나",
        "",
        f"- `quant_pit` 이 난 종목: 날짜당 최소 {min(per_day)} · 중앙 "
        f"{int(np.median(per_day))} · 최대 {max(per_day)}",
        f"- 총괄 판정(3인 전원)이 난 자리: {len(vf):,}건 / 백필 {len(days)}일",
        "- 빠진 종목은 EDGAR 태그가 없거나 TTM 구간에 분기 구멍이 있는 쪽이다 —",
        "  중립값으로 채우지 않았다. 은행·유틸리티·리츠가 상대적으로 많이 빠졌고,",
        "  그만큼 이 측정의 단면은 오늘의 S&P 전체가 아니다.",
        "",
        "## 이 측정이 안 한 것",
        "",
        "- **안전성·F-Score·섹터 상대 밸류에이션 미측정.** EDGAR 태그를 새로 모아야 한다.",
        "  실패했을 때 적을 문장은 재기 전에 정해 뒀다 — \"애널리스트 팀에 예측력이 없다\"가",
        "  아니라 **\"재무 재료를 시점 데이터로 바꿔도 없었다\"**.",
        "- **러너 배선 안 함.** ②를 통과하기 전에는 `paper_trade_runner_toss.py` 를 안 고친다.",
        ("- **봉인은 이 문서로 열었다. 다시 안 연다.**" if SEALED else
         "- **2026-03-01 ~ 2026-07-22 은 봉인.** 판정 후 딱 한 번 연다. 갈려도 판정을 안 바꾼다."),
        "",
        f"재현: `python scripts/measure_quant_pit.py{' sealed' if SEALED else ''}` · "
        "산수 점검 `... selftest`",
        "",
    ]

    text = "\n".join(body)
    print(text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest() if "selftest" in sys.argv else main())
