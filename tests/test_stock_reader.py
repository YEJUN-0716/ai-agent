import json

import pytest

from assistant.config import Settings
from tools import stock_reader
from tools.stock_reader import (
    StockDataError,
    get_analyst_scores,
    get_equity_history,
    get_recent_signals,
    get_virtual_portfolio,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    stock_dir = tmp_path / "stock-analyzer"
    (stock_dir / "data" / "analyst_log").mkdir(parents=True)
    return Settings(
        anthropic_api_key="k",
        telegram_bot_token="t",
        telegram_allowed_chat_ids=frozenset({1}),
        stock_analyzer_path=stock_dir,
        assistant_data_dir=tmp_path / "assistant",
        model="claude-opus-5",
        effort="medium",
        web_host="127.0.0.1",
        web_port=8765,
        history_limit=40,
        study_inbox=tmp_path,
        obsidian_vault=tmp_path,
    )


def test_returns_empty_portfolio_when_file_absent(settings):
    # Arrange — virtual_portfolio.json을 만들지 않는다 (첫 실행 전 상태)

    # Act
    result = get_virtual_portfolio(settings)

    # Assert
    assert result["positions"] == {}
    assert result["started"] is False


def test_reads_positions_from_portfolio_file(settings):
    # Arrange
    (settings.stock_analyzer_path / "virtual_portfolio.json").write_text(
        json.dumps({
            "cash_krw": 9_000_000,
            "positions": {"AAPL": {"qty": 3, "avg_price_usd": 330.0,
                                   "entry_date": "2026-07-20"}},
            "pending": [],
            "realized_pnl_krw": 120_000.0,
            "trades": [],
        }),
        encoding="utf-8",
    )

    # Act
    result = get_virtual_portfolio(settings)

    # Assert
    assert result["started"] is True
    assert result["cash_krw"] == 9_000_000
    assert result["positions"]["AAPL"]["qty"] == 3
    assert result["realized_pnl_krw"] == 120_000.0


class FakeBroker:
    """진짜 브로커 대신 정해진 종가와 환율을 준다."""

    def __init__(self, prices: dict[str, float], fx: float = 1400.0) -> None:
        self._prices = prices
        self._fx = fx

    def krw_per_usd(self) -> float:
        return self._fx

    def last_close_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)


@pytest.fixture
def valued(settings, monkeypatch):
    """평가금액 계산이 가짜 브로커를 쓰도록 바꾼다. 가격 캐시도 비운다."""
    (settings.stock_analyzer_path / "modules").mkdir(parents=True, exist_ok=True)
    (settings.stock_analyzer_path / "modules" / "virtual_broker.py").write_text(
        "", encoding="utf-8"
    )
    monkeypatch.setattr(stock_reader, "_price_cache", {})

    def install(prices: dict[str, float], fx: float = 1400.0) -> None:
        monkeypatch.setattr(
            stock_reader.virtual_trade,
            "broker_module",
            lambda _settings: FakeBroker(prices, fx),
        )

    return install


def _write_portfolio(settings, positions: dict, cash_krw: float = 1_000_000) -> None:
    (settings.stock_analyzer_path / "virtual_portfolio.json").write_text(
        json.dumps({
            "cash_krw": cash_krw,
            "positions": positions,
            "pending": [],
            "realized_pnl_krw": 0.0,
            "trades": [],
        }),
        encoding="utf-8",
    )


def test_valuation_is_reported_in_won(settings, valued):
    # Arrange — 3주를 330달러에 샀고 지금 350달러, 환율 1,400원
    _write_portfolio(
        settings,
        {"AAPL": {"qty": 3, "avg_price_usd": 330.0, "entry_date": "2026-07-20"}},
    )
    valued({"AAPL": 350.0}, fx=1400.0)

    # Act
    result = get_virtual_portfolio(settings)

    # Assert — 평가금액 3×350×1400, 평가손익 3×20×1400
    assert result["positions"]["AAPL"]["market_value_krw"] == 1_470_000
    assert result["positions"]["AAPL"]["unrealized_pnl_krw"] == 84_000
    assert result["stock_value_krw"] == 1_470_000
    assert result["unrealized_pnl_krw"] == 84_000
    assert result["total_equity_krw"] == 1_000_000 + 1_470_000


