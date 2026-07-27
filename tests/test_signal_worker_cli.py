"""CLI 분기 — --record-only 는 발송 경로를 타지 않는다.

매수 알림을 켜는 것과 성적표 재료를 쌓는 것은 별개의 결정이다. 한쪽을
멈춰도 다른 쪽은 돌아야 한다.

signal_worker 는 함수 안에서 import 한다 — 최상단에서 끌어오면 core·yfinance
까지 따라온다. tests/test_analyst_log.py 가 쓰는 패턴을 그대로 따른다.
"""


def _stubs(monkeypatch):
    import signal_worker

    called = {}

    def fake_record():
        called["record"] = True
        return 0

    def fake_main():
        called["full"] = True

    monkeypatch.setattr(signal_worker, "record_only_main", fake_record)
    monkeypatch.setattr(signal_worker, "main", fake_main)
    return signal_worker, called


def test_record_only_flag_skips_full_scan(monkeypatch):
    sw, called = _stubs(monkeypatch)

    rc = sw._cli_entry(["signal_worker.py", "--record-only"])

    assert rc == 0
    assert called == {"record": True}


def test_no_flag_runs_full_scan(monkeypatch):
    sw, called = _stubs(monkeypatch)

    rc = sw._cli_entry(["signal_worker.py"])

    assert rc == 0
    assert called == {"full": True}


def test_record_failure_propagates_exit_code(monkeypatch):
    """0종목 기록은 성공이 아니다 — 워크플로가 빨갛게 죽어야 안다."""
    import signal_worker

    monkeypatch.setattr(signal_worker, "record_only_main", lambda: 1)

    assert signal_worker._cli_entry(["signal_worker.py", "--record-only"]) == 1
