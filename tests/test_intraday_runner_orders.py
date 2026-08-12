"""3b 러너의 **주문 처리 흐름** — 가짜 브로커로 네트워크 없이 통과시킨다.

러너는 하루 한 세션만 돌 수 있다. 첫 체결에서 죽으면 그날이 통째로 날아가고
다음 기회는 24시간 뒤다. 그래서 장 없이 미리 지나가 보는 다섯 단계:

    진입 체결 → 손절 스톱 걸기 → 부분체결 구제 → 목표/손절 청산 → 마감 청산

여기서 잡으려는 건 "수익이 나느냐"가 아니라 **계좌가 이상한 상태로 남느냐**다.
스톱 없는 포지션, 취소 안 된 지정가, 스톱과 목표가 동시에 나가 공매도가 되는
경우 — 셋 다 돈이 조용히 새는 모양이다.
"""
import pandas as pd
import pytest

import intraday_runner as ir


class FakeBroker:
    """주문 상태만 흉내 낸다. 체결은 테스트가 직접 시킨다."""

    def __init__(self):
        self.orders = {}
        self.last = {}          # 마지막 체결가 {티커: 가격}
        self.n = 0
        self.swept = False

    # ── 러너가 부르는 쪽 ────────────────────────────────────────────
    def _new(self, **kw):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {"id": oid, "status": "new", "filled_qty": "0",
                            "filled_avg_price": None, **kw}
        return {"id": oid, "status": "new"}

    def limit_buy(self, sym, qty, price, dry_run=False, tif="gtc", **kw):
        return self._new(symbol=sym, qty=str(qty), type="limit", tif=tif)

    def stop_sell(self, sym, qty, stop_price, dry_run=False, **kw):
        return self._new(symbol=sym, qty=str(qty), type="stop",
                         stop_price=stop_price)

    def market_sell(self, sym, qty, dry_run=False, **kw):
        o = self._new(symbol=sym, qty=str(qty), type="market")
        self.fill(o["id"], qty, self.last.get(sym, 0.0))   # 시장가는 즉시
        return o

    def cancel(self, oid):
        o = self.orders[oid]
        if o["status"] in ("filled", "canceled"):
            return False                     # 이미 끝난 주문 — 러너가 이걸 본다
        o["status"] = "canceled"
        return True

    def wait(self, oid, **kw):
        return dict(self.orders[oid])

    def get(self, oid):
        return dict(self.orders[oid])

    # ── 테스트가 부르는 쪽 ──────────────────────────────────────────
    def fill(self, oid, qty, price, status="filled"):
        self.orders[oid].update(status=status, filled_qty=str(qty),
                                filled_avg_price=str(price))

    def only(self, type_):
        return [o for o in self.orders.values() if o["type"] == type_]


@pytest.fixture
def fb(monkeypatch, tmp_path):
    b = FakeBroker()
    monkeypatch.setattr(ir, "LOG", tmp_path / "runner.jsonl")
    monkeypatch.setattr(ir, "DRY_RUN", False)
    monkeypatch.setattr(ir, "_order", b.get)
    monkeypatch.setattr(ir, "_sweep", lambda: setattr(b, "swept", True))
    monkeypatch.setattr(ir, "latest_trades",
                        lambda syms: {s: b.last[s] for s in syms if s in b.last})
    monkeypatch.setattr(ir.at, "place_limit_buy", b.limit_buy)
    monkeypatch.setattr(ir.at, "place_stop_sell", b.stop_sell)
    monkeypatch.setattr(ir.at, "place_market_sell", b.market_sell)
    monkeypatch.setattr(ir.at, "cancel_order", b.cancel)
    monkeypatch.setattr(ir.at, "wait_for_fill", b.wait)
    return b


NOW = pd.Timestamp("2026-08-12 18:00", tz="UTC")


def _state(**kw):
    st = {"pending": {}, "held": {}, "cooldown": {}, "day_r": 0.0}
    st.update(kw)
    return st


def _pending(fb, ref=100.0, stop=99.7, target=100.6, qty=10, submitted=NOW):
    o = fb.limit_buy("AAPL", qty, ref)
    return {"order_id": o["id"], "entry_ref": ref, "stop": stop,
            "target": target, "risk_pct": 0.30, "qty": qty,
            "submitted": submitted}


def _held(fb, ref=100.0, stop=99.7, target=100.6, qty=10, fill=100.0):
    so = fb.stop_sell("AAPL", qty, stop)
    return {"entry_ref": ref, "stop": stop, "target": target, "risk_pct": 0.30,
            "qty": qty, "fill": fill, "filled_at": NOW,
            "stop_order_id": so["id"]}


