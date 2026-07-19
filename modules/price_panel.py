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


def _merge_panels(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """기존 패널에 신규 패널을 덮어쓰며 합친다. 겹치는 셀은 new 우선."""
    if old.empty:
        return new
    if new.empty:
        return old
    merged = new.combine_first(old)
    return merged.sort_index()


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

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    cached  = _read_cache(cache_path)
    missing, needs_extension = _missing_tickers(cached, tickers, start_ts, end_ts)

    to_fetch = tickers if needs_extension else missing
    if to_fetch:
        fresh = _download_chunked(to_fetch, start, end)
        cached = _merge_panels(cached, fresh)
        _write_cache(cached, cache_path)
    else:
        print(f"[price_panel] 캐시 히트 — 다운로드 없음 ({len(tickers)}종목)")

    window = cached.loc[(cached.index >= start_ts) & (cached.index <= end_ts)] \
        if not cached.empty else cached
    prices_dict, ohlcv_dict = _split_panel(window, tickers)

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
