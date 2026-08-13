"""EP02 영상용 차트 생성.

    python make_charts.py

출력: content/ep02-benchmark/charts/*.png (1920x1080)

EP01 과 같은 규칙이다 — 숫자를 하드코딩하지 않는다. stock-analyzer 의
`scripts.measure_portfolio` 를 그대로 불러서 곡선을 다시 만든다. 대본이 말하는
값과 화면에 뜨는 값이 갈리면 이 채널은 끝이다. 실제 값은 `charts/FIGURES.txt`
에 떨궈서 대본과 대조한다.
"""

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(r"C:\Users\1aass\stock-analyzer")
OUT = Path(__file__).parent / "charts"

os.chdir(REPO)                      # measure_portfolio 의 경로가 전부 상대경로다
sys.path.insert(0, str(REPO))
from scripts.measure_portfolio import (  # noqa: E402
    COST_SWEEP, MAX_POSITIONS, MAX_POSITION_PCT, RISK_PCT_PER_TRADE, TRADES,
    _year_return, bench_curve, cagr, mdd, simulate,
)

COST_MAIN = 6.0                     # 대본이 쓰는 기준선

BG, FG, MUTED = "#0b0f14", "#e6edf3", "#7d8590"
STRAT, BENCH, WARN = "#f85149", "#3fb950", "#d29922"

plt.rcParams.update({
    "font.family": ["Malgun Gothic", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG, "axes.facecolor": BG,
    "text.color": FG, "axes.labelcolor": FG,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": "#21262d", "grid.color": "#161b22",
})


def fig():
    f, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(True, alpha=0.6, lw=0.8)
    return f, ax


def save(f, name):
    OUT.mkdir(exist_ok=True)
    f.savefig(OUT / name, facecolor=BG, bbox_inches="tight", pad_inches=0.4)
    plt.close(f)
    print(f"  {name}")


def load():
    """측정 스크립트와 같은 입력·같은 구간. 여기서 갈리면 대본이 틀린다."""
    d = pd.read_parquet(TRADES)
    d["entry_date"] = pd.to_datetime(d["entry_date"])
    act = d[d["actionable"]].copy()
    start, end = act["entry_date"].min(), d["entry_date"].max()
    years = (end - start).days / 365.25
    runs = {c: simulate(act, c) for c in COST_SWEEP}
    return act, bench_curve(start, end), runs, years, start, end


# ---------------------------------------------------------------- 1. 자본 곡선

def chart_equity(bench, runs, years, notes):
    r = runs[COST_MAIN]
    notes += [
        f"전략 {COST_MAIN:.0f}bp  최종 ×{r['equity']:.3f}  연 {cagr(r['equity'], years):+.1f}%",
        f"매수보유(같은 패널 동일가중)  최종 ×{float(bench.iloc[-1]):.3f}  "
        f"연 {cagr(float(bench.iloc[-1]), years):+.1f}%",
    ]

    f, ax = fig()
    for curve, color, label in (
        (bench, BENCH, "같은 279종목 그냥 사서 들고 있기"),
        (r["curve"], STRAT, f"내 전략 (자리 10 · 왕복 {COST_MAIN:.0f}bp)"),
    ):
        ax.plot(curve.index, curve.values, color=color, lw=3.5, label=label)
        ax.annotate(f"×{float(curve.iloc[-1]):.2f}",
                    (curve.index[-1], float(curve.iloc[-1])), color=color,
                    fontsize=34, weight="bold", xytext=(14, -10),
                    textcoords="offset points")

    ax.axhline(1.0, color=MUTED, lw=1)
    ax.set_title("같은 기간, 같은 종목, 같은 돈.  하나는 골랐고 하나는 안 골랐다",
                 fontsize=32, weight="bold", pad=28, loc="left")
    ax.set_ylabel("자본 (시작 = 1)", fontsize=22)
    ax.legend(fontsize=24, frameon=False, loc="upper left")
    ax.tick_params(labelsize=20)
    save(f, "01_equity.png")


# ---------------------------------------------------------------- 2. 비용 스윕

def chart_cost(bench, runs, years, notes):
    """수수료를 0으로 놓아도 못 넘는다 — 0bp 막대만 강조한다."""
    vals = [cagr(runs[c]["equity"], years) for c in COST_SWEEP]
    bh = cagr(float(bench.iloc[-1]), years)
    notes.append("비용 스윕 연수익  " +
                 "  ".join(f"{c:.0f}bp {v:+.1f}%" for c, v in zip(COST_SWEEP, vals)))

    f, ax = fig()
    x = np.arange(len(COST_SWEEP))
    bars = ax.bar(x, vals, width=0.55, color=[WARN] + [STRAT] * (len(x) - 1))
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.5, f"{v:+.1f}%",
                ha="center", color=FG, fontsize=30, weight="bold")

    ax.axhline(bh, color=BENCH, lw=3.5, ls="--")
    ax.text(len(x) - 0.55, bh + 0.6, f"그냥 들고 있기  {bh:+.1f}%",
            color=BENCH, fontsize=30, weight="bold", ha="right")

    ax.set_xticks(x, [f"왕복 {c:.0f}bp" for c in COST_SWEEP], fontsize=24)
    ax.set_ylim(0, bh * 1.22)
    ax.set_ylabel("연 수익률", fontsize=22)
    ax.set_title("수수료를 0원으로 만들어도 넘지 못한다",
                 fontsize=32, weight="bold", pad=28, loc="left")
    ax.tick_params(labelsize=20)
    save(f, "02_cost_sweep.png")


