"""3b 러너의 판단부 — 네트워크 없이 잡히는 부분만.

주문·폴링은 브로커 왕복이라 여기서 안 본다. 대신 **틀리면 조용히 돈이 새는**
세 가지를 잡는다: 진입 문턱, 수량, R 계산.
"""
import intraday_runner as ir


def _plan(**kw):
    base = {"valid": True, "direction": "long", "risk_pct": 0.50,
            "reason_invalid": ""}
    base.update(kw)
    return base


class TestEntryReason:
    def test_통과(self):
        assert ir.entry_reason(_plan()) == ""

    def test_손절폭이_좁으면_막는다(self):
        # 3a: 0.30% 미만은 왕복 6bp 가 0.4R 을 먹는다.
        assert "손절폭" in ir.entry_reason(_plan(risk_pct=0.29))

    def test_경계값은_통과(self):
        assert ir.entry_reason(_plan(risk_pct=0.30)) == ""

    def test_숏은_막는다(self):
        assert "숏" in ir.entry_reason(_plan(direction="short"))

    def test_무효셋업은_사유를_그대로(self):
        assert ir.entry_reason(
            _plan(valid=False, reason_invalid="손익비 부족")) == "손익비 부족"


class TestPositionSize:
    def test_명목가_상한이_수량을_정한다(self):
        # 15분봉 손절은 촘촘해서 위험 기준(0.5%)만 쓰면 명목가가 자본을 넘는다.
        # 자본 100k · 진입 100 · 손절 99.7(0.3%)
        #   위험기준   500 / 0.3   = 1,666주 = $166,600
        #   명목가상한 10,000 / 100 =   100주  ← 이쪽이 이긴다
        assert ir.position_size(100_000, 100.0, 99.7) == 100

    def test_손절이_넓으면_위험기준이_이긴다(self):
        # 손절 90 → 위험 10/주 → 500/10 = 50주 < 명목가상한 100주
        assert ir.position_size(100_000, 100.0, 90.0) == 50

    def test_손절이_진입_위면_0(self):
        assert ir.position_size(100_000, 100.0, 101.0) == 0

    def test_자본이_작으면_0주(self):
        assert ir.position_size(500, 100.0, 99.7) == 0


class TestTradeR:
    def test_손절이면_약_마이너스1R(self):
        # 계획대로 사서 계획대로 손절: -1R
        assert ir.trade_r(100.0, 99.7, 100.0, 99.7) == -1.0

    def test_위험은_계획_기준이다(self):
        # 계획보다 싸게(99.9) 샀어도 위험 1단위는 계획의 0.3 그대로다.
        # 손절가에 털려도 -0.667R — 잘 산 만큼 분자에서 이미 이득이다.
        assert round(ir.trade_r(100.0, 99.7, 99.9, 99.7), 3) == -0.667

    def test_목표_도달(self):
        # 위험 0.3 · 이익 0.6 → 2R (min_rr 2.0 이 통과시키는 최소치)
        assert round(ir.trade_r(100.0, 99.7, 100.0, 100.6), 2) == 2.0

    def test_위험이_0이면_0(self):
        assert ir.trade_r(100.0, 100.0, 100.0, 101.0) == 0.0
