import numpy as np
import pandas as pd

from modules.intraday_session import regular_hours, session_ids


def _frame(utc_naive_times):
    idx = pd.DatetimeIndex(utc_naive_times)
    n = len(idx)
    return pd.DataFrame(
        {"Open": np.ones(n), "High": np.ones(n), "Low": np.ones(n),
         "Close": np.ones(n), "Volume": np.ones(n)}, index=idx)


def test_regular_hours_drops_premarket_and_afterhours_in_edt():
    # 2026-06-15 은 EDT (UTC-4). 정규장 09:30~16:00 ET = 13:30~20:00 UTC.
    df = _frame([
        "2026-06-15 12:00",  # 08:00 ET 프리마켓 → 버린다
        "2026-06-15 13:30",  # 09:30 ET 첫 봉   → 남긴다
        "2026-06-15 19:45",  # 15:45 ET 막 봉   → 남긴다
        "2026-06-15 20:00",  # 16:00 ET 장 마감 → 버린다 (마감 시각에 시작하는 봉)
        "2026-06-15 22:00",  # 18:00 ET 애프터  → 버린다
    ])
    kept = regular_hours(df)
    assert list(kept.index.strftime("%H:%M")) == ["13:30", "19:45"]


def test_regular_hours_handles_est_offset():
    # 2026-01-15 는 EST (UTC-5). 정규장 = 14:30~21:00 UTC.
    df = _frame([
        "2026-01-15 13:30",  # 08:30 ET 프리마켓 → 버린다
        "2026-01-15 14:30",  # 09:30 ET 첫 봉    → 남긴다
        "2026-01-15 20:45",  # 15:45 ET 막 봉    → 남긴다
    ])
    kept = regular_hours(df)
    assert list(kept.index.strftime("%H:%M")) == ["14:30", "20:45"]


def test_session_ids_group_by_et_trading_day():
    idx = pd.DatetimeIndex([
        "2026-06-15 13:30",  # 6/15 장
        "2026-06-15 19:45",  # 6/15 장
        "2026-06-16 13:30",  # 6/16 장
    ])
    ids = session_ids(idx)
    assert ids[0] == ids[1]
    assert ids[2] > ids[1]


def test_session_ids_is_monotonic_non_decreasing():
    idx = pd.DatetimeIndex([
        "2026-01-15 14:30", "2026-01-15 20:45",
        "2026-06-15 13:30", "2026-06-15 19:45",
    ])
    ids = session_ids(idx)
    assert np.all(np.diff(ids) >= 0)


def test_regular_hours_on_empty_frame_returns_empty():
    df = _frame([])
    assert regular_hours(df).empty
