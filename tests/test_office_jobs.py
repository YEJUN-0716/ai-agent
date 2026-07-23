"""사무실 야간 근무 판정 — 워크플로 파싱과 실적 대조.

실제 .github/ 와 상태 파일은 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
import json
from datetime import date

from modules import office_jobs as oj


def _wf(tmp_path, name, body):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


ACTIVE_DAILY = """\
name: 테스트 잡
on:
  schedule:
    - cron: "0 23 * * 1-5"
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""

DISABLED = """\
name: 테스트 잡
on:
  # schedule:
  #   - cron: "0 23 * * 1-5"
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""

WEEKLY = """\
name: 주간 잡
on:
  schedule:
    - cron: "0 14 * * 0"
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""


def test_active_weekday_cron_is_scheduled(tmp_path):
    _wf(tmp_path, "a.yml", ACTIVE_DAILY)
    s = oj.schedule_of(tmp_path, "a.yml")
    assert s is not None
    assert s["period_days"] == 1
    assert s["weekdays_only"] is True


def test_commented_out_cron_is_not_scheduled(tmp_path):
    """크론을 주석 처리한 잡은 '고장' 이 아니라 '안 돎' 이다."""
    _wf(tmp_path, "b.yml", DISABLED)
    assert oj.schedule_of(tmp_path, "b.yml") is None


def test_weekly_cron_period_is_seven_days(tmp_path):
    _wf(tmp_path, "c.yml", WEEKLY)
    s = oj.schedule_of(tmp_path, "c.yml")
    assert s["period_days"] == 7
    assert s["weekdays_only"] is False


def test_missing_workflow_file(tmp_path):
    assert oj.schedule_of(tmp_path, "nope.yml") is None
    assert oj.workflow_exists(tmp_path, "nope.yml") is False


def test_broken_yaml_does_not_raise(tmp_path):
    """사무실 화면이 파싱 실패로 죽으면 앱의 유일한 내비게이션이 사라진다."""
    _wf(tmp_path, "bad.yml", "on: [[[ this is not yaml")
    assert oj.schedule_of(tmp_path, "bad.yml") is None
    assert oj.workflow_exists(tmp_path, "bad.yml") is True


# ── 실적 조회 ────────────────────────────────────────────────────────

def test_last_success_reads_analyst_log(tmp_path):
    d = tmp_path / "data" / "analyst_log"
    d.mkdir(parents=True)
    (d / "2026.jsonl").write_text(
        '{"date": "2026-07-20", "regime": "bull", "scores": {}}\n'
        '{"date": "2026-07-23", "regime": "bull", "scores": {}}\n',
        encoding="utf-8")

    assert oj.last_success(tmp_path, "analyst_log") == date(2026, 7, 23)


def test_last_success_reads_ic_weights_updated(tmp_path):
    (tmp_path / "ic_weights.json").write_text(
        json.dumps({"updated": "2026-07-19T14:03:11Z"}), encoding="utf-8")

    assert oj.last_success(tmp_path, "ic_weights") == date(2026, 7, 19)


def test_last_success_reads_signal_log_entry_date(tmp_path):
    (tmp_path / "signal_log.json").write_text(
        json.dumps({"signals": [{"entry_date": "2026-07-10"},
                                {"entry_date": "2026-07-18"}]}),
        encoding="utf-8")

    assert oj.last_success(tmp_path, "signal_log") == date(2026, 7, 18)


def test_last_success_reads_equity_log(tmp_path):
    (tmp_path / "equity_log.json").write_text(
        json.dumps({"records": [{"date": "2026-07-01"},
                                {"date": "2026-07-15"}]}),
        encoding="utf-8")

    assert oj.last_success(tmp_path, "equity_log") == date(2026, 7, 15)


def test_last_success_missing_file_is_none(tmp_path):
    assert oj.last_success(tmp_path, "analyst_log") is None
    assert oj.last_success(tmp_path, "ic_weights") is None


def test_last_success_broken_file_does_not_raise(tmp_path):
    (tmp_path / "ic_weights.json").write_text("{ not json", encoding="utf-8")

    assert oj.last_success(tmp_path, "ic_weights") is None


def test_last_success_unknown_key_is_none(tmp_path):
    assert oj.last_success(tmp_path, "no_such_result") is None


# ── 상태 종합 ────────────────────────────────────────────────────────

def _log_on(tmp_path, day):
    d = tmp_path / "data" / "analyst_log"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day[:4]}.jsonl").write_text(
        '{"date": "%s", "regime": "bull", "scores": {}}\n' % day,
        encoding="utf-8")


def test_active_job_recorded_today_is_normal(tmp_path):
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)
    _log_on(tmp_path, "2026-07-23")

    s = oj.job_states(tmp_path, today=date(2026, 7, 23))["analyst_log"]
    assert s["status"] == "정상"
    assert s["days_since"] == 0


def test_one_period_late_is_caution(tmp_path):
    """평일 잡이 영업일 둘 밀린 상태 — 수요일 기록, 금요일 확인."""
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)
    _log_on(tmp_path, "2026-07-22")            # 수요일

    s = oj.job_states(tmp_path, today=date(2026, 7, 24))["analyst_log"]   # 금요일
    assert s["status"] == "주의"


def test_two_periods_late_is_warning(tmp_path):
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)
    _log_on(tmp_path, "2026-07-20")            # 월요일

    s = oj.job_states(tmp_path, today=date(2026, 7, 24))["analyst_log"]   # 금요일
    assert s["status"] == "경고"


