"""universe 모듈 테스트. 네트워크를 타지 않는다."""
from modules import universe

# scripts/audit_universe.py 실행 결과 (2026-07-20).
# 상장폐지·피인수와 yfinance 데이터 공백이 섞여 있으나,
# 사유와 무관하게 가격 데이터가 없으면 분석에 넣을 수 없다.
UNUSABLE = [
    "ANSS", "ATVI", "CTRA", "FBHS", "HES", "IPG", "JNPR",
    "K", "KSU", "MMC", "MRO", "PARA", "PXD", "SEE",
]


def test_no_duplicates():
    """같은 티커가 두 번 들어가면 IC 계산에서 가중치가 왜곡된다."""
    assert len(universe.SP500) == len(set(universe.SP500))


def test_size_is_plausible():
    """
    출처 프리셋이 290종목이고 사용 불가 14종목을 뺀 276이 기대값.
    크게 벗어나면 목록 생성이 깨진 것.
    """
    assert 260 <= len(universe.SP500) <= 300


def test_unusable_tickers_removed():
    """감사에서 데이터가 확인되지 않은 티커는 남아 있으면 안 된다."""
    for dead in UNUSABLE:
        assert dead not in universe.SP500, f"{dead}는 제거됐어야 한다"


def test_universe_is_much_larger_than_old_hardcoded_list():
    """
    이 작업의 목적은 표본 확대다. 기존 37종목보다 충분히 커야 하고,
    크로스섹션 IC에 필요한 최소 표본(통상 100종목)을 넘어야 한다.
    """
    assert len(universe.SP500) > 100


def test_core_large_caps_present():
    """대표 대형주가 빠지면 목록이 잘못 생성된 것."""
    for tk in ("AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "WMT"):
        assert tk in universe.SP500
