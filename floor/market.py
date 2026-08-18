"""심볼 해석과 시세 스냅샷.

두 가지 조회가 있다.
    demo_snapshot  합성값. source="demo" 로 표시하고 화면에도 그대로 띄운다 —
                   숫자가 그럴듯해서 실측으로 착각하는 게 이 앱의 가장 큰 위험이다.
    live_snapshot  실측. Binance·Yahoo·환율·뉴스·공포탐욕을 직접 부른다.

실전에서는 못 가져온 값을 지어내지 않는다. 거래소 한 곳이 죽으면 그 줄만 빠지고,
전부 죽으면 전광판이 통째로 빠진다. source 문자열에 어디서 왔는지 매번 적는다.

탭비트 값만 실측이 아니라 4개 거래소 평균 추정치다. API 직접 조회가 막혀 있다.
"""

from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape

KST = timezone(timedelta(hours=9))

KIND_COIN = "coin"
KIND_US = "us_stock"
KIND_KR = "kr_stock"

# 무기한 선물 가격을 참고하는 거래소들. 탭비트는 이 4곳 평균으로 추정한다.
PERP_EXCHANGES = ("Binance", "Bybit", "OKX", "Gate")
TAPBIT = "Tapbit"


@dataclass(frozen=True)
class Symbol:
    key: str
    label: str
    kind: str
    currency: str
    # 전광판(환율·KRX·거래소별 선물·괴리)은 해외에 USDT 무기한으로 상장된
    # 한국 종목에서만 뜬다. 코인·미국주식에는 나오지 않는다.
    board: bool = False


_KR_STOCKS = (
    Symbol("SKHYNIX", "SK하이닉스", KIND_KR, "KRW", board=True),
    Symbol("SAMSUNG", "삼성전자", KIND_KR, "KRW", board=True),
)

# 사장님이 실제로 칠 법한 표기를 전부 받는다.
_ALIASES: dict[str, str] = {
    "하이닉스": "SKHYNIX",
    "sk하이닉스": "SKHYNIX",
    "sk 하이닉스": "SKHYNIX",
    "000660": "SKHYNIX",
    "skhynix-usdt": "SKHYNIX",
    "삼성전자": "SAMSUNG",
    "삼성": "SAMSUNG",
    "005930": "SAMSUNG",
    "samsung-usdt": "SAMSUNG",
}

COINS = (
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "MATIC",
    "TRX", "LTC", "BCH", "ATOM", "UNI", "NEAR", "APT", "ARB", "OP", "SUI",
    "INJ", "TIA", "SEI", "FIL", "ICP", "HBAR", "VET", "ALGO", "AAVE", "MKR",
    "RUNE", "PEPE", "SHIB", "WIF", "BONK", "ETC",
)

_BY_KEY: dict[str, Symbol] = {s.key: s for s in _KR_STOCKS}


class UnknownSymbol(ValueError):
    """심볼을 못 알아들었을 때. 메시지를 그대로 사용자에게 보여준다."""


def resolve_symbol(raw: str) -> Symbol:
    """입력 문자열을 Symbol 로 바꾼다. 모르는 티커는 미국 주식으로 본다.

    코인·한국주식 목록에 없으면 야후 파이낸스 종목으로 취급한다. 여기서 막으면
    사장님이 아는 종목인데 목록에 없다는 이유로 못 쓰게 되므로, 통과시키고
    실제 조회 단계에서 실패하게 둔다.
    """
    text = (raw or "").strip()
    if not text:
        raise UnknownSymbol("종목을 입력해 주세요. 예: BTC · TSLA · 하이닉스")

    lowered = text.lower()
    if lowered in _ALIASES:
        return _BY_KEY[_ALIASES[lowered]]

    upper = text.upper()
    if upper in _BY_KEY:
        return _BY_KEY[upper]
    if upper in COINS:
        return Symbol(upper, upper, KIND_COIN, "USD")

    if not upper.replace(".", "").replace("-", "").isalnum():
        raise UnknownSymbol(f"종목 이름에 쓸 수 없는 글자가 있습니다: {text!r}")
    return Symbol(upper, upper, KIND_US, "USD")