def test_weekend_gap_does_not_alarm_weekday_job(tmp_path):
    """금요일 기록 → 월요일 확인. 달력으로 3일이지만 영업일로는 하루다.

    달력일로 세면 매주 월요일마다 거짓 경고가 뜬다.
    """
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)
    _log_on(tmp_path, "2026-07-24")            # 금요일

    s = oj.job_states(tmp_path, today=date(2026, 7, 27))["analyst_log"]   # 월요일
    assert s["status"] == "정상"


def test_disabled_cron_is_waiting_not_warning(tmp_path):
    """의도적으로 끈 잡을 고장으로 표시하면 경고등이 상시 점등된다."""
    _wf(tmp_path, "analyst-log.yml", DISABLED)
    _log_on(tmp_path, "2026-01-01")

    s = oj.job_states(tmp_path, today=date(2026, 7, 24))["analyst_log"]
    assert s["status"] == "대기"
    assert s["scheduled"] is False
    assert any("휴직" in r for r in s["reasons"])


def test_scheduled_but_no_result_yet_is_waiting(tmp_path):
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)

    s = oj.job_states(tmp_path, today=date(2026, 7, 24))["analyst_log"]
    assert s["status"] == "대기"
    assert any("첫 실행" in r for r in s["reasons"])


def test_untrackable_job_says_so(tmp_path):
    """결과가 저장소에 안 남는 잡은 초록불로 칠하지 않는다."""
    _wf(tmp_path, "daily-report.yml", ACTIVE_DAILY)

    s = oj.job_states(tmp_path, today=date(2026, 7, 24))["daily_report"]
    assert s["status"] == "대기"
    assert any("추적 불가" in r for r in s["reasons"])


def test_missing_workflow_is_waiting(tmp_path):
    s = oj.job_states(tmp_path, today=date(2026, 7, 24))["analyst_log"]
    assert s["status"] == "대기"


def test_repo_stale_hint_when_two_jobs_lag_together(tmp_path):
    """활성 잡 둘이 나란히 밀렸으면 둘 다 깨진 것보다 안 당긴 쪽이 그럴듯하다."""
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)
    _wf(tmp_path, "ic-update.yml", ACTIVE_DAILY)
    _log_on(tmp_path, "2026-07-16")
    (tmp_path / "ic_weights.json").write_text(
        json.dumps({"updated": "2026-07-16T14:00:00Z"}), encoding="utf-8")

    states = oj.job_states(tmp_path, today=date(2026, 7, 24))
    assert oj.repo_stale_days(states) is not None


def test_no_repo_hint_when_only_one_job_lags(tmp_path):
    _wf(tmp_path, "analyst-log.yml", ACTIVE_DAILY)
    _wf(tmp_path, "ic-update.yml", ACTIVE_DAILY)
    _log_on(tmp_path, "2026-07-16")
    (tmp_path / "ic_weights.json").write_text(
        json.dumps({"updated": "2026-07-24T14:00:00Z"}), encoding="utf-8")

    states = oj.job_states(tmp_path, today=date(2026, 7, 24))
    assert oj.repo_stale_days(states) is None


# ── app 배선 회귀 ────────────────────────────────────────────────────
#
# 실제 저장소 상태로 돈다. 네트워크는 타지 않는다 — 이 직원 함수들은
# 로컬 파일과 세션 상태만 본다.

def test_disabled_job_employee_is_not_perpetually_caution():
    """꺼진 잡의 담당 직원은 '주의' 로 상시 점등되면 안 된다."""
    import app

    rep = app.signal_pipeline_employee()
    assert rep['status'] in ('대기', '정상', '경고')


def test_no_hardcoded_schedule_claim_in_reports():
    """워크플로 스케줄을 문구로 단정하지 않는다 — 꺼지면 그 문구가 거짓이 된다."""
    import app

    for rep in (app.signal_pipeline_employee(), app.execution_mode_employee()):
        joined = ' '.join(rep['reasons'])
        assert 'UTC 22:30' not in joined
        assert 'signal-alerts.yml: 활성' not in joined


def test_scorecard_employee_reports_a_known_status():
    import app

    rep = app.scorecard_employee()
    assert rep['name'] == '성적표'
    assert rep['status'] in ('정상', '주의', '경고', '대기')


# ── 보고서 팝업 ──────────────────────────────────────────────────────

def test_report_dialog_is_a_noop_without_selection():
    """선택이 없으면 조용히 지나가야 한다 — 사무실 첫 진입에서 죽으면 안 된다."""
    import app

    assert app.open_office_report_dialog(None, None) is None


def test_report_dialog_survives_old_streamlit(monkeypatch):
    """st.dialog 가 없는 버전에서도 죽지 않는다 (아래 보고서 카드가 대신 남는다)."""
    import app

    monkeypatch.delattr(app.st, 'dialog', raising=False)
    room = app.OfficeRoom('x', '테스트팀', '🧪', [])
    emp = app.OfficeEmployee('e', '테스트', '🧑', lambda: {
        'name': '테스트', 'icon': '🧑', 'status': '정상', 'reasons': ['ok']})

    assert app.open_office_report_dialog(room, emp) is None


def test_report_dialog_does_not_run_employee_panel(monkeypatch):
    """팝업은 보고서만 띄운다 — 업무 화면을 넣으면 버튼 한 번에 모달이 닫힌다."""
    import app

    calls = {'panel': 0, 'report': 0}

    def _panel():
        calls['panel'] += 1

    def _report():
        calls['report'] += 1
        return {'name': '테스트', 'icon': '🧑', 'status': '정상', 'reasons': ['ok']}

    room = app.OfficeRoom('x', '테스트팀', '🧪', [])
    emp = app.OfficeEmployee('e', '테스트', '🧑', _report, _panel)
    monkeypatch.setattr(app.st, 'dialog', lambda *a, **k: (lambda fn: fn))

    app.open_office_report_dialog(room, emp)

    assert calls['report'] == 1
    assert calls['panel'] == 0
