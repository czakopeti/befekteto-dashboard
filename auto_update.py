"""
auto_update.py - Befekteto Dashboard v4
3 idohorizont: MOST (1-4 het) | 3-6 HONAP | 6-18 HONAP
Mind yfinance vagy FRED - nulla scraping, 100pct megbizható.
Kiszedve: CBOE Skew, AAII, Put/Call, Fed netto likv.
Beepitve: VIX term structure, MACD, Bollinger, Copper/Gold,
          ISM New Orders, LEI, M2 YoY, Consumer Expectations
TA: SPX + egyedi reszvenyek
"""

import json, os, re, sys, datetime, argparse
import requests, pandas as pd, yfinance as yf
from pathlib import Path

FY26_EPS_EST  = 338.0   # HAVONTA FRISSITENDO - FactSet Earnings Insight PDF
PE_FAIR_VALUE = 19.5    # 2015-2025 median, evente felulvizsgaland
FRED_API_KEY  = os.environ.get("FRED_API_KEY", "YOUR_FRED_API_KEY_HERE")
OUTPUT_HTML   = "index.html"
HISTORY_FILE  = "history.json"
ERROR_LOG     = "error_log.json"

# Sajat reszvenyek - ird be a sajatjaidat
MY_STOCKS = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "Nvidia"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/html,*/*"}
errors = []

def log(msg, ok=True): print(f"  {'OK' if ok else 'WW'}  {msg}")

def safe(fn, fallback, label):
    try:
        r = fn(); log(f"{label}: OK"); return r
    except Exception as e:
        log(f"{label} HIBA ({str(e)[:80]}) -> fallback", ok=False)
        errors.append({"source": label, "error": str(e)[:120],
                       "time": datetime.datetime.now().isoformat()})
        return fallback

# ── ALAP ADATOK ──────────────────────────────────────────────

def fetch_spx():
    h    = yf.Ticker("^GSPC").history(period="1y")
    p    = float(h["Close"].iloc[-1])
    ma50 = float(h["Close"].rolling(50).mean().iloc[-1])
    ma200= float(h["Close"].rolling(200).mean().iloc[-1])
    prev = float(h["Close"].iloc[-2])
    ath  = float(h["Close"].max())
    return {
        "spx": round(p), "spxMA200": round(ma200), "spxMA50": round(ma50),
        "spxChg": round((p-prev)/prev*100, 2),
        "spxAboveMA": round((p-ma200)/ma200*100, 1),
        "spxFromHigh": round((p-ath)/ath*100, 1),
    }

def fetch_vix():
    h = yf.Ticker("^VIX").history(period="30d")
    c = float(h["Close"].iloc[-1]); p = float(h["Close"].iloc[-2])
    return {"vix": round(c,1), "vixTrend": round(c-p,1), "vixRising": c>p}

def fetch_fred_series(series_id, n=15):
    if FRED_API_KEY in ("YOUR_FRED_API_KEY_HERE", "", None):
        raise ValueError("FRED API kulcs nincs beallitva!")
    d = requests.get(
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&file_type=json&sort_order=desc&limit={n}",
        timeout=15).json()
    obs = [o for o in d["observations"] if o["value"] != "."]
    return [float(o["value"]) for o in obs]

def fetch_hy_spread():
    return {"hySpread": round(fetch_fred_series("BAMLH0A0HYM2")[0], 2)}

def fetch_forward_pe(spx_price):
    pe = round(spx_price / FY26_EPS_EST, 1)
    val_score = round(max(0, min(30, (PE_FAIR_VALUE+1.5-pe)/6*30)))
    label = ("ALULERTEKELT" if pe<18 else "FAIR" if pe<21
             else "TULERTEKELT" if pe<25 else "EXTREM DRAGA")
    return {"forwardPE": pe, "valScore": val_score, "valLabel": label}

# ── MOST SZEKCIÓ (1-4 het) ───────────────────────────────────

def fetch_ta_spx():
    """VIX term structure, MACD, Bollinger squeeze, RSI divergencia, CNN F&G"""
    h     = yf.Ticker("^GSPC").history(period="6mo")
    close = h["Close"]

    # VIX Term Structure
    try:
        vix_s  = float(yf.Ticker("^VIX").history(period="2d")["Close"].iloc[-1])
        vix3m  = float(yf.Ticker("^VIX3M").history(period="2d")["Close"].iloc[-1])
        tr     = round(vix_s/vix3m, 3)
        t_sig  = "bull" if tr<0.9 else "wait" if tr<1.0 else "bear"
        t_desc = ("Contango - nyugodt" if tr<0.9 else "Flat - figyelem" if tr<1.0 else "Backwardation - PANIK!")
    except Exception:
        tr=0.95; t_sig="wait"; t_desc="Nincs adat"

    # MACD hisztogram
    e12  = close.ewm(span=12).mean()
    e26  = close.ewm(span=26).mean()
    macd = e12-e26
    sig  = macd.ewm(span=9).mean()
    hist = macd-sig
    h_c  = float(hist.iloc[-1])
    h_p  = float(hist.iloc[-2])
    h_p2 = float(hist.iloc[-3])
    m_rising = h_c>h_p
    m_accel  = (h_c-h_p)>(h_p-h_p2)
    m_sig    = "bull" if m_rising and m_accel else "wait" if m_rising else "bear"
    m_desc   = "Momentum epul" if m_sig=="bull" else "Emelkedik" if m_rising else "Gyengul"

    # Bollinger
    bb_m  = close.rolling(20).mean()
    bb_s  = close.rolling(20).std()
    bb_up = bb_m+2*bb_s; bb_lo = bb_m-2*bb_s
    bw_c  = float((bb_up.iloc[-1]-bb_lo.iloc[-1])/bb_m.iloc[-1]*100)
    bw_a  = float(((bb_up-bb_lo).iloc[-20:]/bb_m.iloc[-20:]).mean()*100)
    squeeze = bw_c < bw_a*0.85
    bb_desc = ("SQUEEZE - nagy mozgas kozeleg!" if squeeze else f"Szeles ({bw_c:.1f}pct)")

    # RSI + divergencia
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100-(100/(1+gain/loss))
    r_c   = float(rsi.iloc[-1])
    r_5   = float(rsi.iloc[-5])
    p_up  = close.iloc[-1]>close.iloc[-5]
    r_up  = r_c>r_5
    bull_div = not p_up and r_up and r_c<40
    bear_div = p_up and not r_up and r_c>60
    r_sig = ("bull" if bull_div else "stop" if bear_div
             else "wait" if r_c>75 else "go" if r_c<35 else "wait")
    r_desc = ("Bullish divergencia!" if bull_div
              else "Bearish divergencia!" if bear_div
              else f"RSI:{r_c:.0f} - tuladott" if r_c<35
              else f"RSI:{r_c:.0f} - tulvett" if r_c>75
              else f"RSI:{r_c:.0f} - normalis")

    # CNN Fear & Greed
    cnn=50; cnn_r="Neutral"
    try:
        r2=requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata/",
                        headers={**HEADERS,"Referer":"https://www.cnn.com/"},timeout=15)
        d=r2.json()
        cnn=round(float(d["fear_and_greed"]["score"]))
        cnn_r=d["fear_and_greed"]["rating"]
    except Exception: pass

    return {
        "termRatio":tr,"termSignal":t_sig,"termDesc":t_desc,
        "macdHist":round(h_c,2),"macdSignal":m_sig,"macdDesc":m_desc,
        "bbSqueeze":squeeze,"bbDesc":bb_desc,"bbWidth":round(bw_c,1),
        "rsiSPX":round(r_c,1),"rsiSignal":r_sig,"rsiDesc":r_desc,
        "rsiDiv":"bull" if bull_div else "bear" if bear_div else "none",
        "cnnFG":cnn,"cnnFGRating":cnn_r,
    }

# ── 3-6 HONAP SZEKCIÓ ────────────────────────────────────────

def fetch_medium_term():
    """Copper/Gold, ISM New Orders, Golden Cross, Breadth"""

    # Copper/Gold
    try:
        cu   = float(yf.Ticker("HG=F").history(period="5d")["Close"].dropna().iloc[-1])
        au   = float(yf.Ticker("GC=F").history(period="5d")["Close"].dropna().iloc[-1])
        cg   = round(cu/au, 6)
        cu4w = float(yf.Ticker("HG=F").history(period="30d")["Close"].dropna().iloc[0])
        au4w = float(yf.Ticker("GC=F").history(period="30d")["Close"].dropna().iloc[0])
        cg4w = cu4w/au4w
        cg_t = "bull" if cg>cg4w*1.01 else "bear" if cg<cg4w*0.99 else "wait"
        cg_d = ("Emelkedo - bovules jel" if cg_t=="bull"
                else "Csokkeno - lassulas jel" if cg_t=="bear" else "Stabil")
    except Exception:
        cg=0.000070; cg_t="wait"; cg_d="Nincs adat"

    # ISM New Orders
    try:
        ism_v = fetch_fred_series("NAPMNO")
        ism   = round(ism_v[0],1)
        ism_p = round(ism_v[1],1) if len(ism_v)>1 else ism
        i_sig = "bull" if ism>55 else "bear" if ism<48 else "wait"
        i_d   = (f"{ism} - Bovules" if ism>55 else
                 f"{ism} - Zsugorodes" if ism<48 else
                 f"{ism} - Semleges ({'emelek' if ism>ism_p else 'csokkeno'})")
    except Exception:
        ism=50; i_sig="wait"; i_d="Nincs adat"

    # Golden/Death Cross
    try:
        spx_c  = yf.Ticker("^GSPC").history(period="1y")["Close"]
        ma50   = float(spx_c.rolling(50).mean().iloc[-1])
        ma200  = float(spx_c.rolling(200).mean().iloc[-1])
        ma50_4 = float(spx_c.rolling(50).mean().iloc[-20])
        c_sig  = "bull" if ma50>ma200 else "bear"
        c_d    = ("Golden Cross - bullish trend" if c_sig=="bull" and ma50>ma50_4
                  else "MA50>MA200 de lassul" if c_sig=="bull"
                  else "Death Cross - bearish trend")
    except Exception:
        c_sig="wait"; c_d="Nincs adat"

    # Breadth
    try:
        SAMPLE=["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","JPM","UNH","V",
                "XOM","JNJ","PG","MA","HD","AVGO","CVX","MRK","ABBV","KO",
                "PEP","COST","WMT","BAC","TMO","LLY","ORCL","NFLX","AMD","CRM",
                "ACN","DHR","TXN","NEE","PM","MDT","HON","QCOM","UPS","AMGN",
                "CAT","BMY","LOW","SBUX","GS","BLK","ISRG","SYK","GILD","SPGI"]
        dat=yf.download(SAMPLE,period="3mo",auto_adjust=True,progress=False,threads=True)["Close"]
        ab=tot=0
        for col in dat.columns:
            s=dat[col].dropna()
            if len(s)<50: continue
            m=s.rolling(50).mean().iloc[-1]; l=s.iloc[-1]
            if pd.notna(m) and pd.notna(l):
                tot+=1
                if l>m: ab+=1
        br=round(ab/tot*100) if tot>0 else 50
    except Exception: br=50

    return {
        "cgRatio":cg,"cgTrend":cg_t,"cgDesc":cg_d,
        "ismNewOrders":ism,"ismSignal":i_sig,"ismDesc":i_d,
        "crossSignal":c_sig,"crossDesc":c_d,
        "breadth":br,
    }

# ── 6-18 HONAP SZEKCIÓ ───────────────────────────────────────

def fetch_long_term():
    """LEI, M2 YoY, Consumer Expectations, Hozamgorbe, Rec. prob."""

    # Conference Board LEI (USSLIND)
    try:
        lei_v = fetch_fred_series("USSLIND")
        lei   = round(lei_v[0],2)
        l3    = round(lei_v[2],2) if len(lei_v)>2 else lei
        l6    = round(lei_v[5],2) if len(lei_v)>5 else lei
        lc3   = round(lei-l3,2); lc6=round(lei-l6,2)
        l_sig = ("bull" if lc3>0 and lc6>0 else
                 "bear" if lc3<0 and lc6<0 else "wait")
        l_d   = (f"Emelkedo - bovules jon ({lc3:+.2f}/3h)" if l_sig=="bull"
                 else f"Csokkeno - lassulas ({lc3:+.2f}/3h)" if l_sig=="bear"
                 else f"Vegyes ({lc3:+.2f}/3h)")
    except Exception:
        lei=100; l_sig="wait"; l_d="Nincs adat"; lc3=0

    # M2 YoY
    try:
        m2_v  = fetch_fred_series("M2SL")
        m2_yoy= round((m2_v[0]/m2_v[11]-1)*100,1) if len(m2_v)>=12 else 4.0
        m_sig = "bull" if m2_yoy>5 else "wait" if m2_yoy>0 else "bear"
        m_d   = (f"+{m2_yoy}pct YoY - boseges likv." if m2_yoy>5
                 else f"+{m2_yoy}pct YoY - semleges" if m2_yoy>0
                 else f"{m2_yoy}pct YoY - szukulo")
    except Exception:
        m2_yoy=4; m_sig="wait"; m_d="Nincs adat"

    # Consumer Expectations (UMich)
    try:
        umi_v = fetch_fred_series("UMCSENT")
        umi   = round(umi_v[0],1)
        umi_p = round(umi_v[2],1) if len(umi_v)>2 else umi
        u_t   = "emelkedo" if umi>umi_p else "csokkeno"
        u_sig = "bull" if umi>80 else "bear" if umi<60 else "wait"
        u_d   = f"{umi} - {u_t}"
    except Exception:
        umi=70; u_sig="wait"; u_d="Nincs adat"

    # Hozamgorbe
    try:
        yv  = fetch_fred_series("T10Y2Y")
        yld = round(yv[0]*100)
        ylt = round((yv[0]-yv[1])*100) if len(yv)>1 else 0
    except Exception:
        yld=20; ylt=0

    # Rec. prob.
    try:
        rec = round(fetch_fred_series("RECPROUSM156N")[0],1)
    except Exception:
        rec=5.0

    return {
        "leiCur":lei,"leiSignal":l_sig,"leiDesc":l_d,"leiChg3":lc3,
        "m2Yoy":m2_yoy,"m2Signal":m_sig,"m2Desc":m_d,
        "umiCur":umi,"umiSignal":u_sig,"umiDesc":u_d,
        "yieldCurve":yld,"yieldTrend":ylt,
        "recProb":rec,
    }

# ── REZSIM ───────────────────────────────────────────────────

def detect_regime(base, now, lng):
    vix=base.get("vix",18); cnn=now.get("cnnFG",50)
    rec=lng.get("recProb",5); yld=lng.get("yieldCurve",20)
    lei=lng.get("leiSignal","wait")
    if isinstance(cnn,(int,float)) and cnn<30 and vix>22:
        return "extreme_fear"
    if isinstance(rec,(int,float)) and rec>15 and yld<0:
        return "recession_watch"
    if lei=="bear" and isinstance(rec,(int,float)) and rec>10:
        return "slowdown"
    fear=0
    if vix>25: fear+=2
    if vix>35: fear+=2
    if isinstance(rec,(int,float)) and rec>15: fear+=2
    if yld<-10: fear+=2
    return "fear" if fear>=5 else "neutral" if fear>=2 else "bull"

# ── SCORE SZAMITAS ───────────────────────────────────────────

def sv(signal):
    return {"bull":1,"go":1,"wait":0,"neutral":0,"bear":-1,"stop":-1}.get(signal,0)

def calc_entry_score(now, mid, lng, base):
    vix=base.get("vix",18); yld=lng.get("yieldCurve",20)
    hy=base.get("hySpread",3.5); pe=base.get("forwardPE",20)
    rec=lng.get("recProb",5); cnn=now.get("cnnFG",50)
    regime=detect_regime(base,now,lng)
    s=50
    # MOST szekció (1-4 het)
    term_v  = sv(now.get("termSignal","wait"))
    macd_v  = sv(now.get("macdSignal","wait"))
    rsi_v   = sv(now.get("rsiSignal","wait"))
    cnn_v   = (2 if cnn<25 else 1 if cnn<40 else 0 if cnn<60 else -1)
    now_s   = term_v*5 + macd_v*5 + rsi_v*4 + cnn_v*6
    if regime=="extreme_fear": now_s += cnn_v*6
    s += now_s
    # 3-6 honap
    cg_v  = sv(mid.get("cgTrend","wait"))
    ism_v = sv(mid.get("ismSignal","wait"))
    br    = mid.get("breadth",50)
    br_v  = (2 if br>65 else -1 if br<40 else 0)
    crs_v = sv(mid.get("crossSignal","wait"))
    s += cg_v*7 + ism_v*8 + br_v*5 + crs_v*5
    # 6-18 honap
    lei_v = sv(lng.get("leiSignal","wait"))
    m2_v  = sv(lng.get("m2Signal","wait"))
    umi_v = sv(lng.get("umiSignal","wait"))
    yld_v = (2 if yld>25 else 1 if yld>0 else -1 if yld<-10 else 0)
    s += lei_v*9 + m2_v*7 + umi_v*5 + yld_v*4
    # Makro korrektorok
    s += (3 if vix<16 else 1 if vix<22 else -2 if vix<28 else -5)
    s += (3 if hy<3.0 else 1 if hy<3.8 else -3 if hy>4.5 else 0)
    s += (3 if pe<18 else 1 if pe<21 else -2 if pe>24 else 0)
    if isinstance(rec,(int,float)):
        s += (0 if rec<5 else -3 if rec<12 else -8 if rec<20 else -15)
    return min(100, max(0, round(s)))

def calc_corr_prob(now, mid, lng, base):
    vix=base.get("vix",18); vixT=base.get("vixTrend",0)
    hy=base.get("hySpread",3.5); spxA=base.get("spxAboveMA",2)
    rec=lng.get("recProb",5); yld=lng.get("yieldCurve",20)
    lei=lng.get("leiSignal","wait")
    term=now.get("termSignal","wait"); macd=now.get("macdSignal","wait")
    rsi_d=now.get("rsiDiv","none"); cg=mid.get("cgTrend","wait")
    regime=detect_regime(base,now,lng)
    p=0
    if vix>25 and vixT>0: p+=18
    elif vix>20: p+=7
    if term=="bear": p+=12
    if macd=="bear": p+=8
    if rsi_d=="bear": p+=10
    if cg=="bear": p+=8
    if hy>4.5: p+=15
    elif hy>3.8: p+=5
    if spxA>8: p+=10
    elif spxA>5: p+=4
    if isinstance(rec,(int,float)):
        if rec>20: p+=25
        elif rec>12: p+=12
    if regime=="recession_watch": p+=20
    if lei=="bear": p+=15
    if yld<-15: p+=18
    elif yld<0: p+=6
    return min(95,p)

def calc_kelly(es, cp, regime="bull"):
    if regime=="extreme_fear" and es>=42 and cp<50:
        alloc=max(35,min(70,round((es+18)*0.55)))
        return {"kellyAlloc":alloc,"kellyCash":100-alloc,
                "kellyLabel":"Kontrarian vetel - Extreme Fear"}
    win=min(0.82,es/100*0.95); b=0.6/0.9
    kelly=win-(1-win)/b
    alloc=round(0.4*kelly*100)
    alloc=round(alloc*(1-cp/100*0.65))
    if 40<=es<65 and cp<60: alloc=max(25,alloc)
    alloc=max(0,min(80,alloc))
    if alloc>=65: lbl="Aktiv pozicio"
    elif alloc>=45: lbl="Mersekelt - felezd meg"
    elif alloc>=25: lbl="Ovatos - kis pozicio"
    elif alloc>=10: lbl="Minimalis"
    else: lbl="Defenziv - maradj ki"
    return {"kellyAlloc":alloc,"kellyCash":100-alloc,"kellyLabel":lbl}

def calc_seasonality():
    m=datetime.date.today().month
    mn={1:"jan",2:"feb",3:"már",4:"ápr",5:"máj",6:"jún",
        7:"júl",8:"aug",9:"szept",10:"okt",11:"nov",12:"dec"}
    if m==9: return {"seasonLabel":"Szeptember - leggyengebb honap","seasonStrength":"weak"}
    elif m in [5,6,7,8]: return {"seasonLabel":f"Gyenge szezon ({mn[m]}) - Sell in May","seasonStrength":"weak"}
    elif m in [11,12,1,2,3,4]: return {"seasonLabel":f"Eros szezon ({mn[m]}) - Nov-Apr avg +7.5pct","seasonStrength":"strong"}
    return {"seasonLabel":f"Semleges ({mn[m]})","seasonStrength":"neutral"}

# ── EGYEDI RESZVÉNY TA ───────────────────────────────────────

def fetch_stock(ticker, name):
    try:
        h=yf.Ticker(ticker).history(period="1y")
        if len(h)<60: return None
        c=h["Close"]; p=float(c.iloc[-1])
        ma50=float(c.rolling(50).mean().iloc[-1])
        ma200=float(c.rolling(200).mean().iloc[-1])
        ath=float(c.max()); prev=float(c.iloc[-2])
        d=c.diff(); g=d.clip(lower=0).rolling(14).mean()
        l=(-d.clip(upper=0)).rolling(14).mean()
        rsi=float(100-(100/(1+g.iloc[-1]/l.iloc[-1])))
        e12=c.ewm(span=12).mean(); e26=c.ewm(span=26).mean()
        macd=e12-e26; sig=macd.ewm(span=9).mean()
        hist=float((macd-sig).iloc[-1]); hist_p=float((macd-sig).iloc[-2])
        macd_bull=hist>hist_p and hist>0
        bb_m=float(c.rolling(20).mean().iloc[-1])
        bb_s=float(c.rolling(20).std().iloc[-1])
        bb_pos=(p-(bb_m-2*bb_s))/(4*bb_s)*100 if bb_s>0 else 50
        r5=float((100-(100/(1+g/l))).iloc[-5])
        p_up=p>float(c.iloc[-5]); r_up=rsi>r5
        bull_div=not p_up and r_up and rsi<40
        bear_div=p_up and not r_up and rsi>60
        chg=(p-prev)/prev*100; fath=(p-ath)/ath*100; vma200=(p-ma200)/ma200*100
        sc=50
        if p>ma200: sc+=12
        if p>ma50: sc+=8
        if macd_bull: sc+=10
        if rsi<35: sc+=15
        elif rsi<50: sc+=6
        elif rsi>75: sc-=12
        if bull_div: sc+=12
        if bear_div: sc-=12
        if fath<-15: sc+=8
        elif fath<-5: sc+=4
        if bb_pos<20: sc+=5
        cr=15
        if rsi>72: cr+=18
        if bear_div: cr+=15
        if vma200>20: cr+=18
        elif vma200>10: cr+=8
        if p<ma50: cr+=10
        if fath>-3: cr+=8
        sig_s="go" if sc>=62 else "wait" if sc>=42 else "stop"
        ta=[]
        if bull_div: ta.append("Bullish div.")
        if bear_div: ta.append("Bearish div.")
        if macd_bull: ta.append("MACD↑")
        if rsi<35: ta.append("Tuladott")
        if rsi>75: ta.append("Tulvett")
        if p>ma200: ta.append("MA200↑")
        if not ta: ta.append("Semleges")
        return {"ticker":ticker,"name":name,"price":round(p,2),
                "chgDay":round(chg,2),"ma50":round(ma50,2),"ma200":round(ma200,2),
                "rsi":round(rsi,1),"macdBull":macd_bull,"bbPos":round(bb_pos,0),
                "fromAth":round(fath,1),"vsMA200":round(vma200,1),
                "score":min(100,max(0,round(sc))),
                "corrRisk":min(95,round(cr)),"signal":sig_s,
                "taSummary":" · ".join(ta[:3]),
                "bullDiv":bull_div,"bearDiv":bear_div}
    except Exception as e:
        return {"ticker":ticker,"name":name,"error":str(e)[:80]}

# ── HISTORY ──────────────────────────────────────────────────

def load_history():
    if Path(HISTORY_FILE).exists():
        with open(HISTORY_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return []

def save_history(hist, base, now, mid, lng, es, cp, regime, kelly):
    snap={"date":datetime.date.today().isoformat(),
          "spx":base.get("spx"),"entryScore":es,"corrProb":cp,
          "regime":regime,"kellyAlloc":kelly.get("kellyAlloc"),
          "vix":base.get("vix"),"hySpread":base.get("hySpread"),
          "breadth":mid.get("breadth"),"cnnFG":now.get("cnnFG"),
          "yieldCurve":lng.get("yieldCurve"),"recProb":lng.get("recProb"),
          "leiSignal":lng.get("leiSignal"),"m2Yoy":lng.get("m2Yoy"),
          "cgTrend":mid.get("cgTrend")}
    hist.append(snap); hist=hist[-52:]
    with open(HISTORY_FILE,"w",encoding="utf-8") as f:
        json.dump(hist,f,indent=2,ensure_ascii=False)
    return hist

def save_error_log():
    d={"last_run":datetime.datetime.now().isoformat(),
       "status":"OK" if not errors else "PARTIAL" if len(errors)<4 else "FAILED",
       "errors":errors,"success_count":12-len(errors)}
    with open(ERROR_LOG,"w",encoding="utf-8") as f:
        json.dump(d,f,indent=2,ensure_ascii=False)
    return d
# ── HTML GENERÁLÁS ────────────────────────────────────────────

def generate_html(base, now, mid, lng, es, cp, history, stocks,
                  log_data, kelly, season, regime):
    today    = datetime.date.today().strftime("%Y. %B %d.")
    nfd      = (4 - datetime.date.today().weekday()) % 7 or 7
    next_fri = (datetime.date.today() + datetime.timedelta(days=nfd)).strftime("%B %d.")
    sc_col   = "#00c878" if log_data["status"]=="OK" else "#f0a500" if log_data["status"]=="PARTIAL" else "#f03050"
    st_txt   = "Minden OK" if not errors else f"{len(errors)} forras fallback"
    alloc    = kelly["kellyAlloc"]
    alloc_col= "#00c878" if alloc>=55 else "#f0a500" if alloc>=28 else "#f03050"
    errh     = (f'<div class="eb">Forras hibak (fallback): '
                + ", ".join(e["source"] for e in errors)
                + '</div>') if errors else ""

    # Fo jel
    if cp>=60:
        sig,si="stop","🟣"
        sv2="KORREKCIO KOCKAZAT"
        se=f"Korrekcios val.: <strong>{cp}%</strong>. Merlegel reszleges kilepest. Kelly: {alloc}% SPX."
    elif regime=="extreme_fear":
        sig,si="go","🟢"
        sv2="EXTREME FEAR = KONTRARIAN VETELI JEL"
        se=(f"<strong>Ver folyik az utcan</strong> – CNN F&G: {now.get('cnnFG','?')}. "
            f"Historikusan az egyik legjobb belepesi pont. Kelly: <strong>{alloc}%</strong> SPX.")
    elif es>=65:
        sig,si="go","🟢"
        sv2="MOST ERDEMES BEFEKTETNI"
        se=f"Tobb idohorizonton bullish jelek. Kelly: <strong>{alloc}%</strong> SPX."
    elif es>=40:
        sig,si="wait","🟡"
        sv2="VEGYES JELEK – FELEZD MEG"
        se=f"Fektess be <strong>{alloc}%</strong>-ot most, a maradekot ha score 65+ lesz."
    else:
        sig,si="stop","🔴"
        sv2="NE FEKTESS BE MOST"
        se=f"Tobb indikator gyenge. Kelly: <strong>{alloc}%</strong>. Jobb ar jon."

    regime_map={"bull":"Bikos","neutral":"Semleges","fear":"Felelmi",
                "extreme_fear":"EXTREME FEAR","recession_watch":"Recesszio Figyelő",
                "slowdown":"Lassulas jel"}
    rl  = regime_map.get(regime, regime)
    rcs = ("regime-bull" if regime=="bull" else
           "regime-fear" if "fear" in regime else "regime-neutral")
    scs = ("season-strong" if season["seasonStrength"]=="strong" else
           "season-weak"   if season["seasonStrength"]=="weak"   else "")

    # Backtest
    bt_html=""
    if len(history)>=4:
        s0=history[0].get("spx",0)
        if s0 and s0>0:
            inv=False; sv3=100.0; bh=100.0
            for i in range(1,len(history)):
                ph=history[i-1]; ch=history[i]
                sp=ph.get("spx",1)
                r=(ch.get("spx",sp)-sp)/sp if sp else 0
                if ph.get("corrProb",0)>=60: inv=False
                elif ph.get("entryScore",0)>=65: inv=True
                sv3*=(1+r*(1 if inv else 0)); bh*=(1+r)
            bts=round(sv3-100,1); btb=round(bh-100,1)
            btc="#00c878" if bts>=btb else "#f0a500"
            bt_html=(f'<div class="bt-box"><span class="bt-l">Historikus (signal kovetese):</span>'
                     f'<span style="color:{btc};font-weight:700">Strategia: {bts:+.1f}%</span>'
                     f'<span class="bt-s">vs</span><span>Buy&Hold: {btb:+.1f}%</span>'
                     f'<span class="bt-n">({len(history)} het)</span></div>')

    def ind(cls, name, val, desc):
        col={"go":"var(--bull)","bull":"var(--bull)","wait":"var(--neu)",
             "neutral":"var(--neu)","bear":"var(--bear)","stop":"var(--bear)"}.get(cls,"var(--neu)")
        bt={"go":"BULL","bull":"BULL","wait":"NEU","neutral":"NEU",
            "bear":"BEAR","stop":"BEAR"}.get(cls,"NEU")
        bc={"go":"s-go","bull":"s-go","wait":"s-wait","neutral":"s-wait",
            "bear":"s-stop","stop":"s-stop"}.get(cls,"s-wait")
        bp={"go":85,"bull":85,"wait":50,"neutral":50,"bear":15,"stop":15}.get(cls,50)
        return (f'<div class="ind {cls}"><div class="it">'
                f'<div class="in">{name}</div><div class="is {bc}">{bt}</div></div>'
                f'<div class="iv">{val}</div>'
                f'<div class="pr"><div class="pf" style="width:{bp}%;background:{col}"></div></div>'
                f'<div class="id">{desc}</div></div>')

    vix=base.get("vix",18); yld=lng.get("yieldCurve",20); rec=lng.get("recProb",5)
    cnn=now.get("cnnFG",50); hy=base.get("hySpread",3.5); pe=base.get("forwardPE",20)
    cnn_desc=(f"{now.get('cnnFGRating','?')}"
              +(" · <b style='color:#00c878'>STRONG CONTRARIAN BUY!</b>"
                if isinstance(cnn,(int,float)) and cnn<25 else ""))

    now_html="".join([
        ind(now.get("termSignal","wait"), "VIX Term Structure (VIX/VIX3M)",
            str(now.get("termRatio","?")), now.get("termDesc","?")),
        ind(now.get("macdSignal","wait"), "MACD Hisztogram",
            str(now.get("macdHist","?")), now.get("macdDesc","?")),
        ind(now.get("rsiSignal","wait"), "RSI + Divergencia (SPX)",
            str(now.get("rsiSPX","?")), now.get("rsiDesc","?")),
        ind("wait" if now.get("bbSqueeze") else "neutral", "Bollinger Squeeze (SPX)",
            f"{now.get('bbWidth','?')}%", now.get("bbDesc","?")),
        ind("go" if isinstance(cnn,(int,float)) and cnn<30 else
            "bear" if isinstance(cnn,(int,float)) and cnn>70 else "wait",
            "CNN Fear & Greed", str(cnn), cnn_desc),
        ind("go" if vix<18 else "bear" if vix>25 else "wait", "VIX",
            str(vix), f"Heti: {base.get('vixTrend',0):+.1f}"),
    ])

    br=mid.get("breadth",50)
    mid_html="".join([
        ind(mid.get("cgTrend","wait"), "Copper / Gold arany",
            str(mid.get("cgRatio","?")), mid.get("cgDesc","?")),
        ind(mid.get("ismSignal","wait"), "ISM New Orders",
            str(mid.get("ismNewOrders","?")), mid.get("ismDesc","?")),
        ind(mid.get("crossSignal","wait"), "Golden / Death Cross",
            "MA50 vs MA200", mid.get("crossDesc","?")),
        ind("go" if br>65 else "bear" if br<40 else "wait",
            "Piaci Breadth (pct > MA50)", f"{br}%",
            "Szeles rally" if br>65 else "Szukulo" if br<40 else "Vegyes"),
        ind("go" if hy<3.5 else "bear" if hy>4.5 else "wait",
            "HY Credit Spread", f"{hy}%",
            "Szuk=OK" if hy<3.5 else "Krizis" if hy>4.5 else "Emelkedo"),
        ind("go" if pe<18 else "bear" if pe>24 else "wait",
            "Forward P/E", f"{pe}x", base.get("valLabel","?")),
    ])

    lng_html="".join([
        ind(lng.get("leiSignal","wait"), "Conference Board LEI",
            str(lng.get("leiCur","?")), lng.get("leiDesc","?")),
        ind(lng.get("m2Signal","wait"), "M2 Penzkinálat YoY",
            f"{lng.get('m2Yoy','?')}%", lng.get("m2Desc","?")),
        ind(lng.get("umiSignal","wait"), "Consumer Expectations (UMich)",
            str(lng.get("umiCur","?")), lng.get("umiDesc","?")),
        ind("go" if yld>25 else "bear" if yld<-15 else "wait",
            "Hozamgorbe 10Y-2Y", f"{'+' if yld>0 else ''}{yld} bp",
            "OK" if yld>15 else "INVERTALT!" if yld<-10 else "Lapos"),
        ind("go" if isinstance(rec,(int,float)) and rec<5 else
            "bear" if isinstance(rec,(int,float)) and rec>20 else "wait",
            "Recesszios val. (NY Fed)", f"{rec}%",
            "Alacsony" if isinstance(rec,(int,float)) and rec<5 else
            "MAGAS!" if isinstance(rec,(int,float)) and rec>20 else "Kozepes"),
        ind("go" if base.get("spx",0)>base.get("spxMA200",0) else "bear",
            "SPX vs MA200 (trend)", f"+{base.get('spxAboveMA','?')}%",
            f"MA200: {base.get('spxMA200',0):,}"),
    ])

    def stock_card(s):
        if "error" in s:
            return f'<div class="sc err"><b class="st">{s["ticker"]}</b><div class="se">{s["error"]}</div></div>'
        vc={"go":"#00c878","wait":"#f0a500","stop":"#f03050"}[s["signal"]]
        sl={"go":"VESZEL","wait":"VARJ","stop":"NE MOST"}[s["signal"]]
        rc="rsi-lo" if s["rsi"]<38 else "rsi-hi" if s["rsi"]>72 else ""
        cc="#a78bfa" if s["corrRisk"]>=50 else "#f0a500" if s["corrRisk"]>=30 else "#00c878"
        div=""
        if s.get("bullDiv"): div='<span style="color:#00c878;font-size:8px"> ▲DIV</span>'
        elif s.get("bearDiv"): div='<span style="color:#f03050;font-size:8px"> ▼DIV</span>'
        return (f'<div class="sc {s["signal"]}"><div class="sc-top">'
                f'<div><div class="st">{s["ticker"]}{div}</div><div class="sn">{s["name"]}</div></div>'
                f'<div class="ssig" style="color:{vc};border-color:{vc}40;background:{vc}12">{sl}</div></div>'
                f'<div class="sp">${s["price"]:,.2f}'
                f'<span style="color:{"#00c878" if s["chgDay"]>=0 else "#f03050"};font-size:11px"> {s["chgDay"]:+.2f}%</span></div>'
                f'<div class="sg4">'
                f'<div class="si"><div class="sl2">Score</div><div class="sv" style="color:{vc}">{s["score"]}/100</div></div>'
                f'<div class="si"><div class="sl2">RSI</div><div class="sv {rc}">{s["rsi"]}</div></div>'
                f'<div class="si"><div class="sl2">vs MA200</div><div class="sv" style="color:{"#00c878" if s["vsMA200"]>0 else "#f03050"}">{s["vsMA200"]:+.1f}%</div></div>'
                f'<div class="si"><div class="sl2">ATH-tól</div><div class="sv">{s["fromAth"]:.1f}%</div></div>'
                f'</div>'
                f'<div class="ta-row">{s.get("taSummary","–")}</div>'
                f'<div class="sb-w"><div class="sb" style="width:{s["score"]}%;background:{vc}"></div></div>'
                f'<div class="scr">Korr.kockazat: <b style="color:{cc}">{s["corrRisk"]}%</b></div></div>')

    stk="".join(stock_card(s) for s in stocks)
    hd=json.dumps([h["date"] for h in history[-24:]])
    hs=json.dumps([h.get("entryScore",50) for h in history[-24:]])
    hc=json.dumps([h.get("corrProb",20)   for h in history[-24:]])

    html=f"""<!DOCTYPE html>
<html lang="hu"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Befekteto Dashboard v4 – {today}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
:root{{--bg:#03050a;--bg2:#080c14;--bg3:#0d1522;--b:#162030;--t:#c0d0e8;--m:#3a5068;--d:#101828;
  --bull:#00c878;--neu:#f0a500;--bear:#f03050;--purple:#a78bfa;
  --mono:'JetBrains Mono',monospace;--sans:'Inter',sans-serif}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--t);font-family:var(--sans);font-size:13px;padding:16px 20px 48px}}
.w{{max-width:1200px;margin:0 auto}}
.hdr{{display:flex;justify-content:space-between;gap:14px;padding-bottom:10px;border-bottom:1px solid var(--b);margin-bottom:10px}}
.hdr h1{{font-size:14px;font-weight:700}} .hdr h1 em{{color:var(--bull);font-style:normal}}
.ab{{font-family:var(--mono);font-size:8px;padding:3px 9px;border-radius:20px;background:rgba(0,200,120,.08);color:var(--bull);border:1px solid rgba(0,200,120,.2);display:inline-flex;align-items:center;gap:5px;margin-top:4px}}
.dot{{width:5px;height:5px;border-radius:50%;background:var(--bull);animation:blink 1.5s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
.sb2{{font-family:var(--mono);font-size:8px;padding:2px 8px;border-radius:3px;color:{sc_col};border:1px solid {sc_col}40;background:{sc_col}10}}
.hm{{font-family:var(--mono);font-size:9px;color:var(--m);line-height:1.9;margin-top:3px}} .hm b{{color:var(--neu)}}
.meta-row{{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}}
.meta-tag{{font-family:var(--mono);font-size:8.5px;padding:3px 10px;border-radius:20px;border:1px solid var(--b);background:var(--bg2);color:var(--m)}}
.regime-bull{{color:var(--bull);background:rgba(0,200,120,.07);border-color:rgba(0,200,120,.2)}}
.regime-neutral{{color:var(--neu);background:rgba(240,165,0,.06);border-color:rgba(240,165,0,.15)}}
.regime-fear{{color:var(--bear);background:rgba(240,48,80,.07);border-color:rgba(240,48,80,.18)}}
.season-strong{{color:var(--bull);background:rgba(0,200,120,.06);border-color:rgba(0,200,120,.15)}}
.season-weak{{color:var(--bear);background:rgba(240,48,80,.06);border-color:rgba(240,48,80,.15)}}
.hero{{border-radius:11px;padding:16px 22px;margin-bottom:8px;display:grid;grid-template-columns:auto 1fr auto;gap:18px;align-items:center}}
.hero.go{{background:rgba(0,200,120,.07);border:1.5px solid rgba(0,200,120,.25)}}
.hero.wait{{background:rgba(240,165,0,.06);border:1.5px solid rgba(240,165,0,.2)}}
.hero.stop{{background:rgba(240,48,80,.07);border:1.5px solid rgba(240,48,80,.2)}}
.hi{{font-size:38px;line-height:1}}
.hv{{font-size:17px;font-weight:700;margin-bottom:3px}}
.hero.go .hv{{color:var(--bull)}} .hero.wait .hv{{color:var(--neu)}} .hero.stop .hv{{color:var(--bear)}}
.he{{font-size:12px;color:var(--m);line-height:1.6;max-width:380px}} .he strong{{color:var(--t)}}
.hr{{text-align:right}} .hs{{font-family:var(--mono);font-size:34px;font-weight:700;line-height:1}}
.hero.go .hs{{color:var(--bull)}} .hero.wait .hs{{color:var(--neu)}} .hero.stop .hs{{color:var(--bear)}}
.hsl{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.1em;color:var(--m);margin-top:2px}}
.hall{{font-family:var(--mono);font-size:12px;font-weight:700;margin-top:5px;color:{alloc_col}}}
.hkl{{font-family:var(--mono);font-size:9px;color:var(--m);margin-top:2px}}
.ew{{border-radius:9px;padding:11px 16px;margin-bottom:8px;display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center}}
.ew.a{{background:rgba(167,139,250,.05);border:1.5px solid rgba(167,139,250,.3);animation:bp 2.5s ease-in-out infinite}}
.ew.i{{background:rgba(0,200,120,.03);border:1px solid rgba(0,200,120,.12)}}
@keyframes bp{{0%,100%{{border-color:rgba(167,139,250,.3)}}50%{{border-color:rgba(167,139,250,.6)}}}}
.ewi{{font-size:24px}} .ewt{{font-size:12px;font-weight:600;margin-bottom:2px}}
.ew.a .ewt{{color:var(--purple)}} .ew.i .ewt{{color:var(--bull)}}
.ewd{{font-size:11px;color:var(--m);line-height:1.5}} .ewd strong{{color:var(--t)}}
.ewr{{text-align:right;flex-shrink:0}} .ewp{{font-family:var(--mono);font-size:22px;font-weight:700}}
.ew.a .ewp{{color:var(--purple)}} .ew.i .ewp{{color:var(--bull)}}
.ewpl{{font-family:var(--mono);font-size:8px;color:var(--m);text-transform:uppercase;letter-spacing:.1em;margin-top:2px}}
.sec-hdr{{font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.18em;color:var(--m);padding:8px 0 6px;border-top:1px solid var(--b);margin-top:6px;display:flex;align-items:center;gap:8px}}
.sec-hdr::after{{content:'';flex:1;height:1px;background:var(--b)}}
.sec-badge{{font-family:var(--mono);font-size:8px;padding:2px 8px;border-radius:3px}}
.sb-now{{background:rgba(0,200,120,.08);color:var(--bull);border:1px solid rgba(0,200,120,.15)}}
.sb-mid{{background:rgba(74,158,255,.08);color:#4a9eff;border:1px solid rgba(74,158,255,.15)}}
.sb-lng{{background:rgba(167,139,250,.08);color:var(--purple);border:1px solid rgba(167,139,250,.15)}}
.ig{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:4px}}
.ind{{background:var(--bg2);border:1px solid var(--b);border-radius:9px;padding:10px 12px;position:relative;overflow:hidden}}
.ind::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:2px 2px 0 0}}
.ind.go::before,.ind.bull::before{{background:var(--bull)}}
.ind.wait::before,.ind.neutral::before{{background:var(--neu)}}
.ind.bear::before,.ind.stop::before{{background:var(--bear)}}
.it{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px}}
.in{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.1em;color:var(--m)}}
.is{{font-family:var(--mono);font-size:8px;padding:2px 7px;border-radius:3px;white-space:nowrap}}
.s-go{{background:rgba(0,200,120,.1);color:var(--bull);border:1px solid rgba(0,200,120,.2)}}
.s-wait{{background:rgba(240,165,0,.08);color:var(--neu);border:1px solid rgba(240,165,0,.15)}}
.s-stop{{background:rgba(240,48,80,.09);color:var(--bear);border:1px solid rgba(240,48,80,.15)}}
.iv{{font-family:var(--mono);font-size:18px;font-weight:700;line-height:1;margin-bottom:2px}}
.ind.go .iv,.ind.bull .iv{{color:var(--bull)}} .ind.wait .iv,.ind.neutral .iv{{color:var(--neu)}} .ind.bear .iv,.ind.stop .iv{{color:var(--bear)}}
.id{{font-size:10px;color:var(--m);line-height:1.4}} .id b{{color:var(--t)}}
.pr{{height:2px;background:var(--d);border-radius:2px;margin:5px 0 3px;overflow:hidden}} .pf{{height:100%;border-radius:2px}}
.stitle{{font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.16em;color:var(--m);padding:8px 0 6px;border-top:1px solid var(--b);margin-top:4px;display:flex;align-items:center;gap:8px}}
.stitle::after{{content:'';flex:1;height:1px;background:var(--b)}}
.sgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px;margin-bottom:10px}}
.sc{{background:var(--bg2);border:1px solid var(--b);border-radius:9px;padding:11px 12px;position:relative;overflow:hidden}}
.sc::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
.sc.go::before{{background:var(--bull)}} .sc.wait::before{{background:var(--neu)}} .sc.stop::before{{background:var(--bear)}} .sc.err{{opacity:.6}}
.sc-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:5px}}
.st{{font-family:var(--mono);font-size:13px;font-weight:700;color:var(--t)}} .sn{{font-size:10px;color:var(--m)}}
.ssig{{font-family:var(--mono);font-size:8px;padding:2px 8px;border-radius:3px;white-space:nowrap;align-self:flex-start}}
.sp{{font-family:var(--mono);font-size:16px;font-weight:700;color:var(--t);margin-bottom:5px}}
.sg4{{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:5px}}
.si{{background:var(--bg3);border-radius:5px;padding:4px 6px}}
.sl2{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--m);margin-bottom:2px}}
.sv{{font-family:var(--mono);font-size:11px;font-weight:700}} .rsi-lo{{color:var(--bull)}} .rsi-hi{{color:var(--bear)}}
.ta-row{{font-family:var(--mono);font-size:8.5px;color:var(--m);margin-bottom:5px;padding:3px 0;border-top:1px solid var(--b)}}
.sb-w{{height:3px;background:var(--d);border-radius:2px;overflow:hidden;margin-bottom:5px}} .sb{{height:100%;border-radius:2px}}
.scr{{font-family:var(--mono);font-size:9px;color:var(--m)}} .se{{font-family:var(--mono);font-size:9px;color:var(--bear)}}
.g2{{display:grid;grid-template-columns:3fr 2fr;gap:9px;margin-bottom:9px}}
.panel{{background:var(--bg2);border:1px solid var(--b);border-radius:9px;padding:12px 14px}}
.pt{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.14em;color:var(--m);margin-bottom:10px;display:flex;align-items:center;gap:6px}}
.pt::after{{content:'';flex:1;height:1px;background:var(--b)}}
.pb{{width:100%;border-collapse:collapse;font-size:11px}}
.pb th{{padding:6px 8px;font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--m);border-bottom:1px solid var(--b);text-align:left}}
.pb td{{padding:6px 8px;border-bottom:1px solid rgba(22,32,48,.6);vertical-align:top;line-height:1.5}}
.pb tr:last-child td{{border-bottom:none}}
.ga{{color:var(--bull);font-weight:600}} .wa{{color:var(--neu);font-weight:600}} .ba{{color:var(--bear);font-weight:600}} .ea{{color:var(--purple);font-weight:600}}
.bt-box{{display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:10px;background:var(--bg2);border:1px solid var(--b);border-radius:7px;padding:8px 13px;margin-bottom:8px;flex-wrap:wrap}}
.bt-l{{color:var(--m)}} .bt-s{{color:var(--m)}} .bt-n{{color:var(--m);font-size:9px}}
.eb{{background:rgba(240,165,0,.05);border:1px solid rgba(240,165,0,.18);border-radius:7px;padding:7px 12px;margin-bottom:8px;font-family:var(--mono);font-size:9px;color:var(--m)}}
.footer{{margin-top:14px;padding-top:10px;border-top:1px solid var(--b);font-family:var(--mono);font-size:8.5px;color:var(--m);display:flex;justify-content:space-between;line-height:1.9;gap:20px}}
a{{color:#64a0ff}}
@media(max-width:900px){{.hero,.g2{{grid-template-columns:1fr}} .ig{{grid-template-columns:repeat(2,1fr)}} .ew{{grid-template-columns:1fr}}}}
</style></head><body>
<div class="w">
<div class="hdr">
  <div><h1>Befekteto Dashboard <em>v4 · 3 idohorizont</em></h1>
    <div class="ab"><span class="dot"></span> Automatikusan frissítve: {today}</div></div>
  <div style="text-align:right"><div class="sb2">⬤ {st_txt}</div>
    <div class="hm">Következő: <b>péntek, {next_fri}</b> · {log_data['success_count']}/12 forrás</div></div>
</div>
{errh}{bt_html}
<div class="meta-row">
  <div class="meta-tag {rcs}">Rezsim: {rl}</div>
  <div class="meta-tag {scs}">{season['seasonLabel']}</div>
  <div class="meta-tag">SPX: {base.get('spx','?'):,} ({base.get('spxChg','?'):+.2f}%)</div>
  <div class="meta-tag">VIX term: {now.get('termRatio','?')} · CNN F&G: {cnn}</div>
  <div class="meta-tag">LEI: {lng.get('leiSignal','?')} · M2: {lng.get('m2Yoy','?')}% · ISM: {mid.get('ismNewOrders','?')}</div>
</div>
<div class="hero {sig}">
  <div class="hi">{si}</div>
  <div><div class="hv">{sv2}</div><div class="he">{se}</div></div>
  <div class="hr"><div class="hs">{es}/100</div>
    <div class="hsl">Kompozit belépési score</div>
    <div class="hall">Ajánlott allokáció: {alloc}% SPX / {100-alloc}% cash</div>
    <div class="hkl">Kelly-kritérium · {kelly['kellyLabel']}</div></div>
</div>
<div class="ew {'a' if cp>=40 else 'i'}">
  <div class="ewi">{'⚠️' if cp>=40 else '✅'}</div>
  <div><div class="ewt">{'KORREKCIÓ FIGYELMEZTETŐ – '+str(cp)+'% valószínűség' if cp>=40 else 'Nincs korrekciós figyelmeztető'}</div>
    <div class="ewd">{'<strong>Ha pozícióban vagy:</strong> mérlegelj részleges kilépést.' if cp>=40 else 'LEI, M2 és hozamgörbe pozitív. Ha pozícióban vagy: <strong>ülj nyugodtan.</strong>'}</div></div>
  <div class="ewr"><div class="ewp">{cp}%</div><div class="ewpl">korrekció esélye</div></div>
</div>
<div class="sec-hdr">MOST – azonnali jelek (1–4 hét) <span class="sec-badge sb-now">TA + hangulat</span></div>
<div class="ig">{now_html}</div>
<div class="sec-hdr">3–6 HÓNAP – gazdasági ciklus <span class="sec-badge sb-mid">leading indicators</span></div>
<div class="ig">{mid_html}</div>
<div class="sec-hdr">6–18 HÓNAP – makro előrejelzők <span class="sec-badge sb-lng">LEI · M2 · UMich</span></div>
<div class="ig">{lng_html}</div>
<div class="stitle">Saját részvényeim – TA + belépési score</div>
<div class="sgrid">{stk}</div>
<div class="g2">
  <div class="panel">
    <div class="pt">Kompozit belépési score + korrekciós kockázat historikus</div>
    <canvas id="ch" height="155"></canvas>
  </div>
  <div class="panel">
    <div class="pt">Befektetői playbook</div>
    <table class="pb">
      <thead><tr><th></th><th>Helyzet</th><th>Teendő</th><th>Allok.</th></tr></thead>
      <tbody>
        <tr><td>🟢</td><td>Score≥65</td><td class="ga">FEKTESS BE</td><td class="ga">55-80%</td></tr>
        <tr><td>🟢</td><td>Extreme Fear (CNN&lt;25)</td><td class="ga">KONTR. VÉTEL</td><td class="ga">35-65%</td></tr>
        <tr><td>🟡</td><td>Score 40-65</td><td class="wa">FELEZD MEG</td><td class="wa">25-50%</td></tr>
        <tr><td>🔴</td><td>Score&lt;40</td><td class="ba">TARTSD VISSZA</td><td class="ba">0-20%</td></tr>
        <tr><td>🟣</td><td>Korr.≥60%</td><td class="ea">KILÉPÉS</td><td class="ea">0-15%</td></tr>
        <tr><td>🔄</td><td>Piac–10%+score≥50</td><td class="ga">VISSZAVÁSÁROL</td><td class="ga">50-70%</td></tr>
      </tbody>
    </table>
    <div style="margin-top:9px;background:rgba(167,139,250,.04);border:1px solid rgba(167,139,250,.16);border-radius:6px;padding:9px 11px;font-family:var(--mono);font-size:8.5px;color:var(--m);line-height:1.8">
      <b style="color:var(--purple)">Kilépési trigger (4+ aktív):</b><br>
      VIX term backwardation · LEI csökkeno · Bearish RSI div.<br>
      ISM&lt;48 · HY spread&gt;4.5% · Rec.prob&gt;20%
    </div>
  </div>
</div>
<div class="footer">
  <div>yfinance · FRED API · CNN F&G · GitHub Actions: péntek 20:00<br>
  v4 · 3 idohorizont · TA · Kelly · Hibák: <a href="error_log.json">error_log.json</a></div>
  <div style="text-align:right">Buy & hold timing eszköz · Nem befektetési tanácsadás</div>
</div></div>
<script>
Chart.defaults.color='#3a5068';Chart.defaults.font.family="'JetBrains Mono',monospace";Chart.defaults.font.size=9;
const G='rgba(22,32,48,.9)';
const hD={hd},hS={hs},hC={hc};
new Chart(document.getElementById('ch'),{{type:'line',data:{{labels:hD,datasets:[
  {{label:'Score',data:hS,borderColor:'#00c878',borderWidth:2,pointRadius:3,tension:.3,fill:false,
    pointBackgroundColor:hS.map(s=>s>=65?'#00c878':s>=40?'#f0a500':'#f03050')}},
  {{label:'Korr.%',data:hC,borderColor:'rgba(167,139,250,.7)',borderWidth:1.5,pointRadius:0,tension:.3,fill:false,borderDash:[4,3]}},
  {{label:'Küszöb',data:Array(hD.length).fill(65),type:'line',borderColor:'rgba(0,200,120,.2)',borderWidth:1,borderDash:[3,3],pointRadius:0,fill:false}},
]}},options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
  plugins:{{legend:{{display:true,position:'top',align:'end',labels:{{boxWidth:12,boxHeight:1,padding:10,color:'#3a5068'}}}},
    tooltip:{{backgroundColor:'#080c14',borderColor:'#162030',borderWidth:1}}}},
  scales:{{x:{{grid:{{color:G}},ticks:{{maxTicksLimit:8,maxRotation:0}}}},y:{{grid:{{color:G}},min:0,max:100}}}}
}}}});
</script></body></html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  OK  Dashboard -> {OUTPUT_HTML}")


# ── MAIN ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    print("\n"+"="*60+"\n  BEFEKTETO DASHBOARD v4 – 3 idohorizont\n"+"="*60+"\n")

    print("  SPX + alap...")
    spx_d = safe(fetch_spx, {"spx":6700,"spxMA200":6490,"spxMA50":6400,
                              "spxChg":0,"spxAboveMA":1.5,"spxFromHigh":-1}, "SPX")
    vix_d = safe(fetch_vix, {"vix":16,"vixTrend":0,"vixRising":False}, "VIX")
    hy_d  = safe(fetch_hy_spread, {"hySpread":3.5}, "HY Spread")
    base  = {**spx_d, **vix_d, **hy_d}
    base.update(safe(lambda: fetch_forward_pe(base.get("spx",6700)),
                     {"forwardPE":20,"valScore":10,"valLabel":"FAIR"}, "ForwardPE"))

    print("  TA + CNN (MACD/BB/RSI/VIX-term)...")
    now = safe(fetch_ta_spx, {
        "termRatio":0.95,"termSignal":"wait","termDesc":"Nincs adat",
        "macdHist":0,"macdSignal":"wait","macdDesc":"Nincs adat",
        "bbSqueeze":False,"bbDesc":"Nincs adat","bbWidth":3.0,
        "rsiSPX":50,"rsiSignal":"wait","rsiDesc":"Nincs adat","rsiDiv":"none",
        "cnnFG":50,"cnnFGRating":"Neutral"}, "TA+CNN")

    print("  Copper/Gold, ISM, Cross, Breadth (~30mp)...")
    mid = safe(fetch_medium_term, {
        "cgRatio":0.000070,"cgTrend":"wait","cgDesc":"Nincs adat",
        "ismNewOrders":50,"ismSignal":"wait","ismDesc":"Nincs adat",
        "crossSignal":"wait","crossDesc":"Nincs adat","breadth":50}, "Mid-term")

    print("  LEI, M2, UMich, Hozamgorbe, Rec...")
    lng = safe(fetch_long_term, {
        "leiCur":100,"leiSignal":"wait","leiDesc":"Nincs adat","leiChg3":0,
        "m2Yoy":4,"m2Signal":"wait","m2Desc":"Nincs adat",
        "umiCur":70,"umiSignal":"wait","umiDesc":"Nincs adat",
        "yieldCurve":20,"yieldTrend":0,"recProb":5.0}, "Long-term")

    print("\n  Reszvenyek (TA)...")
    stocks=[]
    for t,n in MY_STOCKS:
        s=safe(lambda t=t,n=n: fetch_stock(t,n),
               {"ticker":t,"name":n,"error":"Hiba"},t)
        if s: stocks.append(s)

    regime  = detect_regime(base,now,lng)
    es      = calc_entry_score(now,mid,lng,base)
    cp      = calc_corr_prob(now,mid,lng,base)
    kelly   = calc_kelly(es,cp,regime)
    season  = calc_seasonality()
    log_data= save_error_log()
    history = load_history()
    history = save_history(history,base,now,mid,lng,es,cp,regime,kelly)
    generate_html(base,now,mid,lng,es,cp,history,stocks,log_data,kelly,season,regime)

    print(f"\n  Score: {es}/100  Korr: {cp}%  Rezsim: {regime}")
    print(f"  Kelly: {kelly['kellyAlloc']}% SPX  LEI: {lng.get('leiSignal')}  M2: {lng.get('m2Yoy')}%")
    print(f"  ISM: {mid.get('ismNewOrders')}  Copper/Gold: {mid.get('cgTrend')}  VIX term: {now.get('termRatio')}")
    print(f"  Hibak: {len(errors)}/12 forras\n")

    if not args.no_browser:
        import subprocess, platform
        if platform.system()=="Windows":  os.startfile(OUTPUT_HTML)
        elif platform.system()=="Darwin": subprocess.run(["open",OUTPUT_HTML])
        else:                              subprocess.run(["xdg-open",OUTPUT_HTML])
    print("  KESZ!\n"+"="*60+"\n")
    if len(errors)>=10: sys.exit(1)


if __name__=="__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err={"last_run":datetime.datetime.now().isoformat(),"status":"CRASHED",
             "errors":[{"source":"FATAL","error":str(e),
                        "traceback":traceback.format_exc()[:800]}],"success_count":0}
        with open(ERROR_LOG,"w",encoding="utf-8") as f:
            json.dump(err,f,indent=2,ensure_ascii=False)
        print(f"\nFATAL: {e}"); traceback.print_exc(); sys.exit(1)




