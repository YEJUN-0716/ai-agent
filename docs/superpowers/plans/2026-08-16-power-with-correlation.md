# 검출력을 실제 두 바구니로 계산한다 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 검출력 게이트(MDE)를 **위약 두 다리가 아니라 실제 두 다리** 위에서 계산하도록 자를 고치고, 지난 다섯 측정의 ② 판정을 재채점 문서로 남긴다.

**Architecture:** 새 통계 코드는 없다. `excess_cagr_ci`(`scripts/measure_pead.py:245`)의 블록 부트스트랩이 이미 두 줄을 **같은 날짜로 묶어** 재표본하므로 두 바구니의 상관은 처음부터 들어가고 있었다. 바뀌는 것은 **그 자에 무엇을 먹이는가** 뿐이다. 눈가림은 말이 아니라 반환값으로 강제한다 — 게이트를 내는 `mde_pp(strat, base)` 는 구간 반폭만 반환하고 점추정을 반환하지 않는다.

**Tech Stack:** Python 3.12+ · numpy · pandas · pytest · ruff

**Spec:** `docs/superpowers/specs/2026-08-16-power-with-correlation-design.md`

## Global Constraints

- **게이트는 10%p 그대로다.** 다섯 측정이 다 넘어도 안 올린다 (설계서 2절). 검사를 통과시키려고 기준을 옮기지 않는다.
- **`mde_pp` 는 점추정을 반환하지 않는다.** 반환값은 `(hi - lo) / 2.0` 하나. 사전 등록 스크립트가 결과를 볼 방법이 구조적으로 없어야 한다 (설계서 1절).
- **위약(셔플)을 지우지 않는다.** ②의 순열검정 p값은 위약 100개 위에서 계속 낸다. 위약 MDE 는 게이트에서 **참고 하한**으로 강등될 뿐 삭제 대상이 아니다 (설계서 5절).
- **①(IC) 쪽 검출력은 안 건드린다.** `date_block_t` · `block_t` · `IC_BLOCK` 은 한 글자도 안 바꾼다 (설계서 5절).
- **다섯 측정을 재실행하지 않는다.** 발행된 95% 구간을 2로 나눈다. 데이터 수급도 API 호출도 없다 (설계서 5절).
- **원 측정 문서의 숫자는 한 글자도 안 고친다.** 맨 위에 한 줄만 덧붙인다 (설계서 3절).
- 린트: `python -m ruff check .` (`select = ["F", "E9"]`, `line-length = 120`).
- 테스트: `python -m pytest tests/ -q`. **CI 는 `tests/` 만 돌린다** — 스크립트 안의 `selftest()` 는 CI 게이트가 아니다. 그래서 이번 자체검사는 `tests/` 에 넣는다.
- 저장소: `c:\Users\1aass\stock-analyzer` (이 계획의 모든 경로는 그 저장소 기준).
- main 에 직접 커밋 금지. 기능 브랜치 → PR → 머지.

## 설계서가 안 정했던 자리 — 사장님 결정 (2026-08-16)

`scripts/measure_fscore.py:431` 에 **이미 다른 `mde_pp`** 가 있다. 인자 하나짜리(`mde_pp(flat_ret)`, 매수보유 줄만)고, 이게 F-Score 측정의 **살아있는 게이트**(`underpowered`)다. 설계서 3절이 "9.29 는 매수보유 줄만으로 낸 값이었다"고 지목한 바로 그 함수인데 4절 표에는 안 적혀 있었다.

**결정: 같이 고친다.** 인자 하나짜리는 `mde_floor_pp` 로 개명해 **참고 하한**으로 남기고, 게이트는 새 `mde_pp(strat, base)` 실측값으로 간다. `mde_pp` 가 점추정을 반환하지 않으므로 실제 전략 줄을 먹여도 눈가림은 그대로 유지된다. → **Task 2**

`scripts/measure_pead.py` 와 `scripts/measure_quant_pit.py` 는 리포트에 MDE 한 줄만 찍고 **판정 로직은 안 건드린다** — 설계서 5절이 그 둘의 재실행을 금지했으므로 게이트를 하드하게 걸면 판정 문서와 코드가 어긋난다. 그 둘을 하드 게이트로 올리는 건 별건이다. → **Task 4**

`scripts/measure_fscore_longshort.py` 는 이미 실제 반폭을 `half` 로 계산해 리포트 289줄에 찍고 있다. **추가 작업 없음.**

---

## 파일 구조

| 파일 | 책임 | Task |
|---|---|---|
| `scripts/measure_pead.py` | 공유 통계 자의 집. `mde_pp` 와 `MDE_LIMIT_PP` 를 여기 하나만 둔다 | 1, 4 |
| `tests/test_mde_power.py` | (신규) 상관↑ → MDE↓ 부등호. 시장 데이터 없이 돈다 | 1 |
| `scripts/measure_fscore.py` | 살아있는 게이트를 실제 두 줄로. 옛 함수는 `mde_floor_pp` 로 개명 | 2 |
| `scripts/pilot_longshort_power.py` | 사전 등록 게이트. 실제 두 줄이 게이트, 위약은 참고 하한 | 3 |
| `scripts/measure_quant_pit.py` | 리포트에 MDE 한 줄 | 4 |
| `docs/measurements/2026-08-16-power-rescore.md` | (신규) 재채점 한 장 | 5 |
| `docs/measurements/*.md` (5개) | 맨 위 한 줄씩 | 5 |

---

### Task 1: 자를 한 곳에 둔다 — `mde_pp` + 상관 자체검사

이번 버그가 정확히 위반한 부등호를 먼저 테스트로 박고, 그 다음에 함수를 만든다.

**Files:**
- Create: `tests/test_mde_power.py`
- Modify: `scripts/measure_pead.py` (`excess_cagr_ci` 바로 아래에 `mde_pp` 추가 · 상수 블록에 `MDE_LIMIT_PP` 추가)

**Interfaces:**
- Consumes: `scripts.measure_pead.excess_cagr_ci(strat: np.ndarray, flat: np.ndarray) -> tuple[float, float, float]` — `(point, lo, hi)`
- Produces:
  - `scripts.measure_pead.mde_pp(strat: np.ndarray, base: np.ndarray) -> float` — 95% 구간 반폭만. **점추정 없음.**
  - `scripts.measure_pead.MDE_LIMIT_PP: float = 10.0` — Task 2·3 이 각자 정의하던 값을 여기서 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

