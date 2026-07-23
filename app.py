import streamlit as st
import streamlit.components.v1 as _st_components
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from typing import Callable, List, NamedTuple, Optional, Union
import requests
import os
import json
import warnings
warnings.filterwarnings('ignore')

# ── 사무실 게임 씬 컴포넌트 (office_game/index.html) ──────────────
# 캔버스 위에서 직원 캐릭터가 실제로 걸어다니고, 클릭하면 (room, emp)를 반환한다.
try:
    _office_game_component = _st_components.declare_component(
        "office_game", path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "office_game"))
    _OFFICE_GAME_AVAILABLE = True
except Exception:
    _OFFICE_GAME_AVAILABLE = False

# ── 퀀트 모듈 ──────────────────────────────────────────────────

try:
    from modules.dart_fundamentals import fetch_krx_fundamentals as _fetch_dart_fundamentals
    _DART_AVAILABLE = True
except Exception:
    _DART_AVAILABLE = False

try:
    from modules.analyst_weights import (
        load_analyst_weights as _load_analyst_weights,
        DIRECTIONAL_ANALYSTS as _DIRECTIONAL_ANALYSTS,
    )
    _ANALYST_WEIGHTS_AVAILABLE = True
except Exception:
    _ANALYST_WEIGHTS_AVAILABLE = False
    _DIRECTIONAL_ANALYSTS = ('차트+파동+모멘텀', '퀀트+재무', 'ICT+CRT')

try:
    from modules.macro_indicators import real_macro_score as _real_macro_score
    _REAL_MACRO_AVAILABLE = True
except Exception:
    _REAL_MACRO_AVAILABLE = False

# 가치·퀄리티 원점수 배합의 단일 진실 공급원. modules/factor_engine.py 도 같은
# 함수를 쓴다 — 계수를 바꾸려면 factor_formulas.py 에서만 바꿀 것.
# 의존성 없는 순수 산술 모듈이라 방어적 import 대상이 아니다 (없으면 즉시 실패해야 한다).
# 신호 판정 규칙의 소유자. app.py 가 import 하는 방향이므로 signal_engine 은
# app 을 import 하지 않는다 — 가격 조회·지표 함수를 주입받는다.
from modules import signal_engine as _signal_engine
# 팩터 점수의 순수 계산부. 여기(app.py)에는 조회 루프만 남긴다.
from modules import factor_scoring as _scoring
# 시장 판별·시장별 벤치마크의 소유자 (국면 지수, 섹터 ETF)
from modules import market_scope as _scope


def _dart_fallback_batch(tickers):
    """KRX(.KS/.KQ) 종목만 골라 DART 재무를 일괄 조회 (yfinance가 KRX 재무를 못 주는 문제 보완).
    DART_API_KEY 미설정·모듈 미탑재 시 빈 dict — 호출부는 전부 yfinance로 폴백된다."""
    if not _DART_AVAILABLE:
        return {}
    krx = [tk for tk in tickers if tk.endswith(('.KS', '.KQ'))]
    if not krx:
        return {}
    try:
        return _fetch_dart_fundamentals(krx)
    except Exception:
        return {}

try:
    from modules.stat_validation import (
        deflated_sharpe_ratio as _dsr,
        block_bootstrap_sharpe_ci as _bb_ci,
        permutation_test_trades as _perm_test,
    )
    _STAT_AVAILABLE = True
except Exception:
    _STAT_AVAILABLE = False

try:
    from modules.ml_signals import (
        train_and_validate_ml_signal as _ml_train,
        predict_current_ml_signal as _ml_predict,
    )
    _ML_AVAILABLE = True
except Exception:
    _ML_AVAILABLE = False

try:
    from modules.risk_management import run_backtest_sized, kelly_fraction
    _RISK_MGMT_ENABLED = True
except Exception:
    _RISK_MGMT_ENABLED = False

try:
    from modules.data_integrity import full_data_integrity_check as _data_integrity_check
    _DATA_INTEGRITY_AVAILABLE = True
except Exception:
    _DATA_INTEGRITY_AVAILABLE = False

try:
    from modules.ops_safety import KillSwitch as _KillSwitch, reconcile_positions as _reconcile_pos
    _OPS_SAFETY_AVAILABLE = True
except Exception:
    _OPS_SAFETY_AVAILABLE = False

try:
    from modules.alpha_decay_monitor import detect_alpha_decay as _detect_alpha_decay, rolling_performance_vs_baseline as _rolling_perf_vs_bt
    _ALPHA_DECAY_AVAILABLE = True
except Exception:
    _ALPHA_DECAY_AVAILABLE = False

try:
    from modules.stress_test import replay_historical_scenario as _replay_scenario, KNOWN_STRESS_PERIODS as _STRESS_PERIODS
    _STRESS_TEST_AVAILABLE = True
except Exception:
    _STRESS_TEST_AVAILABLE = False

try:
    from modules.signal_decay_analysis import compute_signal_ic_decay as _signal_ic_decay
    _SIGNAL_DECAY_AVAILABLE = True
except Exception:
    _SIGNAL_DECAY_AVAILABLE = False

try:
    from modules.factor_risk_model import regression_style_analysis as _style_analysis, rolling_market_beta as _rolling_beta
    _FACTOR_RISK_AVAILABLE = True
except Exception:
    _FACTOR_RISK_AVAILABLE = False



st.set_page_config(page_title="퀀트 트레이딩 시스템", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

_dark_mode = st.session_state.get('ui_dark_mode', False)

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
  padding: 3.5rem 2rem 3rem !important;
  max-width: 1600px;
}
h1 { font-weight: 800 !important; letter-spacing: -0.8px; font-size: 1.75rem !important; color: var(--text-1); }
h2 { font-weight: 700 !important; letter-spacing: -0.4px; font-size: 1.25rem !important; color: var(--text-1); }
h3 { font-weight: 600 !important; letter-spacing: -0.2px; font-size: 1.05rem !important; color: var(--text-2); }
p  { color: var(--text-2); }

