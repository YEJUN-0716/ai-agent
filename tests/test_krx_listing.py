"""
krx_universe — KOSPI/KOSDAQ 동적 유니버스 테스트.

한국 유니버스가 15종목 하드코딩이었다. 상장폐지되거나 순위가 바뀌어도
목록은 그대로였다. FinanceDataReader 로 실제 상장목록을 받아 시가총액
상위 N종목을 자른다.

FDR 조회는 전부 대체한다 — 네트워크를 타지 않는다. 캐시도 tmp_path 로
격리해 저장소 루트에 파일을 남기지 않는다.
"""
import json
import sys

import pandas as pd
import pytest

import signal_worker as worker
from modules import krx_universe as krx


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """캐시 파일을 임시 디렉터리로 — 테스트가 저장소를 더럽히지 않게."""
    monkeypatch.setattr(krx, "_CACHE_FILE", str(tmp_path / "krx_listing_cache.json"))


def _fake_fdr(df):
    """FinanceDataReader 모듈을 통째로 대체하는 가짜."""
    return type("FakeFDR", (), {"StockListing": staticmethod(lambda market: df)})


@pytest.fixture
def patch_fdr(monkeypatch):
    def _install(listings, fail=False):
        calls = []

        def fake_fetch(market):
            calls.append(market)
            if fail:
                raise RuntimeError("FDR 조회 실패")
            return [str(c).zfill(6) for c in listings[market]]

        monkeypatch.setattr(krx, "_fetch_listing", fake_fetch)
        return calls
    return _install


@pytest.fixture
def patch_fdr_module(monkeypatch):
    def _install(df):
        monkeypatch.setitem(sys.modules, "FinanceDataReader", _fake_fdr(df))
    return _install


# ── 1. 이름 해석 ────────────────────────────────────────────────────

@pytest.mark.parametrize("name,market,count", [
    ("KOSPI 100", "KOSPI", 100),
    ("KOSDAQ 50", "KOSDAQ", 50),
    ("kospi 30", "KOSPI", 30),
    ("  KOSPI 5  ", "KOSPI", 5),
])
def test_universe_pattern_accepts_market_and_count(name, market, count):
    matched = krx.UNIVERSE_PATTERN.match(name)
    assert matched
    assert matched.group(1).upper() == market
    assert int(matched.group(2)) == count


@pytest.mark.parametrize("name", [
    "S&P 500 대형 30", "한국 대형 15", "AAPL,MSFT", "KOSPI", "KOSPI abc", "", "NIKKEI 100",
])
def test_resolve_returns_none_for_names_it_does_not_own(name):
    """None 이어야 호출부가 다음 해석기로 넘어간다. 빈 리스트면 조용한 빈 스캔이 된다."""
    assert krx.resolve(name) is None


def test_resolve_returns_none_for_non_strings():
    assert krx.resolve(None) is None
    assert krx.resolve(["KOSPI 10"]) is None


def test_zero_count_is_rejected(patch_fdr):
    patch_fdr({"KOSPI": ["005930"]})
    assert krx.resolve("KOSPI 0") is None


# ── 2. 티커 생성 ────────────────────────────────────────────────────

def test_kospi_tickers_get_ks_suffix(patch_fdr):
    patch_fdr({"KOSPI": ["005930", "000660", "035420"]})
    assert krx.resolve("KOSPI 2") == ["005930.KS", "000660.KS"]


def test_kosdaq_tickers_get_kq_suffix(patch_fdr):
    patch_fdr({"KOSDAQ": ["247540", "086520"]})
    assert krx.resolve("KOSDAQ 2") == ["247540.KQ", "086520.KQ"]


def test_requesting_more_than_listed_returns_what_exists(patch_fdr):
    patch_fdr({"KOSPI": ["005930", "000660"]})
    assert len(krx.resolve("KOSPI 500")) == 2


def test_count_is_capped(patch_fdr):
    """상한이 없으면 실수로 KOSPI 99999 를 넣었을 때 스캔이 몇 시간 단위가 된다."""
    patch_fdr({"KOSPI": [f"{i:06d}" for i in range(krx.MAX_TICKERS + 500)]})
    assert len(krx.resolve(f"KOSPI {krx.MAX_TICKERS + 500}")) == krx.MAX_TICKERS


def test_unsupported_market_raises():
    with pytest.raises(ValueError):
        krx.listing_codes("NYSE")


# ── 3. 상장목록 파싱 (FDR 컬럼명이 버전마다 다르다) ─────────────────

def test_listing_is_sorted_by_market_cap(patch_fdr_module):
    # 끝자리 0 = 보통주. 우선주 필터에 걸리지 않는 코드를 쓴다.
    patch_fdr_module(pd.DataFrame({"Code": ["000010", "000020", "000030"],
                                   "Marcap": [10, 300, 200]}))
    assert krx._fetch_listing("KOSPI") == ["000020", "000030", "000010"]


def test_symbol_column_is_accepted_as_an_alias(patch_fdr_module):
    patch_fdr_module(pd.DataFrame({"Symbol": ["000020", "000010"], "MarketCap": [5, 9]}))
    assert krx._fetch_listing("KOSPI") == ["000010", "000020"]


def test_codes_are_zero_padded(patch_fdr_module):
    """FDR 이 종목코드를 정수로 주는 버전이 있다. 660 은 000660 이다."""
    patch_fdr_module(pd.DataFrame({"Code": [660, 5930], "Marcap": [1, 2]}))
    assert krx._fetch_listing("KOSPI") == ["005930", "000660"]


