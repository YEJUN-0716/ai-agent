"""PIXEL TRADING FLOOR — 데모 파이프라인이 가이드에 적힌 대로 도는지 본다.

특히 두 가지를 잠근다.
  - 모드별 호출 횟수(algo 13 / scalp 5 / attack 5) — 요금이 여기서 갈린다
  - 스캘핑·공격의 판정 기준 시장이 원화가 아니라 무기한이라는 것
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from floor import claude_runner, cli, market, pipeline, report
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


def test_데모가_아니면_실전_러너와_실측시세로_돈다(tmp_path):
    """실전 요청이 데모로 조용히 떨어지지 않는지 본다 — 요금 없이 배선만 확인한다."""
    called: list[str] = []

    def spy_runner(agent: str, ctx: Context):
        called.append(agent)
        return demo_runner(agent, ctx)

    def spy_snapshot(symbol):
        return replace(market.demo_snapshot(symbol), source="테스트 실측")

    app = create_app(reports_dir=tmp_path, runner=spy_runner, snapshot_fn=spy_snapshot)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    events = _stream(client, "/api/analyze?symbol=BTC&mode=scalp")

    assert called == list(SCALP.roster)
    assert _of(events, "quote")[0]["source"] == "테스트 실측"
    assert events[-1]["type"] == "done"
    # 실전에서 합성값 경고가 뜨면 데모로 떨어진 것이다.
    assert not any("합성값" in e["text"] for e in _of(events, "log"))


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


# ── 실전 러너 (claude -p) ─────────────────────────────────
#
# 진짜 프로세스는 한 번도 부르지 않는다. 테스트가 요금을 쓰면 아무도 안 돌린다.


def _ctx(mode=SCALP) -> Context:
    return Context(
        snapshot=market.demo_snapshot(market.resolve_symbol("BTC")),
        mode=mode,
        turn=0,
        prior=(),
    )


_GOOD = {
    "bubble": "롱 우위",
    "observed": "MA20 위, 기준 변동성 1.2%",
    "reading": "추세 추종 구간",
    "counter": "레인지면 수수료만 낸다",
    "trigger": "20봉 저점 이탈",
    "verdict": {
        "action": "LONG",
        "confidence": 62,
        "entry": 100.0,
        "stop": 98.0,
        "target": 104.0,
        "size_pct": 12.0,
        "rationale": "손절 2% 대비 목표 4%로 2R.",
    },
}


def _answers(monkeypatch, *replies: str) -> list[str]:
    """claude 프로세스를 가짜 응답으로 갈아끼우고, 넘어간 프롬프트를 모은다."""
    seen: list[str] = []

    def fake_call(prompt: str) -> str:
        seen.append(prompt)
        return replies[min(len(seen) - 1, len(replies) - 1)]

    monkeypatch.setattr(claude_runner, "_call", fake_call)
    return seen


def test_실전러너가_JSON을_브리핑으로_바꾼다(monkeypatch):
    body = json.dumps(_GOOD, ensure_ascii=False)
    _answers(monkeypatch, f"여기 있습니다\n```json\n{body}\n```")
    brief = claude_runner.claude_runner("ACE", _ctx())
    assert brief.bubble == "롱 우위"
    assert "**관찰**" in brief.body and "**판단이 바뀌는 트리거**" in brief.body
    assert brief.verdict.action == "LONG" and brief.verdict.size_pct == 12.0


def test_형식이_두번_깨지면_판을_접는다(monkeypatch):
    seen = _answers(monkeypatch, "JSON 이 없는 답")
    with pytest.raises(claude_runner.RunnerError):
        claude_runner.claude_runner("TARO", _ctx())
    assert len(seen) == 2  # 한 번만 다시 부른다


def test_두번째에_형식이_맞으면_그걸_쓴다(monkeypatch):
    seen = _answers(monkeypatch, "깨진 답", json.dumps(_GOOD, ensure_ascii=False))
    assert claude_runner.claude_runner("ACE", _ctx()).verdict.action == "LONG"
    assert len(seen) == 2


def test_모드에_없는_판정은_고쳐서_통과시키지_않는다(monkeypatch):
    bad = {**_GOOD, "verdict": {**_GOOD["verdict"], "action": "BUY"}}
    _answers(monkeypatch, json.dumps(bad, ensure_ascii=False))
    with pytest.raises(claude_runner.RunnerError) as exc:
        claude_runner.claude_runner("ACE", _ctx())
    assert "LONG" in str(exc.value)


def test_공격모드는_관망_판정을_받지_않는다(monkeypatch):
    bad = {**_GOOD, "verdict": {**_GOOD["verdict"], "action": "PASS"}}
    _answers(monkeypatch, json.dumps(bad, ensure_ascii=False))
    with pytest.raises(claude_runner.RunnerError):
        claude_runner.claude_runner("ACE", _ctx(ATTACK))


def test_판정_없는_ACE_응답은_거절한다(monkeypatch):
    _answers(
        monkeypatch,
        json.dumps({k: v for k, v in _GOOD.items() if k != "verdict"}, ensure_ascii=False),
    )
    with pytest.raises(claude_runner.RunnerError):
        claude_runner.claude_runner("ACE", _ctx())


def test_자식_프로세스_환경에서_API키를_벗긴다(monkeypatch):
    """이 변수가 남아 있으면 구독이 아니라 API 로 과금된다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-쓰면-안-되는-키")
    assert "ANTHROPIC_API_KEY" not in claude_runner._env()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-쓰면-안-되는-키"  # 이 창은 안 건드린다


