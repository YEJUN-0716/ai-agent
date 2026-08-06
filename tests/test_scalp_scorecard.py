"""스캘핑(15분봉) 성적 — 기록·채점·적중률의 규칙을 잠근다.

실제 data/ 는 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
import pandas as pd

from modules import analyst_log as al
from modules import analyst_scorecard as sc
from modules import scalp_log as sl


# ── 선행수익률 저장소 ────────────────────────────────────────────────

def test_returns_round_trip(tmp_path):
    sl.append_returns("2026-08-06", 26, {"AAPL": 0.8123456}, root=tmp_path)

    out = sl.load_returns(tmp_path)
    assert out[26]["2026-08-06"]["AAPL"] == 0.8123


def test_returns_separate_horizons_do_not_overwrite(tmp_path):
    sl.append_returns("2026-08-06", 26, {"AAPL": 1.0}, root=tmp_path)
    sl.append_returns("2026-08-06", 78, {"AAPL": 3.0}, root=tmp_path)

    out = sl.load_returns(tmp_path)
    assert out[26]["2026-08-06"]["AAPL"] == 1.0
    assert out[78]["2026-08-06"]["AAPL"] == 3.0


def test_same_date_and_horizon_is_replaced(tmp_path):
    """같은 (날짜, 지평)이 두 번 들어가면 그 날이 성적에 두 번 계산된다."""
    sl.append_returns("2026-08-06", 26, {"AAPL": 1.0}, root=tmp_path)
    sl.append_returns("2026-08-06", 26, {"AAPL": 9.0}, root=tmp_path)

    assert sl.load_returns(tmp_path)[26]["2026-08-06"]["AAPL"] == 9.0


def test_empty_root_is_not_an_error(tmp_path):
    assert sl.load_returns(tmp_path / "없는폴더") == {}


# ── 판정 방향 적중률 ─────────────────────────────────────────────────

def _day(date, scores):
    return {"date": date, "regime": "bull", "scores": scores}


def test_verdict_hit_counts_direction():
    days = [_day("2026-08-06", {"UP": {"combined": 70.0},        # 매수
                                "DOWN": {"combined": 30.0}})]    # 매도
    returns = {"2026-08-06": {"UP": 1.5, "DOWN": -2.0}}

    out = sc.verdict_hit_rate(days, returns)
    assert (out["hits"], out["n"], out["hit_rate"]) == (2, 2, 100.0)
    assert (out["buy_n"], out["sell_n"]) == (1, 1)


def test_wrong_direction_is_a_miss():
    days = [_day("2026-08-06", {"UP": {"combined": 70.0}})]
    out = sc.verdict_hit_rate(days, {"2026-08-06": {"UP": -1.0}})

    assert out["hits"] == 0 and out["hit_rate"] == 0.0


def test_neutral_is_excluded_from_denominator():
    """중립을 분모에 넣으면 적중률이 언제나 50% 쪽으로 눌린다."""
    days = [_day("2026-08-06", {"UP": {"combined": 70.0},
                                "MEH": {"combined": 50.0}})]
    out = sc.verdict_hit_rate(days, {"2026-08-06": {"UP": 1.0, "MEH": 5.0}})

    assert out["n"] == 1 and out["neutral_n"] == 1 and out["hit_rate"] == 100.0


def test_flat_return_is_a_miss():
    """0% 는 방향을 맞힌 게 아니다."""
    days = [_day("2026-08-06", {"UP": {"combined": 70.0}})]
    out = sc.verdict_hit_rate(days, {"2026-08-06": {"UP": 0.0}})

    assert out["hits"] == 0


def test_no_returns_yet_gives_none_not_zero():
    """채점 전과 '전부 틀림'은 다르다 — 0% 로 보여주면 안 된다."""
    out = sc.verdict_hit_rate([_day("2026-08-06", {"A": {"combined": 70.0}})], {})

    assert out["n"] == 0 and out["hit_rate"] is None


def test_verdict_threshold_matches_the_screen():
    """성적표가 화면과 다른 문턱을 쓰면 화면의 판정을 재는 게 아니다."""
    import app

    for score in (0, 39.9, 40, 50, 64.9, 65, 100):
        assert app._team_verdict(score) == sc.verdict_of(score)


# ── 겹침 보정 lag ────────────────────────────────────────────────────

def test_sessions_for_bars_converts_bars_to_records():
    """기록은 하루 한 번이다 — lag 에 봉 수를 넘기면 유효표본이 0 에 붙는다."""
    assert sc.sessions_for_bars(26) == 1
    assert sc.sessions_for_bars(78) == 3
    assert sc.sessions_for_bars(1) == 1      # 0 이 되면 안 된다


# ── 기록 → 채점 배선 ─────────────────────────────────────────────────

def _bars(n, start_price=100.0, step=1.0):
    idx = pd.date_range("2026-08-06 13:30", periods=n, freq="15min")
    return pd.Series([start_price + step * i for i in range(n)], index=idx)


def test_resolve_writes_returns_for_recorded_day(tmp_path):
    import signal_worker

    scores_root, returns_root = tmp_path / "scores", tmp_path / "returns"
    series = _bars(30)
    al.append_day("2026-08-06", "bull", {"AAPL": {"chart": 60.0, "ict": 70.0}},
                  root=scores_root, asof=series.index[0].isoformat())

    written = signal_worker.resolve_scalp_returns(
        {"AAPL": series}, score_root=scores_root, returns_root=returns_root)

    # 26봉 뒤(100 → 126)는 계산되고, 78봉 뒤는 아직 미래가 오지 않았다.
    assert written == 1
    out = sl.load_returns(returns_root)
    assert out[26]["2026-08-06"]["AAPL"] == 26.0
    assert 78 not in out


def test_resolve_skips_already_scored(tmp_path):
    """이미 채점된 날을 다시 계산하면, 봉이 사라진 뒤 기록을 빈 값으로 덮는다."""
    import signal_worker

    scores_root, returns_root = tmp_path / "scores", tmp_path / "returns"
    series = _bars(30)
    al.append_day("2026-08-06", "bull", {"AAPL": {"chart": 60.0, "ict": 70.0}},
                  root=scores_root, asof=series.index[0].isoformat())
    sl.append_returns("2026-08-06", 26, {"AAPL": 99.0}, root=returns_root)

    written = signal_worker.resolve_scalp_returns(
        {"AAPL": series}, score_root=scores_root, returns_root=returns_root)

    assert written == 0
    assert sl.load_returns(returns_root)[26]["2026-08-06"]["AAPL"] == 99.0


def test_record_without_asof_is_not_scored(tmp_path):
    """기준 봉을 모르면 몇 봉 뒤를 셀 수 없다 — 날짜만으로 찍으면 안 된다."""
    import signal_worker

    scores_root, returns_root = tmp_path / "scores", tmp_path / "returns"
    al.append_day("2026-08-06", "bull", {"AAPL": {"chart": 60.0, "ict": 70.0}},
                  root=scores_root)

    written = signal_worker.resolve_scalp_returns(
        {"AAPL": _bars(30)}, score_root=scores_root, returns_root=returns_root)

    assert written == 0
    assert sl.load_returns(returns_root) == {}
