"""기록을 커밋하는 워크플로는 앞 스텝이 실패해도 커밋해야 한다.

analyst-log 의 주석은 "15분봉 스텝이 실패해도 일봉 기록은 다음 스텝이
커밋한다" 고 적어 뒀는데 커밋 스텝에 `if: always()` 가 없었다 — 스텝이
빨갛게 끝나면 커밋이 통째로 스킵되고 그날 애널리스트 기록이 사라진다.
scorecard-publish 는 같은 이유로 이미 always() 를 달고 있었다(사본 둘 중
한쪽만 고쳐진 자리).

열거하지 않고 **디렉터리를 훑는다** — 워크플로가 하나 늘 때 목록을 고치는
걸 잊으면 검사가 조용히 비어 간다.
"""
import os
import re

import pytest

WF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), '.github', 'workflows')


def _commit_steps(text):
    """`git commit` 을 실행하는 스텝의 (이름, 그 스텝 본문) 목록."""
    steps = re.split(r'\n      - name: ', text)[1:]
    return [(s.split('\n', 1)[0], s) for s in steps if 'git commit' in s]


def _workflows():
    if not os.path.isdir(WF_DIR):
        pytest.skip('워크플로 디렉터리 없음')
    return [f for f in sorted(os.listdir(WF_DIR)) if f.endswith('.yml')]


def test_commit_steps_run_even_when_an_earlier_step_fails():
    missing = []
    for name in _workflows():
        with open(os.path.join(WF_DIR, name), encoding='utf-8') as f:
            text = f.read()
        for step_name, body in _commit_steps(text):
            if not re.search(r'^\s+if:\s*always\(\)', body, re.M):
                missing.append(f'{name} :: {step_name}')
    assert not missing, (
        '산출물을 커밋하는 스텝에 if: always() 가 없다 — 앞 스텝이 실패하면 '
        f'이미 만들어진 기록까지 같이 버려진다: {missing}')


def test_the_scan_actually_finds_commit_steps():
    """가드가 죽지 않았는지 본다 — 0개를 훑고 통과하면 검사가 아니다."""
    found = sum(len(_commit_steps(open(os.path.join(WF_DIR, n), encoding='utf-8').read()))
                for n in _workflows())
    assert found >= 3, f'커밋 스텝을 {found}개밖에 못 찾았다 — 파싱이 깨졌다'
