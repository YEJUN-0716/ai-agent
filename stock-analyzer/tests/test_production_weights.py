"""
프로덕션 스캔 가중치가 프로덕션 팩터의 IC 에서 나오는지 고정
=============================================================
프로덕션 스캔(`factor_scoring`)은 12-1 모멘텀(252→21봉)·252봉 변동성으로
랭킹하는데, 지금까지 그 비중은 `mom_3m + mom_1m` 의 IC 로 정해져 왔다.
스캔이 계산조차 하지 않는 팩터다.

여기서 잠그는 것:
  1. 프로덕션 가중치는 mom_12_1 / low_vol_252 의 IC 로 스케일링된다.
  2. 페이퍼트레이드(`factor_engine`)가 읽는 6팩터 가중치는 영향받지 않는다.
  3. 프로덕션 IC 가 없으면 IC_FLOOR 로 메우지 않고 아예 생략한다.
  4. n=0 인 팩터(unavailable)는 여기서도 0 으로 빠진다 — derive_ic_regime_weights
     에만 있던 가드가 실전 비중을 정하는 이쪽에는 없었다(2026-08-21 2차 점검).
"""
import pytest

from ic_weight_updater import (
    IC_FLOOR,
    derive_ic_regime_weights,
    derive_production_regime_weights,
)

BASE = {
    "bull": {"mom_3m": 0.35, "mom_1m": 0.10, "low_vol": 0.10,
             "value": 0.15, "quality": 0.15, "ict": 0.15},
    "bear": {"mom_3m": 0.15, "mom_1m": 0.05, "low_vol": 0.30,
             "value": 0.20, "quality": 0.20, "ict": 0.10},
}

# 2026-07-23 실측 형태 — legacy 모멘텀은 음(−), 프로덕션 정의는 양(+).
IC = {
    "mom_3m":      {"mean_ic": -0.0116, "n": 59},
    "mom_1m":      {"mean_ic": 0.0001, "n": 59},
    "low_vol":     {"mean_ic": -0.0259, "n": 59},
    "value":       {"mean_ic": 0.0178, "n": 59},
    "quality":     {"mean_ic": 0.0111, "n": 59},
    "ict":         {"mean_ic": 0.0054, "n": 59},
    "mom_12_1":    {"mean_ic": 0.0240, "n": 59},
    "low_vol_252": {"mean_ic": -0.0207, "n": 59},
}


def test_weights_sum_to_one_per_regime():
    w = derive_production_regime_weights(IC, BASE)
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)


def test_only_production_four_factors_present():
    """ict 는 프로덕션 4팩터에 없다 — 접는 과정에서 빠진다."""
    w = derive_production_regime_weights(IC, BASE)
    for regime in BASE:
        assert set(w[regime]) == {"momentum", "value", "quality", "low_vol"}


def test_momentum_scaled_by_production_ic_not_legacy():
    """모멘텀 비중이 mom_12_1 의 IC 로 정해진다.

    legacy(mom_3m −0.0116, mom_1m +0.0001)는 둘 다 IC_FLOOR 에 눌리는데
    mom_12_1 은 +0.0240 이라 4.8배다. 프로덕션 비중은 그만큼 커져야 한다.
    """
    prod = derive_production_regime_weights(IC, BASE)
    legacy = derive_ic_regime_weights(IC, BASE, unavailable=[])

    for regime in BASE:
        legacy_mom = legacy[regime]["mom_3m"] + legacy[regime]["mom_1m"]
        assert prod[regime]["momentum"] > legacy_mom, (
            f"[{regime}] 프로덕션 모멘텀 비중이 legacy 매핑보다 커지지 않았다"
        )


def test_momentum_weight_matches_hand_computation():
    """스케일링 산술을 직접 계산과 대조한다 (bull 레짐)."""
    w = derive_production_regime_weights(IC, BASE)

    bw = BASE["bull"]
    scaled = {
        "momentum": (bw["mom_3m"] + bw["mom_1m"]) * 0.0240,
        "value":    bw["value"] * 0.0178,
        "quality":  bw["quality"] * 0.0111,
        "low_vol":  bw["low_vol"] * IC_FLOOR,   # −0.0207 → 하한
    }
    total = sum(scaled.values())
    for factor, raw in scaled.items():
        assert w["bull"][factor] == pytest.approx(raw / total, abs=1e-4)


def test_negative_production_ic_is_floored_not_negative():
    """252봉 변동성 IC 가 음이어도 비중이 음수가 되지는 않는다."""
    w = derive_production_regime_weights(IC, BASE)
    for regime in BASE:
        assert w[regime]["low_vol"] > 0


def test_missing_production_ic_returns_none():
    """프로덕션 정의 IC 가 없으면 IC_FLOOR 로 메우지 않고 생략한다.

    없는 값을 하한으로 메우면 그 팩터가 조용히 최소 비중으로 눌린다 —
    지금 고치려는 고장과 정확히 같은 형태다.
    """
    partial = {k: v for k, v in IC.items() if k != "mom_12_1"}
    assert derive_production_regime_weights(partial, BASE) is None


