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
