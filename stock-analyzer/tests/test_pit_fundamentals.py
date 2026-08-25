"""시점(PIT) 재무 지표 — TTM 이 분기 수를 맞추는지 고정.

`point_in_time_fundamentals` 는 walk-forward IC 의 재무 팩터 재료를 만든다.
그 IC 가 `ic_weights.json` 의 production_weights 가 되고, 그게 실전 스캔
랭킹이므로 여기서 틀린 값은 조용히 돈에 닿는다.

여기서 지키는 것은 하나다: **TTM 은 직전 4분기가 다 있을 때만 낸다.**
예전에는 항목마다 있는 것만 더해서, 매출은 4분기·영업이익은 2분기로 만든
`margin` 이 나왔다 (EDGAR 캐시 실측 3.8% 지점, 오차 중위 +80%). 틀린 값이
하필 z-score 꼬리에 앉아 상위 퀸타일로 들어간다.
"""
import numpy as np
import pandas as pd
import pytest

from modules.factor_engine import point_in_time_fundamentals as pit

TK = "AAA"
ASOF = pd.Timestamp("2025-06-30")


def _fin(**overrides):
    """4분기가 다 찬 손익·현금흐름 패널. index=filed."""
    filed = pd.to_datetime(["2024-08-01", "2024-11-01", "2025-02-01", "2025-05-01"])
    cols = {
        "revenue":             [1000.0] * 4,
        "operating_income":    [100.0] * 4,
        "net_income":          [80.0] * 4,
        "operating_cash_flow": [120.0] * 4,
        "capex":               [20.0] * 4,
    }
    cols.update(overrides)
    return {TK: pd.DataFrame(cols, index=filed)}


SHARES = {TK: pd.Series([100.0], index=pd.to_datetime(["2024-08-01"]))}
EQUITY = {TK: pd.Series([2000.0], index=pd.to_datetime(["2024-08-01"]))}


def test_full_four_quarters_are_measured():
    """4분기가 다 있으면 전부 낸다 — 기준선."""
    out = pit(TK, ASOF, price=50.0, fin_hist=_fin(),
              shares_hist=SHARES, equity_hist=EQUITY)

    assert out["margin"] == pytest.approx(400 / 4000 * 100)      # 영업이익률 10%
    assert out["roe"] == pytest.approx(320 / 2000 * 100)         # 순이익 TTM / 자본
    assert out["pe"] == pytest.approx(50.0 / (320 / 100))
    assert out["fcf_yield"] == pytest.approx((480 - 80) / (50.0 * 100) * 100)


def test_ragged_quarters_do_not_make_a_ratio():
    """영업이익만 2분기면 `2분기 이익 ÷ 4분기 매출` 이 아니라 미측정이다."""
    fin = _fin(operating_income=[np.nan, np.nan, 100.0, 100.0])
    out = pit(TK, ASOF, price=50.0, fin_hist=fin,
              shares_hist=SHARES, equity_hist=EQUITY)

    assert np.isnan(out["margin"]), "분기 수가 어긋난 채로 이익률을 냈다"
    # 매출·순이익은 4분기가 다 차 있으므로 그쪽은 그대로 나와야 한다.
    assert out["roe"] == pytest.approx(320 / 2000 * 100)


def test_short_history_does_not_understate_the_level():
    """3분기밖에 없으면 TTM 이 아니다 — 합을 그대로 쓰면 PER 이 부풀어 오른다."""
    fin = _fin()
    fin[TK] = fin[TK].iloc[1:]        # 3분기만 남긴다
    out = pit(TK, ASOF, price=50.0, fin_hist=fin,
              shares_hist=SHARES, equity_hist=EQUITY)

    assert np.isnan(out["pe"]), "3분기 순이익을 TTM 으로 써서 PER 이 부풀었다"
    assert np.isnan(out["margin"])
    assert np.isnan(out["fcf_yield"])


def test_future_filings_are_never_used():
    """as_of 뒤에 공시된 분기는 안 본다 (look-ahead 차단)."""
    out = pit(TK, pd.Timestamp("2025-03-01"), price=50.0, fin_hist=_fin(),
              shares_hist=SHARES, equity_hist=EQUITY)

    # 2025-05-01 공시분이 빠져 3분기밖에 안 남는다 → 미측정.
    assert np.isnan(out["margin"])


def _ends(*dates):
    """분기말(end) 열을 붙인 4행 패널 — assemble_income 이 싣는 그 열."""
    fin = _fin()
    fin[TK]["end"] = pd.to_datetime(list(dates))
    return fin


def test_quarter_gap_is_not_a_ttm():
    """분기가 빠지면 tail(4)가 1년이 아니다 — 15개월치 합을 TTM 이라 부르면 안 된다.

    EDGAR 캐시 실측(5년 창·293종목): PIT 관측점의 2.16%(34종목)가 이 자리이고,
    매출 TTM 이 가장 가까운 성한 창 대비 중위 3.2%·최대 821% 어긋났다.
    """
    fin = _ends("2024-06-30", "2024-09-30", "2024-12-31", "2025-06-30")  # 2025Q1 없음
    out = pit(TK, ASOF, price=50.0, fin_hist=fin,
              shares_hist=SHARES, equity_hist=EQUITY)

    assert all(np.isnan(v) for v in out.values()), "구멍 난 창은 아무 값도 내면 안 된다"


def test_four_real_quarters_still_measured():
    """성한 4분기는 end 열이 붙어도 그대로 낸다 — 가드가 멀쩡한 관측점을 지우면 안 된다."""
    fin = _ends("2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31")
    out = pit(TK, ASOF, price=50.0, fin_hist=fin,
              shares_hist=SHARES, equity_hist=EQUITY)

    assert out["margin"] == pytest.approx(400 / 4000 * 100)
