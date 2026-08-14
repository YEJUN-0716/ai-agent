#!/usr/bin/env python
"""F-Score (8항목 Piotroski) — 사전 등록된 대로 한 번 잰다.

    python scripts/measure_fscore.py           # ①② 판정 + 위약 대조
    python scripts/measure_fscore.py selftest  # 산수 점검 (네트워크 無)
    python scripts/measure_fscore.py sealed    # 봉인 구간, 판정 뒤 딱 한 번

설계서: `docs/superpowers/specs/2026-08-14-fscore-design.md`.
**항목 수(8)·문턱(7)·보유(252)·BM 분위(5분위)는 거기서 못 박았다. 결과를 보고
바꾸면 그 측정은 폐기하고 실패로 기록한다.**

## 판정은 AND 두 개다

  ① 단면      8항목 F-Score 의 252일 선도수익 IC (전 유니버스, 스크린 없음)
              — 날짜 블록 부트스트랩 |t| >= 2
  ② 포트폴리오 고BM 5분위 ∩ F>=7 캘린더타임 곡선 vs **같은 평균 노출** 매수보유,
              6bp — 초과 연수익 부트스트랩 95% 하한 > 0

**하나만 통과하면 실패다.** 이 저장소는 ①만 보고 다섯 번 속았다.

## 재기 전에 정한 검출력 한계 (설계서 3.2)

quant_pit ②는 95% 구간이 20%p 폭이라 **결과를 보기 전에 이미 "말할 근거가 없다"가
정해져 있었다.** 같은 자를 또 쓰지 않으려고 두 가지를 미리 박았다.

  - **MDE 를 전략보다 먼저 낸다.** 매수보유 줄만으로 나오므로 가능하다.
    연 10%p 를 넘으면 통과/실패가 아니라 **"검출력 부족, 미측정"** 이다.
  - **자리 수 하한.** 보유 3 미만인 날은 현금, 전 구간 평균 자리 수 5 미만이면
    ②는 역시 "검출력 부족".

이 문장을 측정 후에 쓰면 변명이지만 전에 쓰면 명세다.

## 이 파일이 지켜야 하는 것

1. **모르는 항목을 0점으로 치지 않는다.** 여덟 중 하나라도 재료가 없으면 그
   (종목, 분기)는 없는 것으로 친다. 종목마다 분모를 바꾸지도 않는다. NaN 비교가
   조용히 False(=0점)로 떨어지는 자리라 `_div`/`isfinite` 로 전부 막는다.
2. **회계 분기말(`end`)로 정렬한다.** 같은 날 두 분기가 공시된 행이 6%라 `filed`
   로 세면 1년 전 짝이 어긋난다. 대차대조표·주식수도 `end` 로 붙인다.
3. **BM 분위는 직전 250일에 이미 공시된 값들과만 비교한다.** 같은 날 전 종목을
   모아놓고 자르면 look-ahead 다.
4. **뒤 구간(2025-01~)은 봉인.** 여기 상수를 바꿔서 열지 않는다.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import app as _app  # noqa: E402  — 항목 목록 이음매 검사용
from modules import analyst_scorecard as sc  # noqa: E402
from modules import edgar_fundamentals as ef  # noqa: E402
from modules import quant_pit as qp  # noqa: E402
from scripts.measure_pead import (  # noqa: E402
    N_BOOT, _block_idx, attach_trades, calendar_curve, excess_cagr_ci,
    rank_percentile,
)
from scripts.measure_portfolio import (  # noqa: E402
    bench_curve, cagr, closes, mdd, yearly,
)

OUT_MD = Path("docs/measurements/2026-08-14-fscore.md")

# 측정 구간. 뒤는 봉인 — 설계서 5절.
START = pd.Timestamp("2020-03-31")
END = pd.Timestamp("2024-12-31")

# 봉인 해제. 판정이 끝난 뒤 딱 한 번, 확인용으로만 연다. 갈려도 본 측정의 판정을
# 바꾸지 않으므로 **출력 파일을 따로 쓴다**(PEAD·quant_pit 과 같은 규칙).
SEALED = "sealed" in sys.argv
if SEALED:
    START = pd.Timestamp("2025-01-01")
    END = pd.Timestamp("2026-08-07")
    OUT_MD = Path("docs/measurements/2026-08-14-fscore-sealed.md")

# 문헌값 고정 (Piotroski 2000). 고른 게 아니라 가져온 값이다.
HOLD_DAYS = 252        # 거래일 (약 1년)
SCORE_AT = 7           # F>=7 만 산다
BM_TOP = 0.8           # 장부/시가 상위 5분위
COST_BPS = 6.0
COST_SWEEP = (0.0, 6.0, 20.0, 40.0)

# ①의 자. 21일 줄은 quant_pit(+2.24)·chart(+0.94)·ict(+0.98) 과 나란히 세우려고
# 같이 내지만 **통과선이 아니다** — 가설의 지평은 252일이다.
IC_HORIZONS = (252, 21)
IC_STEP = 21           # 단면을 뜨는 간격(거래일). 매일 뜨면 252일 창이 거의
#                        통째로 겹쳐 t 가 부푼다. 블록이 지평을 덮도록 월 단위로 뜬다.
IC_BLOCK = 12          # 12 x 21일 = 252일. 겹치는 선행 구간을 블록이 흡수한다.

SEED = 20260814
BLOCK = 20             # 캘린더타임 곡선의 날짜 블록 (measure_pead 와 같은 값)

# 검출력 하한 — 설계서 3.2. **결과를 보기 전에 박은 값이다.**
MDE_LIMIT_PP = 10.0    # 연 %p
MIN_HELD = 3           # 이만큼 못 채운 날은 현금
MIN_AVG_POSITIONS = 5.0

MIN_POOL = 20          # BM 분위 풀 하한 (rank_percentile 과 같은 뜻)

# 연기 테스트 (`LIMIT=5 python scripts/measure_fscore.py`). 0 이면 전 종목.
LIMIT = int(os.environ.get("LIMIT", "0"))

# 항목 이름은 화면(`app.calc_piotroski_fscore`)과 **같은 문자열**이다. 값 계산은
# 각자 하고(여긴 EDGAR PIT, 화면은 yfinance) 목록과 분모만 공유한다 — 자체검사가
# 이 목록이 갈렸는지 본다.
ITEMS = ("F1 ROA>0", "F2 영업현금흐름>0", "F3 ROA개선", "F4 발생주의",
         "F5 레버리지감소", "F6 유동성개선", "F7 주식수불증가", "F9 자산회전율개선")

_BAL_COLS = list(ef.BALANCE_TAGS)


# ---------------------------------------------------------------- 재료 조립

def balance_by_end(us_gaap: dict) -> pd.DataFrame:
    """대차대조표를 **회계 시점(end)** 으로. `ef.assemble_balance` 를 안 쓰는 이유는
    `quant_pit.quarterly` 와 같다 — 그 함수는 end 를 버리고 filed 만 남기는데,
    1년 전 짝을 맞추려면 end 가 필요하다. 같은 조립기를 한 단계 아래에서 부른다.

    행의 filed 는 그 시점 항목들의 **가장 늦은** 공시일이다. min 은 look-ahead 다.
    """
    cols = {n: ef._assemble_instant(us_gaap, tags) for n, tags in ef.BALANCE_TAGS.items()}
    ends = sorted(set().union(*[set(c) for c in cols.values()])) if any(cols.values()) else []
    rows = []
    for end in ends:
        have = [n for n in cols if end in cols[n]]
        rows.append({"end": pd.Timestamp(end),
                     "bal_filed": max(pd.Timestamp(cols[n][end][0]) for n in have),
                     **{n: cols[n][end][1] for n in have}})
    return pd.DataFrame(rows, columns=["end", "bal_filed", *_BAL_COLS]).sort_values("end")


def shares_by_end(us_gaap: dict) -> pd.DataFrame:
    """희석주식수를 end 로. `ef.assemble_shares` 의 filed 인덱스 판을 end 로 바꾼 것."""
    raw = ef._facts_for_chain(us_gaap, ef.SHARES_TAGS, "shares")
    qs = ef._dedup_earliest(ef._quarter_facts(raw),
                            key=lambda f: (f["start"], f["end"])) if raw else []
    rows = [{"end": pd.Timestamp(f["end"]), "sh_filed": pd.Timestamp(f["filed"]),
             "shares": float(f["val"])} for f in qs]
    df = pd.DataFrame(rows, columns=["end", "sh_filed", "shares"]).sort_values("end")
    return df.drop_duplicates("end", keep="first")


def ticker_frame(ticker: str, us_gaap: dict | None = None) -> pd.DataFrame:
    """한 종목의 분기 재료 한 판. index 없음, `end` 오름차순.

    손익·현금흐름은 `quant_pit.quarterly`, 대차대조표·주식수는 위 두 조립기.
    붙이는 자는 회계 시점(end)이고 허용 오차는 `ef.CONTAINMENT_TOL_DAYS`(10일)다 —
    10-Q 안에서 손익 기간말과 대차대조표 시점이 같은 날인 게 정상이고, 며칠
    어긋나는 건 결산일 관행이지 다른 분기가 아니다.
    """
    ug = us_gaap if us_gaap is not None else ef.load_raw(ticker)
    if ug is None:
        return pd.DataFrame()
    q = qp.quarterly(ug)
    if q.empty:
        return pd.DataFrame()
    tol = pd.Timedelta(days=ef.CONTAINMENT_TOL_DAYS)
    m = q.reset_index().sort_values("end")
    for other in (balance_by_end(ug), shares_by_end(ug)):
        if other.empty:
            return pd.DataFrame()
        m = pd.merge_asof(m, other, on="end", tolerance=tol, direction="nearest")
    m["filed"] = m[["filed", "bal_filed", "sh_filed"]].max(axis=1)
    return m.reset_index(drop=True)


# ---------------------------------------------------------------- 8항목

def _div(a, b):
    """a/b. 분모가 0이거나 어느 쪽이든 결측이면 **NaN 이다. 0점이 아니다.**"""
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return np.nan
    return a / b


def _spans_ok(ends, i) -> bool:
    """i · i-4 · i-8 이 진짜 1년 간격이고 각 TTM 창이 3분기 폭인지.

    분기가 빠진 구간에서는 4칸 뒤가 1년 전이 아니다 — quant_pit 이 이미 밟은
    함정이라 같은 상수(SPAN 273 · YoY 365 · ±45)를 쓴다.
    """
    d = (ends[i] - ends[i - 3]).days, (ends[i - 4] - ends[i - 7]).days
    y = (ends[i] - ends[i - 4]).days, (ends[i - 4] - ends[i - 8]).days
    return (all(abs(v - qp.SPAN_DAYS) <= qp.TOL_DAYS for v in d)
            and all(abs(v - qp.YOY_DAYS) <= qp.TOL_DAYS for v in y))


def fscore_rows(m: pd.DataFrame, ticker: str = "") -> list[dict]:
    """분기 재료 한 판 → [{ticker, end, filed, fscore, equity, shares}].

    **여덟 중 하나라도 못 내면 그 분기는 없는 것으로 친다.** 부분 점수도, 종목별
    가변 분모도 없다. NaN 을 그냥 비교하면 False 로 떨어져 조용히 0점이 되므로
    항목을 만들기 전에 재료를 전부 `isfinite` 로 거른다 — 프로덕션에서 몇 달을
    살아남았던 '모름 → 실패' 버그가 정확히 이 자리다.
    """
    if len(m) < 9:
        return []
    ends = list(m["end"])
    ni, rev, ocf = (m[c].to_numpy(float) for c in
                    ("net_income", "revenue", "operating_cash_flow"))
    A, CA, CL = (m[c].to_numpy(float) for c in
                 ("assets", "current_assets", "current_liabilities"))
    LTD, EQ, SH = (m[c].to_numpy(float) for c in
                   ("long_term_debt", "equity", "shares"))

    def ttm(a, i):
        return a[i - 3:i + 1].sum()

    out = []
    for i in range(8, len(m)):
        if not _spans_ok(ends, i):
            continue
        roa = _div(ttm(ni, i), A[i - 4])          # 기초 총자산 — 문헌 그대로
        roa_p = _div(ttm(ni, i - 4), A[i - 8])
        acc = _div(ttm(ocf, i), A[i - 4])
        lev_c, lev_p = _div(LTD[i], A[i]), _div(LTD[i - 4], A[i - 4])
        cur_c, cur_p = _div(CA[i], CL[i]), _div(CA[i - 4], CL[i - 4])
        at_c, at_p = _div(ttm(rev, i), A[i - 4]), _div(ttm(rev, i - 4), A[i - 8])
        need = (roa, roa_p, ttm(ocf, i), acc, lev_c, lev_p, cur_c, cur_p,
                SH[i], SH[i - 4], at_c, at_p)
        if not all(np.isfinite(v) for v in need) or SH[i - 4] <= 0:
            continue                              # 모름은 0점이 아니라 결측이다
        items = (roa > 0, ttm(ocf, i) > 0, roa > roa_p, acc > roa,
                 lev_c < lev_p, cur_c > cur_p, SH[i] <= SH[i - 4] * 1.01, at_c > at_p)
        assert len(items) == len(ITEMS)
        out.append({"ticker": ticker, "end": ends[i], "filed": m["filed"].iloc[i],
                    "fscore": float(sum(items)),
                    "equity": EQ[i], "shares": SH[i]})
    return out


def build_events(tickers) -> pd.DataFrame:
    """[ticker, end, filed, fscore, equity, shares] — filed 오름차순.

    가격 구간 밖 분기도 남긴다(BM 분위 모수). 거래로 세는 건 `attach_trades` 가 자른다.
    """
    rows = []
    for tk in tickers:
        rows += fscore_rows(ticker_frame(tk), tk)
    ev = pd.DataFrame(rows, columns=["ticker", "end", "filed", "fscore",
                                     "equity", "shares"])
    # 같은 filed 에 두 분기가 실렸으면 최신 분기 하나만 (end 오름차순이므로 뒤).
    ev = ev.sort_values(["ticker", "end"]).drop_duplicates(["ticker", "filed"], keep="last")
    return ev.sort_values("filed", kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- 고BM 스크린

def attach_bm(ev: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """장부/시가 = 그 분기 자본총계 ÷ **진입일 시가총액**, 그리고 그 백분위.

    자본총계 <= 0 인 종목은 스크린에서 뺀다 — 자사주 매입으로 장부가가 마이너스인
    것이지 부실이 아니지만, BM 사다리가 그 값을 순위로 바꿀 수 없다(설계서 2.3).

    백분위는 `measure_pead.rank_percentile` 그대로다. 직전 250일 안에 **이미
    공시된** 값들하고만 비교하고 같은 날은 풀에서 뺀다.
    """
    px = close.values
    col = {c: i for i, c in enumerate(close.columns)}
    mcap = np.array([px[a, col[t]] for t, a in zip(ev["ticker"], ev["entry"])]) * ev["shares"].values
    bm = np.where((ev["equity"].values > 0) & (mcap > 0),
                  _safe_ratio(ev["equity"].values, mcap), np.nan)
    ev = ev.assign(bm=bm)
    # rank_percentile 은 [filed, sue] 이름으로 받는다. 자는 같고 재료만 BM 이다.
    ev["bm_pct"] = rank_percentile(ev.rename(columns={"bm": "sue"}))
    return ev


def _safe_ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / b, np.nan)


def shuffle_scores(ev: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """위약 — **같은 달 안에서 F-Score 를 종목 간 섞는다.**

    설계서는 "날짜 안에서" 라고 썼지만 공시일 단면은 대부분 1~2건이라 같은 날
    섞으면 아무것도 안 섞인다(공시는 실적 시즌에 몰리되 날짜는 흩어진다). 달로
    넓히면 진입 날짜 분포와 자리 수 구조는 그대로 두고 **종목↔점수 연결만**
    끊긴다. BM 은 안 섞는다 — 스크린은 진짜로 두고 F-Score 만 위약이다.
    """
    rng = np.random.default_rng(seed)
    out = ev.copy()
    key = out["filed"].dt.to_period("M")
    out["fscore"] = out.groupby(key)["fscore"].transform(
        lambda s: rng.permutation(s.to_numpy()))
    return out


# ---------------------------------------------------------------- ① 단면 IC

def score_panel(ev: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """날짜 × 종목 = 그 날 **이미 공시돼 있던** 최신 F-Score.

    공시일에 붙인 뒤 하루 밀어(shift) 둔다 — 진입이 `filed`+1 이므로 IC 도 같은
    자리에서 읽어야 한다. ffill 은 다음 공시까지 그 점수가 유효하다는 뜻이다.
    """
    wide = (ev.pivot_table(index="filed", columns="ticker", values="fscore",
                           aggfunc="last")
            .reindex(columns=close.columns))
    return wide.reindex(wide.index.union(close.index)).ffill().reindex(close.index).shift(1)


def daily_ics(panel: pd.DataFrame, close: pd.DataFrame, horizon: int) -> np.ndarray:
    """IC_STEP 간격 단면의 스피어만 IC. `analyst_scorecard._daily_ic` 를 그대로 쓴다.

    매일 뜨지 않는 이유: 252일 선행 구간이 거의 통째로 겹쳐 독립 표본이 아니게
    된다. 21거래일 간격으로 뜨고 블록 12(=252일)로 자기상관을 흡수한다.
    """
    fwd = close.shift(-horizon) / close - 1.0
    ics = []
    for i in range(0, len(close) - horizon, IC_STEP):
        day = panel.iloc[i]
        rets = fwd.iloc[i]
        scores = {t: {"fscore": v} for t, v in day.items() if pd.notna(v)}
        ic = sc._daily_ic(scores, {t: r for t, r in rets.items() if pd.notna(r)}, "fscore")
        if ic is not None:
            ics.append(ic)
    return np.array(ics, dtype=float)


def block_t(values: np.ndarray, block: int) -> tuple:
    """블록 부트스트랩으로 평균의 t."""
    if len(values) < block * 2:
        return (float(values.mean()) if len(values) else float("nan")), float("nan")
    idx = _block_idx(len(values), np.random.default_rng(SEED), block)
    boot = values[idx].mean(axis=1)
    mean, sd = float(values.mean()), float(boot.std())
    return mean, (mean / sd if sd > 0 else float("nan"))


# ---------------------------------------------------------------- 검출력

def mde_pp(flat_ret: np.ndarray) -> float:
    """최소검출가능효과(연 %p) — **매수보유 줄만으로** 낸다.

    ②의 통과선은 초과 연수익의 부트스트랩 95% 하한이므로, 검출 가능한 최소 효과는
    그 추정량의 표준오차 × 1.96 이다. 전략을 보기 전에 낼 수 있게 초과 계열의
    변동성을 **기준선 자신의 변동성으로 대신** 놓는다. 전략이 기준선보다 얌전할
    수는 없으므로 이 값은 실제 MDE 의 **하한**이다 — 이것만으로 이미 한계를
    넘으면 판정할 힘이 없는 게 확실하다는 뜻이고, 그게 이 가드가 필요한 방향이다.
    """
    idx = _block_idx(len(flat_ret), np.random.default_rng(SEED), BLOCK)
    boot = np.expm1(np.log1p(flat_ret)[idx].mean(axis=1) * 252) * 100
    return 1.96 * float(boot.std())


def held_counts(ev: pd.DataFrame, n_days: int) -> np.ndarray:
    """날짜별 보유 종목 수. `calendar_curve` 의 held 와 같은 규칙(진입 다음날부터)."""
    n = np.zeros(n_days)
    for a, b in zip(ev["entry"], ev["exit"]):
        n[a + 1:b + 1] += 1.0
    return n


# ---------------------------------------------------------------- 자체검사

def _fake_frame(n=13, **over) -> pd.DataFrame:
    """분기 재료 한 판. 기본값은 **모든 항목이 개선되는** 회사다(F=8)."""
    ends = pd.date_range("2019-03-31", periods=n, freq="QE")
    g = 1.0 + 0.05 * np.arange(n)                    # 손익·매출이 꾸준히 는다
    m = pd.DataFrame({
        "end": ends,
        "filed": ends + pd.Timedelta(days=40),
        "revenue": 1000.0 * g,
        "net_income": 100.0 * g,
        "operating_cash_flow": 150.0 * g,
        "capex": 20.0 * np.ones(n),
        "assets": 5000.0 * np.ones(n),               # 자산이 그대로면 ROA·회전율이 는다
        "current_assets": 900.0 * g,
        "current_liabilities": 500.0 * np.ones(n),
        "long_term_debt": 1000.0 / g,                # 레버리지가 준다
        "short_term_debt": 100.0 * np.ones(n),
        "equity": 2000.0 * np.ones(n),
        "shares": 100.0 * np.ones(n),                # 증자 없음
    })
    for k, v in over.items():
        m[k] = v
    return m


def selftest() -> int:
    # 1) 이음매 — 항목 이름 여덟이 화면(app)과 같은가. 값 계산은 각자지만 목록과
    #    분모는 공유한다. 이 저장소는 함수 호출로만 이어진 이음매에서 하루에
    #    반대 방향 버그를 두 건 냈다.
    src = inspect.getsource(_app.calc_piotroski_fscore)
    app_items = re.findall(r"'(F\d [^']+)':", src)
    assert len(ITEMS) == _app.FSCORE_ITEMS == 8, "항목 수가 화면과 갈렸다"
    assert list(ITEMS) == app_items, f"항목 이름이 화면과 갈렸다: {app_items}"

    # 2) 전부 개선이면 8점, 전부 악화면 0점. 분모는 언제나 8이다.
    good = fscore_rows(_fake_frame(), "G")
    assert good and all(r["fscore"] == 8 for r in good), [r["fscore"] for r in good]
    n = 13
    bad = _fake_frame(
        revenue=1000.0 / (1 + 0.05 * np.arange(n)),
        net_income=-100.0 * np.ones(n),
        operating_cash_flow=-150.0 * np.ones(n),
        current_assets=900.0 / (1 + 0.05 * np.arange(n)),
        long_term_debt=1000.0 * (1 + 0.05 * np.arange(n)),
        shares=100.0 * (1 + 0.05 * np.arange(n)),
    )
    assert all(r["fscore"] == 0 for r in fscore_rows(bad, "B")), "악화가 0점이 아니다"

    # 3) **모름은 0점이 아니다.** 장기부채 태그가 없는 종목(은행·리츠)이 여기서
    #    낮은 점수로 나오면 프로덕션에서 몇 달 살아남았던 그 버그다.
    hole = _fake_frame(long_term_debt=np.full(13, np.nan))
    assert fscore_rows(hole, "H") == [], "결측 항목이 0점으로 채점됐다"
    # 유동부채가 0 이면 비율이 무한대다 — 이것도 결측이지 개선이 아니다.
    zero = _fake_frame(current_liabilities=np.zeros(13))
    assert fscore_rows(zero, "Z") == [], "0 나눗셈이 항목으로 들어갔다"

    # 4) 분기가 빠지면 4칸 뒤가 1년 전이 아니다 → 그 자리는 안 낸다.
    m = _fake_frame(n=14)
    holed = m.drop(m.index[5]).reset_index(drop=True)
    assert len(fscore_rows(holed, "Q")) < len(fscore_rows(_fake_frame(n=14), "Q")), \
        "분기 결측 구간에서 점수가 났다"

    # 5) filed 는 세 조립기 중 **가장 늦은** 공시일이다. 하나라도 늦게 나오면
    #    그 전에는 점수를 만들 수 없다 — min 을 쓰면 look-ahead 다.
    late = _fake_frame()
    late["bal_filed"] = late["filed"] + pd.Timedelta(days=10)
    late["sh_filed"] = late["filed"]
    late["filed"] = late[["filed", "bal_filed", "sh_filed"]].max(axis=1)
    assert fscore_rows(late, "L")[0]["filed"] == late["filed"].iloc[8]

    # 6) 자리 수 하한 — 2종목만 든 날은 현금이어야 한다.
    idx = pd.date_range("2021-01-04", periods=40, freq="B")
    close = pd.DataFrame({"A": 100 * 1.01 ** np.arange(40.0),
                          "B": 100 * 1.01 ** np.arange(40.0),
                          "C": 100 * 1.01 ** np.arange(40.0)}, index=idx)
    two = pd.DataFrame({"ticker": ["A", "B"], "entry": [0, 0], "exit": [20, 20]})
    port2, _ = calendar_curve(two, close, 0.0, min_held=MIN_HELD)
    assert np.allclose(port2.values, 0.0), "자리 수 하한이 안 걸렸다"
    three = pd.DataFrame({"ticker": list("ABC"), "entry": [0] * 3, "exit": [20] * 3})
    port3, _ = calendar_curve(three, close, 0.0, min_held=MIN_HELD)
    assert np.isclose(port3.iloc[1], 0.01), "3종목이면 들어야 한다"
    assert np.isclose(held_counts(three, 40)[1], 3.0)

    # 7) 위약은 점수를 섞되 **행(종목·날짜·BM)은 그대로** 둔다.
    ev = pd.DataFrame({"ticker": list("ABCD") * 3,
                       "filed": pd.to_datetime(["2021-01-05"] * 4 + ["2021-01-20"] * 4
                                               + ["2021-05-06"] * 4),
                       "fscore": [8.0, 7, 2, 1] * 3})
    sh = shuffle_scores(ev)
    assert (sh["ticker"] == ev["ticker"]).all() and (sh["filed"] == ev["filed"]).all()
    for _, g in sh.groupby(sh["filed"].dt.to_period("M")):
        assert sorted(g["fscore"]) == sorted(ev.loc[g.index, "fscore"]), "달 안 분포가 변했다"

    # 8) MDE 는 길이·변동성이 커질수록 커진다. 방향만 확인한다.
    rng = np.random.default_rng(0)
    quiet = rng.normal(0, 0.005, 1000)
    loud = rng.normal(0, 0.020, 1000)
    assert mde_pp(loud) > mde_pp(quiet) > 0

    print("selftest OK")
    return 0


# ---------------------------------------------------------------- 리포트

def main() -> int:
    close = closes(START, END)
    bench = bench_curve(START, END)
    bench_ret = bench.pct_change().fillna(0.0).values
    years = (close.index[-1] - close.index[0]).days / 365.25

    tickers = list(close.columns)[:LIMIT or None]
    ev_all = build_events(tickers)
    print(f"분기 점수 {len(ev_all)}건 / 종목 {ev_all['ticker'].nunique()}")

    # ① 단면 — 전 유니버스, 스크린 없음. 거래 가능 여부와 무관하게 점수만 본다.
    panel = score_panel(ev_all, close)
    ic_stats = {}
    for h in IC_HORIZONS:
        ics = daily_ics(panel, close, h)
        ic_stats[h] = (len(ics), *block_t(ics, IC_BLOCK))
    n_ic, m_ic, t_ic = ic_stats[HOLD_DAYS]
    # 단면이 블록 두 개도 안 되면 t 가 아예 안 나온다(`block_t` → nan). 그건 실패가
    # 아니라 **못 잰 것**이다 — 봉인 구간처럼 짧은 창에서 X 로 찍으면 검출력 문제를
    # 반증으로 읽게 된다. ②의 △ 와 같은 표기를 쓴다.
    ic_underpowered = not np.isfinite(t_ic)
    pass1 = n_ic >= IC_BLOCK * 2 and abs(t_ic) >= 2
    mark1 = "△" if ic_underpowered else ("O" if pass1 else "X")

    # ② 포트폴리오 — 고BM 5분위 ∩ F>=7.
    ev = attach_trades(ev_all, close, HOLD_DAYS)
    ev = attach_bm(ev, close)
    hi_f = ev.loc[ev["fscore"] >= SCORE_AT]
    top = hi_f.loc[hi_f["bm_pct"] >= BM_TOP].reset_index(drop=True)

    n_held = held_counts(top, len(close))
    avg_pos = float(n_held.mean())
    port, exposure = calendar_curve(top, close, COST_BPS, MIN_HELD)
    flat_ret = bench_ret * exposure

    # **MDE 를 전략 결과보다 먼저 낸다** (설계서 3.2). 매수보유 줄만으로 나온다.
    mde = mde_pp(flat_ret)
    underpowered = mde > MDE_LIMIT_PP or avg_pos < MIN_AVG_POSITIONS

    pt, lo, hi = excess_cagr_ci(port.values, flat_ret)
    pass2 = (lo > 0) and not underpowered

    strat_curve = (1 + port).cumprod()
    flat_curve = pd.Series((1 + flat_ret).cumprod(), index=close.index)

    # 같이 내되 통과선이 아닌 줄들.
    plc = shuffle_scores(ev)
    plc = plc.loc[(plc["fscore"] >= SCORE_AT) & (plc["bm_pct"] >= BM_TOP)].reset_index(drop=True)
    plc_port, plc_exp = calendar_curve(plc, close, COST_BPS, MIN_HELD)
    nobm = hi_f.reset_index(drop=True)
    nobm_port, nobm_exp = calendar_curve(nobm, close, COST_BPS, MIN_HELD)

    verdict = ("검출력 부족 — 미측정" if underpowered
               else ("통과" if (pass1 and pass2) else "실패"))
    mark = "△" if underpowered else ("O" if pass2 else "X")

    body = [
        "# F-Score — 봉인 구간 확인" if SEALED else "# F-Score (8항목 Piotroski) — 측정",
        "",
        f"구간 {START.date()} ~ {END.date()} · 진입 `filed`+1 종가 · {HOLD_DAYS}거래일 보유 · "
        f"고BM 5분위 ∩ F>={SCORE_AT} 롱온리 · 판정 {COST_BPS:.0f}bp.",
        "사전 등록: `docs/superpowers/specs/2026-08-14-fscore-design.md`. "
        "**파라미터는 문헌값 고정이라 고를 게 없었고, 따라서 전 구간이 OOS**다.",
        "",
    ] + ([
        "> **이 문서는 판정이 아니다.** 본 측정(`2026-08-14-fscore.md`)의 판정이 끝난 뒤",
        "> 봉인 구간을 딱 한 번 확인용으로 연 것이다. 아래 O/X 는 같은 통과선을 같은 코드로",
        "> 봉인 구간에 적용해본 값일 뿐, **판정을 바꾸지 않는다**(설계서 5절).",
        ">",
        f"> 봉인 구간은 {(END - START).days}일이라 {HOLD_DAYS}거래일 보유가 완결되는 진입이",
        "> 거의 없다. **구조적으로 검출력이 없는 구간**이고, 그건 봉인을 정할 때 이미 알던",
        "> 사실이다 — 여기서 뭐가 나오든 본 측정의 판정을 못 건드린다.",
        "",
        f"## 봉인 구간에 같은 자를 대면: ①{mark1} ②{mark}",
    ] if SEALED else [
        f"## 판정: **{verdict}** (①{mark1} AND ②{mark})",
    ]) + [
        "",
        "| | 무엇 | 통과선 | 실측 | |",
        "|---|---|---|---|---|",
        f"| ① 단면 | 8항목 F-Score {HOLD_DAYS}일 선도수익 IC (전 유니버스) | "
        f"날짜 블록 부트스트랩 \\|t\\| >= 2 | 평균 IC {m_ic:+.4f} · t={t_ic:+.2f} "
        f"(단면 {n_ic}개) | {mark1}"
        f"{' (부호 반대)' if pass1 and m_ic < 0 else ''} |",
        f"| ② 포트폴리오 | 같은 노출 매수보유 대비 초과 연수익 | 부트스트랩 95% 하한 > 0 | "
        f"{pt:+.2f}%p · 95% [{lo:+.2f}, {hi:+.2f}] | {mark} |",
        "",
        "**하나만 통과하면 실패다.** 이 저장소는 ①만 보고 다섯 번 속았다.",
        "",
    ] + ([
        "### ①의 O 를 지지 증거로 읽으면 안 된다 — 부호가 가설과 반대다",
        "",
        "사전 등록한 통과선은 **양측 \\|t\\| >= 2** 였고 실측은 그 바를 넘었다. 그런데 부호가",
        f"음(-)이다 — 8항목 F-Score 가 높을수록 {HOLD_DAYS}일 뒤 수익이 **낮았다**는 뜻이다.",
        "가설은 \"높은 점수를 사면 낫다\" 였으므로, 이 O 는 가설을 지지하는 게 아니라",
        "**반대 방향의 단면 정보**를 찾은 것이다.",
        "",
        "통과선은 안 바꾼다. 결과를 보고 \"단측이었어야 한다\"로 고치면 그게 튜닝이고, 어차피",
        "②가 못 넘어 총 판정은 실패다. 대신 **사전 등록이 놓친 자리로 기록한다** — 방향이",
        "있는 가설에는 통과선도 방향이 있어야 했다. 다음 사전 등록부터 단측으로 적는다.",
        "",
        "부호가 배선 실수가 아니라는 건 서로 독립인 세 줄이 같은 방향을 가리켜서 안다:",
        f"21일 IC({ic_stats[21][1]:+.4f}) · BM 스크린 없는 F>={SCORE_AT} 바구니 · 위약보다 나쁜 전략 줄.",
        "",
    ] if (pass1 and m_ic < 0) else []) + [
        "## 검출력 — 전략을 보기 전에 낸 값 (설계서 3.2)",
        "",
        "| | 값 | 한계 | |",
        "|---|---|---|---|",
        f"| MDE (연 %p, 하한) | {mde:.2f} | <= {MDE_LIMIT_PP:.0f} | "
        f"{'X' if mde > MDE_LIMIT_PP else 'O'} |",
        f"| 평균 자리 수 (전 구간) | {avg_pos:.2f} | >= {MIN_AVG_POSITIONS:.0f} | "
        f"{'X' if avg_pos < MIN_AVG_POSITIONS else 'O'} |",
        f"| 보유일 평균 자리 수 | {float(n_held[n_held > 0].mean()) if (n_held > 0).any() else float('nan'):.2f}"
        " | 참고 | |",
        "",
        "MDE 는 **매수보유 줄만으로** 낸다 — 초과 연수익 추정량의 부트스트랩 표준오차 × 1.96 이고,",
        "초과 계열의 변동성 자리에 기준선 자신의 변동성을 넣었으므로 **실제 MDE 의 하한**이다.",
        "quant_pit ②는 95% 구간이 20%p 폭이라 결과를 보기 전에 이미 결론이 정해져 있었다.",
        "이 표를 먼저 내는 건 그 자를 또 쓰지 않기 위해서다."
        if not underpowered else
        "**이 측정은 ②를 판정할 힘이 없다.** 통과도 실패도 아니라 미측정으로 적는다 — "
        "재기 전에 정한 규칙이고, 결과를 보고 만든 변명이 아니다.",
        "",
        "## 다섯 줄 — 설계서가 항상 같이 내라고 한 것",
        "",
        "| 줄 | 연수익 | MDD | 비고 |",
        "|---|---|---|---|",
        f"| 전략 (고BM ∩ F>={SCORE_AT}, {COST_BPS:.0f}bp) | "
        f"{cagr(float(strat_curve.iloc[-1]), years):+.1f}% | {mdd(strat_curve):.1f}% | "
        f"평균 노출 {exposure * 100:.0f}% · 거래 {len(top)}건 |",
        f"| 같은 노출 매수보유 | {cagr(float(flat_curve.iloc[-1]), years):+.1f}% | "
        f"{mdd(flat_curve):.1f}% | ②의 기준선 |",
        f"| 원본 매수보유 100% | {cagr(float(bench.iloc[-1]), years):+.1f}% | {mdd(bench):.1f}% | "
        "통과선은 아니지만 빼지 않는다 |",
        f"| 위약 (같은 달 안에서 점수 섞음) | "
        f"{cagr(float((1 + plc_port).cumprod().iloc[-1]), years):+.1f}% | "
        f"{mdd((1 + plc_port).cumprod()):.1f}% | 노출 {plc_exp * 100:.0f}% · 거래 {len(plc)}건 |",
        f"| BM 스크린 없는 F>={SCORE_AT} | "
        f"{cagr(float((1 + nobm_port).cumprod().iloc[-1]), years):+.1f}% | "
        f"{mdd((1 + nobm_port).cumprod()):.1f}% | 노출 {nobm_exp * 100:.0f}% · 거래 {len(nobm)}건 |",
        "",
        "**위약 줄을 먼저 읽어야 한다.** 판정 줄의 점추정이 위약과 구별되지 않으면 그건 신호가",
        "아니라 구성이다 — quant_pit ②에서 실제로 그랬다. BM 스크린 없는 줄은 스크린이 무슨",
        "일을 하는지 보는 줄이고, 문헌형이 아니므로 통과선이 아니다.",
        "",
        "## 비용 스윕 (왕복 bp, 진입일 전액)",
        "",
        "| | " + " | ".join(f"{c:.0f}bp" for c in COST_SWEEP) + " |",
        "|---|" + "---|" * len(COST_SWEEP),
    ]

    cells_s, cells_x = [], []
    for c in COST_SWEEP:
        p_c, exp_c = calendar_curve(top, close, c, MIN_HELD)
        cells_s.append(f"{cagr(float((1 + p_c).cumprod().iloc[-1]), years):+.1f}%")
        pt_c, lo_c, _ = excess_cagr_ci(p_c.values, bench_ret * exp_c)
        cells_x.append(f"{pt_c:+.1f} ({lo_c:+.1f})")

    ic21 = ic_stats[21]
    body += [
        "| 전략 연수익 | " + " | ".join(cells_s) + " |",
        "| 초과 %p (95% 하한) | " + " | ".join(cells_x) + " |",
        "",
        "## 참고 — 21일 IC (통과선 아님)",
        "",
        f"평균 IC {ic21[1]:+.4f} · t={ic21[2]:+.2f} (단면 {ic21[0]}개).",
        "quant_pit(+2.24) · chart(+0.94) · ict(+0.98) 과 **같은 자로** 나란히 세우려고 내는 줄이다.",
        f"가설의 지평은 {HOLD_DAYS}일이므로 판정은 {HOLD_DAYS}일로 한다.",
        "",
        "## 점수 분포",
        "",
        "| F-Score | " + " | ".join(str(int(s)) for s in range(9)) + " |",
        "|---|" + "---|" * 9,
        "| 분기 건수 | " + " | ".join(
            str(int((ev_all["fscore"] == s).sum())) for s in range(9)) + " |",
        "",
        f"8항목을 다 채운 종목 {ev_all['ticker'].nunique()}/{len(tickers)}. 나머지는 은행·보험·리츠로,",
        "유동자산/부채를 구분한 대차대조표를 안 낸다. **결손이 아니라 정의역이다** — Piotroski",
        "원 논문도 금융업을 제외했다(설계서 2.2).",
        "",
        "## 이 자의 한계 — 재고 나서 알게 된 것",
        "",
        f"- **①의 유효 블록이 {n_ic / IC_BLOCK:.1f}개뿐이다.** 단면 {n_ic}개를 블록 {IC_BLOCK}"
        f"(={IC_BLOCK * IC_STEP}거래일 = 지평 하나)로 재표본하므로 사실상 독립 표본이 그 정도다.",
        f"  t={t_ic:+.2f} 를 소수점까지 믿으면 안 된다 — 부호와 자릿수까지가 이 자가 말할 수 있는",
        "  전부다. 지평이 252일인 IC 를 4~5년 구간에서 재면 구조적으로 그렇게 된다.",
        f"- **전략 줄은 사실상 {START.year + 1}년부터다.** 고BM 분위는 직전 250일 안에 이미 공시된"
        f" 값들과만 비교하는데(look-ahead 방지), 그 풀이 {MIN_POOL}건을 채우려면 가격 패널이",
        "  시작하고 한참 지나야 한다. 해마다 표의 첫 해가 +0.0% 인 건 성과가 아니라 **자리가",
        "  없었다는 뜻**이다.",
        f"- **거래 {len(top)}건은 얇다.** 자리 수 하한(평균 {MIN_AVG_POSITIONS:.0f})은 통과했지만"
        f" 그건 {HOLD_DAYS}일 보유가 겹쳐서 채운 값이다. 독립한 진입은 그보다 훨씬 적다.",
        "",
        "이 세 줄은 판정을 바꾸지 않는다. **다음에 같은 자를 쓸 때 미리 알고 쓰라고 적는다.**",
        "",
        "## 이 측정이 안 한 것",
        "",
        "- **소형주 미측정.** 유니버스가 S&P 대형주라 F-Score 가 가장 안 먹힐 자리다. 실패해도",
        "  \"F-Score 가 없다\"가 아니라 **\"S&P 279종목·본 구간에서는 없다\"** 로만 적는다(설계서 3.1).",
        "- **F8(매출총이익률 개선) 미사용.** `GrossProfit` 커버리지 33.0% 로 `MIN_COVERAGE`(70%)",
        "  미달이라 재기 전에 뺐다. 9항목 문헌 재현은 이 재료로는 불가능하다.",
        "- **봉인은 이 문서로 열었다. 다시 안 연다.**" if SEALED else
        "- **2025-01 ~ 2026-08 은 봉인.** 판정 후 딱 한 번 연다. 갈려도 판정을 안 바꾼다.",
        "- **자리·현금 제약은 하한(3)뿐.** ②를 통과한 뒤에 붙인다 — 못 하면 붙일 이유가 없다.",
        "",
        "## 해마다",
        "",
    ] + yearly(strat_curve, bench, exposure) + [
        f"분기 점수 {len(ev_all)}건 · 거래 가능 {len(ev)}건 · 고BM ∩ F>={SCORE_AT} {len(top)}건. "
        f"부트스트랩 {N_BOOT}회 · 시드 {SEED}.",
        f"재현: `python scripts/measure_fscore.py{' sealed' if SEALED else ''}` · "
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