Create `tests/test_mde_power.py`:

```python
"""검출력 자(`mde_pp`) — 상관이 올라가면 MDE 가 줄어야 한다.

2026-08-15 F-Score 롱숏 측정에서 검출력 게이트가 **3.5배 빗나갔다.** 사전 등록은
MDE 5.56%p 를 적었는데 실측 반폭은 19.73%p 였다. 자는 멀쩡했고 **입력이 틀렸다** —
게이트에 위약 두 다리를 넣었는데, 달 안에서 점수를 섞으면 고점수 바구니와 저점수
바구니가 같은 풀에서 뽑은 거의 같은 포트폴리오가 되어 상관이 0.956 까지 올라간다.
붙어 있는 두 줄의 차이는 구조적으로 얌전할 수밖에 없고, 그래서 MDE 가 작게 나왔다.

여기서 못 박는 것 둘:

1. **주변 분포가 같아도 상관이 다르면 MDE 가 다르다.** rho=0.95 쪽이 더 작아야 한다.
2. 그게 성립하려면 `excess_cagr_ci` 가 두 줄을 **같은 날짜로 묶어** 재표본해야
   한다. 날짜 짝이 풀리는 순간 상관이 계산에서 사라지고 이 검사가 깨진다.

설계서: `docs/superpowers/specs/2026-08-16-power-with-correlation-design.md` 4절.
시장 데이터를 안 탄다.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.measure_pead import mde_pp  # noqa: E402


def _pair(rho: float, n: int = 1200, sd: float = 0.01, seed: int = 20260816):
    """주변 분포(평균 0 · sd 고정)는 같고 **상관만 rho** 인 일별 수익 두 줄.

    씨앗이 같으므로 첫 줄은 rho 와 무관하게 완전히 같은 계열이다. 둘째 줄은
    분산이 1 로 유지되도록 촐레스키로 섞는다 — 그래서 두 설정의 차이가 상관
    하나로만 남는다.
    """
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.0, size=(2, n))
    a = z[0]
    b = rho * z[0] + np.sqrt(1.0 - rho ** 2) * z[1]
    return a * sd, b * sd


def test_higher_correlation_gives_smaller_mde():
    """rho=0.95(위약처럼 붙은 두 다리) < rho=0.85(실제처럼 갈라진 두 다리)."""
    tight = _pair(0.95)
    split = _pair(0.85)

    # 전제: 주변 분포는 같다. 이게 깨지면 아래 부등호는 상관이 아니라 sd 를 재는 것이다.
    assert np.allclose(tight[0], split[0]), "첫 줄이 두 설정에서 달라졌다"
    assert np.isclose(np.std(tight[1]), np.std(split[1]), rtol=0.05), "둘째 줄의 sd 가 달라졌다"

    assert mde_pp(*tight) < mde_pp(*split), (mde_pp(*tight), mde_pp(*split))


def test_breaking_the_date_pairing_inflates_mde():
    """날짜 짝을 풀면 상관이 계산에서 사라지고 MDE 가 커진다.

    `excess_cagr_ci` 가 두 줄을 같은 인덱스로 재표본한다는 주장이 코드로 확인되는
    자리다. 짝을 맞추는 줄이 사라지면 rho=0.95 짜리도 rho=0 처럼 거칠어진다.
    """
    a, b = _pair(0.95)
    scrambled = np.random.default_rng(7).permutation(b)
    assert mde_pp(a, b) < mde_pp(a, scrambled), (mde_pp(a, b), mde_pp(a, scrambled))


def test_mde_returns_only_a_width():
    """**점추정을 반환하지 않는다** — 눈가림을 말이 아니라 반환값으로 강제한다.

    부호를 통째로 뒤집어도(효과가 +에서 -로) 반환값은 거의 안 움직인다.
    사전 등록 단계에서 이 함수만 봐서는 효과의 방향을 알 수 없다는 뜻이다.
    """
    a, b = _pair(0.85)
    assert isinstance(mde_pp(a, b), float)
    assert np.isclose(mde_pp(a + 0.002, b), mde_pp(b, a + 0.002), rtol=0.15)
```

- [ ] **Step 2: 실패하는 걸 확인한다**

Run: `python -m pytest tests/test_mde_power.py -q`
Expected: FAIL — `ImportError: cannot import name 'mde_pp' from 'scripts.measure_pead'`

- [ ] **Step 3: `mde_pp` 를 `excess_cagr_ci` 바로 아래에 넣는다**

`scripts/measure_pead.py:251` 의 `return point, float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))` 다음 빈 줄 뒤, `# ---- 자체검사` 주석 앞에 삽입:

```python
def mde_pp(strat: np.ndarray, base: np.ndarray) -> float:
    """② 추정량의 95% 반폭 = 이 설계가 잴 수 있는 최소 효과(연 %p).

    **점추정을 반환하지 않는다.** 게이트를 내려면 이 함수를 불러야 하는데 반환값에
    효과의 크기도 부호도 없으므로, 사전 등록 단계에서 결과를 볼 방법이 구조적으로
    없다 — 임상시험의 눈가림 표본수 재계산(blinded sample size re-estimation)과
    같은 장치다. 방해 모수(분산)만 실측으로 채우고 효과는 가린다.

    **실제 두 줄을 넣는다.** 위약 두 다리를 넣으면 안 된다 — 달 안에서 점수를
    섞으면 두 바구니가 거의 같은 포트폴리오가 되어 상관이 올라가고, 그만큼 MDE 가
    작게 나온다. 두 다리를 같은 풀에서 뽑는 설계에서 위약 MDE 는 **구조적 하한**
    이지 게이트가 아니다 (설계서 0절).

    **완전한 눈가림은 아니다.** CAGR 이 `expm1` 을 거치는 비선형 변환이라 구간의
    폭이 수준에 아주 약하게 딸려온다. 지금까지 관측된 +-20%p 범위에서는 무시할
    수준이고, **알고 쓴다** (설계서 1절 마지막 문단).
    """
    _, lo, hi = excess_cagr_ci(strat, base)
    return (hi - lo) / 2.0
```

- [ ] **Step 4: 게이트 상수를 여기 하나만 둔다**

`scripts/measure_pead.py:72` 의 `SEED       = 20260813` 다음 줄에 삽입:

