# 15분봉 트레이드 플랜 측정 (Alpaca 3단계 — 3a) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 일봉에서 +0.69R 을 낸 트레이드 플랜 규칙이 15분봉 단타에서도 통하는지, 규칙을 고를 때 안 본 구간에서 재서 숫자로 답한다.

**Architecture:** 기존 백테스트 엔진(`modules/trade_plan_backtest.py`)에 **세션 경계**를 가르쳐 당일 청산을 재현하고, `build_trade_plan` 의 봉 개수 창을 `scale` 인자로 빼서 두 해석(봉 그대로 / 일수 환산)을 나란히 잰다. Alpaca 에서 15분봉 3년치를 한 번 받아 parquet 에 캐시하고, 이후 측정은 네트워크 없이 돈다.

**Tech Stack:** Python 3.14 · pandas · numpy · pytest · requests (기존 `modules/alpaca_data.py`)

## Global Constraints

- **새 의존성 금지.** `requirements.txt` 를 건드리지 않는다. `python-dotenv` 도 안 쓴다 — 키는 셸에서 읽는다: `set -a && . ./.env && set +a`
- **네트워크는 Task 4 에서만.** 측정 스크립트(Task 5)는 저장 패널만 읽는다.
- **`scale=1` 은 기존 동작과 글자 그대로 같아야 한다.** 기존 테스트가 전부 통과해야 한다 (`pytest -q`, 현재 441 그린).
- **금액 단위 USD.** 원화 환산 없음.
- **청산 시각은 당일 마지막 정규장 15분봉(15:45 ET)의 시가.** 3b 러너와 같은 규칙이어야 한다.
- 봉 인덱스는 **UTC naive** 다 (`modules/alpaca_data._to_frame`). ET 로 바꿔야 세션을 가를 수 있다.
- 커밋은 `feat/alpaca-intraday` 브랜치에. main 직접 커밋 금지.

## 설계서에서 바뀐 것 하나

설계서는 유니버스를 "일봉 성적표와 같은 것"이라고 썼다. 그건 **S&P 500 전체 500종목**이고, 15분봉은 봉이 일봉의 26배라 `build_trade_plan` 을 봉마다 재계산하는 백테스트가 며칠 걸린다. **`S&P 500 대형 30` 프리셋(30종목)으로 줄인다.** 단타는 스프레드가 비용의 전부라 유동성 최상위로 재는 게 오히려 맞다. Task 4 에서 1종목 실측 시간을 재고 나서 최종 확정한다.

---

### Task 1: 세션 유틸 — 정규장 필터와 세션 구분

15분봉에는 프리마켓·애프터마켓 봉이 섞여 온다. 그대로 두면 (1) "그날 마지막 봉"이 20:00 ET 의 거래량 0짜리 봉이 되고 (2) 얇은 장외 봉이 구조 신호로 잡힌다. 정규장만 남기고, 각 봉이 어느 날 장에 속하는지 번호를 붙인다.

**Files:**
- Create: `modules/intraday_session.py`
- Test: `tests/test_intraday_session.py`

**Interfaces:**
- Produces:
  - `regular_hours(df: pd.DataFrame) -> pd.DataFrame` — UTC naive 인덱스를 받아 09:30~16:00 ET 봉만 남긴 것을 돌려준다. 인덱스는 그대로 UTC naive.
  - `session_ids(index: pd.DatetimeIndex) -> np.ndarray` — 봉마다 ET 기준 거래일 번호(int). 같은 날 장이면 같은 값, 다음 날이면 더 큰 값.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_intraday_session.py
import numpy as np
import pandas as pd
import pytest

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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_intraday_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.intraday_session'`

- [ ] **Step 3: 최소 구현**

