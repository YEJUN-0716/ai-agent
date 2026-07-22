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
