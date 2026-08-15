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
