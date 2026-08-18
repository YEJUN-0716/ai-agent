"""애널리스트 방향성 가중치가 어느 블록에서 나오는지 고정.

PR #22 에서 밝혀진 것: regime_weights 의 mom_3m/mom_1m/low_vol 은 실전
스캔이 계산조차 하지 않는 정의다. 차트 애널리스트의 발언권이 그걸 근거로
정해지고 있었다. 실전이 실제로 쓰는 production_weights 를 우선 읽어야 한다.

실제 ic_weights.json 은 건드리지 않는다 — 전부 tmp_path 안에서 돈다.
"""
import json

import pytest

from modules import analyst_weights as aw


def _write(tmp_path, payload, monkeypatch):
    path = tmp_path / "ic_weights.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(aw, "_IC_WEIGHT_FILE", str(path))


LEGACY_BLOCK = {
    "bull": {"mom_3m": 0.10, "mom_1m": 0.10, "low_vol": 0.10,
             "value": 0.25, "quality": 0.25, "ict": 0.20},
}
PRODUCTION_BLOCK = {
    "bull": {"momentum": 0.60, "value": 0.20, "quality": 0.15, "low_vol": 0.05},
}


def test_prefers_production_weights(tmp_path, monkeypatch):
    """실전 스캔이 쓰는 블록을 우선 읽는다."""
    _write(tmp_path, {"production_weights": PRODUCTION_BLOCK,
                      "regime_weights": LEGACY_BLOCK}, monkeypatch)

    w = aw.load_analyst_weights("bull")

    assert sum(w.values()) == pytest.approx(1.0)
    # 차트 = momentum 0.60 + low_vol 0.05 = 0.65, ict 는 regime_weights 의 0.20
    # 합 = 0.65 + 0.35(value+quality) + 0.20 = 1.20
    assert w["차트+파동+모멘텀"] == pytest.approx(0.65 / 1.20)
    assert w["퀀트+재무"] == pytest.approx(0.35 / 1.20)
    assert w["ICT+CRT"] == pytest.approx(0.20 / 1.20)


def test_ict_keeps_a_share_under_production_block(tmp_path, monkeypatch):
    """ict 는 프로덕션 4팩터에 없다 — 0 으로 떨어지면 안 된다."""
    _write(tmp_path, {"production_weights": PRODUCTION_BLOCK,
                      "regime_weights": LEGACY_BLOCK}, monkeypatch)

    assert aw.load_analyst_weights("bull")["ICT+CRT"] > 0


def test_falls_back_to_regime_weights(tmp_path, monkeypatch):
    """아직 새 ic_weight_updater 를 안 돌렸으면 예전 경로로 후퇴한다."""
    _write(tmp_path, {"regime_weights": LEGACY_BLOCK}, monkeypatch)

    w = aw.load_analyst_weights("bull")

    # 차트 = 0.10 + 0.10 + 0.10 = 0.30, 전체 합 1.00
    assert w["차트+파동+모멘텀"] == pytest.approx(0.30)
    assert sum(w.values()) == pytest.approx(1.0)


def test_null_production_block_falls_back(tmp_path, monkeypatch):
    """production_weights 가 null(미측정)이어도 후퇴 경로가 살아 있어야 한다."""
    _write(tmp_path, {"production_weights": None,
                      "regime_weights": LEGACY_BLOCK}, monkeypatch)

    assert aw.load_analyst_weights("bull")["차트+파동+모멘텀"] == pytest.approx(0.30)


def test_unknown_regime_returns_none(tmp_path, monkeypatch):
    _write(tmp_path, {"regime_weights": LEGACY_BLOCK}, monkeypatch)
    assert aw.load_analyst_weights("bear") is None


def test_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "_IC_WEIGHT_FILE", str(tmp_path / "nope.json"))
    assert aw.load_analyst_weights("bull") is None


def test_all_zero_returns_none(tmp_path, monkeypatch):
    """측정 불가로 전부 0 이면 None — 호출부가 기본 비율로 후퇴한다."""
    _write(tmp_path, {"regime_weights": {"bull": {k: 0.0 for k in (
        "mom_3m", "mom_1m", "low_vol", "value", "quality", "ict")}}}, monkeypatch)

    assert aw.load_analyst_weights("bull") is None


def test_negative_weights_do_not_subtract(tmp_path, monkeypatch):
    """음수 가중치가 다른 애널리스트 몫을 갉아먹으면 안 된다."""
    _write(tmp_path, {"regime_weights": {"bull": dict(
        LEGACY_BLOCK["bull"], mom_3m=-0.5)}}, monkeypatch)

    w = aw.load_analyst_weights("bull")
    assert all(v >= 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)