```python
MDE_LIMIT_PP = 10.0    # 검출력 게이트(연 %p). measure_fscore·pilot 이 각자 갖고 있던 값을
                       # 여기 하나로 모은다 — 자와 게이트가 같은 파일에 있어야 한다.
                       # 설계서 2절: 지난 다섯 측정이 다 넘어도 이 값을 안 올린다.
```

- [ ] **Step 5: 통과하는 걸 확인한다**

Run: `python -m pytest tests/test_mde_power.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: 기존 것이 안 깨졌는지 본다**

Run: `python -m pytest tests/ -q && python -m ruff check .`
Expected: 전부 PASS · ruff 무경고

- [ ] **Step 7: 커밋**

```bash
git checkout -b feat/power-with-correlation-impl
git add scripts/measure_pead.py tests/test_mde_power.py
git commit -m "feat: mde_pp — 실제 두 줄로 재는 검출력 자, 점추정은 반환 안 한다"
```

---

### Task 2: F-Score 의 살아있는 게이트를 실제 두 줄로

`measure_fscore.mde_pp(flat_ret)` 는 매수보유 줄만으로 낸 값이라 F-Score 대형주에서 9.29 를 찍고 게이트를 통과시켰다. 실제는 11.94 였다. 개명해서 참고 하한으로 남기고, 게이트는 Task 1 의 `mde_pp` 로 간다.

**Files:**
- Modify: `scripts/measure_fscore.py:72` (import), `:127` (상수 삭제), `:431-442` (개명), `:551` (자체검사), `:634-636` (게이트), `:758-775` (리포트)

**Interfaces:**
- Consumes: `scripts.measure_pead.mde_pp(strat, base) -> float` · `scripts.measure_pead.MDE_LIMIT_PP: float` (Task 1)
- Produces: `scripts.measure_fscore.mde_floor_pp(flat_ret: np.ndarray) -> float` — 기준선 줄만으로 낸 **하한**. 게이트가 아니라 리포트의 참고 줄.

- [ ] **Step 1: 자체검사를 새 이름으로 먼저 바꾼다 (실패 확인용)**

`scripts/measure_fscore.py:548-551` 을 이렇게 바꾼다:

```python
    # 8) MDE 하한은 길이·변동성이 커질수록 커진다. 방향만 확인한다.
    rng = np.random.default_rng(0)
    quiet = rng.normal(0, 0.005, 1000)
    loud = rng.normal(0, 0.020, 1000)
    assert mde_floor_pp(loud) > mde_floor_pp(quiet) > 0

    # 8b) **게이트는 하한이 아니라 실측이다.** 갈라진 두 줄(rho 낮음)의 MDE 가
    #     기준선 줄만으로 낸 하한보다 커야 한다 — 2026-08-15 롱숏에서 이 부등호를
    #     반대로 알고 있었다가 게이트가 3.5배 빗나갔다.
    strat = 0.5 * quiet + np.sqrt(1 - 0.5 ** 2) * rng.normal(0, 0.005, 1000)
    assert mde_pp(strat, quiet) > mde_floor_pp(quiet)
```

- [ ] **Step 2: 실패하는 걸 확인한다**

Run: `python scripts/measure_fscore.py selftest`
Expected: FAIL with `NameError: name 'mde_floor_pp' is not defined`

- [ ] **Step 3: 함수를 개명하고 하한이라는 걸 이름에 박는다**

`scripts/measure_fscore.py:431-442` 를 통째로 바꾼다:

```python
def mde_floor_pp(flat_ret: np.ndarray) -> float:
    """MDE 의 **하한**(연 %p) — 기준선 줄만으로 낸다. **게이트가 아니다.**

    초과 계열의 변동성 자리에 기준선 자신의 변동성을 넣은 값이다. 전략과 기준선이
    완전히 붙어 있을 때만 이 값이 실제 MDE 와 같고, 갈라질수록 실제가 더 크다.
    그래서 이건 "이것만으로 이미 한계를 넘으면 판정할 힘이 없는 게 확실하다"는
    방향으로만 읽을 수 있다.

    2026-08-14 F-Score 대형주 측정이 이 값(9.29)으로 게이트를 통과시켰는데 실제
    반폭은 11.94 였다. 그래서 **게이트는 `measure_pead.mde_pp` 로 옮겼고** 이
    함수는 리포트의 참고 줄로만 남는다 (설계서 3절).
    """
    idx = _block_idx(len(flat_ret), np.random.default_rng(SEED), BLOCK)
    boot = np.expm1(np.log1p(flat_ret)[idx].mean(axis=1) * 252) * 100
    return 1.96 * float(boot.std())
```

- [ ] **Step 4: import 를 고치고 중복 상수를 지운다**

`scripts/measure_fscore.py:72` 의 import 목록에 `mde_pp` 와 `MDE_LIMIT_PP` 를 추가한다. 바뀐 줄:

```python
    N_BOOT, _block_idx, attach_trades, calendar_curve, excess_cagr_ci,
    MDE_LIMIT_PP, mde_pp,
```

(같은 `from scripts.measure_pead import (...)` 괄호 안이다. 나머지 이름은 그대로 둔다.)

그리고 `scripts/measure_pead.py` 로 옮겨간 중복 정의를 지운다 — `scripts/measure_fscore.py:127` 의 이 줄을 **삭제**:

```python
MDE_LIMIT_PP = 10.0    # 연 %p
```

- [ ] **Step 5: 게이트에 실제 두 줄을 먹인다**

`scripts/measure_fscore.py:634-636` 을 바꾼다. before:

```python
    # **MDE 를 전략 결과보다 먼저 낸다** (설계서 3.2). 매수보유 줄만으로 나온다.
    mde = mde_pp(flat_ret)
    underpowered = mde > MDE_LIMIT_PP or avg_pos < MIN_AVG_POSITIONS
```

after:

```python
    # **MDE 를 점추정보다 먼저 낸다.** 실제 두 줄로 재되 `mde_pp` 가 반폭만
    # 반환하므로 여기서 효과의 크기도 부호도 알 수 없다 — 판정을 보고 게이트를
    # 고를 방법이 구조적으로 없다 (2026-08-16 설계서 1절). 아래 `excess_cagr_ci`
    # 호출은 이 두 줄 **다음에** 온다.
    mde = mde_pp(port.values, flat_ret)
    mde_floor = mde_floor_pp(flat_ret)      # 참고 — 기준선 줄만으로 낸 하한
    underpowered = mde > MDE_LIMIT_PP or avg_pos < MIN_AVG_POSITIONS
