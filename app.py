import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

st.set_page_config(
    page_title="종합 주식 분석 시스템",
    page_icon="📊",
    layout="wide"
)

# ─────────────────────────────────────────────
# TECHNICAL ANALYSIS
# ─────────────────────────────────────────────

def calc_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(prices, fast=12, slow=26, signal=9):
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig

def calc_bb(prices, period=20, k=2):
    sma = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    return sma + k * std, sma, sma - k * std

def wave_score(prices, highs, lows):
    """추세/모멘텀 기반 파동 근사값"""
    if len(prices) < 60:
        return 50

    recent_slope = (prices.iloc[-1] - prices.iloc[-20]) / (prices.iloc[-20] + 1e-9)
    medium_slope = (prices.iloc[-1] - prices.iloc[-60]) / (prices.iloc[-60] + 1e-9)

    rh = highs.tail(20).max()
    ph = highs.iloc[-40:-20].max() if len(highs) >= 40 else highs.mean()
    rl = lows.tail(20).min()
    pl = lows.iloc[-40:-20].min() if len(lows) >= 40 else lows.mean()

    hh = 1 if rh > ph else 0  # Higher High
    hl = 1 if rl > pl else 0  # Higher Low

    base = 50
    base += (hh + hl) * 15
    momentum = np.clip(recent_slope * 150 + medium_slope * 80, -40, 40)
    return float(np.clip(base + momentum, 0, 100))


def technical_score(df):
    p = df['Close']
    h = df['High']
    l = df['Low']
    v = df['Volume']

    details = {}

    # MA 정렬 (20%)
    ma20 = p.rolling(20).mean()
    ma60 = p.rolling(60).mean()
    ma120 = p.rolling(120).mean()
    cp, m20, m60, m120 = p.iloc[-1], ma20.iloc[-1], ma60.iloc[-1], ma120.iloc[-1]

    ma = 0
    if cp > m20:   ma += 20
    if cp > m60:   ma += 20
    if cp > m120:  ma += 20
    if m20 > m60:  ma += 20
    if m60 > m120: ma += 20
    # 골든/데스크로스 보정
    if len(ma20) >= 5:
        if m20 > m60 and ma20.iloc[-5] <= ma60.iloc[-5]:
            ma = min(ma + 15, 100)
        elif m20 < m60 and ma20.iloc[-5] >= ma60.iloc[-5]:
            ma = max(ma - 15, 0)
    details['MA정렬'] = float(ma)

    # RSI (15%)
    rsi_s = calc_rsi(p)
    rv = float(rsi_s.iloc[-1])
    if 40 <= rv <= 60:    rsi = 50
    elif 60 < rv <= 70:   rsi = 75
    elif rv > 70:         rsi = 40
    elif 30 <= rv < 40:   rsi = 30
    else:                 rsi = 60
    rsi_trend = rv - float(rsi_s.iloc[-10]) if len(rsi_s) >= 10 else 0
    if rsi_trend > 5 and 40 < rv < 70:  rsi = min(rsi + 20, 100)
    elif rsi_trend < -5:                 rsi = max(rsi - 10, 0)
    details['RSI'] = float(rsi)
    details['RSI값'] = round(rv, 1)

    # MACD (20%)
    macd_l, sig_l, hist = calc_macd(p)
    ch, ph2 = float(hist.iloc[-1]), float(hist.iloc[-2]) if len(hist) >= 2 else 0
    macd = 50
    if float(macd_l.iloc[-1]) > float(sig_l.iloc[-1]): macd += 20
    if ch > 0:    macd += 15
    if ch > ph2:  macd += 15
    else:          macd -= 10
    macd = float(np.clip(macd, 0, 100))
    details['MACD'] = macd

    # 볼린저밴드 (15%)
    bb_u, _, bb_l = calc_bb(p)
    band_range = float(bb_u.iloc[-1]) - float(bb_l.iloc[-1])
    pos = (float(cp) - float(bb_l.iloc[-1])) / (band_range + 1e-9)
    if 0.4 <= pos <= 0.8:     bb = 70
    elif 0.8 < pos <= 0.95:   bb = 85
    elif pos > 0.95:           bb = 45
    elif 0.2 <= pos < 0.4:    bb = 45
    else:                      bb = 30
    details['볼린저밴드'] = float(bb)

    # 거래량 (15%)
    avg_vol = v.rolling(20).mean()
    vol_ratio = float(v.iloc[-1]) / (float(avg_vol.iloc[-1]) + 1e-9)
    pchg = (float(p.iloc[-1]) - float(p.iloc[-5])) / (float(p.iloc[-5]) + 1e-9)
    if pchg > 0 and vol_ratio > 1.2:   vol = 80
    elif pchg > 0 and vol_ratio < 0.8: vol = 55
    elif pchg < 0 and vol_ratio > 1.2: vol = 25
    elif pchg < 0 and vol_ratio < 0.8: vol = 45
    else:                               vol = 50
    details['거래량'] = float(vol)

    # 파동 근사 (15%)
    ws = wave_score(p, h, l)
    details['파동근사'] = ws

    total = (
        details['MA정렬']    * 0.20 +
        details['RSI']       * 0.15 +
        details['MACD']      * 0.20 +
        details['볼린저밴드'] * 0.15 +
        details['거래량']    * 0.15 +
        details['파동근사']  * 0.15
    )
    return float(total), details


