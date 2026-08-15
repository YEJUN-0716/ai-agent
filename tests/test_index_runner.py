"""인덱스 러너 — 매일 깨어나도 결과가 같은가.

이 러너는 매월 1~8일 **매일** 돈다. cron 이 휴장일을 모르기 때문인데, 그래서
멱등성이 기능이 아니라 전제다. 같은 달에 두 번 입금되면 성적이 조용히 위조되고
되돌릴 방법이 없다 — 여기서 막지 못하면 어디서도 못 막는다.

네트워크(환율·시세·배당·텔레그램)는 전부 monkeypatch 한다.
"""

from datetime import date

import pytest

import index_runner as ir
from modules import virtual_broker as vb

PRICES = {"ITOT": 120.0, "AGG": 98.0, "GLDM": 87.0}
FX = 1000.0


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """인덱스 장부를 임시 폴더로 돌리고 바깥세상을 전부 고정한 러너."""
    monkeypatch.setattr(vb, "STATE_FILE", str(tmp_path / "index_portfolio.json"))
    monkeypatch.setattr(vb, "INITIAL_CAPITAL_KRW", 0.0)     # 시작 자본 0원
    monkeypatch.setattr(ir, "DRY_RUN", False)
    monkeypatch.setattr(ir, "fetch_krw_per_usd", lambda *a, **k: FX)
    monkeypatch.setattr(vb, "last_close_price", lambda sym: PRICES.get(sym, 0.0))
    monkeypatch.setattr(vb, "next_open_price",
                        lambda sym, after: (PRICES[sym], "2026-08-04"))
    monkeypatch.setattr(ir, "_dividends_since", lambda sym, since: (0.0, None))
    sent = []
    monkeypatch.setattr(ir, "send_tg", lambda msg: (sent.append(msg), True)[1])
    ir.sent = sent          # 테스트가 읽는 발송 이력
    return ir


def test_dies_without_index_ledger(monkeypatch, tmp_path):
    """VIRTUAL_PORTFOLIO_FILE 이 안 잡혔으면 아무것도 안 하고 죽는다.

    스윙 장부에 인덱스 매수가 한 건이라도 섞이면 두 시스템의 성적을 영영
    분리할 수 없다. 파일이 하나뿐이므로 되돌릴 수도 없다.
    """
    monkeypatch.setattr(vb, "STATE_FILE", "virtual_portfolio.json")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="VIRTUAL_PORTFOLIO_FILE"):
        ir.run(now=date(2026, 8, 3))
    assert not (tmp_path / "virtual_portfolio.json").exists()


def test_five_runs_in_one_month_deposit_once(runner):
    day = date(2026, 8, 3)
    first = runner.run(now=day)
    assert first["deposited"] and len(first["orders"]) == 3
    assert not first["reported"]        # 주문 낸 날은 아직 체결 전이다

    second = runner.run(now=date(2026, 8, 4))
    assert not second["deposited"]      # 이미 이번 달 적립했다
    assert second["reported"]           # 체결이 끝났으니 이제 보고한다

    for d in (5, 6, 7):
        again = runner.run(now=date(2026, 8, d))
        assert not again["deposited"] and not again["orders"] and not again["reported"]

    state = vb.load_state()
    assert state["index_meta"]["deposit_count"] == 1
    assert state["index_meta"]["deposited_krw"] == ir.MONTHLY_KRW
    assert len(runner.sent) == 1
    buys = [t for t in state["trades"] if t["side"] == "buy"]
    assert len(buys) == 3


def test_next_month_deposits_again(runner):
    runner.run(now=date(2026, 8, 3))
    runner.run(now=date(2026, 8, 4))
    nxt = runner.run(now=date(2026, 9, 1))
    assert nxt["deposited"] and nxt["orders"]
    assert vb.load_state()["index_meta"]["deposit_count"] == 2


def test_same_dividend_is_not_credited_twice(runner, monkeypatch):
    """반영일 이후 배당만 받는다 — 러너가 기록한 날짜를 실제로 쓰는지 본다."""
    paid = date(2026, 8, 5)
    monkeypatch.setattr(
        ir, "_dividends_since",
        lambda sym, since: (1.0, paid.isoformat()) if since < paid else (0.0, None))

    runner.run(now=date(2026, 8, 3))     # 주문만 (아직 보유 없음 → 배당 없음)
    runner.run(now=date(2026, 8, 6))     # 체결 후 첫 배당 반영
    after_first = vb.load_state()["cash_krw"]
    assert vb.load_state()["index_meta"]["dividends"]

    got = runner.run(now=date(2026, 8, 7))
    assert got["dividends_usd"] == 0.0
    assert vb.load_state()["cash_krw"] == after_first