```

- [ ] **Step 6: 리포트의 검출력 절을 실제/하한 두 줄로 바꾼다**

`scripts/measure_fscore.py:758-772` 의 검출력 절을 바꾼다. before:

```python
        "## 검출력 — 전략을 보기 전에 낸 값 (설계서 3.2)",
        "",
        "| | 값 | 한계 | |",
        "|---|---|---|---|",
        f"| MDE (연 %p, 하한) | {mde:.2f} | <= {MDE_LIMIT_PP:.0f} | "
        f"{'X' if mde > MDE_LIMIT_PP else 'O'} |",
```

after:

```python
        "## 검출력 — 판정을 보기 전에 낸 값 (2026-08-16 설계서 1절)",
        "",
        "| | 값 | 한계 | |",
        "|---|---|---|---|",
        f"| **MDE (연 %p, 실제 두 줄)** | **{mde:.2f}** | <= {MDE_LIMIT_PP:.0f} | "
        f"{'X' if mde > MDE_LIMIT_PP else 'O'} |",
        f"| MDE 하한 (기준선 줄만) | {mde_floor:.2f} | 참고 | |",
```

이어지는 산문(`:769-771`)도 바꾼다. before:

```python
        "MDE 는 **매수보유 줄만으로** 낸다 — 초과 연수익 추정량의 부트스트랩 표준오차 × 1.96 이고,",
        "초과 계열의 변동성 자리에 기준선 자신의 변동성을 넣었으므로 **실제 MDE 의 하한**이다.",
        "quant_pit ②는 95% 구간이 20%p 폭이라 결과를 보기 전에 이미 결론이 정해져 있었다.",
        "이 표를 먼저 내는 건 그 자를 또 쓰지 않기 위해서다."
```

after:

```python
        "MDE 는 **실제 두 줄의 초과 연수익 95% 구간 반폭**이다. `mde_pp` 는 반폭만 반환하고",
        "점추정을 반환하지 않으므로, 이 값을 내는 동안 효과의 크기도 부호도 안 보인다.",
        "둘째 줄(기준선 줄만으로 낸 하한)은 2026-08-16 이전에 게이트로 쓰던 값이다 — 첫 판에서는",
        "9.29 를 찍어 게이트를 통과시켰는데 실제 반폭은 11.94 였다. 하한을 게이트로 쓰면",
        "**힘이 없는 측정이 통과한다**는 게 그때 드러났다 (설계서 3절)."
```

- [ ] **Step 7: 자체검사와 린트를 돌린다**

Run: `python scripts/measure_fscore.py selftest && python -m ruff check . && python -m pytest tests/ -q`
Expected: `selftest OK` (또는 이 파일의 성공 출력) · ruff 무경고 · pytest 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add scripts/measure_fscore.py
git commit -m "fix: F-Score 검출력 게이트를 실제 두 줄로 — 하한은 mde_floor_pp 로 강등"
```

---

### Task 3: 사전 등록 게이트(pilot)를 실제 두 줄로

이 파일이 게이트를 5.56%p 로 냈고 실측은 19.73%p 였다. 셔플 줄은 지우지 않고 **참고 하한**으로 강등한다.

**Files:**
- Modify: `scripts/pilot_longshort_power.py` (모듈 docstring · import · `mde_pp` 삭제 · `variants` · `main`)

**Interfaces:**
- Consumes: `scripts.measure_pead.mde_pp` · `scripts.measure_pead.MDE_LIMIT_PP` (Task 1)
- Produces: 없음 (실행 스크립트)

- [ ] **Step 1: 모듈 docstring 의 거짓말을 고친다**

`scripts/pilot_longshort_power.py:10-18` 의 "## 신호를 안 본다" 절을 통째로 바꾼다. before:

```
## 신호를 안 본다

F-Score 를 **같은 달 안에서 종목끼리 섞은 뒤**(위약) 스프레드 곡선을 만든다.
섞으면 종목↔점수 연결이 끊기므로 남는 건 **바구니 구성이 만드는 변동성**뿐이다.
그 변동성이 MDE 를 정한다. 진짜 점수로는 한 번도 안 돌린다 — 이 파일에는 실제
F-Score 로 만든 수익률을 찍는 줄이 없다.

대형주 때 "매수보유 줄만으로 MDE 를 낸다"와 같은 종류의 장치다. 결과를 보기 전에
낼 수 있어야 게이트로 쓸 수 있다.
```

after:

```
## 실제 두 바구니로 재되, 결과는 안 본다 (2026-08-16 설계서 1절)

**첫 판은 위약 두 다리로 게이트를 냈고 3.5배 빗나갔다** (5.56%p → 실측 19.73%p).
달 안에서 점수를 섞으면 고점수 바구니와 저점수 바구니가 같은 풀에서 뽑은 거의 같은
포트폴리오가 된다. 상관이 0.956 까지 올라가고(실제는 0.849), 붙어 있는 두 줄의
차이는 구조적으로 얌전할 수밖에 없다. **두 다리를 같은 풀에서 뽑는 설계에서 위약
MDE 는 게이트가 아니라 구조적 하한이다.**

그래서 게이트는 **진짜 F-Score 로 만든 두 바구니**의 일별 수익 두 줄로 낸다. 그
두 줄에서 읽는 것은 `mde_pp` 가 반환하는 **구간의 폭뿐**이고, 점추정·누적수익·부호는
읽지 않는다 — 함수가 아예 반환하지 않는다. 임상시험의 눈가림 표본수 재계산과 같은
장치다: 효과는 가린 채 방해 모수(분산)만 실측으로 채운다.

위약 줄은 지우지 않는다. **참고 하한**으로 같이 찍어, 게이트가 하한보다 얼마나
위에 있는지 보이게 한다.
```

- [ ] **Step 2: import 를 고치고 자기 `mde_pp` 를 지운다**

`scripts/pilot_longshort_power.py:47` 을 바꾼다. before:

```python
from scripts.measure_pead import attach_trades, calendar_curve  # noqa: E402
```

after:

```python
from scripts.measure_pead import (  # noqa: E402
    MDE_LIMIT_PP, attach_trades, calendar_curve, mde_pp,
)
```

