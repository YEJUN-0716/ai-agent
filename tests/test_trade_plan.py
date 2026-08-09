"""
modules/trade_plan.py 동작 고정 테스트.

두 층으로 나눈다.
1) 순수 기하 `_assemble_plan` / `_rr` — 좌표만 넣어 R:R·정렬·유효성을 전수 검증.
   여기가 매수/매도/손절 라인의 수학적 정확성을 못 박는 곳이다.
2) `build_trade_plan` — 합성 OHLCV 로 방향(롱/숏/none) 과 반환 스키마를 확인.
   픽스처가 의도한 ICT 편향에 실제로 들어와 있는지 calc_ict_adjustment 로 먼저
   검증한 뒤(_assert_bias) 판정을 확인한다 — 픽스처가 조용히 다른 국면으로
   흘러가면 테스트가 헛돌기 때문이다.

네트워크를 타지 않는다 — 전부 합성 프레임.
"""
import numpy as np
import pandas as pd
import pytest

from modules.ict_analysis import calc_ict_adjustment
from modules import trade_plan as tp


# ══════════════════════════════════════════════════════════════════
# 1) 순수 기하 — R:R 과 정렬
# ══════════════════════════════════════════════════════════════════
def test_rr_long_math():
    # 진입 96, 손절 93 → 위험 3 ; 목표 102 → 보상 6 → R:R 2.0
    assert tp._rr("long", 96.0, 93.0, 102.0) == pytest.approx(2.0)


def test_rr_short_math():
    # 진입 104, 손절 107 → 위험 3 ; 목표 98 → 보상 6 → R:R 2.0
    assert tp._rr("short", 104.0, 107.0, 98.0) == pytest.approx(2.0)


def test_rr_inverted_returns_nan():
    # 롱인데 목표가 진입보다 아래 → 보상 음수 → nan
    assert np.isnan(tp._rr("long", 100.0, 95.0, 99.0))
    # 롱인데 손절이 진입 위 → 위험 음수 → nan
    assert np.isnan(tp._rr("long", 100.0, 101.0, 110.0))


def test_assemble_long_valid():
    plan = tp._assemble_plan("long", current=98.0,
                             entry_low=95.0, entry_high=97.0, stop=93.0,
                             targets=[102.0, 108.0], min_rr=1.5)
    assert plan["direction"] == "long"
    assert plan["entry"]["ref"] == pytest.approx(96.0)
    assert plan["stop"] < plan["entry"]["low"] < plan["entry"]["ref"] < plan["targets"][0]
    assert plan["rr"][0] == pytest.approx(2.0)     # (102-96)/(96-93)
    assert plan["rr"][1] == pytest.approx(4.0)     # (108-96)/(96-93)
    assert plan["valid"] is True
    assert plan["reason_invalid"] == ""


def test_assemble_short_valid():
    plan = tp._assemble_plan("short", current=102.0,
                             entry_low=103.0, entry_high=105.0, stop=107.0,
                             targets=[98.0, 92.0], min_rr=1.5)
    assert plan["direction"] == "short"
    assert plan["entry"]["ref"] == pytest.approx(104.0)
    assert plan["stop"] > plan["entry"]["high"] > plan["entry"]["ref"] > plan["targets"][0]
    assert plan["rr"][0] == pytest.approx(2.0)     # (104-98)/(107-104)
    assert plan["valid"] is True


def test_assemble_long_low_rr_invalid():
    # 목표가 진입에 너무 가까워 R:R < 1.5 → 무효, 사유 명시
    plan = tp._assemble_plan("long", current=98.0,
                             entry_low=95.0, entry_high=97.0, stop=93.0,
                             targets=[97.0, 100.0], min_rr=1.5)
    assert plan["valid"] is False
    assert "손익비" in plan["reason_invalid"]


