"""
market_scope — 시장 판별과 시장별 벤치마크 테스트.

국면 판정이 SPY 하나로만 돌던 것을 유니버스에 맞게 고른다. IC 가중치가
실전 스캔에 연결된 뒤로 이게 실제 배분에 영향을 준다 — 한국 종목의 팩터
비중을 미국 시장 상태가 정하면 안 된다.

ETF·지수 티커는 실제 조회로 확인한 값이다. 여기서는 형식과 매핑 규칙만
검사한다 (네트워크를 타지 않는다).
"""
import re

import pytest

import app
from modules import market_scope as scope

KRX_TICKER_PATTERN = re.compile(r"^\d{6}\.KS$")
US_ETF_PATTERN = re.compile(r"^[A-Z]{2,5}$")


# ── 1. 시장 판별 ────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,expected", [
    ("005930.KS", scope.KRX),
    ("247540.KQ", scope.KRX),
    ("AAPL", scope.US),
    ("BRK-B", scope.US),
    ("SPY", scope.US),
])
def test_market_of_ticker(ticker, expected):
    assert scope.market_of_ticker(ticker) == expected


def test_all_krx_universe_is_krx():
    assert scope.market_of(["005930.KS", "000660.KS", "247540.KQ"]) == scope.KRX


def test_all_us_universe_is_us():
    assert scope.market_of(["AAPL", "MSFT"]) == scope.US


def test_mixed_universe_follows_the_majority():
    """혼합 유니버스에 정답은 없다 — 다수결이면 소수 종목이 판정을 못 뒤집는다."""
    assert scope.market_of(["005930.KS", "000660.KS", "AAPL"]) == scope.KRX
    assert scope.market_of(["AAPL", "MSFT", "005930.KS"]) == scope.US


def test_tie_falls_back_to_us():
    """동수면 기존 동작(US)을 유지한다."""
    assert scope.market_of(["AAPL", "005930.KS"]) == scope.US


def test_empty_universe_is_us():
    assert scope.market_of([]) == scope.US
    assert scope.market_of(None) == scope.US


# ── 2. 국면 벤치마크 ────────────────────────────────────────────────

def test_us_universe_uses_spy():
    assert scope.regime_benchmark(["AAPL", "MSFT"]) == "SPY"


def test_krx_universe_uses_kospi():
    """한국 유니버스는 KOSPI 종합지수로 국면을 잰다 — 이게 이 모듈의 존재 이유다."""
    assert scope.regime_benchmark(["005930.KS", "000660.KS"]) == "^KS11"


def test_kosdaq_also_uses_kospi_index():
    """KOSDAQ 종목도 KOSPI 지수를 쓴다. 두 시장은 같은 거시 충격에 함께 움직인다."""
    assert scope.regime_benchmark(["247540.KQ", "086520.KQ"]) == "^KS11"


# ── 3. 섹터 ETF ─────────────────────────────────────────────────────

def test_us_sector_maps_to_spdr_etf():
    assert scope.sector_etf("Technology", scope.US) == "XLK"


def test_krx_sector_maps_to_korean_etf():
    assert scope.sector_etf("Technology", scope.KRX) == "139260.KS"


def test_sector_etf_follows_the_ticker_market():
    """같은 섹터라도 종목이 속한 시장의 ETF 와 비교해야 한다."""
    assert scope.sector_etf_for_ticker("Healthcare", "JNJ") == "XLV"
    assert scope.sector_etf_for_ticker("Healthcare", "068270.KS") == "266420.KS"


def test_unmapped_sector_returns_empty_string():
    """매핑이 없으면 빈 문자열 — 엉뚱한 ETF 와 비교시키지 않는다.

    한국은 부동산·유틸리티에 대표성 있는 섹터 ETF 를 확인하지 못했다.
    건설 ETF 를 부동산에 갖다 붙이는 근사는 하지 않는다.
    """
    assert scope.sector_etf("Real Estate", scope.KRX) == ""
    assert scope.sector_etf("Utilities", scope.KRX) == ""
    assert scope.sector_etf("존재하지 않는 섹터", scope.US) == ""


def test_krx_energy_and_materials_share_one_etf():
    """한국은 에너지와 화학이 한 ETF 로 묶여 있다 — 의도된 중복이다."""
    assert (scope.sector_etf("Energy", scope.KRX)
            == scope.sector_etf("Basic Materials", scope.KRX))


# ── 4. 티커 형식 — 오타가 조용한 빈 데이터가 되지 않게 ──────────────

@pytest.mark.parametrize("sector,etf", sorted(scope.SECTOR_ETF[scope.KRX].items()))
def test_krx_sector_etfs_are_well_formed(sector, etf):
    """KRX ETF 도 6자리 + .KS 여야 한다. 틀리면 yfinance 가 빈 프레임을 준다."""
    assert KRX_TICKER_PATTERN.match(etf), f"{sector} → {etf} 형식 오류"


@pytest.mark.parametrize("sector,etf", sorted(scope.SECTOR_ETF[scope.US].items()))
def test_us_sector_etfs_are_well_formed(sector, etf):
    assert US_ETF_PATTERN.match(etf), f"{sector} → {etf} 형식 오류"


def test_every_market_has_a_regime_benchmark():
    for market in scope.SECTOR_ETF:
        assert market in scope.REGIME_BENCHMARK


# ── 5. app.py 연결 ──────────────────────────────────────────────────

def test_app_sector_etf_alias_still_serves_us_lookups():
    """UI 가 app.SECTOR_ETF 를 계속 쓴다 — 미국 맵으로 남아 있어야 한다."""
    assert app.SECTOR_ETF["Technology"] == "XLK"
    assert app.SECTOR_ETF is scope.SECTOR_ETF[scope.US]


def test_get_market_regime_defaults_to_spy(monkeypatch):
    """인자를 안 주면 기존 동작(SPY) 그대로."""
    seen = []

    def fake_download(symbol, *args, **kwargs):
        seen.append(symbol)
        raise RuntimeError("네트워크 차단")

    monkeypatch.setattr(app.yf, "download", fake_download)
    assert app.get_market_regime() == ("neutral", 0.0)
    assert seen == ["SPY"]


def test_get_market_regime_honors_an_explicit_benchmark(monkeypatch):
    seen = []

    def fake_download(symbol, *args, **kwargs):
        seen.append(symbol)
        raise RuntimeError("네트워크 차단")

    monkeypatch.setattr(app.yf, "download", fake_download)
    app.get_market_regime("^KS11")
    assert seen == ["^KS11"]
