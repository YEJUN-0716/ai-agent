"""EP04 영상용 차트 생성.

    python make_charts.py

출력: content/ep04-smallcap/charts/*.png (1920x1080) + charts/FIGURES.txt

EP01~03 과 같은 규칙이다 — **숫자를 하드코딩하지 않는다.**
- 판정·검출력·다섯 줄 숫자는 `stock-analyzer/docs/measurements/*.md` 에서 **읽는다**
  (측정 코드를 다시 돌리면 20분이라, 문서를 재료로 쓴다. 문서는 그 코드가 쓴 것이다)
- 곡선 두 장(벤치마크 비교·BBBY)은 패널에서 **다시 계산한다**

대본이 말하는 값과 화면에 뜨는 값이 갈리면 이 채널은 끝이다. 실제로 EP02 에서
차트가 대본 오류를 잡았다. `charts/FIGURES.txt` 를 대본과 대조할 것.
"""
import os
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import pandas as pd                       # noqa: E402

REPO = Path(r"C:\Users\1aass\stock-analyzer")
OUT = Path(__file__).parent / "charts"
DOCS = REPO / "docs" / "measurements"

os.chdir(REPO)                            # 측정 코드의 경로가 전부 상대경로다
sys.path.insert(0, str(REPO))
# measure_fscore 는 **argv 로** 유니버스를 고른다. 여기서 켜 줘야 `closes` 가
# 소형주 패널을 읽는다 — 스위치를 코드에 하나만 둔 대가로 붙는 한 줄이다.
sys.argv.append("smallcap")
from scripts.measure_fscore import START, END, smallcap_members  # noqa: E402
from scripts.measure_portfolio import bench_curve, cagr, closes   # noqa: E402

BG, FG, MUTED = "#0b0f14", "#e6edf3", "#7d8590"
STRAT, BENCH, WARN, DEAD = "#f85149", "#3fb950", "#d29922", "#a371f7"

