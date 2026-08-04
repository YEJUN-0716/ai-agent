"""심볼 해석과 시세 스냅샷.

1단계(데모)에서는 값이 전부 합성이다. source="demo" 로 표시하고 화면에도
그대로 띄운다 — 숫자가 그럴듯해서 실측으로 착각하는 게 이 앱의 가장 큰 위험이다.
2단계에서 이 파일의 조회 함수만 실제 API로 바꾸면 나머지는 그대로 쓴다.

탭비트 값은 실측이 아니라 4개 거래소 평균 추정치다. API 직접 조회가 막혀 있다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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


def demo_snapshot(symbol: Symbol, *, now: datetime | None = None) -> Snapshot:
    """합성 시세. 심볼을 시드로 써서 같은 종목이면 항상 같은 화면이 나온다."""
    now = now or datetime.now(KST)
    rng = random.Random(f"pixel-floor::{symbol.key}")

    base = {KIND_KR: 190_000.0, KIND_COIN: 68_000.0}.get(symbol.kind, 240.0)
    price = base
    closes: list[float] = []
    for _ in range(60):
        price *= 1 + rng.gauss(0.001, 0.018)
        closes.append(round(price, 2))

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
        fear_greed=rng.randint(18, 82),
        headlines=(
            f"{symbol.label} 관련 수급 변화 관측 — 기관 순매수 이틀째",
            f"{symbol.label} 목표주가 상향 리포트 발간",
            "글로벌 금리 전망 변화에 위험자산 변동성 확대",
            f"{symbol.label} 파생 미결제약정 증가",
        ),
        board_rows=board_rows,
        fx_krw=fx,
    )
