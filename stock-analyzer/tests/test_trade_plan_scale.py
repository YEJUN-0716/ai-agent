import numpy as np
import pandas as pd
import pytest

from modules import trade_plan as tp
from modules.ict_analysis import calc_ict_adjustment
from modules.trade_plan import MIN_BARS, build_trade_plan


def _wave(n: int, seed: int = 7) -> pd.DataFrame:
    """결정적인 가짜 봉. 추세 + 잔물결이라 구조 신호가 실제로 잡힌다."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, n))
    high = close + rng.uniform(0.2, 1.2, n)
    low = close - rng.uniform(0.2, 1.2, n)
    open_ = close - rng.normal(0, 0.4, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": np.full(n, 1e6)}, index=idx)


def test_scale_1_matches_no_scale_argument():
    """scale=1 은 인자를 안 준 것과 글자 그대로 같아야 한다."""
    df = _wave(300)
    assert build_trade_plan(df) == build_trade_plan(df, scale=1)
    assert calc_ict_adjustment(df) == calc_ict_adjustment(df, scale=1)


def test_scale_raises_min_bars_requirement():
    """scale 을 키우면 필요한 워밍업 봉 수도 같이 커진다."""
    df = _wave(MIN_BARS + 5)          # scale=1 에는 충분, scale=4 에는 부족
    assert build_trade_plan(df, scale=1)["reason_invalid"] != "데이터 부족"
    assert build_trade_plan(df, scale=4)["reason_invalid"] == "데이터 부족"


def test_scale_changes_the_plan_on_long_history():
    """창이 실제로 넓어지면 결과가 달라진다 — 인자가 먹히는지 확인."""
    df = _wave(1200)
    assert build_trade_plan(df, scale=1) != build_trade_plan(df, scale=4)


def test_scale_must_be_positive_int():
    df = _wave(300)
    with pytest.raises(ValueError):
        build_trade_plan(df, scale=0)


# ── 봉 종류 ─────────────────────────────────────────────────────────
# 실행등급 밴드(2.34/1.75/1.34%)와 손익분기 bp 는 **일봉** 13,336건으로 잰
# 값이다. 15분봉에 그대로 대면 손절폭이 중위 0.245% 라 전부 D 로 떨어져
# 상수가 된다 — 판정처럼 보이지만 정보가 0 이고, 화면이 인용하는 11bp 는
# 다른 봉의 숫자다. 그래서 D 가 아니라 "?"(모름) 로 답한다.

def _wave_15m(n: int, seed: int = 7) -> pd.DataFrame:
    """같은 값, 15분봉 인덱스. 기하는 그대로고 봉 종류만 다르다."""
    df = _wave(n, seed)
    df.index = pd.date_range("2024-01-01 09:30", periods=n, freq="15min")
    return df


def test_is_daily_reads_the_index():
    assert tp._is_daily(_wave(300))
    assert not tp._is_daily(_wave_15m(300))
    assert tp._is_daily(pd.DataFrame())          # 합성 프레임은 일봉으로 친다


def test_one_duplicated_date_does_not_ungrade_a_daily_panel():
    """중복 하나로 일봉 패널이 등급을 잃으면 그날 러너가 조용히 0 종목을 산다."""
    df = _wave(300)
    idx = df.index.to_list()
    idx[-1] = idx[-2]                            # 같은 날짜가 두 번
    df.index = pd.DatetimeIndex(idx)
    assert tp._is_daily(df)


def test_same_geometry_is_graded_on_daily_and_ungraded_intraday():
    """값이 같아도 봉이 다르면 등급이 다르다 — 밴드가 일봉 기준이라서."""
    daily = tp.build_trade_plan(_wave(300))
    intra = tp.build_trade_plan(_wave_15m(300))
    assert daily["cost_grade"] in ("A", "B", "C", "D")
    assert intra["cost_grade"] == tp.UNGRADED
    assert daily["risk_pct"] == pytest.approx(intra["risk_pct"])   # 기하는 그대로


def test_ungraded_plan_is_never_actionable():
    plan = tp._assemble_plan("long", 100.0, 95.0, 97.0, 90.0, [110.0, 120.0],
                             graded=False)
    assert plan["valid"] and not plan["actionable"]
    assert "일봉" in plan["reason_not_actionable"]


def test_atr_window_follows_scale(monkeypatch):
    """손절 완충 ATR 도 창이다 — 여기만 scale 을 안 타면 3.5시간짜리로 잡힌다."""
    seen = {}
    real = tp._atr

    def spy(df, window=tp.ATR_WINDOW):
        seen["window"] = window
        return real(df, window)

    monkeypatch.setattr(tp, "_atr", spy)
    tp.build_trade_plan(_wave(1200), scale=4)
    assert seen["window"] == tp.ATR_WINDOW * 4