plt.rcParams.update({
    "font.family": ["Malgun Gothic", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "figure.facecolor": BG, "axes.facecolor": BG,
    "text.color": FG, "axes.labelcolor": FG,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": "#21262d", "grid.color": "#161b22",
    "font.size": 20,
})

LOG = []


def note(line):
    print(line)
    LOG.append(line)


def fig():
    f, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(True, axis="y", alpha=0.6, lw=0.8)
    return f, ax


def save(f, name):
    OUT.mkdir(exist_ok=True)
    f.savefig(OUT / name, facecolor=BG, bbox_inches="tight", pad_inches=0.4)
    plt.close(f)
    note(f"  저장 {name}")


# ── 측정 문서에서 숫자 읽기 ────────────────────────────────────────────
def grab(pattern, text, name):
    m = re.search(pattern, text)
    if not m:
        raise SystemExit(f"측정 문서에서 '{name}' 를 못 찾았습니다 — 문서 형식이 바뀌었나요?")
    return m.groups()


def read_doc(fname: str) -> dict:
    t = (DOCS / fname).read_text(encoding="utf-8")
    ic, tval, n = grab(r"평균 IC ([+-][\d.]+) · t=([+-][\w.]+) \(단면 (\d+)개\)", t, "① IC")
    pt, lo, hi = grab(r"\| ([+-][\d.]+)%p · 95% \[([+-][\d.]+), ([+-][\d.]+)\]", t, "② 구간")
    out = {"ic": float(ic), "t": float(tval) if tval != "nan" else float("nan"),
           "n_ic": int(n), "excess": float(pt), "lo": float(lo), "hi": float(hi)}
    mde, = grab(r"\| MDE \(연 %p, 하한\) \| ([\d.]+) \|", t, "MDE")
    out["mde"] = float(mde)
    for label, key in (("전략", "strat"), ("같은 노출 매수보유", "flat"),
                       ("원본 매수보유 100%", "bench"), ("위약", "placebo"),
                       ("BM 스크린 없는", "nobm")):
        m = re.search(rf"\| {label}[^|]*\| ([+-][\d.]+)% \| ([-\d.]+)%", t)
        if m:
            out[key], out[key + "_mdd"] = float(m.group(1)), float(m.group(2))
    m = re.search(r"평균 노출 \d+% · 거래 (\d+)건", t)
    out["trades"] = int(m.group(1)) if m else 0
    m = re.search(r"보유 중 상폐 (\d+)건", t)
    out["dead"] = int(m.group(1)) if m else 0
    m = re.search(r"8항목을 다 채운 종목 (\d+)/(\d+)", t)
    out["scored"], out["names"] = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return out


SC = read_doc("2026-08-14-fscore-smallcap.md")          # 판정 행
LC = read_doc("2026-08-14-fscore.md")                   # 대형주 (지난 측정)
FULL = read_doc("2026-08-14-fscore-smallcap-full.md")   # 2차 행
SEAL = read_doc("2026-08-14-fscore-smallcap-sealed.md")


# ── 1. 판정 ────────────────────────────────────────────────────────────
def chart_verdict():
    f, ax = fig()
    ax.axis("off")
    rows = [("① 단면 IC (252일)", f"t = {SC['t']:+.2f}", "통과선  t ≥ +2  (단측)", "X"),
            ("② 초과 연수익", f"{SC['excess']:+.2f}%p", "95% 하한 > 0", "△"),
            ("검출력 (MDE)", f"{SC['mde']:.2f}%p", "한계  ≤ 10%p", "X")]
    ax.text(0.5, 0.93, "판정:  검출력 부족 — 미측정", ha="center", size=52, color=WARN,
            weight="bold", transform=ax.transAxes)
    for i, (a, b, c, d) in enumerate(rows):
        y = 0.68 - i * 0.20
        ax.text(0.06, y, a, size=34, color=FG, transform=ax.transAxes)
        ax.text(0.46, y, b, size=34, color=FG, weight="bold", transform=ax.transAxes)
        ax.text(0.64, y, c, size=26, color=MUTED, transform=ax.transAxes)
        ax.text(0.94, y, d, size=40, color=STRAT if d == "X" else WARN,
                weight="bold", transform=ax.transAxes)
    ax.text(0.06, 0.06, "둘 다 통과해야 통과다. 그런데 ②는 통과도 실패도 아니다 — 잴 힘이 없었다.",
            size=24, color=MUTED, transform=ax.transAxes)
    save(f, "01-verdict.png")
    note(f"[1] 판정 t={SC['t']:+.2f} · 초과 {SC['excess']:+.2f}%p · MDE {SC['mde']:.2f}")


# ── 2. 검출력이 오히려 나빠졌다 ────────────────────────────────────────
def chart_mde():
    f, ax = fig()
    xs = [f"대형주 {LC['names']:,}종목\n(지난 측정)",
          f"소형주 {SC['names']:,}종목\n(이번 측정)"]
    vals = [LC["mde"], SC["mde"]]
    bars = ax.bar(xs, vals, color=[BENCH, STRAT], width=0.5)
    ax.axhline(10, color=WARN, lw=3, ls="--")
    ax.text(1.45, 10.6, "판정 가능 한계  10%p", color=WARN, size=24, ha="right")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.5, f"{v:.2f}%p",
                ha="center", size=34, color=FG, weight="bold")
    ax.set_ylabel("최소 검출 가능 효과 (연 %p)")
    ax.set_title(f"종목을 {SC['names'] / LC['names']:.0f}배로 늘렸더니 판정할 힘은 절반이 됐다",
                 size=40, pad=30)
    ax.set_ylim(0, max(vals) * 1.25)
    save(f, "02-mde.png")
    note(f"[2] MDE 대형주 {LC['mde']:.2f} → 소형주 {SC['mde']:.2f}")


# ── 3. 점추정 하나만 읽으면 안 되는 이유 ───────────────────────────────
def chart_ci():
    f, ax = fig()
    ax.hlines(0, SC["lo"], SC["hi"], color=MUTED, lw=18, alpha=0.5)
    ax.plot([SC["excess"]], [0], "o", ms=28, color=STRAT)
    ax.axvline(0, color=BENCH, lw=3, ls="--")
    for x, lab, dy in ((SC["lo"], f"{SC['lo']:+.2f}", -0.35), (SC["hi"], f"{SC['hi']:+.2f}", -0.35),
                       (SC["excess"], f"점추정 {SC['excess']:+.2f}%p", 0.35)):
        ax.text(x, dy, lab, ha="center", size=30, color=FG)
    ax.text(0, 0.62, "0 = 그냥 들고 있는 것과 같다", ha="center", size=26, color=BENCH)
    ax.set_ylim(-0.8, 0.9)
    ax.set_yticks([])
    ax.set_xlabel("같은 노출 매수보유 대비 초과 연수익 (%p)")
    ax.set_title("구간이 0을 품고 있으면 점추정은 없는 것과 구분이 안 된다", size=40, pad=30)
    ax.grid(False)
    save(f, "03-ci.png")
    note(f"[3] 구간 [{SC['lo']:+.2f}, {SC['hi']:+.2f}] · 점추정 {SC['excess']:+.2f}")


