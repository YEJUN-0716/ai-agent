"""빗각 「시점」 측정의 창 경계·cut 파싱·공통 표본. 네트워크 없음.

세 개가 조용히 틀리면 판정 숫자가 바뀌는 자리다:
- 봉 타임스탬프는 **봉 시작**이라 마감 시각 라벨 봉을 집으면 미래를 본다
- cut 문자열이 곧 캐시 키다 — 공백 하나가 1,000 종목-일을 다시 받게 한다
- cut 마다 따로 탈락시키면 행마다 다른 종목-일의 중위값이 된다
"""
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from measure_bitgak_close_gap import (  # noqa: E402
    _common_sample, _last_before, _parse_cuts,
)


def _bars(prices: dict[str, float]) -> pd.DataFrame:
    idx = pd.to_datetime([f"2026-09-04 {t}" for t in prices])
    return pd.DataFrame({"Close": list(prices.values())}, index=idx)


def test_last_before_excludes_the_deadline_bar():
    """15:50 라벨 봉은 [15:50, 15:51) 체결 — MOC 마감 뒤라 쓰면 안 된다."""
    d = _bars({"19:48:00": 100.0, "19:49:00": 101.0, "19:50:00": 999.0})
    assert _last_before(d, pd.Timestamp("2026-09-04 19:50:00")) == 101.0


def test_last_before_returns_none_when_window_empty():
    d = _bars({"19:55:00": 100.0})
    assert _last_before(d, pd.Timestamp("2026-09-04 19:50:00")) is None


def test_parse_cuts_normalises_so_cache_keys_dont_fork():
    assert _parse_cuts(" 15:50, 9:40 ,15:50") == ("15:50", "09:40")


@pytest.mark.parametrize("bad", ["1550", "15:5x", "16:05", "09:30", ""])
def test_parse_cuts_rejects_bad_input(bad):
    with pytest.raises(SystemExit):
        _parse_cuts(bad)


def test_common_sample_keeps_only_rows_with_every_cut():
    """한 cut 이 비면 그 종목-일은 전부 빠진다 — 행마다 표본이 다르면 안 된다."""
    pick = [("AAPL", pd.Timestamp("2026-09-04")), ("MSFT", pd.Timestamp("2026-09-04")),
            ("JPM", pd.Timestamp("2026-09-04"))]
    got = {"AAPL|2026-09-04": {"15:50": 0.001, "15:34": 0.002},
           "MSFT|2026-09-04": {"15:50": 0.001, "15:34": None},   # 얇은 날
           "JPM|2026-09-04": {"15:50": 0.003}}                    # 아예 없음
    rows = _common_sample(got, pick, ("15:50", "15:34"))
    assert [r["15:50"] for r in rows] == [0.001]


def test_cut_window_is_ten_minutes_before_the_cut():
    """`deltas_multi` 가 쓰는 창 — [cut−10분, cut). 자 검사의 자 쪽."""
    cut = pd.Timestamp("2026-09-04 19:50:00")
    d = _bars({"19:39:00": 1.0, "19:40:00": 2.0, "19:49:00": 3.0, "19:50:00": 4.0})
    assert _last_before(d.loc[cut - timedelta(minutes=10):], cut) == 3.0
