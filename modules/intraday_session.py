"""15분봉을 '하루 장' 단위로 가르는 유틸.

Alpaca 봉 인덱스는 UTC naive 다(`alpaca_data._to_frame`). 그대로 시각을
자르면 서머타임 때문에 반년마다 한 시간씩 어긋난다 — 그래서 ET 로 바꿔서
자른다. 미국 장은 09:30~16:00 ET 고, 16:00 에 시작하는 봉은 없다(15:45 봉이
막 봉이다).

정규장만 남기는 이유는 두 가지다. 장외 봉을 남기면 '그날 마지막 봉'이
거래량 0짜리 20:00 봉이 되어 당일 청산 시각이 틀리고, 얇은 장외 봉의
꼬리가 구조 신호(FVG·OB)로 잡힌다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_ET = "America/New_York"
_OPEN_MIN = 9 * 60 + 30    # 09:30
_CLOSE_MIN = 16 * 60       # 16:00 — 이 시각에 시작하는 봉은 정규장이 아니다


def _et_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """UTC naive 인덱스 → ET 로 환산한 인덱스."""
    return index.tz_localize("UTC").tz_convert(_ET)


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """정규장(09:30~15:45 ET 시작) 봉만 남긴다. 인덱스는 UTC naive 그대로."""
    if df is None or len(df) == 0:
        return df
    et = _et_index(df.index)
    minutes = et.hour * 60 + et.minute
    keep = (minutes >= _OPEN_MIN) & (minutes < _CLOSE_MIN)
    return df[keep]


def session_ids(index: pd.DatetimeIndex) -> np.ndarray:
    """봉마다 ET 거래일 번호. 같은 날 장이면 같은 값.

    반휴장일(13:00 ET 조기 마감)도 자동으로 맞는다 — 날짜로 가르지 시각으로
    가르지 않으므로, 그날 마지막 봉이 12:45 든 15:45 든 '세션의 끝'이다.
    """
    if len(index) == 0:
        return np.empty(0, dtype=np.int64)
    et = _et_index(index)
    return et.normalize().tz_localize(None).astype("int64").to_numpy()
