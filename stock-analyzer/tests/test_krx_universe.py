"""
KRX(한국) 유니버스 배선 테스트.

한국 종목을 돌리는 데 필요한 부품은 이미 다 있었다 — 프리셋, DART 폴백,
원화 표기, 한국 세금 모듈. 끊겨 있던 건 배선 한 곳이었다:
signal-alerts.yml 이 DART_API_KEY 를 넘기지 않아, Actions 에서 한국
유니버스를 돌리면 DART 폴백이 통째로 죽었다.

**이 고장이 조용하다는 게 문제의 핵심이다.** 가격은 정상적으로 받아지므로
실패 종목 수는 0이고, ROE·이익률만 비어 퀄리티 팩터가 전 종목 동일값으로
주저앉는다. Z-score 가 그걸 50점으로 정규화해 버려서, 알림만 보면
4팩터 중 하나가 사라진 걸 알 방법이 없다.

여기서 (1) 프리셋 형식 (2) 유니버스 해석 (3) 경고 발생 조건 (4) 워크플로
배선을 고정한다. 네트워크를 타지 않는다.
"""
import re
from pathlib import Path

import pytest

import app
import signal_worker as worker

KRX_TICKER_PATTERN = re.compile(r"^\d{6}\.(KS|KQ)$")
KOREAN_PRESET = "한국 대형 15"
# 2026-08-19 ai-agent 와 합치면서 .github 는 한 단계 위(저장소 루트)로 갔다.
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "signal-alerts.yml"


# ── 1. 프리셋 형식 ──────────────────────────────────────────────────

def test_korean_preset_exists():
    assert KOREAN_PRESET in app.UNIVERSE_PRESETS


def test_korean_preset_tickers_are_well_formed():
    """KRX 티커는 6자리 종목코드 + .KS/.KQ. 형식이 틀리면 yfinance 가 조용히
    빈 프레임을 주고, 그 종목은 '분석 실패' 로만 집계돼 원인을 못 찾는다."""
    for ticker in app.UNIVERSE_PRESETS[KOREAN_PRESET]:
        assert KRX_TICKER_PATTERN.match(ticker), f"{ticker} 형식이 KRX 규칙에 안 맞는다"


def test_korean_preset_has_no_duplicates():
    tickers = app.UNIVERSE_PRESETS[KOREAN_PRESET]
    assert len(tickers) == len(set(tickers))


def test_us_presets_contain_no_krx_tickers():
    """미국 프리셋에 KRX 종목이 섞이면 DART 경고가 엉뚱하게 뜬다."""
    for name, tickers in app.UNIVERSE_PRESETS.items():
        if name == KOREAN_PRESET:
            continue
        assert not worker.krx_tickers(tickers), f"{name} 에 KRX 종목이 섞여 있다"


# ── 2. 유니버스 해석 ────────────────────────────────────────────────

def test_preset_name_resolves_to_ticker_list():
    assert worker._resolve_universe(KOREAN_PRESET) == app.UNIVERSE_PRESETS[KOREAN_PRESET]


def test_comma_separated_krx_tickers_are_normalized():
    """UNIVERSE 를 직접 넘길 때 접미사 대소문자가 섞여도 받아준다."""
    assert worker._resolve_universe(" 005930.ks, 000660.KS ") == ["005930.KS", "000660.KS"]


def test_unknown_preset_name_is_treated_as_a_ticker_list():
    """오타 난 프리셋 이름이 조용히 빈 유니버스가 되지 않는다."""
    assert worker._resolve_universe("AAPL,MSFT") == ["AAPL", "MSFT"]


# ── 3. KRX 종목 판별 ────────────────────────────────────────────────

def test_krx_tickers_picks_both_kospi_and_kosdaq():
    mixed = ["AAPL", "005930.KS", "MSFT", "247540.KQ"]
    assert worker.krx_tickers(mixed) == ["005930.KS", "247540.KQ"]