class TestEntry:
    def test_체결되면_손절_스톱이_걸린다(self, fb):
        st = _state(pending={"AAPL": _pending(fb)})
        fb.fill(st["pending"]["AAPL"]["order_id"], 10, 99.95)

        ir._reconcile(st, NOW)

        assert not st["pending"]
        assert st["held"]["AAPL"]["fill"] == 99.95
        stops = fb.only("stop")
        assert len(stops) == 1 and stops[0]["stop_price"] == 99.7
        assert stops[0]["qty"] == "10"

    def test_부분체결은_버리지_않고_스톱을_건다(self, fb):
        """만료된 지정가에 3주가 박혀 있으면 그건 **스톱 없는 포지션**이다."""
        p = _pending(fb, submitted=NOW - pd.Timedelta(hours=3))   # 8봉=2시간 초과
        st = _state(pending={"AAPL": p})
        fb.fill(p["order_id"], 3, 99.9, status="partially_filled")

        ir._reconcile(st, NOW)

        assert not st["pending"]
        assert st["held"]["AAPL"]["qty"] == 3
        assert fb.only("stop")[0]["qty"] == "3"

    def test_미체결_만료는_취소하고_비운다(self, fb):
        p = _pending(fb, submitted=NOW - pd.Timedelta(hours=3))
        st = _state(pending={"AAPL": p})

        ir._reconcile(st, NOW)

        assert not st["pending"] and not st["held"]
        assert fb.orders[p["order_id"]]["status"] == "canceled"
        assert not fb.only("stop")          # 살 게 없으면 스톱도 없다

    def test_거절된_주문은_흔적을_안_남긴다(self, fb):
        p = _pending(fb)
        st = _state(pending={"AAPL": p})
        fb.orders[p["order_id"]]["status"] = "rejected"

        ir._reconcile(st, NOW)

        assert not st["pending"] and not st["held"]


class TestScan:
    """신호 → 주문. 플랜 계산 자체는 test_trade_plan 이 본다 — 여기서는
    러너가 그 플랜을 **어떻게 주문으로 옮기는지**만."""

    @pytest.fixture
    def scan(self, fb, monkeypatch):
        # 정규장 3일치 봉. 값은 아무거나 — 플랜은 아래에서 통째로 갈아끼운다.
        idx = pd.date_range("2026-08-10 13:30", periods=288, freq="15min")
        df = pd.DataFrame({"Open": 100.0, "High": 100.5, "Low": 99.5,
                           "Close": 100.0, "Volume": 1000.0}, index=idx)
        monkeypatch.setattr(ir, "load_intraday", lambda *a, **k: ({}, {"AAPL": df}))
        monkeypatch.setattr(ir, "UNIVERSE", ["AAPL"])
        return fb

    def _plan(self, **kw):
        p = {"valid": True, "direction": "long", "risk_pct": 0.50,
             "reason_invalid": "", "entry": {"ref": 100.0}, "stop": 99.5,
             "targets": [101.5], "rr": [3.0], "confidence": "medium"}
        p.update(kw)
        return p

    def _run(self, monkeypatch, st, plan):
        monkeypatch.setattr(ir, "build_trade_plan", lambda *a, **k: plan)
        ir._scan(st, 100_000.0, pd.Timestamp("2026-08-12 19:45"), NOW)

    def test_지정가는_day_로_건다(self, scan, monkeypatch):
        """GTC 면 러너가 하드킬당했을 때 주문이 밤을 넘겨, 다음 날 아침
        어제 계획으로 체결된다 — 손절도 안 걸린 채로."""
        st = _state()
        self._run(monkeypatch, st, self._plan())

        assert scan.only("limit")[0]["tif"] == "day"
        assert st["pending"]["AAPL"]["target"] == 101.5

    def test_명목가_상한이_수량을_정한다(self, scan, monkeypatch):
        st = _state()
        self._run(monkeypatch, st, self._plan())
        # 위험기준 1,000주 vs 명목가상한 100주
        assert scan.only("limit")[0]["qty"] == "100"

    def test_손절폭이_좁으면_주문을_안_낸다(self, scan, monkeypatch):
        st = _state()
        self._run(monkeypatch, st, self._plan(risk_pct=0.20, stop=99.8))

        assert not st["pending"] and not scan.only("limit")

    def test_쿨다운_중인_종목은_건너뛴다(self, scan, monkeypatch):
        st = _state(cooldown={"AAPL": NOW + pd.Timedelta(minutes=30)})
        self._run(monkeypatch, st, self._plan())

        assert not st["pending"]

    def test_보유_중인_종목은_또_안_산다(self, scan, monkeypatch):
        st = _state(held={"AAPL": _held(scan)})
        self._run(monkeypatch, st, self._plan())

        assert not st["pending"]


