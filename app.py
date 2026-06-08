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

    ma20  = p.rolling(20).mean()
    ma60  = p.rolling(60).mean()
    ma120 = p.rolling(120).mean()
    cp, m20, m60, m120 = float(p.iloc[-1]), float(ma20.iloc[-1]), float(ma60.iloc[-1]), float(ma120.iloc[-1])
    ma = (20 if cp>m20 else 0)+(20 if cp>m60 else 0)+(20 if cp>m120 else 0)+(20 if m20>m60 else 0)+(20 if m60>m120 else 0)
    if len(ma20) >= 5:
        if m20 > m60 and float(ma20.iloc[-5]) <= float(ma60.iloc[-5]):  ma = min(ma+15, 100)
        elif m20 < m60 and float(ma20.iloc[-5]) >= float(ma60.iloc[-5]): ma = max(ma-15, 0)
    det['MA정렬'] = float(ma)

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
    det['RSI'] = float(rsi)
    det['RSI값'] = round(rv, 1)

    ml, sl2, hist = calc_macd(p)
    ch  = float(hist.iloc[-1])
    ph2 = float(hist.iloc[-2]) if len(hist) >= 2 else 0
    macd_v = 50
    if float(ml.iloc[-1]) > float(sl2.iloc[-1]): macd_v += 20
    if ch > 0:   macd_v += 15
    if ch > ph2: macd_v += 15
    else:         macd_v -= 10
    det['MACD'] = float(np.clip(macd_v, 0, 100))

    bb_u, _, bb_l2 = calc_bb(p)
    rng = float(bb_u.iloc[-1]) - float(bb_l2.iloc[-1]) + 1e-9
    pos = (cp - float(bb_l2.iloc[-1])) / rng
    if 0.4 <= pos <= 0.8:    bb = 70
    elif 0.8 < pos <= 0.95:  bb = 85
    elif pos > 0.95:          bb = 45
    elif 0.2 <= pos < 0.4:   bb = 45
    else:                     bb = 30
    det['볼린저밴드'] = float(bb)

    vr  = float(v.iloc[-1]) / (float(v.rolling(20).mean().iloc[-1]) + 1e-9)
    pc  = (cp - float(p.iloc[-5])) / (float(p.iloc[-5]) + 1e-9)
    if pc > 0 and vr > 1.2:   vol = 80
    elif pc > 0 and vr < 0.8: vol = 55
    elif pc < 0 and vr > 1.2: vol = 25
    elif pc < 0 and vr < 0.8: vol = 45
    else:                      vol = 50
    det['거래량'] = float(vol)

    det['파동근사'] = wave_score(p, h, l)

    total = (det['MA정렬']*0.20 + det['RSI']*0.15 + det['MACD']*0.20 +
             det['볼린저밴드']*0.15 + det['거래량']*0.15 + det['파동근사']*0.15)
    return float(total), det

# ─────────────────────────────────────────────
# FUNDAMENTAL ANALYSIS
# ─────────────────────────────────────────────

def _score_per(v):
    if not v or np.isnan(v) or v <= 0: return 50
    return 70 if v<5 else (85 if v<15 else (65 if v<25 else (40 if v<40 else 20)))

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

def calc_mdd(prices):
    roll_max = prices.expanding().max()
    return float(((prices - roll_max) / roll_max * 100).min())

def _score_mdd(m):
    return 90 if m>-10 else (75 if m>-20 else (55 if m>-30 else (35 if m>-40 else (20 if m>-50 else 10))))

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

        per = info.get('trailingPE') or info.get('forwardPE')
        pbr = info.get('priceToBook')
        det['밸류에이션'] = _score_per(per)*0.6 + _score_pbr(pbr)*0.4
        det['PER'] = per; det['PBR'] = pbr

        roe = info.get('returnOnEquity')
        roa = info.get('returnOnAssets')
        pm  = info.get('profitMargins')
        pm_s = 50
        if pm:
            pp = pm*100
            pm_s = 10 if pp<0 else (40 if pp<5 else (60 if pp<10 else (80 if pp<20 else 90)))
        det['수익성'] = _score_roe(roe)*0.4 + _score_roe(roa*3 if roa else None)*0.3 + pm_s*0.3
        det['ROE'] = roe; det['ROA'] = roa

        rg = info.get('revenueGrowth'); eg = info.get('earningsGrowth')
        det['성장성'] = _score_growth(rg)*0.5 + _score_growth(eg)*0.5 if eg else _score_growth(rg)
        det['매출성장'] = rg; det['EPS성장'] = eg

        de = info.get('debtToEquity'); cr = info.get('currentRatio')
        de_s = _score_de(de/100 if de else None)
        cr_s = 50
        if cr: cr_s = 10 if cr<0.5 else (30 if cr<1.0 else (60 if cr<1.5 else (85 if cr<3.0 else 75)))
        det['안전성'] = de_s*0.6 + cr_s*0.4

        mdd_v = calc_mdd(df['Close']) if df is not None else None
        det['MDD']  = float(_score_mdd(mdd_v)) if mdd_v is not None else 50.0
        det['MDD값'] = mdd_v

        fs, fsig = calc_piotroski_fscore(ticker)
        det['F-Score']     = float(fs/9*100) if fs is not None else 50.0
        det['F-Score값']   = fs
        det['F-Score시그널'] = fsig

        total = (det['밸류에이션']*0.25 + det['수익성']*0.25 + det['성장성']*0.20 +
                 det['안전성']*0.10  + det['MDD']*0.10   + det['F-Score']*0.10)
        return float(total), det
    except Exception as e:
        return 50.0, {'오류': str(e)}

