import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import requests
import warnings
warnings.filterwarnings('ignore')

st.set_page_config(page_title="종합 주식 분석 시스템", page_icon="📊", layout="wide")

TV_BG = TV_PAPER = '#131722'
TV_GRID = '#1e2334'
TV_BORDER = '#2a2e39'
TV_TEXT = '#b2b5be'
TV_UP = '#26a69a'
TV_DOWN = '#ef5350'

PRESETS = {
    '미국 대형주':  ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','JPM','V','JNJ'],
    '한국 대형주':  ['005930.KS','000660.KS','035420.KS','005380.KS','051910.KS','006400.KS'],
    '반도체':       ['NVDA','AMD','INTC','TSM','ASML','QCOM','000660.KS','005930.KS'],
    'ETF':          ['SPY','QQQ','IWM','GLD','TLT','EEM','069500.KS','114800.KS'],
}

SECTOR_AVG_PER = {
    'Technology': 28, 'Consumer Cyclical': 22, 'Financial Services': 13,
    'Healthcare': 20, 'Consumer Defensive': 19, 'Communication Services': 20,
    'Industrials': 18, 'Basic Materials': 14, 'Energy': 11,
    'Real Estate': 30, 'Utilities': 17,
}

# ─────────────────────────────────────────────
# TECHNICAL ANALYSIS
# ─────────────────────────────────────────────

def calc_rsi(prices, period=14):
    delta = prices.diff()
    gain  = delta.where(delta > 0, 0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss))

def calc_macd(prices, fast=12, slow=26, signal=9):
    ema_f = prices.ewm(span=fast,   adjust=False).mean()
    ema_s = prices.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig

def calc_bb(prices, period=20, k=2):
    sma = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    return sma + k*std, sma, sma - k*std

def calc_stochastic(high, low, close, k=14, d=3):
    lo = low.rolling(k).min()
    hi = high.rolling(k).max()
    pct_k = (close - lo) / (hi - lo + 1e-9) * 100
    return pct_k, pct_k.rolling(d).mean()

def calc_adx(high, low, close, period=14):
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
    sign = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (volume * sign).cumsum()

def detect_trading_signals(df, t_det):
    """현재 시점 매매 시그널 감지 (RSI/MA크로스/BB/MACD/거래량/스토캐스틱)"""
    p = df['Close']
    cp = float(p.iloc[-1])
    signals = []
    rsi_v = t_det.get('RSI값', 50)
    if rsi_v < 30:   signals.append(('🟢', 'RSI 과매도',     f'RSI {rsi_v:.1f} — 반등 가능'))
    elif rsi_v > 70: signals.append(('🔴', 'RSI 과매수',     f'RSI {rsi_v:.1f} — 조정 주의'))
    ma20_s = p.rolling(20).mean(); ma60_s = p.rolling(60).mean()
    if len(p) >= 22:
        if float(ma20_s.iloc[-2]) <= float(ma60_s.iloc[-2]) and float(ma20_s.iloc[-1]) > float(ma60_s.iloc[-1]):
            signals.append(('🟢', '골든크로스',    'MA20 ↑ MA60 돌파 — 중기 매수 신호'))
        elif float(ma20_s.iloc[-2]) >= float(ma60_s.iloc[-2]) and float(ma20_s.iloc[-1]) < float(ma60_s.iloc[-1]):
            signals.append(('🔴', '데드크로스',    'MA20 ↓ MA60 이탈 — 중기 매도 신호'))
    bb_u_s, _, bb_l_s = calc_bb(p)
    if cp > float(bb_u_s.iloc[-1]):   signals.append(('🔴', 'BB 상단 돌파',  '과매수 구간 — 단기 조정 주의'))
    elif cp < float(bb_l_s.iloc[-1]): signals.append(('🟢', 'BB 하단 이탈', '과매도 구간 — 반등 대기'))
    elif len(p) >= 40:
        bw_n = (float(bb_u_s.iloc[-1]) - float(bb_l_s.iloc[-1])) / (cp + 1e-9)
        bw_a = float(((bb_u_s - bb_l_s) / p).rolling(20).mean().iloc[-1])
        if bw_n < bw_a * 0.7: signals.append(('🟡', 'BB 스퀴즈', '밴드 수축 — 큰 방향성 돌파 임박'))
    ml_s, sl_s, _ = calc_macd(p)
    if len(ml_s) >= 2:
        if float(ml_s.iloc[-2]) <= float(sl_s.iloc[-2]) and float(ml_s.iloc[-1]) > float(sl_s.iloc[-1]):
            signals.append(('🟢', 'MACD 상향 돌파', 'MACD > Signal — 단기 매수 신호'))
        elif float(ml_s.iloc[-2]) >= float(sl_s.iloc[-2]) and float(ml_s.iloc[-1]) < float(sl_s.iloc[-1]):
            signals.append(('🔴', 'MACD 하향 돌파', 'MACD < Signal — 단기 매도 신호'))
    vr = float(df['Volume'].iloc[-1]) / (float(df['Volume'].rolling(20).mean().iloc[-1]) + 1e-9)
    if vr > 2.0:
        dir_s = '상승' if cp > float(p.iloc[-2]) else '하락'
        signals.append(('⚡', '거래량 급증', f'평균의 {vr:.1f}배 ({dir_s}) — 방향성 강화'))
    sk_v = t_det.get('Stoch값', 50)
    if sk_v < 20:   signals.append(('🟢', '스토캐스틱 과매도', f'%K {sk_v:.1f} — 반등 구간'))
    elif sk_v > 80: signals.append(('🔴', '스토캐스틱 과매수', f'%K {sk_v:.1f} — 과열 구간'))
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

    # ── MA 정렬 (15%) ─────────────────────────
    ma20  = p.rolling(20).mean()
    ma60  = p.rolling(60).mean()
    ma120 = p.rolling(120).mean()
    cp, m20, m60, m120 = float(p.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma120.iloc[-1])
    ma = (20 if cp>m20 else 0)+(20 if cp>m60 else 0)+(20 if cp>m120 else 0)+(20 if m20>m60 else 0)+(20 if m60>m120 else 0)
    if len(ma20) >= 5:
        if m20 > m60 and float(ma20.iloc[-5]) <= float(ma60.iloc[-5]):  ma = min(ma+15, 100)
        elif m20 < m60 and float(ma20.iloc[-5]) >= float(ma60.iloc[-5]): ma = max(ma-15, 0)
    det['MA정렬'] = float(ma)

    # ── RSI (10%) ──────────────────────────────
    rsi_s = calc_rsi(p)
    rv = float(rsi_s.iloc[-1])
    if 40 <= rv <= 60:   rsi = 50
    elif 60 < rv <= 70:  rsi = 75
    elif rv > 70:        rsi = 40
    elif 30 <= rv < 40:  rsi = 30
    else:                rsi = 60
    rsi_tr = rv - float(rsi_s.iloc[-10]) if len(rsi_s) >= 10 else 0
    if rsi_tr > 5 and 40 < rv < 70:  rsi = min(rsi+20, 100)
    elif rsi_tr < -5:                  rsi = max(rsi-10, 0)
    # RSI 다이버전스 감지
    if len(p) >= 20:
        pr20 = float(p.iloc[-20]); rs20 = float(rsi_s.iloc[-20]) if len(rsi_s) >= 20 else rv
        if cp < pr20 and rv > rs20:   rsi = min(rsi+15, 100)   # 상승 다이버전스
        elif cp > pr20 and rv < rs20: rsi = max(rsi-15, 0)      # 하락 다이버전스
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

    # ── ADX 추세강도 (17%) ─────────────────────
    adx_s, pdi_s, ndi_s = calc_adx(h, l, p)
    adx = float(adx_s.iloc[-1]) if not np.isnan(float(adx_s.iloc[-1])) else 20.0
    pdi = float(pdi_s.iloc[-1]); ndi = float(ndi_s.iloc[-1])
    bull_trend = pdi > ndi
    if adx > 40:   adx_v = 85 if bull_trend else 15   # 매우 강한 추세
    elif adx > 25: adx_v = 72 if bull_trend else 28   # 추세 명확
    elif adx > 18: adx_v = 58 if bull_trend else 42   # 추세 약함
    else:          adx_v = 50                          # 횡보 (추세 없음)
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
    roll_max = prices.expanding().max()
    return float(((prices - roll_max) / roll_max * 100).min())

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
        roa_c = ni_c/ta_c if ni_c and ta_c else None
        roa_p = ni_p/ta_p if ni_p and ta_p else None
        if roa_c and roa_p:
            if roa_c > roa_p: score+=1; sig['F3 ROA개선']='✅'
            else:              sig['F3 ROA개선']='❌'
        else: sig['F3 ROA개선']='❓'

        if ocf and ta_c and roa_c:
            if ocf/ta_c > roa_c: score+=1; sig['F4 발생주의']='✅'
            else:                  sig['F4 발생주의']='❌'
        else: sig['F4 발생주의']='❓'

        ltd_c=gv(bal,'long','debt');  ltd_p=gv(bal,'long','debt',col=1)
        lev_c = ltd_c/ta_c if ltd_c and ta_c else None
        lev_p = ltd_p/ta_p if ltd_p and ta_p else None
        if lev_c is not None and lev_p is not None:
            if lev_c < lev_p: score+=1; sig['F5 레버리지감소']='✅'
            else:              sig['F5 레버리지감소']='❌'
        else: sig['F5 레버리지감소']='❓'

        ca_c=gv(bal,'current assets');  cl_c=gv(bal,'current liabilities')
        ca_p=gv(bal,'current assets',col=1); cl_p=gv(bal,'current liabilities',col=1)
        cr_c = ca_c/cl_c if ca_c and cl_c else None
        cr_p = ca_p/cl_p if ca_p and cl_p else None
        if cr_c and cr_p:
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
        gm_c = gp_c/rev_c if gp_c and rev_c else None
        gm_p = gp_p/rev_p if gp_p and rev_p else None
        if gm_c and gm_p:
            if gm_c > gm_p: score+=1; sig['F8 매출총이익률개선']='✅'
            else:            sig['F8 매출총이익률개선']='❌'
        else: sig['F8 매출총이익률개선']='❓'

        at_c = rev_c/ta_c if rev_c and ta_c else None
        at_p = rev_p/ta_p if rev_p and ta_p else None
        if at_c and at_p:
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
        pm_s = 50
        if pm:
            pp = pm*100
            pm_s = 10 if pp<0 else (40 if pp<5 else (60 if pp<10 else (80 if pp<20 else 90)))
        det['수익성'] = _score_roe(roe)*0.4 + _score_roe(roa*3 if roa else None)*0.3 + pm_s*0.3
        det['ROE'] = roe; det['ROA'] = roa; det['순이익률'] = pm

        # ── 성장성 (13%) ──────────────────────────────
        rg = info.get('revenueGrowth'); eg = info.get('earningsGrowth')
        det['성장성'] = _score_growth(rg)*0.5 + _score_growth(eg)*0.5 if eg else _score_growth(rg)
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
        de_s  = _score_de(de/100 if de else None)
        cr_s  = 50
        if cr: cr_s = 10 if cr<0.5 else (30 if cr<1.0 else (60 if cr<1.5 else (85 if cr<3.0 else 75)))
        int_cov = abs(ebit/int_exp) if (ebit and int_exp and int_exp != 0) else None
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