def test_krx_tickers_is_empty_for_us_only_universe():
    assert worker.krx_tickers(["AAPL", "MSFT", "BRK-B"]) == []


# ── 4. DART 키 경고 — 조용한 고장을 시끄럽게 만든다 ─────────────────

def test_warns_when_krx_universe_has_no_dart_key():
    warning = worker.krx_data_warning(["005930.KS", "000660.KS"], dart_key="")
    assert warning is not None
    assert "DART_API_KEY" in warning
    assert "2종목" in warning


def test_no_warning_when_dart_key_is_present():
    assert worker.krx_data_warning(["005930.KS"], dart_key="dummy-key") is None


def test_no_warning_for_us_only_universe_even_without_key():
    """미국 종목만 돌 때는 DART 가 필요 없다 — 쓸데없는 경고를 띄우지 않는다."""
    assert worker.krx_data_warning(["AAPL", "MSFT"], dart_key="") is None


def test_warning_counts_only_krx_tickers_in_a_mixed_universe():
    warning = worker.krx_data_warning(["AAPL", "005930.KS", "MSFT"], dart_key="")
    assert "1종목" in warning


def test_warning_reads_the_environment_when_key_is_not_passed(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert worker.krx_data_warning(["005930.KS"]) is not None
    monkeypatch.setenv("DART_API_KEY", "dummy-key")
    assert worker.krx_data_warning(["005930.KS"]) is None


# ── 5. 알림 메시지 ──────────────────────────────────────────────────

REBAL = {"next_rebal": "3일 후", "buy_count": 1, "sell_count": 0, "hold_count": 0}
ACTION = {
    "ticker": "005930.KS", "action": "🟢 매수", "weight": "20.0%",
    "price": "₩70,000", "alloc": "₩2,000,000", "qty": "28주",
    "reason": "팩터 80점", "priority": "HIGH", "mom": "+12.0%",
}


def test_warning_appears_above_the_rankings():
    """랭킹을 믿을지 말지가 먼저다 — 경고가 종목 목록보다 위에 와야 한다."""
    msg = worker.build_message(["005930.KS"], [ACTION], REBAL, [],
                               warning="⚠️ DART_API_KEY 미설정")
    assert "DART_API_KEY" in msg
    assert msg.index("DART_API_KEY") < msg.index("005930.KS")


def test_message_is_unchanged_without_a_warning():
    """경고가 없으면 메시지에 흔적이 남지 않는다."""
    msg = worker.build_message(["AAPL"], [{**ACTION, "ticker": "AAPL"}], REBAL, [])
    assert "⚠️" not in msg


def test_failed_tickers_still_reported_alongside_a_warning():
    """데이터 품질 경고와 분석 실패 보고는 별개다 — 둘 다 나와야 한다."""
    msg = worker.build_message(["005930.KS", "000660.KS"], [ACTION], REBAL,
                               ["000660.KS"], warning="⚠️ DART_API_KEY 미설정")
    assert "DART_API_KEY" in msg
    assert "분석 실패 1종목" in msg


# ── 6. 워크플로 배선 — 이게 원래 끊겨 있던 곳 ───────────────────────

def test_signal_alerts_workflow_passes_the_dart_key():
    """워크플로가 DART_API_KEY 를 넘기지 않으면 위 경고가 매번 뜬다.

    소스 레벨로 확인하는 이유: 이 배선은 코드로는 안 잡힌다. 실제로 Actions 를
    돌려 보기 전까지 조용하고, 돌려도 실패로 나타나지 않는다.
    """
    assert "DART_API_KEY" in WORKFLOW.read_text(encoding="utf-8"), (
        "signal-alerts.yml 이 DART_API_KEY 를 넘기지 않는다 — "
        "KRX 유니버스에서 퀄리티 팩터가 조용히 죽는다."
    )


@pytest.mark.parametrize("var", ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "UNIVERSE"])
def test_signal_alerts_workflow_keeps_its_existing_wiring(var):
    assert var in WORKFLOW.read_text(encoding="utf-8")