# ── 4. 위약이 전략을 이겼다 ────────────────────────────────────────────
def chart_placebo():
    f, ax = fig()
    labels = ["전략\n(고BM ∩ F≥7)", "위약\n(점수만 섞음)", "BM 스크린 없이\nF≥7", "그냥 매수보유"]
    vals = [SC["strat"], SC["placebo"], SC["nobm"], SC["bench"]]
    colors = [STRAT, WARN, MUTED, BENCH]
    bars = ax.bar(labels, vals, color=colors, width=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.8, f"{v:+.1f}%",
                ha="center", size=32, color=FG, weight="bold")
    ax.set_ylabel("연 수익률 (%)")
    ax.set_title("점수를 섞은 줄이 더 벌었다", size=40, pad=30)
    ax.set_ylim(0, max(vals) * 1.2)
    save(f, "04-placebo.png")
    note(f"[4] 전략 {SC['strat']:+.1f}% · 위약 {SC['placebo']:+.1f}% · "
         f"BM무 {SC['nobm']:+.1f}% · 매수보유 {SC['bench']:+.1f}%")


# ── 5. 벤치마크가 죽은 종목을 세면 얼마나 달라지나 ─────────────────────
def chart_benchmark():
    """**이 편의 핵심 그림.** 같은 패널·같은 구간인데, 기준선을 어떻게 만드느냐로
    갈린다. 위: 살아남은 종목만 산 줄(옛 코드). 아래: 그달 구성종목을 다 산 줄."""
    close = closes(START, END)
    members = smallcap_members(START, END)
    names = sorted(set(members["asset_id"]) & set(close.columns))
    close = close[names]
    fair = bench_curve(START, END, members=members, close=close)
    survivor = bench_curve(START, END, close=close)      # members 없으면 옛 방식
    years = (close.index[-1] - close.index[0]).days / 365.25

    f, ax = fig()
    ax.plot(survivor.index, survivor.values, color=STRAT, lw=3.5,
            label=f"시작·종료일에 둘 다 값이 있는 종목만  (연 {cagr(float(survivor.iloc[-1]), years):+.1f}%)")
    ax.plot(fair.index, fair.values, color=BENCH, lw=3.5,
            label=f"그달 구성종목 전부, 상폐는 마지막 종가 청산  (연 {cagr(float(fair.iloc[-1]), years):+.1f}%)")
    ax.legend(loc="upper left", facecolor=BG, edgecolor="#21262d", labelcolor=FG, fontsize=24)
    ax.set_ylabel("1 달러가 얼마가 됐나")
    ax.set_title("같은 데이터, 같은 구간 — 죽은 종목을 세느냐만 다르다", size=40, pad=30)
    ax.grid(True, alpha=0.6, lw=0.8)
    save(f, "05-benchmark.png")
    note(f"[5] 기준선 생존자만 {cagr(float(survivor.iloc[-1]), years):+.1f}% vs "
         f"편향 없음 {cagr(float(fair.iloc[-1]), years):+.1f}% "
         f"(차이 {cagr(float(survivor.iloc[-1]), years) - cagr(float(fair.iloc[-1]), years):+.1f}%p)")


# ── 6. BBBY — 같은 심볼, 다른 회사 ─────────────────────────────────────
def chart_bbby():
    """`886158:BBBY` 로 온 봉과, 그 회사의 진짜 봉(`886158:BBBYQ`)을 나란히."""
    shard_dir = REPO / "data" / "smallcap" / "prices_adj"
    got = {}
    for p in sorted(shard_dir.glob("*.parquet")):
        d = pd.read_parquet(p)
        hit = d[d["ticker"].isin(["BBBY", "BBBYQ"])]
        for tk, g in hit.groupby("ticker"):
            got[tk] = g.set_index("date")["close"].sort_index()
        if len(got) == 2:
            break
    if len(got) < 2:
        note("[6] BBBY 샤드를 못 찾아 건너뜁니다")
        return
    win = slice(pd.Timestamp("2022-06-01"), pd.Timestamp("2023-12-31"))
    f, ax = fig()
    ax.plot(got["BBBY"][win].index, got["BBBY"][win].values, color=STRAT, lw=3.5,
            label="심볼 `BBBY` 로 받은 봉  (실은 2025년에 이 티커를 물려받은 회사)")
    ax.plot(got["BBBYQ"][win].index, got["BBBYQ"][win].values, color=DEAD, lw=3.5,
            label="같은 회사의 진짜 봉  (`BBBYQ` — 파산 표기)")
    ax.set_yscale("log")
    ax.legend(loc="upper right", facecolor=BG, edgecolor="#21262d", labelcolor=FG, fontsize=24)
    ax.set_ylabel("종가 (달러, 로그 눈금)")
    ax.set_title("파산한 회사의 자리에 산 회사의 가격이 앉아 있었다", size=40, pad=30)
    ax.grid(True, alpha=0.6, lw=0.8)
    save(f, "06-bbby.png")
    for tk in ("BBBY", "BBBYQ"):
        s = got[tk][win]
        near = s.asof(pd.Timestamp("2023-04-28"))
        note(f"[6] {tk} 2023-04-28 종가 ${near:.2f}")