# ── 실측 시세 ─────────────────────────────────────────────
#
# 네트워크도 안 탄다. 응답 모양만 고정해 두고 파싱과 "안 지어내기"를 본다.


def _yahoo(price: float, day_pct: float, bars: int = 40) -> dict:
    closes = [round(price * (1 + i / 1000), 2) for i in range(bars)]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"regularMarketPrice": closes[-1]},
                    "indicators": {
                        "quote": [
                            {
                                "high": [c * (1 + day_pct / 200) for c in closes],
                                "low": [c * (1 - day_pct / 200) for c in closes],
                                "close": closes,
                            }
                        ]
                    },
                }
            ]
        }
    }


def _klines(close: float, day_pct: float) -> list[list]:
    def row(c: float) -> list:
        return [0, c, c * (1 + day_pct / 200), c * (1 - day_pct / 200), c, 0]

    # 마지막 봉은 진행 중이라 코드가 직전 완결 봉(가운데)을 쓴다.
    return [row(close * 0.99), row(close), row(close * 1.01)]


_RSS = """<rss><channel>
  <title>CoinDesk: Crypto News</title>
  <item><title>Wells Fargo joins race to tokenize Wall Street</title></item>
  <item><title><![CDATA[Bitcoin&#8217;s $63,000 zone is the battleground]]></title></item>
  <item><title>Polymarket targets $20 billion valuation</title></item>
</channel></rss>"""


def _fake_market(monkeypatch, dead: tuple[str, ...] = ()) -> None:
    def get(url: str):
        if any(mark in url for mark in dead):
            raise market.MarketError("이 조회는 죽었다")
        if "premiumIndex" in url:
            return {"markPrice": "1100", "lastFundingRate": "0.0005"}
        if "bybit" in url:
            return {"result": {"list": [{"lastPrice": "1102", "fundingRate": "0.0006"}]}}
        if "funding-rate" in url:
            return {"data": [{"fundingRate": "0.0004"}]}
        if "okx" in url:
            return {"data": [{"last": "1104"}]}
        if "gateio" in url:
            return [{"last": "1106", "funding_rate": "0.0003"}]
        if "fapi.binance.com" in url:
            return _klines(1100.0, 1.20)  # 무기한 — 실측 예시값
        if "api.binance.com" in url:
            return _klines(68000.0, 1.20)
        if "alternative.me" in url:
            return {"data": [{"value": "55"}]}
        if "/v1/finance/search" in url:
            return {"news": [{"title": "테스트 헤드라인"}]}
        if "coindesk" in url:
            raise AssertionError("코인 뉴스는 RSS 라 _get_text 로 간다")
        if "KRW" in url:
            return _yahoo(1400.0, 0.30)
        if "000660.KS" in url:
            return _yahoo(1_500_000.0, 3.05)  # 원화 정규장 — 실측 예시값
        raise AssertionError(f"예상 못한 조회: {url}")

    monkeypatch.setattr(market, "_get_json", get)

    def get_text(url: str) -> str:
        if any(mark in url for mark in dead):
            raise market.MarketError("이 조회는 죽었다")
        return _RSS

    monkeypatch.setattr(market, "_get_text", get_text)


