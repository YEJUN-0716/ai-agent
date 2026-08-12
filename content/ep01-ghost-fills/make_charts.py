"""EP01 영상용 차트 생성.

    python make_charts.py

출력: content/ep01-ghost-fills/charts/*.png (1920x1080)

숫자는 stock-analyzer 의 측정 결과 parquet 에서 직접 읽는다. 하드코딩하지
않는다 — 대본이 말하는 값과 화면에 뜨는 값이 갈리면 이 채널은 끝이다.
실제 값은 `charts/FIGURES.txt` 에 같이 떨궈서 대본과 대조할 수 있게 한다.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO = Path(r"C:\Users\1aass\stock-analyzer")
TRADES = REPO / "data" / "entry_rule_trades-daily.parquet"
OUT = Path(__file__).parent / "charts"

# measure_entry_rule.py 와 같은 값이라야 한다. 여기서 갈리면 대본이 틀린다.
COST_BPS = 40.0
IS_START = pd.Timestamp("2024-12-01")

BG, FG, MUTED = "#0b0f14", "#e6edf3", "#7d8590"
GHOST, REAL, WARN = "#f85149", "#3fb950", "#d29922"

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


def net_r(df, rule):
    """비용 차감 R. 체결된 것만. measure_entry_rule._net 과 같은 식."""
    sub = df[~df[f"{rule}_outcome"].isin(("nofill", "skip"))]
    return sub.assign(_net=sub[f"{rule}_r"] - (COST_BPS / 1e4) / (sub["risk_pct"] / 100))


def load_oos():
    d = pd.read_parquet(TRADES)
    return d[d["actionable"] & (d["entry_date"] < IS_START)].sort_values("entry_date")


def chart_result(oos, notes):
    """1번 — 유령 체결과 실제로 걸 수 있는 것의 누적 R."""
    a, c = net_r(oos, "A_백테스트"), net_r(oos, "C_갭반영")
    notes += [
        f"OOS actionable 셋업 {len(oos):,}건 (~{(IS_START - pd.Timedelta(days=1)).date()})",
        f"A 백테스트 가정   체결 {len(a):,}건  순평균 {a._net.mean():+.3f}R  누적 {a._net.sum():+,.0f}R",
        f"C 걸 수 있는 주문 체결 {len(c):,}건  순평균 {c._net.mean():+.3f}R  누적 {c._net.sum():+,.0f}R",
    ]

    f, ax = fig()
    for d, color, label in ((a, GHOST, "백테스트 가정 — 닿으면 샀다고 친다"),
                            (c, REAL, "실제로 걸 수 있었던 주문만")):
        ax.plot(d.entry_date, d._net.cumsum(), color=color, lw=3.5, label=label)
        ax.annotate(f"{d._net.mean():+.3f}R", (d.entry_date.iloc[-1], d._net.cumsum().iloc[-1]),
                    color=color, fontsize=30, weight="bold",
                    xytext=(14, -6), textcoords="offset points")

    ax.axhline(0, color=MUTED, lw=1)
    ax.set_title("같은 트레이드, 같은 기간.  '그 가격에 주문을 걸 수 있었나'만 바꿨다",
                 fontsize=32, weight="bold", pad=28, loc="left")
    ax.set_ylabel("누적 R (비용 차감)", fontsize=22)
    ax.legend(fontsize=24, frameon=False, loc="upper left")
    ax.tick_params(labelsize=20)
    save(f, "01_result.png")


def chart_r(notes):
    """2번 — R 이 뭔지."""
    f, ax = fig()
    ax.grid(False)
    for y, color, label in ((100, FG, "진입  100,000원"),
                            (90, GHOST, "손절   90,000원   ← 여기까지가 1R"),
                            (120, REAL, "목표  120,000원   ← 2R")):
        ax.axhline(y, color=color, lw=3)
        ax.text(0.015, y + 1.2, label, color=color, fontsize=28, weight="bold")

    ax.annotate("", xy=(0.9, 100), xytext=(0.9, 90),
                arrowprops=dict(arrowstyle="<->", color=WARN, lw=3))
    ax.text(0.915, 94.5, "1R", color=WARN, fontsize=34, weight="bold")

    ax.set_ylim(84, 126)
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.set_title("R = 손절까지의 거리.  퍼센트와 달리 위험을 같이 잰다",
                 fontsize=32, weight="bold", pad=28, loc="left")
    ax.tick_params(labelsize=20)
    save(f, "02_r_explained.png")


def chart_fill(notes):
    """3번 — 체결 판정 한 줄이 왜 유령인가."""
    f, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    ax.axis("off")

    ax.text(0.04, 0.86, "백테스트가 체결을 판정하던 방식", color=MUTED, fontsize=30)
    ax.text(0.04, 0.72, "이 봉의 저가가 진입 구간에 닿았으면\n→ 체결된 걸로 친다",
            color=FG, fontsize=44, weight="bold", linespacing=1.5)

    ax.text(0.04, 0.46, "봉은 장이 끝나야 완성된다.\n닿았다는 사실은 '닿고 난 뒤에' 안다.",
            color=WARN, fontsize=34, linespacing=1.6)

    ax.text(0.04, 0.22, "닿을 걸 미리 알고 주문을 넣은 것",
            color=GHOST, fontsize=48, weight="bold")
    ax.text(0.04, 0.10, "현실에서 나에겐 그 정보가 없다",
            color=MUTED, fontsize=30)
    save(f, "03_fill_rule.png")


def main():
    print("차트 생성")
    notes = []
    oos = load_oos()
    chart_result(oos, notes)
    chart_r(notes)
    chart_fill(notes)

    OUT.mkdir(exist_ok=True)
    (OUT / "FIGURES.txt").write_text(
        "EP01 화면에 쓰인 실제 수치 — 대본과 대조할 것\n"
        f"출처: {TRADES}\n왕복 비용 {COST_BPS:.0f}bp\n\n" + "\n".join(notes) + "\n",
        encoding="utf-8")
    print("\n".join(notes))
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
