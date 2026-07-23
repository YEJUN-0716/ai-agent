# 사무실 야간 근무 가시화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사무실 화면이 자동화 잡의 실제 상태(도는가·꺼졌는가·밀렸는가)를 상황판·직원 상태·조명 세 겹으로 드러낸다.

**Architecture:** 새 순수 모듈 `modules/office_jobs.py` 가 `.github/workflows/*.yml` 을 파싱해 "예정" 을, 저장소에 커밋된 결과 파일에서 "실적" 을 읽어 대조한다. `app.py` 는 그 판정을 직원 보고서로 바꿔 기존 사무실 체계에 태우고, `office_game/index.html` 은 상황판·조명·그래픽으로 표현한다.

**Tech Stack:** Python 3.12, pyyaml, pytest / vanilla JS Canvas 2D

**Spec:** `docs/superpowers/specs/2026-07-24-office-night-shift-design.md`

## Global Constraints

- `modules/office_jobs.py` 는 `streamlit`·`yfinance`·`app` 을 import 하지 않는다. 리포 루트를 인자로 받는다 (테스트가 `tmp_path` 를 넘겨야 한다).
- **잡의 주기를 코드에 하드코딩하지 않는다.** 크론 표현식에서 유도한다. 하드코딩하면 2026-07-23 고장이 재발한다.
- **잡의 실행 성공이 아니라 저장소에 남은 결과 파일을 실적으로 센다.** 실행 이력을 믿었다면 gitignore 고장(PR #26)을 초록불로 표시했을 것이다.
- 파싱·읽기 실패는 **예외를 밖으로 내지 않는다.** 사무실 화면이 죽으면 앱의 유일한 내비게이션이 사라진다.
- 기존 상태 어휘(`정상`/`주의`/`경고`/`대기`)와 애니메이션 키(`office-pulse`/`office-pulse-fast`/`office-shake`/`office-sleep`)를 그대로 쓴다. 새 상태를 만들지 않는다.
- 꺼진 잡은 `경고` 가 아니라 `대기`(휴직) 다.
- 커밋 메시지는 한국어, conventional commits 접두사.
- CI 게이트: `ruff check .` + `pytest tests/`. 둘 다 통과해야 한다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `modules/office_jobs.py` (신규) | 워크플로 스케줄 판정 + 결과 파일 실적 조회 + 상태 종합 |
| `tests/test_office_jobs.py` (신규) | 위 판정 로직 전부 고정 (네트워크·Streamlit 없음) |
| `app.py` (수정) | 성적표 직원·성과 평가팀 방 추가, 기존 3직원 워크플로 인식, 컴포넌트 인자 전달 |
| `office_game/index.html` (수정) | 상황판 2단, 시간대 조명, 방 색, 벽·창문, 책상 원근, 선택 피드백 |
| `requirements.txt` (수정) | `pyyaml>=6.0` 명시 |

---

### Task 1: 워크플로 스케줄 판정

`.github/workflows/*.yml` 에서 "이 잡이 스케줄로 도는가" 와 "얼마나 자주" 를 읽는다. 크론이 주석 처리돼 있으면 `yaml.safe_load` 결과에 키 자체가 없으므로 자연히 "안 돎" 이 된다.

**Files:**
- Create: `modules/office_jobs.py`
- Create: `tests/test_office_jobs.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces:
  - `schedule_of(root, workflow_filename) -> dict | None`
    반환 `{'cron': str, 'period_days': int, 'weekdays_only': bool}`, 스케줄이 없으면 `None`, 파일이 없거나 파싱 실패면 `None`
  - `workflow_exists(root, workflow_filename) -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_office_jobs.py
"""사무실 야간 근무 판정 — 워크플로 파싱과 실적 대조.

실제 .github/ 와 상태 파일은 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_office_jobs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.office_jobs'`

- [ ] **Step 3: 모듈을 만든다**

```python
# modules/office_jobs.py
"""사무실 화면이 보는 자동화 잡의 상태 — 예정(워크플로)과 실적(결과 파일)의 대조.

2026-07-23 에 성적표 기록이 두 겹으로 죽어 있었다. 크론이 꺼진 것을 몰랐고
(signal-alerts.yml), 고친 뒤에도 결과가 gitignore 에 걸려 커밋되지 않았다.
둘 다 화면에는 아무 흔적이 없었다.

그래서 이 모듈은 두 가지를 고집한다.

1. **주기를 하드코딩하지 않는다.** 크론 표현식에서 유도한다. 코드가 "매일
   돈다" 고 믿는 동안 워크플로는 꺼져 있을 수 있고, 그 불일치를 드러내는 게
   이 모듈의 존재 이유다.
2. **잡의 실행 성공이 아니라 저장소에 남은 결과 파일을 본다.** 실행 이력을
   믿었다면 gitignore 고장도 초록불이었을 것이다.

streamlit·yfinance·app 을 import 하지 않는다. 리포 루트를 인자로 받는다.
"""
import json
import os
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional

WORKFLOW_DIR = os.path.join(".github", "workflows")

# 이 범위를 넘는 간격은 하루씩 세지 않고 달력일로 근사한다. 몇 년치 반복은
# 화면을 그리는 경로에서 할 일이 아니고, 그 정도로 밀렸으면 어차피 '경고' 다.
_MAX_BUSDAY_SCAN = 400


def _workflow_path(root, filename):
    return os.path.join(str(root), WORKFLOW_DIR, filename)


def workflow_exists(root, filename):
    return os.path.exists(_workflow_path(root, filename))


def _cadence_from_cron(cron):
    """크론 표현식 → (period_days, weekdays_only).

    이 저장소가 쓰는 형태만 정확히 다루고 나머지는 '매일' 로 안전하게 떨어진다 —
    모르는 표현식을 주 단위로 넉넉히 잡으면 밀린 잡을 정상으로 표시하게 된다.
    """
    fields = str(cron).split()
    if len(fields) < 5:
        return 1, False
    dow = fields[4]
    if dow in ('*', '?'):
        return 1, False
    if '-' in dow or ',' in dow:
        return 1, True          # "1-5" 같은 평일 범위
    if dow.isdigit():
        return 7, False         # 특정 요일 하나 = 주 1회
    return 1, False


def schedule_of(root, filename):
    """워크플로의 활성 스케줄. 없거나 읽을 수 없으면 None.

    크론이 주석 처리돼 있으면 파싱 결과에 schedule 키 자체가 없다 — 그래서
    '의도적으로 꺼짐' 이 별도 표시 없이 자연스럽게 잡힌다.
    """
    path = _workflow_path(root, filename)
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path, encoding='utf-8') as f:
            doc = yaml.safe_load(f)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None

    # YAML 1.1 에서 따옴표 없는 on 은 boolean True 로 읽힌다.
    trigger = doc.get('on', doc.get(True))
    if not isinstance(trigger, dict):
        return None
    sched = trigger.get('schedule')
    if not isinstance(sched, list) or not sched:
        return None

    entry = sched[0]
    cron = entry.get('cron') if isinstance(entry, dict) else None
    if not cron:
        return None
    period_days, weekdays_only = _cadence_from_cron(cron)
    return {'cron': str(cron), 'period_days': period_days,
            'weekdays_only': weekdays_only}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_office_jobs.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: `pyyaml` 을 명시한다**

`requirements.txt` 의 `pytest>=8.0` 줄 **위에** 다음 줄을 추가한다:

```
pyyaml>=6.0
```

현재 pyyaml 은 다른 패키지가 끌고 와서 우연히 설치돼 있다. 여기에 의존하므로 명시한다.

- [ ] **Step 6: 커밋**

```bash
git add modules/office_jobs.py tests/test_office_jobs.py requirements.txt
git commit -m "feat: 워크플로 스케줄 판정 — 사무실이 크론 상태를 읽는다"
```

---

### Task 2: 실적 조회 — 잡이 마지막으로 결과를 남긴 날

**Files:**
- Modify: `modules/office_jobs.py`
- Modify: `tests/test_office_jobs.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `last_success(root, result_key) -> datetime.date | None`
  - `RESULT_KEYS = ('analyst_log', 'ic_weights', 'signal_log', 'equity_log')`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_office_jobs.py` 의 import 블록에 `import json` 과 `from datetime import date` 를 추가하고, 파일 끝에 다음을 붙인다:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_office_jobs.py -q`
Expected: FAIL — `AttributeError: module 'modules.office_jobs' has no attribute 'last_success'`

- [ ] **Step 3: 구현한다**

`modules/office_jobs.py` 끝에 추가한다:

```python
# ── 실적: 잡이 저장소에 남긴 결과 ────────────────────────────────────
#
# 워크플로 실행 이력이 아니라 커밋된 파일을 본다. 2026-07-23 에 잡은
# 성공했는데 결과가 gitignore 에 걸려 커밋되지 않았다(PR #26) — 실행
# 이력을 믿었다면 그것도 초록불이었다.

RESULT_KEYS = ('analyst_log', 'ic_weights', 'signal_log', 'equity_log')


def _parse_day(value):
    """'2026-07-23' 또는 '2026-07-19T14:03:11Z' → date. 못 읽으면 None."""
    if not value:
        return None
    text = str(value).strip().rstrip('Z')
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _last_analyst_log_day(root):
    d = os.path.join(str(root), 'data', 'analyst_log')
    if not os.path.isdir(d):
        return None
    days = []
    for name in sorted(os.listdir(d)):
        if not name.endswith('.jsonl'):
            continue
        with open(os.path.join(d, name), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    day = _parse_day(json.loads(line).get('date'))
                except ValueError:
                    continue
                if day:
                    days.append(day)
    return max(days) if days else None


def _last_json_day(root, filename, extract):
    path = os.path.join(str(root), filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return extract(json.load(f))


def _from_records(records, key):
    days = [_parse_day(r.get(key)) for r in records if isinstance(r, dict)]
    days = [d for d in days if d]
    return max(days) if days else None


def last_success(root, result_key):
    """이 잡이 마지막으로 저장소에 결과를 남긴 날. 없으면 None."""
    try:
        if result_key == 'analyst_log':
            return _last_analyst_log_day(root)
        if result_key == 'ic_weights':
            return _last_json_day(root, 'ic_weights.json',
                                  lambda d: _parse_day(d.get('updated')))
        if result_key == 'signal_log':
            return _last_json_day(
                root, 'signal_log.json',
                lambda d: _from_records(d.get('signals', []), 'entry_date'))
        if result_key == 'equity_log':
            return _last_json_day(
                root, 'equity_log.json',
                lambda d: _from_records(
                    d if isinstance(d, list) else d.get('records', []), 'date'))
    except Exception:
        return None
    return None
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_office_jobs.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: 커밋**

```bash
git add modules/office_jobs.py tests/test_office_jobs.py
git commit -m "feat: 잡이 저장소에 남긴 실적 조회"
```

---

### Task 3: 상태 종합과 저장소 미당김 힌트

**Files:**
- Modify: `modules/office_jobs.py`
- Modify: `tests/test_office_jobs.py`

**Interfaces:**
- Consumes: `schedule_of`, `workflow_exists`, `last_success`
- Produces:
  - `JobSpec` — `NamedTuple(key, name, icon, workflow, result_key)`
  - `JOBS` — `tuple[JobSpec, ...]`
  - `job_states(root, today=None) -> dict[str, dict]`
    각 값은 `{'key','name','icon','status','reasons','days_since','scheduled','label'}`.
    `status` 는 `'정상'|'주의'|'경고'|'대기'`, `reasons` 는 `list[str]`, `label` 은 상황판용 짧은 문구.
  - `repo_stale_days(states) -> int | None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_office_jobs.py` 끝에 추가한다:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_office_jobs.py -q`
Expected: FAIL — `AttributeError: module 'modules.office_jobs' has no attribute 'job_states'`

- [ ] **Step 3: 구현한다**

`modules/office_jobs.py` 끝에 추가한다:

```python
# ── 잡 목록과 상태 종합 ──────────────────────────────────────────────

class JobSpec(NamedTuple):
    """자동화 잡 하나. result_key 가 None 이면 실적을 알 방법이 없다는 뜻."""
    key: str
    name: str
    icon: str
    workflow: str
    result_key: Optional[str]


JOBS = (
    JobSpec('analyst_log', '성적표 기록', '📊', 'analyst-log.yml', 'analyst_log'),
    JobSpec('ic_update', 'IC 갱신', '📈', 'ic-update.yml', 'ic_weights'),
    JobSpec('signal_alerts', '시그널 알림', '📡', 'signal-alerts.yml', 'signal_log'),
    JobSpec('paper_trade', '페이퍼 트레이드', '💰', 'paper-trade-us.yml', 'equity_log'),
    # 결과가 텔레그램으로만 간다 — 저장소에 아무것도 안 남아 실적을 알 수 없다.
    JobSpec('daily_report', '일별 리포트', '📮', 'daily-report.yml', None),
)

STATUS_OK = '정상'
STATUS_CAUTION = '주의'
STATUS_WARN = '경고'
STATUS_IDLE = '대기'


def _gap(last, today, weekdays_only):
    """last 다음날부터 today 까지의 간격. weekdays_only 면 영업일로 센다.

    달력일로 세면 금요일에 돈 평일 잡이 월요일마다 사흘 밀린 것으로 보여
    매주 거짓 경고가 뜬다.
    """
    calendar_gap = (today - last).days
    if not weekdays_only or calendar_gap > _MAX_BUSDAY_SCAN:
        return calendar_gap
    days = 0
    d = last
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def _state_for(root, spec, today):
    sched = schedule_of(root, spec.workflow)
    base = {'key': spec.key, 'name': spec.name, 'icon': spec.icon,
            'scheduled': sched is not None, 'days_since': None}

    if sched is None:
        if not workflow_exists(root, spec.workflow):
            return dict(base, status=STATUS_IDLE, label='워크플로 없음',
                        reasons=[f'{spec.workflow} 를 찾을 수 없다'])
        return dict(base, status=STATUS_IDLE, label='휴직',
                    reasons=[f'{spec.workflow} 크론이 꺼져 있다 — 휴직 중',
                             '의도적으로 끈 것이라면 정상이다'])

    if spec.result_key is None:
        return dict(base, status=STATUS_IDLE, label='추적 불가',
                    reasons=['결과가 저장소에 남지 않아 실적을 알 수 없다',
                             f'예정: {sched["cron"]}'])

    last = last_success(root, spec.result_key)
    if last is None:
        return dict(base, status=STATUS_IDLE, label='첫 실행 대기',
                    reasons=['스케줄은 살아 있으나 아직 남긴 결과가 없다',
                             f'예정: {sched["cron"]}'])

    gap = _gap(last, today, sched['weekdays_only'])
    period = sched['period_days']
    if gap <= period:
        status, label = STATUS_OK, ('오늘' if gap == 0 else f'{gap}일 전')
    elif gap <= period * 2:
        status, label = STATUS_CAUTION, f'{gap}일 전'
    else:
        status, label = STATUS_WARN, f'{gap}일째 없음'

    return dict(base, status=status, label=label, days_since=gap,
                reasons=[f'마지막 결과: {last.isoformat()} ({gap}일 전)',
                         f'예정: {sched["cron"]}'])


def job_states(root, today=None):
    """모든 잡의 상태. 키는 JobSpec.key."""
    today = today or date.today()
    out = {}
    for spec in JOBS:
        try:
            out[spec.key] = _state_for(root, spec, today)
        except Exception as e:
            out[spec.key] = {'key': spec.key, 'name': spec.name,
                             'icon': spec.icon, 'scheduled': False,
                             'days_since': None, 'status': STATUS_IDLE,
                             'label': '알 수 없음',
                             'reasons': [f'상태 판정 실패: {e}']}
    return out


def repo_stale_days(states):
    """저장소를 안 당긴 것으로 보이면 그 일수, 아니면 None.

    로컬에서 앱을 열면 결과 파일은 마지막 git pull 시점 기준이다. 잡은 잘
    돌았는데 로컬이 안 당겨져 있으면 직원이 억울하게 결근 처리된다.

    활성 잡 둘 이상이 비슷한 폭으로 나란히 밀렸으면, 전부 깨졌을 가능성보다
    안 당겼을 가능성이 높다고 본다. 휴리스틱이라 같은 날 둘이 진짜로 깨지면
    오진한다 — 그래도 개별 직원에게 틀린 경고를 붙이는 것보다는 낫다.
    """
    lagging = [s['days_since'] for s in states.values()
               if s.get('scheduled') and s.get('days_since')
               and s['status'] in (STATUS_CAUTION, STATUS_WARN)]
    if len(lagging) < 2:
        return None
    if max(lagging) - min(lagging) > 1:
        return None
    return max(lagging)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_office_jobs.py -q`
Expected: PASS (22 passed)

- [ ] **Step 5: 전체 스위트와 린트**

Run: `python -m pytest -q && ruff check .`
Expected: PASS — 기존 381 + 신규 22 = 403

- [ ] **Step 6: 커밋**

```bash
git add modules/office_jobs.py tests/test_office_jobs.py
git commit -m "feat: 잡 상태 종합 — 휴직·첫 실행 대기·추적 불가를 고장과 구분"
```

---

### Task 4: 성적표 직원과 성과 평가팀 방

**Files:**
- Modify: `app.py` — import 블록(33행 부근), `OfficeRoom`(4247-4255), `_OFFICE_AVATARS`(4262-4267), `collect_office_game_data`(4467-4485), `factor_ranking_employee` 위(4141), `_rooms`(7501-7523)

**Interfaces:**
- Consumes: `office_jobs.job_states`
- Produces: `scorecard_employee() -> dict` (`build_ops_report` 형식: `{'name','icon','status','reasons'}`)

- [ ] **Step 1: 모듈을 import 한다**

`app.py` 의 `from modules import analyst_team as _analyst_team` 바로 아래에 추가한다:

```python
from modules import office_jobs as _office_jobs
```

- [ ] **Step 2: `OfficeRoom` 에 ghost 좌석을 정식 필드로 넣는다**

`app.py:4247-4255` 의 `OfficeRoom` 을 다음으로 바꾼다:

```python
class OfficeRoom(NamedTuple):
    """사무실의 방(팀) 하나. employees는 직원 리스트이거나, 인자 없는 callable
    (team_panel_fn 실행 *이후*에 평가됨 — AI애널리스트팀처럼 그 방의 공용 패널이 실행돼야
    로스터가 정해지는 경우에 사용).

    ghost: 아직 사람이 없는 자리 수. 최소 좌석 수로 쓰여 "여기 누가 더 온다"를
    빈 책상으로 예고한다."""
    key: str
    name: str
    icon: str
    employees: Union[List[OfficeEmployee], Callable[[], List[OfficeEmployee]]]
    team_panel_fn: Optional[Callable] = None
    ghost: int = 0
```

- [ ] **Step 3: `collect_office_game_data` 가 ghost 필드를 쓰게 한다**

`app.py:4467-4485` 의 루프 본문을 다음으로 바꾼다:

```python
    game_rooms = []
    for room in rooms:
        employees = room.employees() if callable(room.employees) else room.employees
        if not employees:
            if room.key == 'analyst':
                # 분석 전에도 애널리스트팀 방과 빈 책상들을 보여준다 —
                # "종목을 입력하면 출근한다"는 게임 서사를 시각적으로 예고
                game_rooms.append({'key': room.key, 'name': room.name, 'icon': room.icon,
                                   'chars': [], 'ghost': len(TEAM_WEIGHTS) + 1})  # 7명 + 총괄
            elif room.ghost:
                game_rooms.append({'key': room.key, 'name': room.name, 'icon': room.icon,
                                   'chars': [], 'ghost': room.ghost})
            continue
        chars = []
        for emp in employees:
            rep = emp.report_fn()
            color, anim = _office_tile_style(rep)
            n = _office_normalize(rep)
            chars.append({'key': emp.key, 'name': emp.name, 'avatar': emp.avatar,
                          'color': color, 'anim': anim, 'headline': n['headline']})
        game_rooms.append({'key': room.key, 'name': room.name, 'icon': room.icon,
                           'chars': chars, 'ghost': room.ghost})
    return game_rooms
```

- [ ] **Step 4: 아바타를 등록한다**

`app.py:4262` 의 `_OFFICE_AVATARS` dict 에서 `'ML 신호': '👩‍💻',` 줄 뒤에 추가한다:

```python
    '성적표': '👩‍🏫',
```

- [ ] **Step 5: 성적표 직원 함수를 만든다**

`app.py` 의 `def factor_ranking_employee():` (4141행) **바로 위에** 추가한다:

```python
@st.cache_data(ttl=60, show_spinner=False)
def scorecard_employee():
    """성적표 직원(성과 평가팀): 애널리스트 기록 잡이 실제로 돌고 있는지.

    2026-07-23 에 이 기록은 두 겹으로 죽어 있었다 — 크론이 꺼져 있었고(PR #25),
    고친 뒤에도 결과가 gitignore 에 걸려 커밋되지 않았다(PR #26). 화면 어디에도
    흔적이 없었다. 이 직원이 그때 있었다면 첫날 티가 났다.
    """
    state = _office_jobs.job_states(os.path.dirname(__file__))['analyst_log']
    reasons = list(state['reasons'])
    reasons.append('판정까지 5일 기준 유효표본 30 필요 — 그 전엔 비어 있는 게 정상')
    return build_ops_report('성적표', '🎓', state['status'], reasons)
```

- [ ] **Step 6: 방을 추가한다**

`app.py:7501` 의 `_rooms` 리스트에서 `OfficeRoom('qa', ...)` 항목 **뒤에** 추가한다:

```python
        OfficeRoom('perf', '성과 평가팀', '📊', [
            _emp('scorecard', '성적표', scorecard_employee, render_analyst_scorecard),
        ], ghost=3),   # quant 성적·전환 게이트가 나중에 앉을 자리
```

- [ ] **Step 7: 앱이 뜨는지 확인한다**

Run: `python -c "import app"`
Expected: 예외 없이 끝난다 (Streamlit 의 bare-mode 경고 로그는 정상)

- [ ] **Step 8: 전체 스위트와 린트**

Run: `python -m pytest -q && ruff check .`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
git add app.py
git commit -m "feat: 성과 평가팀 방과 성적표 직원 — 기록 잡이 죽으면 사람이 보인다"
```

---

### Task 5: 기존 직원이 워크플로 상태를 반영하게

지금 `signal_pipeline_employee` 는 `signal_log.json` 이 5일 넘으면 무조건 '주의' 를 낸다. 그 잡은 꺼져 있으므로 이 경고는 **영원히 켜져 있다.** 게다가 사유 문구가 `"자동 스캔: signal-alerts.yml (매일 UTC 22:30, 월~금)"` 라고 **사실과 다른 말을 한다.** `execution_mode_employee` 도 `"signal-alerts.yml: 활성"` 이라고 잘못 말한다.

**Files:**
- Modify: `app.py` — `build_ops_report` 아래(4049행), `signal_pipeline_employee`(4075-4081), `execution_mode_employee`(4086-4093), `equity_log_employee`(4110-4139), `factor_ranking_employee`(4141-4152)
- Modify: `tests/test_office_jobs.py`

**Interfaces:**
- Consumes: `office_jobs.job_states`
- Produces: `_job_overlay(report, job_key, extra=None) -> dict`

- [ ] **Step 1: 공용 헬퍼를 만든다**

`app.py` 의 `def build_ops_report(...)` (4044-4049행) **바로 아래에** 추가한다:

```python
def _job_overlay(report, job_key, extra=None):
    """직원 보고서에 담당 자동화 잡의 상태를 덮어씌운다.

    잡이 꺼져 있으면 '주의' 가 아니라 '대기'(휴직) 다. 의도적으로 끈 것을
    고장으로 표시하면 경고등이 상시 점등되고, 사람은 그걸 무시하는 법을
    배운다 — 그러면 진짜 경고도 같이 묻힌다.

    잡 상태가 세션 상태보다 나쁠 때만 덮어쓴다. 세션에서 방금 뭔가 실패한
    것을 잡 상태가 초록으로 가려서는 안 된다.
    """
    state = _office_jobs.job_states(os.path.dirname(__file__))[job_key]
    rank = {'정상': 0, '대기': 1, '주의': 2, '경고': 3}
    reasons = list(report['reasons']) + list(state['reasons']) + list(extra or [])
    status = report['status']
    if not state['scheduled']:
        status = '대기'
    elif rank.get(state['status'], 0) > rank.get(status, 0):
        status = state['status']
    return build_ops_report(report['name'], report['icon'], status, reasons)
```

- [ ] **Step 2: `signal_pipeline_employee` 의 거짓 문구를 없애고 잡 상태를 붙인다**

`app.py:4075-4081` 의 다음 부분을

```python
        status = '주의' if (days_since is not None and days_since > 5) else '정상'
        reasons = [
            f"누적 시그널 {len(data)}건 (평가완료 {done} · 대기 {pending})",
            f"최근 시그널: {last_date}" + (f" ({days_since}일 전)" if days_since is not None else ''),
            "자동 스캔: signal-alerts.yml (매일 UTC 22:30, 월~금)",
        ]
        return build_ops_report('시그널 파이프라인', '📡', status, reasons)
```

다음으로 바꾼다. **하드코딩된 스케줄 문구를 지우는 것이 핵심이다** — 그 줄이 지금 거짓말을 하고 있다.

```python
        reasons = [
            f"누적 시그널 {len(data)}건 (평가완료 {done} · 대기 {pending})",
            f"최근 시그널: {last_date}" + (f" ({days_since}일 전)" if days_since is not None else ''),
        ]
        return _job_overlay(
            build_ops_report('시그널 파이프라인', '📡', '정상', reasons),
            'signal_alerts')
```

- [ ] **Step 3: `execution_mode_employee` 의 거짓 문구를 없앤다**

`app.py:4086-4093` 을 다음으로 바꾼다:

```python
def execution_mode_employee():
    """실행 모드 직원: 현재 시스템이 시그널 전용 모드임을 명시 — 안전성 투명성 담당.

    워크플로가 켜져 있는지는 여기서 단정하지 않는다. 예전에 'signal-alerts.yml:
    활성' 이라고 적어뒀다가 크론이 꺼진 뒤에도 그 문구가 남아 사실과 달라졌다.
    상태는 office_jobs 가 워크플로 파일에서 읽는다.
    """
    states = _office_jobs.job_states(os.path.dirname(__file__))
    reasons = ['현재 모드: 시그널 전용 — 실제 주문 없음',
               '실제 매매는 시그널 확인 후 사용자가 직접 실행']
    for key in ('signal_alerts', 'paper_trade'):
        s = states[key]
        reasons.append(f"{s['name']}: {'예정대로' if s['scheduled'] else '휴직'} — {s['label']}")
    return build_ops_report('실행 모드', '⚙️', '정상', reasons)
```

- [ ] **Step 4: `factor_ranking_employee` 에 IC 잡 상태를 붙인다**

`app.py:4145-4146` 의 이른 반환

```python
        return build_ops_report('팩터 랭킹', '📊', '대기',
            ['아직 팩터 분석 미실행 — "📊 팩터 분석 실행" 버튼으로 시작'])
```

을 다음으로 바꾼다:

```python
        return _job_overlay(
            build_ops_report('팩터 랭킹', '📊', '대기',
                ['아직 팩터 분석 미실행 — "📊 팩터 분석 실행" 버튼으로 시작']),
            'ic_update')
```

그리고 `app.py:4152` 의 마지막 반환

```python
    return build_ops_report('팩터 랭킹', '📊', '주의' if failed else '정상', reasons)
```

을 다음으로 바꾼다:

```python
    return _job_overlay(
        build_ops_report('팩터 랭킹', '📊', '주의' if failed else '정상', reasons),
        'ic_update')
```

- [ ] **Step 5: `equity_log_employee` 에 페이퍼트레이드 잡 상태를 붙인다**

`app.py:4110-4139` 안의 `return build_ops_report('계좌 현황', '📊', ...)` 는 세 곳이다 (파일 없음 / 기록 0건 / 정상 경로). **세 곳 모두** 다음 형태로 감싼다:

```python
        return _job_overlay(
            build_ops_report('계좌 현황', '📊', <원래 status>, <원래 reasons>),
            'paper_trade')
```

예를 들어 4116-4117행은

```python
        return build_ops_report('계좌 현황', '📊', '정상',
            ['equity_log.json 없음 — 시그널 전용 모드에서는 정상 (실거래가 없어 자산 변동 기록도 없음)'])
```

에서

```python
        return _job_overlay(
            build_ops_report('계좌 현황', '📊', '정상',
                ['equity_log.json 없음 — 시그널 전용 모드에서는 정상 (실거래가 없어 자산 변동 기록도 없음)']),
            'paper_trade')
```

가 된다. 확인: 치환 후 `grep -c "build_ops_report('계좌 현황'" app.py` 는 3, `grep -c "'paper_trade')" app.py` 도 3이어야 한다.

- [ ] **Step 6: 회귀 테스트를 쓴다**

`tests/test_office_jobs.py` 끝에 추가한다:

```python
# ── app 배선 회귀 ────────────────────────────────────────────────────
#
# 실제 저장소 상태로 돈다. 네트워크는 타지 않는다 (이 세 직원 함수는
# 로컬 파일과 세션 상태만 본다).

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
```

- [ ] **Step 7: 통과를 확인한다**

Run: `python -m pytest -q && ruff check .`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add app.py tests/test_office_jobs.py
git commit -m "fix: 꺼진 잡을 고장으로 표시하던 문제 + 워크플로 스케줄 거짓 문구 제거"
```

---

### Task 6: 상황판 2단 — 야간 근무 현황

**Files:**
- Modify: `app.py` — 컴포넌트 호출부(7529-7548 부근)
- Modify: `office_game/index.html` — `TOP_BAR_H`(38), 전역 상태(53-54), `drawTopBar`(347-390), `applyRender`(579-595)

**Interfaces:**
- Consumes: `office_jobs.job_states`, `office_jobs.repo_stale_days`
- Produces: 컴포넌트 인자 `jobs: list[dict]` (각 dict 는 `{'icon','name','label','status'}`), `repoStale: int | None`

- [ ] **Step 1: Python 쪽에서 인자를 넘긴다**

`app.py` 의 `_clicked = _office_game_component(` 호출 **바로 위에** 추가한다:

```python
        _job_states = _office_jobs.job_states(os.path.dirname(__file__))
        _job_chips = [{'icon': s['icon'], 'name': s['name'],
                       'label': s['label'], 'status': s['status']}
                      for s in _job_states.values()]
        _repo_stale = _office_jobs.repo_stale_days(_job_states)
```

그리고 호출 인자에 두 개를 더한다:

```python
        _clicked = _office_game_component(
            rooms=_game_rooms, dark=_dark_mode,
            selected=(f"{_sel_now[0]}|{_sel_now[1]}" if _sel_now else None),
            working=bool(_pending_tk), ticker=_pending_tk, result=_result_arg,
            jobs=_job_chips, repoStale=_repo_stale,
            key="office_game_scene", default=None)
```

- [ ] **Step 2: 캔버스에서 받는다**

`office_game/index.html` 의 `let lastResult = null;` (54행) **아래에** 추가한다:

```javascript
let jobs = [];                      // 야간 근무 현황 칩
let repoStale = null;               // 저장소를 안 당긴 것으로 보이는 일수
```

`applyRender` 의 `lastResult = args.result || null;` **아래에** 추가한다:

```javascript
  jobs = Array.isArray(args.jobs) ? args.jobs : [];
  repoStale = (typeof args.repoStale === "number") ? args.repoStale : null;
```

- [ ] **Step 3: 전광판을 두 줄로 넓힌다**

`const TOP_BAR_H = 40;` 을 다음으로 바꾼다:

```javascript
const TOP_BAR_H = 68;               // 전광판: 1줄 업무 지시 + 2줄 야간 근무 현황
const BAR_ROW1_Y = 22;              // 1줄 세로 중심
const BAR_ROW2_Y = 50;              // 2줄 세로 중심
```

`drawTopBar` 안에서 `TOP_BAR_H / 2` 를 쓰는 모든 자리를 `BAR_ROW1_Y` 로 바꾼다.

확인: `grep -c "TOP_BAR_H / 2" office_game/index.html` 이 **0** 이어야 한다.

- [ ] **Step 4: 2줄에 칩을 그린다**

`drawTopBar` 의 벽시계 그리기 뒤, 함수 끝에 추가한다:

```javascript
  // ── 2줄: 야간 근무 현황 ──
  // 평상시엔 조용하고, 경고일 때만 깜빡여 시선을 끈다. 상시 점등은
  // 사람에게 무시하는 법을 가르친다.
  const CHIP_COLOR = { "정상": "#10b981", "주의": "#f59e0b",
                       "경고": "#ef4444", "대기": "#7d93b8" };
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  if (repoStale !== null) {
    ctx.fillStyle = "#f59e0b"; ctx.font = "700 11.5px sans-serif";
    ctx.fillText("⚠️ 저장소를 " + repoStale + "일 안 당겼습니다 — 아래 상태가 오래된 값일 수 있습니다",
                 24, BAR_ROW2_Y);
    return;
  }
  let cx = 24;
  for (const j of jobs) {
    const txt = j.icon + " " + j.name + " " + j.label;
    ctx.font = "700 11.5px sans-serif";
    const w = ctx.measureText(txt).width;
    if (cx + w > LOGICAL_W - 30) break;
    ctx.globalAlpha = (j.status === "경고") ? 0.55 + 0.45 * Math.sin(t * 5) : 1;
    ctx.fillStyle = CHIP_COLOR[j.status] || "#7d93b8";
    ctx.fillText(txt, cx, BAR_ROW2_Y);
    ctx.globalAlpha = 1;
    cx += w + 14;
    ctx.fillStyle = pal().boardSub;
    ctx.fillText("·", cx - 9, BAR_ROW2_Y);
  }
```

- [ ] **Step 5: 육안 확인**

Run: `streamlit run app.py`
Expected: 상단 전광판이 두 줄이고, 2줄에 `📊 성적표 기록 오늘 · 📈 IC 갱신 N일 전 · 📡 시그널 알림 휴직 · 💰 페이퍼 트레이드 휴직 · 📮 일별 리포트 추적 불가` 가 보인다. 방들이 전광판에 가리지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add app.py office_game/index.html
git commit -m "feat: 사무실 전광판 2단 — 야간 근무 현황 표시"
```

---

### Task 7: 시간대 조명

**Files:**
- Modify: `office_game/index.html` — `drawParticles` 위에 새 함수, `frame`(516-544)

**Interfaces:**
- Consumes: `rooms`, `deskPos`
- Produces: `dayPhase() -> "day" | "dusk" | "night"`, `drawTimeOverlay()`

- [ ] **Step 1: 시간대 판정과 오버레이 함수를 만든다**

`function drawParticles(dt) {` **바로 위에** 추가한다:

```javascript
/* ── 시간대 조명 ──────────────────────────────────────────────
   다크모드(dark)는 '사용자 테마', 시간대는 '극중 시각' 으로 축이 다르다.
   그래서 PALETTE 를 건드리지 않고 그 위에 반투명 오버레이를 덮는다.

   야간에 켜지는 자리는 실제로 그 시간에 잡이 도는 팀이다 —
   조명이 장식이 아니라 정보가 되는 지점. */
function dayPhase() {
  const h = new Date().getHours();
  if (h >= 6 && h < 18)  return "day";
  if (h >= 18 && h < 22) return "dusk";
  return "night";
}

const NIGHT_LIT_ROOMS = new Set(["perf", "ops", "siggen"]);   // 야간 잡 담당 방

function drawTimeOverlay() {
  const phase = dayPhase();
  if (phase === "day") return;

  ctx.fillStyle = phase === "dusk" ? "rgba(40,28,60,.18)" : "rgba(8,12,26,.46)";
  ctx.fillRect(0, TOP_BAR_H, LOGICAL_W, canvasH - TOP_BAR_H);

  if (phase !== "night") return;

  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const room of rooms) {
    if (!NIGHT_LIT_ROOMS.has(room.key)) continue;
    const seats = Math.max(room.chars.length, room.ghost || 0);
    for (let i = 0; i < seats; i++) {
      const d = deskPos(room, i);
      const g = ctx.createRadialGradient(d.x, d.y, 4, d.x, d.y, 52);
      g.addColorStop(0, "rgba(255,214,130,.30)");
      g.addColorStop(1, "rgba(255,214,130,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(d.x, d.y, 52, 0, 6.29); ctx.fill();
    }
  }
  ctx.restore();
}
```

- [ ] **Step 2: 프레임 루프에 넣는다**

`frame` 함수의 다음 두 줄을

```javascript
  const list = [...chars.values()].sort((a, b) => a.y - b.y);
  list.forEach(ch => updateChar(ch, dt, t, now));
  list.forEach(ch => drawChar(ch, t));
```

다음으로 바꾼다. 오버레이는 방·책상 위, 캐릭터 아래에 깔려야 캐릭터가 어둠에 묻히지 않는다:

```javascript
  const list = [...chars.values()].sort((a, b) => a.y - b.y);
  list.forEach(ch => updateChar(ch, dt, t, now));
  drawTimeOverlay();
  list.forEach(ch => drawChar(ch, t));
```

- [ ] **Step 3: 육안 확인**

Run: `streamlit run app.py`
Expected: 낮에는 지금과 같다. OS 시계를 23시로 바꾸고 새로고침하면 사무실 전체가 어두워지고 성과 평가팀·운영팀·시그널 생성팀 자리에만 노란 조명 원이 생긴다. 캐릭터는 여전히 또렷하게 보인다.

- [ ] **Step 4: 커밋**

```bash
git add office_game/index.html
git commit -m "feat: 시간대 조명 — 야간엔 그 시간에 도는 잡의 자리만 켜진다"
```

---

### Task 8: 그래픽 — 방 색, 벽·창문, 책상 원근, 선택 피드백

**Files:**
- Modify: `office_game/index.html` — `PALETTE` 아래(74행), `layoutRooms`(78-91), `floorBounds`(95-97), `drawRoom` 위·안(392-442), `frame`(516-544)

**Interfaces:**
- Consumes: `dayPhase()` (Task 7)
- Produces: `ROOM_TINT`, `roomTint(key)`, `WALL_H`, `drawWall()`

- [ ] **Step 1: 방별 색을 정의한다**

`function pal() { return dark ? PALETTE.dark : PALETTE.light; }` **바로 아래에** 추가한다:

```javascript
/* 방마다 색 아이덴티티. 지금은 6개 방이 전부 같은 나무색이라 구분이 안 된다.
   헤더 바와 바닥 색조에만 쓰고, 캐릭터·책상 색은 건드리지 않는다. */
const ROOM_TINT = {
  analyst: "#3b82f6", ops: "#f97316", siggen: "#8b5cf6",
  ml: "#06b6d4", bt: "#b45309", qa: "#10b981", perf: "#eab308",
};
function roomTint(key) { return ROOM_TINT[key] || "#8b6f3e"; }

/* 사무실 벽 띠 높이. 방 배치가 이만큼 아래로 내려간다. */
const WALL_H = 26;
```

- [ ] **Step 2: 방 배치를 벽 아래로 내린다**

`layoutRooms` 의

```javascript
  let x = 8, y = TOP_BAR_H + 8, rowMax = 0;
```

를 다음으로 바꾼다:

```javascript
  let x = 8, y = TOP_BAR_H + WALL_H + 6, rowMax = 0;
```

`floorBounds` 의

```javascript
  return { x1: 26, x2: LOGICAL_W - 26, y1: TOP_BAR_H + 62, y2: canvasH - 22 };
```

를 다음으로 바꾼다:

```javascript
  return { x1: 26, x2: LOGICAL_W - 26, y1: TOP_BAR_H + WALL_H + 60, y2: canvasH - 22 };
```

- [ ] **Step 3: 벽과 창문을 그린다**

`function drawRoom(room, t) {` **바로 위에** 추가한다:

```javascript
/* 벽과 창문. 창밖은 시간대에 따라 바뀌며 조명(drawTimeOverlay)과 같은
   dayPhase() 를 쓴다 — 두 곳이 어긋나면 "밤인데 창밖은 낮" 이 된다. */
function drawWall() {
  const phase = dayPhase();
  const sky = phase === "day"  ? ["#8ec5ff", "#cfe6ff"]
            : phase === "dusk" ? ["#f0956a", "#7a4a86"]
                               : ["#0b1430", "#1a2350"];
  ctx.fillStyle = dark ? "#161d2b" : "#cbb894";
  ctx.fillRect(0, TOP_BAR_H, LOGICAL_W, WALL_H);

  for (let i = 0; i < 4; i++) {
    const wx = 120 + i * 300, wy = TOP_BAR_H + 5, ww = 96, wh = WALL_H - 10;
    const g = ctx.createLinearGradient(wx, wy, wx, wy + wh);
    g.addColorStop(0, sky[0]); g.addColorStop(1, sky[1]);
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.roundRect(wx, wy, ww, wh, 3); ctx.fill();
    if (phase === "night") {                       // 별
      ctx.fillStyle = "rgba(255,255,255,.75)";
      for (let s = 0; s < 5; s++) {
        ctx.beginPath();
        ctx.arc(wx + 12 + s * 18, wy + 4 + (s % 3) * 4, 0.9, 0, 6.29);
        ctx.fill();
      }
    }
    ctx.strokeStyle = dark ? "#2b3446" : "#8b6f3e"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.roundRect(wx, wy, ww, wh, 3); ctx.stroke();
  }
}
```

`frame` 함수의 `drawTopBar(t);` **바로 뒤에** 추가한다:

```javascript
  drawWall();
```

- [ ] **Step 4: 방에 팀 색과 선택 피드백을 넣는다**

`drawRoom` 의 테두리 그리기(402-403행)

```javascript
  ctx.strokeStyle = p.border; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.roundRect(room.x, room.y, room.w, room.h, 10); ctx.stroke();
```

**뒤에** 추가한다:

```javascript
  const tint = roomTint(room.key);
  // 바닥에 팀 색을 아주 옅게 깐다 — 방 구분은 되되 나무 바닥 질감은 남는다.
  ctx.save();
  ctx.beginPath(); ctx.roundRect(room.x, room.y, room.w, room.h, 10); ctx.clip();
  ctx.globalAlpha = 0.10; ctx.fillStyle = tint;
  ctx.fillRect(room.x, room.y, room.w, room.h);
  ctx.globalAlpha = 1; ctx.restore();

  // 선택된 직원이 있는 방을 밝힌다 — 지금은 캐릭터 둘레 원 하나뿐이라
  // 넓은 화면에서 "어디를 보고 있는지" 를 놓치기 쉽다.
  if (selectedId && selectedId.split("|")[0] === room.key) {
    ctx.save();
    ctx.beginPath(); ctx.roundRect(room.x, room.y, room.w, room.h, 10); ctx.clip();
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = "rgba(245,179,1,.07)";
    ctx.fillRect(room.x, room.y, room.w, room.h);
    ctx.restore();
    ctx.strokeStyle = "#f5b301"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.roundRect(room.x, room.y, room.w, room.h, 10); ctx.stroke();
  }
```

그리고 헤더 바 색(405-406행)

```javascript
  ctx.fillStyle = dark ? "#6f5730" : "#8b6f3e";
```

을 다음으로 바꾼다:

```javascript
  ctx.fillStyle = tint;
```

- [ ] **Step 5: 책상에 원근을 준다**

`drawRoom` 안의 책상 상판(421-423행)

```javascript
    ctx.fillStyle = p.deskTop;
    ctx.fillRect(d.x - 22, d.y + 12, 46, 4);
```

를 다음으로 바꾼다:

```javascript
    ctx.fillStyle = p.deskTop;
    ctx.beginPath();                       // 뒤가 좁은 사다리꼴 = 위에서 내려다본 원근
    ctx.moveTo(d.x - 18, d.y + 12);
    ctx.lineTo(d.x + 20, d.y + 12);
    ctx.lineTo(d.x + 24, d.y + 16);
    ctx.lineTo(d.x - 22, d.y + 16);
    ctx.closePath(); ctx.fill();
```

- [ ] **Step 6: 육안 확인**

Run: `streamlit run app.py`
Expected: 방 7개가 각각 다른 색조를 갖고, 상단에 창문 4개가 있는 벽 띠가 보이며, 직원을 클릭하면 그 방 전체가 금색 테두리로 밝아진다. 캔버스가 세로로 잘리지 않고 방이 벽에 겹치지 않는다.

- [ ] **Step 7: 전체 스위트와 린트**

Run: `python -m pytest -q && ruff check .`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add office_game/index.html
git commit -m "feat: 사무실 그래픽 — 방별 색·벽과 창문·책상 원근·선택 피드백"
```

---

### Task 9: 완료 판정 — 2026-07-23 의 고장이 화면에 뜨는가

스펙 10절의 성공 판정이다. 코드를 더 쓰지 않고 손으로 두 경우를 만들어 확인한다.

**Files:** 없음 (검증 후 문서만 커밋)

- [ ] **Step 1: 휴직 경우를 만든다**

`.github/workflows/analyst-log.yml` 의 `schedule:` 과 그 아래 `- cron:` 두 줄 앞에 `#` 를 붙여 저장한다. 앱을 새로고침한다.

Expected: 성적표 직원이 💤(대기) 로 바뀌고, 상황판에 `📊 성적표 기록 휴직` 이 뜬다. **❗ 경고가 아니어야 한다** — 의도적으로 끈 것을 고장으로 표시하면 안 된다.

- [ ] **Step 2: 밀림 경우를 만든다**

주석을 되돌린다. `data/analyst_log/2026.jsonl` 의 마지막 줄 `date` 를 4영업일 전 날짜로 바꿔 저장한다. 앱을 새로고침한다.

Expected: 성적표 직원이 ❗(경고) 로 바뀌고, 상황판 칩이 빨갛게 깜빡인다.

- [ ] **Step 3: 원복한다**

```bash
git checkout .github/workflows/analyst-log.yml data/analyst_log/2026.jsonl
```

- [ ] **Step 4: 설계서와 계획을 커밋한다**

```bash
git add docs/superpowers/specs/2026-07-24-office-night-shift-design.md docs/superpowers/plans/2026-07-24-office-night-shift.md
git commit -m "docs: 사무실 야간 근무 가시화 설계서·구현 계획"
```

---

## Self-Review

**스펙 커버리지**

| 스펙 절 | 태스크 |
|---|---|
| 1. 워크플로가 진실의 원천 | Task 1 |
| 1. `pyyaml` 명시 | Task 1 Step 5 |
| 2. 예정과 실적 대조, 상태 6종 | Task 2, Task 3 |
| 2. 추적 불가(daily-report) | Task 3 (`result_key=None`) |
| 2. 실행 이력이 아니라 결과 파일 | Task 2 전체 |
| 3. 성과 평가팀 방 + 성적표 직원 + ghost 좌석 | Task 4 |
| 3. 기존 3직원 워크플로 인식 | Task 5 |
| 4. 상황판 2단, 경고만 깜빡임 | Task 6 |
| 5. 시간대 조명 | Task 7 |
| 6. 방 색·벽과 창문·책상 원근·선택 피드백 | Task 8 |
| 7. 저장소 미당김 힌트 | Task 3 (`repo_stale_days`) + Task 6 (표시) |
| 8. 파일 구조 | 전체 |
| 9. 테스트 목록 | Task 1~3, Task 5 |
| 10. 성공 판정 | Task 9 |

빠진 스펙 절 없음. 스펙의 비목표(잡 조작 UI, 실패 알림, 스프라이트 교체, 사운드)는 어느 태스크에도 들어 있지 않다.

**타입 일관성**

- `schedule_of` 반환 dict 키 `cron`/`period_days`/`weekdays_only` — Task 1 정의, Task 3 `_state_for` 사용. 일치.
- `job_states` 반환 dict 키 `key`/`name`/`icon`/`status`/`reasons`/`days_since`/`scheduled`/`label` — Task 3 정의, Task 4·5·6 사용. 일치.
- `JobSpec.key` 값 `analyst_log`/`ic_update`/`signal_alerts`/`paper_trade`/`daily_report` — Task 3 정의, Task 4(`'analyst_log'`)·Task 5(`'signal_alerts'`/`'ic_update'`/`'paper_trade'`) 사용. 일치.
- `OfficeRoom.ghost` — Task 4 Step 2 필드 추가 → Step 3 사용 → Step 6 `ghost=3` 전달. 일치.
- `NamedTuple`/`Optional` import — Task 1 의 모듈 상단 import 블록에 이미 포함. Task 3 의 `JobSpec` 이 쓴다. 일치.
- `_MAX_BUSDAY_SCAN` — Task 1 정의, Task 3 `_gap` 사용. 일치.
- `dayPhase()` — Task 7 정의, Task 8 `drawWall` 사용. Task 8 이 뒤에 오므로 순서 맞음.
- `WALL_H` — Task 8 Step 1 정의, Step 2·3 사용. 같은 태스크 안, 순서 맞음.
- `roomTint`/`tint` — Task 8 Step 1 정의, Step 4 사용. 일치.

**알려진 위험**

- Task 6 Step 3 의 `TOP_BAR_H / 2` → `BAR_ROW1_Y` 치환은 기계적이라 놓치기 쉽다. 그래서 `grep -c` 확인을 스텝에 넣었다.
- Task 8 은 `drawRoom` 안의 여러 지점을 손댄다. 순서를 지켜야 `tint` 가 선언 전에 쓰이지 않는다 — Step 4 에서 `const tint` 를 헤더 바 사용부보다 먼저 선언하도록 배치했다.
