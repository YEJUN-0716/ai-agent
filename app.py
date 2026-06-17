import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import requests
import sqlite3
import os
import json
import warnings
warnings.filterwarnings('ignore')

st.set_page_config(page_title="종합 주식 분석 시스템", page_icon="📊", layout="wide")

TV_BG = TV_PAPER = '#131722'
TV_GRID = '#1e2334'
TV_BORDER = '#2a2e39'
TV_TEXT = '#b2b5be'
TV_UP = '#26a69a'
TV_DOWN = '#ef5350'

# ─────────────────────────────────────────────
# PORTFOLIO DB  (sqlite3 로컬 파일)
# ─────────────────────────────────────────────
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'portfolio.db')

def _db_init():
    with sqlite3.connect(_DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker   TEXT    NOT NULL,
                qty      REAL    NOT NULL DEFAULT 1,
                avg_cost REAL    NOT NULL DEFAULT 0,
                note     TEXT    NOT NULL DEFAULT '',
                added_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            )""")
        con.commit()

def _db_load():
    _db_init()
    with sqlite3.connect(_DB_PATH) as con:
        rows = con.execute(
            "SELECT id, ticker, qty, avg_cost, note FROM positions ORDER BY id").fetchall()
    return [{'id': r[0], 'ticker': r[1], 'qty': r[2],
             'avg_cost': r[3], 'note': r[4]} for r in rows]

def _db_add(ticker, qty, avg_cost, note):
    _db_init()
    with sqlite3.connect(_DB_PATH) as con:
        cur = con.execute(
            "INSERT INTO positions (ticker, qty, avg_cost, note) VALUES (?,?,?,?)",
            (ticker, qty, avg_cost, note))
        con.commit()
        return cur.lastrowid

def _db_delete(pos_id):
    with sqlite3.connect(_DB_PATH) as con:
        con.execute("DELETE FROM positions WHERE id=?", (pos_id,))
        con.commit()

# ─────────────────────────────────────────────
# SUPABASE STORAGE (Cloud 영속성)
# ─────────────────────────────────────────────

def _supabase_configured():
    try:
        return bool(st.secrets.get("SUPABASE_URL")) and bool(st.secrets.get("SUPABASE_KEY"))
    except Exception:
        return False

def _get_supabase():
    from supabase import create_client
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def _sb_load():
    sb = _get_supabase()
    res = sb.table("positions").select("*").order("id").execute()
    return [{'id': r['id'], 'ticker': r['ticker'], 'qty': float(r['qty']),
             'avg_cost': float(r['avg_cost']), 'note': r.get('note', '')}
            for r in res.data]

def _sb_add(ticker, qty, avg_cost, note):
    sb = _get_supabase()
    res = sb.table("positions").insert({
        'ticker': ticker, 'qty': qty, 'avg_cost': avg_cost, 'note': note
    }).execute()
    return res.data[0]['id'] if res.data else None

def _sb_delete(pos_id):
    sb = _get_supabase()
    sb.table("positions").delete().eq("id", pos_id).execute()

def db_load():
    if _supabase_configured():
        try:
            return _sb_load()
        except Exception:
            pass
    return _db_load()

def db_add(ticker, qty, avg_cost, note):
    if _supabase_configured():
        try:
            return _sb_add(ticker, qty, avg_cost, note)
        except Exception:
            pass
    return _db_add(ticker, qty, avg_cost, note)

def db_delete(pos_id):
    if _supabase_configured():
        try:
            _sb_delete(pos_id)
            return
        except Exception:
            pass
    _db_delete(pos_id)

# ─────────────────────────────────────────────
# 통합 주식 데이터 다운로드
# ─────────────────────────────────────────────

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

    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df

PRESETS = {
    '미국 대형주':  ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA','JPM','V','JNJ'],
    '한국 대형주':  ['005930.KS','000660.KS','035420.KS','005380.KS','051910.KS','006400.KS'],
    '반도체':       ['NVDA','AMD','INTC','TSM','ASML','QCOM','000660.KS','005930.KS'],
    'ETF':          ['SPY','QQQ','IWM','GLD','TLT','EEM','069500.KS','114800.KS'],
}

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

def run_screener(tickers, w_tech, w_fund, w_macro, prog_bar=None, prog_text=None):
    m_s, _, _ = macro_score()
    results = []
    for i, ticker in enumerate(tickers):
        if prog_text: prog_text.text(f"분석 중: {ticker} ({i+1}/{len(tickers)})")
        if prog_bar:  prog_bar.progress((i+1)/len(tickers))
        try:
            end = datetime.now(); start = end - timedelta(days=520)
            df  = download_stock(ticker, start=start, end=end)
            if df.empty: continue
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

def bt_signals_full(df):
    """technical_score 9개 지표를 전체 기간 벡터화 계산.
    bt_signals(MA+RSI+MACD 3개) 대신 실제 scoring과 동일한 9개 지표를 사용."""
    p, h, l, v = df['Close'], df['High'], df['Low'], df['Volume']

    # ── MA 정렬 (15%) ─────────────────────────
    ma20, ma60, ma120 = p.rolling(20).mean(), p.rolling(60).mean(), p.rolling(120).mean()
    ma_s = ((p > ma20)*20 + (p > ma60)*20 + (p > ma120)*20 +
            (ma20 > ma60)*20 + (ma60 > ma120)*20).astype(float)
    gc = (ma20 > ma60) & (ma20.shift(5) <= ma60.shift(5))
    dc = (ma20 < ma60) & (ma20.shift(5) >= ma60.shift(5))
    ma_s = (ma_s + gc.astype(float)*15 - dc.astype(float)*15).clip(0, 100).fillna(50)

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
    f_score/m_score 전달 시 실전과 동일한 종합점수 기반으로 매매.
    """
    tech_sigs = bt_signals_full(df)
    if f_score is not None and m_score is not None and w_tech < 100:
        sigs = tech_sigs * (w_tech / 100) + f_score * (w_fund / 100) + m_score * (w_macro / 100)
    else:
        sigs = tech_sigs
    prices = df['Close'].values
    dates  = df.index
    n      = len(df)

    capital   = float(initial_capital)
    shares    = 0.0
    in_pos    = False
    entry_val = 0.0   # 매수 시점 투입 자본 (수수료·슬리피지 후)
    equity    = np.full(n, float(initial_capital))
    trades    = []

    for i in range(20, n):
        px  = float(prices[i])
        sig = float(sigs.iloc[i])
        if not in_pos and sig > buy_th:
            buy_px   = px * (1 + slippage)            # 슬리피지 적용 체결가
            fee      = capital * commission             # 매수 수수료
            shares   = (capital - fee) / buy_px
            entry_val = capital                        # 수수료 차감 전 투입액 기준
            capital  = 0.0
            in_pos   = True
            trades.append({'날짜': dates[i], '구분': '🟢 매수',
                           '가격': round(px, 2), '체결가(수수료+슬리피지)': round(buy_px*(1+commission/(1+slippage+1e-9)), 2),
                           '신호': round(sig, 1), '수익률': ''})
        elif in_pos and sig < sell_th:
            sell_px  = px * (1 - slippage)            # 슬리피지 적용 체결가
            gross    = shares * sell_px
            fee      = gross * commission
            net      = gross - fee
            pnl      = (net / entry_val - 1) * 100
            capital  = net
            shares   = 0.0
            in_pos   = False
            trades.append({'날짜': dates[i], '구분': '🔴 매도',
                           '가격': round(px, 2), '체결가(수수료+슬리피지)': round(sell_px*(1-commission), 2),
                           '신호': round(sig, 1), '수익률': f"{pnl:+.2f}%"})
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
    daily_ret = eq_s.pct_change().dropna()
    sharpe    = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    calmar    = abs(cagr / mdd) if mdd < 0 else 0.0

    sells = [t for t in trades if '매도' in t['구분']]
    pnls  = []
    for t in sells:
        try: pnls.append(float(t['수익률'].replace('%', '').replace('+', '')))
        except: pass
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p <= 0]
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

