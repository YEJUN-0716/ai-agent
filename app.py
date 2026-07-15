import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import requests
import os
import json
import warnings
warnings.filterwarnings('ignore')

# ── 퀀트 모듈 ──────────────────────────────────────────────────
try:
    from modules.pit_data_logger import PITStore as _PITStore
    _pit_store = _PITStore("pit_fundamentals.db")
    _PIT_AVAILABLE = True
except Exception:
    _PIT_AVAILABLE = False

try:
    from modules.stat_validation import (
        deflated_sharpe_ratio as _dsr,
        block_bootstrap_sharpe_ci as _bb_ci,
        permutation_test_trades as _perm_test,
    )
    _STAT_AVAILABLE = True
except Exception:
    _STAT_AVAILABLE = False

_PT_AVAILABLE = False

try:
    from modules.ml_signals import train_and_validate_ml_signal as _ml_train
    _ML_AVAILABLE = True
except Exception:
    _ML_AVAILABLE = False

try:
    from modules.risk_management import run_backtest_sized
    _RISK_MGMT_ENABLED = True
except Exception:
    _RISK_MGMT_ENABLED = False

try:
    from modules.data_integrity import full_data_integrity_check as _data_integrity_check
    _DATA_INTEGRITY_AVAILABLE = True
except Exception:
    _DATA_INTEGRITY_AVAILABLE = False

try:
    from modules.portfolio_allocator import (
        inverse_vol_weights as _inv_vol_weights,
        risk_parity_weights as _rp_weights,
        combine_strategies as _combine_strategies,
        diversification_report as _diversification_report,
        compute_strategy_correlation as _strategy_corr,
    )
    _PORTFOLIO_ALLOCATOR_AVAILABLE = True
except Exception:
    _PORTFOLIO_ALLOCATOR_AVAILABLE = False

try:
    from modules.ops_safety import KillSwitch as _KillSwitch, reconcile_positions as _reconcile_pos, AlertDispatcher as _AlertDispatcher
    _OPS_SAFETY_AVAILABLE = True
except Exception:
    _OPS_SAFETY_AVAILABLE = False

_PT_TRACKER_AVAILABLE = False

try:
    from modules.tax_kr import OverseasStockLedger as _TaxLedger, calc_capital_gains_tax as _calc_tax, suggest_year_end_tax_loss_harvesting as _tax_harvest
    _TAX_KR_AVAILABLE = True
except Exception:
    _TAX_KR_AVAILABLE = False

try:
    from modules.alpha_decay_monitor import detect_alpha_decay as _detect_alpha_decay, rolling_performance_vs_baseline as _rolling_perf_vs_bt, cusum_change_detection as _cusum_detect
    _ALPHA_DECAY_AVAILABLE = True
except Exception:
    _ALPHA_DECAY_AVAILABLE = False

try:
    from modules.stress_test import replay_historical_scenario as _replay_scenario, run_all_scenarios as _run_all_scenarios, synthetic_shock_test as _synthetic_shock, KNOWN_STRESS_PERIODS as _STRESS_PERIODS
    _STRESS_TEST_AVAILABLE = True
except Exception:
    _STRESS_TEST_AVAILABLE = False

try:
    from modules.signal_decay_analysis import full_decay_analysis as _signal_decay_full, compute_signal_ic_decay as _signal_ic_decay
    _SIGNAL_DECAY_AVAILABLE = True
except Exception:
    _SIGNAL_DECAY_AVAILABLE = False

try:
    from modules.factor_risk_model import regression_style_analysis as _style_analysis, rolling_market_beta as _rolling_beta, sector_concentration_report as _sector_conc
    _FACTOR_RISK_AVAILABLE = True
except Exception:
    _FACTOR_RISK_AVAILABLE = False


def _pit_snapshot(ticker, info):
    """yf.Ticker(...).info 호출 직후 PIT 스냅샷 저장. 실패해도 앱은 계속 진행."""
    if _PIT_AVAILABLE and info:
        try:
            _pit_store.snapshot(ticker, info)
        except Exception:
            pass

st.set_page_config(page_title="퀀트 트레이딩 시스템", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800&family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ═══════════════════════════════════════════
   DESIGN TOKENS
═══════════════════════════════════════════ */
:root {
  --bg:        #f8fafc;
  --surface:   #ffffff;
  --surface2:  #f1f5f9;
  --border:    #e2e8f0;
  --border2:   #cbd5e1;

  --text-1:    #0f172a;
  --text-2:    #334155;
  --text-3:    #64748b;
  --text-4:    #94a3b8;

  --green:     #10b981;
  --green-bg:  #ecfdf5;
  --green-bd:  #6ee7b7;
  --red:       #ef4444;
  --red-bg:    #fef2f2;
  --red-bd:    #fca5a5;
  --amber:     #f59e0b;
  --amber-bg:  #fffbeb;
  --amber-bd:  #fcd34d;
  --blue:      #3b82f6;
  --blue-bg:   #eff6ff;
  --blue-bd:   #93c5fd;

  --radius-sm: 6px;
  --radius:    10px;
  --radius-lg: 14px;

  --shadow-sm: 0 1px 2px rgba(0,0,0,.06);
  --shadow:    0 2px 8px rgba(0,0,0,.08);
  --shadow-md: 0 4px 16px rgba(0,0,0,.10);
}

/* ═══════════════════════════════════════════
   BASE
═══════════════════════════════════════════ */
html, body, [class*="css"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: var(--text-1);
  background: var(--bg);
}
.block-container {
  padding: 1rem 2rem 3rem !important;
  max-width: 1600px;
}
h1 { font-weight: 800 !important; letter-spacing: -0.8px; font-size: 1.75rem !important; color: var(--text-1); }
h2 { font-weight: 700 !important; letter-spacing: -0.4px; font-size: 1.25rem !important; color: var(--text-1); }
h3 { font-weight: 600 !important; letter-spacing: -0.2px; font-size: 1.05rem !important; color: var(--text-2); }
p  { color: var(--text-2); }

/* ═══════════════════════════════════════════
   TABS — 메인 (pill/segment)
═══════════════════════════════════════════ */
.stTabs [data-baseweb="tab-list"] {
  gap: 2px;
  background: var(--surface2);
  padding: 3px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  border-bottom: 1px solid var(--border) !important;
}
.stTabs [data-baseweb="tab"] {
  border-radius: var(--radius-sm) !important;
  padding: 7px 20px !important;
  font-weight: 500;
  font-size: 14px;
  color: var(--text-3) !important;
  border: none !important;
  background: transparent !important;
  transition: all 150ms ease;
  white-space: nowrap;
}
.stTabs [aria-selected="true"] {
  background: var(--surface) !important;
  color: var(--text-1) !important;
  font-weight: 600 !important;
  box-shadow: var(--shadow-sm) !important;
}
.stTabs [data-baseweb="tab-panel"] {
  padding-top: 1.25rem !important;
}

/* ── 서브탭 (underline style) ── */
.stTabs .stTabs [data-baseweb="tab-list"] {
  background: transparent;
  padding: 0;
  border: none !important;
  border-bottom: 2px solid var(--border) !important;
  border-radius: 0;
  gap: 0;
}
.stTabs .stTabs [data-baseweb="tab"] {
  border-radius: 0 !important;
  padding: 7px 16px !important;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-4) !important;
  background: transparent !important;
  box-shadow: none !important;
  border-bottom: 2px solid transparent !important;
  margin-bottom: -2px;
}
.stTabs .stTabs [aria-selected="true"] {
  color: var(--green) !important;
  font-weight: 600 !important;
  border-bottom: 2px solid var(--green) !important;
  background: transparent !important;
  box-shadow: none !important;
}
.stTabs .stTabs [data-baseweb="tab-panel"] {
  padding-top: 1rem !important;
}

/* ═══════════════════════════════════════════
   METRIC CARDS
═══════════════════════════════════════════ */
[data-testid="metric-container"] {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 18px !important;
  transition: box-shadow 200ms ease, border-color 200ms ease;
}
[data-testid="metric-container"]:hover {
  box-shadow: var(--shadow);
  border-color: var(--border2);
}
[data-testid="metric-container"] label {
  font-size: 11px !important;
  font-weight: 600 !important;
  color: var(--text-3) !important;
  text-transform: uppercase;
  letter-spacing: 0.7px;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
  font-size: 24px !important;
  font-weight: 700 !important;
  color: var(--text-1) !important;
  font-family: 'JetBrains Mono', monospace !important;
  letter-spacing: -0.5px;
}
[data-testid="stMetricDelta"] {
  font-size: 12px !important;
  font-weight: 600 !important;
}

/* ═══════════════════════════════════════════
   BUTTONS
═══════════════════════════════════════════ */
.stButton > button {
  border-radius: var(--radius-sm) !important;
  font-weight: 600 !important;
  font-size: 14px !important;
  transition: all 160ms ease !important;
  height: 40px !important;
}
.stButton > button[kind="primary"] {
  background: var(--green) !important;
  border: none !important;
  color: #fff !important;
  box-shadow: 0 1px 3px rgba(16,185,129,.35) !important;
}
.stButton > button[kind="primary"]:hover {
  background: #059669 !important;
  box-shadow: 0 4px 14px rgba(16,185,129,.40) !important;
  transform: translateY(-1px) !important;
}
.stButton > button[kind="secondary"] {
  border: 1px solid var(--border2) !important;
  color: var(--text-2) !important;
  background: var(--surface) !important;
}
.stButton > button[kind="secondary"]:hover {
  border-color: var(--green) !important;
  color: var(--green) !important;
  background: var(--green-bg) !important;
}

/* ═══════════════════════════════════════════
   INPUTS
═══════════════════════════════════════════ */
.stTextInput input,
.stNumberInput input,
.stSelectbox > div > div,
.stTextArea textarea {
  border-radius: var(--radius-sm) !important;
  border: 1px solid var(--border2) !important;
  font-size: 14px !important;
  background: var(--surface) !important;
  color: var(--text-1) !important;
  transition: border-color 150ms ease, box-shadow 150ms ease;
}
.stTextInput input:focus,
.stNumberInput input:focus,
.stTextArea textarea:focus {
  border-color: var(--green) !important;
  box-shadow: 0 0 0 3px rgba(16,185,129,.12) !important;
  outline: none !important;
}
.stTextInput label,
.stNumberInput label,
.stSelectbox label,
.stTextArea label {
  font-size: 12px !important;
  font-weight: 600 !important;
  color: var(--text-3) !important;
  text-transform: uppercase !important;
  letter-spacing: 0.5px !important;
}

/* ═══════════════════════════════════════════
   EXPANDER
═══════════════════════════════════════════ */
details {
  border-radius: var(--radius) !important;
  border: 1px solid var(--border) !important;
  background: var(--surface);
  margin-bottom: 8px !important;
  overflow: hidden;
}
details[open] { box-shadow: var(--shadow-sm); }
summary {
  font-weight: 600 !important;
  font-size: 14px !important;
  color: var(--text-2) !important;
  padding: 12px 16px !important;
  cursor: pointer;
}
summary:hover { background: var(--surface2) !important; }

/* ═══════════════════════════════════════════
   DATAFRAME
═══════════════════════════════════════════ */
.stDataFrame {
  border-radius: var(--radius) !important;
  border: 1px solid var(--border) !important;
  overflow: hidden;
}
.stDataFrame [data-testid="stDataFrameResizable"] th {
  background: var(--surface2) !important;
  font-weight: 600 !important;
  font-size: 12px !important;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--text-3) !important;
  border-bottom: 1px solid var(--border) !important;
}
.stDataFrame [data-testid="stDataFrameResizable"] td {
  font-size: 13px !important;
  color: var(--text-2) !important;
  border-bottom: 1px solid var(--bg) !important;
}

/* ═══════════════════════════════════════════
   ALERTS
═══════════════════════════════════════════ */
.stAlert {
  border-radius: var(--radius) !important;
  border-width: 1px !important;
  font-size: 14px !important;
}
[data-testid="stNotificationContentInfo"]    { border-color: var(--blue-bd) !important; background: var(--blue-bg) !important; }
[data-testid="stNotificationContentSuccess"] { border-color: var(--green-bd) !important; background: var(--green-bg) !important; }
[data-testid="stNotificationContentWarning"] { border-color: var(--amber-bd) !important; background: var(--amber-bg) !important; }
[data-testid="stNotificationContentError"]   { border-color: var(--red-bd) !important; background: var(--red-bg) !important; }

/* ═══════════════════════════════════════════
   MISC
═══════════════════════════════════════════ */
.stCheckbox label  { font-size: 14px !important; font-weight: 500; color: var(--text-2); }
.stRadio label     { font-size: 14px !important; color: var(--text-2); }
.stCaption, [data-testid="stCaptionContainer"] { color: var(--text-4) !important; font-size: 12px !important; }
hr { border: none !important; border-top: 1px solid var(--border) !important; margin: 1.2rem 0 !important; }
[data-testid="stMarkdownContainer"] p { font-size: 14px; line-height: 1.7; color: var(--text-2); }
code { font-family: 'JetBrains Mono', monospace !important; font-size: 12px !important; background: var(--surface2) !important; padding: 2px 6px !important; border-radius: 4px !important; color: var(--text-1) !important; }

/* ── 진행바 (score bar 등) ── */
.qt-progress-track {
  background: var(--surface2);
  border-radius: 6px;
  height: 8px;
  overflow: hidden;
}
.qt-progress-fill {
  height: 8px;
  border-radius: 6px;
  transition: width 400ms ease;
}

/* ── 사이드바 숨김 ── */
[data-testid="stSidebar"]       { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }

/* ── 모바일 ── */
@media (max-width: 768px) {
  .block-container { padding: 0.75rem 1rem 2rem !important; }
  h1 { font-size: 1.35rem !important; }
  h2 { font-size: 1.05rem !important; }
  .stTabs [data-baseweb="tab"] { padding: 6px 10px !important; font-size: 12px !important; }
  [data-testid="metric-container"] { padding: 10px 12px !important; }
  [data-testid="metric-container"] [data-testid="stMetricValue"] { font-size: 18px !important; }
}

/* ── 스크롤바 ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--surface2); }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-4); }
</style>""", unsafe_allow_html=True)

TV_BG = TV_PAPER = '#ffffff'
TV_GRID = '#e0e0e0'
TV_BORDER = '#cccccc'
TV_TEXT = '#1a1a1a'
TV_UP = '#26a69a'
TV_DOWN = '#ef5350'

# 종목 → 섹터 ETF 매핑 (detect_chart_pattern 섹터 RS 계산용)
TICKER_ETF = {
    # 반도체
    'NVDA':'SMH','AMD':'SMH','INTC':'SMH','MU':'SMH','TSM':'SMH',
    'AMAT':'SMH','LRCX':'SMH','KLAC':'SMH','ASML':'SMH','ARM':'SMH',
    'MRVL':'SMH','SNDK':'SMH','ON':'SMH','TXN':'SMH','QCOM':'SMH',
    # 빅테크/소프트웨어
    'MSFT':'XLK','AAPL':'XLK','ORCL':'XLK','CRM':'XLK','NOW':'XLK',
    'ADBE':'XLK','PLTR':'XLK','DDOG':'XLK','PANW':'XLK','CRWD':'XLK',
    'SNOW':'XLK','NET':'XLK','MDB':'XLK','ZS':'XLK',
    # 커뮤니케이션
    'META':'XLC','GOOGL':'XLC','GOOG':'XLC','NFLX':'XLC','DIS':'XLC',
    # 소비재
    'AMZN':'XLY','TSLA':'XLY','NKE':'XLY','SBUX':'XLY','BKNG':'XLY',
    # 금융
    'JPM':'XLF','BAC':'XLF','GS':'XLF','MS':'XLF','V':'XLF','MA':'XLF',
    # 헬스케어
    'JNJ':'XLV','UNH':'XLV','PFE':'XLV','ABBV':'XLV','LLY':'XLV','MRK':'XLV',
    # 에너지
    'XOM':'XLE','CVX':'XLE','COP':'XLE','SLB':'XLE',
    # 산업재
    'CAT':'XLI','BA':'XLI','HON':'XLI','GE':'XLI','UPS':'XLI',
}


@st.cache_data(ttl=3600, show_spinner=False)
def _get_market_regime():
    """SPY·QQQ vs MA200으로 시장 레짐 판단. 1시간 캐시."""
    try:
        data = yf.download(['SPY', 'QQQ'], period='1y', interval='1d',
                           progress=False, auto_adjust=True)
        closes = data['Close']
        flags = {}
        for sym in ['SPY', 'QQQ']:
            c = closes[sym].dropna()
            if len(c) >= 200:
                flags[sym] = float(c.iloc[-1]) > float(c.rolling(200).mean().iloc[-1])
        spy_b, qqq_b = flags.get('SPY'), flags.get('QQQ')
        if spy_b and qqq_b:    return 'bull'
        if spy_b is False and qqq_b is False: return 'bear'
        return 'mixed'
    except Exception:
        return 'unknown'


# ─────────────────────────────────────────────
# 추가 팩터 데이터 (무료 yfinance)
# ─────────────────────────────────────────────

def _fetch_extra_factors(ticker, info):
    """애널리스트 추천·공매도 비율·EPS 서프라이즈 팩터 수집."""
    out = {}
    # ① 애널리스트 추천 (1=강력매수~5=강력매도 → 인버트)
    rec = info.get('recommendationMean')
    if rec and 1 <= rec <= 5:
        out['analyst_raw'] = round((5 - rec) / 4 * 100, 1)
    # ② 공매도 비율 (낮을수록 좋음 → 인버트)
    sr = info.get('shortRatio')
    if sr is not None and sr >= 0:
        out['short_raw'] = round(max(0, 100 - min(sr * 8, 100)), 1)
    # ③ EPS 서프라이즈 (최근 4분기 평균)
    try:
        eh = yf.Ticker(ticker).earnings_history
        if eh is not None and not eh.empty:
            need = {'epsEstimate', 'epsActual'}
            if need.issubset(eh.columns):
                recent = eh.dropna(subset=list(need)).tail(4)
                if not recent.empty:
                    surp = ((recent['epsActual'] - recent['epsEstimate']) /
                            recent['epsEstimate'].abs().clip(lower=0.01) * 100)
                    out['eps_surprise_raw'] = round(float(surp.mean()), 1)
    except Exception:
        pass
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def calc_sector_rotation():
    """12개 섹터 ETF 모멘텀 랭킹 (1시간 캐시)."""
    SECTORS = {
        'XLK':'기술', 'XLC':'커뮤니케이션', 'XLY':'임의소비재',
        'XLF':'금융', 'XLV':'헬스케어', 'XLE':'에너지',
        'XLI':'산업재', 'XLB':'소재', 'XLRE':'부동산',
        'XLP':'필수소비재', 'XLU':'유틸리티', 'SMH':'반도체',
    }
    try:
        data = yf.download(list(SECTORS), period='1y', interval='1d',
                           progress=False, auto_adjust=True)
        closes = data['Close']
        rows = []
        for sym, name in SECTORS.items():
            if sym not in closes.columns:
                continue
            c = closes[sym].dropna()
            if len(c) < 21:
                continue
            cur = float(c.iloc[-1])
            r1  = (cur / float(c.iloc[-21])  - 1) * 100 if len(c) >= 21  else None
            r3  = (cur / float(c.iloc[-63])  - 1) * 100 if len(c) >= 63  else None
            r6  = (cur / float(c.iloc[-126]) - 1) * 100 if len(c) >= 126 else None
            ma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else None
            _pairs = [(r1,0.5),(r3,0.3),(r6,0.2)]
            mom = (sum(x*w for x,w in _pairs if x is not None) /
                   sum(w   for x,w in _pairs if x is not None)) \
                  if any(x is not None for x,_ in _pairs) else 0
            rows.append({
                'ETF': sym, '섹터': name, '현재가': f"${cur:.2f}",
                '1M%': round(r1, 1) if r1 is not None else None,
                '3M%': round(r3, 1) if r3 is not None else None,
                '6M%': round(r6, 1) if r6 is not None else None,
                'MA50↑': '✅' if (ma50 and cur > ma50) else '❌',
                '모멘텀': round(mom, 1),
            })
        df = pd.DataFrame(rows).sort_values('모멘텀', ascending=False).reset_index(drop=True)
        df.insert(0, '순위', range(1, len(df)+1))
        return df
    except Exception:
        return pd.DataFrame()


def kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float,
                   half_kelly: bool = True) -> float:
    """Kelly Criterion 최적 투입 비율. half_kelly=True 권장."""
    if avg_loss_pct <= 0 or win_rate <= 0:
        return 0.0
    b = avg_win_pct / avg_loss_pct
    if b == 0:
        return 0.0
    f = (win_rate * b - (1 - win_rate)) / b
    f = max(0.0, f)
    if half_kelly:
        f *= 0.5
    return min(f, 0.25)


def monte_carlo_portfolio(returns_arr, capital: float,
                          horizon_days: int = 252, n_sim: int = 1000):
    """수익률 배열로 몬테카를로 시뮬레이션. 백분위 결과 반환."""
    mu  = float(np.mean(returns_arr))
    sig = float(np.std(returns_arr))
    paths = np.zeros((n_sim, horizon_days + 1))
    paths[:, 0] = capital
    rng = np.random.default_rng(42)
    daily = rng.normal(mu, sig, (n_sim, horizon_days))
    for t in range(1, horizon_days + 1):
        paths[:, t] = paths[:, t - 1] * (1 + daily[:, t - 1])
    final = paths[:, -1]
    pct   = np.percentile(final, [5, 25, 50, 75, 95])
    return {
        'paths': paths,
        'final': final,
        'p5':  pct[0], 'p25': pct[1], 'p50': pct[2],
        'p75': pct[3], 'p95': pct[4],
        'prob_profit':    float(np.mean(final > capital)),
        'prob_loss_20':   float(np.mean(final < capital * 0.80)),
        'max_loss_p5':    round((pct[0] / capital - 1) * 100, 1),
        'best_p95':       round((pct[4] / capital - 1) * 100, 1),
    }


# ─────────────────────────────────────────────
# 통합 주식 데이터 다운로드
# ─────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def download_stock(ticker, start, end, interval='1d'):
    """한국 주식: FinanceDataReader 우선, yfinance 폴백.
    그 외: yfinance 사용."""
    is_krx = ticker.endswith('.KS') or ticker.endswith('.KQ')

    if is_krx and interval == '1d':
        try:
            import FinanceDataReader as fdr
            code = ticker.split('.')[0]
            df = fdr.DataReader(code, start, end)
            if df is not None and not df.empty and len(df) >= 5:
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                df.index.name = 'Date'
                return df
        except Exception:
            pass

    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df


SECTOR_ETF = {
    'Technology':             'XLK',
    'Consumer Cyclical':      'XLY',
    'Financial Services':     'XLF',
    'Healthcare':             'XLV',
    'Consumer Defensive':     'XLP',
    'Communication Services': 'XLC',
    'Industrials':            'XLI',
    'Basic Materials':        'XLB',
    'Energy':                 'XLE',
    'Real Estate':            'XLRE',
    'Utilities':              'XLU',
}

SECTOR_AVG_PER = {
    'Technology': 28, 'Consumer Cyclical': 22, 'Financial Services': 13,
    'Healthcare': 20, 'Consumer Defensive': 19, 'Communication Services': 20,
    'Industrials': 18, 'Basic Materials': 14, 'Energy': 11,
    'Real Estate': 30, 'Utilities': 17,
}

# 시장 국면별 기술적 지표 가중치 (합계=1.0)
REGIME_TECH_WEIGHTS = {
    # 강세장: 추세/모멘텀 지표(MA·ADX·MACD) 강조
    'bull':    {'MA정렬':0.20,'RSI':0.07,'MACD':0.15,'볼린저밴드':0.07,
                '거래량':0.09,'파동근사':0.09,'스토캐스틱':0.07,'ADX추세강도':0.20,'OBV':0.06},
    # 중립장: 기본 가중치
    'neutral': {'MA정렬':0.15,'RSI':0.10,'MACD':0.13,'볼린저밴드':0.10,
                '거래량':0.07,'파동근사':0.08,'스토캐스틱':0.10,'ADX추세강도':0.17,'OBV':0.10},
    # 약세장: 반전/과매도 지표(RSI·BB·OBV) 강조
    'bear':    {'MA정렬':0.08,'RSI':0.15,'MACD':0.10,'볼린저밴드':0.15,
                '거래량':0.07,'파동근사':0.05,'스토캐스틱':0.13,'ADX추세강도':0.12,'OBV':0.15},
}

# ─────────────────────────────────────────────
# TECHNICAL ANALYSIS
# ─────────────────────────────────────────────

try:
    import pandas_ta as _pta
    _PTA_AVAILABLE = True
except ImportError:
    _pta = None
    _PTA_AVAILABLE = False


def calc_rsi(prices, period=14):
    if _PTA_AVAILABLE:
        result = _pta.rsi(prices, length=period)
        if result is not None:
            return result
    delta = prices.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))


def calc_macd(prices, fast=12, slow=26, signal=9):
    if _PTA_AVAILABLE:
        result = _pta.macd(prices, fast=fast, slow=slow, signal=signal)
        if result is not None:
            mc = result[f'MACD_{fast}_{slow}_{signal}']
            sg = result[f'MACDs_{fast}_{slow}_{signal}']
            ht = result[f'MACDh_{fast}_{slow}_{signal}']
            return mc, sg, ht
    ema_f = prices.ewm(span=fast,   adjust=False).mean()
    ema_s = prices.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig


def calc_bb(prices, period=20, k=2):
    if _PTA_AVAILABLE:
        result = _pta.bbands(prices, length=period, std=float(k))
        if result is not None:
            kf = float(k)
            return result[f'BBU_{period}_{kf}'], result[f'BBM_{period}_{kf}'], result[f'BBL_{period}_{kf}']
    sma = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    return sma + k*std, sma, sma - k*std


def calc_stochastic(high, low, close, k=14, d=3):
    if _PTA_AVAILABLE:
        result = _pta.stoch(high, low, close, k=k, d=d)
        if result is not None:
            return result[f'STOCHk_{k}_{d}_{d}'], result[f'STOCHd_{k}_{d}_{d}']
    lo = low.rolling(k).min()
    hi = high.rolling(k).max()
    pct_k = (close - lo) / (hi - lo + 1e-9) * 100
    return pct_k, pct_k.rolling(d).mean()


def calc_adx(high, low, close, period=14):
    if _PTA_AVAILABLE:
        result = _pta.adx(high, low, close, length=period)
        if result is not None:
            return result[f'ADX_{period}'], result[f'DMP_{period}'], result[f'DMN_{period}']
    pc  = close.shift(1)
    tr  = pd.concat([(high-low), (high-pc).abs(), (low-pc).abs()], axis=1).max(axis=1)
    up  = high.diff(); dn = -low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    atr = tr.rolling(period).mean()
    pdi = pdm.rolling(period).mean() / (atr + 1e-9) * 100
    ndi = ndm.rolling(period).mean() / (atr + 1e-9) * 100
    dx  = (pdi - ndi).abs() / (pdi + ndi + 1e-9) * 100
    return dx.rolling(period).mean(), pdi, ndi


def calc_obv(close, volume):
    if _PTA_AVAILABLE:
        result = _pta.obv(close, volume)
        if result is not None:
            return result
    sign = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (volume * sign).cumsum()

def detect_trading_signals(df, t_det):
    """컨플루언스 기반 매매 시그널 감지.
    ADX 추세 필터, 거래량 확인, whipsaw 방지 적용."""
    p = df['Close']
    cp = float(p.iloc[-1])
    signals = []
    bull, bear = 0, 0

    adx_val  = t_det.get('ADX값', 20)
    has_trend = adx_val > 20
    rsi_v = t_det.get('RSI값', 50)
    sk_v  = t_det.get('Stoch값', 50)

    vr = float(df['Volume'].iloc[-1]) / (float(df['Volume'].rolling(20).mean().iloc[-1]) + 1e-9)
    vol_ok = vr >= 0.8

    # ── RSI (동적 임계값: 추세장에서 완화) ────────
    rsi_ob = 65 if (has_trend and adx_val > 30) else 70
    rsi_os = 35 if (has_trend and adx_val > 30) else 30
    if rsi_v < rsi_os:
        signals.append(('🟢', 'RSI 과매도', f'RSI {rsi_v:.1f} < {rsi_os} — 반등 가능'))
        bull += 1
    elif rsi_v > rsi_ob:
        signals.append(('🔴', 'RSI 과매수', f'RSI {rsi_v:.1f} > {rsi_ob} — 조정 주의'))
        bear += 1

    # ── MA 크로스 (ADX 필터 + 3봉 지속 확인) ─────
    ma20_s = p.rolling(20).mean(); ma60_s = p.rolling(60).mean()
    if len(p) >= 25:
        above_cnt = sum(1 for j in range(-3, 0) if float(ma20_s.iloc[j]) > float(ma60_s.iloc[j]))
        below_cnt = 3 - above_cnt
        was_below_5d = float(ma20_s.iloc[-5]) <= float(ma60_s.iloc[-5])
        was_above_5d = float(ma20_s.iloc[-5]) >= float(ma60_s.iloc[-5])

        if above_cnt >= 3 and was_below_5d:
            if has_trend:
                signals.append(('🟢', '골든크로스', f'MA20 ↑ MA60 (3봉 지속, ADX {adx_val:.0f})'))
                bull += 2
            else:
                signals.append(('🟡', '골든크로스 (약)', f'MA20 ↑ MA60 — ADX {adx_val:.0f} 추세 약함'))
                bull += 1
        elif below_cnt >= 3 and was_above_5d:
            if has_trend:
                signals.append(('🔴', '데드크로스', f'MA20 ↓ MA60 (3봉 지속, ADX {adx_val:.0f})'))
                bear += 2
            else:
                signals.append(('🟡', '데드크로스 (약)', f'MA20 ↓ MA60 — ADX {adx_val:.0f} 추세 약함'))
                bear += 1

    # ── 볼린저밴드 ─────────────────────────────────
    bb_u_s, _, bb_l_s = calc_bb(p)
    if cp > float(bb_u_s.iloc[-1]):
        signals.append(('🔴', 'BB 상단 돌파', '과매수 구간 — 단기 조정 주의'))
        bear += 1
    elif cp < float(bb_l_s.iloc[-1]):
        signals.append(('🟢', 'BB 하단 이탈', '과매도 구간 — 반등 대기'))
        bull += 1
    elif len(p) >= 40:
        bw_n = (float(bb_u_s.iloc[-1]) - float(bb_l_s.iloc[-1])) / (cp + 1e-9)
        bw_a = float(((bb_u_s - bb_l_s) / p).rolling(20).mean().iloc[-1])
        if bw_n < bw_a * 0.7:
            signals.append(('🟡', 'BB 스퀴즈', '밴드 수축 — 큰 방향성 돌파 임박'))

    # ── MACD (크로스 + 히스토그램 방향) ────────────
    ml_s, sl_s, hist_s = calc_macd(p)
    if len(ml_s) >= 3:
        cross_up  = float(ml_s.iloc[-2]) <= float(sl_s.iloc[-2]) and float(ml_s.iloc[-1]) > float(sl_s.iloc[-1])
        cross_dn  = float(ml_s.iloc[-2]) >= float(sl_s.iloc[-2]) and float(ml_s.iloc[-1]) < float(sl_s.iloc[-1])
        hist_rising = float(hist_s.iloc[-1]) > float(hist_s.iloc[-2])
        if cross_up:
            signals.append(('🟢', 'MACD 상향 돌파', 'MACD > Signal — 단기 매수 신호'))
            bull += 1
        elif cross_dn:
            signals.append(('🔴', 'MACD 하향 돌파', 'MACD < Signal — 단기 매도 신호'))
            bear += 1
        elif float(ml_s.iloc[-1]) > float(sl_s.iloc[-1]) and hist_rising:
            bull += 1
        elif float(ml_s.iloc[-1]) < float(sl_s.iloc[-1]) and not hist_rising:
            bear += 1

    # ── 스토캐스틱 (동적 임계값) ────────────────────
    stoch_ob = 75 if has_trend else 80
    stoch_os = 25 if has_trend else 20
    if sk_v < stoch_os:
        signals.append(('🟢', '스토캐스틱 과매도', f'%K {sk_v:.1f} < {stoch_os} — 반등 구간'))
        bull += 1
    elif sk_v > stoch_ob:
        signals.append(('🔴', '스토캐스틱 과매수', f'%K {sk_v:.1f} > {stoch_ob} — 과열 구간'))
        bear += 1

    # ── OBV 다이버전스 ─────────────────────────────
    if len(p) >= 20:
        obv = calc_obv(p, df['Volume'])
        if cp < float(p.iloc[-20]) and float(obv.iloc[-1]) > float(obv.iloc[-20]):
            signals.append(('🟢', 'OBV 상승 다이버전스', '가격↓ 거래량↑ — 매집 가능'))
            bull += 1
        elif cp > float(p.iloc[-20]) and float(obv.iloc[-1]) < float(obv.iloc[-20]):
            signals.append(('🔴', 'OBV 하락 다이버전스', '가격↑ 거래량↓ — 분산 가능'))
            bear += 1

    # ── 거래량 확인 ────────────────────────────────
    if vr > 2.0:
        dir_s = '상승' if cp > float(p.iloc[-2]) else '하락'
        signals.append(('⚡', '거래량 급증', f'평균의 {vr:.1f}배 ({dir_s}) — 방향성 강화'))
        if cp > float(p.iloc[-2]): bull += 1
        else: bear += 1
    elif not vol_ok:
        signals.append(('⚠️', '거래량 부족', f'평균의 {vr:.1f}배 — 신호 신뢰도 감소'))

    # ── 컨플루언스 종합 판정 ───────────────────────
    net = bull - bear
    total_sigs = bull + bear
    if total_sigs == 0:
        conf = ('⚪', '시그널 없음', '뚜렷한 매매 신호가 없습니다')
    elif net >= 3:
        conf = ('🟢', f'강한 매수 합류 ({bull}:{bear})', f'{bull}개 매수 신호 동시 발생 — 높은 신뢰도')
    elif net >= 2:
        conf = ('🟢', f'매수 우세 ({bull}:{bear})', f'매수 신호 우세 — 보통 신뢰도')
    elif net <= -3:
        conf = ('🔴', f'강한 매도 합류 ({bear}:{bull})', f'{bear}개 매도 신호 동시 발생 — 높은 신뢰도')
    elif net <= -2:
        conf = ('🔴', f'매도 우세 ({bear}:{bull})', f'매도 신호 우세 — 보통 신뢰도')
    else:
        conf = ('🟡', f'혼조세 ({bull}:{bear})', '매수/매도 엇갈림 — 관망 권장')

    signals.insert(0, conf)
    return signals

def wave_score(prices, highs, lows):
    if len(prices) < 60:
        return 50.0
    rs = (prices.iloc[-1] - prices.iloc[-20]) / (prices.iloc[-20] + 1e-9)
    rm = (prices.iloc[-1] - prices.iloc[-60]) / (prices.iloc[-60] + 1e-9)
    rh = highs.tail(20).max()
    ph = highs.iloc[-40:-20].max() if len(highs) >= 40 else highs.mean()
    rl = lows.tail(20).min()
    pl = lows.iloc[-40:-20].min() if len(lows) >= 40 else lows.mean()
    base = 50 + (1 if rh > ph else 0)*15 + (1 if rl > pl else 0)*15
    return float(np.clip(base + np.clip(rs*150 + rm*80, -40, 40), 0, 100))

def technical_score(df):
    p, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']
    det = {}

    # ── ADX 선행 계산 (다른 지표에서 활용) ──────
    adx_s, pdi_s, ndi_s = calc_adx(h, l, p)
    adx = float(adx_s.iloc[-1]) if not np.isnan(float(adx_s.iloc[-1])) else 20.0
    pdi = float(pdi_s.iloc[-1]); ndi = float(ndi_s.iloc[-1])
    bull_trend = pdi > ndi
    has_trend = adx > 20

    # ── MA 정렬 (15%) ─────────────────────────
    ma20  = p.rolling(20).mean()
    ma60  = p.rolling(60).mean()
    ma120 = p.rolling(120).mean()
    cp, m20, m60, m120 = float(p.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma120.iloc[-1])
    ma = (20 if cp>m20 else 0)+(20 if cp>m60 else 0)+(20 if cp>m120 else 0)+(20 if m20>m60 else 0)+(20 if m60>m120 else 0)
    if len(ma20) >= 5:
        cross_up = m20 > m60 and float(ma20.iloc[-5]) <= float(ma60.iloc[-5])
        cross_dn = m20 < m60 and float(ma20.iloc[-5]) >= float(ma60.iloc[-5])
        if cross_up and has_trend:
            persist = sum(1 for j in range(-3, 0) if float(ma20.iloc[j]) > float(ma60.iloc[j]))
            ma = min(ma + (15 if persist >= 3 else 5), 100)
        elif cross_dn and has_trend:
            persist = sum(1 for j in range(-3, 0) if float(ma20.iloc[j]) < float(ma60.iloc[j]))
            ma = max(ma - (15 if persist >= 3 else 5), 0)
    det['MA정렬'] = float(ma)

    # ── RSI (10%) — 동적 임계값 ────────────────
    rsi_s = calc_rsi(p)
    rv = float(rsi_s.iloc[-1])
    rsi_ob = 65 if (has_trend and bull_trend and adx > 30) else 70
    rsi_os = 35 if (has_trend and not bull_trend and adx > 30) else 30
    if rsi_os <= rv <= 60:      rsi = 50
    elif 60 < rv <= rsi_ob:     rsi = 75
    elif rv > rsi_ob:           rsi = 40
    elif rsi_os - 10 <= rv < rsi_os: rsi = 30
    else:                        rsi = 60
    rsi_tr = rv - float(rsi_s.iloc[-10]) if len(rsi_s) >= 10 else 0
    if rsi_tr > 5 and rsi_os < rv < rsi_ob: rsi = min(rsi+20, 100)
    elif rsi_tr < -5:                        rsi = max(rsi-10, 0)
    if len(p) >= 20:
        pr20 = float(p.iloc[-20]); rs20 = float(rsi_s.iloc[-20]) if len(rsi_s) >= 20 else rv
        if cp < pr20 and rv > rs20:   rsi = min(rsi+15, 100)
        elif cp > pr20 and rv < rs20: rsi = max(rsi-15, 0)
    det['RSI'] = float(rsi)
    det['RSI값'] = round(rv, 1)

    # ── MACD (13%) ─────────────────────────────
    ml, sl2, hist = calc_macd(p)
    ch  = float(hist.iloc[-1])
    ph2 = float(hist.iloc[-2]) if len(hist) >= 2 else 0
    macd_v = 50
    if float(ml.iloc[-1]) > float(sl2.iloc[-1]): macd_v += 20
    if ch > 0:   macd_v += 15
    if ch > ph2: macd_v += 15
    else:         macd_v -= 10
    # MACD 다이버전스
    if len(p) >= 20 and len(hist) >= 20:
        if cp < float(p.iloc[-20]) and float(hist.iloc[-1]) > float(hist.iloc[-20]):
            macd_v = min(macd_v+12, 100)
        elif cp > float(p.iloc[-20]) and float(hist.iloc[-1]) < float(hist.iloc[-20]):
            macd_v = max(macd_v-12, 0)
    det['MACD'] = float(np.clip(macd_v, 0, 100))

    # ── 볼린저밴드 (10%) ───────────────────────
    bb_u, _, bb_l2 = calc_bb(p)
    rng = float(bb_u.iloc[-1]) - float(bb_l2.iloc[-1]) + 1e-9
    pos = (cp - float(bb_l2.iloc[-1])) / rng
    if 0.4 <= pos <= 0.8:    bb = 70
    elif 0.8 < pos <= 0.95:  bb = 85
    elif pos > 0.95:          bb = 45
    elif 0.2 <= pos < 0.4:   bb = 45
    else:                     bb = 30
    # 밴드 폭 수축(스퀴즈) 탐지 — 돌파 임박
    if len(p) >= 40:
        bw_now = rng / (cp + 1e-9)
        bw_avg = float(((bb_u - bb_l2) / p).rolling(20).mean().iloc[-1])
        if bw_now < bw_avg * 0.7: bb = min(bb+10, 100)   # 스퀴즈 → 돌파 기대
    det['볼린저밴드'] = float(bb)

    # ── 거래량 (7%) ────────────────────────────
    vr  = float(v.iloc[-1]) / (float(v.rolling(20).mean().iloc[-1]) + 1e-9)
    pc  = (cp - float(p.iloc[-5])) / (float(p.iloc[-5]) + 1e-9)
    if pc > 0 and vr > 1.2:   vol = 80
    elif pc > 0 and vr < 0.8: vol = 55
    elif pc < 0 and vr > 1.2: vol = 25
    elif pc < 0 and vr < 0.8: vol = 45
    else:                      vol = 50
    det['거래량'] = float(vol)

    # ── 파동근사 (8%) ──────────────────────────
    det['파동근사'] = wave_score(p, h, l)

    # ── 스토캐스틱 (10%) ───────────────────────
    sk_s, sd_s = calc_stochastic(h, l, p)
    sk, sd = float(sk_s.iloc[-1]), float(sd_s.iloc[-1])
    if sk > 80:   stoch = 35   # 과매수
    elif sk > 60: stoch = 65
    elif sk > 40: stoch = 55
    elif sk > 20: stoch = 60
    else:         stoch = 70   # 과매도 반등 가능
    # %K가 %D 상향 돌파 → 강세 신호
    if len(sk_s) >= 2 and len(sd_s) >= 2:
        if float(sk_s.iloc[-2]) < float(sd_s.iloc[-2]) and sk > sd: stoch = min(stoch+20, 100)
        elif float(sk_s.iloc[-2]) > float(sd_s.iloc[-2]) and sk < sd: stoch = max(stoch-20, 0)
    det['스토캐스틱'] = float(stoch)
    det['Stoch값'] = round(sk, 1)

    # ── ADX 추세강도 (17%) — 이미 선행 계산됨 ───
    if adx > 40:   adx_v = 85 if bull_trend else 15
    elif adx > 25: adx_v = 72 if bull_trend else 28
    elif adx > 18: adx_v = 58 if bull_trend else 42
    else:          adx_v = 50
    det['ADX추세강도'] = float(adx_v)
    det['ADX값'] = round(adx, 1)

    # ── OBV (온밸런스볼륨) (10%) ───────────────
    obv = calc_obv(p, v)
    obv_ma = obv.rolling(20).mean()
    obv_v = 65 if float(obv.iloc[-1]) > float(obv_ma.iloc[-1]) else 35
    # OBV 다이버전스: 가격↓ OBV↑ → 강세 반전 신호
    if len(p) >= 20:
        if cp < float(p.iloc[-20]) and float(obv.iloc[-1]) > float(obv.iloc[-20]):
            obv_v = min(obv_v+20, 100)
        elif cp > float(p.iloc[-20]) and float(obv.iloc[-1]) < float(obv.iloc[-20]):
            obv_v = max(obv_v-20, 0)
    det['OBV'] = float(obv_v)

    # 가중치: MA(15) RSI(10) MACD(13) BB(10) 거래량(7) 파동(8) 스토캐스틱(10) ADX(17) OBV(10)
    total = (det['MA정렬']*0.15 + det['RSI']*0.10 + det['MACD']*0.13 +
             det['볼린저밴드']*0.10 + det['거래량']*0.07 + det['파동근사']*0.08 +
             det['스토캐스틱']*0.10 + det['ADX추세강도']*0.17 + det['OBV']*0.10)
    return float(total), det

# ─────────────────────────────────────────────
# FUNDAMENTAL ANALYSIS
# ─────────────────────────────────────────────

def _score_per(v):
    if not v or np.isnan(v) or v <= 0: return 50
    return 70 if v<5 else (85 if v<15 else (65 if v<25 else (40 if v<40 else 20)))

def _score_per_relative(per, sector_avg):
    """업종 평균 PER 대비 상대적 저평가/고평가 점수"""
    if not per or np.isnan(per) or per <= 0: return 50
    ratio = per / sector_avg
    if ratio < 0.5:   return 90
    elif ratio < 0.7: return 80
    elif ratio < 0.9: return 70
    elif ratio < 1.1: return 60
    elif ratio < 1.3: return 45
    elif ratio < 1.5: return 30
    else:             return 15

def _score_pbr(v):
    if not v or np.isnan(v) or v <= 0: return 50
    return 85 if v<1 else (75 if v<2 else (55 if v<4 else (35 if v<8 else 20)))

def _score_roe(v):
    if v is None or np.isnan(v): return 50
    r = v*100 if abs(v) <= 1 else v
    return 10 if r<0 else (30 if r<5 else (50 if r<10 else (75 if r<20 else (90 if r<30 else 85))))

def _score_growth(v):
    if v is None or np.isnan(v): return 50
    return 10 if v<-0.1 else (30 if v<0 else (50 if v<0.05 else (70 if v<0.15 else (85 if v<0.30 else 95))))

def _score_de(v):
    if v is None or np.isnan(v): return 50
    return 30 if v<0 else (90 if v<0.3 else (75 if v<0.7 else (55 if v<1.5 else (35 if v<3.0 else 15))))

def _score_peg(v):
    if v is None or np.isnan(v) or v <= 0: return 50
    if v < 0.5:   return 95
    elif v < 1.0: return 85
    elif v < 1.5: return 70
    elif v < 2.0: return 55
    elif v < 3.0: return 35
    else:         return 15

def _score_ev_ebitda(v):
    if v is None or np.isnan(v) or v <= 0: return 50
    if v < 6:     return 90
    elif v < 10:  return 80
    elif v < 15:  return 65
    elif v < 20:  return 50
    elif v < 30:  return 30
    else:         return 15

def _score_fcf_yield(pct):
    if pct is None or np.isnan(pct): return 50
    if pct > 10:  return 90
    elif pct > 6: return 80
    elif pct > 3: return 65
    elif pct > 1: return 50
    elif pct > 0: return 35
    else:         return 15

def _score_int_coverage(v):
    if v is None or np.isnan(v): return 50
    if v > 15:   return 95
    elif v > 8:  return 80
    elif v > 4:  return 65
    elif v > 2:  return 45
    elif v > 1:  return 25
    else:        return 10

def calc_mdd(prices):
    p = prices[prices > 0].dropna()
    if p.empty:
        return 0.0
    roll_max = p.expanding().max()
    return float(((p - roll_max) / roll_max * 100).min())

def _score_mdd(m):
    return 90 if m>-10 else (75 if m>-20 else (55 if m>-30 else (35 if m>-40 else (20 if m>-50 else 10))))

def _score_52w_position(cp, high52, low52):
    """52주 고저 대비 현재가 위치 점수 (중간대가 가장 좋음)"""
    if high52 <= low52 or high52 == 0: return 50
    pos = (cp - low52) / (high52 - low52) * 100
    if pos < 20:   return 40   # 52주 저점 근처 - 낙폭과대 or 하락추세
    elif pos < 35: return 60
    elif pos < 50: return 70
    elif pos < 65: return 80   # 중간~상단 스윗스팟
    elif pos < 80: return 75
    elif pos < 90: return 60
    else:          return 45   # 52주 고점 근처 - 과매수 우려

def _get_fs_val(df, *kw, col=0):
    if df is None or df.empty: return None
    for idx in df.index:
        if all(k.lower() in str(idx).lower() for k in kw):
            try:
                v = df.iloc[df.index.get_loc(idx), col]
                return float(v) if not pd.isna(v) else None
            except: pass
    return None

def calc_piotroski_fscore(ticker):
    try:
        t   = yf.Ticker(ticker)
        info= t.info
        fin, bal, cf = t.financials, t.balance_sheet, t.cashflow
        score, sig = 0, {}
        gv = _get_fs_val

        roa = info.get('returnOnAssets')
        if roa and roa > 0: score+=1; sig['F1 ROA>0']='✅'
        else:                           sig['F1 ROA>0']='❌'

        ocf = gv(cf,'operating') or gv(cf,'cash','operation')
        if ocf and ocf > 0: score+=1; sig['F2 영업현금흐름>0']='✅'
        else:                           sig['F2 영업현금흐름>0']='❌'

        ni_c=gv(fin,'net income');    ni_p=gv(fin,'net income',col=1)
        ta_c=gv(bal,'total assets');  ta_p=gv(bal,'total assets',col=1)
        roa_c = ni_c/ta_c if ni_c is not None and ta_c else None
        roa_p = ni_p/ta_p if ni_p is not None and ta_p else None
        if roa_c is not None and roa_p is not None:
            if roa_c > roa_p: score+=1; sig['F3 ROA개선']='✅'
            else:              sig['F3 ROA개선']='❌'
        else: sig['F3 ROA개선']='❓'

        if ocf is not None and ta_c and roa_c is not None:
            if ocf/ta_c > roa_c: score+=1; sig['F4 발생주의']='✅'
            else:                  sig['F4 발생주의']='❌'
        else: sig['F4 발생주의']='❓'

        ltd_c=gv(bal,'long','debt');  ltd_p=gv(bal,'long','debt',col=1)
        lev_c = ltd_c/ta_c if ltd_c is not None and ta_c else None
        lev_p = ltd_p/ta_p if ltd_p is not None and ta_p else None
        if lev_c is not None and lev_p is not None:
            if lev_c < lev_p: score+=1; sig['F5 레버리지감소']='✅'
            else:              sig['F5 레버리지감소']='❌'
        else: sig['F5 레버리지감소']='❓'

        ca_c=gv(bal,'current assets');  cl_c=gv(bal,'current liabilities')
        ca_p=gv(bal,'current assets',col=1); cl_p=gv(bal,'current liabilities',col=1)
        cr_c = ca_c/cl_c if ca_c is not None and cl_c else None
        cr_p = ca_p/cl_p if ca_p is not None and cl_p else None
        if cr_c is not None and cr_p is not None:
            if cr_c > cr_p: score+=1; sig['F6 유동성개선']='✅'
            else:            sig['F6 유동성개선']='❌'
        else: sig['F6 유동성개선']='❓'

        sh_c = gv(bal,'ordinary shares') or gv(bal,'common stock')
        sh_p = gv(bal,'ordinary shares',col=1) or gv(bal,'common stock',col=1)
        if sh_c and sh_p:
            if sh_c <= sh_p*1.01: score+=1; sig['F7 주식수불증가']='✅'
            else:                   sig['F7 주식수불증가']='❌'
        else: sig['F7 주식수불증가']='❓'

        rev_c=gv(fin,'total revenue'); rev_p=gv(fin,'total revenue',col=1)
        gp_c =gv(fin,'gross profit');  gp_p =gv(fin,'gross profit',col=1)
        gm_c = gp_c/rev_c if gp_c is not None and rev_c else None
        gm_p = gp_p/rev_p if gp_p is not None and rev_p else None
        if gm_c is not None and gm_p is not None:
            if gm_c > gm_p: score+=1; sig['F8 매출총이익률개선']='✅'
            else:            sig['F8 매출총이익률개선']='❌'
        else: sig['F8 매출총이익률개선']='❓'

        at_c = rev_c/ta_c if rev_c is not None and ta_c else None
        at_p = rev_p/ta_p if rev_p is not None and ta_p else None
        if at_c is not None and at_p is not None:
            if at_c > at_p: score+=1; sig['F9 자산회전율개선']='✅'
            else:            sig['F9 자산회전율개선']='❌'
        else: sig['F9 자산회전율개선']='❓'

        return score, sig
    except Exception as e:
        return None, {'오류': str(e)}

def fundamental_score(ticker, df=None):
    try:
        info = yf.Ticker(ticker).info
        _pit_snapshot(ticker, info)
        det  = {}

        # ── 밸류에이션 (20%): PER·PBR·PEG·EV/EBITDA ──
        per      = info.get('trailingPE') or info.get('forwardPE')
        pbr      = info.get('priceToBook')
        peg      = info.get('pegRatio')
        ev_ebitda= info.get('enterpriseToEbitda')
        sector   = info.get('sector', '')
        sector_avg = SECTOR_AVG_PER.get(sector, 20)
        per_s  = _score_per(per)*0.4 + _score_per_relative(per, sector_avg)*0.6
        det['밸류에이션'] = per_s*0.35 + _score_pbr(pbr)*0.25 + _score_peg(peg)*0.20 + _score_ev_ebitda(ev_ebitda)*0.20
        det['PER'] = per; det['PBR'] = pbr; det['PEG'] = peg; det['EV/EBITDA'] = ev_ebitda
        det['업종'] = sector or 'N/A'; det['업종평균PER'] = sector_avg

        # ── 수익성 (20%): ROE·ROA·순이익률 ──────────
        roe = info.get('returnOnEquity')
        roa = info.get('returnOnAssets')
        pm  = info.get('profitMargins')
        pm_s = 50
        if pm:
            pp = pm*100
            pm_s = 10 if pp<0 else (40 if pp<5 else (60 if pp<10 else (80 if pp<20 else 90)))
        det['수익성'] = _score_roe(roe)*0.4 + _score_roe(roa*300 if roa is not None else None)*0.3 + pm_s*0.3
        det['ROE'] = roe; det['ROA'] = roa; det['순이익률'] = pm

        # ── 성장성 (13%) ──────────────────────────────
        rg = info.get('revenueGrowth'); eg = info.get('earningsGrowth')
        det['성장성'] = _score_growth(rg)*0.5 + _score_growth(eg)*0.5 if eg is not None else _score_growth(rg)
        det['매출성장'] = rg; det['EPS성장'] = eg

        # ── FCF 품질 (12%): FCF수익률 ─────────────────
        fcf  = info.get('freeCashflow')
        mcap = info.get('marketCap')
        fcf_yield = (fcf / mcap * 100) if (fcf and mcap and mcap > 0) else None
        det['FCF품질'] = float(_score_fcf_yield(fcf_yield))
        det['FCF수익률'] = fcf_yield

        # ── 안전성 (10%): D/E·유동비율·이자보상배율 ──
        de  = info.get('debtToEquity');  cr = info.get('currentRatio')
        ebit    = info.get('ebit');      int_exp = info.get('interestExpense')
        de_s  = _score_de(de/100 if de is not None else None)
        cr_s  = 50
        if cr: cr_s = 10 if cr<0.5 else (30 if cr<1.0 else (60 if cr<1.5 else (85 if cr<3.0 else 75)))
        int_cov = (ebit/abs(int_exp)) if (ebit and int_exp and int_exp != 0) else None
        ic_s  = _score_int_coverage(int_cov)
        det['안전성'] = de_s*0.45 + cr_s*0.30 + ic_s*0.25
        det['D/E'] = de; det['유동비율'] = cr; det['이자보상배율'] = int_cov

        # ── MDD (8%) ──────────────────────────────────
        mdd_v = calc_mdd(df['Close']) if df is not None else None
        det['MDD']  = float(_score_mdd(mdd_v)) if mdd_v is not None else 50.0
        det['MDD값'] = mdd_v

        # ── F-Score (10%) ─────────────────────────────
        fs, fsig = calc_piotroski_fscore(ticker)
        det['F-Score']      = float(fs/9*100) if fs is not None else 50.0
        det['F-Score값']    = fs
        det['F-Score시그널'] = fsig

        # ── 52주 위치 (7%) ────────────────────────────
        if df is not None and len(df) >= 30:
            cp52 = float(df['Close'].iloc[-1])
            h52  = float(df['High'].tail(252).max())
            l52  = float(df['Low'].tail(252).min())
            det['52주위치'] = float(_score_52w_position(cp52, h52, l52))
            det['52주고가'] = h52; det['52주저가'] = l52
        else:
            det['52주위치'] = 50.0

        # 가중치: 밸류에이션20 수익성20 성장성13 FCF품질12 안전성10 MDD8 F-Score10 52주위치7
        total = (det['밸류에이션']*0.20 + det['수익성']*0.20 + det['성장성']*0.13 +
                 det['FCF품질']*0.12    + det['안전성']*0.10  + det['MDD']*0.08 +
                 det['F-Score']*0.10    + det['52주위치']*0.07)
        return float(total), det
    except Exception as e:
        return 50.0, {'오류': str(e)}

# ─────────────────────────────────────────────
# MACRO & INTEREST RATE
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600)
def get_market_regime():
    """SPY vs MA200 기반 시장 국면 감지 (bull/bear/neutral)"""
    try:
        end = datetime.now(); start = end - timedelta(days=310)
        spy = yf.download('SPY', start=start, end=end, progress=False)
        if isinstance(spy.columns, pd.MultiIndex): spy.columns = spy.columns.droplevel(1)
        spy = spy.dropna(subset=['Close'])
        if len(spy) < 200: return 'neutral', 0.0
        cp = float(spy['Close'].iloc[-1])
        ma200 = float(spy['Close'].rolling(200).mean().iloc[-1])
        diff_pct = (cp - ma200) / ma200 * 100
        if cp > ma200 * 1.03:   return 'bull', diff_pct
        elif cp < ma200 * 0.97: return 'bear', diff_pct
        else:                   return 'neutral', diff_pct
    except:
        return 'neutral', 0.0

@st.cache_data(ttl=3600)
def macro_score():
    det, data = {}, {}
    end = datetime.now(); start = end - timedelta(days=400)
    try:
        tnx = yf.download('^TNX',     start=start, end=end, progress=False)
        fvx = yf.download('^FVX',     start=start, end=end, progress=False)
        vix = yf.download('^VIX',     start=start, end=end, progress=False)
        dxy = yf.download('DX-Y.NYB', start=start, end=end, progress=False)
        hyg = yf.download('HYG',      start=start, end=end, progress=False)  # 하이일드 채권
        lqd = yf.download('LQD',      start=start, end=end, progress=False)  # 투자등급 채권
        gld = yf.download('GLD',      start=start, end=end, progress=False)  # 금(인플레/공포)
        for d in [tnx, fvx, vix, dxy, hyg, lqd, gld]:
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.droplevel(1)
            d.dropna(subset=['Close'], inplace=True)

        # ── 금리환경 (28%) ────────────────────────────
        if len(tnx) >= 60:
            cr = float(tnx['Close'].iloc[-1]); r3 = float(tnx['Close'].iloc[-60]); chg = cr-r3
            data['10Y금리'] = cr; data['3M금리변화'] = chg
            lvl = 85 if cr<2 else (75 if cr<3 else (60 if cr<4 else (45 if cr<5 else 30)))
            tr  = 80 if chg<-0.3 else (65 if chg<0 else (50 if chg<0.3 else (35 if chg<0.8 else 20)))
            det['금리환경'] = lvl*0.4 + tr*0.6
        else: det['금리환경'] = 50.0

        # ── 장단기금리차 (20%) ────────────────────────
        if len(tnx) > 0 and len(fvx) > 0:
            sp = float(tnx['Close'].iloc[-1]) - float(fvx['Close'].iloc[-1]); data['장단기스프레드'] = sp
            det['장단기금리차'] = 80 if sp>1.5 else (70 if sp>0.5 else (50 if sp>=0 else (35 if sp>-0.5 else 20)))
        else: det['장단기금리차'] = 50.0

        # ── VIX 공포지수 (20%) ────────────────────────
        if len(vix) >= 20:
            cv = float(vix['Close'].iloc[-1]); av = float(vix['Close'].tail(30).mean()); data['VIX'] = cv
            vs = 75 if cv<15 else (70 if cv<20 else (55 if cv<25 else (35 if cv<35 else 20)))
            if cv-av < -3: vs = min(vs+15, 100)
            elif cv-av > 5: vs = max(vs-15, 0)
            det['VIX'] = float(vs)
        else: det['VIX'] = 50.0

        # ── 달러지수 (12%) ────────────────────────────
        if len(dxy) >= 60:
            cd = float(dxy['Close'].iloc[-1]); d3 = float(dxy['Close'].iloc[-60])
            cp2 = (cd-d3)/d3*100; data['DXY'] = cd
            det['달러지수'] = 70 if cp2<-3 else (60 if cp2<0 else (50 if cp2<3 else (40 if cp2<6 else 30)))
        else: det['달러지수'] = 50.0

        # ── 신용스프레드 HYG/LQD (12%) ───────────────
        # HYG(하이일드)가 LQD(투자등급) 대비 부진 → 신용위험 확대 → 주식에 부정적
        if len(hyg) >= 60 and len(lqd) >= 60:
            hyg_r = (float(hyg['Close'].iloc[-1]) - float(hyg['Close'].iloc[-60])) / float(hyg['Close'].iloc[-60]) * 100
            lqd_r = (float(lqd['Close'].iloc[-1]) - float(lqd['Close'].iloc[-60])) / float(lqd['Close'].iloc[-60]) * 100
            spread_diff = hyg_r - lqd_r  # 양수 = 하이일드 강세 = 신용 환경 양호
            data['신용스프레드(HYG-LQD)'] = round(spread_diff, 2)
            det['신용스프레드'] = (80 if spread_diff > 2 else (70 if spread_diff > 0
                                    else (50 if spread_diff > -2 else (35 if spread_diff > -5 else 20))))
        else: det['신용스프레드'] = 50.0

        # ── 원자재/인플레 GLD (8%) ────────────────────
        # 금 급등 = 인플레 압력 or 공포 심리 → 주식에 약세
        if len(gld) >= 60:
            cg = float(gld['Close'].iloc[-1]); g3 = float(gld['Close'].iloc[-60])
            gld_chg = (cg - g3) / g3 * 100; data['GLD변화(3M)'] = round(gld_chg, 2)
            # 금 완만한 상승은 중립, 급등은 부정적
            det['원자재/인플레'] = (65 if gld_chg < 0 else (60 if gld_chg < 5
                                      else (50 if gld_chg < 10 else (40 if gld_chg < 20 else 25))))
        else: det['원자재/인플레'] = 50.0

        # 가중치: 금리환경28 장단기금리차20 VIX20 달러지수12 신용스프레드12 원자재/인플레8
        total = (det['금리환경']*0.28 + det['장단기금리차']*0.20 + det['VIX']*0.20 +
                 det['달러지수']*0.12  + det['신용스프레드']*0.12 + det['원자재/인플레']*0.08)
        return float(total), det, data
    except Exception as e:
        return 50.0, {'오류': str(e)}, {}

# ─────────────────────────────────────────────
# SCREENER
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# BACKTESTING
# ─────────────────────────────────────────────

def bt_signals(df):
    p = df['Close']
    ma20 = p.rolling(20).mean(); ma60 = p.rolling(60).mean()
    ma_s = pd.Series(0.0, index=p.index)
    ma_s[p > ma20] += 30; ma_s[p > ma60] += 20; ma_s[ma20 > ma60] += 50

    rsi = calc_rsi(p)
    rsi_s = pd.Series(50.0, index=p.index)
    rsi_s[(rsi >= 40) & (rsi <= 70)] = 65
    rsi_s[rsi > 70] = 35
    rsi_s[(rsi >= 30) & (rsi < 40)] = 40
    rsi_s[rsi < 30] = 60

    ml, sl2, hist = calc_macd(p)
    macd_s = pd.Series(40.0, index=p.index)
    macd_s[ml > sl2] = 65
    macd_s[(ml > sl2) & (hist > 0) & (hist > hist.shift(1))] = 80
    macd_s[(ml < sl2) & (hist < 0)] = 20

    return (ma_s*0.40 + rsi_s*0.30 + macd_s*0.30).fillna(50)

def bt_signals_full(df):
    """technical_score 9개 지표를 전체 기간 벡터화 계산.
    bt_signals(MA+RSI+MACD 3개) 대신 실제 scoring과 동일한 9개 지표를 사용."""
    p, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']

    # ── ADX 선행 계산 ──────────────────────────
    adx_v_bt, pdi_bt, ndi_bt = calc_adx(h, l, p)
    adx_f_bt = adx_v_bt.fillna(0)
    has_trend_bt = adx_f_bt > 20

    # ── MA 정렬 (15%) — ADX 필터 + 3봉 지속 확인
    ma20, ma60, ma120 = p.rolling(20).mean(), p.rolling(60).mean(), p.rolling(120).mean()
    ma_s = ((p > ma20)*20 + (p > ma60)*20 + (p > ma120)*20 +
            (ma20 > ma60)*20 + (ma60 > ma120)*20).astype(float)
    gc = (ma20 > ma60) & (ma20.shift(5) <= ma60.shift(5))
    dc = (ma20 < ma60) & (ma20.shift(5) >= ma60.shift(5))
    gc_persist = gc & (ma20.shift(1) > ma60.shift(1)) & (ma20.shift(2) > ma60.shift(2))
    dc_persist = dc & (ma20.shift(1) < ma60.shift(1)) & (ma20.shift(2) < ma60.shift(2))
    gc_bonus = gc_persist & has_trend_bt
    dc_bonus = dc_persist & has_trend_bt
    gc_weak = gc & ~gc_bonus
    dc_weak = dc & ~dc_bonus
    ma_s = (ma_s + gc_bonus.astype(float)*15 - dc_bonus.astype(float)*15
                 + gc_weak.astype(float)*5  - dc_weak.astype(float)*5).clip(0, 100).fillna(50)

    # ── RSI (10%) ──────────────────────────────
    rsi = calc_rsi(p)
    rs  = pd.Series(50.0, index=p.index)
    rs[(rsi > 60) & (rsi <= 70)] = 75
    rs[rsi > 70]                 = 40
    rs[(rsi >= 30) & (rsi < 40)] = 30
    rs[rsi < 30]                 = 60
    rs_b = pd.Series(0.0, index=p.index)
    rsi_tr = rsi - rsi.shift(10)
    rs_b[(rsi_tr > 5)  & (rsi > 40) & (rsi < 70)] = 20
    rs_b[rsi_tr < -5]                              = -10
    pr20 = p.shift(20); rsi20 = rsi.shift(20)
    rs_b[(p < pr20) & (rsi > rsi20)] += 15
    rs_b[(p > pr20) & (rsi < rsi20)] -= 15
    rs = (rs + rs_b).clip(0, 100).fillna(50)

    # ── MACD (13%) ─────────────────────────────
    ml, sl2, hist = calc_macd(p)
    ms   = pd.Series(50.0, index=p.index)
    ms_b = pd.Series(0.0, index=p.index)
    ms_b[ml > sl2]              += 20
    ms_b[hist > 0]               += 15
    ms_b[hist > hist.shift(1)]   += 15
    ms_b[hist <= hist.shift(1)]  -= 10
    pr20m = p.shift(20)
    ms_b[(p < pr20m) & (hist > hist.shift(20))] += 12
    ms_b[(p > pr20m) & (hist < hist.shift(20))] -= 12
    ms = (ms + ms_b).clip(0, 100).fillna(50)

    # ── 볼린저밴드 (10%) ───────────────────────
    bb_u, _, bb_l = calc_bb(p)
    rng = (bb_u - bb_l).clip(lower=1e-9)
    pos = (p - bb_l) / rng
    bs  = pd.Series(50.0, index=p.index)
    bs[(pos >= 0.4) & (pos <= 0.8)] = 70
    bs[(pos > 0.8)  & (pos <= 0.95)]= 85
    bs[pos > 0.95]                  = 45
    bs[(pos >= 0.2) & (pos < 0.4)]  = 45
    bs[pos < 0.2]                   = 30
    bw_now = rng / p.clip(lower=1e-9)
    bw_avg = bw_now.rolling(20).mean()
    bs_b = pd.Series(0.0, index=p.index)
    bs_b[bw_now < bw_avg * 0.7] = 10
    bs = (bs + bs_b).clip(0, 100).fillna(50)

    # ── 거래량 (7%) ────────────────────────────
    vma20 = v.rolling(20).mean().clip(lower=1e-9)
    vr, pc5 = v / vma20, p.pct_change(5)
    vs = pd.Series(50.0, index=p.index)
    vs[(pc5 > 0) & (vr > 1.2)] = 80
    vs[(pc5 > 0) & (vr < 0.8)] = 55
    vs[(pc5 < 0) & (vr > 1.2)] = 25
    vs[(pc5 < 0) & (vr < 0.8)] = 45
    vs = vs.fillna(50)

    # ── 파동근사 (8%) ──────────────────────────
    rh20  = h.rolling(20).max()
    ph20  = h.shift(20).rolling(20).max()
    rl20  = l.rolling(20).min()
    pl20  = l.shift(20).rolling(20).min()
    rs20v = (p - p.shift(19)) / (p.shift(19) + 1e-9)
    rm60v = (p - p.shift(59)) / (p.shift(59) + 1e-9)
    wave_base = (50 + (rh20 > ph20).astype(float)*15 +
                     (rl20 > pl20).astype(float)*15)
    wave_s = (wave_base + (rs20v*150 + rm60v*80).clip(-40, 40)).clip(0, 100).fillna(50)

    # ── 스토캐스틱 (10%) ───────────────────────
    sk_s, sd_s = calc_stochastic(h, l, p)
    sts  = pd.Series(50.0, index=p.index)
    sts[sk_s > 80]                 = 35
    sts[(sk_s > 60) & (sk_s <= 80)]= 65
    sts[(sk_s > 40) & (sk_s <= 60)]= 55
    sts[(sk_s > 20) & (sk_s <= 40)]= 60
    sts[sk_s <= 20]                = 70
    sts_b = pd.Series(0.0, index=p.index)
    k_up   = (sk_s.shift(1) < sd_s.shift(1)) & (sk_s >= sd_s)
    k_down = (sk_s.shift(1) > sd_s.shift(1)) & (sk_s <  sd_s)
    sts_b[k_up]   =  20
    sts_b[k_down] = -20
    sts = (sts + sts_b).clip(0, 100).fillna(50)

    # ── ADX (17%) ──────────────────────────────
    adx_v, pdi, ndi = calc_adx(h, l, p)
    adx_f = adx_v.fillna(0)
    bull  = (pdi > ndi).fillna(False)
    ads   = pd.Series(50.0, index=p.index)
    ads[(adx_f > 40) &  bull]                  = 85
    ads[(adx_f > 40) & ~bull]                  = 15
    ads[(adx_f > 25) & (adx_f <= 40) &  bull]  = 72
    ads[(adx_f > 25) & (adx_f <= 40) & ~bull]  = 28
    ads[(adx_f > 18) & (adx_f <= 25) &  bull]  = 58
    ads[(adx_f > 18) & (adx_f <= 25) & ~bull]  = 42

    # ── OBV (10%) ──────────────────────────────
    obv    = calc_obv(p, v)
    obv_ma = obv.rolling(20).mean()
    obv_s  = pd.Series(35.0, index=p.index)
    obv_s[obv > obv_ma] = 65
    obv_b  = pd.Series(0.0, index=p.index)
    pr20o  = p.shift(20); obv20 = obv.shift(20)
    obv_b[(p < pr20o) & (obv > obv20)] =  20
    obv_b[(p > pr20o) & (obv < obv20)] = -20
    obv_s  = (obv_s + obv_b).clip(0, 100).fillna(50)

    # 가중 합산 (technical_score 동일 가중치)
    total = (ma_s*0.15 + rs*0.10 + ms*0.13 + bs*0.10 + vs*0.07 +
             wave_s*0.08 + sts*0.10 + ads*0.17 + obv_s*0.10)
    return total.fillna(50)


def run_backtest(df, buy_th=65, sell_th=45, initial_capital=10_000_000,
                 commission=0.0005, slippage=0.0003,
                 f_score=None, m_score=None,
                 w_tech=100, w_fund=0, w_macro=0):
    """수수료·슬리피지 반영 백테스트.
    f_score/m_score 전달 시 펀더멘털/매크로로 임계값을 보정.
    좋은 펀더멘털 → 매수 조건 완화, 나쁜 펀더멘털 → 매수 조건 강화.

    [수정: look-ahead bias 제거]
    - 신호(sig)는 D일 종가까지의 데이터로 D일에 "확정"된다.
    - 과거 코드는 그 신호가 확정되는 바로 그 D일 종가로 체결했는데,
      이는 "장이 마감돼야 아는 정보로 마감 시점에 거래한다"는
      실현 불가능한 가정이라 수익률이 실제보다 부풀려진다.
    - 수정 후: D일에 신호가 뜨면 다음날(D+1) 시가(Open)로 체결한다.
      (Open 컬럼이 없으면 D+1 종가로 폴백)

    [주의: f_score/m_score 관련 look-ahead 위험]
    - f_score/m_score가 "현재 시점 스냅샷"(예: yfinance의 최신 재무데이터)이면
      과거 전체 구간에 동일한 미래 정보가 고정 적용되어 lookahead bias가 생긴다.
    - 라이브 매매(오늘 신호 계산)에 쓰는 건 문제없지만,
      과거 성과를 "검증"하는 목적이라면 w_fund=0, w_macro=0으로 두고
      순수 기술적 백테스트 결과만 신뢰할 것.
    """
    sigs = bt_signals_full(df)
    if f_score is not None and m_score is not None and w_tech < 100:
        fm_offset = (f_score - 50) * (w_fund / 100) + (m_score - 50) * (w_macro / 100)
        buy_th  = buy_th  - fm_offset  # 재무 좋으면 진입 조건 완화
        sell_th = sell_th + fm_offset  # 재무 좋으면 청산 조건 강화
    prices = df['Close'].values
    opens  = df['Open'].values if 'Open' in df.columns else None
    dates  = df.index
    n      = len(df)

    capital   = float(initial_capital)
    shares    = 0.0
    in_pos    = False
    entry_val = 0.0   # 매수 시점 투입 자본 (수수료·슬리피지 후)
    equity    = np.full(n, float(initial_capital))
    trades    = []
    pending   = None   # 'buy' / 'sell' — 전날 신호로 오늘 체결 대기중인 주문

    for i in range(20, n):
        px = float(prices[i])   # 시가평가(마킹)에는 계속 종가 사용

        # ── 1) 전날 확정된 신호를 "오늘" 체결 (다음날 시가 기준) ──
        exec_px = float(opens[i]) if opens is not None and not np.isnan(opens[i]) else px
        if pending == 'buy' and not in_pos:
            buy_px    = exec_px * (1 + slippage)
            fee       = capital * commission
            shares    = (capital - fee) / buy_px
            entry_val = capital
            capital   = 0.0
            in_pos    = True
            trades.append({'날짜': dates[i], '구분': '🟢 매수',
                           '가격': round(exec_px, 2), '체결가(수수료+슬리피지)': round(buy_px*(1+commission/(1+slippage+1e-9)), 2),
                           '신호': round(float(sigs.iloc[i-1]), 1), '수익률': ''})
        elif pending == 'sell' and in_pos:
            sell_px  = exec_px * (1 - slippage)
            gross    = shares * sell_px
            fee      = gross * commission
            net      = gross - fee
            pnl      = (net / entry_val - 1) * 100
            capital  = net
            shares   = 0.0
            in_pos   = False
            trades.append({'날짜': dates[i], '구분': '🔴 매도',
                           '가격': round(exec_px, 2), '체결가(수수료+슬리피지)': round(sell_px*(1-commission), 2),
                           '신호': round(float(sigs.iloc[i-1]), 1), '수익률': f"{pnl:+.2f}%"})
        pending = None

        # ── 2) 오늘 종가로 신호 계산 → 체결은 "내일"로 예약 ──
        sig = float(sigs.iloc[i])
        if not in_pos and sig > buy_th:
            pending = 'buy'
        elif in_pos and sig < sell_th:
            pending = 'sell'

        equity[i] = capital + shares * px

    final_v = float(equity[-1])
    days    = (dates[-1] - dates[20]).days
    years   = max(days / 365, 0.01)
    bh_ret  = (float(prices[-1]) - float(prices[20])) / float(prices[20]) * 100
    tot_ret = (final_v - initial_capital) / initial_capital * 100
    cagr    = ((final_v / initial_capital) ** (1 / years) - 1) * 100

    eq_s      = pd.Series(equity).replace(0, np.nan).ffill()
    roll_max  = eq_s.expanding().max()
    dd_series = (eq_s - roll_max) / roll_max * 100
    mdd       = float(dd_series.min())
    daily_ret = eq_s.pct_change().dropna().iloc[19:]  # 워밍업 0수익률 19개 제외
    sharpe    = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    calmar    = cagr / abs(mdd) if mdd < 0 else 0.0

    sells = [t for t in trades if '매도' in t['구분']]
    pnls  = []
    for t in sells:
        try: pnls.append(float(t['수익률'].replace('%', '').replace('+', '')))
        except: pass
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    avg_win  = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float('inf')

    cost_drag = (commission + slippage) * 2 * len(sells) * 100  # 총 비용 부담률(%)

    metrics = {
        '전략 수익률':    f"{tot_ret:+.1f}%",
        '매수보유 수익률': f"{bh_ret:+.1f}%",
        'CAGR':           f"{cagr:+.1f}%",
        '최대낙폭(MDD)':  f"{mdd:.1f}%",
        'Sharpe Ratio':   f"{sharpe:.2f}",
        'Calmar Ratio':   f"{calmar:.2f}",
        'Profit Factor':  f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞",
        '총 매매':        f"{len(sells)}회",
        '승률':           f"{win_rate:.1f}%",
        '평균 수익':      f"{avg_win:+.2f}%",
        '평균 손실':      f"{avg_loss:+.2f}%",
        '비용 부담':      f"{cost_drag:.2f}%",
    }

    bh_eq      = np.full(n, float(initial_capital))
    bh_eq[20:] = (df['Close'].iloc[20:].values / float(prices[20])) * initial_capital
    eq_df      = pd.DataFrame({'날짜': dates, '전략': equity, '매수보유': bh_eq})
    return metrics, eq_df, pd.DataFrame(trades)



def run_portfolio_backtest(tickers, weights, period_days, buy_th, sell_th,
                           initial_capital, commission, slippage,
                           w_tech=100, w_fund=0, w_macro=0):
    """멀티 종목 포트폴리오 백테스트.
    각 종목에 weight 비율만큼 자본 배분 후 개별 run_backtest 실행,
    포트폴리오 전체 자산 곡선과 합산 지표를 반환."""
    end   = datetime.now()
    start = end - timedelta(days=period_days + 60)
    pf_m_score, _, _ = macro_score() if (w_fund > 0 or w_macro > 0) else (None, {}, {})

    eq_combined = None  # 포트폴리오 합산 equity
    results = []        # 종목별 결과

    for tk, wt in zip(tickers, weights):
        alloc = initial_capital * wt
        try:
            raw = download_stock(tk, start=start, end=end)
            raw = raw.dropna(subset=['Close'])
            if len(raw) < 60:
                results.append({'ticker': tk, 'weight': wt, 'error': '데이터 부족'})
                continue
            pf_f_score = None
            if w_fund > 0 or w_macro > 0:
                try:
                    pf_f_score, _ = fundamental_score(tk, raw)
                except Exception:
                    pass
            m, eq_df, _ = run_backtest(raw, buy_th, sell_th, alloc, commission, slippage,
                                       f_score=pf_f_score, m_score=pf_m_score,
                                       w_tech=w_tech, w_fund=w_fund, w_macro=w_macro)
            results.append({'ticker': tk, 'weight': wt, 'metrics': m, 'eq_df': eq_df, 'error': None})

            # 공통 날짜 인덱스로 합산
            eq_s = eq_df.set_index('날짜')['전략']
            if eq_combined is None:
                eq_combined = eq_s.copy()
            else:
                eq_combined = eq_combined.add(eq_s, fill_value=0)
        except Exception as e:
            results.append({'ticker': tk, 'weight': wt, 'error': str(e)[:40]})

    # SPY 벤치마크 (같은 기간)
    spy_eq = None
    try:
        spy_raw = yf.download('SPY', start=start, end=end, progress=False)
        if isinstance(spy_raw.columns, pd.MultiIndex):
            spy_raw.columns = spy_raw.columns.droplevel(1)
        spy_raw = spy_raw.dropna(subset=['Close'])
        spy_first = float(spy_raw['Close'].iloc[20])
        spy_eq    = (spy_raw['Close'].iloc[20:] / spy_first) * initial_capital
        spy_eq.index.name = '날짜'
    except Exception:
        pass

    return results, eq_combined, spy_eq


def calc_sector_relative(ticker, sector, df):
    """종목 vs 섹터 ETF vs SPY 상대 강도 분석.
    Returns dict: horizons × {ticker_ret, etf_ret, spy_ret, rs_vs_etf, rs_vs_spy}
    """
    etf = SECTOR_ETF.get(sector, '')
    end = datetime.now()
    start = end - timedelta(days=200)
    tickers_to_dl = ['SPY'] + ([etf] if etf else [])

    try:
        bench = yf.download(tickers_to_dl, start=start, end=end, progress=False)
        if isinstance(bench.columns, pd.MultiIndex):
            spy_close = bench['Close']['SPY'].dropna()
            etf_close = bench['Close'][etf].dropna() if etf else None
        else:
            spy_close = bench['Close'].dropna() if 'SPY' in tickers_to_dl else None
            etf_close = None
    except Exception:
        return {}

    stock_close = df['Close']
    results = {}
    for label, days in [('1개월', 21), ('3개월', 63), ('6개월', 126)]:
        try:
            n = min(days, len(stock_close) - 1)
            tk_ret  = (float(stock_close.iloc[-1]) / float(stock_close.iloc[-n]) - 1) * 100
            spy_ret = (float(spy_close.iloc[-1])   / float(spy_close.iloc[-n])   - 1) * 100 if spy_close is not None and len(spy_close) >= n else None
            etf_ret = (float(etf_close.iloc[-1])   / float(etf_close.iloc[-n])   - 1) * 100 if etf_close is not None and len(etf_close) >= n else None
            results[label] = {
                'tk_ret':  tk_ret,
                'etf_ret': etf_ret,
                'spy_ret': spy_ret,
                'rs_etf':  (tk_ret - etf_ret) if etf_ret is not None else None,
                'rs_spy':  (tk_ret - spy_ret) if spy_ret is not None else None,
            }
        except Exception:
            continue
    return {'data': results, 'etf': etf, 'sector': sector}


def regime_adjusted_technical(t_det, regime='neutral'):
    """t_det 컴포넌트 점수를 국면별 가중치로 재계산."""
    w = REGIME_TECH_WEIGHTS.get(regime, REGIME_TECH_WEIGHTS['neutral'])
    return float(
        t_det.get('MA정렬',50)*w['MA정렬'] + t_det.get('RSI',50)*w['RSI'] +
        t_det.get('MACD',50)*w['MACD'] + t_det.get('볼린저밴드',50)*w['볼린저밴드'] +
        t_det.get('거래량',50)*w['거래량'] + t_det.get('파동근사',50)*w['파동근사'] +
        t_det.get('스토캐스틱',50)*w['스토캐스틱'] + t_det.get('ADX추세강도',50)*w['ADX추세강도'] +
        t_det.get('OBV',50)*w['OBV'])


def calc_indicator_ics(df, horizon=20):
    """9개 지표 각각의 IC(정보계수)를 독립 계산 — 어떤 지표가 실제로 예측력이 있는지 측정."""
    p, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']
    fwd = p.pct_change(horizon).shift(-horizon) * 100

    def _ic(series):
        c = pd.DataFrame({'s': series, 'f': fwd}).dropna().iloc[20:]
        return float(c['s'].corr(c['f'])) if len(c) > 20 else 0.0

    ma20, ma60, ma120 = p.rolling(20).mean(), p.rolling(60).mean(), p.rolling(120).mean()
    ma_s = ((p>ma20)*20+(p>ma60)*20+(p>ma120)*20+(ma20>ma60)*20+(ma60>ma120)*20).astype(float)

    rsi = calc_rsi(p)
    rs  = pd.Series(50.0, index=p.index)
    rs[(rsi>60)&(rsi<=70)]=75; rs[rsi>70]=40; rs[(rsi>=30)&(rsi<40)]=30; rs[rsi<30]=60

    ml, sl2, hist = calc_macd(p)
    ms_b = pd.Series(0.0, index=p.index)
    ms_b[ml>sl2]+=20; ms_b[hist>0]+=15
    ms_b[hist>hist.shift(1)]+=15; ms_b[hist<=hist.shift(1)]-=10

    bb_u,_,bb_l = calc_bb(p)
    rng=(bb_u-bb_l).clip(lower=1e-9); pos=(p-bb_l)/rng
    bs=pd.Series(50.0,index=p.index)
    bs[(pos>=0.4)&(pos<=0.8)]=70; bs[(pos>0.8)&(pos<=0.95)]=85
    bs[pos>0.95]=45; bs[(pos>=0.2)&(pos<0.4)]=45; bs[pos<0.2]=30

    vma20=v.rolling(20).mean().clip(lower=1e-9); vr=v/vma20; pc5=p.pct_change(5)
    vs=pd.Series(50.0,index=p.index)
    vs[(pc5>0)&(vr>1.2)]=80; vs[(pc5>0)&(vr<0.8)]=55
    vs[(pc5<0)&(vr>1.2)]=25; vs[(pc5<0)&(vr<0.8)]=45

    sk_s,sd_s=calc_stochastic(h,l,p)
    sts=pd.Series(50.0,index=p.index)
    sts[sk_s>80]=35; sts[(sk_s>60)&(sk_s<=80)]=65
    sts[(sk_s>40)&(sk_s<=60)]=55; sts[(sk_s>20)&(sk_s<=40)]=60; sts[sk_s<=20]=70

    adx_v,pdi,ndi=calc_adx(h,l,p)
    adx_f=adx_v.fillna(0); bull=(pdi>ndi).fillna(False)
    ads=pd.Series(50.0,index=p.index)
    ads[(adx_f>40)&bull]=85; ads[(adx_f>40)&~bull]=15
    ads[(adx_f>25)&(adx_f<=40)&bull]=72; ads[(adx_f>25)&(adx_f<=40)&~bull]=28

    obv=calc_obv(p,v); obv_ma=obv.rolling(20).mean()
    obv_s=pd.Series(35.0,index=p.index); obv_s[obv>obv_ma]=65

    DEFAULT_W = REGIME_TECH_WEIGHTS['neutral']
    ics = {
        'MA정렬':      _ic(ma_s.fillna(50)),
        'RSI':         _ic(rs.fillna(50)),
        'MACD':        _ic((pd.Series(50.0,index=p.index)+ms_b).clip(0,100).fillna(50)),
        '볼린저밴드':   _ic(bs.fillna(50)),
        '거래량':       _ic(vs.fillna(50)),
        '스토캐스틱':   _ic(sts.fillna(50)),
        'ADX추세강도':  _ic(ads),
        'OBV':         _ic(obv_s.fillna(50)),
    }
    # 파동근사는 벡터화 간소화
    rh20=h.rolling(20).max(); ph20=h.shift(20).rolling(20).max()
    rl20=l.rolling(20).min(); pl20=l.shift(20).rolling(20).min()
    wave_base=(50+(rh20>ph20)*15+(rl20>pl20)*15).astype(float)
    rs20v=(p-p.shift(19))/(p.shift(19)+1e-9); rm60v=(p-p.shift(59))/(p.shift(59)+1e-9)
    wave_s=(wave_base+(rs20v*150+rm60v*80).clip(-40,40)).clip(0,100)
    ics['파동근사'] = _ic(wave_s.fillna(50))

    # IC 기반 권장 가중치 (|IC| 비례, 최소 3%)
    abs_ics = {k: max(abs(v), 0.001) for k, v in ics.items()}
    total_abs = sum(abs_ics.values())
    suggested = {k: max(abs_ics[k]/total_abs, 0.03) for k in abs_ics}
    s_total = sum(suggested.values())
    suggested = {k: round(v/s_total, 3) for k, v in suggested.items()}

    return ics, suggested, DEFAULT_W


def run_walkforward(df, buy_th, sell_th, initial_capital, commission, slippage,
                    f_score=None, m_score=None, w_tech=100, w_fund=0, w_macro=0):
    """70/30 시간분할 워크-포워드 검증.
    학습기간(in-sample) 과 검증기간(out-of-sample) 성과를 비교해 과적합 여부를 진단."""
    split = int(len(df) * 0.70)
    train_df = df.iloc[:split]
    # 검증 구간은 지표 워밍업을 위해 20봉 오버랩
    test_df  = df.iloc[max(0, split - 20):]

    key_metrics = ['전략 수익률', 'CAGR', '최대낙폭(MDD)', 'Sharpe Ratio',
                   'Calmar Ratio', '승률', '총 매매']

    results = {}
    for label, sub_df in [('학습 (In-Sample)', train_df), ('검증 (Out-of-Sample)', test_df)]:
        if len(sub_df) < 60:
            results[label] = {k: 'N/A' for k in key_metrics}
            results[label]['기간'] = f"{sub_df.index[0].strftime('%Y-%m-%d')} ~ {sub_df.index[-1].strftime('%Y-%m-%d')}"
            continue
        m, _, _ = run_backtest(sub_df, buy_th, sell_th, initial_capital, commission, slippage,
                               f_score=f_score, m_score=m_score,
                               w_tech=w_tech, w_fund=w_fund, w_macro=w_macro)
        results[label] = {k: m.get(k, '-') for k in key_metrics}
        results[label]['기간'] = f"{sub_df.index[0].strftime('%Y-%m-%d')} ~ {sub_df.index[-1].strftime('%Y-%m-%d')}"

    # 과적합 지수: IS CAGR vs OOS CAGR 차이 (클수록 과적합)
    def _parse_pct(s):
        try: return float(str(s).replace('%','').replace('+',''))
        except: return 0.0
    is_cagr  = _parse_pct(results['학습 (In-Sample)'].get('CAGR', 0))
    oos_cagr = _parse_pct(results['검증 (Out-of-Sample)'].get('CAGR', 0))
    overfit  = is_cagr - oos_cagr

    return results, overfit, df.index[split].strftime('%Y-%m-%d')


def analyze_score_correlation(df):
    """bt_signals 점수와 N일 후 수익률의 상관관계를 분석.
    Returns list of dicts per horizon: IC, bucket_stats DataFrame, scatter DataFrame.
    """
    sigs   = bt_signals_full(df)
    closes = df['Close']
    results = []
    bins   = [0, 30, 40, 50, 60, 70, 80, 101]
    labels = ['0-30', '30-40', '40-50', '50-60', '60-70', '70-80', '80+']

    for horizon in [5, 10, 20]:
        fwd_ret = closes.pct_change(horizon).shift(-horizon) * 100
        combined = pd.DataFrame({'score': sigs, 'fwd_ret': fwd_ret}).dropna()
        combined = combined.iloc[20:]   # 워밍업 구간 제외

        ic = float(combined['score'].corr(combined['fwd_ret'])) if len(combined) > 10 else 0.0

        combined['bucket'] = pd.cut(combined['score'], bins=bins, labels=labels, right=False)
        bucket_stats = (combined.groupby('bucket', observed=True)['fwd_ret']
                        .agg(평균수익률='mean', 표준편차='std', 샘플수='count')
                        .reset_index())
        bucket_stats.columns = ['점수구간', '평균수익률(%)', '표준편차', '샘플수']

        results.append({'horizon': horizon, 'IC': ic,
                        'bucket_stats': bucket_stats, 'scatter': combined})
    return results

# ─────────────────────────────────────────────
# TELEGRAM ALERTS
# ─────────────────────────────────────────────

def send_telegram(token, chat_id, msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}, timeout=5)
        return r.status_code == 200, r.json().get('description', '')
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def score_color(s):
    return '#00C851' if s >= 70 else ('#FF8800' if s >= 50 else '#FF4444')

def score_label(s):
    return ('강한 매수 🚀' if s >= 80 else ('매수 📈' if s >= 65 else
            ('중립 ➡️' if s >= 50 else ('매도 📉' if s >= 35 else '강한 매도 ⚠️'))))

def gauge(value, title):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=value,
        title={'text': title, 'font': {'size': 13}},
        number={'font': {'size': 36, 'color': score_color(value)}},
        gauge={
            'axis': {'range': [0,100], 'tickwidth': 1},
            'bar':  {'color': score_color(value), 'thickness': 0.3},
            'steps': [{'range':[0,35],'color':'#FFEBEE'},
                      {'range':[35,65],'color':'#FFF8E1'},
                      {'range':[65,100],'color':'#E8F5E9'}],
            'threshold': {'line':{'color':'#555','width':2},'thickness':0.75,'value':50},
        }
    ))
    fig.update_layout(height=220, margin=dict(l=20,r=20,t=50,b=10))
    return fig

def fmt(v, pct=False, mul=100):
    if v is None: return 'N/A'
    try:
        f = float(v)
        return 'N/A' if np.isnan(f) else (f'{f*mul:.1f}%' if pct else f'{f:.2f}')
    except: return 'N/A'

def _style_score(val):
    try:
        v = float(val)
        if v >= 65:  return 'background-color:#1a3a2a;color:#4caf50;font-weight:bold'
        elif v >= 50: return 'background-color:#3a2e1a;color:#ff9800;font-weight:bold'
        else:         return 'background-color:#3a1a1a;color:#f44336;font-weight:bold'
    except: return ''

def _news_sentiment_keyword(ticker):
    """키워드 기반 감성 분석 (폴백용)"""
    try:
        news = yf.Ticker(ticker).news
        if not news: return 50.0, []
        pos_kw = ['surge','rally','beat','exceed','record','gain','growth','strong','bullish',
                  'upgrade','buy','positive','profit','revenue','raise','outperform','deal','win',
                  'breakthrough','partnership','acquisition','dividend','boost',
                  '상승','급등','호실적','매수','성장','기록','호재','흑자','돌파','계약']
        neg_kw = ['fall','drop','miss','decline','loss','cut','downgrade','sell','weak','bearish',
                  'concern','risk','warning','crash','layoff','lawsuit','fine','fraud',
                  'investigation','debt','recall','shortage','penalty','halt',
                  '하락','급락','손실','매도','악재','위기','조사','적자','리콜','제재']
        articles, total_score, count = [], 0, 0
        for item in news[:8]:
            # yfinance 0.2.50+ 신형식: item['content']['title']
            # 구형식: item['title']
            if 'content' in item and isinstance(item['content'], dict):
                c = item['content']
                title = c.get('title') or c.get('headline', '')
                pub_str = c.get('pubDate', '') or c.get('displayTime', '')
                try:
                    pub_dt = pub_str[5:10].replace('-', '/') if pub_str else '-'
                except:
                    pub_dt = '-'
            else:
                title = item.get('title', '')
                pub_ts = item.get('providerPublishTime', 0)
                pub_dt = datetime.fromtimestamp(pub_ts).strftime('%m/%d') if pub_ts else '-'

            if not title: continue
            tl = title.lower()
            pos = sum(1 for k in pos_kw if k in tl)
            neg = sum(1 for k in neg_kw if k in tl)
            score = pos - neg
            articles.append({
                '날짜': pub_dt,
                '헤드라인': title[:85] + ('…' if len(title) > 85 else ''),
                '감성': '🟢 긍정' if score > 0 else ('🔴 부정' if score < 0 else '⚪ 중립'),
            })
            total_score += score; count += 1
        if not articles: return 50.0, []
        avg = total_score / count if count else 0
        return float(np.clip(50 + avg * 12, 0, 100)), articles
    except:
        return 50.0, []

def _get_anthropic_key():
    try:
        k = st.secrets.get("ANTHROPIC_API_KEY", "")
        if k: return k
    except Exception:
        pass
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if k: return k
    return st.session_state.get('anthropic_key', '')


def find_sr_levels(close_s, high_s, low_s):
    """피벗 포인트 클러스터링으로 주요 지지/저항 레벨 반환."""
    from scipy.signal import find_peaks as _fp

    close = np.array(close_s, dtype=float)
    high  = np.array(high_s,  dtype=float)
    low   = np.array(low_s,   dtype=float)
    cur   = close[-1]

    prom  = cur * 0.008
    pk,  _ = _fp(high, distance=5, prominence=prom)
    tr,  _ = _fp(-low, distance=5, prominence=prom)

    raw = ([(float(high[i]), 'resistance') for i in pk[-25:]] +
           [(float(low[i]),  'support')    for i in tr[-25:]])
    raw.sort(key=lambda x: x[0])

    clusters = []
    for lvl, typ in raw:
        if clusters and abs(lvl - clusters[-1]['level']) / clusters[-1]['level'] < 0.015:
            clusters[-1]['level'] = (clusters[-1]['level'] + lvl) / 2
            clusters[-1]['count'] += 1
        else:
            clusters.append({'level': lvl, 'type': typ, 'count': 1})

    for c in clusters:
        c['dist_pct'] = (c['level'] - cur) / cur * 100
        c['above']    = c['level'] > cur

    clusters.sort(key=lambda x: abs(x['dist_pct']))
    return clusters[:8]


def detect_chart_pattern(ticker, period='6mo'):
    """종목 차트 패턴 감지 — 4가지 정확도 강화 포함.
    ① 시장 레짐(SPY/QQQ MA200) ② 멀티타임프레임(일+주봉)
    ③ 실적 발표 근접 여부 ④ 섹터 대비 상대강도
    """
    from scipy.signal import find_peaks as _fp
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date as _date, datetime as _dt

    sec_etf = TICKER_ETF.get(ticker.upper(), 'SPY')

    # ── 병렬 데이터 수집 ───────────────────────
    def _dl_daily():
        return yf.Ticker(ticker).history(period=period, auto_adjust=True)

    def _dl_weekly():
        return yf.Ticker(ticker).history(period='2y', interval='1wk', auto_adjust=True)

    def _dl_sector():
        syms = list({ticker, sec_etf})
        if len(syms) < 2:
            return None
        return yf.download(syms, period='3mo', interval='1d',
                           progress=False, auto_adjust=True)

    def _dl_earnings():
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is None:
                return None
            earn = None
            if isinstance(cal, dict):
                raw = cal.get('Earnings Date')
                earn = raw[0] if isinstance(raw, list) and raw else raw
            elif isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
                earn = cal.loc['Earnings Date'].iloc[0]
            if earn is None:
                return None
            if isinstance(earn, (pd.Timestamp, _dt)):
                earn = earn.date()
            elif isinstance(earn, str):
                earn = _dt.strptime(earn[:10], '%Y-%m-%d').date()
            days = (earn - _date.today()).days
            return int(days) if days >= 0 else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as _ex:
        _fd = _ex.submit(_dl_daily)
        _fw = _ex.submit(_dl_weekly)
        _fs = _ex.submit(_dl_sector)
        _fe = _ex.submit(_dl_earnings)
        df     = _fd.result()
        df_w   = _fw.result()
        df_sec = _fs.result()
        earn_days = _fe.result()

    if df is None or len(df) < 40:
        return None

    # ── 일봉 기본 분석 ─────────────────────────
    close = df['Close'].values.astype(float)
    high  = df['High'].values.astype(float)
    low   = df['Low'].values.astype(float)
    vol   = df['Volume'].values.astype(float)
    c_s   = pd.Series(close)
    cur   = float(close[-1])

    ma20 = c_s.rolling(20).mean().values
    ma50 = c_s.rolling(50).mean().values
    m20  = float(ma20[-1]) if not np.isnan(ma20[-1]) else None
    m50  = float(ma50[-1]) if not np.isnan(ma50[-1]) else None
    ma_bull = bool(m20 and m50 and cur > m20 > m50)

    golden = death = False
    if m20 and m50:
        for k in range(2, min(11, len(ma20))):
            p20, p50 = float(ma20[-k]), float(ma50[-k])
            if np.isnan(p20) or np.isnan(p50):
                break
            if not golden and float(ma20[-1]) > float(ma50[-1]) and p20 <= p50:
                golden = True; break
            if not death and float(ma20[-1]) < float(ma50[-1]) and p20 >= p50:
                death = True; break

    pk, _ = _fp(close, distance=8)
    tr, _ = _fp(-close, distance=8)
    uptrend = downtrend = False
    if len(pk) >= 2 and len(tr) >= 2:
        uptrend   = bool(close[pk[-1]] > close[pk[-2]] and close[tr[-1]] > close[tr[-2]])
        downtrend = bool(close[pk[-1]] < close[pk[-2]] and close[tr[-1]] < close[tr[-2]])

    _rsi_val = calc_rsi(c_s).iloc[-1]
    rsi_v = float(_rsi_val) if np.isfinite(_rsi_val) else 50.0

    up_vol = np.mean([vol[i] for i in range(-5, 0) if close[i] > close[i-1]] or [0])
    vol_ok = bool(up_vol > np.mean(vol[-20:]) * 1.1)

    bb_u, _, bb_l = calc_bb(c_s)
    bb_squeeze = bool((float(bb_u.iloc[-1]) - float(bb_l.iloc[-1])) / cur < 0.05)

    # ── ② 주봉 멀티타임프레임 ─────────────────
    weekly_signal = 'unknown'
    if df_w is not None and len(df_w) >= 20:
        wc = df_w['Close'].values.astype(float)
        wh = df_w['High'].values.astype(float)
        wc_s = pd.Series(wc)
        wma20 = float(wc_s.rolling(20).mean().iloc[-1])
        bull_w = float(wc[-1]) > wma20 and not np.isnan(wma20)
        wpk, _ = _fp(wc, distance=4)
        wtr, _ = _fp(-wc, distance=4)
        up_w = (len(wpk) >= 2 and len(wtr) >= 2 and
                wc[wpk[-1]] > wc[wpk[-2]] and wc[wtr[-1]] > wc[wtr[-2]])
        weekly_signal = ('bullish' if (bull_w and up_w) else
                         'bearish' if (not bull_w and not up_w) else 'neutral')

    # ── ③ 실적 발표 근접 ─────────────────────
    earn_risk = ('high'    if earn_days is not None and earn_days <= 7  else
                 'medium'  if earn_days is not None and earn_days <= 14 else
                 'low')

    # ── ④ 섹터 대비 상대강도 ────────────────
    sector_rs = None
    try:
        if df_sec is not None and not df_sec.empty:
            cl = df_sec['Close']
            # MultiIndex: cl[ticker], cl[sec_etf]
            if ticker in cl.columns and sec_etf in cl.columns:
                tkr_c = cl[ticker].dropna()
                etf_c = cl[sec_etf].dropna()
                if len(tkr_c) >= 2 and len(etf_c) >= 2:
                    tkr_r = float(tkr_c.iloc[-1] / tkr_c.iloc[0] - 1) * 100
                    etf_r = float(etf_c.iloc[-1] / etf_c.iloc[0] - 1) * 100
                    sector_rs = {
                        'etf':        sec_etf,
                        'ticker_ret': round(tkr_r, 1),
                        'etf_ret':    round(etf_r, 1),
                        'rs':         round(tkr_r - etf_r, 1),
                        'outperform': tkr_r > etf_r,
                    }
    except Exception:
        pass

    # ── 종합 시그널 ────────────────────────────
    patterns = []
    if golden:     patterns.append('골든크로스')
    if uptrend:    patterns.append('상승추세 (HH·HL)')
    if ma_bull:    patterns.append('MA 정배열')
    if vol_ok:     patterns.append('상승 거래량')
    if death:      patterns.append('데드크로스')
    if downtrend:  patterns.append('하락추세 (LH·LL)')
    if bb_squeeze: patterns.append('BB 수렴')
    if rsi_v < 30: patterns.append('RSI 과매도')
    if rsi_v > 70: patterns.append('RSI 과매수')
    if weekly_signal == 'bullish': patterns.append('주봉 강세')
    if weekly_signal == 'bearish': patterns.append('주봉 약세')
    if sector_rs and sector_rs['outperform']:  patterns.append(f"섹터 아웃퍼폼 vs {sec_etf}")
    if sector_rs and not sector_rs['outperform']: patterns.append(f"섹터 언더퍼폼 vs {sec_etf}")

    bull = sum([golden, uptrend, ma_bull, vol_ok,
                weekly_signal == 'bullish',
                bool(sector_rs and sector_rs['outperform'])])
    bear = sum([death, downtrend, not ma_bull and not uptrend,
                weekly_signal == 'bearish',
                bool(sector_rs and not sector_rs['outperform'])])
    signal = 'bullish' if bull >= 3 else 'bearish' if bear >= 3 else 'neutral'

    return {
        'ticker':        ticker,
        'signal':        signal,
        'patterns':      patterns,
        'rsi':           round(rsi_v, 1),
        'ma_bull':       ma_bull,
        'uptrend':       uptrend,
        'weekly_signal': weekly_signal,
        'earn_days':     earn_days,
        'earn_risk':     earn_risk,
        'sector_rs':     sector_rs,
        'sr_levels':     find_sr_levels(df['Close'], df['High'], df['Low']),
        'price':         round(cur, 2),
    }


def get_news_sentiment(ticker):
    """뉴스 감성 분석 (Claude API 우선, 키워드 폴백)"""
    api_key = _get_anthropic_key()
    if not api_key:
        return _news_sentiment_keyword(ticker)

    try:
        news = yf.Ticker(ticker).news
        if not news:
            return 50.0, []

        raw_articles = []
        for item in news[:8]:
            if 'content' in item and isinstance(item['content'], dict):
                c = item['content']
                title = c.get('title') or c.get('headline', '')
                pub_str = c.get('pubDate', '') or c.get('displayTime', '')
                try:
                    pub_dt = pub_str[5:10].replace('-', '/') if pub_str else '-'
                except Exception:
                    pub_dt = '-'
            else:
                title = item.get('title', '')
                pub_ts = item.get('providerPublishTime', 0)
                pub_dt = datetime.fromtimestamp(pub_ts).strftime('%m/%d') if pub_ts else '-'
            if title:
                raw_articles.append({'date': pub_dt, 'title': title})

        if not raw_articles:
            return 50.0, []

        headlines = "\n".join([f"- {a['title']}" for a in raw_articles])

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": (
                f"다음은 {ticker} 종목 관련 최근 뉴스 헤드라인입니다. "
                f"각 헤드라인이 주가에 미치는 영향을 분석해주세요.\n\n"
                f"{headlines}\n\n"
                f"반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):\n"
                f'{{"overall_score": <0~100 정수, 50=중립, 70+=긍정, 30-=부정>, '
                f'"articles": [{{"sentiment": "긍정" 또는 "부정" 또는 "중립", '
                f'"reason": "한줄 근거"}}]}}'
            )}]
        )

        text = response.content[0].text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        result = json.loads(text)
        overall = float(np.clip(result.get('overall_score', 50), 0, 100))

        ai_articles = result.get('articles', [])
        display_articles = []
        for i, a in enumerate(raw_articles):
            if i < len(ai_articles):
                sa = ai_articles[i]
                s = sa.get('sentiment', '중립')
                icon = '🟢 긍정' if s == '긍정' else ('🔴 부정' if s == '부정' else '⚪ 중립')
                reason = sa.get('reason', '')[:60]
            else:
                icon, reason = '⚪ 중립', ''
            display_articles.append({
                '날짜': a['date'],
                '헤드라인': a['title'][:85] + ('…' if len(a['title']) > 85 else ''),
                '감성': icon,
                '분석': reason,
            })

        return overall, display_articles
    except Exception:
        return _news_sentiment_keyword(ticker)

# ─────────────────────────────────────────────
# ADVANCED ANALYTICS
# ─────────────────────────────────────────────

@st.cache_data(ttl=1800)
def technical_score_multi(ticker):
    """일봉·주봉·월봉 3개 타임프레임 기술적 점수"""
    end = datetime.now()
    configs = [('일봉', '1d', 520), ('주봉', '1wk', 1825), ('월봉', '1mo', 3650)]
    results = {}
    for label, interval, days in configs:
        try:
            start = end - timedelta(days=days)
            d = download_stock(ticker, start=start, end=end, interval=interval)
            d = d.dropna(subset=['Close'])
            if len(d) >= 30:
                score, det = technical_score(d)
                results[label] = {'score': round(score, 1), 'det': det}
            else:
                results[label] = None
        except:
            results[label] = None
    return results

def detect_candle_patterns(df):
    """주요 캔들 패턴 자동 감지 (단봉·이봉·삼봉)"""
    if len(df) < 3: return []
    o, h, l, c = df['Open'], df['High'], df['Low'], df['Close']
    patterns = []

    for i in [-1, -2, -3]:
        if abs(i) >= len(df): continue
        body   = abs(float(c.iloc[i]) - float(o.iloc[i]))
        total  = float(h.iloc[i]) - float(l.iloc[i]) + 1e-9
        upper  = float(h.iloc[i]) - max(float(c.iloc[i]), float(o.iloc[i]))
        lower  = min(float(c.iloc[i]), float(o.iloc[i])) - float(l.iloc[i])
        is_bull = float(c.iloc[i]) >= float(o.iloc[i])
        ago = '최근' if i == -1 else f'{abs(i)}일전'
        dt  = str(df.index[i])[:10]

        if body / total < 0.08:
            patterns.append({'날짜': dt, '패턴': '⚖️ 도지', '신호': '⚪ 중립/반전 가능', '경과': ago})
        elif lower >= 2*body and upper <= body*0.3 and not is_bull:
            patterns.append({'날짜': dt, '패턴': '🔨 망치형', '신호': '🟢 상승 반전 신호', '경과': ago})
        elif upper >= 2*body and lower <= body*0.3 and is_bull:
            patterns.append({'날짜': dt, '패턴': '⭐ 슈팅스타', '신호': '🔴 하락 반전 신호', '경과': ago})
        elif body/total > 0.85 and is_bull:
            patterns.append({'날짜': dt, '패턴': '📈 강세 마루보주', '신호': '🟢 강한 상승 지속', '경과': ago})
        elif body/total > 0.85 and not is_bull:
            patterns.append({'날짜': dt, '패턴': '📉 약세 마루보주', '신호': '🔴 강한 하락 지속', '경과': ago})

    if len(df) >= 2:
        pb = abs(float(c.iloc[-2]) - float(o.iloc[-2]))
        cb = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
        pbull = float(c.iloc[-2]) >= float(o.iloc[-2])
        cbull = float(c.iloc[-1]) >= float(o.iloc[-1])
        dt = str(df.index[-1])[:10]
        if cb > pb*1.1 and not pbull and cbull:
            patterns.append({'날짜': dt, '패턴': '🟢 강세 장악형', '신호': '🟢 매수 반전 신호', '경과': '최근'})
        elif cb > pb*1.1 and pbull and not cbull:
            patterns.append({'날짜': dt, '패턴': '🔴 약세 장악형', '신호': '🔴 매도 반전 신호', '경과': '최근'})

    if len(df) >= 3:
        b1  = abs(float(c.iloc[-3]) - float(o.iloc[-3]))
        t2  = float(h.iloc[-2]) - float(l.iloc[-2]) + 1e-9
        b2  = abs(float(c.iloc[-2]) - float(o.iloc[-2]))
        b3  = abs(float(c.iloc[-1]) - float(o.iloc[-1]))
        bull1 = float(c.iloc[-3]) >= float(o.iloc[-3])
        bull3 = float(c.iloc[-1]) >= float(o.iloc[-1])
        dt = str(df.index[-1])[:10]
        if not bull1 and b2/t2 < 0.3 and bull3 and b3 > b1*0.5:
            patterns.append({'날짜': dt, '패턴': '🌅 Morning Star (샛별형)', '신호': '🟢 강한 매수 반전', '경과': '최근'})
        elif bull1 and b2/t2 < 0.3 and not bull3 and b3 > b1*0.5:
            patterns.append({'날짜': dt, '패턴': '🌆 Evening Star (석별형)', '신호': '🔴 강한 매도 반전', '경과': '최근'})

    return patterns

def calc_momentum(df):
    """1M/3M/6M/12M 모멘텀 수익률 및 점수"""
    p  = df['Close']
    cp = float(p.iloc[-1])
    def ret(d):
        return (cp - float(p.iloc[-d])) / float(p.iloc[-d]) * 100 if len(p) >= d else None
    m1, m3, m6, m12 = ret(21), ret(63), ret(126), ret(252)
    def sc(v):
        if v is None: return 50
        if v > 30: return 90
        elif v > 15: return 75
        elif v > 5: return 65
        elif v > 0: return 55
        elif v > -10: return 40
        elif v > -20: return 25
        else: return 10
    vals = [v for v in [m3, m6, m12] if v is not None]
    score = sum(sc(v) for v in vals) / len(vals) if vals else 50.0
    return {'score': round(score, 1), '1M': m1, '3M': m3, '6M': m6, '12M': m12}

def calc_dcf(ticker, treasury_yield=4.5):
    """그레이엄 변형 공식 기반 내재가치 산출 (기본/보수적 2가지)"""
    try:
        info = yf.Ticker(ticker).info
        _pit_snapshot(ticker, info)
        eps  = info.get('trailingEps') or info.get('forwardEps')
        if not eps or eps <= 0: return None, {}
        g_raw = info.get('earningsGrowth')
        if g_raw is None: g_raw = info.get('revenueGrowth')
        if g_raw is None: g_raw = 0.07
        g = float(g_raw) * 100 if abs(float(g_raw)) <= 1 else float(g_raw)
        g = max(min(g, 30.0), -5.0)
        y = max(treasury_yield, 1.0)
        intrinsic    = eps * (8.5 + 2*g) * (4.4 / y)
        conservative = eps * (8.5 + g)   * (4.4 / y)
        cp = info.get('currentPrice') or info.get('regularMarketPrice')
        if not cp: return None, {}
        return intrinsic, {
            'EPS': eps, '예상성장률(g)': round(g, 1), '적용금리(Y)': round(y, 2),
            '내재가치_기본': intrinsic, '내재가치_보수': conservative, '현재가': cp,
            '상승여력_기본': (intrinsic - cp) / cp * 100,
            '상승여력_보수': (conservative - cp) / cp * 100,
        }
    except:
        return None, {}

@st.cache_data(ttl=1800)
def calc_risk_metrics(ticker):
    """Beta, 역사적 VaR(95%/99%), CVaR, 연간변동성, Sharpe"""
    try:
        end = datetime.now(); start = end - timedelta(days=390)
        sdf   = download_stock(ticker, start=start, end=end)
        spydf = download_stock('SPY', start=start, end=end)
        sr = sdf['Close'].pct_change().dropna()
        mr = spydf['Close'].pct_change().dropna()
        idx = sr.index.intersection(mr.index)
        if len(idx) < 60: return {}
        sr = sr.loc[idx]; mr = mr.loc[idx]
        beta   = np.cov(sr.values, mr.values)[0][1] / (np.var(mr.values) + 1e-12)
        var95  = float(np.percentile(sr.values, 5))  * 100
        var99  = float(np.percentile(sr.values, 1))  * 100
        thresh = np.percentile(sr.values, 5)
        cvar95 = float(sr[sr <= thresh].mean()) * 100 if (sr <= thresh).any() else var95
        vol    = float(sr.std()) * np.sqrt(252) * 100
        sharpe = float((sr.mean() - 4.5/252/100) / sr.std() * np.sqrt(252)) if sr.std() > 0 else 0
        return {
            'Beta': round(beta, 2),
            'VaR 95% (1일)': f"{var95:.2f}%",
            'VaR 99% (1일)': f"{var99:.2f}%",
            'CVaR 95%': f"{cvar95:.2f}%",
            '연간 변동성': f"{vol:.1f}%",
            'Sharpe (RF 4.5%)': round(sharpe, 2),
        }
    except:
        return {}

# ─────────────────────────────────────────────
# MONTE CARLO SIMULATION
# ─────────────────────────────────────────────

def calc_monte_carlo(df, days=60, n_sims=500, initial=None):
    """GBM(기하 브라운 운동) 기반 몬테카를로 시뮬레이션.
    Returns: simulations array (n_sims × days), stats dict"""
    close = df['Close']
    if initial is None:
        initial = float(close.iloc[-1])

    log_ret = np.log(close / close.shift(1)).dropna()
    mu = float(log_ret.mean())
    sigma = float(log_ret.std())

    sims = np.zeros((n_sims, days + 1))
    sims[:, 0] = initial

    rng = np.random.default_rng(42)
    for t in range(1, days + 1):
        z = rng.standard_normal(n_sims)
        sims[:, t] = sims[:, t-1] * np.exp((mu - 0.5 * sigma**2) + sigma * z)

    final_prices = sims[:, -1]
    returns = (final_prices - initial) / initial * 100

    stats = {
        'days': days,
        'n_sims': n_sims,
        'current': initial,
        'median': float(np.median(final_prices)),
        'mean': float(np.mean(final_prices)),
        'p5': float(np.percentile(final_prices, 5)),
        'p25': float(np.percentile(final_prices, 25)),
        'p75': float(np.percentile(final_prices, 75)),
        'p95': float(np.percentile(final_prices, 95)),
        'prob_up': float(np.mean(final_prices > initial) * 100),
        'prob_down10': float(np.mean(returns < -10) * 100),
        'prob_up10': float(np.mean(returns > 10) * 100),
        'ret_median': float(np.median(returns)),
        'ret_mean': float(np.mean(returns)),
        'ret_p5': float(np.percentile(returns, 5)),
        'ret_p95': float(np.percentile(returns, 95)),
        'daily_vol': sigma,
    }
    return sims, stats

# ─────────────────────────────────────────────
# TRADE LEVELS
# ─────────────────────────────────────────────

def calc_trade_levels(df, total_score):
    """단타(1~5일)·스윙(2~4주) 실전 매매가 산출.
    RSI/MACD/스토캐스틱/거래량 조건 통합, VWAP, 3분할 매수, 시간손절 포함."""
    p, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']
    cp  = float(p.iloc[-1])
    _prev_c = p.shift(1)
    _tr = pd.concat([(h-l), (h-_prev_c).abs(), (l-_prev_c).abs()], axis=1).max(axis=1)
    atr = float(_tr.rolling(14).mean().iloc[-1])

    ma5   = float(p.rolling(5).mean().iloc[-1])
    ma10  = float(p.rolling(10).mean().iloc[-1])
    ma20  = float(p.rolling(20).mean().iloc[-1])
    ma60  = float(p.rolling(60).mean().iloc[-1])
    ma120 = float(p.rolling(120).mean().iloc[-1])

    bb_u, bb_mid, bb_l_v = calc_bb(p)
    bb_upper = float(bb_u.iloc[-1])
    bb_lower = float(bb_l_v.iloc[-1])
    bb_middle = float(bb_mid.iloc[-1])

    high5  = float(h.tail(5).max())
    low5   = float(l.tail(5).min())
    high20 = float(h.tail(20).max())
    low20  = float(l.tail(20).min())
    high60 = float(h.tail(60).max())
    low60  = float(l.tail(60).min())

    vr = float(v.iloc[-1]) / (float(v.rolling(20).mean().iloc[-1]) + 1e-9)
    vwap = float((p * v).rolling(20).sum().iloc[-1] / (v.rolling(20).sum().iloc[-1] + 1e-9))

    rsi_v = float(calc_rsi(p).iloc[-1])
    ml, sl, _ = calc_macd(p)
    macd_bull = float(ml.iloc[-1]) > float(sl.iloc[-1])
    sk_s, sd_s = calc_stochastic(h, l, p)
    stoch_v = float(sk_s.iloc[-1])
    adx_s, pdi_s, ndi_s = calc_adx(h, l, p)
    adx_v = float(adx_s.iloc[-1]) if not np.isnan(float(adx_s.iloc[-1])) else 20
    trend_up = float(pdi_s.iloc[-1]) > float(ndi_s.iloc[-1])
    above_vwap = cp > vwap

    # ── 피보나치 (60일 스윙) ───────────────────────
    sw_high = high60; sw_low = low60; fib_rng = sw_high - sw_low
    fib = {
        '23.6%': sw_high - 0.236*fib_rng, '38.2%': sw_high - 0.382*fib_rng,
        '50.0%': sw_high - 0.500*fib_rng, '61.8%': sw_high - 0.618*fib_rng,
        '78.6%': sw_high - 0.786*fib_rng,
        '확장 127.2%': sw_high + 0.272*fib_rng, '확장 161.8%': sw_high + 0.618*fib_rng,
    }

    # ── 피봇 포인트 ───────────────────────────────
    prev_h = float(h.iloc[-2]) if len(h) >= 2 else float(h.iloc[-1])
    prev_l = float(l.iloc[-2]) if len(l) >= 2 else float(l.iloc[-1])
    prev_c = float(p.iloc[-2]) if len(p) >= 2 else float(p.iloc[-1])
    pivot  = (prev_h + prev_l + prev_c) / 3
    piv = {
        'R3': pivot + 2*(prev_h-prev_l), 'R2': pivot + (prev_h-prev_l),
        'R1': 2*pivot - prev_l, 'PP': pivot,
        'S1': 2*pivot - prev_h, 'S2': pivot - (prev_h-prev_l),
        'S3': pivot - 2*(prev_h-prev_l),
    }

    def _nearest_below(levels, ref, max_dist_pct=5.0):
        valid = [x for x in levels if ref * (1 - max_dist_pct/100) < x <= ref]
        return sorted(valid, reverse=True)

    def _nearest_above(levels, ref, max_dist_pct=10.0):
        valid = [x for x in levels if ref < x < ref * (1 + max_dist_pct/100)]
        return sorted(valid)

    sup_pool = [ma10, ma20, ma60, ma120, bb_lower, bb_middle, vwap,
                piv['S1'], piv['S2'], fib['38.2%'], fib['50.0%'], fib['61.8%'], low5, low20]
    res_pool = [ma10, ma20, ma60, ma120, bb_upper, piv['R1'], piv['R2'],
                fib['23.6%'], fib['확장 127.2%'], high5, high20, high60]

    safe = lambda a, b: a / b * 100 if b > 0 else 0.0

    # ── 진입 조건 체크 ─────────────────────────────
    dt_conditions = []
    if rsi_v < 35: dt_conditions.append('RSI 과매도 ✅')
    elif rsi_v > 65: dt_conditions.append('RSI 과매수 ⚠️')
    else: dt_conditions.append(f'RSI {rsi_v:.0f} 중립')
    if macd_bull: dt_conditions.append('MACD 매수 ✅')
    else: dt_conditions.append('MACD 매도 ⚠️')
    if stoch_v < 25: dt_conditions.append('스토캐 과매도 ✅')
    elif stoch_v > 75: dt_conditions.append('스토캐 과매수 ⚠️')
    if above_vwap: dt_conditions.append('VWAP 위 ✅')
    else: dt_conditions.append('VWAP 아래 ⚠️')
    if vr >= 1.0: dt_conditions.append(f'거래량 {vr:.1f}x ✅')
    else: dt_conditions.append(f'거래량 {vr:.1f}x 부족 ⚠️')
    dt_bull_cnt = sum(1 for c in dt_conditions if '✅' in c)

    # ── 단타 (1~5일) ──────────────────────────────
    if total_score >= 70 and dt_bull_cnt >= 3:
        dt_e1 = cp;  dt_be1 = '현재가 (강세 + 지표 합류)'
        dt_strategy = f'✅ 즉시 진입 ({dt_bull_cnt}/5 조건 충족)'
        dt_alloc = '1차 60% / 2차 30% / 3차 10%'
    elif total_score >= 55 and dt_bull_cnt >= 2:
        sups = _nearest_below(sup_pool, cp, 3.0)
        dt_e1 = sups[0] if sups else max(cp - atr * 0.3, vwap)
        dt_be1 = f'지지선 / VWAP {safe(vwap-cp, cp):+.1f}%'
        dt_strategy = f'⏳ 지지 확인 후 ({dt_bull_cnt}/5 조건)'
        dt_alloc = '1차 50% / 2차 30% / 3차 20%'
    else:
        sups = _nearest_below(sup_pool, cp, 5.0)
        dt_e1 = sups[0] if sups else piv['S1']
        dt_be1 = '하방 지지선'
        dt_strategy = f'🔍 반등 확인 필수 ({dt_bull_cnt}/5 조건)'
        dt_alloc = '1차 40% / 2차 30% / 3차 30%'

    sups2 = _nearest_below(sup_pool, dt_e1 * 0.999, 4.0)
    dt_e2 = sups2[0] if sups2 else dt_e1 - atr * 0.6
    sups3 = _nearest_below(sup_pool, dt_e2 * 0.999, 4.0)
    dt_e3 = sups3[0] if sups3 else dt_e2 - atr * 0.6

    dt_stop = dt_e3 - atr * 0.5
    if dt_stop >= dt_e3 * 0.995: dt_stop = dt_e3 - atr * 0.8

    res_above = _nearest_above(res_pool, cp, 8.0)
    dt_t1 = res_above[0] if res_above else cp + atr * 1.2
    dt_t2_c = [x for x in res_above if x > dt_t1 * 1.005]
    dt_t2 = dt_t2_c[0] if dt_t2_c else dt_t1 + atr * 1.0

    dt_trailing = f"고점 −ATR×0.7 ({atr*0.7:.2f})"
    dt_time_stop = '3일 내 +1% 미달 시 청산 검토'
    dt_risk = max(dt_e1 - dt_stop, 1e-9)

    # ── 스윙 진입 조건 ─────────────────────────────
    sw_conditions = []
    if trend_up: sw_conditions.append('추세 상승(DI+>DI−) ✅')
    else: sw_conditions.append('추세 하락(DI−>DI+) ⚠️')
    if adx_v > 20: sw_conditions.append(f'ADX {adx_v:.0f} 추세 ✅')
    else: sw_conditions.append(f'ADX {adx_v:.0f} 횡보 ⚠️')
    if cp > ma20: sw_conditions.append('MA20 위 ✅')
    else: sw_conditions.append('MA20 아래 ⚠️')
    if cp > ma60: sw_conditions.append('MA60 위 ✅')
    else: sw_conditions.append('MA60 아래 ⚠️')
    if macd_bull: sw_conditions.append('MACD 매수 ✅')
    else: sw_conditions.append('MACD 매도 ⚠️')
    sw_bull_cnt = sum(1 for c in sw_conditions if '✅' in c)

    # ── 스윙 (2~4주) ──────────────────────────────
    if total_score >= 70 and sw_bull_cnt >= 3:
        sw_e1 = cp;  sw_be1 = '현재가 (추세 + 지표 합류)'
        sw_strategy = f'✅ 분할 매수 시작 ({sw_bull_cnt}/5 조건)'
        sw_alloc = '1차 40% / 2차 30% / 3차 30%'
    elif total_score >= 55 and sw_bull_cnt >= 2:
        sw_sups = _nearest_below([fib['38.2%'], fib['50.0%'], ma20, ma60, vwap, low20], cp, 5.0)
        sw_e1 = sw_sups[0] if sw_sups else cp - atr * 0.5
        sw_be1 = 'Fib / MA / VWAP 지지'
        sw_strategy = f'⏳ 지지 대기 ({sw_bull_cnt}/5 조건)'
        sw_alloc = '1차 40% / 2차 30% / 3차 30%'
    else:
        sw_sups = _nearest_below([fib['50.0%'], fib['61.8%'], ma60, ma120, low60], cp, 8.0)
        sw_e1 = sw_sups[0] if sw_sups else fib['61.8%']
        sw_be1 = '깊은 지지선'
        sw_strategy = f'🔍 추세 전환 확인 필수 ({sw_bull_cnt}/5 조건)'
        sw_alloc = '1차 30% / 2차 30% / 3차 40%'

    sw_sups2 = _nearest_below([fib['50.0%'], fib['61.8%'], fib['78.6%'], ma60, ma120, low60],
                               sw_e1 * 0.999, 6.0)
    sw_e2 = sw_sups2[0] if sw_sups2 else sw_e1 - atr * 1.2
    sw_sups3 = _nearest_below([fib['61.8%'], fib['78.6%'], ma120, low60],
                               sw_e2 * 0.999, 6.0)
    sw_e3 = sw_sups3[0] if sw_sups3 else sw_e2 - atr * 1.2

    sw_stop = sw_e3 - atr * 1.2
    if sw_stop >= sw_e3 * 0.995: sw_stop = sw_e3 - atr * 1.8

    sw_res = _nearest_above([fib['23.6%'], fib['확장 127.2%'], fib['확장 161.8%'],
                              sw_high, ma120, high60], cp, 15.0)
    sw_t1 = sw_res[0] if sw_res else cp * 1.08
    sw_t2_c = [x for x in sw_res if x > sw_t1 * 1.005]
    sw_t2 = sw_t2_c[0] if sw_t2_c else sw_t1 * 1.05
    sw_t3 = sw_t2 * 1.05

    sw_trailing = f"고점 −ATR×1.5 ({atr*1.5:.2f})"
    sw_time_stop = '2주 내 +3% 미달 시 비중 축소 검토'
    sw_risk = max(sw_e1 - sw_stop, 1e-9)

    return {
        'cp': cp, 'atr': atr, 'pivot': pivot, 'fib': fib, 'piv': piv, 'vwap': vwap,
        'dantta': {
            'strategy': dt_strategy, 'alloc': dt_alloc,
            'conditions': dt_conditions, 'bull_cnt': dt_bull_cnt,
            'entry1': dt_e1, 'basis_e1': dt_be1,
            'entry2': dt_e2, 'basis_e2': '2차 지지선',
            'entry3': dt_e3, 'basis_e3': '3차 지지선 (최종)',
            'target1': dt_t1, 'basis_t1': '최근접 저항선',
            'target2': dt_t2, 'basis_t2': '차순위 저항선',
            'stop': dt_stop, 'basis_stop': '3차매수 −ATR×0.5',
            'trailing': dt_trailing, 'time_stop': dt_time_stop,
            'rr1': round((dt_t1-dt_e1)/dt_risk, 1),
            'rr2': round((dt_t2-dt_e1)/dt_risk, 1),
            'ret1': safe(dt_t1-dt_e1, dt_e1),
            'ret2': safe(dt_t2-dt_e1, dt_e1),
            'risk_pct': safe(dt_e1-dt_stop, dt_e1),
        },
        'swing': {
            'strategy': sw_strategy, 'alloc': sw_alloc,
            'conditions': sw_conditions, 'bull_cnt': sw_bull_cnt,
            'entry1': sw_e1, 'basis_e1': sw_be1,
            'entry2': sw_e2, 'basis_e2': 'Fib 50~61.8% / MA60',
            'entry3': sw_e3, 'basis_e3': 'Fib 78.6% / MA120 (최종)',
            'target1': sw_t1, 'basis_t1': '최근접 저항선',
            'target2': sw_t2, 'basis_t2': '차순위 저항선',
            'target3': sw_t3, 'basis_t3': '확장 목표 (+5%)',
            'stop': sw_stop, 'basis_stop': '3차매수 −ATR×1.2',
            'trailing': sw_trailing, 'time_stop': sw_time_stop,
            'rr1': round((sw_t1-sw_e1)/sw_risk, 1),
            'rr2': round((sw_t2-sw_e1)/sw_risk, 1),
            'ret1': safe(sw_t1-sw_e1, sw_e1),
            'ret2': safe(sw_t2-sw_e1, sw_e1),
            'risk_pct': safe(sw_e1-sw_stop, sw_e1),
        },
    }


def _parse_pct_value(value):
    """'12.3%' 같은 표시 문자열을 float 퍼센트 값으로 변환."""
    try:
        if value is None:
            return None
        if isinstance(value, (int, float, np.integer, np.floating)):
            return float(value)
        return float(str(value).replace('%', '').replace(',', '').strip())
    except Exception:
        return None


def build_execution_plan(lv, total_score, total_adj, regime, risk_data,
                         capital, risk_per_trade_pct, max_position_pct,
                         min_rr=1.5):
    """추천 매매가를 실전 포지션 사이징과 거래 가능 여부로 변환."""
    plans = {}
    risk_budget = max(float(capital) * float(risk_per_trade_pct) / 100, 0.0)
    max_position_value = max(float(capital) * float(max_position_pct) / 100, 0.0)
    risk_vol = _parse_pct_value(risk_data.get('연간 변동성')) if risk_data else None
    beta = risk_data.get('Beta') if risk_data else None

    for key, label in [('dantta', '단타'), ('swing', '스윙')]:
        tr = lv[key]
        entry = float(tr['entry1'])
        stop = float(tr['stop'])
        target = float(tr['target1'])
        per_share_risk = max(entry - stop, 1e-9)
        rr = (target - entry) / per_share_risk

        qty_by_risk = risk_budget / per_share_risk if risk_budget > 0 else 0.0
        qty_by_alloc = max_position_value / entry if entry > 0 else 0.0
        qty = max(min(qty_by_risk, qty_by_alloc), 0.0)
        position_value = qty * entry
        expected_risk = qty * per_share_risk
        expected_reward = qty * max(target - entry, 0.0)

        blockers = []
        warnings = []
        if total_adj < 55:
            blockers.append('국면 조정 점수 55점 미만')
        if rr < min_rr:
            blockers.append(f'손익비 {rr:.1f}:1 < 기준 {min_rr:.1f}:1')
        if risk_budget <= 0 or max_position_value <= 0:
            blockers.append('계좌/리스크 설정 필요')
        if beta is not None and beta >= 1.5:
            warnings.append('고베타 종목')
        if risk_vol is not None and risk_vol >= 45:
            warnings.append('연간 변동성 45% 이상')
        if regime == 'bear' and key == 'dantta':
            warnings.append('약세장 단타는 비중 축소 권장')
        if total_score >= 75 and rr >= min_rr and not blockers:
            verdict = '진입 가능'
        elif not blockers:
            verdict = '조건부 진입'
        else:
            verdict = '대기/회피'

        plans[key] = {
            'label': label,
            'verdict': verdict,
            'blockers': blockers,
            'warnings': warnings,
            'qty': qty,
            'position_value': position_value,
            'risk_amount': expected_risk,
            'reward_amount': expected_reward,
            'risk_budget': risk_budget,
            'max_position_value': max_position_value,
            'rr': rr,
            'entry': entry,
            'stop': stop,
            'target': target,
            'risk_pct_of_account': expected_risk / capital * 100 if capital > 0 else 0.0,
            'alloc_pct_of_account': position_value / capital * 100 if capital > 0 else 0.0,
        }
    return plans


# ─────────────────────────────────────────────
# QUANT ENGINE
# ─────────────────────────────────────────────

def _zscore_to_score(series):
    """Z-score를 20~80 범위의 점수로 변환. 평균=50, ±2σ가 20/80."""
    m = series.mean(); s = series.std()
    if pd.isna(s) or s < 1e-9:
        return pd.Series(50.0, index=series.index)
    z = (series - m) / s
    return (z * 15 + 50).clip(20, 80).fillna(50.0)

def calc_factor_scores(tickers, prog_bar=None, prog_text=None,
                       extra_factors=False, min_avg_volume=0, factor_weights=None):
    """멀티팩터 랭킹: 4팩터 + 선택적 3추가팩터(애널·공매도·EPS서프라이즈)."""
    import time
    end = datetime.now(); start = end - timedelta(days=520)
    results = []
    failed = []
    for i, tk in enumerate(tickers):
        if prog_text: prog_text.text(f"팩터 분석: {tk} ({i+1}/{len(tickers)})")
        if prog_bar: prog_bar.progress((i+1)/len(tickers))
        try:
            df = download_stock(tk, start=start, end=end)
            if df is None or df.empty:
                failed.append(tk); continue
            df = df.dropna(subset=['Close'])
            if len(df) < 30:
                failed.append(tk); continue
            cp = float(df['Close'].iloc[-1])
            # 유동성 필터
            if min_avg_volume > 0:
                avg_vol = float(df['Volume'].tail(20).mean()) if 'Volume' in df.columns else 0
                if avg_vol < min_avg_volume:
                    failed.append(tk); continue
            mom_12m = (cp / float(df['Close'].iloc[-252]) - 1) * 100 if len(df) >= 252 else 0
            mom_1m = (cp / float(df['Close'].iloc[-21]) - 1) * 100 if len(df) >= 21 else 0
            daily_ret = df['Close'].pct_change().dropna()
            annual_vol = float(daily_ret.std()) * np.sqrt(252) * 100

            per, pbr, roe_v, pm_v, rg_v, name = None, None, 0, 0, 0, tk
            info = {}
            try:
                info = yf.Ticker(tk).info or {}
                _pit_snapshot(tk, info)
                per = info.get('trailingPE') or info.get('forwardPE')
                pbr = info.get('priceToBook')
                roe = info.get('returnOnEquity')
                roe_v = round(roe * 100, 2) if roe is not None else 0
                pm = info.get('profitMargins')
                pm_v = (pm*100 if pm else 0)
                rg = info.get('revenueGrowth')
                rg_v = (rg*100 if rg else 0)
                name = info.get('shortName', tk)[:20]
            except Exception:
                pass

            ep = (1.0/per*100) if per and per > 0 else 0
            bp = (1.0/pbr*100) if pbr and pbr > 0 else 0
            row = {
                'ticker': tk, 'name': name, 'price': cp,
                'momentum_raw': round(mom_12m - mom_1m, 2),
                'value_raw': round(ep*0.5+bp*0.5, 2),
                'quality_raw': round(roe_v*0.4+pm_v*0.3+rg_v*0.3, 2),
                'low_vol_raw': round(max(100-annual_vol, 0), 2),
                'vol': round(annual_vol, 1), 'per': per, 'pbr': pbr, 'roe': roe_v,
            }
            if extra_factors:
                row.update(_fetch_extra_factors(tk, info))
                try:
                    from modules.ict_analysis import ict_factor_score as _ict_fn
                    row["ict_raw"] = _ict_fn(df)
                except Exception:
                    row["ict_raw"] = 50.0
            results.append(row)
            if i < len(tickers) - 1:
                time.sleep(0.3)
        except Exception:
            failed.append(tk)
    if not results: return pd.DataFrame()
    rdf = pd.DataFrame(results)
    for col in ['momentum_raw', 'value_raw', 'quality_raw', 'low_vol_raw']:
        rdf[col.replace('_raw', '')] = _zscore_to_score(rdf[col])
    # 추가 팩터 정규화
    extra_cols, extra_w = [], []
    for col, w in [('analyst_raw', 0.10), ('short_raw', 0.05), ('eps_surprise_raw', 0.10), ('ict_raw', 0.10)]:
        if col in rdf.columns:
            fname = col.replace('_raw', '')
            rdf[fname] = _zscore_to_score(rdf[col])
            extra_cols.append(fname)
            extra_w.append(w)
    base_w = 1 - sum(extra_w)
    _fw = factor_weights or {'momentum': 0.30, 'value': 0.25, 'quality': 0.30, 'low_vol': 0.15}
    _fw_sum = sum(_fw.values()) or 1.0
    rdf['composite'] = (rdf['momentum'] * _fw.get('momentum', 0.30) / _fw_sum * base_w +
                        rdf['value']    * _fw.get('value',    0.25) / _fw_sum * base_w +
                        rdf['quality']  * _fw.get('quality',  0.30) / _fw_sum * base_w +
                        rdf['low_vol']  * _fw.get('low_vol',  0.15) / _fw_sum * base_w)
    for fname, w in zip(extra_cols, extra_w):
        rdf['composite'] += rdf[fname] * w
    rdf = rdf.sort_values('composite', ascending=False).reset_index(drop=True)
    rdf['rank'] = range(1, len(rdf)+1)
    if failed:
        rdf.attrs['failed'] = failed
    return rdf


@st.cache_data(ttl=1800, show_spinner=False)
def optimize_portfolio(tickers, method='equal', risk_free=0.045):
    """포트폴리오 최적화: equal/min_vol/risk_parity/max_sharpe"""
    end = datetime.now(); start = end - timedelta(days=390)
    prices = pd.DataFrame()
    valid_tickers = []
    for tk in tickers:
        try:
            df = download_stock(tk, start=start, end=end)
            if not df.empty and len(df) >= 60:
                prices[tk] = df['Close']
                valid_tickers.append(tk)
        except Exception:
            continue
    if len(valid_tickers) < 2: return {}, {}, pd.DataFrame()
    returns = prices.pct_change().dropna()
    n = len(valid_tickers)
    cov = returns.cov() * 252
    corr = returns.corr()
    mean_ret = returns.mean() * 252

    if method == 'equal':
        w = np.array([1.0/n]*n)
    elif method == 'min_vol':
        from scipy.optimize import minimize
        cons = {'type': 'eq', 'fun': lambda w: w.sum() - 1}
        bounds = [(0.02, 0.40)] * n
        x0 = np.array([1.0/n]*n)
        res = minimize(lambda w: np.sqrt(w @ cov.values @ w),
                       x0, method='SLSQP', bounds=bounds, constraints=cons)
        w = res.x if res.success else x0
    elif method == 'risk_parity':
        vol = np.sqrt(np.diag(cov.values))
        inv_vol = 1.0 / (vol + 1e-10)
        w = inv_vol / inv_vol.sum()
    elif method == 'max_sharpe':
        from scipy.optimize import minimize
        def neg_sharpe(w):
            ret = w @ mean_ret.values
            vol = np.sqrt(w @ cov.values @ w)
            return -(ret - risk_free) / (vol + 1e-10)
        cons = {'type': 'eq', 'fun': lambda w: w.sum() - 1}
        bounds = [(0.02, 0.40)] * n
        x0 = np.array([1.0/n]*n)
        res = minimize(neg_sharpe, x0, method='SLSQP', bounds=bounds, constraints=cons)
        w = res.x if res.success else x0
    else:
        w = np.array([1.0/n]*n)
    w = w / w.sum()
    port_ret = float(w @ mean_ret.values) * 100
    port_vol = float(np.sqrt(w @ cov.values @ w)) * 100
    port_sharpe = (port_ret/100 - risk_free) / (port_vol/100 + 1e-10)
    weights = {tk: round(float(wt), 4) for tk, wt in zip(valid_tickers, w)}
    stats = {'expected_return': round(port_ret, 2), 'volatility': round(port_vol, 2),
             'sharpe': round(port_sharpe, 2), 'n_assets': n}
    return weights, stats, corr


def generate_system_signals(tickers, factor_df=None, weights=None, top_n=5, capital=10000):
    """시스템 트레이딩 엔진: 규칙 기반 매수/매도/리밸런싱 시그널."""
    actions = []
    end = datetime.now()
    if factor_df is not None and not factor_df.empty:
        buy_candidates = factor_df.head(top_n)['ticker'].tolist()
        sell_candidates = factor_df.tail(max(len(factor_df)//3, 1))['ticker'].tolist()
    else:
        buy_candidates = tickers[:top_n]; sell_candidates = []

    for tk in tickers:
        try:
            df = download_stock(tk, start=end - timedelta(days=120), end=end)
            if df.empty or len(df) < 30: continue
            df = df.dropna(subset=['Close']); p = df['Close']; cp = float(p.iloc[-1])
            rsi = float(calc_rsi(p).iloc[-1])
            ma20 = float(p.rolling(20).mean().iloc[-1])
            ma60 = float(p.rolling(60).mean().iloc[-1])
            mom = calc_momentum(df); mom_3m = mom.get('3M', 0) or 0
            in_buy = tk in buy_candidates; in_sell = tk in sell_candidates
            trend_up = cp > ma20 > ma60; trend_dn = cp < ma20 < ma60
            oversold = rsi < 35; overbought = rsi > 70
            target_w = weights.get(tk, 0) if weights else (1.0/top_n if in_buy else 0)

            f_score_v = 0
            if factor_df is not None and not factor_df.empty:
                f_row = factor_df[factor_df['ticker'] == tk]
                if not f_row.empty:
                    f_score_v = float(f_row['composite'].iloc[0])

            is_top_factor = f_score_v >= 75
            is_strong_factor = f_score_v >= 50
            is_krx = tk.endswith('.KS') or tk.endswith('.KQ')
            fp = f"₩{cp:,.0f}" if is_krx else f"${cp:.2f}"

            def _make_action(action, tw, reason, priority):
                alloc = capital * tw
                qty = alloc / cp if cp > 0 else 0
                qty_str = f"{qty:,.0f}주" if is_krx else f"{qty:,.2f}주"
                alloc_str = f"₩{alloc:,.0f}" if is_krx else f"${alloc:,.0f}"
                return {'ticker': tk, 'action': action, 'weight': f"{tw*100:.1f}%",
                        'price': fp, 'alloc': alloc_str, 'qty': qty_str,
                        'reason': reason, 'priority': priority, 'mom': f"{mom_3m:+.1f}%"}

            if in_buy and is_top_factor and not overbought:
                actions.append(_make_action('🟢 매수', target_w,
                    f"팩터 {f_score_v:.0f}점 (최상위) — 추세 무관 진입 (RSI {rsi:.0f})", 'HIGH'))
            elif in_buy and (trend_up or oversold) and not overbought:
                actions.append(_make_action('🟢 매수', target_w,
                    f"팩터 {f_score_v:.0f}점 + {'과매도 반등' if oversold else '상승추세'} (RSI {rsi:.0f})",
                    'HIGH' if oversold else 'NORMAL'))
            elif in_buy and is_strong_factor and not overbought:
                actions.append(_make_action('🟡 조건부 매수', target_w * 0.7,
                    f"팩터 {f_score_v:.0f}점 — 추세 확인 시 비중 확대 (RSI {rsi:.0f})", 'NORMAL'))
            elif in_buy:
                actions.append(_make_action('🟡 대기', target_w,
                    f"팩터 {f_score_v:.0f}점, 추세·팩터 모두 약함 (RSI {rsi:.0f})", 'LOW'))
            elif in_sell or (trend_dn and overbought):
                actions.append(_make_action('🔴 매도', 0,
                    f"팩터 {f_score_v:.0f}점 하위{'+ 하락추세' if trend_dn else ''} (RSI {rsi:.0f})", 'HIGH'))
            elif trend_dn:
                actions.append(_make_action('🟠 비중축소', target_w * 0.5,
                    f"하락추세 (RSI {rsi:.0f})", 'NORMAL'))
            else:
                actions.append(_make_action('⚪ 관망', target_w,
                    f"팩터 {f_score_v:.0f}점 — 뚜렷한 방향 없음 (RSI {rsi:.0f})", 'LOW'))
        except Exception:
            continue

    rebal_days = 20 - (datetime.now().timetuple().tm_yday % 20)
    rebal_info = {
        'next_rebal': f"{rebal_days}일 후",
        'buy_count': sum(1 for a in actions if '매수' in a['action']),
        'sell_count': sum(1 for a in actions if '매도' in a['action'] or '축소' in a['action']),
        'hold_count': sum(1 for a in actions if '관망' in a['action'] or '대기' in a['action']),
    }
    return actions, rebal_info


UNIVERSE_PRESETS = {
    'S&P 500 대형 30': ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','BRK-B','JPM','V',
                        'JNJ','UNH','XOM','PG','HD','MA','ABBV','MRK','KO','PEP',
                        'COST','AVGO','LLY','WMT','MCD','CRM','ADBE','CSCO','ACN','TMO'],
    'NASDAQ 기술주 20': ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','AVGO','ADBE','CRM',
                        'AMD','INTC','QCOM','NFLX','PYPL','INTU','AMAT','MU','LRCX','SNPS'],
    '반도체 15': ['NVDA','AMD','INTC','TSM','ASML','QCOM','AVGO','MU','LRCX','AMAT',
                 'MRVL','ON','NXPI','TXN','KLAC'],
    '배당 귀족 15': ['JNJ','PG','KO','PEP','MMM','EMR','ABT','ADP','AFL','SHW',
                    'GD','ITW','ED','WMT','MCD'],
    '한국 대형 15': ['005930.KS','000660.KS','035420.KS','005380.KS','051910.KS',
                    '006400.KS','035720.KS','003670.KS','105560.KS','055550.KS',
                    '000270.KS','068270.KS','028260.KS','034730.KS','012330.KS'],
}


def get_factor_timing_weights():
    """VIX/금리 환경 기반 팩터 가중치 자동 조절.
    고변동성(VIX↑): 저변동성·퀄리티 강조 / 저변동성(VIX↓): 모멘텀 강조
    금리 상승기: 밸류 강조 / 금리 하락기: 모멘텀·성장 강조"""
    try:
        vix_df = yf.download('^VIX', period='3mo', progress=False)
        tnx_df = yf.download('^TNX', period='3mo', progress=False)
        for d in [vix_df, tnx_df]:
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.droplevel(1)
        vix = float(vix_df['Close'].iloc[-1]) if not vix_df.empty else 20
        vix_avg = float(vix_df['Close'].mean()) if not vix_df.empty else 20
        rate = float(tnx_df['Close'].iloc[-1]) if not tnx_df.empty else 4.0
        rate_chg = float(tnx_df['Close'].iloc[-1] - tnx_df['Close'].iloc[-30]) if len(tnx_df) >= 30 else 0
    except Exception:
        vix, vix_avg, rate, rate_chg = 20, 20, 4.0, 0

    if vix > 25:
        w = {'momentum': 0.15, 'value': 0.25, 'quality': 0.35, 'low_vol': 0.25}
        regime = '고변동성 — 퀄리티·저변동 강조'
    elif vix < 15:
        w = {'momentum': 0.40, 'value': 0.20, 'quality': 0.25, 'low_vol': 0.15}
        regime = '저변동성 — 모멘텀 강조'
    else:
        w = {'momentum': 0.30, 'value': 0.25, 'quality': 0.30, 'low_vol': 0.15}
        regime = '보통'

    if rate_chg > 0.3:
        w['value'] = min(w['value'] + 0.10, 0.40)
        w['momentum'] = max(w['momentum'] - 0.10, 0.10)
        regime += ' + 금리상승(밸류↑)'
    elif rate_chg < -0.3:
        w['momentum'] = min(w['momentum'] + 0.10, 0.45)
        w['value'] = max(w['value'] - 0.10, 0.10)
        regime += ' + 금리하락(모멘텀↑)'

    total = sum(w.values())
    w = {k: round(v/total, 2) for k, v in w.items()}
    env = {'vix': round(vix, 1), 'vix_avg': round(vix_avg, 1),
           'rate': round(rate, 2), 'rate_chg': round(rate_chg, 2), 'regime': regime}
    return w, env


def calc_factor_scores_sectoral(tickers, factor_weights=None, prog_bar=None, prog_text=None):
    """섹터 중립 멀티팩터 랭킹. 섹터 내 Z-score 정규화로 섹터 편향 제거."""
    import time
    if factor_weights is None:
        factor_weights = {'momentum': 0.30, 'value': 0.25, 'quality': 0.30, 'low_vol': 0.15}
    end = datetime.now(); start = end - timedelta(days=520)
    results = []
    for i, tk in enumerate(tickers):
        if prog_text: prog_text.text(f"팩터 분석: {tk} ({i+1}/{len(tickers)})")
        if prog_bar: prog_bar.progress((i+1)/len(tickers))
        try:
            df = download_stock(tk, start=start, end=end)
            if df is None or df.empty: continue
            df = df.dropna(subset=['Close'])
            if len(df) < 30: continue
            cp = float(df['Close'].iloc[-1])
            daily_ret = df['Close'].pct_change().dropna()
            annual_vol = float(daily_ret.std()) * np.sqrt(252) * 100
            mom_12m = (cp / float(df['Close'].iloc[-252]) - 1) * 100 if len(df) >= 252 else 0
            mom_1m = (cp / float(df['Close'].iloc[-21]) - 1) * 100 if len(df) >= 21 else 0

            sector, per, pbr, roe_v, pm_v, rg_v, name = 'Unknown', None, None, 0, 0, 0, tk
            try:
                info = yf.Ticker(tk).info or {}
                sector = info.get('sector', 'Unknown')
                per = info.get('trailingPE') or info.get('forwardPE')
                pbr = info.get('priceToBook')
                roe = info.get('returnOnEquity')
                roe_v = round(roe * 100, 2) if roe is not None else 0
                pm = info.get('profitMargins')
                pm_v = (pm*100 if pm else 0)
                rg = info.get('revenueGrowth')
                rg_v = (rg*100 if rg else 0)
                name = info.get('shortName', tk)[:20]
            except Exception:
                pass

            ep = (1.0/per*100) if per and per > 0 else 0
            bp = (1.0/pbr*100) if pbr and pbr > 0 else 0
            results.append({
                'ticker': tk, 'name': name, 'price': cp, 'sector': sector,
                'momentum_raw': mom_12m - mom_1m, 'value_raw': ep*0.5+bp*0.5,
                'quality_raw': roe_v*0.4+pm_v*0.3+rg_v*0.3,
                'low_vol_raw': max(100-annual_vol, 0),
                'vol': round(annual_vol, 1), 'per': per, 'pbr': pbr, 'roe': roe_v,
            })
            if i < len(tickers) - 1:
                time.sleep(0.3)
        except Exception:
            continue
    if not results: return pd.DataFrame()
    rdf = pd.DataFrame(results)
    for col in ['momentum_raw','value_raw','quality_raw','low_vol_raw']:
        fname = col.replace('_raw','')
        rdf[f'{fname}_global'] = _zscore_to_score(rdf[col])
        def _sector_zscore(g):
            if len(g) < 3: return pd.Series(np.nan, index=g.index)
            return _zscore_to_score(g)
        rdf[fname] = rdf.groupby('sector')[col].transform(_sector_zscore)
        rdf[fname] = rdf[fname].fillna(rdf[f'{fname}_global'])

    rdf['composite'] = sum(rdf[k] * v for k, v in factor_weights.items())
    rdf = rdf.sort_values('composite', ascending=False).reset_index(drop=True)
    rdf['rank'] = range(1, len(rdf)+1)
    return rdf


def backtest_factor_strategy(tickers, top_n=5, years=3, rebal_months=1,
                              factor_weights=None, commission=0.001, slippage=0.0005,
                              pit_safe=True):
    """팩터 전략 백테스트: 매월 팩터 Top N 매수, 리밸런싱.

    [수정: look-ahead bias 제거]
    - 기존 코드는 yfinance의 "현재 시점" PER/PBR/ROE 등으로 value/quality
      점수를 딱 한 번 계산해서, 3년치(등) 과거 전체 리밸런싱에 동일하게 적용했다.
      이는 "3년 전 시점에 오늘자 재무제표를 미리 알고 있었다"는 것과 같은
      명백한 미래정보 유출(look-ahead bias)이며 백테스트 성과를 부풀린다.
    - yfinance 무료 API는 과거 시점(point-in-time) 재무데이터를 제공하지
      않기 때문에, 근본적인 해결책은 유료 PIT 데이터 소스를 쓰는 것뿐이다.
    - 임시(안전) 해결책: pit_safe=True(기본값)이면 value/quality처럼
      시점별 재계산이 불가능한 팩터는 백테스트 랭킹에서 제외하고,
      momentum/low_vol처럼 "그 시점 가격 데이터만으로" 매 리밸런싱마다
      다시 계산 가능한 팩터만 사용한다.
    - pit_safe=False로 두면 기존 방식(value/quality 포함)도 쓸 수 있지만,
      결과 해석 시 look-ahead bias가 섞여 있다는 점을 감안해야 한다.
    """
    if factor_weights is None:
        factor_weights = {'momentum': 0.30, 'value': 0.25, 'quality': 0.30, 'low_vol': 0.15}

    pit_note = None
    if pit_safe:
        dropped = [f for f in ('value', 'quality') if factor_weights.get(f, 0) > 0]
        factor_weights = {k: v for k, v in factor_weights.items() if k in ('momentum', 'low_vol')}
        w_sum = sum(factor_weights.values()) or 1.0
        factor_weights = {k: v / w_sum for k, v in factor_weights.items()}
        if dropped:
            pit_note = (f"{', '.join(dropped)} 팩터는 과거 시점(point-in-time) 재무데이터가 없어 "
                        f"백테스트에서 제외했습니다 (look-ahead bias 방지). 현재 momentum/low_vol만 "
                        f"{factor_weights}로 재정규화하여 사용합니다.")

    end = datetime.now()
    start = end - timedelta(days=years*365+60)

    all_prices = {}
    ticker_info = {}   # 참고용(현재 시점 스냅샷) — pit_safe=False일 때만 백테스트에 실제 사용됨
    for tk in tickers:
        try:
            df = download_stock(tk, start=start, end=end)
            if not df.empty and len(df) >= 60:
                all_prices[tk] = df['Close']
            info = yf.Ticker(tk).info
            if info:
                _pit_snapshot(tk, info)
                per = info.get('trailingPE') or info.get('forwardPE')
                pbr = info.get('priceToBook')
                roe = info.get('returnOnEquity')
                pm = info.get('profitMargins')
                rg = info.get('revenueGrowth')
                ep = (1.0/per*100) if per and per > 0 else 0
                bp = (1.0/pbr*100) if pbr and pbr > 0 else 0
                roe_v = round(roe * 100, 2) if roe is not None else 0
                pm_v = (pm*100 if pm else 0)
                rg_v = (rg*100 if rg else 0)
                ticker_info[tk] = {
                    'value': ep*0.5 + bp*0.5,
                    'quality': roe_v*0.4 + pm_v*0.3 + rg_v*0.3,
                }
        except Exception:
            continue
    if len(all_prices) < top_n + 2: return {}, pd.DataFrame(), []

    price_df = pd.DataFrame(all_prices).dropna(how='all').ffill()

    if len(price_df) < 61:
        return {}, pd.DataFrame(), []
    _start_idx = price_df.index[252] if len(price_df) >= 252 else price_df.index[60]
    months = pd.date_range(start=_start_idx, end=price_df.index[-1], freq=f'{rebal_months}MS')

    equity = 10000.0
    eq_history = []
    holdings = []
    trade_log = []
    total_turnover = 0.0

    for mi, month_start in enumerate(months):
        avail = price_df.loc[:month_start].tail(252)
        if len(avail) < 60: continue

        scores = {}
        for tk in all_prices:
            if tk not in avail.columns: continue
            col = avail[tk].dropna()
            if len(col) < 60: continue
            cp_m = float(col.iloc[-1])
            mom12 = (cp_m / float(col.iloc[-252])-1)*100 if len(col) >= 252 else 0
            mom1  = (cp_m / float(col.iloc[-21])-1)*100 if len(col) >= 21 else 0
            vol_m = float(col.pct_change().dropna().std()) * np.sqrt(252) * 100
            ti = ticker_info.get(tk, {})
            scores[tk] = {
                'momentum': mom12 - mom1,
                'value': ti.get('value', 50),
                'quality': ti.get('quality', 50),
                'low_vol': max(100-vol_m, 0),
            }
        if len(scores) < top_n: continue

        sdf = pd.DataFrame(scores).T
        for f in ['momentum','value','quality','low_vol']:
            sdf[f] = _zscore_to_score(sdf[f])
        sdf['composite'] = sum(sdf[f]*factor_weights.get(f, 0.25) for f in factor_weights)
        top = sdf.nlargest(top_n, 'composite').index.tolist()

        old_set = set(holdings)
        new_set = set(top)
        turnover = len(old_set.symmetric_difference(new_set)) / max(len(new_set), 1)
        total_turnover += turnover
        # 왕복 비용: 수수료 + 슬리피지 각 편도, 매도·매수 양쪽 적용
        round_trip_cost = 2 * commission + 2 * slippage
        cost = equity * turnover * round_trip_cost
        equity -= cost

        month_end_idx = price_df.index[price_df.index >= month_start]
        if mi + 1 < len(months):
            next_month = months[mi+1]
            period_idx = price_df.index[(price_df.index >= month_start) & (price_df.index < next_month)]
        else:
            period_idx = price_df.index[price_df.index >= month_start]

        if len(period_idx) == 0: continue
        period_ret = price_df.loc[period_idx, top].pct_change().mean(axis=1).fillna(0)
        for d, r in period_ret.items():
            equity *= (1 + r)
            eq_history.append({'date': d, 'equity': equity})

        trade_log.append({
            'date': month_start.strftime('%Y-%m'),
            'holdings': ', '.join(top),
            'turnover': f"{turnover*100:.0f}%",
            'cost': f"{cost:.0f}",
        })
        holdings = top

    if not eq_history: return {}, pd.DataFrame(), trade_log
    eq_df = pd.DataFrame(eq_history)

    total_ret = (equity / 10000 - 1) * 100
    y = max(years, 0.01)
    cagr = ((equity/10000)**(1/y)-1)*100
    eq_s = eq_df['equity']
    roll_max = eq_s.expanding().max()
    mdd = float(((eq_s - roll_max)/roll_max*100).min())
    daily_r = eq_s.pct_change().dropna()
    sharpe = float(daily_r.mean()/daily_r.std()*np.sqrt(252)) if daily_r.std() > 0 else 0
    avg_turnover = total_turnover / max(len(trade_log), 1) * 100
    round_trip_cost = 2 * commission + 2 * slippage
    total_cost_pct = total_turnover * round_trip_cost * 100

    spy_df = download_stock('SPY', start=start, end=end)
    spy_ret = 0
    if not spy_df.empty:
        _spy_slice = spy_df['Close'].loc[spy_df.index >= eq_df['date'].iloc[0]]
        if not _spy_slice.empty:
            spy_ret = (float(spy_df['Close'].iloc[-1]) / float(_spy_slice.iloc[0]) - 1) * 100

    metrics = {
        'total_return': round(total_ret, 1), 'cagr': round(cagr, 1),
        'mdd': round(mdd, 1), 'sharpe': round(sharpe, 2),
        'avg_turnover': round(avg_turnover, 1), 'total_cost': round(total_cost_pct, 2),
        'spy_return': round(spy_ret, 1), 'alpha': round(total_ret - spy_ret, 1),
        'n_rebalances': len(trade_log),
        'pit_note': pit_note,   # None이면 제한사항 없음(또는 pit_safe=False)
    }
    return metrics, eq_df, trade_log

# ─────────────────────────────────────────────
# GOOGLE SHEETS 연동 (매매 일지 영속 저장)
# ─────────────────────────────────────────────

def _gs_configured():
    """Streamlit secrets에 GS 설정이 있는지 확인."""
    try:
        return ("gcp_service_account" in st.secrets and
                "google_sheets" in st.secrets and
                "spreadsheet_id" in st.secrets["google_sheets"])
    except Exception:
        return False

@st.cache_resource(show_spinner=False)
def _gs_client():
    """gspread 클라이언트. 인증 실패 시 None 반환."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(
            dict(st.secrets["gcp_service_account"]), scopes=scopes)
        return gspread.authorize(creds)
    except Exception:
        return None

def _gs_load_trades():
    """Google Sheets에서 매매 기록 로드. 실패 시 None."""
    gc = _gs_client()
    if gc is None:
        return None
    try:
        sh = gc.open_by_key(st.secrets["google_sheets"]["spreadsheet_id"])
        ws_name = st.secrets["google_sheets"].get("worksheet_name", "trades")
        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            return []
        records = ws.get_all_records()
        # 빈 문자열 → None 변환 (수치 컬럼 복원)
        cleaned = []
        for r in records:
            cleaned.append({k: (None if v == '' else v) for k, v in r.items()})
        return cleaned
    except Exception:
        return None

def _gs_save_trades(trades):
    """매매 기록을 Google Sheets에 저장. 성공 시 True."""
    gc = _gs_client()
    if gc is None:
        return False
    try:
        sh = gc.open_by_key(st.secrets["google_sheets"]["spreadsheet_id"])
        ws_name = st.secrets["google_sheets"].get("worksheet_name", "trades")
        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            ws = sh.add_worksheet(title=ws_name, rows=2000, cols=20)
        ws.clear()
        if trades:
            df_gs = pd.DataFrame(trades).fillna('')
            ws.update([df_gs.columns.tolist()] + df_gs.values.tolist())
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────

def _draw_chart_legacy(df, ticker, is_krw):
    p, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']
    m20  = p.rolling(20).mean(); m60 = p.rolling(60).mean(); m120 = p.rolling(120).mean()
    bb_u, bb_mid, bb_l = calc_bb(p)
    macd_l, sig_l, hist = calc_macd(p)
    rsi_s = calc_rsi(p)
    sk_s, sd_s = calc_stochastic(h, l, p)

    vol_c = [TV_UP if float(p.iloc[i]) >= float(df['Open'].iloc[i]) else TV_DOWN for i in range(len(df))]
    vol_a = [0.7 if float(p.iloc[i]) >= float(df['Open'].iloc[i]) else 0.5 for i in range(len(df))]
    fp = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"
    cp = float(p.iloc[-1])

    fig = make_subplots(rows=5, cols=1, shared_xaxes=True,
                        row_heights=[0.45, 0.10, 0.15, 0.15, 0.15], vertical_spacing=0.015,
                        subplot_titles=None)

    # ── 1) 캔들 + MA + BB ───────────────────────
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=h, low=l, close=p,
        name='', increasing=dict(line=dict(color=TV_UP,width=1), fillcolor=TV_UP),
        decreasing=dict(line=dict(color=TV_DOWN,width=1), fillcolor=TV_DOWN),
        showlegend=False), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=bb_u, name='BB', showlegend=False,
        line=dict(color='rgba(149,117,205,0.5)', width=0.8), legendgroup='bb'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb_l, showlegend=False,
        line=dict(color='rgba(149,117,205,0.5)', width=0.8),
        fill='tonexty', fillcolor='rgba(149,117,205,0.05)', legendgroup='bb'), row=1, col=1)

    for ma_s, c, nm in [(m20,'#f5c518','MA20'),(m60,'#2962ff','MA60'),(m120,'#ff6d00','MA120')]:
        last = float(ma_s.iloc[-1]) if not np.isnan(float(ma_s.iloc[-1])) else None
        fig.add_trace(go.Scatter(x=df.index, y=ma_s, name=nm, showlegend=False,
                                 line=dict(color=c, width=1.2)), row=1, col=1)
        if last:
            fig.add_annotation(x=df.index[-1], y=last, text=f" {nm} {fp(last)}",
                showarrow=False, xanchor='left', font=dict(color=c, size=9),
                xref='x', yref='y')

    fig.add_annotation(x=df.index[-1], y=cp,
        text=f"  {fp(cp)}", showarrow=False, xanchor='left',
        font=dict(color='#FFD700', size=11, family='monospace'),
        bgcolor='#ffffff', bordercolor='#FFD700', borderwidth=1, borderpad=2,
        xref='x', yref='y')
    fig.add_hline(y=cp, line_dash='dot', line_color='#FFD700', line_width=0.8, row=1, col=1)

    fig.add_annotation(text=ticker, x=0.5, y=0.5, xref='paper', yref='y',
        showarrow=False, font=dict(color='rgba(0,0,0,0.04)', size=72),
        xanchor='center', yanchor='middle')

    o_l, h_l, l_l, c_l = float(df['Open'].iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1]), cp
    chg_d = c_l - float(p.iloc[-2]) if len(p) >= 2 else 0
    chg_p = chg_d / float(p.iloc[-2]) * 100 if len(p) >= 2 else 0
    chg_c = TV_UP if chg_d >= 0 else TV_DOWN
    fig.add_annotation(
        text=(f"<b>O</b> {fp(o_l)}  <b>H</b> {fp(h_l)}  <b>L</b> {fp(l_l)}  "
              f"<b>C</b> {fp(c_l)}  <span style='color:{chg_c}'>{chg_d:+.2f} ({chg_p:+.2f}%)</span>"),
        x=0.003, y=1.0, xref='paper', yref='y domain',
        showarrow=False, font=dict(color=TV_TEXT, size=11, family='monospace'),
        xanchor='left', yanchor='top', bgcolor='rgba(255,255,255,0.9)')

    # ── 2) 거래량 ────────────────────────────────
    fig.add_trace(go.Bar(x=df.index, y=v, name='', showlegend=False,
        marker_color=vol_c, marker_opacity=vol_a), row=2, col=1)
    vol_ma = v.rolling(20).mean()
    fig.add_trace(go.Scatter(x=df.index, y=vol_ma, name='', showlegend=False,
        line=dict(color='#ff9800', width=0.8, dash='dot')), row=2, col=1)

    # ── 3) MACD ──────────────────────────────────
    h_colors = [TV_UP if float(hist.iloc[i]) >= 0 else TV_DOWN for i in range(len(hist))]
    fig.add_trace(go.Bar(x=df.index, y=hist, name='', showlegend=False,
        marker_color=h_colors, opacity=0.6), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=macd_l, name='', showlegend=False,
        line=dict(color='#2962ff', width=1.3)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig_l, name='', showlegend=False,
        line=dict(color='#ff6d00', width=1.3)), row=3, col=1)
    fig.add_hline(y=0, line_color=TV_BORDER, line_width=0.8, row=3, col=1)
    macd_v = float(macd_l.iloc[-1]); sig_v = float(sig_l.iloc[-1])
    fig.add_annotation(x=df.index[-1], y=macd_v, text=f" MACD {macd_v:.2f}",
        showarrow=False, xanchor='left', font=dict(color='#2962ff', size=9), xref='x3', yref='y3')
    fig.add_annotation(x=df.index[-1], y=sig_v, text=f" SIG {sig_v:.2f}",
        showarrow=False, xanchor='left', font=dict(color='#ff6d00', size=9), xref='x3', yref='y3')

    # ── 4) RSI ───────────────────────────────────
    fig.add_hrect(y0=30, y1=70, fillcolor='rgba(0,0,0,0.03)', line_width=0, row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=rsi_s, name='', showlegend=False,
        line=dict(color='#ce93d8', width=1.4)), row=4, col=1)
    fig.add_hline(y=70, line_color=TV_DOWN, line_width=0.7, line_dash='dash', row=4, col=1)
    fig.add_hline(y=30, line_color=TV_UP, line_width=0.7, line_dash='dash', row=4, col=1)
    fig.add_hline(y=50, line_color=TV_BORDER, line_width=0.5, row=4, col=1)
    rsi_v = float(rsi_s.iloc[-1])
    rsi_c = TV_DOWN if rsi_v > 70 else (TV_UP if rsi_v < 30 else TV_TEXT)
    fig.add_annotation(x=df.index[-1], y=rsi_v, text=f" RSI {rsi_v:.1f}",
        showarrow=False, xanchor='left', font=dict(color=rsi_c, size=10, family='monospace'),
        xref='x4', yref='y4')

    # ── 5) 스토캐스틱 ────────────────────────────
    fig.add_hrect(y0=20, y1=80, fillcolor='rgba(0,0,0,0.03)', line_width=0, row=5, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sk_s, name='', showlegend=False,
        line=dict(color='#42a5f5', width=1.3)), row=5, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sd_s, name='', showlegend=False,
        line=dict(color='#ef5350', width=1.0, dash='dot')), row=5, col=1)
    fig.add_hline(y=80, line_color=TV_DOWN, line_width=0.7, line_dash='dash', row=5, col=1)
    fig.add_hline(y=20, line_color=TV_UP, line_width=0.7, line_dash='dash', row=5, col=1)
    sk_v = float(sk_s.iloc[-1]); sd_v = float(sd_s.iloc[-1])
    fig.add_annotation(x=df.index[-1], y=sk_v, text=f" %K {sk_v:.0f}",
        showarrow=False, xanchor='left', font=dict(color='#42a5f5', size=9), xref='x5', yref='y5')
    fig.add_annotation(x=df.index[-1], y=sd_v, text=f" %D {sd_v:.0f}",
        showarrow=False, xanchor='left', font=dict(color='#ef5350', size=9), xref='x5', yref='y5')

    # ── 패널 라벨 ────────────────────────────────
    for rn, lbl in [(1,''),(2,'Vol'),(3,'MACD'),(4,'RSI'),(5,'Stoch')]:
        if lbl:
            fig.add_annotation(text=lbl, xref='paper', yref=f'y{rn}',
                x=0.003, y=1, showarrow=False,
                font=dict(color='rgba(178,181,190,0.6)', size=10), xanchor='left', yanchor='top')

    # ── 레이아웃 ─────────────────────────────────
    ax = dict(gridcolor=TV_GRID, gridwidth=1, zerolinecolor=TV_BORDER,
              tickfont=dict(color=TV_TEXT, size=9), showline=True, linecolor=TV_BORDER, side='right')
    fig.update_layout(
        height=900, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
        font=dict(color=TV_TEXT, family='Inter,sans-serif', size=11),
        xaxis_rangeslider_visible=False, hovermode='x unified',
        hoverlabel=dict(bgcolor='#ffffff', font_color=TV_TEXT, bordercolor=TV_BORDER, font_size=11),
        legend=dict(visible=False),
        margin=dict(l=0, r=80, t=10, b=0),
    )
    for i in range(1, 6):
        fig.update_xaxes(row=i, col=1, gridcolor=TV_GRID, showgrid=True,
                         tickfont=dict(color=TV_TEXT, size=9), showline=True, linecolor=TV_BORDER,
                         showticklabels=(i == 5))
        fig.update_yaxes(row=i, col=1, **ax)
    fig.update_yaxes(row=4, col=1, range=[0, 100])
    fig.update_yaxes(row=5, col=1, range=[0, 100])
    return fig


def main():
    st.markdown("""
<div style="display:flex;align-items:center;gap:12px;padding:4px 0 12px 0;
            border-bottom:2px solid #e2e8f0;margin-bottom:18px">
  <div style="width:40px;height:40px;border-radius:10px;
              background:linear-gradient(135deg,#10b981,#059669);
              display:flex;align-items:center;justify-content:center;
              font-size:20px;flex-shrink:0">📈</div>
  <div>
    <div style="font-size:1.45rem;font-weight:800;color:#0f172a;
                letter-spacing:-0.5px;line-height:1.2">퀀트 트레이딩 시스템</div>
    <div style="font-size:12.5px;color:#64748b;margin-top:2px">
      종목 분석 &nbsp;·&nbsp; 팩터 퀀트 &nbsp;·&nbsp; 토스증권 자동매매 &nbsp;·&nbsp; 리스크 관리
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    # 사이드바 제거 — 값 하드코딩
    w_tech, w_fund, w_macro = 35, 40, 25
    total_w          = 100
    acct_capital     = 10_000_000
    risk_per_trade   = 1.0
    max_position_pct = 20
    min_rr           = 1.5

    tab1, tab6, tab_journal = st.tabs(["종목 분석", "퀀트 · 자동매매", "매매 일지"])

    # ── Tab 1: 단일 종목 분석 ─────────────────
    with tab1:
        st.markdown("""
<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;
            padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:#64748b;
              text-transform:uppercase;letter-spacing:.7px;margin-bottom:10px">
    종목 검색
  </div>
""", unsafe_allow_html=True)
        c_mkt, c_tkr, c_btn1, c_btn2 = st.columns([2, 3, 1, 1])
        with c_mkt:
            market = st.selectbox("시장", ["미국 (NYSE/NASDAQ)", "한국 (KRX)", "ETF/인덱스"],
                                  label_visibility="collapsed")
        with c_tkr:
            if market == "한국 (KRX)":
                ca, cb = st.columns([2,1])
                ticker_raw = ca.text_input("종목코드", placeholder="005930",
                                           label_visibility="collapsed")
                sfx = ".KS" if "KS" in cb.radio("거래소", [".KS",".KQ"], horizontal=True,
                                                   label_visibility="collapsed") else ".KQ"
                ticker = (ticker_raw.strip()+sfx).upper() if ticker_raw else ""
            elif market == "미국 (NYSE/NASDAQ)":
                ticker = st.text_input("티커", placeholder="AAPL  /  NVDA  /  TSLA",
                                       label_visibility="collapsed").strip().upper()
            else:
                ticker = st.text_input("ETF 티커", placeholder="SPY  /  QQQ  /  GLD",
                                       label_visibility="collapsed").strip().upper()
        with c_btn1:
            run = st.button("분석 시작", type="primary", disabled=(not ticker), use_container_width=True)
        with c_btn2:
            refresh = st.button("새로고침", disabled=('tab1' not in st.session_state), use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
        if refresh:
            if 'tab1' in st.session_state:
                del st.session_state['tab1']
            run = True

        if run:
            prog = st.progress(0); msg = st.empty()
            msg.text("📥 데이터 다운로드 중...")
            prog.progress(5)

            end_dt   = datetime.now()
            start_dt = end_dt - timedelta(days=520)
            _df = download_stock(ticker, start=start_dt, end=end_dt)

            if _df.empty:
                st.error(f"'{ticker}' 데이터를 찾을 수 없습니다.")
                prog.empty(); msg.empty()
            else:
                _df = _df.dropna(subset=['Close'])
                if len(_df) < 30:
                    st.error("데이터 부족 (30일 미만).")
                    prog.empty(); msg.empty()
                else:
                    prog.progress(15); msg.text("📈 차트·파동 분석 중...")
                    _t_score, _t_det  = technical_score(_df)
                    _candle_pats      = detect_candle_patterns(_df)
                    _mom_data         = calc_momentum(_df)
                    _ic_data          = calc_indicator_ics(_df)
                    prog.progress(35); msg.text("💰 재무제표·퀀트 분석 중...")
                    _f_score, _f_det  = fundamental_score(ticker, _df)
                    prog.progress(52); msg.text("🌍 매크로·금리 분석 중...")
                    _m_score, _m_det, _m_data = macro_score()
                    _regime, _regime_diff = get_market_regime()
                    prog.progress(63); msg.text("🕐 멀티 타임프레임 분석 중...")
                    _mtf_scores      = technical_score_multi(ticker)
                    prog.progress(70); msg.text("📊 점수 최적화 중...")

                    _ics, _suggested_w, _default_w = _ic_data
                    _abs_ics = {k: max(abs(v), 0.001) for k, v in _ics.items()}
                    _ic_total = sum(_abs_ics.values())
                    _ic_w = {k: max(_abs_ics[k] / _ic_total, 0.03) for k in _abs_ics}
                    _ic_w_sum = sum(_ic_w.values())
                    _ic_w = {k: v / _ic_w_sum for k, v in _ic_w.items()}

                    _t_score_ic = float(sum(_t_det.get(k, 50) * _ic_w.get(k, 0.11)
                                            for k in ['MA정렬','RSI','MACD','볼린저밴드','거래량',
                                                       '파동근사','스토캐스틱','ADX추세강도','OBV']))

                    _t_score_regime = regime_adjusted_technical(_t_det, _regime)

                    _t_score_final = _t_score_ic * 0.5 + _t_score_regime * 0.5

                    _mtf_list = [x['score'] for x in [_mtf_scores.get('일봉'),
                                 _mtf_scores.get('주봉'), _mtf_scores.get('월봉')] if x]
                    _mtf_bonus = 0.0
                    if len(_mtf_list) >= 2:
                        _mtf_avg = sum(_mtf_list) / len(_mtf_list)
                        _mtf_bull = sum(1 for s in _mtf_list if s >= 60)
                        _mtf_bear = sum(1 for s in _mtf_list if s < 40)
                        if _mtf_bull == len(_mtf_list):
                            _mtf_bonus = 5.0
                        elif _mtf_bear == len(_mtf_list):
                            _mtf_bonus = -5.0
                        elif _mtf_bull > _mtf_bear:
                            _mtf_bonus = 2.0
                        elif _mtf_bear > _mtf_bull:
                            _mtf_bonus = -2.0

                    _total_raw = _t_score_final*(w_tech/100) + _f_score*(w_fund/100) + _m_score*(w_macro/100)
                    _total = float(np.clip(_total_raw + _mtf_bonus, 0, 100))

                    _t_score_adj = _t_score_regime
                    _total_adj = _t_score_adj*(w_tech/100) + _f_score*(w_fund/100) + _m_score*(w_macro/100)

                    _score_method = (f"IC적응({sum(1 for v in _ics.values() if abs(v)>=0.05)}개 유효) "
                                    f"+ 국면({_regime}) + MTF({_mtf_bonus:+.0f})")

                    prog.progress(74); msg.text("💵 DCF 내재가치 산출 중...")
                    _dcf_val, _dcf_det = calc_dcf(ticker, _m_data.get('10Y금리', 4.5))
                    prog.progress(84); msg.text("⚠️ 리스크 분석 중...")
                    _risk_data       = calc_risk_metrics(ticker)
                    prog.progress(93); msg.text("📰 뉴스 감성 분석 중...")
                    _news_score, _news_articles = get_news_sentiment(ticker)

                    try:
                        _info = yf.Ticker(ticker).info
                        _pit_snapshot(ticker, _info)
                        _name = _info.get('longName') or _info.get('shortName') or ticker
                    except: _info, _name = {}, ticker

                    _is_krw = ticker.endswith('.KS') or ticker.endswith('.KQ')
                    _reg_price  = _info.get('regularMarketPrice') or _info.get('currentPrice')
                    _cp  = float(_reg_price) if _reg_price else float(_df['Close'].iloc[-1])
                    _pp  = float(_info.get('regularMarketPreviousClose') or (_df['Close'].iloc[-2] if len(_df) >= 2 else _cp))
                    _pre_price  = _info.get('preMarketPrice')
                    _pre_chg    = _info.get('preMarketChangePercent')
                    _post_price = _info.get('postMarketPrice')
                    _post_chg   = _info.get('postMarketChangePercent')
                    _live_price = _cp
                    _live_label = '현재가'
                    if _post_price and _post_price > 0:
                        _live_price = float(_post_price)
                        _live_label = '현재가 (애프터)'
                    elif _pre_price and _pre_price > 0:
                        _live_price = float(_pre_price)
                        _live_label = '현재가 (프리마켓)'

                    _earn_str = ""
                    try:
                        cal = yf.Ticker(ticker).calendar
                        if cal is not None and not (isinstance(cal, dict) and len(cal) == 0):
                            if isinstance(cal, dict):
                                ed = cal.get('Earnings Date')
                                if ed:
                                    ed = ed[0] if isinstance(ed, list) else ed
                                    days_left = (pd.Timestamp(ed).date() - datetime.now().date()).days
                                    _earn_str = f"다음 실적발표: **{pd.Timestamp(ed).strftime('%Y-%m-%d')}** ({days_left:+d}일)"
                                    if 0 <= days_left <= 14: _earn_str += " 🔔"
                            elif isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
                                ed = cal.loc['Earnings Date'].iloc[0]
                                days_left = (pd.Timestamp(ed).date() - datetime.now().date()).days
                                _earn_str = f"다음 실적발표: **{pd.Timestamp(ed).strftime('%Y-%m-%d')}** ({days_left:+d}일)"
                                if 0 <= days_left <= 14: _earn_str += " 🔔"
                    except Exception:
                        pass

                    prog.progress(100); prog.empty(); msg.empty()

                    st.session_state['tab1'] = {
                        'ticker': ticker, 'df': _df, 'end_dt': end_dt,
                        't_score': _t_score, 't_det': _t_det, 'candle_pats': _candle_pats,
                        'mom_data': _mom_data, 'ic_data': _ic_data,
                        'f_score': _f_score, 'f_det': _f_det,
                        'm_score': _m_score, 'm_det': _m_det, 'm_data': _m_data,
                        'total': _total, 'mtf_scores': _mtf_scores,
                        'dcf_val': _dcf_val, 'dcf_det': _dcf_det,
                        'risk_data': _risk_data,
                        'news_score': _news_score, 'news_articles': _news_articles,
                        'regime': _regime, 'regime_diff': _regime_diff,
                        't_score_adj': _t_score_adj, 'total_adj': _total_adj,
                        'info': _info, 'name': _name,
                        'is_krw': _is_krw, 'cp': _cp, 'pp': _pp,
                        'live_price': _live_price, 'live_label': _live_label,
                        'pre_price': _pre_price, 'pre_chg': _pre_chg,
                        'post_price': _post_price, 'post_chg': _post_chg,
                        'score_method': _score_method, 'earn_str': _earn_str,
                        'w_tech': w_tech, 'w_fund': w_fund, 'w_macro': w_macro,
                    }

        if 'tab1' not in st.session_state:
            st.info("티커를 입력하고 **분석 시작** 버튼을 눌러주세요.\n\n"
                    "예) `AAPL` `NVDA` `TSLA` | 한국: `005930` (삼성전자) | ETF: `SPY` `QQQ`")
        else:
            _a = st.session_state['tab1']
            ticker    = _a['ticker']; df = _a['df']; end_dt = _a['end_dt']
            t_score   = _a['t_score']; t_det = _a['t_det']; candle_pats = _a['candle_pats']
            mom_data  = _a['mom_data']; ic_data = _a['ic_data']
            f_score   = _a['f_score']; f_det = _a['f_det']
            m_score   = _a['m_score']; m_det = _a['m_det']; m_data = _a['m_data']
            total     = _a['total']; mtf_scores = _a['mtf_scores']
            dcf_val   = _a['dcf_val']; dcf_det = _a['dcf_det']
            risk_data = _a['risk_data']
            news_score = _a['news_score']; news_articles = _a['news_articles']
            regime    = _a['regime']; regime_diff = _a['regime_diff']
            t_score_adj = _a['t_score_adj']; total_adj = _a['total_adj']
            info      = _a['info']; name = _a['name']
            is_krw    = _a['is_krw']; cp = _a['cp']; pp = _a['pp']
            live_price = _a.get('live_price', cp)
            live_label = _a.get('live_label', '현재가')
            pre_price = _a.get('pre_price'); pre_chg = _a.get('pre_chg')
            post_price = _a.get('post_price'); post_chg = _a.get('post_chg')
            score_method = _a.get('score_method', '')
            earn_str  = _a['earn_str']
            w_tech    = _a['w_tech']; w_fund = _a['w_fund']; w_macro = _a['w_macro']

            fmt_p  = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"
            chg = (cp-pp)/pp*100 if pp else 0

            regime_icon  = {'bull':'🐂 강세장','bear':'🐻 약세장','neutral':'➡️ 중립장'}.get(regime, '➡️ 중립장')
            regime_color = {'bull':'#26a69a','bear':'#ef5350','neutral':'#b2b5be'}.get(regime, '#b2b5be')

            # ── 종목 헤더 카드 ──────────────────────────────
            live_chg = (live_price - pp) / pp * 100 if pp > 0 else 0
            _52h = float(df['High'].tail(252).max())
            _52l = float(df['Low'].tail(252).min())
            _chg_color = '#10b981' if live_chg >= 0 else '#ef4444'
            _chg_arrow = '▲' if live_chg >= 0 else '▼'
            _regime_badge_color = {'bull':'#10b981','bear':'#ef4444','neutral':'#f59e0b'}.get(regime,'#94a3b8')
            _regime_bg          = {'bull':'#ecfdf5','bear':'#fef2f2','neutral':'#fffbeb'}.get(regime,'#f8fafc')

            # 프리/애프터 마켓 문자열
            _ext_html = ''
            if not is_krw:
                _ep = []
                if pre_price and pre_price > 0:
                    _pv = pre_chg * 100 if pre_chg and abs(pre_chg) < 1 else (pre_chg or 0)
                    _ep.append(f"<span style='color:#94a3b8'>프리마켓</span> <b>{fmt_p(pre_price)}</b> "
                               f"<span style='color:{'#10b981' if _pv>=0 else '#ef4444'}'>{_pv:+.2f}%</span>")
                if post_price and post_price > 0:
                    _pov = post_chg * 100 if post_chg and abs(post_chg) < 1 else (post_chg or 0)
                    _ep.append(f"<span style='color:#94a3b8'>애프터</span> <b>{fmt_p(post_price)}</b> "
                               f"<span style='color:{'#10b981' if _pov>=0 else '#ef4444'}'>{_pov:+.2f}%</span>")
                if _ep:
                    _ext_html = f"<div style='font-size:12px;color:#64748b;margin-top:6px'>{'&nbsp;&nbsp;·&nbsp;&nbsp;'.join(_ep)}</div>"

            _earn_html = (f"<div style='font-size:12px;color:#f59e0b;margin-top:4px'>📅 {earn_str}</div>"
                          if earn_str else '')

            st.markdown(f"""
<div style="background:#fff;border:1px solid #e2e8f0;border-radius:14px;
            padding:20px 24px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.06)">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
    <div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-size:1.4rem;font-weight:800;color:#0f172a;letter-spacing:-.3px">{name}</span>
        <code style="font-size:13px;background:#f1f5f9;color:#334155;padding:3px 8px;border-radius:6px;font-weight:600">{ticker}</code>
        <span style="font-size:11px;padding:3px 10px;border-radius:20px;font-weight:600;
                     background:{_regime_bg};color:{_regime_badge_color};
                     border:1px solid {_regime_badge_color}40">{regime_icon}</span>
      </div>
      <div style="margin-top:8px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <span style="font-size:2rem;font-weight:800;color:#0f172a;font-family:'JetBrains Mono',monospace;letter-spacing:-1px">{fmt_p(live_price)}</span>
        <span style="font-size:1rem;font-weight:700;color:{_chg_color}">{_chg_arrow} {abs(live_chg):.2f}%</span>
        <span style="font-size:12px;color:#94a3b8">{live_label}</span>
      </div>
      {_ext_html}
      {_earn_html}
    </div>
    <div style="display:flex;gap:20px;flex-wrap:wrap">
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px">52주 고가</div>
        <div style="font-size:15px;font-weight:700;color:#10b981;font-family:'JetBrains Mono',monospace;margin-top:2px">{fmt_p(_52h)}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px">52주 저가</div>
        <div style="font-size:15px;font-weight:700;color:#ef4444;font-family:'JetBrains Mono',monospace;margin-top:2px">{fmt_p(_52l)}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px">기준일</div>
        <div style="font-size:13px;font-weight:600;color:#334155;margin-top:2px">{end_dt.strftime('%Y-%m-%d')}</div>
      </div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

            # ── 종합 점수 대시보드 ──────────────────────────
            def _score_badge(sc):
                lbl = score_label(sc)
                c = score_color(sc)
                return (f"<span style='font-size:12px;font-weight:700;padding:2px 10px;"
                        f"border-radius:20px;background:{c}20;color:{c};border:1px solid {c}50'>{lbl}</span>")

            def _bar_html(pct, color):
                pct = max(0, min(100, pct))
                return (f"<div style='background:#f1f5f9;border-radius:6px;height:8px;margin-top:6px'>"
                        f"<div style='background:{color};width:{pct:.0f}%;height:8px;border-radius:6px'></div></div>")

            sc_col1, sc_col2, sc_col3, sc_col4 = st.columns(4)
            with sc_col1:
                _tc = score_color(total)
                st.markdown(f"""
<div style="background:#fff;border:2px solid {_tc}50;border-radius:12px;padding:18px 20px;
            text-align:center;box-shadow:0 2px 10px {_tc}20">
  <div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px">종합 점수</div>
  <div style="font-size:3.2rem;font-weight:800;color:{_tc};font-family:'JetBrains Mono',monospace;
              line-height:1;margin:8px 0 6px">{total:.1f}</div>
  {_score_badge(total)}
</div>""", unsafe_allow_html=True)

            with sc_col2:
                _c2 = score_color(t_score)
                st.markdown(f"""
<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px">
    차트+파동 <span style="color:#cbd5e1">({w_tech}%)</span>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <span style="font-size:2rem;font-weight:800;color:{_c2};font-family:'JetBrains Mono',monospace">{t_score:.0f}</span>
    {_score_badge(t_score)}
  </div>
  {_bar_html(t_score, _c2)}
</div>""", unsafe_allow_html=True)

            with sc_col3:
                _c3 = score_color(f_score)
                st.markdown(f"""
<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px">
    재무+퀀트 <span style="color:#cbd5e1">({w_fund}%)</span>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <span style="font-size:2rem;font-weight:800;color:{_c3};font-family:'JetBrains Mono',monospace">{f_score:.0f}</span>
    {_score_badge(f_score)}
  </div>
  {_bar_html(f_score, _c3)}
</div>""", unsafe_allow_html=True)

            with sc_col4:
                _c4 = score_color(m_score)
                st.markdown(f"""
<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px">
    매크로+금리 <span style="color:#cbd5e1">({w_macro}%)</span>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <span style="font-size:2rem;font-weight:800;color:{_c4};font-family:'JetBrains Mono',monospace">{m_score:.0f}</span>
    {_score_badge(m_score)}
  </div>
  {_bar_html(m_score, _c4)}
</div>""", unsafe_allow_html=True)

            # ── 국면 조정 배너 ──────────────────────────
            _rcm = {'bull':'#10b981','bear':'#ef4444','neutral':'#f59e0b'}
            _rlm = {'bull':'강세장 가중치 적용 시','bear':'약세장 가중치 적용 시','neutral':'중립장 가중치 적용 시'}
            _rc  = _rcm.get(regime,'#94a3b8')
            _rl  = _rlm.get(regime,'국면 가중치 적용 시')
            diff = total_adj - total
            _dc  = '#10b981' if diff >= 0 else '#ef4444'
            st.markdown(f"""
<div style="background:{_rc}0f;border:1px solid {_rc}30;border-radius:10px;
            padding:10px 18px;display:flex;justify-content:space-between;align-items:center;
            margin:12px 0">
  <span style="font-size:13px;font-weight:600;color:{_rc}">{regime_icon} &nbsp; {_rl}</span>
  <span style="font-family:'JetBrains Mono',monospace;font-weight:800;font-size:17px;color:{score_color(total_adj)}">
    {total_adj:.1f}
    <span style="font-size:12px;font-weight:600;color:{_dc};margin-left:4px">({diff:+.1f})</span>
  </span>
</div>""", unsafe_allow_html=True)

            sub1, sub2, sub3, sub4, sub5 = st.tabs(["요약", "차트", "세부분석", "매매전략", "리스크"])

            with sub2:
                # ── 2×2 차트 그리드 ──────────────────────────────
                st.subheader("📈 차트 분석")

                if is_krw:
                    _tv_sym = f"KRX:{ticker.split('.')[0]}"
                else:
                    _tv_sym = ticker

                def _tv_widget_url(height: int = 400) -> str:
                    return (
                        f"https://www.tradingview.com/widgetembed/"
                        f"?symbol={_tv_sym}&interval=D&theme=dark&style=1"
                        f"&timezone=Asia%2FSeoul&locale=kr"
                        f"&studies=STD%3BMASimple%2CSTD%3BRSI%2CSTD%3BMACD"
                        f"&hide_side_toolbar=0&allow_symbol_change=0"
                        f"&width=100%25&height={height}"
                    )

                def _build_sr_fig(height: int = 200, n_candles: int = 90) -> go.Figure:
                    _sr   = find_sr_levels(df['Close'], df['High'], df['Low'])
                    _n    = min(n_candles, len(df))
                    _fig  = go.Figure()
                    _fig.add_trace(go.Scatter(
                        x=df.index[-_n:], y=df['Close'].values[-_n:],
                        line=dict(color='#2962ff', width=1.5), showlegend=False))
                    for _sl in (_sr or []):
                        _sc = '#ef5350' if _sl['above'] else '#26a69a'
                        _fig.add_hline(
                            y=_sl['level'], line_dash='dash', line_color=_sc, line_width=1,
                            annotation_text=f"{'저항' if _sl['above'] else '지지'} {fmt_p(_sl['level'])}",
                            annotation_font=dict(color=_sc, size=9),
                            annotation_position='right')
                    _fig.update_layout(
                        height=height, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        margin=dict(l=0, r=110, t=10, b=0),
                        xaxis=dict(gridcolor=TV_GRID, showgrid=True),
                        yaxis=dict(gridcolor=TV_GRID, showgrid=True))
                    return _fig

                # 2×2 그리드 (미니 프리뷰 + 전체화면 세션 상태 토글)
                if "chart_zoom" not in st.session_state:
                    st.session_state.chart_zoom = None

                grid_r1c1, grid_r1c2 = st.columns(2)
                grid_r2c1, grid_r2c2 = st.columns(2)

                with grid_r1c1:
                    st.caption("📈 TradingView 차트")
                    st.iframe(_tv_widget_url(260), height=275)
                    if st.button("🔍 전체 화면", key="btn_tv"):
                        st.session_state.chart_zoom = "tv"
                        st.rerun()

                with grid_r1c2:
                    st.caption("📊 지지/저항")
                    st.plotly_chart(_build_sr_fig(200, 60), width='stretch')
                    if st.button("🔍 전체 화면", key="btn_sr"):
                        st.session_state.chart_zoom = "sr"
                        st.rerun()

                _ict_mini_ok = False
                with grid_r2c1:
                    st.caption("🎯 ICT / Smart Money")
                    try:
                        from modules.ict_analysis import plot_ict_chart
                        _ict_mini_fig = plot_ict_chart(df, n_candles=60, ticker=ticker)
                        _ict_mini_fig.update_layout(height=200, margin=dict(l=0, r=0, t=20, b=0))
                        st.plotly_chart(_ict_mini_fig, width='stretch')
                        _ict_mini_ok = True
                    except Exception:
                        st.info("ICT 분석 모듈 로드 중...")
                    if st.button("🔍 전체 화면", key="btn_ict"):
                        st.session_state.chart_zoom = "ict"
                        st.rerun()

                with grid_r2c2:
                    st.caption("📐 빗각채널")
                    try:
                        from modules.ict_analysis import plot_channel_chart
                        _ch_mini_fig = plot_channel_chart(df, n_candles=60, swing_lookback=5, ticker=ticker)
                        _ch_mini_fig.update_layout(height=200, margin=dict(l=0, r=0, t=20, b=0))
                        st.plotly_chart(_ch_mini_fig, width='stretch')
                    except Exception:
                        st.info("채널 분석 모듈 로드 중...")
                    if st.button("🔍 전체 화면", key="btn_ch"):
                        st.session_state.chart_zoom = "ch"
                        st.rerun()

                # 전체화면 뷰 (세션 상태 기반)
                if st.session_state.chart_zoom:
                    st.divider()
                    _zoom_titles = {"tv": "📈 TradingView", "sr": "📊 지지/저항", "ict": "🎯 ICT 분석", "ch": "📐 빗각채널"}
                    _zcol1, _zcol2 = st.columns([1, 7])
                    with _zcol1:
                        if st.button("✕ 닫기", key="close_zoom"):
                            st.session_state.chart_zoom = None
                            st.rerun()
                    _zcol2.markdown(f"**{_zoom_titles.get(st.session_state.chart_zoom, '')}**")
                    _zoom = st.session_state.chart_zoom
                    if _zoom == "tv":
                        st.iframe(_tv_widget_url(680), height=700)
                    elif _zoom == "sr":
                        st.plotly_chart(_build_sr_fig(520, 120), width='stretch')
                        _sr2 = find_sr_levels(df['Close'], df['High'], df['Low'])
                        if _sr2:
                            _sc1, _sc2 = st.columns(2)
                            with _sc1:
                                st.markdown("**🟢 지지선**")
                                for s in [x for x in _sr2 if not x['above']][:4]:
                                    st.markdown(f"- {fmt_p(s['level'])} ({s['dist_pct']:+.1f}%)")
                            with _sc2:
                                st.markdown("**🔴 저항선**")
                                for s in [x for x in _sr2 if x['above']][:4]:
                                    st.markdown(f"- {fmt_p(s['level'])} ({s['dist_pct']:+.1f}%)")
                    elif _zoom == "ict":
                        try:
                            from modules.ict_analysis import (
                                plot_ict_chart, find_fvg, find_order_blocks,
                                find_swing_points, find_bos_choch, premium_discount,
                                ict_factor_score,
                            )
                            _n_c = st.slider("표시 캔들 수", 40, 200, 80, 10, key="ict_zoom_c")
                            st.plotly_chart(plot_ict_chart(df, n_candles=_n_c, ticker=ticker),
                                            width='stretch')
                            _ict_cur = float(df["Close"].iloc[-1])
                            _fvgs    = find_fvg(df, lookback=_n_c + 20)
                            _obs     = find_order_blocks(df, lookback=_n_c + 20)
                            _pd_info = premium_discount(df)
                            _swings  = find_swing_points(df.tail(_n_c + 10), lookback=5)
                            _evs     = find_bos_choch(df.tail(_n_c + 10), _swings)
                            _score   = ict_factor_score(df)
                            _dc1, _dc2, _dc3, _dc4 = st.columns(4)
                            _dc1.metric("강세 FVG", f"{sum(1 for f in _fvgs if f['type']=='bull' and not f['filled'] and f['top']<_ict_cur)}개")
                            _dc2.metric("약세 FVG", f"{sum(1 for f in _fvgs if f['type']=='bear' and not f['filled'] and f['bottom']>_ict_cur)}개")
                            _dc3.metric("강세 OB", f"{sum(1 for o in _obs if o['type']=='bull' and not o['mitigated'])}개")
                            _dc4.metric("ICT 점수", f"{_score:.1f}/100")
                            _dp1, _dp2 = st.columns(2)
                            _dp1.metric("구간", _pd_info["zone"].upper(),
                                        f"범위 내 {_pd_info['position_pct']:.0f}%")
                            _dp2.metric("마지막 구조 이탈",
                                        _evs[-1]["type"].replace("_"," ").upper() if _evs else "없음")
                        except Exception as _e:
                            st.warning(f"ICT 분석 오류: {_e}")
                    elif _zoom == "ch":
                        try:
                            from modules.ict_analysis import find_trend_channel, plot_channel_chart
                            _cdc1, _cdc2 = st.columns([2, 1])
                            _n_ch  = _cdc1.slider("표시 캔들 수", 40, 200, 80, 10, key="ch_zoom_c")
                            _sw_lb = _cdc2.slider("스윙 민감도", 3, 10, 5, 1, key="ch_zoom_s")
                            st.plotly_chart(
                                plot_channel_chart(df, n_candles=_n_ch, swing_lookback=_sw_lb, ticker=ticker),
                                width='stretch')
                            _ch = find_trend_channel(df, lookback=_n_ch, swing_lookback=_sw_lb)
                            if _ch:
                                _dir_map = {"bullish": "📈 상승", "bearish": "📉 하락", "sideways": "↔️ 횡보"}
                                _cc1, _cc2, _cc3 = st.columns(3)
                                _cc1.metric("채널 방향", _dir_map.get(_ch["direction"], _ch["direction"]))
                                _cc2.metric("채널 폭 (%)", f"{_ch['width_pct']:.1f}%")
                                _cc3.metric("현재 위치", f"{_ch['zone']} ({_ch['position_pct']:.0f}%)")
                            else:
                                st.info("채널 감지 실패 — 캔들 수를 늘리거나 스윙 민감도를 낮춰보세요.")
                        except Exception as _e:
                            st.warning(f"채널 분석 오류: {_e}")

                # 4-차트 컨센서스 요약
                st.divider()
                st.markdown("##### 🔍 4-차트 컨센서스 분석")
                _cons_cols = st.columns(4)
                _rsi_v = float(calc_rsi(df['Close']).iloc[-1])
                _macd_l, _sig_l, _ = calc_macd(df['Close'])
                _bb_u, _bb_m, _bb_l = calc_bb(df['Close'])
                _adx_s, _, _ = calc_adx(df['High'], df['Low'], df['Close'])
                _adx_v = float(_adx_s.iloc[-1]) if not np.isnan(float(_adx_s.iloc[-1])) else 0
                _cur_p = float(df['Close'].iloc[-1])
                _sr_lvls = find_sr_levels(df['Close'], df['High'], df['Low'])
                _nearest_sup = max([s['level'] for s in (_sr_lvls or []) if not s['above']], default=None)
                _nearest_res = min([s['level'] for s in (_sr_lvls or []) if s['above']], default=None)

                _c1_signal = "강세" if _rsi_v < 65 and float(_macd_l.iloc[-1]) > float(_sig_l.iloc[-1]) else ("약세" if _rsi_v > 75 else "중립")
                _c2_signal = ("지지 근접" if _nearest_sup and (_cur_p - _nearest_sup) / _cur_p < 0.03
                              else "저항 근접" if _nearest_res and (_nearest_res - _cur_p) / _cur_p < 0.03 else "중립")
                _c3_signal = "강세" if _ict_mini_ok else "중립"
                _c4_signal = "강한 추세" if _adx_v > 25 else "횡보"

                _sig_color = {"강세": "#26a69a", "약세": "#ef5350", "중립": "#9598a1",
                              "강한 추세": "#26a69a", "횡보": "#9598a1",
                              "지지 근접": "#26a69a", "저항 근접": "#ef5350"}
                for _col, _label, _val in zip(
                    _cons_cols,
                    ["기술 지표", "지지/저항", "ICT", "추세 강도"],
                    [_c1_signal, _c2_signal, _c3_signal, _c4_signal],
                ):
                    _col.markdown(
                        f"<div style='text-align:center;padding:8px;border-radius:6px;"
                        f"background:{_sig_color.get(_val,'#9598a1')}22'>"
                        f"<div style='font-size:11px;color:#9598a1'>{_label}</div>"
                        f"<div style='font-size:15px;font-weight:600;color:{_sig_color.get(_val, '#9598a1')}'>"
                        f"{_val}</div></div>",
                        unsafe_allow_html=True)

                # 기술적 분석 수치 (확장 패널)
                with st.expander("📊 기술적 지표 수치"):
                    ic1, ic2, ic3, ic4 = st.columns(4)
                    ic1.metric("RSI", f"{_rsi_v:.1f}", "과매수" if _rsi_v > 70 else ("과매도" if _rsi_v < 30 else "보통"))
                    ic2.metric("MACD", f"{float(_macd_l.iloc[-1]):.2f}", f"Signal {float(_sig_l.iloc[-1]):.2f}")
                    _sk_s, _sd_s = calc_stochastic(df['High'], df['Low'], df['Close'])
                    ic3.metric("스토캐스틱 %K", f"{float(_sk_s.iloc[-1]):.1f}", f"%D {float(_sd_s.iloc[-1]):.1f}")
                    ic4.metric("ADX", f"{_adx_v:.1f}", "강한 추세" if _adx_v > 25 else "횡보")
                    ic5, ic6, ic7, ic8 = st.columns(4)
                    ic5.metric("MA20", fmt_p(float(df['Close'].rolling(20).mean().iloc[-1])))
                    ic6.metric("MA60", fmt_p(float(df['Close'].rolling(60).mean().iloc[-1])))
                    ic7.metric("BB 상단", fmt_p(float(_bb_u.iloc[-1])))
                    ic8.metric("BB 하단", fmt_p(float(_bb_l.iloc[-1])))

                with st.expander("📖 ICT 용어 설명"):
                    st.markdown("""
| 용어 | 설명 |
|---|---|
| **FVG (Fair Value Gap)** | 3캔들 사이 가격 공백 — 가격이 메우러 돌아오는 경향 |
| **Bull FVG** | 강세 불균형 — 현재가 아래 있으면 지지 역할 |
| **Bear FVG** | 약세 불균형 — 현재가 위에 있으면 저항 역할 |
| **OB (Order Block)** | 기관이 대량 주문 낸 캔들 — 이후 가격이 돌아오는 구간 |
| **BOS (Break of Structure)** | 기존 추세 방향으로 이전 스윙 돌파 |
| **CHoCH (Change of Character)** | 추세 반전 신호 — 반대 방향으로 스윙 돌파 |
| **Premium / Discount** | EQ(50%) 위 = 비싼 구간, 아래 = 싼 구간 |
""")

            with sub1:
                # ── 매매 시그널 ──────────────────────────────
                trade_signals = detect_trading_signals(df, t_det)
                if trade_signals:
                    st.markdown("#### 매매 시그널")
                    sig_n = min(len(trade_signals), 4)
                    sig_cols = st.columns(sig_n)
                    for sig_i, (sig_ico, sig_nm, sig_dc) in enumerate(trade_signals[:sig_n]):
                        sig_clr = ('#10b981' if sig_ico == '🟢' else
                                   '#ef4444' if sig_ico == '🔴' else
                                   '#f59e0b' if sig_ico == '🟡' else '#3b82f6')
                        sig_cols[sig_i].markdown(
                            f"<div style='background:{sig_clr}0e;border:1px solid {sig_clr}40;"
                            f"border-radius:10px;padding:14px 16px'>"
                            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
                            f"<span style='font-size:16px'>{sig_ico}</span>"
                            f"<span style='color:{sig_clr};font-weight:700;font-size:14px'>{sig_nm}</span></div>"
                            f"<span style='color:#334155;font-size:12px;line-height:1.5'>{sig_dc}</span></div>",
                            unsafe_allow_html=True)

                st.markdown("#### 분석 요약")
                mtf_d = mtf_scores.get('일봉'); mtf_w = mtf_scores.get('주봉'); mtf_m = mtf_scores.get('월봉')
                mtf_list = [x['score'] for x in [mtf_d, mtf_w, mtf_m] if x is not None and 'score' in x]
                mtf_avg  = sum(mtf_list) / len(mtf_list) if mtf_list else 50.0
                mtf_summary = (f"일봉 {mtf_d.get('score',50):.0f} · 주봉 {mtf_w.get('score',50):.0f} · 월봉 {mtf_m.get('score',50):.0f}"
                               if mtf_d and mtf_w and mtf_m else "N/A")
                mom_3 = mom_data.get('3M'); mom_12 = mom_data.get('12M')
                mom_summary = (f"3M {mom_3:+.1f}% · 12M {mom_12:+.1f}%"
                               if mom_3 is not None and mom_12 is not None else "N/A")
                dcf_summary = (f"기본 {fmt_p(dcf_det.get('내재가치_기본',0))} ({dcf_det.get('상승여력_기본',0):+.1f}%)"
                               if dcf_det else "N/A")

                def _summary_card(icon, title, score, note, is_score=True):
                    sc_val = float(score) if is_score else 0
                    clr = score_color(sc_val) if is_score else '#94a3b8'
                    lbl = score_label(sc_val) if is_score else '참고용'
                    sc_disp = f"{sc_val:.1f}" if is_score else str(score)
                    bar = (f"<div style='background:#f1f5f9;border-radius:4px;height:5px;margin-top:8px'>"
                           f"<div style='background:{clr};width:{min(sc_val,100):.0f}%;height:5px;border-radius:4px'></div>"
                           f"</div>") if is_score else ""
                    return (
                        f"<div style='background:#fff;border:1px solid #e2e8f0;border-radius:10px;"
                        f"padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.05)'>"
                        f"<div style='font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;"
                        f"letter-spacing:.6px;margin-bottom:6px'>{icon} {title}</div>"
                        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                        f"<span style='font-size:1.5rem;font-weight:800;color:{clr};"
                        f"font-family:\"JetBrains Mono\",monospace'>{sc_disp}</span>"
                        f"<span style='font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;"
                        f"background:{clr}18;color:{clr}'>{lbl}</span>"
                        f"</div>"
                        f"<div style='font-size:11px;color:#94a3b8;margin-top:4px'>{note}</div>"
                        f"{bar}</div>"
                    )

                _cards = [
                    _summary_card("📈", "차트+파동",       t_score,   f"가중치 {w_tech}%"),
                    _summary_card("💰", "재무+퀀트",       f_score,   f"가중치 {w_fund}% | {f_det.get('업종','N/A')}"),
                    _summary_card("🌍", "매크로+금리",     m_score,   f"가중치 {w_macro}%"),
                    _summary_card("🕐", "멀티 타임프레임", mtf_avg,   mtf_summary),
                    _summary_card("📊", "모멘텀",           mom_data.get('score',50), mom_summary),
                    _summary_card("📰", "뉴스 감성",       news_score, "참고용"),
                    _summary_card("💵", "DCF 내재가치",     dcf_det.get('상승여력_기본',0) if dcf_det else 50,
                                  dcf_summary, is_score=False),
                ]

                _r1, _r2 = st.columns(4), st.columns(3)
                for _i, (_col, _card) in enumerate(zip(list(_r1) + list(_r2), _cards)):
                    with _col:
                        st.markdown(_card, unsafe_allow_html=True)

                st.markdown(
                    "<p style='font-size:11px;color:#94a3b8;margin-top:12px;text-align:center'>"
                    "⚠️ 본 분석은 투자 참고용이며 투자 결정의 책임은 본인에게 있습니다.</p>",
                    unsafe_allow_html=True)

            with sub3:
                st.subheader("카테고리별 점수")

                def _score_bar(label, score, color):
                    pct = max(min(score, 100), 0)
                    st.markdown(
                        f"<div style='margin:10px 0'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:5px'>"
                        f"<span style='font-weight:600;font-size:14px;color:#334155'>{label}</span>"
                        f"<span style='font-weight:700;color:{color};font-size:14px;"
                        f"font-family:\"JetBrains Mono\",monospace'>{score:.0f}</span></div>"
                        f"<div style='background:#f1f5f9;border-radius:6px;height:9px'>"
                        f"<div style='background:{color};width:{pct}%;height:9px;border-radius:6px;"
                        f"transition:width 400ms ease'></div>"
                        f"</div></div>", unsafe_allow_html=True)

                _score_bar(f"📈 차트+파동 ({w_tech}%)", t_score, score_color(t_score))
                _score_bar(f"💰 재무+퀀트 ({w_fund}%)", f_score, score_color(f_score))
                _score_bar(f"🌍 매크로+금리 ({w_macro}%)", m_score, score_color(m_score))

                with st.expander("📈 차트+파동 세부"):
                    SKIP = {'RSI값', 'ADX값', 'Stoch값'}
                    HINT = {
                        'RSI':      f"RSI {t_det.get('RSI값','N/A')}",
                        'ADX추세강도': f"ADX {t_det.get('ADX값','N/A')} ({'추세' if isinstance(t_det.get('ADX값'), (int,float)) and t_det.get('ADX값') > 25 else '횡보'})",
                        '스토캐스틱': f"%K {t_det.get('Stoch값','N/A')}",
                    }
                    for k, v in t_det.items():
                        if k in SKIP: continue
                        if not isinstance(v, (int, float)) or not np.isfinite(v): continue
                        hint = f" *({HINT[k]})*" if k in HINT else ''
                        _n = int(min(100, max(0, v)) / 10)
                        st.markdown(f"**{k}** {'█'*_n}{'░'*(10-_n)} `{v:.0f}점`{hint}")
                    st.divider()
                    st.caption("**📊 모멘텀**")
                    m_cols = st.columns(4)
                    for col_m, (lbl, val) in zip(m_cols, [('1M', mom_data.get('1M')),('3M', mom_data.get('3M')),('6M', mom_data.get('6M')),('12M', mom_data.get('12M'))]):
                        if val is not None: col_m.metric(lbl, f"{val:+.1f}%")
                        else: col_m.metric(lbl, "N/A")
                    st.divider()
                    st.caption("**🕯️ 캔들 패턴 (최근 3일)**")
                    if candle_pats:
                        st.dataframe(pd.DataFrame(candle_pats), width='stretch', hide_index=True)
                    else:
                        st.caption("  특이 패턴 없음")

                with st.expander("🔬 지표 예측력 (IC)"):
                    ics, suggested_w, default_w = ic_data
                    ic_items = sorted(ics.items(), key=lambda x: abs(x[1]), reverse=True)
                    w_rows = []
                    for k, ic_v in ic_items:
                        grade = '강함 💪' if abs(ic_v) >= 0.10 else ('보통 🔶' if abs(ic_v) >= 0.05 else '약함 ❌')
                        w_rows.append({'지표': k, 'IC': f"{ic_v:+.3f}", '예측력': grade,
                                       'IC 권장(%)': f"{suggested_w.get(k,0)*100:.1f}",
                                       '현재(%)': f"{default_w.get(k,0)*100:.1f}"})
                    st.dataframe(pd.DataFrame(w_rows), width='stretch', hide_index=True)

                with st.expander("💰 재무+퀀트 세부"):
                    for k in ['밸류에이션','수익성','성장성','FCF품질','안전성','MDD','F-Score','52주위치']:
                        v = f_det.get(k, 50)
                        if not isinstance(v, (int, float)) or not np.isfinite(v): continue
                        _n = int(min(100, max(0, v)) / 10)
                        st.markdown(f"**{k}** {'█'*_n}{'░'*(10-_n)} `{v:.0f}점`")
                    st.divider()
                    sec_nm = f_det.get('업종','N/A'); sec_per = f_det.get('업종평균PER', 20)
                    st.caption(f"업종: {sec_nm}  (업종평균 PER: {sec_per})")
                    st.caption(f"PER: {fmt(f_det.get('PER'))}  |  PBR: {fmt(f_det.get('PBR'))}  |  PEG: {fmt(f_det.get('PEG'))}  |  EV/EBITDA: {fmt(f_det.get('EV/EBITDA'))}")
                    st.caption(f"ROE: {fmt(f_det.get('ROE'),pct=True)}  |  ROA: {fmt(f_det.get('ROA'),pct=True)}  |  순이익률: {fmt(f_det.get('순이익률'),pct=True)}")
                    fcf_y = f_det.get('FCF수익률')
                    st.caption(f"FCF수익률: {f'{fcf_y:.1f}%' if fcf_y is not None else 'N/A'}  |  이자보상배율: {fmt(f_det.get('이자보상배율'))}")
                    st.caption(f"매출성장: {fmt(f_det.get('매출성장'),pct=True)}  |  EPS성장: {fmt(f_det.get('EPS성장'),pct=True)}")
                    mdd_v = f_det.get('MDD값')
                    st.caption(f"MDD: {f'{mdd_v:.1f}%' if mdd_v is not None else 'N/A'}")
                    fs_v = f_det.get('F-Score값')
                    st.caption(f"Piotroski F-Score: {f'{fs_v}/9' if fs_v is not None else 'N/A'}")
                    for sk, sv in f_det.get('F-Score시그널', {}).items():
                        if '오류' not in sk: st.caption(f"  {sv} {sk}")
                    if dcf_det:
                        st.divider()
                        st.caption("**💵 DCF 내재가치 (그레이엄 공식)**")
                        dc1, dc2 = st.columns(2)
                        dc1.metric("내재가치 (기본)", fmt_p(dcf_det.get('내재가치_기본', 0)),
                                   f"{dcf_det.get('상승여력_기본', 0):+.1f}%")
                        dc2.metric("내재가치 (보수)", fmt_p(dcf_det.get('내재가치_보수', 0)),
                                   f"{dcf_det.get('상승여력_보수', 0):+.1f}%")

                with st.expander("🌍 매크로+금리 세부"):
                    for k in ['금리환경','장단기금리차','VIX','달러지수','신용스프레드','원자재/인플레']:
                        v = m_det.get(k, 50)
                        if not isinstance(v, (int, float)) or not np.isfinite(v): continue
                        _n = int(min(100, max(0, v)) / 10)
                        st.markdown(f"**{k}** {'█'*_n}{'░'*(10-_n)} `{v:.0f}점`")
                    st.divider()
                    if '10Y금리' in m_data:
                        st.caption(f"10Y 금리: {m_data['10Y금리']:.2f}%  |  3M변화: {m_data.get('3M금리변화',0):+.2f}%p")
                    if '장단기스프레드' in m_data:
                        st.caption(f"장단기 스프레드: {m_data['장단기스프레드']:.2f}%p")
                    if 'VIX' in m_data:
                        st.caption(f"VIX: {m_data['VIX']:.1f}  |  DXY: {m_data.get('DXY','N/A')}")
                    if '신용스프레드(HYG-LQD)' in m_data:
                        st.caption(f"신용스프레드(HYG-LQD 3M): {m_data['신용스프레드(HYG-LQD)']:+.2f}%p")
                    if 'GLD변화(3M)' in m_data:
                        st.caption(f"금(GLD) 3M 변화: {m_data['GLD변화(3M)']:+.2f}%")

                # ── 멀티 타임프레임 분석 ──────────────────
                st.subheader("🕐 멀티 타임프레임 분석")
                mtf_labels = ['일봉 (Daily)', '주봉 (Weekly)', '월봉 (Monthly)']
                mtf_keys   = ['일봉', '주봉', '월봉']
                mtf_cols_ui = st.columns(3)
                signals = []
                for col_mtf, lbl, key in zip(mtf_cols_ui, mtf_labels, mtf_keys):
                    info_mtf = mtf_scores.get(key)
                    if info_mtf:
                        sc  = info_mtf['score']
                        det = info_mtf['det']
                        col_mtf.metric(lbl, f"{sc:.1f}점", score_label(sc))
                        signals.append(sc)
                        with col_mtf:
                            st.caption(" · ".join(f"{k} {v:.0f}" for k, v in det.items() if k not in ('RSI값',)))
                    else:
                        col_mtf.metric(lbl, "N/A")

                if signals:
                    consensus = sum(signals) / len(signals)
                    bull_cnt  = sum(1 for s in signals if s >= 65)
                    bear_cnt  = sum(1 for s in signals if s < 50)
                    if bull_cnt == len(signals):
                        mtf_msg = "🟢 **전 타임프레임 강세** — 추세 일치, 신호 신뢰도 높음"
                    elif bear_cnt == len(signals):
                        mtf_msg = "🔴 **전 타임프레임 약세** — 하락 추세 강함"
                    elif bull_cnt > bear_cnt:
                        mtf_msg = "🟡 **중장기 강세, 단기 조정** — 눌림목 매수 고려"
                    elif bear_cnt > bull_cnt:
                        mtf_msg = "🟠 **중장기 약세, 단기 반등** — 데드캣 주의"
                    else:
                        mtf_msg = "⚪ **혼조세** — 방향성 확인 후 진입 권장"
                    st.info(mtf_msg)
                # ── 섹터 상대 강도 ─────────────────────────
                sector_name = info.get('sector', '') if info else ''
                if sector_name and not is_krw:
                    with st.expander(f"📊 섹터 상대 강도 — {sector_name} ({SECTOR_ETF.get(sector_name, 'ETF 없음')})"):
                        with st.spinner("섹터 데이터 로딩 중..."):
                            sr = calc_sector_relative(ticker, sector_name, df)
                        if sr and sr.get('data'):
                            sr_data = sr['data']
                            sr_horizons = list(sr_data.keys())
                            sr_cols = st.columns(len(sr_horizons))
                            for sri, hor in enumerate(sr_horizons):
                                d = sr_data[hor]
                                tk_r   = d['tk_ret']
                                etf_r  = d.get('etf_ret')
                                spy_r  = d.get('spy_ret')
                                rs_etf = d.get('rs_etf')
                                with sr_cols[sri]:
                                    st.markdown(f"**{hor}**")
                                    st.metric(ticker, f"{tk_r:+.1f}%")
                                    if etf_r is not None:
                                        st.metric(sr['etf'], f"{etf_r:+.1f}%",
                                                  delta=f"RS {rs_etf:+.1f}%p",
                                                  delta_color="normal" if rs_etf >= 0 else "inverse")
                                    if spy_r is not None:
                                        st.metric("SPY", f"{spy_r:+.1f}%",
                                                  delta=f"RS {d['rs_spy']:+.1f}%p",
                                                  delta_color="normal" if d['rs_spy'] >= 0 else "inverse")

                            # 3개월 기준 막대차트
                            if '3개월' in sr_data:
                                d3 = sr_data['3개월']
                                bar_labels = [ticker]
                                bar_vals   = [d3['tk_ret']]
                                if d3['etf_ret'] is not None:
                                    bar_labels.append(sr['etf'])
                                    bar_vals.append(d3['etf_ret'])
                                if d3['spy_ret'] is not None:
                                    bar_labels.append('SPY')
                                    bar_vals.append(d3['spy_ret'])
                                bar_clr = [TV_UP if v >= 0 else TV_DOWN for v in bar_vals]
                                fig_sr = go.Figure(go.Bar(
                                    x=bar_labels, y=bar_vals,
                                    marker_color=bar_clr,
                                    text=[f"{v:+.1f}%" for v in bar_vals],
                                    textposition='outside'))
                                fig_sr.add_hline(y=0, line_color=TV_TEXT, line_width=1, opacity=0.4)
                                fig_sr.update_layout(
                                    title=dict(text="3개월 수익률 비교", font=dict(size=12)),
                                    height=260, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                    font=dict(color=TV_TEXT),
                                    yaxis=dict(gridcolor=TV_GRID, zeroline=False),
                                    xaxis=dict(gridcolor=TV_GRID),
                                    margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
                                st.plotly_chart(fig_sr, width='stretch')
                        else:
                            st.info("섹터 데이터를 가져올 수 없습니다.")

            with sub4:
                # ── 매수/매도 추천가 ──────────────────────
                st.subheader("💡 매매 추천가")
                lv    = calc_trade_levels(df, total)
                fmt_p = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"
                cp_lv = lv['cp']
                dt    = lv['dantta']
                sw    = lv['swing']

                def _render_strategy(label, icon, color, bg, tr, cp_ref):
                    rr_c = '#4caf50' if tr['rr1'] >= 2 else ('#ff9800' if tr['rr1'] >= 1 else '#ef5350')
                    st.markdown(
                        f"<div style='background:{bg};border:1px solid {color}66;border-radius:10px;"
                        f"padding:14px 16px;margin-bottom:8px'>"
                        f"<div style='color:{color};font-weight:700;font-size:16px'>{icon} {label}</div>"
                        f"<div style='color:#1a1a1a;font-size:13px;margin-top:4px'>{tr['strategy']}</div>"
                        f"<div style='color:#1a1a1a;font-size:11px;margin-top:4px'>"
                        f"손익비 <b style='color:{rr_c}'>R {tr['rr1']:.1f}:1</b>"
                        f" · 손절 <b style='color:#ef5350'>{tr['risk_pct']:.1f}%</b>"
                        f" · 비중 <b>{tr['alloc']}</b></div>"
                        f"</div>", unsafe_allow_html=True)

                    st.caption("**진입 조건 체크**")
                    cond_text = " &nbsp;|&nbsp; ".join(tr.get('conditions', []))
                    st.markdown(f"<div style='font-size:12px;color:#1a1a1a'>{cond_text}</div>",
                                unsafe_allow_html=True)

                    rows = [
                        {'구분':'🟢 1차 매수','가격':fmt_p(tr['entry1']),
                         '대비':f"{(tr['entry1']-cp_ref)/cp_ref*100:+.1f}%",'근거':tr['basis_e1']},
                        {'구분':'🟡 2차 매수','가격':fmt_p(tr['entry2']),
                         '대비':f"{(tr['entry2']-cp_ref)/cp_ref*100:+.1f}%",'근거':tr['basis_e2']},
                        {'구분':'🟠 3차 매수','가격':fmt_p(tr['entry3']),
                         '대비':f"{(tr['entry3']-cp_ref)/cp_ref*100:+.1f}%",'근거':tr['basis_e3']},
                        {'구분':'🔵 1차 목표','가격':fmt_p(tr['target1']),
                         '대비':f"+{tr['ret1']:.1f}%",'근거':tr['basis_t1']},
                        {'구분':'🔷 2차 목표','가격':fmt_p(tr['target2']),
                         '대비':f"+{tr['ret2']:.1f}%",'근거':tr['basis_t2']},
                    ]
                    if 'target3' in tr:
                        rows.append({'구분':'💎 3차 목표','가격':fmt_p(tr['target3']),
                                     '대비':f"+{(tr['target3']-tr['entry1'])/tr['entry1']*100:.1f}%",
                                     '근거':tr.get('basis_t3','확장 목표')})
                    rows.append({'구분':'🔴 손절가','가격':fmt_p(tr['stop']),
                                 '대비':f"-{tr['risk_pct']:.1f}%",'근거':tr['basis_stop']})
                    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

                    st.caption(f"📏 트레일링: {tr.get('trailing','')}  |  ⏰ 시간손절: {tr.get('time_stop','')}")
                    if lv.get('vwap'):
                        vwap_diff = (lv['vwap'] - cp_ref) / cp_ref * 100
                        st.caption(f"📊 VWAP: {fmt_p(lv['vwap'])} ({vwap_diff:+.1f}%)")

                _render_strategy("단타 전략 (1~5일)", "⚡", "#42a5f5", "#162640", dt, cp_lv)
                _render_strategy("스윙 전략 (2~4주)", "📈", "#ff9800", "#2a2210", sw, cp_lv)

                st.markdown("#### 🛡️ 실전 포지션 플랜")
                exec_plans = build_execution_plan(
                    lv, total, total_adj, regime, risk_data,
                    acct_capital, risk_per_trade, max_position_pct, min_rr)
                plan_cols = st.columns(2)
                for plan_col, plan_key in zip(plan_cols, ['dantta', 'swing']):
                    plan = exec_plans[plan_key]
                    verdict_color = (
                        '#26a69a' if plan['verdict'] == '진입 가능'
                        else ('#ff9800' if plan['verdict'] == '조건부 진입' else '#ef5350')
                    )
                    qty_text = f"{plan['qty']:,.0f}주" if is_krw else f"{plan['qty']:,.2f}주"
                    notes = plan['blockers'] + plan['warnings']
                    notes_text = " · ".join(notes) if notes else "조건 충족"
                    with plan_col:
                        st.markdown(
                            f"<div style='background:#ffffff;border:1px solid {verdict_color}88;"
                            f"border-radius:8px;padding:12px 14px;margin-bottom:8px'>"
                            f"<div style='display:flex;justify-content:space-between;gap:10px'>"
                            f"<b>{plan['label']} 실행 판정</b>"
                            f"<b style='color:{verdict_color}'>{plan['verdict']}</b></div>"
                            f"<div style='color:#1a1a1a;font-size:12px;margin-top:6px'>{notes_text}</div>"
                            f"</div>", unsafe_allow_html=True)
                        pc1, pc2, pc3 = st.columns(3)
                        pc1.metric("최대 수량", qty_text)
                        pc2.metric("투입 금액", fmt_p(plan['position_value']),
                                   f"{plan['alloc_pct_of_account']:.1f}%")
                        pc3.metric("예상 손실", fmt_p(plan['risk_amount']),
                                   f"{plan['risk_pct_of_account']:.2f}%")
                        pc4, pc5 = st.columns(2)
                        pc4.metric("1차 기대수익", fmt_p(plan['reward_amount']))
                        pc5.metric("손익비", f"R {plan['rr']:.1f}:1")

                st.caption(
                    f"계산 기준: 계좌 {fmt_p(acct_capital)} · 거래당 손실 {risk_per_trade:.1f}% "
                    f"· 종목당 최대 {max_position_pct}% · 최소 손익비 R {min_rr:.1f}"
                )

                with st.expander("📐 피보나치 & 피봇 포인트 세부"):
                    fa, fb = st.columns(2)
                    with fa:
                        st.caption("**피보나치 되돌림·확장 (60일 스윙)**")
                        for k, v in lv['fib'].items():
                            marker = " ◀ 현재가 근처" if abs(v - cp_lv) < lv['atr']*0.5 else ""
                            st.caption(f"  {k}: {fmt_p(v)}{marker}")
                    with fb:
                        st.caption("**피봇 포인트 (전일 기준)**")
                        for k, v in lv['piv'].items():
                            marker = " ◀ 현재가 근처" if abs(v - cp_lv) < lv['atr']*0.5 else ""
                            st.caption(f"  {k}: {fmt_p(v)}{marker}")
                    st.caption(f"ATR(14): {fmt_p(lv['atr'])}  |  현재가: {fmt_p(cp_lv)}")

                st.caption("⚠️ 추천가는 기술적 지지/저항 기반 참고값이며 실제 투자 결정의 책임은 본인에게 있습니다.")

            with sub5:
                # ── 뉴스 감성 ──────────────────────────────
                st.subheader("📰 뉴스 감성 분석")
                if _get_anthropic_key():
                    st.caption("🤖 Claude AI 감성 분석")
                else:
                    st.caption("📝 키워드 기반 분석 (Claude API 키 설정 시 AI 분석으로 업그레이드)")
                ns_col1, ns_col2 = st.columns([1, 3])
                with ns_col1:
                    ns_color = score_color(news_score)
                    ns_label = '긍정적' if news_score >= 60 else ('부정적' if news_score < 40 else '중립')
                    st.markdown(
                        f"<div style='text-align:center;padding:15px 5px'>"
                        f"<div style='font-size:42px;font-weight:bold;color:{ns_color}'>{news_score:.0f}</div>"
                        f"<div style='color:{ns_color};font-size:14px'>{ns_label}</div>"
                        f"<div style='color:#1a1a1a;font-size:11px;margin-top:4px'>감성 점수</div>"
                        f"</div>", unsafe_allow_html=True)
                with ns_col2:
                    if news_articles:
                        st.dataframe(pd.DataFrame(news_articles), width='stretch', hide_index=True)
                    else:
                        st.info("뉴스 데이터를 가져올 수 없습니다.")

                # ── 리스크 분석 ────────────────────────────
                st.subheader("⚠️ 리스크 분석")
                if risk_data:
                    rk_cols = st.columns(3)
                    beta_val = risk_data.get('Beta', 1.0)
                    beta_str = f"{beta_val:.2f}"
                    beta_desc = ("📈 고베타 (시장보다 변동 큼)" if beta_val > 1.2
                                 else ("📉 저베타 (시장보다 안정)" if beta_val < 0.8
                                       else "➡️ 시장 수준 변동성"))
                    rk_cols[0].metric("Beta (vs SPY)", beta_str, beta_desc)
                    rk_cols[1].metric("VaR 95% (1일)", risk_data.get('VaR 95% (1일)', 'N/A'),
                                      help="95% 신뢰수준: 하루 최대 손실 추정")
                    rk_cols[2].metric("연간 변동성", risk_data.get('연간 변동성', 'N/A'))
                    rk_cols2 = st.columns(3)
                    rk_cols2[0].metric("VaR 99% (1일)", risk_data.get('VaR 99% (1일)', 'N/A'),
                                       help="99% 신뢰수준: 극단적 하루 손실 추정")
                    rk_cols2[1].metric("CVaR 95%", risk_data.get('CVaR 95%', 'N/A'),
                                       help="VaR 초과 시 평균 손실 (Expected Shortfall)")
                    sharpe = risk_data.get('Sharpe (RF 4.5%)', 0)
                    sharpe_desc = ("우수" if sharpe > 1 else ("보통" if sharpe > 0 else "저조"))
                    rk_cols2[2].metric("Sharpe Ratio", f"{sharpe:.2f}", sharpe_desc)
                    with st.expander("💡 리스크 지표 해석"):
                        st.markdown("""
    | 지표 | 의미 | 해석 기준 |
    |---|---|---|
    | **Beta** | 시장(SPY) 대비 민감도 | >1.2 고위험, 0.8~1.2 중간, <0.8 방어적 |
    | **VaR 95%** | 하루 95% 확률로 이 손실 이내 | 절댓값 클수록 단기 위험 높음 |
    | **CVaR 95%** | VaR 초과 시 예상 평균 손실 | 꼬리 리스크 측정 |
    | **연간 변동성** | 연율화 표준편차 | 20% 이하 안정, 40% 이상 고변동 |
    | **Sharpe** | 위험 단위당 초과 수익 | >1 우수, 0~1 보통, <0 저조 |
    """)
                else:
                    st.info("리스크 데이터를 불러올 수 없습니다.")

                # ── 몬테카를로 시뮬레이션 ─────────────────
                st.subheader("🎲 몬테카를로 시뮬레이션")
                st.caption("과거 변동성을 기반으로 미래 가격 분포를 시뮬레이션합니다.")

                mc_c1, mc_c2, mc_c3 = st.columns(3)
                mc_days = mc_c1.selectbox("예측 기간", [30, 60, 90, 120, 180, 252],
                                          index=1, format_func=lambda x: f"{x}거래일 (~{x//21}개월)")
                mc_sims = mc_c2.selectbox("시뮬레이션 횟수", [200, 500, 1000, 2000], index=1)
                mc_run = mc_c3.button("🎲 시뮬레이션 실행", key="mc_run")

                if mc_run:
                    with st.spinner("시뮬레이션 실행 중..."):
                        mc_paths, mc_stats = calc_monte_carlo(df, days=mc_days, n_sims=mc_sims)

                    mc_m1, mc_m2, mc_m3, mc_m4 = st.columns(4)
                    mc_m1.metric("상승 확률", f"{mc_stats['prob_up']:.1f}%")
                    mc_m2.metric("예상 중앙값", fmt_p(mc_stats['median']),
                                 f"{mc_stats['ret_median']:+.1f}%")
                    mc_m3.metric("낙관 (95%)", fmt_p(mc_stats['p95']),
                                 f"{mc_stats['ret_p95']:+.1f}%")
                    mc_m4.metric("비관 (5%)", fmt_p(mc_stats['p5']),
                                 f"{mc_stats['ret_p5']:+.1f}%")

                    mc_m5, mc_m6, mc_m7 = st.columns(3)
                    mc_m5.metric("10%+ 상승 확률", f"{mc_stats['prob_up10']:.1f}%")
                    mc_m6.metric("10%+ 하락 확률", f"{mc_stats['prob_down10']:.1f}%")
                    mc_m7.metric("일간 변동성", f"{mc_stats['daily_vol']*100:.2f}%")

                    # 시뮬레이션 경로 차트
                    fig_mc = go.Figure()
                    future_dates = pd.bdate_range(df.index[-1], periods=mc_days+1)[1:]
                    n_show = min(mc_sims, 100)
                    for i in range(n_show):
                        fig_mc.add_trace(go.Scatter(
                            x=future_dates, y=mc_paths[i],
                            mode='lines', line=dict(width=0.5, color='rgba(41,98,255,0.08)'),
                            showlegend=False, hoverinfo='skip'))

                    p5_path  = np.percentile(mc_paths, 5, axis=0)
                    p50_path = np.percentile(mc_paths, 50, axis=0)
                    p95_path = np.percentile(mc_paths, 95, axis=0)

                    fig_mc.add_trace(go.Scatter(x=future_dates, y=p95_path, name='95% (낙관)',
                        line=dict(color='#26a69a', width=2.0, dash='dash')))
                    fig_mc.add_trace(go.Scatter(x=future_dates, y=p50_path, name='50% (중앙)',
                        line=dict(color='#FFD700', width=2.5)))
                    fig_mc.add_trace(go.Scatter(x=future_dates, y=p5_path, name='5% (비관)',
                        line=dict(color='#ef5350', width=2.0, dash='dash')))

                    fig_mc.add_hline(y=cp, line_dash='dot', line_color='#888', line_width=1,
                        annotation_text=f"현재가 {fmt_p(cp)}", annotation_position="left",
                        annotation_font=dict(color='#888', size=10))

                    fig_mc.update_layout(
                        height=420, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT),
                        title=dict(text=f"{mc_days}거래일 후 가격 분포 ({mc_sims}회 시뮬레이션)",
                                   font=dict(size=13)),
                        xaxis=dict(gridcolor=TV_GRID),
                        yaxis=dict(gridcolor=TV_GRID, side='right', tickformat=',.0f', title='예상 가격'),
                        legend=dict(orientation='h', y=1.02, bgcolor='rgba(0,0,0,0)'),
                        margin=dict(l=0, r=60, t=40, b=0))
                    st.plotly_chart(fig_mc, width='stretch')

                    # 최종 가격 분포 히스토그램
                    final_returns = (mc_paths[:, -1] - cp) / cp * 100
                    fig_hist = go.Figure()
                    fig_hist.add_trace(go.Histogram(
                        x=final_returns, nbinsx=50,
                        marker_color='#42a5f5',
                        opacity=0.75, name='수익률 분포'))
                    fig_hist.add_vline(x=0, line_color='#FFD700', line_width=2,
                        annotation_text="현재가", annotation_position="top",
                        annotation_font=dict(color='#FFD700', size=10))
                    fig_hist.add_vline(x=float(np.median(final_returns)),
                        line_color='#2962ff', line_width=1.5, line_dash='dash',
                        annotation_text=f"중앙값 {np.median(final_returns):+.1f}%",
                        annotation_position="top",
                        annotation_font=dict(color='#2962ff', size=10))
                    fig_hist.update_layout(
                        height=280, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT),
                        title=dict(text=f"{mc_days}거래일 후 예상 수익률 분포", font=dict(size=13)),
                        xaxis=dict(title='예상 수익률 (%)', gridcolor=TV_GRID),
                        yaxis=dict(title='빈도', gridcolor=TV_GRID),
                        margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
                    st.plotly_chart(fig_hist, width='stretch')

                    st.caption("⚠️ 몬테카를로 시뮬레이션은 과거 변동성이 미래에도 지속된다고 가정합니다. 실제 수익을 보장하지 않습니다.")


    # ── Tab 6: 퀀트 ──────────────────────────────────
    with tab6:
        st.markdown("""
<div style="display:flex;align-items:center;gap:10px;padding:6px 0 16px 0">
  <div style="font-size:1.2rem;font-weight:800;color:#0f172a;letter-spacing:-.3px">퀀트 트레이딩</div>
  <div style="height:1px;flex:1;background:#e2e8f0"></div>
  <div style="font-size:11.5px;color:#94a3b8">팩터 랭킹 → 포트 최적화 → 시스템 시그널 → 백테스트</div>
</div>""", unsafe_allow_html=True)

        qu_c1, qu_c2 = st.columns([1, 2])
        with qu_c1:
            qt_preset = st.selectbox("유니버스 프리셋", ["직접 입력"] + list(UNIVERSE_PRESETS.keys()), key="qt_preset")
        with qu_c2:
            if qt_preset == "직접 입력":
                qt_input = st.text_input("종목 (쉼표 구분, 10~30개 권장)",
                    "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,JPM,V,JNJ,UNH,XOM,PG,HD,MA", key="qt_universe")
                qt_tickers = [t.strip().upper() for t in qt_input.split(',') if t.strip()]
            else:
                qt_tickers = UNIVERSE_PRESETS[qt_preset]
                st.info(f"{len(qt_tickers)}개 종목: {', '.join(qt_tickers[:8])}{'...' if len(qt_tickers) > 8 else ''}")

        qt_sub1, qt_sub2, qt_sub3, qt_sub4, qt_sub5, qt_sub6, qt_sub7, qt_sub8, qt_sub9, qt_sub10 = st.tabs([
            "팩터 랭킹", "포트폴리오 최적화", "시스템 시그널",
            "팩터 백테스트", "종목 백테스팅", "섹터 로테이션", "ML 신호",
            "고급 분석", "운영 안전성", "세금 계산기",
        ])

        with qt_sub1:
            qt_use_timing = st.checkbox("🕐 팩터 타이밍 자동 적용 (VIX/금리 기반 가중치 조절)", value=True, key="qt_timing")
            qt_sector_neutral = st.checkbox("🏭 섹터 중립화 (섹터 편향 제거)", value=True, key="qt_sector")
            _ef_col, _liq_col = st.columns(2)
            qt_extra_factors = _ef_col.checkbox(
                "➕ 추가 팩터 (애널리스트·공매도·EPS 서프라이즈)", value=False, key="qt_extra_f",
                help="3개 추가 팩터 활성화 — 각 종목 API 추가 호출로 약 2배 더 느림")
            qt_min_vol = _liq_col.number_input(
                "💧 최소 일평균 거래량 (유동성 필터)", min_value=0, value=500_000,
                step=100_000, key="qt_min_vol",
                help="0이면 필터 없음. 예: 500,000 = 50만주 미만 제외")

            if qt_use_timing:
                _ft_w, _ft_env = get_factor_timing_weights()
                st.markdown(
                    f"<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;"
                    f"padding:12px 18px;margin:8px 0;display:flex;flex-wrap:wrap;gap:20px;align-items:center'>"
                    f"<div>"
                    f"<div style='font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;"
                    f"letter-spacing:.7px;margin-bottom:4px'>시장 환경</div>"
                    f"<div style='font-size:13px;font-weight:600;color:#334155'>"
                    f"VIX <span style='color:#3b82f6;font-family:\"JetBrains Mono\",monospace'>{_ft_env['vix']}</span>"
                    f"<span style='color:#94a3b8;font-size:11px'> (평균 {_ft_env['vix_avg']})</span>"
                    f" &nbsp;·&nbsp; 10Y금리 <span style='color:#f59e0b;font-family:\"JetBrains Mono\",monospace'>"
                    f"{_ft_env['rate']}%</span>"
                    f"<span style='color:#94a3b8;font-size:11px'> ({_ft_env['rate_chg']:+.2f}%p)</span>"
                    f"</div></div>"
                    f"<div>"
                    f"<div style='font-size:10px;font-weight:700;color:#94a3b8;text-transform:uppercase;"
                    f"letter-spacing:.7px;margin-bottom:4px'>팩터 가중치</div>"
                    f"<div style='font-size:13px;font-weight:600;color:#334155'>"
                    f"모멘텀 <b>{_ft_w['momentum']*100:.0f}%</b> &nbsp;·&nbsp; "
                    f"밸류 <b>{_ft_w['value']*100:.0f}%</b> &nbsp;·&nbsp; "
                    f"퀄리티 <b>{_ft_w['quality']*100:.0f}%</b> &nbsp;·&nbsp; "
                    f"저변동 <b>{_ft_w['low_vol']*100:.0f}%</b>"
                    f"</div></div>"
                    f"<div style='font-size:12px;font-weight:600;color:#10b981;margin-left:auto'>{_ft_env['regime']}</div>"
                    f"</div>", unsafe_allow_html=True)
                qt_fw = _ft_w
            else:
                qt_fw = {'momentum': 0.30, 'value': 0.25, 'quality': 0.30, 'low_vol': 0.15}

            if st.button("📊 팩터 분석 실행", type="primary", key="qt_factor_run"):
                pb = st.progress(0); pt = st.empty()
                if qt_sector_neutral:
                    fdf = calc_factor_scores_sectoral(qt_tickers, factor_weights=qt_fw, prog_bar=pb, prog_text=pt)
                    if not fdf.empty and qt_extra_factors:
                        # extra factors only available in non-sectoral path for now
                        _ef = calc_factor_scores(qt_tickers, extra_factors=True, min_avg_volume=0)
                        for _ecol in ['analyst','short','eps_surprise']:
                            if _ecol in (_ef.columns if not _ef.empty else []):
                                fdf = fdf.merge(_ef[['ticker', _ecol]], on='ticker', how='left')
                else:
                    fdf = calc_factor_scores(qt_tickers, pb, pt,
                                             extra_factors=qt_extra_factors,
                                             min_avg_volume=int(qt_min_vol),
                                             factor_weights=qt_fw)
                # Apply liquidity filter to sectoral path too
                if qt_sector_neutral and qt_min_vol > 0 and not fdf.empty:
                    _end = datetime.now(); _start = _end - timedelta(days=30)
                    _keep = []
                    for _tk in fdf['ticker']:
                        try:
                            _dv = download_stock(_tk, _start, _end)
                            if _dv is not None and 'Volume' in _dv.columns:
                                if float(_dv['Volume'].mean()) >= qt_min_vol:
                                    _keep.append(_tk)
                        except Exception:
                            _keep.append(_tk)
                    fdf = fdf[fdf['ticker'].isin(_keep)].reset_index(drop=True)
                    fdf['rank'] = range(1, len(fdf)+1)
                pb.empty(); pt.empty()
                if fdf.empty:
                    st.error("분석 가능한 종목이 없습니다.")
                else:
                    st.session_state['qt_factors'] = fdf

            if 'qt_factors' in st.session_state:
                fdf = st.session_state['qt_factors']
                _failed = fdf.attrs.get('failed', [])
                st.success(f"📊 {len(fdf)}개 종목 팩터 분석 완료" +
                          (f" (⚠️ {len(_failed)}개 실패: {', '.join(_failed)})" if _failed else ""))

                top5 = fdf.head(5)
                st.markdown("#### 🏆 팩터 Top 5")
                for _, r in top5.iterrows():
                    comp_c = '#26a69a' if r['composite'] >= 65 else ('#ff9800' if r['composite'] >= 45 else '#ef5350')
                    st.markdown(
                        f"<div style='background:#ffffff;border-left:3px solid {comp_c};"
                        f"border-radius:6px;padding:8px 14px;margin:4px 0;"
                        f"display:flex;justify-content:space-between;align-items:center'>"
                        f"<span><b>#{int(r['rank'])} {r['ticker']}</b> "
                        f"<span style='color:#1a1a1a'>{r['name']}</span></span>"
                        f"<span style='color:{comp_c};font-weight:700;font-size:18px'>"
                        f"{r['composite']:.0f}점</span></div>", unsafe_allow_html=True)

                with st.expander("📋 전체 팩터 테이블", expanded=True):
                    cols = ['rank','ticker','name']
                    col_names = ['순위','티커','종목명']
                    if 'sector' in fdf.columns:
                        cols.append('sector'); col_names.append('섹터')
                    cols += ['composite','momentum','value','quality','low_vol','vol','per','pbr','roe']
                    col_names += ['종합','모멘텀','밸류','퀄리티','저변동','변동성%','PER','PBR','ROE%']
                    col_pairs = [(c, n) for c, n in zip(cols, col_names) if c in fdf.columns]
                    disp = fdf[[c for c, _ in col_pairs]].copy()
                    disp.columns = [n for _, n in col_pairs]
                    for c in ['종합','모멘텀','밸류','퀄리티','저변동']:
                        disp[c] = disp[c].round(0).astype(int)
                    st.dataframe(disp, width='stretch', hide_index=True, height=400)

                fig_radar = go.Figure()
                for _, r in top5.iterrows():
                    fig_radar.add_trace(go.Scatterpolar(
                        r=[r['momentum'], r['value'], r['quality'], r['low_vol']],
                        theta=['모멘텀','밸류','퀄리티','저변동성'],
                        fill='toself', name=r['ticker'], opacity=0.6))
                fig_radar.update_layout(
                    height=350, polar=dict(radialaxis=dict(range=[0,100], gridcolor=TV_GRID),
                                           bgcolor=TV_BG, angularaxis=dict(gridcolor=TV_GRID)),
                    plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    font=dict(color=TV_TEXT), margin=dict(l=40, r=40, t=30, b=30),
                    legend=dict(orientation='h', y=-0.1))
                st.plotly_chart(fig_radar, width='stretch')

        with qt_sub2:
            st.caption("선택한 종목들의 최적 비중을 계산합니다.")
            opt_method = st.selectbox("최적화 방법", [
                ("균등 배분", "equal"), ("리스크 패리티", "risk_parity"),
                ("최소 변동성", "min_vol"), ("최대 샤프", "max_sharpe"),
            ], format_func=lambda x: x[0], key="qt_opt_method")

            opt_input = st.text_input("최적화 종목 (팩터 Top N 또는 직접 입력)",
                value=", ".join(qt_tickers[:8]), key="qt_opt_tickers")
            opt_tickers = [t.strip().upper() for t in opt_input.split(',') if t.strip()]

            if st.button("⚖️ 포트폴리오 최적화 실행", type="primary", key="qt_opt_run"):
                with st.spinner("최적화 계산 중..."):
                    w, stats, corr = optimize_portfolio(tuple(opt_tickers), method=opt_method[1])
                if not w:
                    st.error("최적화 실패 — 종목 2개 이상 필요")
                else:
                    st.session_state['qt_opt'] = {'weights': w, 'stats': stats, 'corr': corr}

            if 'qt_opt' in st.session_state:
                qo = st.session_state['qt_opt']
                w, stats, corr = qo['weights'], qo['stats'], qo['corr']

                oc1, oc2, oc3 = st.columns(3)
                oc1.metric("기대 수익률", f"{stats['expected_return']:+.1f}%")
                oc2.metric("변동성", f"{stats['volatility']:.1f}%")
                oc3.metric("샤프 비율", f"{stats['sharpe']:.2f}")

                st.markdown("#### 📊 최적 비중")
                w_sorted = sorted(w.items(), key=lambda x: x[1], reverse=True)
                for tk, wt in w_sorted:
                    bar_w = max(wt * 100, 1)
                    st.markdown(
                        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
                        f"<span style='width:60px;font-weight:600'>{tk}</span>"
                        f"<div style='flex:1;background:#1e2334;border-radius:4px;height:20px'>"
                        f"<div style='background:#2962ff;width:{bar_w}%;height:20px;"
                        f"border-radius:4px;text-align:center;color:white;font-size:11px;"
                        f"line-height:20px'>{wt*100:.1f}%</div></div></div>",
                        unsafe_allow_html=True)

                if not corr.empty:
                    st.markdown("#### 🔗 상관관계 히트맵")
                    fig_corr = go.Figure(go.Heatmap(
                        z=corr.values, x=corr.columns, y=corr.index,
                        colorscale='RdBu_r', zmid=0, zmin=-1, zmax=1,
                        text=corr.round(2).values, texttemplate='%{text}',
                        textfont=dict(size=10)))
                    fig_corr.update_layout(
                        height=max(300, len(corr)*35), plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT), margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig_corr, width='stretch')

                    # ── 집중도 경고 ─────────────────────────
                    high_corr = [(corr.columns[i], corr.columns[j], corr.values[i,j])
                                 for i in range(len(corr)) for j in range(i+1, len(corr))
                                 if abs(corr.values[i,j]) > 0.8]
                    if high_corr:
                        st.warning("⚠️ 고상관 쌍 (>0.8): " +
                                   ", ".join(f"{a}↔{b} ({v:.2f})" for a, b, v in high_corr[:4]))

                # ── 몬테카를로 시뮬레이션 ────────────────────
                st.markdown("#### 🎲 몬테카를로 시뮬레이션")
                _mc_cap = st.number_input("시뮬레이션 투자금 ($)", min_value=1000,
                                          value=10000, step=1000, key="mc_cap")
                _mc_days = st.slider("시뮬레이션 기간 (거래일)", 63, 504, 252, 63,
                                     key="mc_days", format="%d일")
                if st.button("🎲 몬테카를로 실행", key="mc_run_portfolio"):
                    _mc_tickers = list(w.keys())
                    try:
                        _mc_end = datetime.now(); _mc_start = _mc_end - timedelta(days=520)
                        _mc_raw = {}
                        for _t in _mc_tickers:
                            _dl = download_stock(_t, _mc_start, _mc_end)
                            if _dl is not None and not _dl.empty:
                                _mc_raw[_t] = _dl['Close']
                        _mc_prices = pd.DataFrame(_mc_raw).dropna()
                        if not _mc_prices.empty:
                            _mc_rets = _mc_prices.pct_change().dropna()
                            # 실제 다운로드된 컬럼에 맞춰 가중치 재정규화
                            _valid_wts = {_t: w[_t] for _t in _mc_rets.columns if _t in w}
                            _total_w = sum(_valid_wts.values()) or 1.0
                            _norm_wts = {_t: _v / _total_w for _t, _v in _valid_wts.items()}
                            _port_rets = (_mc_rets[list(_norm_wts.keys())] *
                                          pd.Series(_norm_wts)).sum(axis=1)
                            _mc = monte_carlo_portfolio(_port_rets.values, float(_mc_cap), int(_mc_days))
                            st.session_state['qt_mc'] = _mc
                        else:
                            st.warning("데이터 다운로드 실패 - 종목을 확인하세요")
                    except Exception as _e:
                        st.error(f"시뮬레이션 오류: {_e}")

                if 'qt_mc' in st.session_state:
                    _mc = st.session_state['qt_mc']
                    _mcc1, _mcc2, _mcc3, _mcc4 = st.columns(4)
                    _mcc1.metric("중앙값 (50%)", f"${_mc['p50']:,.0f}",
                                 f"{(_mc['p50']/_mc_cap-1)*100:+.1f}%")
                    _mcc2.metric("낙관 (95%)", f"${_mc['p95']:,.0f}",
                                 f"{_mc['best_p95']:+.1f}%")
                    _mcc3.metric("비관 (5%)",  f"${_mc['p5']:,.0f}",
                                 f"{_mc['max_loss_p5']:+.1f}%")
                    _mcc4.metric("수익 확률",  f"{_mc['prob_profit']*100:.0f}%",
                                 f"-20% 위험 {_mc['prob_loss_20']*100:.0f}%")
                    # 팬 차트
                    _t_axis = list(range(_mc['paths'].shape[1]))
                    _fig_mc2 = go.Figure()
                    # 1000개 경로 중 샘플 50개만 표시
                    _rng2 = np.random.default_rng(0)
                    for _p in _rng2.choice(_mc['paths'].shape[0], size=min(50, _mc['paths'].shape[0]), replace=False):
                        _fig_mc2.add_trace(go.Scatter(
                            x=_t_axis, y=_mc['paths'][_p], mode='lines',
                            line=dict(color='rgba(41,98,255,0.08)', width=1),
                            showlegend=False))
                    for _pct_val, _col, _nm in [(_mc['p5'],'#ef5350','5%'),
                                                 (_mc['p50'],'#ffffff','50%'),
                                                 (_mc['p95'],'#26a69a','95%')]:
                        _fig_mc2.add_hline(y=_pct_val, line_dash='dash',
                                           line_color=_col, line_width=1.5,
                                           annotation_text=f"{_nm} ${_pct_val:,.0f}",
                                           annotation_font_color=_col)
                    _fig_mc2.add_hline(y=_mc_cap, line_color='#ffeb3b',
                                       line_dash='dot', line_width=1,
                                       annotation_text="원금", annotation_font_color='#ffeb3b')
                    _fig_mc2.update_layout(
                        height=320, plot_bgcolor='#0d1117', paper_bgcolor='#0d1117',
                        font=dict(color='#e2e8f0'),
                        xaxis=dict(title="거래일", gridcolor='#1e293b'),
                        yaxis=dict(title="포트폴리오 가치 ($)", gridcolor='#1e293b'),
                        margin=dict(l=0, r=120, t=10, b=0))
                    st.plotly_chart(_fig_mc2, width='stretch')

            # ── 전략 결합 (Portfolio Allocator) ──────────────────
            if _PORTFOLIO_ALLOCATOR_AVAILABLE:
                with st.expander("🔗 전략 결합 & 분산화 분석 (포트폴리오 할당기)", expanded=False):
                    st.caption("개별 자산/전략의 equity curve를 역변동성 또는 리스크패리티 비중으로 결합합니다.")
                    _pa_tickers_raw = st.text_input("비교 티커 (쉼표 구분)", "SPY,QQQ,GLD,TLT", key="pa_tickers")
                    _pa_method = st.radio("비중 산출 방식", ["역변동성", "리스크 패리티"], horizontal=True, key="pa_method")
                    if st.button("🔗 전략 결합 분석 실행", key="pa_run"):
                        _pa_tickers = [t.strip().upper() for t in _pa_tickers_raw.split(',') if t.strip()]
                        if len(_pa_tickers) < 2:
                            st.error("최소 2개 티커 필요")
                        else:
                            with st.spinner("데이터 다운로드 중..."):
                                _pa_end = datetime.now()
                                _pa_start = _pa_end - timedelta(days=365 * 3)
                                _pa_closes = {}
                                for _pat in _pa_tickers:
                                    _pa_df = download_stock(_pat, start=_pa_start, end=_pa_end)
                                    if not _pa_df.empty:
                                        _pa_closes[_pat] = _pa_df['Close']
                            if len(_pa_closes) < 2:
                                st.error("데이터 없는 티커가 있습니다.")
                            else:
                                _pa_ret = pd.DataFrame({k: v.pct_change() for k, v in _pa_closes.items()}).dropna()
                                if _pa_method == "역변동성":
                                    _pa_w = _inv_vol_weights(_pa_ret)
                                else:
                                    _pa_w = _rp_weights(_pa_ret)
                                st.markdown("**산출된 비중**")
                                _pa_w_df = _pa_w.rename("비중(%)").apply(lambda x: f"{x*100:.1f}%")
                                st.dataframe(_pa_w_df.to_frame(), width='stretch')
                                _pa_div = _diversification_report(_pa_ret, _pa_w)
                                _pad_c1, _pad_c2 = st.columns(2)
                                _pad_c1.metric("분산화 비율(DR)", f"{_pa_div['diversification_ratio']:.3f}")
                                _pad_c2.metric("포트폴리오 연율 변동성", f"{_pa_div['portfolio_annualized_vol_pct']:.2f}%")
                                st.caption(_pa_div['note'])
                                if _pa_div['high_correlation_pairs']:
                                    st.warning("⚠️ 높은 상관 쌍: " +
                                               ", ".join([f"{p['전략A']}-{p['전략B']}({p['상관']:.2f})" for p in _pa_div['high_correlation_pairs']]))
                                _pa_eq = _combine_strategies(_pa_closes, _pa_w, initial_capital=10_000_000)
                                _pa_fig = go.Figure()
                                for col in _pa_closes:
                                    _pa_norm = (_pa_closes[col] / _pa_closes[col].iloc[0] * 10_000_000)
                                    _pa_fig.add_trace(go.Scatter(x=_pa_norm.index, y=_pa_norm.values, mode='lines',
                                                                  name=col, line=dict(width=1, dash='dot')))
                                _pa_fig.add_trace(go.Scatter(x=_pa_eq.index, y=_pa_eq['combined'].values, mode='lines',
                                                              name='결합 포트폴리오', line=dict(color='#ff9800', width=2.5)))
                                _pa_fig.update_layout(height=300, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                                       font=dict(color=TV_TEXT), title=dict(text="결합 포트폴리오 vs 개별", font=dict(size=13)),
                                                       yaxis=dict(title="자산가치(원)", gridcolor=TV_GRID),
                                                       xaxis=dict(gridcolor=TV_GRID),
                                                       margin=dict(l=20, r=20, t=40, b=20))
                                st.plotly_chart(_pa_fig, width='stretch')

        with qt_sub3:
            st.caption("팩터 랭킹 + 기술적 필터를 결합한 규칙 기반 매매 시그널")

            sc1, sc2, sc3 = st.columns(3)
            qt_top_n = sc1.slider("매수 후보 수 (Top N)", 3, 10, 5, key="qt_top_n")
            qt_capital = sc2.number_input("총 투자금", min_value=100, value=10000, step=1000, key="qt_capital")
            qt_rebal = sc3.selectbox("리밸런싱 주기", ["월간 (20일)", "격주 (10일)", "주간 (5일)"],
                                      key="qt_rebal")

            if st.button("🤖 시스템 시그널 생성", type="primary", key="qt_sig_run"):
                fdf = st.session_state.get('qt_factors')
                qo = st.session_state.get('qt_opt')
                opt_w = qo['weights'] if qo else None
                with st.spinner("시그널 생성 중..."):
                    actions, rebal = generate_system_signals(
                        qt_tickers, factor_df=fdf, weights=opt_w,
                        top_n=qt_top_n, capital=qt_capital)
                st.session_state['qt_signals'] = {'actions': actions, 'rebal': rebal}

            if 'qt_signals' in st.session_state:
                qs = st.session_state['qt_signals']
                actions, rebal = qs['actions'], qs['rebal']

                rc1, rc2, rc3, rc4 = st.columns(4)
                rc1.metric("🟢 매수", f"{rebal['buy_count']}종목")
                rc2.metric("🔴 매도/축소", f"{rebal['sell_count']}종목")
                rc3.metric("⚪ 관망/대기", f"{rebal['hold_count']}종목")
                rc4.metric("📅 다음 리밸런싱", rebal['next_rebal'])

                st.markdown("#### 📋 액션 리스트")
                for a in sorted(actions, key=lambda x: {'HIGH':0,'NORMAL':1,'LOW':2}.get(x['priority'],3)):
                    act_colors = {'🟢 매수':'#26a69a','🔴 매도':'#ef5350',
                                  '🟠 비중축소':'#ff9800','🟡 조건부 매수':'#ff9800',
                                  '🟡 대기':'#ffeb3b','⚪ 관망':'#999999'}
                    ac = act_colors.get(a['action'], '#999999')
                    pri_badge = (f"<span style='background:#ef535022;color:#ef5350;padding:1px 6px;"
                                 f"border-radius:3px;font-size:10px;margin-left:4px'>HIGH</span>"
                                 if a['priority'] == 'HIGH' else '')
                    price_str = a.get('price', '')
                    alloc_str = a.get('alloc', '')
                    qty_str = a.get('qty', '')
                    detail = f"{price_str} · {alloc_str} · {qty_str}" if price_str else ''
                    _detail_html = (
                        f"<div style='color:#1a1a1a;font-weight:600;font-size:13px;margin-top:4px'>{detail}</div>"
                        if detail else ''
                    )
                    st.markdown(
                        f"<div style='background:#ffffff;border-left:4px solid {ac};"
                        f"border-radius:6px;padding:10px 14px;margin:4px 0'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='font-size:15px'><b>{a['ticker']}</b> {a['action']}{pri_badge}</span>"
                        f"<span style='color:#1a1a1a;font-size:12px'>비중 {a['weight']} · 3M {a['mom']}</span></div>"
                        f"<div style='color:#555;font-size:12px;margin-top:4px'>{a['reason']}</div>"
                        f"{_detail_html}"
                        f"</div>", unsafe_allow_html=True)

                st.caption("⚠️ 시스템 시그널은 규칙 기반 참고용이며 최종 판단은 본인에게 있습니다.")

                # ── 켈리 포지션 사이징 ─────────────────────────
                with st.expander("📐 Kelly Criterion 포지션 사이징", expanded=False):
                    st.caption("과거 백테스트 결과 기반 수학적 최적 투입 비율 — half-Kelly 적용 (안전 마진)")
                    _kc1, _kc2, _kc3 = st.columns(3)
                    _k_wr  = _kc1.slider("승률 (%)", 30, 80, 55, key="k_wr") / 100
                    _k_aw  = _kc2.number_input("평균 수익 (%)", min_value=0.1, value=8.0, step=0.5, key="k_aw")
                    _k_al  = _kc3.number_input("평균 손실 (%)", min_value=0.1, value=4.0, step=0.5, key="k_al")
                    _kf    = kelly_fraction(_k_wr, _k_aw, _k_al, half_kelly=True)
                    _kf_full = kelly_fraction(_k_wr, _k_aw, _k_al, half_kelly=False)
                    _kk1, _kk2, _kk3 = st.columns(3)
                    _kk1.metric("Kelly 비율 (Full)", f"{_kf_full*100:.1f}%", "이론 최적")
                    _kk2.metric("Half-Kelly (권장)", f"{_kf*100:.1f}%", "실전 사용")
                    _kk3.metric("$10,000 기준", f"${10000*_kf:,.0f}", "종목당 투입")
                    if _kf > 0.20:
                        st.warning("Kelly 비율이 20% 초과 — cap 적용됨. 변동성 큰 전략입니다.")
                    elif _kf < 0.05:
                        st.info("Kelly 비율이 낮음 — 승률 또는 손익비를 개선하거나 포지션 축소 권장.")
                    st.caption("Kelly 공식: f* = (p×b − q) / b, b=avg_win/avg_loss, Half-Kelly = f*/2")

                # ── 토스증권 자동매매 안내 ────────────────────
                with st.expander("🤖 토스증권 자동매매 시스템", expanded=False):
                    st.markdown(
                        """
**GitHub Actions 기반 자동 실행 파이프라인**

| 구분 | 실행 시간 | 워크플로 |
|------|-----------|----------|
| 🇰🇷 국내 (KRX) | 매일 장마감 후 UTC 07:00 | `paper-trade.yml` |
| 🇺🇸 미국 | 매일 장마감 후 UTC 21:30 | `paper-trade-us.yml` |

- 시그널 생성 → 토스증권 API → 자동 주문 전송
- 매수·매도·스톱로스 결과는 **텔레그램**으로 실시간 알림
- 결과는 `equity_log.json`, `signal_log.json`에 자동 저장 (GitHub Actions commit)
""",
                        unsafe_allow_html=False,
                    )
                    st.caption("⚙️ `.github/workflows/paper-trade.yml` 및 `paper_trade_runner_toss.py` 참고")

                # ── 패턴 필터 ──────────────────────────────────
                buy_tkrs = [a['ticker'] for a in actions if '매수' in a['action']]
                if buy_tkrs:
                    with st.expander(f"🔍 패턴 필터 — 매수 후보 {len(buy_tkrs)}종목 (4중 검증)", expanded=False):
                        st.caption("① 시장 레짐 ② 일봉+주봉 패턴 ③ 실적 발표 근접 ④ 섹터 상대강도")

                        # ① 시장 레짐 배너 (항상 표시)
                        _regime = _get_market_regime()
                        _r_color = {'bull':'#26a69a','bear':'#ef5350','mixed':'#ff9800','unknown':'#9e9e9e'}
                        _r_label = {'bull':'🟢 강세장 (SPY·QQQ > MA200) — 롱 우호적',
                                    'bear':'🔴 약세장 (SPY·QQQ < MA200) — 개별 강세 패턴도 실패율 높음',
                                    'mixed':'🟡 혼조 (SPY·QQQ 엇갈림) — 선택적 진입',
                                    'unknown':'⚪ 레짐 확인 불가'}
                        _rc = _r_color.get(_regime, '#9e9e9e')
                        st.markdown(
                            f"<div style='background:{_rc}18;border:1px solid {_rc}55;"
                            f"border-radius:8px;padding:8px 14px;margin-bottom:10px'>"
                            f"<b>시장 레짐:</b> {_r_label.get(_regime,'')}</div>",
                            unsafe_allow_html=True)

                        if st.button("📊 패턴 분석 실행", key="pat_run"):
                            with st.spinner("차트 패턴 분석 중 (일봉+주봉+섹터 병렬 수집)..."):
                                _pat_results = [detect_chart_pattern(t) for t in buy_tkrs]
                                _pat_results = [r for r in _pat_results if r]
                            st.session_state['qt_patterns'] = _pat_results

                        if 'qt_patterns' in st.session_state:
                            _prs = st.session_state['qt_patterns']
                            if _prs:
                                _si = {'bullish':'🟢','bearish':'🔴','neutral':'⚪'}
                                _wi = {'bullish':'🟢 강세','bearish':'🔴 약세','neutral':'⚪ 중립','unknown':'- -'}
                                _ei = {'low':'✅ 여유','medium':'⚠️ 14일내','high':'🚨 7일내'}
                                _rec = {'bullish':'✅ 이중확인','neutral':'➖ 중립','bearish':'⚠️ 불일치'}
                                _pf_rows = []
                                for r in _prs:
                                    _rs = r.get('sector_rs')
                                    _pf_rows.append({
                                        '종목':       r['ticker'],
                                        '현재가':     f"${r['price']:,.2f}",
                                        '일봉 패턴':  f"{_si.get(r['signal'],'⚪')} {r['signal'].upper()}",
                                        '주봉':       _wi.get(r.get('weekly_signal','unknown'),'--'),
                                        'RSI':        r['rsi'],
                                        '실적':       _ei.get(r.get('earn_risk','low'),'--') + (f" ({r['earn_days']}일)" if r.get('earn_days') is not None else ''),
                                        '섹터 RS':    (f"{'↑' if _rs['outperform'] else '↓'} {_rs['rs']:+.1f}% vs {_rs['etf']}" if _rs else '--'),
                                        '권장':       _rec.get(r['signal'],'➖'),
                                    })
                                st.dataframe(pd.DataFrame(_pf_rows), width='stretch', hide_index=True)

                                st.markdown("---")
                                for r in _prs:
                                    _sr_txt = ' · '.join(
                                        f"{'저항' if s['above'] else '지지'} ${s['level']:.2f}({s['dist_pct']:+.1f}%)"
                                        for s in r.get('sr_levels', [])[:3])
                                    _earn_note = (f" · ⚠️ 실적 {r['earn_days']}일 후" if r.get('earn_days') is not None and r['earn_days'] <= 14 else '')
                                    _rs = r.get('sector_rs')
                                    _rs_note = (f" · {'아웃' if _rs['outperform'] else '언더'}퍼폼 {_rs['rs']:+.1f}% vs {_rs['etf']}" if _rs else '')
                                    _msg = f"**{r['ticker']}** | {_sr_txt}{_earn_note}{_rs_note}"
                                    if r['signal'] == 'bullish':
                                        st.success(_msg)
                                    elif r['signal'] == 'bearish':
                                        st.warning(_msg)
                                    else:
                                        st.info(_msg)

            # ── 시그널 적중률 추적 (signal_log.json) ─────────────
            st.divider()
            st.subheader("📊 과거 시그널 적중률")
            st.caption("GitHub Actions 페이퍼 트레이딩이 발생시킨 매수 시그널의 21일 후 실제 수익률 통계.")
            import json as _json
            _sl_path = os.path.join(os.path.dirname(__file__), "signal_log.json")
            if os.path.exists(_sl_path):
                try:
                    with open(_sl_path) as _slf:
                        _sl_data = _json.load(_slf).get("signals", [])
                    _sl_done = [s for s in _sl_data if s.get("return_pct") is not None]
                    _sl_pend = [s for s in _sl_data if s.get("return_pct") is None]
                    if _sl_done:
                        _rets = [s["return_pct"] for s in _sl_done]
                        _wins = [r for r in _rets if r > 0]
                        _sl_c1, _sl_c2, _sl_c3, _sl_c4 = st.columns(4)
                        _sl_c1.metric("완료 시그널", f"{len(_sl_done)}건")
                        _sl_c2.metric("승률", f"{len(_wins)/len(_sl_done)*100:.0f}%",
                                      help="21일 후 수익 > 0인 비율")
                        _sl_c3.metric("평균 수익률", f"{float(np.mean(_rets)):+.1f}%")
                        _sl_c4.metric("대기 중", f"{len(_sl_pend)}건",
                                      help="아직 21일이 지나지 않은 시그널")
                        # 수익률 분포 바차트
                        _sl_df = pd.DataFrame(_sl_done).sort_values("entry_date")
                        _fig_sl = go.Figure()
                        _fig_sl.add_bar(
                            x=_sl_df["symbol"] + "<br>" + _sl_df["entry_date"],
                            y=_sl_df["return_pct"],
                            marker_color=[("#26a69a" if r > 0 else "#ef5350")
                                          for r in _sl_df["return_pct"]],
                            text=[f"{r:+.1f}%" for r in _sl_df["return_pct"]],
                            textposition="outside",
                        )
                        _fig_sl.add_hline(y=0, line_color="#555", line_width=1)
                        _fig_sl.update_layout(
                            title="시그널별 21일 수익률",
                            height=300, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                            font=dict(color=TV_TEXT),
                            xaxis=dict(gridcolor=TV_GRID, tickangle=-45),
                            yaxis=dict(gridcolor=TV_GRID, ticksuffix="%"),
                            margin=dict(l=0, r=0, t=40, b=60), showlegend=False,
                        )
                        st.plotly_chart(_fig_sl, width='stretch')
                    else:
                        st.info("아직 판정된 시그널이 없습니다. 페이퍼 트레이딩이 21일 이상 실행되면 결과가 쌓입니다.")
                except Exception as _e:
                    st.warning(f"시그널 로그 로드 오류: {_e}")
            else:
                st.info("signal_log.json 없음 — 페이퍼 트레이딩 첫 실행 후 생성됩니다.")

        with qt_sub4:
            st.caption("팩터 전략을 과거 데이터로 검증합니다. 매월 팩터 Top N을 매수하고 리밸런싱한 결과.")

            btc1, btc2 = st.columns(2)
            bt_years = btc1.selectbox("백테스트 기간", [1, 2, 3, 5], index=2, format_func=lambda x: f"{x}년", key="qt_bt_years")
            bt_topn = btc2.slider("Top N 종목", 3, 10, 5, key="qt_bt_topn")
            with st.expander("⚙️ 비용 설정 (수수료 · 슬리피지)", expanded=False):
                _bcc1, _bcc2 = st.columns(2)
                bt_commission = _bcc1.slider("수수료율 (편도 %)", 0.0, 0.30, 0.10, 0.05,
                                             help="증권사 매매 수수료. 미국 주식 ~0.05%", key="qt_bt_commission") / 100
                bt_slippage   = _bcc2.slider("슬리피지율 (편도 %)", 0.0, 0.20, 0.05, 0.01,
                                             help="호가 스프레드·체결 지연. 대형주 ~0.03~0.05%", key="qt_bt_slip") / 100
                _rtrip = (bt_commission + bt_slippage) * 2 * 100
                st.caption(f"왕복 총비용: **{_rtrip:.2f}%** / 리밸런싱 — 비용 미반영 시 수익률 {_rtrip:.1f}%p 과장됨")

            if st.button("📉 팩터 전략 백테스트 실행", type="primary", key="qt_bt_run"):
                with st.spinner(f"{len(qt_tickers)}개 종목 × {bt_years}년 백테스트 중... (1~3분 소요)"):
                    bt_m, bt_eq, bt_log = backtest_factor_strategy(
                        qt_tickers, top_n=bt_topn, years=bt_years,
                        factor_weights=qt_fw if qt_use_timing else None,
                        commission=bt_commission, slippage=bt_slippage)
                if not bt_m:
                    st.error("데이터 부족 — 종목 수를 늘리거나 기간을 줄여주세요.")
                else:
                    st.session_state['qt_bt'] = {'metrics': bt_m, 'eq_df': bt_eq, 'log': bt_log}

            if 'qt_bt' in st.session_state:
                qbt = st.session_state['qt_bt']
                bt_m, bt_eq, bt_log = qbt['metrics'], qbt['eq_df'], qbt['log']

                if bt_m.get('pit_note'):
                    st.warning(f"⚠️ {bt_m['pit_note']}", icon="⚠️")

                mc1, mc2, mc3, mc4 = st.columns(4)
                alpha_c = '#26a69a' if bt_m['alpha'] >= 0 else '#ef5350'
                mc1.metric("전략 수익률", f"{bt_m['total_return']:+.1f}%",
                          f"CAGR {bt_m['cagr']:+.1f}%")
                mc2.metric("SPY 수익률", f"{bt_m['spy_return']:+.1f}%")
                mc3.metric("알파 (초과수익)", f"{bt_m['alpha']:+.1f}%")
                mc4.metric("MDD", f"{bt_m['mdd']:.1f}%")

                mc5, mc6, mc7, mc8 = st.columns(4)
                mc5.metric("샤프 비율", f"{bt_m['sharpe']:.2f}")
                mc6.metric("평균 턴오버", f"{bt_m['avg_turnover']:.0f}%")
                mc7.metric("누적 비용 차감", f"-{bt_m['total_cost']:.2f}%",
                           help="수수료 + 슬리피지 합산 (비용 0% 대비 수익률 차이)")
                mc8.metric("리밸런싱 횟수", f"{bt_m['n_rebalances']}회")

                if not bt_eq.empty:
                    fig_bt = go.Figure()
                    fig_bt.add_trace(go.Scatter(x=bt_eq['date'], y=bt_eq['equity'],
                        name='팩터 전략', line=dict(color='#2962ff', width=2.5),
                        fill='tozeroy', fillcolor='rgba(41,98,255,0.08)'))
                    fig_bt.add_hline(y=10000, line_dash='dot', line_color='#999999',
                        line_width=0.8, annotation_text="시작점",
                        annotation_font=dict(color='#1a1a1a', size=10))
                    fig_bt.update_layout(
                        title=dict(text='팩터 전략 자산 곡선', font=dict(size=14, color='#ffffff')),
                        height=400, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color='#1a1a1a'),
                        yaxis=dict(title='자산', gridcolor=TV_GRID, tickformat=',.0f', side='right'),
                        xaxis=dict(gridcolor=TV_GRID),
                        margin=dict(l=0, r=60, t=50, b=0), showlegend=False)
                    st.plotly_chart(fig_bt, width='stretch')

                if bt_log:
                    with st.expander("📋 리밸런싱 내역"):
                        st.dataframe(pd.DataFrame(bt_log), width='stretch', hide_index=True, height=300)

                st.caption("⚠️ 과거 성과는 미래 수익을 보장하지 않습니다. 거래비용·슬리피지 반영.")

            st.divider()
            st.subheader("🔬 팩터 IC 검증 (예측력 통계 검증)")
            st.caption(
                "팩터 점수가 실제 미래 수익률을 얼마나 잘 예측하는지 수치로 검증합니다.  \n"
                "IC(Information Coefficient) = 팩터 점수 vs 실제 수익률의 스피어만 상관계수.  \n"
                "**IC > 0.05 → 유효한 팩터 / ICIR > 0.5 → 신뢰 가능 / 퀸타일 Q5 > Q1 → 팩터가 수익을 예측**")

            _ic_col1, _ic_col2 = st.columns(2)
            _ic_years   = _ic_col1.selectbox("검증 기간", [1, 2, 3], index=1,
                                              format_func=lambda x: f"{x}년", key="ic_years")
            _ic_fwd     = _ic_col2.selectbox("예측 기간 (Forward)", [10, 21, 42],
                                              format_func=lambda x: f"{x}거래일",
                                              index=1, key="ic_fwd")

            if st.button("🔬 팩터 IC 분석 실행", type="primary", key="ic_run"):
                _ic_prog = st.progress(0, text="데이터 수집 중...")
                try:
                    from modules.factor_validator import run_ic_analysis as _run_ic
                    _ic_df, _ic_quinile, _ic_summary, _ic_ls = _run_ic(
                        qt_tickers,
                        lookback_years=_ic_years,
                        forward_days=_ic_fwd,
                        progress_cb=lambda p: _ic_prog.progress(p, text=f"분석 중 {p*100:.0f}%…"),
                    )
                    _ic_prog.empty()
                    if _ic_df.empty:
                        st.error("데이터 부족 — 종목을 늘리거나 기간을 줄여보세요.")
                    else:
                        st.session_state["ic_result"] = {
                            "df": _ic_df, "quintile": _ic_quinile,
                            "summary": _ic_summary, "ls": _ic_ls,
                        }
                except Exception as _e:
                    _ic_prog.empty()
                    st.error(f"IC 분석 오류: {_e}")

            if "ic_result" in st.session_state:
                _icr = st.session_state["ic_result"]
                _ics = _icr["summary"]
                _ic_df = _icr["df"]

                # 지표 카드
                _ic_color = "#26a69a" if _ics["mean_ic"] > 0 else "#ef5350"
                _ic_c1, _ic_c2, _ic_c3, _ic_c4, _ic_c5 = st.columns(5)
                _ic_c1.metric("평균 IC", f"{_ics['mean_ic']:+.4f}",
                              help="0.05 이상이면 유효한 팩터")
                _ic_c2.metric("ICIR", f"{_ics['icir']:+.3f}",
                              help="IC 평균/표준편차. 0.5 이상이면 안정적")
                _ic_c3.metric("t-통계량", f"{_ics['t_stat']:+.2f}",
                              help="|t| > 2.0이면 통계적으로 유의미")
                _ic_c4.metric("IC 양수 비율", f"{_ics['pct_positive']:.0f}%",
                              help="60% 이상이면 팩터가 일관성 있음")
                _ic_c5.metric("분석 기간 수", f"{_ics['n_periods']}회")

                # 평가 메시지
                _verdict_parts = []
                if abs(_ics["icir"]) >= 0.5:
                    _verdict_parts.append("✅ ICIR 0.5 이상 — 팩터 일관성 **우수**")
                elif abs(_ics["icir"]) >= 0.3:
                    _verdict_parts.append("🟡 ICIR 0.3~0.5 — 팩터 일관성 **보통**")
                else:
                    _verdict_parts.append("🔴 ICIR 0.3 미만 — 팩터 일관성 **낮음**")
                if abs(_ics["t_stat"]) >= 2.0:
                    _verdict_parts.append("✅ t통계량 유의미 (95% 신뢰)")
                else:
                    _verdict_parts.append("🟡 t통계량 미미 — 기간 확대 필요")
                if _ics["pct_positive"] >= 60:
                    _verdict_parts.append("✅ IC 양수 비율 높음 — 방향성 일관")
                st.info("  |  ".join(_verdict_parts))

                # IC 시계열 차트
                _fig_ic = go.Figure()
                _fig_ic.add_bar(
                    x=_ic_df["date"], y=_ic_df["ic"],
                    marker_color=[("#26a69a" if v >= 0 else "#ef5350")
                                  for v in _ic_df["ic"]],
                    name="IC",
                )
                _fig_ic.add_hline(y=0, line_color="#555", line_width=1)
                _fig_ic.add_hline(y=float(_ics["mean_ic"]), line_dash="dash",
                                  line_color="#ff9800", line_width=1.5,
                                  annotation_text=f"평균 IC {_ics['mean_ic']:+.4f}",
                                  annotation_font=dict(color="#ff9800"))
                _fig_ic.update_layout(
                    title="IC 시계열 (기간별 팩터 예측력)",
                    height=280, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    font=dict(color=TV_TEXT),
                    xaxis=dict(gridcolor=TV_GRID),
                    yaxis=dict(gridcolor=TV_GRID, zeroline=False),
                    margin=dict(l=0, r=0, t=40, b=0), showlegend=False,
                )
                st.plotly_chart(_fig_ic, width='stretch')

                # 퀸타일 누적 수익률 차트
                if _icr["quintile"]:
                    _fig_q = go.Figure()
                    _q_colors = {"Q1": "#ef5350", "Q2": "#ff7043", "Q3": "#ffa726",
                                 "Q4": "#66bb6a", "Q5": "#26a69a"}
                    for _qn, _qseries in sorted(_icr["quintile"].items()):
                        _fig_q.add_trace(go.Scatter(
                            x=_qseries.index, y=_qseries.values,
                            name=f"{_qn} ({'상위' if _qn=='Q5' else '하위' if _qn=='Q1' else _qn})",
                            line=dict(color=_q_colors.get(_qn, "#aaa"),
                                      width=2.5 if _qn in ("Q1","Q5") else 1.2,
                                      dash="solid" if _qn in ("Q1","Q5") else "dot"),
                        ))
                    if not _icr["ls"].empty:
                        _fig_q.add_trace(go.Scatter(
                            x=_icr["ls"].index, y=_icr["ls"].values,
                            name="롱쇼트(Q5-Q1)", fill="tozeroy",
                            fillcolor="rgba(41,98,255,0.08)",
                            line=dict(color="#2962ff", width=2, dash="dash"),
                        ))
                    _fig_q.add_hline(y=0, line_color="#555", line_width=1)
                    _fig_q.update_layout(
                        title="퀸타일별 누적 수익률 (Q5=고점수, Q1=저점수)",
                        height=320, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT),
                        xaxis=dict(gridcolor=TV_GRID),
                        yaxis=dict(gridcolor=TV_GRID, ticksuffix="%"),
                        margin=dict(l=0, r=0, t=40, b=0),
                        legend=dict(bgcolor="rgba(0,0,0,0)"),
                    )
                    st.plotly_chart(_fig_q, width='stretch')
                    st.caption("Q5(상위 20%)가 Q1(하위 20%)보다 일관되게 높으면 팩터가 수익을 예측합니다.")



        with qt_sub5:
            st.subheader("📉 전략 백테스팅")
            st.caption("사이드바 가중치와 동일한 **종합점수**(차트+재무+매크로) 기반으로 과거 성과를 검증합니다. 수수료·슬리피지 반영.")

            c1, c2, c3 = st.columns(3)
            bt_ticker  = c1.text_input("티커", "AAPL").strip().upper()
            bt_period  = c2.selectbox("기간", ["1년","2년","3년","5년"], index=1)
            bt_capital = c3.number_input("초기자금 (원)", value=10_000_000, step=1_000_000, min_value=100_000)

            c4, c5 = st.columns(2)
            buy_th  = c4.slider("매수 임계값", 50, 80, 58, 1, help="신호가 이 점수를 넘으면 매수 (낮을수록 매매 많음)")
            sell_th = c5.slider("매도 임계값", 30, 55, 42, 1, help="신호가 이 점수 아래로 내려오면 매도 (높을수록 매매 많음)")

            with st.expander("⚙️ 비용 설정 (수수료 · 슬리피지)"):
                cc1, cc2 = st.columns(2)
                bt_commission = cc1.slider("수수료율 (편도, %)", 0.0, 0.5, 0.05, 0.01,
                                            help="증권사 매매 수수료. 미국 주식 ~0.05%, 한국 주식 ~0.015%") / 100
                bt_slippage   = cc2.slider("슬리피지율 (편도, %)", 0.0, 0.5, 0.03, 0.01,
                                            help="호가 스프레드 + 체결 지연. 유동성 낮을수록 증가") / 100
                total_cost = (bt_commission + bt_slippage) * 2 * 100
                st.caption(f"왕복 총비용: **{total_cost:.2f}%** / 매매 — 거래 빈도가 높을수록 수익률 압박 증가")

            period_days = {"1년":365, "2년":730, "3년":1095, "5년":1825}

            if st.button("📉 백테스팅 시작", type="primary"):
                st.session_state['bt_run_count'] = st.session_state.get('bt_run_count', 0) + 1
                with st.spinner("백테스팅 실행 중..."):
                    end_dt2   = datetime.now()
                    start_dt2 = end_dt2 - timedelta(days=period_days[bt_period]+60)
                    _bt_df = download_stock(bt_ticker, start=start_dt2, end=end_dt2)
                    _bt_df = _bt_df.dropna(subset=['Close']) if _bt_df is not None else pd.DataFrame()

                if _bt_df is None or _bt_df.empty or len(_bt_df) < 60:
                    st.error("데이터가 부족합니다.")
                else:
                    _bt_f, _bt_m = None, None
                    if total_w == 100 and (w_fund > 0 or w_macro > 0):
                        with st.spinner("재무·매크로 점수 산출 중..."):
                            _bt_f, _ = fundamental_score(bt_ticker, _bt_df)
                            _bt_m, _, _ = macro_score()

                    _metrics, _eq_df, _trades_df = run_backtest(
                        _bt_df, buy_th, sell_th, bt_capital, bt_commission, bt_slippage,
                        f_score=_bt_f, m_score=_bt_m,
                        w_tech=w_tech, w_fund=w_fund, w_macro=w_macro)

                    _corr_results = analyze_score_correlation(_bt_df)

                    _bt_sigs = bt_signals_full(_bt_df)

                    st.session_state['tab3'] = {
                        'bt_df': _bt_df, 'metrics': _metrics, 'eq_df': _eq_df,
                        'trades_df': _trades_df, 'corr_results': _corr_results,
                        'bt_sigs': _bt_sigs,
                        'bt_f_score': _bt_f, 'bt_m_score': _bt_m,
                        'bt_ticker': bt_ticker, 'bt_capital': bt_capital,
                        'buy_th': buy_th, 'sell_th': sell_th,
                        'bt_commission': bt_commission, 'bt_slippage': bt_slippage,
                        'w_tech': w_tech, 'w_fund': w_fund, 'w_macro': w_macro,
                    }

            if 'tab3' in st.session_state:
                _bt = st.session_state['tab3']
                bt_df = _bt['bt_df']; metrics = _bt['metrics']
                eq_df = _bt['eq_df']; trades_df = _bt['trades_df']
                corr_results = _bt['corr_results']
                bt_f_score = _bt['bt_f_score']; bt_m_score = _bt['bt_m_score']

                if bt_f_score is not None and bt_m_score is not None:
                    _fm_off = (bt_f_score - 50) * (_bt['w_fund'] / 100) + (bt_m_score - 50) * (_bt['w_macro'] / 100)
                    st.info(f"📊 재무 {bt_f_score:.0f}점 · 매크로 {bt_m_score:.0f}점 → 임계값 보정 {_fm_off:+.1f}점 (매수 {_bt['buy_th']-_fm_off:.0f} / 매도 {_bt['sell_th']-_fm_off:.0f})")

                # ── 지표 12개 (3행×4열) ──────────────────
                m_keys = list(metrics.keys()); m_vals = list(metrics.values())
                for row_start in range(0, len(m_keys), 4):
                    row_keys = m_keys[row_start:row_start+4]
                    row_vals = m_vals[row_start:row_start+4]
                    row_cols = st.columns(len(row_keys))
                    for ci, (k, v) in enumerate(zip(row_keys, row_vals)):
                        row_cols[ci].metric(k, v)
                st.divider()

                # ── 신호 분포 ─────────────────────────────
                with st.expander("📊 신호 점수 분포 (임계값 튜닝 참고)"):
                    _sig_vals = _bt['bt_sigs'].dropna().iloc[20:]
                    if len(_sig_vals) == 0:
                        st.info("백테스트 신호 데이터가 부족합니다.")
                    else:
                        _adj_buy = _bt['buy_th']
                        _adj_sell = _bt['sell_th']
                        if bt_f_score is not None and bt_m_score is not None:
                            _fm_off = (bt_f_score - 50) * (_bt['w_fund'] / 100) + (bt_m_score - 50) * (_bt['w_macro'] / 100)
                            _adj_buy = _bt['buy_th'] - _fm_off
                            _adj_sell = _bt['sell_th'] - _fm_off
                        _pct_above = float((_sig_vals > _adj_buy).sum() / len(_sig_vals) * 100)
                        _pct_below = float((_sig_vals < _adj_sell).sum() / len(_sig_vals) * 100)
                        sc1, sc2, sc3 = st.columns(3)
                        sc1.metric("매수 구간 비율", f"{_pct_above:.1f}%", f"점수 > {_adj_buy:.0f}")
                        sc2.metric("매도 구간 비율", f"{_pct_below:.1f}%", f"점수 < {_adj_sell:.0f}")
                        sc3.metric("평균 신호 점수", f"{float(_sig_vals.mean()):.1f}")
                        fig_dist = go.Figure()
                        fig_dist.add_trace(go.Histogram(x=_sig_vals, nbinsx=40,
                            marker_color='rgba(41,98,255,0.5)', name='신호 분포'))
                        fig_dist.add_vline(x=_adj_buy, line_color=TV_UP, line_width=2,
                            annotation_text=f"매수 {_adj_buy:.0f}", annotation_position="top",
                            annotation_font=dict(color=TV_UP, size=11))
                        fig_dist.add_vline(x=_adj_sell, line_color=TV_DOWN, line_width=2,
                            annotation_text=f"매도 {_adj_sell:.0f}", annotation_position="top",
                            annotation_font=dict(color=TV_DOWN, size=11))
                        fig_dist.update_layout(height=250, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                            font=dict(color=TV_TEXT), showlegend=False,
                            title=dict(text='신호 점수 분포', font=dict(size=13)),
                            xaxis=dict(title='신호 점수', gridcolor=TV_GRID),
                            yaxis=dict(title='빈도', gridcolor=TV_GRID),
                            margin=dict(l=10, r=10, t=40, b=10))
                        st.plotly_chart(fig_dist, width='stretch')
                    st.caption("매수 구간이 5~15%, 매도 구간이 5~15% 정도면 적절합니다. 슬라이더로 조절하세요.")

                # ── 자산 곡선 ─────────────────────────────
                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(x=eq_df['날짜'], y=eq_df['전략'], name='전략',
                    line=dict(color='#2962ff', width=2),
                    fill='tozeroy', fillcolor='rgba(41,98,255,0.08)'))
                fig_eq.add_trace(go.Scatter(x=eq_df['날짜'], y=eq_df['매수보유'], name='매수보유',
                    line=dict(color='#888', width=1.5, dash='dash')))

                if not trades_df.empty:
                    buys_df  = trades_df[trades_df['구분'].str.contains('매수')]
                    sells_df = trades_df[trades_df['구분'].str.contains('매도')]
                    for bdate in buys_df['날짜']:
                        row = eq_df[eq_df['날짜'] == bdate]
                        if not row.empty:
                            fig_eq.add_trace(go.Scatter(x=[bdate], y=[float(row['전략'].iloc[0])],
                                mode='markers', marker=dict(symbol='triangle-up', size=14, color=TV_UP),
                                name='매수', showlegend=False))
                    for sdate in sells_df['날짜']:
                        row = eq_df[eq_df['날짜'] == sdate]
                        if not row.empty:
                            fig_eq.add_trace(go.Scatter(x=[sdate], y=[float(row['전략'].iloc[0])],
                                mode='markers', marker=dict(symbol='triangle-down', size=14, color=TV_DOWN),
                                name='매도', showlegend=False))

                fig_eq.update_layout(height=420, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    font=dict(color=TV_TEXT), hovermode='x unified',
                    title=dict(text='전략 vs 매수보유 자산 곡선', font=dict(size=14)),
                    yaxis=dict(gridcolor=TV_GRID, tickformat=',.0f', side='right', title='자산 (원)'),
                    xaxis=dict(gridcolor=TV_GRID),
                    legend=dict(orientation='h', bgcolor='rgba(0,0,0,0)'),
                    margin=dict(l=0, r=60, t=50, b=0))
                st.plotly_chart(fig_eq, width='stretch')

                if not trades_df.empty:
                    st.subheader(f"매매 내역 ({len(trades_df)}건)")
                    st.dataframe(trades_df, width='stretch', hide_index=True)

                # ── 📊 점수-수익률 상관관계 검증 ─────────────
                st.divider()
                st.subheader("📊 점수-수익률 상관관계 검증")
                st.caption(
                    "신호 점수가 실제 미래 수익률과 얼마나 연관되는지 검증합니다. "
                    "**IC(정보계수)** > 0 이면 점수가 높을수록 수익률이 높은 경향이 있음을 의미합니다.")

                corr_results = _bt['corr_results']

                # IC 카드 3개
                ic_cols = st.columns(3)
                for ci, cr in enumerate(corr_results):
                    ic_val = cr['IC']
                    ic_color = "off" if abs(ic_val) < 0.05 else ("inverse" if ic_val < 0 else "normal")
                    ic_cols[ci].metric(
                        f"IC ({cr['horizon']}일 후 수익률)",
                        f"{ic_val:+.3f}",
                        delta=("유효 신호 ✅" if abs(ic_val) >= 0.05 else "신호 미약 ⚠️"),
                        delta_color=ic_color)

                st.caption("IC 해석: |IC| ≥ 0.05 → 약한 예측력 / ≥ 0.10 → 의미있는 예측력 / ≥ 0.15 → 강한 예측력")
                st.divider()

                # 20일 기준 점수 구간별 평균 수익률 막대차트
                cr20 = next((r for r in corr_results if r['horizon'] == 20), corr_results[-1] if corr_results else None)
                bs   = cr20['bucket_stats'].dropna(subset=['평균수익률(%)']) if cr20 else pd.DataFrame()
                if not bs.empty:
                    bar_colors = [TV_UP if v >= 0 else TV_DOWN for v in bs['평균수익률(%)'].tolist()]
                    fig_bar = go.Figure()
                    fig_bar.add_trace(go.Bar(
                        x=bs['점수구간'], y=bs['평균수익률(%)'],
                        marker_color=bar_colors,
                        error_y=dict(type='data', array=bs['표준편차'].tolist(), visible=True,
                                     color=TV_TEXT, thickness=1.2, width=4),
                        text=[f"{v:+.2f}%" for v in bs['평균수익률(%)'].tolist()],
                        textposition='outside', textfont=dict(size=11)))
                    fig_bar.add_hline(y=0, line_color=TV_TEXT, line_width=1, opacity=0.4)
                    fig_bar.update_layout(
                        title=dict(text=f"점수 구간별 평균 20일 후 수익률 (n={len(cr20['scatter'])})", font=dict(size=13)),
                        height=340, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT),
                        xaxis=dict(title='신호 점수 구간', gridcolor=TV_GRID),
                        yaxis=dict(title='평균 수익률 (%)', gridcolor=TV_GRID, zeroline=False),
                        margin=dict(l=20, r=20, t=50, b=20), showlegend=False)
                    st.plotly_chart(fig_bar, width='stretch')

                    # 구간별 상세 통계 테이블
                    with st.expander("📋 구간별 상세 통계"):
                        display_bs = bs.copy()
                        display_bs['평균수익률(%)'] = display_bs['평균수익률(%)'].map(lambda x: f"{x:+.2f}%")
                        display_bs['표준편차']       = display_bs['표준편차'].map(lambda x: f"{x:.2f}%")
                        st.dataframe(display_bs, width='stretch', hide_index=True)

                        # 5일, 10일 IC 도 표로
                        ic_summary = pd.DataFrame([
                            {'기간': f"{cr['horizon']}일 후", 'IC': f"{cr['IC']:+.3f}",
                             '예측력': ('강함 💪' if abs(cr['IC']) >= 0.15 else
                                       ('보통 🔶' if abs(cr['IC']) >= 0.10 else
                                        ('약함 🔸' if abs(cr['IC']) >= 0.05 else '없음 ❌')))}
                            for cr in corr_results
                        ])
                        st.dataframe(ic_summary, width='stretch', hide_index=True)

                # ── 📐 워크-포워드 검증 ──────────────────────
                st.divider()
                st.subheader("📐 워크-포워드 검증 (과적합 진단)")
                st.caption(
                    "전체 기간을 **학습 70%** / **검증 30%** 로 분리해 동일 전략을 각각 실행합니다. "
                    "학습 성과와 검증 성과 차이가 클수록 **과적합** 가능성이 높습니다.")

                if st.button("📐 워크-포워드 검증 실행", key="wf_btn"):
                    wf_results, wf_overfit, wf_split_date = run_walkforward(
                        bt_df, _bt['buy_th'], _bt['sell_th'], _bt['bt_capital'],
                        _bt['bt_commission'], _bt['bt_slippage'],
                        f_score=bt_f_score, m_score=bt_m_score,
                        w_tech=_bt['w_tech'], w_fund=_bt['w_fund'], w_macro=_bt['w_macro'])

                    wf_col1, wf_col2 = st.columns(2)
                    wf_key_order = ['기간', '전략 수익률', 'CAGR', '최대낙폭(MDD)',
                                    'Sharpe Ratio', 'Calmar Ratio', '승률', '총 매매']
                    for wf_ci, (wf_label, wf_m) in enumerate(wf_results.items()):
                        col = wf_col1 if wf_ci == 0 else wf_col2
                        bg  = 'rgba(41,98,255,0.08)' if wf_ci == 0 else 'rgba(255,82,82,0.08)'
                        bdr = '#2962ff' if wf_ci == 0 else '#ef5350'
                        col.markdown(
                            f"<div style='background:{bg};border-left:3px solid {bdr};"
                            f"border-radius:6px;padding:10px 14px;margin-bottom:8px'>"
                            f"<b>{wf_label}</b></div>", unsafe_allow_html=True)
                        for wf_k in wf_key_order:
                            if wf_k in wf_m:
                                col.metric(wf_k, wf_m[wf_k])

                    # 과적합 진단 메시지
                    st.divider()
                    if abs(wf_overfit) < 3:
                        st.success(f"✅ **과적합 없음** — 학습/검증 CAGR 차이 {wf_overfit:+.1f}%p (기준 ±3%p 이내)")
                    elif abs(wf_overfit) < 8:
                        st.warning(f"🔶 **경미한 과적합** — 학습/검증 CAGR 차이 {wf_overfit:+.1f}%p — 임계값 재검토 권장")
                    else:
                        st.error(f"🔴 **강한 과적합** — 학습/검증 CAGR 차이 {wf_overfit:+.1f}%p — 임계값이 과거에 최적화되어 미래에는 적용 불가")
                    st.caption(f"분할 기준일: {wf_split_date}")

                # ── 🛡️ 리스크 관리 사이징 비교 ──
                if _RISK_MGMT_ENABLED:
                    with st.expander("🛡️ 리스크 관리 사이징 적용 비교 (변동성 타겟팅 + 서킷브레이커)", expanded=False):
                        st.caption(
                            "기존 백테스트는 신호가 뜨면 전량 매수/매도합니다. "
                            "여기서는 변동성 타겟팅·신호 강도 비례 사이징·드로다운 서킷브레이커를 적용해 비교합니다."
                        )
                        if st.button("🛡️ 사이징 적용해서 비교 실행", key="btn_risk_sized"):
                            with st.spinner("사이징 백테스트 실행 중..."):
                                try:
                                    _sized_m, _sized_eq, _sized_tr = run_backtest_sized(
                                        bt_df, bt_signals_full,
                                        buy_th=_bt['buy_th'], sell_th=_bt['sell_th'],
                                        initial_capital=_bt['bt_capital'],
                                        commission=_bt['bt_commission'], slippage=_bt['bt_slippage'],
                                        use_vol_target=True, target_vol=0.20,
                                        use_signal_sizing=True,
                                        use_circuit_breaker=True, dd_threshold=-15)
                                    rc1, rc2 = st.columns(2)
                                    with rc1:
                                        st.markdown("**기존 (전량 매매)**")
                                        st.write({k: metrics[k] for k in ['전략 수익률', '최대낙폭(MDD)', 'Sharpe Ratio']})
                                    with rc2:
                                        st.markdown("**사이징 적용**")
                                        st.write({
                                            '전략 수익률': f"{_sized_m['total_return']:+.1f}%",
                                            '최대낙폭(MDD)': f"{_sized_m['mdd']:.1f}%",
                                            'Sharpe': f"{_sized_m['sharpe']:.2f}",
                                            '거래 수': _sized_m['n_trades'],
                                        })
                                except Exception as _e:
                                    st.error(f"사이징 백테스트 오류: {_e}")

                # ── 📐 통계적 유의성 검증 (DSR · 블록부트스트랩 · 순열검정) ──
                st.divider()
                with st.expander("🔬 통계적 유의성 검증 (과최적화 진단)", expanded=False):
                    if not _STAT_AVAILABLE:
                        st.error("modules/stat_validation.py 로드 실패")
                    else:
                        st.caption("DSR(Deflated Sharpe Ratio) · 블록 부트스트랩 CI · 순열검정 — Bailey & Lopez de Prado (2014)")
                        _sv_c1, _sv_c2 = st.columns(2)
                        _sv_trials = _sv_c1.number_input(
                            "파라미터 튜닝 시도 횟수", min_value=1,
                            value=st.session_state.get('bt_run_count', 1),
                            step=1, key="sv_trials",
                            help="매수/매도 임계값 조합을 몇 번 시도해봤는지. 많을수록 우연히 좋아 보일 확률↑")
                        _sv_boot_n = _sv_c2.number_input(
                            "부트스트랩 반복수", min_value=100, max_value=5000, value=1000, step=100, key="sv_boot")

                        if st.button("🔬 검증 실행", key="sv_run"):
                            _sv_bt = st.session_state.get('tab3')
                            if _sv_bt is None:
                                st.warning("먼저 백테스팅을 실행해주세요.")
                            else:
                                _sv_eq  = _sv_bt['eq_df']
                                _sv_trd = _sv_bt['trades_df']
                                _sv_met = _sv_bt['metrics']

                                # 일별 수익률 재구성 (워밍업 20일 제외 — 초기 0.0이 std를 왜곡)
                                _sv_ret = _sv_eq['전략'].pct_change().dropna().values[19:]

                                # DSR은 per-period(일별) Sharpe 단위 요구 — 연율화 미적용
                                _obs_sr = (float(np.mean(_sv_ret) / np.std(_sv_ret))
                                           if len(_sv_ret) > 1 and np.std(_sv_ret) > 0 else 0.0)

                                sv1, sv2, sv3 = st.columns(3)

                                # ① DSR
                                try:
                                    _n_obs = len(_sv_ret)
                                    _dsr_res = _dsr(_obs_sr, int(_sv_trials), _n_obs)
                                    _dsr_pct = _dsr_res['dsr_probability'] * 100
                                    _dsr_sig = _dsr_res['is_significant_95pct']
                                    sv1.metric(
                                        "DSR (Deflated Sharpe)",
                                        f"{_dsr_pct:.1f}%",
                                        delta=("유의미 ✅" if _dsr_sig else "우연 수준 ⚠️"),
                                        delta_color=("normal" if _dsr_sig else "inverse"))
                                    st.caption(f"💡 {_dsr_res['interpretation']}")
                                except Exception as _e:
                                    sv1.error(f"DSR 계산 오류: {_e}")

                                # ② 블록 부트스트랩 CI
                                try:
                                    _bb_res = _bb_ci(_sv_ret, n_boot=int(_sv_boot_n))
                                    sv2.metric(
                                        "Sharpe 90% CI",
                                        f"{_bb_res['point_estimate_sharpe']:.2f}",
                                        delta=f"[{_bb_res['ci_90pct_low']:.2f}, {_bb_res['ci_90pct_high']:.2f}]",
                                        delta_color="normal")
                                    _prob_neg = _bb_res['prob_sharpe_below_0'] * 100
                                    if _prob_neg > 20:
                                        sv2.warning(f"Sharpe < 0 확률: {_prob_neg:.0f}%")
                                    else:
                                        sv2.success(f"Sharpe < 0 확률: {_prob_neg:.0f}%")
                                except Exception as _e:
                                    sv2.error(f"부트스트랩 오류: {_e}")

                                # ③ 순열검정
                                try:
                                    _sells_only = (_sv_trd[_sv_trd['구분'].str.contains('매도', na=False)]
                                                   if not _sv_trd.empty else _sv_trd)
                                    _pnls = []
                                    for _v in (_sells_only['수익률'] if '수익률' in _sells_only.columns else []):
                                        try:
                                            _pnls.append(float(str(_v).replace('%', '').replace('+', '')))
                                        except (ValueError, AttributeError):
                                            pass
                                    if len(_pnls) >= 5:
                                        _perm_res = _perm_test(_pnls, n_perm=2000)
                                        _pv = _perm_res['p_value']
                                        sv3.metric(
                                            "순열검정 p-value",
                                            f"{_pv:.3f}",
                                            delta=("유의미 (p<0.05) ✅" if _pv < 0.05 else "우연 수준 ⚠️"),
                                            delta_color=("off" if _pv < 0.05 else "inverse"))
                                        st.caption(f"💡 {_perm_res['interpretation']}")
                                    else:
                                        sv3.info(f"매도 거래 {len(_pnls)}건 — 순열검정은 5건 이상 필요")
                                except Exception as _e:
                                    sv3.error(f"순열검정 오류: {_e}")

            # ── 📦 멀티 종목 포트폴리오 백테스트 ──────────
            st.divider()
            st.subheader("📦 멀티 종목 포트폴리오 백테스트")
            st.caption("여러 종목에 자본을 배분해 포트폴리오 전체의 백테스트 성과를 확인합니다.")

            with st.expander("⚙️ 포트폴리오 설정", expanded=True):
                pbt_str = st.text_input("종목 목록 (쉼표 구분, 최대 5개)",
                                         "AAPL,MSFT,NVDA,GOOGL,META",
                                         help="미국: AAPL / 한국: 005930.KS")
                pbt_tickers = [t.strip().upper() for t in pbt_str.split(',') if t.strip()][:5]

                pbt_wt_mode = st.radio("비중 방식", ["균등 배분", "직접 입력"], horizontal=True)
                if pbt_wt_mode == "균등 배분":
                    pbt_weights = [1.0 / len(pbt_tickers)] * len(pbt_tickers) if pbt_tickers else []
                else:
                    pbt_wt_cols = st.columns(len(pbt_tickers))
                    pbt_weights = []
                    for pi, tk in enumerate(pbt_tickers):
                        w = pbt_wt_cols[pi].number_input(tk, 0.0, 1.0,
                            round(1.0 / len(pbt_tickers), 2), 0.05, key=f"pbt_w_{pi}")
                        pbt_weights.append(w)
                    wt_sum = sum(pbt_weights)
                    if abs(wt_sum - 1.0) > 0.01:
                        st.warning(f"비중 합계: {wt_sum:.2f} (1.00이 되어야 합니다)")
                    else:
                        pbt_weights = [w / wt_sum for w in pbt_weights]  # 정규화

                pbt_c1, pbt_c2, pbt_c3, pbt_c4 = st.columns(4)
                pbt_period  = pbt_c1.selectbox("기간", ["1년","2년","3년","5년"], index=1, key="pbt_period")
                pbt_capital = pbt_c2.number_input("초기자금 (원)", value=10_000_000,
                                                   step=1_000_000, min_value=100_000, key="pbt_cap")
                pbt_buy_th  = pbt_c3.slider("매수 임계값", 50, 80, 58, 1, key="pbt_buy")
                pbt_sell_th = pbt_c4.slider("매도 임계값", 30, 60, 42, 1, key="pbt_sell")

            pbt_period_days = {"1년": 365, "2년": 730, "3년": 1095, "5년": 1825}

            if st.button("📦 포트폴리오 백테스트 실행", type="primary", key="pbt_run"):
                if not pbt_tickers:
                    st.error("종목을 입력해주세요.")
                else:
                    with st.spinner(f"{len(pbt_tickers)}개 종목 다운로드 및 백테스트 실행 중..."):
                        pbt_results, pbt_eq, pbt_spy = run_portfolio_backtest(
                            pbt_tickers, pbt_weights,
                            pbt_period_days[pbt_period],
                            pbt_buy_th, pbt_sell_th,
                            pbt_capital, bt_commission, bt_slippage,
                            w_tech=w_tech, w_fund=w_fund, w_macro=w_macro)

                    # ── 개별 종목 성과 ────────────────────────
                    st.markdown("#### 종목별 성과")
                    ok_res  = [r for r in pbt_results if not r.get('error')]
                    err_res = [r for r in pbt_results if r.get('error')]
                    if err_res:
                        for r in err_res:
                            st.warning(f"⚠️ {r['ticker']}: {r['error']}")

                    if ok_res:
                        sum_rows = []
                        for r in ok_res:
                            m = r['metrics']
                            sum_rows.append({
                                '종목':      r['ticker'],
                                '비중':      f"{r['weight']*100:.0f}%",
                                '전략수익률': m.get('전략 수익률', '-'),
                                'CAGR':      m.get('CAGR', '-'),
                                'MDD':       m.get('최대낙폭(MDD)', '-'),
                                'Sharpe':    m.get('Sharpe Ratio', '-'),
                                '승률':      m.get('승률', '-'),
                            })
                        st.dataframe(pd.DataFrame(sum_rows), width='stretch', hide_index=True)

                    # ── 포트폴리오 통합 자산 곡선 ─────────────
                    if pbt_eq is not None and not pbt_eq.empty:
                        st.markdown("#### 포트폴리오 자산 곡선 (SPY 벤치마크 포함)")
                        fig_pbt = go.Figure()
                        fig_pbt.add_trace(go.Scatter(
                            x=pbt_eq.index, y=pbt_eq.values,
                            name='포트폴리오',
                            line=dict(color='#2962ff', width=2.5),
                            fill='tozeroy', fillcolor='rgba(41,98,255,0.07)'))
                        if pbt_spy is not None:
                            # align spy to portfolio start capital
                            spy_norm = pbt_spy / float(pbt_spy.iloc[0]) * pbt_capital
                            fig_pbt.add_trace(go.Scatter(
                                x=spy_norm.index, y=spy_norm.values,
                                name='SPY (벤치마크)',
                                line=dict(color='#888', width=1.5, dash='dash')))
                        # 개별 종목 곡선 (얇게)
                        pal = ['#ff6b6b','#ffa94d','#69db7c','#74c0fc','#da77f2']
                        for ri, r in enumerate(ok_res):
                            eq_s = r['eq_df'].set_index('날짜')['전략']
                            fig_pbt.add_trace(go.Scatter(
                                x=eq_s.index, y=eq_s.values,
                                name=f"{r['ticker']} ({r['weight']*100:.0f}%)",
                                line=dict(color=pal[ri % len(pal)], width=1, dash='dot'),
                                opacity=0.7))
                        fig_pbt.update_layout(
                            height=450, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                            font=dict(color=TV_TEXT), hovermode='x unified',
                            yaxis=dict(gridcolor=TV_GRID, tickformat=',.0f', side='right'),
                            xaxis=dict(gridcolor=TV_GRID),
                            legend=dict(orientation='h', bgcolor='rgba(0,0,0,0)',
                                        yanchor='bottom', y=1.02),
                            margin=dict(l=0, r=60, t=40, b=0))
                        st.plotly_chart(fig_pbt, width='stretch')

                        # 포트폴리오 요약 지표
                        pf_final  = float(pbt_eq.iloc[-1])
                        pf_ret    = (pf_final / pbt_capital - 1) * 100
                        pf_days   = (pbt_eq.index[-1] - pbt_eq.index[0]).days
                        pf_years  = max(pf_days / 365, 0.01)
                        pf_cagr   = ((pf_final / pbt_capital) ** (1 / pf_years) - 1) * 100
                        pf_dr     = pd.Series(pbt_eq.values).pct_change().dropna()
                        pf_sharpe = float(pf_dr.mean() / pf_dr.std() * np.sqrt(252)) if pf_dr.std() > 0 else 0
                        roll_max  = pd.Series(pbt_eq.values).expanding().max()
                        pf_mdd    = float(((pd.Series(pbt_eq.values) - roll_max) / roll_max * 100).min())
                        pm1, pm2, pm3, pm4 = st.columns(4)
                        pm1.metric("포트폴리오 수익률", f"{pf_ret:+.1f}%")
                        pm2.metric("CAGR",             f"{pf_cagr:+.1f}%")
                        pm3.metric("MDD",              f"{pf_mdd:.1f}%")
                        pm4.metric("Sharpe",           f"{pf_sharpe:.2f}")

        # ── qt_sub6: 섹터 로테이션 ─────────────────────────────
        with qt_sub6:
            st.caption("12개 섹터 ETF 모멘텀 랭킹 — 상위 3~4개 섹터 집중, 하위 회피 전략")
            _sr_c, _sr_btn = st.columns([4, 1])
            with _sr_btn:
                if st.button("🔄 새로고침", key="sr_refresh"):
                    calc_sector_rotation.clear()
            with st.spinner("섹터 데이터 로딩 중..."):
                _sdf = calc_sector_rotation()
            if _sdf.empty:
                st.error("데이터 로드 실패")
            else:
                # 색상 강조: 상위 3 = 초록, 하위 3 = 빨강
                _top3  = set(_sdf.head(3)['ETF'])
                _bot3  = set(_sdf.tail(3)['ETF'])
                st.markdown("**🟢 상위 3 (비중 확대 후보) · 🔴 하위 3 (회피/비중 축소)**")
                _sc1, _sc2, _sc3 = st.columns(3)
                for _i, (_, _row) in enumerate(_sdf.head(3).iterrows()):
                    [_sc1, _sc2, _sc3][_i].metric(
                        f"🟢 #{_row['순위']} {_row['ETF']}",
                        f"{_row['3M%']:+.1f}% (3M)" if _row['3M%'] is not None else '--',
                        f"모멘텀 {_row['모멘텀']:+.1f}")
                _sb1, _sb2, _sb3 = st.columns(3)
                for _i, (_, _row) in enumerate(_sdf.tail(3).iterrows()):
                    [_sb1, _sb2, _sb3][_i].metric(
                        f"🔴 #{_row['순위']} {_row['ETF']}",
                        f"{_row['3M%']:+.1f}% (3M)" if _row['3M%'] is not None else '--',
                        f"모멘텀 {_row['모멘텀']:+.1f}")

                st.markdown("#### 📊 전체 섹터 순위")
                def _color_sr(val):
                    if isinstance(val, (int, float)):
                        if val > 5:  return 'color: #26a69a; font-weight:600'
                        if val < -5: return 'color: #ef5350; font-weight:600'
                    return ''
                st.dataframe(_sdf.style.map(_color_sr, subset=['1M%','3M%','6M%','모멘텀']),
                             width='stretch', hide_index=True)

                # 모멘텀 바 차트
                _fig_sr2 = go.Figure(go.Bar(
                    x=_sdf['모멘텀'], y=_sdf['ETF'] + ' ' + _sdf['섹터'],
                    orientation='h',
                    marker_color=['#26a69a' if v >= 0 else '#ef5350' for v in _sdf['모멘텀']],
                    text=[f"{v:+.1f}" for v in _sdf['모멘텀']],
                    textposition='outside'))
                _fig_sr2.update_layout(
                    height=380, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    xaxis_title="모멘텀 점수 (%)", yaxis=dict(autorange='reversed'),
                    margin=dict(l=0, r=60, t=10, b=0), font=dict(color=TV_TEXT))
                st.plotly_chart(_fig_sr2, width='stretch')
                st.caption("모멘텀 = 1M×50% + 3M×30% + 6M×20% 가중 평균 · 1시간 캐시")

        # ── qt_sub7: ML 신호 ───────────────────────────────────────
        with qt_sub7:
            st.subheader("🧠 ML 신호 (Purged K-Fold 검증)")
            st.caption(
                "9개 기술지표 feature → GradientBoosting 분류 → Purged K-Fold AUC 검증. "
                "Lopez de Prado 방식: 검증 구간과 label 기간이 겹치는 훈련 샘플 제거(purge) + embargo.")

            if not _ML_AVAILABLE:
                st.error("modules/ml_signals.py 또는 scikit-learn 로드 실패. requirements.txt 확인.")
            else:
                _ml_c1, _ml_c2, _ml_c3 = st.columns(3)
                _ml_ticker  = _ml_c1.text_input("티커", "AAPL", key="ml_ticker").strip().upper()
                _ml_horizon = _ml_c2.slider("예측 지평(일)", 5, 60, 20, 5, key="ml_horizon",
                                             help="N일 후 상승 여부 예측")
                _ml_splits  = _ml_c3.slider("K-Fold 수", 3, 10, 5, 1, key="ml_splits")

                _ml_period  = st.selectbox("학습 기간", ["3년","5년","10년"], index=1, key="ml_period")
                _ml_period_days = {"3년": 1095, "5년": 1825, "10년": 3650}

                if st.button("🧠 ML 신호 학습 & 검증", type="primary", key="ml_run"):
                    with st.spinner(f"{_ml_ticker} 데이터 다운로드 및 모델 학습 중..."):
                        _ml_end = datetime.now()
                        _ml_start = _ml_end - timedelta(days=_ml_period_days[_ml_period] + 100)
                        _ml_df = download_stock(_ml_ticker, start=_ml_start, end=_ml_end)

                    if _ml_df.empty or len(_ml_df) < 200:
                        st.error(f"데이터 부족: {len(_ml_df)}일 (최소 200일 필요)")
                    else:
                        try:
                            with st.spinner("Purged K-Fold 교차검증 실행 중..."):
                                _ml_res = _ml_train(_ml_df, horizon=_ml_horizon, n_splits=_ml_splits)
                            st.session_state['ml_result'] = {'res': _ml_res, 'ticker': _ml_ticker, 'df': _ml_df}
                        except Exception as _ml_e:
                            st.error(f"학습 오류: {_ml_e}")

                if 'ml_result' in st.session_state:
                    _ml_r = st.session_state['ml_result']
                    _res  = _ml_r['res']

                    # AUC 지표
                    _auc_color = "normal" if _res['mean_auc'] >= 0.55 else "inverse"
                    _auc_label = ("좋은 edge ✅" if _res['mean_auc'] >= 0.58 else
                                  ("약한 edge 🔶" if _res['mean_auc'] >= 0.53 else "신호 없음 ❌"))
                    m1, m2, m3 = st.columns(3)
                    m1.metric("평균 AUC", f"{_res['mean_auc']:.4f}", delta=_auc_label, delta_color=_auc_color)
                    m2.metric("AUC 표준편차", f"{_res['std_auc']:.4f}",
                              delta=("안정적 ✅" if _res['std_auc'] < 0.05 else "불안정 ⚠️"),
                              delta_color=("off" if _res['std_auc'] < 0.05 else "inverse"))
                    m3.metric("유효 Fold 수", f"{_res['n_folds_used']}개")

                    st.caption(f"💡 {_res['interpretation']}")

                    # Fold별 AUC 바 차트
                    _fold_aucs = _res['fold_aucs']
                    _fig_auc = go.Figure()
                    _fig_auc.add_trace(go.Bar(
                        x=[f"Fold {i+1}" for i in range(len(_fold_aucs))],
                        y=_fold_aucs,
                        marker_color=['#26a69a' if a >= 0.55 else '#ef5350' for a in _fold_aucs],
                        text=[f"{a:.3f}" for a in _fold_aucs],
                        textposition='outside'))
                    _fig_auc.add_hline(y=0.5, line_dash='dash', line_color='#888',
                                       annotation_text="동전던지기 (0.5)", annotation_position="right")
                    _fig_auc.add_hline(y=0.55, line_dash='dot', line_color='#26a69a',
                                       annotation_text="edge 기준 (0.55)", annotation_position="right")
                    _fig_auc.update_layout(
                        height=300, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT), title=dict(text="Fold별 Out-of-Fold AUC", font=dict(size=13)),
                        yaxis=dict(range=[0.35, 1.0], gridcolor=TV_GRID, title="AUC"),
                        xaxis=dict(gridcolor=TV_GRID),
                        margin=dict(l=20, r=80, t=50, b=20), showlegend=False)
                    st.plotly_chart(_fig_auc, width='stretch')

                    # 피처 중요도
                    with st.expander("📊 피처 중요도", expanded=True):
                        _fi = _res['feature_importance']
                        _fi_names = list(_fi.keys())
                        _fi_vals  = list(_fi.values())
                        _fig_fi = go.Figure(go.Bar(
                            x=_fi_vals, y=_fi_names, orientation='h',
                            marker_color='rgba(41,98,255,0.6)',
                            text=[f"{v:.3f}" for v in _fi_vals],
                            textposition='outside'))
                        _fig_fi.update_layout(
                            height=280, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                            font=dict(color=TV_TEXT),
                            xaxis=dict(title="중요도", gridcolor=TV_GRID),
                            yaxis=dict(autorange='reversed'),
                            margin=dict(l=20, r=80, t=20, b=20), showlegend=False)
                        st.plotly_chart(_fig_fi, width='stretch')
                        _fi_label = {'ma_align':'이평 정렬', 'rsi':'RSI(14)', 'macd_hist':'MACD 히스토그램',
                                     'bb_pos':'볼린저 위치', 'vol_ratio':'거래량 비율', 'mom_20':'모멘텀(20일)',
                                     'mom_5':'모멘텀(5일)', 'hl_pos':'고저 위치', 'atr_ratio':'ATR 변동성'}
                        st.caption(" · ".join([f"{_fi_label.get(k,k)}: {v:.3f}" for k, v in _fi.items()]))

                    # OOF 예측 확률 시계열
                    with st.expander("📈 Out-of-Fold 예측 확률 추이"):
                        _oof = _res['oof_predictions'].dropna()
                        if not _oof.empty:
                            _fig_oof = go.Figure()
                            _fig_oof.add_trace(go.Scatter(
                                x=_oof.index, y=_oof.values, mode='lines',
                                line=dict(color='#2962ff', width=1.5),
                                name='상승 확률'))
                            _fig_oof.add_hline(y=0.5, line_dash='dash', line_color='#888')
                            _fig_oof.update_layout(
                                height=250, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                font=dict(color=TV_TEXT),
                                yaxis=dict(range=[0,1], gridcolor=TV_GRID, title="상승 확률"),
                                xaxis=dict(gridcolor=TV_GRID),
                                margin=dict(l=20, r=20, t=20, b=20), showlegend=False)
                            st.plotly_chart(_fig_oof, width='stretch')
                            # 최근 신호
                            _last_prob = float(_oof.iloc[-1])
                            if _last_prob >= 0.60:
                                st.success(f"최신 신호: 상승 확률 **{_last_prob*100:.1f}%** — ML 매수 신호")
                            elif _last_prob <= 0.40:
                                st.error(f"최신 신호: 상승 확률 **{_last_prob*100:.1f}%** — ML 매도 신호")
                            else:
                                st.info(f"최신 신호: 상승 확률 **{_last_prob*100:.1f}%** — 중립 (0.40~0.60)")

        # ── qt_sub8: 고급 분석 ────────────────────────────────────
        with qt_sub8:
            st.subheader("🧪 고급 분석")

            # ── 데이터 무결성 ──
            with st.expander("🔍 데이터 무결성 검증", expanded=False):
                _di_ticker = st.text_input("티커 (데이터 무결성 검사)", "AAPL", key="di_ticker").strip().upper()
                if st.button("🔍 데이터 무결성 검사 실행", key="di_run"):
                    if not _DATA_INTEGRITY_AVAILABLE:
                        st.error("modules/data_integrity.py 로드 실패.")
                    else:
                        with st.spinner("데이터 다운로드 및 검증 중..."):
                            _di_end = datetime.now()
                            _di_start = _di_end - timedelta(days=365 * 3)
                            _di_df = download_stock(_di_ticker, start=_di_start, end=_di_end)
                        if _di_df.empty:
                            st.error("데이터 없음.")
                        else:
                            _di_result = _data_integrity_check(_di_df, _di_ticker)
                            if _di_result['overall_ok']:
                                st.success(f"✅ {_di_ticker}: 데이터 무결성 이상 없음 ({len(_di_df)}행)")
                            else:
                                st.warning(f"⚠️ {_di_ticker}: 무결성 이슈 감지됨")
                            _di_ohlc = _di_result['ohlc_sanity']
                            st.markdown(f"**OHLC 검사**: {'✅ 이상 없음' if _di_ohlc['is_clean'] else '❌ ' + ' / '.join(_di_ohlc['issues'])}")
                            _di_jmp = _di_result['suspicious_jumps']
                            st.markdown(f"**급변 탐지**: {_di_jmp['note']}")
                            if _di_jmp['jumps']:
                                st.dataframe(pd.DataFrame(_di_jmp['jumps']), width='stretch')

            # ── 스트레스 테스트 ──
            with st.expander("💥 역사적 시나리오 스트레스 테스트", expanded=False):
                if not _STRESS_TEST_AVAILABLE:
                    st.error("modules/stress_test.py 로드 실패.")
                else:
                    _st_ticker = st.text_input("티커 (스트레스 테스트)", "SPY", key="st_ticker").strip().upper()
                    _st_scenario = st.selectbox(
                        "시나리오",
                        options=list(_STRESS_PERIODS.keys()),
                        format_func=lambda k: _STRESS_PERIODS[k]['label'],
                        key="st_scenario"
                    )
                    st.caption(_STRESS_PERIODS[_st_scenario]['desc'])
                    if st.button("💥 시나리오 실행", key="st_run"):
                        with st.spinner("해당 기간 데이터 다운로드 및 백테스트 중..."):
                            _st_end = datetime.now()
                            _st_start = _st_end - timedelta(days=365 * 5)
                            _st_full_df = download_stock(_st_ticker, start=_st_start, end=_st_end)
                        if _st_full_df.empty:
                            st.error("데이터 없음.")
                        else:
                            _st_res = _replay_scenario(
                                run_backtest, _st_full_df, _st_scenario,
                                buy_th=65, sell_th=45, initial_capital=10_000_000
                            )
                            if 'error' in _st_res:
                                st.error(_st_res['error'])
                            else:
                                _st_m = _st_res['metrics']
                                _stc1, _stc2, _stc3 = st.columns(3)
                                _stc1.metric("전략 수익률", f"{_st_m.get('전략 수익률', 'N/A')}")
                                _stc2.metric("MDD", f"{_st_m.get('최대낙폭(MDD)', 'N/A')}")
                                _stc3.metric("Sharpe", f"{_st_m.get('Sharpe Ratio', 'N/A')}")
                                st.caption(f"기간: {_st_res['period']} · {_st_res['n_rows']}거래일")

            # ── 알파 디케이 ──
            with st.expander("📉 알파 디케이 모니터", expanded=False):
                if not _ALPHA_DECAY_AVAILABLE:
                    st.error("modules/alpha_decay_monitor.py 또는 scipy 로드 실패.")
                else:
                    st.caption("백테스트 모수(일평균수익률·표준편차)와 실전(live) 수익률을 비교해 알파 소멸 여부를 감지합니다.")
                    _ad_c1, _ad_c2 = st.columns(2)
                    _ad_bt_mean = _ad_c1.number_input("백테스트 일평균수익률", value=0.0003, format="%.6f", key="ad_bt_mean",
                                                       help="예: 연 7.5% → 0.075/252 ≒ 0.000298")
                    _ad_bt_std = _ad_c2.number_input("백테스트 일별 표준편차", value=0.008, format="%.6f", key="ad_bt_std")
                    _ad_ticker = st.text_input("실전 비교 티커", "SPY", key="ad_ticker").strip().upper()
                    _ad_window = st.slider("롤링 윈도우(일)", 20, 120, 60, 10, key="ad_window")
                    if st.button("📉 알파 디케이 분석 실행", key="ad_run"):
                        with st.spinner("데이터 다운로드 중..."):
                            _ad_end = datetime.now()
                            _ad_start = _ad_end - timedelta(days=365 * 2)
                            _ad_df = download_stock(_ad_ticker, start=_ad_start, end=_ad_end)
                        if _ad_df.empty or len(_ad_df) < _ad_window + 5:
                            st.error(f"데이터 부족 (최소 {_ad_window+5}일 필요).")
                        else:
                            _ad_ret = _ad_df['Close'].pct_change().dropna()
                            _ad_result = _detect_alpha_decay(_ad_ret, _ad_bt_mean, _ad_bt_std, _ad_window)
                            if _ad_result.get('detected'):
                                st.error(f"🚨 알파 디케이 감지: {_ad_result['reason']}")
                            else:
                                st.success(f"✅ 정상: {_ad_result['reason']}")
                            _ad_perf = _rolling_perf_vs_bt(_ad_ret, _ad_bt_mean, _ad_bt_std, _ad_window)
                            _ad_fig = go.Figure()
                            _ad_fig.add_trace(go.Scatter(x=_ad_perf.index, y=_ad_perf['z_score'],
                                                          mode='lines', name='Z-Score', line=dict(color='#2962ff', width=1.5)))
                            _ad_fig.add_hline(y=-2.0, line_dash='dash', line_color='#ef5350', annotation_text='경고선 (-2σ)')
                            _ad_fig.update_layout(height=280, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                                   font=dict(color=TV_TEXT), title=dict(text="롤링 Z-Score (실전 vs 백테스트)", font=dict(size=13)),
                                                   yaxis=dict(gridcolor=TV_GRID), xaxis=dict(gridcolor=TV_GRID),
                                                   margin=dict(l=20, r=20, t=40, b=20))
                            st.plotly_chart(_ad_fig, width='stretch')

            # ── 시그널 디케이 ──
            with st.expander("⏳ 시그널 IC 감쇠 분석", expanded=False):
                if not _SIGNAL_DECAY_AVAILABLE:
                    st.error("modules/signal_decay_analysis.py 또는 scipy 로드 실패.")
                else:
                    st.caption("RSI 같은 기술 신호의 IC(정보계수)가 보유기간에 따라 어떻게 감쇠하는지 측정합니다.")
                    _sd_ticker = st.text_input("티커 (시그널 디케이)", "AAPL", key="sd_ticker").strip().upper()
                    if st.button("⏳ IC 감쇠 분석 실행", key="sd_run"):
                        with st.spinner("데이터 다운로드 및 IC 계산 중..."):
                            _sd_end = datetime.now()
                            _sd_start = _sd_end - timedelta(days=365 * 5)
                            _sd_df = download_stock(_sd_ticker, start=_sd_start, end=_sd_end)
                        if _sd_df.empty or len(_sd_df) < 200:
                            st.error("데이터 부족.")
                        else:
                            _sd_rsi = calc_rsi(_sd_df['Close'], period=14).dropna()
                            _sd_close = _sd_df['Close'].loc[_sd_rsi.index]
                            _sd_horizons = [1, 5, 10, 20, 40, 60]
                            _sd_ic = _signal_ic_decay(_sd_rsi, _sd_close, _sd_horizons)
                            st.dataframe(_sd_ic.style.format({'IC': '{:.4f}', 't_stat': '{:.2f}', 'p_value': '{:.4f}'}),
                                         width='stretch')
                            _sd_fig = go.Figure()
                            _sd_fig.add_trace(go.Bar(x=_sd_ic.index.tolist(), y=_sd_ic['IC'].tolist(),
                                                      marker_color=['#26a69a' if v > 0 else '#ef5350' for v in _sd_ic['IC']]))
                            _sd_fig.update_layout(height=250, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                                   font=dict(color=TV_TEXT), title=dict(text="RSI14 IC Decay", font=dict(size=13)),
                                                   xaxis=dict(title="보유기간(일)", gridcolor=TV_GRID),
                                                   yaxis=dict(title="IC", gridcolor=TV_GRID),
                                                   margin=dict(l=20, r=20, t=40, b=20))
                            st.plotly_chart(_sd_fig, width='stretch')

            # ── 팩터 리스크 모델 ──
            with st.expander("🏗️ 팩터 리스크 모델 (스타일 분석)", expanded=False):
                if not _FACTOR_RISK_AVAILABLE:
                    st.error("modules/factor_risk_model.py 또는 scipy 로드 실패.")
                else:
                    st.caption("전략 수익률을 시장·팩터로 회귀분석해 순수 알파와 베타를 분리합니다.")
                    _fr_ticker = st.text_input("전략 티커", "AAPL", key="fr_ticker").strip().upper()
                    if st.button("🏗️ 스타일 분석 실행", key="fr_run"):
                        with st.spinner("데이터 다운로드 중..."):
                            _fr_end = datetime.now()
                            _fr_start = _fr_end - timedelta(days=365 * 3)
                            _fr_stock = download_stock(_fr_ticker, start=_fr_start, end=_fr_end)
                            _fr_spy = download_stock("SPY", start=_fr_start, end=_fr_end)
                        if _fr_stock.empty or _fr_spy.empty:
                            st.error("데이터 없음.")
                        else:
                            _fr_ret = _fr_stock['Close'].pct_change().dropna()
                            _fr_mkt = _fr_spy['Close'].pct_change().dropna()
                            _fr_aligned = pd.concat([_fr_ret.rename('stock'), _fr_mkt.rename('MKT')], axis=1).dropna()
                            _fr_sa = _style_analysis(_fr_aligned['stock'], pd.DataFrame({'MKT': _fr_aligned['MKT']}))
                            if 'error' in _fr_sa:
                                st.error(_fr_sa['error'])
                            else:
                                _frc1, _frc2, _frc3 = st.columns(3)
                                _frc1.metric("연율화 알파", f"{_fr_sa['alpha_annualized_pct']:.2f}%")
                                _frc2.metric("시장 Beta", f"{_fr_sa['betas'].get('MKT', 0):.3f}")
                                _frc3.metric("R²", f"{_fr_sa['r_squared']:.3f}")
                                st.caption(_fr_sa['interpretation'])
                            _fr_rb = _rolling_beta(_fr_aligned['stock'], _fr_aligned['MKT'])
                            _fr_fig = go.Figure()
                            _fr_fig.add_trace(go.Scatter(x=_fr_rb.index, y=_fr_rb.values,
                                                          mode='lines', name='Rolling Beta (60일)', line=dict(color='#ff9800', width=1.5)))
                            _fr_fig.add_hline(y=1.0, line_dash='dash', line_color='#888', annotation_text='β=1')
                            _fr_fig.update_layout(height=250, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                                   font=dict(color=TV_TEXT), title=dict(text="롤링 시장 베타(60일)", font=dict(size=13)),
                                                   yaxis=dict(gridcolor=TV_GRID), xaxis=dict(gridcolor=TV_GRID),
                                                   margin=dict(l=20, r=20, t=40, b=20))
                            st.plotly_chart(_fr_fig, width='stretch')

        # ── qt_sub9: 운영 안전성 ──────────────────────────────────
        with qt_sub9:
            st.subheader("🔒 운영 안전성")

            if not _OPS_SAFETY_AVAILABLE:
                st.error("modules/ops_safety.py 로드 실패.")
            else:
                # 킬스위치
                with st.expander("🛑 킬스위치 (일일 손실 한도)", expanded=True):
                    st.caption("당일 손실이 임계치를 초과하면 자동매매 거래 차단. 세션 내에서만 유지됩니다.")
                    _ks_c1, _ks_c2 = st.columns(2)
                    _ks_loss_limit = _ks_c1.number_input("일일 최대 손실 한도 (%)", value=3.0, min_value=0.5, max_value=20.0, step=0.5, key="ks_loss_limit")
                    _ks_max_errors = _ks_c2.number_input("연속 오류 한도 (회)", value=5, min_value=1, max_value=20, key="ks_max_errors")

                    if 'ks_instance' not in st.session_state:
                        st.session_state['ks_instance'] = _KillSwitch(
                            max_daily_loss_pct=_ks_loss_limit, max_errors=int(_ks_max_errors))

                    _ks = st.session_state['ks_instance']
                    _ks_status = _ks.status()

                    if _ks_status['triggered']:
                        st.error(f"🛑 킬스위치 발동됨: {_ks_status['reason']}")
                    else:
                        st.success("✅ 킬스위치 정상 — 거래 허용 상태")

                    _ks_col1, _ks_col2, _ks_col3 = st.columns(3)
                    _ks_cur_eq = _ks_col1.number_input("현재 자산가치 (원)", value=10_000_000, step=100_000, key="ks_cur_eq")
                    if _ks_col2.button("📋 손실 한도 체크", key="ks_check"):
                        _ks.set_day_start_equity(10_000_000)
                        if _ks.check_daily_loss(_ks_cur_eq):
                            st.error(f"🛑 손실 한도 초과 → 거래 차단")
                        else:
                            st.success("✅ 정상 범위")
                    if _ks_col3.button("🔄 킬스위치 초기화", key="ks_reset"):
                        _ks.reset()
                        st.success("킬스위치 초기화 완료")
                        st.rerun()

                # 포지션 대사
                with st.expander("⚖️ 포지션 대사 (의도 vs 실제)", expanded=False):
                    st.caption("앱이 생각하는 보유 수량과 실제 포지션을 비교해 불일치를 탐지합니다.")
                    _rec_intended_raw = st.text_area(
                        "의도 포지션 (JSON, 예: {\"삼성전자.KS\": 10, \"005490.KS\": 5})",
                        value='{}', key="rec_intended")
                    if st.button("⚖️ 포지션 대사 실행", key="rec_run"):
                        try:
                            import json as _json_rec
                            _intended = _json_rec.loads(_rec_intended_raw)
                            if _OPS_SAFETY_AVAILABLE:
                                _rec_result = _reconcile_pos(_intended, [])
                                if _rec_result['ok']:
                                    st.success("✅ 포지션 일치")
                                else:
                                    st.error(f"❌ 불일치 {len(_rec_result['mismatches'])}건")
                                    st.dataframe(pd.DataFrame(_rec_result['mismatches']), width='stretch')
                            else:
                                st.info("ops_safety 모듈 없음 — 의도 포지션만 표시합니다.")
                                st.json(_intended)
                        except ValueError:
                            st.error("JSON 파싱 오류")

            # ── 토스 자동매매 성과 안내 ──────────────────────────
            with st.expander("📈 토스증권 자동매매 성과 모니터링", expanded=True):
                st.markdown(
                    """
자동매매 실행 결과는 **텔레그램 봇**으로 매일 수신됩니다.

**확인 방법:**
- 텔레그램에서 일별 P&L 리포트 확인 (`daily_report_toss.py`)
- GitHub Actions → `paper-trade.yml` 로그에서 실행 내역 확인
- `equity_log.json`: 일별 자산가치 추적
- `signal_log.json`: 매수/매도 시그널 이력

**주요 성과 지표 (GitHub Actions 로그):**
- 총 자산 · 매수여력 · 당일 수익률
- 보유 포지션 목록 · 미실현 손익
- 누적 수익률 · Sharpe · 최대낙폭(MDD)
"""
                )
                st.caption("⚙️ 자동 실행: `.github/workflows/paper-trade.yml` (국내) / `paper-trade-us.yml` (미국)")

        # ── qt_sub10: 세금 계산기 ─────────────────────────────────
        with qt_sub10:
            st.subheader("💴 해외주식 양도소득세 계산기")
            st.caption("⚠️ 교육·참고 목적. 실제 납세 전 반드시 세무사에게 확인하세요.")

            if not _TAX_KR_AVAILABLE:
                st.error("modules/tax_kr.py 로드 실패.")
            else:
                if 'tax_ledger' not in st.session_state:
                    st.session_state['tax_ledger'] = _TaxLedger()
                _ledger = st.session_state['tax_ledger']

                # 매수 기록 입력
                with st.expander("📥 매수 기록 입력", expanded=True):
                    _tx_c1, _tx_c2, _tx_c3, _tx_c4, _tx_c5 = st.columns(5)
                    _tx_ticker = _tx_c1.text_input("티커", "AAPL", key="tx_ticker").strip().upper()
                    _tx_buy_date = _tx_c2.date_input("매수일", key="tx_buy_date")
                    _tx_qty = _tx_c3.number_input("주수", value=10.0, min_value=0.01, key="tx_qty")
                    _tx_price_usd = _tx_c4.number_input("매수가($)", value=150.0, min_value=0.01, key="tx_price_usd")
                    _tx_fx = _tx_c5.number_input("환율(₩/$)", value=1350.0, min_value=100.0, key="tx_fx")
                    if st.button("📥 매수 기록 추가", key="tx_buy_add"):
                        from datetime import date as _date
                        _ledger.buy(_tx_ticker, _tx_buy_date, _tx_qty, _tx_price_usd, _tx_fx)
                        st.success(f"{_tx_ticker} {_tx_qty}주 매수 기록 추가 (취득원가: {_tx_qty*_tx_price_usd*_tx_fx:,.0f}원)")

                # 매도 기록 입력
                with st.expander("📤 매도 기록 입력 (FIFO 자동 계산)", expanded=False):
                    _ts_c1, _ts_c2, _ts_c3, _ts_c4, _ts_c5 = st.columns(5)
                    _ts_ticker = _ts_c1.text_input("티커", "AAPL", key="ts_ticker").strip().upper()
                    _ts_sell_date = _ts_c2.date_input("매도일", key="ts_sell_date")
                    _ts_qty = _ts_c3.number_input("매도 주수", value=5.0, min_value=0.01, key="ts_qty")
                    _ts_price_usd = _ts_c4.number_input("매도가($)", value=180.0, min_value=0.01, key="ts_price_usd")
                    _ts_fx = _ts_c5.number_input("매도시 환율(₩/$)", value=1320.0, min_value=100.0, key="ts_fx")
                    if st.button("📤 매도 기록 추가 (FIFO)", key="ts_sell_add"):
                        try:
                            _ledger.sell(_ts_ticker, _ts_sell_date, _ts_qty, _ts_price_usd, _ts_fx)
                            _last = _ledger.realized[-1]
                            _gain_color = "success" if _last.gain_krw >= 0 else "error"
                            getattr(st, _gain_color)(f"매도 완료 — 양도차익: {_last.gain_krw:+,}원")
                        except Exception as _tx_e:
                            st.error(f"오류: {_tx_e}")

                # 현재 보유 & 세금 계산
                _tax_hd = _ledger.holdings()
                if _tax_hd:
                    st.markdown("**현재 보유 포지션**")
                    st.dataframe(pd.DataFrame(_tax_hd).T, width='stretch')

                if _ledger.realized:
                    _tx_year = st.selectbox("과세 연도", options=sorted(set(t.sell_year for t in _ledger.realized), reverse=True), key="tx_year")
                    _tx_calc = _calc_tax(_ledger.realized, _tx_year)
                    _tc1, _tc2, _tc3, _tc4 = st.columns(4)
                    _tc1.metric("총 양도차익", f"{_tx_calc['gross_gain_krw']:,}원")
                    _tc2.metric("기본공제", f"{_tx_calc['basic_deduction_krw']:,}원")
                    _tc3.metric("과세표준", f"{_tx_calc['taxable_gain_krw']:,}원")
                    _tc4.metric("예상 납부세액", f"{_tx_calc['estimated_tax_krw']:,}원", delta="22% 적용")
                    st.caption(_tx_calc['note'])

                if st.button("🗑️ 세금 장부 초기화", key="tx_reset"):
                    st.session_state['tax_ledger'] = _TaxLedger()
                    st.success("장부 초기화 완료")
                    st.rerun()

    # ── Tab: 매매 일지 ─────────────────────────────────────────
    with tab_journal:
        _gs_ok = _gs_configured()
        if _gs_ok:
            st.caption("매매 기록 추적 · Google Sheets 자동 동기화 · 시그널 적중률 분석")
        else:
            st.caption("매매 기록 추적 · 시그널 적중률 분석 · CSV 백업/복원")

        # ── 초기화 + Google Sheets 자동 로드 ──────────────────
        if 'trades' not in st.session_state:
            st.session_state['trades'] = []
            if _gs_ok and not st.session_state.get('_gs_loaded'):
                with st.spinner("Google Sheets에서 기록 불러오는 중..."):
                    _loaded = _gs_load_trades()
                if _loaded is not None:
                    st.session_state['trades'] = _loaded
                    st.session_state['_gs_loaded'] = True

        # ── Google Sheets 상태 표시줄 ──────────────────────────
        if _gs_ok:
            _gsc1, _gsc2, _gsc3 = st.columns([2, 1, 1])
            sid = st.secrets["google_sheets"].get("spreadsheet_id", "")
            _gsc1.success(f"Google Sheets 연결됨  |  스프레드시트 ID: `{sid[:20]}…`")
            if _gsc2.button("☁️ GS에 저장", key="gs_push"):
                with st.spinner("저장 중..."):
                    _ok = _gs_save_trades(st.session_state['trades'])
                if _ok:
                    st.success("Google Sheets 저장 완료")
                else:
                    st.error("저장 실패 — 인증 또는 권한을 확인하세요")
            if _gsc3.button("🔄 GS에서 불러오기", key="gs_pull"):
                with st.spinner("불러오는 중..."):
                    _pulled = _gs_load_trades()
                if _pulled is not None:
                    st.session_state['trades'] = _pulled
                    st.rerun()
                else:
                    st.error("불러오기 실패")
        else:
            with st.expander("☁️ Google Sheets 연동 설정 방법", expanded=False):
                st.markdown("""
**설정하면 앱을 껐다 켜도 매매 기록이 유지됩니다.**

1. [Google Cloud Console](https://console.cloud.google.com/) → 새 프로젝트 생성
2. APIs & Services → **Google Sheets API** + **Google Drive API** 활성화
3. IAM & Admin → Service Accounts → 새 계정 생성 → JSON 키 다운로드
4. Google Sheets에서 새 스프레드시트 생성 → 서비스 계정 이메일에 **편집자** 권한 공유
5. Streamlit Cloud → App settings → **Secrets** 탭에 아래 형식으로 추가:

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "..."
private_key = "-----BEGIN RSA PRIVATE KEY-----\\n...\\n-----END RSA PRIVATE KEY-----\\n"
client_email = "your-sa@project.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"

[google_sheets]
spreadsheet_id = "스프레드시트_URL에서_/d/와_/edit_사이_ID"
worksheet_name = "trades"
```
""")

        # ── CSV 업로드로 복원 (GS 미설정 시 주 백업 수단) ──────
        with st.expander("📂 CSV로 불러오기", expanded=False):
            _up = st.file_uploader("이전 세션 CSV 업로드", type='csv', key='journal_upload')
            if _up:
                try:
                    _udf = pd.read_csv(_up)
                    st.session_state['trades'] = _udf.to_dict('records')
                    if _gs_ok:
                        _gs_save_trades(st.session_state['trades'])
                    st.success(f"{len(st.session_state['trades'])}건 복원 완료")
                except Exception as _ue:
                    st.error(f"업로드 오류: {_ue}")

        # ── 새 매매 입력 폼 ────────────────────────────────────
        with st.expander("➕ 새 매매 기록 추가", expanded=True):
            _j1, _j2, _j3, _j4 = st.columns(4)
            _j_tkr   = _j1.text_input("종목 티커", key="j_tkr").upper().strip()
            _j_act   = _j2.selectbox("구분", ["매수", "매도"], key="j_act")
            _j_date  = _j3.date_input("날짜", key="j_date")
            _j_price = _j4.number_input("가격 ($)", min_value=0.01, value=100.0, step=0.01, key="j_price")
            _j5, _j6, _j7, _j8 = st.columns(4)
            _j_shares = _j5.number_input("수량 (주)", min_value=1, value=10, key="j_shares")
            _j_sig    = _j6.selectbox("시그널 출처", ["퀀트 시스템", "패턴 분석", "수동 판단", "대시보드"], key="j_sig")
            _j_exit_p = _j7.number_input("청산가 (0=미청산)", min_value=0.0, value=0.0, step=0.01, key="j_exit_p")
            _j_note   = _j8.text_input("메모", key="j_note")
            if st.button("✅ 기록 추가", key="j_add"):
                if _j_tkr:
                    _pnl = (_j_exit_p - _j_price) * _j_shares if (_j_act == "매수" and _j_exit_p > 0) else \
                           (_j_price - _j_exit_p) * _j_shares if (_j_act == "매도" and _j_exit_p > 0) else None
                    _pnl_pct = (_j_exit_p / _j_price - 1) * 100 if (_j_act == "매수" and _j_exit_p > 0) else \
                               (_j_price / _j_exit_p - 1) * 100 if (_j_act == "매도" and _j_exit_p > 0) else None
                    st.session_state['trades'].append({
                        '날짜': str(_j_date), '종목': _j_tkr, '구분': _j_act,
                        '진입가': _j_price, '수량': _j_shares,
                        '투자금': round(_j_price * _j_shares, 2),
                        '청산가': _j_exit_p if _j_exit_p > 0 else None,
                        '손익($)': round(_pnl, 2) if _pnl is not None else None,
                        '손익(%)': round(_pnl_pct, 2) if _pnl_pct is not None else None,
                        '시그널': _j_sig, '메모': _j_note,
                    })
                    if _gs_ok:
                        _gs_save_trades(st.session_state['trades'])
                    st.success(f"{_j_tkr} {_j_act} 기록 추가" + (" + GS 저장" if _gs_ok else " 완료"))
                else:
                    st.warning("종목 티커를 입력하세요.")

        # ── 기록 테이블 + 전체 성과 요약 ──────────────────────
        if st.session_state['trades']:
            _tdf = pd.DataFrame(st.session_state['trades'])

            _closed = _tdf[_tdf['손익(%)'].notna()].copy()
            _closed['손익(%)'] = pd.to_numeric(_closed['손익(%)'], errors='coerce')
            _closed['손익($)'] = pd.to_numeric(_closed['손익($)'], errors='coerce')
            _closed = _closed.dropna(subset=['손익(%)'])

            if not _closed.empty:
                _wins   = _closed[_closed['손익(%)'] > 0]
                _losses = _closed[_closed['손익(%)'] <= 0]
                _wr     = len(_wins) / len(_closed) * 100
                _avg_w  = float(_wins['손익(%)'].mean()) if not _wins.empty else 0
                _avg_l  = abs(float(_losses['손익(%)'].mean())) if not _losses.empty else 0
                _total_pnl = float(_closed['손익($)'].sum())
                _pf = (_wins['손익($)'].sum() / max(abs(_losses['손익($)'].sum()), 0.01)
                       if not _wins.empty else 0)
                _kf_calc = kelly_fraction(_wr/100, _avg_w, _avg_l) if _avg_l > 0 else 0
                _exp_val = _wr/100 * _avg_w - (1-_wr/100) * _avg_l

                _jm1, _jm2, _jm3, _jm4, _jm5, _jm6 = st.columns(6)
                _jm1.metric("승률",       f"{_wr:.1f}%",    f"{len(_closed)}건 청산")
                _jm2.metric("평균 수익",  f"+{_avg_w:.1f}%", f"{len(_wins)}승")
                _jm3.metric("평균 손실",  f"-{_avg_l:.1f}%", f"{len(_losses)}패")
                _jm4.metric("수익 팩터",  f"{_pf:.2f}",      "1.5+ 우수")
                _jm5.metric("기대값/매매", f"{_exp_val:+.2f}%")
                _jm6.metric("권장 Kelly", f"{_kf_calc*100:.1f}%", "half-Kelly")

            # 전체 기록 표
            st.markdown("#### 📋 전체 매매 기록")
            _edit_df = st.data_editor(_tdf, width='stretch', num_rows="dynamic",
                                      key="journal_editor")
            _sv1, _sv2 = st.columns(2)
            if _sv1.button("💾 변경사항 저장", key="j_save"):
                st.session_state['trades'] = _edit_df.to_dict('records')
                if _gs_ok:
                    _gs_save_trades(st.session_state['trades'])
                st.success("저장 완료" + (" + GS 동기화" if _gs_ok else ""))
            _csv_data = _tdf.to_csv(index=False).encode('utf-8-sig')
            _sv2.download_button("⬇️ CSV 다운로드", _csv_data,
                                 file_name=f"trades_{datetime.now().strftime('%Y%m%d')}.csv",
                                 mime='text/csv', key='j_download')

            # ── 시그널 출처별 적중률 분석 ──────────────────────
            if not _closed.empty and '시그널' in _closed.columns:
                st.markdown("#### 📡 시그널 출처별 성과 분석")

                def _sig_stats(g):
                    wins_g = g[g['손익(%)'] > 0]
                    loss_g = g[g['손익(%)'] <= 0]
                    wr_g = len(wins_g) / len(g) * 100
                    avg_w_g = float(wins_g['손익(%)'].mean()) if not wins_g.empty else 0
                    avg_l_g = abs(float(loss_g['손익(%)'].mean())) if not loss_g.empty else 0
                    pf_g = (wins_g['손익($)'].sum() /
                            max(abs(loss_g['손익($)'].sum()), 0.01)) if not wins_g.empty else 0
                    ev_g = wr_g/100 * avg_w_g - (1 - wr_g/100) * avg_l_g
                    return pd.Series({
                        '거래수': len(g),
                        '승률(%)': round(wr_g, 1),
                        '평균수익(%)': round(avg_w_g, 2),
                        '평균손실(%)': round(avg_l_g, 2),
                        '수익팩터': round(pf_g, 2),
                        '기대값(%)': round(ev_g, 2),
                        '총손익($)': round(float(g['손익($)'].sum()), 2),
                    })

                _sig_df = _closed.groupby('시그널').apply(_sig_stats).reset_index()
                _sig_df = _sig_df.sort_values('기대값(%)', ascending=False)

                # 컬러 스타일 적용
                def _color_wr(val):
                    if isinstance(val, (int, float)):
                        if val >= 60: return 'color: #26a69a; font-weight:600'
                        if val < 45:  return 'color: #ef5350; font-weight:600'
                    return ''
                def _color_ev(val):
                    if isinstance(val, (int, float)):
                        if val > 0: return 'color: #26a69a'
                        if val < 0: return 'color: #ef5350'
                    return ''

                st.dataframe(
                    _sig_df.style
                        .map(_color_wr, subset=['승률(%)'])
                        .map(_color_ev, subset=['기대값(%)', '총손익($)']),
                    width='stretch', hide_index=True)

                # 시그널별 승률 바 차트
                _fig_sig = go.Figure()
                _fig_sig.add_trace(go.Bar(
                    name='승률(%)', x=_sig_df['시그널'], y=_sig_df['승률(%)'],
                    marker_color=['#26a69a' if v >= 50 else '#ef5350' for v in _sig_df['승률(%)']],
                    text=[f"{v:.0f}%" for v in _sig_df['승률(%)']],
                    textposition='outside', yaxis='y'))
                _fig_sig.add_trace(go.Bar(
                    name='기대값(%)', x=_sig_df['시그널'], y=_sig_df['기대값(%)'],
                    marker_color='rgba(41,98,255,0.6)',
                    text=[f"{v:+.2f}%" for v in _sig_df['기대값(%)']],
                    textposition='outside', yaxis='y2'))
                _fig_sig.update_layout(
                    height=300, barmode='group',
                    plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    font=dict(color=TV_TEXT),
                    yaxis=dict(title='승률(%)', gridcolor=TV_GRID, side='left'),
                    yaxis2=dict(title='기대값(%)', overlaying='y', side='right',
                                showgrid=False),
                    legend=dict(orientation='h', y=1.1),
                    margin=dict(l=0, r=60, t=30, b=0))
                st.plotly_chart(_fig_sig, width='stretch')

            # ── 누적 손익 곡선 ──────────────────────────────────
            if not _closed.empty and '날짜' in _closed.columns:
                try:
                    _cum = _closed.sort_values('날짜').copy()
                    _cum['누적손익($)'] = _cum['손익($)'].cumsum()
                    _fig_cum = go.Figure()
                    _fig_cum.add_trace(go.Scatter(
                        x=_cum['날짜'], y=_cum['누적손익($)'],
                        mode='lines+markers', name='누적 손익',
                        line=dict(color='#2962ff', width=2),
                        fill='tozeroy',
                        fillcolor='rgba(41,98,255,0.08)'))
                    _fig_cum.add_hline(y=0, line_color='#999999', line_dash='dot', line_width=1)
                    _fig_cum.update_layout(
                        height=220, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                        font=dict(color=TV_TEXT),
                        yaxis=dict(title='누적 손익 ($)', gridcolor=TV_GRID),
                        xaxis=dict(gridcolor=TV_GRID),
                        margin=dict(l=0, r=20, t=10, b=0), showlegend=False)
                    st.plotly_chart(_fig_cum, width='stretch')
                except Exception:
                    pass

            # ── 종목별 손익 바 차트 ─────────────────────────────
            if not _closed.empty:
                _fig_j = go.Figure(go.Bar(
                    x=_closed['종목'], y=_closed['손익(%)'],
                    marker_color=['#26a69a' if v > 0 else '#ef5350'
                                  for v in _closed['손익(%)']],
                    text=[f"{v:+.1f}%" for v in _closed['손익(%)']],
                    textposition='outside'))
                _fig_j.update_layout(
                    height=260, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    yaxis_title="손익 (%)", font=dict(color=TV_TEXT),
                    margin=dict(l=0, r=20, t=10, b=0))
                st.plotly_chart(_fig_j, width='stretch')
        else:
            st.info("아직 매매 기록이 없습니다. 위 폼으로 첫 거래를 기록하세요.")
            if not _gs_ok:
                st.caption("💡 세션이 종료되면 기록이 사라집니다. Google Sheets 연동 또는 CSV 백업을 권장합니다.")


if __name__ == "__main__":
    main()