# ─────────────────────────────────────────────
# MACRO & INTEREST RATE
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600)
def macro_score():
    det, data = {}, {}
    end = datetime.now(); start = end - timedelta(days=400)
    try:
        tnx = yf.download('^TNX',    start=start, end=end, progress=False)
        fvx = yf.download('^FVX',    start=start, end=end, progress=False)
        vix = yf.download('^VIX',    start=start, end=end, progress=False)
        dxy = yf.download('DX-Y.NYB',start=start, end=end, progress=False)
        for d in [tnx, fvx, vix, dxy]:
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.droplevel(1)

        if len(tnx) >= 60:
            cr = float(tnx['Close'].iloc[-1]); r3 = float(tnx['Close'].iloc[-60]); chg = cr-r3
            data['10Y금리'] = cr; data['3M금리변화'] = chg
            lvl = 85 if cr<2 else (75 if cr<3 else (60 if cr<4 else (45 if cr<5 else 30)))
            tr  = 80 if chg<-0.3 else (65 if chg<0 else (50 if chg<0.3 else (35 if chg<0.8 else 20)))
            det['금리환경'] = lvl*0.4 + tr*0.6
        else: det['금리환경'] = 50.0

        if len(tnx) > 0 and len(fvx) > 0:
            sp = float(tnx['Close'].iloc[-1]) - float(fvx['Close'].iloc[-1]); data['장단기스프레드'] = sp
            det['장단기금리차'] = 80 if sp>1.5 else (70 if sp>0.5 else (50 if sp>=0 else (35 if sp>-0.5 else 20)))
        else: det['장단기금리차'] = 50.0

        if len(vix) >= 20:
            cv = float(vix['Close'].iloc[-1]); av = float(vix['Close'].tail(30).mean()); data['VIX'] = cv
            vs = 75 if cv<15 else (70 if cv<20 else (55 if cv<25 else (35 if cv<35 else 20)))
            if cv-av < -3: vs = min(vs+15, 100)
            elif cv-av > 5: vs = max(vs-15, 0)
            det['VIX'] = float(vs)
        else: det['VIX'] = 50.0

        if len(dxy) >= 60:
            cd = float(dxy['Close'].iloc[-1]); d3 = float(dxy['Close'].iloc[-60])
            cp2 = (cd-d3)/d3*100; data['DXY'] = cd
            det['달러지수'] = 70 if cp2<-3 else (60 if cp2<0 else (50 if cp2<3 else (40 if cp2<6 else 30)))
        else: det['달러지수'] = 50.0

        total = det['금리환경']*0.35 + det['장단기금리차']*0.25 + det['VIX']*0.25 + det['달러지수']*0.15
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

            t_s, _ = technical_score(df)
            f_s, _ = fundamental_score(ticker, df)
            total  = t_s*(w_tech/100) + f_s*(w_fund/100) + m_s*(w_macro/100)
            cp  = float(df['Close'].iloc[-1])
            pp  = float(df['Close'].iloc[-2]) if len(df) >= 2 else cp
            chg = (cp-pp)/pp*100
            results.append({
                '티커': ticker, '종합점수': round(total,1),
                '차트+파동': round(t_s,1), '재무+퀀트': round(f_s,1), '매크로': round(m_s,1),
                '등급': score_label(total), '등락(%)': round(chg,2),
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

    tab1, tab2, tab3, tab4 = st.tabs(["📊 종목 분석", "🔍 스크리너", "📉 백테스팅", "🔔 알림"])

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
                    prog.progress(20); msg.text("📈 차트·파동 분석 중...")
                    t_score, t_det = technical_score(df)
                    prog.progress(45); msg.text("💰 재무제표·퀀트 분석 중...")
                    f_score, f_det = fundamental_score(ticker, df)
                    prog.progress(65); msg.text("🌍 매크로·금리 분석 중...")
                    m_score, m_det, m_data = macro_score()
                    prog.progress(95)
                    total = t_score*(w_tech/100) + f_score*(w_fund/100) + m_score*(w_macro/100)
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

                    st.header(f"{name}  `{ticker}`")
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("현재가", fmt_p(cp), f"{chg:+.2f}%")
                    c2.metric("52주 고가", fmt_p(float(df['High'].tail(252).max())))
                    c3.metric("52주 저가", fmt_p(float(df['Low'].tail(252).min())))
                    c4.metric("분석 기준일", end_dt.strftime("%Y-%m-%d"))
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

                    st.subheader("카테고리별 점수")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.plotly_chart(gauge(t_score, f"📈 차트+파동  ({w_tech}%)"), use_container_width=True)
                        with st.expander("세부 점수"):
                            for k, v in t_det.items():
                                if k != 'RSI값':
                                    ex = f" *(현재 RSI: {t_det['RSI값']})*" if k=='RSI' else ''
                                    st.markdown(f"**{k}** {'█'*int(v/10)}{'░'*(10-int(v/10))} `{v:.0f}점`{ex}")
                    with col2:
                        st.plotly_chart(gauge(f_score, f"💰 재무제표+퀀트  ({w_fund}%)"), use_container_width=True)
                        with st.expander("세부 점수"):
                            for k in ['밸류에이션','수익성','성장성','안전성','MDD','F-Score']:
                                v = f_det.get(k, 50)
                                st.markdown(f"**{k}** {'█'*int(v/10)}{'░'*(10-int(v/10))} `{v:.0f}점`")
                            st.divider()
                            st.caption(f"PER: {fmt(f_det.get('PER'))}  |  PBR: {fmt(f_det.get('PBR'))}")
                            st.caption(f"ROE: {fmt(f_det.get('ROE'),pct=True)}  |  ROA: {fmt(f_det.get('ROA'),pct=True)}")
                            st.caption(f"매출성장: {fmt(f_det.get('매출성장'),pct=True)}  |  EPS성장: {fmt(f_det.get('EPS성장'),pct=True)}")
                            mdd_v = f_det.get('MDD값')
                            st.caption(f"MDD: {f'{mdd_v:.1f}%' if mdd_v else 'N/A'}")
                            fs_v = f_det.get('F-Score값')
                            st.caption(f"Piotroski F-Score: {f'{fs_v}/9' if fs_v is not None else 'N/A'}")
                            for sk, sv in f_det.get('F-Score시그널', {}).items():
                                if '오류' not in sk: st.caption(f"  {sv} {sk}")
                    with col3:
                        st.plotly_chart(gauge(m_score, f"🌍 매크로+금리  ({w_macro}%)"), use_container_width=True)
                        with st.expander("세부 점수"):
                            for k in ['금리환경','장단기금리차','VIX','달러지수']:
                                v = m_det.get(k, 50)
                                st.markdown(f"**{k}** {'█'*int(v/10)}{'░'*(10-int(v/10))} `{v:.0f}점`")
                            st.divider()
                            if '10Y금리' in m_data:
                                st.caption(f"10Y 금리: {m_data['10Y금리']:.2f}%  |  3M변화: {m_data.get('3M금리변화',0):+.2f}%p")
                            if '장단기스프레드' in m_data:
                                st.caption(f"장단기 스프레드: {m_data['장단기스프레드']:.2f}%p")
                            if 'VIX' in m_data:
                                st.caption(f"VIX: {m_data['VIX']:.1f}  |  DXY: {m_data.get('DXY','N/A')}")
                    st.divider()

                    st.subheader("📈 차트")
                    st.plotly_chart(_draw_chart(df, ticker, is_krw), use_container_width=True)

                    st.subheader("📋 분석 요약")
                    st.dataframe(pd.DataFrame([
                        {'카테고리':'종합 점수',      '점수':f"{total:.1f}",   '등급':score_label(total),   '가중치':'100%'},
                        {'카테고리':'📈 차트+파동',   '점수':f"{t_score:.1f}", '등급':score_label(t_score),  '가중치':f'{w_tech}%'},
                        {'카테고리':'💰 재무제표+퀀트','점수':f"{f_score:.1f}", '등급':score_label(f_score), '가중치':f'{w_fund}%'},
                        {'카테고리':'🌍 매크로+금리', '점수':f"{m_score:.1f}", '등급':score_label(m_score), '가중치':f'{w_macro}%'},
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

        if st.button("🔍 스크리닝 시작", type="primary", disabled=(total_w!=100)):
            pb = st.progress(0); pt = st.empty()
            result_df = run_screener(ticker_list, w_tech, w_fund, w_macro, pb, pt)
            pb.empty(); pt.empty()

            if result_df.empty:
                st.warning("분석 가능한 종목이 없습니다.")
            else:
                st.success(f"총 {len(result_df)}개 종목 분석 완료")
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


if __name__ == "__main__":
    main()