`:50-58` 의 로컬 상수·함수를 **삭제**하고 `SCORE_LOW` · `N_SHUFFLE` 만 남긴다. before:

```python
SCORE_LOW = 3          # 저점수 바구니. Piotroski 의 저분위 구간이지 고른 값이 아니다.
MDE_LIMIT_PP = 10.0    # 게이트. 롱온리 측정과 **같은 값**이다.
N_SHUFFLE = 20


def mde_pp(strat: np.ndarray, base: np.ndarray) -> float:
    """초과 연수익 추정량의 부트스트랩 95% 폭 ÷ 2 = 1.96 × 표준오차."""
    _, lo, hi = excess_cagr_ci(strat, base)
    return (hi - lo) / 2.0
```

after:

```python
SCORE_LOW = 3          # 저점수 바구니. Piotroski 의 저분위 구간이지 고른 값이 아니다.
N_SHUFFLE = 20         # 위약은 게이트가 아니라 **참고 하한**을 내는 데만 쓴다.
```

`excess_cagr_ci` 는 이제 이 파일에서 안 쓰이므로 `:43-46` 의 `measure_fscore` import 목록에서 **삭제**한다 (안 지우면 ruff F401). 바뀐 블록:

```python
from scripts.measure_fscore import (  # noqa: E402
    BM_TOP, END, HOLD_DAYS, MIN_HELD, SCORE_AT, START, attach_bm,
    shuffle_scores, smallcap_events, smallcap_members,
)
```

- [ ] **Step 3: 설계별 두 다리를 한 곳에서 만든다**

`:61-80` 의 `variants` 를 아래 두 함수로 갈아끼운다. 실제와 위약이 **정확히 같은 코드 경로**를 타야 비교가 성립하므로, 다리를 만드는 함수를 하나로 두고 실제 `ev` 와 셔플된 `ev` 를 각각 먹인다.

```python
def designs(ev, close, bench_ret) -> dict:
    """설계별 (전략 줄, 기준선 줄, 얇은 쪽 거래 수). 실제/위약 모두 이 함수를 탄다.

    실제 점수를 넣으면 실제 두 다리, `shuffle_scores` 를 거친 걸 넣으면 위약 두
    다리가 나온다. **같은 코드 경로**여야 두 MDE 를 견줄 수 있다.
    """
    def curve(sub):
        return calendar_curve(sub.reset_index(drop=True), close, 0.0, MIN_HELD)

    hi_f = ev.loc[ev["fscore"] >= SCORE_AT]
    lo_f = ev.loc[ev["fscore"] <= SCORE_LOW]
    bm_hi = ev.loc[ev["bm_pct"] >= BM_TOP]
    bh = bm_hi.loc[bm_hi["fscore"] >= SCORE_AT]
    bl = bm_hi.loc[bm_hi["fscore"] <= SCORE_LOW]
    l0 = hi_f.loc[hi_f["bm_pct"] >= BM_TOP]

    l0_ret, l0_exp = curve(l0)
    return {
        "L0 롱온리 (고BM ∩ F>=7 vs 매수보유)": (l0_ret.values, bench_ret * l0_exp, len(l0)),
        f"A 전 유니버스 (F>={SCORE_AT} − F<={SCORE_LOW})":
            (curve(hi_f)[0].values, curve(lo_f)[0].values, min(len(hi_f), len(lo_f))),
        f"B 고BM 안에서 (F>={SCORE_AT} − F<={SCORE_LOW})":
            (curve(bh)[0].values, curve(bl)[0].values, min(len(bh), len(bl))),
    }


def variants(ev, close, n_shuffle: int) -> dict:
    """문턱 조합별 (실제 MDE, 위약 중앙값, 얇은 쪽 거래 수).

    문턱을 검출력으로 고르는 건 튜닝이 아니다 — `mde_pp` 는 점추정을 반환하지
    않으므로, 고르는 기준에 **수익률과 점수의 관계가 한 번도 안 들어간다.**
    """
    out = {}
    for hi_th, lo_th in ((SCORE_AT, SCORE_LOW), (6, 4)):
        def spread(frame, hi_th=hi_th, lo_th=lo_th):
            hi = frame.loc[frame["fscore"] >= hi_th].reset_index(drop=True)
            lo = frame.loc[frame["fscore"] <= lo_th].reset_index(drop=True)
            return (calendar_curve(hi, close, 0.0, MIN_HELD)[0].values,
                    calendar_curve(lo, close, 0.0, MIN_HELD)[0].values,
                    min(len(hi), len(lo)))

        s, b, size = spread(ev)
        plc = [mde_pp(*spread(shuffle_scores(ev, seed=20260815 + i))[:2])
               for i in range(n_shuffle)]
        out[f"F>={hi_th} − F<={lo_th}"] = (mde_pp(s, b), float(np.median(plc)), size)
    return out
```

- [ ] **Step 4: `main` 의 게이트를 실제 두 줄로 바꾼다**

`:100-142` 의 루프와 표 출력을 바꾼다. before는 `rows`/`sizes` 를 셔플 루프로 채우고 중앙값으로 게이트를 냈다. after:

```python
    real = designs(ev, close, bench_ret)
    floor = {k: [] for k in real}
    for i in range(n_shuffle):
        sh = shuffle_scores(ev, seed=20260815 + i)      # ← 참고 하한용. 게이트 아님.
        for k, (s, b, _) in designs(sh, close, bench_ret).items():
            floor[k].append(mde_pp(s, b))
        if (i + 1) % 5 == 0:
            print(f"  위약 {i + 1}/{n_shuffle}")

    print("\n| 설계 | **MDE (실제 두 줄)** | 위약 중앙값 (참고 하한) | 얇은 쪽 거래 수 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (s, b, size) in real.items():
        m = mde_pp(s, b)
        print(f"| {k} | **{m:.2f}** | {np.median(floor[k]):.2f} | {size} | "
              f"{'O' if m <= MDE_LIMIT_PP else 'X'} |")
    print(f"\n게이트 {MDE_LIMIT_PP:.0f}%p · 위약 셔플 {n_shuffle}회 · 보유 {HOLD_DAYS}일 · 비용 0bp.")
    print("**게이트는 실제 두 바구니로 낸다.** `mde_pp` 는 구간 반폭만 반환하므로 "
          "이 파일은 점추정·부호·누적수익을 한 번도 보지 않는다 (설계서 1절).")
    print("위약 중앙값은 **구조적 하한**이다 — 섞으면 두 바구니가 거의 같은 포트폴리오가 "
          "되어 상관이 올라간다. 게이트로 쓰면 안 된다 (설계서 0절).")
```

