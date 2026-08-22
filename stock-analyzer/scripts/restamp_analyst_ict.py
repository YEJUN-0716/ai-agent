#!/usr/bin/env python
"""저장된 ICT 점수를 **고친 코드로** 다시 찍는다 — 기록 수리 도구.

    python scripts/restamp_analyst_ict.py            # 미리보기 (안 쓴다)
    python scripts/restamp_analyst_ict.py --write    # 실제로 덮어쓴다

## 왜 필요한가

`find_bos_choch` 가 이벤트를 스윙 **쌍 순서**로 돌려주고 있었다(PR #169).
소비자는 `events[-1]` 을 "가장 최근 구조 전환"으로 읽으므로, 오래된 레벨이
뒤늦게 깨진 판에서는 반대 방향을 집었다. 실측 4.67% 의 봉에서 ICT 애널리스트
점수가 평균 49.4점 어긋났고, 그 값이 `data/analyst_log*` 에 그대로 남아 있다.

## 무엇을 건드리는가

**`ict` 만 다시 찍는다.** `chart` 는 손대지 않는다 — 야후가 배당·분할로 과거
수정주가를 계속 갱신하기 때문에 지금 다시 계산하면 그날 화면에 뜬 값과
어긋난다(`backfill_analyst_log.py` 문서 참고). `quant`·`quant_pit` 은 시점
데이터라 애초에 되돌릴 수 없다.

`verdict` 는 저장된 값에 **차이만 더한다** — `verdict += w_ict × Δict`.
가중치는 오늘의 `ic_weights.json` 이 아니라 **그날 기록에서 최소제곱으로
되찾는다**(가중치는 매주 바뀌므로 오늘 값을 소급하면 그건 다른 날의 자다).
잔차로 되찾기가 성공했는지 매일 확인하고, 실패한 날은 건드리지 않는다.

## 재현 가능한 것만 덮어쓴다

가장 중요한 규칙이다. 값이 달라진 이유가 "결함을 고쳐서"가 아니라 "패널이
그 사이 바뀌어서"일 수 있다. 그래서 각 기록마다

  1. **결함을 되살린 코드**로 다시 계산해 저장값과 같은지 본다
  2. 같으면 → 재현된 것이다. 고친 값으로 덮어쓴다
  3. 다르면 → 그날 입력을 재현하지 못한 것이다. **그대로 둔다**

2026-08-22 전수 실측: 143,791건 중 **98.8% 가 재현**됐고(140,671건), 재현 못 한
1,733건(1.2%)은 수정주가 갱신으로 구조가 달라진 자리라 그대로 뒀다.

⚠️ 이 검사를 만들 때 한 번 틀렸다. "결함을 되살린 코드"라며 **이미 고쳐진
함수를 다시 감싼** 탓에 옛값이 아니라 새값을 재고 있었고, 그래서 첫 실행이
`restamped 0` 을 냈다. 옛 동작을 재현하려면 사본이 필요하다 — 지금 함수의
출력을 후처리해서 옛 순서를 되살릴 수는 없다.
"""
import argparse
import io
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 점수 계산에 실제로 쓴 창 — signal_worker.ANALYST_PANEL_DAYS 와 같아야 한다.
WINDOW_DAYS = 400
MIN_BARS = 60
PANEL = os.path.join("data", "price_panel_v1.parquet")
STORES = (os.path.join("data", "analyst_log_backfill"),
          os.path.join("data", "analyst_log"))
DECIMALS = 1
# 가중치 되찾기 — 저장된 점수가 소수 1자리라 잔차는 반올림 크기를 넘지 않아야
# 한다. 넘으면 verdict 가 세 점수의 가중평균이 아니라는 뜻이므로 손대지 않는다.
MAX_WEIGHT_RESIDUAL = 0.2