def test_missing_code_column_raises_loudly(patch_fdr_module):
    """컬럼명이 또 바뀌면 빈 유니버스로 조용히 넘어가지 말고 터져야 한다."""
    patch_fdr_module(pd.DataFrame({"Name": ["삼성전자"], "Marcap": [1]}))
    with pytest.raises(ValueError, match="종목코드 컬럼"):
        krx._fetch_listing("KOSPI")


def test_listing_without_a_marcap_column_keeps_source_order(patch_fdr_module):
    """시총 컬럼이 없는 FDR 버전이어도 유니버스는 나와야 한다."""
    patch_fdr_module(pd.DataFrame({"Code": ["000660", "005930"]}))
    assert krx._fetch_listing("KOSPI") == ["000660", "005930"]


def test_empty_listing_yields_no_tickers(patch_fdr_module):
    patch_fdr_module(pd.DataFrame())
    assert krx._fetch_listing("KOSPI") == []


# ── 4. 캐시 ─────────────────────────────────────────────────────────

def test_listing_is_fetched_once_and_then_cached(patch_fdr):
    calls = patch_fdr({"KOSPI": ["005930", "000660"]})
    krx.resolve("KOSPI 2")
    krx.resolve("KOSPI 1")
    assert calls == ["KOSPI"], "두 번째 호출이 캐시를 안 쓰고 다시 받았다"


def test_cache_can_be_bypassed(patch_fdr):
    calls = patch_fdr({"KOSPI": ["005930"]})
    krx.top_tickers("KOSPI", 1, use_cache=False)
    krx.top_tickers("KOSPI", 1, use_cache=False)
    assert calls == ["KOSPI", "KOSPI"]


def test_cache_file_holds_plain_codes(patch_fdr):
    patch_fdr({"KOSPI": ["005930", "000660"]})
    krx.resolve("KOSPI 2")
    with open(krx._CACHE_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"KOSPI": ["005930", "000660"]}


def test_unwritable_cache_does_not_break_the_scan(monkeypatch, patch_fdr):
    """캐시는 있으면 좋은 것 — 못 써도 유니버스는 나와야 한다."""
    patch_fdr({"KOSPI": ["005930"]})
    monkeypatch.setattr(krx, "_CACHE_FILE", "/존재하지_않는_경로/cache.json")
    assert krx.resolve("KOSPI 1") == ["005930.KS"]


# ── 5. signal_worker 연결 ───────────────────────────────────────────

def test_worker_resolves_dynamic_krx_universe(patch_fdr):
    patch_fdr({"KOSPI": ["005930", "000660", "035420"]})
    assert worker._resolve_universe("KOSPI 3") == ["005930.KS", "000660.KS", "035420.KS"]


def test_worker_still_prefers_fixed_presets(patch_fdr):
    patch_fdr({"KOSPI": ["005930"]})
    import app
    assert worker._resolve_universe("한국 대형 15") == app.UNIVERSE_PRESETS["한국 대형 15"]


def test_worker_falls_through_to_comma_separated_tickers(patch_fdr):
    patch_fdr({"KOSPI": ["005930"]})
    assert worker._resolve_universe("AAPL,MSFT") == ["AAPL", "MSFT"]


def test_listing_failure_is_reported_not_swallowed(patch_fdr, capsys):
    """FDR 이 죽으면 이유가 로그에 남아야 한다.

    폴백은 "KOSPI 100" → ["KOSPI 100"] 을 만들고, 그건 존재하지 않는 종목이라
    실패 1건으로만 집계된다. 로그가 없으면 왜 빈 스캔인지 알 방법이 없다.
    """
    patch_fdr({}, fail=True)
    assert worker._resolve_universe("KOSPI 100") == ["KOSPI 100"]
    assert "KRX 상장목록 조회 실패" in capsys.readouterr().err


# ── 6. 우선주 제외 ──────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [
    ("005930", True),    # 삼성전자 (보통주)
    ("005935", False),   # 삼성전자우
    ("000660", True),    # SK하이닉스
    ("000665", False),   # 우선주
    ("003557", False),   # 2우B 류
    ("009150", True),    # 삼성전기
])
def test_common_stock_detection(code, expected):
    assert krx.is_common_stock(code) is expected


def test_preferred_shares_are_excluded_from_the_listing(patch_fdr_module):
    """우선주가 섞이면 같은 회사가 랭킹에 두 자리를 차지한다.

    실측으로 확인한 문제다 — KOSPI 시총 상위 20에 005935(삼성전자우)가
    4위로 들어온다. 우선주는 유동성이 얇고 의결권이 없어 PER·PBR 이
    보통주와 체계적으로 달라, 밸류 팩터를 왜곡한다.
    """
    patch_fdr_module(pd.DataFrame({
        "Code": ["005930", "000660", "005935", "009150"],
        "Marcap": [400, 300, 200, 100],
    }))
    assert krx._fetch_listing("KOSPI") == ["005930", "000660", "009150"]


def test_filtering_happens_before_the_count_is_applied(patch_fdr_module):
    """상위 2종목을 요청했는데 우선주가 한 자리를 먹으면 안 된다."""
    patch_fdr_module(pd.DataFrame({
        "Code": ["005930", "005935", "000660"],
        "Marcap": [400, 300, 200],
    }))
    assert krx.top_tickers("KOSPI", 2, use_cache=False) == ["005930.KS", "000660.KS"]
