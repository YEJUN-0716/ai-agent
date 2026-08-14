"""
생존자 편향 없는 소형주 유니버스
=================================================================
`modules/universe.py` 는 **현재** 상장된 290종목을 손으로 적은 목록이라, 과거를
재면 망한 회사가 표본에서 빠진다. F-Score 처럼 "부실한 회사를 걸러내는" 가설을
소형주에서 재려면 그 편향의 크기를 알 수 없어 통과해도 실패해도 못 믿는다.

이 모듈은 **그날 상장돼 있었고 소형주였던 종목**의 월별 패널을 만든다. 죽은
종목은 죽기 전까지 들어 있고 죽은 뒤에 사라진다.

종목 목록의 출처는 **SEC company_tickers.json 의 과거 스냅샷**(웨이백 머신)이다.
그 파일은 매 시점의 상장 종목을 티커와 CIK 로 **같이** 주므로, 스냅샷을 모으면
"그때 상장돼 있던 종목"이 CIK 와 함께 남는다 — 나중에 죽은 종목까지 포함해서.
회사명 매칭이 필요 없다는 게 이 출처의 핵심이다.

설계서: docs/superpowers/specs/2026-08-14-smallcap-universe-design.md

네트워크를 모른다 — 웨이백·SEC·Alpaca 호출은
scripts/build_smallcap_universe.py 가 한다.
"""
import re

import numpy as np
import pandas as pd

# 러셀2000 정의. 문헌값이라 고를 게 없고, 고를 게 없으면 튜닝할 여지도 없다.
TOP_EXCLUDE = 1000
UNIVERSE_SIZE = 2000

MIN_PRICE          = 1.0    # 동전주 노이즈는 가설과 무관하다
MIN_TRADING_DAYS   = 60     # 신규상장 첫 달 제외
MAX_PRICE_STALE_D  = 10     # 기준일 직전 이만큼 안에 거래가 있어야 '상장 중'
MAX_SHARES_STALE_D = 400    # 주식수 공시가 이보다 오래되면 못 믿는다


# SEC 티커의 대시 접미사. 우선주(-PA)·워런트(-WT)·유닛(-UN)·권리(-RT)는
# 보통주가 아니라 이 유니버스에 들어올 물건이 아니다.
_NOT_COMMON = re.compile(r"^(P[A-Z]?|WT|W|UN|U|RT|R|CL)$")
# Alpaca 가 받는 심볼 모양. 이 밖의 글자가 하나라도 있으면 배치가 400 이다.
# 종류주 접미사는 **한 글자**다(BRK.B·BF.B). 두 글자를 허용하면 WY/WD 같은
# when-distributed 표기가 WY.WD 로 통과해 버린다.
_CLEAN_SYMBOL = re.compile(r"^[A-Z0-9]{1,6}(\.[A-Z])?$")


# ── 티커 표기 ──────────────────────────────────────────────────────────
def alpaca_symbol(ticker: str):
    """SEC 티커 → Alpaca 심볼. 보통주가 아니면 None.

    SEC 는 종류주를 `BRK-B` 로, Alpaca 는 `BRK.B` 로 쓴다. 대시가 든 심볼을
    그대로 넘기면 Alpaca 가 **배치 전체를 400 으로 거절한다** — 한 종목이
    나머지 99종목을 같이 죽이는 자리라 여기서 거른다.
    """
    ticker = ticker.replace("/", "-")   # SBE/U 처럼 슬래시로 쓰는 발행사도 있다
    if "-" in ticker:
        base, _, suffix = ticker.rpartition("-")
        if not base or _NOT_COMMON.match(suffix):
            return None
        ticker = f"{base}.{suffix}"
    # 남은 이상 표기(HLM_P·SOV^B 같은 우선주)는 여기서 다 걸린다. 하나씩
    # 쫓아다니며 막다가 셋을 연달아 놓쳤다 — 허용 문자를 정하는 쪽이 끝이 있다.
    return ticker if _CLEAN_SYMBOL.match(ticker) else None