# ─────────────────────────────────────────────
# ALPACA REAL-TIME DATA
# ─────────────────────────────────────────────
_ALPACA_DATA = "https://data.alpaca.markets/v2"

def _alpaca_headers(key, secret):
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

def _get_alpaca_keys():
    """secrets → env → session_state 순서로 Alpaca 키 조회."""
    try:
        k = st.secrets.get("ALPACA_API_KEY", "")
        s = st.secrets.get("ALPACA_SECRET_KEY", "")
        if k and s:
            return k, s
    except Exception:
        pass
    k = os.environ.get("ALPACA_API_KEY", "")
    s = os.environ.get("ALPACA_SECRET_KEY", "")
    if k and s:
        return k, s
    return (st.session_state.get('alpaca_key', ''),
            st.session_state.get('alpaca_secret', ''))

def get_alpaca_quote(symbol, key, secret):
    """최신 호가 (bid/ask/spread). IEX 피드 사용 (무료)."""
    try:
        r = requests.get(
            f"{_ALPACA_DATA}/stocks/{symbol}/quotes/latest",
            headers=_alpaca_headers(key, secret),
            params={"feed": "iex"}, timeout=5)
        if r.status_code == 200:
            q = r.json().get('quote', {})
            ap = q.get('ap', 0) or 0
            bp = q.get('bp', 0) or 0
            return {
                'ask':       ap,
                'bid':       bp,
                'ask_size':  q.get('as', 0),
                'bid_size':  q.get('bs', 0),
                'spread':    ap - bp,
                'spread_pct': (ap - bp) / bp * 100 if bp > 0 else 0,
                'timestamp': q.get('t', ''),
            }
        return {'error': f"HTTP {r.status_code}: {r.text[:80]}"}
    except Exception as e:
        return {'error': str(e)[:80]}