- [ ] **Step 5: 남은 두 절의 `variants` 호출과 표를 고친다**

`:144-163` 의 두 블록에서 `variants(..., None, n_shuffle)` → `variants(..., n_shuffle)` 로 인자를 줄이고, 표를 3열로 바꾼다. 전구간 블록:

```python
    print("\n| 설계 (전 유니버스 스프레드) | **MDE (실제)** | 위약 중앙값 | 얇은 쪽 | 게이트 |")
    print("|---|---|---|---|---|")
    for k, (real_mde, plc_med, sz) in variants(ev_f, close_f, n_shuffle).items():
        print(f"| {k} | **{real_mde:.2f}** | {plc_med:.2f} | {sz} | "
              f"{'O' if real_mde <= MDE_LIMIT_PP else 'X'} |")
```

본구간 블록도 `variants(ev, close, n_shuffle)` 로 같은 모양으로 바꾼다.

- [ ] **Step 6: import 가 되는지와 린트를 확인한다**

이 스크립트는 시장 데이터를 타므로 여기서 끝까지 돌리지 않는다. 배선만 본다:

Run: `python -c "import scripts.pilot_longshort_power" && python -m ruff check . && python -m pytest tests/ -q`
Expected: 무출력(import 성공) · ruff 무경고 · pytest 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add scripts/pilot_longshort_power.py
git commit -m "fix: 사전 등록 게이트를 실제 두 바구니로 — 위약은 참고 하한으로 강등"
```

---

### Task 4: 남은 두 리포트에 MDE 한 줄

`measure_fscore_longshort` 는 이미 실제 반폭(`half`)을 찍고 있으므로 건드리지 않는다. **판정 로직은 안 바꾼다** — 설계서 5절이 이 둘의 재실행을 금지했으므로 게이트를 하드하게 걸면 이미 발행된 판정 문서와 코드가 어긋난다.

**Files:**
- Modify: `scripts/measure_pead.py:404-405` (판정 표에 한 줄), `scripts/measure_quant_pit.py:57` (import) · `:400-401` (판정 표에 한 줄)

**Interfaces:**
- Consumes: `scripts.measure_pead.MDE_LIMIT_PP` (Task 1). 두 스크립트 모두 `lo`, `hi` 가 이미 지역 변수로 있으므로 `mde_pp` 를 다시 부르지 않는다 — 같은 구간을 2,000회 더 재표본하는 건 낭비다.
- Produces: 없음

- [ ] **Step 1: `measure_pead` 판정 표에 검출력 줄을 넣는다**

`scripts/measure_pead.py:404-405` 의 ② 줄 **바로 다음**에 삽입:

```python
        f"| ② 검출력 | 이 설계가 잴 수 있는 최소 효과 (구간 반폭) | "
        f"참고 — 게이트 {MDE_LIMIT_PP:.0f}%p | MDE {(hi - lo) / 2.0:.2f}%p | "
        f"{'O' if (hi - lo) / 2.0 <= MDE_LIMIT_PP else 'X'} |",
```

그리고 그 아래 `"**하나만 통과하면 실패다.** ..."` 두 줄 다음에 한 줄 덧붙인다:

```python
        "",
        "> **검출력 줄은 판정에 안 들어간다** (2026-08-16 설계서 5절 — 이 측정은 재실행하지",
        "> 않는다). 게이트를 넘으면 ②는 \"실패\"가 아니라 **\"미측정\"** 으로 읽는다.",
```

- [ ] **Step 2: `measure_quant_pit` 도 같게 한다**

`scripts/measure_quant_pit.py:57` 의 import 에 `MDE_LIMIT_PP` 를 추가한다:

```python
from scripts.measure_pead import MDE_LIMIT_PP, _block_idx, excess_cagr_ci  # noqa: E402
```

`:400-401` 의 ② 줄 **바로 다음**에 삽입:

```python
        f"| ② 검출력 | 이 설계가 잴 수 있는 최소 효과 (구간 반폭) | "
        f"참고 — 게이트 {MDE_LIMIT_PP:.0f}%p | MDE {(hi - lo) / 2.0:.2f}%p | "
        f"{'O' if (hi - lo) / 2.0 <= MDE_LIMIT_PP else 'X'} |",
```

그리고 `"**하나만 통과하면 실패다.** ①만 보고 이 저장소는 다섯 번 속았다."` 다음에:

```python
        "",
        "> **검출력 줄은 판정에 안 들어간다** (2026-08-16 설계서 5절). 게이트를 넘으면 ②는",
        "> \"실패\"가 아니라 **\"미측정\"** 으로 읽는다.",
```

- [ ] **Step 3: 배선과 린트를 확인한다**

Run: `python -c "import scripts.measure_pead, scripts.measure_quant_pit" && python -m ruff check . && python -m pytest tests/ -q`
Expected: 무출력 · ruff 무경고 · pytest 전부 PASS

- [ ] **Step 4: 커밋**

```bash
git add scripts/measure_pead.py scripts/measure_quant_pit.py
git commit -m "feat: PEAD·quant-pit 리포트에 MDE 한 줄 (참고, 판정 아님)"
```

---

### Task 5: 지난 다섯 측정을 재채점한다 (문서만)

**코드도 실행도 없다.** 이미 발행된 95% 구간을 2로 나눈다.

**Files:**
- Create: `docs/measurements/2026-08-16-power-rescore.md`
- Modify: `docs/measurements/2026-08-13-pead.md` · `2026-08-14-fscore.md` · `2026-08-14-quant-pit.md` · `2026-08-14-fscore-smallcap.md` · `2026-08-15-fscore-longshort.md` (각 H1 바로 아래 한 줄)

- [ ] **Step 1: 재채점 문서를 쓴다**

Create `docs/measurements/2026-08-16-power-rescore.md`:

```markdown
# 지난 다섯 측정의 ② 재채점 — 전부 "미측정"이었다

2026-08-16. 설계서: `docs/superpowers/specs/2026-08-16-power-with-correlation-design.md`.
**재실행도 데이터 수급도 없다** — 이미 발행된 ② 95% 구간을 2로 나눈 것뿐이다.
원 문서의 숫자는 한 글자도 안 고쳤다.

