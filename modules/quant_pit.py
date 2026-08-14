"""퀀트+재무 점수의 재료를 EDGAR 시점 데이터로 바꾼 것 — `quant_pit`.

    python modules/quant_pit.py        # 자체검사 (네트워크 無)

설계서: `docs/superpowers/specs/2026-08-13-quant-pit-design.md`.
**가중치·재정규화·점수화 문턱은 거기서 못 박았다. 결과를 보고 바꾸면 폐기다.**

## 현행 `fundamental_score` 를 고치지 않는다

새 함수를 옆에 만든다. 현행 점수는 화면과 실기록이 계속 쓰고, 비교 대상으로도
필요하다. **점수화 함수는 app.py 것을 그대로 import 한다** — 재료만 바꾸는
측정이라 점수화까지 바꾸면 무엇이 원인인지 못 가른다(설계서 2.2).

## 8항목 중 6개만 — 그리고 비율은 손대지 않는다

EDGAR 캐시로 지금 당장 만들 수 있는 것만 남기고 **현행 가중치 비율을 그대로
유지한 채** 남은 80% 로 재정규화한다. 비율을 손으로 고르는 순간 이 측정은
튜닝이 된다.

  밸류에이션 20% → PER·PBR 만 (PEG·EV/EBITDA 태그 없음), 0.35/0.25 를 0.6 으로
  수익성     20% → ROE·순이익률 (ROA 는 총자산 태그 없음), 0.4/0.3 을 0.7 로
  성장성     13% · FCF품질 12% · MDD 8% · 52주위치 7% → 전부 채택
  안전성     10% · F-Score 10% → **제외** (태그를 새로 모아야 한다)

PER 은 절대 점수만 쓴다. `_score_per_relative` 가 쓰는 yfinance `sector` 는
시점 데이터가 아니라 **오늘의 라벨**이라 과거로 되돌릴 수 없다.

## 시점 규칙 — 이 측정의 전부

- 분기 값은 **`filed` 다음 거래일부터** 쓴다(`filed < asof`). 공시 당일은 안 쓴다.
- 주가가 필요한 항목(PER·PBR·FCF수익률의 시가총액)은 **그 날짜의 종가**.
- 손익·현금흐름은 **TTM(직전 4분기 합)**, 자본총계·주식수는 시점 팩트라 최신 값.
- 같은 날 두 분기가 공시된 행이 6% 라 `filed` 순서로 세면 짝이 어긋난다 →
  회계 분기말(`end`)로 정렬하고 간격을 검증한다(설계서 3절 함정 1).

계산할 수 없으면 **None 을 낸다.** 중립값 50 으로 채우면 '계산 불가'가
'중립 판단'으로 성적에 섞인다 — analyst_log 와 같은 규칙이다.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import app as _app  # noqa: E402  — 점수화 함수는 화면과 같은 것을 쓴다
from modules import edgar_fundamentals as ef  # noqa: E402

# TTM 을 내는 손익·현금흐름 태그. 넷 다 있어야 한 분기로 센다 — 하나가 비면
# 그 분기의 FCF 나 순이익률이 조용히 다른 분기 값과 섞인다.
TTM_TAGS = ("revenue", "net_income", "operating_cash_flow", "capex")
# 현금흐름표는 누적(YTD)으로 실린다 — ef._ytd_quarters 로 따로 조립한다.
YTD_TAGS = ef.YTD_TAGS
# 이 둘이 없으면 그 분기는 없는 것으로 친다. 현금흐름은 빠질 수 있다 —
# ADP 처럼 CapEx 를 `PaymentsToAcquireOtherPropertyPlantAndEquipment` 로 내는
# 발행사가 279종목 중 70개 가까이 되고, 태그를 새로 모으는 건 이번 범위 밖이다
# (설계서 2.1). 그 종목은 FCF 품질만 중립 50 이 된다 — 현행 fundamental_score
# 도 yfinance 가 freeCashflow 를 안 주면 같은 자리에 50 을 넣는다.
CORE_TAGS = ("revenue", "net_income")

# 4개 분기말의 간격(3분기 ≈ 273일)과 1년 전 짝(365일). PEAD 와 같은 ±45일.
SPAN_DAYS, YOY_DAYS, TOL_DAYS = 273, 365, 45

# 현행 fundamental_score 의 항목 가중치에서 **만들 수 있는 것만** 남긴 것.
# 합이 0.80 인 게 정상이다 — 안전성(10%)·F-Score(10%) 를 뺀 자리다.
WEIGHTS = {"밸류에이션": 0.20, "수익성": 0.20, "성장성": 0.13,
           "FCF품질": 0.12, "MDD": 0.08, "52주위치": 0.07}
WEIGHT_SUM = 0.80

# 항목 안쪽 비율도 현행 그대로 두고 남은 것만 재정규화한다.
PER_W, PBR_W = 0.35, 0.25          # ÷ 0.60  (PEG·EV/EBITDA 제외)
ROE_W, PM_W = 0.40, 0.30           # ÷ 0.70  (ROA 제외)


# 발행사마다 같은 줄을 다른 태그로 낸다 — GS 는 매출이 `Revenues` 가 아니라
# `RevenuesNetOfInterestExpense` 이고, ITW 는 순이익이 `NetIncomeLoss` 가 아니라
# `ProfitLoss` 다. edgar_fundamentals.TAG_CHAINS 만으로는 279종목 중 58개가
# 통째로 빠진다(빠지는 쪽이 은행·유틸리티·리츠라 편향까지 생긴다).
#
# **production 의 TAG_CHAINS 는 안 건드린다.** 여기서만 대체 태그를 얹는다 —
# 측정 도중에 factor_engine 이 쓰는 재무값이 바뀌면 무엇이 원인인지 못 가른다.
# quant_pit 이 통과하면 그때 위로 올린다(설계서 2.1 과 같은 규칙).
EXTRA_TAGS = {
    "revenue": ["RevenuesNetOfInterestExpense",
                "RegulatedAndUnregulatedOperatingRevenue",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
}

# 태그를 고를 때 보는 구간. 최근 커버리지가 중요하다 — 2018년에 끊긴 태그가
# 사실 수가 더 많다고 뽑히면 501일 창에서는 아무 값도 안 나온다.
TAG_PICK_SINCE = "2019-01-01"


def _chain(name: str) -> list:
    return list(ef.TAG_CHAINS[name]) + EXTRA_TAGS.get(name, [])


def _best_series(us_gaap: dict, name: str) -> dict:
    """대체 목록 중 **최근 분기를 가장 많이 채우는 태그 하나**로 조립한다.

    `_facts_for_chain` 처럼 다 합치면 안 된다 — `NetIncomeLoss` 와 `ProfitLoss`
    는 소수주주지분만큼 값이 다르고, 같은 이력 안에서 섞이면 있지도 않은 YoY
    점프가 생긴다. 한 종목 안에서는 한 정의로 간다.

    고르는 자는 **조립 후 분기 수**다. 원시 사실 수로 고르면 CAT 처럼 연간
    공시가 없어 Q4 가 유도되지 않는 태그가 뽑히고, 그 종목은 해마다 한 분기가
    비어 TTM 이 영영 안 선다.
    """
    best, best_n = {}, 0
    for tag in _chain(name):
        if tag not in us_gaap:
            continue
        d = (ef._ytd_quarters(us_gaap, [tag]) if name in YTD_TAGS
             else ef._assemble_tag(us_gaap, [tag]))
        n = sum(1 for end in d if end >= TAG_PICK_SINCE)
        if n > best_n:
            best, best_n = d, n
    return best


def quarterly(us_gaap: dict) -> pd.DataFrame:
    """index=회계 분기말(end), cols=[filed, revenue, net_income, operating_cash_flow, capex].

    `assemble_income` 을 안 쓰는 이유는 measure_pead 와 같다 — 그 함수는 end 를
    버리고 filed 만 인덱스로 남기는데, 같은 날 두 분기가 실린 행이 6% 라 분기
    짝이 어긋난다. 같은 모듈의 조립기를 한 단계 아래에서 부른다.

    행의 `filed` 는 그 분기 태그들의 **가장 늦은** 공시일이다. 마지막 조각이
    나오기 전에는 그 행 전체를 알 수 없다 — min 을 쓰면 look-ahead 다.
    """
    cols = {n: _best_series(us_gaap, n) for n in TTM_TAGS}
    ends = sorted(set().union(*[set(c) for c in cols.values()])) if any(cols.values()) else []
    rows = []
    for end in ends:
        have = [n for n in TTM_TAGS if end in cols[n]]
        if not set(CORE_TAGS) <= set(have):
            continue                      # 손익이 없으면 그 분기는 없는 것과 같다
        rows.append({"end": pd.Timestamp(end),
                     "filed": max(pd.Timestamp(cols[n][end][0]) for n in have),
                     **{n: cols[n][end][1] for n in have}})
    if not rows:
        return pd.DataFrame(columns=["filed", *TTM_TAGS])
    # 한 종목이 CapEx 를 아예 안 내면 그 열 자체가 없어진다 — 열을 고정해 둔다.
    return (pd.DataFrame(rows).sort_values("end").set_index("end")
            .reindex(columns=["filed", *TTM_TAGS]))


def load_ticker(ticker: str, us_gaap: dict | None = None) -> dict | None:
    """한 종목의 재료를 한 번만 조립한다 — 501일을 도는 백필이 날짜마다 다시 파싱하지 않도록."""
    ug = us_gaap if us_gaap is not None else ef.load_raw(ticker)
    if ug is None:
        return None
    q = quarterly(ug)
    if q.empty:
        return None
    return {"q": q, "equity": ef.assemble_equity(ug), "shares": ef.assemble_shares(ug)}


def _latest_before(series: pd.Series, asof: pd.Timestamp):
    """filed 인덱스 Series 에서 asof **전에** 공시된 마지막 값."""
    if series is None or series.empty:
        return None
    past = series[series.index < asof]
    return float(past.iloc[-1]) if len(past) else None


def pit_fundamentals(parts: dict, asof: pd.Timestamp) -> dict | None:
    """asof 시점에 **이미 공시돼 있던** 재무. 못 만들면 None.

    TTM 은 직전 4분기 합, 성장성은 그 앞 4분기 합과의 비교라 8분기가 필요하다.
    분기가 빠진 구간에서는 4칸 뒤가 1년 전이 아니므로 end 간격으로 걸러낸다.
    """
    q = parts["q"]
    avail = q[q["filed"] < asof]
    if len(avail) < 8:
        return None

    last4, prev4 = avail.iloc[-4:], avail.iloc[-8:-4]
    if abs((last4.index[-1] - last4.index[0]).days - SPAN_DAYS) > TOL_DAYS:
        return None
    if abs((prev4.index[-1] - prev4.index[0]).days - SPAN_DAYS) > TOL_DAYS:
        return None
    if abs((last4.index[-1] - prev4.index[-1]).days - YOY_DAYS) > TOL_DAYS:
        return None

    shares = _latest_before(parts["shares"], asof)
    if not shares or shares <= 0:
        return None      # 시가총액을 못 내면 밸류에이션·FCF 가 통째로 빈다
    # 자본총계가 음수인 회사가 279종목에 15개 있다(AZO·BA·MCD·PM…). 자사주
    # 매입으로 장부가가 마이너스인 것이지 부실이 아니고, PBR·ROE 사다리는 그
    # 값을 순위로 바꿀 수 없다 → 두 칸만 중립으로 두고 종목은 남긴다.
    # 현행 fundamental_score 도 yfinance 가 priceToBook 을 안 주면 50 을 넣는다.
    equity = _latest_before(parts["equity"], asof)
    if equity is not None and equity <= 0:
        equity = None

    ttm = last4[list(TTM_TAGS)].sum(min_count=4)   # 한 분기라도 비면 그 TTM 은 NaN
    prev = prev4[list(TTM_TAGS)].sum(min_count=4)
    if not (ttm["revenue"] > 0) or not (prev["revenue"] > 0):
        return None
    fcf = ttm["operating_cash_flow"] - ttm["capex"]

    return {
        "net_income": float(ttm["net_income"]),
        "revenue": float(ttm["revenue"]),
        "fcf": None if pd.isna(fcf) else float(fcf),
        "equity": equity,
        "shares": shares,
        "revenue_yoy": float(ttm["revenue"] / prev["revenue"] - 1.0),
        # 순이익 YoY 는 앞 값이 음수면 부호가 뒤집혀 의미가 없다 → 그때는 성장성을
        # 매출만으로 낸다(현행 fundamental_score 가 earningsGrowth 결측일 때와 같다).
        "income_yoy": (float(ttm["net_income"] / prev["net_income"] - 1.0)
                       if prev["net_income"] > 0 else None),
    }


def score_from(parts: dict, asof: pd.Timestamp, df: pd.DataFrame) -> float | None:
    """조립된 재료 + 그 날짜까지 자른 가격창 → 0~100. 못 내면 None.

    df 는 백필 워커가 쓰는 창(400일)을 그대로 받는다 — MDD·52주위치가 현행
    fundamental_score 와 같은 재료를 보게 하려면 같은 창이어야 한다.
    """
    asof = pd.Timestamp(asof)
    if df is None or len(df) == 0 or df.index[-1] > asof:
        return None
    f = pit_fundamentals(parts, asof)
    if f is None:
        return None

    close = float(df["Close"].iloc[-1])
    mcap = close * f["shares"]
    if not (mcap > 0):
        return None

    per = mcap / f["net_income"] if f["net_income"] > 0 else None
    pbr = mcap / f["equity"] if f["equity"] else None
    roe = f["net_income"] / f["equity"] if f["equity"] else None
    pm = f["net_income"] / f["revenue"]
    fcf_yield = None if f["fcf"] is None else f["fcf"] / mcap * 100.0

    det = {}
    det["밸류에이션"] = (_app._score_per(per) * PER_W
                        + _app._score_pbr(pbr) * PBR_W) / (PER_W + PBR_W)
    det["수익성"] = (_app._score_roe(roe) * ROE_W
                    + _app._score_profit_margin(pm) * PM_W) / (ROE_W + PM_W)
    if f["income_yoy"] is None:
        det["성장성"] = float(_app._score_growth(f["revenue_yoy"]))
    else:
        det["성장성"] = (_app._score_growth(f["revenue_yoy"]) * 0.5
                        + _app._score_growth(f["income_yoy"]) * 0.5)
    det["FCF품질"] = float(_app._score_fcf_yield(fcf_yield))
    det["MDD"] = float(_app._score_mdd(_app.calc_mdd(df["Close"])))
    if len(df) >= 30:
        det["52주위치"] = float(_app._score_52w_position(
            close, float(df["High"].tail(252).max()), float(df["Low"].tail(252).min())))
    else:
        return None

    total = sum(det[k] * w for k, w in WEIGHTS.items()) / WEIGHT_SUM
    return float(np.clip(total, 0.0, 100.0))


def quant_pit_score(ticker: str, asof, df: pd.DataFrame,
                    parts: dict | None = None) -> float | None:
    """한 종목 한 날짜의 `quant_pit` 점수. parts 를 주면 재파싱을 건너뛴다."""
    parts = parts or load_ticker(ticker)
    return None if parts is None else score_from(parts, pd.Timestamp(asof), df)


# ---------------------------------------------------------------- 자체검사

def _fake_parts(filed_shift_days=0, drop_quarter=None):
    """16분기 합성 재료. 매출·순이익이 분기마다 10% 씩 크는 회사."""
    ends = pd.date_range("2021-03-31", periods=16, freq="QE")
    filed = ends + pd.Timedelta(days=40 + filed_shift_days)
    base = 100.0 * 1.10 ** np.arange(16)
    q = pd.DataFrame({
        "filed": filed, "revenue": base * 10, "net_income": base,
        "operating_cash_flow": base * 1.5, "capex": base * 0.5,
    }, index=ends)
    if drop_quarter is not None:
        q = q.drop(q.index[drop_quarter])
    return {"q": q,
            "equity": pd.Series(base * 20, index=filed),
            "shares": pd.Series(np.full(16, 1000.0), index=filed)}


def _fake_prices(asof, n=300, drift=1.0005):
    idx = pd.bdate_range(end=asof, periods=n)
    close = 100.0 * drift ** np.arange(n)
    return pd.DataFrame({"Close": close, "High": close * 1.01, "Low": close * 0.99},
                        index=idx)


def selftest() -> int:
    parts = _fake_parts()
    q = parts["q"]
    filed_last = q["filed"].iloc[-1]

    # 1) 공시 당일은 안 쓴다 — filed 당일과 그 다음날의 재료가 달라야 한다.
    f_same = pit_fundamentals(parts, filed_last)
    f_next = pit_fundamentals(parts, filed_last + pd.Timedelta(days=1))
    assert f_same["net_income"] < f_next["net_income"], "공시 당일 값을 이미 쓰고 있다"
    assert f_next["net_income"] == q["net_income"].iloc[-4:].sum()

    # 2) TTM 은 직전 4분기 합, 성장성은 그 앞 4분기와의 비교.
    prev = q["net_income"].iloc[-8:-4].sum()
    assert np.isclose(f_next["income_yoy"], f_next["net_income"] / prev - 1.0)
    assert np.isclose(f_next["fcf"],
                      q["operating_cash_flow"].iloc[-4:].sum() - q["capex"].iloc[-4:].sum())

    # 3) 분기가 빠지면 4칸 뒤가 1년 전이 아니다 → 그 자리는 안 낸다.
    holed = _fake_parts(drop_quarter=10)
    assert pit_fundamentals(holed, filed_last + pd.Timedelta(days=1)) is None, \
        "분기 결측 구간에서 TTM 을 냈다"

    # 4) 8분기가 안 되면 안 낸다 (성장성이 조용히 중립으로 안 섞이도록).
    young = {**parts, "q": q.iloc[:7]}
    assert pit_fundamentals(young, filed_last + pd.Timedelta(days=1)) is None

    # 5) 가중치 — 남은 여섯의 합은 0.80 이고, 모든 항목이 같은 점수면 그 값이 나온다.
    assert np.isclose(sum(WEIGHTS.values()), WEIGHT_SUM)
    assert np.isclose(sum(50.0 * w for w in WEIGHTS.values()) / WEIGHT_SUM, 50.0)

    # 6) 점수는 0~100 이고, 미래 가격을 안 본다 — df 끝이 asof 를 넘으면 거절.
    asof = pd.Timestamp("2025-06-02")
    df = _fake_prices(asof)
    s = score_from(parts, asof, df)
    assert s is not None and 0.0 <= s <= 100.0, s
    assert score_from(parts, asof - pd.Timedelta(days=30), df) is None, \
        "asof 뒤의 봉이 든 창을 그대로 받았다"

    # 7) 재료가 바뀌면 점수가 움직인다 — 순이익만 반토막.
    poor = _fake_parts()
    poor["q"]["net_income"] = poor["q"]["net_income"] * 0.2
    assert score_from(poor, asof, df) < s, "재료를 나쁘게 해도 점수가 안 내려갔다"

    # 8) CapEx 태그가 없는 발행사도 점수는 난다 — FCF 자리만 중립 50.
    nocapex = _fake_parts()
    nocapex["q"]["capex"] = np.nan
    f_nc = pit_fundamentals(nocapex, asof)
    assert f_nc is not None and f_nc["fcf"] is None, "CapEx 결측이 TTM 을 통째로 죽였다"
    assert score_from(nocapex, asof, df) is not None

    # 9) 재료가 비면 None (백필이 그 종목의 그 날을 빼도록).
    assert score_from({"q": q.iloc[:0], "equity": parts["equity"],
                       "shares": parts["shares"]}, asof, df) is None

    print("selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
