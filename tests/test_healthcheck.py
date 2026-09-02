"""산출물 점검의 판정 한 줄 — 기준을 넘겼는가, 이력이 없는가.

이 로직이 틀리는 방식은 두 가지뿐이다. 늑대가 안 울거나(낡았는데 통과),
너무 울거나(멀쩡한데 경보). 둘 다 여기서 잡는다. 네트워크·git 없음.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import healthcheck  # noqa: E402

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _ages(**kw):
    """CHECKS 전체를 '방금'으로 채우고, 지정한 경로만 나이를 준다."""
    ages = {path: NOW for path, _, _ in healthcheck.CHECKS}
    for path, days in kw.items():
        ages[path.replace("__", "/")] = None if days is None else NOW - timedelta(days=days)
    return ages


def test_전부_최신이면_조용하다():
    assert healthcheck.audit(NOW, _ages()) == []


def test_기준을_넘기면_잡힌다():
    # equity_log 기준은 4일
    bad = healthcheck.audit(NOW, _ages(**{"stock-analyzer__equity_log.json": 5}))
    assert [b[0] for b in bad] == ["stock-analyzer/equity_log.json"]


def test_기준_안쪽이면_안_잡힌다():
    # 금요일 산출물을 월요일에 보는 상황(3일)은 정상이어야 한다
    assert healthcheck.audit(NOW, _ages(**{"stock-analyzer__equity_log.json": 3})) == []


def test_이력이_없으면_잡힌다():
    bad = healthcheck.audit(NOW, _ages(**{"data__heartbeat.json": None}))
    assert bad == [("data/heartbeat.json", None, 3, "옵시디언 동기화 (자택 PC, 매시간)")]


def test_주기가_다르면_기준도_다르다():
    # 주간(IC)은 5일이 정상, 평일 산출물은 5일이면 이상
    ages = _ages(**{"stock-analyzer__ic_weights.json": 5, "stock-analyzer__signal_log.json": 5})
    assert [b[0] for b in healthcheck.audit(NOW, ages)] == ["stock-analyzer/signal_log.json"]
