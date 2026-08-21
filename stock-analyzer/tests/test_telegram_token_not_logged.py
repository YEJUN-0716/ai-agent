"""텔레그램 발송 실패 로그에 봇 토큰이 남으면 안 된다.

requests 의 연결 오류 문구에는 요청 URL 전체가 들어간다 —
`/bot<토큰>/sendMessage`. 이 저장소는 공개이고 워크플로 로그도 공개로
읽히므로, 예외 원문을 찍는 순간 토큰이 공개된다. scorecard_worker 만
고쳐져 있고 나머지 세 벌은 안 고쳐져 있었다(2026-08-20 리뷰).
"""
import pathlib

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


# 이 목록이 이 테스트가 아는 발송 사본의 전부다. 2026-08-20 리뷰는 덩어리 안의
# 파일만 grep 해서 modules/ops_safety.py 의 다섯 번째 사본을 놓쳤다(부르는 코드가
# 없어 2026-08-21 에 삭제). 새 사본이 생기면 여기서 먼저 걸려, 그 사본도 위처럼
# 잠그게 만든다.
_KNOWN_SENDERS = {
    "app.py",
    "daily_report_toss.py",
    "paper_trade_runner_toss.py",
    "scorecard_worker.py",
}


def test_no_unlocked_telegram_sender():
    root = pathlib.Path(__file__).resolve().parent.parent
    found = {
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*.py")
        if "api.telegram.org" in p.read_text(encoding="utf-8")
    }
    found -= {str(pathlib.Path(__file__).relative_to(root)).replace("\\", "/")}
    assert found == _KNOWN_SENDERS, (
        f"텔레그램 발송 사본이 바뀌었다: {found ^ _KNOWN_SENDERS} — "
        f"새 사본이면 예외 원문 대신 type(e).__name__ 만 찍는지 여기서 잠글 것")