def test_한국종목은_원화_차트와_해외_무기한을_같이_들고_온다(monkeypatch):
    """실측 예시 그대로 — KRX 3.05% vs 무기한 1.20%."""
    _fake_market(monkeypatch)
    snap = market.live_snapshot(market.resolve_symbol("하이닉스"))
    assert snap.source != "demo" and "Yahoo" in snap.source
    assert snap.krw_vol_pct == pytest.approx(3.05, abs=0.05)
    assert snap.perp_vol_pct == pytest.approx(1.20, abs=0.05)
    assert snap.fx_krw and snap.fx_krw > 1000


def test_전광판은_거래소별_실측이고_탭비트만_추정이다(monkeypatch):
    _fake_market(monkeypatch)
    rows = market.live_snapshot(market.resolve_symbol("하이닉스")).board_rows
    assert [r["exchange"] for r in rows] == [*market.PERP_EXCHANGES, market.TAPBIT]
    assert [r["estimated"] for r in rows].count(True) == 1
    real = [r["perp_usdt"] for r in rows if not r["estimated"]]
    assert rows[-1]["perp_usdt"] == pytest.approx(sum(real) / len(real), abs=1e-4)


def test_죽은_거래소는_줄째로_빠지고_값을_지어내지_않는다(monkeypatch):
    _fake_market(monkeypatch, dead=("bybit",))
    rows = market.live_snapshot(market.resolve_symbol("하이닉스")).board_rows
    assert [r["exchange"] for r in rows] == ["Binance", "OKX", "Gate", market.TAPBIT]


def test_코인은_바이낸스_현물과_무기한만_쓴다(monkeypatch):
    _fake_market(monkeypatch)
    snap = market.live_snapshot(market.resolve_symbol("BTC"))
    assert "Binance" in snap.source and snap.board_rows == ()
    assert snap.perp_vol_pct == snap.krw_vol_pct  # 코인엔 원화 정규장 차트가 없다
    assert snap.fear_greed == 55


def test_코인_뉴스는_종목을_짚은_기사를_앞에_둔다(monkeypatch):
    """야후 검색이 코인 질의를 무시하고 무관한 펀드 기사를 주는 걸 대체한 자리."""
    _fake_market(monkeypatch)
    heads = market.live_snapshot(market.resolve_symbol("BTC")).headlines
    assert heads[0].startswith("Bitcoin’s $63,000")  # CDATA·HTML 엔티티까지 푼다
    assert len(heads) == 3


def test_뉴스를_못_가져오면_비우고_화면에_그렇게_적는다(monkeypatch, tmp_path):
    _fake_market(monkeypatch, dead=("coindesk",))
    snap = market.live_snapshot(market.resolve_symbol("BTC"))
    assert snap.headlines == ()

    events = list(
        pipeline.run("BTC", "scalp", reports_dir=tmp_path, snapshot_fn=lambda _: snap)
    )
    assert any("가져오지 못했습니다" in e["text"] for e in _of(events, "log"))


def test_시세를_못_가져오면_판을_접고_리포트를_남기지_않는다(tmp_path):
    def boom(_symbol):
        raise market.MarketError("바이낸스 조회 실패")

    events = list(pipeline.run("BTC", "scalp", reports_dir=tmp_path, snapshot_fn=boom))
    assert events[-1]["type"] == "error" and "바이낸스" in events[-1]["text"]
    assert list(tmp_path.glob("*.md")) == []