class TestExit:
    def test_스톱이_터지면_마이너스1R_과_슬리피지가_남는다(self, fb):
        h = _held(fb)
        st = _state(held={"AAPL": h})
        fb.fill(h["stop_order_id"], 10, 99.69)      # 손절가 99.7 에서 1bp 밀림

        ir._reconcile(st, NOW)

        assert not st["held"]
        # (99.69-100)/(100-99.7) = -1.033R
        assert round(st["day_r"], 3) == -1.033
        assert st["cooldown"]["AAPL"] == NOW + pd.Timedelta(minutes=45)
        row = [r for r in _rows(ir.LOG) if r["event"] == "exit"][-1]
        assert row["reason"] == "stop"
        assert round(row["slip_bp"], 1) == 1.0     # (99.7-99.69)/99.7*1e4

    def test_목표에_닿으면_스톱을_걷고_시장가로_턴다(self, fb):
        h = _held(fb)
        st = _state(held={"AAPL": h})
        fb.last["AAPL"] = 100.62                    # 목표 100.6 통과

        ir._reconcile(st, NOW)

        assert not st["held"]
        assert fb.orders[h["stop_order_id"]]["status"] == "canceled"
        assert len(fb.only("market")) == 1
        assert round(st["day_r"], 2) == round((100.62 - 100.0) / 0.3, 2)

    def test_목표_직전에_스톱이_터졌으면_스톱으로_센다(self, fb):
        """취소가 실패했다는 건 이미 체결됐다는 뜻이다. 그대로 시장가를 내면
        없는 주식을 팔아 **공매도**가 된다."""
        h = _held(fb)
        st = _state(held={"AAPL": h})
        fb.last["AAPL"] = 100.62
        fb.fill(h["stop_order_id"], 10, 99.70)
        # 스톱 조회는 아직 'new' 로 보이고 취소만 실패하는 순간을 만든다.
        seen = {"n": 0}
        real_get = fb.get

        def flaky(oid):
            if oid == h["stop_order_id"] and seen["n"] == 0:
                seen["n"] = 1
                return {**real_get(oid), "status": "new"}
            return real_get(oid)

        ir._order = flaky
        try:
            ir._reconcile(st, NOW)
        finally:
            ir._order = real_get

        assert not st["held"]
        assert not fb.only("market")            # 시장가를 내면 안 된다
        assert round(st["day_r"], 2) == -1.0

    def test_목표에_안_닿으면_들고_있는다(self, fb):
        st = _state(held={"AAPL": _held(fb)})
        fb.last["AAPL"] = 100.4

        ir._reconcile(st, NOW)

        assert "AAPL" in st["held"] and not fb.only("market")


class TestShutdown:
    def test_마감청산은_보유를_털고_대기주문을_취소한다(self, fb):
        h = _held(fb)
        p = _pending(fb)
        st = _state(held={"AAPL": h}, pending={"MSFT": p})
        fb.last["AAPL"] = 100.1

        ir._shutdown(st, "eod", NOW)

        assert not st["held"] and not st["pending"]
        assert fb.orders[h["stop_order_id"]]["status"] == "canceled"
        assert fb.orders[p["order_id"]]["status"] == "canceled"
        assert len(fb.only("market")) == 1
        assert fb.swept                          # 러너가 놓친 게 있어도 브로커가 턴다
        assert [r for r in _rows(ir.LOG) if r["event"] == "exit"][-1]["reason"] == "eod"

    def test_한_종목이_실패해도_나머지를_턴다(self, fb, monkeypatch):
        st = _state(held={"AAPL": _held(fb), "MSFT": _held(fb)})
        fb.last.update({"AAPL": 100.1, "MSFT": 100.1})
        real = fb.market_sell

        def boom(sym, qty, **kw):
            if sym == "AAPL":
                raise RuntimeError("브로커 오류")
            return real(sym, qty, **kw)

        monkeypatch.setattr(ir.at, "place_market_sell", boom)
        ir._shutdown(st, "eod", NOW)

        assert "MSFT" not in st["held"]          # 나머지는 털렸다
        assert fb.swept                          # 남은 AAPL 은 안전망이 받는다


class TestBarTiming:
    @pytest.mark.parametrize("hhmm,last,due", [
        ("18:01", None, True),        # 경계 지나고 1분 → 그린다
        ("18:00", None, False),       # 경계 직후 — 봉이 아직 안 실려 있다
        ("18:07", pd.Timestamp("2026-08-12 18:00", tz="UTC"), False),  # 이미 그렸다
        ("18:16", pd.Timestamp("2026-08-12 18:00", tz="UTC"), True),   # 다음 봉
    ])
    def test_봉마다_한_번만(self, hhmm, last, due):
        now = pd.Timestamp(f"2026-08-12 {hhmm}", tz="UTC")
        assert ir.scan_due(now, last) is due

    def test_진행_중인_봉은_안_본다(self):
        # 18:16 에 마감된 마지막 봉은 18:00 시작분이다 (18:15 봉은 진행 중).
        now = pd.Timestamp("2026-08-12 18:16", tz="UTC")
        assert ir.bar_cutoff(now) == pd.Timestamp("2026-08-12 18:00")


def _rows(path):
    import json
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