def test_unpriced_position_is_not_counted_as_a_total_loss(settings, valued):
    """현재가를 못 받은 종목을 0원으로 치면 없는 손실이 생긴다.

    2026-08-04에 브로커 보고서가 실제로 "총 자산 nan원"을 찍은 적이 있다.
    여기서는 0원이 같은 자리를 차지한다 — 사장님은 전액 손실로 읽는다.
    """
    # Arrange — MSFT는 가격을 못 받는다
    _write_portfolio(
        settings,
        {
            "AAPL": {"qty": 3, "avg_price_usd": 330.0, "entry_date": "2026-07-20"},
            "MSFT": {"qty": 2, "avg_price_usd": 500.0, "entry_date": "2026-07-20"},
        },
    )
    valued({"AAPL": 330.0}, fx=1400.0)

    # Act
    result = get_virtual_portfolio(settings)

    # Assert — 빠진 종목은 손익 0이 아니라 '모름'으로 남고, 이름을 알린다
    assert "unrealized_pnl_krw" not in result["positions"]["MSFT"]
    assert result["unrealized_pnl_krw"] == 0
    assert result["stock_value_krw"] == 3 * 330 * 1400
    assert "MSFT" in result["valuation_note"]


def test_valuation_uses_the_injected_exchange_rate(settings, valued):
    # Arrange — 환율이 1,400이 아니면 원화 평가금액도 그만큼 달라야 한다
    _write_portfolio(
        settings,
        {"AAPL": {"qty": 1, "avg_price_usd": 100.0, "entry_date": "2026-07-20"}},
    )
    valued({"AAPL": 100.0}, fx=1300.0)

    # Act
    result = get_virtual_portfolio(settings)

    # Assert
    assert result["positions"]["AAPL"]["market_value_krw"] == 130_000
    assert result["fx_krw_per_usd"] == 1300.0


def test_raises_readable_error_on_corrupt_json(settings):
    # Arrange
    (settings.stock_analyzer_path / "virtual_portfolio.json").write_text(
        "{ 망가진 파일", encoding="utf-8"
    )

    # Act / Assert
    with pytest.raises(StockDataError, match="virtual_portfolio.json"):
        get_virtual_portfolio(settings)


def test_returns_most_recent_signals_first(settings):
    # Arrange
    (settings.stock_analyzer_path / "signal_log.json").write_text(
        json.dumps({"signals": [
            {"symbol": "AMAT", "action": "매수", "entry_date": "2026-07-08",
             "entry_price": 570.5, "score": 68.7, "rsi": 50.2,
             "return_pct": None},
            {"symbol": "AAPL", "action": "🟢 매수", "entry_date": "2026-07-17",
             "entry_price": 333.74, "score": 61.0, "rsi": 72.0,
             "return_pct": 4.2},
        ]}),
        encoding="utf-8",
    )

    # Act
    result = get_recent_signals(settings, limit=1)

    # Assert
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


def test_returns_empty_list_when_signal_log_absent(settings):
    # Act
    result = get_recent_signals(settings)

    # Assert
    assert result == []


def test_reads_equity_records(settings):
    # Arrange
    (settings.stock_analyzer_path / "equity_log.json").write_text(
        json.dumps({"records": [
            {"date": "2026-07-27", "equity_krw": 10_050_000},
            {"date": "2026-07-28", "equity_krw": 10_120_000},
        ]}),
        encoding="utf-8",
    )

    # Act
    result = get_equity_history(settings, limit=1)

    # Assert
    assert result == [{"date": "2026-07-28", "equity_krw": 10_120_000}]


def test_reads_analyst_scores_newest_first(settings):
    # Arrange
    log = settings.stock_analyzer_path / "data" / "analyst_log" / "2026.jsonl"
    log.write_text(
        json.dumps({"date": "2026-07-22", "regime": "bull",
                    "scores": {"AAPL": {"chart": 50.0, "ict": 60.0}}}) + "\n"
        + json.dumps({"date": "2026-07-23", "regime": "bull",
                      "scores": {"AAPL": {"chart": 73.8, "ict": 100.0}}}) + "\n",
        encoding="utf-8",
    )

    # Act
    result = get_analyst_scores(settings, limit=1)

    # Assert
    assert result[0]["date"] == "2026-07-23"
    assert result[0]["scores"]["AAPL"]["chart"] == 73.8


