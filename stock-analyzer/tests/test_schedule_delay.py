"""늦게 온 아침 브리핑은 스스로 늦었다고 말해야 한다.

2026-08 말 GitHub 예약 큐 적체로 KST 08:30 예정이던 보고가 15:58 에 왔다.
사람이 시계를 보지 않으면 알 수 없었다. 여기서 검사하는 건 세 가지다 —
자정을 넘겨 밀린 경우(가장 흔한 실제 사례)를 음수로 재지 않는지, 예약이
아닌 실행을 지연으로 오해하지 않는지, 몇 분짜리 상시 지연에 침묵하는지.
"""
from datetime import datetime, timezone

from daily_report_toss import schedule_delay

CRON = "37 22 * * 1-5"          # daily-report.yml 의 예약


def _at(hh, mm, day=28):
    return datetime(2026, 8, day, hh, mm, tzinfo=timezone.utc)


def test_delay_across_midnight(monkeypatch):
    """22:37 예약이 다음날 05:58 에 떴다 — 7시간 21분이지 음수가 아니다."""
    monkeypatch.setenv("GH_EVENT_SCHEDULE", CRON)
    assert schedule_delay(_at(5, 58)) == "⏱ 예약(22:37 UTC)보다 7시간 21분 늦게 실행됨"


def test_small_delay_is_silent(monkeypatch):
    """정시 근처 몇 분은 늘 있다. 매일 붙으면 아무도 안 읽는다."""
    monkeypatch.setenv("GH_EVENT_SCHEDULE", CRON)
    assert schedule_delay(_at(22, 55)) == ""


def test_delay_under_an_hour_omits_hours(monkeypatch):
    monkeypatch.setenv("GH_EVENT_SCHEDULE", CRON)
    assert schedule_delay(_at(23, 30)) == "⏱ 예약(22:37 UTC)보다 53분 늦게 실행됨"


def test_manual_run_is_not_a_delay(monkeypatch):
    """workflow_dispatch 는 예약 시각이 없다 — 잴 대상이 아니다."""
    monkeypatch.delenv("GH_EVENT_SCHEDULE", raising=False)
    assert schedule_delay(_at(5, 58)) == ""
    monkeypatch.setenv("GH_EVENT_SCHEDULE", "")
    assert schedule_delay(_at(5, 58)) == ""