@dataclass(frozen=True)
class Snapshot:
    """전광판과 에이전트가 함께 보는 시세 한 장."""

    symbol: Symbol
    price: float
    change_pct: float
    closes: tuple[float, ...]
    ma20: tuple[float | None, ...]
    ma50: tuple[float | None, ...]
    high20: float
    low20: float
    market_open: bool
    perp_vol_pct: float
    krw_vol_pct: float
    fear_greed: int
    headlines: tuple[str, ...]
    # (시, 고, 저, 종) 완결 일봉. closes 는 화면용이라 20/50 이평에 맞춰 짧게
    # 자르지만, 검증 플랜 엔진은 ICT 구조를 보려면 봉 전체가 필요하다
    # (trade_plan.MIN_BARS=60). 시가는 오더블록 판정(양봉/음봉)에 쓰인다.
    bars: tuple[tuple[float, float, float, float], ...] = ()
    # 스캘핑·공격 모드용 완결 15분봉. 검증 관문이 일봉으로 단타를 재면 시간축이
    # 어긋나 검증이 아니라 오판이 된다. 못 가져오면 빈 값으로 두고 관문이
    # "검사 못 함"으로 끝나게 한다 — 빈 값을 통과로 읽는 자리는 없다.
    bars_15m: tuple[tuple[float, float, float, float], ...] = ()
    # 그 15분봉을 어느 시장에서 가져왔는지. 무기한은 USDT 라 화면 통화와 다르다.
    bars_15m_from: str = ""
    board_rows: tuple[dict, ...] = ()
    fx_krw: float | None = None
    source: str = "demo"
    taken_at: str = field(default_factory=lambda: datetime.now(KST).isoformat())


def _moving_average(values: tuple[float, ...], window: int) -> tuple[float | None, ...]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(round(sum(values[i + 1 - window : i + 1]) / window, 4))
    return tuple(out)


def _is_krx_open(now: datetime) -> bool:
    """KRX 정규장(평일 09:00–15:30 KST) 여부. 공휴일은 보지 않는다."""
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes <= 15 * 60 + 30


def _synth_bars(
    rng: random.Random, base: float, count: int, drift: float, sigma: float
) -> tuple[list[float], tuple[tuple[float, float, float, float], ...]]:
    """(종가 시리즈, (시,고,저,종) 봉) 합성.

    시가는 전봉 종가로 둔다. 갭 없는 합성 시리즈라 이게 가장 자연스럽고,
    양봉·음봉이 실제 흐름대로 섞여 오더블록이 잡힌다.
    """
    price = base
    series: list[float] = []
    for _ in range(count):
        price *= 1 + rng.gauss(drift, sigma)
        series.append(round(price, 2))
    wick = sigma / 3
    bars = tuple(
        (
            series[i - 1] if i else c,
            round(max(c, series[i - 1] if i else c) * (1 + abs(rng.gauss(0, wick))), 2),
            round(min(c, series[i - 1] if i else c) * (1 - abs(rng.gauss(0, wick))), 2),
            c,
        )
        for i, c in enumerate(series)
    )
    return series, bars


