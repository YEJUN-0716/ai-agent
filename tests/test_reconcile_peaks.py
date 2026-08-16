"""peak_prices 대사(reconcile) — 플랜 포지션이 거짓 경보를 만들지 않는지.

2026-08-14 실행에서 ALLY·KHC·VZ 3건이 "외부 매도 의심"으로 알림이 갔다.
셋 다 전날 플랜 손절/목표로 정상 청산된 것이었다. 가상 장부에는 외부 매도가
존재할 수 없으므로 이 알림이 뜨면 그 자체가 결함이다.
"""
import paper_trade_runner_toss as runner


def _pos(sym, plan=None, price=100.0):
    return {"symbol": sym, "current_price": str(price),
            "avg_entry_price": str(price), "_raw": {"plan": plan} if plan else {}}


def _reconcile(peaks, positions):
    """러너 본문과 같은 방식으로 legacy 를 갈라 대사한다."""
    alerts = []
    legacy = [p for p in positions if not (p.get("_raw") or {}).get("plan")]
    out = runner.reconcile_positions(peaks, positions, legacy, alerts.append)
    return out, alerts


def test_플랜_포지션은_peak를_만들지_않는다():
    peaks, alerts = _reconcile({}, [_pos("F", plan={"stop": 13.0})])
    assert peaks == {}, "플랜 포지션에 peak 가 생기면 청산 뒤 거짓 ghost 가 된다"
    assert alerts == []


def test_이미_들어간_플랜_peak는_조용히_빠진다():
    peaks, alerts = _reconcile({"F": 14.4}, [_pos("F", plan={"stop": 13.0})])
    assert peaks == {}
    assert alerts == [], "보유 중인 종목으로 경보를 울리면 안 된다"


def test_플랜_청산_다음날_경보가_없다():
    # 어제 F 를 플랜 라인으로 청산 → 오늘 계좌에 없다. 예전엔 여기서
    # "외부 매도 의심" 이 떴다. 수정 후엔 peaks 에 F 가 애초에 없다.
    peaks, alerts = _reconcile({}, [_pos("MSFT", plan={"stop": 480.0})])
    assert alerts == []


def test_legacy_보유분은_그대로_추적한다():
    peaks, alerts = _reconcile({}, [_pos("KHC", price=24.8)])
    assert peaks == {"KHC": 24.8}, "트레일링 대상은 여전히 peak 가 필요하다"


def test_진짜_ghost는_알린다():
    # legacy 로 추적하던 종목이 계좌에서 사라졌다 → 이건 진짜 이상하다.
    peaks, alerts = _reconcile({"KHC": 24.8}, [_pos("F", plan={"stop": 13.0})])
    assert peaks == {}
    assert len(alerts) == 1 and "KHC" in alerts[0]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