def test_default_gate_is_one_to_two():
    """기본 기준선은 1:2 다 — 이기는 판에서 잃는 판의 두 배는 벌어야 추천이다."""
    # 진입 96, 손절 93 → 위험 3. 목표 101.4 → 보상 5.4 = R 1.8 (미달)
    near = tp._assemble_plan("long", current=98.0, entry_low=95.0, entry_high=97.0,
                             stop=93.0, targets=[101.4])
    assert near["rr"][0] == pytest.approx(1.8)
    assert near["valid"] is False
    assert "손익비" in near["reason_invalid"]

    # 목표 102.0 → 보상 6.0 = R 2.0 (경계 통과)
    at_line = tp._assemble_plan("long", current=98.0, entry_low=95.0, entry_high=97.0,
                                stop=93.0, targets=[102.0])
    assert at_line["rr"][0] == pytest.approx(2.0)
    assert at_line["valid"] is True


def test_assemble_long_bad_ordering_invalid():
    # 손절이 진입 구간 위 → 정렬 불가 → 무효
    plan = tp._assemble_plan("long", current=98.0,
                             entry_low=95.0, entry_high=97.0, stop=98.0,
                             targets=[102.0], min_rr=1.5)
    assert plan["valid"] is False
    assert "정렬" in plan["reason_invalid"]


def test_assemble_short_bad_ordering_invalid():
    # 숏인데 손절이 진입 아래 → 정렬 불가
    plan = tp._assemble_plan("short", current=102.0,
                             entry_low=103.0, entry_high=105.0, stop=100.0,
                             targets=[98.0], min_rr=1.5)
    assert plan["valid"] is False


# ══════════════════════════════════════════════════════════════════
# 2) build_trade_plan — 합성 프레임으로 방향/스키마
# ══════════════════════════════════════════════════════════════════
def _oscillating(n: int = 80, level: float = 100.0) -> pd.DataFrame:
    """실제 캔들 바디가 있는 진동 프레임 (스윙 고·저점이 생기도록)."""
    i = np.arange(n)
    close = level + 3.0 * np.sin(i / 2.0)
    open_ = close - 0.3 * np.sin(i / 2.0)
    high = np.maximum(open_, close) + 0.8
    low = np.minimum(open_, close) - 0.8
    vol = np.full(n, 1_000_000.0)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol})


def _force_crt(df: pd.DataFrame, setup: str) -> pd.DataFrame:
    """
    마지막 4봉을 CRT Phase2 로 강제한다 (calc_ict_adjustment 에서 ±20).
    기준 Range(직전 3봉): High 102 / Low 98 → mid 100.
      bullish: 오늘 저가 97(<98 스윕), 종가 101 ∈ (100,102)
      bearish: 오늘 고가 103(>102 스윕), 종가 99 ∈ (98,100)
    """
    df = df.copy()
    loc = {c: df.columns.get_loc(c) for c in ("Open", "High", "Low", "Close")}
    for k in (-4, -3, -2):                      # 기준 Range 3봉
        df.iloc[k, loc["High"]] = 102.0
        df.iloc[k, loc["Low"]] = 98.0
        df.iloc[k, loc["Open"]] = 99.5
        df.iloc[k, loc["Close"]] = 100.5
    if setup == "bullish":
        df.iloc[-1, loc["Low"]] = 97.0
        df.iloc[-1, loc["High"]] = 101.5
        df.iloc[-1, loc["Open"]] = 99.0
        df.iloc[-1, loc["Close"]] = 101.0
    else:  # bearish
        df.iloc[-1, loc["High"]] = 103.0
        df.iloc[-1, loc["Low"]] = 98.5
        df.iloc[-1, loc["Open"]] = 100.5
        df.iloc[-1, loc["Close"]] = 99.0
    return df


def _assert_bias(df: pd.DataFrame, *, positive: bool):
    """픽스처가 의도한 방향 편향(|adj|>=BIAS_TH)에 실제로 들어와 있는지 확인."""
    adj = calc_ict_adjustment(df)["adjustment"]
    if positive:
        assert adj >= tp.BIAS_TH, f"강세 편향을 의도했으나 adj={adj}"
    else:
        assert adj <= -tp.BIAS_TH, f"약세 편향을 의도했으나 adj={adj}"