def demo_snapshot(symbol: Symbol, *, now: datetime | None = None) -> Snapshot:
    """합성 시세. 심볼을 시드로 써서 같은 종목이면 항상 같은 화면이 나온다."""
    now = now or datetime.now(KST)
    rng = random.Random(f"pixel-floor::{symbol.key}")

    base = {KIND_KR: 190_000.0, KIND_COIN: 68_000.0}.get(symbol.kind, 240.0)
    # 화면은 뒤 60봉만 그린다. 앞쪽은 검증 플랜 엔진이 ICT 구조를 찾을 준비운동
    # 구간이다 — 60봉(MIN_BARS)에 딱 맞추면 스윙·오더블록이 안 잡힌다.
    series, bars = _synth_bars(rng, base, 200, 0.001, 0.018)
    closes = series[-60:]

    last, prev = closes[-1], closes[-2]
    window = tuple(closes[-20:])

    # 이 두 값이 스캘핑 판정을 가른다. 원화 변동성은 정규장에 부풀려지므로
    # 무기한 쪽을 따로 계산해 함께 들고 다닌다.
    perp_vol = abs(rng.gauss(1.2, 0.3))
    krw_vol = perp_vol * rng.uniform(1.8, 2.8) if symbol.kind == KIND_KR else perp_vol

    board_rows: tuple[dict, ...] = ()
    fx = None
    if symbol.board:
        fx = round(rng.uniform(1330, 1395), 2)
        perp_usd = round(last / fx * rng.uniform(0.995, 1.02), 4)
        rows = []
        for name in PERP_EXCHANGES:
            quoted = round(perp_usd * rng.uniform(0.996, 1.004), 4)
            rows.append(
                {
                    "exchange": name,
                    "perp_usdt": quoted,
                    "funding_pct": round(rng.gauss(0.01, 0.02), 4),
                    "gap_pct": round((quoted * fx / last - 1) * 100, 2),
                    "estimated": False,
                }
            )
        average = round(sum(r["perp_usdt"] for r in rows) / len(rows), 4)
        rows.append(
            {
                "exchange": TAPBIT,
                "perp_usdt": average,
                "funding_pct": None,
                "gap_pct": round((average * fx / last - 1) * 100, 2),
                # API 직접 조회가 막혀 있어 4개 평균으로 추정한 값이다.
                "estimated": True,
            }
        )
        board_rows = tuple(rows)

    # 아래 두 줄의 순서를 바꾸지 말 것. 시드가 같아도 난수를 뽑는 순서가 바뀌면
    # 같은 종목의 데모 화면이 통째로 달라진다.
    fear_greed = rng.randint(18, 82)
    # 15분봉은 하루치보다 덜 움직인다. 20배 스캘핑이 말이 되는 폭으로 만든다.
    _, bars_15m = _synth_bars(rng, last, 240, 0.0002, 0.004)

    return Snapshot(
        symbol=symbol,
        price=last,
        change_pct=round((last / prev - 1) * 100, 2),
        closes=tuple(closes),
        ma20=_moving_average(tuple(closes), 20),
        ma50=_moving_average(tuple(closes), 50),
        high20=max(window),
        low20=min(window),
        market_open=_is_krx_open(now) if symbol.kind == KIND_KR else True,
        perp_vol_pct=round(perp_vol, 2),
        krw_vol_pct=round(krw_vol, 2),
        bars=bars,
        bars_15m=bars_15m,
        bars_15m_from="데모 15분봉",
        fear_greed=fear_greed,
        headlines=(
            f"{symbol.label} 관련 수급 변화 관측 — 기관 순매수 이틀째",
            f"{symbol.label} 목표주가 상향 리포트 발간",
            "글로벌 금리 전망 변화에 위험자산 변동성 확대",
            f"{symbol.label} 파생 미결제약정 증가",
        ),
        board_rows=board_rows,
        fx_krw=fx,
    )


# ── 실전 시세 ─────────────────────────────────────────────
#
# 여기부터는 전부 실측이다. 값 하나를 못 가져오면 그 값을 빼거나 판을 접는다.
# 합성으로 메우면 데모와 실전이 화면에서 구분이 안 된다 — 그게 제일 위험하다.

# 야후는 UA 없는 요청을 막는다.
_UA = {"User-Agent": "Mozilla/5.0 (compatible; pixel-trading-floor)"}
_TIMEOUT = 12.0

_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
_YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_FX_TICKER = "KRW=X"

# 한국 종목은 야후 티커와 뉴스 검색어가 따로 논다. 티커로 검색하면 무관한 기사가
# 나와서(실측: 000660.KS → 항공기 리스 채권 기사) 회사 이름으로 찾는다.
_YAHOO_TICKERS = {"SKHYNIX": "000660.KS", "SAMSUNG": "005930.KS"}
_NEWS_QUERIES = {"SKHYNIX": "SK Hynix", "SAMSUNG": "Samsung Electronics"}

_HEADLINE_COUNT = 5

