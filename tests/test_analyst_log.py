"""애널리스트 점수 기록 저장소 — 왕복과 결측 처리 고정.

실제 data/analyst_log/ 는 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
from modules import analyst_log as al


def test_round_trip(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.14, "ict": 48.5}}, root=tmp_path)

    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert days[0]["date"] == "2026-07-23"
    assert days[0]["regime"] == "bull"
    # 소수 1자리로 절삭된다
    assert days[0]["scores"]["AAPL"]["chart"] == 62.1


def test_missing_slug_stays_missing(tmp_path):
    """계산 불가는 키를 뺀다 — 50 으로 채우면 '중립 판단'으로 성적에 섞인다."""
    al.append_day("2026-07-23", "bull", {"AAPL": {"chart": 62.1}}, root=tmp_path)

    day = al.load_days(tmp_path)[0]
    assert "ict" not in day["scores"]["AAPL"]


def test_none_score_is_dropped(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.1, "ict": None}}, root=tmp_path)

    assert "ict" not in al.load_days(tmp_path)[0]["scores"]["AAPL"]


def test_ticker_with_no_scores_is_dropped(tmp_path):
    al.append_day("2026-07-23", "bull",
                  {"AAPL": {"chart": 62.1}, "MSFT": {}}, root=tmp_path)

    assert "MSFT" not in al.load_days(tmp_path)[0]["scores"]


def test_days_are_sorted_across_years(tmp_path):
    al.append_day("2027-01-05", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-12-31", "bear", {"A": {"chart": 2.0}}, root=tmp_path)

    assert [d["date"] for d in al.load_days(tmp_path)] == ["2026-12-31", "2027-01-05"]


def test_since_filter(tmp_path):
    al.append_day("2026-07-01", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 2.0}}, root=tmp_path)

    assert len(al.load_days(tmp_path, since="2026-07-10")) == 1


def test_duplicate_date_is_replaced(tmp_path):
    """같은 날 스캔이 두 번 돌아도 그 날이 두 번 계산되면 안 된다."""
    al.append_day("2026-07-23", "bull", {"A": {"chart": 1.0}}, root=tmp_path)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 9.0}}, root=tmp_path)

    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert days[0]["scores"]["A"]["chart"] == 9.0


def test_missing_directory_returns_empty(tmp_path):
    assert al.load_days(tmp_path / "nope") == []


# ── 일일 스캔 배선 ───────────────────────────────────────────────────
#
# 기록은 성적표의 유일한 재료다. 하지만 기록이 깨졌다고 일일 스캔(텔레그램
# 알림·시그널 로그)까지 죽으면 안 된다 — 부수 기능이 본체를 무너뜨리는 형태다.

def _panel(n_bars=300):
    import numpy as np
    import pandas as pd

    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_bars)
    close = pd.Series([100 + 0.2 * i + 3 * np.sin(i / 9) for i in range(n_bars)],
                      index=idx)
    return pd.DataFrame({
        "Open": close * 0.99, "High": close * 1.02,
        "Low": close * 0.98, "Close": close,
        "Volume": pd.Series([1_000_000] * n_bars, index=idx),
    })


def test_recording_failure_does_not_raise(monkeypatch):
    """기록이 깨져도 일일 스캔은 계속돼야 한다."""
    import signal_worker

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": _panel()}))

    def _boom(*a, **k):
        raise RuntimeError("디스크 꽉 참")

    monkeypatch.setattr(signal_worker.analyst_log, "append_day", _boom)
    assert signal_worker.record_analyst_scores(["AAPL"], "bull") == 0


def test_panel_failure_does_not_raise(monkeypatch):
    """가격 패널을 못 받아도 스캔은 계속된다."""
    import signal_worker

    def _boom(*a, **k):
        raise RuntimeError("yfinance 다운")

    monkeypatch.setattr(signal_worker.price_panel, "load_panel", _boom)
    assert signal_worker.record_analyst_scores(["AAPL"], "bull") == 0


def test_empty_universe_records_nothing(monkeypatch):
    import signal_worker

    assert signal_worker.record_analyst_scores([], "bull") == 0


def test_records_chart_and_ict(monkeypatch, tmp_path):
    """정상 경로 — 두 애널리스트 점수가 기록된다."""
    import signal_worker

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": _panel()}))

    captured = {}

    def _capture(date_str, regime, scores, **kw):
        captured["scores"] = scores

    monkeypatch.setattr(signal_worker.analyst_log, "append_day", _capture)

    assert signal_worker.record_analyst_scores(["AAPL"], "bull") == 1
    assert set(captured["scores"]["AAPL"]) <= {"chart", "ict"}
    assert captured["scores"]["AAPL"]


def test_short_history_ticker_is_skipped(monkeypatch):
    """봉이 모자란 종목은 기록하지 않는다 — 지표가 잡음이다."""
    import signal_worker

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"NEW": _panel(n_bars=20)}))
    monkeypatch.setattr(signal_worker.analyst_log, "append_day",
                        lambda *a, **k: None)

    assert signal_worker.record_analyst_scores(["NEW"], "bull") == 0


# ── 국면 슬러그 ──────────────────────────────────────────────────────
#
# 팩터 타이밍의 regime 은 '저변동성 — 모멘텀 강조 + 금리하락(모멘텀↑)' 같은
# 표시용 문장이다. 그걸 그대로 기록하면 ic_weights.json 의 국면 키
# (bull/bear/neutral)와 맞지 않아 나중에 국면별 성적을 갈라 볼 수 없고,
# 문구가 바뀌는 순간 과거 기록과도 끊긴다.

def test_regime_slug_is_a_stable_key(monkeypatch):
    import signal_worker

    monkeypatch.setattr(signal_worker.core, "get_market_regime",
                        lambda *a, **k: ("bull", 3.2))

    assert signal_worker.market_regime_slug(["AAPL"]) == "bull"


def test_regime_slug_falls_back_to_neutral(monkeypatch):
    """국면 조회가 깨져도 기록 자체는 진행돼야 한다."""
    import signal_worker

    def _boom(*a, **k):
        raise RuntimeError("네트워크 없음")

    monkeypatch.setattr(signal_worker.core, "get_market_regime", _boom)

    assert signal_worker.market_regime_slug(["AAPL"]) == "neutral"


# ── 기록 전용 진입점 ─────────────────────────────────────────────────
#
# signal-alerts 의 크론이 꺼진 뒤(2026-07-20) 기록을 부르는 곳이 사라졌다.
# 매수 알림을 멈추는 것과 "누가 잘 맞히나" 를 재는 것은 별개의 결정이라
# 발송 없이 기록만 도는 경로가 따로 있어야 한다.

def test_record_only_records_and_succeeds(monkeypatch):
    import signal_worker

    monkeypatch.setenv("UNIVERSE", "AAPL,MSFT")
    monkeypatch.setattr(signal_worker.core, "get_market_regime",
                        lambda *a, **k: ("bear", -4.0))
    monkeypatch.setattr(signal_worker, "record_analyst_scores",
                        lambda tickers, regime: len(tickers))

    assert signal_worker.record_only_main() == 0


def test_record_only_fails_loudly_when_nothing_recorded(monkeypatch):
    """0종목 기록은 성공이 아니다 — 조용히 넘어가면 몇 달 뒤 빈 성적표를 본다."""
    import signal_worker

    monkeypatch.setenv("UNIVERSE", "AAPL")
    monkeypatch.setattr(signal_worker.core, "get_market_regime",
                        lambda *a, **k: ("bull", 1.0))
    monkeypatch.setattr(signal_worker, "record_analyst_scores",
                        lambda tickers, regime: 0)

    assert signal_worker.record_only_main() == 1


def test_record_only_passes_slug_not_prose(monkeypatch):
    """기록에 들어가는 국면은 표시용 문장이 아니라 슬러그다."""
    import signal_worker

    monkeypatch.setenv("UNIVERSE", "AAPL")
    monkeypatch.setattr(signal_worker.core, "get_market_regime",
                        lambda *a, **k: ("bull", 1.0))

    seen = {}

    def _record(tickers, regime):
        seen["regime"] = regime
        return len(tickers)

    monkeypatch.setattr(signal_worker, "record_analyst_scores", _record)
    signal_worker.record_only_main()

    assert seen["regime"] == "bull"


def test_record_only_fails_on_empty_universe(monkeypatch):
    import signal_worker

    monkeypatch.setenv("UNIVERSE", "  ")
    monkeypatch.setattr(signal_worker, "_resolve_universe", lambda raw: [])

    assert signal_worker.record_only_main() == 1