def test_build_long_direction():
    df = _force_crt(_oscillating(), "bullish")
    _assert_bias(df, positive=True)
    plan = tp.build_trade_plan(df)
    assert plan["direction"] == "long"
    assert plan["bias_score"] >= tp.BIAS_TH
    # 진입 구간이 잡혀 있고, 잡혔다면 정렬이 롱 방향으로 성립
    assert plan["entry"]["low"] <= plan["entry"]["high"]
    if plan["valid"]:
        assert plan["stop"] < plan["entry"]["low"]
        assert all(t > plan["entry"]["ref"] for t in plan["targets"])
        assert plan["rr"][0] >= tp.DEFAULT_MIN_RR


def test_build_short_direction():
    df = _force_crt(_oscillating(), "bearish")
    _assert_bias(df, positive=False)
    plan = tp.build_trade_plan(df)
    assert plan["direction"] == "short"
    assert plan["bias_score"] <= -tp.BIAS_TH
    if plan["valid"]:
        assert plan["stop"] > plan["entry"]["high"]
        assert all(t < plan["entry"]["ref"] for t in plan["targets"])
        assert plan["rr"][0] >= tp.DEFAULT_MIN_RR


def test_build_no_bias_returns_none():
    # 평평한 프레임 — 뚜렷한 구조 없음 → 방향 none, 무효
    df = _oscillating()
    plan = tp.build_trade_plan(df)
    if abs(calc_ict_adjustment(df)["adjustment"]) < tp.BIAS_TH:
        assert plan["direction"] == "none"
        assert plan["valid"] is False


def test_build_insufficient_bars():
    df = _oscillating(n=30)
    plan = tp.build_trade_plan(df)
    assert plan["direction"] == "none"
    assert plan["valid"] is False
    assert "데이터 부족" in plan["reason_invalid"]


def test_build_schema_keys():
    df = _force_crt(_oscillating(), "bullish")
    plan = tp.build_trade_plan(df)
    for key in ("direction", "bias_score", "confidence", "confluence", "current",
                "entry", "stop", "targets", "rr", "valid", "reason_invalid", "signals"):
        assert key in plan, f"반환 dict 에 {key} 누락"


# ── 숏 레짐 필터 ────────────────────────────────────────────────────
def _close_frame(closes) -> pd.DataFrame:
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                         "Close": c, "Volume": np.full(len(c), 1e6)})


def test_short_trend_ok_downtrend_passes():
    df = _close_frame(np.linspace(150, 100, 90))   # 하락추세
    ok, why = tp._short_trend_ok(df)
    assert ok is True and why == ""


def test_short_trend_ok_uptrend_blocks():
    df = _close_frame(np.linspace(100, 150, 90))   # 상승추세
    ok, why = tp._short_trend_ok(df)
    assert ok is False and "보류" in why


def test_short_trend_ok_insufficient_blocks():
    df = _close_frame(np.linspace(120, 100, 40))   # 봉 부족
    ok, why = tp._short_trend_ok(df)
    assert ok is False and "데이터 부족" in why


def test_short_regime_gate_blocks_uptrend(monkeypatch):
    # 방향은 강한 숏(|ICT|>=12, 저확신 억제 통과)이지만 종목이 상승추세면 레짐이 보류
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": -20, "signals": ["a", "b", "c"]})
    df = _close_frame(np.linspace(100, 150, 90))          # 상승추세
    blocked = tp.build_trade_plan(df, short_trend_filter=True)
    assert blocked["direction"] == "short"
    assert blocked["valid"] is False
    assert "보류" in blocked["reason_invalid"]
    # 필터를 끄면 레짐 사유로 막지 않는다 (라인 계산 단계로 진입)
    unfiltered = tp.build_trade_plan(df, short_trend_filter=False)
    assert "보류" not in unfiltered["reason_invalid"]


def test_low_conf_short_suppressed(monkeypatch):
    # |ICT| 가 medium 문턱(12) 아래인 숏은 억제된다 (실측상 저확신 숏 기대값 낮음)
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": -11, "signals": ["x"]})
    df = _close_frame(np.linspace(150, 100, 90))   # 하락추세라 레짐은 통과
    plan = tp.build_trade_plan(df)
    assert plan["direction"] == "short"
    assert plan["valid"] is False
    assert "억제" in plan["reason_invalid"]


