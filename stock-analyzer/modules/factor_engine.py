"""
시장 레짐 감지 + 시점(PIT) 재무 지표
=========================================================
Streamlit 의존성 없는 순수 pandas/numpy 구현. 소비자는 셋뿐이다:

  - `get_market_regime`            → paper_trade_runner_toss.py, scripts/
  - `point_in_time_fundamentals`   → factor_validator.py (walk-forward IC)
  - `REGIME_WEIGHTS`               → ic_weight_updater.py (IC 조정의 기준값)

2026-08-19: 여기 있던 5-팩터 스캐너(`calc_factor_scores`·`generate_signals`·
`fetch_fundamentals`·`fetch_returns_matrix`)를 지웠다. 페이퍼트레이드 러너가
2026-08-10 에 트레이드 플랜으로 갈아탄 뒤 부르는 곳이 하나도 없었다.
프로덕션 팩터 스캔은 app.py + modules/factor_scoring.py 가 한다.
"""
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

from modules.factor_formulas import accrual_quality as _accrual_quality

warnings.filterwarnings("ignore")

# ── 레짐별 팩터 가중치 ──────────────────────────────────────────────
# ic_weight_updater.py 가 매주 실측 IC 로 이 값을 조정해 ic_weights.json 의
# `regime_weights` 블록에 쓴다.
#
# ⚠️ **그 블록은 살아 있다. 지우지 말 것.** 2026-08-19~20 사이 이 자리에
# "읽는 코드가 없다" 고 적혀 있었는데 사실이 아니었다. 죽은 것은 이 모듈에
# 있던 `_load_regime_weights` 하나였고, 파일 쪽 독자는 둘이 남아 있다:
#   - `modules/analyst_weights.load_analyst_weights` — `regime_weights.<레짐>.ict`
#     가 **ICT 애널리스트 몫의 유일한 공급원**이다(production_weights 4팩터에는
#     ict 가 없다). 2026-08-20 실측으로 총괄 판정의 3.6~4.2% 를 차지한다.
#     블록이 사라지면 그 몫이 조용히 0 이 되고, 값은 성적표·공개 채널·
#     주문 meta 로 그대로 흘러간다.
#   - `app.py::_pick_4f_weights` — production_weights 가 없을 때의 후퇴 경로.
REGIME_WEIGHTS = {
    "bull":    {"mom_3m": 0.315, "mom_1m": 0.225, "low_vol": 0.135, "value": 0.135, "quality": 0.09, "ict": 0.10},
    "neutral": {"mom_3m": 0.225, "mom_1m": 0.180, "low_vol": 0.180, "value": 0.180, "quality": 0.135, "ict": 0.10},
    "bear":    {"mom_3m": 0.090, "mom_1m": 0.045, "low_vol": 0.360, "value": 0.225, "quality": 0.18, "ict": 0.10},
}


def get_market_regime() -> tuple:
    """
    SPY 200일 이동평균 + VIX로 시장 레짐 감지.
    - bull    : SPY ≥ 200MA AND VIX < 25
    - bear    : SPY < 97% of 200MA OR VIX > 30
    - neutral : 그 사이
    Returns (regime: str, details: dict)
    """
    # 기본값: neutral 레짐이 되도록 설정 (네트워크 실패 시 안전한 폴백)
    spy_ratio, vix_cur = 0.99, 25.0
    try:
        spy = yf.download("SPY", period="1y", progress=False, auto_adjust=True)
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.droplevel(1)
        c = spy["Close"].dropna()
        if len(c) >= 200:
            spy_ratio = float(c.iloc[-1]) / float(c.rolling(200).mean().iloc[-1])
    except Exception as e:
        print(f"  [레짐] SPY 오류: {e}")

    try:
        vix = yf.download("^VIX", period="5d", progress=False, auto_adjust=True)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.droplevel(1)
        vc = vix["Close"].dropna()
        if not vc.empty:
            vix_cur = float(vc.iloc[-1])
    except Exception as e:
        print(f"  [레짐] VIX 오류: {e}")

    if spy_ratio >= 1.0 and vix_cur < 25:
        regime = "bull"
    elif spy_ratio < 0.97 or vix_cur > 30:
        regime = "bear"
    else:
        regime = "neutral"

    return regime, {"spy_ratio": round(spy_ratio, 3), "vix": round(vix_cur, 1)}


def _ttm(recent_q, col):
    """recent_q 에서 col 의 TTM 합. 직전 4분기가 다 차지 않으면 NaN.

    항목마다 있는 분기가 다르다 — 매출은 4분기가 오는데 영업이익은 2분기만
    오는 종목이 실제로 있다. 예전에는 "있는 것만" 더해서 `2분기 이익 ÷ 4분기
    매출` 같은 비율이 조용히 나왔다 (EDGAR 캐시 실측: 시점의 3.8% 에서 발생,
    margin 오차 중위 +80%. 4분기가 안 되는데 TTM 으로 쓴 지점도 2.3% 로,
    PER 이 중위 +300% 부풀었다). 틀린 값이 하필 z-score 꼬리에 앉아 상위
    퀸타일로 들어가므로 IC 를 통째로 오염시킨다.

    분기 수가 안 맞으면 아예 내지 않는다 — quant_pit 의 `sum(min_count=4)`
    와 같은 규칙이다.
    """
    if (col in recent_q.columns and len(recent_q) == 4
            and recent_q[col].notna().all()):
        return float(recent_q[col].sum())
    return np.nan


