"""ICT 구조 감지 — 이벤트 목록이 시각순인가.

`find_bos_choch` 는 스윙 **쌍 순서**로 이벤트를 쌓는데, 소비자 셋이 전부
`events[-1]` 을 "가장 최근 구조 전환" 으로 읽는다. 오래된 스윙이 뒤늦게
깨지면 그 돌파가 나중 쌍의 돌파보다 **뒤**에 일어났는데도 목록에서는 앞에
온다 — 그러면 ICT 애널리스트가 반대 방향을 집는다.

여기서 잠그는 것은 "정렬돼 나온다" 하나다. 소비자마다 세우게 두면 언젠가
한 곳이 안 따라온다.
"""
import numpy as np
import pandas as pd

from modules.ict_analysis import calc_ict_adjustment, find_bos_choch


def _closes(values) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"Close": np.asarray(values, dtype=float)}, index=idx)


def _swings(rows) -> pd.DataFrame:
    return pd.DataFrame([{"idx": i, "date": None, "price": p, "type": t}
                         for i, p, t in rows])


def _late_break_frame():
    """오래된 레벨이 **나중에** 깨지는 판.

      스윙 H(110) @2 · L(90) @5 · H(105) @8
      → 쌍1(H110→L90) 은 close>110 을 찾는데 그건 k=20 에서야 온다 (BOS_bull)
      → 쌍2(L90→H105) 는 close<90 을 찾고 그건 k=10 에 온다   (BOS_bear)

    쌍 순서로 쌓으면 [bull@20, bear@10] 이라 `events[-1]` 이 bear@10 —
    실제 최신 전환(bull@20)의 반대다.
    """
    closes = [100.0] * 30
    closes[10] = 85.0     # L(90) 하향 돌파 — 먼저 일어난다
    closes[20] = 115.0    # H(110) 상향 돌파 — 나중에 일어난다
    return _closes(closes), _swings([(2, 110.0, "H"), (5, 90.0, "L"), (8, 105.0, "H")])


def test_events_come_back_in_time_order():
    df, sw = _late_break_frame()
    events = find_bos_choch(df, sw)
    assert [e["idx"] for e in events] == sorted(e["idx"] for e in events)


def test_last_event_is_the_most_recent_break():
    """소비자가 읽는 자리 — events[-1] 이 실제로 가장 늦게 깨진 것이어야 한다."""
    df, sw = _late_break_frame()
    events = find_bos_choch(df, sw)
    assert len(events) == 2                      # 판이 의도대로 깔렸는지 먼저
    assert events[-1]["idx"] == max(e["idx"] for e in events)
    assert "bull" in events[-1]["type"]          # 정렬 전에는 bear 를 집었다


def test_adjustment_reads_the_latest_direction():
    """±10 짜리 BOS 항이 최신 전환의 부호를 따라가는가 (합성 봉 전체 경로)."""
    rng = np.random.default_rng(3)
    n = 120
    close = 100 + np.cumsum(rng.normal(0.0, 0.8, n))
    df = pd.DataFrame({
        "Open": close, "High": close + 0.5, "Low": close - 0.5,
        "Close": close, "Volume": np.full(n, 1e6),
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))
    out = calc_ict_adjustment(df)
    bos = [s for s in out["signals"] if "구조 전환" in s]
    if not bos:                                   # 이 판에 BOS 가 안 잡히면 볼 게 없다
        return
    sub = df.tail(80)
    from modules.ict_analysis import find_swing_points
    events = find_bos_choch(sub, find_swing_points(sub, lookback=5))
    assert events[-1]["type"].upper() in bos[0]