```python
# modules/intraday_session.py
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
    """봉마다 ET 거래일 번호. 같은 날 장이면 같은 값."""
    if len(index) == 0:
        return np.empty(0, dtype=np.int64)
    et = _et_index(index)
    return et.normalize().tz_localize(None).astype("int64").to_numpy()
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_intraday_session.py -q`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add modules/intraday_session.py tests/test_intraday_session.py
git commit -m "feat: 15분봉 정규장 필터 + 세션 구분 유틸"
```

---

### Task 2: 백테스트에 당일 청산을 가르친다

`_simulate_outcome` 은 지금 세션을 모른다. 손절·목표 어느 쪽도 안 닿으면 `timeout` 으로 R=0 을 준다. 단타는 그러면 안 된다 — 마감에 시장가로 털고 나온 **실제 손익**이 R 에 들어가야 한다. 안 그러면 백테스트가 러너를 대표하지 못한다.

**Files:**
- Modify: `modules/trade_plan_backtest.py` (`_simulate_outcome`, `backtest_trade_plans`)
- Test: `tests/test_trade_plan_backtest_intraday.py`

**Interfaces:**
- Consumes: `modules.intraday_session.session_ids` (Task 1)
- Produces:
  - `_simulate_outcome(..., *, sessions: np.ndarray | None = None, opens: np.ndarray | None = None)` — `sessions` 를 주면 (1) 진입은 같은 세션 안에서만 찾고 (2) 세션 마지막 봉에서 그 봉의 **시가**로 청산한다. 결과 `outcome` 에 `"eod"` 가 추가되고 `r` 은 실현 R.
  - `backtest_trade_plans(..., *, sessions: np.ndarray | None = None, scale: int = 1)` — `sessions` 를 그대로 넘기고, `scale` 은 `build_trade_plan` 에 넘긴다 (Task 3 에서 쓴다).

**세션 규칙 (정확히 이대로):**
- 진입 탐색은 `start_idx+1` 부터, **세션 마지막 봉은 제외**하고 찾는다. 마지막 봉에 체결되면 같은 봉에서 바로 청산해야 해서 봉 안의 순서를 알 수 없다.
- 보유 중 세션 마지막 봉에 닿으면 **그 봉의 손절·목표를 보지 않고** 시가로 청산한다. 러너가 15:45 에 시장가를 내는 것과 같다.
- 실현 R: long `(exit - entry_ref) / (entry_ref - stop)`, short `(entry_ref - exit) / (stop - entry_ref)`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_trade_plan_backtest_intraday.py
import numpy as np

from modules.trade_plan_backtest import _simulate_outcome

# 세션 2개 × 4봉. 인덱스 0~3 이 첫날, 4~7 이 둘째 날.
SESSIONS = np.array([1, 1, 1, 1, 2, 2, 2, 2])


def test_eod_exit_uses_open_of_last_bar_of_session():
    # 롱 진입 100, 손절 90 (위험 10), 목표 130. 손절·목표 둘 다 안 닿고
    # 세션 마지막 봉(idx 3)의 시가 105 에 털린다 → r = (105-100)/10 = +0.5
    highs = np.array([100.0, 101, 102, 106, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 98, 104, 100, 100, 100, 100])
    opens = np.array([100.0, 100, 100, 105, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=98.0, entry_high=100.0, stop=90.0, target=130.0, rr=3.0,
        sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "eod"
    assert res["r"] == 0.5
    assert res["exit_idx"] == 3


def test_stop_before_eod_still_wins_out():
    # idx 2 에서 손절(90)을 친다 → EOD 까지 가지 않는다.
    highs = np.array([100.0, 101, 102, 106, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 89, 104, 100, 100, 100, 100])
    opens = np.array([100.0, 100, 100, 105, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=98.0, entry_high=100.0, stop=90.0, target=130.0, rr=3.0,
        sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "loss"
    assert res["r"] == -1.0


def test_no_fill_when_entry_not_touched_before_session_end():
    # 진입 구간 89~90 에 첫 세션 동안 안 닿는다 → 다음 세션으로 넘어가지 않는다.
    highs = np.array([100.0, 101, 102, 103, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 98, 97, 85, 85, 85, 85])
    opens = np.array([100.0, 100, 100, 100, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=89.0, entry_high=90.0, stop=80.0, target=120.0, rr=3.0,
        sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "nofill"
    assert res["fill_idx"] is None


def test_short_eod_exit_r_sign():
    # 숏 진입 100, 손절 110 (위험 10), 목표 70. EOD 시가 95 → r = (100-95)/10 = +0.5
    highs = np.array([100.0, 101, 102, 96, 100, 100, 100, 100])
    lows = np.array([100.0, 99, 98, 94, 100, 100, 100, 100])
    opens = np.array([100.0, 100, 102, 95, 100, 100, 100, 100])
    res = _simulate_outcome(
        highs, lows, 0, "short",
        entry_low=100.0, entry_high=102.0, stop=110.0, target=70.0, rr=3.0,
        sessions=SESSIONS, opens=opens)
    assert res["outcome"] == "eod"
    assert res["r"] == 0.5


def test_sessions_none_keeps_old_timeout_behaviour():
    # 세션을 안 주면 예전 그대로 — 홀드 창 안에 아무것도 안 닿으면 timeout, R=0.
    highs = np.array([100.0, 101, 102, 103])
    lows = np.array([100.0, 99, 98, 97])
    res = _simulate_outcome(
        highs, lows, 0, "long",
        entry_low=98.0, entry_high=100.0, stop=80.0, target=130.0, rr=3.0)
    assert res["outcome"] == "timeout"
    assert res["r"] == 0.0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_trade_plan_backtest_intraday.py -q`
Expected: FAIL — `_simulate_outcome() got an unexpected keyword argument 'sessions'`

- [ ] **Step 3: `_simulate_outcome` 을 고친다**

`modules/trade_plan_backtest.py` 의 `_simulate_outcome` 를 통째로 아래로 교체한다.