## 왜 나눗셈만으로 되나

②의 통과선은 초과 연수익 추정량의 부트스트랩 95% 하한 > 0 이다. 그 추정량이 잴 수
있는 최소 효과(MDE)는 곧 **구간의 반폭**이다. 각 측정이 이미 구간을 발행했으므로
MDE 는 그 자리에 있었고, 아무도 안 봤을 뿐이다.

## 표

| 측정 | ② 95% 구간 | 실제 MDE | 사전 등록이 주장한 MDE | 게이트 10%p |
|---|---|---|---|---|
| PEAD (`2026-08-13-pead.md`) | [-11.10, +11.78] | **11.44** | — | X |
| F-Score 대형주 (`2026-08-14-fscore.md`) | [-21.08, +2.80] | **11.94** | 9.29 (O 였다) | X |
| quant-pit (`2026-08-14-quant-pit.md`) | [-19.79, +11.71] | **15.75** | — | X |
| F-Score 소형주 (`2026-08-14-fscore-smallcap.md`) | [-8.18, +24.60] | **16.39** | 18.67 | X |
| F-Score 롱숏 (`2026-08-15-fscore-longshort.md`) | [-35.62, +3.85] | **19.74** | 5.56 | X |

**다섯 건 전부 게이트를 넘는다. ②로 "실패"를 선언할 자격이 있었던 측정은 하나도
없다** — 전부 **"미측정"** 이 맞다.

## 읽는 법 — 세 가지를 구별한다

1. **①은 그대로 산다.** F-Score 대형주의 IC t=-4.04 처럼 단면 검정으로 선 결론은
   건드리지 않는다. 이번 재채점은 ②만 건드린다.
2. **순열검정(②의 p값)도 그대로 산다.** 위약을 **게이트로** 쓰지 말자는 것이지
   위약을 버리자는 게 아니다.
3. **"미측정"은 "신호가 있다"가 아니다.** 이 설계로는 ②를 잴 수 없었다는 뜻이다.
   그때 할 수 있는 건 셋뿐이다: 창을 늘린다(√T), 추정량을 바꾼다, 아니면 안 잰다.

## 사전 등록 MDE 가 왜 두 방향으로 틀렸나

| | 사전 등록 | 실제 | 왜 |
|---|---|---|---|
| F-Score 대형주 | 9.29 (**작게**) | 11.94 | 매수보유 줄만으로 냈다 — 전략 줄이 기준선보다 거칠어서 실제가 더 크다 |
| F-Score 소형주 | 18.67 (**크게**) | 16.39 | 기준선이 전체 지수라 위약이 두 줄을 붙여놓지 않았다 — 여기선 위약이 오히려 보수적이었다 |
| F-Score 롱숏 | 5.56 (**3.5배 작게**) | 19.74 | 두 다리를 **같은 풀에서** 뽑는 설계다. 섞으면 상관이 0.849 → 0.956 으로 올라가 스프레드가 얌전해진다 |

**"위약 MDE 를 쓰지 말라"가 아니다.** 맞는 문장은 **"두 다리를 같은 풀에서 뽑는
설계에서 위약 MDE 는 구조적 하한이다"** 이다.

## 게이트는 안 올린다

새 자로 재면 다섯 건이 다 넘지만 게이트는 **10%p 그대로다**(설계서 2절). 검사를
통과시키려고 기준을 옮기는 건 이 저장소가 이미 금지한 일이다. 이 문서는 게이트를
정당화하지 않는다 — 게이트에 **올바른 입력**을 넣는 것까지만 한다.

## 앞으로

`scripts/measure_pead.py` 의 `mde_pp(strat, base)` 하나가 게이트를 낸다. 반환값이
구간 반폭뿐이라 사전 등록 단계에서 결과를 볼 방법이 없다. `measure_fscore` ·
`measure_pead` · `measure_quant_pit` · `measure_fscore_longshort` 리포트에 MDE 가
한 줄씩 박히므로 **다음부터는 재채점이 필요 없다.**

> **주의.** 아래 다섯 문서에 덧붙인 한 줄은 손으로 쓴 것이고, 그 문서들은 각자
> 측정 스크립트가 통째로 덮어쓴다. **다시 돌리면 그 줄이 사라진다.** 대신 스크립트
> 리포트에 MDE 줄이 들어갔으므로, 재생성되면 그 자리에 같은 사실이 자동으로 찍힌다.
```

- [ ] **Step 2: 원 문서 다섯 장 맨 위에 한 줄씩 덧붙인다**

각 파일의 **H1 바로 다음 빈 줄 뒤**에 아래 블록을 넣는다. 파일마다 MDE 숫자만 다르다.

`docs/measurements/2026-08-13-pead.md` — H1 `# PEAD (실적 서프라이즈 드리프트) — 측정` 다음:

```markdown
> **이 문서의 ② 판정은 2026-08-16 재채점에서 "미측정"으로 바뀌었다.** 이 설계의 실제
> MDE 는 11.44%p(아래 ② 구간의 반폭)로 게이트 10%p 를 넘는다 — ②를 판정할 힘이
> 없었다. 아래 숫자는 한 글자도 안 고쳤다.
> 재채점: `docs/measurements/2026-08-16-power-rescore.md`.
```

`2026-08-14-fscore.md` — H1 `# F-Score (8항목 Piotroski) — 측정` 다음. **`11.44%p`** → **`11.94%p`**, 그리고 한 줄 더:

```markdown
> **이 문서의 ② 판정은 2026-08-16 재채점에서 "미측정"으로 바뀌었다.** 이 설계의 실제
> MDE 는 11.94%p(아래 ② 구간의 반폭)로 게이트 10%p 를 넘는다 — ②를 판정할 힘이
> 없었다. 이 문서가 적은 MDE 9.29 는 **매수보유 줄만으로** 낸 하한이었다.
> **①(IC t=-4.04)은 그대로 산다.** 아래 숫자는 한 글자도 안 고쳤다.
> 재채점: `docs/measurements/2026-08-16-power-rescore.md`.
```

`2026-08-14-quant-pit.md` — H1 `# 퀀트 팀 재료를 EDGAR 시점 데이터로 바꾸면 총괄 판정이 값을 하나 (2026-08-14)` 다음. MDE **15.75%p**:

```markdown
> **이 문서의 ② 판정은 2026-08-16 재채점에서 "미측정"으로 바뀌었다.** 이 설계의 실제
> MDE 는 15.75%p(아래 ② 구간의 반폭)로 게이트 10%p 를 넘는다 — ②를 판정할 힘이
> 없었다. 아래 숫자는 한 글자도 안 고쳤다.
> 재채점: `docs/measurements/2026-08-16-power-rescore.md`.
```

`2026-08-14-fscore-smallcap.md` — H1 `# F-Score 소형주 재측정 — 유니버스 하나만 바꿨다` 다음. MDE **16.39%p** (사전 등록 18.67 이 오히려 컸다):

```markdown
> **이 문서의 ② 판정은 2026-08-16 재채점에서도 "미측정"이다** (원 판정과 같다).
> 실제 MDE 는 16.39%p(아래 ② 구간의 반폭)로 게이트 10%p 를 넘는다. 이 문서가 적은
> MDE 18.67 은 위약 위에서 낸 값인데 **실제보다 컸다** — 기준선이 전체 지수라
> 위약이 두 줄을 붙여놓지 않았기 때문이다. 아래 숫자는 한 글자도 안 고쳤다.
> 재채점: `docs/measurements/2026-08-16-power-rescore.md`.
```

`2026-08-15-fscore-longshort.md` — H1 `# F-Score 롱숏 분위 스프레드 — 판정` 다음. MDE **19.74%p**:

```markdown
> **이 문서의 ② 판정은 2026-08-16 재채점에서 "미측정"으로 바뀌었다.** 실제 MDE 는
> 19.74%p(아래 ② 구간의 반폭)로 게이트 10%p 를 넘는다. 사전 등록이 적은 5.56 은
> **위약 두 다리** 위에서 낸 값이고, 두 다리를 같은 풀에서 뽑는 이 설계에서 위약
> MDE 는 구조적 하한이다(상관 0.956 vs 실제 0.849). 아래 숫자는 한 글자도 안 고쳤다.
> 재채점: `docs/measurements/2026-08-16-power-rescore.md`.
```

- [ ] **Step 3: 표의 산수를 눈으로 검산한다**

각 줄의 `(hi - lo) / 2` 가 표의 MDE 와 맞는지 확인한다:

Run:
```bash
python -c "
for name, lo, hi in [('PEAD',-11.10,11.78),('fscore',-21.08,2.80),('quant-pit',-19.79,11.71),('smallcap',-8.18,24.60),('longshort',-35.62,3.85)]:
    print(f'{name:12} {(hi-lo)/2:.2f}')
"
```
Expected:
```
PEAD         11.44
fscore       11.94
quant-pit    15.75
smallcap     16.39
longshort    19.74
```

- [ ] **Step 4: 커밋하고 PR 을 연다**

```bash
git add docs/measurements/
git commit -m "docs: 지난 다섯 측정 ② 재채점 — 전부 실패가 아니라 미측정"
git push -u origin feat/power-with-correlation-impl
gh pr create --title "검출력을 실제 두 바구니로 계산한다" --body "$(cat <<'EOF'
설계서 `docs/superpowers/specs/2026-08-16-power-with-correlation-design.md` 구현.

## 무엇을 고쳤나

새 통계 코드는 없다. `excess_cagr_ci` 의 블록 부트스트랩은 처음부터 두 줄을 같은
날짜로 묶어 재표본했으므로 두 바구니의 상관은 이미 들어가고 있었다. **틀린 건 그
자에 먹인 입력**이었다 — 게이트에 위약 두 다리가 들어갔다.

- `measure_pead.mde_pp(strat, base)` 하나가 게이트를 낸다. **점추정을 반환하지
  않는다** — 사전 등록 단계에서 결과를 볼 방법이 구조적으로 없다.
- `measure_fscore` 의 살아있는 게이트를 실제 두 줄로. 옛 인자 하나짜리는
  `mde_floor_pp` 로 개명해 참고 하한으로 남겼다 (9.29 를 찍고 통과시켰는데 실제는
  11.94 였다).
- `pilot_longshort_power` 의 게이트도 실제 두 줄로. 위약은 안 지우고 참고 하한으로.
- PEAD·quant-pit 리포트에 MDE 한 줄 (참고, 판정 로직 안 건드림).
- 지난 다섯 측정의 ② 를 재채점 — 재실행 없이 발행된 구간을 2로 나눴다. 전부 "실패"가
  아니라 **"미측정"**.

## 게이트는 10%p 그대로다

다섯 건이 다 넘지만 안 올린다. 검사를 통과시키려고 기준을 옮기지 않는다.

## 자체검사

`tests/test_mde_power.py` — 주변 분포가 같고 상관만 다른 두 쌍을 만들어
`mde_pp(rho=0.95) < mde_pp(rho=0.85)` 를 못 박는다. `excess_cagr_ci` 가 날짜 짝을
안 맞추게 되는 순간 깨진다. 시장 데이터를 안 탄다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## 자체 검토 (계획을 쓴 뒤 확인한 것)

**설계서 커버리지**

| 설계서 절 | Task |
|---|---|
| 0. 무엇이 틀렸나 | Task 1 테스트가 이 부등호를 박는다 |
| 1. 실제 두 줄, 평균은 안 본다 + 눈가림 | Task 1 (`mde_pp`) · Task 2 · Task 3 |
| 2. 게이트 10%p 그대로 | Global Constraints · Task 1 Step 4 (`MDE_LIMIT_PP` 한 곳) |
| 3. 지난 다섯 재채점 | Task 5 |
| 4. 바꾸는 코드 표 | `measure_pead` → T1·T4 · `pilot` → T3 · 리포트 4개 → T2·T4 (`longshort` 는 이미 있음) · `tests/` → T1 |
| 5. 안 하는 것 | Global Constraints 에 다섯 줄 그대로 |
| 6. 틀릴 수 있는 자리 | `mde_pp` docstring 마지막 문단 (`expm1` 비선형성) |

**설계서 4절 표를 벗어난 작업 하나:** `measure_fscore.mde_pp` 개명·게이트 교체(Task 2). 사장님이 2026-08-16 에 결정했고 이 문서 상단에 근거를 적었다.

**설계서 4절 표에서 뺀 작업 하나:** `measure_fscore_longshort` 리포트 MDE 줄. `:289` 에 **이미 있다**.
