# 유니버스 확대와 가격 패널 캐시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ic_weight_updater.py`가 37종목이 아닌 S&P 500 전체(~500종목)로 10분 안에 완주하도록, 캐시 기반 가격 패널 모듈을 만들어 순차 다운로드를 대체한다.

**Architecture:** 신규 모듈 `modules/price_panel.py`(캐시 + 일괄 다운로드)와 `modules/universe.py`(티커 목록 단일화)를 만든다. `modules/factor_validator.py`에 3벌 중복된 다운로드 루프를 `load_panel()` 한 줄 호출로 교체한다. `app.py`는 건드리지 않는다.

**Tech Stack:** Python 3.12, pandas, yfinance, pyarrow(parquet), pytest

## Global Constraints

- Python **3.12**. `ic-update.yml`은 이미 3.12를 쓴다 — 다른 워크플로는 이번 범위 밖.
- 캐시 파일 경로: `data/price_panel_v1.parquet`. `data/`는 gitignore.
- 캐시 스키마 버전은 파일명(`_v1`)과 GitHub Actions cache key(`price-panel-v1-`) **양쪽**에 박는다. 스키마 변경 시 둘 다 올린다.
- `load_panel`의 반환 타입은 `tuple[dict[str, pd.Series], dict[str, pd.DataFrame]]`. 기존 루프 산출물과 동일해야 하위 로직을 손대지 않는다.
- 다운로드 성공률이 요청 티커의 **80% 미만이면 예외를 던진다.** 표본 부족 상태로 가중치를 쓰느니 실패가 낫다.
- 조용한 실패 금지. 개별 티커 실패는 수집해서 stdout에 출력한다. `except Exception: pass`를 새로 쓰지 않는다.
- 신규 코드의 주석·로그·docstring은 기존 코드와 같이 **한국어**로 쓴다.
- 테스트는 네트워크를 타지 않는다. yfinance는 목으로 대체한다.

---

### Task 1: pytest 도입과 pyarrow 의존성

이 레포에는 테스트 프레임워크가 없다. Task 2 이후가 전부 테스트로 시작하므로 먼저 깔아둔다.

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py` (빈 파일)
- Create: `tests/test_smoke.py`
- Modify: `.gitignore`

- [ ] **Step 1: requirements.txt에 의존성 추가**

파일 끝에 두 줄 추가:

```
pyarrow>=15.0
pytest>=8.0
```

- [ ] **Step 2: .gitignore에 data/ 추가**

파일 끝에 추가:

```
data/
.pytest_cache/
```

- [ ] **Step 3: 설치**

Run: `pip install -r requirements.txt`
Expected: pyarrow, pytest 설치 완료 (이미 있으면 "already satisfied")

- [ ] **Step 4: 빈 패키지 파일 생성**

`tests/__init__.py` — 내용 없는 빈 파일.

- [ ] **Step 5: 스모크 테스트 작성**

`tests/test_smoke.py`:

```python
"""pytest가 이 레포에서 동작하는지 확인하는 최소 테스트."""
import pyarrow
import pandas as pd


def test_pyarrow_roundtrip(tmp_path):
    """parquet 읽기/쓰기가 되는지 — price_panel의 전제 조건."""
    df = pd.DataFrame({"a": [1, 2, 3]}, index=pd.date_range("2026-01-01", periods=3))
    path = tmp_path / "t.parquet"
    df.to_parquet(path)
    assert pd.read_parquet(path).equals(df)
```

- [ ] **Step 6: 테스트 실행**

Run: `pytest tests/ -v`
Expected: `1 passed`

- [ ] **Step 7: 커밋**

```bash
git add requirements.txt .gitignore tests/
git commit -m "chore: pytest와 pyarrow 도입

price_panel 모듈 작업을 위한 사전 준비."
```

---

### Task 2: `price_panel` — 캐시 미스 경로 (전량 다운로드)

가장 단순한 경로부터 만든다. 캐시가 없으면 전부 받아서 기존 루프와 같은 형태로 반환.

**Files:**
- Create: `modules/price_panel.py`
- Create: `tests/test_price_panel.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `load_panel(tickers: list[str], start, end, cache_path: str | None = None) -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame]]`
  - `CACHE_PATH: str` — 기본 캐시 경로 상수 `"data/price_panel_v1.parquet"`
  - `MIN_SUCCESS_RATE: float` — `0.80`
  - `PanelCoverageError(Exception)` — 성공률 미달 시 발생
  - `last_coverage() -> dict` — 직전 `load_panel` 호출의 `{"requested": int, "resolved": int, "failed": list[str]}`

`load_panel`이 반환하는 두 dict의 계약 (기존 루프와 동일):
- 첫 번째: `{ticker: Close Series}` — NaN 제거됨
- 두 번째: `{ticker: OHLCV DataFrame}` — Close 기준 NaN 행 제거됨
- 거래일 80개 미만인 티커는 양쪽 모두에서 제외

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_price_panel.py`:

```python
"""price_panel 모듈 테스트. 네트워크를 타지 않고 yfinance를 목으로 대체한다."""
import pandas as pd
import numpy as np
import pytest

from modules import price_panel


def _fake_ohlcv(tickers, n_days=200):
    """yf.download가 여러 티커에 대해 돌려주는 MultiIndex 컬럼 형태를 흉내낸다."""
    idx = pd.date_range("2025-01-01", periods=n_days, freq="B")
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], tickers]
    )
    rng = np.random.default_rng(0)
    data = rng.uniform(90, 110, size=(n_days, len(cols)))
    return pd.DataFrame(data, index=idx, columns=cols)


