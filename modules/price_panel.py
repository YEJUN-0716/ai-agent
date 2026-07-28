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
FIELDS           = ["Open", "High", "Low", "Close", "Volume"]

# 이력이 이만큼 안 되는 종목은 버린다. 신규상장·거래부진 종목이 IC 계산에
# 섞이는 것을 막으려고 넣은 값이고, 그 호출자들은 400일 이상을 요청한다.
MIN_TRADING_DAYS = 80

# 채점처럼 "기록일과 그 며칠 뒤 종가"만 있으면 되는 호출자를 위한 값.
# 이력 길이로 종목을 거르지 않고, 쓸 수 있는지는 채점기가 스스로 판단한다
# (analyst_scorecard.build_forward_returns 가 선행 구간이 모자란 종목을 뺀다).
MIN_TRADING_DAYS_SCORING = 1

# 캐시 최종일이 요청 종료일보다 이만큼 뒤처져도 재다운로드하지 않는다.
# 주말(2일)에 연휴가 붙으면 거래 공백이 4일까지 벌어지므로 5일로 둔다.
# 주간 IC 배치 기준값이다. 일간 신호에 쓰려면 더 좁혀야 한다.
MAX_STALE_DAYS   = 5

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
            # 티커 1개일 때 yfinance는 평면 컬럼을 준다 - MultiIndex로 승격
            raw.columns = pd.MultiIndex.from_product([raw.columns, chunk])
        frames.append(raw)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)


def _split_panel(panel: pd.DataFrame, tickers: list,
                 min_trading_days: int = MIN_TRADING_DAYS) -> tuple:
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
        if len(sub) < min_trading_days:
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
            print(f"[price_panel] 캐시 스키마 불일치 - 재구축: {path}")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception as e:
        print(f"[price_panel] 캐시 손상 - 재구축: {e}")
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

    # 캐시 인덱스는 거래일 자정이고 호출자는 datetime.now()(시각 포함)를
    # 넘기는 경우가 많다. 시각을 떼고 날짜끼리 비교하지 않으면 같은 날에도
    # 낡은 것으로 오판한다.
    cache_last  = cached.index.max().normalize()
    cache_first = cached.index.min().normalize()
    want_last   = pd.Timestamp(end_ts).normalize()
    want_first  = pd.Timestamp(start_ts).normalize()

    tol = pd.Timedelta(days=MAX_STALE_DAYS)
    needs_extension = (
        cache_last < want_last - tol
        or cache_first > want_first + tol
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


def _assert_window_fits(start_ts, end_ts, min_trading_days: int) -> None:
    """요청 구간이 문턱보다 짧으면 받아보기 전에 멈춘다.

    구간 안의 영업일 수가 min_trading_days 보다 적으면 어떤 종목도 문턱을
    넘을 수 없다 — 다운로드가 완벽해도 결과는 반드시 0종목이다. 그런데도
    끝까지 진행하면 "데이터 확보율 0/276 (0%)" 만 남아, 원인이 구간 길이인데
    네트워크 장애처럼 읽힌다. 2026-07-28 성적표 워커가 정확히 이렇게 죽었고
    yfinance 를 의심하느라 시간을 버렸다. 276종목을 헛되이 받는 비용도 같이
    없앤다. 영업일은 휴장일을 빼지 않으므로 실제 거래일은 이보다 적다 —
    즉 이 검사는 "확실히 불가능할 때만" 걸린다.
    """
    bdays = len(pd.bdate_range(start_ts.normalize(), end_ts.normalize()))
    if bdays < min_trading_days:
        raise PanelCoverageError(
            f"요청 구간이 너무 짧다 — {start_ts:%Y-%m-%d}~{end_ts:%Y-%m-%d} "
            f"영업일 {bdays}일 < 최소 거래일 {min_trading_days}일. "
            f"구간을 늘리거나 min_trading_days 를 낮춰 호출해야 한다."
        )


def load_panel(tickers: list, start, end, cache_path: str = None,
               min_trading_days: int = None) -> tuple:
    """
    tickers의 OHLCV를 반환한다.

    Parameters
    ----------
    min_trading_days : 이 거래일 수에 못 미치는 티커는 버린다.
        생략하면 MIN_TRADING_DAYS(80) — 신규상장 종목을 걸러야 하는
        IC·팩터 호출자용 기본값이다. 짧은 구간만 필요한 채점기는
        MIN_TRADING_DAYS_SCORING 을 넘긴다.

    Returns
    -------
    (prices_dict, ohlcv_dict)
        prices_dict : {ticker: Close Series}
        ohlcv_dict  : {ticker: OHLCV DataFrame}
        거래일 min_trading_days 미만인 티커는 양쪽에서 제외된다.

    Raises
    ------
    PanelCoverageError : 확보율이 MIN_SUCCESS_RATE 미만이거나,
        요청 구간이 min_trading_days 를 애초에 채울 수 없을 때
    """
    global _last_coverage
    cache_path = cache_path or CACHE_PATH
    tickers    = list(dict.fromkeys(tickers))  # 중복 제거, 순서 유지
    min_trading_days = (MIN_TRADING_DAYS if min_trading_days is None
                        else int(min_trading_days))

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    _assert_window_fits(start_ts, end_ts, min_trading_days)

    cached  = _read_cache(cache_path)
    missing, needs_extension = _missing_tickers(cached, tickers, start_ts, end_ts)

    to_fetch = tickers if needs_extension else missing
    if to_fetch:
        fresh = _download_chunked(to_fetch, start, end)
        cached = _merge_panels(cached, fresh)
        _write_cache(cached, cache_path)
    else:
        print(f"[price_panel] 캐시 히트 - 다운로드 없음 ({len(tickers)}종목)")

    window = cached.loc[(cached.index >= start_ts) & (cached.index <= end_ts)] \
        if not cached.empty else cached
    prices_dict, ohlcv_dict = _split_panel(window, tickers, min_trading_days)

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
