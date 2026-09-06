"""sip 금수(최근 15분) 가드. 네트워크 없음.

실측 경계(2026-09-06): `end` 가 900초 전이면 403, 905초 전이면 OK.
러너는 15:15 자료를 15:30 크론이 부르므로 간격이 **딱 900초**다 — 가드가
없으면 기동 속도와 시계 오차가 그날 회차를 정한다.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_bitgak_paper as R  # noqa: E402


def test_old_end_does_not_wait(monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: pytest.fail(f"기다리면 안 된다: {s}"))
    R._await_sip(datetime.now(timezone.utc) - timedelta(hours=1))


def test_cron_boundary_waits_instead_of_gambling(monkeypatch):
    """15:30 크론이 15:15 자료를 부르는 그 자리 — 딱 900초 간격."""
    slept = []
    monkeypatch.setattr(R.time, "sleep", slept.append)
    R._await_sip(datetime.now(timezone.utc) - timedelta(seconds=900))
    assert slept and 0 < slept[0] <= 31, slept


def test_future_end_raises_instead_of_sleeping_for_hours(monkeypatch):
    """장 밖 `scan` 은 end 가 미래다 — 13시간 자는 게 아니라 세워야 한다."""
    monkeypatch.setattr(R.time, "sleep", lambda s: pytest.fail(f"기다리면 안 된다: {s}"))
    with pytest.raises(RuntimeError, match="최근 15분"):
        R._await_sip(datetime.now(timezone.utc) + timedelta(hours=13))


def test_chart_pullback_is_outside_the_embargo():
    """차트의 now-20분은 가드에 안 걸린다 — 러너 고치다 화면을 느리게 만들지 말 것."""
    from modules.bitgak_chart import SIP_DELAY_MIN
    assert timedelta(minutes=SIP_DELAY_MIN) > R.SIP_LAG