```python
def _simulate_outcome(
    highs: np.ndarray, lows: np.ndarray, start_idx: int, direction: str,
    entry_low: float, entry_high: float, stop: float, target: float, rr: float,
    *, fill_window: int = DEFAULT_FILL_WINDOW, hold_window: int = DEFAULT_HOLD_WINDOW,
    sessions: np.ndarray | None = None, opens: np.ndarray | None = None,
) -> dict:
    """
    start_idx 다음 봉부터 진입 체결을 찾고, 체결되면 손절/목표를 시뮬레이션.

    반환: {"outcome": "win"|"loss"|"timeout"|"eod"|"nofill", "r": float,
           "fill_idx": int|None, "exit_idx": int|None}
      long  체결: 이후 봉의 Low  <= entry_high (되돌림 진입)
      short 체결: 이후 봉의 High >= entry_low

    sessions 를 주면 **당일 청산 단타**로 시뮬레이션한다 (opens 도 함께 필요).
    세션 마지막 봉에서는 손절·목표를 보지 않고 그 봉의 **시가**로 턴다 —
    러너가 15:45 ET 에 시장가를 내는 것과 같은 규칙이어야, 여기서 잰 숫자가
    러너를 대표한다. 마감 종가로 재면 러너가 못 내는 15분치 이득이 섞인다.
    """
    n = len(highs)
    intraday = sessions is not None
    if intraday and opens is None:
        raise ValueError("sessions 를 주면 opens 도 필요합니다 (EOD 청산가).")

    def _is_session_end(k: int) -> bool:
        return intraday and (k + 1 >= n or sessions[k + 1] != sessions[k])

    # ── 진입 체결 ──
    # 단타는 같은 세션 안에서만 체결을 찾는다. 세션 마지막 봉은 뺀다 —
    # 거기서 체결되면 같은 봉에서 바로 청산해야 하는데 봉 안의 순서를 모른다.
    fill_idx = None
    for j in range(start_idx + 1, min(start_idx + 1 + fill_window, n)):
        if intraday:
            if sessions[j] != sessions[start_idx] or _is_session_end(j):
                break
        if direction == "long" and lows[j] <= entry_high:
            fill_idx = j
            break
        if direction == "short" and highs[j] >= entry_low:
            fill_idx = j
            break
    if fill_idx is None:
        return {"outcome": "nofill", "r": 0.0, "fill_idx": None, "exit_idx": None}

    # ── 보유 ──
    # 이 함수는 entry_ref 를 인자로 안 받는다. 구간 중간값이 가진 정보로
    # 할 수 있는 최선이다 (기존 win/loss 경로의 R 은 rr 로 들어오므로 영향 없음).
    entry_ref = (entry_low + entry_high) / 2
    for k in range(fill_idx, min(fill_idx + hold_window, n)):
        if intraday and _is_session_end(k):
            exit_px = float(opens[k])
            denom = (entry_ref - stop) if direction == "long" else (stop - entry_ref)
            r = ((exit_px - entry_ref) / denom) if direction == "long" \
                else ((entry_ref - exit_px) / denom)
            return {"outcome": "eod", "r": round(float(r), 4),
                    "fill_idx": fill_idx, "exit_idx": k}
        if direction == "long":
            hit_stop = lows[k] <= stop
            hit_tgt = highs[k] >= target
        else:
            hit_stop = highs[k] >= stop
            hit_tgt = lows[k] <= target
        if hit_stop:                       # 같은 봉에 둘 다면 손절 우선 (보수적)
            return {"outcome": "loss", "r": -1.0, "fill_idx": fill_idx, "exit_idx": k}
        if hit_tgt:
            return {"outcome": "win", "r": float(rr), "fill_idx": fill_idx, "exit_idx": k}
    return {"outcome": "timeout", "r": 0.0, "fill_idx": fill_idx, "exit_idx": None}
```

- [ ] **Step 4: `backtest_trade_plans` 에 세션을 흘려보낸다**

`modules/trade_plan_backtest.py` 의 `backtest_trade_plans` 시그니처와 본문 두 곳을 고친다.

시그니처:

```python
def backtest_trade_plans(
    df: pd.DataFrame, *, min_rr: float = DEFAULT_MIN_RR,
    fill_window: int = DEFAULT_FILL_WINDOW, hold_window: int = DEFAULT_HOLD_WINDOW,
    cooldown: int = DEFAULT_COOLDOWN, min_history: int = MIN_BARS,
    sessions: np.ndarray | None = None, scale: int = 1,
) -> dict:
```

배열 준비 (`lows = ...` 다음 줄에 추가):

```python
    opens = df["Open"].to_numpy(dtype=float) if sessions is not None else None
```

플랜 생성과 시뮬레이션 호출:

```python
        plan = build_trade_plan(df.iloc[: i + 1], min_rr=min_rr, scale=scale)
```

```python
        res = _simulate_outcome(
            highs, lows, i, plan["direction"],
            plan["entry"]["low"], plan["entry"]["high"],
            plan["stop"], plan["targets"][0], plan["rr"][0],
            fill_window=fill_window, hold_window=hold_window,
            sessions=sessions, opens=opens,
        )
```

`_stats` 를 고친다. `filled` 에 `"eod"` 를 넣고, `timeouts` 를 **뺄셈이 아니라 직접 세기**로 바꾼다 — `len(filled) - len(resolved)` 를 그대로 두면 EOD 청산이 전부 timeout 으로 집계된다.

```python
    filled = [t for t in trades if t["outcome"] in ("win", "loss", "timeout", "eod")]
```

```python
        "timeouts": len([t for t in trades if t["outcome"] == "timeout"]),
        "eod_exits": len([t for t in trades if t["outcome"] == "eod"]),
```

**참고:** `scale` 인자는 Task 3 에서 `build_trade_plan` 에 생긴다. Task 3 전에는 Step 4 의 `scale=scale` 이 `TypeError` 를 낸다 — 그래서 이 Step 은 Task 3 과 **한 커밋으로 묶어** 마지막에 검증한다. Step 5 를 먼저 돌려 세션 테스트만 통과시키고, `scale` 전달 줄은 Task 3 Step 3 에서 넣는다.

- [ ] **Step 5: 세션 테스트 통과 확인 (scale 전달 줄은 아직 넣지 않은 상태)**

Run: `python -m pytest tests/test_trade_plan_backtest_intraday.py tests/test_trade_plan_backtest.py -q`
Expected: 새 테스트 5개 + 기존 테스트 전부 passed

- [ ] **Step 6: 커밋**

```bash
git add modules/trade_plan_backtest.py tests/test_trade_plan_backtest_intraday.py
git commit -m "feat: 백테스트에 당일 청산(EOD) — 세션 경계와 실현 R"
```

---

### Task 3: 창(window)을 `scale` 인자로 뺀다

`build_trade_plan` 이 보는 창은 전부 **봉 개수**다. 일봉에서 "50일 추세"인 `REGIME_MA=50` 이 15분봉에서는 2일이 된다. 15분 총괄 판정에서 `calc_momentum` 이 "3개월 모멘텀"을 이틀 반으로 계산해 점수가 50 에 붙어 있던 것과 같은 함정이다.

`scale` 은 창 상수에 곱하는 배수다. `scale=1` 이면 지금과 완전히 같고, `scale=26` 이면 15분봉에서 일봉과 같은 실제 시간을 본다 (정규장 하루 = 15분봉 26개).