# ─────────────────────────────────────────────
# FUNDAMENTAL ANALYSIS
# ─────────────────────────────────────────────

def _score_per(v):
    if not v or np.isnan(v) or v <= 0: return 50
    if v < 5:    return 70
    if v < 15:   return 85
    if v < 25:   return 65
    if v < 40:   return 40
    return 20

def _score_pbr(v):
    if not v or np.isnan(v) or v <= 0: return 50
    if v < 1:   return 85
    if v < 2:   return 75
    if v < 4:   return 55
    if v < 8:   return 35
    return 20

def _score_roe(v):
    if v is None or np.isnan(v): return 50
    r = v * 100 if abs(v) <= 1 else v
    if r < 0:   return 10
    if r < 5:   return 30
    if r < 10:  return 50
    if r < 20:  return 75
    if r < 30:  return 90
    return 85

def _score_growth(v):
    if v is None or np.isnan(v): return 50
    if v < -0.10: return 10
    if v < 0:     return 30
    if v < 0.05:  return 50
    if v < 0.15:  return 70
    if v < 0.30:  return 85
    return 95

def _score_de(v):
    if v is None or np.isnan(v): return 50
    if v < 0:   return 30
    if v < 0.3: return 90
    if v < 0.7: return 75
    if v < 1.5: return 55
    if v < 3.0: return 35
    return 15


def fundamental_score(ticker):
    try:
        info = yf.Ticker(ticker).info
        details = {}

        per = info.get('trailingPE') or info.get('forwardPE')
        pbr = info.get('priceToBook')
        val = _score_per(per) * 0.6 + _score_pbr(pbr) * 0.4
        details['밸류에이션'] = val
        details['PER'] = per
        details['PBR'] = pbr

        roe = info.get('returnOnEquity')
        roa = info.get('returnOnAssets')
        pm  = info.get('profitMargins')
        pm_score = 50
        if pm:
            pm_pct = pm * 100
            if pm_pct < 0:   pm_score = 10
            elif pm_pct < 5: pm_score = 40
            elif pm_pct < 10: pm_score = 60
            elif pm_pct < 20: pm_score = 80
            else:             pm_score = 90
        qual = _score_roe(roe) * 0.4 + _score_roe(roa * 3 if roa else None) * 0.3 + pm_score * 0.3
        details['수익성'] = qual
        details['ROE'] = roe
        details['ROA'] = roa

        rg = info.get('revenueGrowth')
        eg = info.get('earningsGrowth')
        grow = _score_growth(rg) * 0.5 + _score_growth(eg) * 0.5 if eg else _score_growth(rg)
        details['성장성'] = grow
        details['매출성장'] = rg
        details['EPS성장'] = eg

        de = info.get('debtToEquity')
        cr = info.get('currentRatio')
        de_score = _score_de(de / 100 if de else None)
        cr_score = 50
        if cr:
            if cr < 0.5:    cr_score = 10
            elif cr < 1.0:  cr_score = 30
            elif cr < 1.5:  cr_score = 60
            elif cr < 3.0:  cr_score = 85
            else:           cr_score = 75
        safe = de_score * 0.6 + cr_score * 0.4
        details['안전성'] = safe

        total = (
            details['밸류에이션'] * 0.30 +
            details['수익성']     * 0.30 +
            details['성장성']     * 0.25 +
            details['안전성']     * 0.15
        )
        return float(total), details
    except Exception as e:
        return 50.0, {'오류': str(e)}


