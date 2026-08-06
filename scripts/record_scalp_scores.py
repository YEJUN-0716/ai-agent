#!/usr/bin/env python
"""15분봉 점수 기록·채점 진입점 — .github/workflows/analyst-log.yml 이 부른다.

로직은 signal_worker.record_scalp_main() 이 갖는다. 여기는 리포 루트를
import 경로에 넣고 종료코드를 넘기는 껍데기다 (record_analyst_scores.py 와
같은 모양).

일봉 기록과 잡을 나누지 않고 스텝만 나눈 이유: 같은 시각(미국 장마감 후)에
같은 유니버스를 재고 결과를 같은 커밋으로 돌려놔야 한다. 다만 스텝은 갈라야
한다 — 분봉 다운로드가 실패해도 일봉 기록은 이미 끝나 있어야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signal_worker  # noqa: E402  (경로 삽입 뒤에 import 해야 한다)


if __name__ == '__main__':
    sys.exit(signal_worker.record_scalp_main())