**Files:**
- Modify: `modules/ict_analysis.py:367-470` (`calc_ict_adjustment`), `modules/ict_analysis.py:321` (`detect_crt_setup` 호출부)
- Modify: `modules/trade_plan.py` (`build_trade_plan`, `_pick_long_entry`, `_pick_short_entry`, `_long_targets`, `_short_targets`, `_short_trend_ok`)
- Modify: `modules/trade_plan_backtest.py` (Task 2 Step 4 에서 미뤄둔 `scale=scale` 줄)
- Test: `tests/test_trade_plan_scale.py`

**Interfaces:**
- Produces:
  - `calc_ict_adjustment(df: pd.DataFrame, *, scale: int = 1) -> dict`
  - `build_trade_plan(df, *, min_rr=..., short_trend_filter=True, direction=None, scale: int = 1) -> dict`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_trade_plan_scale.py
import numpy as np
import pandas as pd
import pytest

from modules.ict_analysis import calc_ict_adjustment
from modules.trade_plan import MIN_BARS, build_trade_plan


def _wave(n: int, seed: int = 7) -> pd.DataFrame:
    """결정적인 가짜 봉. 추세 + 잔물결이라 구조 신호가 실제로 잡힌다."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.2, 1.2, n)
    low = close - rng.uniform(0.2, 1.2, n)
    open_ = close - rng.normal(0, 0.4, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": np.full(n, 1e6)}, index=idx)


def test_scale_1_matches_no_scale_argument():
    """scale=1 은 인자를 안 준 것과 글자 그대로 같아야 한다."""
    df = _wave(300)
    assert build_trade_plan(df) == build_trade_plan(df, scale=1)
    assert calc_ict_adjustment(df) == calc_ict_adjustment(df, scale=1)


def test_scale_raises_min_bars_requirement():
    """scale 을 키우면 필요한 워밍업 봉 수도 같이 커진다."""
    df = _wave(MIN_BARS + 5)          # scale=1 에는 충분, scale=4 에는 부족
    assert build_trade_plan(df, scale=1)["reason_invalid"] != "데이터 부족"
    assert build_trade_plan(df, scale=4)["reason_invalid"] == "데이터 부족"


def test_scale_changes_the_plan_on_long_history():
    """창이 실제로 넓어지면 결과가 달라진다 — 인자가 먹히는지 확인."""
    df = _wave(1200)
    assert build_trade_plan(df, scale=1) != build_trade_plan(df, scale=4)


def test_scale_must_be_positive_int():
    df = _wave(300)
    with pytest.raises(ValueError):
        build_trade_plan(df, scale=0)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_trade_plan_scale.py -q`
Expected: FAIL — `build_trade_plan() got an unexpected keyword argument 'scale'`

- [ ] **Step 3: `calc_ict_adjustment` 에 scale 을 넣는다**

`modules/ict_analysis.py` 의 `calc_ict_adjustment` 시그니처를 바꾸고, 안의 창 숫자에 `scale` 을 곱한다.

```python
def calc_ict_adjustment(df: pd.DataFrame, *, scale: int = 1) -> dict:
```

docstring 끝에 한 문단 추가:

```
    scale — 창(lookback)에 곱하는 배수. 일봉은 1. 15분봉에서 일봉과 같은
    실제 시간을 보려면 26(정규장 하루 = 15분봉 26개). 이 함수의 창은 전부
    **봉 개수**라, scale 없이 분봉에 먹이면 "3개월 구조"가 이틀이 된다.
```

본문에서 아래 5곳을 고친다 (현재 줄번호 기준 — 실제 위치는 문자열로 찾을 것):

| 현재 | 바꿀 것 |
|---|---|
| `if df.empty or len(df) < 60:` | `if df.empty or len(df) < 60 * scale:` |
| `crt = detect_crt_setup(df)` | `crt = detect_crt_setup(df, period=3 * scale)` |
| `fvgs = find_fvg(df, lookback=60, min_gap_pct=0.03)` | `fvgs = find_fvg(df, lookback=60 * scale, min_gap_pct=0.03)` |
| `obs = find_order_blocks(df, lookback=60, min_move_pct=1.0)` | `obs = find_order_blocks(df, lookback=60 * scale, min_move_pct=1.0)` |
| `swings = find_swing_points(df.tail(80), lookback=5)` | `swings = find_swing_points(df.tail(80 * scale), lookback=5 * scale)` |
| `events = find_bos_choch(df.tail(80), swings)` | `events = find_bos_choch(df.tail(80 * scale), swings)` |
| `pd_info = premium_discount(df, lookback=60)` | `pd_info = premium_discount(df, lookback=60 * scale)` |

- [ ] **Step 4: `trade_plan.py` 에 scale 을 넣는다**

`_pick_long_entry`, `_pick_short_entry`, `_long_targets`, `_short_targets`, `_short_trend_ok` 다섯 함수에 `scale: int = 1` 키워드 인자를 더하고, 안의 창 숫자에 곱한다:

- `find_order_blocks(df, lookback=80, ...)` → `lookback=80 * scale`
- `find_fvg(df, lookback=80, ...)` → `lookback=80 * scale`
- `premium_discount(df, lookback=60)` → `lookback=60 * scale`
- `find_swing_points(df.tail(80), lookback=5)` → `find_swing_points(df.tail(80 * scale), lookback=5 * scale)`
- `_short_trend_ok` 안의 `REGIME_MA` → `REGIME_MA * scale`, `REGIME_SLOPE_LOOKBACK` → `REGIME_SLOPE_LOOKBACK * scale` (길이 검사 `len(close) < REGIME_MA + REGIME_SLOPE_LOOKBACK` 포함)

`build_trade_plan` 시그니처와 본문:

```python
def build_trade_plan(df: pd.DataFrame, *, min_rr: float = DEFAULT_MIN_RR,
                     short_trend_filter: bool = True,
                     direction: str | None = None, scale: int = 1) -> dict:
```

시그니처 검사에 한 줄 추가 (`if direction not in (...)` 바로 뒤):

```python
    if not isinstance(scale, int) or scale < 1:
        raise ValueError(f"scale 은 1 이상의 정수여야 합니다: {scale!r}")
```

데이터 부족 검사:

```python
    if df is None or df.empty or len(df) < MIN_BARS * scale:
        return empty
```

ICT 호출과 하위 함수 호출에 전부 `scale=scale` 을 넘긴다:

```python
        adj_info = calc_ict_adjustment(df, scale=scale)
```

`_pick_long_entry(df, cur)` → `_pick_long_entry(df, cur, scale=scale)` 식으로 다섯 군데 모두.

- [ ] **Step 5: Task 2 에서 미뤄둔 줄을 넣는다**

`modules/trade_plan_backtest.py` 의 `build_trade_plan` 호출을 고친다:

```python
        plan = build_trade_plan(df.iloc[: i + 1], min_rr=min_rr, scale=scale)
```

그리고 `min_history` 기본값도 scale 을 따라가야 한다 — `backtest_trade_plans` 본문 첫머리에 추가:

```python
    if min_history == MIN_BARS and scale != 1:
        min_history = MIN_BARS * scale
```

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m pytest -q`
Expected: 기존 441개 + 새 테스트 전부 passed. **`scale=1` 회귀가 하나라도 깨지면 멈추고 원인을 찾는다** — 그게 이 Task 의 유일한 안전장치다.

- [ ] **Step 7: 커밋**

```bash
git add modules/ict_analysis.py modules/trade_plan.py modules/trade_plan_backtest.py tests/test_trade_plan_scale.py
git commit -m "feat: 트레이드 플랜 창을 scale 인자로 — 분봉에서 창의 뜻이 달라지는 문제"
```

---

### Task 4: 15분봉 패널을 받아 캐시한다

측정을 반복하려면 데이터가 로컬에 있어야 한다. 한 번 받아 parquet 에 넣고, 이후 측정은 네트워크 없이 돈다.

**Files:**
- Create: `scripts/fetch_intraday_panel.py`
- Modify: `.gitignore` (패널 캐시 제외)
- 산출물: `data/intraday_panel_15m.parquet` (커밋 안 함)

**Interfaces:**
- Consumes: `modules.alpaca_data.get_bars`, `modules.intraday_session.regular_hours`
- Produces: 컬럼이 `(field, ticker)` MultiIndex 인 parquet. `scripts/measure_trade_plan_oos.py` 가 쓰는 `data/price_panel_v1.parquet` 와 같은 모양이라 `_ohlcv()` 를 그대로 재사용할 수 있다.

- [ ] **Step 1: 스크립트를 쓴다**

```python
#!/usr/bin/env python
"""15분봉 패널을 Alpaca 에서 받아 parquet 로 저장한다.

    set -a && . ./.env && set +a
    python scripts/fetch_intraday_panel.py [종목수] [년수]