# ── 7. 이 유니버스는 매년 죽는다 ───────────────────────────────────────
def chart_delistings():
    import json
    rep = json.loads((REPO / "data" / "smallcap_universe_report.json").read_text(encoding="utf-8"))
    d = rep["selftest"]["delistings_by_year"]
    f, ax = fig()
    ax.bar(list(d), [int(v) for v in d.values()], color=DEAD, width=0.6)
    for i, v in enumerate(d.values()):
        ax.text(i, int(v) + 6, str(int(v)), ha="center", size=28, color=FG)
    ax.set_ylabel("그 해 구성종목 중 12개월 안에 상장폐지된 수")
    ax.set_title(f"매월 {int(rep['universe']['median_members']):,}종목 · 해마다 이만큼 죽는다",
                 size=40, pad=30)
    save(f, "07-delistings.png")
    note(f"[7] 상폐/연도 {d} · 유니버스 {rep['universe']['rows']:,}행")


# ── 8. 대형주와 소형주의 ① ─────────────────────────────────────────────
def chart_ic():
    f, ax = fig()
    xs = ["대형주 (지난 측정)", "소형주 (이번 측정)"]
    vals = [LC["t"], SC["t"]]
    bars = ax.bar(xs, vals, color=[MUTED, BENCH], width=0.45)
    ax.axhline(2, color=WARN, lw=3, ls="--")
    ax.axhline(0, color=FG, lw=1.5)
    ax.text(1.45, 2.15, "통과선  t = +2", color=WARN, size=24, ha="right")
    for b, v, ic in zip(bars, vals, [LC["ic"], SC["ic"]]):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.2 if v > 0 else -0.5),
                f"t={v:+.2f}\nIC {ic:+.4f}", ha="center", size=30, color=FG, weight="bold")
    ax.set_ylabel("날짜 블록 부트스트랩 t")
    ax.set_title("부호는 뒤집혔지만, 통과선을 못 넘은 t 는 방향을 말할 자격이 없다",
                 size=36, pad=30)
    save(f, "08-ic.png")
    note(f"[8] 대형주 IC {LC['ic']:+.4f} t={LC['t']:+.2f} · 소형주 IC {SC['ic']:+.4f} t={SC['t']:+.2f}")


def main():
    note(f"측정 문서 기준 — 판정 {DOCS / '2026-08-14-fscore-smallcap.md'}")
    chart_verdict()
    chart_mde()
    chart_ci()
    chart_placebo()
    chart_benchmark()
    chart_bbby()
    chart_delistings()
    chart_ic()
    note("")
    note(f"2차 행(전구간): t={FULL['t']:+.2f} · 초과 {FULL['excess']:+.2f}%p "
         f"[{FULL['lo']:+.2f}, {FULL['hi']:+.2f}] · MDE {FULL['mde']:.2f} · 거래 {FULL['trades']}건")
    note(f"봉인 구간: 초과 {SEAL['excess']:+.2f}%p [{SEAL['lo']:+.2f}, {SEAL['hi']:+.2f}] "
         f"· MDE {SEAL['mde']:.2f} · 단면 {SEAL['n_ic']}개")
    note(f"보유 중 상폐: 본구간 {SC['dead']}건 · 거래 {SC['trades']}건")
    OUT.mkdir(exist_ok=True)
    (OUT / "FIGURES.txt").write_text("\n".join(LOG), encoding="utf-8")
    print(f"\n대본 대조용: {OUT / 'FIGURES.txt'}")


if __name__ == "__main__":
    main()
