"""edgar_fundamentals 테스트. 네트워크를 타지 않고 requests를 목으로 대체한다."""
import json
import pandas as pd
import pytest

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


def test_facts_for_chain_uses_first_present_tag():
    """대체 목록에서 먼저 존재하는 태그를 쓴다."""
    ug = {"Revenues": {"units": {"USD": [_fact("2020-01-01", "2020-03-31", 5, "2020-05-01")]}}}
    got = ef._facts_for_chain(ug, ef.TAG_CHAINS["revenue"], "USD")
    assert len(got) == 1 and got[0]["val"] == 5


def test_facts_for_chain_empty_when_no_tag():
    assert ef._facts_for_chain({}, ["Nope"], "USD") == []


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
    assert list(df.columns) == ["revenue", "operating_income", "net_income"]
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
