"""빗각 8단계 번인 러너의 selftest 를 CI 게이트에 올린다.

검사 여덟은 스크립트 안에 산다(`run_bitgak_paper.selftest`) — MOC body 오타,
스톱 회귀, 멱등, 접두사, 상호 취소, 정정값, dry-run 누수, 장부 스키마.
여기서는 그걸 pytest 가 매번 돌리게만 한다. 네트워크 없음.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_bitgak_paper import selftest  # noqa: E402


def test_bitgak_paper_selftest():
    selftest()