def test_skips_corrupt_lines_in_analyst_log(settings):
    # Arrange — 한 줄이 망가져도 나머지는 읽혀야 한다
    log = settings.stock_analyzer_path / "data" / "analyst_log" / "2026.jsonl"
    log.write_text(
        "{ 망가진 줄\n"
        + json.dumps({"date": "2026-07-23", "regime": "bull", "scores": {}})
        + "\n",
        encoding="utf-8",
    )

    # Act
    result = get_analyst_scores(settings)

    # Assert
    assert len(result) == 1
    assert result[0]["date"] == "2026-07-23"


def test_equity_history_returns_newest_first_with_limit(settings):
    # Arrange — 3개 이상의 구별 가능한 레코드
    (settings.stock_analyzer_path / "equity_log.json").write_text(
        json.dumps({"records": [
            {"date": "2026-07-26", "equity_krw": 10_000_000},
            {"date": "2026-07-27", "equity_krw": 10_050_000},
            {"date": "2026-07-28", "equity_krw": 10_120_000},
        ]}),
        encoding="utf-8",
    )

    # Act
    result = get_equity_history(settings, limit=2)

    # Assert — 최신 2개를 최신순으로 반환
    assert len(result) == 2
    assert result[0]["date"] == "2026-07-28"
    assert result[0]["equity_krw"] == 10_120_000
    assert result[1]["date"] == "2026-07-27"
    assert result[1]["equity_krw"] == 10_050_000


def test_recent_signals_returns_newest_first_with_limit(settings):
    # Arrange — 3개 이상의 구별 가능한 시그널
    (settings.stock_analyzer_path / "signal_log.json").write_text(
        json.dumps({"signals": [
            {"symbol": "AMAT", "action": "매수", "entry_date": "2026-07-08",
             "entry_price": 570.5, "score": 68.7, "rsi": 50.2,
             "return_pct": None},
            {"symbol": "TSLA", "action": "매수", "entry_date": "2026-07-15",
             "entry_price": 250.0, "score": 55.0, "rsi": 45.0,
             "return_pct": 2.0},
            {"symbol": "AAPL", "action": "🟢 매수", "entry_date": "2026-07-17",
             "entry_price": 333.74, "score": 61.0, "rsi": 72.0,
             "return_pct": 4.2},
        ]}),
        encoding="utf-8",
    )

    # Act
    result = get_recent_signals(settings, limit=2)

    # Assert — 최신 2개를 최신순으로 반환
    assert len(result) == 2
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["entry_date"] == "2026-07-17"
    assert result[1]["symbol"] == "TSLA"
    assert result[1]["entry_date"] == "2026-07-15"


def test_analyst_scores_returns_newest_first_with_limit(settings):
    # Arrange — 3개 이상의 구별 가능한 날짜 기록
    log = settings.stock_analyzer_path / "data" / "analyst_log" / "2026.jsonl"
    log.write_text(
        json.dumps({"date": "2026-07-21", "regime": "neutral",
                    "scores": {"AAPL": {"chart": 40.0, "ict": 50.0}}}) + "\n"
        + json.dumps({"date": "2026-07-22", "regime": "bull",
                      "scores": {"AAPL": {"chart": 50.0, "ict": 60.0}}}) + "\n"
        + json.dumps({"date": "2026-07-23", "regime": "bull",
                      "scores": {"AAPL": {"chart": 73.8, "ict": 100.0}}}) + "\n",
        encoding="utf-8",
    )

    # Act
    result = get_analyst_scores(settings, limit=2)

    # Assert — 최신 2개를 최신순으로 반환
    assert len(result) == 2
    assert result[0]["date"] == "2026-07-23"
    assert result[0]["scores"]["AAPL"]["chart"] == 73.8
    assert result[1]["date"] == "2026-07-22"
    assert result[1]["scores"]["AAPL"]["chart"] == 50.0
