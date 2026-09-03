"""CRT 레인지 정의 — **현행(롤링) vs 정본(고정 HTF 캔들)**.

    python -m scripts.pilot_crt_range_definition           # 대조표
    python -m scripts.pilot_crt_range_definition selftest  # 벡터화 판정이 프로덕션과 같은지

질문: `detect_crt_setup` 이 쓰는 "직전 period 봉의 롤링 max/min" 은 정본 CRT 의
"HTF 캔들 하나의 고·저" 와 **다른 물건인가, 같은 걸 달리 쓴 것인가.**

정본(theinnercircletraders / innercircletrader.net)은 HTF 캔들 **하나**가 레인지고,
그 경계는 캔들이 닫히기 전까지 고정이다. 우리 구현은 매일 창이 미끄러지는 롤링
max/min 이라 경계가 매일 바뀐다. 두 팔은 서로 다른 구간을 보므로 발동일이 갈린다 —
얼마나 갈리는지가 이 스크립트다.

## 이건 사전 등록이 아니라 **이미 돌고 있는 항의 감사**다

CRT Phase2 는 지금 러너에서 ICT 조정점수 ±30 중 ±20 을 차지한다. 새 노선을 여는
게 아니라 **이미 배포된 정의가 정본과 어긋났는지** 보는 것이므로, 결과를 먼저 보는
문제(peeking)가 아니다. 다만 아래 둘은 성격이 다르니 섞어 읽지 않는다:

  1. **발동률·일치율** — 결과를 안 본다. 정의 차이 그 자체의 크기다. 이게 본론.
  2. **다음날 수익** — 결과를 본다. **탐색적 관측이고 판정이 아니다.** CRT 는
     지금껏 한 번도 검증된 적이 없으므로(메모리: ICT 계열 수익률 예측 4번 실패)
     여기서 부호가 좋게 나와도 "된다"가 아니다. 그래서 위약 MDE 를 같이 찍는다 —
     **MDE 를 못 넘는 숫자는 방향을 읽지 않는다.**

## 함정

- **프로덕션 가격 캐시를 건드리지 않는다.** 네트워크를 안 쓰고 이미 있는
  `data/price_panel_v1.parquet`(일봉 OHLC)만 읽는다. 측정 스크립트가 프로덕션
  캐시를 낡게 만든 전례가 있다(module-review-chunks 3차).
- **두 팔의 발동 조건은 글자 그대로 같다.** 레인지를 만드는 방법 하나만 다르다.
  조건식을 같이 손대면 무엇 때문에 달라졌는지 못 가른다.
- **벡터화가 프로덕션과 같은지 selftest 가 확인한다.** 판정을 옮겨 적다 틀리면
  이 표 전체가 무의미해진다. 무작위 표본을 `detect_crt_setup` 에 직접 먹여 맞춘다.
- **여기 MDE 는 하한이다 — 진짜 MDE 는 이보다 크다.** 위약이 종목별로 독립하게
  날짜를 섞으므로, 3일 저가 스윕이 시장 전체에서 같은 날 몰리는 구조(횡단면 상관)를
  없애 버린다. 없앤 만큼 분산이 작게 나오고 MDE 가 낙관적으로 찍힌다. **"안 넘었다"
  결론은 이 편향에 안전하지만(진짜 MDE 는 더 크니까), 나중에 어떤 줄이 MDE 를
  넘었다고 주장하려면 날짜 통째로 섞는 위약으로 다시 재야 한다.**
  (power-with-correlation 에서 "하한이 하한이 아니었다"로 한 번 데인 자리다)
- 거래비용·체결은 안 넣었다. 여기서 재는 건 정의 차이지 전략 성적이 아니다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.ict_analysis import detect_crt_setup  # noqa: E402

PANEL = Path(__file__).resolve().parents[1] / "data" / "price_panel_v1.parquet"
PERIOD = 3      # 프로덕션 기본값 (calc_ict_adjustment 의 period=3*scale, 러너는 scale=1)
REPS = 400      # 위약 반복
SEED = 20260903


def load():
    """(High, Low, Close) 2차원 배열 (날짜 x 종목) + 축."""
    df = pd.read_parquet(PANEL).sort_index()
    tickers = df["Close"].columns
    hi = df["High"][tickers].to_numpy(float)
    lo = df["Low"][tickers].to_numpy(float)
    cl = df["Close"][tickers].to_numpy(float)
    return hi, lo, cl, df.index, tickers


def _fire(rng_hi, rng_lo, hi, lo, cl):
    """레인지가 주어졌을 때의 발동 — 프로덕션 조건식 그대로.

    bull: 저가가 ERL Low 를 깨고, 종가가 **중간선 위** ~ High 사이
    bear: 고가가 ERL High 를 넘고, 종가가 Low ~ **중간선 아래**
    """
    mid = (rng_hi + rng_lo) / 2
    bull = (lo < rng_lo) & (cl > mid) & (cl < rng_hi)
    bear = (hi > rng_hi) & (cl > rng_lo) & (cl < mid)
    ok = np.isfinite(rng_hi) & np.isfinite(rng_lo) & np.isfinite(cl)
    return bull & ok, bear & ok


def arm_rolling(hi, lo, cl, period=PERIOD):
    """현행 — 직전 period 봉의 롤링 max/min. 경계가 매일 미끄러진다."""
    n = len(cl)
    rng_hi = np.full_like(cl, np.nan)
    rng_lo = np.full_like(cl, np.nan)
    for t in range(period, n):
        rng_hi[t] = np.nanmax(hi[t - period:t], axis=0)
        rng_lo[t] = np.nanmin(lo[t - period:t], axis=0)
    return _fire(rng_hi, rng_lo, hi, lo, cl)


def arm_fixed(hi, lo, cl, period=PERIOD):
    """정본 — 직전에 **닫힌** period 봉 블록 하나가 레인지. 블록 안에서는 고정."""
    n = len(cl)
    rng_hi = np.full_like(cl, np.nan)
    rng_lo = np.full_like(cl, np.nan)
    for t in range(period, n):
        b = t // period                      # 오늘이 속한 블록
        s, e = (b - 1) * period, b * period  # 직전에 닫힌 블록
        if s < 0:
            continue
        rng_hi[t] = np.nanmax(hi[s:e], axis=0)
        rng_lo[t] = np.nanmin(lo[s:e], axis=0)
    return _fire(rng_hi, rng_lo, hi, lo, cl)


def next_ret(cl):
    """다음날 수익. 마지막 날은 NaN."""
    r = np.full_like(cl, np.nan)
    r[:-1] = cl[1:] / cl[:-1] - 1
    return r


def gap_and_mde(mask, ret, sign, rng):
    """발동일 평균수익 − 기준선, 그리고 위약 MDE.

    위약은 **종목별로 발동 개수를 보존한 채 날짜만 섞는다.** 그래서 종목 구성과
    표본 크기는 그대로고 타이밍만 무작위다 — 방해 모수를 안 건드린다.

    ponytail: 종목별 독립 셔플이라 횡단면 상관을 지운다 → 나오는 MDE 는 하한이다.
    날짜 통째 셔플로 올리는 건, 어떤 줄이 이 하한을 넘었을 때 하면 된다.
    """
    valid = np.isfinite(ret)
    m = mask & valid
    n = int(m.sum())
    if n == 0:
        return np.nan, np.nan, np.nan, 0

    base = float(np.nanmean(ret[valid]))
    obs = sign * (float(np.nanmean(ret[m])) - base)

    counts = m.sum(axis=0)
    pools = {j: np.flatnonzero(valid[:, j]) for j, k in enumerate(counts) if k}
    null = np.empty(REPS)
    for i in range(REPS):
        picked = [ret[rng.choice(pools[j], size=int(counts[j]), replace=False), j]
                  for j in pools]
        null[i] = sign * (float(np.concatenate(picked).mean()) - base)

    se = float(null.std(ddof=1))
    mde = 2.8 * se          # 양측 5%, 검출력 80%
    return obs * 100, mde * 100, base * 100, n


def run() -> None:
    hi, lo, cl, idx, tickers = load()
    ret = next_ret(cl)
    rng = np.random.default_rng(SEED)

    arms = {"현행 (롤링)": arm_rolling(hi, lo, cl),
            "정본 (고정블록)": arm_fixed(hi, lo, cl)}
    live = np.isfinite(cl) & np.isfinite(ret)
    denom = int(live.sum())

    print("\n### CRT 레인지 정의 대조 — {}~{}, {}종목, period={}, 종목·일 {:,}".format(
        idx[0].date(), idx[-1].date(), len(tickers), PERIOD, denom))
    print("\n{:<16} | {:>11} | {:>11} | {:>8} | {:>8}".format(
        "팔", "bull 발동률", "bear 발동률", "bull n", "bear n"))
    print("-" * 66)
    for name, (bull, bear) in arms.items():
        print("{:<16} | {:>10.2f}% | {:>10.2f}% | {:>8,} | {:>8,}".format(
            name, bull.sum() / denom * 100, bear.sum() / denom * 100,
            int(bull.sum()), int(bear.sum())))

    (ba, ea) = arms["현행 (롤링)"]
    (bb, eb) = arms["정본 (고정블록)"]
    for label, x, y in (("bull", ba, bb), ("bear", ea, eb)):
        inter, union = (x & y).sum(), (x | y).sum()
        print("\n{} 일치율(Jaccard) : {:>5.1f}%  (둘 다 {:,} / 현행만 {:,} / 정본만 {:,})".format(
            label, inter / union * 100, int(inter),
            int((x & ~y).sum()), int((y & ~x).sum())))

    print("\n--- 탐색적 관측 (판정 아님) — 다음날 수익, %p ---")
    print("{:<16} | {:<5} | {:>9} | {:>8} | {:>6} | {:>8}".format(
        "팔", "방향", "효과", "MDE", "넘었나", "n"))
    print("-" * 64)
    for name, (bull, bear) in arms.items():
        for label, mask, sign in (("bull", bull, +1), ("bear", bear, -1)):
            obs, mde, base, n = gap_and_mde(mask, ret, sign, rng)
            mark = "O" if abs(obs) > mde else "X"
            print("{:<16} | {:<5} | {:>+9.4f} | {:>8.4f} | {:>6} | {:>8,}".format(
                name, label, obs, mde, mark, n))
    print("\n기준선(전체 평균 다음날 수익) = {:+.4f}%p".format(np.nanmean(ret[live]) * 100))
    print("효과는 부호를 맞춘 값이다 — bear 는 '기준선 − 발동일'이라 양수면 예측대로 하락.")
    print("MDE 를 못 넘은 줄(X)은 방향을 읽지 않는다.\n")


def selftest() -> None:
    """벡터화 판정이 프로덕션 `detect_crt_setup` 과 글자 그대로 같은가."""
    hi, lo, cl, idx, tickers = load()
    bull, bear = arm_rolling(hi, lo, cl)
    rng = np.random.default_rng(1)

    # 1. 무작위 (날짜, 종목) 표본을 프로덕션 함수에 직접 먹여 맞춘다.
    df_all = pd.read_parquet(PANEL).sort_index()
    checked = mismatch = 0
    for _ in range(300):
        t = int(rng.integers(PERIOD + 2, len(idx)))
        j = int(rng.integers(0, len(tickers)))
        tk = tickers[j]
        sub = pd.DataFrame({"High": df_all["High"][tk], "Low": df_all["Low"][tk],
                            "Close": df_all["Close"][tk]}).iloc[: t + 1]
        if not np.isfinite(sub.tail(PERIOD + 1).to_numpy(float)).all():
            continue
        want = detect_crt_setup(sub, period=PERIOD)["setup"]
        got = "bullish" if bull[t, j] else "bearish" if bear[t, j] else None
        checked += 1
        mismatch += want != got
    assert checked > 50, "표본이 너무 적다: {}".format(checked)
    assert mismatch == 0, "프로덕션과 {}/{} 불일치".format(mismatch, checked)
    print("1. 프로덕션 일치 — {}표본 전부 같음 OK".format(checked))

    # 2. bull 과 bear 는 서로 배타여야 한다.
    assert not (bull & bear).any(), "같은 날 bull·bear 동시 발동 — 조건식이 겹친다"
    print("2. bull/bear 배타 OK")

    # 3. 고정 블록의 레인지는 블록 안에서 안 변한다.
    n = len(cl)
    rh = np.full_like(cl, np.nan)
    for t in range(PERIOD, n):
        b = t // PERIOD
        if b >= 1:
            rh[t] = np.nanmax(hi[(b - 1) * PERIOD: b * PERIOD], axis=0)
    for b in range(2, n // PERIOD):
        blk = rh[b * PERIOD:(b + 1) * PERIOD]
        assert np.allclose(blk, blk[0], equal_nan=True), "블록 {} 안에서 레인지가 변했다".format(b)
    print("3. 고정 블록 레인지 불변 OK")

    # 4. 위약이 실제로 0 근처인가 — 자가 안 휘었는지.
    ret = next_ret(cl)
    fake = np.zeros_like(bull)
    idxs = np.flatnonzero(np.isfinite(ret[:, 0]))
    fake[idxs[:200], 0] = True
    obs, mde, base, k = gap_and_mde(fake, ret, +1, np.random.default_rng(7))
    assert abs(obs) < mde * 3, "무작위 마스크가 MDE 를 크게 넘었다 — 자가 휘었다"
    print("4. 위약 마스크 효과 {:+.4f}%p < MDE {:.4f}%p 대역 OK".format(obs, mde))

    print("\nselftest 통과")


if __name__ == "__main__":
    selftest() if len(sys.argv) > 1 and sys.argv[1] == "selftest" else run()