def test_cache_miss_downloads_and_returns_both_dicts(tmp_path, monkeypatch):
    """캐시가 없으면 다운로드하고, 기존 루프와 같은 형태의 두 dict를 반환한다."""
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        return _fake_ohlcv(list(tickers))

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    prices, ohlcv = price_panel.load_panel(
        ["AAPL", "MSFT"],
        start="2025-01-01",
        end="2025-10-01",
        cache_path=str(tmp_path / "p.parquet"),
    )

    assert len(calls) == 1
    assert set(prices) == {"AAPL", "MSFT"}
    assert set(ohlcv) == {"AAPL", "MSFT"}
    assert isinstance(prices["AAPL"], pd.Series)
    assert "Close" in ohlcv["AAPL"].columns
    assert not prices["AAPL"].isna().any()
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_price_panel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.price_panel'`

- [ ] **Step 3: 최소 구현**

`modules/price_panel.py`:

```python
"""
가격 패널 캐시
==============
여러 티커의 OHLCV를 일괄 다운로드하고 parquet에 캐시한다.
factor_validator.py의 종목별 순차 다운로드(1분/종목)를 대체하기 위한 모듈.

yfinance와 pandas만 안다. 팩터 로직·Streamlit·워크플로에 의존하지 않는다.
"""
import os
import pandas as pd
import yfinance as yf

CACHE_PATH       = "data/price_panel_v1.parquet"
MIN_SUCCESS_RATE = 0.80
CHUNK_SIZE       = 100
MIN_TRADING_DAYS = 80
FIELDS           = ["Open", "High", "Low", "Close", "Volume"]

_last_coverage = {"requested": 0, "resolved": 0, "failed": []}


class PanelCoverageError(Exception):
    """요청 티커 대비 확보율이 MIN_SUCCESS_RATE 미만일 때 발생."""


def last_coverage() -> dict:
    """직전 load_panel 호출의 커버리지. ic_weights.json 기록용."""
    return dict(_last_coverage)


def _download_chunked(tickers: list, start, end) -> pd.DataFrame:
    """티커를 CHUNK_SIZE씩 끊어 일괄 다운로드하고 wide DataFrame으로 합친다."""
    frames = []
    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[i:i + CHUNK_SIZE]
        raw = yf.download(
            chunk, start=start, end=end,
            progress=False, auto_adjust=True, threads=True,
            group_by="column",
        )
        if raw is None or raw.empty:
            continue
        if not isinstance(raw.columns, pd.MultiIndex):
            # 티커 1개일 때 yfinance는 평면 컬럼을 준다 — MultiIndex로 승격
            raw.columns = pd.MultiIndex.from_product([raw.columns, chunk])
        frames.append(raw)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)


def _split_panel(panel: pd.DataFrame, tickers: list) -> tuple:
    """wide 패널을 기존 루프와 같은 (prices_dict, ohlcv_dict)로 분해한다."""
    prices_dict, ohlcv_dict = {}, {}
    available = set(panel.columns.get_level_values(1)) if not panel.empty else set()

    for tk in tickers:
        if tk not in available:
            continue
        try:
            sub = panel.xs(tk, axis=1, level=1)
        except KeyError:
            continue
        if "Close" not in sub.columns:
            continue
        sub = sub.dropna(subset=["Close"])
        if len(sub) < MIN_TRADING_DAYS:
            continue
        prices_dict[tk] = sub["Close"].dropna()
        ohlcv_dict[tk]  = sub
    return prices_dict, ohlcv_dict


def load_panel(tickers: list, start, end, cache_path: str = None) -> tuple:
    """
    tickers의 OHLCV를 반환한다.

    Returns
    -------
    (prices_dict, ohlcv_dict)
        prices_dict : {ticker: Close Series}
        ohlcv_dict  : {ticker: OHLCV DataFrame}
        거래일 MIN_TRADING_DAYS 미만인 티커는 양쪽에서 제외된다.

    Raises
    ------
    PanelCoverageError : 확보율이 MIN_SUCCESS_RATE 미만
    """
    global _last_coverage
    cache_path = cache_path or CACHE_PATH
    tickers    = list(dict.fromkeys(tickers))  # 중복 제거, 순서 유지

    panel = _download_chunked(tickers, start, end)
    prices_dict, ohlcv_dict = _split_panel(panel, tickers)

    failed = [t for t in tickers if t not in prices_dict]
    _last_coverage = {
        "requested": len(tickers),
        "resolved":  len(prices_dict),
        "failed":    failed,
    }

    print(f"[price_panel] 확보 {len(prices_dict)}/{len(tickers)}종목")
    if failed:
        print(f"[price_panel] 실패 {len(failed)}종목: {', '.join(failed[:20])}"
              + (" ..." if len(failed) > 20 else ""))

    if tickers and len(prices_dict) / len(tickers) < MIN_SUCCESS_RATE:
        raise PanelCoverageError(
            f"데이터 확보율 {len(prices_dict)}/{len(tickers)} "
            f"({len(prices_dict) / len(tickers):.0%}) < {MIN_SUCCESS_RATE:.0%}"
        )

    return prices_dict, ohlcv_dict
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_price_panel.py -v`
Expected: `1 passed`

- [ ] **Step 5: 커밋**

```bash
git add modules/price_panel.py tests/test_price_panel.py
git commit -m "feat: price_panel 모듈 — 일괄 다운로드 경로

종목별 순차 다운로드를 대체할 모듈의 첫 단계.
캐시 미스 시 청크 단위 일괄 다운로드 후 기존 루프와
동일한 (prices_dict, ohlcv_dict) 형태로 반환한다."
```

---

### Task 3: 확보율 미달 시 예외

**Files:**
- Modify: `tests/test_price_panel.py`

**Interfaces:**
- Consumes: Task 2의 `load_panel`, `PanelCoverageError`, `MIN_SUCCESS_RATE`
- Produces: 없음 (Task 2 구현의 검증)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_price_panel.py` 끝에 추가:

```python
def test_low_coverage_raises(tmp_path, monkeypatch):
    """확보율이 80% 미만이면 조용히 넘어가지 않고 예외를 던진다."""
    def fake_download(tickers, **kwargs):
        # 10개 요청 중 2개만 응답 → 20%
        return _fake_ohlcv(["AAPL", "MSFT"])

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    with pytest.raises(price_panel.PanelCoverageError):
        price_panel.load_panel(
            [f"T{i}" for i in range(8)] + ["AAPL", "MSFT"],
            start="2025-01-01", end="2025-10-01",
            cache_path=str(tmp_path / "p.parquet"),
        )