# ---------------------------------------------------------------- 3. 해마다

def chart_yearly(bench, runs, notes):
    r = runs[COST_MAIN]
    exposure = r["avg_open"]
    years_list = sorted({*r["curve"].index.year} | {*bench.index.year})
    s = [_year_return(r["curve"], y) for y in years_list]
    b = [_year_return(bench, y) for y in years_list]
    e = [v * exposure for v in b]
    won = [y for y, a, c in zip(years_list, s, b) if a > c]
    won_exp = [y for y, a, c in zip(years_list, s, e) if a > c]
    notes += [
        f"평균 노출 {exposure * 100:.0f}%",
        f"원본 매수보유를 이긴 해 {len(won)}/{len(years_list)}년 → {won or '없음'}"
        f"  (2022는 둘 다 마이너스인 해다: {s[years_list.index(2022)]:+.1f}% vs "
        f"{b[years_list.index(2022)]:+.1f}%)",
        f"노출 맞춘 매수보유를 이긴 해 {len(won_exp)}/{len(years_list)}년 → {won_exp}",
    ]

    f, ax = fig()
    x = np.arange(len(years_list))
    w = 0.27
    for off, vals, color, label in (
        (-w, s, STRAT, f"내 전략 ({COST_MAIN:.0f}bp)"),
        (0.0, b, BENCH, "그냥 들고 있기"),
        (w, e, MUTED, f"그냥 들고 있기 × 노출 {exposure * 100:.0f}%"),
    ):
        ax.bar(x + off, vals, width=w, color=color, label=label)

    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks(x, [str(y) for y in years_list], fontsize=26)
    ax.set_ylim(min(b + s) * 1.5, max(b + s) * 1.15)   # 2021 막대가 범례에 물린다
    ax.set_ylabel("연 수익률", fontsize=22)
    ax.set_title(f"{len(years_list)}개 연도 중 {len(years_list) - len(won)}년을 졌다.  "
                 f"이긴 {len(won)}년은 둘 다 마이너스인 해였다",
                 fontsize=32, weight="bold", pad=28, loc="left")
    ax.legend(fontsize=24, frameon=False, loc="lower left")
    ax.tick_params(labelsize=20)
    save(f, "03_yearly.png")


# ---------------------------------------------------------------- 4. 자리 vs 신호

