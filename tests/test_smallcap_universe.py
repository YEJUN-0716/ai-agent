"""소형주 유니버스 — 네트워크 없는 로직 검증."""
import pandas as pd
import pytest

from modules import smallcap_universe as su


def _closes(start, days, price=10.0, freq="B"):
    idx = pd.bdate_range(start, periods=days) if freq == "B" else \
        pd.date_range(start, periods=days, freq=freq)
    return pd.Series([price] * len(idx), index=idx)


# ── 상장 이력 ──────────────────────────────────────────────────────────
def _snaps(rows):
    """(스냅샷일, 티커, CIK) 행 목록."""
    return [(pd.Timestamp(d), t, c) for d, t, c in rows]


def test_spans_span_first_to_last_sighting():
    spans, rep = su.build_listing_spans(_snaps([
        ("2018-01-01", "AAPL", 320193),
        ("2020-01-01", "AAPL", 320193),
        ("2026-01-01", "AAPL", 320193),
    ]))
    row = spans.iloc[0]
    assert row["listing_id"] == "320193:AAPL"
    assert row["first_seen"] == pd.Timestamp("2018-01-01")
    assert row["last_seen"] == pd.Timestamp("2026-01-01")
    assert rep["recycled_tickers"] == 0


def test_dead_ticker_survives_because_a_snapshot_saw_it():
    """SIVB 는 오늘 어느 목록에도 없다. 2022년 스냅샷에는 있었다."""
    spans, _ = su.build_listing_spans(_snaps([("2022-03-01", "SIVB", 719739)]))
    assert spans["listing_id"].tolist() == ["719739:SIVB"]


def test_recycled_ticker_becomes_two_listings():
    spans, rep = su.build_listing_spans(_snaps([
        ("2018-01-01", "BBBY", 886158),   # Bed Bath & Beyond
        ("2022-01-01", "BBBY", 886158),
        ("2025-01-01", "BBBY", 999999),   # 물려받은 회사
    ]))
    assert rep["recycled_tickers"] == 1
    assert set(spans["listing_id"]) == {"886158:BBBY", "999999:BBBY"}


def test_recycled_bars_are_split_at_the_midpoint():
    """죽은 회사의 봉에 새 주인의 봉이 이어 붙으면 안 된다."""
    spans, _ = su.build_listing_spans(_snaps([
        ("2020-01-01", "BBBY", 886158),
        ("2023-01-01", "BBBY", 886158),
        ("2025-01-01", "BBBY", 999999),
    ]))
    closes = {"BBBY": _closes("2019-01-01", 1800)}   # 2019~2025 쭉 이어진 봉
    sliced = su.slice_closes(closes, spans)

    old, new = sliced["886158:BBBY"], sliced["999999:BBBY"]
    assert old.index[0] == closes["BBBY"].index[0]   # 첫 주인은 스냅샷 이전도 갖는다
    assert old.index[-1] < new.index[0]
    assert old.index[-1] < pd.Timestamp("2024-01-02")


def test_single_owner_keeps_history_before_the_first_snapshot():
    spans, _ = su.build_listing_spans(_snaps([("2020-01-01", "AAA", 111)]))
    closes = {"AAA": _closes("2016-01-01", 1500)}
    sliced = su.slice_closes(closes, spans)
    assert len(sliced["111:AAA"]) == len(closes["AAA"])


# ── 시점 시가총액 ──────────────────────────────────────────────────────
def test_dead_stock_leaves_the_panel():
    """상폐 종목의 마지막 종가가 패널 끝까지 살아남으면 안 된다."""
    closes = {"dead": _closes("2020-01-01", 120)}          # 2020-06 즈음 끝
    shares = {"dead": pd.Series([1e6], index=[pd.Timestamp("2020-01-01")])}
    dates = [pd.Timestamp("2020-05-29"), pd.Timestamp("2021-05-31")]

    panel = su.market_cap_panel(closes, shares, dates)
    assert panel["date"].tolist() == [pd.Timestamp("2020-05-29")]