def get_alpaca_bars(symbol, key, secret, timeframe='5Min', limit=100):
    """분봉 데이터 (오늘 장중). IEX 피드 사용."""
    try:
        r = requests.get(
            f"{_ALPACA_DATA}/stocks/{symbol}/bars",
            headers=_alpaca_headers(key, secret),
            params={"timeframe": timeframe, "limit": limit,
                    "feed": "iex", "sort": "asc"}, timeout=10)
        if r.status_code != 200:
            return None
        bars = r.json().get('bars', [])
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df['t'] = pd.to_datetime(df['t'], utc=True).dt.tz_convert('America/New_York')
        df = df.set_index('t')
        df = df.rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low',
                                 'c': 'Close', 'v': 'Volume'})
        return df[['Open', 'High', 'Low', 'Close', 'Volume']]
    except Exception:
        return None

def test_alpaca_connection(key, secret):
    """연결 테스트 — AAPL 최신 호가 조회로 확인."""
    result = get_alpaca_quote('AAPL', key, secret)
    if result and 'error' not in result:
        return True, f"연결 성공 ✅  AAPL ask={result['ask']:.2f}  bid={result['bid']:.2f}"
    return False, result.get('error', '알 수 없는 오류') if result else '연결 실패'


def run_portfolio_backtest(tickers, weights, period_days, buy_th, sell_th,
                           initial_capital, commission, slippage,
                           w_tech=100, w_fund=0, w_macro=0):
    """멀티 종목 포트폴리오 백테스트.
    각 종목에 weight 비율만큼 자본 배분 후 개별 run_backtest 실행,
    포트폴리오 전체 자산 곡선과 합산 지표를 반환."""
    end   = datetime.now()
    start = end - timedelta(days=period_days + 60)
    pf_m_score, _, _ = macro_score() if w_fund > 0 else (None, {}, {})

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
            if w_fund > 0:
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

