#!/usr/bin/env python
"""대기 지정가를 취소할 수 있게 하면 성적이 나아지는가.

    python scripts/measure_pending_cancel.py            # 측정 + 리포트
    python scripts/measure_pending_cancel.py selftest    # 창 단축 산수 점검

사전 등록: `docs/superpowers/specs/2026-08-16-pending-cancel-design.md`
판정선(①②③·검출력 게이트)은 이 파일을 쓰기 전에 그 문서에 못 박았다.

## 왜 재는가

미체결 지정가가 체결/만료까지 **자리와 현금을 동시에 묶는다.** 앞선 측정에서
actionable 7,466건 중 5,245건(70%)이 **현금 부족**으로 버려졌다 — 자리 경합
1,130건(15%)의 세 배가 넘는다. 병목이 자리가 아니라 현금이면, 손댈 곳은
자리 수가 아니라 **그 현금을 묶고 있는 대기 주문**이다.

네트워크 無 — 저장 패널과 측정 parquet 만 읽는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.measure_portfolio import (  # noqa: E402
    RULE, COST_SWEEP, TRADES, cagr, closes, bench_curve, mdd, mtm_curve, simulate,
)
# 자는 PEAD·F-Score 판정이 쓴 것과 **같은 것**을 쓴다. 측정마다 자를 새로 깎으면
# 통과선의 의미가 측정마다 달라진다.
from scripts.measure_pead import excess_cagr_ci, mde_pp  # noqa: E402

OUT_MD = Path("docs/measurements/2026-08-16-pending-cancel.md")

BASE_WINDOW = 15        # 측정 하네스가 실제로 쓴 창 (measure_entry_rule.py:60)
RUNNER_WINDOW = 20      # 러너가 실제로 도는 창 (modules/virtual_broker.py:200)
N_PLACEBO = 100
JUDGE_COST = 6.0
MDE_GATE = 4.0          # %p. 넘으면 판정은 "실패"가 아니라 "미측정"
EFFECT_WORTH = 2.0      # %p. 배선할 값어치가 있다고 사전 등록에 적은 크기


def shorten_window(act: pd.DataFrame, days: int, cal: pd.DatetimeIndex):
    """대기 창을 `days` 거래일로 줄인다. **새 만료보다 늦은 체결은 몰수한다.**

    창을 줄이면 현금이 빨리 풀리지만 그만큼 **안 걸리는 주문**이 생긴다. 체결은
    그대로 두고 만료만 당기면 "취소했는데 체결은 됐다"는 유령 장부가 된다 —
    [[backtest-fill-must-be-placeable]] 가 15분봉 전략을 통째로 뒤집었던 그 결함과
    같은 종류다. 그래서 `fill_date` 가 새 만료보다 뒤면 outcome 을 nofill 로 바꾼다.
    """
    a = act.copy()
    pos = cal.searchsorted(a["entry_date"].to_numpy(), side="left")
    new = cal.to_numpy()[np.minimum(pos + days, len(cal) - 1)]
    a["expire_date"] = np.minimum(a["expire_date"].to_numpy(), new)
    late = a["fill_date"].notna() & (a["fill_date"] > a["expire_date"])
    a.loc[late, f"{RULE}_outcome"] = "nofill"
    return a, int(late.sum())


def daily_ret(pos_log: list[dict], close: pd.DataFrame) -> np.ndarray:
    """평가 기준 곡선의 일간 수익률. 두 팔을 **같은 날짜**로 묶어 비교하려고 쓴다."""
    c = mtm_curve(pos_log, close)
    return c.pct_change().fillna(0.0).to_numpy() if len(c) else np.zeros(len(close))


def run_arm(df: pd.DataFrame, close: pd.DataFrame, cancel: str | None = None,
            seed: int = 0, costs=COST_SWEEP) -> dict:
    out = {c: simulate(df, c, cancel=cancel, seed=seed) for c in costs}
    out["ret"] = daily_ret(out[JUDGE_COST]["pos_log"], close)
    return out


def selftest() -> int:
    """창 단축 산수만 손으로 확인한다. 시장 데이터 없이 돈다."""
    cal = pd.bdate_range("2020-01-01", periods=40)
    a = pd.DataFrame([
        # 3거래일째 체결 → 5거래일 창에서도 살아남는다.
        {"ticker": "A", "entry_date": cal[0], "fill_date": cal[3],
         "expire_date": cal[15], f"{RULE}_outcome": "win"},
        # 9거래일째 체결 → 5거래일 창에서는 **못 걸린다.**
        {"ticker": "B", "entry_date": cal[0], "fill_date": cal[9],
         "expire_date": cal[15], f"{RULE}_outcome": "win"},
        # 애초에 미체결 → 만료만 당겨진다.
        {"ticker": "C", "entry_date": cal[0], "fill_date": pd.NaT,
         "expire_date": cal[15], f"{RULE}_outcome": "nofill"},
    ])
    s, lost = shorten_window(a, 5, cal)
    assert lost == 1, lost
    assert list(s[f"{RULE}_outcome"]) == ["win", "nofill", "nofill"], s
    assert (s["expire_date"] == cal[5]).all(), s["expire_date"]

    # 창을 늘려도 원래 만료를 넘지 않는다 — parquet 에 없는 체결을 만들 수 없다.
    s2, lost2 = shorten_window(a, 30, cal)
    assert lost2 == 0 and (s2["expire_date"] == cal[15]).all(), s2["expire_date"]

    print("selftest 통과 (5건)")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        return selftest()

    d = pd.read_parquet(TRADES)
    d["entry_date"] = pd.to_datetime(d["entry_date"])
    for c in ("fill_date", "expire_date", "exit_date"):
        d[c] = pd.to_datetime(d[c])
    act = d[d["actionable"]].copy()
    start, end = act["entry_date"].min(), d["entry_date"].max()
    years = (end - start).days / 365.25
    close = closes(start, end)
    cal = close.index

    base = run_arm(act, close)
    arms: list[tuple[str, dict, int]] = [("기준선 (창 15, 취소 없음)", base, 0)]

    r1 = run_arm(act, close, cancel="grade")
    arms.append(("**R1** A후보가 대기 B를 취소", r1, 0))

    lost = {}
    for w in (5, 10):
        sw, lost[w] = shorten_window(act, w, cal)
        arms.append((f"{'**R2** ' if w == 5 else ''}창 {w}거래일",
                     run_arm(sw, close), lost[w]))

    # 러너의 20거래일 창은 **이 parquet 으로 못 잰다.** 만료가 15거래일에서 잘린
    # 채로 저장돼 있어 16~20 일째 체결이 애초에 기록에 없다 — 창을 늘려 봐야
    # 기준선과 같은 줄이 나온다(그 사실을 selftest 가 못 박는다). 재려면
    # FILL_WINDOW=20 으로 measure_entry_rule.py 를 다시 돌려야 한다.
    sw20, lost20 = shorten_window(act, RUNNER_WINDOW, cal)
    same20 = int((sw20[f"{RULE}_outcome"] == act[f"{RULE}_outcome"]).sum())

    # 위약: 같은 트리거, 등급 안 보고 무작위 취소. 판정 비용 한 구간만 돈다.
    plac = [run_arm(act, close, cancel="random", seed=s, costs=(JUDGE_COST,))
            for s in range(N_PLACEBO)]

    def excess(arm) -> tuple[float, float, float]:
        return excess_cagr_ci(arm["ret"], base["ret"])

    body = [
        "# 대기 주문 취소 규칙 — 묶인 현금을 풀면 나아지는가 (2026-08-16)",
        "",
        f"구간 {start.date()} ~ {end.date()} ({years:.1f}년) · 279종목 · 자리 10 · "
        f"판정 {JUDGE_COST:.0f}bp · 전부 **평가 기준(mtm) 곡선**.",
        "사전 등록: `docs/superpowers/specs/2026-08-16-pending-cancel-design.md`",
        "",
        "> **메모의 '20거래일'은 틀렸다.** 측정 하네스는 15거래일"
        "(`scripts/measure_entry_rule.py:60`), 러너는 20거래일"
        "(`modules/virtual_broker.py:200`) 로 돈다. 5.9년 성적은 15에서 나온 값이라 "
        "**기준선을 15로 잡았고, 20은 이 데이터로 못 잰다**(아래 상자).",
        "",
        "## 팔별 성적",
        "",
        "| 팔 | 잡은 트레이드 | 취소/몰수 | 현금 부족 스킵 | 연 수익률 (6bp) | MDD |",
        "|---|---|---|---|---|---|",
    ]
    for name, arm, lostn in arms:
        r = arm[JUDGE_COST]
        c = mtm_curve(r["pos_log"], close)
        killed = r["canceled"] or lostn
        body.append(
            f"| {name} | {r['taken']:,} | {killed:,} | {r['skipped_cash']:,} | "
            f"{cagr(float(c.iloc[-1]), years):+.2f}% | {mdd(c):.1f}% |")
    bench = bench_curve(start, end, close=close)
    body += [
        f"| 매수보유 (같은 패널 동일가중) | — | — | — | "
        f"{cagr(float(bench.iloc[-1]), years):+.2f}% | {mdd(bench):.1f}% |", "",
        "`취소/몰수` 는 R1 은 취소한 대기 건수, R2 는 **창이 줄어 못 걸린 체결** "
        "건수다. 둘 다 그 트레이드의 손익을 통째로 몰수한 뒤의 숫자다.", "",
        "> **러너가 실제로 도는 20거래일 창은 이 표에 없다 — 이 데이터로 못 잰다.** "
        f"parquet 의 만료가 15거래일에서 잘린 채 저장돼 있어 16~20일째 체결이 "
        f"애초에 기록에 없다(창을 20으로 늘려도 {same20:,}/{len(act):,}건이 "
        f"글자 그대로 같은 줄이다). **러너는 백테스트가 한 번도 안 재 본 설정으로 "
        "돌고 있다** — 재려면 `FILL_WINDOW=20` 으로 "
        "`scripts/measure_entry_rule.py` 를 다시 돌려야 한다.", "",
        "## 판정", "",
        "| 규칙 | ① 초과 연수익 95% 구간 | MDE | ② 비용 4구간 | ③ 위약 p | 판정 |",
        "|---|---|---|---|---|---|",
    ]

    def sign_ok(arm) -> tuple[bool, list[str]]:
        cells = []
        for c in COST_SWEEP:
            diff = (cagr(arm[c]["equity"], years) - cagr(base[c]["equity"], years))
            cells.append(f"{diff:+.2f}")
        return all(float(x) > 0 for x in cells), cells

    verdicts = {}
    for name, arm, _ in arms[1:]:
        pt, lo, hi = excess(arm)
        mde = mde_pp(arm["ret"], base["ret"])
        ok2, cells = sign_ok(arm)
        if arm is r1:
            pl = [excess(p)[0] for p in plac]
            p3 = float(np.mean([x >= pt for x in pl]))
            p3s = f"{p3:.3f}"
        else:
            p3, p3s = float("nan"), "—"       # 위약은 R1 의 주장(등급 선택)에만 건다
        ok1 = lo > 0
        ok3 = not (arm is r1) or p3 < 0.05
        v = ("미측정" if mde > MDE_GATE else
             "**통과**" if (ok1 and ok2 and ok3) else "실패")
        verdicts[name] = (v, pt, lo, hi, mde, ok1, ok2, ok3, p3)
        body.append(
            f"| {name} | {pt:+.2f}%p [{lo:+.2f}, {hi:+.2f}] {'O' if ok1 else 'X'} | "
            f"{mde:.2f}%p {'O' if mde <= MDE_GATE else 'X'} | "
            f"{' / '.join(cells)} {'O' if ok2 else 'X'} | "
            f"{p3s} {'O' if ok3 else 'X'} | {v} |")

    pl_pts = np.array([excess(p)[0] for p in plac])
    r1_pt = excess(r1)[0]
    body += [
        "",
        f"①은 기준선과 **같은 날짜로 묶은** 블록 부트스트랩(블록 20일, 2,000회)이다. "
        f"검출력 게이트 MDE ≤ {MDE_GATE:.1f}%p — 넘으면 '실패'가 아니라 **'미측정'** "
        f"이고, 배선할 값어치로 잡은 효과는 연 +{EFFECT_WORTH:.0f}%p 다. "
        "②는 비용 0/6/20/40bp 의 개선 폭(%p)이고 **네 개 전부 +** 라야 통과다.", "",
        "## ③ 위약 — 등급이 값을 한 건가, 그냥 돈을 푼 건가", "",
        f"R1 과 **같은 트리거**(막힌 A후보)에서 취소 대상만 **등급을 안 보고 무작위**"
        f"로 고른 팔 {N_PLACEBO}회.", "",
        "| | 초과 연수익 (6bp) |", "|---|---|",
        f"| R1 (등급 보고 취소) | {r1_pt:+.2f}%p |",
        f"| 위약 {N_PLACEBO}회 평균 | {pl_pts.mean():+.2f}%p |",
        f"| 위약 5~95 백분위 | [{np.percentile(pl_pts, 5):+.2f}, "
        f"{np.percentile(pl_pts, 95):+.2f}]%p |",
        f"| 위약 취소 건수 평균 | {np.mean([p[JUDGE_COST]['canceled'] for p in plac]):,.0f}건 "
        f"(R1 {r1[JUDGE_COST]['canceled']:,}건) |", "",
        "**위약이 R1 만큼 벌면 값을 한 건 등급 선택이 아니라 '묶인 돈을 푼 것'이다.** "
        "그러면 붙일 규칙은 등급 비교가 들어간 R1 이 아니라 훨씬 단순한 R2 다.", "",
        "> **이 위약은 건수가 안 맞는다 — 알고 쓴다.** R1 은 대기가 전부 A등급이면 "
        "취소할 게 없어 그냥 막히지만, 위약은 등급을 안 보므로 언제나 취소할 게 "
        "있다. 그래서 취소 건수가 한 자릿수 배 차이 난다. 즉 이 위약은 '등급 선택 "
        "vs 무작위'만이 아니라 '적게 취소 vs 많이 취소'도 같이 섞여 있다. 건수를 "
        "맞춘 위약을 새로 짜는 건 **결과를 본 뒤 설계를 바꾸는 것**이라 안 했다 — "
        "어차피 R1 은 ③ 전에 검출력 게이트에서 '미측정'으로 끝났다.", "",
        "## 그래서 무엇을 하나 — **러너를 안 건드린다**", "",
        f"세 규칙 다 검출력 게이트에서 걸렸다. MDE 가 {min(v[4] for v in verdicts.values()):.1f}"
        f"~{max(v[4] for v in verdicts.values()):.1f}%p 인데 배선할 값어치로 잡은 효과는 "
        f"연 +{EFFECT_WORTH:.0f}%p 다 — **이 설계는 그 크기를 잴 수 있는 자가 아니다.** "
        "'실패'라고 쓰면 안 되는 이유가 그거고, 그렇다고 점추정이 좋다고 켜면 "
        "잰 적 없는 걸 켜는 것이다.", "",
        "**창 길이를 바꾼 줄이 단조롭지 않다.** 같은 손잡이 하나를 돌렸는데 "
        + " → ".join(
            f"{w}일 {cagr(float(mtm_curve(a[JUDGE_COST]['pos_log'], close).iloc[-1]), years):+.1f}%"
            for w, a in ((5, arms[2][1]), (10, arms[3][1]), (15, base)))
        + " 로 오르내린다. 파라미터 하나에 결과가 이만큼 튀면 잰 것은 규칙이 아니라 "
        "**마찰**이다 — `2026-08-13-timing-vs-exposure.md` 에서 일봉/월말 판정이 "
        "갈렸던 것과 같은 신호다.", "",
        "다음에 이걸 다시 열려면 순서는 이렇다.", "",
        "1. `FILL_WINDOW=20` 으로 `measure_entry_rule.py` 를 다시 돌려 **러너가 "
        "실제로 도는 설정**의 기준선을 먼저 만든다. 지금은 그 줄이 없다.",
        "2. 검출력부터 본다. MDE 를 2%p 밑으로 못 내리면 이 질문은 5.9년 한 경로로는 "
        "답이 안 나오고, 그때는 창을 늘리거나 질문을 바꿔야 한다.",
        "3. 그 다음에야 규칙을 건다.", "",
        "**어느 쪽이든 매수보유는 여전히 안 보인다.** 기준선이 "
        f"{cagr(float(mtm_curve(base[JUDGE_COST]['pos_log'], close).iloc[-1]), years):+.1f}%, "
        f"매수보유가 {cagr(float(bench.iloc[-1]), years):+.1f}% 다. 대기 취소로 "
        "좁힐 수 있는 거리가 아니다 — 이 측정이 답하려던 건 '지금 도는 러너를 "
        "조금 낫게 만들 수 있나' 하나였고, 그 답이 **'이 자로는 모른다'** 다.", "",
        "재현: `python scripts/measure_pending_cancel.py` · 산수 점검 "
        "`... selftest` + `python scripts/measure_portfolio.py selftest`", "",
    ]
    text = "\n".join(body)
    print(text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