# ─────────────────────────────────────────────
# MACRO & INTEREST RATE
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600)
def macro_score():
    details = {}
    data = {}
    end = datetime.now()
    start = end - timedelta(days=400)

    try:
        tnx = yf.download('^TNX', start=start, end=end, progress=False)
        fvx = yf.download('^FVX', start=start, end=end, progress=False)
        vix = yf.download('^VIX', start=start, end=end, progress=False)
        dxy = yf.download('DX-Y.NYB', start=start, end=end, progress=False)

        # MultiIndex 처리
        for df in [tnx, fvx, vix, dxy]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

        # 금리 환경 (35%)
        if len(tnx) >= 60:
            cur_r = float(tnx['Close'].iloc[-1])
            r3m   = float(tnx['Close'].iloc[-60])
            chg   = cur_r - r3m
            data['10Y금리'] = cur_r
            data['3M금리변화'] = chg

            if cur_r < 2:   lvl = 85
            elif cur_r < 3: lvl = 75
            elif cur_r < 4: lvl = 60
            elif cur_r < 5: lvl = 45
            else:            lvl = 30

            if chg < -0.3:   trend = 80
            elif chg < 0:    trend = 65
            elif chg < 0.3:  trend = 50
            elif chg < 0.8:  trend = 35
            else:             trend = 20

            details['금리환경'] = lvl * 0.4 + trend * 0.6
        else:
            details['금리환경'] = 50.0

        # 장단기 금리차 (25%)
        if len(tnx) > 0 and len(fvx) > 0:
            spread = float(tnx['Close'].iloc[-1]) - float(fvx['Close'].iloc[-1])
            data['장단기스프레드'] = spread
            if spread > 1.5:    details['장단기금리차'] = 80.0
            elif spread > 0.5:  details['장단기금리차'] = 70.0
            elif spread >= 0:   details['장단기금리차'] = 50.0
            elif spread > -0.5: details['장단기금리차'] = 35.0
            else:                details['장단기금리차'] = 20.0
        else:
            details['장단기금리차'] = 50.0

        # VIX (25%)
        if len(vix) >= 20:
            cv = float(vix['Close'].iloc[-1])
            av = float(vix['Close'].tail(30).mean())
            data['VIX'] = cv
            if cv < 15:    vs = 75
            elif cv < 20:  vs = 70
            elif cv < 25:  vs = 55
            elif cv < 35:  vs = 35
            else:           vs = 20
            if cv - av < -3:   vs = min(vs + 15, 100)
            elif cv - av > 5:  vs = max(vs - 15, 0)
            details['VIX'] = float(vs)
        else:
            details['VIX'] = 50.0

        # 달러 (15%)
        if len(dxy) >= 60:
            cd  = float(dxy['Close'].iloc[-1])
            d3m = float(dxy['Close'].iloc[-60])
            chg_pct = (cd - d3m) / d3m * 100
            data['DXY'] = cd
            if chg_pct < -3:   ds = 70
            elif chg_pct < 0:  ds = 60
            elif chg_pct < 3:  ds = 50
            elif chg_pct < 6:  ds = 40
            else:               ds = 30
            details['달러지수'] = float(ds)
        else:
            details['달러지수'] = 50.0

        total = (
            details['금리환경']    * 0.35 +
            details['장단기금리차'] * 0.25 +
            details['VIX']         * 0.25 +
            details['달러지수']    * 0.15
        )
        return float(total), details, data

    except Exception as e:
        return 50.0, {'오류': str(e)}, {}


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def score_color(s):
    if s >= 70: return '#00C851'
    if s >= 50: return '#FF8800'
    return '#FF4444'

def score_label(s):
    if s >= 80: return '강한 매수 🚀'
    if s >= 65: return '매수 📈'
    if s >= 50: return '중립 ➡️'
    if s >= 35: return '매도 📉'
    return '강한 매도 ⚠️'