def legacy_find_bos_choch(df, swings):
    """**결함이 있던 그대로의 사본** (PR #169 이전, f0ae0d9).

    기록을 재현하려면 그때 돌던 코드가 필요하다. 지금 함수는 마지막에
    idx 로 세워서 돌려주므로 그 출력에서는 옛 순서를 되살릴 수 없다 —
    그래서 루프를 통째로 베껴 둔다. `sorted(legacy(...)) == 현재(...)` 를
    테스트가 잠근다(tests/test_restamp_analyst_ict.py).

    ⚠️ 새 코드가 아니다. 고치지 말 것. 이 사본이 바뀌면 재현이 깨진다.
    """
    if swings.empty or len(swings) < 2:
        return []
    closes = df["Close"].values
    dates = df.index
    events = []
    swing_list = swings.sort_values("idx").to_dict("records")

    for j in range(1, len(swing_list)):
        prev, curr = swing_list[j - 1], swing_list[j]
        after = curr["idx"] + 1
        if after >= len(closes):
            continue

        if prev["type"] == "H":
            for k in range(after, len(closes)):
                if closes[k] > prev["price"]:
                    label = "BOS_bull" if curr["type"] == "L" else "CHoCH_bull"
                    events.append({"type": label, "price": prev["price"],
                                   "date": dates[k], "idx": k})
                    break
        elif prev["type"] == "L":
            for k in range(after, len(closes)):
                if closes[k] < prev["price"]:
                    label = "BOS_bear" if curr["type"] == "H" else "CHoCH_bear"
                    events.append({"type": label, "price": prev["price"],
                                   "date": dates[k], "idx": k})
                    break
    return events


def _score_ticker(task):
    """한 종목의 (날짜 → (옛값, 새값)). 새값은 순서가 실제로 갈린 날만 다시 잰다."""
    ticker, df, dates = task
    import modules.ict_analysis as ict
    from modules.analyst_team import ict_score

    fixed = ict.find_bos_choch
    state = {"split": False}

    def legacy_spy(frame, swings):
        """옛 동작 그대로 돌려주되, 고친 코드와 답이 갈리는지 표시해 둔다."""
        events = legacy_find_bos_choch(frame, swings)
        if events:
            by_pair = events[-1]["type"]                       # 옛 코드가 집던 것
            by_time = max(events, key=lambda e: e["idx"])["type"]   # 고친 코드가 집는 것
            if ("bull" in by_pair) != ("bull" in by_time):
                state["split"] = True
        return events

    def score(cut):
        return round(ict_score(ict.ict_factor_score(cut),
                               ict.calc_ict_adjustment(cut)["adjustment"]), DECIMALS)

    out = {}
    for date in dates:
        asof = pd.Timestamp(date)
        cut = df[(df.index <= asof) & (df.index > asof - pd.Timedelta(days=WINDOW_DAYS))]
        # 마지막 봉이 그 날짜여야 한다. 휴장일이나 패널이 못 미치는 날은 건너뛴다 —
        # 다른 날의 구조로 그 날 점수를 덮어쓰면 수리가 아니라 훼손이다.
        if len(cut) < MIN_BARS or cut.index[-1] != asof:
            continue
        state["split"] = False
        ict.find_bos_choch = legacy_spy
        old = score(cut)
        if state["split"]:
            ict.find_bos_choch = fixed
            new = score(cut)
        else:
            new = old                      # 순서가 안 갈렸으면 고쳐도 같은 값이다
        ict.find_bos_choch = fixed
        out[date] = (old, new)
    return ticker, out


def recover_weights(scores):
    """그날 기록에서 방향성 3인 가중치를 되찾는다 → (w_ict, 최대잔차) | None.

    verdict = w_c·chart + w_q·quant + w_i·ict, 합 = 1. 종목이 수백이고 미지수가
    둘이라 과결정계다 — 그날 실제로 쓴 가중치가 그대로 나온다.
    """
    rows = [(s["chart"], s["quant"], s["ict"], s["verdict"])
            for s in scores.values()
            if all(k in s for k in ("chart", "quant", "ict", "verdict"))]
    if len(rows) < 20:
        return None
    arr = np.array(rows, dtype=float)
    c, q, i, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    a = np.column_stack([c - i, q - i])
    (wc, wq), *_ = np.linalg.lstsq(a, v - i, rcond=None)
    w = np.array([wc, wq, 1.0 - wc - wq])
    resid = float(np.abs(arr[:, :3] @ w - v).max())
    if resid > MAX_WEIGHT_RESIDUAL or not (0.0 <= w[2] <= 1.0):
        return None
    return float(w[2]), resid


