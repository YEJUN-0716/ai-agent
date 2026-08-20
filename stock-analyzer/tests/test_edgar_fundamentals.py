"""edgar_fundamentals 테스트. 네트워크를 타지 않고 requests를 목으로 대체한다."""
import pandas as pd

from modules import edgar_fundamentals as ef


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_valid_us_gaap_rejects_ffd_only():
    """us-gaap 네임스페이스가 없으면 None (XOM 함정)."""
    assert ef._valid_us_gaap({"facts": {"ffd": {}}}) is None
    assert ef._valid_us_gaap({"facts": {"us-gaap": {"X": 1}}}) == {"X": 1}
    assert ef._valid_us_gaap(None) is None


def test_load_raw_caches_valid_only(tmp_path, monkeypatch):
    """유효 응답은 디스크에 캐시하고, 두 번째 호출은 네트워크를 안 탄다."""
    calls = []
    payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": []}}}}}

    def fake_get(url, **kw):
        calls.append(url)
        if "company_tickers" in url:
            return _Resp(200, {"0": {"ticker": "AAPL", "cik_str": 320193}})
        return _Resp(200, payload)

    monkeypatch.setattr(ef.requests, "get", fake_get)
    monkeypatch.setattr(ef.time, "sleep", lambda *_: None)

    ug = ef.load_raw("AAPL", cache_dir=str(tmp_path))
    assert ug == payload["facts"]["us-gaap"]
    n_after_first = len(calls)

    ef.load_raw("AAPL", cache_dir=str(tmp_path))
    # 티커맵 재사용 + 디스크 캐시 → companyfacts 재요청 없음
    assert not any("companyfacts" in u for u in calls[n_after_first:])


def _fact(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def test_facts_for_chain_single_tag():
    """태그 하나만 있으면 그 팩트를 반환한다."""
    ug = {"Revenues": {"units": {"USD": [_fact("2020-01-01", "2020-03-31", 5, "2020-05-01")]}}}
    got = ef._facts_for_chain(ug, ef.TAG_CHAINS["revenue"], "USD")
    assert len(got) == 1 and got[0]["val"] == 5


def test_facts_for_chain_merges_across_tags():
    """
    발행사가 연도별로 태그를 바꾸면(구 SalesRevenueNet → 신 RevenueFromContract...)
    두 태그를 병합해야 과거 이력이 잘리지 않는다.
    """
    ug = {
        "SalesRevenueNet": {"units": {"USD": [
            _fact("2015-01-01", "2015-03-31", 10, "2015-05-01"),  # 구 태그, 과거
        ]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _fact("2020-01-01", "2020-03-31", 20, "2020-05-01"),  # 신 태그, 최근
        ]}},
    }
    got = ef._facts_for_chain(ug, ef.TAG_CHAINS["revenue"], "USD")
    vals = sorted(f["val"] for f in got)
    assert vals == [10, 20], "구·신 태그가 모두 병합돼야 한다"


def test_facts_for_chain_empty_when_no_tag():
    assert ef._facts_for_chain({}, ["Nope"], "USD") == []


def test_fetch_companyfacts_survives_network_error(monkeypatch):
    """
    네트워크 예외(timeout/DNS/연결 리셋)에 전체가 죽지 않고 재시도 후 None.
    276개 순차 요청 중 하나만 터져도 IC 전체가 중단되던 버그 방지.
    """
    def boom(url, **kw):
        raise ef.requests.exceptions.ConnectionError("reset")

    monkeypatch.setattr(ef.requests, "get", boom)
    monkeypatch.setattr(ef.time, "sleep", lambda *_: None)
    assert ef._fetch_companyfacts(320193) is None


def test_quarter_and_annual_classification():
    """기간 길이로 분기(80~100)와 연간(350~380)을 가른다. 9개월(YTD)은 둘 다 아님."""
    raw = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),  # 90일 분기
        _fact("2020-01-01", "2020-09-30", 3, "2020-11-01"),  # 273일 (YTD) — 제외
        _fact("2020-01-01", "2020-12-31", 4, "2021-02-01"),  # 365일 연간
    ]
    q = ef._quarter_facts(raw)
    a = ef._annual_facts(raw)
    assert [f["val"] for f in q] == [1]
    assert [f["val"] for f in a] == [4]


