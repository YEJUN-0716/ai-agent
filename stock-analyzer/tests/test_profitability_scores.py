"""_score_roe / _score_roa 단위 고정 테스트.

두 함수는 **소수**를 받는다 (yfinance returnOnEquity/returnOnAssets 와 같은
단위). 예전엔 abs(v)<=1 일 때만 100 을 곱해서, 100% 를 넘는 ROE 를 곱하지
않고 그대로 % 로 읽었다. 애플처럼 자사주를 많이 산 우량주가 ROE 1.5(150%)
로 들어오면 1.5% 취급되어 30 점 — 0.99 가 85 점인데 그보다 낮았다.

수익성은 펀더멘털 점수의 한 축이고 ROE 는 그 안에서 40% 다. 우량주일수록
점수가 깎이는 방향이라 조용히 틀리기 좋았다.

여기서 (1) 단조성 — 좋은 ROE 가 낮은 ROE 보다 높은 점수를 받는다 (2) 1.0
경계에서 튀지 않는다 (3) 저장소의 다른 ROE 소비자와 같은 단위를 쓴다 를
못 박는다. 네트워크를 타지 않는다.
"""
import numpy as np
import pytest

import app


# ── 1. 뒤집힘 회귀 — 이게 이 파일의 존재 이유 ──────────────────

def test_roe_above_one_hundred_percent_is_not_read_as_one_percent():
    """ROE 1.5 = 150%. 1.5% 가 아니다. 애플이 실제로 이 구간이다."""
    assert app._score_roe(1.5) >= app._score_roe(0.99)


def test_roa_above_one_hundred_percent_is_not_read_as_low():
    """ROA 도 같은 변환을 쓴다. 실물 기업에서 100% 초과는 거의 없지만,
    같은 코드가 두 벌 있으면 한쪽만 고쳐지고 다른 쪽이 남는다."""
    assert app._score_roa(1.5) >= app._score_roa(0.99)


def test_no_cliff_at_the_old_boundary():
    """1.0 을 넘는 순간 점수가 떨어지면 안 된다 — 옛 버그의 형태다."""
    assert app._score_roe(1.01) >= app._score_roe(0.999)
    assert app._score_roa(1.01) >= app._score_roa(0.999)


# ── 2. 단조성 ───────────────────────────────────────────────────

@pytest.mark.parametrize("scorer", [app._score_roe, app._score_roa])
def test_losing_money_scores_lowest(scorer):
    """적자는 어떤 흑자보다도 낮다."""
    assert scorer(-0.1) < scorer(0.01) < scorer(0.25)


@pytest.mark.parametrize("scorer", [app._score_roe, app._score_roa])
def test_missing_data_is_neutral(scorer):
    """모르는 것은 좋지도 나쁘지도 않다 — 중립 50."""
    assert scorer(None) == 50
    assert scorer(np.nan) == 50


# ── 3. 저장소 안에서 단위가 하나여야 한다 ───────────────────────

def test_unit_matches_the_other_roe_consumers():
    """factor_scoring/factor_engine 은 roe*100 을 조건 없이 한다.

    여기만 조건을 달면 같은 종목이 화면과 팩터 랭킹에서 다른 등급을 받는다.
    ROE 0.15 → 15% → 75 점 구간(10~20%)에 들어가는지로 단위를 고정한다.
    """
    assert app._score_roe(0.15) == 75
    assert app._score_roa(0.12) == 85   # 12% → 10~15% 구간
