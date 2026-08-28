"""저녁 체인의 예약 시각은 순서와 간격을 지켜야 한다.

2026-08-28 에 GitHub 예약 큐 적체(실행이 최대 7.5시간 지연)를 흡수하려고
세 잡을 한 시간씩 당겼다. 당기는 건 한 시간이 한계다 — 애널리스트 기록이
겨울 장마감(21:00 UTC) 전으로 넘어가면 종가가 확정되기 전 지표를 재고,
그 하루치 판단이 통째로 틀어진다. 다음에 또 당기려는 사람을 여기서 막는다.

간격도 지연 흡수와 무관하게 필요하다: 앞 잡이 장부·기록을 커밋해야 뒤
잡이 그걸 읽는다. analyst-log 는 실측 7분까지 걸린다.
"""
import io
import os
import re

import pytest

WF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), '.github', 'workflows')

# 실행 순서대로. 미국 장마감(겨울 21:00 / 여름 20:00 UTC) 뒤에 이어진다.
CHAIN = ['paper-trade-us', 'analyst-log', 'daily-report', 'scorecard-publish']
WINTER_CLOSE_MIN = 21 * 60          # 21:00 UTC — 이보다 앞서면 종가가 없다
MIN_GAP_MIN = 15                    # 앞 잡의 커밋이 끝날 최소 여유


def _cron_minutes(name):
    path = os.path.join(WF_DIR, name + '.yml')
    if not os.path.isfile(path):
        pytest.skip('워크플로 없음: ' + name)
    text = io.open(path, encoding='utf-8').read()
    # 주석(#로 시작하는 줄)이 아닌 진짜 cron 항목만.
    crons = re.findall(r'^\s*- cron: "(\S+) (\S+) (\S+) (\S+) (\S+)"', text, re.M)
    assert len(crons) == 1, name + ' 의 cron 이 1개가 아니다: ' + repr(crons)
    minute, hour, _, _, dow = crons[0]
    assert dow == '1-5', name + ' 은 평일에만 돌아야 한다: ' + dow
    return int(hour) * 60 + int(minute)


def test_chain_runs_in_order_after_close():
    times = [(n, _cron_minutes(n)) for n in CHAIN]

    analyst = dict(times)['analyst-log']
    assert analyst > WINTER_CLOSE_MIN, (
        '애널리스트 기록이 겨울 장마감(21:00 UTC) 전이다 — 종가 확정 전에 잰다')

    for (prev_name, prev), (name, cur) in zip(times, times[1:]):
        assert cur - prev >= MIN_GAP_MIN, (
            '{} → {} 간격이 {}분뿐이다 (최소 {}분)'.format(
                prev_name, name, cur - prev, MIN_GAP_MIN))