def test_dedup_keeps_earliest_filed():
    """같은 (start,end)가 여러 filed로 오면 가장 이른 것만 남긴다."""
    facts = [
        _fact("2020-01-01", "2020-03-31", 10, "2020-05-01"),
        _fact("2020-01-01", "2020-03-31", 11, "2021-05-01"),  # 이듬해 비교 재게시
    ]
    out = ef._dedup_earliest(facts, key=lambda f: (f["start"], f["end"]))
    assert len(out) == 1 and out[0]["filed"] == "2020-05-01"


def test_derive_q4_when_absent():
    """Q1~Q3 분기 + 연간이 있고 Q4가 없으면 Q4 = FY - (Q1+Q2+Q3)."""
    quarters = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 2, "2020-08-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
    ]
    annuals = [_fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K")]
    out = ef._derive_q4(quarters, annuals)
    q4 = [f for f in out if f["end"] == "2020-12-31"]
    assert len(q4) == 1
    assert q4[0]["val"] == 10 - (1 + 2 + 3)   # 4
    assert q4[0]["filed"] == "2021-02-01"


def test_no_derive_when_q4_already_present():
    """Q4가 이미 3개월 팩트로 있으면 유도하지 않는다."""
    quarters = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 2, "2020-08-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
        _fact("2020-10-01", "2020-12-31", 4, "2021-02-01"),
    ]
    annuals = [_fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K")]
    out = ef._derive_q4(quarters, annuals)
    q4 = [f for f in out if f["end"] == "2020-12-31"]
    assert len(q4) == 1 and q4[0]["val"] == 4   # 원본 유지, 유도 안 함


def test_derive_when_reported_q4_arrives_later(monkeypatch):
    """Q4 3개월 팩트가 연간보다 **늦게** 공시되면 유도해서 그 자리를 메운다 (CAT 2024Q4).

    10-K는 2025-02-14에 나왔는데 그 분기 팩트는 14개월 뒤 비교표시 8-K에 처음 실린다.
    그대로 두면 2025년 내내 TTM에 구멍이 난다.
    """
    ug = {"Revenues": {"units": {"USD": [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 2, "2020-08-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
        _fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K"),
        _fact("2020-10-01", "2020-12-31", 4, "2022-04-01", form="8-K"),  # 14개월 뒤
    ]}}}
    d = ef._assemble_tag(ug, ["Revenues"])
    assert d["2020-12-31"] == ("2021-02-01", 4.0)   # 유도값, 10-K 공시일


def test_no_derive_when_quarter_missing():
    """Q1~Q3 중 하나라도 없으면 Q4를 만들지 않는다 — 추측 금지."""
    quarters = [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
    ]
    annuals = [_fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K")]
    out = ef._derive_q4(quarters, annuals)
    assert not [f for f in out if f["end"] == "2020-12-31"]


def _income_ug():
    """Q1~Q3 분기 + 연간을 세 손익 태그 모두에 담은 us-gaap 목."""
    def series(base):
        return {"units": {"USD": [
            _fact("2020-01-01", "2020-03-31", base + 1, "2020-05-01"),
            _fact("2020-04-01", "2020-06-30", base + 2, "2020-08-01"),
            _fact("2020-07-01", "2020-09-30", base + 3, "2020-11-01"),
            _fact("2020-01-01", "2020-12-31", base + 10, "2021-02-01", form="10-K"),
        ]}}
    return {
        "RevenueFromContractWithCustomerExcludingAssessedTax": series(100),
        "OperatingIncomeLoss": series(10),
        "NetIncomeLoss": series(0),
    }


def test_assemble_income_contract():
    """조립 결과가 '분기당 1행' 계약을 지킨다."""
    df = ef.assemble_income(_income_ug())
    # 4개 분기(Q1~Q3 + 유도 Q4)
    assert len(df) == 4
    assert list(df.columns) == ["revenue", "operating_income", "net_income",
                                "operating_cash_flow", "capex"]
    # 인덱스(filed) 단조 비감소
    assert df.index.is_monotonic_increasing
    # net_income TTM = 1+2+3+4(유도) = 10
    assert df["net_income"].sum() == 0 + 1 + 2 + 3 + (10 - 6)


def test_ytd_cash_flow_becomes_four_quarters():
    """현금흐름표는 누적으로 실린다 — 같은 회계연도끼리 차분해 4분기가 나와야 한다.

    분기 필터만 쓰면 Q1(90일) 하나만 걸려서 연 1행이 된다(AAPL 20년에 18행).
    """
    ug = {"NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
        _fact("2020-01-01", "2020-03-31", 10, "2020-05-01"),   # Q1 누적 = 10
        _fact("2020-01-01", "2020-06-30", 30, "2020-08-01"),   # 반기 누적 → Q2 = 20
        _fact("2020-01-01", "2020-09-30", 60, "2020-11-01"),   # 3분기 누적 → Q3 = 30
        _fact("2020-01-01", "2020-12-31", 100, "2021-02-01", form="10-K"),  # FY → Q4 = 40
    ]}}}
    d = ef._ytd_quarters(ug, ["NetCashProvidedByUsedInOperatingActivities"])
    assert [d[k][1] for k in sorted(d)] == [10, 20, 30, 40]
    assert d["2020-12-31"][0] == "2021-02-01"   # Q4는 10-K 공시일에 알려진다


