# 성적표 공개 채널 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 애널리스트 점수 기록을 매수 알림에서 떼어내 매 영업일 쌓이게 하고, 채점 결과를 공개 텔레그램 채널에 틀린 성적까지 그대로 발행한다.

**Architecture:** 워크플로 두 개로 분리한다. 기록기(22:30 UTC)는 이미 구현된 `record_only_main()` 을 부르며 텔레그램 토큰을 아예 주입받지 않는다. 발행기(23:45 UTC)는 `analyst_log` 와 가격에서 매번 성적을 다시 계산하고, 지평별 표본 수가 늘었을 때만 발행한다. 성적은 저장하지 않는다 — 저장하는 것은 발행 이력뿐이다.

**Tech Stack:** Python 3.12, pytest, numpy/scipy(기존), requests, GitHub Actions

**설계 문서:** [2026-07-28-scorecard-channel-design.md](../specs/2026-07-28-scorecard-channel-design.md)

## Global Constraints

- 발행문에 **종목별 매수·매도·목표가 표현을 쓰지 않는다.** 점수·순위·사후 채점 결과만 발행한다. (유사투자자문업 회피)
- 모든 발행 메시지 하단에 면책 문구를 **항상** 붙인다. 옵션으로 만들지 않는다. 문구 원문:
  `이 채널은 예측 기록과 사후 채점을 공개합니다. 투자 자문이나 매매 권유가 아니며, 투자 판단과 그 결과는 본인에게 귀속됩니다.`
- 기록 워크플로에 `TELEGRAM_TOKEN` 을 **주입하지 않는다.** 발송 경로를 물리적으로 막는 장치다.
- 발행 대상은 새 시크릿 `TELEGRAM_PUBLIC_CHANNEL_ID` 다. 기존 `TELEGRAM_CHAT_ID`(개인 P&L)와 절대 섞지 않는다.
- 채점은 유니버스 전 종목으로 하고, 발행 노출은 애널리스트별 **상위 5종목**만 한다.
- 통계적 판정은 `n` 이 아니라 **`effective_n`**(겹침 보정 유효 표본)으로 한다.
- 음수 IC 를 숨기거나 절댓값으로 바꾸지 않는다. 표본이 모자라면 모자라다고 쓴다.
- 기존 `signal-alerts.yml` 의 크론은 **켜지 않는다.** `workflow_dispatch` 전용으로 둔다.

---

### Task 1: 기록 진입점과 워크플로

기록이 07-25 에서 끊겨 있다. 매일 결손이 쌓이므로 가장 먼저 복구한다.

**Files:**
- Modify: `signal_worker.py:383-384` (`__main__` 블록)
- Create: `.github/workflows/analyst-record.yml`
- Test: `tests/test_signal_worker_cli.py`

**Interfaces:**
- Consumes: `signal_worker.record_only_main()` — 기존 구현, `int` 반환(0=성공, 1=실패)
- Consumes: `signal_worker.main()` — 기존 구현, 반환값 없음(`None`), 실패 시 `sys.exit(1)`
- Produces: `signal_worker._cli_entry(argv: list) -> int`

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_signal_worker_cli.py` 생성:

```python
"""CLI 분기 — --record-only 는 발송 경로를 타지 않는다.

매수 알림을 켜는 것과 성적표 재료를 쌓는 것은 별개의 결정이다. 한쪽을
멈춰도 다른 쪽은 돌아야 한다.

signal_worker 는 함수 안에서 import 한다 — 최상단에서 끌어오면 core·yfinance
까지 따라온다. tests/test_analyst_log.py 가 쓰는 패턴을 그대로 따른다.
"""


def _stubs(monkeypatch):
    import signal_worker

    called = {}

    def fake_record():
        called["record"] = True
        return 0

    def fake_main():
        called["full"] = True

    monkeypatch.setattr(signal_worker, "record_only_main", fake_record)
    monkeypatch.setattr(signal_worker, "main", fake_main)
    return signal_worker, called


