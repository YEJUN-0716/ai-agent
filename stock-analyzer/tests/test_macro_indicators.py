"""macro_indicators 테스트. FRED 호출을 목으로 대체해 네트워크를 안 탄다."""
from modules import macro_indicators as mi


def _monthly(values, start_year=2025):
    """[{'date','value'}...] — 월간 시리즈. 마지막이 최신이다."""
    return [{"date": f"{start_year + i // 12}-{i % 12 + 1:02d}-01", "value": float(v)}
            for i, v in enumerate(values)]


def test_yoy_pct_flat_series_is_zero_not_none():
    """변화가 없으면 0.0 이다 — '못 쟀다'(None)와 다른 값이어야 한다."""
    assert mi._yoy_pct(_monthly([100] * 13)) == 0.0
    assert mi._yoy_pct(_monthly([100] * 12)) is None, "13개월이 안 되면 못 잰다"


def test_core_cpi_of_exactly_zero_is_used_not_headline(monkeypatch):
    """근원 CPI 가 정확히 0.0% 여도 헤드라인으로 떨어지지 않는다.

    `_yoy_pct(core) or _yoy_pct(cpi)` 로 쓰면 0.0 이 거짓이라 헤드라인이 이긴다.
    """
    series = {
        "CPILFESL": _monthly([100] * 13),          # 근원 CPI: YoY 0.0%
        "CPIAUCSL": _monthly([100] + [110] * 12),  # 헤드라인: YoY +10%
        "UNRATE":   _monthly([4.0] * 13),
        "INDPRO":   _monthly([100] * 13),
        "T10Y2Y":   _monthly([1.5] * 13),
    }
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    monkeypatch.setattr(mi, "_fetch_series",
                        lambda sid, key, start: series.get(sid, []))

    got = mi.real_macro_score()
    assert got["available"]
    assert got["data"]["근원CPI(YoY%)"] == 0.0
    assert got["detail"]["인플레이션"] == 80, "0.0% 는 2.5% 미만 구간이다"


def test_no_key_falls_back_quietly(monkeypatch):
    """키가 없으면 조용히 중립(50)으로 떨어진다 — 호출부가 죽지 않는다."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    got = mi.real_macro_score()
    assert got["available"] is False and got["score"] == 50.0