def test_ytd_skips_quarter_when_previous_missing():
    """앞 누적이 없으면 두 분기 합을 한 분기로 잡지 않는다 — 그 자리는 비운다."""
    ug = {"NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
        _fact("2020-01-01", "2020-03-31", 10, "2020-05-01"),
        _fact("2020-01-01", "2020-09-30", 60, "2020-11-01"),   # 반기 누적 결측
    ]}}}
    d = ef._ytd_quarters(ug, ["NetCashProvidedByUsedInOperatingActivities"])
    assert sorted(d) == ["2020-03-31"]


def test_derived_q4_keeps_earliest_filed():
    """이듬해 10-K가 비교표시로 같은 분기를 다시 실어도 filed는 최초 공시로 남는다.

    CAT 2024-12-31이 filed 2026-03-26으로 밀려 2025년 내내 TTM에 구멍이 났던 자리다.
    """
    ug = {"NetIncomeLoss": {"units": {"USD": [
        _fact("2020-01-01", "2020-03-31", 1, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 2, "2020-08-01"),
        _fact("2020-07-01", "2020-09-30", 3, "2020-11-01"),
        _fact("2020-01-01", "2020-12-31", 10, "2021-02-01", form="10-K"),
        # 이듬해 10-K의 비교표시 — start가 하루 어긋나 Q4가 한 번 더 유도된다
        _fact("2019-12-30", "2020-12-31", 10, "2022-02-01", form="10-K"),
    ]}}}
    d = ef._assemble_tag(ug, ["NetIncomeLoss"])
    assert d["2020-12-31"] == ("2021-02-01", 4.0)


def test_row_filed_is_latest_piece():
    """행의 filed는 그 분기 태그들의 가장 늦은 공시일 — min을 쓰면 look-ahead다."""
    ug = {
        "NetIncomeLoss": {"units": {"USD": [
            _fact("2020-01-01", "2020-03-31", 1, "2020-05-01")]}},
        "OperatingIncomeLoss": {"units": {"USD": [
            _fact("2020-01-01", "2020-03-31", 2, "2020-05-20")]}},  # 늦게 나온 조각
    }
    df = ef.assemble_income(ug)
    assert df.index[0] == pd.Timestamp("2020-05-20")


def test_assemble_shares_no_q4_derivation():
    """주식수는 분기 팩트만 쓰고 Q4를 유도하지 않는다 (합산 대상이 아님)."""
    ug = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        _fact("2020-01-01", "2020-03-31", 1000, "2020-05-01"),
        _fact("2020-04-01", "2020-06-30", 1010, "2020-08-01"),
    ]}}}
    s = ef.assemble_shares(ug)
    assert list(s.values) == [1000, 1010]
    assert s.index.is_monotonic_increasing


def test_public_api_uses_disk_cache_once(monkeypatch):
    """두 공개 함수가 load_raw를 통해 종목당 한 번만 원본을 읽는다."""
    ug = _income_ug()
    ug["WeightedAverageNumberOfDilutedSharesOutstanding"] = {"units": {"shares": [
        _fact("2020-01-01", "2020-03-31", 1000, "2020-05-01"),
    ]}}
    seen = []

    def fake_load_raw(tk, cache_dir=None):
        seen.append(tk)
        return ug if tk == "AAPL" else None

    monkeypatch.setattr(ef, "load_raw", fake_load_raw)

    fin = ef.fetch_quarterly_fundamentals_history(["AAPL", "DEAD"])
    assert not fin["AAPL"].empty
    assert fin["DEAD"].empty
    cov = ef.last_coverage()
    assert cov["requested"] == 2
    assert cov["resolved"] == 1
    assert cov["failed"] == ["DEAD"]
    assert 0.0 <= cov["metric_coverage"]["net_income"] <= 1.0