def test_medium_conf_short_not_suppressed_for_conf(monkeypatch):
    # |ICT| >= 12 인 숏은 저확신 사유로는 막지 않는다
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": -15, "signals": ["x", "y"]})
    df = _close_frame(np.linspace(150, 100, 90))
    plan = tp.build_trade_plan(df)
    assert plan["direction"] == "short"
    assert "억제" not in plan["reason_invalid"]


# ── 방향 주입 (총괄 판정이 방향의 주인일 때) ────────────────────────
def test_forced_direction_overrides_ict_bias(monkeypatch):
    """ICT 는 롱이라 말해도 방향을 주입받았으면 그 방향으로 계획한다."""
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": +20, "signals": ["x"]})
    df = _close_frame(np.linspace(150, 100, 90))       # 하락추세 — 레짐은 통과
    plan = tp.build_trade_plan(df, direction="short")
    assert plan["direction"] == "short"


def test_forced_direction_against_structure_is_low_confidence(monkeypatch):
    """반대 방향 구조는 확신의 근거가 될 수 없다 — |ICT| 20 이어도 high 가 아니다."""
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": +20, "signals": ["x"]})
    df = _close_frame(np.linspace(150, 100, 90))
    plan = tp.build_trade_plan(df, direction="short")
    assert plan["confidence"] == "low"
    assert any("반대 방향" in s for s in plan["signals"])


def test_forced_short_still_obeys_regime_gate(monkeypatch):
    """방향을 주입해도 게이트는 그대로다 — 상승추세 종목 숏은 여전히 보류."""
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": -20, "signals": ["a", "b"]})
    df = _close_frame(np.linspace(100, 150, 90))       # 상승추세
    plan = tp.build_trade_plan(df, direction="short")
    assert plan["valid"] is False
    assert "보류" in plan["reason_invalid"]


def test_forced_long_works_without_ict_bias(monkeypatch):
    """ICT 가 방향을 못 정해도(|점수| < 10) 주입된 방향으로 라인까지 간다."""
    monkeypatch.setattr(tp, "calc_ict_adjustment",
                        lambda df, **kw: {"adjustment": 3, "signals": []})
    df = _force_crt(_oscillating(), "bullish")
    plan = tp.build_trade_plan(df, direction="long")
    assert plan["direction"] == "long"
    assert plan["targets"]


def test_forced_direction_rejects_unknown_value():
    # 경계에서 막지 않으면 'none' 같은 값이 조용히 롱으로 흘러간다.
    df = _close_frame(np.linspace(150, 100, 90))
    with pytest.raises(ValueError):
        tp.build_trade_plan(df, direction="매도")


# ── 실행 등급 (비용 내성) ────────────────────────────────────────────
#
# 확신도는 결과와 연결된 것이 측정되지 않았다 (롱 기준 high/medium/low 의
# 기대값 95% 구간이 겹친다, 2026-08-10). 실행 가치를 가르는 건 손절 거리다 —
# 총 기대값은 4분위가 거의 같은데 손익분기 비용이 29.6bp ~ 88.8bp 로 갈린다.

def test_cost_grade_follows_stop_distance():
    cost_grade = tp.cost_grade

    assert cost_grade(100.0, 97.0)[0] == "A"      # 3.0% > 2.34
    assert cost_grade(100.0, 98.0)[0] == "B"      # 2.0% > 1.75
    assert cost_grade(100.0, 98.5)[0] == "C"      # 1.5% > 1.34
    assert cost_grade(100.0, 99.5)[0] == "D"      # 0.5% — 실제 비용에 죽는다


def test_cost_grade_is_direction_agnostic():
    """숏은 손절이 위에 있다 — 거리는 절댓값으로 잰다."""
    assert tp.cost_grade(100.0, 103.0) == tp.cost_grade(100.0, 97.0)