def test_record_only_flag_skips_full_scan(monkeypatch):
    sw, called = _stubs(monkeypatch)

    rc = sw._cli_entry(["signal_worker.py", "--record-only"])

    assert rc == 0
    assert called == {"record": True}


def test_no_flag_runs_full_scan(monkeypatch):
    sw, called = _stubs(monkeypatch)

    rc = sw._cli_entry(["signal_worker.py"])

    assert rc == 0
    assert called == {"full": True}


def test_record_failure_propagates_exit_code(monkeypatch):
    """0종목 기록은 성공이 아니다 — 워크플로가 빨갛게 죽어야 안다."""
    import signal_worker

    monkeypatch.setattr(signal_worker, "record_only_main", lambda: 1)

    assert signal_worker._cli_entry(["signal_worker.py", "--record-only"]) == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_signal_worker_cli.py -v`
Expected: FAIL — `AttributeError: module 'signal_worker' has no attribute '_cli_entry'`

- [ ] **Step 3: `_cli_entry` 를 구현한다**

`signal_worker.py` 파일 맨 끝의 `if __name__ == "__main__":` 블록을 다음으로 교체:

```python
def _cli_entry(argv):
    """--record-only 면 기록만 돌린다 — 텔레그램 발송 경로를 타지 않는다.

    매수를 추천할지와 "누가 잘 맞히나" 를 잴지는 별개의 결정이다.
    signal-alerts 의 크론이 꺼진 뒤에도 성적표 재료는 쌓여야 한다.
    """
    if "--record-only" in argv:
        return record_only_main()
    main()
    return 0


if __name__ == "__main__":
    sys.exit(_cli_entry(sys.argv))
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_signal_worker_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 기록 워크플로를 만든다**

`.github/workflows/analyst-record.yml` 생성:

```yaml
name: 애널리스트 점수 기록 (발송 없음)

on:
  # 미국 장마감 후. paper-trade-us(21:30 UTC) 완료 여유를 두고 22:30 UTC.
  #
  # signal-alerts 와 분리한 이유: 기록이 발송에 묶여 있어서, 팩터에 알파가
  # 없어 매수 알림을 끄자 성적표 재료까지 같이 끊겼다(2026-07-20 ~ 07-25).
  # 이 워크플로는 TELEGRAM_TOKEN 을 주입받지 않는다 — 발송 경로를 물리적으로
  # 막는다.
  schedule:
    - cron: "30 22 * * 1-5"
  workflow_dispatch:

concurrency:
  group: analyst-record
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  record:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: 저장소 체크아웃
        uses: actions/checkout@v5

      - name: Python 설정
        uses: actions/setup-python@v6
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: 의존성 설치
        run: pip install -r requirements.txt

      - name: 애널리스트 점수 기록
        env:
          PYTHONUTF8:   "1"
          # KRX 유니버스용. 없으면 퀄리티 팩터가 조용히 죽는다.
          DART_API_KEY: ${{ secrets.DART_API_KEY }}
          UNIVERSE:     "S&P 500 전체 (500종목)"
        run: python signal_worker.py --record-only

      - name: 기록 커밋 (변경 있을 때만)
        run: |
          git config user.email "actions@github.com"
          git config user.name "GitHub Actions"
          git add data/analyst_log
          if ! git diff --staged --quiet; then
            git commit -m "chore: analyst log $(date -u +%Y-%m-%d)"
            git pull --rebase origin main
            git push
          else
            echo "기록 변경 없음 — 커밋 생략"
          fi
```

- [ ] **Step 6: 커밋한다**

```bash
git add tests/test_signal_worker_cli.py signal_worker.py .github/workflows/analyst-record.yml
git commit -m "feat: 기록 전용 CLI 진입점과 워크플로 — 발송에서 분리"
```

---

### Task 2: 발행 이력 저장소