# ── /floor 세션 러너 ──────────────────────────────────────
#
# 3단계의 전부는 "프로세스를 안 띄운다"는 것. 나머지 검사는 `-p` 러너와 같은
# 함수를 지나야 하므로, 여기서는 세션 경로가 그 문을 정말 지나는지를 본다.


def _prep(tmp_path, symbol="BTC", mode="scalp", snap=None):
    """판 하나를 차린다. 시세는 고정값이라 네트워크를 안 탄다."""
    snap = snap or market.demo_snapshot(market.resolve_symbol(symbol))
    text, state = cli.prep(
        symbol,
        mode,
        reports_dir=tmp_path,
        snapshot_fn=lambda _symbol: snap,
        state_dir=tmp_path / "state",
        now=datetime(2026, 8, 5, 15, 30, tzinfo=market.KST),
    )
    return text, state, snap


def _session_briefings(mode) -> list[dict]:
    """세션이 순서대로 썼다고 치는 브리핑 한 벌."""
    out = []
    for key, _turn in mode.order:
        item = {
            "agent": key,
            "bubble": f"{key} 한 줄",
            "observed": "MA20 위, 기준 변동성 1.2%",
            "reading": "추세 추종 구간",
            "counter": "레인지면 수수료만 낸다",
            "trigger": "20봉 저점 이탈",
        }
        if key in claude_runner.VERDICT_AGENTS:
            item["verdict"] = {**_GOOD["verdict"], "action": mode.actions[0]}
            if key == "PM":
                item["verdict"] |= {"pm_status": "승인", "pm_comment": "간다"}
        out.append(item)
    return out


def _write_json(directory, payload):
    path = directory / "briefings.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode,expected", [(ALGO, 13), (SCALP, 5), (ATTACK, 5)])
def test_세션_발언_순서의_길이가_호출_횟수와_같다(mode, expected):
    assert len(mode.order) == mode.calls == expected


@pytest.mark.parametrize("key,mode", [("algo", ALGO), ("scalp", SCALP), ("attack", ATTACK)])
def test_브리핑팩이_모드의_발언_순서를_전부_담는다(tmp_path, key, mode):
    text, _state, _snap = _prep(tmp_path, mode=key)
    for index, (agent, _turn) in enumerate(mode.order, start=1):
        assert f"{index}. **{agent}**" in text
    assert f"발언 {mode.calls}회" in text
    assert mode.basis in text  # 어느 시장 기준인지가 팩 첫 화면에 있어야 한다


def test_얼린_판이_그대로_되살아난다(tmp_path):
    """13명이 같은 가격을 봐야 한다. JSON 왕복에서 값이 변하면 그게 깨진다."""
    _text, state, snap = _prep(tmp_path)
    mode, revived, retro, now = cli._read_state(state)
    assert mode is SCALP and now.hour == 15 and retro == ()
    assert revived == snap  # 튜플·중첩 dataclass 까지 그대로


def test_세션_러너는_클로드_프로세스를_한번도_띄우지_않는다(tmp_path, monkeypatch):
    """3단계의 이유 전부. 여기가 뚫리면 세션으로 돌리는 의미가 없다."""

    def forbidden(*args, **kwargs):
        raise AssertionError("세션 러너가 프로세스를 띄웠다")

    monkeypatch.setattr(subprocess, "run", forbidden)
    _text, state, _snap = _prep(tmp_path)
    path, verdict = cli.save(
        state, _write_json(tmp_path, _session_briefings(SCALP)), reports_dir=tmp_path
    )
    assert path.exists() and verdict.action == "LONG"