# 코인 뉴스. 야후 검색은 코인 질의를 무시하고 아무 펀드 기사나 돌려준다.
_COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"
_RSS_TITLE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
# 기사 제목은 티커보다 이름을 쓴다. 거래량 상위 셋만 넣고 나머지는 티커로 찾는다.
_COIN_NAMES = {"BTC": "Bitcoin", "ETH": "Ether", "SOL": "Solana"}


class MarketError(RuntimeError):
    """시세를 못 가져왔을 때. 메시지가 그대로 화면에 뜬다."""


def _get_json(url: str):
    request = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MarketError(f"{urllib.parse.urlsplit(url).netloc} 조회 실패: {exc}") from exc


def _num(value) -> float:
    return float(value)


def _range_pct(high: float, low: float, close: float) -> float:
    """하루치 고저 폭. 이 값이 손절 폭을 정하고, 스캘핑의 오기각을 가른다."""
    if not close:
        raise MarketError("종가가 0입니다")
    return round(abs(high - low) / close * 100, 2)


# ── 거래소 ────────────────────────────────────────────────


def _binance_klines(
    pair: str, *, perp: bool, limit: int, interval: str = "1d"
) -> list[list]:
    host = "https://fapi.binance.com/fapi/v1" if perp else "https://api.binance.com/api/v3"
    rows = _get_json(f"{host}/klines?symbol={pair}&interval={interval}&limit={limit}")
    if not isinstance(rows, list) or len(rows) < 2:
        raise MarketError(f"바이낸스에 {pair} {interval} 봉이 없습니다")
    return rows


def _klines_bars(rows: list[list]) -> tuple[tuple[float, float, float, float], ...]:
    """(시, 고, 저, 종). 마지막 봉은 진행 중이라 뗀다 — 구조 판정에 섞으면 안 된다."""
    return tuple((_num(r[1]), _num(r[2]), _num(r[3]), _num(r[4])) for r in rows[:-1])


def _binance_perp(base: str) -> tuple[float, float]:
    data = _get_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={base}USDT")
    return round(_num(data["markPrice"]), 4), round(_num(data["lastFundingRate"]) * 100, 4)


def _bybit_perp(base: str) -> tuple[float, float]:
    data = _get_json(
        f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={base}USDT"
    )
    row = data["result"]["list"][0]
    return round(_num(row["lastPrice"]), 4), round(_num(row["fundingRate"]) * 100, 4)


def _okx_perp(base: str) -> tuple[float, float]:
    inst = f"{base}-USDT-SWAP"
    last = _get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst}")
    funding = _get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst}")
    return (
        round(_num(last["data"][0]["last"]), 4),
        round(_num(funding["data"][0]["fundingRate"]) * 100, 4),
    )


def _gate_perp(base: str) -> tuple[float, float]:
    data = _get_json(
        f"https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={base}_USDT"
    )
    row = data[0]
    rate = row.get("funding_rate") or row["funding_rate_indicative"]
    return round(_num(row["last"]), 4), round(_num(rate) * 100, 4)


# PERP_EXCHANGES 와 순서가 같아야 화면 표가 데모와 같은 줄 순서로 뜬다.
_PERP_FETCHERS = (
    ("Binance", _binance_perp),
    ("Bybit", _bybit_perp),
    ("OKX", _okx_perp),
    ("Gate", _gate_perp),
)


def _board(base: str, fx: float, krx_price: float) -> tuple[dict, ...]:
    """4개 거래소 무기한 + 탭비트(평균 추정). 죽은 거래소는 줄째로 뺀다."""
    rows: list[dict] = []
    for name, fetch in _PERP_FETCHERS:
        try:
            price, funding = fetch(base)
        except (MarketError, KeyError, IndexError, TypeError, ValueError):
            continue  # 한 곳이 없다고 판을 접지 않는다. 지어내지도 않는다.
        rows.append(
            {
                "exchange": name,
                "perp_usdt": price,
                "funding_pct": funding,
                "gap_pct": round((price * fx / krx_price - 1) * 100, 2),
                "estimated": False,
            }
        )
    if not rows:
        return ()

    average = round(sum(r["perp_usdt"] for r in rows) / len(rows), 4)
    rows.append(
        {
            "exchange": TAPBIT,
            "perp_usdt": average,
            "funding_pct": None,
            "gap_pct": round((average * fx / krx_price - 1) * 100, 2),
            # 직접 조회가 막혀 있어 위 거래소 평균으로 추정한 값이다.
            "estimated": True,
        }
    )
    return tuple(rows)