def gauge(value, title):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        title={'text': title, 'font': {'size': 13}},
        number={'font': {'size': 36, 'color': score_color(value)}},
        gauge={
            'axis': {'range': [0, 100], 'tickwidth': 1},
            'bar': {'color': score_color(value), 'thickness': 0.3},
            'steps': [
                {'range': [0,  35], 'color': '#FFEBEE'},
                {'range': [35, 65], 'color': '#FFF8E1'},
                {'range': [65,100], 'color': '#E8F5E9'},
            ],
            'threshold': {'line': {'color': '#555', 'width': 2}, 'thickness': 0.75, 'value': 50}
        }
    ))
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=50, b=10))
    return fig

def fmt(v, pct=False, mul=100):
    if v is None: return 'N/A'
    try:
        f = float(v)
        if np.isnan(f): return 'N/A'
        return f'{f*mul:.1f}%' if pct else f'{f:.2f}'
    except:
        return 'N/A'


# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────

def main():
    st.title("📊 종합 주식 분석 시스템")
    st.caption("차트+파동 · 재무제표+퀀트 · 매크로+금리 종합 점수")

    # ── Sidebar ──────────────────────────────
    with st.sidebar:
        st.header("⚙️ 설정")
        market = st.selectbox("시장", ["미국 (NYSE/NASDAQ)", "한국 (KRX)", "ETF/인덱스"])

        if market == "한국 (KRX)":
            ticker_raw = st.text_input("종목코드", placeholder="005930 (삼성전자)")
            suffix = st.radio("거래소", [".KS (KOSPI)", ".KQ (KOSDAQ)"], horizontal=True)
            sfx = ".KS" if "KS" in suffix else ".KQ"
            ticker = (ticker_raw.strip() + sfx).upper() if ticker_raw else ""
            st.caption("예: 005930=삼성전자, 000660=SK하이닉스, 035420=NAVER")
        elif market == "미국 (NYSE/NASDAQ)":
            ticker = st.text_input("티커", placeholder="AAPL").strip().upper()
            st.caption("예: AAPL MSFT NVDA TSLA AMZN GOOGL META")
        else:
            ticker = st.text_input("ETF 티커", placeholder="SPY").strip().upper()
            st.caption("예: SPY QQQ IWM TLT GLD 069500.KS")

        st.divider()
        st.subheader("가중치 조정")
        w_tech  = st.slider("📈 차트+파동 (%)",       0, 100, 35, 5)
        w_fund  = st.slider("💰 재무제표+퀀트 (%)",   0, 100, 40, 5)
        w_macro = st.slider("🌍 매크로+금리 (%)",     0, 100, 25, 5)
        total_w = w_tech + w_fund + w_macro

        if total_w == 100:
            st.success(f"가중치 합계: {total_w}%  ✅")
        else:
            st.error(f"가중치 합계: {total_w}% (100%로 맞춰주세요)")

        run = st.button("📊 분석 시작", type="primary", use_container_width=True,
                        disabled=(total_w != 100 or not ticker))

    # ── Welcome ───────────────────────────────
    if not run:
        st.markdown("""
        ### 사용 방법
        1. 왼쪽에서 **시장 선택** → **티커 입력** → **분석 시작**
        2. 가중치를 조정해 나만의 투자 기준에 맞게 설정하세요

        | 점수 | 해석 |
        |:----:|:----:|
        | 80~100 | 강한 매수 🚀 |
        | 65~80  | 매수 📈 |
        | 50~65  | 중립 ➡️ |
        | 35~50  | 매도 📉 |
        | 0~35   | 강한 매도 ⚠️ |

        ---
        **지표 구성**
        - **차트+파동**: MA정렬, RSI, MACD, 볼린저밴드, 거래량, 추세/모멘텀 파동근사
        - **재무제표+퀀트**: PER/PBR, ROE/ROA, 매출·EPS 성장률, 부채비율/유동비율
        - **매크로+금리**: 10Y 금리 수준·추세, 장단기금리차, VIX 공포지수, 달러지수
        """)
        return

    # ── Analysis ──────────────────────────────
    prog = st.progress(0)
    msg  = st.empty()

    msg.text("📥 가격 데이터 다운로드 중...")
    prog.progress(5)

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=520)
    df = yf.download(ticker, start=start_dt, end=end_dt, progress=False)

    if df.empty:
        st.error(f"'{ticker}' 데이터를 찾을 수 없습니다. 티커를 확인해주세요.")
        prog.empty(); msg.empty()
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df = df.dropna(subset=['Close'])

    if len(df) < 30:
        st.error("데이터가 너무 부족합니다 (30일 미만). 다른 티커를 시도해주세요.")
        prog.empty(); msg.empty()
        return

    prog.progress(20)
    msg.text("📈 차트·파동 분석 중...")
    t_score, t_detail = technical_score(df)

    prog.progress(45)
    msg.text("💰 재무제표·퀀트 분석 중...")
    f_score, f_detail = fundamental_score(ticker)

    prog.progress(65)
    msg.text("🌍 매크로·금리 환경 분석 중...")
    m_score, m_detail, m_data = macro_score()

    prog.progress(90)
    total = t_score * (w_tech/100) + f_score * (w_fund/100) + m_score * (w_macro/100)

    prog.progress(100)
    prog.empty(); msg.empty()

    # ── Header ────────────────────────────────
    try:
        info = yf.Ticker(ticker).info
        name = info.get('longName') or info.get('shortName') or ticker
    except:
        info, name = {}, ticker

    st.header(f"{name}  `{ticker}`")

    cp  = float(df['Close'].iloc[-1])
    pp  = float(df['Close'].iloc[-2]) if len(df) >= 2 else cp
    chg = (cp - pp) / pp * 100
    is_krw = ticker.endswith('.KS') or ticker.endswith('.KQ')

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("현재가", f"₩{cp:,.0f}" if is_krw else f"${cp:.2f}", f"{chg:+.2f}%")
    c2.metric("52주 고가", f"₩{df['High'].tail(252).max():,.0f}" if is_krw else f"${df['High'].tail(252).max():.2f}")
    c3.metric("52주 저가", f"₩{df['Low'].tail(252).min():,.0f}"  if is_krw else f"${df['Low'].tail(252).min():.2f}")
    c4.metric("분석 기준일", end_dt.strftime("%Y-%m-%d"))

    st.divider()

    # ── Total Score ───────────────────────────
    col_g, col_v = st.columns([1, 1])
    with col_g:
        st.plotly_chart(gauge(total, "종합 점수"), use_container_width=True)
    with col_v:
        st.markdown(f"""
        <div style='text-align:center; padding:20px 0'>
            <div style='font-size:72px; font-weight:bold; color:{score_color(total)}'>{total:.1f}</div>
            <div style='font-size:28px; color:{score_color(total)}'>{score_label(total)}</div>
            <br>
            <div style='color:#888; font-size:15px'>
                차트+파동 <b>{t_score:.0f}</b>점 &nbsp;|&nbsp;
                재무+퀀트 <b>{f_score:.0f}</b>점 &nbsp;|&nbsp;
                매크로+금리 <b>{m_score:.0f}</b>점
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # ── Category Scores ───────────────────────
    st.subheader("카테고리별 점수")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.plotly_chart(gauge(t_score, f"📈 차트+파동  (가중 {w_tech}%)"), use_container_width=True)
        with st.expander("세부 점수 보기"):
            for k, v in t_detail.items():
                if k != 'RSI값':
                    extra = f"  *(RSI현재값: {t_detail['RSI값']})*" if k == 'RSI' else ''
                    bar = '█' * int(v / 10) + '░' * (10 - int(v / 10))
                    st.markdown(f"**{k}** {bar} `{v:.0f}점`{extra}")

    with col2:
        st.plotly_chart(gauge(f_score, f"💰 재무제표+퀀트  (가중 {w_fund}%)"), use_container_width=True)
        with st.expander("세부 점수 보기"):
            for k in ['밸류에이션', '수익성', '성장성', '안전성']:
                v = f_detail.get(k, 50)
                bar = '█' * int(v / 10) + '░' * (10 - int(v / 10))
                st.markdown(f"**{k}** {bar} `{v:.0f}점`")
            st.divider()
            st.caption(f"PER: {fmt(f_detail.get('PER'))}  |  PBR: {fmt(f_detail.get('PBR'))}")
            st.caption(f"ROE: {fmt(f_detail.get('ROE'), pct=True)}  |  ROA: {fmt(f_detail.get('ROA'), pct=True)}")
            st.caption(f"매출성장: {fmt(f_detail.get('매출성장'), pct=True)}  |  EPS성장: {fmt(f_detail.get('EPS성장'), pct=True)}")

    with col3:
        st.plotly_chart(gauge(m_score, f"🌍 매크로+금리  (가중 {w_macro}%)"), use_container_width=True)
        with st.expander("세부 점수 보기"):
            for k in ['금리환경', '장단기금리차', 'VIX', '달러지수']:
                v = m_detail.get(k, 50)
                bar = '█' * int(v / 10) + '░' * (10 - int(v / 10))
                st.markdown(f"**{k}** {bar} `{v:.0f}점`")
            st.divider()
            if '10Y금리' in m_data:
                st.caption(f"10Y 금리: {m_data['10Y금리']:.2f}%  |  3M변화: {m_data.get('3M금리변화', 0):+.2f}%p")
            if '장단기스프레드' in m_data:
                st.caption(f"장단기 스프레드: {m_data['장단기스프레드']:.2f}%p")
            if 'VIX' in m_data:
                st.caption(f"VIX: {m_data['VIX']:.1f}  |  DXY: {m_data.get('DXY', 'N/A')}")

    st.divider()

    # ── Price Chart ───────────────────────────
    st.subheader("📈 차트 (캔들·MA·볼린저밴드·MACD·RSI)")

    p  = df['Close']
    m20 = p.rolling(20).mean()
    m60 = p.rolling(60).mean()
    m120 = p.rolling(120).mean()
    bb_u, bb_m, bb_l = calc_bb(p)
    macd_l, sig_l, hist = calc_macd(p)
    rsi_s = calc_rsi(p)

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.55, 0.22, 0.23],
        vertical_spacing=0.04,
        subplot_titles=['가격', 'MACD', 'RSI']
    )

    # 캔들
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        name='캔들', increasing_line_color='#ef5350', decreasing_line_color='#26a69a'
    ), row=1, col=1)

    for ma, color, name in [(m20,'#2196F3','MA20'), (m60,'#FF9800','MA60'), (m120,'#F44336','MA120')]:
        fig.add_trace(go.Scatter(x=df.index, y=ma, name=name, line=dict(color=color, width=1.2)), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=bb_u, name='BB상단', line=dict(color='#9E9E9E', dash='dot', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb_l, name='BB하단', line=dict(color='#9E9E9E', dash='dot', width=1),
                             fill='tonexty', fillcolor='rgba(158,158,158,0.08)'), row=1, col=1)

    # MACD
    hcolors = ['#ef5350' if v >= 0 else '#26a69a' for v in hist]
    fig.add_trace(go.Bar(x=df.index, y=hist, name='히스토그램', marker_color=hcolors, showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=macd_l, name='MACD', line=dict(color='#2196F3', width=1.2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig_l,  name='시그널', line=dict(color='#FF9800', width=1.2)), row=2, col=1)

    # RSI
    fig.add_trace(go.Scatter(x=df.index, y=rsi_s, name='RSI', line=dict(color='#9C27B0', width=1.5)), row=3, col=1)
    fig.add_hline(y=70, line_dash='dash', line_color='#ef5350', line_width=1, row=3, col=1)
    fig.add_hline(y=30, line_dash='dash', line_color='#26a69a', line_width=1, row=3, col=1)

    fig.update_layout(
        height=680, xaxis_rangeslider_visible=False,
        plot_bgcolor='#0E1117', paper_bgcolor='#0E1117',
        font=dict(color='#FAFAFA'),
        legend=dict(orientation='h', y=1.02, x=0)
    )
    fig.update_xaxes(gridcolor='#1E2130', showgrid=True)
    fig.update_yaxes(gridcolor='#1E2130', showgrid=True)

    st.plotly_chart(fig, use_container_width=True)

    # ── Summary Table ─────────────────────────
    st.subheader("📋 분석 요약")
    rows = [
        {'카테고리': '종합 점수',     '점수': f"{total:.1f}", '등급': score_label(total),   '가중치': '100%'},
        {'카테고리': '📈 차트+파동',  '점수': f"{t_score:.1f}", '등급': score_label(t_score),  '가중치': f'{w_tech}%'},
        {'카테고리': '💰 재무제표+퀀트','점수': f"{f_score:.1f}", '등급': score_label(f_score), '가중치': f'{w_fund}%'},
        {'카테고리': '🌍 매크로+금리', '점수': f"{m_score:.1f}", '등급': score_label(m_score), '가중치': f'{w_macro}%'},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.caption("⚠️ 본 분석은 투자 참고용이며 투자 결정의 책임은 본인에게 있습니다.")


if __name__ == "__main__":
    main()