한 번만 받으면 된다. 이후 측정(measure_trade_plan_intraday.py)은 네트워크를
안 탄다.

유니버스가 30종목인 이유: 백테스트가 봉마다 build_trade_plan 을 재계산하는데
15분봉은 일봉의 26배다. S&P 500 전체로는 며칠 걸린다. 단타는 스프레드가
비용의 전부라 유동성 최상위로 재는 편이 오히려 맞다.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from modules.alpaca_data import get_bars  # noqa: E402
from modules.intraday_session import regular_hours  # noqa: E402

OUT = Path("data/intraday_panel_15m.parquet")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

# paper_trade_runner_toss.UNIVERSE_PRESETS["S&P 500 대형 30"] 과 같은 목록.
# 러너를 import 하면 토스 설정까지 딸려 오므로 여기 적어 둔다.
UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B", "JPM", "V",
    "JNJ", "UNH", "XOM", "PG", "HD", "MA", "ABBV", "MRK", "KO", "PEP",
    "COST", "AVGO", "LLY", "WMT", "MCD", "CRM", "ADBE", "CSCO", "ACN", "TMO",
]

# 한 번에 몇 종목씩 부를지. REST 200회/분 제한이 있고, 한 번 부를 때
# 페이지가 여러 장 나가므로 넉넉히 잡는다.
CHUNK = 5
SLEEP_SEC = 1.0
# 3년 × 26봉 × 252일 ≈ 19,600봉/종목. 한 페이지 10,000봉이라 종목당 2장,
# 5종목이면 10장. max_pages 를 넉넉히 준다.
MAX_PAGES = 60