def point_in_time_fundamentals(tk: str, as_of_date, price: float,
                                 fin_hist: dict, shares_hist: dict,
                                 equity_hist: dict = None) -> dict:
    """
    as_of_date 기준 look-ahead-free 재무 지표 계산.

    전제 계약 (edgar_fundamentals.assemble_income이 보장):
      - fin_hist[tk]는 분기당 정확히 1행 (중복 기간 없음)
      - 인덱스는 실제 공시일(filed), **행 순서는 분기말(end)**
      - 따라서 tail(4)가 가장 최근 4개 분기 = TTM으로 유효하다
    이 계약이 깨지면 (예: 중복 분기, 연간 행 혼입) TTM이 조용히 부풀려진다.
    filed 는 단조가 아니다(옛 분기가 뒤늦게 처음 공시된다) — 아래 `<= as_of` 는
    순서를 안 건드리는 **마스크**여야 하고, `.loc[:as_of]` 슬라이스면 안 된다.

    equity_hist(선택): 자본총계 Series(index=filed). 있으면 ROE를 계산한다.

    반환: {"pe", "margin", "roe", "fcf_yield", "accrual_q"} (미측정은 NaN)
    """
    out = {"pe": np.nan, "margin": np.nan, "roe": np.nan,
           "fcf_yield": np.nan, "accrual_q": np.nan}
    try:
        df = fin_hist.get(tk, pd.DataFrame())
        if df.empty:
            return out
        past = df[df.index <= pd.Timestamp(as_of_date)]
        if past.empty:
            return out

        recent_q = past.tail(4)
        net_ttm   = _ttm(recent_q, "net_income")
        rev_ttm   = _ttm(recent_q, "revenue")
        op_ttm    = _ttm(recent_q, "operating_income")
        ocf_ttm   = _ttm(recent_q, "operating_cash_flow")
        capex_ttm = _ttm(recent_q, "capex")

        sh = shares_hist.get(tk, pd.Series(dtype=float))
        if not sh.empty:
            sh_past  = sh[sh.index <= pd.Timestamp(as_of_date)]
            n_shares = float(sh_past.iloc[-1]) if not sh_past.empty else float(sh.iloc[-1])
        else:
            n_shares = np.nan

        # PER (EPS>0일 때만)
        if pd.notna(n_shares) and n_shares > 0 and pd.notna(net_ttm) and price > 0:
            eps = net_ttm / n_shares
            if eps > 0:
                out["pe"] = min(price / eps, 200.0)

        # 영업이익률
        if pd.notna(rev_ttm) and rev_ttm > 0 and pd.notna(op_ttm):
            out["margin"] = (op_ttm / rev_ttm) * 100

        # ROE = net_ttm / 자본총계 (as_of 이전 최신 공시)
        if equity_hist is not None:
            eq = equity_hist.get(tk, pd.Series(dtype=float))
            if not eq.empty:
                eq_past = eq[eq.index <= pd.Timestamp(as_of_date)]
                equity_v = float(eq_past.iloc[-1]) if not eq_past.empty else np.nan
                if pd.notna(equity_v) and equity_v > 0 and pd.notna(net_ttm):
                    out["roe"] = (net_ttm / equity_v) * 100

        # FCF = OCF - CapEx (CapEx는 지출이라 양수 보고 → 차감)
        if pd.notna(ocf_ttm) and pd.notna(capex_ttm):
            fcf_ttm = ocf_ttm - capex_ttm
            # FCF 수익률 = FCF_TTM / 시가총액 (시총 = price × 주식수)
            if pd.notna(n_shares) and n_shares > 0 and price > 0:
                mcap = price * n_shares
                if mcap > 0:
                    out["fcf_yield"] = fcf_ttm / mcap * 100
            # 발생액 품질 — 공식은 factor_formulas 소유다(라이브 경로와 같은 함수).
            # 여기 사본이 남아 있으면 배합을 바꿀 때 IC 쪽만 옛 값으로 잰다.
            # 순이익이 없거나 0 이하면 NaN 으로 둔다 — 라이브의 중립 50 과 다른데,
            # IC 는 단면 z-score 라 '판정 불가'를 중립 상수로 채우면 그 종목이
            # 관측으로 섞인다. 소비 측 _zscore_col 이 NaN 을 z=0(평균)으로 받는다.
            if pd.notna(net_ttm) and net_ttm > 0:
                out["accrual_q"] = _accrual_quality(fcf_ttm, net_ttm)
    except Exception:
        pass
    return out