같은 판정을 두 번 보내지 않기 위한 최소 상태다. 성적은 저장하지 않는다.

**Files:**
- Create: `modules/publish_log.py`
- Test: `tests/test_publish_log.py`

**Interfaces:**
- Produces: `publish_log.LOG_DIRNAME: str`
- Produces: `publish_log.last_published_n(horizon: int, root=LOG_DIRNAME) -> int | None`
- Produces: `publish_log.record_published(date_str: str, horizon: int, n: int, root=LOG_DIRNAME) -> None`

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_publish_log.py` 생성:

```python
"""발행 이력 — 같은 판정을 두 번 보내지 않기 위한 최소 상태.

실제 data/analyst_log/ 는 건드리지 않는다. 전부 tmp_path 안에서 돈다.
"""
from modules import publish_log as pl


def test_never_published_returns_none(tmp_path):
    assert pl.last_published_n(5, root=tmp_path) is None


def test_round_trip(tmp_path):
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)

    assert pl.last_published_n(5, root=tmp_path) == 1


def test_horizons_are_independent(tmp_path):
    """5일을 발행했다고 21일이 발행된 것은 아니다."""
    pl.record_published("2026-07-30", 5, 3, root=tmp_path)

    assert pl.last_published_n(21, root=tmp_path) is None


def test_largest_n_wins(tmp_path):
    """표본은 단조증가한다 — 파일 순서에 기대지 않고 최대값을 쓴다."""
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)
    pl.record_published("2026-08-06", 5, 6, root=tmp_path)

    assert pl.last_published_n(5, root=tmp_path) == 6


def test_broken_line_is_skipped(tmp_path):
    """깨진 줄 하나가 발행 전체를 막지 않는다."""
    pl.record_published("2026-07-30", 5, 2, root=tmp_path)
    path = tmp_path / "published_2026.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{깨진 줄\n",
                    encoding="utf-8")

    assert pl.last_published_n(5, root=tmp_path) == 2
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_publish_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.publish_log'`

- [ ] **Step 3: 모듈을 구현한다**

`modules/publish_log.py` 생성:

```python
"""발행 이력 — data/analyst_log/published_YYYY.jsonl.

성적은 저장하지 않는다. analyst_log 와 가격이 유일한 진실이고 성적은
그것의 함수다. 중간 결과를 저장하면 원본과 어긋날 자리만 생긴다.

여기 남기는 것은 "무엇을 이미 보냈는가" 하나뿐이다. 지평별 마지막 표본 수를
비교해, 늘었으면 새로 판정된 날이 생긴 것으로 본다.
"""
import json
import os

LOG_DIRNAME = os.path.join("data", "analyst_log")
FILE_PREFIX = "published_"


def _year_path(root, year):
    return os.path.join(str(root), f"{FILE_PREFIX}{year}.jsonl")


def _read_all(root):
    root = str(root)
    if not os.path.isdir(root):
        return []

    records = []
    for name in sorted(os.listdir(root)):
        if not (name.startswith(FILE_PREFIX) and name.endswith(".jsonl")):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue      # 깨진 줄은 버린다 — 되살릴 방법이 없다
    return records


def last_published_n(horizon, root=LOG_DIRNAME):
    """그 지평에서 마지막으로 발행한 표본 수. 발행한 적 없으면 None."""
    horizon = int(horizon)
    ns = [r["n"] for r in _read_all(root)
          if r.get("horizon") == horizon and isinstance(r.get("n"), int)]
    return max(ns) if ns else None


def record_published(date_str, horizon, n, root=LOG_DIRNAME):
    """발행 1건을 기록한다. 한 줄이 발행 1건이다."""
    path = _year_path(root, date_str[:4])
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    record = {"published_at": date_str, "horizon": int(horizon), "n": int(n)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_publish_log.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋한다**

```bash
git add modules/publish_log.py tests/test_publish_log.py
git commit -m "feat: 발행 이력 저장소 — 중복 발행 차단"
```

---

### Task 3: 메시지 조립기

숫자를 문장으로 바꾼다. 계산도 네트워크도 하지 않으므로 전부 단위 테스트로 고정된다.

**Files:**
- Create: `modules/scorecard_message.py`
- Test: `tests/test_scorecard_message.py`

**Interfaces:**
- Consumes: `analyst_scorecard.score_analysts()` 의 반환 형태 —
  `{slug: {"mean_ic": float, "se": float|None, "t_stat": float|None, "n": int, "effective_n": float, "hit_rate": float}}`
- Produces: `scorecard_message.DISCLAIMER: str`
- Produces: `scorecard_message.MIN_EFFECTIVE_N: int`
- Produces: `scorecard_message.build_scorecard_message(horizon: int, stats: dict, missing_slugs: list) -> str`
- Produces: `scorecard_message.build_record_message(date_str: str, regime: str, top_by_slug: dict) -> str`
  — `top_by_slug` 는 `{slug: [(ticker, score), ...]}`

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_scorecard_message.py` 생성:

```python
"""발행 메시지 — 성적을 좋게 보이게 하는 장치가 없어야 한다.

이 채널의 유일한 차별화는 틀린 것을 그대로 보여주는 것이다. 그래서
음수 IC 표기와 표본 부족 표기를 테스트로 고정한다.
"""
from modules import scorecard_message as sm

_SMALL = {"chart": {"mean_ic": -0.03, "se": None, "t_stat": None,
                    "n": 1, "effective_n": 1.0, "hit_rate": 0.0}}
_BIG = {"quant": {"mean_ic": -0.094, "se": 0.02, "t_stat": -4.7,
                  "n": 60, "effective_n": 12.0, "hit_rate": 35.0}}


def test_scorecard_always_carries_disclaimer():
    msg = sm.build_scorecard_message(5, _SMALL, [])

    assert sm.DISCLAIMER in msg


def test_record_message_always_carries_disclaimer():
    msg = sm.build_record_message("2026-07-28", "bull",
                                  {"chart": [("AAPL", 73.8)]})

    assert sm.DISCLAIMER in msg


def test_small_sample_is_flagged_as_undecidable():
    """n=1 이면 통계적 판단 불가를 명시한다 — 감추지 않는 것이 전제다."""
    msg = sm.build_scorecard_message(5, _SMALL, [])

    assert "통계적 판단 불가" in msg


def test_sufficient_sample_shows_t_stat():
    msg = sm.build_scorecard_message(5, _BIG, [])

    assert "통계적 판단 불가" not in msg
    assert "-4.7" in msg


def test_negative_ic_is_shown_signed():
    """음수 IC 를 절댓값으로 바꾸거나 숨기지 않는다."""
    msg = sm.build_scorecard_message(5, _BIG, [])

    assert "-0.0940" in msg


def test_missing_slug_is_disclosed_with_reason():
    """슬러그를 조용히 빼면 성적표가 완전한 것처럼 보인다."""
    msg = sm.build_scorecard_message(5, _SMALL, ["quant"])

    assert "퀀트+재무" in msg
    assert "일별 펀더멘털 수집 미구축" in msg


def test_no_buy_sell_wording_in_record_message():
    """매수·매도 표현은 유사투자자문업 신고 대상이 된다."""
    msg = sm.build_record_message("2026-07-28", "bull",
                                  {"chart": [("AAPL", 73.8)]})

    for banned in ("매수", "매도", "목표가", "추천"):
        assert banned not in msg
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_scorecard_message.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.scorecard_message'`

- [ ] **Step 3: 모듈을 구현한다**

`modules/scorecard_message.py` 생성:

```python
"""발행 메시지 조립 — 숫자를 문장으로 바꾼다. 계산도 네트워크도 없다.

성적을 좋게 보이게 하는 장치를 두지 않는다. 음수 IC 는 음수로 쓰고, 표본이
모자라면 모자라다고 쓴다. 이 채널의 유일한 차별화가 그것이다.

매수·매도·목표가 표현을 쓰지 않는다 — 점수와 순위, 사후 채점만 발행한다.
"""

DISCLAIMER = (
    "이 채널은 예측 기록과 사후 채점을 공개합니다. 투자 자문이나 매매 "
    "권유가 아니며, 투자 판단과 그 결과는 본인에게 귀속됩니다."
)

# 유효 표본이 이보다 적으면 평균 IC 를 판정 근거로 말하지 않는다.
# 겹치는 선행 구간 때문에 겉보기 n 은 쉽게 커지지만 effective_n 은 안 커진다.
MIN_EFFECTIVE_N = 10

SLUG_NAMES = {
    "chart": "차트+파동+모멘텀",
    "quant": "퀀트+재무",
    "ict":   "ICT+CRT",
}

MISSING_REASON = {"quant": "일별 펀더멘털 수집 미구축"}


def _slug_name(slug):
    return SLUG_NAMES.get(slug, slug)


def build_scorecard_message(horizon, stats, missing_slugs):
    """N일 지평 성적표. stats 는 score_analysts() 의 반환값."""
    lines = [f"📊 {horizon}일 지평 성적표", ""]

    for slug in sorted(stats):
        s = stats[slug]
        effective_n = float(s.get("effective_n") or 0)
        lines.append(f"*{_slug_name(slug)}*")
        lines.append(f"  평균 IC {s['mean_ic']:+.4f} · 적중률 {s['hit_rate']:.1f}%")

        if effective_n < MIN_EFFECTIVE_N:
            lines.append(
                f"  판정 표본 n={s['n']} (유효 {effective_n:.1f}) — 통계적 판단 불가")
        else:
            lines.append(f"  t={s['t_stat']} · 유효표본 {effective_n:.1f}")
        lines.append("")

    for slug in missing_slugs:
        reason = MISSING_REASON.get(slug, "기록 없음")
        lines.append(f"※ {_slug_name(slug)}는 아직 기록하지 않음 — {reason}")

    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_record_message(date_str, regime, top_by_slug):
    """오늘의 예측 기록. top_by_slug 는 {slug: [(ticker, score), ...]}."""
    lines = [f"🧬 {date_str} 예측 기록 (국면: {regime})", ""]

    for slug in sorted(top_by_slug):
        entries = top_by_slug[slug]
        if not entries:
            continue
        lines.append(f"*{_slug_name(slug)}* 상위 {len(entries)}")
        for ticker, score in entries:
            lines.append(f"  {ticker} {score:.1f}")
        lines.append("")

    lines.append("이 기록은 5·21·63일 뒤 채점됩니다.")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_scorecard_message.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋한다**

```bash
git add modules/scorecard_message.py tests/test_scorecard_message.py
git commit -m "feat: 발행 메시지 조립기 — 면책 고정, 표본 부족 명시"
```

---

### Task 4: 발행 진입점과 워크플로

**Files:**
- Create: `scorecard_worker.py`
- Create: `.github/workflows/scorecard-publish.yml`
- Test: `tests/test_scorecard_worker.py`

**Interfaces:**
- Consumes: `analyst_log.load_days() -> [{"date", "regime", "scores"}]`
- Consumes: `price_panel.load_panel(tickers, start, end) -> (prices_dict, ohlcv_dict)` — `prices_dict` 는 `{ticker: Close Series}`
- Consumes: `analyst_scorecard.HORIZONS`, `.build_forward_returns(prices, dates, horizon)`, `.score_analysts(days, forward_returns, horizon)`
- Consumes: `publish_log.last_published_n()`, `.record_published()`
- Consumes: `scorecard_message.build_scorecard_message()`, `.build_record_message()`
- Produces: `scorecard_worker.new_horizons(stats_by_horizon: dict, root=...) -> list[int]`
- Produces: `scorecard_worker.top_by_slug(day: dict, limit: int) -> dict`
- Produces: `scorecard_worker.main() -> int`

- [ ] **Step 1: 실패 테스트를 쓴다**

`tests/test_scorecard_worker.py` 생성:

```python
"""발행 판별 — 같은 판정을 두 번 보내지 않고, 새 판정은 놓치지 않는다.

네트워크가 필요한 main() 은 여기서 테스트하지 않는다. 판별 로직만 순수
함수로 떼어내 고정한다.

scorecard_worker 는 함수 안에서 import 한다 — price_panel 이 최상단에서
yfinance 를 끌어온다. tests/test_analyst_log.py 의 패턴을 따른다.
"""
from modules import publish_log as pl


