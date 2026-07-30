import json

import pytest

from assistant.config import Settings
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
