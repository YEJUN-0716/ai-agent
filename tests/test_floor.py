"""PIXEL TRADING FLOOR — 데모 파이프라인이 가이드에 적힌 대로 도는지 본다.

특히 두 가지를 잠근다.
  - 모드별 호출 횟수(algo 13 / scalp 5 / attack 5) — 요금이 여기서 갈린다
  - 스캘핑·공격의 판정 기준 시장이 원화가 아니라 무기한이라는 것
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from floor import market, pipeline, report
from floor.agents import (
    ALGO,
    ATTACK,
    BASIS_KRW,
    BASIS_PERP,
    SCALP,
    UnknownMode,
    resolve_mode,
)
from floor.demo import Context, demo_runner
from floor.server import create_app


def _events(symbol: str, mode: str, reports_dir) -> list[dict]:
    return list(pipeline.run(symbol, mode, reports_dir=reports_dir))


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


# ── 호출 횟수 ─────────────────────────────────────────────


@pytest.mark.parametrize("mode,expected", [(ALGO, 13), (SCALP, 5), (ATTACK, 5)])
def test_모드별_클로드_호출_횟수가_가이드와_같다(mode, expected):
    assert mode.calls == expected


def test_에이전트_이벤트_수가_선언한_호출_횟수와_같다(tmp_path):
    for key, mode in (("algo", ALGO), ("scalp", SCALP), ("attack", ATTACK)):
        events = _events("BTC", key, tmp_path)
        assert len(_of(events, "agent")) == mode.calls


def test_토론은_BULL_BEAR가_네턴_번갈아_간다():
    debate = ALGO.steps[1]
    assert debate.order() == (("BULL", 1), ("BEAR", 2), ("BULL", 3), ("BEAR", 4))


# ── 판정 기준 시장 ────────────────────────────────────────


def test_스캘핑과_공격은_무기한_알고리즘은_원화_기준이다():
    assert SCALP.basis == BASIS_PERP
    assert ATTACK.basis == BASIS_PERP
    assert ALGO.basis == BASIS_KRW


def test_스캘핑은_부풀려진_원화_변동성_대신_무기한_변동성으로_계획한다(tmp_path):
    """원화 차트로 재면 손절 폭이 커져 멀쩡한 셋업이 청산 위험으로 기각된다."""
    snap = market.demo_snapshot(market.resolve_symbol("하이닉스"))
    assert snap.krw_vol_pct > snap.perp_vol_pct  # 한국 종목은 원화가 부풀려진다

    scalp = _of(_events("하이닉스", "scalp", tmp_path), "verdict")[0]
    algo = _of(_events("하이닉스", "algo", tmp_path), "verdict")[0]
    scalp_stop = abs(scalp["entry"] - scalp["stop"]) / scalp["entry"]
    algo_stop = abs(algo["entry"] - algo["stop"]) / algo["entry"]
    assert scalp_stop < algo_stop


def test_공격모드는_관망을_내지_않는다(tmp_path):
    for symbol in ("BTC", "TSLA", "삼성전자", "SOL", "DOGE"):
        verdict = _of(_events(symbol, "attack", tmp_path), "verdict")[0]
        assert verdict["action"] in ATTACK.actions
        assert verdict["action"] not in ("HOLD", "PASS")


# ── 심볼 해석 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,key",
    [
        ("하이닉스", "SKHYNIX"),
        ("000660", "SKHYNIX"),
        ("SKHYNIX-USDT", "SKHYNIX"),
        ("삼성", "SAMSUNG"),
        ("005930", "SAMSUNG"),
        ("btc", "BTC"),
        ("tsla", "TSLA"),
    ],
)
def test_사장님이_쓰는_표기를_전부_알아듣는다(raw, key):
    assert market.resolve_symbol(raw).key == key


def test_빈값과_이상한_글자는_거절한다():
    with pytest.raises(market.UnknownSymbol):
        market.resolve_symbol("   ")
    with pytest.raises(market.UnknownSymbol):
        market.resolve_symbol("../../etc/passwd")


def test_전광판은_한국_종목에서만_뜬다():
    assert market.resolve_symbol("하이닉스").board is True
    assert market.resolve_symbol("BTC").board is False
    assert market.demo_snapshot(market.resolve_symbol("BTC")).board_rows == ()


def test_탭비트는_네개_거래소_평균이고_추정으로_표시된다():
    rows = market.demo_snapshot(market.resolve_symbol("삼성전자")).board_rows
    estimated = [r for r in rows if r["estimated"]]
    assert len(estimated) == 1 and estimated[0]["exchange"] == market.TAPBIT
    real = [r["perp_usdt"] for r in rows if not r["estimated"]]
    assert len(real) == len(market.PERP_EXCHANGES)
    assert estimated[0]["perp_usdt"] == pytest.approx(sum(real) / len(real), abs=1e-4)


def test_모르는_모드는_쓸수있는_값을_알려주며_거절한다():
    with pytest.raises(UnknownMode) as exc:
        resolve_mode("초공격")
    assert "algo" in str(exc.value)


# ── 사건 순서 ─────────────────────────────────────────────


def test_콘솔_순서가_수집_헤드라인_회고_브리핑_판정이다(tmp_path):
    events = _events("BTC", "algo", tmp_path)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "meta" and kinds[1] == "quote"
    assert kinds[-1] == "done" and kinds[-2] == "saved"

    heads = [e["text"] for e in _of(events, "log") if e["text"].startswith("■")]
    assert heads == ["■ 데이터 수집 — BTC", "■ 뉴스 헤드라인", "■ 과거 판정 회고"]
    assert _of(events, "verdict")


def test_데모_시세는_합성값이라고_콘솔에_남긴다(tmp_path):
    texts = [e["text"] for e in _of(_events("BTC", "algo", tmp_path), "log")]
    assert any("합성값" in t for t in texts)


def test_알고리즘은_PM_판정으로_끝나고_스캘핑은_ACE로_끝난다(tmp_path):
    algo = _of(_events("BTC", "algo", tmp_path), "verdict")[0]
    scalp = _of(_events("BTC", "scalp", tmp_path), "verdict")[0]
    assert algo["pm_status"] in ("승인", "수정승인", "기각")
    assert scalp["pm_status"] is None


def test_에이전트가_실패하면_에러로_끊고_리포트를_남기지_않는다(tmp_path):
    def broken(agent: str, ctx: Context):
        if agent == "VIBE":
            raise RuntimeError("호출 실패")
        return demo_runner(agent, ctx)

    events = list(pipeline.run("BTC", "scalp", reports_dir=tmp_path, runner=broken))
    assert events[-1]["type"] == "error" and "VIBE" in events[-1]["text"]
    assert list(tmp_path.glob("*.md")) == []


# ── 리포트 ────────────────────────────────────────────────


def test_리포트가_규칙대로_저장되고_다시_읽힌다(tmp_path):
    saved = _of(_events("하이닉스", "algo", tmp_path), "saved")[0]
    assert saved["name"].endswith(".md") and "SKHYNIX" in saved["name"]

    text = report.read(tmp_path, saved["name"])
    assert "판정 기준 시장" in text
    assert "투자 조언이 아니며" in text
    assert "합성값입니다" in text

    listed = report.listing(tmp_path)
    assert len(listed) == 1 and listed[0].symbol == "SKHYNIX"


def test_저장된_판정이_다음_판단의_회고로_들어간다(tmp_path):
    first = report.now_kst().replace(hour=10, minute=0)
    list(pipeline.run("BTC", "algo", reports_dir=tmp_path, now=first))

    second = list(pipeline.run("BTC", "algo", reports_dir=tmp_path))
    texts = [e["text"] for e in _of(second, "log")]
    assert not any("지난 판정 없음" in t for t in texts)
    assert any("이후" in t and "%" in t for t in texts)


def test_다른_종목의_판정은_회고에_섞이지_않는다(tmp_path):
    list(pipeline.run("BTC", "algo", reports_dir=tmp_path))
    texts = [e["text"] for e in _of(_events("삼성전자", "algo", tmp_path), "log")]
    assert any("지난 판정 없음" in t for t in texts)


def test_리포트_이름으로_바깥_파일을_열_수_없다(tmp_path):
    (tmp_path.parent / "secret.md").write_text("비밀", encoding="utf-8")
    for name in ("../secret.md", "..%2Fsecret.md", "secret.md", "/etc/passwd"):
        with pytest.raises(report.ReportError):
            report.read(tmp_path, name)


def test_머리말이_깨진_파일은_목록에서_건너뛴다(tmp_path):
    (tmp_path / "2026-08-04-BTC-1200.md").write_text("머리말 없음", encoding="utf-8")
    (tmp_path / "메모.md").write_text("---\nsymbol: BTC\n---\n", encoding="utf-8")
    assert report.listing(tmp_path) == ()


def test_리포트_이름은_날짜_종목_시각_형식이다():
    assert (
        report.report_name("SKHYNIX", datetime(2026, 8, 4, 15, 28))
        == "2026-08-04-SKHYNIX-1528.md"
    )


# ── 서버 ──────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path):
    # base_url 을 루프백으로 고정한다. TestClient 기본값 testserver 는
    # 허용 호스트 목록에 없어 전부 400 이 된다 — 그게 이 서버의 의도다.
    return TestClient(create_app(reports_dir=tmp_path), base_url="http://127.0.0.1:8000")


def _stream(client, url: str) -> list[dict]:
    with client.stream("GET", url) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    return [
        json.loads(chunk[len("data: ") :])
        for chunk in body.strip().split("\n\n")
        if chunk.startswith("data: ")
    ]


def test_메타는_모드_세개와_에이전트_열세명을_준다(client):
    data = client.get("/api/meta").json()
    assert [m["key"] for m in data["modes"]] == ["algo", "scalp", "attack"]
    assert len(data["agents"]) == 13


def test_데모가_아니면_실전_준비중이라고_알린다(client):
    events = _stream(client, "/api/analyze?symbol=BTC&mode=algo")
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "demo=1" in events[0]["text"]


def test_데모_요청은_판정까지_흘러온다(client):
    kinds = [e["type"] for e in _stream(client, "/api/analyze?symbol=BTC&mode=scalp&demo=1")]
    assert "verdict" in kinds and kinds[-1] == "done"


def test_리포트_페이지와_zip이_열린다(client):
    _stream(client, "/api/analyze?symbol=BTC&mode=scalp&demo=1")
    page = client.get("/reports")
    assert page.status_code == 200 and "BTC" in page.text
    bundle = client.get("/reports/all.zip")
    assert bundle.status_code == 200 and bundle.content[:2] == b"PK"


def test_없는_리포트는_404다(client):
    assert client.get("/reports/2026-01-01-NOPE-0000.md").status_code == 404


def test_다른_호스트_이름으로는_못_부른다(client):
    response = client.get("/api/meta", headers={"host": "evil.example.com"})
    assert response.status_code == 400