def _sw():
    import scorecard_worker
    return scorecard_worker


def test_first_ever_publish_is_new(tmp_path):
    stats = {5: {"chart": {"n": 1}}}

    assert _sw().new_horizons(stats, root=tmp_path) == [5]


def test_same_sample_is_not_republished(tmp_path):
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)
    stats = {5: {"chart": {"n": 1}}}

    assert _sw().new_horizons(stats, root=tmp_path) == []


def test_grown_sample_is_republished(tmp_path):
    pl.record_published("2026-07-30", 5, 1, root=tmp_path)
    stats = {5: {"chart": {"n": 4}}}

    assert _sw().new_horizons(stats, root=tmp_path) == [5]


def test_empty_stats_is_skipped(tmp_path):
    """채점된 날이 없으면 발행하지 않는다."""
    assert _sw().new_horizons({5: {}}, root=tmp_path) == []


def test_uses_largest_n_across_analysts(tmp_path):
    """애널리스트마다 채점된 날 수가 다를 수 있다 — 최대값으로 판단한다."""
    stats = {5: {"chart": {"n": 4}, "ict": {"n": 2}}}

    assert _sw().new_horizons(stats, root=tmp_path) == [5]


def test_top_by_slug_takes_highest_scores():
    day = {"scores": {
        "AAPL": {"chart": 73.8, "ict": 20.0},
        "MSFT": {"chart": 41.8},
        "NVDA": {"chart": 61.9, "ict": 90.0},
    }}

    top = _sw().top_by_slug(day, limit=2)

    assert top["chart"] == [("AAPL", 73.8), ("NVDA", 61.9)]
    assert top["ict"] == [("NVDA", 90.0), ("AAPL", 20.0)]


def test_top_by_slug_skips_absent_slug():
    """점수가 없는 종목은 그 슬러그 순위에 넣지 않는다."""
    day = {"scores": {"AAPL": {"chart": 73.8}, "MSFT": {"ict": 50.0}}}

    top = _sw().top_by_slug(day, limit=5)

    assert top["chart"] == [("AAPL", 73.8)]
    assert top["ict"] == [("MSFT", 50.0)]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_scorecard_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scorecard_worker'`

- [ ] **Step 3: 워커를 구현한다**

`scorecard_worker.py` 생성:

```python
"""성적표 공개 채널 발행기 — 채점 후 발행. 기록은 하지 않는다.

기록기(signal_worker --record-only)와 이 워커는 워크플로가 다르다. 기록이
발송에 묶여 있어서, 알파가 없어 매수 알림을 끄자 성적표 재료까지 같이
끊긴 것이 이 분리의 이유다.

성적을 파일에 쌓지 않는다. analyst_log 와 가격이 유일한 진실이고 성적은
그것의 함수다. 매번 전체를 다시 계산한다 — 기록이 하루 한 줄이라 비용이
무시할 수준이다. 저장하는 것은 발행 이력뿐이다.
"""
import os
import sys
from datetime import datetime, timedelta

import requests

from modules import (analyst_log, analyst_scorecard, price_panel,
                     publish_log, scorecard_message)

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHANNEL_ID = os.environ.get("TELEGRAM_PUBLIC_CHANNEL_ID", "")

# 선행수익률 계산에 필요한 과거 구간. 최장 지평(63봉) + 여유.
PANEL_DAYS = 400

# 발행 노출 종목 수. 채점은 전 종목으로 하고 노출만 줄인다.
TOP_N = 5

# 아직 기록하지 않는 슬러그 — 조용히 빼지 않고 발행문에 사유를 밝힌다.
MISSING_SLUGS = ["quant"]


def send_tg(msg):
    """공개 채널로 발송한다. 개인 채팅(TELEGRAM_CHAT_ID)과 섞지 않는다."""
    if not TG_TOKEN or not TG_CHANNEL_ID:
        print("[TG] 환경변수 없음 — 발송 생략")
        return False

    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHANNEL_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10,
    )
    if resp.status_code == 400 and "parse entities" in resp.text:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHANNEL_ID, "text": msg},
            timeout=10,
        )

    ok = resp.status_code == 200
    print("[TG] 발송 성공" if ok else f"[TG 오류] {resp.text}")
    return ok


def new_horizons(stats_by_horizon, root=publish_log.LOG_DIRNAME):
    """표본이 늘어난 지평만 돌려준다 — 같은 판정을 두 번 보내지 않는다."""
    out = []
    for horizon in sorted(stats_by_horizon):
        stats = stats_by_horizon[horizon]
        n = max((s.get("n", 0) for s in stats.values()), default=0)
        if n <= 0:
            continue
        last = publish_log.last_published_n(horizon, root=root)
        if last is None or n > last:
            out.append(horizon)
    return out


def top_by_slug(day, limit=TOP_N):
    """그날 기록에서 슬러그별 상위 종목 — [(ticker, score), ...]."""
    buckets = {}
    for ticker, per_analyst in day.get("scores", {}).items():
        for slug, score in per_analyst.items():
            buckets.setdefault(slug, []).append((ticker, float(score)))

    return {slug: sorted(rows, key=lambda r: r[1], reverse=True)[:limit]
            for slug, rows in buckets.items()}


def main():
    days = analyst_log.load_days()
    if not days:
        print("기록이 없다 — 발행할 것이 없다.", file=sys.stderr)
        return 1

    latest = days[-1]
    send_tg(scorecard_message.build_record_message(
        latest.get("date", ""), latest.get("regime", "unknown"),
        top_by_slug(latest)))

    tickers = sorted({t for d in days for t in d.get("scores", {})})
    end = datetime.now()
    try:
        prices, _ = price_panel.load_panel(
            tickers, end - timedelta(days=PANEL_DAYS), end)
    except Exception as e:
        print(f"가격 패널 로드 실패 — 채점 불가: {e}", file=sys.stderr)
        return 1

    dates = [d["date"] for d in days]
    stats_by_horizon = {}
    for horizon in analyst_scorecard.HORIZONS:
        fwd = analyst_scorecard.build_forward_returns(prices, dates, horizon)
        stats_by_horizon[horizon] = analyst_scorecard.score_analysts(
            days, fwd, horizon)

    today = datetime.now().strftime("%Y-%m-%d")
    for horizon in new_horizons(stats_by_horizon):
        stats = stats_by_horizon[horizon]
        if not send_tg(scorecard_message.build_scorecard_message(
                horizon, stats, MISSING_SLUGS)):
            print(f"{horizon}일 성적표 발송 실패", file=sys.stderr)
            return 1
        n = max(s.get("n", 0) for s in stats.values())
        publish_log.record_published(today, horizon, n)
        print(f"{horizon}일 성적표 발행 (n={n})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_scorecard_worker.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 발행 워크플로를 만든다**

`.github/workflows/scorecard-publish.yml` 생성:

```yaml
name: 성적표 공개 채널 발행

