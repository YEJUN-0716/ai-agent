#!/usr/bin/env python
"""애널리스트 점수 기록 전용 진입점 — .github/workflows/analyst-log.yml 이 부른다.

로직은 signal_worker.record_only_main() 이 갖는다. 여기는 리포 루트를
import 경로에 넣고 종료코드를 넘기는 껍데기다.

왜 signal_worker.py 를 그냥 돌리지 않는가: 그쪽은 매수/매도 시그널을 계산해
텔레그램으로 보낸다. 그 발송은 2026-07-20 에 의도적으로 멈춘 상태다(팩터에
측정 가능한 알파가 없음). 기록만 되살리려고 발송까지 켤 수는 없어서 경로를
갈랐다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signal_worker  # noqa: E402  (경로 삽입 뒤에 import 해야 한다)


if __name__ == '__main__':
    sys.exit(signal_worker.record_only_main())