def chart_slots(act, runs, notes):
    taken = runs[COST_MAIN]["taken"]
    notes.append(f"actionable 셋업 {len(act):,}건  |  실제로 잡은 트레이드 {taken:,}건  "
                 f"= {len(act) / taken:.1f}배 (대본의 '여덟 배'는 이 비교다. "
                 f"자리 {MAX_POSITIONS}개와 직접 비교하면 {len(act) / MAX_POSITIONS:.0f}배다)")

    f, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # 왼쪽 — 자리 10칸. 오른쪽 — 신호 더미(점).
    for i in range(MAX_POSITIONS):
        ax.add_patch(plt.Rectangle((0.06 + i % 5 * 0.055, 0.52 - i // 5 * 0.09),
                                   0.045, 0.075, facecolor="#161b22",
                                   edgecolor=BENCH, lw=3))
    ax.text(0.06, 0.68, f"자리 {MAX_POSITIONS}개", color=BENCH, fontsize=52, weight="bold")
    ax.text(0.06, 0.34, "동시에 들 수 있는 종목", color=MUTED, fontsize=28)

    rng = np.random.default_rng(20260813)
    n_dots = 600                     # 7,466건을 점으로 다 찍으면 뭉갠다
    ax.scatter(0.55 + rng.random(n_dots) * 0.4, 0.28 + rng.random(n_dots) * 0.42,
               s=26, color=STRAT, alpha=0.55)
    ax.text(0.55, 0.78, f"신호 {len(act):,}건", color=STRAT, fontsize=52, weight="bold")
    ax.text(0.55, 0.20, f"조건을 통과한 셋업 (점은 축약)", color=MUTED, fontsize=28)

    ax.text(0.06, 0.06, "평균 R 은 한 판이 어땠는지를 말한다.  내 계좌가 얼마가 됐는지는 말하지 않는다",
            color=FG, fontsize=34, weight="bold")
    save(f, "04_slots_vs_signals.png")


# ---------------------------------------------------------------- 5. 왜 못 샀나

def chart_skipped(runs, notes):
    r = runs[COST_MAIN]
    slot, cash = r["skipped_slot"], r["skipped_cash"]
    notes.append(f"자리가 없어 버림 {slot:,}건  |  현금이 모자라 버림 {cash:,}건  |  "
                 f"잡은 트레이드 {r['taken']:,}건")

    f, ax = fig()
    ax.grid(False)
    bars = ax.barh([1, 0], [slot, cash], height=0.5, color=[MUTED, STRAT])
    for b, v, lab in zip(bars, [slot, cash],
                         [f"자리가 없어서  {slot:,}건", f"살 돈이 없어서  {cash:,}건"]):
        ax.text(v + cash * 0.012, b.get_y() + b.get_height() / 2, lab,
                va="center", color=FG, fontsize=34, weight="bold")

    ax.set_yticks([])
    ax.set_xlim(0, cash * 1.45)
    ax.set_xlabel("못 산 셋업 수", fontsize=22)
    ax.set_title(f"설정에는 '자리 {MAX_POSITIONS}개'라고 적혀 있다.  "
                 "실제로 막은 건 자리가 아니라 현금이었다",
                 fontsize=32, weight="bold", pad=28, loc="left")
    ax.tick_params(labelsize=20)
    save(f, "05_why_skipped.png")


# ---------------------------------------------------------------- 6. 여섯 종목

def chart_six(act, notes):
    """손절폭이 촘촘해서 15% 상한이 먼저 문다 → 여섯 종목이면 자본 90%."""
    med = float(act["risk_pct"].median())
    want = RISK_PCT_PER_TRADE / med * 100.0        # 위험 0.5%를 채우는 명목 비중 %
    n_full = int(100.0 // MAX_POSITION_PCT)
    notes.append(f"손절폭 중앙값 {med:.2f}%  →  위험 {RISK_PCT_PER_TRADE}%를 채우려면 "
                 f"명목 {want:.0f}%  →  상한 {MAX_POSITION_PCT:.0f}%가 먼저 문다  →  "
                 f"{n_full}종목이면 자본 {n_full * MAX_POSITION_PCT:.0f}%")

    f, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    ax.axis("off")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 1)

    for i in range(n_full):
        x0 = i * MAX_POSITION_PCT
        ax.add_patch(plt.Rectangle((x0 + 0.4, 0.42), MAX_POSITION_PCT - 0.8, 0.2,
                                   facecolor=STRAT, alpha=0.85))
        ax.text(x0 + MAX_POSITION_PCT / 2, 0.52, f"{i + 1}", ha="center", va="center",
                color=BG, fontsize=40, weight="bold")
    rest = 100 - n_full * MAX_POSITION_PCT
    ax.add_patch(plt.Rectangle((n_full * MAX_POSITION_PCT + 0.4, 0.42), rest - 0.8, 0.2,
                               facecolor="#161b22", edgecolor=MUTED, lw=2))
    ax.text(100 - rest / 2, 0.52, f"{rest:.0f}%", ha="center", va="center",
            color=MUTED, fontsize=30, weight="bold")

    ax.text(0, 0.70, f"한 종목 상한 {MAX_POSITION_PCT:.0f}%  ×  {n_full}종목  =  자본 "
                     f"{n_full * MAX_POSITION_PCT:.0f}%",
            color=FG, fontsize=46, weight="bold")
    ax.text(0, 0.30, f"손절폭 중앙값이 {med:.2f}% 라 위험 {RISK_PCT_PER_TRADE}% 를 채우려면 "
                     f"한 종목에 자본의 {want:.0f}% 가 들어간다", color=MUTED, fontsize=30)
    ax.text(0, 0.16, f"{n_full + 1}번째 종목은 살 돈이 없다.  자리 {MAX_POSITIONS}개는 "
                     "사실상 한 번도 안 쓰였다", color=WARN, fontsize=36, weight="bold")
    save(f, "06_six_names.png")


def main():
    print("차트 생성")
    notes = []
    act, bench, runs, years, start, end = load()
    notes.append(f"구간 {start.date()} ~ {end.date()} ({years:.1f}년) · "
                 f"actionable 셋업 {len(act):,}건")
    chart_equity(bench, runs, years, notes)
    chart_cost(bench, runs, years, notes)
    chart_yearly(bench, runs, notes)
    chart_slots(act, runs, notes)
    chart_skipped(runs, notes)
    chart_six(act, notes)

    OUT.mkdir(exist_ok=True)
    (OUT / "FIGURES.txt").write_text(
        "EP02 화면에 쓰인 실제 수치 — 대본과 대조할 것\n"
        f"출처: {REPO / TRADES}\n\n" + "\n".join(notes) + "\n"
        "\n7번 화면(앱 — 게이트 유지 / 러너 OFF)은 차트가 아니라 화면 녹화다.\n",
        encoding="utf-8")
    print("\n".join(notes))
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
