#!/usr/bin/env python
"""PEAD (실적 서프라이즈 드리프트) — 사전 등록된 대로 한 번 잰다.

    python scripts/measure_pead.py           # 측정 + 리포트
    python scripts/measure_pead.py selftest  # 산수 점검 (네트워크 無)

설계서: `docs/superpowers/specs/2026-08-13-pead-design.md`.
**파라미터는 거기서 못 박았다. 결과를 보고 바꾸면 그 측정은 폐기다.**

## 이 파일이 지켜야 하는 것

1. **`filed`는 10-Q 제출일이지 8-K 발표일이 아니다.** 드리프트 앞부분을 놓치고
   시작한다. 그대로 간다 — `filed+1` 종가는 **걸 수 있는 주문**이고, 이 저장소가
   두 번 죽은 이유가 걸 수 없는 가격이었다. 실패하면 "신호가 없다"가 아니라
   **"filed 진입으로는 없다"** 로 적는다. 그래서 `filed` 당일 초과수익을
   공짜 진단으로 같이 낸다.
2. **분위 랭킹이 look-ahead가 된다.** 같은 분기를 모아놓고 10분위를 매기면 아직
   안 나온 공시를 쓴다. 직전 250일 안에 **이미 공시된** 이벤트하고만 비교한다.
   같은 날 공시는 풀에서 뺀다 (그날 아침엔 못 본다).
3. **랭킹 모수와 SUE 이력은 가격 패널(2020-03)보다 앞선 공시도 쓴다.** 전부
   과거라 look-ahead가 아니고, 안 그러면 측정 구간 앞 1년이 통째로 날아간다.
   거래만 가격이 있는 날부터 한다.
4. **판정은 AND 두 개다.** ①만 보고 다섯 번 속았고 ②를 넉 달 안 재서 전략
   하나를 날렸다. 하나만 통과하면 실패다.
5. **뒤 구간(2025-01~)은 봉인.** 여기 상수를 바꿔서 열지 않는다 — 판정이 끝난
   뒤 딱 한 번, 손으로 연다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules import edgar_fundamentals as ef  # noqa: E402
from scripts import measure_portfolio as mp  # noqa: E402  — PANEL 을 갈아끼운다
from scripts.measure_portfolio import (  # noqa: E402
    bench_curve, cagr, closes, mdd, yearly,
)

OUT_MD = Path("docs/measurements/2026-08-13-pead.md")

# 측정 구간. 뒤는 봉인 — 설계서 5절.
START = pd.Timestamp("2020-03-31")
END   = pd.Timestamp("2024-12-31")

# 봉인 해제 (`python scripts/measure_pead.py sealed`). 판정이 끝난 뒤 딱 한 번,
# 확인용으로만 연다 — 설계서 5절. 갈려도 본 측정의 판정을 바꾸지 않으므로
# **출력 파일을 따로 쓴다.** 같은 파일에 덮어쓰면 판정을 덮는 것과 구별이 안 된다.
SEALED = "sealed" in sys.argv
if SEALED:
    START = pd.Timestamp("2025-01-01")
    END   = pd.Timestamp("2026-08-07")
    OUT_MD = Path("docs/measurements/2026-08-13-pead-sealed.md")

# 대형주 재측정 — **바꾸는 건 유니버스와 창 둘뿐이다** (설계서
# `2026-08-16-largecap-repanel-design.md`). 파라미터는 한 글자도 안 바꾼다.
#
#   python scripts/measure_pead.py largecap          # 판정 행 (8.93년)
#   python scripts/measure_pead.py largecap oldwin   # 참고 행 (옛 창 × 새 유니버스)
#
# 봉인 구간을 안 둔다 — 2025-01~ 은 `2026-08-13-pead-sealed.md` 로 이미 열었다.
# 이미 본 구간을 봉인이라 부르면 거짓말이고, 빼놓으면 선택적 보고다.
LARGECAP = "largecap" in sys.argv
OLDWIN   = "oldwin" in sys.argv        # 창과 유니버스를 분리하는 참고 행. 판정 아님.
LARGECAP_UNIVERSE = Path("data/largecap_universe.parquet")
LARGECAP_PANEL    = Path("data/largecap_panel.parquet")
EVENTS_CACHE      = Path("data/largecap_pead_events.parquet")
if LARGECAP:
    mp.PANEL = LARGECAP_PANEL          # `closes` 가 읽는 자리. 값 계산은 그대로다.
    START  = pd.Timestamp("2017-09-01")
    END    = pd.Timestamp("2026-08-07")
    OUT_MD = Path("docs/measurements/2026-08-16-pead-largecap.md")
    if OLDWIN:
        START  = pd.Timestamp("2020-03-31")
        END    = pd.Timestamp("2024-12-31")
        OUT_MD = Path("docs/measurements/2026-08-16-pead-largecap-oldwin.md")

# 귀속 검사 — **바꾸는 건 ②의 상대 하나다**: 같은 노출 매수보유 → 중위 분위 곡선
# (설계서 `2026-08-16-pead-signal-vs-construction-design.md`). 파라미터는 안 바꾼다.
#
#   python scripts/measure_pead.py largecap midbase
#
# 통과가 나온 largecap 리포트 안에 이미 반대 증거가 있다 — 롱숏 t=+0.55, 위약과의
# 폭 1.8%p 미검정. 이 측정은 그 통과를 깨러 간다.
MIDBASE = "midbase" in sys.argv
MIDBASE_MDE_LIMIT_PP = 3.0            # 설계서 3절 — "결정이 바뀌는 최소 폭". 못 박았다.
MIDBASE_WIDTHS = (0.10, 0.20, 0.30)   # 설계서 4절 — 이 순서로만 넓히고 되돌아가지 않는다.
MIDBASE_SPLIT = pd.Timestamp("2020-03-31")   # 처음 본 2.6년 / 나머지 (참고 행)
if MIDBASE:
    OUT_MD = Path("docs/measurements/2026-08-16-pead-signal-vs-construction.md")

# 문헌값 고정 (Bernard & Thomas 1989). 고른 게 아니라 가져온 값이다.
HOLD_DAYS     = 60     # 거래일
SUE_WINDOW    = 8      # σ를 내는 분기 수
MIN_QUARTERS  = 12     # 최소 이력 = 차분 4 + σ 8
TOP_PCT       = 0.9    # 상위 10분위
RANK_DAYS     = 250    # 랭킹 창 (달력일)

COST_BPS   = 6.0                    # 판정 기준 — Alpaca 커미션 0 + 현실적 스프레드
COST_SWEEP = (0.0, 6.0, 20.0, 40.0)

MIN_EVENTS = 30        # ①의 유효표본 통과선
N_BOOT     = 2000
BLOCK      = 20        # 날짜 블록 (자기상관 보존)
SEED       = 20260813
MDE_LIMIT_PP = 10.0    # 검출력 게이트(연 %p). measure_fscore·pilot 이 각자 갖고 있던 값을
                       # 여기 하나로 모은다 — 자와 게이트가 같은 파일에 있어야 한다.
                       # 설계서 2절: 지난 다섯 측정이 다 넘어도 이 값을 안 올린다.

# 랭킹 풀이 이만큼도 안 되면 백분위가 잡음이다. 통과선이 아니라 유효성 가드다
# (276종목이면 정상 구간에서 700개 안팎이라 실제로는 안 걸린다).
MIN_POOL = 20


# ---------------------------------------------------------------- SUE

def net_income_quarters(ticker: str) -> pd.DataFrame:
    """index=회계 분기말(end), cols=[filed, net_income].

    `fetch_quarterly_fundamentals_history`를 안 쓰는 이유가 하나 있다 — 그 함수는
    end를 버리고 filed만 인덱스로 남기는데, **같은 날 두 분기가 공시된 행이 6%**다
    (10-K에 Q4가 유도돼 들어가는 경우 등). 그러면 filed 순서로 shift(4)를 걸었을 때
    "1년 전 같은 분기" 짝이 조용히 어긋난다. 파싱을 새로 짜는 게 아니라 **같은
    모듈의 조립기를 한 단계 아래에서** 부른다.
    """
    # 이름이 `"CIK:티커"`(유니버스 asset_id)면 **CIK 로 받는다.** `ef.get_cik` 은
    # SEC 의 *현재* 매핑이라 죽은 티커에 쓰면 없거나, 더 나쁘게는 그 티커를 물려받은
    # 다른 회사를 준다(BBBY: 886158 파산 → 1130713 재상장). 소형주에서 실제로 밟았다.
    cik, sep, sym = ticker.partition(":")
    ug = ef.load_raw(sym if sep else ticker, cik=int(cik) if sep else None)
    if ug is None:
        return pd.DataFrame()
    tag = ef._assemble_tag(ug, ef.TAG_CHAINS["net_income"])
    if not tag:
        return pd.DataFrame()
    rows = [{"end": pd.Timestamp(end), "filed": pd.Timestamp(filed), "net_income": val}
            for end, (filed, val) in tag.items()]
    return pd.DataFrame(rows).sort_values("end").set_index("end")


def sue_series(q: pd.DataFrame) -> pd.Series:
    """SUE_q = (NI_q − NI_{q−4}) / σ(직전 8분기의 같은 차분).

    σ 창은 **현재 분기를 뺀** 직전 8개다. 포함시키면 서프라이즈가 커질수록 분모도
    같이 커져서 SUE가 서프라이즈에 단조가 아니게 된다 — 자체검사가 이걸 잡았다.
    그래서 첫 이벤트는 설계서의 최소 이력 12분기가 아니라 13분기째에 난다
    (12는 바닥이고, 창이 자연히 한 칸 더 먹는다). 전부 공시된 값이라 look-ahead는 없다.

    분기가 빠진 구간에서는 shift(4)가 1년 전이 아니다. end 간격으로 걸러낸다.
    """
    ni = q["net_income"]
    diff = ni - ni.shift(4)
    gap = q.index.to_series().diff(4).dt.days
    diff = diff.where((gap - 365).abs() <= 45)
    sd = diff.shift(1).rolling(SUE_WINDOW, min_periods=SUE_WINDOW).std()
    return diff / sd.replace(0.0, np.nan)


def build_events(tickers) -> pd.DataFrame:
    """[ticker, filed, sue] — filed 오름차순. 가격 구간 밖 공시도 남긴다(랭킹 모수)."""
    frames = []
    for tk in tickers:
        q = net_income_quarters(tk)
        if len(q) < MIN_QUARTERS:
            continue
        ev = pd.DataFrame({"ticker": tk, "filed": q["filed"].values,
                           "sue": sue_series(q).values}).dropna()
        # 같은 filed에 두 분기가 실렸으면 최신 분기 하나만 (end 오름차순이므로 뒤).
        frames.append(ev.drop_duplicates("filed", keep="last"))
    ev = pd.concat(frames, ignore_index=True)
    return ev.sort_values("filed", kind="stable").reset_index(drop=True)


def universe_members(path, start, end) -> pd.DataFrame:
    """[date, asset_id] — 그 창의 **월별** 구성종목. 벤치마크가 이걸로 리밸런스한다.

    창 시작 **직전** 기준일까지 포함한다. 안 그러면 첫 리밸런스가 올 때까지
    기준선이 현금으로 놀고, 그만큼 넘기 쉬운 자가 된다.

    (소형주 `measure_fscore.smallcap_members` 와 같은 함수다. 대형주가 두 번째
    사용자가 되면서 여기로 올렸다 — 자가 두 벌로 갈리면 한쪽만 고치는 날이 온다.)
    """
    u = pd.read_parquet(path)
    dates = pd.to_datetime(sorted(u["date"].unique()))
    prior = dates[dates <= start]
    lo = prior[-1] if len(prior) else start
    return u.loc[(u["date"] >= lo) & (u["date"] <= end), ["date", "asset_id"]]


def cached_events(names, cache: Path) -> pd.DataFrame:
    """`build_events` 를 한 번만 돌리고 여러 창이 나눠 쓴다.

    점수는 창과 무관하다 — 창은 거래를 자를 때만 쓴다(`attach_trades`). 캐시의
    유효성은 파일 이름이 아니라 **내용**(종목 목록의 해시)으로 본다. 유니버스를
    다시 지으면 종목이 바뀌는데 캐시가 살아 있으면 **다른 자로 잰 점수**를
    조용히 재사용한다 (일봉 샤드를 순번으로 이름 붙였다가 같은 사고를 냈다).
    """
    import hashlib
    import json
    key = hashlib.sha1("\n".join(sorted(names)).encode()).hexdigest()[:16]
    meta = cache.with_suffix(".json")
    if cache.exists() and meta.exists():
        if json.loads(meta.read_text(encoding="utf-8")).get("universe_key") == key:
            return pd.read_parquet(cache)
    ev = build_events(names)
    ev.to_parquet(cache, index=False)
    meta.write_text(json.dumps({"universe_key": key, "names": len(names)}),
                    encoding="utf-8")
    return ev


def rank_percentile(ev: pd.DataFrame) -> np.ndarray:
    """직전 250일 안에 **이미 공시된** 이벤트들 사이에서의 백분위. 같은 날은 뺀다."""
    f = ev["filed"].values.astype("datetime64[D]")
    s = ev["sue"].values
    lo = np.searchsorted(f, f - np.timedelta64(RANK_DAYS, "D"), "left")
    hi = np.searchsorted(f, f, "left")
    pct = np.full(len(ev), np.nan)
    for i in range(len(ev)):
        pool = s[lo[i]:hi[i]]
        if len(pool) >= MIN_POOL:
            pct[i] = float((pool < s[i]).mean())
    return pct


# ---------------------------------------------------------------- 거래

def attach_trades(ev: pd.DataFrame, close: pd.DataFrame, hold: int = HOLD_DAYS) -> pd.DataFrame:
    """진입 = filed 다음 거래일 종가, 청산 = `hold` 거래일 뒤 종가. 못 거는 건 뺀다.

    **보유 중 상장폐지는 마지막 거래일 종가에 청산한다** — 잔여 보유기간은 현금이다.
    예전에는 창에 NaN 이 하나라도 있으면 그 거래를 통째로 뺐다. 상폐가 든 패널에서
    그건 **손실만 골라 버리는 필터**다(롱온리에서 상폐는 대개 큰 손실이다). 대형주
    패널에는 상폐가 없어서 무해했던 가정이고, 데이터가 바뀌면 무해하던 가정이
    편향이 된다 — 소형주 설계서 5.2 절.

    진입일 종가가 없으면 그 거래는 여전히 뺀다. **못 산 걸 산 것으로 치지는 않는다.**"""
    idx = close.index
    pos_entry = np.searchsorted(idx.values, ev["filed"].values, side="right")
    pos_exit = pos_entry + hold
    # `pos_entry == 0` = 가격 구간이 **시작되기 전에** 공시된 건이다. build_events가
    # 랭킹 모수로 쓰려고 일부러 남겨둔 것들인데(설계서 3.2), 그대로 두면 전부 패널
    # 첫날 종가에 산 거래로 잡힌다 — 2016년 공시를 2020-03-31에 사는 셈이고,
    # 같은 날 같은 가격에 수백 건이 몰려 단면 t와 캘린더 곡선을 통째로 오염시킨다.
    # 랭킹에는 쓰되 거래로는 세지 않는다.
    ok = (pos_entry > 0) & (pos_exit < len(idx)) & ev["ticker"].isin(close.columns).values
    ev = ev.loc[ok].copy()
    ev["entry"] = pos_entry[ok]
    ev["exit"] = pos_exit[ok]

    px = close.values
    col = {c: i for i, c in enumerate(close.columns)}
    rets, alive, dead = [], [], []
    for tk, a, b in zip(ev["ticker"], ev["entry"], ev["exit"]):
        s = px[a:b + 1, col[tk]]
        have = np.isfinite(s)
        buyable = bool(have[0] and s[0] > 0)
        alive.append(buyable)
        # have 의 마지막 값 = 청산가. 창이 다 살아 있으면 그건 그냥 마지막 종가고,
        # 중간에 죽었으면 마지막 거래일 종가다. 같은 한 줄이 두 경우를 다 편다.
        rets.append(s[have][-1] / s[0] - 1.0 if buyable else np.nan)
        dead.append(buyable and not bool(have[-1]))
    ev["gross"] = rets
    ev["delisted"] = dead        # 보유 중 상폐 = 조기 청산. 보고서가 세는 수다.
    return ev.loc[alive].reset_index(drop=True)


def calendar_curve(ev: pd.DataFrame, close: pd.DataFrame, cost_bps: float,
                   min_held: int = 1):
    """캘린더타임 곡선. 날짜 t의 수익률 = 그날 보유 중인 포지션의 동일가중 평균.

    보유 종목이 `min_held` 미만이면 현금(0%). 기본값 1 = 자리 제약 없음(학계
    표준형)이고 동시에 `bench_curve`와 같은 자로 잰 값이라 직접 비교된다.
    2 이상은 바구니가 얇은 날의 한 종목 잡음을 곡선에서 빼려는 쪽이 쓴다.

    왕복 비용은 진입에 전액 문다. 포지션이 평균에 들어오는 첫날(진입 다음날)에
    걸며, 그날 자본의 1/보유수만 그 종목에 들어가 있으므로 비용도 그만큼 나뉜다.
    """
    ret = close.pct_change().fillna(0.0).values
    n_days, n_cols = ret.shape
    col = {c: i for i, c in enumerate(close.columns)}

    held = np.zeros((n_days, n_cols))
    cost = np.zeros(n_days)
    for tk, a, b in zip(ev["ticker"], ev["entry"], ev["exit"]):
        held[a + 1:b + 1, col[tk]] += 1.0     # 진입일 종가에 샀으므로 수익은 다음날부터
        cost[a + 1] += cost_bps / 1e4
    n_held = held.sum(axis=1)
    on = n_held >= min_held
    port = np.zeros(n_days)
    port[on] = ((held * ret).sum(axis=1)[on] - cost[on]) / n_held[on]
    return pd.Series(port, index=close.index), float(on.mean())


# ---------------------------------------------------------------- 통계

def _block_idx(n: int, rng, block: int = BLOCK) -> np.ndarray:
    """길이 n을 block 단위로 재표본한 인덱스 (N_BOOT, n).

    `stat_validation.block_bootstrap_sharpe_ci`와 같은 재표본 방식이지만 그 함수는
    통계량이 Sharpe로 박혀 있다. 여기 필요한 건 초과 CAGR과 단면 평균이라
    재표본만 6줄로 따로 둔다.
    """
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(N_BOOT, nb))
    return ((starts[:, :, None] + np.arange(block)) % n).reshape(N_BOOT, -1)[:, :n]


def date_block_t(values: np.ndarray, dates: np.ndarray) -> tuple:
    """날짜 블록 부트스트랩으로 단면 평균의 t. 같은 날 이벤트는 한 블록에 묶인다."""
    order = np.argsort(dates, kind="stable")
    values, dates = values[order], dates[order]
    _, first = np.unique(dates, return_index=True)
    sums = np.add.reduceat(values, first)
    cnts = np.add.reduceat(np.ones(len(values)), first)
    idx = _block_idx(len(first), np.random.default_rng(SEED))
    boot = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    mean = float(values.mean())
    sd = float(boot.std())
    return mean, (mean / sd if sd > 0 else float("nan"))


def excess_cagr_ci(strat: np.ndarray, flat: np.ndarray) -> tuple:
    """초과 연수익(%p)의 블록 부트스트랩 95% 구간. 두 줄을 **같은 날짜로** 묶어 뽑는다."""
    ls, lf = np.log1p(strat), np.log1p(flat)
    idx = _block_idx(len(ls), np.random.default_rng(SEED))
    boot = (np.expm1(ls[idx].mean(axis=1) * 252) - np.expm1(lf[idx].mean(axis=1) * 252)) * 100
    point = (np.expm1(ls.mean() * 252) - np.expm1(lf.mean() * 252)) * 100
    return point, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


def mde_pp(strat: np.ndarray, base: np.ndarray) -> float:
    """② 추정량의 95% 반폭 = 이 설계가 잴 수 있는 최소 효과(연 %p).

    **점추정을 반환하지 않는다.** 게이트를 내려면 이 함수를 불러야 하는데 반환값에
    효과의 크기도 부호도 없으므로, 사전 등록 단계에서 결과를 볼 방법이 구조적으로
    없다 — 임상시험의 눈가림 표본수 재계산(blinded sample size re-estimation)과
    같은 장치다. 방해 모수(분산)만 실측으로 채우고 효과는 가린다.

    **실제 두 줄을 넣는다.** 위약 두 다리를 넣으면 안 된다 — 달 안에서 점수를
    섞으면 두 바구니가 거의 같은 포트폴리오가 되어 상관이 올라가고, 그만큼 MDE 가
    작게 나온다. 두 다리를 같은 풀에서 뽑는 설계에서 위약 MDE 는 **구조적 하한**
    이지 게이트가 아니다 (설계서 0절).

    **완전한 눈가림은 아니다.** CAGR 이 `expm1` 을 거치는 비선형 변환이라 구간의
    폭이 수준에 아주 약하게 딸려온다. 지금까지 관측된 +-20%p 범위에서는 무시할
    수준이고, **알고 쓴다** (설계서 1절 마지막 문단).
    """
    _, lo, hi = excess_cagr_ci(strat, base)
    return (hi - lo) / 2.0


# ------------------------------------------------------- 귀속 검사 (midbase)

def midbase_legs(ev: pd.DataFrame, width: float):
    """폭 `width` 의 상위 다리와 중위 다리. 설계서 4절 표 그대로.

    10% → `pct>=0.90` / `[0.45, 0.55)`, 20% → `>=0.80` / `[0.40, 0.60)`, …
    """
    top = ev.loc[ev["pct"] >= 1 - width]
    mid = ev.loc[(ev["pct"] >= 0.5 - width / 2) & (ev["pct"] < 0.5 + width / 2)]
    return top.reset_index(drop=True), mid.reset_index(drop=True)


def pick_rung(mdes, limit: float = MIDBASE_MDE_LIMIT_PP):
    """게이트를 **처음** 넘는 폭의 인덱스. 없으면 None.

    넘은 뒤에 더 넓혀보지 않고, 판정을 본 뒤 다른 폭으로 되돌아가지 않는다
    (설계서 4절). `mde_pp` 는 효과의 크기도 부호도 안 주므로 여기까지 눈가림이다.
    """
    return next((i for i, m in enumerate(mdes) if m <= limit), None)


# ---------------------------------------------------------------- 자체검사

def selftest() -> int:
    """SUE 부호·분기 정렬·look-ahead 없음·포트폴리오 산수. 시장 데이터 없이 돈다."""
    ends = pd.date_range("2016-03-31", periods=16, freq="QE")
    filed = ends + pd.Timedelta(days=40)

    # 1) 계절 랜덤워크: 매년 같은 폭으로 늘면 차분이 일정 → σ=0 → SUE 없음.
    flat = pd.DataFrame({"filed": filed, "net_income": 100.0 + 10 * np.arange(16)},
                        index=ends)
    assert sue_series(flat).notna().sum() == 0, "차분이 일정하면 SUE가 나오면 안 된다"

    # 2) 부호: 마지막 분기만 서프라이즈 → 양수, 뒤집으면 음수, 크기는 같다.
    ni = 100.0 + 10 * np.arange(16.0)
    ni[3::4] += np.array([5.0, -3.0, 4.0, -2.0])      # 차분에 흔들림을 준다
    up = flat.assign(net_income=ni.copy())
    ni2 = ni.copy(); ni2[-1] += 60
    up2 = flat.assign(net_income=ni2)
    s1, s2 = sue_series(up).iloc[-1], sue_series(up2).iloc[-1]
    assert s2 > s1, "서프라이즈가 크면 SUE가 커야 한다"
    dn = flat.assign(net_income=-ni2)
    assert np.isclose(sue_series(dn).iloc[-1], -s2), "부호만 뒤집혀야 한다"

    # 3) 분기가 빠지면 shift(4)는 1년 전이 아니다 → 그 자리는 버린다.
    holed = up2.drop(up2.index[10])
    assert np.isnan(sue_series(holed).iloc[-1]), "분기 결측 구간은 SUE를 내면 안 된다"

    # 4) look-ahead 없음 — 나중 이벤트를 붙여도 앞 이벤트의 백분위는 안 변하고,
    #    같은 날 공시는 자기 풀에 안 들어간다.
    days = pd.date_range("2021-01-04", periods=60, freq="B")
    base = pd.DataFrame({"ticker": "T", "filed": list(days) + [days[-1]],
                         "sue": list(np.linspace(-2, 2, 60)) + [9.0]})
    p = rank_percentile(base)
    later = pd.concat([base, pd.DataFrame({"ticker": ["T"], "filed": [days[-1] + pd.Timedelta(days=5)],
                                           "sue": [99.0]})], ignore_index=True)
    assert np.allclose(p, rank_percentile(later)[:len(p)], equal_nan=True), "과거 백분위가 변했다"
    assert p[-1] == p[-2] or np.isnan(p[-1]), "같은 날 이벤트가 서로의 풀에 들어갔다"

    # 5) 포트폴리오 산수 — 하루 1%씩 오르는 한 종목 한 포지션, 60일 보유.
    idx = pd.date_range("2021-01-04", periods=80, freq="B")
    close = pd.DataFrame({"A": 100 * 1.01 ** np.arange(80.0),
                          "B": np.full(80, 50.0)}, index=idx)
    ev = pd.DataFrame({"ticker": ["A"], "entry": [0], "exit": [60]})
    port, exp = calendar_curve(ev, close, 0.0)
    assert np.isclose((1 + port).prod() - 1, 1.01 ** 60 - 1), "무비용 곡선이 종목 수익과 달랐다"
    assert np.isclose(exp, 60 / 80), "노출은 보유일수 비율이어야 한다"
    port_c, _ = calendar_curve(ev, close, 40.0)
    assert np.isclose(port.iloc[1] - port_c.iloc[1], 40 / 1e4), "왕복 비용을 진입에 전액 안 물었다"
    assert np.isclose(port[2:].values, port_c[2:].values).all(), "비용이 하루가 아닌 곳에 묻었다"

    # 6) 두 종목을 같이 들면 동일가중 평균이고, 비용도 그만큼 나뉜다.
    ev2 = pd.DataFrame({"ticker": ["A", "B"], "entry": [0, 0], "exit": [60, 60]})
    p2, _ = calendar_curve(ev2, close, 0.0)
    assert np.isclose(p2.iloc[1], 0.01 / 2), "동일가중 평균이 아니다"

    # 7) 가격 구간 시작 전 공시는 거래로 세지 않는다. 랭킹 모수로 남겨둔 것들이
    #    전부 패널 첫날 매수로 잡히던 버그를 잡는 줄이다.
    pre = pd.DataFrame({"ticker": ["A", "A"], "sue": [1.0, 1.0],
                        "filed": [idx[0] - pd.Timedelta(days=30), idx[0]]})
    got = attach_trades(pre, close)
    assert len(got) == 1 and got["entry"].iloc[0] == 1, "구간 전 공시가 첫날 매수로 잡혔다"

    # 8) 이름이 `"CIK:티커"` 면 **CIK 로** companyfacts 를 받는다. 티커로 받으면
    #    `ef.get_cik` 의 *현재* 매핑을 타서 죽은 티커에 다음 주인이 붙는다.
    seen, orig = {}, ef.load_raw
    try:
        ef.load_raw = lambda t, cik=None: seen.update(t=t, cik=cik)
        net_income_quarters("886158:BBBY")
        assert seen == {"t": "BBBY", "cik": 886158}, f"asset_id 를 CIK 로 안 풀었다: {seen}"
        net_income_quarters("AAPL")
        assert seen == {"t": "AAPL", "cik": None}, f"평범한 티커가 갈렸다: {seen}"
    finally:
        ef.load_raw = orig

    # 9) 벤치마크에 members 를 주면 **월별 리밸런스**다. 안 주면 시작일과 종료일에
    #    둘 다 값이 있는 종목만 사므로, 상폐가 든 패널에서 기준선이 "안 죽은 것만
    #    산 줄"이 된다 — 설계서 7절이 "제일 조용히 틀릴 자리"로 꼽은 곳.
    d = pd.date_range("2021-01-04", periods=40, freq="B")
    px = pd.DataFrame({"A": np.linspace(100, 200, 40),
                       "B": np.r_[np.linspace(100, 50, 20), np.full(20, np.nan)]}, index=d)
    mem = pd.DataFrame({"date": [d[0], d[0], d[20]], "asset_id": ["A", "B", "A"]})
    naive = bench_curve(d[0], d[-1], close=px)
    fair = bench_curve(d[0], d[-1], members=mem, close=px)
    assert np.isclose(naive.iloc[-1], 2.0), "members 없는 줄이 죽은 종목을 안 뺐다"
    assert fair.iloc[-1] < naive.iloc[-1], "members 를 줘도 상폐 손실이 안 들어갔다"

    # 10) 귀속 검사의 두 다리 — 경계가 설계서 4절 표와 같아야 한다. 상위는 닫힌
    #     하한, 중위는 [닫힘, 열림) 이라 0.55 는 중위가 아니다.
    q = pd.DataFrame({"pct": [0.34, 0.35, 0.45, 0.5449, 0.55, 0.699, 0.70, 0.80, 0.90, 1.0]})
    t10, m10 = midbase_legs(q, 0.10)
    assert list(t10["pct"]) == [0.90, 1.0], f"10% 상위 다리가 다르다: {list(t10['pct'])}"
    assert list(m10["pct"]) == [0.45, 0.5449], f"10% 중위 다리가 다르다: {list(m10['pct'])}"
    t30, m30 = midbase_legs(q, 0.30)
    assert list(t30["pct"]) == [0.70, 0.80, 0.90, 1.0], "30% 상위 다리가 다르다"
    assert list(m30["pct"]) == [0.35, 0.45, 0.5449, 0.55], "30% 중위 다리가 다르다"

    # 11) 폭은 **처음** 게이트를 넘는 데서 멈추고 되돌아가지 않는다 (설계서 4절).
    assert pick_rung([9.0, 2.0, 1.0], 3.0) == 1, "더 좋은 뒤 폭으로 되돌아갔다"
    assert pick_rung([1.0, 0.5, 0.4], 3.0) == 0, "첫 폭이 넘었는데 안 멈췄다"
    assert pick_rung([9.0, 8.0, 7.0], 3.0) is None, "세 폭 다 못 넘으면 미측정이다"

    print("selftest OK")
    return 0


# ---------------------------------------------------------------- 리포트

def prepare():
    """가격 패널 · 기준선 · 거래가 붙은 이벤트. 판정 행과 귀속 검사가 **같은 자**를 쓴다.

    이 함수를 두 벌로 갈면 한쪽만 고치는 날이 온다 (`universe_members` 와 같은 이유).
    """
    close = closes(START, END)
    if LARGECAP:
        # 유니버스와 창만 바꾼다. 종목의 키는 티커가 아니라 asset_id(`"CIK:티커"`)다.
        # **벤치마크에 `members` 를 안 주면** 기준선이 "시작일과 종료일에 둘 다 값이
        # 있는 종목만 산 줄"이 되어, 생존자 편향을 빼려고 한 일이 여기서 통째로
        # 샌다 (설계서 7절 — 이 작업에서 제일 조용히 틀릴 자리).
        members = universe_members(LARGECAP_UNIVERSE, START, END)
        close = close[sorted(set(members["asset_id"]) & set(close.columns))]
        bench = bench_curve(START, END, members=members, close=close)
        all_names = sorted(pd.read_parquet(LARGECAP_UNIVERSE)["asset_id"].unique())
        ev = cached_events(all_names, EVENTS_CACHE)
        ev = ev.loc[ev["ticker"].isin(set(close.columns))].reset_index(drop=True)
    else:
        bench = bench_curve(START, END)
        ev = build_events(list(close.columns))

    ev["pct"] = rank_percentile(ev)
    ev = ev.dropna(subset=["pct"])
    return close, bench, attach_trades(ev, close)


def main() -> int:
    try:                       # 윈도우 콘솔은 cp949 다 — 리포트를 찍다 죽는다
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    close, bench, ev = prepare()
    bench_ret = bench.pct_change().fillna(0.0).values

    top = ev.loc[ev["pct"] >= TOP_PCT].reset_index(drop=True)
    bot = ev.loc[ev["pct"] < 1 - TOP_PCT].reset_index(drop=True)
    mid = ev.loc[(ev["pct"] >= 0.45) & (ev["pct"] < 0.55)].reset_index(drop=True)

    # ① 단면 — 60일 초과수익 (같은 기간 패널 동일가중 대비), 6bp.
    bv = bench.values

    def excess(df):
        b = bv[df["exit"].values] / bv[df["entry"].values] - 1.0
        return df["gross"].values - COST_BPS / 1e4 - b

    ex_top, ex_bot = excess(top), excess(bot)
    d_top = close.index.values[top["entry"].values]
    m_top, t_top = date_block_t(ex_top, d_top)
    m_bot, t_bot = date_block_t(ex_bot, close.index.values[bot["entry"].values])
    ls = np.concatenate([ex_top, -ex_bot])
    m_ls, t_ls = date_block_t(ls, np.concatenate([d_top, close.index.values[bot["entry"].values]]))
    # 대형주 재측정의 ①은 **단측 t >= +2** 다. 가설이 방향을 가지면 통과선도 방향을
    # 가져야 한다 — 양측 |t|>=2 는 부호가 반대인데 통과하는 자리를 남긴다(설계서 6.2).
    # 단측이 양측보다 **엄격하다**. 결과를 유리하게 만드는 변경이 아니다.
    pass1 = len(top) >= MIN_EVENTS and (t_top >= 2 if LARGECAP else abs(t_top) >= 2)

    # ② 포트폴리오 — 캘린더타임 vs 같은 평균 노출 매수보유, 6bp.
    port, exposure = calendar_curve(top, close, COST_BPS)
    flat_ret = bench_ret * exposure
    # **MDE 를 점추정보다 먼저 낸다.** `mde_pp` 는 구간 반폭만 반환하고 효과의
    # 크기도 부호도 안 준다 — 판정을 보고 게이트를 고를 방법이 구조적으로 없다
    # (설계서 6.3). 아래 `excess_cagr_ci` 호출은 이 줄 **다음에** 온다.
    mde = mde_pp(port.values, flat_ret)
    underpowered = mde > MDE_LIMIT_PP
    pt, lo, hi = excess_cagr_ci(port.values, flat_ret)
    pass2 = lo > 0
    # 게이트를 못 넘으면 ②는 실패가 아니라 **미측정**이다. 점추정을 읽지 않는다.
    mark2 = "△" if (LARGECAP and underpowered) else ("O" if pass2 else "X")

    strat_curve = (1 + port).cumprod()
    flat_curve = pd.Series((1 + flat_ret).cumprod(), index=close.index)
    years = (close.index[-1] - close.index[0]).days / 365.25

    # 위약 줄을 여기서 낸다 — 리포트 문장 안에서 계산하면 "통과했을 때 무슨 말을
    # 할지"를 문장이 정하게 된다. 값 먼저, 문장은 그 값으로 쓴다.
    plc_cagr = cagr(float((1 + calendar_curve(mid, close, COST_BPS)[0]).cumprod().iloc[-1]), years)
    strat_cagr = cagr(float(strat_curve.iloc[-1]), years)
    bench_cagr = cagr(float(bench.iloc[-1]), years)

    # 공짜 진단 — filed 당일 초과수익. 유의하게 크면 filed ≈ 발표일이고,
    # 0에 가까우면 뉴스가 이미 소화된 뒤라는 뜻이다. 해석 규칙(설계서 3.1)을
    # 실제로 쓰려면 이 한 줄이 있어야 한다.
    day_pos = np.maximum(top["entry"].values - 1, 1)   # filed 당일 = 진입 전 거래일
    px = close.values
    col = {c: i for i, c in enumerate(close.columns)}
    ci = np.array([col[t] for t in top["ticker"]])
    d0 = px[day_pos, ci] / px[day_pos - 1, ci] - 1.0 - bench_ret[day_pos]
    keep = ~np.isnan(d0)
    m_d0, t_d0 = date_block_t(d0[keep], close.index.values[day_pos[keep]])

    verdict = ("미측정" if (LARGECAP and underpowered) else
               "통과" if (pass1 and pass2) else "실패")
    body = [
        "# PEAD — 봉인 구간 확인" if SEALED else
        "# PEAD 대형주 — 참고 행 (옛 창 × 새 유니버스)" if (LARGECAP and OLDWIN) else
        "# PEAD 대형주 재측정 — 자를 길게, 편향 없이 다시 만들었다" if LARGECAP else
        "# PEAD (실적 서프라이즈 드리프트) — 측정",
        "",
        f"구간 {START.date()} ~ {END.date()} · 진입 `filed`+1 종가 · {HOLD_DAYS}거래일 보유 · "
        f"상위 10분위 롱온리 · 판정 {COST_BPS:.0f}bp.",
        "사전 등록: `docs/superpowers/specs/2026-08-16-largecap-repanel-design.md` "
        "(파라미터는 `2026-08-13-pead-design.md` 그대로). **바뀐 건 유니버스와 창 둘뿐이다.**"
        if LARGECAP else
        "사전 등록: `docs/superpowers/specs/2026-08-13-pead-design.md`. "
        "파라미터는 문헌값 고정이라 **고를 게 없었고, 따라서 전 구간이 OOS**다.",
        "",
    ] + ([
        "> **이 문서는 판정이 아니다 — 참고 행이다.** 창과 유니버스가 같이 바뀌면 숫자가",
        "> 움직여도 원인을 모른다. 이 행은 **옛 창(2020-03~2024-12)에 새 유니버스만** 대서",
        "> 유니버스 효과를 떼어낸 것이다. 판정 행은 `2026-08-16-pead-largecap.md` 하나다",
        "> (설계서 6.4).",
        "",
        f"## 참고: ①{'O' if pass1 else 'X'} ②{mark2}",
    ] if (LARGECAP and OLDWIN) else [
        "> **새 데이터로 하는 새 검정이 아니다.** 2020-03~2026-08 의 답은 이미 봤다"
        "(`2026-08-13-pead.md`,",
        "> `2026-08-13-pead-sealed.md`). 처음 보는 건 **2017-09~2020-03 의 2.6년, 창의 29%** 뿐이다.",
        "> 이 사전 등록이 묶는 것은 데이터의 새로움이 아니라 **결정 규칙**이다 — 판정선과",
        "> 검출력 게이트를 돌리기 전에 문서에 못 박았고, 파라미터는 한 글자도 안 바꿨다.",
        "",
        "> **봉인 구간은 없다.** 2025-01~ 은 두 가설 다 이미 열었다. 이미 본 구간을 봉인이라",
        "> 부르면 거짓말이고, 빼놓으면 선택적 보고다. 그래서 전부 넣었다(설계서 6.1).",
        "",
        f"## 판정: **{verdict}** (①{'O' if pass1 else 'X'} AND ②{mark2})",
    ] if LARGECAP else [
        "> **이 문서는 판정이 아니다.** 본 측정(`2026-08-13-pead.md`)에서 PEAD는 이미 **실패**로",
        "> 판정됐고, 봉인 구간은 그 뒤에 딱 한 번 확인용으로 연 것이다. 아래 O/X는 같은 통과선을",
        "> 같은 코드로 봉인 구간에 적용해본 값일 뿐, **판정을 바꾸지 않는다**(설계서 5절).",
        "",
        f"## 봉인 구간에 같은 자를 대면: ①{'O' if pass1 else 'X'} ②{'O' if pass2 else 'X'}",
    ] if SEALED else [
        "> **정정 (PR #106 대비).** 첫 판에서는 가격 구간이 시작되기도 전에 공시된 건이",
        "> 전부 패널 첫날 종가 매수로 잡혔다 — 상위 10분위 1055건 중 638건이 그것이었다.",
        "> `attach_trades`에서 걸러내고 다시 돌린 값이 이 문서다. 이벤트 수와 ①의 부호가",
        "> 바뀌었고 **판정(실패)은 그대로다.** 파라미터는 손대지 않았다(튜닝 금지 조항).",
        "",
        f"## 판정: **{verdict}** (①{'O' if pass1 else 'X'} AND ②{'O' if pass2 else 'X'})",
    ]) + [
        "",
        "| | 무엇 | 통과선 | 실측 | |",
        "|---|---|---|---|---|",
        f"| ① 단면 | 상위 10분위 60일 초과수익 | 유효표본 ≥ {MIN_EVENTS} · "
        + ("t ≥ +2 (단측)" if LARGECAP else "\\|t\\| ≥ 2") + " | "
        f"n={len(top)} · 평균 {m_top * 100:+.2f}% · t={t_top:+.2f} | {'O' if pass1 else 'X'} |",
        f"| ② 포트폴리오 | 같은 노출 매수보유 대비 초과 연수익 | 부트스트랩 95% 하한 > 0 | "
        + ("**게이트 미달 — 읽지 않는다**" if (LARGECAP and underpowered) else
           f"{pt:+.2f}%p · 95% [{lo:+.2f}, {hi:+.2f}]") + f" | {mark2} |",
        f"| ② 검출력 | 이 설계가 잴 수 있는 최소 효과 (구간 반폭) | "
        + (f"**게이트** {MDE_LIMIT_PP:.0f}%p" if LARGECAP
           else f"참고 — 게이트 {MDE_LIMIT_PP:.0f}%p")
        + f" | MDE {mde:.2f}%p | {'O' if not underpowered else 'X'} |",
        "",
        "**하나만 통과하면 실패다.** 이 저장소는 ①만 보고 다섯 번 속았고, ②를 넉 달 안 재서",
        "전략 하나를 통째로 날렸다.",
        "",
    ] + ([
        "> **검출력 게이트는 ②를 읽기 전에 걸었다.** `mde_pp` 는 구간 반폭만 반환하고 효과의",
        "> 크기도 부호도 안 준다 — 결과를 보고 게이트를 고를 방법이 구조적으로 없다(설계서 6.3).",
        "> 게이트를 못 넘으면 ②는 \"실패\"가 아니라 **\"미측정\"** 이고, 그러면 창을 더 못 늘리므로",
        "> (웨이백 첫 스냅샷 2017-08-28 이 천장) **대형주 노선은 거기서 닫힌다.**",
        "",
    ] if LARGECAP else [
        "> **검출력 줄은 판정에 안 들어간다** (2026-08-16 설계서 5절 — 이 측정은 재실행하지",
        "> 않는다). 게이트를 넘으면 ②는 \"실패\"가 아니라 **\"미측정\"** 으로 읽는다.",
        "",
        "> 2026-08-14 재생성. 소형주 측정이 공유 자에 **상폐 규칙**을 넣으면서 이 표의 소수점이",
        "> 움직였다 — 보유 창에 값이 끊긴 이벤트를 통째로 버리지 않고 **마지막 거래일 종가에**",
        "> **청산**해 세기 때문이다(소형주 설계서 5.2). 유효 이벤트가 늘어난 만큼 ②의 점추정이",
        "> 내려갔고, **판정은 그대로 X·X 다.** 자를 고치면 옛 측정도 다시 돌려 값을 맞춘다 —",
        "> 문서만 그대로 두면 재현이 안 되는 기록이 된다.",
        "",
    ]) + [
        "## 네 줄 — 설계서가 항상 같이 내라고 한 것",
        "",
        "| 줄 | 연수익 | MDD | 비고 |",
        "|---|---|---|---|",
        f"| 전략 ({COST_BPS:.0f}bp) | {cagr(float(strat_curve.iloc[-1]), years):+.1f}% | "
        f"{mdd(strat_curve):.1f}% | 평균 노출 {exposure * 100:.0f}% |",
        f"| 같은 노출 매수보유 | {cagr(float(flat_curve.iloc[-1]), years):+.1f}% | "
        f"{mdd(flat_curve):.1f}% | ②의 기준선 |",
        f"| 원본 매수보유 100% | {cagr(float(bench.iloc[-1]), years):+.1f}% | {mdd(bench):.1f}% | "
        "통과선은 아니지만 빼지 않는다 |",
        "",
        "PEAD는 지수를 이긴다는 주장이 아니라 **시장 초과수익 주장**이다. 60일 보유·상위",
        "10분위면 자본이 항상 100% 들어가 있지 않으므로, 노출 차이로 지는 것은 신호에 대한",
        "반증이 아니다. 이 문장은 측정 전에 썼다(설계서 4절).",
        "",
        "## 비용 스윕 (왕복 bp, 진입일 전액)",
        "",
        "| | " + " | ".join(f"{c:.0f}bp" for c in COST_SWEEP) + " |",
        "|---|" + "---|" * len(COST_SWEEP),
    ]

    cells_s, cells_x = [], []
    for c in COST_SWEEP:
        p_c, exp_c = calendar_curve(top, close, c)
        cells_s.append(f"{cagr(float((1 + p_c).cumprod().iloc[-1]), years):+.1f}%")
        pt_c, lo_c, _ = excess_cagr_ci(p_c.values, bench_ret * exp_c)
        cells_x.append(f"{pt_c:+.1f} ({lo_c:+.1f})")
    body += [
        "| 전략 연수익 | " + " | ".join(cells_s) + " |",
        "| 초과 %p (95% 하한) | " + " | ".join(cells_x) + " |",
        "",
        "## 참고 — 문헌형 롱숏과 하위 분위",
        "",
        f"- 하위 10분위 초과수익 {m_bot * 100:+.2f}% · t={t_bot:+.2f} (n={len(bot)})",
        f"- 롱숏 스프레드 {m_ls * 100:+.2f}% · t={t_ls:+.2f}",
        "",
        "숏은 통과 판정에 안 쓴다. 이 저장소 실측 기준 숏 손익분기가 24.7bp로 실제 비용",
        "구간 한복판이라 못 쓴다 — 결과를 보고 뺀 게 아니라 재기 전에 뺐다.",
        "",
        "## 위약 대조 — ②의 점추정을 그냥 읽으면 안 되는 이유",
        "",
        f"같은 기계에 **중위 10분위**(45~55%, 신호가 없는 자리)를 넣으면 "
        f"연 {plc_cagr:+.1f}% (n={len(mid)}) 가 나온다.",
        "",
        "②의 점추정에는 신호와 **구성**이 섞여 있다 — 60일 겹치기로 수십 종목만 들고 매일",
        "동일가중으로 재조정한 바구니를, "
        + ("월별 리밸런스하는 1,000종목 동일가중" if LARGECAP else "279종목")
        + " 매수보유와 비교하는 구조 자체가",
        "차이를 만든다. 위약 줄이 그 몫이다:",
        "",
        f"| 전략 | 위약(중위 10분위) | 기준선 |",
        "|---|---|---|",
        f"| {strat_cagr:+.1f}% | {plc_cagr:+.1f}% | {bench_cagr:+.1f}% |",
        "",
        f"**전략이 위약을 넘는 폭은 {strat_cagr - plc_cagr:+.1f}%p 다.** 위약은 사전 등록된 통과선이"
        " 아니지만(통과선은",
        "매수보유 대비 95% 하한 하나다) 먼저 읽는다 — **판정이 통과여도 이 줄과 구별이 안 되면**",
        "**그 통과는 신호가 아니라 구성이다.** 이 줄에는 위약의 신뢰구간이 없다는 것도 같이 적는다:",
        "두 줄의 차이를 검정한 게 아니라 점추정 두 개를 나란히 놓은 것뿐이다.",
        "",
        "## 공짜 진단 — `filed` 당일 초과수익",
        "",
        f"평균 {m_d0 * 100:+.3f}% · t={t_d0:+.2f} (n={int(keep.sum())})",
        "",
        "`filed`는 10-Q 제출일이지 8-K 실적 발표일이 아니다. 이 값이 유의하게 크면 filed가",
        "발표일에 가깝다는 뜻이고, 0에 가까우면 뉴스가 이미 소화된 뒤라는 뜻이다.",
        "**실패했을 때 \"신호가 없다\"가 아니라 \"filed 진입으로는 없다\"로만 적기 위해**",
        "재는 줄이다. 8-K 진입은 미측정으로 남는다.",
        "",
        "## 해마다",
        "",
    ] + yearly(strat_curve, bench, exposure) + [
        "## 이 측정이 안 한 것",
        "",
        "- **봉인은 이 문서로 열었다. 다시 안 연다.** 파라미터를 바꿔 재실행하면 폐기 사유다."
        if SEALED else
        "- **2017-08 이전은 못 잰다.** 웨이백 `company_tickers.json` 첫 스냅샷이 2017-08-28 이고,"
        " 그 전에 죽은 종목은 어느 스냅샷에도 없다. 창의 천장이다."
        if LARGECAP else
        "- **2025-01 ~ 2026-08은 봉인.** 판정 후 딱 한 번 연다. 갈려도 판정을 안 바꾼다.",
        "- **8-K 발표일 진입 미측정.** EDGAR submissions를 새로 받아야 해서 이번 범위 밖이다.",
        "- **자리·현금 제약 없음.** ②를 통과한 뒤에 붙인다 — 못 하면 붙일 이유가 없다.",
        "",
        f"이벤트 {len(ev)}건 중 상위 10분위 {len(top)}건. "
        f"재현: `python scripts/measure_pead.py{' sealed' if SEALED else ''}"
        f"{' largecap' if LARGECAP else ''}{' oldwin' if OLDWIN else ''}` · "
        "산수 점검 `... selftest`",
        "",
    ]

    text = "\n".join(body)
    print(text)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(text, encoding="utf-8")
    print(f"저장: {OUT_MD}")
    return 0


def main_midbase() -> int:
    """귀속 검사 — **구성이 같은 대조군 대비, SUE 순서가 초과수익을 만드나**(설계서 1절).

    더 좋은 전략을 찾는 게 아니다. 파라미터를 한 글자도 안 바꾸고, 바꾸는 건 ②의
    상대 하나(매수보유 → 중위 분위)다.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    close, bench, ev = prepare()
    bv = bench.values
    bench_ret = bench.pct_change().fillna(0.0).values
    years = (close.index[-1] - close.index[0]).days / 365.25

    def excess(df):
        b = bv[df["exit"].values] / bv[df["entry"].values] - 1.0
        return df["gross"].values - COST_BPS / 1e4 - b

    def entered(df):
        return close.index.values[df["entry"].values]

    # --- 폭 사다리. **MDE 만 먼저 전부 낸다** (설계서 4절). `mde_pp` 는 구간 반폭만
    #     반환하고 효과의 크기도 부호도 안 주므로, 폭을 고르는 동안 눈가림이 유지된다.
    #     아래 `excess_cagr_ci` 호출은 폭이 정해진 **다음에** 온다 (490행 규율).
    rungs = []
    for w in MIDBASE_WIDTHS:
        t_leg, m_leg = midbase_legs(ev, w)
        p_top, e_top = calendar_curve(t_leg, close, COST_BPS)
        p_mid, e_mid = calendar_curve(m_leg, close, COST_BPS)
        rungs.append(dict(w=w, top=t_leg, mid=m_leg, p_top=p_top, p_mid=p_mid,
                          e_top=e_top, e_mid=e_mid,
                          mde=mde_pp(p_top.values, p_mid.values)))
    k = pick_rung([r["mde"] for r in rungs])
    underpowered = k is None
    # 30% 도 못 넘으면 미측정이다. 판정 문장은 사전 등록된 기본 폭(10%)으로 적는다.
    r = rungs[0 if underpowered else k]
    top, mid = r["top"], r["mid"]

    # --- ① 단면. 두 다리를 **같은 날짜로 짝지어** 뽑는다 — `date_block_t` 가 고유
    #     날짜로 reduceat 한 뒤 날짜 블록을 재표본하므로 같은 날 상위·중위가 한 블록이다.
    ex_top, ex_mid = excess(top), excess(mid)
    d_top, d_mid = entered(top), entered(mid)
    m_top, t_top = date_block_t(ex_top, d_top)
    m_mid, t_mid = date_block_t(ex_mid, d_mid)
    # concat 추정량의 평균은 (n_top·m_top − n_mid·m_mid)/(n_top+n_mid) 라 두 다리가
    # 비슷한 크기면 **진짜 차이의 약 1/2** 이다. t 는 영향받지 않는다 (설계서 6.2).
    m_ls, t_ls = date_block_t(np.concatenate([ex_top, -ex_mid]),
                              np.concatenate([d_top, d_mid]))
    pass1 = min(len(top), len(mid)) >= MIN_EVENTS and t_ls >= 2

    # --- ② 포트폴리오. 게이트를 못 넘었으면 점추정을 **계산하지도 않는다.**
    if underpowered:
        pt = lo = hi = None
        pass2 = False
    else:
        pt, lo, hi = excess_cagr_ci(r["p_top"].values, r["p_mid"].values)
        pass2 = lo > 0
    mark2 = "△" if underpowered else ("O" if pass2 else "X")
    verdict = "미측정" if underpowered else ("통과" if (pass1 and pass2) else "실패")

    cagr_top = cagr(float((1 + r["p_top"]).cumprod().iloc[-1]), years)
    cagr_mid = cagr(float((1 + r["p_mid"]).cumprod().iloc[-1]), years)

    # --- 참고 행 (판정 아님, 설계서 7절)
    ladder = []
    for q in range(10):
        qlo, qhi = q / 10, (q + 1) / 10
        sel = ev.loc[(ev["pct"] >= qlo) & (ev["pct"] < qhi if q < 9 else ev["pct"] <= 1.0)]
        lm, lt = date_block_t(excess(sel), entered(sel))
        ladder.append((qlo, qhi, len(sel), lm, lt))
    # 단조성을 값으로 본다. **t 만 보면 안 된다** — 상위 칸이 t 는 제일 큰데 평균은
    # 가운데 칸이 더 큰 모양이 실제로 나온다(칸마다 분산이 다르다).
    best_m = max(ladder, key=lambda x: x[3])
    best_t = max(ladder, key=lambda x: x[4])
    monotone = best_m[1] >= 1.0 and best_t[1] >= 1.0

    subs = []
    for lo_d, hi_d in ((START, MIDBASE_SPLIT), (MIDBASE_SPLIT, END + pd.Timedelta(days=1))):
        a, b = np.datetime64(lo_d), np.datetime64(hi_d)
        st, sm = (d_top >= a) & (d_top < b), (d_mid >= a) & (d_mid < b)
        s_m, s_t = date_block_t(np.concatenate([ex_top[st], -ex_mid[sm]]),
                                np.concatenate([d_top[st], d_mid[sm]]))
        cm = (close.index >= lo_d) & (close.index < hi_d)
        s_pt, s_lo, s_hi = excess_cagr_ci(r["p_top"].values[cm], r["p_mid"].values[cm])
        subs.append((lo_d, hi_d - pd.Timedelta(days=1), int(st.sum()), int(sm.sum()),
                     s_m, s_t, s_pt, s_lo, s_hi))

    # 짝짓기가 실제로 정밀도를 샀나 — 같은 폭에서 상대만 매수보유로 돌린 반폭.
    # 설계서 3절의 전제("두 다리를 같은 풀에서 뽑으면 상관이 높아 MDE 가 작게 나온다")를
    # 사후에 확인하는 줄이다. `mde_pp` 라 여전히 효과의 크기도 부호도 안 나온다.
    mde_flat = mde_pp(r["p_top"].values, bench_ret * r["e_top"])

    z_top, _ = calendar_curve(top, close, 0.0)
    z_mid, _ = calendar_curve(mid, close, 0.0)
    z_pt, z_lo, z_hi = excess_cagr_ci(z_top.values, z_mid.values)

    # --- 리포트. **값으로 쓰는 문장은 값에서 나온다** (설계서 6.6 — largecap 에서
    #     상수 문자열로 박힌 "하한은 못 넘었다"가 통과가 나오자 거짓말을 했다).
    cell2 = ("**게이트 미달 — 읽지 않는다**" if underpowered
             else f"{pt:+.2f}%p · 95% [{lo:+.2f}, {hi:+.2f}]")
    body = [
        "# PEAD 통과는 신호였나 구성이었나 — 귀속 검사",
        "",
        f"구간 {START.date()} ~ {END.date()} · 진입 `filed`+1 종가 · {HOLD_DAYS}거래일 보유 · "
        f"판정 {COST_BPS:.0f}bp · 폭 {r['w'] * 100:.0f}%.",
        "사전 등록: `docs/superpowers/specs/2026-08-16-pead-signal-vs-construction-design.md`.",
        "",
        "> **전략 개선이 아니라 귀속 검사다.** 답하는 질문은 하나 — **구성이 같은 대조군",
        "> 대비, SUE 순서가 초과수익을 만드나.** 파라미터는 한 글자도 안 바꿨고, 바꾼 건",
        "> ②의 상대 하나다: 같은 노출 매수보유 → **중위 분위 곡선**. 통과해도 수익이",
        "> 커지지 않는다 — 통과는 \"그 8.4%p 중 일부가 신호였다\", 실패는 \"그 8.4%p 는",
        "> 바구니 구성이었다\"는 뜻이다.",
        "",
        f"## 판정: **{verdict}** (①{'O' if pass1 else 'X'} AND ②{mark2})",
        "",
        "| | 무엇 | 통과선 | 실측 | |",
        "|---|---|---|---|---|",
        f"| ① 단면 | 상위 − 중위 60일 초과수익 | 유효표본 ≥ {MIN_EVENTS} · t ≥ +2 (단측) | "
        f"n={len(top)}/{len(mid)} · {m_ls * 100:+.2f}% · t={t_ls:+.2f} | {'O' if pass1 else 'X'} |",
        f"| ② 포트폴리오 | 두 캘린더타임 곡선 **차이**의 연수익 | 부트스트랩 95% 하한 > 0 | "
        f"{cell2} | {mark2} |",
        f"| ② 검출력 | 그 차이 추정량의 95% 반폭 | **게이트** {MIDBASE_MDE_LIMIT_PP:.0f}%p | "
        f"MDE {r['mde']:.2f}%p | {'X' if underpowered else 'O'} |",
        "",
        "**하나만 통과하면 실패다.** ①은 단면에서 순서가 살아 있나, ②는 그게 들고 있는",
        "동안 돈으로 남나 — 다른 실패 모드를 잡는다.",
        "",
        "### ①의 점추정을 \"차이\"로 읽으면 안 된다",
        "",
        "| 다리 | n | 60일 초과수익 | t |",
        "|---|---|---|---|",
        f"| 상위 {r['w'] * 100:.0f}% | {len(top)} | {m_top * 100:+.2f}% | {t_top:+.2f} |",
        f"| 중위 {r['w'] * 100:.0f}% | {len(mid)} | {m_mid * 100:+.2f}% | {t_mid:+.2f} |",
        f"| concat 추정량 | {len(top) + len(mid)} | {m_ls * 100:+.2f}% | {t_ls:+.2f} |",
        "",
        f"concat 의 평균은 `(n_top·m_top − n_mid·m_mid)/(n_top+n_mid)` 라 두 다리가 비슷한",
        f"크기일 때 **진짜 차이의 약 절반**이다 — 실제 차이는 "
        f"{(m_top - m_mid) * 100:+.2f}%p 이고 concat 은 {m_ls * 100:+.2f}% 다. "
        "**t 는 영향받지 않는다**(분자·분모가 같이 줄어든다).",
        "",
        "### 노출은 보정하지 않았다 — 대신 나란히 낸다",
        "",
        "| 줄 | 연수익 | MDD | 평균 노출 |",
        "|---|---|---|---|",
        f"| 상위 {r['w'] * 100:.0f}% ({COST_BPS:.0f}bp) | {cagr_top:+.1f}% | "
        f"{mdd((1 + r['p_top']).cumprod()):.1f}% | {r['e_top'] * 100:.0f}% |",
        f"| 중위 {r['w'] * 100:.0f}% ({COST_BPS:.0f}bp) | {cagr_mid:+.1f}% | "
        f"{mdd((1 + r['p_mid']).cumprod()):.1f}% | {r['e_mid'] * 100:.0f}% |",
        "",
        f"노출 차이는 {abs(r['e_top'] - r['e_mid']) * 100:.1f}%p 다. 두 다리가 같은 기계라",
        "노출이 비슷할 것이라는 전제로 보정 없이 비교했고(설계서 6.4), 크게 벌어지면 그건",
        "보정 실수가 아니라 **발견이다** — 이벤트 밀도가 다르다는 뜻이고 그 자체가",
        "\"구성이 다르다\"의 증거다.",
        "",
        "## 폭 사다리 — 어디서 멈췄나 (설계서 4절)",
        "",
        "| 폭 | 상위 다리 | 중위 다리 | n (상위/중위) | MDE | |",
        "|---|---|---|---|---|---|",
    ] + [
        f"| {x['w'] * 100:.0f}% | `pct >= {1 - x['w']:.2f}` | "
        f"`{0.5 - x['w'] / 2:.2f} <= pct < {0.5 + x['w'] / 2:.2f}` | "
        f"{len(x['top'])}/{len(x['mid'])} | {x['mde']:.2f}%p | "
        + ("**← 판정** " if x is r and not underpowered else "")
        + ("O" if x["mde"] <= MIDBASE_MDE_LIMIT_PP else "X") + " |"
        for x in rungs
    ] + [
        "",
        f"게이트 {MIDBASE_MDE_LIMIT_PP:.0f}%p 를 **처음 넘는 폭에서 멈추고 되돌아가지 않는다.** "
        + (f"세 폭 전부 게이트를 못 넘었다 — 그래서 판정은 \"실패\"가 아니라 **\"미측정\"** 이고,"
           " 창을 더 못 늘리므로(웨이백 첫 스냅샷 2017-08-28 이 천장) 여기서 닫힌다."
           if underpowered else
           f"{r['w'] * 100:.0f}% 에서 처음 넘었고(MDE {r['mde']:.2f}%p), 그 폭으로 판정했다."),
        "",
        "> **게이트는 ②의 점추정을 읽기 전에 걸었다.** `mde_pp` 는 구간 반폭만 반환하고",
        "> 효과의 크기도 부호도 안 준다 — 폭을 고르는 동안 눈가림이 유지된다. 게이트를",
        f"> 재기 전에 {MIDBASE_MDE_LIMIT_PP:.0f}%p 로 정한 근거는 설계서 3절이다: 기존 10%p 는 짝지은",
        "> 설계에서 장식이 되고, 이미 본 1.8%p 근처로 잡으면 사전 등록이 아니다.",
        "",
        "### 짝짓기가 정밀도를 샀나 — 설계서 3절의 전제를 사후에 잰다",
        "",
        f"폭 {r['w'] * 100:.0f}% 에서 상대만 **같은 노출 매수보유**로 바꾸면 MDE 는 "
        f"{mde_flat:.2f}%p 다. 중위 다리와 짝지었을 때가 {r['mde']:.2f}%p 이므로 짝짓기로 줄어든 "
        f"반폭은 **{mde_flat - r['mde']:+.2f}%p** 다.",
        "",
        ("설계서 3절은 두 다리를 같은 풀에서 뽑으면 상관이 높아 MDE 가 **작게** 나올 것으로 보고"
         " 게이트를 10%p 에서 3%p 로 조였다. **그 예상은 이 데이터에서 일어나지 않았다** —"
         " 시장이 양쪽에서 상쇄될 것이라는 전제가 두 바구니의 실제 상관을 과대평가했다."
         " 게이트가 장식이 아니었다는 것까지는 맞았고, 그 이유는 설계서가 적은 이유가 아니다."
         if mde_flat <= r["mde"] else
         "설계서 3절이 본 대로 짝짓기가 반폭을 줄였다. 게이트를 10%p 에 두었다면 이 사다리가"
         " 첫 줄에서 통과했을 것이고, 그러면 1.8%p 급을 못 재는 설계로 판정을 쓰게 됐다."),
        "",
        "## 참고 — 10분위 사다리 (판정 아님)",
        "",
        "중위 분위가 정말 중립인지는 **가정**이다(설계서 6.3). 관계가 U 자면 중위가 가장",
        "낮아 이 검정이 통과하기 쉬워지고, 계단형이면 중위가 이미 상위 칸이라 어려워진다.",
        "",
        "| 분위 | n | 60일 초과수익 | t |",
        "|---|---|---|---|",
    ] + [
        f"| {qlo * 100:.0f}~{qhi * 100:.0f}% | {n} | {m * 100:+.2f}% | {t:+.2f} |"
        for qlo, qhi, n, m, t in ladder
    ] + [
        "",
        f"평균이 가장 큰 칸은 **{best_m[0] * 100:.0f}~{best_m[1] * 100:.0f}%**"
        f"({best_m[3] * 100:+.2f}%), t 가 가장 큰 칸은 "
        f"**{best_t[0] * 100:.0f}~{best_t[1] * 100:.0f}%**({best_t[4]:+.2f}) 다."
        + (" 둘 다 상위 칸이다 — 사다리가 오른쪽으로 오르는 모양이라 중위를 중립으로 본"
           " 가정이 지지된다." if monotone else
           f" 상위 칸(평균 {ladder[-1][3] * 100:+.2f}% · t={ladder[-1][4]:+.2f})이 두 값 다"
           " 최대는 아니다. **SUE 와 수익률의 관계가 단조가 아니라는 뜻이고, 그러면 중위"
           " 분위를 \"신호가 없는 자리\"로 쓴 전제 자체가 흔들린다**(설계서 6.3)."
           " 이 줄은 판정이 아니지만, 판정을 어느 방향으로 읽어야 하는지는 이 줄이 정한다 —"
           " 중위가 중립이 아니면 ①의 상위−중위는 신호의 크기를 **과소**하게 잰다."),
        "",
        "## 참고 — 처음 본 2.6년 대 나머지 (판정 아님)",
        "",
        "largecap 재측정에서 ②를 넘긴 게 효과가 커져서가 아니라 창이 길어져서였다.",
        "그 2.6년이 이번 차이를 혼자 만들고 있는지 본다.",
        "",
        "| 구간 | n (상위/중위) | ① concat | t | ② 차이 %p | 95% |",
        "|---|---|---|---|---|---|",
    ] + [
        f"| {a.date()} ~ {b.date()} | {nt}/{nm} | {sm * 100:+.2f}% | {st:+.2f} | "
        f"{spt:+.2f} | [{slo:+.2f}, {shi:+.2f}] |"
        for a, b, nt, nm, sm, st, spt, slo, shi in subs
    ] + [
        "",
        "## 참고 — 0bp 한 줄 (판정 아님)",
        "",
        f"비용을 0 으로 두면 두 곡선의 차이는 {z_pt:+.2f}%p · 95% [{z_lo:+.2f}, {z_hi:+.2f}] 다"
        + (f" (판정 {COST_BPS:.0f}bp 에서는 {pt:+.2f}%p)." if not underpowered else "."),
        "",
        "양쪽 다리가 같은 6bp 를 진입일에 전액 문다. 두께가 비슷하면 비용은 차이에서 거의",
        "상쇄되므로 비용 스윕 표는 판정에 아무 말도 안 한다 — 상쇄가 실제로 일어나는지",
        "확인하려고 이 한 줄만 낸다(설계서 6.5).",
        "",
        "## 이 판정이 닫는 것",
        "",
    ] + ([
        "②의 하한이 0 을 넘었다. **다음 단계는 실매매가 아니라 \"그 폭이 비용과 자리",
        "제약을 견디나\"** 다 — 이 급의 효과는 마찰에 통째로 먹힐 수 있다(설계서 11절).",
    ] if verdict == "통과" else [
        "②는 게이트를 못 넘었으므로 점추정을 읽지 않는다. **\"차이가 없다\"도 \"있다\"도 아니고",
        "\"이 설계로는 못 쟀다\"** 가 ②의 결론이다. 남은 손잡이가 없다 — 창은 웨이백 천장에",
        "닿았고 폭은 30% 까지 다 썼다.",
        "",
        f"**①은 게이트와 무관하게 재졌고, 통과선 +2 를 {'넘었다' if pass1 else '못 넘었다'}"
        f"(t={t_ls:+.2f}, n={len(top)}/{len(mid)}).** 단면에서 상위와 중위를 가르는 순서는"
        + (" 살아 있다. ②만 못 쟀다." if pass1 else
           " 이 창·이 유니버스에서 보이지 않는다. 그래서 닫는 문장은 ② 쪽이 \"못 쟀다\"고,"
           " ① 쪽은 \"못 봤다\"다 — 둘을 합쳐 통과라고 부를 수 있는 자리는 없다."),
        "",
        "- **`filed` 진입 노선을 닫는다.** ②를 못 잰 채로 실매매·8-K로 나가면 전부 미검증",
        "  위에 쌓인다(설계서 0절). 다음 후보는 EDGAR submissions 수급이고, `filed` 는",
        "  10-Q 제출일이지 실적 발표일이 아니므로 그건 **다른 진입이지 같은 신호의 재시도가**",
        "  **아니다.**",
    ] if underpowered else [
        "- **`filed` 진입 노선을 닫는다.** 상위 분위 · 60일 보유 · 이 유니버스 · 이 창에서",
        "  SUE 순서는 바구니 구성과 구별되지 않는다.",
        f"- **8-K 발표일 진입은 미측정으로 남긴다.** `filed` 당일 초과수익이 크다는 건 뉴스가",
        "  이미 소화된 뒤에 사고 있을 가능성을 뜻한다. 그래서 정확한 문장은 \"신호가 없다\"가",
        "  아니라 **\"filed 진입으로는 없다\"** 다. 다음 후보는 EDGAR submissions 수급이다.",
        "- **실매매·자리·현금 제약은 전부 뒤로.** 통과해야 붙일 이유가 생긴다.",
    ]) + [
        "",
        "**F-Score 대형주 노선도 같이 닫는다**(설계서 10절). `2026-08-16-fscore-largecap.md` 는",
        "미측정이었고(MDE 13.05 > 10 · ① t=−0.74), 검출력을 올릴 유일한 손잡이인 창이 웨이백",
        "천장에 이미 닿았다. 옛 t=−4.04 가 −0.74 로 사라진 건 창이 아니라 **유니버스** 때문이었고,",
        "그 값은 신호가 아니라 생존자 편향이었다는 것까지가 이 노선의 마지막 기록이다.",
        "",
        "## 이 측정이 안 한 것",
        "",
        "- **새 데이터를 한 건도 안 받았다.** 캐시된 패널과 이벤트만 읽는다.",
        "- **파라미터를 안 바꿨다.** 바꾼 건 ②의 상대 하나, 그리고 게이트가 요구할 때만 폭이다.",
        "- **위약의 신뢰구간을 이제 냈다.** largecap 리포트는 점추정 두 개를 나란히 놓은",
        "  것뿐이었고, 이 문서가 그 차이를 처음 검정한다.",
        "",
        f"이벤트 {len(ev)}건 · 재현: `python scripts/measure_pead.py largecap midbase` · "
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
    if MIDBASE and not (LARGECAP or "selftest" in sys.argv):
        raise SystemExit("midbase 는 largecap 과 같이 쓴다: "
                         "python scripts/measure_pead.py largecap midbase")
    raise SystemExit(selftest() if "selftest" in sys.argv else
                     main_midbase() if MIDBASE else main())