def test_coverage_is_recorded(tmp_path, monkeypatch):
    """실패한 티커 목록이 last_coverage()로 조회된다."""
    def fake_download(tickers, **kwargs):
        return _fake_ohlcv(["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"])

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    price_panel.load_panel(
        ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "DEAD"],
        start="2025-01-01", end="2025-10-01",
        cache_path=str(tmp_path / "p.parquet"),
    )
    cov = price_panel.last_coverage()
    assert cov["requested"] == 6
    assert cov["resolved"] == 5
    assert cov["failed"] == ["DEAD"]
```

- [ ] **Step 2: 실행**

Run: `pytest tests/test_price_panel.py -v`
Expected: `3 passed` — Task 2 구현이 이미 이 동작을 포함하므로 통과해야 한다. 실패하면 Task 2의 `load_panel` 말미 예외 로직과 `_last_coverage` 대입을 확인할 것.

- [ ] **Step 3: 커밋**

```bash
git add tests/test_price_panel.py
git commit -m "test: 확보율 미달 예외와 커버리지 기록 검증"
```

---

### Task 4: 캐시 쓰기와 히트 경로

**Files:**
- Modify: `modules/price_panel.py`
- Modify: `tests/test_price_panel.py`

**Interfaces:**
- Consumes: Task 2의 `load_panel`, `_download_chunked`, `_split_panel`, `CACHE_PATH`
- Produces:
  - `_read_cache(path: str) -> pd.DataFrame` — 없거나 손상이면 빈 DataFrame
  - `_write_cache(panel: pd.DataFrame, path: str) -> None` — tmp → replace 원자적 쓰기

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_price_panel.py` 끝에 추가:

```python
def test_cache_hit_skips_download(tmp_path, monkeypatch):
    """같은 요청을 두 번 하면 두 번째는 네트워크를 타지 않는다."""
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        return _fake_ohlcv(list(tickers))

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    args = dict(start="2025-01-01", end="2025-10-01", cache_path=cache)
    price_panel.load_panel(["AAPL", "MSFT"], **args)
    assert len(calls) == 1

    prices, ohlcv = price_panel.load_panel(["AAPL", "MSFT"], **args)
    assert len(calls) == 1, "캐시 히트인데 다운로드가 발생했다"
    assert set(prices) == {"AAPL", "MSFT"}


def test_corrupt_cache_rebuilds(tmp_path, monkeypatch):
    """손상된 캐시 파일은 예외 없이 재구축된다."""
    cache = tmp_path / "p.parquet"
    cache.write_bytes(b"this is not parquet")

    monkeypatch.setattr(
        price_panel.yf, "download",
        lambda tickers, **kw: _fake_ohlcv(list(tickers)),
    )

    prices, _ = price_panel.load_panel(
        ["AAPL", "MSFT"], start="2025-01-01", end="2025-10-01",
        cache_path=str(cache),
    )
    assert set(prices) == {"AAPL", "MSFT"}
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_price_panel.py::test_cache_hit_skips_download -v`
Expected: FAIL — `AssertionError: 캐시 히트인데 다운로드가 발생했다` (아직 캐시를 안 쓴다)

- [ ] **Step 3: 캐시 입출력 함수 추가**

`modules/price_panel.py`의 `_split_panel` 아래에 추가:

```python
def _read_cache(path: str) -> pd.DataFrame:
    """캐시를 읽는다. 없거나 손상됐으면 빈 DataFrame을 돌려주고 경고만 남긴다."""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        if not isinstance(df.columns, pd.MultiIndex) or df.empty:
            print(f"[price_panel] 캐시 스키마 불일치 — 재구축: {path}")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception as e:
        print(f"[price_panel] 캐시 손상 — 재구축: {e}")
        return pd.DataFrame()


def _write_cache(panel: pd.DataFrame, path: str) -> None:
    """원자적으로 캐시를 기록한다 (tmp 파일에 쓴 뒤 교체)."""
    if panel.empty:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    panel.to_parquet(tmp)
    os.replace(tmp, path)
```

- [ ] **Step 4: `load_panel`이 캐시를 쓰도록 교체**

`load_panel` 본문에서 아래 두 줄을

```python
    panel = _download_chunked(tickers, start, end)
    prices_dict, ohlcv_dict = _split_panel(panel, tickers)
```

이렇게 바꾼다:

```python
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    cached  = _read_cache(cache_path)
    missing = _missing_tickers(cached, tickers, start_ts, end_ts)

    if missing:
        fresh = _download_chunked(missing, start, end)
        cached = _merge_panels(cached, fresh)
        _write_cache(cached, cache_path)
    else:
        print(f"[price_panel] 캐시 히트 — 다운로드 없음 ({len(tickers)}종목)")

    window = cached.loc[(cached.index >= start_ts) & (cached.index <= end_ts)] \
        if not cached.empty else cached
    prices_dict, ohlcv_dict = _split_panel(window, tickers)
```

- [ ] **Step 5: 결손분 판별과 병합 함수 추가**

`_write_cache` 아래에 추가:

```python
def _missing_tickers(cached: pd.DataFrame, tickers: list,
                     start_ts, end_ts) -> list:
    """캐시로 충족되지 않는 티커 목록. 지금은 티커 단위로만 판별한다."""
    if cached.empty:
        return list(tickers)
    have = set(cached.columns.get_level_values(1))
    return [t for t in tickers if t not in have]


def _merge_panels(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """기존 패널에 신규 패널을 덮어쓰며 합친다. 겹치는 셀은 new 우선."""
    if old.empty:
        return new
    if new.empty:
        return old
    merged = new.combine_first(old)
    return merged.sort_index()
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_price_panel.py -v`
Expected: `5 passed`

- [ ] **Step 7: 커밋**