def load_frames():
    panel = pd.read_parquet(PANEL)
    frames = {}
    for ticker in sorted({t for _, t in panel.columns}):
        df = pd.DataFrame({f: panel[(f, ticker)]
                           for f in ("Open", "High", "Low", "Close")}).dropna()
        if len(df) >= MIN_BARS:
            frames[ticker] = df
    return frames


def load_store(root):
    """{연도파일: [행, ...]} — 줄 순서를 그대로 지킨다."""
    out = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(root, name)
        out[path] = [json.loads(line) for line in io.open(path, encoding="utf-8")
                     if line.strip()]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="실제로 파일을 덮어쓴다")
    ap.add_argument("--workers", type=int, default=max(os.cpu_count() - 1, 1))
    args = ap.parse_args()

    frames = load_frames()
    print(f"패널 {len(frames)}종목 · "
          f"{min(f.index[0] for f in frames.values()).date()} ~ "
          f"{max(f.index[-1] for f in frames.values()).date()}")

    stores = {root: load_store(root) for root in STORES}
    wanted = {}                                   # ticker -> {날짜}
    total = 0
    for by_path in stores.values():
        for rows in by_path.values():
            for row in rows:
                for ticker, s in row["scores"].items():
                    if "ict" in s:
                        total += 1
                        wanted.setdefault(ticker, set()).add(row["date"])
    print(f"ict 가 있는 기록 {total:,}건 · 종목 {len(wanted)}")

    tasks = [(t, frames[t], sorted(d)) for t, d in sorted(wanted.items()) if t in frames]
    missing = sorted(set(wanted) - set(frames))
    if missing:
        print(f"패널에 없는 종목 {len(missing)}: {', '.join(missing[:8])}")

    computed = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n, (ticker, per_date) in enumerate(pool.map(_score_ticker, tasks), 1):
            computed[ticker] = per_date
            if n % 25 == 0 or n == len(tasks):
                print(f"  {n}/{len(tasks)}종목", flush=True)

    stats = {"restamped": 0, "unchanged": 0, "unreproducible": 0,
             "no_bars": 0, "no_ticker": 0, "verdict": 0}
    weight_skips = []
    deltas = []
    for by_path in stores.values():
        for rows in by_path.values():
            for row in rows:
                rec = recover_weights(row["scores"])
                if rec is None and any("verdict" in s for s in row["scores"].values()):
                    weight_skips.append(row["date"])
                w_ict = rec[0] if rec else None
                for ticker, s in row["scores"].items():
                    if "ict" not in s:
                        continue
                    per_date = computed.get(ticker)
                    if per_date is None:
                        stats["no_ticker"] += 1
                        continue
                    pair = per_date.get(row["date"])
                    if pair is None:
                        stats["no_bars"] += 1
                        continue
                    old, new = pair
                    if old != s["ict"]:
                        stats["unreproducible"] += 1
                        continue
                    if new == s["ict"]:
                        stats["unchanged"] += 1
                        continue
                    delta = round(new - s["ict"], DECIMALS)
                    deltas.append(delta)
                    s["ict"] = new
                    stats["restamped"] += 1
                    if "verdict" in s and w_ict is not None:
                        s["verdict"] = round(s["verdict"] + w_ict * delta, DECIMALS)
                        stats["verdict"] += 1

    print("\n-- 결과 --")
    for k, v in stats.items():
        print(f"  {k:16s} {v:,}")
    if deltas:
        a = np.abs(deltas)
        print(f"  바뀐 값의 폭: 중위 {np.median(a):.1f}점 · 평균 {a.mean():.1f} "
              f"· 최대 {a.max():.1f}")
    if weight_skips:
        print(f"  가중치 되찾기 실패로 verdict 안 건드린 날 {len(weight_skips)}: "
              f"{', '.join(weight_skips[:5])}")

    if not args.write:
        print("\n미리보기다 — 아무것도 안 썼다. 덮어쓰려면 --write")
        return 0

    for by_path in stores.values():
        for path, rows in by_path.items():
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  썼다 {path} ({len(rows)}일)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
