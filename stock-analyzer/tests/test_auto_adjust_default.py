"""yfinance 의 auto_adjust 기본값을 잠근다.

저장소의 `yf.download` 호출 20곳이 인자를 안 주고 기본값 True(=배당·분할 조정
총수익 가격)에 기대고 있다. requirements.txt 가 `yfinance>=0.2.31` 이라 기본값이
바뀐 버전이 깔리면 그 20곳이 조용히 미조정가로 바뀐다 — 수익률·상대강도·
신용스프레드가 전부 배당만큼 틀리는데 예외는 안 난다.

호출 하나하나가 아니라 가정 하나를 잠근다. 앞으로 추가될 다운로드까지 덮는다.
일부러 미조정가를 쓰는 곳(modules/virtual_broker.py, scripts/measure_index_autopilot.py)
은 이미 auto_adjust=False 를 명시하고 있으므로 이 잠금과 무관하다.
"""
import inspect

import yfinance as yf


def test_yf_download_auto_adjust_defaults_true():
    default = inspect.signature(yf.download).parameters['auto_adjust'].default
    assert default is True, (
        f"yfinance 의 auto_adjust 기본값이 {default!r} 로 바뀌었다 — "
        "인자를 생략한 yf.download 호출이 전부 미조정가를 받는다. "
        "requirements.txt 의 yfinance 버전을 되돌리거나, 모든 호출에 "
        "auto_adjust=True 를 명시할 것")