def check_alerts(watchlist, token, chat_id, threshold, w_tech, w_fund, w_macro):
    m_s, _, _ = macro_score()
    sent = []
    for ticker in watchlist:
        try:
            end = datetime.now(); start = end - timedelta(days=520)
            df  = download_stock(ticker, start=start, end=end)
            if df.empty: continue
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
        dt_e1 = piv['S1']
        dt_be1 = '피봇 S1'
        dt_strategy = '⏳ S1 지지 확인 후 진입'
    else:
        dt_e1 = piv['S1']
        dt_be1 = '피봇 S1'
        dt_strategy = '🔍 S1 회복/지지 확인 후 진입'

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
    sw_levels = [fib['38.2%'], fib['50.0%'], fib['61.8%'], ma20, ma60, low20]
    sw_sup = sorted(sw_levels, key=lambda x: (abs(x - cp), -x))
    sw_res = sorted([x for x in [fib['23.6%'], fib['확장 127.2%'], fib['확장 161.8%'],
                                  sw_high, ma120] if x > cp*1.001])

    sw_e1   = sw_sup[0] if sw_sup else cp * 0.96
    sw_e2_candidates = sorted([x for x in sw_levels if x < sw_e1 * 0.999], reverse=True)
    sw_e2   = sw_e2_candidates[0] if sw_e2_candidates else sw_e1 * 0.96
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
        st.divider()
        st.subheader("🛡️ 실전 리스크 설정")
        acct_capital = st.number_input("계좌 기준 금액", min_value=100_000, value=10_000_000,
                                       step=1_000_000, help="포지션 사이징 계산 기준 금액입니다.")
        risk_per_trade = st.slider("1회 거래 허용 손실 (%)", 0.1, 5.0, 1.0, 0.1,
                                   help="손절 시 계좌에서 감수할 최대 손실 비율입니다.")
        max_position_pct = st.slider("종목당 최대 비중 (%)", 1, 100, 20, 1,
                                     help="한 종목에 투입할 수 있는 최대 계좌 비중입니다.")
        min_rr = st.slider("최소 손익비 (R)", 0.5, 5.0, 1.5, 0.1,
                           help="1차 목표가 기준 최소 보상/위험 비율입니다.")

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
            df = download_stock(ticker, start=start_dt, end=end_dt)

            if df.empty:
                st.error(f"'{ticker}' 데이터를 찾을 수 없습니다.")
                prog.empty(); msg.empty()
            else:
                df = df.dropna(subset=['Close'])

                if len(df) < 30:
                    st.error("데이터 부족 (30일 미만).")
                    prog.empty(); msg.empty()
                else:
                    prog.progress(15); msg.text("📈 차트·파동 분석 중...")
                    t_score, t_det  = technical_score(df)
                    candle_pats     = detect_candle_patterns(df)
                    mom_data        = calc_momentum(df)
                    ic_data         = calc_indicator_ics(df)
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
                    # 국면 조정 기술적 점수 & 종합점수
                    t_score_adj = regime_adjusted_technical(t_det, regime)
                    total_adj   = t_score_adj*(w_tech/100) + f_score*(w_fund/100) + m_score*(w_macro/100)
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

                    # ── 이익발표일 조회 ──────────────────────────
                    earn_str = ""
                    try:
                        cal = yf.Ticker(ticker).calendar
                        if cal is not None and not (isinstance(cal, dict) and len(cal) == 0):
                            if isinstance(cal, dict):
                                ed = cal.get('Earnings Date')
                                if ed:
                                    ed = ed[0] if isinstance(ed, list) else ed
                                    days_left = (pd.Timestamp(ed).date() - datetime.now().date()).days
                                    earn_str = f"다음 실적발표: **{pd.Timestamp(ed).strftime('%Y-%m-%d')}** ({days_left:+d}일)"
                                    if 0 <= days_left <= 14:
                                        earn_str += " 🔔"
                            elif isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
                                ed = cal.loc['Earnings Date'].iloc[0]
                                days_left = (pd.Timestamp(ed).date() - datetime.now().date()).days
                                earn_str = f"다음 실적발표: **{pd.Timestamp(ed).strftime('%Y-%m-%d')}** ({days_left:+d}일)"
                                if 0 <= days_left <= 14:
                                    earn_str += " 🔔"
                    except Exception:
                        pass

                    st.header(f"{name}  `{ticker}`")
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("현재가", fmt_p(cp), f"{chg:+.2f}%")
                    c2.metric("52주 고가", fmt_p(float(df['High'].tail(252).max())))
                    c3.metric("52주 저가", fmt_p(float(df['Low'].tail(252).min())))
                    c4.metric("분석 기준일", end_dt.strftime("%Y-%m-%d"))
                    if earn_str:
                        st.caption(earn_str)
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

                    # 국면 조정 점수 비교 카드
                    regime_label_map = {'bull':'🐂 강세장 가중치', 'bear':'🐻 약세장 가중치', 'neutral':'➡️ 중립장 가중치'}
                    regime_color_map = {'bull':'#26a69a', 'bear':'#ef5350', 'neutral':'#b2b5be'}
                    diff = total_adj - total
                    diff_str = f"{diff:+.1f}점"
                    st.markdown(
                        f"<div style='background:{regime_color_map[regime]}18;"
                        f"border:1px solid {regime_color_map[regime]}55;"
                        f"border-radius:8px;padding:10px 16px;"
                        f"display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='color:{regime_color_map[regime]};font-weight:600'>"
                        f"{regime_label_map[regime]} 적용 시</span>"
                        f"<span style='font-size:20px;font-weight:bold;color:{score_color(total_adj)}'>"
                        f"{total_adj:.1f}점 &nbsp;"
                        f"<span style='font-size:13px;color:{'#26a69a' if diff>=0 else '#ef5350'}'>"
                        f"({diff_str})</span></span></div>",
                        unsafe_allow_html=True)
                    # ── ⚡ Alpaca 실시간 시세 ─────────────────────
                    _al_key, _al_sec = _get_alpaca_keys()
                    if _al_key and _al_sec and not is_krw:
                        with st.expander("⚡ 실시간 시세 (Alpaca)", expanded=True):
                            al_r_col, al_btn_col = st.columns([4, 1])
                            al_r_col.caption(f"Alpaca IEX 피드 — 무료 플랜 15분 지연 / 유료 플랜 실시간")
                            if al_btn_col.button("🔄 새로고침", key="alpaca_refresh"):
                                if 'alpaca_cache' in st.session_state:
                                    del st.session_state['alpaca_cache']

                            cache_key = f"alpaca_{ticker}"
                            if cache_key not in st.session_state:
                                with st.spinner("실시간 데이터 로딩..."):
                                    _aq = get_alpaca_quote(ticker, _al_key, _al_sec)
                                    _ab = get_alpaca_bars(ticker, _al_key, _al_sec,
                                                          timeframe='5Min', limit=80)
                                st.session_state[cache_key] = {'quote': _aq, 'bars': _ab,
                                                               'fetched': datetime.now()}
                            cached = st.session_state[cache_key]
                            aq = cached['quote']
                            ab = cached['bars']
                            fetched_at = cached['fetched'].strftime('%H:%M:%S')

                            if aq and 'error' not in aq:
                                aq_c1, aq_c2, aq_c3, aq_c4 = st.columns(4)
                                aq_c1.metric("매도 호가 (Ask)", f"${aq['ask']:.2f}",
                                             f"{aq['ask_size']}주")
                                aq_c2.metric("매수 호가 (Bid)", f"${aq['bid']:.2f}",
                                             f"{aq['bid_size']}주")
                                aq_c3.metric("스프레드",
                                             f"${aq['spread']:.3f}",
                                             f"{aq['spread_pct']:.3f}%")
                                ts_str = str(aq['timestamp'])[:19].replace('T', ' ')
                                aq_c4.metric("호가 시각", ts_str[:10], ts_str[11:19])
                            elif aq and 'error' in aq:
                                st.warning(f"호가 조회 실패: {aq['error']}")

                            if ab is not None and not ab.empty:
                                fig_in = go.Figure(go.Candlestick(
                                    x=ab.index, open=ab['Open'], high=ab['High'],
                                    low=ab['Low'], close=ab['Close'],
                                    increasing_line_color=TV_UP,
                                    decreasing_line_color=TV_DOWN,
                                    name='5분봉'))
                                fig_in.update_layout(
                                    height=280, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                    font=dict(color=TV_TEXT),
                                    title=dict(text=f"{ticker} 장중 5분봉  (조회: {fetched_at})",
                                               font=dict(size=12)),
                                    xaxis=dict(gridcolor=TV_GRID, rangeslider_visible=False,
                                               type='category',
                                               tickformat='%H:%M',
                                               nticks=12),
                                    yaxis=dict(gridcolor=TV_GRID, side='right'),
                                    margin=dict(l=0, r=60, t=35, b=0))
                                st.plotly_chart(fig_in, use_container_width=True)
                            else:
                                st.info("분봉 데이터 없음 — 장 중에만 표시됩니다.")
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
                        with st.expander("🔬 지표 예측력 분석 (IC · 정보계수)"):
                            ics, suggested_w, default_w = ic_data
                            ic_items = sorted(ics.items(), key=lambda x: abs(x[1]), reverse=True)
                            ic_labels = [k for k, _ in ic_items]
                            ic_values = [v for _, v in ic_items]
                            ic_colors = [TV_UP if v >= 0 else TV_DOWN for v in ic_values]
                            fig_ic = go.Figure()
                            fig_ic.add_trace(go.Bar(
                                x=ic_labels, y=ic_values,
                                marker_color=ic_colors,
                                text=[f"{v:+.3f}" for v in ic_values],
                                textposition='outside', textfont=dict(size=10)))
                            fig_ic.add_hline(y=0,    line_color=TV_TEXT,    line_width=1, opacity=0.5)
                            fig_ic.add_hline(y=0.05,  line_color='#ff9800', line_width=1, line_dash='dash', opacity=0.6)
                            fig_ic.add_hline(y=-0.05, line_color='#ff9800', line_width=1, line_dash='dash', opacity=0.6)
                            fig_ic.update_layout(
                                title=dict(text=f"20일 후 수익률 예측력 (IC)", font=dict(size=11)),
                                height=270, plot_bgcolor=TV_BG, paper_bgcolor=TV_PAPER,
                                font=dict(color=TV_TEXT),
                                xaxis=dict(gridcolor=TV_GRID, tickfont=dict(size=10)),
                                yaxis=dict(gridcolor=TV_GRID, zeroline=False),
                                margin=dict(l=5, r=5, t=35, b=5), showlegend=False)
                            st.plotly_chart(fig_ic, use_container_width=True)
                            st.caption("주황 점선 = |IC| 0.05 기준 (유효 예측력) | IC > 0: 점수 높을수록 수익↑")
                            st.divider()
                            st.caption("**지표별 IC vs 현재·권장 가중치**")
                            w_rows = []
                            for k in ic_labels:
                                ic_v = ics[k]
                                grade = '강함 💪' if abs(ic_v) >= 0.10 else ('보통 🔶' if abs(ic_v) >= 0.05 else '약함 ❌')
                                w_rows.append({
                                    '지표': k,
                                    'IC': f"{ic_v:+.3f}",
                                    '예측력': grade,
                                    'IC 권장(%)': f"{suggested_w.get(k,0)*100:.1f}",
                                    '현재(%)':    f"{default_w.get(k,0)*100:.1f}",
                                })
                            st.dataframe(pd.DataFrame(w_rows), use_container_width=True, hide_index=True)
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
                                    st.plotly_chart(fig_sr, use_container_width=True)
                            else:
                                st.info("섹터 데이터를 가져올 수 없습니다.")
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
                                f"<div style='background:#151923;border:1px solid {verdict_color}66;"
                                f"border-radius:8px;padding:12px 14px;margin-bottom:8px'>"
                                f"<div style='display:flex;justify-content:space-between;gap:10px'>"
                                f"<b>{plan['label']} 실행 판정</b>"
                                f"<b style='color:{verdict_color}'>{plan['verdict']}</b></div>"
                                f"<div style='color:#999;font-size:12px;margin-top:6px'>{notes_text}</div>"
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
                    st.divider()

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
        st.caption("사이드바 가중치와 동일한 **종합점수**(차트+재무+매크로) 기반으로 과거 성과를 검증합니다. 수수료·슬리피지 반영.")

        c1, c2, c3 = st.columns(3)
        bt_ticker  = c1.text_input("티커", "AAPL").strip().upper()
        bt_period  = c2.selectbox("기간", ["1년","2년","3년","5년"], index=1)
        bt_capital = c3.number_input("초기자금 (원)", value=10_000_000, step=1_000_000, min_value=100_000)

        c4, c5 = st.columns(2)
        buy_th  = c4.slider("매수 임계값", 50, 90, 65, 5, help="신호가 이 점수를 넘으면 매수")
        sell_th = c5.slider("매도 임계값", 20, 60, 45, 5, help="신호가 이 점수 아래로 내려오면 매도")

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
            with st.spinner("백테스팅 실행 중..."):
                end_dt2   = datetime.now()
                start_dt2 = end_dt2 - timedelta(days=period_days[bt_period]+60)
                bt_df = download_stock(bt_ticker, start=start_dt2, end=end_dt2)
                bt_df = bt_df.dropna(subset=['Close'])

            if bt_df.empty or len(bt_df) < 60:
                st.error("데이터가 부족합니다.")
            else:
                bt_f_score, bt_m_score = None, None
                if total_w == 100 and w_fund > 0:
                    with st.spinner("재무·매크로 점수 산출 중..."):
                        bt_f_score, _ = fundamental_score(bt_ticker, bt_df)
                        bt_m_score, _, _ = macro_score()

                metrics, eq_df, trades_df = run_backtest(
                    bt_df, buy_th, sell_th, bt_capital, bt_commission, bt_slippage,
                    f_score=bt_f_score, m_score=bt_m_score,
                    w_tech=w_tech, w_fund=w_fund, w_macro=w_macro)

                if bt_f_score is not None:
                    st.info(f"📊 종합점수 백테스트 — 차트 {w_tech}% (동적) + 재무 {bt_f_score:.0f}점 × {w_fund}% + 매크로 {bt_m_score:.0f}점 × {w_macro}%")

                # ── 지표 12개 (3행×4열) ──────────────────
                m_keys = list(metrics.keys()); m_vals = list(metrics.values())
                for row_start in range(0, len(m_keys), 4):
                    row_keys = m_keys[row_start:row_start+4]
                    row_vals = m_vals[row_start:row_start+4]
                    row_cols = st.columns(len(row_keys))
                    for ci, (k, v) in enumerate(zip(row_keys, row_vals)):
                        row_cols[ci].metric(k, v)
                st.divider()

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
                                mode='markers', marker=dict(symbol='triangle-up', size=12, color=TV_UP),
                                showlegend=False))
                    for sdate in sells_df['날짜']:
                        row = eq_df[eq_df['날짜'] == sdate]
                        if not row.empty:
                            fig_eq.add_trace(go.Scatter(x=[sdate], y=[float(row['전략'].iloc[0])],
                                mode='markers', marker=dict(symbol='triangle-down', size=12, color=TV_DOWN),
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

                # ── 📊 점수-수익률 상관관계 검증 ─────────────
                st.divider()
                st.subheader("📊 점수-수익률 상관관계 검증")
                st.caption(
                    "신호 점수가 실제 미래 수익률과 얼마나 연관되는지 검증합니다. "
                    "**IC(정보계수)** > 0 이면 점수가 높을수록 수익률이 높은 경향이 있음을 의미합니다.")

                corr_results = analyze_score_correlation(bt_df)

                # IC 카드 3개
                ic_cols = st.columns(3)
                for ci, cr in enumerate(corr_results):
                    ic_val = cr['IC']
                    ic_color = "normal" if abs(ic_val) < 0.05 else ("inverse" if ic_val < 0 else "off")
                    ic_cols[ci].metric(
                        f"IC ({cr['horizon']}일 후 수익률)",
                        f"{ic_val:+.3f}",
                        delta=("유효 신호 ✅" if abs(ic_val) >= 0.05 else "신호 미약 ⚠️"),
                        delta_color=ic_color)

                st.caption("IC 해석: |IC| ≥ 0.05 → 약한 예측력 / ≥ 0.10 → 의미있는 예측력 / ≥ 0.15 → 강한 예측력")
                st.divider()

                # 20일 기준 점수 구간별 평균 수익률 막대차트
                cr20 = next((r for r in corr_results if r['horizon'] == 20), corr_results[-1])
                bs   = cr20['bucket_stats'].dropna(subset=['평균수익률(%)'])
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
                    st.plotly_chart(fig_bar, use_container_width=True)

                    # 구간별 상세 통계 테이블
                    with st.expander("📋 구간별 상세 통계"):
                        display_bs = bs.copy()
                        display_bs['평균수익률(%)'] = display_bs['평균수익률(%)'].map(lambda x: f"{x:+.2f}%")
                        display_bs['표준편차']       = display_bs['표준편차'].map(lambda x: f"{x:.2f}%")
                        st.dataframe(display_bs, use_container_width=True, hide_index=True)

                        # 5일, 10일 IC 도 표로
                        ic_summary = pd.DataFrame([
                            {'기간': f"{cr['horizon']}일 후", 'IC': f"{cr['IC']:+.3f}",
                             '예측력': ('강함 💪' if abs(cr['IC']) >= 0.15 else
                                       ('보통 🔶' if abs(cr['IC']) >= 0.10 else
                                        ('약함 🔸' if abs(cr['IC']) >= 0.05 else '없음 ❌')))}
                            for cr in corr_results
                        ])
                        st.dataframe(ic_summary, use_container_width=True, hide_index=True)

                # ── 📐 워크-포워드 검증 ──────────────────────
                st.divider()
                st.subheader("📐 워크-포워드 검증 (과적합 진단)")
                st.caption(
                    "전체 기간을 **학습 70%** / **검증 30%** 로 분리해 동일 전략을 각각 실행합니다. "
                    "학습 성과와 검증 성과 차이가 클수록 **과적합** 가능성이 높습니다.")

                if st.button("📐 워크-포워드 검증 실행", key="wf_btn"):
                    wf_results, wf_overfit, wf_split_date = run_walkforward(
                        bt_df, buy_th, sell_th, bt_capital, bt_commission, bt_slippage,
                        f_score=bt_f_score, m_score=bt_m_score,
                        w_tech=w_tech, w_fund=w_fund, w_macro=w_macro)

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

            pbt_c1, pbt_c2, pbt_c3 = st.columns(3)
            pbt_period  = pbt_c1.selectbox("기간", ["1년","2년","3년","5년"], index=1, key="pbt_period")
            pbt_capital = pbt_c2.number_input("초기자금 (원)", value=10_000_000,
                                               step=1_000_000, min_value=100_000, key="pbt_cap")
            pbt_buy_th  = pbt_c3.slider("매수 임계값", 50, 90, 65, 5, key="pbt_buy")

        pbt_period_days = {"1년": 365, "2년": 730, "3년": 1095, "5년": 1825}

        if st.button("📦 포트폴리오 백테스트 실행", type="primary", key="pbt_run"):
            if not pbt_tickers:
                st.error("종목을 입력해주세요.")
            else:
                with st.spinner(f"{len(pbt_tickers)}개 종목 다운로드 및 백테스트 실행 중..."):
                    pbt_results, pbt_eq, pbt_spy = run_portfolio_backtest(
                        pbt_tickers, pbt_weights,
                        pbt_period_days[pbt_period],
                        pbt_buy_th, sell_th,
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
                    st.dataframe(pd.DataFrame(sum_rows), use_container_width=True, hide_index=True)

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
                    st.plotly_chart(fig_pbt, use_container_width=True)

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

    # ── Tab 4: 알림 ──────────────────────────────────
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

        # ── ⚡ Alpaca 실시간 데이터 설정 ──────────────
        st.divider()
        st.subheader("⚡ Alpaca 실시간 데이터 연동")
        st.caption(
            "Alpaca API를 연결하면 **미국 주식 실시간 호가(bid/ask)** 및 "
            "**장중 분봉 차트**를 Tab 1에서 확인할 수 있습니다. "
            "무료 계정(IEX 피드) 기준 15분 지연 데이터 제공.")

        with st.expander("📋 Alpaca 가입 및 키 발급 방법"):
            st.markdown("""
            1. [alpaca.markets](https://alpaca.markets) 에서 무료 계정 생성
            2. **Paper Trading** 계정으로 시작 (신용카드 불필요)
            3. 대시보드 → **API Keys** → `Generate New Key`
            4. API Key ID 와 Secret Key 를 아래에 입력
            5. 유료 플랜(Unlimited, $9/월) 가입 시 진짜 실시간 데이터 제공
            """)

        al_c1, al_c2 = st.columns(2)
        al_key    = al_c1.text_input("Alpaca API Key ID",
                                      value=st.session_state.get('alpaca_key', ''),
                                      placeholder="PKXXXXXXXXXXXXXXXXXXXXXXXX",
                                      type="password")
        al_secret = al_c2.text_input("Alpaca Secret Key",
                                      value=st.session_state.get('alpaca_secret', ''),
                                      placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                                      type="password")

        al_c3, al_c4 = st.columns(2)
        if al_c3.button("🔌 연결 테스트", key="alpaca_test"):
            if not al_key or not al_secret:
                st.error("API Key와 Secret Key를 모두 입력해주세요.")
            else:
                with st.spinner("연결 테스트 중..."):
                    ok, msg = test_alpaca_connection(al_key, al_secret)
                if ok:
                    st.success(msg)
                    st.session_state['alpaca_key']    = al_key
                    st.session_state['alpaca_secret'] = al_secret
                else:
                    st.error(f"연결 실패: {msg}")

        if al_c4.button("💾 키 저장 (세션)", key="alpaca_save"):
            if al_key and al_secret:
                st.session_state['alpaca_key']    = al_key
                st.session_state['alpaca_secret'] = al_secret
                st.success("✅ 세션에 저장됨 — Tab 1 종목 분석 시 실시간 위젯이 표시됩니다.")
            else:
                st.warning("키를 입력한 후 저장하세요.")

        if st.session_state.get('alpaca_key'):
            st.info(f"⚡ Alpaca 연결 중 — Tab 1에서 미국 주식 분석 시 실시간 호가가 표시됩니다.")

        # ── 🤖 Claude AI 뉴스 감성 분석 설정 ─────────
        st.divider()
        st.subheader("🤖 Claude AI 뉴스 감성 분석")
        st.caption(
            "Anthropic Claude API를 연결하면 뉴스 헤드라인의 감성을 AI가 정밀 분석합니다. "
            "미설정 시 키워드 기반 분석이 사용됩니다.")

        with st.expander("📋 API 키 발급 방법"):
            st.markdown("""
1. [console.anthropic.com](https://console.anthropic.com) 에서 계정 생성
2. **API Keys** → `Create Key` 클릭
3. 발급된 키(`sk-ant-...`)를 아래에 입력하거나 Streamlit Secrets에 등록

**Streamlit Cloud Secrets 방식** (권장):
```toml
ANTHROPIC_API_KEY = "sk-ant-api03-..."
```
""")

        ant_key = st.text_input("Anthropic API Key",
                                value=st.session_state.get('anthropic_key', ''),
                                placeholder="sk-ant-api03-...",
                                type="password", key="ant_key_input")

        ant_c1, ant_c2 = st.columns(2)
        if ant_c1.button("🔌 연결 테스트", key="ant_test"):
            if not ant_key:
                st.error("API Key를 입력해주세요.")
            else:
                try:
                    import anthropic
                    client = anthropic.Anthropic(api_key=ant_key)
                    resp = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=20,
                        messages=[{"role": "user", "content": "Say OK"}])
                    st.success(f"✅ 연결 성공 — {resp.model}")
                    st.session_state['anthropic_key'] = ant_key
                except Exception as e:
                    st.error(f"연결 실패: {str(e)[:80]}")

        if ant_c2.button("💾 키 저장 (세션)", key="ant_save"):
            if ant_key:
                st.session_state['anthropic_key'] = ant_key
                st.success("✅ 세션에 저장됨 — 종목 분석 시 AI 뉴스 감성 분석이 활성화됩니다.")
            else:
                st.warning("키를 입력한 후 저장하세요.")

        if _get_anthropic_key():
            st.info("🤖 Claude AI 연결 중 — Tab 1 뉴스 감성 분석에 AI가 적용됩니다.")

    # ── Tab 5: 포트폴리오 ────────────────────────
    with tab5:
        st.subheader("💼 포트폴리오 관리")
        st.caption("보유 종목의 손익과 분석 점수를 한눈에 확인하세요. 포지션은 **로컬 DB에 영구 저장**됩니다.")

        # DB에서 최초 1회 로드
        if 'pf_db_loaded' not in st.session_state:
            st.session_state.portfolio    = db_load()
            st.session_state.pf_db_loaded = True
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
                        new_id = db_add(tk_in, float(pf_q), float(pf_c), pf_nt)
                        st.session_state.portfolio.append(
                            {'id': new_id, 'ticker': tk_in, 'qty': float(pf_q),
                             'avg_cost': float(pf_c), 'note': pf_nt})
                        st.success(f"✅ {tk_in} 추가됨 (DB 저장 완료)")
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
                    if pos.get('id'):
                        db_delete(pos['id'])
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
                        pf_df = download_stock(tk, start=pf_start, end=pf_end)
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

        if _supabase_configured():
            st.caption("☁️ Supabase 연동 중 — 데이터가 클라우드에 영구 보존됩니다.")
        else:
            st.caption(f"💾 로컬 SQLite 사용 중 (`{_DB_PATH}`). Streamlit Cloud 재배포 시 초기화될 수 있습니다.")
            with st.expander("☁️ Supabase 연동으로 영구 보존하기 (무료)"):
                st.markdown("""
**설정 방법 (5분 소요)**

**1단계: Supabase 가입 & 프로젝트 생성**
1. [supabase.com](https://supabase.com) → **Start your project** → GitHub 로그인
2. **New Project** → 이름: `stock-analyzer` → 비밀번호 설정 → **Create**
3. 잠시 대기 (프로젝트 생성 1~2분)

**2단계: 테이블 생성**
1. 왼쪽 **SQL Editor** 클릭 → 아래 SQL 붙여넣기 → **Run**:

```sql
CREATE TABLE positions (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker   TEXT NOT NULL,
    qty      DOUBLE PRECISION NOT NULL DEFAULT 1,
    avg_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
    note     TEXT NOT NULL DEFAULT '',
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**3단계: API 키 복사**
1. 왼쪽 **Project Settings** → **API**
2. `Project URL` 과 `anon public` 키 복사

**4단계: Streamlit Secrets 등록**
1. [share.streamlit.io](https://share.streamlit.io) → 앱 → **Settings** → **Secrets**:

```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "eyJhbGci..."
```

2. **Save** → 앱 재시작 → 완료!
""")


if __name__ == "__main__":
    main()
