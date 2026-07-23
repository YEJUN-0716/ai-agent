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
                    reasons=['추적 불가 — 결과가 저장소에 남지 않아 실적을 알 수 없다',
                             f'예정: {sched["cron"]}'])

    last = last_success(root, spec.result_key)
    if last is None:
        return dict(base, status=STATUS_IDLE, label='첫 실행 대기',
                    reasons=['첫 실행 대기 — 스케줄은 살아 있으나 아직 남긴 결과가 없다',
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
