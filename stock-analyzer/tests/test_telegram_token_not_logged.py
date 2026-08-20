"""텔레그램 발송 실패 로그에 봇 토큰이 남으면 안 된다.

requests 의 연결 오류 문구에는 요청 URL 전체가 들어간다 —
`/bot<토큰>/sendMessage`. 이 저장소는 공개이고 워크플로 로그도 공개로
읽히므로, 예외 원문을 찍는 순간 토큰이 공개된다. scorecard_worker 만
고쳐져 있고 나머지 세 벌은 안 고쳐져 있었다(2026-08-20 리뷰).
"""
import pytest
import requests

TOKEN = "1234567890:AA_SECRET_BOT_TOKEN_VALUE"


@pytest.fixture
def boom(monkeypatch):
    """post 가 requests 의 진짜 예외 모양으로 터지게 한다 — URL 포함."""
    def _post(url, **kw):
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: /bot{TOKEN}/sendMessage")
    monkeypatch.setattr(requests, "post", _post)


def test_daily_report_send_tg(boom, monkeypatch, capsys):
    import daily_report_toss as rpt
    monkeypatch.setattr(rpt, "TG_TOKEN", TOKEN)
    monkeypatch.setattr(rpt, "TG_CHAT_ID", "42")
    monkeypatch.setattr(rpt.requests, "post", requests.post)
    rpt.send_tg("hi")                       # 죽지 않는다 — 아침 보고가 통째로 날아가면 안 된다
    assert TOKEN not in capsys.readouterr().out


def test_runner_send_tg(boom, monkeypatch, capsys):
    import paper_trade_runner_toss as runner
    monkeypatch.setattr(runner, "TG_TOKEN", TOKEN)
    monkeypatch.setattr(runner, "TG_CHAT_ID", "42")
    monkeypatch.setattr(runner.requests, "post", requests.post)
    runner.send_tg("hi")
    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err


def test_scorecard_send_tg(boom, monkeypatch, capsys):
    import scorecard_worker as sw
    monkeypatch.setattr(sw, "TG_TOKEN", TOKEN)
    monkeypatch.setattr(sw, "TG_CHANNEL_ID", "42")
    monkeypatch.setattr(sw.requests, "post", requests.post)
    assert sw.send_tg("hi") is False
    assert TOKEN not in capsys.readouterr().out


def test_app_send_telegram_returns_no_token(boom, monkeypatch):
    import app
    monkeypatch.setattr(app.requests, "post", requests.post)
    ok, err = app.send_telegram(TOKEN, "42", "hi")
    assert ok is False and TOKEN not in err