def main() -> int:
    n_tickers = int(sys.argv[1]) if len(sys.argv) > 1 else len(UNIVERSE)
    years = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    if not os.environ.get("ALPACA_API_KEY"):
        print("ALPACA_API_KEY 가 없습니다. `set -a && . ./.env && set +a` 먼저.",
              file=sys.stderr)
        return 1

    tickers = UNIVERSE[:n_tickers]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(years * 365))
    print(f"{len(tickers)}종목 · {start.date()} ~ {end.date()} · 15Min", flush=True)

    frames: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        got = get_bars(chunk, timeframe="15Min", start=start, end=end,
                       max_pages=MAX_PAGES)
        for tk, df in got.items():
            df = regular_hours(df).dropna(subset=["Close"])
            if len(df):
                frames[tk] = df
        print(f"  {min(i + CHUNK, len(tickers))}/{len(tickers)}종목 · "
              f"{sum(len(d) for d in frames.values()):,}봉 · "
              f"{time.time() - t0:.0f}초", flush=True)
        time.sleep(SLEEP_SEC)

    if not frames:
        print("받은 봉이 없습니다.", file=sys.stderr)
        return 1

    panel = pd.concat(
        {(f, tk): frames[tk][f] for tk in frames for f in FIELDS}, axis=1)
    panel.columns = pd.MultiIndex.from_tuples(panel.columns)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT)

    span = f"{panel.index[0]} ~ {panel.index[-1]}"
    print(f"\n저장: {OUT} · {len(frames)}종목 · {len(panel):,}행 · {span}")
    print(f"파일 크기: {OUT.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 1종목 1년으로 먼저 시험한다**

Run:
```bash
set -a && . ./.env && set +a
python scripts/fetch_intraday_panel.py 1 1
```
Expected: `저장: data/intraday_panel_15m.parquet · 1종목 · 6,5xx행` 정도. 행 수가 6,000~7,000 이면 정규장 필터가 먹은 것이다 (1년 ≈ 252일 × 26봉 = 6,552). **10,000 을 넘으면 장외 봉이 섞인 것이니 Task 1 을 다시 본다.**

- [ ] **Step 3: 백테스트 1종목 소요 시간을 잰다**

Run:
```bash
python -c "
import time, pandas as pd
from modules import trade_plan_backtest as bt
from modules.intraday_session import session_ids
p = pd.read_parquet('data/intraday_panel_15m.parquet')
tk = sorted({t for _, t in p.columns})[0]
df = pd.DataFrame({f: p[(f, tk)] for f in ['Open','High','Low','Close','Volume']}).dropna()
s = session_ids(df.index)
t0 = time.time()
out = bt.backtest_trade_plans(df, fill_window=8, hold_window=26, sessions=s, scale=1)
print(tk, len(df), '봉', f'{time.time()-t0:.1f}초', out['all']['setups'], '셋업')
"
```
Expected: 소요 시간이 출력된다. **이 숫자로 전체 규모를 정한다** — 1종목 1년이 T초면 30종목 3년은 대략 `T × 30 × 3 / 워커수` 초다. 30분을 넘으면 종목 수나 연수를 줄이고, 그 결정을 Task 5 스크립트 상단 주석에 남긴다.

- [ ] **Step 4: 전체 패널을 받는다**

Run:
```bash
set -a && . ./.env && set +a
python scripts/fetch_intraday_panel.py
```
Expected: 30종목 · 60만행 내외 · 수십 MB

- [ ] **Step 5: 캐시를 git 에서 제외한다**

`.gitignore` 의 가격 패널 캐시 항목 근처에 추가:

```
data/intraday_panel_15m.parquet
```

- [ ] **Step 6: 커밋**

```bash
git add scripts/fetch_intraday_panel.py .gitignore
git commit -m "feat: 15분봉 패널 수집 스크립트 — 정규장만, parquet 캐시"
```

---

### Task 5: 측정 스크립트

**Files:**
- Create: `scripts/measure_trade_plan_intraday.py`
- 산출물: `data/trade_plan_intraday_result.json`, `docs/measurements/2026-08-10-trade-plan-intraday.txt`

**Interfaces:**
- Consumes: `modules.trade_plan_backtest.backtest_trade_plans`(`sessions`, `scale`), `modules.intraday_session.session_ids`, `modules.stat_validation.permutation_test_trades`
- Produces: 없음 (최종 산출물)

**재는 것:**
- 설정 A(`scale=1`, 창을 봉 그대로) vs 설정 B(`scale=26`, 일수 환산) 를 나란히
- 구간 분할: `IS_START=2024-12-20` 이후(숏 레짐필터·저확신 컷을 고를 때 본 구간) vs 그 앞
- 연도별
- 순열검정 p-value (`permutation_test_trades`)
- **비용 차감**: R 은 위험 1단위 기준이라 그 자체로 비용을 못 잰다. 트레이드마다 `risk_pct = (entry_ref - stop) / entry_ref` 를 알고 있으므로, 왕복 비용 `cost_bps` 를 R 로 바꾸면 `cost_r = (cost_bps / 10000) / risk_pct` 다. 단타는 트레이드가 잦아 여기서 죽는다면 그게 답이다.

- [ ] **Step 1: 스크립트를 쓴다**

```python
#!/usr/bin/env python
"""트레이드 플랜을 **15분봉에서** 잰다 — 3단계가 성립하는지의 시험.

    python scripts/measure_trade_plan_intraday.py [워커수]

## 왜 필요한가

트레이드 플랜의 기대값 +0.69R 은 **일봉에서 잰 숫자**다. 15분봉에서 같은
규칙이 통한다는 근거는 없다. 이 저장소는 수익률 예측을 네 번 시도해 네 번
실패했고 통과한 것은 트레이드 기하학 하나뿐이다. 측정 없이 자동 주문을
붙이는 것이 피해야 할 실패 방식이다.

## 무엇을 재는가

설정 A (scale=1)   창을 봉 그대로. "5시간짜리 추세"로 해석한다.
설정 B (scale=26)  창 × 26. 15분봉에서 일봉과 같은 실제 시간을 본다.

둘 다 **당일 청산**이다 — 세션 마지막 봉(15:45 ET) 시가에 턴다. 3b 러너와
같은 규칙이라야 여기서 잰 숫자가 러너를 대표한다.

네트워크 無 — 저장 패널만 읽는다 (scripts/fetch_intraday_panel.py 로 먼저 받을 것).
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules import trade_plan_backtest as bt  # noqa: E402
from modules.intraday_session import session_ids  # noqa: E402
from modules.stat_validation import permutation_test_trades  # noqa: E402

# 1종목으로 먼저 시험할 수 있게 환경변수로 갈아끼운다.
PANEL = Path(os.environ.get("PANEL", "data/intraday_panel_15m.parquet"))
OUT_JSON = Path("data/trade_plan_intraday_result.json")
OUT_TXT = Path("docs/measurements/2026-08-10-trade-plan-intraday.txt")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]

# 당일 안에서 진입을 기다리는 최대 봉 수(2시간)와 보유 상한(하루).
# 세션 경계가 어차피 더 짧게 자르므로 상한 역할만 한다.
FILL_WINDOW = 8
HOLD_WINDOW = 26

# 15분봉 하루 정규장 봉 수. 설정 B 의 배수.
BARS_PER_DAY = 26

# 숏 레짐필터·저확신 컷을 고를 때 본 구간의 시작 (일봉 측정 기준).
IS_START = pd.Timestamp("2024-12-20")

# 왕복 거래비용(bp). 대형주 15분봉 스프레드 + 슬리피지 가정.
# 값이 바뀌면 결론이 바뀐다 — 재실행할 때 이 숫자부터 다시 볼 것.
COST_BPS = 6.0

SETTINGS = {"A_봉그대로": 1, "B_일수환산": BARS_PER_DAY}


def _ohlcv(panel: pd.DataFrame, tk: str) -> pd.DataFrame:
    return pd.DataFrame({f: panel[(f, tk)] for f in FIELDS}).dropna()


def _run_ticker(args):
    """한 종목 × 한 설정 — 진입 시각을 붙인 트레이드 목록."""
    tk, df, scale = args
    out = bt.backtest_trade_plans(
        df, fill_window=FILL_WINDOW, hold_window=HOLD_WINDOW,
        sessions=session_ids(df.index), scale=scale)
    for t in out["trades"]:
        t["ticker"] = tk
        t["entry_date"] = df.index[t["idx"]]
        # 비용을 R 로 바꾸려면 위험이 가격의 몇 %였는지가 필요하다.
        ref, stop = t["entry_ref"], t["stop_price"]
        t["risk_pct"] = abs(ref - stop) / ref if ref else float("nan")
    return out["trades"]


def _net_r(trades: list[dict]) -> list[float]:
    """비용 차감 R. 체결된 트레이드만."""
    out = []
    for t in trades:
        if t["outcome"] == "nofill":
            continue
        rp = t["risk_pct"]
        cost_r = (COST_BPS / 10000.0) / rp if rp and rp == rp else float("nan")
        out.append(t["r"] - cost_r)
    return out


def _fmt(s: dict) -> str:
    wr, ex, ar = s["win_rate"], s["expectancy_r"], s["avg_r"]
    wr = " nan" if wr != wr else f"{wr * 100:4.0f}%"
    ex = "  nan" if ex != ex else f"{ex:+5.2f}R"
    ar = "  nan" if ar != ar else f"{ar:+5.2f}R"
    return (f"setups={s['setups']:5d}  filled={s['filled']:5d}  "
            f"W/L={s['wins']:4d}/{s['losses']:4d}  eod={s.get('eod_exits', 0):4d}  "
            f"winrate={wr}  expectancy={ex}  avg={ar}")


def _block(label: str, trades: list[dict]) -> str:
    lines = [f"  {label:22} {_fmt(bt._stats(trades))}"]
    net = [r for r in _net_r(trades) if r == r]
    if net:
        lines.append(f"    └ 비용차감({COST_BPS:.0f}bp)     "
                     f"평균 {np.mean(net):+5.2f}R  (n={len(net)})")
    for direction, dlab in (("long", "롱"), ("short", "숏")):
        sub = [t for t in trades if t["direction"] == direction]
        if sub:
            lines.append(f"    └ {dlab:20} {_fmt(bt._stats(sub))}")
    return "\n".join(lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not PANEL.exists():
        print(f"패널이 없습니다: {PANEL}\n"
              f"먼저: python scripts/fetch_intraday_panel.py", file=sys.stderr)
        return 1

    workers = int(sys.argv[1]) if len(sys.argv) > 1 else max(os.cpu_count() - 1, 1)
    panel = pd.read_parquet(PANEL)
    tickers = sorted({t for _, t in panel.columns})
    frames = {tk: _ohlcv(panel, tk) for tk in tickers}

    span = f"{panel.index[0]} ~ {panel.index[-1]}"
    print(f"{len(frames)}종목 · {len(panel):,}봉 · {span} · 워커 {workers}\n",
          flush=True)

    result: dict = {"span": span, "tickers": len(frames), "cost_bps": COST_BPS,
                    "fill_window": FILL_WINDOW, "hold_window": HOLD_WINDOW,
                    "settings": {}}
    body: list[str] = [f"15분봉 트레이드 플랜 측정 · {len(frames)}종목 · {span}",
                       f"당일 청산(15:45 ET 시가) · 비용 {COST_BPS:.0f}bp 왕복", ""]

    for name, scale in SETTINGS.items():
        # 워밍업이 scale 배로 커진다. 봉이 모자란 종목은 뺀다.
        need = 60 * scale + FILL_WINDOW + HOLD_WINDOW
        tasks = [(tk, df, scale) for tk, df in frames.items() if len(df) > need]
        print(f"── 설정 {name} (scale={scale}) · {len(tasks)}종목 ──", flush=True)

        trades: list[dict] = []
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for got in pool.map(_run_ticker, tasks):
                trades += got
                done += 1
                if done % 5 == 0 or done == len(tasks):
                    print(f"  {done}/{len(tasks)}종목 · 누적 {len(trades)}건",
                          flush=True)

        oos = [t for t in trades if t["entry_date"] < IS_START]
        ins = [t for t in trades if t["entry_date"] >= IS_START]

        body += [
            f"── 설정 {name} (scale={scale}) ──",
            f"  ── 일봉 규칙을 고를 때 안 본 구간 (~{(IS_START - pd.Timedelta(days=1)).date()}) ──",
            _block("전체", oos),
            "",
            f"  ── 본 구간 ({IS_START.date()}~) ──",
            _block("전체", ins),
            "",
            "  ── 연도별 ──",
        ]
        for year in sorted({t["entry_date"].year for t in trades}):
            body.append(_block(str(year), [t for t in trades
                                           if t["entry_date"].year == year]))

        net_oos = [r for r in _net_r(oos) if r == r]
        perm = None
        if len(net_oos) >= 5:
            perm = permutation_test_trades(np.array(net_oos), seed=42)
            body += ["", f"  순열검정(OOS, 비용차감): p={perm['p_value']:.4f} "
                         f"{'유의' if perm['is_significant_95pct'] else '우연과 구분 안 됨'}"]
        body.append("")

        result["settings"][name] = {
            "scale": scale,
            "tickers": len(tasks),
            "oos": bt._stats(oos),
            "is": bt._stats(ins),
            "oos_net_avg_r": float(np.mean(net_oos)) if net_oos else None,
            "oos_net_n": len(net_oos),
            "permutation": perm,
        }

    text = "\n".join(body)
    print("\n" + text)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text(text, encoding="utf-8")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                   default=str), encoding="utf-8")
    print(f"\n저장: {OUT_TXT}\n저장: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 1종목으로 먼저 돌려 본다**

Run:
```bash
python -c "
import pandas as pd
p = pd.read_parquet('data/intraday_panel_15m.parquet')
tk = sorted({t for _, t in p.columns})[0]
p[[c for c in p.columns if c[1] == tk]].to_parquet('data/_one.parquet')
print('1종목 패널:', tk, len(p))
"
PANEL=data/_one.parquet python scripts/measure_trade_plan_intraday.py 2
```
Expected: 두 설정 모두 표가 출력되고 `data/trade_plan_intraday_result.json` 이 생긴다. 여기서 죽으면 전체를 돌리기 전에 고친다.

- [ ] **Step 3: 전체를 돌린다**

Run: `python scripts/measure_trade_plan_intraday.py`
Expected: 두 설정 표 + p-value + 저장 두 줄. Task 4 Step 3 에서 잰 시간의 대략 2배(설정 2개)가 걸린다.

- [ ] **Step 4: 임시 파일을 지우고 커밋**

```bash
rm -f data/_one.parquet
git add scripts/measure_trade_plan_intraday.py docs/measurements/2026-08-10-trade-plan-intraday.txt data/trade_plan_intraday_result.json
git commit -m "feat: 15분봉 트레이드 플랜 측정 — 두 창 설정 나란히, 비용 차감"
```

---

### Task 6: 판정 — 3b 로 갈지 여기서 멈출지

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-alpaca-intraday-engine-design.md` (결과 절 추가)

**통과 기준 (셋 다 만족해야 3b 로 간다):**

1. 두 설정 중 적어도 하나에서 **OOS 비용차감 평균 R > 0**
2. 그 설정의 **순열검정 p < 0.05**
3. **연도별로 한 해에만 몰려 있지 않다** — 2022 같은 하락장 표본이 있고 거기서 무너지지 않는다

- [ ] **Step 1: 결과를 설계서에 적는다**

`docs/superpowers/specs/2026-08-10-alpaca-intraday-engine-design.md` 끝에 `## 3a 측정 결과 (YYYY-MM-DD)` 절을 추가한다. 두 설정의 OOS 총계, 비용차감 R, p-value, 연도별 요약, 그리고 **통과/불통과 판정**을 한 줄로 적는다.

- [ ] **Step 2: 커밋하고 PR 을 연다**

```bash
git add docs/superpowers/specs/2026-08-10-alpaca-intraday-engine-design.md
git commit -m "docs: 15분봉 트레이드 플랜 측정 결과와 3b 진행 판정"
git push -u origin feat/alpaca-intraday
gh pr create --title "feat: 15분봉 트레이드 플랜 측정 (Alpaca 3단계 — 3a)" --body "$(cat <<'EOF'
일봉에서 +0.69R 을 낸 트레이드 플랜 규칙이 15분봉 단타에서도 통하는지 쟀다.

- 백테스트에 세션 경계와 당일 청산(15:45 ET 시가)을 가르쳤다
- `build_trade_plan(scale=)` — 봉 개수 창을 인자로 뺐다. `scale=1` 은 기존과 동일
- 15분봉 패널 수집 + 두 창 설정(봉 그대로 / 일수 환산) 나란히 측정, 비용 차감

**결과와 판정은 설계서 `## 3a 측정 결과` 절에 있다.**

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: 사장님께 보고한다**

숫자와 판정을 세 줄 안에 보고한다. **통과면 3b(단타 러너) 계획을 새로 쓴다. 불통과면 3b 를 만들지 않고, 대안(다른 신호, 다른 봉 크기, 단타 포기)을 놓고 사장님이 정한다.**

---

## 이 계획에 없는 것

**3b 단타 러너는 여기 없다.** 3a 가 통과해야 만든다. 러너의 모양(어느 설정을 쓸지, 진입 대기 시간, 종목 수)은 3a 가 낸 숫자가 정한다 — 지금 쓰면 측정 전에 답을 정해 놓는 것이다.

3b 착수 전 확인할 전제 하나: **페이퍼 계좌 잔여 포지션 0.** 2026-08-10 09:30 ET 개장에 매도 10건이 체결될 예정이다. 안 됐으면 단타 리스크 관리가 남의 물량을 청산한다.