def run_screener(tickers, w_tech, w_fund, w_macro, prog_bar=None, prog_text=None):
    m_s, _, _ = macro_score()
    results = []
    for i, ticker in enumerate(tickers):
        if prog_text: prog_text.text(f"분석 중: {ticker} ({i+1}/{len(tickers)})")
        if prog_bar:  prog_bar.progress((i+1)/len(tickers))
        try:
            end = datetime.now(); start = end - timedelta(days=520)
            df  = yf.download(ticker, start=start, end=end, progress=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
            df  = df.dropna(subset=['Close'])
            if len(df) < 30: continue

            t_s, t_det_sc = technical_score(df)
            f_s, _ = fundamental_score(ticker, df)
            total  = t_s*(w_tech/100) + f_s*(w_fund/100) + m_s*(w_macro/100)
            mom    = calc_momentum(df)
            cp  = float(df['Close'].iloc[-1])
            pp  = float(df['Close'].iloc[-2]) if len(df) >= 2 else cp
            chg = (cp-pp)/pp*100
            m3  = mom.get('3M')
            sigs = []
            rsi_sc = t_det_sc.get('RSI값', 50)
            if rsi_sc < 30: sigs.append('RSI과매도')
            elif rsi_sc > 70: sigs.append('RSI과매수')
            bbu_sc, _, bbl_sc = calc_bb(df['Close'])
            bw_sc  = (float(bbu_sc.iloc[-1])-float(bbl_sc.iloc[-1]))/(cp+1e-9)
            bwa_sc = float(((bbu_sc-bbl_sc)/df['Close']).rolling(20).mean().iloc[-1])
            if bw_sc < bwa_sc*0.7: sigs.append('BB스퀴즈')
            ma20_sc = df['Close'].rolling(20).mean(); ma60_sc = df['Close'].rolling(60).mean()
            if len(df) >= 22 and float(ma20_sc.iloc[-2]) <= float(ma60_sc.iloc[-2]) and float(ma20_sc.iloc[-1]) > float(ma60_sc.iloc[-1]):
                sigs.append('골든크로스')
            ml_sc, sl_sc, _ = calc_macd(df['Close'])
            if len(ml_sc) >= 2 and float(ml_sc.iloc[-2]) <= float(sl_sc.iloc[-2]) and float(ml_sc.iloc[-1]) > float(sl_sc.iloc[-1]):
                sigs.append('MACD↑')
            results.append({
                '티커': ticker, '종합점수': round(total,1),
                '차트+파동': round(t_s,1), '재무+퀀트': round(f_s,1), '매크로': round(m_s,1),
                '모멘텀(3M)': f"{m3:+.1f}%" if m3 is not None else 'N/A',
                '등급': score_label(total), '등락(%)': round(chg,2),
                '시그널': ' '.join(sigs) or '-',
            })
        except: continue

    if not results: return pd.DataFrame()
    return pd.DataFrame(results).sort_values('종합점수', ascending=False).reset_index(drop=True)

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

def run_backtest(df, buy_th=65, sell_th=45, initial_capital=10_000_000):
    sigs   = bt_signals(df)
    prices = df['Close'].values
    dates  = df.index
    n      = len(df)

    capital  = float(initial_capital)
    shares   = 0.0
    in_pos   = False
    entry_px = 0.0
    equity   = np.full(n, float(initial_capital))
    trades   = []

    for i in range(20, n):
        px  = float(prices[i])
        sig = float(sigs.iloc[i])
        if not in_pos and sig > buy_th:
            shares = capital/px; entry_px = px; capital = 0.0; in_pos = True
            trades.append({'날짜': dates[i], '구분': '🟢 매수', '가격': round(px,2), '신호': round(sig,1), '수익률': ''})
        elif in_pos and sig < sell_th:
            capital = shares*px; pnl = (px-entry_px)/entry_px*100; shares = 0.0; in_pos = False
            trades.append({'날짜': dates[i], '구분': '🔴 매도', '가격': round(px,2), '신호': round(sig,1), '수익률': f"{pnl:+.2f}%"})
        equity[i] = capital + shares*px

    final_v = float(equity[-1])
    days    = (dates[-1] - dates[20]).days
    years   = max(days/365, 0.01)
    bh_ret  = (float(prices[-1]) - float(prices[20])) / float(prices[20]) * 100
    tot_ret = (final_v - initial_capital) / initial_capital * 100
    cagr    = ((final_v/initial_capital)**(1/years) - 1)*100

    eq_s      = pd.Series(equity).replace(0, np.nan).ffill()
    roll_max  = eq_s.expanding().max()
    mdd       = float(((eq_s - roll_max)/roll_max*100).min())
    daily_ret = eq_s.pct_change().dropna()
    sharpe    = float(daily_ret.mean()/daily_ret.std()*np.sqrt(252)) if daily_ret.std() > 0 else 0

    sells    = [t for t in trades if '매도' in t['구분']]
    wins     = [t for t in sells if isinstance(t['수익률'], str) and '+' in t['수익률']]
    win_rate = len(wins)/len(sells)*100 if sells else 0

    metrics = {
        '전략 수익률':    f"{tot_ret:+.1f}%",
        '매수보유 수익률': f"{bh_ret:+.1f}%",
        'CAGR':           f"{cagr:+.1f}%",
        '최대낙폭(MDD)':  f"{mdd:.1f}%",
        'Sharpe Ratio':   f"{sharpe:.2f}",
        '총 매매':        f"{len(sells)}회",
        '승률':           f"{win_rate:.1f}%",
        '최종 자산':      f"₩{final_v:,.0f}",
    }

    bh_eq    = np.full(n, float(initial_capital))
    bh_eq[20:] = (df['Close'].iloc[20:].values / float(prices[20])) * initial_capital
    eq_df    = pd.DataFrame({'날짜': dates, '전략': equity, '매수보유': bh_eq})
    return metrics, eq_df, pd.DataFrame(trades)

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

def check_alerts(watchlist, token, chat_id, threshold, w_tech, w_fund, w_macro):
    m_s, _, _ = macro_score()
    sent = []
    for ticker in watchlist:
        try:
            end = datetime.now(); start = end - timedelta(days=520)
            df  = yf.download(ticker, start=start, end=end, progress=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
            df  = df.dropna(subset=['Close'])
            if len(df) < 30: continue
            t_s, _ = technical_score(df)
            f_s, _ = fundamental_score(ticker, df)
            total  = t_s*(w_tech/100) + f_s*(w_fund/100) + m_s*(w_macro/100)
            cp     = float(df['Close'].iloc[-1])
            if total >= threshold:
                msg = (f"📊 *종합 주식 분석 알림*\n\n*{ticker}*  현재가: {cp:,.2f}\n"
                       f"종합점수: *{total:.1f}점* ({score_label(total)})\n"
                       f"차트: {t_s:.0f} | 재무: {f_s:.0f} | 매크로: {m_s:.0f}\n"
                       f"_{datetime.now().strftime('%Y-%m-%d %H:%M')}_")
                ok, err = send_telegram(token, chat_id, msg)
                sent.append({'티커': ticker, '점수': round(total,1), '발송': '✅ 성공' if ok else f'❌ {err}'})
        except: continue
    return sent

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

def get_news_sentiment(ticker):
    """yfinance 뉴스 헤드라인 키워드 기반 감성 분석 (구/신 API 형식 모두 지원)"""
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
            d = yf.download(ticker, start=start, end=end, interval=interval, progress=False)
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.droplevel(1)
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
        return (cp - float(p.iloc[-d])) / float(p.iloc[-d]) * 100 if len(p) > d else None
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
        g_raw = info.get('earningsGrowth') or info.get('revenueGrowth') or 0.07
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
        sdf   = yf.download(ticker, start=start, end=end, progress=False)
        spydf = yf.download('SPY',  start=start, end=end, progress=False)
        for d in [sdf, spydf]:
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.droplevel(1)
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
# TRADE LEVELS
# ─────────────────────────────────────────────

def calc_trade_levels(df, total_score):
    """단타(1~5일)·스윙(2~4주) 분리 매매가 산출"""
    p, h, l = df['Close'], df['High'], df['Low']
    cp  = float(p.iloc[-1])
    atr = float((h - l).rolling(14).mean().iloc[-1])

    ma20  = float(p.rolling(20).mean().iloc[-1])
    ma60  = float(p.rolling(60).mean().iloc[-1])
    ma120 = float(p.rolling(120).mean().iloc[-1])

    bb_u, _, bb_l_v = calc_bb(p)
    bb_upper = float(bb_u.iloc[-1])
    bb_lower = float(bb_l_v.iloc[-1])

    high20 = float(h.tail(20).max())
    low20  = float(l.tail(20).min())
    low60  = float(l.tail(60).min())

    # ── 피보나치 (60일 스윙) ───────────────────────
    sw_high  = float(h.tail(60).max())
    sw_low   = float(l.tail(60).min())
    fib_rng  = sw_high - sw_low
    fib = {
        '23.6%':       sw_high - 0.236 * fib_rng,
        '38.2%':       sw_high - 0.382 * fib_rng,
        '50.0%':       sw_high - 0.500 * fib_rng,
        '61.8%':       sw_high - 0.618 * fib_rng,
        '78.6%':       sw_high - 0.786 * fib_rng,
        '확장 127.2%': sw_high + 0.272 * fib_rng,
        '확장 161.8%': sw_high + 0.618 * fib_rng,
    }

    # ── 피봇 포인트 (전일 기준) ────────────────────
    prev_h = float(h.iloc[-2]) if len(h) >= 2 else float(h.iloc[-1])
    prev_l = float(l.iloc[-2]) if len(l) >= 2 else float(l.iloc[-1])
    prev_c = float(p.iloc[-2]) if len(p) >= 2 else float(p.iloc[-1])
    pivot  = (prev_h + prev_l + prev_c) / 3
    piv = {
        'R3': pivot + 2*(prev_h - prev_l),
        'R2': pivot + (prev_h - prev_l),
        'R1': 2*pivot - prev_l,
        'PP': pivot,
        'S1': 2*pivot - prev_h,
        'S2': pivot - (prev_h - prev_l),
        'S3': pivot - 2*(prev_h - prev_l),
    }

    # ── 단타 (1~5일): 피봇·ATR 중심 ──────────────
    if total_score >= 65:
        dt_e1 = cp;                   dt_be1 = '현재가(즉시)'
        dt_strategy = '✅ 즉시 진입'
    elif total_score >= 50:
        dt_e1 = piv['S1'] if piv['S1'] < cp*0.999 else bb_lower
        dt_be1 = '피봇 S1 / BB하단'
        dt_strategy = '⏳ S1 지지 확인 후 진입'
    else:
        dt_e1 = piv['S2'] if piv['S2'] < cp*0.999 else low20
        dt_be1 = '피봇 S2 / 20일저점'
        dt_strategy = '🔍 S2에서만 단기 진입'

    dt_e2   = max(piv['S2'], dt_e1 - atr * 0.8)
    dt_stop = dt_e1 - atr * 0.5

    dt_t1_pool = sorted([x for x in [piv['R1'], cp + atr*1.5, bb_upper] if x > cp*1.001])
    dt_t2_pool = sorted([x for x in [piv['R2'], cp + atr*3.0, high20]   if x > cp*1.001])
    dt_t1 = dt_t1_pool[0] if dt_t1_pool else cp + atr * 1.5
    dt_t2 = next((x for x in dt_t2_pool if x > dt_t1*1.005), dt_t1 * 1.03)

    dt_risk = max(dt_e1 - dt_stop, 1e-9)
    dt_rr1  = (dt_t1 - dt_e1) / dt_risk
    dt_rr2  = (dt_t2 - dt_e1) / dt_risk

    # ── 스윙 (2~4주): 피보나치·MA 중심 ───────────
    sw_sup = sorted([x for x in [fib['38.2%'], fib['50.0%'], fib['61.8%'],
                                  ma20, ma60, low20] if x < cp*0.999], reverse=True)
    sw_res = sorted([x for x in [fib['23.6%'], fib['확장 127.2%'], fib['확장 161.8%'],
                                  sw_high, ma120] if x > cp*1.001])

    sw_e1   = sw_sup[0] if sw_sup else cp * 0.96
    sw_e2   = sw_sup[1] if len(sw_sup) > 1 else sw_e1 * 0.96
    sw_stop = sw_e1 - atr * 1.5
    sw_t1   = sw_res[0] if sw_res else cp * 1.08
    sw_t2   = sw_res[1] if len(sw_res) > 1 else sw_t1 * 1.05

    if total_score >= 65:
        sw_strategy = '✅ 분할 매수 시작'
    elif total_score >= 50:
        sw_strategy = '⏳ Fib / MA 지지 대기'
    else:
        sw_strategy = '🔍 추세 전환 확인 후 진입'

    sw_risk = max(sw_e1 - sw_stop, 1e-9)
    sw_rr1  = (sw_t1 - sw_e1) / sw_risk
    sw_rr2  = (sw_t2 - sw_e1) / sw_risk

    safe = lambda a, b: a / b * 100 if b > 0 else 0.0

    return {
        'cp': cp, 'atr': atr, 'pivot': pivot, 'fib': fib, 'piv': piv,
        'dantta': {
            'strategy': dt_strategy,
            'entry1':  dt_e1,  'basis_e1':   dt_be1,
            'entry2':  dt_e2,  'basis_e2':   'S2 / −ATR×0.8',
            'target1': dt_t1,  'basis_t1':   'R1 / +ATR×1.5',
            'target2': dt_t2,  'basis_t2':   'R2 / +ATR×3.0',
            'stop':    dt_stop,'basis_stop':  '−ATR×0.5 (타이트)',
            'rr1': round(dt_rr1,1), 'rr2': round(dt_rr2,1),
            'ret1': safe(dt_t1-dt_e1, dt_e1),
            'ret2': safe(dt_t2-dt_e1, dt_e1),
            'risk_pct': safe(dt_e1-dt_stop, dt_e1),
        },
        'swing': {
            'strategy': sw_strategy,
            'entry1':  sw_e1,  'basis_e1':   'Fib 38.2% / MA20',
            'entry2':  sw_e2,  'basis_e2':   'Fib 50% / MA60',
            'target1': sw_t1,  'basis_t1':   '60일 고점 / Fib127%',
            'target2': sw_t2,  'basis_t2':   'Fib 161.8% 확장',
            'stop':    sw_stop,'basis_stop':  '−ATR×1.5 (여유)',
            'rr1': round(sw_rr1,1), 'rr2': round(sw_rr2,1),
            'ret1': safe(sw_t1-sw_e1, sw_e1),
            'ret2': safe(sw_t2-sw_e1, sw_e1),
            'risk_pct': safe(sw_e1-sw_stop, sw_e1),
        },
    }

def _draw_levels_chart(lv, is_krw):
    """단타·스윙 매매가 통합 시각화 (파란=단타, 주황=스윙)"""
    fmt_p = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"
    cp = lv['cp']
    dt = lv['dantta']
    sw = lv['swing']

    # 단타 레벨 (파란 계열, x=0)
    dt_levels = [
        ('⚡ 단타 2차목표', dt['target2'], '#1565C0', 'dot',   dt['basis_t2']),
        ('⚡ 단타 1차목표', dt['target1'], '#42a5f5', 'solid', dt['basis_t1']),
        ('⚡ 단타 1차매수', dt['entry1'],  '#4caf50', 'solid', dt['basis_e1']),
        ('⚡ 단타 2차매수', dt['entry2'],  '#81c784', 'dot',   dt['basis_e2']),
        ('⚡ 단타 손절',   dt['stop'],    '#ef5350', 'dash',  dt['basis_stop']),
    ]
    # 스윙 레벨 (주황 계열, x=1)
    sw_levels = [
        ('📈 스윙 2차목표', sw['target2'], '#e65100', 'dot',   sw['basis_t2']),
        ('📈 스윙 1차목표', sw['target1'], '#ff9800', 'solid', sw['basis_t1']),
        ('📈 스윙 1차매수', sw['entry1'],  '#26a69a', 'solid', sw['basis_e1']),
        ('📈 스윙 2차매수', sw['entry2'],  '#80cbc4', 'dot',   sw['basis_e2']),
        ('📈 스윙 손절',   sw['stop'],    '#b71c1c', 'dash',  sw['basis_stop']),
    ]

    all_prices = [cp] + [x[1] for x in dt_levels + sw_levels]
    valid = [p for p in all_prices if p > 0]
    margin = (max(valid) - min(valid)) * 0.12

    fig = go.Figure()

    # 현재가
    fig.add_hline(y=cp, line_color='#FFD700', line_width=2, line_dash='dot')
    fig.add_trace(go.Scatter(x=[0, 1], y=[cp, cp], mode='lines+text',
        line=dict(color='#FFD700', width=0),
        text=['', f"  <b>현재가</b> {fmt_p(cp)}"],
        textposition='middle right', textfont=dict(color='#FFD700', size=12),
        showlegend=False))

    for name, price, color, style, basis in dt_levels:
        pct = (price - cp) / cp * 100
        label = f"  <b>{name}</b>  {fmt_p(price)}  ({pct:+.1f}%)  [{basis}]"
        fig.add_trace(go.Scatter(x=[0], y=[price], mode='markers+text',
            marker=dict(size=10, color=color, symbol='line-ew', line=dict(color=color, width=2.5)),
            text=[label], textposition='middle right',
            textfont=dict(color=color, size=11), showlegend=False))
        fig.add_hline(y=price, line_color=color, line_width=0.8, line_dash=style,
                      annotation_text='', annotation_position='right')

    for name, price, color, style, basis in sw_levels:
        pct = (price - cp) / cp * 100
        label = f"  <b>{name}</b>  {fmt_p(price)}  ({pct:+.1f}%)  [{basis}]"
        fig.add_trace(go.Scatter(x=[1], y=[price], mode='markers+text',
            marker=dict(size=10, color=color, symbol='line-ew', line=dict(color=color, width=2.5)),
            text=[label], textposition='middle right',
            textfont=dict(color=color, size=11), showlegend=False))
        fig.add_hline(y=price, line_color=color, line_width=0.8, line_dash=style)

    # 구간 하이라이트
    fig.add_hrect(y0=dt['entry1'], y1=dt['target1'], fillcolor='rgba(66,165,245,0.07)', line_width=0)
    fig.add_hrect(y0=dt['stop'],   y1=dt['entry1'],  fillcolor='rgba(239,83,80,0.06)',  line_width=0)
    fig.add_hrect(y0=sw['entry1'], y1=sw['target1'], fillcolor='rgba(255,152,0,0.06)',  line_width=0)

    # 컬럼 구분선
    fig.add_vline(x=0.5, line_color=TV_BORDER, line_width=1, line_dash='dot')
    fig.add_annotation(x=0, y=max(valid)+margin*0.8, text="⚡ 단타 (1~5일)",
                       showarrow=False, font=dict(color='#42a5f5', size=12))
    fig.add_annotation(x=1, y=max(valid)+margin*0.8, text="📈 스윙 (2~4주)",
                       showarrow=False, font=dict(color='#ff9800', size=12))

    fig.update_layout(
        height=520, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
        font=dict(color=TV_TEXT, size=11),
        xaxis=dict(visible=False, range=[-0.5, 3.2]),
        yaxis=dict(range=[min(valid)-margin, max(valid)+margin*1.2],
                   gridcolor=TV_GRID, tickfont=dict(color=TV_TEXT, size=10), side='right'),
        margin=dict(l=10, r=280, t=30, b=10),
    )
    return fig


# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────

def _draw_chart(df, ticker, is_krw):
    p    = df['Close']
    m20  = p.rolling(20).mean(); m60 = p.rolling(60).mean(); m120 = p.rolling(120).mean()
    bb_u, bb_mid, bb_l = calc_bb(p)
    macd_l, sig_l, hist = calc_macd(p)
    rsi_s = calc_rsi(p)

    vol_c = ['rgba(38,166,154,0.5)' if float(df['Close'].iloc[i]) >= float(df['Open'].iloc[i])
             else 'rgba(239,83,80,0.5)' for i in range(len(df))]

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                        row_heights=[0.52,0.14,0.17,0.17], vertical_spacing=0.02)

    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        name='', increasing=dict(line=dict(color=TV_UP,width=1),fillcolor=TV_UP),
        decreasing=dict(line=dict(color=TV_DOWN,width=1),fillcolor=TV_DOWN)), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=bb_u, name='BB',
        line=dict(color='rgba(149,117,205,0.6)',width=1), legendgroup='bb'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb_l, name='BB',
        line=dict(color='rgba(149,117,205,0.6)',width=1),
        fill='tonexty', fillcolor='rgba(149,117,205,0.06)',
        showlegend=False, legendgroup='bb'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=bb_mid, name='BB Mid',
        line=dict(color='rgba(149,117,205,0.4)',width=1,dash='dot'), showlegend=False), row=1, col=1)

    for ma_s, c, ma_name in [(m20,'#f5c518','MA 20'),(m60,'#2962ff','MA 60'),(m120,'#ff6d00','MA 120')]:
        fig.add_trace(go.Scatter(x=df.index, y=ma_s, name=ma_name,
                                 line=dict(color=c,width=1.4)), row=1, col=1)

    cur_p  = float(df['Close'].iloc[-1])
    p_str  = f"₩{cur_p:,.0f}" if is_krw else f"${cur_p:.2f}"
    fig.add_hline(y=cur_p, line_dash='dot', line_color='#FFD700', line_width=1.2, row=1, col=1,
        annotation_text=f"  {p_str}", annotation_position="right",
        annotation_font=dict(color='#FFD700',size=11,family='monospace'),
        annotation_bgcolor=TV_BG)

    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='거래량', marker_color=vol_c), row=2, col=1)

    hcol = [TV_DOWN if float(v) >= 0 else TV_UP for v in hist]
    fig.add_trace(go.Bar(x=df.index, y=hist, name='히스토그램',
                         marker_color=hcol, showlegend=False, opacity=0.7), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=macd_l, name='MACD',
                             line=dict(color='#2962ff',width=1.3)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig_l,  name='Signal',
                             line=dict(color='#ff6d00',width=1.3)), row=3, col=1)
    fig.add_hline(y=0, line_color=TV_BORDER, line_width=1, row=3, col=1)

    fig.add_hrect(y0=30, y1=70, fillcolor='rgba(255,255,255,0.03)', line_width=0, row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=rsi_s, name='RSI',
                             line=dict(color='#ce93d8',width=1.4)), row=4, col=1)
    fig.add_hline(y=70, line_color=TV_DOWN, line_width=0.8, line_dash='dash', row=4, col=1)
    fig.add_hline(y=50, line_color=TV_BORDER, line_width=0.8, row=4, col=1)
    fig.add_hline(y=30, line_color=TV_UP,   line_width=0.8, line_dash='dash', row=4, col=1)

    ax = dict(gridcolor=TV_GRID, gridwidth=1, zerolinecolor=TV_BORDER,
              tickfont=dict(color=TV_TEXT,size=10), showline=True, linecolor=TV_BORDER, side='right')
    fig.update_layout(height=780, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
        font=dict(color=TV_TEXT, family='Inter,sans-serif', size=11),
        xaxis_rangeslider_visible=False, hovermode='x unified',
        hoverlabel=dict(bgcolor='#1e2334', font_color=TV_TEXT, bordercolor=TV_BORDER),
        legend=dict(orientation='h', y=1.01, x=0,
                    bgcolor='rgba(19,23,34,0.8)', bordercolor=TV_BORDER, borderwidth=1, font=dict(size=11)),
        margin=dict(l=0, r=60, t=30, b=0))
    for i in range(1, 5):
        fig.update_xaxes(row=i, col=1, gridcolor=TV_GRID, showgrid=True,
                         tickfont=dict(color=TV_TEXT,size=10), showline=True, linecolor=TV_BORDER,
                         showticklabels=(i==4))
        fig.update_yaxes(row=i, col=1, **ax)
    for rn, lbl in [(1,'Price'),(2,'Vol'),(3,'MACD'),(4,'RSI')]:
        fig.add_annotation(text=lbl, xref='paper', yref=f'y{rn}',
                           x=0.003, y=1, showarrow=False,
                           font=dict(color=TV_TEXT, size=10), xanchor='left', yanchor='top')
    return fig