# ── 야후 ──────────────────────────────────────────────────


def _yahoo_chart(ticker: str, span: str = "6mo", interval: str = "1d") -> dict:
    data = _get_json(
        f"{_YAHOO_CHART}{urllib.parse.quote(ticker)}?range={span}&interval={interval}"
    )
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise MarketError(f"야후가 {ticker} 를 모릅니다") from exc
    if result.get("indicators", {}).get("quote"):
        return result
    raise MarketError(f"야후 {ticker} 응답에 일봉이 없습니다")


def _yahoo_bars(result: dict) -> list[tuple[float, float, float, float]]:
    """(시, 고, 저, 종) 값이 다 찬 봉만.

    주의: 장중에도 오늘 봉의 값이 채워져 내려오므로 "완결 봉"이라는 보장은 없다.
    구조 판정에 쓸 봉은 호출한 쪽에서 마지막 하나를 떼야 한다.
    """
    q = result["indicators"]["quote"][0]
    bars = [
        (float(o), float(h), float(low), float(c))
        for o, h, low, c in zip(q["open"], q["high"], q["low"], q["close"])
        if None not in (o, h, low, c)
    ]
    if len(bars) < 21:
        raise MarketError("일봉이 21개 미만이라 20봉 레인지를 못 만듭니다")
    return bars


def _yahoo_price(result: dict, bars: list[tuple[float, float, float, float]]) -> float:
    price = result.get("meta", {}).get("regularMarketPrice")
    return round(float(price if price is not None else bars[-1][3]), 2)


def _get_text(url: str) -> str:
    request = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise MarketError(f"{urllib.parse.urlsplit(url).netloc} 조회 실패: {exc}") from exc


def _crypto_headlines(key: str) -> tuple[str, ...]:
    """코인 뉴스. 야후 검색은 코인 질의를 무시하고 무관한 펀드 기사를 준다(실측).

    종목을 짚은 기사를 앞에 두되, 없으면 시장 전체 기사로 채운다 — 코인은
    개별 뉴스보다 시장 흐름이 먼저 움직이는 일이 잦다.
    """
    try:
        feed = _get_text(_COINDESK_RSS)
    except MarketError:
        return ()

    # XML 파서를 안 쓴다. 남의 피드를 파싱하는 데 파서를 붙이면 엔티티 폭탄까지
    # 같이 받는다. 필요한 건 <item> 안의 제목 한 줄뿐이라 그것만 긁는다.
    titles = []
    for block in re.findall(r"<item\b.*?</item>", feed, re.S):
        found = _RSS_TITLE.search(block)
        if found:
            titles.append(unescape(found.group(1)).strip())
    words = (key, _COIN_NAMES.get(key, key))
    hit = [t for t in titles if any(w.lower() in t.lower() for w in words)]
    rest = [t for t in titles if t not in hit]
    return tuple((hit + rest)[:_HEADLINE_COUNT])


def _headlines(query: str) -> tuple[str, ...]:
    """야후 뉴스 검색. 실패하면 빈 값 — 없는 뉴스를 지어내지 않는다."""
    url = (
        f"{_YAHOO_SEARCH}?q={urllib.parse.quote(query)}"
        f"&newsCount={_HEADLINE_COUNT}&quotesCount=0"
    )
    try:
        items = _get_json(url).get("news", [])
    except (MarketError, AttributeError):
        return ()
    return tuple(item["title"] for item in items if item.get("title"))[:_HEADLINE_COUNT]