on:
  # 23:45 UTC = KST 08:45. daily-report(23:30 UTC)와 15분 간격을 둔다.
  # 기록기(22:30 UTC)가 끝난 뒤다.
  schedule:
    - cron: "45 23 * * 1-5"
  workflow_dispatch:

concurrency:
  group: scorecard-publish
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  publish:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: 저장소 체크아웃
        uses: actions/checkout@v5

      - name: Python 설정
        uses: actions/setup-python@v6
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: 의존성 설치
        run: pip install -r requirements.txt

      - name: 채점 후 공개 채널 발행
        env:
          PYTHONUTF8:                 "1"
          TELEGRAM_TOKEN:             ${{ secrets.TELEGRAM_TOKEN }}
          # 개인 P&L 채팅(TELEGRAM_CHAT_ID)과 분리된 공개 채널이다.
          TELEGRAM_PUBLIC_CHANNEL_ID: ${{ secrets.TELEGRAM_PUBLIC_CHANNEL_ID }}
        run: python scorecard_worker.py

      - name: 발행 이력 커밋 (변경 있을 때만)
        run: |
          git config user.email "actions@github.com"
          git config user.name "GitHub Actions"
          git add data/analyst_log
          if ! git diff --staged --quiet; then
            git commit -m "chore: 발행 이력 $(date -u +%Y-%m-%d)"
            git pull --rebase origin main
            git push
          else
            echo "발행 이력 변경 없음 — 커밋 생략"
          fi
```

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m pytest tests/ -q`
Expected: 기존 테스트 전부 통과 + 신규 22건 통과. 실패가 있으면 신규 코드가 기존 동작을 깬 것이므로 고친다.

- [ ] **Step 7: 커밋한다**

```bash
git add scorecard_worker.py tests/test_scorecard_worker.py .github/workflows/scorecard-publish.yml
git commit -m "feat: 성적표 발행기와 워크플로 — 채점 후 공개 채널 발행"
```

---

## 측정 노트는 코드가 없다

스펙 3절의 발행물 세 종류 중 **측정 노트에는 태스크가 없다.** 빠뜨린 것이
아니라 자동화할 것이 없기 때문이다. `docs/measurements/` 의 실측 기록을
사람이 읽고 풀어 쓴 글이므로, 텔레그램 앱에서 채널에 직접 올리면 된다.

첫 노트 재료는 이미 저장소에 있다:

- `docs/measurements/2026-07-23-factor-definition-comparison.md`
- 유니버스 37→276 확대 후 전 팩터 `|ICIR| < 0.1`, `mom_3m` 부호 반전
  (`.github/workflows/signal-alerts.yml` 주석에 경위가 남아 있다)

수동 발행에도 면책 문구를 붙인다. `modules/scorecard_message.py` 의
`DISCLAIMER` 상수를 복사해 쓴다.

## 배포 전 사람이 할 일

코드가 다 돌아도 이것 없이는 발행되지 않는다.

1. 텔레그램 공개 채널을 만들고 기존 봇을 **관리자**로 추가한다 (채널은 관리자만 발행할 수 있다).
2. 채널 ID 를 저장소 시크릿 `TELEGRAM_PUBLIC_CHANNEL_ID` 로 등록한다. 공개 채널은 `@채널명` 형태도 받는다.
3. `analyst-record.yml` 을 `workflow_dispatch` 로 한 번 수동 실행해 기록이 쌓이는지 확인한다.
4. `scorecard-publish.yml` 을 수동 실행해 채널에 메시지가 도착하는지 확인한다.

## 완료 판정

- 기록이 매 영업일 자동으로 쌓인다 (연속 10영업일 결손 0)
- 첫 5일 지평 채점 결과가 채널에 발행되고, 같은 판정이 다음날 다시 발행되지 않는다
- 발송 실패 시 워크플로가 실패로 끝난다 (조용히 넘어가지 않는다)
