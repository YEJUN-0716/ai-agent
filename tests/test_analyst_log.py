"""애널리스트 점수 기록 저장소 — 왕복과 결측 처리 고정.

실제 data/analyst_log/ 는 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
import pytest

from modules import analyst_log as al


@pytest.fixture
def no_network(monkeypatch):
    """기록 경로에서 네트워크를 타는 두 곳을 막는다 — 재무 조회와 가중치 조회.

    막지 않으면 이 테스트들이 종목마다 yfinance 를 실제로 부른다.
    """
    import signal_worker

    monkeypatch.setattr(signal_worker, "_quant_score", lambda *a, **k: None)
    monkeypatch.setattr(signal_worker, "_directional_weights", lambda: {})
    monkeypatch.setattr(signal_worker, "QUANT_FETCH_SLEEP_SEC", 0)
    return signal_worker


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


# ── 더 적은 종목으로 덮어쓰지 않는다 ─────────────────────────────────
#
# 2026-08-06 에 5종목짜리 스모크 테스트가 같은 날 276종목 기록을 지웠다.
# 그 장은 되살릴 수 없다 — 점수는 재계산해도 그날의 퀀트(.info)는 시점
# 데이터라 다시 못 받는다.

def test_smaller_record_does_not_replace_bigger(tmp_path):
    full = {f"T{i}": {"chart": float(i)} for i in range(20)}
    al.append_day("2026-08-06", "bull", full, root=tmp_path)

    assert al.append_day("2026-08-06", "bull",
                         {"AAPL": {"chart": 1.0}}, root=tmp_path) is False

    days = al.load_days(tmp_path)
    assert len(days) == 1
    assert len(days[0]["scores"]) == 20


def test_bigger_record_replaces_smaller(tmp_path):
    """반쪽만 받은 날의 재실행은 그대로 갱신돼야 한다 — 막는 건 축소뿐이다."""
    al.append_day("2026-08-06", "bull", {"AAPL": {"chart": 1.0}}, root=tmp_path)

    full = {f"T{i}": {"chart": float(i)} for i in range(20)}
    assert al.append_day("2026-08-06", "bull", full, root=tmp_path) is True
    assert len(al.load_days(tmp_path)[0]["scores"]) == 20


# ── 일일 스캔 배선 ───────────────────────────────────────────────────
#
# 기록은 성적표의 유일한 재료다. 하지만 기록이 깨졌다고 일일 스캔(텔레그램
# 알림·시그널 로그)까지 죽으면 안 된다 — 부수 기능이 본체를 무너뜨리는 형태다.

def _panel(n_bars=300):
    import numpy as np
    import pandas as pd

    # 주말이면 직전 금요일로 스냅한다 — end 가 영업일이 아니면 pandas 버전에 따라
    # bdate_range 가 periods 보다 1 개 적은 인덱스를 줘서 토·일에만 길이가 어긋난다.
    _today = pd.Timestamp.today().normalize()
    _end = _today - pd.Timedelta(days=max(_today.weekday() - 4, 0))
    idx = pd.bdate_range(end=_end, periods=n_bars)
    assert len(idx) == n_bars, f"영업일 인덱스 길이 불일치: {len(idx)} != {n_bars}"
    close = pd.Series([100 + 0.2 * i + 3 * np.sin(i / 9) for i in range(n_bars)],
                      index=idx)
    return pd.DataFrame({
        "Open": close * 0.99, "High": close * 1.02,
        "Low": close * 0.98, "Close": close,
        "Volume": pd.Series([1_000_000] * n_bars, index=idx),
    })


def test_recording_failure_does_not_raise(monkeypatch, no_network):
    """기록이 깨져도 일일 스캔은 계속돼야 한다."""
    signal_worker = no_network

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


def _capture_append(monkeypatch, signal_worker):
    captured = {}
    monkeypatch.setattr(
        signal_worker.analyst_log, "append_day",
        lambda date_str, regime, scores, **kw: (captured.update(
            scores=scores, date=date_str) or True))
    return captured


def test_records_chart_and_ict(monkeypatch, no_network):
    """정상 경로 — 두 애널리스트 점수가 기록된다."""
    signal_worker = no_network

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": _panel()}))
    captured = _capture_append(monkeypatch, signal_worker)

    assert signal_worker.record_analyst_scores(["AAPL"], "bull") == 1
    assert set(captured["scores"]["AAPL"]) <= {"chart", "ict"}
    assert captured["scores"]["AAPL"]


# ── 기록 날짜는 봉에서 나온다 ────────────────────────────────────────
#
# datetime.now() 를 쓰던 동안 크론 지연(23:00 UTC → 실제 23:57~01:41)이
# 자정을 넘을 때마다 그 장이 다음 날짜로 저장됐다. 토요일 기록(2026-07-25,
# 08-01)이 남았고, 두 장이 같은 날짜로 겹쳐 07-27·08-05 장이 사라졌다.

def test_record_date_comes_from_last_bar_not_clock(monkeypatch, no_network):
    import pandas as pd

    signal_worker = no_network
    panel = _panel(n_bars=100)
    panel.index = pd.bdate_range(end="2026-07-23", periods=len(panel))

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": panel}))
    captured = _capture_append(monkeypatch, signal_worker)

    assert signal_worker.record_analyst_scores(["AAPL"], "bull") == 1
    assert captured["date"] == "2026-07-23"


# ── 퀀트+재무와 총괄 판정 ────────────────────────────────────────────
#
# 성적표가 화면 판정을 재려면 세 명이 다 기록돼야 하고, 그 셋을 섞은 점수를
# 기록 시점에 남겨야 한다 — 가중치가 주마다 바뀌어 나중에 다시 섞으면 그날
# 화면에 뜬 값이 아니게 된다.

def test_records_quant_and_verdict(monkeypatch, no_network):
    signal_worker = no_network

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": _panel()}))
    monkeypatch.setattr(signal_worker, "_quant_score", lambda tk, df: 30.0)
    captured = _capture_append(monkeypatch, signal_worker)

    assert signal_worker.record_analyst_scores(["AAPL"], "bull") == 1

    row = captured["scores"]["AAPL"]
    assert row["quant"] == 30.0
    # 가중치가 비었으므로 동일가중 — 세 점수의 단순평균이어야 한다.
    assert row["verdict"] == pytest.approx(
        (row["chart"] + row["ict"] + row["quant"]) / 3)


def test_verdict_uses_screen_weights(monkeypatch, no_network):
    """화면과 같은 가중치로 섞어야 화면 판정을 재는 것이 된다."""
    signal_worker = no_network

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": _panel()}))
    monkeypatch.setattr(signal_worker, "_quant_score", lambda tk, df: 0.0)
    monkeypatch.setattr(signal_worker, "_directional_weights",
                        lambda: {"chart": 0.0, "quant": 100.0, "ict": 0.0})
    captured = _capture_append(monkeypatch, signal_worker)

    signal_worker.record_analyst_scores(["AAPL"], "bull")

    # 퀀트에 가중치가 전부 실렸으므로 퀀트 점수 그대로여야 한다.
    assert captured["scores"]["AAPL"]["verdict"] == 0.0


def test_no_verdict_when_quant_missing(monkeypatch, no_network):
    """재무를 못 받으면 판정을 남기지 않는다 — 두 명만 섞은 값은 화면이 낸
    판정이 아니고, 50 으로 채우면 '못 받았다'가 '중립 판단'이 된다."""
    signal_worker = no_network

    monkeypatch.setattr(signal_worker.price_panel, "load_panel",
                        lambda tks, s, e: ({}, {"AAPL": _panel()}))
    captured = _capture_append(monkeypatch, signal_worker)

    signal_worker.record_analyst_scores(["AAPL"], "bull")

    row = captured["scores"]["AAPL"]
    assert "quant" not in row and "verdict" not in row
    assert "chart" in row          # 차트·ICT 기록은 그대로 살아 있다


def test_quant_score_drops_blocked_fetch(monkeypatch):
    """야후가 막히면 fundamental_score 가 50.0 을 준다 — 그걸 기록하면 안 된다."""
    import signal_worker

    monkeypatch.setattr(signal_worker.core, "fundamental_score",
                        lambda tk, df=None: (50.0, {"데이터없음": True}))
    assert signal_worker._quant_score("AAPL", None) is None

    monkeypatch.setattr(signal_worker.core, "fundamental_score",
                        lambda tk, df=None: (50.0, {"오류": "timeout"}))
    assert signal_worker._quant_score("AAPL", None) is None

    monkeypatch.setattr(signal_worker.core, "fundamental_score",
                        lambda tk, df=None: (61.5, {"업종": "Tech"}))
    assert signal_worker._quant_score("AAPL", None) == 61.5


def test_todays_quant_reads_daily_record(tmp_path):
    """15분봉 스텝은 앞 스텝이 남긴 퀀트를 읽는다 — 조회를 두 번 하지 않는다."""
    import signal_worker

    al.append_day("2026-08-07", "bull",
                  {"AAPL": {"chart": 60.0, "ict": 70.0, "quant": 44.0},
                   "MSFT": {"chart": 55.0}},          # 퀀트 없는 종목
                  root=tmp_path)

    got = signal_worker.todays_quant("2026-08-07", root=tmp_path)

    assert got == {"AAPL": 44.0}
    assert signal_worker.todays_quant("2026-08-06", root=tmp_path) == {}


def test_short_history_ticker_is_skipped(monkeypatch, no_network):
    """봉이 모자란 종목은 기록하지 않는다 — 지표가 잡음이다."""
    signal_worker = no_network

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


# ── 백필 재구성분과 실기록 ───────────────────────────────────────────
#
# 둘은 같은 물건이 아니다. 백필은 과거 봉으로 되돌려 계산한 값이라 "그날
# 화면에 뜬 점수" 라고 말할 수 없다. 파일을 갈라 두고, 합칠 때는 실기록이
# 이긴다. 표본이 무엇으로 이뤄졌는지는 따로 셀 수 있어야 한다.

def _mixed_logs(monkeypatch, tmp_path):
    live, back = tmp_path / "live", tmp_path / "back"
    monkeypatch.setattr(al, "LOG_DIRNAME", str(live))
    monkeypatch.setattr(al, "BACKFILL_DIRNAME", str(back))
    return live, back


def test_scoring_days_merges_backfill_before_live(monkeypatch, tmp_path):
    live, back = _mixed_logs(monkeypatch, tmp_path)
    al.write_days([("2026-05-01", "bull", {"A": {"chart": 1.0}}),
                   ("2026-06-01", "bear", {"A": {"chart": 2.0}})], back)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 3.0}}, root=live)

    days = al.load_scoring_days()
    assert [d["date"] for d in days] == ["2026-05-01", "2026-06-01", "2026-07-23"]
    assert al.sample_mix() == {"live": 1, "backfill": 2}


def test_live_record_wins_over_backfill_on_the_same_date(monkeypatch, tmp_path):
    """겹치면 실제로 화면에 뜬 쪽이 사실이다 — 재구성분은 버린다."""
    live, back = _mixed_logs(monkeypatch, tmp_path)
    al.write_days([("2026-07-23", "bull", {"A": {"chart": 1.0}})], back)
    al.append_day("2026-07-23", "bull", {"A": {"chart": 9.0}}, root=live)

    days = al.load_scoring_days()
    assert len(days) == 1
    assert days[0]["scores"]["A"]["chart"] == 9.0
    assert al.sample_mix() == {"live": 1, "backfill": 0}


def test_write_days_splits_by_year_and_sorts(tmp_path):
    n = al.write_days([("2027-01-05", "bull", {"A": {"chart": 1.0}}),
                       ("2026-12-31", "bear", {"A": {"chart": 2.0}}),
                       ("2026-03-02", "bull", {"A": {"chart": 3.0}})], tmp_path)

    assert n == 3
    assert [d["date"] for d in al.load_days(tmp_path)] == [
        "2026-03-02", "2026-12-31", "2027-01-05"]
    assert (tmp_path / "2026.jsonl").exists() and (tmp_path / "2027.jsonl").exists()


def test_write_days_drops_empty_scores(tmp_path):
    """값이 하나도 없는 날은 줄을 만들지 않는다 — 빈 날이 표본에 끼면
    채점된 날 수가 실제보다 많아 보인다."""
    n = al.write_days([("2026-03-02", "bull", {"A": {"chart": None}}),
                       ("2026-03-03", "bull", {"A": {"chart": 1.0}})], tmp_path)

    assert n == 1
    assert [d["date"] for d in al.load_days(tmp_path)] == ["2026-03-03"]