def main():
    st.title("📊 종합 주식 분석 시스템")
    st.caption("차트+파동 · 재무제표+퀀트 · 매크로+금리 종합 점수")

    with st.sidebar:
        st.header("⚙️ 글로벌 설정")
        st.subheader("가중치")
        w_tech  = st.slider("📈 차트+파동 (%)",    0, 100, 35, 5)
        w_fund  = st.slider("💰 재무제표+퀀트 (%)", 0, 100, 40, 5)
        w_macro = st.slider("🌍 매크로+금리 (%)",   0, 100, 25, 5)
        total_w = w_tech + w_fund + w_macro
        if total_w == 100: st.success(f"가중치 합계: {total_w}% ✅")
        else:              st.error(f"가중치 합계: {total_w}% (100% 필요)")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 종목 분석", "🔍 스크리너", "📉 백테스팅", "🔔 알림", "💼 포트폴리오"])

    # ── Tab 1: 단일 종목 분석 ─────────────────
    with tab1:
        c_mkt, c_tkr = st.columns([1,2])
        with c_mkt:
            market = st.selectbox("시장", ["미국 (NYSE/NASDAQ)", "한국 (KRX)", "ETF/인덱스"])
        with c_tkr:
            if market == "한국 (KRX)":
                ca, cb = st.columns([2,1])
                ticker_raw = ca.text_input("종목코드", placeholder="005930")
                sfx = ".KS" if "KS" in cb.radio("거래소", [".KS",".KQ"], horizontal=True) else ".KQ"
                ticker = (ticker_raw.strip()+sfx).upper() if ticker_raw else ""
            elif market == "미국 (NYSE/NASDAQ)":
                ticker = st.text_input("티커", placeholder="AAPL").strip().upper()
            else:
                ticker = st.text_input("ETF 티커", placeholder="SPY").strip().upper()

        run = st.button("📊 분석 시작", type="primary", disabled=(total_w!=100 or not ticker))

        if not run:
            st.info("티커를 입력하고 **분석 시작** 버튼을 눌러주세요.\n\n"
                    "예) `AAPL` `NVDA` `TSLA` | 한국: `005930` (삼성전자) | ETF: `SPY` `QQQ`")
        else:
            prog = st.progress(0); msg = st.empty()
            msg.text("📥 데이터 다운로드 중...")
            prog.progress(5)

            end_dt   = datetime.now()
            start_dt = end_dt - timedelta(days=520)
            df = yf.download(ticker, start=start_dt, end=end_dt, progress=False)

            if df.empty:
                st.error(f"'{ticker}' 데이터를 찾을 수 없습니다.")
                prog.empty(); msg.empty()
            else:
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                df = df.dropna(subset=['Close'])

                if len(df) < 30:
                    st.error("데이터 부족 (30일 미만).")
                    prog.empty(); msg.empty()
                else:
                    prog.progress(15); msg.text("📈 차트·파동 분석 중...")
                    t_score, t_det  = technical_score(df)
                    candle_pats     = detect_candle_patterns(df)
                    mom_data        = calc_momentum(df)
                    prog.progress(35); msg.text("💰 재무제표·퀀트 분석 중...")
                    f_score, f_det  = fundamental_score(ticker, df)
                    prog.progress(52); msg.text("🌍 매크로·금리 분석 중...")
                    m_score, m_det, m_data = macro_score()
                    total = t_score*(w_tech/100) + f_score*(w_fund/100) + m_score*(w_macro/100)
                    prog.progress(63); msg.text("🕐 멀티 타임프레임 분석 중...")
                    mtf_scores      = technical_score_multi(ticker)
                    prog.progress(74); msg.text("💵 DCF 내재가치 산출 중...")
                    dcf_val, dcf_det = calc_dcf(ticker, m_data.get('10Y금리', 4.5))
                    prog.progress(84); msg.text("⚠️ 리스크 분석 중...")
                    risk_data       = calc_risk_metrics(ticker)
                    prog.progress(93); msg.text("📰 뉴스 감성 분석 중...")
                    news_score, news_articles = get_news_sentiment(ticker)
                    regime, regime_diff = get_market_regime()
                    prog.progress(100); prog.empty(); msg.empty()

                    try:
                        info = yf.Ticker(ticker).info
                        name = info.get('longName') or info.get('shortName') or ticker
                    except: info, name = {}, ticker

                    is_krw = ticker.endswith('.KS') or ticker.endswith('.KQ')
                    fmt_p  = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"
                    cp  = float(df['Close'].iloc[-1])
                    pp  = float(df['Close'].iloc[-2]) if len(df) >= 2 else cp
                    chg = (cp-pp)/pp*100

                    regime_icon  = {'bull':'🐂 강세장','bear':'🐻 약세장','neutral':'➡️ 중립장'}[regime]
                    regime_color = {'bull':'#26a69a','bear':'#ef5350','neutral':'#b2b5be'}[regime]

                    st.header(f"{name}  `{ticker}`")
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("현재가", fmt_p(cp), f"{chg:+.2f}%")
                    c2.metric("52주 고가", fmt_p(float(df['High'].tail(252).max())))
                    c3.metric("52주 저가", fmt_p(float(df['Low'].tail(252).min())))
                    c4.metric("분석 기준일", end_dt.strftime("%Y-%m-%d"))
                    st.markdown(
                        f"<span style='background:{regime_color}22;color:{regime_color};"
                        f"border:1px solid {regime_color};border-radius:6px;"
                        f"padding:3px 10px;font-size:13px;font-weight:600'>"
                        f"시장 국면: {regime_icon}  (SPY vs MA200 {regime_diff:+.1f}%)</span>",
                        unsafe_allow_html=True)
                    st.divider()

                    cg, cv = st.columns(2)
                    with cg: st.plotly_chart(gauge(total,"종합 점수"), use_container_width=True)
                    with cv:
                        st.markdown(f"""
                        <div style='text-align:center;padding:20px 0'>
                          <div style='font-size:72px;font-weight:bold;color:{score_color(total)}'>{total:.1f}</div>
                          <div style='font-size:28px;color:{score_color(total)}'>{score_label(total)}</div>
                          <br><div style='color:#888;font-size:15px'>
                          차트+파동 <b>{t_score:.0f}</b>점 &nbsp;|&nbsp;
                          재무+퀀트 <b>{f_score:.0f}</b>점 &nbsp;|&nbsp;
                          매크로+금리 <b>{m_score:.0f}</b>점</div>
                        </div>""", unsafe_allow_html=True)
                    st.divider()

                    # ── 매매 시그널 ──────────────────────────────
                    trade_signals = detect_trading_signals(df, t_det)
                    if trade_signals:
                        st.subheader("🚨 매매 시그널")
                        sig_n = min(len(trade_signals), 3)
                        sig_cols = st.columns(sig_n)
                        for sig_i, (sig_ico, sig_nm, sig_dc) in enumerate(trade_signals):
                            sig_clr = ('#26a69a' if sig_ico == '🟢' else
                                       '#ef5350' if sig_ico == '🔴' else
                                       '#ff9800' if sig_ico == '🟡' else '#42a5f5')
                            sig_cols[sig_i % sig_n].markdown(
                                f"<div style='background:{sig_clr}18;border:1px solid {sig_clr}44;"
                                f"border-radius:8px;padding:10px 14px;margin:4px 0'>"
                                f"<span style='font-size:18px'>{sig_ico}</span> "
                                f"<span style='color:{sig_clr};font-weight:600;font-size:14px'>{sig_nm}</span><br>"
                                f"<span style='color:#888;font-size:12px'>{sig_dc}</span></div>",
                                unsafe_allow_html=True)
                        st.divider()

                    st.subheader("카테고리별 점수")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.plotly_chart(gauge(t_score, f"📈 차트+파동  ({w_tech}%)"), use_container_width=True)
                        with st.expander("세부 점수 · 캔들 패턴 · 모멘텀"):
                            SKIP = {'RSI값', 'ADX값', 'Stoch값'}
                            HINT = {
                                'RSI':      f"RSI {t_det.get('RSI값','N/A')}",
                                'ADX추세강도': f"ADX {t_det.get('ADX값','N/A')} ({'추세' if float(t_det.get('ADX값',0)) > 25 else '횡보'})",
                                '스토캐스틱': f"%K {t_det.get('Stoch값','N/A')}",
                            }
                            for k, v in t_det.items():
                                if k in SKIP: continue
                                hint = f" *({HINT[k]})*" if k in HINT else ''
                                st.markdown(f"**{k}** {'█'*int(v/10)}{'░'*(10-int(v/10))} `{v:.0f}점`{hint}")
                            st.divider()
                            st.caption("**📊 모멘텀**")
                            m_cols = st.columns(4)
                            for col_m, (lbl, val) in zip(m_cols, [('1M', mom_data['1M']),('3M', mom_data['3M']),('6M', mom_data['6M']),('12M', mom_data['12M'])]):
                                if val is not None:
                                    col_m.metric(lbl, f"{val:+.1f}%")
                                else:
                                    col_m.metric(lbl, "N/A")
                            st.divider()
                            st.caption("**🕯️ 캔들 패턴 (최근 3일)**")
                            if candle_pats:
                                st.dataframe(pd.DataFrame(candle_pats), use_container_width=True, hide_index=True)
                            else:
                                st.caption("  특이 패턴 없음")
                    with col2:
                        st.plotly_chart(gauge(f_score, f"💰 재무제표+퀀트  ({w_fund}%)"), use_container_width=True)
                        with st.expander("세부 점수"):
                            for k in ['밸류에이션','수익성','성장성','FCF품질','안전성','MDD','F-Score','52주위치']:
                                v = f_det.get(k, 50)
                                st.markdown(f"**{k}** {'█'*int(v/10)}{'░'*(10-int(v/10))} `{v:.0f}점`")
                            st.divider()
                            sec_nm = f_det.get('업종','N/A'); sec_per = f_det.get('업종평균PER', 20)
                            st.caption(f"업종: {sec_nm}  (업종평균 PER: {sec_per})")
                            peg_v = f_det.get('PEG'); ev_v = f_det.get('EV/EBITDA')
                            st.caption(f"PER: {fmt(f_det.get('PER'))}  |  PBR: {fmt(f_det.get('PBR'))}  |  PEG: {fmt(peg_v)}  |  EV/EBITDA: {fmt(ev_v)}")
                            st.caption(f"ROE: {fmt(f_det.get('ROE'),pct=True)}  |  ROA: {fmt(f_det.get('ROA'),pct=True)}  |  순이익률: {fmt(f_det.get('순이익률'),pct=True)}")
                            fcf_y = f_det.get('FCF수익률')
                            st.caption(f"FCF수익률: {f'{fcf_y:.1f}%' if fcf_y is not None else 'N/A'}  |  이자보상배율: {fmt(f_det.get('이자보상배율'))}")
                            st.caption(f"매출성장: {fmt(f_det.get('매출성장'),pct=True)}  |  EPS성장: {fmt(f_det.get('EPS성장'),pct=True)}")
                            mdd_v = f_det.get('MDD값')
                            st.caption(f"MDD: {f'{mdd_v:.1f}%' if mdd_v else 'N/A'}")
                            fs_v = f_det.get('F-Score값')
                            st.caption(f"Piotroski F-Score: {f'{fs_v}/9' if fs_v is not None else 'N/A'}")
                            for sk, sv in f_det.get('F-Score시그널', {}).items():
                                if '오류' not in sk: st.caption(f"  {sv} {sk}")
                            if dcf_det:
                                st.divider()
                                st.caption("**💵 DCF 내재가치 (그레이엄 공식)**")
                                dv_b = dcf_det.get('내재가치_기본', 0)
                                dv_c = dcf_det.get('내재가치_보수', 0)
                                up_b = dcf_det.get('상승여력_기본', 0)
                                up_c = dcf_det.get('상승여력_보수', 0)
                                dc1, dc2 = st.columns(2)
                                dc1.metric("내재가치 (기본)", fmt_p(dv_b), f"{up_b:+.1f}%")
                                dc2.metric("내재가치 (보수)", fmt_p(dv_c), f"{up_c:+.1f}%")
                                st.caption(f"EPS: {fmt(dcf_det.get('EPS'))}  |  g: {dcf_det.get('예상성장률(g)',0):.1f}%  |  Y: {dcf_det.get('적용금리(Y)',0):.2f}%")
                    with col3:
                        st.plotly_chart(gauge(m_score, f"🌍 매크로+금리  ({w_macro}%)"), use_container_width=True)
                        with st.expander("세부 점수"):
                            for k in ['금리환경','장단기금리차','VIX','달러지수','신용스프레드','원자재/인플레']:
                                v = m_det.get(k, 50)
                                st.markdown(f"**{k}** {'█'*int(v/10)}{'░'*(10-int(v/10))} `{v:.0f}점`")
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
                    st.divider()

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
                    st.divider()

                    # ── 매수/매도 추천가 ──────────────────────
                    st.subheader("💡 매매 추천가")
                    lv    = calc_trade_levels(df, total)
                    fmt_p = lambda x: f"₩{x:,.0f}" if is_krw else f"${x:.2f}"
                    cp_lv = lv['cp']
                    dt    = lv['dantta']
                    sw    = lv['swing']

                    lv_c1, lv_c2 = st.columns(2)

                    with lv_c1:
                        dt_rr_color = '#4caf50' if dt['rr1'] >= 2 else ('#ff9800' if dt['rr1'] >= 1 else '#ef5350')
                        st.markdown(
                            f"<div style='background:#0d1b2e;border:1px solid #42a5f544;border-radius:10px;"
                            f"padding:12px 16px;margin-bottom:8px'>"
                            f"<div style='color:#42a5f5;font-weight:700;font-size:15px'>⚡ 단타 전략 (1~5일)</div>"
                            f"<div style='color:#aaa;font-size:12px;margin-top:3px'>{dt['strategy']}</div>"
                            f"<div style='color:#888;font-size:11px;margin-top:2px'>"
                            f"손익비 1차 <b style='color:{dt_rr_color}'>R {dt['rr1']:.1f}:1</b>"
                            f" &nbsp;·&nbsp; 2차 <b style='color:{dt_rr_color}'>R {dt['rr2']:.1f}:1</b>"
                            f" &nbsp;·&nbsp; 손절폭 <b style='color:#ef5350'>{dt['risk_pct']:.1f}%</b></div>"
                            f"</div>", unsafe_allow_html=True)
                        dt_rows = [
                            {'구분':'🟢 1차 매수','가격':fmt_p(dt['entry1']),'현재가 대비':f"{(dt['entry1']-cp_lv)/cp_lv*100:+.1f}%",'근거':dt['basis_e1']},
                            {'구분':'🟩 2차 매수','가격':fmt_p(dt['entry2']),'현재가 대비':f"{(dt['entry2']-cp_lv)/cp_lv*100:+.1f}%",'근거':dt['basis_e2']},
                            {'구분':'🔵 1차 목표','가격':fmt_p(dt['target1']),'현재가 대비':f"+{dt['ret1']:.1f}%",'근거':dt['basis_t1']},
                            {'구분':'🔷 2차 목표','가격':fmt_p(dt['target2']),'현재가 대비':f"+{dt['ret2']:.1f}%",'근거':dt['basis_t2']},
                            {'구분':'🔴 손절가',  '가격':fmt_p(dt['stop']),  '현재가 대비':f"-{dt['risk_pct']:.1f}%",'근거':dt['basis_stop']},
                        ]
                        st.dataframe(pd.DataFrame(dt_rows), use_container_width=True, hide_index=True)

                    with lv_c2:
                        sw_rr_color = '#4caf50' if sw['rr1'] >= 2 else ('#ff9800' if sw['rr1'] >= 1 else '#ef5350')
                        st.markdown(
                            f"<div style='background:#1a1a0a;border:1px solid #ff980044;border-radius:10px;"
                            f"padding:12px 16px;margin-bottom:8px'>"
                            f"<div style='color:#ff9800;font-weight:700;font-size:15px'>📈 스윙 전략 (2~4주)</div>"
                            f"<div style='color:#aaa;font-size:12px;margin-top:3px'>{sw['strategy']}</div>"
                            f"<div style='color:#888;font-size:11px;margin-top:2px'>"
                            f"손익비 1차 <b style='color:{sw_rr_color}'>R {sw['rr1']:.1f}:1</b>"
                            f" &nbsp;·&nbsp; 2차 <b style='color:{sw_rr_color}'>R {sw['rr2']:.1f}:1</b>"
                            f" &nbsp;·&nbsp; 손절폭 <b style='color:#ef5350'>{sw['risk_pct']:.1f}%</b></div>"
                            f"</div>", unsafe_allow_html=True)
                        sw_rows = [
                            {'구분':'🟢 1차 매수','가격':fmt_p(sw['entry1']),'현재가 대비':f"{(sw['entry1']-cp_lv)/cp_lv*100:+.1f}%",'근거':sw['basis_e1']},
                            {'구분':'🟩 2차 매수','가격':fmt_p(sw['entry2']),'현재가 대비':f"{(sw['entry2']-cp_lv)/cp_lv*100:+.1f}%",'근거':sw['basis_e2']},
                            {'구분':'🔵 1차 목표','가격':fmt_p(sw['target1']),'현재가 대비':f"+{sw['ret1']:.1f}%",'근거':sw['basis_t1']},
                            {'구분':'🔷 2차 목표','가격':fmt_p(sw['target2']),'현재가 대비':f"+{sw['ret2']:.1f}%",'근거':sw['basis_t2']},
                            {'구분':'🔴 손절가',  '가격':fmt_p(sw['stop']),  '현재가 대비':f"-{sw['risk_pct']:.1f}%",'근거':sw['basis_stop']},
                        ]
                        st.dataframe(pd.DataFrame(sw_rows), use_container_width=True, hide_index=True)

                    st.plotly_chart(_draw_levels_chart(lv, is_krw), use_container_width=True)

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
                    st.divider()

                    # ── 뉴스 감성 ──────────────────────────────
                    st.subheader("📰 뉴스 감성 분석")
                    ns_col1, ns_col2 = st.columns([1, 3])
                    with ns_col1:
                        ns_color = score_color(news_score)
                        ns_label = '긍정적' if news_score >= 60 else ('부정적' if news_score < 40 else '중립')
                        st.markdown(
                            f"<div style='text-align:center;padding:15px 5px'>"
                            f"<div style='font-size:42px;font-weight:bold;color:{ns_color}'>{news_score:.0f}</div>"
                            f"<div style='color:{ns_color};font-size:14px'>{ns_label}</div>"
                            f"<div style='color:#888;font-size:11px;margin-top:4px'>감성 점수</div>"
                            f"</div>", unsafe_allow_html=True)
                    with ns_col2:
                        if news_articles:
                            st.dataframe(pd.DataFrame(news_articles), use_container_width=True, hide_index=True)
                        else:
                            st.info("뉴스 데이터를 가져올 수 없습니다.")
                    st.divider()

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
                    st.divider()

                    # ── 차트 ──────────────────────────────────
                    st.subheader("📈 차트")
                    st.plotly_chart(_draw_chart(df, ticker, is_krw), use_container_width=True)

                    st.subheader("📋 분석 요약")
                    mtf_d = mtf_scores.get('일봉'); mtf_w = mtf_scores.get('주봉'); mtf_m = mtf_scores.get('월봉')
                    mtf_list = [x['score'] for x in [mtf_d, mtf_w, mtf_m] if x is not None]
                    mtf_avg  = sum(mtf_list) / len(mtf_list) if mtf_list else 50.0
                    mtf_summary = (f"일봉 {mtf_d['score']:.0f} / 주봉 {mtf_w['score']:.0f} / 월봉 {mtf_m['score']:.0f}"
                                   if mtf_d and mtf_w and mtf_m else "N/A")
                    mom_3 = mom_data.get('3M'); mom_12 = mom_data.get('12M')
                    mom_summary = (f"3M {mom_3:+.1f}% / 12M {mom_12:+.1f}%"
                                   if mom_3 is not None and mom_12 is not None else "N/A")
                    dcf_summary = (f"기본 {fmt_p(dcf_det.get('내재가치_기본',0))} ({dcf_det.get('상승여력_기본',0):+.1f}%)"
                                   if dcf_det else "N/A")
                    st.dataframe(pd.DataFrame([
                        {'카테고리':'종합 점수',          '점수':f"{total:.1f}",       '등급':score_label(total),       '비고': f"시장: {regime_icon}"},
                        {'카테고리':'📈 차트+파동',       '점수':f"{t_score:.1f}",     '등급':score_label(t_score),     '비고':f'가중치 {w_tech}%'},
                        {'카테고리':'💰 재무제표+퀀트',   '점수':f"{f_score:.1f}",     '등급':score_label(f_score),     '비고':f'가중치 {w_fund}% | 업종: {f_det.get("업종","N/A")}'},
                        {'카테고리':'🌍 매크로+금리',     '점수':f"{m_score:.1f}",     '등급':score_label(m_score),     '비고':f'가중치 {w_macro}%'},
                        {'카테고리':'🕐 멀티 타임프레임', '점수':f"{mtf_avg:.1f}",     '등급':score_label(mtf_avg),     '비고': mtf_summary},
                        {'카테고리':'📊 모멘텀',          '점수':f"{mom_data['score']:.1f}", '등급':score_label(mom_data['score']), '비고': mom_summary},
                        {'카테고리':'💵 DCF 내재가치',    '점수':'참고용',             '등급':'-',                      '비고': dcf_summary},
                        {'카테고리':'📰 뉴스 감성',       '점수':f"{news_score:.1f}",  '등급':score_label(news_score),  '비고':'참고용'},
                    ]), use_container_width=True, hide_index=True)
                    st.caption("⚠️ 본 분석은 투자 참고용이며 투자 결정의 책임은 본인에게 있습니다.")

    # ── Tab 2: 스크리너 ───────────────────────
    with tab2:
        st.subheader("🔍 멀티 종목 스크리너")
        st.caption("여러 종목을 한번에 분석해 점수 순으로 랭킹합니다.")

        c1, c2 = st.columns([1,2])
        with c1:
            preset = st.selectbox("프리셋", ["직접 입력"] + list(PRESETS.keys()))
        with c2:
            if preset == "직접 입력":
                ticker_str  = st.text_input("티커 입력 (쉼표 구분)", "AAPL,MSFT,NVDA,GOOGL,TSLA")
                ticker_list = [t.strip().upper() for t in ticker_str.split(',') if t.strip()]
            else:
                ticker_list = PRESETS[preset]
                st.info(f"선택된 종목: {', '.join(ticker_list)}")

        with st.expander("🔎 시그널 필터 (선택)"):
            sf1, sf2, sf3, sf4 = st.columns(4)
            flt_rsi  = sf1.checkbox("RSI 과매도 (<30)")
            flt_sqz  = sf2.checkbox("BB 스퀴즈")
            flt_gold = sf3.checkbox("골든크로스")
            flt_macd = sf4.checkbox("MACD 상향")
            sc_min_sc = st.slider("최소 종합점수", 0, 90, 0, 5, key="sc_min_sc")

        if st.button("🔍 스크리닝 시작", type="primary", disabled=(total_w!=100)):
            pb = st.progress(0); pt = st.empty()
            result_df = run_screener(ticker_list, w_tech, w_fund, w_macro, pb, pt)
            pb.empty(); pt.empty()

            if result_df.empty:
                st.warning("분석 가능한 종목이 없습니다.")
            else:
                if flt_rsi:       result_df = result_df[result_df['시그널'].str.contains('RSI과매도',  na=False)]
                if flt_sqz:       result_df = result_df[result_df['시그널'].str.contains('BB스퀴즈',   na=False)]
                if flt_gold:      result_df = result_df[result_df['시그널'].str.contains('골든크로스', na=False)]
                if flt_macd:      result_df = result_df[result_df['시그널'].str.contains('MACD↑',     na=False)]
                if sc_min_sc > 0: result_df = result_df[result_df['종합점수'] >= sc_min_sc]

                if result_df.empty:
                    st.warning("필터 조건에 맞는 종목이 없습니다.")
                else:
                    st.success(f"총 {len(result_df)}개 종목 분석 완료")
                    try:
                        styled = result_df.style.map(_style_score,
                                                     subset=['종합점수','차트+파동','재무+퀀트','매크로'])
                    except AttributeError:
                        styled = result_df.style.applymap(_style_score,
                                                          subset=['종합점수','차트+파동','재무+퀀트','매크로'])
                    st.dataframe(styled, use_container_width=True, height=460)

                    top5 = result_df.head(5)
                    fig_bar = go.Figure()
                    for col, c in [('차트+파동','#f5c518'),('재무+퀀트','#2962ff'),('매크로','#ff6d00')]:
                        fig_bar.add_trace(go.Bar(name=col, x=top5['티커'], y=top5[col], marker_color=c))
                    fig_bar.update_layout(barmode='group', height=300,
                        plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER, font=dict(color=TV_TEXT),
                        legend=dict(orientation='h'), yaxis=dict(range=[0,100], gridcolor=TV_GRID),
                        xaxis=dict(gridcolor=TV_GRID), margin=dict(t=20,b=20))
                    st.plotly_chart(fig_bar, use_container_width=True)

    # ── Tab 3: 백테스팅 ───────────────────────
    with tab3:
        st.subheader("📉 전략 백테스팅")
        st.caption("MA + RSI + MACD 기반 신호 전략의 과거 성과를 검증합니다.")

        c1, c2, c3 = st.columns(3)
        bt_ticker  = c1.text_input("티커", "AAPL").strip().upper()
        bt_period  = c2.selectbox("기간", ["1년","2년","3년","5년"], index=1)
        bt_capital = c3.number_input("초기자금 (원)", value=10_000_000, step=1_000_000, min_value=100_000)

        c4, c5 = st.columns(2)
        buy_th  = c4.slider("매수 임계값", 50, 90, 65, 5, help="신호가 이 점수를 넘으면 매수")
        sell_th = c5.slider("매도 임계값", 20, 60, 45, 5, help="신호가 이 점수 아래로 내려오면 매도")

        period_days = {"1년":365, "2년":730, "3년":1095, "5년":1825}

        if st.button("📉 백테스팅 시작", type="primary"):
            with st.spinner("백테스팅 실행 중..."):
                end_dt2   = datetime.now()
                start_dt2 = end_dt2 - timedelta(days=period_days[bt_period]+60)
                bt_df = yf.download(bt_ticker, start=start_dt2, end=end_dt2, progress=False)
                if isinstance(bt_df.columns, pd.MultiIndex): bt_df.columns = bt_df.columns.droplevel(1)
                bt_df = bt_df.dropna(subset=['Close'])

            if bt_df.empty or len(bt_df) < 60:
                st.error("데이터가 부족합니다.")
            else:
                metrics, eq_df, trades_df = run_backtest(bt_df, buy_th, sell_th, bt_capital)

                m_keys = list(metrics.keys()); m_vals = list(metrics.values())
                cols = st.columns(4)
                for i in range(4): cols[i].metric(m_keys[i], m_vals[i])
                cols2 = st.columns(4)
                for i in range(4): cols2[i].metric(m_keys[i+4], m_vals[i+4])
                st.divider()

                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(x=eq_df['날짜'], y=eq_df['전략'], name='전략',
                    line=dict(color='#2962ff',width=2),
                    fill='tozeroy', fillcolor='rgba(41,98,255,0.08)'))
                fig_eq.add_trace(go.Scatter(x=eq_df['날짜'], y=eq_df['매수보유'], name='매수보유',
                    line=dict(color='#888',width=1.5,dash='dash')))

                if not trades_df.empty:
                    buys  = trades_df[trades_df['구분'].str.contains('매수')]
                    sells2 = trades_df[trades_df['구분'].str.contains('매도')]
                    for bdate in buys['날짜']:
                        row = eq_df[eq_df['날짜']==bdate]
                        if not row.empty:
                            fig_eq.add_trace(go.Scatter(x=[bdate], y=[float(row['전략'].iloc[0])],
                                mode='markers', marker=dict(symbol='triangle-up',size=12,color=TV_UP),
                                showlegend=False))
                    for sdate in sells2['날짜']:
                        row = eq_df[eq_df['날짜']==sdate]
                        if not row.empty:
                            fig_eq.add_trace(go.Scatter(x=[sdate], y=[float(row['전략'].iloc[0])],
                                mode='markers', marker=dict(symbol='triangle-down',size=12,color=TV_DOWN),
                                showlegend=False))

                fig_eq.update_layout(height=420, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                    font=dict(color=TV_TEXT), hovermode='x unified',
                    yaxis=dict(gridcolor=TV_GRID, tickformat=',.0f', side='right'),
                    xaxis=dict(gridcolor=TV_GRID),
                    legend=dict(orientation='h', bgcolor='rgba(0,0,0,0)'),
                    margin=dict(l=0, r=60, t=20, b=0))
                st.plotly_chart(fig_eq, use_container_width=True)

                if not trades_df.empty:
                    st.subheader(f"매매 내역 ({len(trades_df)}건)")
                    st.dataframe(trades_df, use_container_width=True, hide_index=True)

    # ── Tab 4: 알림 ───────────────────────────
    with tab4:
        st.subheader("🔔 텔레그램 알림 설정")

        with st.expander("📱 봇 설정 방법 보기"):
            st.markdown("""
            1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 봇 이름 입력 → **토큰** 복사
            2. 만든 봇에게 아무 메시지나 먼저 보내기
            3. **@userinfobot** 검색 → `/start` → **Chat ID** 확인
            4. 아래에 입력 후 테스트 메시지로 확인
            """)

        c1, c2 = st.columns(2)
        tg_token  = c1.text_input("봇 토큰", placeholder="1234567890:AAF...", type="password")
        tg_chatid = c2.text_input("Chat ID", placeholder="123456789")
        st.divider()

        alert_str  = st.text_input("감시 종목 (쉼표 구분)", "AAPL,NVDA,005930.KS")
        alert_list = [t.strip().upper() for t in alert_str.split(',') if t.strip()]
        alert_th   = st.slider("알림 기준 점수 (이상일 때 발송)", 50, 95, 65, 5)

        c1, c2 = st.columns(2)
        if c1.button("📤 테스트 메시지 발송"):
            if not tg_token or not tg_chatid:
                st.error("토큰과 Chat ID를 입력해주세요.")
            else:
                ok, err = send_telegram(tg_token, tg_chatid,
                    "✅ *종합 주식 분석 시스템* 연결 성공!\n\n알림이 정상 작동합니다.")
                if ok: st.success("테스트 메시지 발송 성공! 텔레그램을 확인하세요.")
                else:  st.error(f"발송 실패: {err}")

        if c2.button("🔍 지금 점검하고 알림 발송", type="primary"):
            if not tg_token or not tg_chatid:
                st.error("토큰과 Chat ID를 입력해주세요.")
            elif total_w != 100:
                st.error("가중치 합계를 100%로 맞춰주세요.")
            else:
                with st.spinner(f"{len(alert_list)}개 종목 점검 중..."):
                    sent = check_alerts(alert_list, tg_token, tg_chatid, alert_th, w_tech, w_fund, w_macro)
                if sent:
                    st.success(f"{len(sent)}개 종목 알림 발송 완료")
                    st.dataframe(pd.DataFrame(sent), use_container_width=True, hide_index=True)
                else:
                    st.info(f"기준 점수 {alert_th}점 이상인 종목이 없습니다.")

    # ── Tab 5: 포트폴리오 ────────────────────────
    with tab5:
        st.subheader("💼 포트폴리오 관리")
        st.caption("보유 종목의 손익과 분석 점수를 한눈에 확인하세요. (세션 동안 유지)")

        if 'portfolio' not in st.session_state:
            st.session_state.portfolio = []

        # ── 포지션 추가 ──────────────────────────
        with st.expander("➕ 포지션 추가", expanded=(len(st.session_state.portfolio) == 0)):
            with st.form("pf_form", clear_on_submit=True):
                pfc1, pfc2, pfc3 = st.columns([2, 1, 1])
                pf_t  = pfc1.text_input("티커", placeholder="AAPL 또는 005930.KS")
                pf_q  = pfc2.number_input("수량", min_value=0.001, value=1.0, format="%.3f")
                pf_c  = pfc3.number_input("평균매수가", min_value=0.0, value=0.0, format="%.2f")
                pf_nt = st.text_input("메모 (선택)", placeholder="장기보유, 스윙 등")
                if st.form_submit_button("추가", type="primary"):
                    tk_in = pf_t.strip().upper()
                    if tk_in:
                        st.session_state.portfolio.append(
                            {'ticker': tk_in, 'qty': float(pf_q), 'avg_cost': float(pf_c), 'note': pf_nt})
                        st.success(f"✅ {tk_in} 추가됨")
                        st.rerun()

        if not st.session_state.portfolio:
            st.info("포지션을 추가한 후 분석을 실행하세요.")
        else:
            # 보유 종목 목록
            st.caption(f"**보유 종목 {len(st.session_state.portfolio)}개**")
            for pf_i, pos in enumerate(st.session_state.portfolio):
                pc1, pc2 = st.columns([5, 1])
                note_str = f"  — {pos['note']}" if pos.get('note') else ''
                pc1.markdown(f"**{pos['ticker']}** &nbsp; {pos['qty']:.3f}주 @ {pos['avg_cost']:.2f}{note_str}")
                if pc2.button("삭제", key=f"pf_del_{pf_i}"):
                    st.session_state.portfolio.pop(pf_i)
                    if 'pf_res' in st.session_state: del st.session_state['pf_res']
                    st.rerun()
            st.divider()

            if st.button("📊 포트폴리오 분석 실행", type="primary", disabled=(total_w != 100)):
                pf_m_s, _, _ = macro_score()
                pf_rows = []
                pf_pb = st.progress(0); pf_pt = st.empty()
                for pf_idx, pos in enumerate(st.session_state.portfolio):
                    tk = pos['ticker']
                    pf_pt.text(f"분석 중: {tk} ({pf_idx+1}/{len(st.session_state.portfolio)})")
                    pf_pb.progress((pf_idx+1)/len(st.session_state.portfolio))
                    try:
                        pf_end = datetime.now(); pf_start = pf_end - timedelta(days=520)
                        pf_df = yf.download(tk, start=pf_start, end=pf_end, progress=False)
                        if isinstance(pf_df.columns, pd.MultiIndex): pf_df.columns = pf_df.columns.droplevel(1)
                        pf_df = pf_df.dropna(subset=['Close'])
                        if len(pf_df) < 30: raise ValueError("데이터 부족")
                        pf_ts, _ = technical_score(pf_df)
                        pf_fs, _ = fundamental_score(tk, pf_df)
                        pf_sc = pf_ts*(w_tech/100) + pf_fs*(w_fund/100) + pf_m_s*(w_macro/100)
                        pf_cp = float(pf_df['Close'].iloc[-1])
                        pf_is_krw = tk.endswith('.KS') or tk.endswith('.KQ')
                        pf_fp = (lambda krw: (lambda x: f"₩{x:,.0f}" if krw else f"${x:.2f}"))(pf_is_krw)
                        avg_c = pos['avg_cost']
                        pnl   = (pf_cp - avg_c) * pos['qty'] if avg_c > 0 else 0.0
                        pnl_p = (pf_cp - avg_c) / avg_c * 100 if avg_c > 0 else 0.0
                        val   = pf_cp * pos['qty']
                        pf_rows.append({
                            '_is_krw': pf_is_krw, '_val': val, '_sc': pf_sc, '_pnl': pnl,
                            '티커': tk, '수량': pos['qty'],
                            '매수가': pf_fp(avg_c) if avg_c > 0 else '-',
                            '현재가': pf_fp(pf_cp),
                            '평가금액': pf_fp(val),
                            '손익': (f"+{pf_fp(pnl)}" if pnl >= 0 else pf_fp(pnl)) if avg_c > 0 else '-',
                            '수익률': f"{pnl_p:+.1f}%" if avg_c > 0 else '-',
                            '종합점수': round(pf_sc, 1),
                            '신호': score_label(pf_sc),
                            '메모': pos.get('note', ''),
                        })
                    except Exception as pf_e:
                        pf_rows.append({
                            '티커': tk, '수량': pos['qty'], '매수가': '-', '현재가': '오류',
                            '평가금액': '-', '손익': '-', '수익률': '-',
                            '종합점수': 0, '신호': '오류', '메모': str(pf_e)[:30],
                        })
                pf_pb.empty(); pf_pt.empty()
                st.session_state.pf_res = pf_rows

            if 'pf_res' in st.session_state and st.session_state.pf_res:
                pf_rows = st.session_state.pf_res
                valid_pf = [r for r in pf_rows if '_val' in r]

                if valid_pf:
                    usd_pf = [r for r in valid_pf if not r.get('_is_krw')]
                    krw_pf = [r for r in valid_pf if r.get('_is_krw')]
                    avg_sc_pf = sum(r['_sc'] for r in valid_pf) / len(valid_pf)
                    pm1, pm2, pm3 = st.columns(3)
                    if usd_pf:
                        tot_usd = sum(r['_val'] for r in usd_pf)
                        pnl_usd = sum(r['_pnl'] for r in usd_pf)
                        pm1.metric("USD 평가금액", f"${tot_usd:,.2f}", f"P&L ${pnl_usd:+,.2f}")
                    if krw_pf:
                        tot_krw = sum(r['_val'] for r in krw_pf)
                        pnl_krw = sum(r['_pnl'] for r in krw_pf)
                        pm2.metric("KRW 평가금액", f"₩{tot_krw:,.0f}", f"P&L ₩{pnl_krw:+,.0f}")
                    pm3.metric("평균 종합점수", f"{avg_sc_pf:.1f}점", score_label(avg_sc_pf))
                    st.divider()

                disp_cols = ['티커','수량','매수가','현재가','평가금액','손익','수익률','종합점수','신호','메모']
                df_pf = pd.DataFrame(pf_rows)
                df_pf = df_pf[[c for c in disp_cols if c in df_pf.columns]]
                try:
                    df_pf_styled = df_pf.style.map(_style_score, subset=['종합점수'])
                except AttributeError:
                    df_pf_styled = df_pf.style.applymap(_style_score, subset=['종합점수'])
                st.dataframe(df_pf_styled, use_container_width=True, hide_index=True)

                if valid_pf and len(valid_pf) >= 2:
                    st.subheader("📊 비중 분포")
                    pie_cols = st.columns(2)
                    for pie_i, (ccy, c_rows) in enumerate([('USD', usd_pf), ('KRW', krw_pf)]):
                        if len(c_rows) >= 2:
                            fig_pie = go.Figure(go.Pie(
                                labels=[r['티커'] for r in c_rows],
                                values=[r['_val'] for r in c_rows],
                                hole=0.4, textinfo='label+percent',
                                textfont=dict(color='white', size=12),
                            ))
                            fig_pie.update_layout(
                                height=300, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                font=dict(color=TV_TEXT), margin=dict(l=0,r=0,t=30,b=0),
                                showlegend=False,
                                title=dict(text=f'{ccy} 비중', font=dict(color=TV_TEXT, size=13))
                            )
                            pie_cols[pie_i].plotly_chart(fig_pie, use_container_width=True)

                if valid_pf:
                    weak_pf   = [r['티커'] for r in valid_pf if r.get('_sc', 100) < 40]
                    strong_pf = [r['티커'] for r in valid_pf if r.get('_sc', 0) >= 75]
                    if weak_pf:   st.warning(f"⚠️ 매도 검토: **{', '.join(weak_pf)}** — 종합점수 40점 미만")
                    if strong_pf: st.success(f"🚀 강세 유지: **{', '.join(strong_pf)}** — 종합점수 75점 이상")

        st.caption("⚠️ 포지션 정보는 페이지 새로고침 시 초기화됩니다.")


if __name__ == "__main__":
    main()