```bash
git add modules/price_panel.py tests/test_price_panel.py
git commit -m "feat: price_panel parquet 캐시

캐시 히트 시 네트워크 호출 없음. 손상된 캐시는 경고 후 재구축.
쓰기는 tmp → replace 원자적 교체."
```

---

### Task 5: 증분 갱신 — 신규 티커와 날짜 연장

Task 4의 `_missing_tickers`는 티커 단위로만 본다. 날짜 범위가 늘어난 경우를 처리한다.

**Files:**
- Modify: `modules/price_panel.py`
- Modify: `tests/test_price_panel.py`

**Interfaces:**
- Consumes: Task 4의 `_missing_tickers`, `_merge_panels`, `_read_cache`, `_write_cache`
- Produces: `_missing_tickers`를 `(missing_tickers, needs_date_extension)` 튜플 반환으로 변경

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_price_panel.py` 끝에 추가:

```python
def test_new_ticker_downloads_only_that_ticker(tmp_path, monkeypatch):
    """티커를 추가하면 신규 종목만 요청한다."""
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(sorted(tickers))
        return _fake_ohlcv(list(tickers))

    monkeypatch.setattr(price_panel.yf, "download", fake_download)
    args = dict(start="2025-01-01", end="2025-10-01", cache_path=cache)

    price_panel.load_panel(["AAPL", "MSFT"], **args)
    price_panel.load_panel(["AAPL", "MSFT", "NVDA"], **args)

    assert calls[1] == ["NVDA"], f"신규 종목만 받아야 하는데 {calls[1]}"


def test_extended_end_date_refetches(tmp_path, monkeypatch):
    """요청 종료일이 캐시 최종일보다 뒤면 다시 받는다."""
    cache = str(tmp_path / "p.parquet")
    calls = []

    def fake_download(tickers, **kwargs):
        # 실제 yfinance처럼 요청된 end까지만 돌려준다.
        # 요청 범위를 넘겨 반환하면 캐시가 과도하게 채워져 테스트가 무의미해진다.
        calls.append(kwargs.get("end"))
        full = _fake_ohlcv(list(tickers), n_days=600)
        return full.loc[full.index <= pd.Timestamp(kwargs["end"])]

    monkeypatch.setattr(price_panel.yf, "download", fake_download)

    price_panel.load_panel(["AAPL", "MSFT"], start="2025-01-01",
                           end="2025-06-01", cache_path=cache)
    price_panel.load_panel(["AAPL", "MSFT"], start="2025-01-01",
                           end="2026-06-01", cache_path=cache)

    assert len(calls) == 2, "날짜가 연장됐는데 다운로드가 없었다"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_price_panel.py::test_extended_end_date_refetches -v`
Expected: FAIL — `AssertionError: 날짜가 연장됐는데 다운로드가 없었다`

- [ ] **Step 3: `_missing_tickers`를 날짜 인식하도록 교체**

`modules/price_panel.py`의 `_missing_tickers` 전체를 아래로 교체:

```python
def _missing_tickers(cached: pd.DataFrame, tickers: list,
                     start_ts, end_ts) -> tuple:
    """
    캐시로 충족되지 않는 부분을 판별한다.

    Returns
    -------
    (missing, needs_extension)
        missing         : 캐시에 아예 없는 티커 (전 기간 다운로드 필요)
        needs_extension : 캐시 날짜 범위가 요청을 못 덮음 (전 티커 재요청 필요)
    """
    if cached.empty:
        return list(tickers), False

    have    = set(cached.columns.get_level_values(1))
    missing = [t for t in tickers if t not in have]

    # yfinance는 거래일만 준다. 달력일 기준 여유 3일을 둔다.
    tol = pd.Timedelta(days=3)
    needs_extension = (
        cached.index.max() < end_ts - tol
        or cached.index.min() > start_ts + tol
    )
    return missing, needs_extension
```

- [ ] **Step 4: `load_panel`의 호출부 수정**

`load_panel` 안의

```python
    missing = _missing_tickers(cached, tickers, start_ts, end_ts)

    if missing:
        fresh = _download_chunked(missing, start, end)
```

를 아래로 교체:

```python
    missing, needs_extension = _missing_tickers(cached, tickers, start_ts, end_ts)

    to_fetch = tickers if needs_extension else missing
    if to_fetch:
        fresh = _download_chunked(to_fetch, start, end)
```

그 아래 `cached = _merge_panels(cached, fresh)` 이하는 그대로 둔다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_price_panel.py -v`
Expected: `7 passed`

- [ ] **Step 6: 커밋**

```bash
git add modules/price_panel.py tests/test_price_panel.py
git commit -m "feat: price_panel 증분 갱신

신규 티커는 그 종목만, 날짜 범위 연장 시에는 전 종목 재요청.
거래일/달력일 차이를 감안해 3일 허용오차를 둔다."
```

---

### Task 6: `universe` 모듈 — 티커 목록 단일화

**Files:**
- Create: `modules/universe.py`
- Create: `scripts/audit_universe.py`
- Create: `tests/test_universe.py`

**Interfaces:**
- Consumes: Task 2의 `load_panel` (감사 스크립트에서 사용)
- Produces: `modules.universe.SP500: list[str]`

- [ ] **Step 1: 사멸 티커 감사 스크립트 작성**

스펙에서 확정을 구현 시점으로 미룬 부분이다. `app.py:3124`의 500종목 프리셋을 실제로 조회해 응답 없는 티커를 뽑는다.

`scripts/audit_universe.py`:

```python
"""
app.py의 'S&P 500 전체' 프리셋에서 데이터가 안 나오는 티커를 찾아낸다.
modules/universe.py의 SP500 목록을 확정할 때 1회 실행하는 도구.

  python scripts/audit_universe.py
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")
import yfinance as yf


def main():
    import app  # noqa: E402 — Streamlit 앱이지만 import 시 UI는 뜨지 않는다
    tickers = app.UNIVERSE_PRESETS["S&P 500 전체 (500종목)"]
    print(f"프리셋 티커 {len(tickers)}개 조회 중...")

    end   = datetime.now()
    start = end - timedelta(days=400)
    raw   = yf.download(tickers, start=start, end=end,
                        progress=False, auto_adjust=True, threads=True)

    close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
    dead  = [t for t in tickers if t not in close.columns or close[t].dropna().empty]

    print(f"\n응답 없는 티커 {len(dead)}개:")
    for t in dead:
        print(f"  {t}")
    print("\n이 목록을 근거로 modules/universe.py의 SP500을 확정할 것.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 감사 스크립트 실행**

Run: `python scripts/audit_universe.py`
Expected: 응답 없는 티커 목록 출력. 스펙에 적힌 `ATVI`, `FISV`, `PXD`, `KSU`, `FBHS`가 포함되어야 한다. 중복 티커도 눈에 띌 수 있다 (프리셋에 `ANSS` 등이 두 번 나온다).

**출력 결과를 다음 단계에 그대로 반영할 것.** 스펙의 5개는 확인된 것일 뿐 전부가 아니다.

- [ ] **Step 3: `modules/universe.py` 작성**

`app.py:3124`의 `'S&P 500 전체 (500종목)'` 리스트를 복사해 오되, Step 2에서 나온 사멸 티커를 제거하고 사명 변경분을 교체한다. 확인된 교정:

| 원본 | 조치 | 사유 |
|---|---|---|
| `ATVI` | 제거 | MSFT 피인수 (2023) |
| `FISV` | `FI`로 교체 | 사명 변경 |
| `PXD` | 제거 | XOM 피인수 (2024) |
| `KSU` | 제거 | CP 합병 (2021) |
| `FBHS` | `FBIN`으로 교체 | 분할 |

파일 구조:

```python
"""
분석 유니버스 정의
==================
티커 목록이 app.py·paper_trade_runner*.py·ic_weight_updater.py에
흩어져 있던 것을 여기로 모은다.

주의: 이 목록은 *현재* 상장 종목이다. 과거 시점 구성종목이 아니므로
과거 데이터 분석에는 생존자 편향이 있다 (survivorship_check 참조).
"""

SP500 = [
    # 정보기술
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "INTU", "AMD", "QCOM",
    # ... app.py 프리셋의 나머지를 섹터 주석과 함께 옮긴다 ...
]

# 중복 방지 — 프리셋에는 같은 티커가 두 번 등장하는 섹터가 있었다
SP500 = list(dict.fromkeys(SP500))
```

- [ ] **Step 4: 테스트 작성**

`tests/test_universe.py`:

```python
"""universe 모듈 테스트. 네트워크를 타지 않는다."""
from modules import universe


def test_no_duplicates():
    """같은 티커가 두 번 들어가면 IC 계산에서 가중치가 왜곡된다."""
    assert len(universe.SP500) == len(set(universe.SP500))


def test_size_is_plausible():
    """S&P 500 규모여야 한다. 크게 벗어나면 목록이 깨진 것."""
    assert 400 <= len(universe.SP500) <= 520


def test_known_dead_tickers_removed():
    """감사에서 확인된 사멸 티커가 남아 있으면 안 된다."""
    for dead in ("ATVI", "FISV", "PXD", "KSU", "FBHS"):
        assert dead not in universe.SP500, f"{dead}는 제거됐어야 한다"


def test_replacements_present():
    """사명 변경·분할 후 티커가 들어 있어야 한다."""
    for alive in ("FI", "FBIN"):
        assert alive in universe.SP500
```

- [ ] **Step 5: 테스트 실행**

Run: `pytest tests/test_universe.py -v`
Expected: `4 passed`

- [ ] **Step 6: 커밋**

```bash
git add modules/universe.py scripts/audit_universe.py tests/test_universe.py
git commit -m "feat: universe 모듈 — S&P 500 티커 목록 단일화