def test_세션_리포트가_화면_리포트와_같은_서랍_같은_머리말이다(tmp_path):
    _text, state, _snap = _prep(tmp_path)
    path, verdict = cli.save(
        state, _write_json(tmp_path, _session_briefings(SCALP)), reports_dir=tmp_path
    )
    calls = report.listing(tmp_path)  # 화면이 /reports 를 그릴 때 쓰는 그 함수
    assert path.parent == tmp_path and len(calls) == 1
    assert calls[0].symbol == "BTC" and calls[0].action == verdict.action
    assert "AI 시뮬레이션" in path.read_text(encoding="utf-8")


def test_세션_저장이_빠진_발언을_거절한다(tmp_path):
    """호출 횟수는 요금이자 판정의 근거다. 12명이 낸 걸 13명이 낸 걸로 남길 수 없다."""
    _text, state, _snap = _prep(tmp_path, mode="algo")
    short = _session_briefings(ALGO)[:-1]  # PM 을 빼먹었다
    with pytest.raises(cli.FloorError) as exc:
        cli.save(state, _write_json(tmp_path, short), reports_dir=tmp_path)
    assert "13개" in str(exc.value)
    assert list(tmp_path.glob("*.md")) == []


def test_세션_저장이_어긋난_발언_순서를_거절한다(tmp_path):
    _text, state, _snap = _prep(tmp_path)
    items = _session_briefings(SCALP)
    items[0], items[1] = items[1], items[0]
    with pytest.raises(cli.FloorError) as exc:
        cli.save(state, _write_json(tmp_path, items), reports_dir=tmp_path)
    assert "순서" in str(exc.value)
    assert list(tmp_path.glob("*.md")) == []


def test_세션_저장도_모드에_없는_판정을_거절한다(tmp_path):
    """`-p` 러너와 같은 검사를 지난다는 증거. 세션이 썼다고 느슨해지지 않는다."""
    _text, state, _snap = _prep(tmp_path)
    items = _session_briefings(SCALP)
    items[-1]["verdict"]["action"] = "BUY"
    with pytest.raises(claude_runner.RunnerError) as exc:
        cli.save(state, _write_json(tmp_path, items), reports_dir=tmp_path)
    assert "LONG" in str(exc.value)
    assert list(tmp_path.glob("*.md")) == []


def test_세션_저장이_판정_없는_판을_거절한다(tmp_path):
    _text, state, _snap = _prep(tmp_path)
    items = _session_briefings(SCALP)
    del items[-1]["verdict"]
    with pytest.raises(claude_runner.RunnerError):
        cli.save(state, _write_json(tmp_path, items), reports_dir=tmp_path)


def test_세션_팩에도_과거_판정_회고가_들어간다(tmp_path):
    """저장한 리포트는 다음 판단의 입력이다 — 화면이든 세션이든 같은 서랍을 본다."""
    _text, state, snap = _prep(tmp_path)
    cli.save(state, _write_json(tmp_path, _session_briefings(SCALP)), reports_dir=tmp_path)

    again, _state, _snap = _prep(tmp_path, snap=snap)
    assert "지난 판정 없음" not in again
    assert "LONG(확신도 62%)" in again


@pytest.mark.parametrize(
    "words,expected",
    [
        (["BTC"], ("BTC", "algo")),
        (["하이닉스", "scalp"], ("하이닉스", "scalp")),
        (["SK", "하이닉스", "scalp"], ("SK 하이닉스", "scalp")),
        (["삼성전자", "공격"], ("삼성전자", "공격")),
        (["SK", "하이닉스"], ("SK 하이닉스", "algo")),
    ],
)
def test_슬래시커맨드_인자를_종목과_모드로_가른다(words, expected):
    assert cli.split_args(words) == expected


def test_판_상태가_깨졌으면_반쯤_녹여_쓰지_않는다(tmp_path):
    _text, state, _snap = _prep(tmp_path)
    state.write_text('{"mode": "scalp"}', encoding="utf-8")
    with pytest.raises(cli.FloorError) as exc:
        cli.save(state, _write_json(tmp_path, _session_briefings(SCALP)), reports_dir=tmp_path)
    assert "prep 부터 다시" in str(exc.value)