/* ═══════════════════════════════════════════
   TABS — 메인 (pill/segment)
═══════════════════════════════════════════ */
.stTabs [role="tablist"] {
  gap: 2px;
  background: var(--surface2);
  padding: 3px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  border-bottom: 1px solid var(--border) !important;
}
.stTabs [data-testid="stTab"] {
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
.stTabs [data-testid="stTabPanel"] {
  padding-top: 1.25rem !important;
}

/* ── 서브탭 (underline style) ── */
.stTabs .stTabs [role="tablist"] {
  background: transparent;
  padding: 0;
  border: none !important;
  border-bottom: 2px solid var(--border) !important;
  border-radius: 0;
  gap: 0;
}
.stTabs .stTabs [data-testid="stTab"] {
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
.stTabs .stTabs [data-testid="stTabPanel"] {
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
  background: var(--surface) !important;
  margin-bottom: 8px !important;
  overflow: hidden;
}
details[open] { box-shadow: var(--shadow-sm); }
summary {
  font-weight: 600 !important;
  font-size: 14px !important;
  color: var(--text-2) !important;
  background: var(--surface) !important;
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
  .block-container { padding: 3.25rem 1rem 2rem !important; }
  h1 { font-size: 1.35rem !important; }
  h2 { font-size: 1.05rem !important; }
  .stTabs [data-testid="stTab"] { padding: 6px 10px !important; font-size: 12px !important; }
  [data-testid="metric-container"] { padding: 10px 12px !important; }
  [data-testid="metric-container"] [data-testid="stMetricValue"] { font-size: 18px !important; }

  /* 서브탭(퀀트 10개 등)이 화면 너비를 넘길 때 스와이프 가능함을 암시하는 우측 페이드 */
  .stTabs .stTabs [role="tablist"] {
    mask-image: linear-gradient(to right, black calc(100% - 28px), transparent 100%);
    -webkit-mask-image: linear-gradient(to right, black calc(100% - 28px), transparent 100%);
  }
}

/* ── 스크롤바 ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--surface2); }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-4); }
</style>""", unsafe_allow_html=True)

if _dark_mode:
    st.markdown("""<style>
:root {
  --bg:        #0b1220;
  --surface:   #111827;
  --surface2:  #1a2233;
  --border:    #232b3d;
  --border2:   #334155;

  --text-1:    #e5e7eb;
  --text-2:    #cbd5e1;
  --text-3:    #94a3b8;
  --text-4:    #64748b;

  --green-bg:  rgba(16,185,129,.14);
  --red-bg:    rgba(239,68,68,.14);
  --amber-bg:  rgba(245,158,11,.14);
  --blue-bg:   rgba(59,130,246,.14);
}
.stApp { background: var(--bg) !important; }
</style>""", unsafe_allow_html=True)

    TV_BG = TV_PAPER = '#131722'
    TV_GRID = '#2a2e39'
    TV_BORDER = '#363c4e'
    TV_TEXT = '#d1d4dc'
else:
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
    """애널리스트 추천·공매도 비율·EPS 서프라이즈 팩터 수집.

    스케일 변환 규칙은 factor_scoring 소유. 여기서는 조회만 한다.
    """
    out = {}
    # ① 애널리스트 추천, ② 공매도 비율 — 둘 다 info 에 이미 들어 있다
    for key, score in (('analyst_raw', _scoring.analyst_score(info.get('recommendationMean'))),
                       ('short_raw', _scoring.short_ratio_score(info.get('shortRatio')))):
        if score is not None:
            out[key] = score
    # ③ EPS 서프라이즈 — 별도 조회가 필요하다
    try:
        surprise = _scoring.eps_surprise_score(yf.Ticker(ticker).earnings_history)
        if surprise is not None:
            out['eps_surprise_raw'] = surprise
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


# 섹터 ETF 맵의 소유자는 market_scope (미국·한국 양쪽). UI 호환용 별칭.
SECTOR_ETF = _scope.SECTOR_ETF[_scope.US]

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
    fast_k = (close - lo) / (hi - lo + 1e-9) * 100
    slow_k = fast_k.rolling(d).mean()   # Slow %K = 3-period SMA of fast %K
    slow_d = slow_k.rolling(d).mean()   # Slow %D = 3-period SMA of Slow %K
    return slow_k, slow_d


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
    # Wilder's smoothed MA (EWM alpha=1/period) — SMA gives wrong ADX values
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    pdi = pdm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9) * 100
    ndi = ndm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9) * 100
    dx  = (pdi - ndi).abs() / (pdi + ndi + 1e-9) * 100
    return dx.ewm(alpha=1/period, adjust=False).mean(), pdi, ndi


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

    # ── RSI (동적 임계값: 추세장에서 방향별 완화) ─
    bull_trend_sig = t_det.get('bull_trend', True)
    rsi_ob = 65 if (has_trend and bull_trend_sig and adx_val > 30) else 70
    rsi_os = 35 if (has_trend and not bull_trend_sig and adx_val > 30) else 30
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
        conf = ('🟢', f'매수 우세 ({bull}:{bear})', '매수 신호 우세 — 보통 신뢰도')
    elif net <= -3:
        conf = ('🔴', f'강한 매도 합류 ({bear}:{bull})', f'{bear}개 매도 신호 동시 발생 — 높은 신뢰도')
    elif net <= -2:
        conf = ('🔴', f'매도 우세 ({bear}:{bull})', '매도 신호 우세 — 보통 신뢰도')
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
    det['bull_trend'] = bool(bull_trend)

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
    if rv < rsi_os:              rsi = 70   # 과매도 → 반등 매수 신호
    elif rv < rsi_os + 10:      rsi = 60   # 과매도 회복 구간
    elif rv <= 60:               rsi = 50   # 중립
    elif rv <= rsi_ob:           rsi = 65   # 모멘텀 지속 구간
    else:                        rsi = 35   # 과매수 → 조정 신호
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
        if bw_now < bw_avg * 0.7 and bull_trend: bb = min(bb+10, 100)   # 스퀴즈 + 상승추세 → 상향 돌파 기대
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
    elif adx > 20: adx_v = 58 if bull_trend else 42  # has_trend 기준과 일치
    else:          adx_v = 50
    det['ADX추세강도'] = float(adx_v)
    det['ADX값'] = round(adx, 1)

    # ── OBV (온밸런스볼륨) (10%) ───────────────
    obv = calc_obv(p, v)
    obv_ma = obv.rolling(20).mean()
    obv_above = float(obv.iloc[-1]) > float(obv_ma.iloc[-1])
    obv_rising = (float(obv.iloc[-1]) > float(obv.iloc[-5])) if len(obv) >= 5 else True
    if obv_above and obv_rising:      obv_v = 75  # OBV MA 위 + 상승 중
    elif obv_above and not obv_rising: obv_v = 55  # OBV MA 위 + 하락 전환
    elif not obv_above and obv_rising: obv_v = 40  # OBV MA 아래지만 회복 중
    else:                              obv_v = 25  # OBV MA 아래 + 하락 중
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

def _score_roa(v):
    """ROA 전용 스코어 (yfinance 소수 반환 → % 변환 후 적용)"""
    if v is None or np.isnan(v): return 50
    r = v * 100 if abs(v) <= 1 else v   # 소수 → %
    if r < 0:    return 10
    elif r < 2:  return 30
    elif r < 5:  return 50
    elif r < 10: return 70
    elif r < 15: return 85
    else:        return 95

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
        # DART 폴백 (KRX 종목은 yfinance에 ROE/순이익률이 비어있는 경우가 많음)
        if ticker.endswith(('.KS', '.KQ')) and (roe is None or pm is None):
            _dd = _dart_fallback_batch([ticker]).get(ticker, {})
            if roe is None and _dd.get('net_income') and _dd.get('equity'):
                try:
                    roe = _dd['net_income'] / _dd['equity']
                except ZeroDivisionError:
                    pass
            if pm is None and _dd.get('margin') is not None:
                pm = _dd['margin'] / 100  # DART는 영업이익률 — 순이익률 미제공이라 근사치로만 사용
        pm_s = 50
        if pm is not None:   # 0.0도 의미 있는 값 — if pm: 쓰면 0% 기업이 중립 처리됨
            pp = pm*100
            pm_s = 10 if pp<0 else (40 if pp<5 else (60 if pp<10 else (80 if pp<20 else 90)))
        det['수익성'] = _score_roe(roe)*0.4 + _score_roa(roa)*0.3 + pm_s*0.3
        det['ROE'] = roe; det['ROA'] = roa; det['순이익률'] = pm

        # ── 성장성 (13%) ──────────────────────────────
        rg = info.get('revenueGrowth'); eg = info.get('earningsGrowth')
        det['성장성'] = _score_growth(rg)*0.5 + _score_growth(eg)*0.5 if eg is not None else _score_growth(rg)
        det['매출성장'] = rg; det['EPS성장'] = eg

        # ── FCF 품질 (12%): FCF수익률 ─────────────────
        fcf  = info.get('freeCashflow')
        mcap = info.get('marketCap')
        fcf_yield = (fcf / mcap * 100) if (fcf is not None and mcap and mcap > 0) else None
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
def get_market_regime(benchmark=None):
    """벤치마크 지수 vs MA200 기반 시장 국면 감지 (bull/bear/neutral).

    benchmark 를 안 주면 SPY — 미국 유니버스용 기존 동작이다. 한국 유니버스는
    ^KS11(KOSPI)을 넘겨야 한다. 안 그러면 미국 시장이 강세라는 이유로 한국
    종목의 팩터 가중치가 정해진다.
    """
    benchmark = benchmark or _scope.REGIME_BENCHMARK[_scope.US]
    try:
        end = datetime.now(); start = end - timedelta(days=310)
        spy = yf.download(benchmark, start=start, end=end, progress=False)
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
        irx = yf.download('^IRX',     start=start, end=end, progress=False)  # 3개월 T-bill (10Y-3M 스프레드)
        vix = yf.download('^VIX',     start=start, end=end, progress=False)
        dxy = yf.download('DX-Y.NYB', start=start, end=end, progress=False)
        hyg = yf.download('HYG',      start=start, end=end, progress=False)  # 하이일드 채권
        lqd = yf.download('LQD',      start=start, end=end, progress=False)  # 투자등급 채권
        gld = yf.download('GLD',      start=start, end=end, progress=False)  # 금(인플레/공포)
        for d in [tnx, irx, vix, dxy, hyg, lqd, gld]:
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

        # ── 장단기금리차 (20%) — 10Y-3M (Fed 선호 경기침체 지표) ──
        if len(tnx) > 0 and len(irx) > 0:
            # ^IRX = 13주 T-bill (연율); 10Y-3M 스프레드 — 임계값을 10Y-3M 정상 범위(-2.5~+3%)로 재보정
            sp = float(tnx['Close'].iloc[-1]) - float(irx['Close'].iloc[-1]); data['장단기스프레드(10Y-3M)'] = sp
            det['장단기금리차'] = 80 if sp>2.0 else (65 if sp>0.5 else (50 if sp>=0 else (35 if sp>-1.0 else 20)))
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


@st.cache_data(ttl=3600)
def geopolitical_risk_score():
    """지정학 안정도 점수 (시장가격 프록시, 0~100 — 높을수록 안정/저위험).

    macro_score와 같은 프록시 방식이지만 (1) 지정학 충격은 급성이라 1개월 창을
    쓰고 (2) 유가·방산주·유가변동성 등 macro_score가 안 보는 자산에 집중한다.
    실물 뉴스/사건이 아니라 시장이 반영한 지정학 스트레스의 간접 측정이며,
    데이터 결측 시 항목별 50(중립)으로 graceful degradation.
    """
    det, data = {}, {}
    end = datetime.now(); start = end - timedelta(days=120)
    try:
        oil = yf.download('CL=F',  start=start, end=end, progress=False)  # WTI 원유 (공급 충격)
        gold = yf.download('GC=F', start=start, end=end, progress=False)  # 금 (안전자산 도피)
        vix = yf.download('^VIX',  start=start, end=end, progress=False)  # 공포지수
        ita = yf.download('ITA',   start=start, end=end, progress=False)  # 방산 ETF
        spy = yf.download('SPY',   start=start, end=end, progress=False)  # 상대강도 기준
        ovx = yf.download('^OVX',  start=start, end=end, progress=False)  # 원유 변동성지수
        for d in [oil, gold, vix, ita, spy, ovx]:
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.droplevel(1)
            d.dropna(subset=['Close'], inplace=True)

        def _mom_1m(s):
            return (float(s['Close'].iloc[-1]) - float(s['Close'].iloc[-21])) / float(s['Close'].iloc[-21]) * 100

        # ── 유가 급등 (25%) — 공급 충격/분쟁 시 급등 → 위험 ──
        if len(oil) >= 21:
            oc = _mom_1m(oil); data['유가변화(1M)'] = round(oc, 1)
            det['유가'] = 80 if oc < -5 else (65 if oc < 0 else (50 if oc < 8 else (32 if oc < 18 else 18)))
        else: det['유가'] = 50.0

        # ── 금 안전자산 수요 (20%) — 급등 = 도피 심리 → 위험 ──
        if len(gold) >= 21:
            gc = _mom_1m(gold); data['금변화(1M)'] = round(gc, 1)
            det['금'] = 72 if gc < 0 else (60 if gc < 4 else (48 if gc < 8 else (32 if gc < 15 else 20)))
        else: det['금'] = 50.0

        # ── VIX 절대수준 (20%) ──
        if len(vix) >= 1:
            cv = float(vix['Close'].iloc[-1]); data['VIX'] = round(cv, 1)
            det['VIX'] = 80 if cv < 15 else (68 if cv < 20 else (52 if cv < 27 else (34 if cv < 38 else 18)))
        else: det['VIX'] = 50.0

        # ── 방산주 상대강도 (15%) — 방산이 시장 초과 = 분쟁 프라이싱 → 위험 ──
        if len(ita) >= 21 and len(spy) >= 21:
            rs = _mom_1m(ita) - _mom_1m(spy); data['방산상대강도(1M)'] = round(rs, 1)
            det['방산상대강도'] = 70 if rs < -2 else (58 if rs < 1 else (45 if rs < 4 else (32 if rs < 8 else 20)))
        else: det['방산상대강도'] = 50.0

        # ── 원유 변동성 OVX (10%) — 에너지 불확실성 ──
        if len(ovx) >= 1:
            co = float(ovx['Close'].iloc[-1]); data['OVX'] = round(co, 1)
            det['원유변동성'] = 75 if co < 30 else (60 if co < 40 else (45 if co < 55 else 28))
        else: det['원유변동성'] = 50.0

        # ── 금 변동성 대용 — 위 항목으로 충분, 잔여 10%는 VIX 급변으로 흡수 ──
        if len(vix) >= 6:
            v_now = float(vix['Close'].iloc[-1]); v_5 = float(vix['Close'].iloc[-6])
            v_spike = (v_now - v_5) / (v_5 + 1e-9) * 100; data['VIX급변(5D)'] = round(v_spike, 1)
            det['VIX급변'] = 70 if v_spike < -10 else (55 if v_spike < 10 else (38 if v_spike < 30 else 20))
        else: det['VIX급변'] = 50.0

        # 가중치: 유가25 금20 VIX20 방산15 원유변동성10 VIX급변10
        total = (det['유가']*0.25 + det['금']*0.20 + det['VIX']*0.20 +
                 det['방산상대강도']*0.15 + det['원유변동성']*0.10 + det['VIX급변']*0.10)
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
    bull_bt = (pdi_bt > ndi_bt).fillna(False)  # 방향 벡터 — RSI/BB/ADX 섹션에서 공유 사용

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

    # ── RSI (10%) — 동적 임계값 (technical_score와 동일 로직 벡터화) ──
    rsi = calc_rsi(p)
    # 강한 방향성 추세에서 임계값 동적 조정 (ADX>30 조건)
    rsi_os_bt = pd.Series(30.0, index=p.index)
    rsi_os_bt[(~bull_bt) & (adx_f_bt > 30)] = 35.0  # 강한 하락추세 → 과매도 임계 상향
    rsi_ob_bt = pd.Series(70.0, index=p.index)
    rsi_ob_bt[bull_bt & (adx_f_bt > 30)]    = 65.0  # 강한 상승추세 → 과매수 임계 하향
    rs  = pd.Series(50.0, index=p.index)
    rs[rsi < rsi_os_bt]                              = 70  # 과매도 → 반등 신호
    rs[(rsi >= rsi_os_bt) & (rsi < rsi_os_bt + 10)] = 60  # 과매도 회복 구간
    rs[(rsi > 60) & (rsi <= rsi_ob_bt)]              = 65  # 모멘텀 구간
    rs[rsi > rsi_ob_bt]                              = 35  # 과매수 → 조정 신호
    rs_b = pd.Series(0.0, index=p.index)
    rsi_tr = rsi - rsi.shift(10)
    rs_b[(rsi_tr > 5) & (rsi > rsi_os_bt) & (rsi < rsi_ob_bt)] = 20
    rs_b[rsi_tr < -5]                                            = -10
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
    bs_b[(bw_now < bw_avg * 0.7) & bull_bt] = 10  # 스퀴즈 + 상승추세일 때만 보너스
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

    # ── ADX (17%) ── 선행 계산 값 재사용 (중복 호출 제거) ──
    adx_f = adx_f_bt
    bull  = bull_bt
    ads   = pd.Series(50.0, index=p.index)
    ads[(adx_f > 40) &  bull]                  = 85
    ads[(adx_f > 40) & ~bull]                  = 15
    ads[(adx_f > 25) & (adx_f <= 40) &  bull]  = 72
    ads[(adx_f > 25) & (adx_f <= 40) & ~bull]  = 28
    ads[(adx_f > 20) & (adx_f <= 25) &  bull]  = 58  # has_trend 기준 20과 통일
    ads[(adx_f > 20) & (adx_f <= 25) & ~bull]  = 42

    # ── OBV (10%) ──────────────────────────────
    obv    = calc_obv(p, v)
    obv_ma = obv.rolling(20).mean()
    obv_above = (obv > obv_ma); obv_rising = (obv > obv.shift(5)).fillna(False)
    obv_s = pd.Series(25.0, index=p.index)
    obv_s[obv_above  & obv_rising]  = 75
    obv_s[obv_above  & ~obv_rising] = 55
    obv_s[~obv_above & obv_rising]  = 40
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
    sharpe    = float((daily_ret.mean() - 0.045/252) / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
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
    """종목 vs 섹터 ETF vs 시장지수 상대 강도 분석.

    지수와 섹터 ETF 는 종목이 속한 시장 것을 쓴다 — 한국 종목을 SPY·XLK 와
    비교하던 것이 원래 동작이었다. spy_* 키 이름은 호출부 호환을 위해
    유지하되, 실제 기준은 반환 dict 의 'benchmark' 가 알려준다.
    """
    etf = _scope.sector_etf_for_ticker(sector, ticker)
    market_index = _scope.REGIME_BENCHMARK[_scope.market_of_ticker(ticker)]
    end = datetime.now()
    start = end - timedelta(days=200)
    tickers_to_dl = [market_index] + ([etf] if etf else [])

    try:
        bench = yf.download(tickers_to_dl, start=start, end=end, progress=False)
        if isinstance(bench.columns, pd.MultiIndex):
            spy_close = bench['Close'][market_index].dropna()
            etf_close = bench['Close'][etf].dropna() if etf else None
        else:
            spy_close = bench['Close'].dropna()
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
    # benchmark 를 함께 돌려준다 — UI 가 'SPY 대비' 라고 못 박으면 안 된다.
    return {'data': results, 'etf': etf, 'sector': sector,
            'benchmark': market_index}


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
    rs[rsi<30]=70; rs[(rsi>=30)&(rsi<40)]=60; rs[(rsi>60)&(rsi<=70)]=65; rs[rsi>70]=35

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
    ads[(adx_f>20)&(adx_f<=25)&bull]=58; ads[(adx_f>20)&(adx_f<=25)&~bull]=42

    obv=calc_obv(p,v); obv_ma=obv.rolling(20).mean()
    obv_above=(obv>obv_ma); obv_rising=(obv>obv.shift(5)).fillna(False)
    obv_s=pd.Series(25.0,index=p.index)
    obv_s[obv_above & obv_rising]=75
    obv_s[obv_above & ~obv_rising]=55
    obv_s[~obv_above & obv_rising]=40
    # OBV 다이버전스 보너스 (bt_signals_full/technical_score와 동기화)
    pr20o=p.shift(20); obv20=obv.shift(20)
    obv_b=pd.Series(0.0,index=p.index)
    obv_b[(p<pr20o)&(obv>obv20)]+=20   # 강세 다이버전스: 가격↓ OBV↑
    obv_b[(p>pr20o)&(obv<obv20)]-=20   # 약세 다이버전스: 가격↑ OBV↓
    obv_s=(obv_s+obv_b).clip(0,100)

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
        eps  = info.get('trailingEps') or info.get('forwardEps')
        if not eps or eps <= 0: return None, {}
        g_raw = info.get('earningsGrowth')
        if g_raw is None: g_raw = 0.07  # revenueGrowth는 EPS 성장과 다르므로 사용 안 함; 보수적 기본값 7%
        # yfinance는 소수 반환(0.15=15%); 단 초고성장(>100%)은 소수로 >1.0 가능 — 클램핑이 처리
        g = float(g_raw) * 100 if abs(float(g_raw)) < 10 else float(g_raw)
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
        end = datetime.now(); start = end - timedelta(days=756)  # 3년 → Beta 더 안정적
        sdf   = download_stock(ticker, start=start, end=end)
        spydf = download_stock('SPY', start=start, end=end)
        sr = sdf['Close'].pct_change().dropna()
        mr = spydf['Close'].pct_change().dropna()
        idx = sr.index.intersection(mr.index)
        if len(idx) < 60: return {}
        sr = sr.loc[idx]; mr = mr.loc[idx]
        # 현재 ^IRX(3M T-bill) 기반 무위험수익률
        try:
            _irx = yf.download('^IRX', period='5d', progress=False)
            if isinstance(_irx.columns, pd.MultiIndex): _irx.columns = _irx.columns.droplevel(1)
            _irx_val = _irx['Close'].dropna()
            rf_annual = float(_irx_val.iloc[-1]) / 100 if not _irx_val.empty else 0.045
        except Exception:
            rf_annual = 0.045
        rf_label = f"{rf_annual*100:.1f}%"
        beta   = np.cov(sr.values, mr.values)[0][1] / (np.var(mr.values) + 1e-12)
        var95  = float(np.percentile(sr.values, 5))  * 100
        var99  = float(np.percentile(sr.values, 1))  * 100
        thresh = np.percentile(sr.values, 5)
        cvar95 = float(sr[sr <= thresh].mean()) * 100 if (sr <= thresh).any() else var95
        vol    = float(sr.std()) * np.sqrt(252) * 100
        daily_rf = rf_annual / 252
        sharpe = float((sr.mean() - daily_rf) / sr.std() * np.sqrt(252)) if sr.std() > 0 else 0
        return {
            'Beta': round(beta, 2),
            'VaR 95% (1일)': f"{var95:.2f}%",
            'VaR 99% (1일)': f"{var99:.2f}%",
            'CVaR 95%': f"{cvar95:.2f}%",
            '연간 변동성': f"{vol:.1f}%",
            f'Sharpe (RF {rf_label})': round(sharpe, 2),
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

# 정규화 규칙은 factor_scoring 소유. calc_factor_scores_sectoral 도 이 이름을 쓴다.
_zscore_to_score = _scoring.zscore_to_score

def _pick_4f_weights(ic_data, regime):
    """ic_weights.json 내용에서 이 스캔의 4팩터 가중치를 고른다 (순수 함수).

    IO 와 분리해 둔 이유는 선택 규칙 자체를 테스트로 잠그기 위해서다 —
    어느 블록을 읽느냐가 실전 포트폴리오를 바꾼다.
    """
    pw = (ic_data.get('production_weights') or {}).get(regime, {})
    if pw:
        total_pw = sum(pw.values())
        if total_pw >= 0.01:
            return {k: v / total_pw for k, v in pw.items()}

    rw = ic_data.get('regime_weights', {}).get(regime, {})
    if not rw:
        return None
    momentum = rw.get('mom_3m', 0) + rw.get('mom_1m', 0)
    value    = rw.get('value',   0)
    quality  = rw.get('quality', 0)
    low_vol  = rw.get('low_vol', 0)
    total_4f = momentum + value + quality + low_vol
    if total_4f < 0.01:
        return None
    return {k: v / total_4f for k, v in
            [('momentum', momentum), ('value', value), ('quality', quality), ('low_vol', low_vol)]}


def _load_ic_factor_weights_4f(regime=None, benchmark=None):
    """ic_weights.json에서 이 스캔의 4팩터(momentum/value/quality/low_vol) 가중치를 읽는다.

    `production_weights` 를 먼저 본다. 이 스캔은 12-1 모멘텀(252→21봉)과
    252봉 변동성으로 랭킹하는데, 그 예측력으로 배분된 가중치가 거기 들어 있다.

    없으면 예전처럼 6팩터 `regime_weights` 를 접어서 쓴다. 그 경로는
    momentum 을 mom_3m + mom_1m 으로 잡는데, **이 스캔이 계산조차 하지 않는
    팩터들이다.** ic_weight_updater 를 아직 새로 돌리지 않았을 때를 위한
    후퇴 경로일 뿐이니 여기에 기대지 말 것.
    """
    try:
        import json as _json, os as _os
        ic_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'ic_weights.json')
        with open(ic_path, encoding='utf-8') as _f:
            _d = _json.load(_f)
        if regime is None:
            regime, _ = get_market_regime(benchmark)

        return _pick_4f_weights(_d, regime)
    except Exception:
        return None


def calc_factor_scores(tickers, prog_bar=None, prog_text=None,
                       extra_factors=False, min_avg_volume=0, factor_weights=None):
    """멀티팩터 랭킹: 4팩터 + 선택적 3추가팩터(애널·공매도·EPS서프라이즈).

    점수 공식·정규화·가중 합성은 modules/factor_scoring.py 가 소유한다.
    여기 남은 것은 조회 루프뿐이다 — 종목별 다운로드, yfinance 재무 조회,
    레이트리밋 슬립, 진행률 표시.

    signal_worker.py 가 `core.calc_factor_scores(...)` 로 부르는 진입점이라
    시그니처와 반환 스키마는 그대로 유지한다.
    """
    import time
    end = datetime.now(); start = end - timedelta(days=520)
    results = []
    failed = []
    _dart_data = _dart_fallback_batch(tickers)
    for i, tk in enumerate(tickers):
        if prog_text: prog_text.text(f"팩터 분석: {tk} ({i+1}/{len(tickers)})")
        if prog_bar: prog_bar.progress((i+1)/len(tickers))
        try:
            df = _scoring.clean_price_frame(download_stock(tk, start=start, end=end),
                                            min_avg_volume=min_avg_volume)
            if df is None:
                failed.append(tk); continue

            # 재무 조회는 실패해도 스캔을 멈추지 않는다 — 가격 팩터는 살아 있다.
            try:
                info = yf.Ticker(tk).info or {}
            except Exception:
                info = {}

            row = {'ticker': tk}
            row.update(_scoring.price_factors(df))
            row.update(_scoring.fundamental_factors(tk, info, _dart_data.get(tk)))
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
    # IC 가중치는 명시 가중치가 있어도 읽는다. 팩터 타이밍(시장 환경)과
    # IC(팩터 예측력)는 직교하는 신호라 둘을 섞는다 — blend_ic_weights 참고.
    rdf = _scoring.rank_by_composite(
        results,
        factor_weights=factor_weights,
        ic_weights=_load_ic_factor_weights_4f(
            benchmark=_scope.regime_benchmark(tickers)),
    )
    if failed:
        rdf.attrs['failed'] = failed
    return rdf


def generate_system_signals(tickers, factor_df=None, weights=None, top_n=5, capital=10000):
    """시스템 트레이딩 엔진: 규칙 기반 매수/매도/리밸런싱 시그널.

    판정 로직은 modules/signal_engine.py 가 소유한다. 여기서는 app.py 쪽
    가격 조회·지표 함수를 주입해 넘기기만 한다 — signal_worker.py 가
    `core.generate_system_signals(...)` 로 부르는 진입점이라 시그니처는 유지한다.

    download_stock 을 lambda 로 감싸는 이유: 이름을 호출 시점에 app 모듈
    전역에서 다시 찾게 해, 테스트가 app.download_stock 을 monkeypatch 하면
    그대로 반영되게 하려는 것이다.
    """
    return _signal_engine.generate_system_signals(
        tickers,
        fetch_prices=lambda tk, start, end: download_stock(tk, start=start, end=end),
        calc_rsi=calc_rsi,
        calc_momentum=calc_momentum,
        calc_adx=calc_adx,
        factor_df=factor_df,
        weights=weights,
        top_n=top_n,
        capital=capital,
    )


UNIVERSE_PRESETS = {
    # ── 기존 프리셋 ───────────────────────────────────────────────────────────
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

    # ── S&P 500 전체 (시가총액 상위 기준) ────────────────────────────────────
    'S&P 500 전체 (500종목)': [
        # 정보기술
        'AAPL','MSFT','NVDA','AVGO','ORCL','CRM','ADBE','INTU','AMD','QCOM',
        'TXN','AMAT','LRCX','KLAC','MU','SNPS','CDNS','FTNT','ANSS','EPAM',
        'HPQ','HPE','NTAP','WDC','STX','JNPR','KEYS','TRMB','CTSH','ACN',
        'IBM','IT','FFIV','PAYC','GDDY','GPN','FIS','FISV','PAYX','ADP',
        # 커뮤니케이션
        'GOOGL','META','NFLX','DIS','CMCSA','T','VZ','TMUS','CHTR','EA',
        'ATVI','TTWO','OMC','IPG','LYV','WBD','FOX','NWSA','PARA','MTCH',
        # 임의소비재
        'AMZN','TSLA','HD','MCD','NKE','LOW','SBUX','TJX','BKNG','MAR',
        'HLT','YUM','ORLY','AZO','ULTA','ROST','DG','DLTR','BBY','KMX',
        'PHM','DHI','LEN','NVR','TOL','F','GM','APTV','BWA','LEA',
        # 필수소비재
        'WMT','COST','PG','KO','PEP','PM','MO','MDLZ','CL','KMB',
        'GIS','K','SJM','CAG','HRL','MKC','CHD','CLX','EL','COTY',
        # 헬스케어
        'LLY','UNH','JNJ','ABBV','MRK','TMO','ABT','DHR','BMY','AMGN',
        'GILD','VRTX','REGN','BIIB','ILMN','IQV','IDXX','WAT','MTD','A',
        'HCA','CI','ELV','HUM','CNC','MOH','DVA','HSIC','ZBH','STE',
        # 금융
        'BRK-B','JPM','BAC','WFC','GS','MS','BLK','AXP','C','USB',
        'TFC','PNC','SCHW','COF','MTB','FITB','HBAN','RF','CFG','KEY',
        'CB','MMC','AON','MET','PRU','AFL','AIG','PGR','TRV','ALL',
        'BX','KKR','APO','CG','ARES','TROW','IVZ','BEN','AMG','NTRS',
        # 산업재
        'GE','HON','CAT','UPS','BA','RTX','LMT','NOC','GD','TDG',
        'ITW','EMR','ETN','PH','ROK','DOV','IR','XYL','GNRC','FBHS',
        'UNP','CSX','NSC','KSU','WAB','FDX','CHRW','EXPD','GWW','FAST',
        # 에너지
        'XOM','CVX','COP','EOG','SLB','MPC','PSX','VLO','PXD','DVN',
        'HAL','BKR','FANG','OXY','APA','MRO','HES','CTRA','EQT','RRC',
        # 소재
        'LIN','APD','SHW','ECL','PPG','NEM','FCX','NUE','STLD','RS',
        'ALB','MOS','CF','FMC','IFF','RPM','SON','SEE','PKG','IP',
        # 유틸리티
        'NEE','DUK','SO','D','AEP','EXC','SRE','PEG','XEL','ED',
        'ETR','FE','EIX','WEC','ES','AWK','CMS','NI','LNT','EVRG',
        # 부동산
        'AMT','PLD','CCI','EQIX','PSA','O','WELL','SPG','DLR','EQR',
        'AVB','MAA','UDR','CPT','ESS','HST','REG','FRT','BXP','VTR',
    ],

    # ── 섹터별 대표주 ─────────────────────────────────────────────────────────
    '헬스케어 15': ['LLY','UNH','JNJ','ABBV','MRK','TMO','ABT','DHR','BMY','AMGN',
                   'GILD','VRTX','REGN','HCA','CI'],
    '금융 15': ['JPM','BAC','WFC','GS','MS','BLK','AXP','C','COF','PGR',
               'CB','MMC','BX','KKR','SCHW'],
    '에너지 15': ['XOM','CVX','COP','EOG','SLB','MPC','PSX','VLO','PXD','DVN',
                 'HAL','OXY','BKR','HES','FANG'],
    '소비재·유통 15': ['AMZN','HD','MCD','NKE','LOW','SBUX','TJX','BKNG','COST','WMT',
                      'ORLY','AZO','ROST','DG','DLTR'],
    '산업재 15': ['GE','HON','CAT','UPS','RTX','LMT','ITW','EMR','ETN','PH',
                 'UNP','CSX','FDX','NOC','GD'],
    '리츠·부동산 12': ['AMT','PLD','CCI','EQIX','PSA','O','WELL','SPG','DLR','EQR',
                      'AVB','VTR'],

    # ── 성장주 테마 ───────────────────────────────────────────────────────────
    'AI·클라우드 20': ['NVDA','MSFT','GOOGL','META','AMZN','ORCL','CRM','SNPS','CDNS','PLTR',
                      'AI','SNOW','DDOG','MDB','NET','CFLT','ZS','CRWD','S','GTLB'],
    'EV·자율주행 15': ['TSLA','RIVN','LCID','FSR','NIO','LI','XPEV','GM','F','APTV',
                      'ON','MCHP','TI','NXPI','MPWR'],
    '바이오테크 15': ['MRNA','BNTX','REGN','VRTX','BIIB','ILMN','GILD','AMGN','SGEN','ALNY',
                     'BMRN','RARE','IONS','EXAS','FATE'],
    '핀테크·결제 12': ['V','MA','PYPL','SQ','AFRM','SOFI','NU','UPST','LC','BILL',
                      'FIS','FISV'],
    '사이버보안 12': ['CRWD','ZS','PANW','FTNT','S','CYBR','OKTA','TENB','RPD','QLYS',
                     'VRNS','SAIL'],
}

# 'S&P 500 전체' 프리셋은 백엔드(modules/universe.SP500)를 단일 출처로 삼는다.
# 그동안 이 하드코딩 리스트가 백엔드의 생존편향 정리(상폐·피인수 종목 제거)를
# 반영하지 못해 ATVI/PXD/KSU/MRO/HES 등 부도·피인수 종목이 UI에만 남아 있었다.
# 백엔드를 참조하면 ic_weight_updater가 쓰는 유니버스와 영구히 일치한다.
try:
    from modules.universe import SP500 as _BACKEND_SP500
    if _BACKEND_SP500:
        UNIVERSE_PRESETS['S&P 500 전체 (500종목)'] = list(_BACKEND_SP500)
except Exception:
    pass


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
        # 고변동성: 저변동성 보호 유지 (IC 음수여도 하락장 방어 역할)
        w = {'momentum': 0.15, 'value': 0.25, 'quality': 0.35, 'low_vol': 0.25}
        regime = '고변동성 — 퀄리티·저변동 강조'
    elif vix < 15:
        # P1-B: low_vol ICIR=-0.199 반영, 모멘텀 강화
        w = {'momentum': 0.45, 'value': 0.22, 'quality': 0.25, 'low_vol': 0.08}
        regime = '저변동성 — 모멘텀 강조'
    else:
        # P1-B: 보통 국면도 low_vol 축소
        w = {'momentum': 0.35, 'value': 0.25, 'quality': 0.32, 'low_vol': 0.08}
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
    w = {k: v / total for k, v in w.items()}
    # 3자리 반올림 후 합계 오차를 가장 큰 항목에 보정 (round 2에서 합≠1.0 방지)
    w = {k: round(v, 3) for k, v in w.items()}
    _diff = round(1.0 - sum(w.values()), 3)
    if _diff:
        w[max(w, key=w.get)] = round(w[max(w, key=w.get)] + _diff, 3)
    env = {'vix': round(vix, 1), 'vix_avg': round(vix_avg, 1),
           'rate': round(rate, 2), 'rate_chg': round(rate_chg, 2), 'regime': regime}
    return w, env


def calc_factor_scores_sectoral(tickers, factor_weights=None, prog_bar=None, prog_text=None):
    """섹터 중립 멀티팩터 랭킹. 섹터 내 Z-score 정규화로 섹터 편향 제거.

    점수 공식·정규화·합성은 modules/factor_scoring.py 가 소유한다.
    여기 남은 것은 조회 루프뿐 — calc_factor_scores 와 같은 구조다.

    signal_worker.py 가 SECTOR_NEUTRAL 기본값(true)으로 부르는 진입점이라
    시그니처와 반환 스키마는 그대로 유지한다.
    """
    import time
    end = datetime.now(); start = end - timedelta(days=520)
    results = []
    failed = []
    _dart_data = _dart_fallback_batch(tickers)
    for i, tk in enumerate(tickers):
        if prog_text: prog_text.text(f"팩터 분석: {tk} ({i+1}/{len(tickers)})")
        if prog_bar: prog_bar.progress((i+1)/len(tickers))
        try:
            df = _scoring.clean_price_frame(download_stock(tk, start=start, end=end))
            if df is None:
                failed.append(tk); continue

            # 재무 조회가 실패해도 가격 팩터로 랭킹은 계속한다.
            try:
                info = yf.Ticker(tk).info or {}
            except Exception:
                info = {}

            row = {'ticker': tk, 'sector': info.get('sector', _scoring.UNKNOWN_SECTOR)}
            # 이쪽 경로는 원점수를 반올림하지 않는다 (round_raw=None).
            row.update(_scoring.price_factors(df, round_raw=None))
            row.update(_scoring.fundamental_factors(tk, info, _dart_data.get(tk),
                                                    round_raw=None))
            results.append(row)
            if i < len(tickers) - 1:
                time.sleep(0.3)
        except Exception:
            failed.append(tk)
    if not results: return pd.DataFrame()
    rdf = _scoring.rank_by_sector_neutral_composite(
        results,
        factor_weights=factor_weights,
        ic_weights=_load_ic_factor_weights_4f(
            benchmark=_scope.regime_benchmark(tickers)),
    )
    # signal_worker.py 가 텔레그램 알림에 실패 종목 수를 찍는다. 이 키가 없으면
    # 조용히 0으로 보고돼, 유니버스 절반이 죽어도 알림은 정상으로 보인다.
    if failed:
        rdf.attrs['failed'] = failed
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
    sharpe = float((daily_r.mean() - 0.045/252) / daily_r.std() * np.sqrt(252)) if daily_r.std() > 0 else 0
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


# ─────────────────────────────────────────────
# AI 애널리스트 팀 — 4개 부서 직원 + 총괄 + 트레이더
# ─────────────────────────────────────────────

TEAM_WEIGHTS = {'차트+파동+모멘텀': 20, '퀀트+재무': 20, '매크로+금리': 15,
                'ICT+CRT': 15, '백테스팅팀': 15, '리스크팀': 15}


def _team_verdict(score):
    return '매수' if score >= 65 else ('매도' if score <= 40 else '중립')


def _current_analyst_weights():
    """방향성 3인(차트+파동+모멘텀·퀀트+재무·ICT+CRT)의 표시/블렌드 가중치(%, 합 100).

    ic_weights.json의 팩터 ICIR이 있으면 그 비율로, 없거나 측정 불가(전부 0)면
    TEAM_WEIGHTS의 3인분 비율로 폴백한다 — analyst-team-feedback-loop 승인 설계.
    """
    if _ANALYST_WEIGHTS_AVAILABLE:
        try:
            regime, _ = get_market_regime()
        except Exception:
            regime = 'neutral'
        w = _load_analyst_weights(regime)
        if w:
            return {k: round(v * 100, 1) for k, v in w.items()}
    _fallback_total = sum(TEAM_WEIGHTS[k] for k in _DIRECTIONAL_ANALYSTS) or 1
    return {k: round(TEAM_WEIGHTS[k] / _fallback_total * 100, 1) for k in _DIRECTIONAL_ANALYSTS}


def build_analyst_report(name, icon, score, reasons, detail=None, role='directional'):
    """6개 부서 직원 공통 보고서 포맷.

    role: 'directional'(방향성 블렌드 대상 — IC가중치 적용) |
          'context'(매크로, 레짐 맥락 참고용) |
          'confidence'(백테스트, 신뢰도 플래그) |
          'sizing'(리스크, Kelly 사이징 전용 — 이미 트레이더 단계에서 별도 소비).
    방향성 3인 외에는 방향성 점수 블렌드에 들어가지 않는다(manager_consolidate 참고).
    """
    weight = _current_analyst_weights().get(name, 33.3) if role == 'directional' else TEAM_WEIGHTS.get(name, 15)
    return {
        'name': name, 'icon': icon, 'score': round(float(score), 1),
        'weight': weight, 'role': role,
        'verdict': _team_verdict(score),
        'reasons': [r for r in reasons if r][:4],
        'detail': detail or {},
    }


def ict_crt_analyst(df):
    """ICT+CRT 직원: 구조 점수 + CRT/FVG/OB 조정을 합산한 보고서."""
    try:
        from modules.ict_analysis import ict_factor_score, calc_ict_adjustment
        base = ict_factor_score(df)
        adj_info = calc_ict_adjustment(df)
        score = float(np.clip(base + adj_info['adjustment'], 0, 100))
        reasons = adj_info['signals'] or ['뚜렷한 ICT/CRT 신호 없음 — 구조적으로 중립']
        return build_analyst_report('ICT+CRT', '🧭', score, reasons, adj_info)
    except Exception as e:
        return build_analyst_report('ICT+CRT', '🧭', 50.0, [f'ICT 분석 실패: {e}'])


def technical_momentum_analyst(t_score, t_det, mom_data):
    """차트+파동+모멘텀 직원: 기술점수 70% + 모멘텀점수 30%."""
    mom_score = mom_data.get('score', 50.0) if mom_data else 50.0
    score = t_score * 0.7 + mom_score * 0.3
    reasons = []
    if t_det.get('RSI값') is not None:
        reasons.append(f"RSI {t_det['RSI값']}")
    if t_det.get('ADX추세강도') is not None:
        reasons.append(f"ADX추세강도 {t_det['ADX추세강도']:.0f}점")
    if t_det.get('MA정렬') is not None:
        reasons.append(f"MA정렬 {t_det['MA정렬']:.0f}점")
    if mom_data and mom_data.get('3M') is not None:
        reasons.append(f"3개월 모멘텀 {mom_data['3M']:+.1f}%")
    return build_analyst_report('차트+파동+모멘텀', '📈', score, reasons,
                                 {**t_det, '모멘텀점수': mom_score})


def quant_fundamental_analyst(f_score, f_det, dcf_det=None):
    """퀀트+재무 직원: fundamental_score(밸류·수익성·성장성·안전성·품질) 기반."""
    reasons = []
    if f_det.get('업종'):
        reasons.append(f"업종 {f_det['업종']} (평균 PER {f_det.get('업종평균PER','N/A')})")
    if f_det.get('ROE') is not None:
        reasons.append(f"ROE {f_det['ROE']*100:.1f}%")
    if f_det.get('매출성장') is not None:
        reasons.append(f"매출성장 {f_det['매출성장']*100:+.1f}%")
    if dcf_det and dcf_det.get('상승여력_기본') is not None:
        reasons.append(f"DCF 내재가치 대비 {dcf_det['상승여력_기본']:+.1f}%")
    return build_analyst_report('퀀트+재무', '💼', f_score, reasons, f_det)


@st.cache_data(ttl=3600)
def get_real_macro():
    """FRED 실물 경제지표(CPI·실업률·산업생산·수익률곡선). 키 없으면 available=False."""
    if not _REAL_MACRO_AVAILABLE:
        return {'available': False, 'score': 50.0, 'detail': {}, 'data': {}, 'reason': ''}
    return _real_macro_score()


def macro_rate_analyst(m_score, m_det):
    """매크로+금리 직원: 시장프록시 macro_score(금리·VIX·환율 등)에 FRED 실물
    경제지표(CPI·실업률·산업생산)를 결합. 역할=레짐 맥락(context) — 방향성
    점수 블렌드에는 포함되지 않는다."""
    reasons = [f"{k}: {v}" for k, v in list(m_det.items())[:3] if not isinstance(v, dict)]
    detail = dict(m_det)
    score = m_score
    real = get_real_macro()
    if real.get('available'):
        # 시장프록시 70% + 실물지표 30% (실물은 월간·지연 데이터라 비중 축소)
        score = m_score * 0.7 + real['score'] * 0.3
        for k, v in list(real['data'].items())[:3]:
            reasons.append(f"{k}: {v}")
        detail['_실물경제'] = real['detail']
        detail['_실물경제_data'] = real['data']
    return build_analyst_report('매크로+금리', '🌍', score, reasons, detail, role='context')


def geopolitical_analyst(g_score, g_det):
    """지정학 직원: geopolitical_risk_score(유가·금·방산·VIX 프록시) 기반.
    역할=리스크 맥락(context) — 종목 방향성이 아니라 시장 전반의 지정학 스트레스를
    나타내므로 방향성 블렌드에 포함되지 않는다. 점수 높을수록 안정/저위험."""
    reasons = [f"{k}: {v}" for k, v in list(g_det.items())[:4] if not isinstance(v, dict)]
    return build_analyst_report('지정학', '🌐', g_score, reasons, g_det, role='context')


def _parse_pct(s):
    """'63.4%' / '+2.1%' 같은 포맷된 문자열을 float로 역파싱."""
    try:
        return float(str(s).replace('%', '').replace('+', ''))
    except Exception:
        return None


def backtest_analyst(df):
    """백테스팅팀 직원: 기술 시그널 전략을 이 종목 과거 데이터에 그대로 적용했을 때의 성과 검증.
    look-ahead bias 방지를 위해 순수 기술적 백테스트만 사용(재무/매크로 보정 없음)."""
    try:
        metrics, _, _ = run_backtest(df)
        sharpe = _parse_pct(metrics.get('Sharpe Ratio'))
        win_rate = _parse_pct(metrics.get('승률'))

        sharpe_score = float(np.clip((sharpe or 0) * 20 + 50, 0, 100))
        winrate_score = float(np.clip(win_rate if win_rate is not None else 50, 0, 100))
        score = sharpe_score * 0.5 + winrate_score * 0.5

        reasons = [
            f"승률 {metrics.get('승률','N/A')} (총 {metrics.get('총 매매','N/A')})",
            f"Sharpe {metrics.get('Sharpe Ratio','N/A')} · Profit Factor {metrics.get('Profit Factor','N/A')}",
            f"전략 {metrics.get('전략 수익률','N/A')} vs 매수보유 {metrics.get('매수보유 수익률','N/A')}",
            f"최대낙폭(MDD) {metrics.get('최대낙폭(MDD)','N/A')}",
        ]
        detail = {
            **metrics,
            '_win_rate_raw': win_rate,
            '_avg_win_raw': _parse_pct(metrics.get('평균 수익')),
            '_avg_loss_raw': _parse_pct(metrics.get('평균 손실')),
        }
        return build_analyst_report('백테스팅팀', '📉', score, reasons, detail, role='confidence')
    except Exception as e:
        return build_analyst_report('백테스팅팀', '📉', 50.0, [f'백테스트 실패: {e}'], role='confidence')


def risk_analyst(ticker, backtest_report=None):
    """리스크팀 직원: Beta·VaR·연간변동성·Sharpe 기반 안전성 점수 +
    (백테스팅팀 자료가 있으면) Kelly 공식 기반 권장 비중."""
    try:
        rm = calc_risk_metrics(ticker)
        if not rm:
            return build_analyst_report('리스크팀', '🛡️', 50.0, ['리스크 데이터 부족 — 데이터 이력 짧음'], role='sizing')

        vol = _parse_pct(rm.get('연간 변동성'))
        var95 = _parse_pct(rm.get('VaR 95% (1일)'))
        beta = rm.get('Beta', 1.0) or 1.0
        sharpe_key = next((k for k in rm if k.startswith('Sharpe')), None)
        sharpe = rm.get(sharpe_key, 0) if sharpe_key else 0

        vol_score    = float(np.clip(100 - (vol if vol is not None else 30) * 1.5, 0, 100))
        var_score    = float(np.clip(100 + (var95 if var95 is not None else -3) * 15, 0, 100))
        sharpe_score = float(np.clip((sharpe or 0) * 20 + 50, 0, 100))
        beta_score   = float(np.clip(100 - abs(beta - 1.0) * 30, 50, 100))
        score = vol_score * 0.35 + var_score * 0.25 + sharpe_score * 0.30 + beta_score * 0.10

        reasons = [
            f"Beta {beta:.2f}", f"연간 변동성 {rm.get('연간 변동성','N/A')}",
            f"VaR 95%(1일) {rm.get('VaR 95% (1일)','N/A')}",
            f"{sharpe_key or 'Sharpe'} {sharpe}",
        ]

        if backtest_report and _RISK_MGMT_ENABLED:
            d = backtest_report.get('detail', {})
            wr, aw, al = d.get('_win_rate_raw'), d.get('_avg_win_raw'), d.get('_avg_loss_raw')
            if wr is not None and aw is not None and al not in (None, 0):
                kelly = kelly_fraction(wr / 100, aw, al)
                reasons.append(f"권장 비중(Half-Kelly): 총자본의 {kelly*100:.1f}%")

        return build_analyst_report('리스크팀', '🛡️', score, reasons, rm, role='sizing')
    except Exception as e:
        return build_analyst_report('리스크팀', '🛡️', 50.0, [f'리스크 분석 실패: {e}'], role='sizing')


def manager_consolidate(reports):
    """총괄 직원: 방향성 3명(차트+파동+모멘텀·퀀트+재무·ICT+CRT)의 IC가중 블렌드로
    매수/매도를 판정 — 가중합 + 합의율.

    매크로(레짐 맥락)·백테스트(신뢰도 플래그)·리스크(Kelly 사이징)는 역할이
    다르므로 방향성 점수·합의율 계산에서 제외한다(analyst-team-feedback-loop
    승인 설계 — 6명 전부를 매수/매도 "투표"에 섞으면 방향성 정확도가 흐려진다).
    role 태그가 없는 구버전 reports가 들어오면 방어적으로 전원을 방향성으로 취급한다.
    """
    directional = [r for r in reports if r.get('role', 'directional') == 'directional']
    if not directional:
        directional = reports

    total_w = sum(r['weight'] for r in directional) or 1
    weighted_score = sum(r['score'] * r['weight'] for r in directional) / total_w
    verdicts = [r['verdict'] for r in directional]
    buy_n, sell_n, neu_n = verdicts.count('매수'), verdicts.count('매도'), verdicts.count('중립')
    n = len(directional)
    majority = n * 0.6  # 60% 이상 동의 시 "우세"로 판정

    if buy_n >= majority:
        consensus = f'매수 우세 ({buy_n}/{n}명 매수)'
    elif sell_n >= majority:
        consensus = f'매도 우세 ({sell_n}/{n}명 매도)'
    elif buy_n > sell_n and buy_n > 0:
        consensus = f'매수 쏠림, 의견 갈림 ({buy_n}매수·{sell_n}매도·{neu_n}중립)'
    elif sell_n > buy_n and sell_n > 0:
        consensus = f'매도 쏠림, 의견 갈림 ({buy_n}매수·{sell_n}매도·{neu_n}중립)'
    else:
        consensus = f'의견 분산 — 관망 권고 ({buy_n}매수·{sell_n}매도·{neu_n}중립)'

    agreement = round(max(buy_n, sell_n, neu_n) / n * 100)
    strongest = max(directional, key=lambda r: abs(r['score'] - 50))
    weakest_agree = min(directional, key=lambda r: 0 if r['verdict'] == _team_verdict(weighted_score) else 1)

    # 참고 정보 — 방향성 블렌드에는 안 들어가지만 총괄 보고서에 함께 표시
    contexts = [r for r in reports if r.get('role') == 'context']
    confidence = next((r for r in reports if r.get('role') == 'confidence'), None)
    macro_note = (' · '.join(f"{c['icon']} {c['name']} {c['score']:.0f}점({c['verdict']})" for c in contexts) + ' — 리스크 맥락 참고'
                  if contexts else None)
    confidence_note = None
    if confidence:
        conf_level = '높음' if confidence['score'] >= 60 else ('보통' if confidence['score'] >= 45 else '낮음')
        confidence_note = f"{confidence['icon']} 백테스트 신뢰도 {conf_level} ({confidence['score']:.0f}점)"

    return {
        'total_score': round(weighted_score, 1),
        'verdict': _team_verdict(weighted_score),
        'consensus': consensus,
        'agreement': agreement,
        'buy_n': buy_n, 'sell_n': sell_n, 'neutral_n': neu_n,
        'strongest_opinion': f"{strongest['icon']} {strongest['name']} ({strongest['score']:.0f}점, {strongest['verdict']})",
        'dissent': (f"{weakest_agree['icon']} {weakest_agree['name']}만 {weakest_agree['verdict']} 의견"
                    if weakest_agree['verdict'] != _team_verdict(weighted_score) else None),
        'macro_note': macro_note,
        'confidence_note': confidence_note,
    }


def trader_signal_lines(df, manager_report, risk_report=None):
    """트레이더 직원: 총괄 보고서 + 지지/저항 레벨로 매수/매도 라인 산출.
    리스크팀 보고서에 Kelly 권장 비중이 있으면 그대로 인용한다."""
    cp = float(df['Close'].iloc[-1])
    sr = find_sr_levels(df['Close'], df['High'], df['Low'])
    supports = sorted([s['level'] for s in sr if not s['above']], reverse=True)
    resistances = sorted([s['level'] for s in sr if s['above']])

    verdict = manager_report['verdict']
    agreement = manager_report['agreement']

    buy_line  = supports[0] if supports else cp * 0.97
    sell_line = resistances[0] if resistances else cp * 1.05

    if verdict == '매수':
        stance = '분할 매수 검토' if agreement >= 75 else '소액 선진입, 지지선 확인 후 비중 확대'
    elif verdict == '매도':
        stance = '비중 축소 검토' if agreement >= 75 else '일부 이익실현, 저항선 이탈 시 전량 정리'
    else:
        stance = '신규 진입 보류 — 저항/지지 재테스트 대기'

    position_note = None
    if risk_report:
        for r in risk_report['reasons']:
            if 'Half-Kelly' in r:
                position_note = r
                break

    return {
        'stance': stance,
        'buy_line': buy_line, 'sell_line': sell_line,
        'buy_dist': (buy_line - cp) / cp * 100,
        'sell_dist': (sell_line - cp) / cp * 100,
        'current': cp,
        'position_note': position_note,
    }


# ─────────────────────────────────────────────
# 자동매매 운영팀 — 모니터링·보고 전용 (실주문 로직 없음)
# ─────────────────────────────────────────────

def build_ops_report(name, icon, status, reasons):
    """운영팀·시스템/멀티종목팀 공통 보고서 포맷. status: 정상/주의/경고/대기.
    팀 소속은 이 dict가 아니라 사무실의 방 구성(main()의 _rooms 목록)으로 결정된다 —
    보고서 자체에 팀을 중복 태깅하지 않는다."""
    return {'name': name, 'icon': icon, 'status': status,
            'reasons': [r for r in reasons if r][:4]}


@st.cache_data(ttl=60, show_spinner=False)
def signal_pipeline_employee():
    """시그널 파이프라인 직원: signal_log.json 상태 점검 (신호 발생/평가 현황).
    앱 안에서 signal_log.json을 쓰는 두 지점(시스템 시그널 생성, 21일 수익률 평가)이
    각자 .clear()로 이 캐시를 무효화하므로, 세션 내 갱신은 즉시 반영되고 그 사이엔
    매 rerun·중복 호출마다 파일을 다시 읽지 않는다."""
    path = os.path.join(os.path.dirname(__file__), "signal_log.json")
    if not os.path.exists(path):
        return build_ops_report('시그널 파이프라인', '📡', '주의',
                                 ['signal_log.json 없음 — 아직 발생한 시그널 이력이 없음'])
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f).get('signals', [])
        if not data:
            return build_ops_report('시그널 파이프라인', '📡', '주의', ['기록된 시그널 0건'])

        dates = sorted(s['entry_date'] for s in data if s.get('entry_date'))
        last_date = dates[-1] if dates else None
        pending = sum(1 for s in data if s.get('return_pct') is None)
        done = len(data) - pending
        days_since = ((datetime.now().date() - pd.to_datetime(last_date).date()).days
                      if last_date else None)

        status = '주의' if (days_since is not None and days_since > 5) else '정상'
        reasons = [
            f"누적 시그널 {len(data)}건 (평가완료 {done} · 대기 {pending})",
            f"최근 시그널: {last_date}" + (f" ({days_since}일 전)" if days_since is not None else ''),
            "자동 스캔: signal-alerts.yml (매일 UTC 22:30, 월~금)",
        ]
        return build_ops_report('시그널 파이프라인', '📡', status, reasons)
    except Exception as e:
        return build_ops_report('시그널 파이프라인', '📡', '경고', [f'로그 읽기 실패: {e}'])


def execution_mode_employee():
    """실행 모드 직원: 현재 시스템이 시그널 전용 모드임을 명시 — 안전성 투명성 담당."""
    return build_ops_report('실행 모드', '⚙️', '정상', [
        '현재 모드: 시그널 전용 — 실제 주문 없음',
        'signal-alerts.yml: 활성 (텔레그램 알림만 발송)',
        'paper-trade-us.yml: 크론 비활성화 (DRY_RUN 기본값 true)',
        '실제 매매는 시그널 확인 후 사용자가 직접 실행',
    ])


def risk_guardrail_employee():
    """리스크 가드레일 직원: 킬스위치·서킷브레이커 모듈 로드 상태와 설정 위치 안내
    (계좌 연동 없이 모니터링만 — 실제 값 점검은 아래 위젯에서 수행)."""
    reasons = [
        '일일 손실 한도 킬스위치: 아래 "킬스위치" 위젯에서 설정·점검',
        '포지션 대사(의도 vs 실제): 아래 "포지션 대사" 위젯에서 실행',
        ('드로다운 서킷브레이커 모듈 로드됨' if _RISK_MGMT_ENABLED
         else '⚠️ risk_management 모듈 로드 실패 — 서킷브레이커 비활성'),
    ]
    status = '정상' if (_OPS_SAFETY_AVAILABLE and _RISK_MGMT_ENABLED) else '주의'
    return build_ops_report('리스크 가드레일', '🛡️', status, reasons)


@st.cache_data(ttl=60, show_spinner=False)
def equity_log_employee():
    """계좌 현황 직원: equity_log.json 상태 (시그널 전용 모드에서는 보통 비어있는 게 정상).
    equity_log.json은 앱 안에서는 쓰지 않고 외부 페이퍼 트레이딩 스크립트만 갱신하므로
    무효화 없이 캐시해도 안전하다."""
    path = os.path.join(os.path.dirname(__file__), "equity_log.json")
    if not os.path.exists(path):
        return build_ops_report('계좌 현황', '📊', '정상',
            ['equity_log.json 없음 — 시그널 전용 모드에서는 정상 (실거래가 없어 자산 변동 기록도 없음)'])
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        records = data if isinstance(data, list) else data.get('records', [])
        if not records:
            return build_ops_report('계좌 현황', '📊', '정상',
                ['equity_log.json 존재하나 기록 0건 (과거 페이퍼 트레이딩 이력 없음)'])
        last = records[-1]
        reasons = [f'{len(records)}개 기록 존재 (과거 페이퍼 트레이딩 이력)']
        if last.get('date') and last.get('equity') is not None:
            reasons.append(f"최근 기록: {last['date']} · 자산 {last['equity']:,.0f}")
        return build_ops_report('계좌 현황', '📊', '정상', reasons)
    except Exception as e:
        return build_ops_report('계좌 현황', '📊', '경고', [f'로그 읽기 실패: {e}'])


# ─────────────────────────────────────────────
# 시스템/멀티종목 담당팀 — 사무실의 팀별 방에 배치되는 직원 (보고서 + 실제 업무 화면)
# 4팀 7명: 시그널 생성팀(팩터 랭킹·시스템 시그널·섹터 로테이션),
#          ML 시그널팀(ML 신호), 백테스트 검증팀(팩터 백테스트·종목 백테스팅),
#          퀀트 리서치/QA팀(고급 분석)
# ─────────────────────────────────────────────

def factor_ranking_employee():
    """팩터 랭킹 담당(시그널 생성팀): 세션에 실행된 팩터 분석 결과 상태."""
    fdf = st.session_state.get('qt_factors')
    if fdf is None or fdf.empty:
        return build_ops_report('팩터 랭킹', '📊', '대기',
            ['아직 팩터 분석 미실행 — "📊 팩터 분석 실행" 버튼으로 시작'])
    failed = fdf.attrs.get('failed', [])
    top = fdf.iloc[0]
    reasons = [f"{len(fdf)}개 종목 분석 완료 (1위: {top['ticker']} {top['composite']:.0f}점)"]
    if failed:
        reasons.append(f"⚠️ {len(failed)}개 종목 분석 실패: {', '.join(failed[:3])}")
    return build_ops_report('팩터 랭킹', '📊', '주의' if failed else '정상', reasons)


def system_signal_employee():
    """시스템 시그널 담당(시그널 생성팀): 세션에서 생성한 매매 액션 상태."""
    qs = st.session_state.get('qt_signals')
    if not qs:
        return build_ops_report('시스템 시그널', '🤖', '대기',
            ['아직 시그널 미생성 — "🤖 시스템 시그널 생성" 버튼으로 시작'])
    rebal = qs['rebal']
    reasons = [f"매수 {rebal['buy_count']} · 매도 {rebal['sell_count']} · 관망 {rebal['hold_count']}",
               f"다음 리밸런싱: {rebal['next_rebal']}"]
    return build_ops_report('시스템 시그널', '🤖', '정상', reasons)


def sector_rotation_employee():
    """섹터 로테이션 담당(시그널 생성팀): 신규 다운로드 없이 정적 상태만 안내(비용 방지)."""
    return build_ops_report('섹터 로테이션', '🔄', '정상',
        ['12개 섹터 ETF 모멘텀 랭킹 상시 제공 (1시간 캐시)',
         '모멘텀 = 1M×50% + 3M×30% + 6M×20%'])


def ml_signal_employee():
    """ML 신호 담당(ML 시그널팀): Purged K-Fold 검증 결과 상태."""
    if not _ML_AVAILABLE:
        return build_ops_report('ML 신호', '🧠', '경고', ['modules/ml_signals.py 로드 실패'])
    mr = st.session_state.get('ml_result')
    if not mr:
        return build_ops_report('ML 신호', '🧠', '대기',
            ['아직 학습 미실행 — "🧠 ML 신호 학습 & 검증" 버튼으로 시작'])
    res = mr['res']
    reasons = [f"{mr['ticker']} 평균 AUC {res['mean_auc']:.4f} ({res['interpretation']})",
               f"AUC 표준편차 {res['std_auc']:.4f} · Fold {res['n_folds_used']}개"]
    return build_ops_report('ML 신호', '🧠', '정상' if res['mean_auc'] >= 0.53 else '주의', reasons)


def factor_backtest_employee():
    """팩터 백테스트 담당(백테스트 검증팀): 팩터 전략 백테스트 + IC 검증 상태."""
    reasons, status = [], '대기'
    qbt = st.session_state.get('qt_bt')
    if qbt:
        m = qbt['metrics']
        reasons.append(f"전략 {m['total_return']:+.1f}% (알파 {m['alpha']:+.1f}%p) · Sharpe {m['sharpe']:.2f}")
        status = '정상'
    icr = st.session_state.get('ic_result')
    if icr:
        s = icr['summary']
        reasons.append(f"IC 검증: 평균 IC {s['mean_ic']:+.4f} · ICIR {s['icir']:+.2f}")
        status = '정상'
    if not reasons:
        reasons = ['아직 백테스트/IC 검증 미실행']
    return build_ops_report('팩터 백테스트', '📉', status, reasons)


def stock_backtest_employee():
    """종목 백테스팅 담당(백테스트 검증팀): 개별 종목 전략 백테스트 상태."""
    bt = st.session_state.get('tab3')
    if not bt:
        return build_ops_report('종목 백테스팅', '📊', '대기',
            ['아직 백테스팅 미실행 — "📉 백테스팅 시작" 버튼으로 시작'])
    m = bt['metrics']
    reasons = [f"{bt['bt_ticker']} 전략 {m.get('전략 수익률','N/A')} vs 매수보유 {m.get('매수보유 수익률','N/A')}",
               f"Sharpe {m.get('Sharpe Ratio','N/A')} · MDD {m.get('최대낙폭(MDD)','N/A')}"]
    return build_ops_report('종목 백테스팅', '📊', '정상', reasons)


def advanced_research_employee():
    """고급 분석 담당(퀀트 리서치/QA팀): 무결성·스트레스·알파디케이·시그널디케이·팩터리스크
    5개 검증 모듈의 로드 상태를 점검(각 분석은 세션 상태로 남지 않아 실행 결과 자체는 집계하지 않음)."""
    mods = [('데이터 무결성', _DATA_INTEGRITY_AVAILABLE), ('스트레스 테스트', _STRESS_TEST_AVAILABLE),
            ('알파 디케이', _ALPHA_DECAY_AVAILABLE), ('시그널 디케이', _SIGNAL_DECAY_AVAILABLE),
            ('팩터 리스크모델', _FACTOR_RISK_AVAILABLE)]
    loaded = [n for n, ok in mods if ok]
    failed = [n for n, ok in mods if not ok]
    status = '정상' if not failed else ('경고' if len(failed) >= 3 else '주의')
    reasons = [f"검증 모듈 {len(loaded)}/5개 로드됨: {', '.join(loaded) or '없음'}"]
    if failed:
        reasons.append(f"⚠️ 로드 실패: {', '.join(failed)}")
    return build_ops_report('고급 분석', '🔬', status, reasons)


# ─────────────────────────────────────────────
# 사무실 뷰 — 팀별 방 + 직원 클릭 → 보고서, 마지막 총괄 트레이더 종합 보고
# ─────────────────────────────────────────────

class OfficeEmployee(NamedTuple):
    """방 안의 직원 한 명. panel_fn이 있으면 보고서 카드 아래에 실제 업무 화면(입력폼·실행버튼·결과)을
    이어서 그린다 — 이름 있는 필드라 emp[3]/emp[4] 같은 위치 인덱싱과 len() 방어 코드가 필요 없다."""
    key: str
    name: str
    avatar: str
    report_fn: Callable
    panel_fn: Optional[Callable] = None


class OfficeRoom(NamedTuple):
    """사무실의 방(팀) 하나. employees는 직원 리스트이거나, 인자 없는 callable
    (team_panel_fn 실행 *이후*에 평가됨 — AI애널리스트팀처럼 그 방의 공용 패널이 실행돼야
    로스터가 정해지는 경우에 사용)."""
    key: str
    name: str
    icon: str
    employees: Union[List[OfficeEmployee], Callable[[], List[OfficeEmployee]]]
    team_panel_fn: Optional[Callable] = None


_OFFICE_STATUS_COLOR = {'정상': '#10b981', '주의': '#f59e0b', '경고': '#ef4444', '대기': '#94a3b8'}

# 보고서 카드의 기능성 아이콘(rep['icon'])은 Tab1 애널리스트 카드에서도 재사용되므로 그대로 두고,
# 사무실 방 안의 아바타(클릭 타일)에만 쓸 사람 캐릭터 이모지를 직원 이름 기준으로 별도 매핑한다.
_OFFICE_AVATARS = {
    '차트+파동+모멘텀': '🧑‍💻', '퀀트+재무': '🧑‍💼', '매크로+금리': '🧑‍🏫',
    'ICT+CRT': '🕵️', '백테스팅팀': '🧑‍🔬', '리스크팀': '💂', '총괄': '🧑‍⚖️',
    '실행 모드': '🧑‍✈️', '시그널 파이프라인': '🧑‍🔧', '리스크 가드레일': '👮', '계좌 현황': '🕴️',
    '팩터 랭킹': '🧑‍🔬', '시스템 시그널': '🤖', '섹터 로테이션': '🧑‍🚀',
    'ML 신호': '👩‍💻',
    '팩터 백테스트': '🧑‍🔬', '종목 백테스팅': '🧑‍💻',
    '고급 분석': '🕵️‍♀️',
}


def _office_avatar(name):
    return _OFFICE_AVATARS.get(name, '🧑‍💼')


def inject_office_css():
    """방 컨테이너를 사무실 바닥·파티션처럼 보이게 하는 CSS + 직원 타일 "일하는 중" 애니메이션
    키프레임을 주입. st.container(key=...)가 만드는 안정적인 st-key-* 클래스를 이용해
    사무실 방에만 스코프를 좁힌다(앱 전역 컨테이너에는 영향 없음). 실제 색/애니메이션
    선택(직원별로 다름)은 render_office_rooms가 매 rerun 별도의 작은 <style> 블록으로 주입한다.

    주의: 세션당 1회 주입(session_state 게이트)은 금물 — Streamlit은 rerun마다 화면을
    새로 그리므로 두 번째 rerun부터 <style>이 DOM에서 사라져 사무실 스타일이 전부 풀린다.
    매 rerun 호출돼야 하며, 중복 호출 방지는 호출부(render_office_rooms 한 곳)가 담당한다."""
    st.markdown("""
<style>
/* ── 사무실 바닥재: 원목 파케이 플로어링 느낌의 마루 패턴 ── */
div[class*="st-key-office_room_"] {
    background:
        repeating-linear-gradient(90deg, rgba(120,90,40,.05) 0 2px, transparent 2px 34px),
        repeating-linear-gradient(0deg, rgba(120,90,40,.045) 0 2px, transparent 2px 34px),
        linear-gradient(180deg, rgba(120,100,60,.05) 0%, rgba(120,100,60,.05) 100%);
    border-radius: 14px !important;
    border: 1px solid var(--border) !important;
    border-top: 3px solid #8b6f3e !important;
    padding: 16px 14px 14px 14px !important;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.4), 0 1px 3px rgba(0,0,0,.05) !important;
}
/* ── 직원 = 책상 앞에 선 캐릭터 타일 (기존 알약 버튼보다 훨씬 큼직하게) ── */
div[class*="st-key-office_room_"] button {
    font-size: 1.65rem !important;
    line-height: 1.35 !important;
    min-height: 88px !important;
    padding: 10px 8px 14px 8px !important;
    border-radius: 12px 12px 6px 6px !important;
    background: var(--surface) !important;
    border-bottom: 5px solid var(--border2, #cbd5e1) !important;
    white-space: pre-line !important;
    transition: transform .15s ease, box-shadow .15s ease;
}
div[class*="st-key-office_room_"] button p { font-size: 1.65rem !important; line-height: 1.3 !important; }
div[class*="st-key-office_room_"] button:hover {
    transform: translateY(-3px);
    box-shadow: 0 6px 14px rgba(0,0,0,.12) !important;
}
div[class*="st-key-office_room_"] button:active { transform: translateY(-1px) scale(.98); }

@keyframes office-pulse {
    0%, 100% { box-shadow: 0 0 0 0 var(--office-glow, rgba(16,185,129,.35)); }
    50%      { box-shadow: 0 0 0 5px rgba(16,185,129,0); }
}
@keyframes office-pulse-fast {
    0%, 100% { box-shadow: 0 0 0 0 var(--office-glow, rgba(245,158,11,.4)); }
    50%      { box-shadow: 0 0 0 5px rgba(245,158,11,0); }
}
@keyframes office-shake {
    0%, 100% { transform: translateX(0); }
    20%      { transform: translateX(-2px); }
    40%      { transform: translateX(2px); }
    60%      { transform: translateX(-2px); }
    80%      { transform: translateX(2px); }
}
@keyframes office-sleep {
    0%, 100% { opacity: .55; }
    50%      { opacity: .85; }
}

/* ── 직원들이 자리로 뛰어가는 애니메이션 (분석 실행 중에만 표시) ── */
@keyframes office-walk-bounce {
    0%, 100% { transform: translateY(0) scaleX(1); }
    25%      { transform: translateY(-9px) scaleX(.94); }
    50%      { transform: translateY(0) scaleX(1.05); }
    75%      { transform: translateY(-5px) scaleX(.97); }
}
.office-walk-strip {
    display: flex; align-items: center; gap: 14px;
    background: var(--surface2); border: 1px dashed var(--border2, #cbd5e1);
    border-radius: 10px; padding: 10px 16px; margin: 4px 0 12px 0;
}
.office-walk-strip .office-walk-char {
    font-size: 1.6rem; display: inline-block;
    animation: office-walk-bounce 0.7s ease-in-out infinite;
}
.office-walk-strip .office-walk-label {
    font-size: 12.5px; font-weight: 600; color: var(--text-3);
}

/* ── 시세판(칠판/전광판 스타일) 종목 입력 패널 — 라이트/다크 무관하게 항상 짙은 보드 ── */
div[class*="st-key-office_ticker_board"] {
    background:
        radial-gradient(ellipse at top left, rgba(16,185,129,.10), transparent 60%),
        repeating-linear-gradient(180deg, rgba(255,255,255,.015) 0 1px, transparent 1px 3px),
        linear-gradient(155deg, #0b1220 0%, #111a2c 55%, #0b1220 100%);
    border: 1px solid #24314d !important;
    border-radius: 14px !important;
    padding: 18px 20px 16px 20px !important;
    box-shadow: inset 0 0 40px rgba(0,0,0,.35), 0 4px 16px rgba(0,0,0,.18) !important;
}
div[class*="st-key-office_ticker_board"] label,
div[class*="st-key-office_ticker_board"] p { color: #d6dee8 !important; }
div[class*="st-key-office_ticker_board"] input,
div[class*="st-key-office_ticker_board"] [data-baseweb="select"] > div {
    background: #0f1829 !important;
    border: 1px solid #2c3b5c !important;
    color: #7ee6b8 !important;
    font-family: 'JetBrains Mono', monospace !important;
}
div[class*="st-key-office_ticker_board"] input::placeholder { color: #4c5f80 !important; }
</style>
""", unsafe_allow_html=True)


_OFFICE_ANIM = {'정상': 'office-pulse', '주의': 'office-pulse-fast', '경고': 'office-shake', '대기': 'office-sleep'}


def _office_tile_style(rep):
    """직원 타일 테두리 색·애니메이션을 보고서에서 계산 — 클릭 전에도 "지금 뭘 하고 있는지"가
    보이게 한다(정상=은은한 초록 펄스, 주의=빠른 호박색 펄스, 경고=흔들림, 대기=졸린 깜빡임)."""
    if 'score' in rep:
        color = score_color(rep['score'])
        anim = ('office-pulse' if rep['verdict'] == '매수'
                else 'office-shake' if rep['verdict'] == '매도' else 'office-sleep')
    else:
        color = _OFFICE_STATUS_COLOR.get(rep['status'], '#94a3b8')
        anim = _OFFICE_ANIM.get(rep['status'], '')
    return color, anim


def _office_normalize(rep):
    """분석직원(score 보고서) / 운영·시스템직원(status 보고서)을 공통 표시 포맷으로 변환."""
    if 'score' in rep:
        return {'icon': rep['icon'], 'name': rep['name'], 'color': score_color(rep['score']),
                'headline': f"{rep['score']:.0f}점 · {rep['verdict']}", 'reasons': rep['reasons']}
    return {'icon': rep['icon'], 'name': rep['name'],
            'color': _OFFICE_STATUS_COLOR.get(rep['status'], '#94a3b8'),
            'headline': rep['status'], 'reasons': rep['reasons']}


def _reasons_to_html(reasons, empty_msg='참고할 정보 없음'):
    """근거 리스트를 <li> HTML로 변환 — 사무실 카드와 Tab1 팀 카드가 공유하는 로직."""
    return ''.join(f"<li>{r}</li>" for r in reasons) or f'<li>{empty_msg}</li>'


_OFFICE_WALK_CHARS = ['🧑‍💻', '🧑‍💼', '🕵️', '🧑‍🔬', '👮', '🧑‍✈️']


def office_walk_strip_show(placeholder, label="직원들이 자리로 뛰어가는 중..."):
    """분석 시작 시 직원들이 책상으로 뛰어가는 모습을 흉내내는 애니메이션 띠를 표시.
    placeholder(st.empty())에 그려서, 분석이 끝나면 호출부에서 placeholder.empty()로 지운다."""
    chars_html = "".join(
        f'<span class="office-walk-char" style="animation-delay:{i*0.12:.2f}s">{c}</span>'
        for i, c in enumerate(_OFFICE_WALK_CHARS))
    placeholder.markdown(f"""
<div class="office-walk-strip">
  {chars_html}
  <span class="office-walk-label">🏃 {label}</span>
</div>""", unsafe_allow_html=True)


def render_company_summary():
    """전사 종합 보고 배너 — 페이지 하단 상시 표시 + 게임에서 사장 클릭 시 상세 영역에도 표시."""
    status_fns = [
        execution_mode_employee, signal_pipeline_employee, risk_guardrail_employee, equity_log_employee,
        factor_ranking_employee, system_signal_employee, sector_rotation_employee,
        ml_signal_employee, factor_backtest_employee, stock_backtest_employee, advanced_research_employee,
    ]
    statuses = [fn()['status'] for fn in status_fns]
    warn_n, caution_n = statuses.count('경고'), statuses.count('주의')
    ok_n, wait_n = statuses.count('정상'), statuses.count('대기')
    if warn_n:
        color, headline = '#ef4444', f"⚠️ 경고 {warn_n}건 — 확인 필요"
    elif caution_n:
        color, headline = '#f59e0b', f"🟡 주의 {caution_n}건"
    else:
        color, headline = '#10b981', "✅ 전체 정상 운영 중"

    analyst_line = ''
    snap = st.session_state.get('office_analyst_snapshot')
    if snap:
        m = snap['manager']
        analyst_line = (f"<div style='margin-top:6px'>📈 {snap['ticker']} 분석: "
                        f"<b style='color:{score_color(m['total_score'])}'>{m['total_score']:.1f}점 · {m['consensus']}</b></div>")

    st.markdown(f"""
<div style="background:{color}0d;border:1px solid {color}40;border-radius:12px;padding:16px 20px">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.6px">📐 총괄 트레이더 — 전사 종합 보고</div>
  <div style="font-size:1.3rem;font-weight:800;color:{color};margin:6px 0">{headline}</div>
  <div style="font-size:12px;color:var(--text-3)">정상 {ok_n} · 주의 {caution_n} · 경고 {warn_n} · 대기 {wait_n} (운영·시스템팀 {len(statuses)}명 기준){analyst_line}</div>
</div>""", unsafe_allow_html=True)


def collect_office_game_data(rooms: List[OfficeRoom]):
    """게임 씬(office_game 컴포넌트)에 넘길 방·캐릭터 데이터를 수집.
    직원별 보고서를 평가해 상태색·애니메이션(behavior)을 뽑는다 — report_fn들은
    로컬 파일/세션만 읽는 가벼운 함수라 render_office_rooms와 중복 호출해도 부담 없다."""
    game_rooms = []
    for room in rooms:
        employees = room.employees() if callable(room.employees) else room.employees
        if not employees:
            if room.key == 'analyst':
                # 분석 전에도 애널리스트팀 방과 빈 책상들을 보여준다 —
                # "종목을 입력하면 출근한다"는 게임 서사를 시각적으로 예고
                game_rooms.append({'key': room.key, 'name': room.name, 'icon': room.icon,
                                   'chars': [], 'ghost': len(TEAM_WEIGHTS) + 1})  # 7명 + 총괄
            continue
        chars = []
        for emp in employees:
            rep = emp.report_fn()
            color, anim = _office_tile_style(rep)
            n = _office_normalize(rep)
            chars.append({'key': emp.key, 'name': emp.name, 'avatar': emp.avatar,
                          'color': color, 'anim': anim, 'headline': n['headline']})
        game_rooms.append({'key': room.key, 'name': room.name, 'icon': room.icon, 'chars': chars})
    return game_rooms


def render_office_report_card(rep):
    """선택된 직원의 보고서 카드를 표시."""
    n = _office_normalize(rep)
    _reason_html = _reasons_to_html(n['reasons'])
    st.markdown(f"""
<div style="background:{n['color']}0d;border:1px solid {n['color']}40;border-radius:10px;
            padding:12px 16px;margin:8px 0 4px 0">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
    <span style="font-size:13px;font-weight:700;color:var(--text-2)">{n['icon']} {n['name']}</span>
    <span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;background:{n['color']}20;color:{n['color']}">{n['headline']}</span>
  </div>
  <ul style="font-size:12px;color:var(--text-3);margin:0;padding-left:18px;line-height:1.7">{_reason_html}</ul>
</div>""", unsafe_allow_html=True)


def render_office_rooms(rooms: List[OfficeRoom]):
    """여러 방을 2단계로 렌더링: 1) 모든 방의 헤더·(팀 공용 패널)·버튼을 먼저 그려 클릭을 확정하고(카드 자리는 예약만),
    2) 최종 확정된 선택값으로 해당 방의 카드 자리에만 보고서를 채운다.
    한 번의 rerun 안에서 버튼을 st.button()으로 순차 평가하는 Streamlit 특성상,
    방마다 즉시 session_state를 읽어 카드를 그리면 방금 클릭한 방보다 먼저 그려진 방이
    직전 선택값을 그대로 보여주는 결함이 생기므로 2단계로 분리한다.

    team_panel_fn이 있는 방(입력폼·차트 등 넓은 화면이 필요)은 항상 전체 폭으로,
    없는 방(버튼만 있는 단순한 방)은 사무실 평면도처럼 2개씩 나란히 배치한다."""
    inject_office_css()
    slots = {}
    tile_css = []

    def _render_room_body(room):
        st.markdown(f"""
<div style="display:inline-flex;align-items:center;gap:9px;margin:18px 0 8px 0;
            background:linear-gradient(180deg,#8b6f3e 0%,#6f5730 100%);
            border-radius:6px 6px 3px 3px;padding:7px 16px 7px 12px;
            box-shadow:0 3px 6px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.15)">
  <span style="font-size:17px">{room.icon}</span>
  <span style="font-size:12.5px;font-weight:800;color:#fdf6e3;letter-spacing:.4px;
               text-transform:uppercase">{room.name}</span>
</div>""", unsafe_allow_html=True)
        with st.container(border=True, key=f"office_room_{room.key}"):
            if room.team_panel_fn is not None:
                room.team_panel_fn()
            employees = room.employees() if callable(room.employees) else room.employees
            reports = {}
            if employees:
                st.caption(f"👥 직원 {len(employees)}명")
                cols = st.columns(len(employees))
                for col, emp in zip(cols, employees):
                    with col:
                        rep = emp.report_fn()
                        reports[emp.key] = rep
                        color, anim = _office_tile_style(rep)
                        tile_css.append(
                            f'.st-key-office_btn_{room.key}_{emp.key} button {{'
                            f'border:2px solid {color} !important;'
                            f'--office-glow:{color}59;'
                            f'animation:{anim} 2.4s ease-in-out infinite;}}')
                        _name_line = emp.name
                        if anim == 'office-sleep':
                            _name_line = f"😴 {_name_line}"
                        elif anim == 'office-shake':
                            _name_line = f"❗ {_name_line}"
                        label = f"{emp.avatar}\n{_name_line}"
                        if st.button(label, key=f"office_btn_{room.key}_{emp.key}", use_container_width=True):
                            st.session_state['office_view'] = (room.key, emp.key)
            slots[room.key] = (st.container(), employees, reports)

    i = 0
    while i < len(rooms):
        room = rooms[i]
        if room.team_panel_fn is not None:
            _render_room_body(room)
            i += 1
        else:
            pair = [room]
            if i + 1 < len(rooms) and rooms[i + 1].team_panel_fn is None:
                pair.append(rooms[i + 1])
                i += 2
            else:
                i += 1
            for col, r in zip(st.columns(len(pair)), pair):
                with col:
                    _render_room_body(r)

    if tile_css:
        st.markdown(f"<style>{''.join(tile_css)}</style>", unsafe_allow_html=True)

    _sel = st.session_state.get('office_view')
    for room_key, (slot, employees, reports) in slots.items():
        with slot:
            if employees and _sel and _sel[0] == room_key:
                emp = next((e for e in employees if e.key == _sel[1]), None)
                if emp is not None:
                    render_office_report_card(reports.get(emp.key) or emp.report_fn())
                    if emp.panel_fn is not None:
                        st.markdown("")
                        emp.panel_fn()
            elif employees:
                st.caption("👆 직원을 클릭하면 이 방에서 보고서와 업무 화면을 볼 수 있습니다.")


def office_analyst_employees():
    """AI 애널리스트팀 방의 직원 목록 — Tab1에서 마지막으로 분석한 종목 스냅샷 기반.
    아직 분석 이력이 없으면 None."""
    snap = st.session_state.get('office_analyst_snapshot')
    if not snap:
        return None
    mgr = snap['manager']
    mgr_rep = {'icon': '🧑‍💼', 'name': '총괄', 'score': mgr['total_score'], 'verdict': mgr['verdict'],
               'reasons': [mgr['consensus'], f"팀 합의율 {mgr['agreement']}%",
                           f"가장 강한 의견: {mgr['strongest_opinion']}"]
               + ([mgr['dissent']] if mgr['dissent'] else [])}
    employees = [OfficeEmployee(r['name'], r['name'], _office_avatar(r['name']), (lambda rr=r: rr))
                 for r in snap['reports']]
    employees.append(OfficeEmployee('총괄', '총괄', _office_avatar('총괄'), lambda: mgr_rep))
    return employees


# 종목 분석 가중치 — 사이드바 제거, 값 하드코딩. 유일한 기준값(_DEFAULT_*)에서 파생시켜서
# 여러 곳에 흩어진 하드코딩이 서로 다른 값으로 갈라지지 않게 한다. render_stock_analysis_panel은
# 캐시된 스냅샷을 재표시할 때 이 값들을 다시 지역 할당하므로(Python 스코프 규칙상 함수 안에서
# 이름이 재할당되면 그 이름은 함수 전체에서 지역 변수가 됨) 자기 이름으로 초기화할 수 없어
# _DEFAULT_* 별도 이름에서 시작값을 가져온다 — w_tech = w_tech 형태는 UnboundLocalError를 낸다.
_DEFAULT_W_TECH, _DEFAULT_W_FUND, _DEFAULT_W_MACRO = 35, 40, 25
_DEFAULT_TOTAL_W = 100
w_tech, w_fund, w_macro = _DEFAULT_W_TECH, _DEFAULT_W_FUND, _DEFAULT_W_MACRO
total_w = _DEFAULT_TOTAL_W


def main():
    _hdr_col1, _hdr_col2 = st.columns([5, 1])
    with _hdr_col1:
        st.markdown("""
<div style="display:flex;align-items:center;gap:12px;padding:4px 0 12px 0;margin-bottom:0">
  <div style="width:40px;height:40px;border-radius:10px;
              background:linear-gradient(135deg,#10b981,#059669);
              display:flex;align-items:center;justify-content:center;
              font-size:20px;flex-shrink:0">📈</div>
  <div>
    <div style="font-size:1.45rem;font-weight:800;color:var(--text-1);
                letter-spacing:-0.5px;line-height:1.2">퀀트 트레이딩 시스템</div>
    <div style="font-size:12.5px;color:var(--text-3);margin-top:2px">
      종목 분석 &nbsp;·&nbsp; 팩터 퀀트 &nbsp;·&nbsp; 시그널 알림 &nbsp;·&nbsp; 리스크 관리
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
    with _hdr_col2:
        st.toggle("🌙 다크모드", value=_dark_mode, key="ui_dark_mode")
    st.divider()

    # ── 홈 요약 배너 (시장 레짐 스냅샷) ──────────────────
    _home_regime = _get_market_regime()
    _home_r_color = {'bull': '#10b981', 'bear': '#ef4444', 'mixed': '#f59e0b', 'unknown': '#94a3b8'}.get(_home_regime, '#94a3b8')
    _home_r_label = {
        'bull': '🐂 강세장 — SPY·QQQ 모두 200일선 위 (롱 우호적)',
        'bear': '🐻 약세장 — SPY·QQQ 모두 200일선 아래 (신중 접근)',
        'mixed': '🟡 혼조 — SPY·QQQ 엇갈림 (선택적 진입)',
        'unknown': '⚪ 시장 레짐 확인 불가',
    }.get(_home_regime, '⚪ 시장 레짐 확인 불가')
    st.markdown(
        f"<div style='background:{_home_r_color}12;border:1px solid {_home_r_color}40;"
        f"border-radius:8px;padding:8px 16px;margin-bottom:14px;display:flex;"
        f"align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px'>"
        f"<span style='font-size:13px;font-weight:600;color:{_home_r_color}'>{_home_r_label}</span>"
        f"<span style='font-size:11px;color:var(--text-4)'>📡 시그널 전용 모드 — 매일 장마감 후 자동 스캔, 실제 주문 없음</span>"
        f"</div>", unsafe_allow_html=True)


    # ── AI 애널리스트팀 방 공용 패널: 단일 종목 분석 ──────
    def render_stock_analysis_panel():
        # 사이드바 제거 — 값 하드코딩 (이 패널 전용). 캐시된 스냅샷 재표시 시 아래에서
        # w_tech 등을 다시 지역 할당하므로 모듈 전역 이름을 직접 못 쓰고 _DEFAULT_*에서 시작한다.
        w_tech, w_fund, w_macro = _DEFAULT_W_TECH, _DEFAULT_W_FUND, _DEFAULT_W_MACRO
        acct_capital     = 10_000_000
        risk_per_trade   = 1.0
        max_position_pct = 20
        min_rr           = 1.5

        with st.container(key="office_ticker_board"):
            st.markdown("""
<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
  <span style="width:8px;height:8px;border-radius:50%;background:#ef4444;
               box-shadow:0 0 8px #ef4444;animation:office-pulse-fast 1.6s infinite"></span>
  <span style="font-size:11px;font-weight:800;color:#ef4444;letter-spacing:1.5px">LIVE</span>
  <span style="font-size:12px;font-weight:700;color:#9fb3d1;letter-spacing:2px;
               text-transform:uppercase;margin-left:4px">시세 입력 · 종목 검색</span>
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
        if refresh:
            if 'tab1' in st.session_state:
                del st.session_state['tab1']
            run = True

        # ── 분석은 "예약 → rerun → 실행" 2단계 ──────────────────
        # 예약된 rerun에서 위 게임 씬이 먼저 working=True로 렌더돼 전 직원이 자리로
        # 뛰어가고, 그 다음 여기서 실제 분석(블로킹)이 돈다 — 그동안 iframe은
        # 클라이언트에서 독립적으로 계속 움직인다. 분석 종료 후 main 끝의 flip
        # rerun이 게임을 대기 모드로 되돌린다.
        if run and ticker:
            st.session_state['pending_analysis'] = ticker
            st.rerun()

        _err_prev = st.session_state.pop('office_analysis_error', None)
        if _err_prev:
            st.error(_err_prev)

        run_ticker = st.session_state.pop('pending_analysis', None)
        if run_ticker:
            ticker = run_ticker
            walk_ph = st.empty()
            office_walk_strip_show(walk_ph)
            prog = st.progress(0); msg = st.empty()
            msg.text("📥 데이터 다운로드 중...")
            prog.progress(5)

            end_dt   = datetime.now()
            start_dt = end_dt - timedelta(days=520)
            _df = download_stock(ticker, start=start_dt, end=end_dt)

            if _df.empty:
                st.session_state['office_analysis_error'] = f"'{ticker}' 데이터를 찾을 수 없습니다."
                st.session_state['_analysis_done_flip'] = True
                prog.empty(); msg.empty(); walk_ph.empty()
            else:
                _df = _df.dropna(subset=['Close'])
                if len(_df) < 30:
                    st.session_state['office_analysis_error'] = "데이터 부족 (30일 미만)."
                    st.session_state['_analysis_done_flip'] = True
                    prog.empty(); msg.empty(); walk_ph.empty()
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
                    _g_score, _g_det, _g_data = geopolitical_risk_score()
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

                    # 뉴스 감성 틸트: 검증된(IC 측정) 팩터가 아니므로 ±4점으로 제한
                    # (_mtf_bonus와 동일한 bounded-tilt 패턴 — LLM 오독이 신호를 뒤집지
                    #  못하게 상한). 실제 헤드라인이 있을 때만 반영, 중립(50)·무뉴스는 0.
                    _news_bonus = 0.0
                    if _news_articles:
                        _news_bonus = float(np.clip((_news_score - 50) / 50 * 4, -4, 4))
                        _total = float(np.clip(_total + _news_bonus, 0, 100))
                        _total_adj = float(np.clip(_total_adj + _news_bonus, 0, 100))
                        _score_method += f" + 뉴스({_news_bonus:+.0f})"

                    # ML 신호 틸트: 이 종목 과거에서 out-of-fold AUC가 게이트(0.55)를
                    # 넘을 때만(has_edge) ±4점 반영. 예측력 미검증이면 0 — 검증 안 된
                    # 신호를 스코어에 섞지 않는 저장소 원칙과 동일. (없으면 조용히 skip)
                    _ml_info = None
                    _ml_bonus = 0.0
                    if _ML_AVAILABLE:
                        try:
                            _ml_info = _ml_predict(_df)
                            if _ml_info.get('has_edge') and _ml_info.get('prob') is not None:
                                _ml_bonus = float(np.clip((_ml_info['prob'] - 0.5) / 0.5 * 4, -4, 4))
                                _total = float(np.clip(_total + _ml_bonus, 0, 100))
                                _total_adj = float(np.clip(_total_adj + _ml_bonus, 0, 100))
                                _score_method += f" + ML({_ml_bonus:+.0f})"
                        except Exception:
                            _ml_info = None

                    try:
                        _info = yf.Ticker(ticker).info
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
                                    _earn_str = f"다음 실적발표: <b>{pd.Timestamp(ed).strftime('%Y-%m-%d')}</b> ({days_left:+d}일)"
                                    if 0 <= days_left <= 14: _earn_str += " 🔔"
                            elif isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
                                ed = cal.loc['Earnings Date'].iloc[0]
                                days_left = (pd.Timestamp(ed).date() - datetime.now().date()).days
                                _earn_str = f"다음 실적발표: <b>{pd.Timestamp(ed).strftime('%Y-%m-%d')}</b> ({days_left:+d}일)"
                                if 0 <= days_left <= 14: _earn_str += " 🔔"
                    except Exception:
                        pass

                    prog.progress(100); prog.empty(); msg.empty(); walk_ph.empty()

                    st.session_state['tab1'] = {
                        'ticker': ticker, 'df': _df, 'end_dt': end_dt,
                        't_score': _t_score, 't_det': _t_det, 'candle_pats': _candle_pats,
                        'mom_data': _mom_data, 'ic_data': _ic_data,
                        'f_score': _f_score, 'f_det': _f_det,
                        'm_score': _m_score, 'm_det': _m_det, 'm_data': _m_data,
                        'g_score': _g_score, 'g_det': _g_det, 'g_data': _g_data,
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
                    # 분석 완료 — 이 rerun은 결과·스냅샷까지 렌더한 뒤 main 끝에서
                    # 한 번 더 rerun해 게임 씬을 대기 모드로 되돌린다.
                    st.session_state['_analysis_done_flip'] = True

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
            g_score   = _a.get('g_score', 50.0); g_det = _a.get('g_det', {})
            total     = _a['total']; mtf_scores = _a['mtf_scores']
            dcf_det   = _a['dcf_det']
            risk_data = _a['risk_data']
            news_score = _a['news_score']; news_articles = _a['news_articles']
            regime    = _a['regime']
            total_adj = _a['total_adj']
            info      = _a['info']; name = _a['name']
            is_krw    = _a['is_krw']; cp = _a['cp']; pp = _a['pp']
            live_price = _a.get('live_price', cp)
            live_label = _a.get('live_label', '현재가')
            pre_price = _a.get('pre_price'); pre_chg = _a.get('pre_chg')
            post_price = _a.get('post_price'); post_chg = _a.get('post_chg')
            earn_str  = _a['earn_str']
            w_tech    = _a['w_tech']; w_fund = _a['w_fund']; w_macro = _a['w_macro']

            fmt_p  = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"

            regime_icon  = {'bull':'🐂 강세장','bear':'🐻 약세장','neutral':'➡️ 중립장'}.get(regime, '➡️ 중립장')

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
                    _ep.append(f"<span style='color:var(--text-4)'>프리마켓</span> <b>{fmt_p(pre_price)}</b> "
                               f"<span style='color:{'#10b981' if _pv>=0 else '#ef4444'}'>{_pv:+.2f}%</span>")
                if post_price and post_price > 0:
                    _pov = post_chg * 100 if post_chg and abs(post_chg) < 1 else (post_chg or 0)
                    _ep.append(f"<span style='color:var(--text-4)'>애프터</span> <b>{fmt_p(post_price)}</b> "
                               f"<span style='color:{'#10b981' if _pov>=0 else '#ef4444'}'>{_pov:+.2f}%</span>")
                if _ep:
                    _ext_html = f"<div style='font-size:12px;color:var(--text-3);margin-top:6px'>{'&nbsp;&nbsp;·&nbsp;&nbsp;'.join(_ep)}</div>"

            _earn_html = (f"<div style='font-size:12px;color:#f59e0b;margin-top:4px'>📅 {earn_str}</div>"
                          if earn_str else '')
            _extra_html = _ext_html + _earn_html

            st.markdown(f"""
<div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;
            padding:20px 24px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.06)">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
    <div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-size:1.4rem;font-weight:800;color:var(--text-1);letter-spacing:-.3px">{name}</span>
        <code style="font-size:13px;background:var(--surface2);color:var(--text-2);padding:3px 8px;border-radius:6px;font-weight:600">{ticker}</code>
        <span style="font-size:11px;padding:3px 10px;border-radius:20px;font-weight:600;
                     background:{_regime_bg};color:{_regime_badge_color};
                     border:1px solid {_regime_badge_color}40">{regime_icon}</span>
      </div>
      <div style="margin-top:8px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <span style="font-size:2rem;font-weight:800;color:var(--text-1);font-family:'JetBrains Mono',monospace;letter-spacing:-1px">{fmt_p(live_price)}</span>
        <span style="font-size:1rem;font-weight:700;color:{_chg_color}">{_chg_arrow} {abs(live_chg):.2f}%</span>
        <span style="font-size:12px;color:var(--text-4)">{live_label}</span>
      </div>{_extra_html}
    </div>
    <div style="display:flex;gap:20px;flex-wrap:wrap">
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px">52주 고가</div>
        <div style="font-size:15px;font-weight:700;color:#10b981;font-family:'JetBrains Mono',monospace;margin-top:2px">{fmt_p(_52h)}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px">52주 저가</div>
        <div style="font-size:15px;font-weight:700;color:#ef4444;font-family:'JetBrains Mono',monospace;margin-top:2px">{fmt_p(_52l)}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:10px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px">기준일</div>
        <div style="font-size:13px;font-weight:600;color:var(--text-2);margin-top:2px">{end_dt.strftime('%Y-%m-%d')}</div>
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
                return (f"<div style='background:var(--surface2);border-radius:6px;height:8px;margin-top:6px'>"
                        f"<div style='background:{color};width:{pct:.0f}%;height:8px;border-radius:6px'></div></div>")

            sc_col1, sc_col2, sc_col3, sc_col4 = st.columns(4)
            with sc_col1:
                _tc = score_color(total)
                st.markdown(f"""
<div style="background:var(--surface);border:2px solid {_tc}50;border-radius:12px;padding:18px 20px;
            text-align:center;box-shadow:0 2px 10px {_tc}20">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px">종합 점수</div>
  <div style="font-size:3.2rem;font-weight:800;color:{_tc};font-family:'JetBrains Mono',monospace;
              line-height:1;margin:8px 0 6px">{total:.1f}</div>
  {_score_badge(total)}
</div>""", unsafe_allow_html=True)

            with sc_col2:
                _c2 = score_color(t_score)
                st.markdown(f"""
<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px">
    차트+파동 <span style="color:var(--text-4)">({w_tech}%)</span>
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
<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px">
    재무+퀀트 <span style="color:var(--text-4)">({w_fund}%)</span>
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
<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px">
    매크로+금리 <span style="color:var(--text-4)">({w_macro}%)</span>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <span style="font-size:2rem;font-weight:800;color:{_c4};font-family:'JetBrains Mono',monospace">{m_score:.0f}</span>
    {_score_badge(m_score)}
  </div>
  {_bar_html(m_score, _c4)}
</div>""", unsafe_allow_html=True)

            # ── AI 애널리스트 팀 리포트 ──────────────────────
            with st.expander("🏢 AI 애널리스트 팀 리포트 — 7개 부서 → 총괄 → 매수/매도 라인", expanded=False):
                with st.spinner("팀 리포트 작성 중 (백테스트·리스크 분석 포함)..."):
                    _bt_rep = backtest_analyst(df)
                    _team_reports = [
                        technical_momentum_analyst(t_score, t_det, mom_data),
                        quant_fundamental_analyst(f_score, f_det, dcf_det),
                        macro_rate_analyst(m_score, m_det),
                        ict_crt_analyst(df),
                        _bt_rep,
                        risk_analyst(ticker, _bt_rep),
                        geopolitical_analyst(g_score, g_det),
                    ]

                def _render_report_card(_rep):
                    _rc = score_color(_rep['score'])
                    _reason_html = _reasons_to_html(_rep['reasons'], '참고할 세부 신호 없음')
                    st.markdown(f"""
<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;
            box-shadow:0 1px 4px rgba(0,0,0,.06);height:100%">
  <div style="font-size:12px;font-weight:700;color:var(--text-3)">{_rep['icon']} {_rep['name']} <span style="color:var(--text-4)">({_rep['weight']}%)</span></div>
  <div style="display:flex;align-items:baseline;gap:8px;margin:6px 0">
    <span style="font-size:1.6rem;font-weight:800;color:{_rc};font-family:'JetBrains Mono',monospace">{_rep['score']:.0f}</span>
    <span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:12px;background:{_rc}20;color:{_rc}">{_rep['verdict']}</span>
  </div>
  <ul style="font-size:11px;color:var(--text-3);margin:0;padding-left:16px;line-height:1.6">{_reason_html}</ul>
</div>""", unsafe_allow_html=True)

                for _row_start in range(0, len(_team_reports), 3):
                    _row_reports = _team_reports[_row_start:_row_start + 3]
                    _tcols = st.columns(3)
                    for _tcol, _rep in zip(_tcols, _row_reports):
                        with _tcol:
                            _render_report_card(_rep)

                st.markdown("")
                _mgr = manager_consolidate(_team_reports)
                _mc  = score_color(_mgr['total_score'])
                _dissent_html = f"<br>⚠️ {_mgr['dissent']}" if _mgr['dissent'] else ''
                _context_bits = ' · '.join(x for x in (_mgr.get('macro_note'), _mgr.get('confidence_note')) if x)
                _context_html = (f"<div style='font-size:11px;color:var(--text-4);margin-top:6px'>{_context_bits}</div>"
                                 if _context_bits else '')
                st.markdown(f"""
<div style="background:{_mc}0d;border:1px solid {_mc}40;border-radius:10px;padding:14px 18px;margin-top:4px">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.6px">🧑‍💼 총괄 직원 종합 보고서 <span style="text-transform:none;font-weight:500">— 방향성 3인 IC가중 블렌드</span></div>
  <div style="display:flex;align-items:baseline;gap:12px;margin:6px 0;flex-wrap:wrap">
    <span style="font-size:2rem;font-weight:800;color:{_mc};font-family:'JetBrains Mono',monospace">{_mgr['total_score']:.1f}</span>
    <span style="font-size:13px;font-weight:700;color:{_mc}">{_mgr['consensus']}</span>
    <span style="font-size:11px;color:var(--text-4)">팀 합의율 {_mgr['agreement']}%</span>
  </div>
  <div style="font-size:12px;color:var(--text-3)">가장 강한 의견: {_mgr['strongest_opinion']}{_dissent_html}</div>
  {_context_html}
</div>""", unsafe_allow_html=True)

                _risk_rep = next(r for r in _team_reports if r['name'] == '리스크팀')
                _trader = trader_signal_lines(df, _mgr, _risk_rep)
                st.session_state['office_analyst_snapshot'] = {
                    'ticker': ticker, 'reports': _team_reports, 'manager': _mgr, 'trader': _trader,
                }
                _tl_color = '#10b981' if _mgr['verdict'] == '매수' else ('#ef4444' if _mgr['verdict'] == '매도' else '#f59e0b')
                _pos_html = (f"<div style='font-size:12px;color:var(--text-3);margin-top:6px'>💰 {_trader['position_note']}</div>"
                             if _trader['position_note'] else '')
                st.markdown(f"""
<div style="background:{_tl_color}0d;border:1px solid {_tl_color}40;border-radius:10px;padding:14px 18px;margin-top:8px">
  <div style="font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;letter-spacing:.6px">📐 트레이더 직원 — 매수/매도 라인</div>
  <div style="font-size:13px;font-weight:700;color:{_tl_color};margin:6px 0">{_trader['stance']}</div>
  <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:13px">
    <span>🟢 매수 라인: <b style="font-family:'JetBrains Mono',monospace">{fmt_p(_trader['buy_line'])}</b>
      <span style="color:var(--text-4);font-size:11px">({_trader['buy_dist']:+.1f}%)</span></span>
    <span>🔴 매도 라인: <b style="font-family:'JetBrains Mono',monospace">{fmt_p(_trader['sell_line'])}</b>
      <span style="color:var(--text-4);font-size:11px">({_trader['sell_dist']:+.1f}%)</span></span>
  </div>{_pos_html}
</div>""", unsafe_allow_html=True)
                st.caption("⚠️ 규칙 기반 자동 산출 — 투자 참고용이며 매매 판단의 책임은 본인에게 있습니다.")

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
                    _tv_theme = 'dark' if _dark_mode else 'light'
                    return (
                        f"https://www.tradingview.com/widgetembed/"
                        f"?symbol={_tv_sym}&interval=D&theme={_tv_theme}&style=1"
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
                        f"<div style='font-size:11px;color:var(--text-3)'>{_label}</div>"
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
                            f"<span style='color:var(--text-2);font-size:12px;line-height:1.5'>{sig_dc}</span></div>",
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
                    bar = (f"<div style='background:var(--surface2);border-radius:4px;height:5px;margin-top:8px'>"
                           f"<div style='background:{clr};width:{min(sc_val,100):.0f}%;height:5px;border-radius:4px'></div>"
                           f"</div>") if is_score else ""
                    return (
                        f"<div style='background:var(--surface);border:1px solid var(--border);border-radius:10px;"
                        f"padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.05)'>"
                        f"<div style='font-size:11px;font-weight:700;color:var(--text-4);text-transform:uppercase;"
                        f"letter-spacing:.6px;margin-bottom:6px'>{icon} {title}</div>"
                        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                        f"<span style='font-size:1.5rem;font-weight:800;color:{clr};"
                        f"font-family:\"JetBrains Mono\",monospace'>{sc_disp}</span>"
                        f"<span style='font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;"
                        f"background:{clr}18;color:{clr}'>{lbl}</span>"
                        f"</div>"
                        f"<div style='font-size:11px;color:var(--text-4);margin-top:4px'>{note}</div>"
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
                    "<p style='font-size:11px;color:var(--text-4);margin-top:12px;text-align:center'>"
                    "⚠️ 본 분석은 투자 참고용이며 투자 결정의 책임은 본인에게 있습니다.</p>",
                    unsafe_allow_html=True)

            with sub3:
                st.subheader("카테고리별 점수")

                def _score_bar(label, score, color):
                    pct = max(min(score, 100), 0)
                    st.markdown(
                        f"<div style='margin:10px 0'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:5px'>"
                        f"<span style='font-weight:600;font-size:14px;color:var(--text-2)'>{label}</span>"
                        f"<span style='font-weight:700;color:{color};font-size:14px;"
                        f"font-family:\"JetBrains Mono\",monospace'>{score:.0f}</span></div>"
                        f"<div style='background:var(--surface2);border-radius:6px;height:9px'>"
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
                    if '장단기스프레드(10Y-3M)' in m_data:
                        st.caption(f"장단기 스프레드(10Y-3M): {m_data['장단기스프레드(10Y-3M)']:.2f}%p")
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
                        f"<div style='color:var(--text-1);font-size:13px;margin-top:4px'>{tr['strategy']}</div>"
                        f"<div style='color:var(--text-1);font-size:11px;margin-top:4px'>"
                        f"손익비 <b style='color:{rr_c}'>R {tr['rr1']:.1f}:1</b>"
                        f" · 손절 <b style='color:#ef5350'>{tr['risk_pct']:.1f}%</b>"
                        f" · 비중 <b>{tr['alloc']}</b></div>"
                        f"</div>", unsafe_allow_html=True)

                    st.caption("**진입 조건 체크**")
                    cond_text = " &nbsp;|&nbsp; ".join(tr.get('conditions', []))
                    st.markdown(f"<div style='font-size:12px;color:var(--text-1)'>{cond_text}</div>",
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
                            f"<div style='background:var(--surface);border:1px solid {verdict_color}88;"
                            f"border-radius:8px;padding:12px 14px;margin-bottom:8px'>"
                            f"<div style='display:flex;justify-content:space-between;gap:10px'>"
                            f"<b>{plan['label']} 실행 판정</b>"
                            f"<b style='color:{verdict_color}'>{plan['verdict']}</b></div>"
                            f"<div style='color:var(--text-1);font-size:12px;margin-top:6px'>{notes_text}</div>"
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
                        f"<div style='color:var(--text-1);font-size:11px;margin-top:4px'>감성 점수</div>"
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


    # ── 시스템/멀티종목 담당 직원 패널 정의 모음 (사무실 탭에서 호출됨) ──
    # 탭이 아니라 main() 내부 함수로만 존재 — main()의 w_tech/w_fund/w_macro/total_w
    # 클로저를 그대로 쓰기 위해 이 위치(main() 안)에 정의를 둔다.
    if True:
        def render_universe_picker():
            """시그널 생성팀 방 공용 자원 — 여러 팀 직원이 함께 쓰는 종목 유니버스 입력.
            st.session_state['qt_tickers']에 저장해 어느 직원 패널에서든 읽을 수 있게 한다."""
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
                    if len(qt_tickers) >= 100:
                        st.warning(f"⚠️ {len(qt_tickers)}개 종목 — 조회에 수 분 이상 소요될 수 있습니다. {', '.join(qt_tickers[:8])}...")
                    else:
                        st.info(f"{len(qt_tickers)}개 종목: {', '.join(qt_tickers[:8])}{'...' if len(qt_tickers) > 8 else ''}")
            st.session_state['qt_tickers'] = qt_tickers
            st.caption("💡 이 유니버스는 팩터 백테스트 직원 카드(백테스트 검증팀)에서도 함께 사용됩니다.")

        def render_factor_ranking_panel():
            qt_tickers = st.session_state.get('qt_tickers', [])
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
                    f"<div style='background:var(--bg);border:1px solid var(--border);border-radius:10px;"
                    f"padding:12px 18px;margin:8px 0;display:flex;flex-wrap:wrap;gap:20px;align-items:center'>"
                    f"<div>"
                    f"<div style='font-size:10px;font-weight:700;color:var(--text-4);text-transform:uppercase;"
                    f"letter-spacing:.7px;margin-bottom:4px'>시장 환경</div>"
                    f"<div style='font-size:13px;font-weight:600;color:var(--text-2)'>"
                    f"VIX <span style='color:#3b82f6;font-family:\"JetBrains Mono\",monospace'>{_ft_env['vix']}</span>"
                    f"<span style='color:var(--text-4);font-size:11px'> (평균 {_ft_env['vix_avg']})</span>"
                    f" &nbsp;·&nbsp; 10Y금리 <span style='color:#f59e0b;font-family:\"JetBrains Mono\",monospace'>"
                    f"{_ft_env['rate']}%</span>"
                    f"<span style='color:var(--text-4);font-size:11px'> ({_ft_env['rate_chg']:+.2f}%p)</span>"
                    f"</div></div>"
                    f"<div>"
                    f"<div style='font-size:10px;font-weight:700;color:var(--text-4);text-transform:uppercase;"
                    f"letter-spacing:.7px;margin-bottom:4px'>팩터 가중치</div>"
                    f"<div style='font-size:13px;font-weight:600;color:var(--text-2)'>"
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
                        f"<div style='background:var(--surface);border-left:3px solid {comp_c};"
                        f"border-radius:6px;padding:8px 14px;margin:4px 0;"
                        f"display:flex;justify-content:space-between;align-items:center'>"
                        f"<span><b>#{int(r['rank'])} {r['ticker']}</b> "
                        f"<span style='color:var(--text-1)'>{r['name']}</span></span>"
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

        def render_system_signal_panel():
            qt_tickers = st.session_state.get('qt_tickers', [])
            st.caption("팩터 랭킹 + 기술적 필터를 결합한 규칙 기반 매매 시그널")

            sc1, sc2, sc3 = st.columns(3)
            qt_top_n = sc1.slider("매수 후보 수 (Top N)", 3, 10, 5, key="qt_top_n")
            qt_capital = sc2.number_input("가상 투자금 $ (시뮬레이션 전용, 실거래 없음)",
                                          min_value=1000, value=100000, step=10000, key="qt_capital")
            sc3.selectbox("리밸런싱 주기", ["월간 (20일)", "격주 (10일)", "주간 (5일)"],
                          key="qt_rebal")

            if st.button("🤖 시스템 시그널 생성", type="primary", key="qt_sig_run"):
                fdf = st.session_state.get('qt_factors')
                # 포트폴리오 최적화 탭이 제거되어 커스텀 비중 입력 경로가 없음 — 항상 균등 비중.
                with st.spinner("시그널 생성 중..."):
                    actions, rebal = generate_system_signals(
                        qt_tickers, factor_df=fdf, weights=None,
                        top_n=qt_top_n, capital=qt_capital)
                st.session_state['qt_signals'] = {'actions': actions, 'rebal': rebal}

                # ── 매수 시그널 → signal_log.json 저장 ──────────────────
                import json as _json_w
                _sl_path_w = os.path.join(os.path.dirname(__file__), "signal_log.json")
                _buy_acts  = [a for a in actions if '매수' in a['action']]
                if _buy_acts:
                    _existing_w: dict = {}
                    if os.path.exists(_sl_path_w):
                        try:
                            with open(_sl_path_w) as _fw: _existing_w = _json_w.load(_fw)
                        except Exception: pass
                    _sigs_w = _existing_w.get('signals', [])
                    _today_w = datetime.now().strftime('%Y-%m-%d')
                    _existing_keys = {(s.get('symbol'), s.get('entry_date')) for s in _sigs_w}
                    for _a in _buy_acts:
                        _raw_px = _a['price'].replace('$','').replace('₩','').replace(',','')
                        try: _raw_px = float(_raw_px)
                        except Exception: _raw_px = 0.0
                        if (_a['ticker'], _today_w) not in _existing_keys:
                            _sigs_w.append({'symbol': _a['ticker'], 'entry_date': _today_w,
                                            'entry_price': _raw_px, 'action': _a['action'],
                                            'reason': _a['reason'], 'source': 'app',
                                            'return_pct': None})
                    try:
                        with open(_sl_path_w, 'w') as _fw2:
                            _json_w.dump({'signals': _sigs_w}, _fw2, ensure_ascii=False, indent=2)
                        signal_pipeline_employee.clear()
                    except Exception: pass

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
                    pri_badge = ("<span style='background:#ef535022;color:#ef5350;padding:1px 6px;"
                                 "border-radius:3px;font-size:10px;margin-left:4px'>HIGH</span>"
                                 if a['priority'] == 'HIGH' else '')
                    price_str = a.get('price', '')
                    alloc_str = a.get('alloc', '')
                    qty_str = a.get('qty', '')
                    detail = f"{price_str} · {alloc_str} · {qty_str}" if price_str else ''
                    _detail_html = (
                        f"<div style='color:var(--text-1);font-weight:600;font-size:13px;margin-top:4px'>{detail}</div>"
                        if detail else ''
                    )
                    st.markdown(
                        f"<div style='background:var(--surface);border-left:4px solid {ac};"
                        f"border-radius:6px;padding:10px 14px;margin:4px 0'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='font-size:15px'><b>{a['ticker']}</b> {a['action']}{pri_badge}</span>"
                        f"<span style='color:var(--text-1);font-size:12px'>비중 {a['weight']} · 3M {a['mom']}</span></div>"
                        f"<div style='color:var(--text-3);font-size:12px;margin-top:4px'>{a['reason']}</div>"
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

                # ── 시그널 자동 발송 안내 ─────────────────────
                with st.expander("📡 시그널 자동 발송 (GitHub Actions → 텔레그램)", expanded=False):
                    st.markdown(
                        """
**⚠️ 이 시스템은 시그널 발송 전용입니다 — 실제 주문 집행 없음.**

| 구분 | 실행 시간 | 워크플로 |
|------|-----------|----------|
| 🇺🇸 미국 장 마감 후 | 매일 UTC 21:30 (월~금) | `signal-alerts.yml` |

- 장마감 후 유니버스 전체 스캔 → 팩터 상위 종목 필터링
- 매수 / 매도 / 조건부 매수 시그널만 **텔레그램**으로 발송
- 실제 주문은 발송하지 않으며, 직접 판단 후 수동 매매
- 발송 기록은 `signal_log.json`에 저장 → 아래 적중률 추적에 활용
""",
                        unsafe_allow_html=False,
                    )
                    st.caption("⚙️ `.github/workflows/signal-alerts.yml` 및 `signal_worker.py` 참고")

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
            st.caption("앱 또는 GitHub Actions에서 발생한 매수 시그널의 21일 후 실제 수익률 추적.")
            import json as _json
            _sl_path = os.path.join(os.path.dirname(__file__), "signal_log.json")
            if os.path.exists(_sl_path):
                try:
                    with open(_sl_path) as _slf:
                        _sl_data = _json.load(_slf).get("signals", [])

                    # 21일 이상 경과한 미평가 시그널 → 현재가 조회 후 return_pct 계산
                    _today_dt = datetime.now().date()
                    _needs_eval = [s for s in _sl_data
                                   if s.get('return_pct') is None
                                   and s.get('entry_date') and s.get('entry_price')
                                   and (_today_dt - pd.to_datetime(s['entry_date']).date()).days >= 21]
                    if _needs_eval:
                        _eval_tickers = list({s['symbol'] for s in _needs_eval})
                        _cur_prices: dict = {}
                        for _etk in _eval_tickers:
                            try:
                                _ep_df = yf.download(_etk, period='2d', progress=False)
                                if isinstance(_ep_df.columns, pd.MultiIndex):
                                    _ep_df.columns = _ep_df.columns.droplevel(1)
                                if not _ep_df.empty:
                                    _cur_prices[_etk] = float(_ep_df['Close'].dropna().iloc[-1])
                            except Exception:
                                pass
                        _log_updated = False
                        for _s in _sl_data:
                            if (_s.get('return_pct') is None and _s.get('symbol') in _cur_prices
                                    and _s.get('entry_price') and _s['entry_price'] > 0):
                                _age = (_today_dt - pd.to_datetime(_s['entry_date']).date()).days
                                if _age >= 21:
                                    _s['return_pct'] = round(
                                        (_cur_prices[_s['symbol']] / _s['entry_price'] - 1) * 100, 2)
                                    _log_updated = True
                        if _log_updated:
                            try:
                                with open(_sl_path, 'w') as _slf2:
                                    _json.dump({'signals': _sl_data}, _slf2,
                                               ensure_ascii=False, indent=2)
                                signal_pipeline_employee.clear()
                            except Exception:
                                pass

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
                        if _sl_pend:
                            st.info(f"대기 중인 시그널 {len(_sl_pend)}건 — 시그널 발생 21일 후 자동 평가됩니다.")
                        else:
                            st.info("아직 시그널 기록이 없습니다. '시스템 시그널 생성'을 실행하면 매수 시그널이 자동 기록됩니다.")
                except Exception as _e:
                    st.warning(f"시그널 로그 로드 오류: {_e}")
            else:
                st.info("signal_log.json 없음 — 시그널을 생성하면 자동으로 만들어집니다.")

        def render_factor_backtest_panel():
            qt_tickers = st.session_state.get('qt_tickers', [])
            st.caption("팩터 전략을 과거 데이터로 검증합니다. 매월 팩터 Top N을 매수하고 리밸런싱한 결과.")
            st.info(
                "**⚠️ 생존자 편향 주의** — 현재 선택된 유니버스는 *지금* 살아있는 종목만 포함합니다. "
                "백테스트 기간 중 퇴출·상장폐지된 종목은 자동으로 제외되어 실제보다 수익률이 "
                "과대평가될 수 있습니다. 워크-포워드 검증(학습/검증 분리) 결과를 함께 참고하세요."
            )

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
                    # '팩터 랭킹' 직원 카드의 체크박스(key="qt_timing")를 그대로 존중한다 — 그
                    # 카드가 이번 세션에 한 번도 안 열렸다면 체크박스 기본값(True)으로 대체한다.
                    _bt_use_timing = st.session_state.get('qt_timing', True)
                    _bt_fw = get_factor_timing_weights()[0] if _bt_use_timing else None
                    bt_m, bt_eq, bt_log = backtest_factor_strategy(
                        qt_tickers, top_n=bt_topn, years=bt_years,
                        factor_weights=_bt_fw,
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



        def render_stock_backtest_panel():
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
                        pf_sharpe = float((pf_dr.mean() - 0.045/252) / pf_dr.std() * np.sqrt(252)) if pf_dr.std() > 0 else 0
                        roll_max  = pd.Series(pbt_eq.values).expanding().max()
                        pf_mdd    = float(((pd.Series(pbt_eq.values) - roll_max) / roll_max * 100).min())
                        pm1, pm2, pm3, pm4 = st.columns(4)
                        pm1.metric("포트폴리오 수익률", f"{pf_ret:+.1f}%")
                        pm2.metric("CAGR",             f"{pf_cagr:+.1f}%")
                        pm3.metric("MDD",              f"{pf_mdd:.1f}%")
                        pm4.metric("Sharpe",           f"{pf_sharpe:.2f}")

        # ── 섹터 로테이션 패널 (시그널 생성팀 직원용) ─────────────
        def render_sector_rotation_panel():
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

        # ── ML 신호 패널 (ML 시그널팀 직원용) ────────────────────
        def render_ml_signal_panel():
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

        # ── 고급 분석 패널 (퀀트 리서치/QA팀 직원용) ──────────────
        def render_advanced_research_panel():
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

        # ── 운영 안전성 패널 (자동매매 운영팀 직원용) ────────────
        def render_execution_mode_panel():
            with st.expander("📡 시그널 자동 발송 현황", expanded=True):
                st.markdown(
                    """
**🛑 현재 모드: 자동 발송 중단 — 수동 실행만 가능**

`signal-alerts.yml`의 크론 스케줄은 **2026-07-20부로 비활성화**되어 있습니다.
유니버스를 37 → 276종목으로 확대해 5년 walk-forward IC를 재측정한 결과 전 팩터가
`|ICIR| < 0.1`로 나와, 예측력이 확인되지 않은 신호를 매일 알림으로 보내지 않기
위해 멈춘 상태입니다 (근거: `ic_weights.json`, `docs/superpowers/specs/`).
신호원을 다시 찾기 전까지는 재개하지 않습니다.

**지금 시그널이 필요하면:**
- GitHub Actions → `signal-alerts.yml` → *Run workflow*(수동 실행)로만 가능합니다.
- 실제 주문 워크플로(`paper-trade-us.yml`)도 마찬가지로 비활성화 상태입니다.

**텔레그램으로 수신되는 내용 (수동 실행 시):**
- 매수 / 조건부 매수 / 매도 / 관망 시그널
- 종목별 현재가 · 가상 투자금 배분 · 수량 · 근거

**시그널 적중률 추적:**
- 매수 시그널은 `signal_log.json`에 자동 기록
- 사무실 → 시그널 생성팀 → '시스템 시그널' 직원 카드의 '📊 과거 시그널 적중률'에서 21일 후 수익률 확인
"""
                )
                st.caption("⚙️ `.github/workflows/signal-alerts.yml` · `signal_worker.py` — 크론 비활성화, 수동(workflow_dispatch) 실행만 가능. 주문 워크플로도 비활성화 상태")

        def render_risk_guardrail_panel():
            if not _OPS_SAFETY_AVAILABLE:
                st.error("modules/ops_safety.py 로드 실패.")
                return
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
                        st.error("🛑 손실 한도 초과 → 거래 차단")
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

    st.markdown("""
<div style="display:flex;align-items:center;gap:14px;margin:6px 0 16px 0;padding:16px 22px;
            background:linear-gradient(135deg,#1a2332 0%,#0f1622 100%);
            border-radius:12px;box-shadow:0 4px 14px rgba(0,0,0,.16)">
  <div style="width:46px;height:46px;border-radius:8px;background:rgba(255,255,255,.08);
              display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0;
              border:1px solid rgba(255,255,255,.12)">🏢</div>
  <div>
    <div style="font-size:1.1rem;font-weight:800;color:#f5f0e2;letter-spacing:.3px">퀀트 증권 트레이딩 데스크</div>
    <div style="font-size:12px;color:#9fb3d1;margin-top:2px">
      팀별 방을 둘러보고 직원을 클릭해 보고서와 업무 화면을 확인하세요 · 마지막엔 총괄 트레이더가 전체를 종합 보고합니다
    </div>
  </div>
</div>""", unsafe_allow_html=True)

    def _emp(emp_key, name, fn, panel_fn=None):
        return OfficeEmployee(emp_key, name, _office_avatar(name), fn, panel_fn)

    def _analyst_roster():
        return office_analyst_employees() or []

    _rooms = [
        OfficeRoom('analyst', 'AI 애널리스트팀', '📈', _analyst_roster, render_stock_analysis_panel),
        OfficeRoom('ops', '자동매매 운영팀', '⚙️', [
            _emp('exec', '실행 모드', execution_mode_employee, render_execution_mode_panel),
            _emp('sig', '시그널 파이프라인', signal_pipeline_employee),
            _emp('risk', '리스크 가드레일', risk_guardrail_employee, render_risk_guardrail_panel),
            _emp('eq', '계좌 현황', equity_log_employee),
        ]),
        OfficeRoom('siggen', '시그널 생성팀', '📡', [
            _emp('rank', '팩터 랭킹', factor_ranking_employee, render_factor_ranking_panel),
            _emp('sys', '시스템 시그널', system_signal_employee, render_system_signal_panel),
            _emp('sector', '섹터 로테이션', sector_rotation_employee, render_sector_rotation_panel),
        ], render_universe_picker),
        OfficeRoom('ml', 'ML 시그널팀', '🧠', [
            _emp('ml', 'ML 신호', ml_signal_employee, render_ml_signal_panel),
        ]),
        OfficeRoom('bt', '백테스트 검증팀', '📉', [
            _emp('factor_bt', '팩터 백테스트', factor_backtest_employee, render_factor_backtest_panel),
            _emp('stock_bt', '종목 백테스팅', stock_backtest_employee, render_stock_backtest_panel),
        ]),
        OfficeRoom('qa', '퀀트 리서치/QA팀', '🔬', [
            _emp('adv', '고급 분석', advanced_research_employee, render_advanced_research_panel),
        ]),
    ]

    # ── 게임 씬: 캐릭터가 실제로 걸어다니는 사무실 평면 ──────
    # 사무실이 유일한 내비게이션 — 캐릭터 클릭 → (room, emp) 반환 → 바로 아래에
    # 보고서·업무 화면이 열린다. 같은 클릭이 이후 선택을 계속 덮어쓰지 않도록
    # nonce 변화가 있을 때만 반영한다. 컴포넌트 로드 실패 시에만 기존 방 UI로 폴백.
    if _OFFICE_GAME_AVAILABLE:
        inject_office_css()   # 게임 모드에선 render_office_rooms를 안 거치므로 여기서 직접 주입
        _game_rooms = collect_office_game_data(_rooms)
        _sel_now = st.session_state.get('office_view')
        # 작업 모드: 분석이 예약돼 있으면(pending) 게임 속 전 직원이 자리로 뛰어가 일한다.
        # 이 rerun에서 아래 시세판 패널이 실제 분석(블로킹)을 실행하는 동안에도
        # iframe 애니메이션은 클라이언트에서 독립적으로 계속 돈다.
        _pending_tk = st.session_state.get('pending_analysis')
        _snap_board = st.session_state.get('office_analyst_snapshot')
        _result_arg = None
        if _snap_board:
            _mb = _snap_board['manager']
            _result_arg = {'ticker': _snap_board['ticker'],
                           'score': round(_mb['total_score'], 1),
                           'verdict': _mb['verdict'],
                           'color': score_color(_mb['total_score'])}
        _clicked = _office_game_component(
            rooms=_game_rooms, dark=_dark_mode,
            selected=(f"{_sel_now[0]}|{_sel_now[1]}" if _sel_now else None),
            working=bool(_pending_tk), ticker=_pending_tk, result=_result_arg,
            key="office_game_scene", default=None)
        if _clicked and _clicked.get('nonce') != st.session_state.get('_office_game_nonce'):
            st.session_state['_office_game_nonce'] = _clicked['nonce']
            if _clicked.get('boss'):
                st.session_state['office_view'] = ('boss', 'summary')
            else:
                st.session_state['office_view'] = (_clicked['room'], _clicked['emp'])
        st.caption("🖱️ 캐릭터를 클릭하면 바로 아래에 보고서와 업무 화면이 열립니다 · "
                   "상태에 따라 일하고(타이핑) · 산책하고(🚰) · 졸고(💤) · 뛰어다니는(❗) 모습이 달라집니다")

        # ── 클릭한 직원의 보고서 + 업무 화면 ──────────────
        _sel = st.session_state.get('office_view')
        with st.container(border=True, key="office_selected_view"):
            _sel_room = next((r for r in _rooms if _sel and r.key == _sel[0]), None)
            _sel_emps = (_sel_room.employees() if callable(_sel_room.employees)
                         else _sel_room.employees) if _sel_room else []
            _sel_emp = next((e for e in (_sel_emps or []) if e.key == _sel[1]), None) if _sel else None
            if _sel == ('boss', 'summary'):
                st.markdown(
                    "<div style='font-size:12px;font-weight:700;color:var(--text-3);margin-bottom:2px'>"
                    "🤵 사장실 &nbsp;›&nbsp; 전사 종합 보고</div>", unsafe_allow_html=True)
                render_company_summary()
            elif _sel_emp is None:
                st.caption("👆 사무실에서 직원(또는 사장님) 캐릭터를 클릭하면 이 자리에 보고서가 열립니다.")
            else:
                st.markdown(
                    f"<div style='font-size:12px;font-weight:700;color:var(--text-3);margin-bottom:2px'>"
                    f"{_sel_room.icon} {_sel_room.name} &nbsp;›&nbsp; "
                    f"{_sel_emp.avatar} {_sel_emp.name}</div>", unsafe_allow_html=True)
                # 시그널 생성팀·백테스트 검증팀 직원은 종목 유니버스 입력이 선행돼야 한다
                if _sel_room.key in ('siggen', 'bt'):
                    render_universe_picker()
                render_office_report_card(_sel_emp.report_fn())
                if _sel_emp.panel_fn is not None:
                    st.markdown("")
                    _sel_emp.panel_fn()

        # ── 시세판 · 종목 분석 (항상 표시 — 여기서 분석해야 애널리스트팀이 출근한다) ──
        st.markdown("""
<div style="display:inline-flex;align-items:center;gap:9px;margin:18px 0 8px 0;
            background:linear-gradient(180deg,#8b6f3e 0%,#6f5730 100%);
            border-radius:6px 6px 3px 3px;padding:7px 16px 7px 12px;
            box-shadow:0 3px 6px rgba(0,0,0,.18),inset 0 1px 0 rgba(255,255,255,.15)">
  <span style="font-size:17px">📈</span>
  <span style="font-size:12.5px;font-weight:800;color:#fdf6e3;letter-spacing:.4px;
               text-transform:uppercase">AI 애널리스트팀 · 시세판</span>
</div>""", unsafe_allow_html=True)
        with st.container(border=True, key="office_room_analyst_board"):
            render_stock_analysis_panel()
    else:
        render_office_rooms(_rooms)

    # ── 총괄 트레이더 최종 보고 ──────────────
    st.markdown("---")
    render_company_summary()

    # 분석이 방금 끝났으면 한 번 더 rerun — 게임 씬 상단이 이번 rerun에선 아직
    # working=True로 그려져 있으므로, 결과가 저장된 상태에서 다시 그려 대기 모드로 되돌린다.
    if st.session_state.pop('_analysis_done_flip', False):
        st.rerun()


if __name__ == "__main__":
    main()