app.py 프리셋의 사멸 티커(ATVI, PXD, KSU 등) 제거,
사명 변경분(FISV→FI, FBHS→FBIN) 반영, 중복 제거.
확정 근거는 scripts/audit_universe.py 실행 결과."
```

---

### Task 7: `factor_validator`의 중복 루프 3벌 교체

**Files:**
- Modify: `modules/factor_validator.py:170-183` (`run_ic_analysis`)
- Modify: `modules/factor_validator.py:386-400` (`run_per_factor_ic_analysis`)
- Modify: `modules/factor_validator.py:512-528` (`run_out_of_sample_validation`)

**Interfaces:**
- Consumes: Task 2·4·5의 `price_panel.load_panel`
- Produces: 없음 (내부 교체)

행 번호는 이전 편집으로 밀릴 수 있다. 함수 이름으로 위치를 찾을 것.

- [ ] **Step 1: import 추가**

`modules/factor_validator.py` 상단 import 블록(26~27행의 `factor_engine` import 근처)에 추가:

```python
from modules.price_panel import load_panel
```

- [ ] **Step 2: `run_ic_analysis`의 루프 교체**

아래 블록을

```python
    prices_dict = {}
    ohlcv_dict  = {}
    for i, tk in enumerate(tickers):
        if progress_cb:
            progress_cb(i / len(tickers) * 0.40)
        try:
            raw = yf.download(tk, start=start, end=end, progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw = raw.xs(tk, axis=1, level=1) if tk in raw.columns.get_level_values(1) else raw
                raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
            if not raw.empty and "Close" in raw.columns and len(raw) >= 80:
                prices_dict[tk] = raw["Close"].dropna()
                ohlcv_dict[tk]  = raw.dropna(subset=["Close"])
        except Exception:
            pass
```

이렇게 바꾼다:

```python
    if progress_cb:
        progress_cb(0.10)
    prices_dict, ohlcv_dict = load_panel(tickers, start, end)
    if progress_cb:
        progress_cb(0.40)
```

- [ ] **Step 3: `run_per_factor_ic_analysis`의 루프 교체**

동일한 형태의 블록이다. Step 2와 같은 코드로 교체한다 (진행률 값도 동일: 0.10 → 0.40).

- [ ] **Step 4: `run_out_of_sample_validation`의 루프 교체**

이 블록은 변수명이 다르다. 아래를

```python
    prices_dict = {}
    ohlcv_dict  = {}
    for i, tk in enumerate(tickers):
        if progress_cb:
            progress_cb(0.05 + i / len(tickers) * 0.30)
        try:
            raw = yf.download(tk, start=train_start_str, end=test_end_str,
                              progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw = raw.xs(tk, axis=1, level=1) if tk in raw.columns.get_level_values(1) else raw
                raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
            if not raw.empty and "Close" in raw.columns and len(raw) >= 80:
                prices_dict[tk] = raw["Close"].dropna()
                ohlcv_dict[tk]  = raw.dropna(subset=["Close"])
        except Exception:
            pass
```

이렇게 바꾼다:

```python
    prices_dict, ohlcv_dict = load_panel(tickers, train_start_str, test_end_str)
    if progress_cb:
        progress_cb(0.35)
```

- [ ] **Step 5: 소규모 실동작 확인 (네트워크 사용)**

Run:

```bash
python -c "
from modules.factor_validator import run_per_factor_ic_analysis
r = run_per_factor_ic_analysis(['AAPL','MSFT','NVDA','GOOGL','AMZN','META','JPM','XOM','JNJ','PG'], lookback_years=2)
for f, s in r.items():
    print(f, s)
"
```

Expected: `[price_panel] 확보 10/10종목` 출력 후, `mom_3m`·`mom_1m`·`low_vol`·`ict`는 `n > 0`, `value`·`quality`는 `n: 0`. 1분 이내 완료. 두 번째 실행은 `[price_panel] 캐시 히트` 출력과 함께 수 초 내 완료.

- [ ] **Step 6: 전체 테스트 실행**

Run: `pytest tests/ -v`
Expected: `12 passed` (스모크 1 + price_panel 7 + universe 4)

- [ ] **Step 7: 커밋**

```bash
git add modules/factor_validator.py
git commit -m "refactor: factor_validator의 중복 다운로드 루프 3벌을 load_panel로 교체

종목별 순차 다운로드(1분/종목)가 청크 일괄 다운로드로 바뀐다.
except Exception: pass 3곳 제거 — 실패 티커는 이제 집계·출력된다."
```

---

### Task 8: 측정되지 않은 팩터를 가중치에서 제외

스펙의 "펀더멘털 팩터 처리" 결정을 구현한다. **실거래 시그널이 바뀌는 변경이다.**

**Files:**
- Modify: `ic_weight_updater.py:33-47` (`derive_ic_regime_weights`)
- Create: `tests/test_ic_weights.py`

**Interfaces:**
- Consumes: 없음
- Produces: `derive_ic_regime_weights(per_factor_ic: dict, base_weights: dict, unavailable: list = None) -> dict`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_ic_weights.py`:

```python
"""IC 가중치 도출 로직 테스트."""
import pytest
from ic_weight_updater import derive_ic_regime_weights

BASE = {
    "bull":    {"mom_3m": 0.35, "mom_1m": 0.10, "low_vol": 0.10,
                "value": 0.15, "quality": 0.15, "ict": 0.15},
    "bear":    {"mom_3m": 0.15, "mom_1m": 0.05, "low_vol": 0.30,
                "value": 0.20, "quality": 0.20, "ict": 0.10},
}

# value/quality는 PIT 재무 미확보로 n=0
IC = {
    "mom_3m":  {"mean_ic": 0.0202, "n": 58},
    "mom_1m":  {"mean_ic": 0.0056, "n": 58},
    "low_vol": {"mean_ic": -0.0692, "n": 58},
    "value":   {"mean_ic": 0.0, "n": 0},
    "quality": {"mean_ic": 0.0, "n": 0},
    "ict":     {"mean_ic": 0.0054, "n": 58},
}


def test_unavailable_factors_get_zero_weight():
    """IC가 측정되지 않은 팩터에는 자본을 배분하지 않는다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=["value", "quality"])
    for regime in BASE:
        assert w[regime]["value"] == 0.0
        assert w[regime]["quality"] == 0.0


def test_remaining_weights_renormalize_to_one():
    """제외 후 나머지 팩터의 합이 1이 되어야 한다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=["value", "quality"])
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)


def test_no_unavailable_matches_old_behavior():
    """제외할 팩터가 없으면 기존처럼 전 팩터에 배분한다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=[])
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)
        assert w[regime]["value"] > 0


def test_all_unavailable_falls_back_to_base():
    """전 팩터가 미측정이면 0으로 나누지 않고 기본 가중치를 쓴다."""
    w = derive_ic_regime_weights(IC, BASE, unavailable=list(IC.keys()))
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_ic_weights.py -v`
Expected: FAIL — `TypeError: derive_ic_regime_weights() got an unexpected keyword argument 'unavailable'`

- [ ] **Step 3: `derive_ic_regime_weights` 교체**

`ic_weight_updater.py`의 함수 전체를 아래로 교체:

```python
def derive_ic_regime_weights(per_factor_ic: dict, base_weights: dict,
                             unavailable: list = None) -> dict:
    """
    기존 REGIME_WEIGHTS를 per_factor_ic로 스케일링 후 재정규화.
    IC가 낮은 팩터는 가중치 감소, 높은 팩터는 증가.

    unavailable에 든 팩터(IC 미측정, n=0)는 가중치 0으로 제외하고
    나머지를 재정규화한다. 측정되지 않은 신호에 자본을 배분하지 않기 위함.
    """
    unavailable = set(unavailable or [])
    ic_regime_weights = {}

    for regime, bw in base_weights.items():
        usable = {f: b for f, b in bw.items() if f not in unavailable}

        # 전 팩터가 미측정이면 나눌 것이 없다 — 기본 가중치로 후퇴
        if not usable:
            total = sum(bw.values()) or 1.0
            ic_regime_weights[regime] = {f: round(v / total, 4) for f, v in bw.items()}
            continue

        scaled = {}
        for factor, base in usable.items():
            ic_val = max(per_factor_ic.get(factor, {}).get("mean_ic", IC_FLOOR), IC_FLOOR)
            scaled[factor] = base * ic_val

        total = sum(scaled.values())
        if total <= 0:
            total = 1.0
        weights = {f: round(v / total, 4) for f, v in scaled.items()}
        for f in unavailable:
            if f in bw:
                weights[f] = 0.0
        ic_regime_weights[regime] = weights

    return ic_regime_weights
```

- [ ] **Step 4: 호출부에 `unavailable` 전달**

`ic_weight_updater.py`의 `main()`에서 `ic_rw = derive_ic_regime_weights(per_factor, REGIME_WEIGHTS)` 한 줄을 찾아 아래 두 줄로 바꾼다:

```python
    unavailable = [f for f, s in per_factor.items() if s.get("n", 0) == 0]
    ic_rw = derive_ic_regime_weights(per_factor, REGIME_WEIGHTS,
                                     unavailable=unavailable)
```

`main()` 아래쪽에 `ic_unavailable_factors`를 따로 계산하는 코드가 이미 있다 (`ic_weights.json`에 이 키가 기록되고 있다). 그 계산을 지우고 방금 만든 `unavailable` 변수를 쓰도록 통일한다 — 같은 값을 두 번 계산하면 나중에 한쪽만 바뀐다.

- [ ] **Step 5: `ic_data_note` 문구 수정**

`caveats`의 `ic_data_note`가 "기본 REGIME_WEIGHTS 비례 배분 적용중"이라고 되어 있는데 실제 동작과 다르다. 아래 문구로 교체:

```python
f"IC 미계산 팩터 {unavailable}: PIT 재무 데이터 미확보 → "
f"가중치 0으로 제외하고 나머지 팩터를 재정규화했습니다. "
f"SimFin·EDGAR 연동 시 해결 가능."
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_ic_weights.py -v`
Expected: `4 passed`

- [ ] **Step 7: 커밋**

```bash
git add ic_weight_updater.py tests/test_ic_weights.py
git commit -m "fix: IC 미측정 팩터를 가중치에서 제외

value/quality는 PIT 재무 미확보로 n=0인데도 IC_FLOOR 덕에
하락장 가중치의 31%를 차지하고 있었다. 0으로 제외하고 나머지를
재정규화한다. ic_data_note 문구도 실제 동작에 맞게 수정.

실거래 시그널이 바뀌는 변경 — 하락장 종목 선정이 달라진다."
```

---

### Task 9: 유니버스 500종목으로 전환

**Files:**
- Modify: `ic_weight_updater.py:19-25` (`TICKERS`)
- Modify: `ic_weight_updater.py` (`ic_weights.json` 기록부)

**Interfaces:**
- Consumes: Task 6의 `modules.universe.SP500`, Task 2의 `price_panel.last_coverage`
- Produces: 없음

- [ ] **Step 1: 하드코딩 목록 제거**

`ic_weight_updater.py`의 아래 블록을

```python
# 대표 유니버스 (다양한 섹터 포함)
TICKERS = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","V","JNJ",
    "UNH","XOM","PG","HD","MA","ABBV","MRK","KO","PEP","COST",
    "AVGO","LLY","WMT","MCD","CRM","ADBE","CSCO","ACN","TMO",
    "AMD","INTC","QCOM","NFLX","AMAT","MU","TXN","KLAC",
]
```

이렇게 바꾼다:

```python
from modules.universe import SP500

# 분석 유니버스 — S&P 500 전체.
# 37종목으로는 팩터의 크로스섹션 분산이 부족해 IC 통계가 의미를 갖기 어렵다.
TICKERS = SP500
```

`from modules.universe import SP500`은 파일 상단 import 블록으로 옮긴다.

- [ ] **Step 2: 커버리지를 `ic_weights.json`에 기록**

`ic_weights.json`을 만드는 dict에 키를 추가한다. `"universe_size": len(TICKERS)` 옆에:

```python
        "coverage": price_panel.last_coverage(),
```

파일 상단에 `from modules import price_panel` 추가.

- [ ] **Step 3: 소규모로 배선 확인**

전체 500종목은 오래 걸리므로 먼저 30종목으로 경로를 확인한다.

Run:

```bash
python -c "
import ic_weight_updater as u
from modules import price_panel
u.TICKERS = u.SP500[:30]
u.LOOKBACK_YEARS = 2
u.main()
" 2>&1 | tail -40
```

Expected: `[price_panel] 확보 30/30종목` → IC 표 출력 → `value`/`quality`가 가중치 표에서 `0.0000` → `ic_weights.json` 기록 완료.

- [ ] **Step 4: `ic_weights.json` 확인**

Run:

```bash
python -c "
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
d = json.load(open('ic_weights.json', encoding='utf-8'))
print('universe_size:', d['universe_size'])
print('coverage:', d['coverage'])
for r, w in d['regime_weights'].items():
    print(r, w)
"
```

Expected: `coverage`에 `requested`/`resolved`/`failed`가 있고, 전 레짐에서 `value`·`quality`가 `0.0`, 나머지 합이 1.0.

- [ ] **Step 5: 이 시험 실행이 만든 `ic_weights.json` 되돌리기**

30종목 2년짜리 결과를 커밋하면 안 된다.

Run: `git checkout ic_weights.json`

- [ ] **Step 6: 커밋**

```bash
git add ic_weight_updater.py
git commit -m "feat: IC 분석 유니버스를 37종목에서 S&P 500 전체로 확대

modules.universe.SP500을 단일 출처로 사용.
데이터 확보 커버리지를 ic_weights.json에 기록한다."
```

---

### Task 10: 워크플로에 캐시 연동

**Files:**
- Modify: `.github/workflows/ic-update.yml`

**Interfaces:**
- Consumes: Task 4의 `CACHE_PATH` (`data/price_panel_v1.parquet`)
- Produces: 없음

- [ ] **Step 1: 캐시 스텝 추가**

`.github/workflows/ic-update.yml`의 "의존성 설치" 스텝 **뒤**, "IC 가중치 계산 및 저장" 스텝 **앞**에 삽입:

```yaml
      - name: 가격 패널 캐시 복원
        uses: actions/cache@v4
        with:
          path: data/
          # 캐시 키는 불변이라 덮어쓸 수 없다. run_id로 매번 새로 저장하고
          # restore-keys 접두사로 직전 캐시를 복원하는 표준 패턴.
          # v1은 패널 스키마 버전 — 구조 변경 시 올려서 전량 재구축을 강제한다.
          key: price-panel-v1-${{ github.run_id }}
          restore-keys: |
            price-panel-v1-
```

- [ ] **Step 2: 타임아웃 조정**

`timeout-minutes: 120`의 주석을 실제에 맞게 고친다:

```yaml
    timeout-minutes: 120  # 캐시 미스 시 첫 실행이 오래 걸린다 (500종목 전량 다운로드)
```

- [ ] **Step 3: YAML 문법 확인**

Run:

```bash
python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/ic-update.yml', encoding='utf-8'))
steps = d['jobs']['update-ic-weights']['steps']
print([s.get('name') for s in steps])
"
```

Expected: 스텝 이름 목록에 `가격 패널 캐시 복원`이 `의존성 설치`와 `IC 가중치 계산 및 저장` 사이에 있어야 한다.

- [ ] **Step 4: 커밋**

```bash
git add .github/workflows/ic-update.yml
git commit -m "ci: ic-update 워크플로에 가격 패널 캐시 연동

actions/cache로 data/를 실행 간 공유한다. 캐시 만료 시에도
price_panel이 전량 재구축하므로 실패하지 않는다."
```

---

### Task 11: 전체 실행과 결과 판정

여기서 이 작업의 성공 기준을 실제로 확인한다.

**Files:**
- Modify: `ic_weights.json` (실행 산출물)

**Interfaces:**
- Consumes: Task 1~10 전부
- Produces: 없음

- [ ] **Step 1: 전체 테스트**

Run: `pytest tests/ -v`
Expected: `16 passed` (스모크 1 + price_panel 7 + universe 4 + ic_weights 4)

- [ ] **Step 2: 500종목 전체 실행 (1회차, 오래 걸림)**

Run: `python ic_weight_updater.py`
Expected: `[price_panel] 확보 N/~500종목`. 확보율 80% 미만이면 `PanelCoverageError`가 나고, 그 경우 출력된 실패 티커 목록으로 `modules/universe.py`를 수정한 뒤 다시 실행한다.

첫 실행은 전량 다운로드라 15~40분 걸릴 수 있다.

- [ ] **Step 3: 2회차 실행으로 캐시 효과 확인**

Run: `time python ic_weight_updater.py`
Expected: `[price_panel] 캐시 히트 — 다운로드 없음` 출력. **10분 이내 완료** — 성공 기준 1번.

- [ ] **Step 4: IC 결과 판정**

Run:

```bash
python -c "
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
d = json.load(open('ic_weights.json', encoding='utf-8'))
print('universe_size:', d['universe_size'], '| coverage:', d['coverage']['resolved'], '/', d['coverage']['requested'])
print()
print(f\"{'factor':<10}{'mean_ic':>9}{'icir':>8}{'n':>5}\")
for f, s in d['per_factor_ic'].items():
    print(f\"{f:<10}{s['mean_ic']:>9.4f}{s['icir']:>8.3f}{s['n']:>5}\")
"
```

Expected: `universe_size`가 ~500, 가격 기반 팩터(`mom_3m`, `mom_1m`, `low_vol`, `ict`)의 `n`이 37종목일 때와 비슷하거나 늘어남.

- [ ] **Step 5: 결과를 판정하고 기록**

성공 기준 3번이다. `ICIR`을 보고 판단한다:

- `|ICIR| >= 0.5` → 해당 팩터에 활용 가능한 신호가 있다
- `0.2 <= |ICIR| < 0.5` → 약하지만 존재. 다른 팩터와 조합할 가치 있음
- `|ICIR| < 0.2` → **유의한 신호 없음**

37종목 기준 현재 값은 `mom_3m` 0.077, `mom_1m` 0.024, `ict` 0.028, `low_vol` -0.21이다. 500종목에서도 대부분 0.2 미만으로 나올 가능성이 높다.

**그 경우 다음 단계는 팩터 튜닝이 아니다.** 이 팩터군에 알파가 없다는 결론이며, 다른 신호원을 찾아야 한다. 결과를 그대로 사용자에게 보고하고 판단을 구한다 — 숫자를 좋게 보이도록 파라미터를 만지지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add ic_weights.json
git commit -m "chore: S&P 500 유니버스 기준 IC 가중치 산출

37종목 → ~500종목 확대 후 첫 실행 결과."
```

- [ ] **Step 7: 사용자 보고**

Step 4의 표와 Step 5의 판정을 보고한다. 특히 `|ICIR| < 0.2`인 팩터가 무엇인지, 그것이 전체 가중치의 몇 %를 차지하는지 명시한다.

---

## 완료 후 남는 것 (이번 범위 밖)

- **생존편향**: 현재 상장 종목으로 과거를 측정하므로 IC가 낙관 편향. `caveats.survivorship_bias_warning`에 명시만 되어 있다. 해결하려면 시점별 구성종목 복원이 필요하다.
- **PIT 재무 데이터**: value/quality 팩터는 여전히 측정 불가. SimFin·EDGAR 연동 또는 `pit_data_logger` 누적을 기다려야 한다.
- **다른 워크플로**: `signal-alerts.yml`·`paper-trade-us.yml`은 캐시를 쓰지 않는다. 러너들도 `load_panel`로 옮기면 이득이 있다.
- **`app.py`**: 7,405줄 모놀리스에 티커 목록과 팩터 로직이 여전히 중복되어 있다.