def test_cost_grade_reports_the_distance():
    grade, pct = tp.cost_grade(200.0, 194.0)
    assert grade == "A" and pct == pytest.approx(3.0)


def test_every_plan_carries_a_grade():
    """유효하든 아니든 키 모양은 같아야 한다 — 화면이 KeyError 로 죽는다."""
    empty = tp.build_trade_plan(pd.DataFrame())
    assert empty["cost_grade"] == "D" and empty["risk_pct"] == 0.0

    plan = tp._assemble_plan("long", 100.0, 95.0, 97.0, 93.0, [102.0, 108.0])
    assert plan["cost_grade"] in ("A", "B", "C", "D")
    assert plan["risk_pct"] > 0


# ── 실행 문턱 (actionable) ───────────────────────────────────────────
#
# valid 와 별개의 질문이다. 한 플래그가 두 질문에 답하면 나중에 어느 쪽을
# 고쳐야 할지 알 수 없다. 걸러도 라인은 그대로 계산된다 — 없애는 게 아니라
# 관찰로 내려보내는 것이다.

def test_actionable_needs_valid_geometry_first():
    plan = tp._assemble_plan("long", 100.0, 95.0, 97.0, 93.0, [98.0], min_rr=2.0)
    assert plan["valid"] is False          # R:R 미달
    assert plan["actionable"] is False
    assert "손익비" in plan["reason_not_actionable"]


def test_good_long_at_grade_a_is_actionable():
    plan = tp._assemble_plan("long", 100.0, 94.0, 96.0, 89.0, [110.0, 120.0])
    assert plan["valid"] and plan["cost_grade"] == "A"
    assert plan["actionable"] is True
    assert plan["reason_not_actionable"] == ""


def test_tight_stop_is_valid_but_not_actionable():
    """기하는 멀쩡한데 손절이 너무 가까워 수수료에 먹히는 계획."""
    plan = tp._assemble_plan("long", 100.0, 99.4, 99.6, 99.0, [102.0, 104.0])
    assert plan["valid"] is True           # 정렬·손익비는 통과
    assert plan["cost_grade"] == "D"
    assert plan["actionable"] is False
    assert "수수료" in plan["reason_not_actionable"]
    # 라인은 그대로 남는다 — 관찰 기록이 있어야 필터를 되돌릴 수 있다
    assert plan["entry"]["ref"] > 0 and plan["targets"]


def test_short_is_never_actionable_whatever_the_grade():
    """숏은 등급이 못 구한다 — 어느 등급이든 총 기대값이 롱의 1/3."""
    plan = tp._assemble_plan("short", 100.0, 103.0, 105.0, 112.0, [86.0, 78.0])
    assert plan["valid"] and plan["cost_grade"] == "A"
    assert plan["actionable"] is False
    assert "short" in plan["reason_not_actionable"]


def test_vetoed_short_reports_the_veto_reason_not_a_stale_one():
    """게이트에 막힌 숏의 사유가 빈 계획의 '데이터 부족' 으로 덮이면 안 된다.

    _short_veto 는 empty 를 복사해서 만든다 — 거기에 남아 있던
    reason_not_actionable 을 갱신하지 않으면 화면이 엉뚱한 말을 한다
    (실물에서 AAPL 이 '데이터 부족' 으로 뜨던 것, 봉은 1597개였다).
    """
    import numpy as np

    n = 300
    close = pd.Series([100 + 0.25 * i for i in range(n)],   # 꾸준한 상승 = 숏 보류
                      index=pd.bdate_range("2025-01-01", periods=n))
    df = pd.DataFrame({
        "Open": close * 0.998, "High": close * 1.01,
        "Low": close * 0.99, "Close": close,
        "Volume": pd.Series([1_000_000] * n, index=close.index),
    })

    plan = tp.build_trade_plan(df, direction="short")
    assert plan["actionable"] is False
    assert "데이터 부족" not in plan["reason_not_actionable"]
    assert plan["reason_not_actionable"] == plan["reason_invalid"]
    assert np.isfinite(plan["current"])