# ── 상장 이력 ──────────────────────────────────────────────────────────
def build_listing_spans(rows) -> tuple:
    """(스냅샷일, 티커, CIK) 행들 → 상장 구간표 + 보고서.

    한 (티커, CIK) 쌍이 처음/마지막으로 목격된 스냅샷이 그 회사가 그 티커를
    쓰던 구간이다. `listing_id` = `"CIK:티커"` 가 이 패널에서 종목의 키다.

    **티커는 재활용된다** — BBBY 로 일봉을 요청하면 Bed Bath & Beyond 가 2023년에
    죽은 뒤의 봉까지 딸려 온다. 스냅샷은 그 티커가 언제 누구 것이었는지 날짜로
    알려주므로, 버리지 않고 구간으로 가를 수 있다(`listing_windows`).
    """
    df = pd.DataFrame(list(rows), columns=["snapshot", "ticker", "cik"])
    if df.empty:
        return (pd.DataFrame(columns=["ticker", "cik", "first_seen", "last_seen",
                                      "snapshots", "listing_id"]),
                {"listings": 0, "tickers": 0, "recycled_tickers": 0, "snapshots": 0})

    df["snapshot"] = pd.to_datetime(df["snapshot"])
    g = (df.groupby(["ticker", "cik"])["snapshot"]
           .agg(first_seen="min", last_seen="max", snapshots="count")
           .reset_index())
    g["listing_id"] = g["cik"].astype(str) + ":" + g["ticker"]

    report = {"listings": int(len(g)), "tickers": int(g["ticker"].nunique()),
              "recycled_tickers": int((g["ticker"].value_counts() > 1).sum()),
              "snapshots": int(df["snapshot"].nunique())}
    return g.sort_values(["ticker", "first_seen"]).reset_index(drop=True), report


def listing_windows(spans: pd.DataFrame) -> dict:
    """{listing_id: (시작, 끝)} — 그 티커의 봉 중 이 회사 것으로 볼 구간.

    티커 주인이 하나면 구간은 열려 있다(±무한). 봉이 첫 스냅샷보다 앞서 있는
    건 정상이다 — 스냅샷이 2017년부터라 그 전 이력은 스냅샷에 안 잡힌다.

    주인이 여럿이면 **앞 주인의 마지막 목격일과 뒤 주인의 첫 목격일 사이
    중간점**에서 자른다. 경계 근처 몇 주가 어느 쪽에 붙을지는 알 수 없지만,
    상폐와 재상장 사이에는 보통 몇 달이 있어 실무상 안전하다.
    """
    out = {}
    for _, grp in spans.groupby("ticker", sort=False):
        g = grp.sort_values("first_seen")
        ids  = g["listing_id"].tolist()
        firsts, lasts = g["first_seen"].tolist(), g["last_seen"].tolist()
        for i, lid in enumerate(ids):
            lo = pd.Timestamp.min if i == 0 else \
                lasts[i - 1] + (firsts[i] - lasts[i - 1]) / 2
            hi = pd.Timestamp.max if i == len(ids) - 1 else \
                lasts[i] + (firsts[i + 1] - lasts[i]) / 2
            out[lid] = (lo, hi)
    return out


def slice_closes(closes_by_ticker: dict, spans: pd.DataFrame) -> dict:
    """{티커: 종가 Series} → {listing_id: 종가 Series}. 재활용 구간을 가른다."""
    windows = listing_windows(spans)
    out = {}
    for row in spans.itertuples(index=False):
        series = closes_by_ticker.get(row.ticker)
        if series is None or len(series) == 0:
            continue
        lo, hi = windows[row.listing_id]
        cut = series.loc[(series.index >= lo) & (series.index <= hi)]
        if len(cut):
            out[row.listing_id] = cut
    return out


# ── 시점 시가총액 ──────────────────────────────────────────────────────
def _asof(series: pd.Series, when: pd.Timestamp, max_stale_days: int):
    """`when` 이전 마지막 값. 그 값이 너무 오래되면 None (모르는 것으로 친다)."""
    if series is None or len(series) == 0:
        return None
    idx = series.index[series.index <= when]
    if len(idx) == 0:
        return None
    last = idx[-1]
    if (when - last).days > max_stale_days:
        return None
    val = series.loc[last]
    return float(val.iloc[-1] if hasattr(val, "iloc") else val)