def test_low_coverage_metric_is_dropped(monkeypatch):
    """
    한 지표의 커버리지가 MIN_COVERAGE 미만이면 그 지표를 전 종목에서 제외한다.
    편향된 부분집합으로 IC를 계산하지 않고, 해당 팩터를 n=0(미측정)으로
    떨어뜨려 ic_weight_updater가 unavailable로 처리하게 한다.
    """
    good = _income_ug()  # revenue/operating_income/net_income 모두 있음

    def only_net_income(base):
        return {"units": {"USD": [
            _fact("2020-01-01", "2020-03-31", base + 1, "2020-05-01"),
            _fact("2020-04-01", "2020-06-30", base + 2, "2020-08-01"),
            _fact("2020-07-01", "2020-09-30", base + 3, "2020-11-01"),
            _fact("2020-01-01", "2020-12-31", base + 10, "2021-02-01", form="10-K"),
        ]}}
    # revenue/operating_income가 없는 종목 (net_income만)
    partial = {"NetIncomeLoss": only_net_income(0)}

    ugs = {"AAPL": good, "MSFT": partial, "NVDA": partial, "GOOGL": partial}
    monkeypatch.setattr(ef, "load_raw", lambda tk, cache_dir=None: ugs.get(tk))

    fin = ef.fetch_quarterly_fundamentals_history(["AAPL", "MSFT", "NVDA", "GOOGL"])
    cov = ef.last_coverage()
    # revenue 커버리지 1/4 = 25% < 70% → 전 종목에서 제외
    assert cov["metric_coverage"]["revenue"] < ef.MIN_COVERAGE
    for tk in ugs:
        df = fin[tk]
        if not df.empty:
            assert df["revenue"].isna().all(), f"{tk}: 커버리지 미달 revenue가 제외돼야 한다"
    # net_income은 4/4 = 100% → 유지
    assert not fin["AAPL"]["net_income"].isna().all()


def test_fetch_shares_history_returns_series(monkeypatch):
    ug = {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        _fact("2020-01-01", "2020-03-31", 1000, "2020-05-01"),
    ]}}}
    monkeypatch.setattr(ef, "load_raw", lambda tk, cache_dir=None: ug)
    out = ef.fetch_shares_history(["AAPL"])
    assert isinstance(out["AAPL"], pd.Series)
    assert out["AAPL"].iloc[-1] == 1000


def _quarters(years, val=1.0):
    """연도 목록 → 그 해 4분기 팩트. filed는 분기말 + 한 달."""
    ends = [("03-31", "01-01"), ("06-30", "04-01"), ("09-30", "07-01"), ("12-31", "10-01")]
    return [_fact(f"{y}-{s}", f"{y}-{e}", val, f"{y + (e == '12-31')}-{'01' if e == '12-31' else f'{int(e[:2]) + 1:02d}'}-15")
            for y in years for e, s in ends]


def _ug(net_income=None, profit_loss=None):
    ug = {}
    if net_income:
        ug["NetIncomeLoss"] = {"units": {"USD": net_income}}
    if profit_loss:
        ug["ProfitLoss"] = {"units": {"USD": profit_loss}}
    return ug


def test_pick_alt_tag_when_production_tag_is_dead():
    """ITW처럼 NetIncomeLoss가 옛날에 끊긴 종목은 ProfitLoss로 간다."""
    ug = _ug(net_income=_quarters([2014, 2015], val=1.0),
             profit_loss=_quarters([2022, 2023, 2024], val=2.0))
    got = ef._assemble_metric(ug, "net_income")
    assert {v for _, v in got.values()} == {2.0}, "한 정의로만 조립돼야 한다 (섞으면 가짜 YoY)"
    assert max(got) >= "2024-12-31"


def test_dead_alt_tag_loses_even_with_more_recent_quarters():
    """MRK CapEx 함정 — 2019년 이후 분기 수는 더 많아도 이미 끊긴 태그는 안 뽑는다."""
    ug = _ug(net_income=_quarters([2024, 2025, 2026], val=1.0),      # 12분기, 살아 있음
             profit_loss=_quarters([2019, 2020, 2021, 2022], val=2.0))  # 16분기, 2022에 끊김
    got = ef._assemble_metric(ug, "net_income")
    assert {v for _, v in got.values()} == {1.0}
    assert max(got) >= "2026-01-01"


