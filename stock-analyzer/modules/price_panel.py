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

# 분봉 창. yfinance 는 15분봉을 최근 60일까지만 준다 — 그 이상을 요청해도
# 늘지 않는다(실측: 요청 60일에 실제 40일 남짓, AAPL 1042봉).
INTRADAY_PERIOD   = "60d"
INTRADAY_INTERVAL = "15m"

_last_coverage = {"requested": 0, "resolved": 0, "failed": []}


class PanelCoverageError(Exception):
    """요청 티커 대비 확보율이 MIN_SUCCESS_RATE 미만일 때 발생."""


def last_coverage() -> dict:
    """직전 load_panel 호출의 커버리지. ic_weights.json 기록용."""
    return dict(_last_coverage)


def _download_chunked(tickers: list, start=None, end=None, *,
                      period: str = None, interval: str = "1d") -> pd.DataFrame:
    """티커를 CHUNK_SIZE씩 끊어 일괄 다운로드하고 wide DataFrame으로 합친다.

    분봉은 start/end 대신 period 로 받는다 — yfinance 가 분봉에 대해서는
    "최근 N일" 만 제공하기 때문이다(15분봉 60일).
    """
    frames = []
    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[i:i + CHUNK_SIZE]
        raw = yf.download(
            chunk, start=start, end=end, period=period, interval=interval,
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


def load_intraday(tickers: list, period: str = INTRADAY_PERIOD,
                  interval: str = INTRADAY_INTERVAL, min_bars: int = 1) -> tuple:
    """분봉 패널 — (prices, ohlcv). 캐시 없이 매번 새로 받는다.

    캐시를 두지 않는 이유: 15분봉은 창이 매일 굴러가고 60일이 지난 봉은
    yfinance 가 더는 주지 않는다. 캐시는 사라진 봉을 되살려 주지도 못하면서
    "받았다" 는 착각만 남긴다. 채점에 필요한 값은 사라지기 전에 계산해
    scalp_log 에 남긴다.

    인덱스의 tz 는 떼고 UTC 기준 naive 로 맞춘다. 거래소가 섞이면 봉 시각의
    tz 도 섞여 '몇 봉 뒤' 를 재는 쪽이 TypeError 로 죽거나 조용히 어긋난다.
    기록에 남기는 기준 시각(asof)도 이 축이라야 다음 실행에서 다시 찾는다.
    """
    panel = _download_chunked(tickers, period=period, interval=interval)
    if panel.empty:
        return {}, {}
    if getattr(panel.index, "tz", None) is not None:
        panel.index = panel.index.tz_convert("UTC").tz_localize(None)
    return _split_panel(panel, tickers, min_trading_days=min_bars)


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
        missing         : 캐시에 없거나 이력이 요청 구간을 못 덮는 티커
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

    # 캐시에 티커가 "있다"는 그 티커의 이력이 요청 구간을 덮는다는 뜻이 아니다.
    # 짧은 창으로 처음 받힌 티커는 그대로 두면 영원히 잘린 채 남는다 — 다음에
    # 길게 요청해도 '있음'으로 걸러져 재다운로드가 안 되고, 조용히 짧은 이력이
    # 돌아간다(실측: 캐시의 CSCO·INTC·PFE 가 538봉, 나머지 276종목은 1602봉).
    # 진짜 신규상장이면 다시 받아도 짧아서 매번 재요청하게 되지만, 이미 도는
    # 일괄 다운로드에 열 하나가 얹히는 비용이라 유니버스가 오래된 종목 위주인
    # 지금은 사실상 공짜다.
    if not needs_extension and "Close" in cached.columns.get_level_values(0):
        closes = cached["Close"]
        starts = closes.apply(lambda s: s.first_valid_index())
        # 열이 통째로 NaN 이면 first_valid_index() 는 None 을 내지만 Series 에는
        # **NaT** 로 들어앉는다 — `is not None` 로 거르면 안 걸리고 뒤의
        # `pd.Timestamp(NaT).normalize()` 가 AttributeError 로 죽는다. 일괄
        # 다운로드에서 한 티커만 실패하면 yfinance 가 빈 열을 주므로 실제로
        # 생길 수 있는 모양이다. NaN 인 열은 잘린 것과 같이 다시 받는다.
        truncated = [t for t in tickers
                     if t in have and (pd.isna(starts.get(t))
                                       or pd.Timestamp(starts[t]).normalize() > want_first + tol)]
        missing = list(dict.fromkeys(missing + truncated))
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