def test_benchmark_receives_dividends_too(runner, monkeypatch):
    """벤치 ITOT 도 배당을 받아 재투자한다.

    안 주면 벤치가 매년 배당수익률만큼 낮게 나와, 지고 있어도 이긴 것처럼
    보인다. 성공 판정 ②의 문턱이 −0.5%p 라 그 편향 하나로 판정이 뒤집힌다.
    """
    runner.run(now=date(2026, 8, 3))                    # 적립 → 벤치 주수 생김
    runner.run(now=date(2026, 8, 4))                    # 체결
    before = vb.load_state()["index_meta"]["bench_itot_shares"]
    assert before > 0

    paid = date(2026, 8, 5)
    monkeypatch.setattr(
        ir, "_dividends_since",
        lambda sym, since: (1.0, paid.isoformat())
        if sym == "ITOT" and since < paid else (0.0, None))
    runner.run(now=date(2026, 8, 6))

    after = vb.load_state()["index_meta"]["bench_itot_shares"]
    assert after == pytest.approx(before + 1.0 * (1 - ir.DIV_WITHHOLDING)
                                  * before / PRICES["ITOT"])


def test_runner_succeeds_without_telegram(runner, monkeypatch):
    """발송 실패는 러너 실패가 아니다. 대신 보고를 완료로 찍지 않는다."""
    monkeypatch.setattr(ir, "send_tg", lambda msg: False)
    runner.run(now=date(2026, 8, 3))
    second = runner.run(now=date(2026, 8, 4))
    assert not second["reported"]
    assert "last_report_month" not in vb.load_state()["index_meta"]

    # 다음 실행에서 다시 시도한다 — 발송이 되면 그때 완료로 찍힌다.
    monkeypatch.setattr(ir, "send_tg", lambda msg: True)
    assert runner.run(now=date(2026, 8, 5))["reported"]


def test_never_sells(runner):
    runner.run(now=date(2026, 8, 3))
    runner.run(now=date(2026, 8, 4))
    runner.run(now=date(2026, 9, 1))
    state = vb.load_state()
    assert all(o["side"] != "sell" for o in state["pending"])
    assert all(t["side"] != "sell" for t in state["trades"])


def test_report_carries_benchmark_and_tax(runner):
    """벤치마크 줄과 미실현 양도세는 성공 판정 기준이라 빠지면 안 된다."""
    runner.run(now=date(2026, 8, 3))
    runner.run(now=date(2026, 8, 4))
    msg = runner.sent[0]
    assert "벤치 ITOT 100% 적립 대비" in msg and "미실현 양도세" in msg
    assert "[인덱스 자동운용] 2026-08 (1회차)" in msg.splitlines()[0]
    # 이월된 자산도 숨기지 않는다 — 정수주 마찰이 보고서에 드러나야 한다.
    assert all(t in msg for t in ir.TARGETS)


def test_orders_never_exceed_cash(runner):
    """계획한 주문 총액이 장부 현금을 넘으면 예약 단계에서 터진다."""
    runner.run(now=date(2026, 8, 3))
    state = vb.load_state()
    assert vb.available_krw(state) >= 0
    assert state["cash_krw"] > 0        # 이월 현금은 남아 있다


def test_dry_run_report_admits_it_is_pre_settlement(runner, monkeypatch, capsys):
    """드라이런 보고서는 체결 전 값이라고 스스로 밝힌다.

    드라이런은 주문을 안 내므로 pending 이 비고, 그래서 적립 당일 바로 보고
    경로를 탄다 — 실전이라면 다음 실행 몫이다. 그 출력의 이월·보유·평가는
    매수가 반영되기 전 값이고, 경고가 없으면 리허설을 실전 결과로 읽게 된다.
    """
    monkeypatch.setattr(ir, "DRY_RUN", True)
    result = runner.run(now=date(2026, 8, 3))

    assert result["deposited"] and result["orders"]
    out = capsys.readouterr().out
    assert "[인덱스 자동운용] 2026-08 (1회차)" in out
    assert "※ DRY_RUN — 체결 전이라 이월·보유·평가 세 줄은 실제와 다릅니다" in out
    assert not runner.sent           # 드라이런은 발송하지 않는다


def test_real_run_report_has_no_dry_run_warning(runner, capsys):
    """실전 보고는 체결 뒤에 나가므로 경고를 달지 않는다."""
    runner.run(now=date(2026, 8, 3))        # 주문만, 보고 없음
    capsys.readouterr()
    second = runner.run(now=date(2026, 8, 4))   # 체결 뒤 보고

    assert second["reported"]
    assert "DRY_RUN" not in capsys.readouterr().out
    assert "DRY_RUN" not in runner.sent[0]