def test_tie_on_recent_quarters_keeps_longer_history():
    """BBY 함정 — 최근 커버리지가 같으면 이력이 긴 쪽을 남긴다."""
    ug = _ug(net_income=_quarters(range(2009, 2027), val=1.0),
             profit_loss=_quarters(range(2019, 2027), val=2.0))
    got = ef._assemble_metric(ug, "net_income")
    assert {v for _, v in got.values()} == {1.0}
    assert min(got) < "2010-01-01"


def _ifact(end, val, filed):
    """시점(instant) 팩트 — start가 없다."""
    return {"end": end, "val": val, "filed": filed, "form": "10-Q"}


def _bs_ug(primary, secondary):
    return {"StockholdersEquity": {"units": {"USD": primary}},
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest":
                {"units": {"USD": secondary}}}


def test_instant_tag_priority_beats_quarter_count():
    """대차대조표는 분기 수로 안 고른다 — 지배주주 자본이 최근에 있으면 그걸 쓴다."""
    ug = _bs_ug([_ifact(f"202{y}-03-31", 10, f"202{y}-05-01") for y in (4, 5, 6)],
                [_ifact(f"20{y}-03-31", 99, f"20{y}-05-01") for y in range(10, 26)])
    got = ef._assemble_instant(ug, ef.EQUITY_TAGS)
    assert {v for _, v in got.values()} == {10}, "분기 수가 많다고 소수주주지분 포함으로 넘어가면 안 된다"


def test_instant_tag_falls_back_when_primary_is_stale():
    """지배주주 자본이 옛날에 끊긴 종목은 소수주주지분 포함으로 떨어진다."""
    ug = _bs_ug([_ifact("2012-03-31", 10, "2012-05-01")],
                [_ifact(f"202{y}-03-31", 99, f"202{y}-05-01") for y in (4, 5, 6)])
    got = ef._assemble_instant(ug, ef.EQUITY_TAGS)
    assert {v for _, v in got.values()} == {99}


def test_assemble_balance_row_filed_is_latest_piece():
    """한 시점의 항목들이 다른 날 공시되면 행의 filed는 마지막 조각이다(min은 look-ahead)."""
    ug = {"Assets": {"units": {"USD": [_ifact("2024-03-31", 100, "2024-05-01")]}},
          "AssetsCurrent": {"units": {"USD": [_ifact("2024-03-31", 40, "2024-06-15")]}}}
    bal = ef.assemble_balance(ug)
    assert list(bal.index) == [pd.Timestamp("2024-06-15")]
    assert bal["assets"].iloc[0] == 100 and bal["current_assets"].iloc[0] == 40
    assert bal["equity"].isna().all()


def _instant(end, filed, val):
    return {"end": end, "filed": filed, "val": val}


def test_assemble_equity_keeps_every_end_from_one_filing():
    """한 공시가 대차대조표 시점을 여럿 실어도 행이 사라지지 않는다.

    filed 를 dict 키로 쓰던 옛 코드는 그중 하나(값이 큰 쪽)만 남겼다 — 실측으로
    자본총계 관측의 4.4%가 이렇게 사라졌고, AMD 는 2011년 자본총계 자리에
    2007년 값(3.2배)이 앉았다. 소비 측은 filed <= as_of 의 마지막을 집으므로
    같은 filed 안에서는 가장 늦은 시점이 마지막이어야 한다.
    """
    ug = {"StockholdersEquity": {"units": {"USD": [
        _instant("2007-12-29", "2011-02-18", 3230.0),   # 늦게 처음 공시된 옛 시점
        _instant("2010-12-25", "2011-02-18", 1013.0),   # 같은 공시, 최신 시점
    ]}}}
    s = ef.assemble_equity(ug)
    assert len(s) == 2
    assert s.index.is_monotonic_increasing
    assert s.loc[:pd.Timestamp("2011-03-01")].iloc[-1] == 1013.0


def test_assemble_equity_drops_late_filed_older_end():
    """이미 아는 시점보다 옛 시점이 뒤늦게 처음 공시되면 뺀다.

    MTCH 의 2020-12-31 주식수가 2022-02-24 에 처음 나온다. 남겨 두면 소비 측이
    집는 마지막 값이 1년 전 시점으로 되돌아간다.
    """
    ug = {"StockholdersEquity": {"units": {"USD": [
        _instant("2021-09-30", "2021-11-05", 316.0),
        _instant("2020-12-31", "2022-02-24", 307.0),
    ]}}}
    s = ef.assemble_equity(ug)
    assert list(s) == [316.0]
    assert s.loc[:pd.Timestamp("2022-06-30")].iloc[-1] == 316.0