def _fear_greed() -> int:
    """alternative.me 공포탐욕. 크립토 기준 지수라 주식에서는 참고값이다."""
    try:
        return int(_get_json("https://api.alternative.me/fng/?limit=1")["data"][0]["value"])
    except (MarketError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise MarketError(f"공포탐욕 지수를 못 가져왔습니다: {exc}") from exc


def _fx_krw() -> float:
    bars = _yahoo_bars(_yahoo_chart(_FX_TICKER, span="3mo"))
    return round(bars[-1][3], 2)  # 종가. 봉이 (시, 고, 저, 종) 이라 3번이다.


# ── 15분봉 ────────────────────────────────────────────────
#
# 스캘핑·공격의 판정 기준 시장은 24시간 USDT 무기한이다. 검증 관문도 **같은 차트**를
# 봐야 한다 — 원화 정규장 15분봉으로 무기한 단타를 재면 폭락일에 변동성이 몇 배로
# 부풀려져(실측: KRX 3.05% vs 무기한 1.20%) 멀쩡한 셋업이 오기각된다. 무기한이
# 아예 없는 미국 주식만 야후 15분봉으로 잰다.
#
# 못 가져오면 빈 값을 돌려주고 판은 계속 간다. 관문이 "검사 못 함"으로 끝날 뿐,
# 없는 봉을 지어내지도 다른 시장 봉으로 대신하지도 않는다.

INTRADAY = "15m"
# 엔진은 60봉(MIN_BARS)이 최소이고 숏 레짐 필터가 70봉을 본다. 넉넉히 받는다.
INTRADAY_LIMIT = 300
_INTRADAY_SPAN = "1mo"  # 야후 15분봉이 허용하는 조회 범위


def _intraday_perp(base: str) -> tuple[tuple[tuple[float, ...], ...], str]:
    pair = f"{base}USDT"
    try:
        rows = _binance_klines(pair, perp=True, limit=INTRADAY_LIMIT, interval=INTRADAY)
    except MarketError:
        return (), ""
    return _klines_bars(rows), f"무기한 {pair} 15분봉"


def _intraday_yahoo(ticker: str) -> tuple[tuple[tuple[float, ...], ...], str]:
    try:
        bars = _yahoo_bars(_yahoo_chart(ticker, span=_INTRADAY_SPAN, interval=INTRADAY))
    except MarketError:
        return (), ""
    # 야후는 장중에도 진행 중인 봉을 채워 준다. 일봉과 같은 이유로 마지막을 뗀다.
    return tuple(bars[:-1]), f"Yahoo {ticker} 15분봉"


def _finish(
    symbol: Symbol,
    closes: list[float],
    price: float,
    perp_vol: float,
    krw_vol: float,
    *,
    headlines: tuple[str, ...],
    source: str,
    now: datetime,
    bars: tuple[tuple[float, float, float, float], ...] = (),
    intraday: tuple[tuple[tuple[float, ...], ...], str] = ((), ""),
    board_rows: tuple[dict, ...] = (),
    fx: float | None = None,
) -> Snapshot:
    window = tuple(closes[-20:])
    return Snapshot(
        symbol=symbol,
        price=price,
        change_pct=round((price / closes[-2] - 1) * 100, 2),
        closes=tuple(closes),
        ma20=_moving_average(tuple(closes), 20),
        ma50=_moving_average(tuple(closes), 50),
        high20=max(window),
        low20=min(window),
        market_open=_is_krx_open(now) if symbol.kind == KIND_KR else True,
        perp_vol_pct=perp_vol,
        krw_vol_pct=krw_vol,
        fear_greed=_fear_greed(),
        headlines=headlines,
        bars=bars,
        bars_15m=intraday[0],
        bars_15m_from=intraday[1],
        board_rows=board_rows,
        fx_krw=fx,
        source=source,
    )


def _coin_snapshot(symbol: Symbol, now: datetime) -> Snapshot:
    pair = f"{symbol.key}USDT"
    # 화면은 60봉이면 되지만 검증 플랜 엔진은 60봉이 최소선이라 딱 맞추면 구조를
    # 못 찾는다. 넉넉히 받아 화면엔 뒤 60개만 쓴다.
    spot = _binance_klines(pair, perp=False, limit=200)
    bars = _klines_bars(spot)
    closes = [round(_num(row[4]), 2) for row in spot[-60:]]

    # 판정 기준은 무기한 차트다. 마지막 봉은 진행 중이라 직전 완결 봉으로 잰다.
    perp = _binance_klines(pair, perp=True, limit=3)[-2]
    perp_vol = _range_pct(_num(perp[2]), _num(perp[3]), _num(perp[4]))

    return _finish(
        symbol,
        closes,
        closes[-1],
        perp_vol,
        perp_vol,  # 코인은 원화 정규장 차트가 없다. 같은 값을 들고 간다.
        bars=bars,
        intraday=_intraday_perp(symbol.key),
        headlines=_crypto_headlines(symbol.key),
        source=f"Binance {pair} 현물·무기한 · F&G alternative.me · 뉴스 CoinDesk",
        now=now,
    )


def _stock_snapshot(symbol: Symbol, now: datetime) -> Snapshot:
    ticker = _YAHOO_TICKERS.get(symbol.key, symbol.key)
    chart = _yahoo_chart(ticker)
    bars = _yahoo_bars(chart)
    price = _yahoo_price(chart, bars)
    # 야후는 장중에도 오늘 봉의 OHLC 를 채워 내려준다 — "값이 있다"로는 완결 봉인지
    # 알 수 없다. 구조 판정에 진행 중인 봉이 섞이면 안 되므로 바이낸스와 똑같이
    # 마지막 봉을 뗀다. 화면용 closes 는 아래에서 실시간가를 따로 붙인다.
    gate_bars = tuple(bars[:-1])
    # 실시간가는 마지막 봉과 같은 날이다. 덧붙이면 오늘이 두 번 들어가
    # 전일 대비가 '오늘 종가 대비'로 바뀐다 (장 마감 후엔 항상 0.00%).
    closes = [round(bar[3], 2) for bar in bars[:-1]] + [price]
    day_vol = _range_pct(bars[-1][1], bars[-1][2], bars[-1][3])
    news = _headlines(_NEWS_QUERIES.get(symbol.key, symbol.key))

    if symbol.kind != KIND_KR:
        # 미국 주식엔 대응 무기한 시장이 없다. 일봉 레인지를 그대로 쓰고 출처에 적는다.
        return _finish(
            symbol,
            closes,
            price,
            day_vol,
            day_vol,
            bars=gate_bars,
            # 대응 무기한이 없으니 15분봉도 같은 야후 차트에서 온다 (통화도 그대로).
            intraday=_intraday_yahoo(ticker),
            headlines=news,
            source=f"Yahoo {ticker} 일봉 · 무기한 시장 없음 · 뉴스 Yahoo",
            now=now,
        )

    # 한국 종목: 화면 가격은 원화, 판정 변동성은 해외 무기한. 둘을 같이 들고 간다.
    perp = _binance_klines(f"{symbol.key}USDT", perp=True, limit=3)[-2]
    perp_vol = _range_pct(_num(perp[2]), _num(perp[3]), _num(perp[4]))
    fx = _fx_krw()
    return _finish(
        symbol,
        closes,
        price,
        perp_vol,
        day_vol,
        bars=gate_bars,
        # 스캘핑 판정 기준이 무기한이므로 15분봉도 무기한에서 가져온다. 원화가
        # 아니라 USDT 라, 관문이 내놓는 라인의 단위도 화면 가격과 다르다.
        intraday=_intraday_perp(symbol.key),
        headlines=news,
        source=(
            f"Yahoo {ticker} · 무기한 {'/'.join(PERP_EXCHANGES)} · "
            f"환율 Yahoo {_FX_TICKER} · 뉴스 Yahoo"
        ),
        now=now,
        board_rows=_board(symbol.key, fx, price),
        fx=fx,
    )


def live_snapshot(symbol: Symbol, *, now: datetime | None = None) -> Snapshot:
    """실측 시세 한 장. 못 가져오면 MarketError 로 판을 접는다."""
    now = now or datetime.now(KST)
    if symbol.kind == KIND_COIN:
        return _coin_snapshot(symbol, now)
    return _stock_snapshot(symbol, now)