def market_cap_panel(closes: dict, shares: dict, dates) -> pd.DataFrame:
    """시점 시가총액 패널 → (date, asset_id, price, shares, mktcap).

    closes: {listing_id: **미조정** 종가 Series}. 조정 종가에 당시 주식수를
      곱하면 나중에 분할한 회사의 과거 시총이 틀린다.
    shares: {listing_id: 주식수 Series, index=**쓸 수 있게 되는 날**}.
      `index <= date` 로만 쓰므로 룩어헤드가 구조적으로 불가능하다.

    기준일 직전 MAX_PRICE_STALE_D 일 안에 거래가 없으면 그 종목은 그날
    상장돼 있지 않은 것으로 본다 — 이게 없으면 죽은 종목의 마지막 종가가
    패널 끝까지 살아남는다.
    """
    rows = []
    for when in pd.to_datetime(list(dates)):
        for aid, close in closes.items():
            if close is None or len(close) == 0:
                continue
            hist = close.loc[close.index <= when]
            if len(hist) < MIN_TRADING_DAYS:
                continue
            if (when - hist.index[-1]).days > MAX_PRICE_STALE_D:
                continue
            price = float(hist.iloc[-1])
            if not np.isfinite(price) or price < MIN_PRICE:
                continue
            sh = _asof(shares.get(aid), when, MAX_SHARES_STALE_D)
            if not sh or sh <= 0:
                continue
            rows.append({"date": when, "asset_id": aid, "price": price,
                         "shares": sh, "mktcap": price * sh})
    return pd.DataFrame(rows, columns=["date", "asset_id", "price",
                                       "shares", "mktcap"])


def select_universe(cap_panel: pd.DataFrame, top_exclude: int = TOP_EXCLUDE,
                    size: int = UNIVERSE_SIZE) -> pd.DataFrame:
    """시총 상위 top_exclude 를 뺀 다음 size 종목 = 러셀2000 규칙."""
    if cap_panel.empty:
        return cap_panel.assign(rank=pd.Series(dtype=int))
    out = cap_panel.sort_values(["date", "mktcap"], ascending=[True, False]).copy()
    out["rank"] = out.groupby("date").cumcount() + 1
    keep = (out["rank"] > top_exclude) & (out["rank"] <= top_exclude + size)
    return out.loc[keep].reset_index(drop=True)


# ── 이 유니버스가 진짜인지 확인하는 검사 ────────────────────────────────
def delistings_by_year(universe: pd.DataFrame, last_bar: dict,
                       panel_end) -> dict:
    """연도별 '그 해 구성종목 중 12개월 안에 상장폐지된 종목 수'.

    상장폐지의 정의는 **그 뒤로 봉이 없다** 이다. 유니버스에서 빠지는 것과는
    다르다 — 시총이 커져서 빠지는 종목도 있다.

    12개월이 아직 안 지난 해는 셀 수 없으므로 뺀다.
    """
    panel_end = pd.Timestamp(panel_end)
    res = {}
    for year, grp in universe.groupby(universe["date"].dt.year):
        horizon = pd.Timestamp(year=int(year), month=12, day=31) + pd.DateOffset(years=1)
        if horizon > panel_end:
            continue
        res[int(year)] = int(sum(
            1 for aid in grp["asset_id"].unique()
            if last_bar.get(aid) is not None and pd.Timestamp(last_bar[aid]) <= horizon))
    return res


def survivorship_selftest(universe: pd.DataFrame, last_bar: dict,
                          panel_end) -> tuple:
    """통과 못 하면 유니버스를 내놓지 않는다 → (통과여부, 진단).

    핵심은 첫 항목이다. 어느 해든 상장폐지 종목이 0 이면 죽은 종목이
    어디선가 다시 새어 나간 것이고, 그러면 이 패널로 잰 건 못 믿는다.
    """
    diag = {"delistings_by_year": delistings_by_year(universe, last_bar, panel_end)}
    checks = {
        "delistings_every_year": bool(diag["delistings_by_year"])
                                 and all(n > 0 for n in diag["delistings_by_year"].values()),
        "no_duplicate_rows":     not universe.duplicated(["date", "asset_id"]).any(),
        "rank_matches_mktcap":   _rank_is_sorted(universe),
    }
    diag["checks"] = checks
    return all(checks.values()), diag


def _rank_is_sorted(universe: pd.DataFrame) -> bool:
    """같은 날 안에서 rank 오름차순 = 시총 내림차순인지."""
    if universe.empty:
        return False
    for _, grp in universe.groupby("date"):
        g = grp.sort_values("rank")
        if not g["mktcap"].is_monotonic_decreasing:
            return False
    return True