def test_shares_are_not_used_before_they_were_filed():
    closes = {"a": _closes("2020-01-01", 200)}
    shares = {"a": pd.Series([1e6], index=[pd.Timestamp("2020-06-01")])}

    before = su.market_cap_panel(closes, shares, [pd.Timestamp("2020-05-29")])
    after  = su.market_cap_panel(closes, shares, [pd.Timestamp("2020-06-30")])
    assert before.empty
    assert after["mktcap"].iloc[0] == pytest.approx(10.0 * 1e6)


def test_stale_shares_are_dropped():
    closes = {"a": _closes("2020-01-01", 700)}
    shares = {"a": pd.Series([1e6], index=[pd.Timestamp("2020-01-02")])}
    panel = su.market_cap_panel(closes, shares, [pd.Timestamp("2022-06-30")])
    assert panel.empty


def test_penny_stocks_and_short_history_are_dropped():
    shares = {"p": pd.Series([1e6], index=[pd.Timestamp("2020-01-01")]),
              "s": pd.Series([1e6], index=[pd.Timestamp("2020-01-01")])}
    closes = {"p": _closes("2020-01-01", 200, price=0.5),   # 동전주
              "s": _closes("2020-05-01", 20)}               # 이력 20일
    panel = su.market_cap_panel(closes, shares, [pd.Timestamp("2020-06-30")])
    assert panel.empty


# ── 선정 · 자기검사 ────────────────────────────────────────────────────
def _cap_panel(n, when="2020-06-30"):
    return pd.DataFrame({"date": [pd.Timestamp(when)] * n,
                         "asset_id": [f"a{i}" for i in range(n)],
                         "price": [10.0] * n, "shares": [1.0] * n,
                         "mktcap": [float(n - i) for i in range(n)]})


def test_select_universe_is_russell_window():
    uni = su.select_universe(_cap_panel(60), top_exclude=10, size=20)
    assert uni["rank"].tolist() == list(range(11, 31))
    assert uni["mktcap"].is_monotonic_decreasing


def test_selftest_fails_when_nobody_ever_delists():
    """죽은 종목이 하나도 없으면 생존자 편향이 다시 샌 것이다."""
    uni = su.select_universe(_cap_panel(30), top_exclude=10, size=10)
    last_bar = {aid: pd.Timestamp("2030-01-01") for aid in uni["asset_id"]}
    ok, diag = su.survivorship_selftest(uni, last_bar, pd.Timestamp("2026-08-01"))
    assert not ok
    assert diag["checks"]["delistings_every_year"] is False


def test_selftest_passes_when_members_die():
    uni = su.select_universe(_cap_panel(30), top_exclude=10, size=10)
    last_bar = {aid: pd.Timestamp("2030-01-01") for aid in uni["asset_id"]}
    last_bar[uni["asset_id"].iloc[0]] = pd.Timestamp("2020-11-30")
    ok, diag = su.survivorship_selftest(uni, last_bar, pd.Timestamp("2026-08-01"))
    assert ok
    assert diag["delistings_by_year"] == {2020: 1}


def test_delistings_skip_years_without_a_full_year_ahead():
    uni = su.select_universe(_cap_panel(30, when="2026-06-30"),
                             top_exclude=10, size=10)
    assert su.delistings_by_year(uni, {}, pd.Timestamp("2026-08-01")) == {}


# ── 티커 표기 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("sec,alpaca", [
    ("AAPL",     "AAPL"),
    ("BRK-B",    "BRK.B"),      # 종류주는 표기만 다르다
    ("AACT-UN",  None),         # 유닛
    ("AAIC-PB",  None),         # 우선주
    ("AACT-WT",  None),         # 워런트
    ("ABC-RT",   None),         # 권리
    ("HLM_P",    None),         # 밑줄 표기 우선주 — Alpaca 가 400 을 낸다
    ("SBE/U",    None),         # 슬래시 표기 유닛
    ("XYZ/A",    "XYZ.A"),      # 슬래시 표기 종류주
    ("SOV^B",    None),         # 캐럿 표기 우선주
    ("WEIRD$1",  None),         # 모르는 표기는 통과시키지 않는다
    ("WSG US",   None),         # 공백
    ("WY/WD",    None),         # when-distributed — 종류주가 아니다
])
def test_alpaca_symbol(sec, alpaca):
    assert su.alpaca_symbol(sec) == alpaca
