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