def test_unmeasured_production_ic_returns_none():
    """n=0 은 '측정 못 함'이지 '예측력 0' 이 아니다."""
    unmeasured = dict(IC, mom_12_1={"mean_ic": 0.0, "n": 0})
    assert derive_production_regime_weights(unmeasured, BASE) is None


# ── 소비 측: 프로덕션 스캔이 어느 블록을 읽는가 ──────────────────────

PROD_BLOCK = {
    "bull": {"momentum": 0.55, "value": 0.20, "quality": 0.15, "low_vol": 0.10},
}
LEGACY_BLOCK = {
    "bull": {"mom_3m": 0.10, "mom_1m": 0.05, "low_vol": 0.30,
             "value": 0.25, "quality": 0.25, "ict": 0.05},
}


def test_scan_prefers_production_weights():
    """production_weights 가 있으면 그것을 쓴다 — 6팩터 매핑이 아니라."""
    from app import _pick_4f_weights

    got = _pick_4f_weights(
        {"production_weights": PROD_BLOCK, "regime_weights": LEGACY_BLOCK}, "bull")

    assert got["momentum"] == pytest.approx(0.55)
    assert sum(got.values()) == pytest.approx(1.0)


def test_scan_falls_back_to_legacy_mapping_when_absent():
    """아직 ic_weight_updater 를 새로 안 돌렸으면 예전 매핑으로 후퇴한다."""
    from app import _pick_4f_weights

    got = _pick_4f_weights({"regime_weights": LEGACY_BLOCK}, "bull")

    # momentum = mom_3m + mom_1m = 0.15. ict(0.05)는 빠지므로 4팩터 합은
    # 0.15 + 0.25 + 0.25 + 0.30 = 0.95, 그걸로 재정규화한다.
    assert got["momentum"] == pytest.approx(0.15 / 0.95)
    assert sum(got.values()) == pytest.approx(1.0)


def test_null_production_weights_does_not_crash():
    """production_weights 가 null(미측정)이어도 후퇴 경로가 살아 있어야 한다."""
    from app import _pick_4f_weights

    got = _pick_4f_weights(
        {"production_weights": None, "regime_weights": LEGACY_BLOCK}, "bull")
    assert got is not None
    assert sum(got.values()) == pytest.approx(1.0)


def test_unknown_regime_returns_none():
    from app import _pick_4f_weights

    assert _pick_4f_weights({"regime_weights": LEGACY_BLOCK}, "bear") is None


def test_paper_trade_weights_are_untouched():
    """페이퍼트레이드용 6팩터 가중치는 프로덕션 도출과 무관하게 그대로다."""
    before = derive_ic_regime_weights(IC, BASE, unavailable=[])
    derive_production_regime_weights(IC, BASE)
    after = derive_ic_regime_weights(IC, BASE, unavailable=[])
    assert before == after
    for regime in BASE:
        assert set(after[regime]) == set(BASE[regime])


# ── unavailable 가드 — 두 경로에 같은 규칙이 걸려 있어야 한다 ─────────
#
# EDGAR 가 죽으면 value·quality 의 IC 가 n=0 이 된다. 실제로 있었던 상태다
# (ic_weights.json 2026-07-19 · 07-20: ic_unavailable_factors = ["value","quality"]).
# 예전 코드는 그 둘을 regime_weights 에서만 0 으로 빼고, production_weights
# 에서는 IC_FLOOR 로 살려 bull 기준 8.6% 를 배분했다.

UNAVAIL_IC = {**IC,
              "value":   {"mean_ic": 0.0, "n": 0},
              "quality": {"mean_ic": 0.0, "n": 0}}


def test_unavailable_factors_get_zero_production_weight():
    w = derive_production_regime_weights(UNAVAIL_IC, BASE, unavailable=["value", "quality"])
    for regime in BASE:
        assert w[regime]["value"] == 0.0, regime
        assert w[regime]["quality"] == 0.0, regime


def test_remaining_production_weights_still_sum_to_one():
    w = derive_production_regime_weights(UNAVAIL_IC, BASE, unavailable=["value", "quality"])
    for regime in BASE:
        assert sum(w[regime].values()) == pytest.approx(1.0, abs=1e-3)


def test_unavailable_matches_the_sibling_guard():
    """같은 팩터가 두 경로에서 모두 0 이어야 한다 — 한쪽만 빼면 그게 옛 고장이다."""
    unavailable = ["value", "quality"]
    prod = derive_production_regime_weights(UNAVAIL_IC, BASE, unavailable=unavailable)
    legacy = derive_ic_regime_weights(UNAVAIL_IC, BASE, unavailable=unavailable)
    for regime in BASE:
        for factor in unavailable:
            assert legacy[regime][factor] == 0.0
            assert prod[regime][factor] == 0.0


def test_no_unavailable_leaves_weights_unchanged():
    assert (derive_production_regime_weights(IC, BASE, unavailable=[])
            == derive_production_regime_weights(IC, BASE))


def test_all_four_unavailable_returns_none():
    ic = {**IC, **{k: {"mean_ic": 0.0, "n": 0}
                   for k in ("value", "quality")}}
    assert derive_production_regime_weights(
        ic, BASE, unavailable=["mom_12_1", "low_vol_252", "value", "quality"]) is None
