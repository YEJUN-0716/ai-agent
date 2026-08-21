"""가중치 칸을 고르는 레짐이 **러너와 같은 자**인지 고정.

`ic_weights.json` 의 가중치표는 bull/neutral/bear 세 칸인데, 그 칸을 고르는
규칙이 두 개였다:

  - app.py `regime_of` — SPY/MA200 ±3% 버퍼, VIX 안 봄  → 스캔 비중·애널리스트 비중
  - modules/factor_engine.get_market_regime — SPY/MA200 + VIX → 러너의 보유 자리 상한

2019-12~2026-08 실측 1,680거래일 중 231일(13.8%)이 서로 다른 답이었고, 26일은
정반대였다(러너 bear ↔ 스캔 bull). 레짐이 한 칸 움직이면 비중의 14% 가 이동한다.
2026-08-21 에 자를 하나로 합쳤다 — `regime_of` 는 성적표·백필의 **라벨용**으로
남는다(과거 기록이 그 자로 찍혀 있어 바꾸면 기록이 안 이어진다).
"""
import pytest


@pytest.fixture
def app_mod():
    import app
    return app


def _set_regime(monkeypatch, app_mod, regime):
    import modules.factor_engine as fe
    monkeypatch.setattr(fe, "get_market_regime",
                        lambda: (regime, {"spy_ratio": 1.0, "vix": 20.0}))
    app_mod._weight_regime.clear()


def test_weight_regime_follows_the_runner(monkeypatch, app_mod):
    for regime in ("bull", "neutral", "bear"):
        _set_regime(monkeypatch, app_mod, regime)
        assert app_mod._weight_regime() == regime


def test_scan_weights_follow_the_runner_regime(monkeypatch, app_mod, tmp_path):
    """VIX 때문에 러너가 bear 라면 스캔도 bear 칸을 읽어야 한다."""
    _set_regime(monkeypatch, app_mod, "bear")
    got = app_mod._load_ic_factor_weights_4f()
    _set_regime(monkeypatch, app_mod, "bull")
    got_bull = app_mod._load_ic_factor_weights_4f()

    # 실제 ic_weights.json 을 읽는다 — 두 칸이 다르면 자가 붙어 있다는 뜻이다.
    assert got and got_bull
    assert got != got_bull


def test_label_ruler_is_still_there(app_mod):
    """성적표·백필이 쓰는 라벨용 자는 남아 있어야 한다 — 기록의 연속성."""
    assert callable(app_mod.regime_of)
    assert callable(app_mod.get_market_regime)


def test_network_failure_falls_back_to_neutral(monkeypatch, app_mod):
    import modules.factor_engine as fe

    def _boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(fe, "get_market_regime", _boom)
    app_mod._weight_regime.clear()
    assert app_mod._weight_regime() == "neutral"
