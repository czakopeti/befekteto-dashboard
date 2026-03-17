"""
auto_update.py – Befekteto Dashboard v3 (World-Class)
Futtatas lokalis: python auto_update.py
Futtatas szerveren: python auto_update.py --no-browser
"""

import json, os, re, sys, datetime, argparse
import requests, pandas as pd, yfinance as yf
from pathlib import Path

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KONFIG – HAVONTA FRISSITENDO
# Forras: google "FactSet Earnings Insight PDF" -> CY 2026 sor
# 2026.01:315 | 2026.02:325 | 2026.03:338 | 2026.04:??? 
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FY26_EPS_EST  = 338.0
PE_FAIR_VALUE = 19.5   # 2015-2025 median ~19.5x, evente felulvizsgaland

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_FRED_API_KEY_HERE")
OUTPUT_HTML  = "index.html"
HISTORY_FILE = "history.json"
ERROR_LOG    = "error_log.json"

MY_STOCKS = [
    ("AAPL",  "Apple"),
    ("TSLA",  "Tesla"),
    ("NVDA",  "Nvidia"),
    ("CRWD",  "Crowdstrike"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/html,*/*"}
errors = []

def log(msg, ok=True):
    print(f"  {'OK' if ok else 'WW'}  {msg}")

def safe(fn, fallback, label):
    try:
        r = fn()
        log(f"{label}: OK")
        return r
    except Exception as e:
        log(f"{label} HIBA ({str(e)[:80]}) -> fallback", ok=False)
        errors.append({"source": label, "error": str(e)[:120],
                       "time": datetime.datetime.now().isoformat()})
        return fallback

# ── ADATFORRÁSOK ──────────────────────────────────────────────

def fetch_spx():
    h = yf.Ticker("^GSPC").history(period="1y")
    p = float(h["Close"].iloc[-1])
    ma200 = float(h["Close"].rolling(200).mean().iloc[-1])
    prev  = float(h["Close"].iloc[-2])
    ath   = float(h["Close"].max())
    rets  = h["Close"].pct_change().dropna()
    vol20 = float(rets.tail(20).std() * (252**0.5) * 100)
    return {
        "spx": round(p), "spxMA200": round(ma200),
        "spxChg": round((p - prev) / prev * 100, 2),
        "spxAboveMA": round((p - ma200) / ma200 * 100, 1),
        "spxFromHigh": round((p - ath) / ath * 100, 1),
        "realizedVol": round(vol20, 1),
    }

def fetch_vix():
    h = yf.Ticker("^VIX").history(period="30d")
    c = float(h["Close"].iloc[-1])
    p = float(h["Close"].iloc[-2])
    avg4w = float(h["Close"].tail(20).mean())
    return {"vix": round(c, 1), "vixTrend": round(c - p, 1),
            "vixAvg4w": round(avg4w, 1), "vixRising": c > avg4w}

def fetch_skew():
    h = yf.Ticker("^SKEW").history(period="10d")
    val = float(h["Close"].iloc[-1])
    return {"skew": round(val, 1), "skewElevated": val > 145}

def fetch_eps_score():
    """
    EPS revision score – 3 forrasbol probalkozik sorban:
    1. S&P Global xlsx (legpontosabb)
    2. FactSet Earnings Insight scraping (backup)
    3. Szamitas az SPX arfolyambol + forward P/E-bol (vegso fallback)
    """
    # --- 1. PROBALKOZAS: S&P Global xlsx ---
    try:
        url = ("https://www.spglobal.com/spdji/en/documents/"
               "additional-material/sp-500-eps-est.xlsx")
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Referer": "https://www.spglobal.com/",
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=30)
        if r.status_code == 200 and len(r.content) > 10000:
            with open("sp500_eps_raw.xlsx", "wb") as f:
                f.write(r.content)
            xl   = pd.ExcelFile("sp500_eps_raw.xlsx")
            df   = pd.read_excel("sp500_eps_raw.xlsx",
                                 sheet_name=xl.sheet_names[0],
                                 header=None).dropna(how="all")
            last2 = df.tail(2)
            cur  = [float(v) for v in last2.iloc[-1, 1:5]
                    if pd.notna(v) and str(v) not in ("", "nan")]
            prev = [float(v) for v in last2.iloc[-2, 1:5]
                    if pd.notna(v) and str(v) not in ("", "nan")]
            if len(cur) >= 2:
                up    = sum(c > p for c, p in zip(cur, prev))
                avg   = sum(cur) / len(cur)
                delta = sum(c - p for c, p in zip(cur, prev)) / len(cur)
                base  = min(70, max(0, (avg - 3) / 12 * 70))
                log("EPS: S&P Global xlsx OK")
                return {"epsScore": round(base + up / len(cur) * 30),
                        "epsTrend": round(delta, 2),
                        "epsRaw": [round(v, 2) for v in cur],
                        "epsSource": "spglobal"}
    except Exception as e:
        log(f"EPS xlsx hiba: {e}", ok=False)

    # --- 2. PROBALKOZAS: FactSet Earnings Insight oldal scraping ---
    try:
        r2 = requests.get(
            "https://www.factset.com/earningsinsight",
            headers=HEADERS, timeout=20)
        # Keressuk a "bottom-up EPS estimate" sort
        m = re.search(
            r'bottom.up\s+EPS\s+estimate.*?(\d+\.\d+)',
            r2.text, re.IGNORECASE | re.DOTALL)
        if not m:
            # Alternativ: keressuk az earnings growth szazalekot
            m = re.search(
                r'estimated.*?earnings.*?growth.*?(\d+\.\d+)%',
                r2.text, re.IGNORECASE | re.DOTALL)
        if m:
            growth_pct = float(m.group(1))
            # Growth % -> score konverzio
            score = round(min(95, max(30, (growth_pct - 3) / 12 * 70 + 30)))
            log("EPS: FactSet scraping OK")
            return {"epsScore": score, "epsTrend": 0.5,
                    "epsRaw": [], "epsSource": "factset_scrape"}
    except Exception as e:
        log(f"EPS FactSet hiba: {e}", ok=False)

    # --- 3. FALLBACK: yfinance SPY/SPX earnings alapjan ---
    try:
        # SPY trailing EPS * (1 + vart novekedesi rac) = forward EPS becslés
        spy = yf.Ticker("SPY")
        info = spy.fast_info
        # Ha van trailing EPS adat
        trailing_pe = getattr(info, 'pe_ratio', None)
        price = getattr(info, 'last_price', 600)
        if trailing_pe and trailing_pe > 0:
            trailing_eps = price / trailing_pe
            # Forward EPS ~ trailing * 1.12 (tipikus 12% novekedesi var)
            forward_eps_est = trailing_eps * 1.12
            # Score szamitas: jo ha a forward EPS emelkedo
            score = round(min(85, max(45, (forward_eps_est / FY26_EPS_EST) * 70)))
            log("EPS: yfinance proxy OK")
            return {"epsScore": score, "epsTrend": 1.0,
                    "epsRaw": [], "epsSource": "yfinance_proxy"}
    except Exception as e:
        log(f"EPS yfinance proxy hiba: {e}", ok=False)

    raise ValueError("EPS: minden forras sikertelen")


def fetch_put_call():
    """
    Put/Call arany – 3 modszer:
    1. CBOE CSV API (legmegbizhatobb)
    2. CBOE weblap scraping
    3. StockCharts proxy
    """
    # --- 1. CBOE CSV letoltes ---
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        csv_url = "https://www.cboe.com/us/options/market_statistics/daily_market_statistics.csv"
        r = requests.get(csv_url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and "Total" in r.text:
            lines = r.text.strip().split('\n')
            for line in lines:
                if 'Total' in line or 'TOTAL' in line:
                    parts = re.findall(r'[\d.]+', line)
                    if parts:
                        val = float(parts[-1])
                        if 0.4 < val < 2.5:
                            log("Put/Call: CBOE CSV OK")
                            return {"putCall": round(val, 2)}
    except Exception as e:
        log(f"Put/Call CSV hiba: {e}", ok=False)

    # --- 2. CBOE weblap scraping ---
    try:
        r2 = requests.get(
            "https://www.cboe.com/us/options/market_statistics/daily/",
            headers={**HEADERS,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache"},
            timeout=20)
        # Tobb regex minta probalkozas
        patterns = [
            r'[Tt]otal\s*[Pp]ut[\/\s][Cc]all.*?(\d+\.\d+)',
            r'[Tt]otal.*?(\d+\.\d+)',
            r'"pcRatio"\s*:\s*"?(\d+\.\d+)',
            r'data-value="(\d+\.\d+)"',
        ]
        for pat in patterns:
            m = re.search(pat, r2.text.replace('\n', ' '))
            if m:
                val = float(m.group(1))
                if 0.4 < val < 2.5:
                    log("Put/Call: CBOE scraping OK")
                    return {"putCall": round(val, 2)}

        # Altalanos szam kereses 0.6-1.4 tartomanyban
        vals = re.findall(r'\b[01]\.\d{2}\b', r2.text)
        pc_vals = [float(v) for v in vals if 0.5 < float(v) < 1.8]
        if pc_vals:
            val = sorted(pc_vals)[len(pc_vals)//2]  # median
            log("Put/Call: CBOE altalanos OK")
            return {"putCall": round(val, 2)}
    except Exception as e:
        log(f"Put/Call CBOE hiba: {e}", ok=False)

    # --- 3. Macrotrends / alternativ forras ---
    try:
        r3 = requests.get(
            "https://markets.cboe.com/us/options/market_statistics/",
            headers=HEADERS, timeout=15)
        m = re.search(r'(\d+\.\d+)', r3.text)
        if m:
            val = float(m.group(1))
            if 0.4 < val < 2.5:
                return {"putCall": round(val, 2)}
    except Exception:
        pass

    raise ValueError("Put/Call: minden forras sikertelen")


def fetch_aaii():
    """
    AAII Sentiment – 3 modszer:
    1. AAII API endpoint
    2. AAII weblap scraping (tobb regex)
    3. Alternative: CNN Fear & Greed alapjan becsles
    """
    # --- 1. AAII direkt API ---
    try:
        r = requests.get(
            "https://www.aaii.com/sentimentsurvey/sent_results.js",
            headers={**HEADERS, "Referer": "https://www.aaii.com/"},
            timeout=15)
        if r.status_code == 200 and len(r.text) > 50:
            bm  = re.search(r'"bullish"\s*:\s*([\d.]+)', r.text, re.IGNORECASE)
            brm = re.search(r'"bearish"\s*:\s*([\d.]+)', r.text, re.IGNORECASE)
            if bm and brm:
                b = round(float(bm.group(1)), 1)
                br = round(float(brm.group(1)), 1)
                if 0 < b < 100 and 0 < br < 100:
                    log("AAII: API OK")
                    return {"aaiiNet": round(b - br, 1), "aaiiB": b, "aaiiBear": br}
    except Exception as e:
        log(f"AAII API hiba: {e}", ok=False)

    # --- 2. AAII weblap scraping (tobb minta) ---
    try:
        r2 = requests.get(
            "https://www.aaii.com/sentiment-survey",
            headers={**HEADERS,
                "Referer": "https://www.google.com/",
                "Accept-Language": "en-US,en;q=0.9"},
            timeout=25)
        text = r2.text

        # Tobb regex minta
        patterns_bull = [
            r'[Bb]ullish[^<\d]*?(\d+\.?\d*)\s*%',
            r'(\d+\.?\d*)\s*%[^<]*?[Bb]ullish',
            r'"bullish"[^:]*?:\s*"?(\d+\.?\d*)',
            r'[Bb]ull\b[^<\d]*?(\d+\.?\d*)',
        ]
        patterns_bear = [
            r'[Bb]earish[^<\d]*?(\d+\.?\d*)\s*%',
            r'(\d+\.?\d*)\s*%[^<]*?[Bb]earish',
            r'"bearish"[^:]*?:\s*"?(\d+\.?\d*)',
            r'[Bb]ear\b[^<\d]*?(\d+\.?\d*)',
        ]
        bull_val = bear_val = None
        for pat in patterns_bull:
            m = re.search(pat, text)
            if m:
                v = float(m.group(1))
                if 5 < v < 90:
                    bull_val = v
                    break
        for pat in patterns_bear:
            m = re.search(pat, text)
            if m:
                v = float(m.group(1))
                if 5 < v < 90:
                    bear_val = v
                    break
        if bull_val and bear_val:
            log("AAII: weblap scraping OK")
            return {"aaiiNet": round(bull_val - bear_val, 1),
                    "aaiiB": round(bull_val, 1), "aaiiBear": round(bear_val, 1)}

        # Ha csak szamokat talal: keresd az AAII tablazatot
        pcts = re.findall(r'(\d{1,2}\.\d)\s*%', text)
        if len(pcts) >= 2:
            b = float(pcts[0]); br = float(pcts[1])
            if 5 < b < 80 and 5 < br < 80:
                log("AAII: tablazat scraping OK")
                return {"aaiiNet": round(b - br, 1),
                        "aaiiB": round(b, 1), "aaiiBear": round(br, 1)}
    except Exception as e:
        log(f"AAII weblap hiba: {e}", ok=False)

    raise ValueError("AAII: minden forras sikertelen")

def fetch_valuation(spx_price):
    pe = round(spx_price / FY26_EPS_EST, 1)
    val_score = round(max(0, min(30, (PE_FAIR_VALUE + 1.5 - pe) / 6 * 30)))
    label = ("ALULERTEKELT" if pe < 18 else
             "FAIR"         if pe < 21 else
             "TULERTEKELT"  if pe < 25 else "EXTREM DRAGA")
    return {"forwardPE": pe, "valScore": val_score, "valLabel": label}

def fetch_fred_val(series_id):
    if FRED_API_KEY in ("YOUR_FRED_API_KEY_HERE", "", None):
        raise ValueError("FRED API kulcs nincs beallitva GitHub Secrets-ben!")
    d = requests.get(
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&file_type=json&sort_order=desc&limit=10",
        timeout=15).json()
    return float(d["observations"][0]["value"])

def fetch_yield_curve():
    return {"yieldCurve": round(fetch_fred_val("T10Y2Y") * 100)}

def fetch_hy_spread():
    return {"hySpread": round(fetch_fred_val("BAMLH0A0HYM2"), 2)}

def fetch_recession_prob():
    prob = fetch_fred_val("RECPROUSM156N")
    return {"recProb": round(prob, 1)}

def fetch_fed_liquidity():
    walcl = fetch_fred_val("WALCL")
    tga   = fetch_fred_val("WTREGEN")
    rrp   = fetch_fred_val("RRPONTSYD")
    net   = round((walcl - tga - rrp) / 1000, 0)
    return {"fedNetLiq": net, "fedBalance": round(walcl / 1000)}

def fetch_breadth():
    SAMPLE = [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","JPM","UNH","V",
        "XOM","JNJ","PG","MA","HD","AVGO","CVX","MRK","ABBV","KO",
        "PEP","COST","WMT","BAC","TMO","LLY","ORCL","NFLX","AMD","CRM",
        "ACN","DHR","TXN","NEE","PM","MDT","HON","QCOM","UPS","AMGN",
        "CAT","BMY","LOW","SBUX","GS","BLK","ISRG","SYK","GILD","SPGI"
    ]
    data = yf.download(SAMPLE, period="3mo", auto_adjust=True,
                       progress=False, threads=True)["Close"]
    above = total = 0
    for col in data.columns:
        s = data[col].dropna()
        if len(s) < 50:
            continue
        ma50 = s.rolling(50).mean().iloc[-1]
        last = s.iloc[-1]
        if pd.notna(ma50) and pd.notna(last):
            total += 1
            if last > ma50:
                above += 1
    return {"breadth": round(above / total * 100) if total > 0 else 60}

def fetch_cnn_fear_greed():
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
    r = requests.get(url, headers={**HEADERS,
        "Referer": "https://www.cnn.com/markets/fear-and-greed"}, timeout=15)
    d = r.json()
    score  = round(float(d["fear_and_greed"]["score"]))
    rating = d["fear_and_greed"]["rating"]
    return {"cnnFG": score, "cnnFGRating": rating}

# ── REZSIM DETEKTALAS ─────────────────────────────────────────

def detect_regime(data):
    """
    5 rezsim:
      extreme_fear    – CNN < 30 ES VIX > 25
      recession_watch – recProb > 15 ES yieldCurve < 0
      fear            – altalanos felek
      neutral         – vegyes
      bull            – normal bika
    """
    vix = data.get("vix", 18)
    eps = data.get("epsScore", 60)
    rec = data.get("recProb", 5)
    yld = data.get("yieldCurve", 20)
    cnn = data.get("cnnFG", 50)

    if isinstance(cnn, (int, float)) and cnn < 30 and vix > 25:
        return "extreme_fear"

    if (isinstance(rec, (int, float)) and rec > 15 and
            isinstance(yld, int) and yld < 0):
        return "recession_watch"

    fear = 0
    if vix > 25:  fear += 2
    if vix > 35:  fear += 2
    if isinstance(rec, (int, float)) and rec > 15: fear += 2
    if isinstance(yld, int) and yld < -10:         fear += 2
    if eps < 45:  fear += 1

    return "fear" if fear >= 5 else "neutral" if fear >= 2 else "bull"

# ── SCORE SZAMITAS ────────────────────────────────────────────

def calc_entry_score(data):
    eps  = data.get("epsScore", 60)
    vix  = data.get("vix", 18)
    yld  = data.get("yieldCurve", 10)
    br   = data.get("breadth", 60)
    aaii = data.get("aaiiNet", 0)
    hy   = data.get("hySpread", 3.5)
    pc   = data.get("putCall", 0.85)
    pe   = data.get("forwardPE", 20)
    rec  = data.get("recProb", 5)
    liq  = data.get("fedNetLiq", 5000)
    cnn  = data.get("cnnFG", 50)
    regime = detect_regime(data)

    w = {"eps":1.0,"vix":1.0,"yld":1.0,"br":1.0,
         "aaii":1.0,"hy":1.0,"pc":1.0,"val":1.0}

    if regime == "extreme_fear":
        w["aaii"] = 2.5; w["pc"]  = 2.5
        w["eps"]  = 0.5; w["br"]  = 0.5
    elif regime == "recession_watch":
        w["yld"]  = 2.5; w["hy"]  = 2.5
        w["eps"]  = 0.7; w["br"]  = 0.7
    elif regime == "fear":
        w["aaii"] = 2.0; w["pc"]  = 2.0
        w["yld"]  = 1.8; w["hy"]  = 1.8
        w["eps"]  = 0.6; w["br"]  = 0.6
    elif regime == "neutral":
        w["yld"]  = 1.4; w["hy"]  = 1.4; w["val"] = 1.2
    else:  # bull
        w["eps"]  = 1.6; w["br"]  = 1.4; w["val"] = 1.3

    s = 0
    s += min(22, max(0, (eps - 30) / 50 * 22)) * w["eps"]
    s += (16 if vix < 16 else 11 if vix < 22 else 5 if vix < 28 else 0) * w["vix"]
    s += (12 if yld > 25 else 8 if yld > 0 else 4 if yld > -15 else 0) * w["yld"]
    s += (12 if br > 70  else 8 if br > 55  else 4 if br > 40   else 0) * w["br"]
    s += (10 if aaii < -20 else 7 if aaii < 0 else 4 if aaii < 15 else 0) * w["aaii"]
    s += (8  if hy < 3.0 else 5 if hy < 3.8 else 2 if hy < 5.0 else 0) * w["hy"]
    s += (8  if pc > 1.15 else 5 if pc > 0.95 else 2 if pc > 0.8 else 0) * w["pc"]
    s += (8  if pe < 18  else 5 if pe < 21   else 2 if pe < 23  else 0) * w["val"]

    if isinstance(rec, (int, float)):
        if rec > 20:   s -= 15
        elif rec > 10: s -= 7

    if isinstance(cnn, (int, float)):
        cnn_raw = (15 if cnn < 25 else 10 if cnn < 40 else 4 if cnn < 55 else 0)
        s += cnn_raw * (2.0 if regime == "extreme_fear" else 1.0)

    if isinstance(liq, (int, float)):
        s += (4 if liq > 5500 else 2 if liq > 4500 else 0)

    max_p = (22 * w["eps"] + 16 + 12 * w["yld"] + 12 * w["br"] +
             10 * w["aaii"] + 8 * w["hy"] + 8 * w["pc"] + 8 * w["val"] + 15 + 4)
    return min(100, max(0, round(s / max_p * 100)))

def calc_corr_prob(data):
    vix  = data.get("vix", 18);    vixT = data.get("vixTrend", 0)
    eps  = data.get("epsScore", 60); epsT = data.get("epsTrend", 0)
    yld  = data.get("yieldCurve", 10); br = data.get("breadth", 60)
    hy   = data.get("hySpread", 3.5); spxA = data.get("spxAboveMA", 2)
    skew = data.get("skew", 130);  liq  = data.get("fedNetLiq", 5000)
    rec  = data.get("recProb", 5)

    # Rezsim detektalas a fuggvenyen belul (nem kulso valtozo!)
    current_regime = detect_regime(data)

    p = 0
    if vix > 25 and vixT > 0: p += 20
    elif vix > 20:             p += 8
    if epsT < -2:              p += 18
    elif epsT < 0:             p += 7
    if isinstance(yld, int):
        if yld < -15:  p += 18
        elif yld < 5:  p += 6
    if isinstance(br, int):
        if br < 45:    p += 15
        elif br < 55:  p += 5
    if isinstance(hy, (int, float)):
        if hy > 4.5:   p += 15
        elif hy > 3.8: p += 5
    if spxA > 8:       p += 12
    elif spxA > 5:     p += 4
    if eps < 45 and vix > 22: p += 10
    if isinstance(rec, (int, float)):
        if rec > 20:   p += 25
        elif rec > 12: p += 12
    if current_regime == "recession_watch": p += 20
    if isinstance(skew, (int, float)):
        if skew > 150: p += 10
        elif skew > 145: p += 5
    if isinstance(liq, (int, float)) and liq < 4000: p += 10
    return min(95, p)

def calc_kelly_allocation(entry_score, corr_prob, regime="bull"):
    """
    Frakcionalt Kelly (0.4x) – max 80%.

    FONTOS: Extreme Fear rezsimben a kontrarian logika miatt
    a Kelly allokaciot FELFELÉ igazitjuk – ez az a pillanat
    amikor a vér folyik az utcan, es historikusan a legjobb
    belépési lehetőség kínálkozik.

    Score-allokacio osszhang:
      score >= 65  (FEKTESS BE)  → 50-80%
      score 40-64  (FELEZD MEG)  → 25-50%
      score < 40   (TARTSD VISSZA) → 0-20%
      corr >= 60   (KILEPES)     → 0-15%
    """
    # Extreme Fear: kontrarian premium (vér folyik az utcán = veteli lehetoseg)
    # CNN Fear & Greed < 25 historikusan a legjobb belépési pontok egyike
    if regime == "extreme_fear":
        # Extreme Fear override: minimum 40% allokacio ha score >= 45
        # (a fear maga bullish jel kontrarian szemlel szerint)
        if entry_score >= 45 and corr_prob < 50:
            boosted = min(80, entry_score + 20)  # boost a score-t
            alloc = round(boosted * 0.55)
            alloc = max(35, min(80, alloc))
            return {"kellyAlloc": alloc, "kellyCash": 100 - alloc,
                    "kellyLabel": f"Kontrarian vetel – Extreme Fear ({alloc}%)"}

    win_prob   = min(0.82, entry_score / 100 * 0.95)
    corr_adj   = corr_prob / 100
    b          = 0.6 / 0.9
    full_kelly = win_prob - (1 - win_prob) / b
    alloc      = round(0.4 * full_kelly * 100)
    alloc      = round(alloc * (1 - corr_adj * 0.65))
    alloc      = max(0, min(80, alloc))

    # Osszhang a fo jellel: ha score 40-65 (FELEZD MEG), minimum 25%
    if 40 <= entry_score < 65 and corr_prob < 60:
        alloc = max(25, alloc)

    cash = 100 - alloc

    if alloc >= 65:   label = "Aktiv pozicio"
    elif alloc >= 45: label = "Mersekelt – felezd meg"
    elif alloc >= 25: label = "Ovatos – kis pozicio"
    elif alloc >= 10: label = "Minimalis – tartsd vissza"
    else:             label = "Defenziv – maradj ki"

    return {"kellyAlloc": alloc, "kellyCash": cash, "kellyLabel": label}

def calc_seasonality():
    today = datetime.date.today()
    month = today.month
    mn    = {1:"jan",2:"feb",3:"mar",4:"apr",5:"maj",6:"jun",
             7:"jul",8:"aug",9:"szept",10:"okt",11:"nov",12:"dec"}
    if month == 9:
        return {"seasonLabel": "Szeptember – historikusan leggyengebb honap",
                "seasonStrength": "weak", "month": month, "isStrongSeason": False}
    elif month in [5, 6, 7, 8]:
        return {"seasonLabel": f"Gyenge szezon ({mn[month]}) – Sell in May zona",
                "seasonStrength": "weak", "month": month, "isStrongSeason": False}
    elif month in [11, 12, 1, 2, 3, 4]:
        return {"seasonLabel": f"Eros szezon ({mn[month]}) – Nov-Apr avg +7.5%",
                "seasonStrength": "strong", "month": month, "isStrongSeason": True}
    return {"seasonLabel": f"Semleges ({mn[month]})",
            "seasonStrength": "neutral", "month": month, "isStrongSeason": False}

# ── EGYEDI RESZVENY ───────────────────────────────────────────

def fetch_stock(ticker, name):
    try:
        h = yf.Ticker(ticker).history(period="1y")
        if len(h) < 50:
            return None
        p    = float(h["Close"].iloc[-1])
        ma50 = float(h["Close"].rolling(50).mean().iloc[-1])
        ma200= float(h["Close"].rolling(200).mean().iloc[-1])
        ath  = float(h["Close"].max())
        d    = h["Close"].diff()
        g    = d.clip(lower=0).rolling(14).mean()
        l    = (-d.clip(upper=0)).rolling(14).mean()
        rsi  = float(100 - (100 / (1 + g.iloc[-1] / l.iloc[-1])))
        chg  = (p - float(h["Close"].iloc[-2])) / float(h["Close"].iloc[-2]) * 100
        fath = (p - ath) / ath * 100
        vma200 = (p - ma200) / ma200 * 100
        sc = 50
        if p > ma200:   sc += 15
        if p > ma50:    sc += 10
        if rsi < 40:    sc += 15   # tuladott = jo belepesi pont
        elif rsi < 55:  sc += 8
        elif rsi > 75:  sc -= 15   # tulvett = kockazatos
        if fath < -15:  sc += 10   # ATH-tol tavol = olcso
        elif fath < -5: sc += 5

        # Korrekcios kockazat – nem a "fog-e esni" hanem "mennyire sebezheto"
        # Alap: 15% (minden reszvenynek van kockazata)
        cr = 15
        if rsi > 70:         cr += 20  # tulvett
        if vma200 > 20:      cr += 20  # sokat ment fel MA200 folott
        elif vma200 > 10:    cr += 10
        if p < ma50:         cr += 10  # MA50 alatt = gyenge trend
        if fath > -3:        cr += 10  # ATH kozeleseben = draga
        # Ha az altalanos piac gyenge (breadth alacsony), minden reszveny kockazatosabb
        # Ezt a globalis piaci adatokkal kalibraljuk
        sig = "go" if sc >= 60 else "wait" if sc >= 40 else "stop"
        return {"ticker": ticker, "name": name, "price": round(p, 2),
                "ma50": round(ma50, 2), "ma200": round(ma200, 2),
                "rsi": round(rsi, 1), "chgDay": round(chg, 2),
                "fromAth": round(fath, 1), "vsMA200": round(vma200, 1),
                "score": min(100, max(0, round(sc))),
                "corrRisk": min(95, round(cr)), "signal": sig}
    except Exception as e:
        return {"ticker": ticker, "name": name, "error": str(e)[:80]}

# ── HISTORY ───────────────────────────────────────────────────

def load_history():
    if Path(HISTORY_FILE).exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(hist, data, es, cp, regime, kelly):
    snap = {
        "date": datetime.date.today().isoformat(),
        "spx": data.get("spx"), "entryScore": es,
        "corrProb": cp, "regime": regime,
        "kellyAlloc": kelly.get("kellyAlloc"),
        **{k: data.get(k) for k in ["vix","epsScore","yieldCurve",
           "breadth","hySpread","aaiiNet","forwardPE","recProb",
           "fedNetLiq","cnnFG"]},
    }
    hist.append(snap)
    hist = hist[-52:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2, ensure_ascii=False)
    return hist

def save_error_log():
    d = {
        "last_run": datetime.datetime.now().isoformat(),
        "status": "OK" if not errors else "PARTIAL" if len(errors) < 5 else "FAILED",
        "errors": errors,
        "success_count": 13 - len(errors)
    }
    with open(ERROR_LOG, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    return d

# ── HTML GENERAALS ────────────────────────────────────────────

def generate_html(data, es, cp, history, stocks, log_data, kelly, season, regime):
    today    = datetime.date.today().strftime("%Y. %B %d.")
    nfd      = (4 - datetime.date.today().weekday()) % 7 or 7
    next_fri = (datetime.date.today() + datetime.timedelta(days=nfd)).strftime("%B %d.")

    sc_col = "#00c878" if log_data["status"] == "OK" else "#f0a500" if log_data["status"] == "PARTIAL" else "#f03050"
    st_txt = "Minden OK" if not errors else f"{len(errors)} forras fallback"

    # Kelly allokacio elore kell a fo jel szoveghez
    alloc     = kelly["kellyAlloc"]
    alloc_col = "#00c878" if alloc >= 50 else "#f0a500" if alloc >= 25 else "#f03050"

    # Fo jel – szinkronban a Kelly allokacióval es a rezsimmel
    if cp >= 60:
        sig, si = "stop", "🟣"
        sv = "KORREKCIO KOCKAZAT – KILEPES MERLEGEL"
        se = (f"Korrekcios valoszinuseg: <strong>{cp}%</strong>. "
              f"Merlegel reszleges kilepest. Kelly ajánlás: {alloc}% SPX.")
    elif regime == "extreme_fear":
        sig, si = "go", "🟢"
        sv = "EXTREME FEAR = KONTRARIAN VETELI JEL"
        se = (f"<strong>Vér folyik az utcán</strong> – CNN Fear&amp;Greed: {data.get('cnnFG','–')}. "
              f"Historikusan ez az egyik legjobb belépési pont. "
              f"Kelly ajánlás: <strong>{alloc}%</strong> SPX.")
    elif es >= 65:
        sig, si = "go", "🟢"
        sv = "MOST ERDEMES BEFEKTETNI"
        se = (f"EPS emelkedo, VIX {data.get('vix','–')}, fundamentumok erosek. "
              f"Kelly ajánlás: <strong>{alloc}%</strong> SPX.")
    elif es >= 40:
        sig, si = "wait", "🟡"
        sv = "VEGYES JELEK – FELEZD MEG A TOKÉT"
        se = (f"Fektess be <strong>{alloc}%</strong>-ot most (Kelly ajánlás), "
              f"a maradékot ha score 65+ lesz.")
    else:
        sig, si = "stop", "🔴"
        sv = "NE FEKTESS BE MOST"
        se = (f"Tobb indikator gyenge. Kelly: <strong>{alloc}%</strong>. "
              f"Jobb ar jon hamarosan.")

    regime_map = {
        "bull":           "Bikos (EPS+Breadth dominál)",
        "neutral":        "Semleges (kiegyensulyozott)",
        "fear":           "Felelmi (kontrarian jelek dominálnak)",
        "extreme_fear":   "EXTREME FEAR – eroskontrarian veteli jel!",
        "recession_watch":"Recesszio Figyelő – makro kockazat emelkedett",
    }
    regime_label = regime_map.get(regime, regime)
    regime_css   = "regime-bull" if regime == "bull" else "regime-fear" if "fear" in regime else "regime-neutral"
    season_css   = "season-strong" if season["seasonStrength"] == "strong" else "season-weak" if season["seasonStrength"] == "weak" else ""

    # Backtest
    bt_html = ""
    if len(history) >= 4:
        s0 = history[0].get("spx", 0)
        if s0 and s0 > 0:
            inv = False; sv2 = 100.0; bh = 100.0
            for i in range(1, len(history)):
                ph = history[i-1]; ch = history[i]
                sp = ph.get("spx", 1)
                r  = (ch.get("spx", sp) - sp) / sp if sp else 0
                if ph.get("corrProb", 0) >= 60:   inv = False
                elif ph.get("entryScore", 0) >= 65: inv = True
                sv2 *= (1 + r * (1 if inv else 0))
                bh  *= (1 + r)
            bts = round(sv2 - 100, 1); btb = round(bh - 100, 1)
            btc = "#00c878" if bts >= btb else "#f0a500"
            bt_html = (f'<div class="bt-box">'
                       f'<span class="bt-l">Historikus (signal kovetese):</span>'
                       f'<span style="color:{btc};font-weight:700">Strategia: {bts:+.1f}%</span>'
                       f'<span class="bt-s">vs</span>'
                       f'<span>Buy&Hold: {btb:+.1f}%</span>'
                       f'<span class="bt-n">({len(history)} het adat)</span></div>')

    eps   = data.get("epsScore", "–");   epsT  = data.get("epsTrend", 0)
    vix   = data.get("vix", "–");        vixT  = data.get("vixTrend", 0)
    yld   = data.get("yieldCurve", "–"); br    = data.get("breadth", "–")
    hy    = data.get("hySpread", "–");   anetv = data.get("aaiiNet", 0)
    aaiiB = data.get("aaiiB", "–");      aaiiBe= data.get("aaiiBear", "–")
    pe    = data.get("forwardPE", "–");  vall  = data.get("valLabel", "–")
    rec   = data.get("recProb", "–");    liq   = data.get("fedNetLiq", "–")
    skew  = data.get("skew", "–");       cnn   = data.get("cnnFG", "–")
    cnnR  = data.get("cnnFGRating", "–"); pc   = data.get("putCall", "–")

    def ic(v, g, w):
        if not isinstance(v, (int, float)): return "wait"
        return "go" if v <= g else "wait" if v <= w else "stop"
    def icr(v, b, w):
        if not isinstance(v, (int, float)): return "wait"
        return "stop" if v <= b else "wait" if v <= w else "go"

    ie   = "go" if isinstance(eps,(int,float)) and eps>=65 else "wait" if isinstance(eps,(int,float)) and eps>=40 else "stop"
    iv   = "go" if isinstance(vix,(int,float)) and vix<18 else "wait" if isinstance(vix,(int,float)) and vix<25 else "stop"
    iy   = "go" if isinstance(yld,int) and yld>15 else "wait" if isinstance(yld,int) and yld>-10 else "stop"
    ibr  = "go" if isinstance(br,int) and br>65 else "wait" if isinstance(br,int) and br>45 else "stop"
    ihy  = "go" if isinstance(hy,(int,float)) and hy<3.5 else "wait" if isinstance(hy,(int,float)) and hy<5 else "stop"
    ia   = "go" if anetv<-20 else "stop" if anetv>30 else "wait"
    ipe  = "go" if isinstance(pe,(int,float)) and pe<18 else "wait" if isinstance(pe,(int,float)) and pe<22 else "stop"
    irec = "go" if isinstance(rec,(int,float)) and rec<5 else "wait" if isinstance(rec,(int,float)) and rec<15 else "stop"
    iliq = "go" if isinstance(liq,(int,float)) and liq>5500 else "wait" if isinstance(liq,(int,float)) and liq>4000 else "stop"
    isk  = "go" if isinstance(skew,(int,float)) and skew<140 else "wait" if isinstance(skew,(int,float)) and skew<150 else "stop"
    icnn = "go" if isinstance(cnn,(int,float)) and cnn<30 else "wait" if isinstance(cnn,(int,float)) and cnn<55 else "stop"
    ipc  = "go" if isinstance(pc,(int,float)) and pc>1.1 else "wait" if isinstance(pc,(int,float)) and pc>0.8 else "stop"

    def badge(c):
        return {"go":("s-go","BULL"),"wait":("s-wait","NEU"),"stop":("s-stop","BEAR")}[c]

    def ind(name, cls, val, desc, pct):
        bm, bl = badge(cls)
        col = "var(--bull)" if cls=="go" else "var(--neu)" if cls=="wait" else "var(--bear)"
        pct_safe = min(100, max(0, pct)) if isinstance(pct, (int, float)) else 50
        return (f'<div class="ind {cls}">'
                f'<div class="it"><div class="in">{name}</div>'
                f'<div class="is {bm}">{bl}</div></div>'
                f'<div class="iv">{val}</div>'
                f'<div class="pr"><div class="pf" style="width:{pct_safe:.0f}%;background:{col}"></div></div>'
                f'<div class="id">{desc}</div></div>')

    eps_pct  = eps if isinstance(eps,(int,float)) else 50
    vix_pct  = max(0,min(100,100-(float(vix)-10)/40*100)) if isinstance(vix,(int,float)) else 50
    yld_pct  = max(0,min(100,(int(yld)+50)/100*100)) if isinstance(yld,int) else 50
    br_pct   = br if isinstance(br,int) else 50
    hy_pct   = max(0,min(100,(8-float(hy))/7*100)) if isinstance(hy,(int,float)) else 50
    aaii_pct = max(0,min(100,(-anetv+60)/120*100))
    pe_pct   = max(0,min(100,(25-(float(pe) if isinstance(pe,(int,float)) else 20))/10*100))
    rec_pct  = max(0,min(100,100-float(rec)*3)) if isinstance(rec,(int,float)) else 70
    liq_pct  = max(0,min(100,(float(liq)-3000)/5000*100)) if isinstance(liq,(int,float)) else 50
    skew_pct = max(0,min(100,(170-(float(skew) if isinstance(skew,(int,float)) else 140))/40*100))
    cnn_pct  = cnn if isinstance(cnn,(int,float)) else 50
    pc_pct   = max(0,min(100,(float(pc)-0.5)/1.0*100)) if isinstance(pc,(int,float)) else 50

    cnn_desc = (f"<b>{cnnR}</b>"
                + (" · <b style='color:#00c878'>STRONG CONTRARIAN BUY!</b>"
                   if isinstance(cnn,(int,float)) and cnn < 25
                   else " · Semleges" if isinstance(cnn,(int,float)) and cnn < 55
                   else " · <b style='color:#f03050'>Euforia – figyelj!</b>"))

    inds = "".join([
        ind("EPS Revision", ie, f"{eps}/100",
            f"Trend: <b>{'+' if epsT>0 else ''}{epsT}/het</b> · rezsim: {regime}", eps_pct),
        ind("Forward P/E", ipe, f"{pe}x",
            f"FY26: <b>{vall}</b> · Fair: {PE_FAIR_VALUE}x", pe_pct),
        ind("VIX", iv, str(vix),
            f"Heti: <b>{'+' if vixT>0 else ''}{vixT}</b> · {'emelkedo' if vixT>0 else 'csokkeno'}", vix_pct),
        ind("CBOE Skew (Black Swan)", isk, str(skew),
            "<b>Intezmenyi vedekezés aktiv!</b>" if data.get("skewElevated") else "Normalis szint", skew_pct),
        ind("Hozamgorbe 10Y-2Y", iy, f"{('+' if isinstance(yld,int) and yld>0 else '')}{yld} bp",
            "<b>" + ("Egeszseg: OK" if iy=="go" else "Lapos: figyelj" if iy=="wait" else "INVERTALT!") + "</b>", yld_pct),
        ind("Recesszios valoszinuseg", irec, f"{rec}%",
            "NY Fed modell · <b>" + ("Alacsony" if irec=="go" else "Kozepes" if irec=="wait" else "MAGAS!") + "</b>", rec_pct),
        ind("Piaci Breadth (>MA50)", ibr, f"{br}%",
            "<b>" + ("Szeles rally" if ibr=="go" else "Vegyes" if ibr=="wait" else "Szukuloe") + "</b>", br_pct),
        ind("HY Credit Spread", ihy, f"{hy}%",
            "<b>" + ("Szuk=OK" if ihy=="go" else "Emelkedo" if ihy=="wait" else "KRIZIS!") + "</b>", hy_pct),
        ind("Put/Call arany", ipc, str(pc),
            "<b>" + ("Kontrarian BUY!" if ipc=="go" else "Semleges" if ipc=="wait" else "Euforia=figyelj") + "</b>", pc_pct),
        ind("AAII Sentiment", ia, f"{'+' if anetv>0 else ''}{anetv}",
            f"Bull:{aaiiB}% Bear:{aaiiBe}% · <b>"
            + ("KONTR.BUY!" if anetv<-20 else "KONTR.SELL!" if anetv>30 else "Semleges") + "</b>", aaii_pct),
        ind("CNN Fear & Greed", icnn, str(cnn), cnn_desc, cnn_pct),
        ind("Fed Netto Likviditas", iliq,
            f"${liq}B" if isinstance(liq,(int,float)) else str(liq),
            "<b>" + ("Boseges" if iliq=="go" else "Szukul" if iliq=="wait" else "QT nyomas!") + "</b>", liq_pct),
    ])

    def stock_card(s):
        if "error" in s:
            return (f'<div class="sc err">'
                    f'<b class="st">{s["ticker"]}</b>'
                    f'<div class="se">{s["error"]}</div></div>')
        vc  = {"go":"#00c878","wait":"#f0a500","stop":"#f03050"}[s["signal"]]
        sl  = {"go":"VESZEL","wait":"VARJ","stop":"NE MOST"}[s["signal"]]
        rc  = "rsi-lo" if s["rsi"]<40 else "rsi-hi" if s["rsi"]>70 else ""
        cc  = "#a78bfa" if s["corrRisk"]>=50 else "#f0a500" if s["corrRisk"]>=30 else "#00c878"
        chg_col = "#00c878" if s["chgDay"] >= 0 else "#f03050"
        vma_col = "#00c878" if s["vsMA200"] > 0 else "#f03050"
        ath_col = "#f0a500" if s["fromAth"] < -5 else "#c0d0e8"
        return (f'<div class="sc {s["signal"]}">'
                f'<div class="sc-top"><div><div class="st">{s["ticker"]}</div>'
                f'<div class="sn">{s["name"]}</div></div>'
                f'<div class="ssig" style="color:{vc};border-color:{vc}40;background:{vc}12">{sl}</div></div>'
                f'<div class="sp">${s["price"]:,.2f} '
                f'<span style="color:{chg_col};font-size:11px">{s["chgDay"]:+.2f}%</span></div>'
                f'<div class="sg4">'
                f'<div class="si"><div class="sl2">Score</div><div class="sv" style="color:{vc}">{s["score"]}/100</div></div>'
                f'<div class="si"><div class="sl2">RSI</div><div class="sv {rc}">{s["rsi"]}</div></div>'
                f'<div class="si"><div class="sl2">vs MA200</div><div class="sv" style="color:{vma_col}">{s["vsMA200"]:+.1f}%</div></div>'
                f'<div class="si"><div class="sl2">ATH-tol</div><div class="sv" style="color:{ath_col}">{s["fromAth"]:.1f}%</div></div>'
                f'</div>'
                f'<div class="sb-w"><div class="sb" style="width:{s["score"]}%;background:{vc}"></div></div>'
                f'<div class="scr">Korr.kockazat: <b style="color:{cc}">{s["corrRisk"]}%</b></div>'
                f'</div>')

    stk   = "\n".join(stock_card(s) for s in stocks)
    errh  = (f'<div class="eb">Forras hibak (fallback): '
             + ", ".join(e["source"] for e in errors)
             + '</div>') if errors else ""

    hd = json.dumps([h["date"] for h in history[-24:]])
    hs = json.dumps([h.get("entryScore", 50) for h in history[-24:]])
    hc = json.dumps([h.get("corrProb", 20)   for h in history[-24:]])
    ca = cp >= 40

    html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Befekteto Dashboard v3 – {today}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
:root{{
  --bg:#03050a;--bg2:#080c14;--bg3:#0d1522;--b:#162030;
  --t:#c0d0e8;--m:#3a5068;--d:#101828;
  --bull:#00c878;--neu:#f0a500;--bear:#f03050;--purple:#a78bfa;
  --mono:'JetBrains Mono',monospace;--sans:'Inter',sans-serif
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--t);font-family:var(--sans);font-size:13px;padding:18px 22px 48px}}
.w{{max-width:1180px;margin:0 auto}}
.hdr{{display:flex;justify-content:space-between;gap:14px;padding-bottom:11px;border-bottom:1px solid var(--b);margin-bottom:11px}}
.hdr h1{{font-size:15px;font-weight:700}} .hdr h1 em{{color:var(--bull);font-style:normal}}
.ab{{font-family:var(--mono);font-size:8.5px;padding:3px 9px;border-radius:20px;background:rgba(0,200,120,.08);color:var(--bull);border:1px solid rgba(0,200,120,.2);display:inline-flex;align-items:center;gap:5px;margin-top:4px}}
.dot{{width:5px;height:5px;border-radius:50%;background:var(--bull);animation:blink 1.5s infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
.sb2{{font-family:var(--mono);font-size:8px;padding:2px 8px;border-radius:3px;color:{sc_col};border:1px solid {sc_col}40;background:{sc_col}10}}
.hm{{font-family:var(--mono);font-size:9px;color:var(--m);line-height:1.9;margin-top:3px}} .hm b{{color:var(--neu)}}
.hero{{border-radius:11px;padding:17px 22px;margin-bottom:8px;display:grid;grid-template-columns:auto 1fr auto;gap:18px;align-items:center}}
.hero.go{{background:rgba(0,200,120,.07);border:1.5px solid rgba(0,200,120,.25)}}
.hero.wait{{background:rgba(240,165,0,.06);border:1.5px solid rgba(240,165,0,.2)}}
.hero.stop{{background:rgba(240,48,80,.07);border:1.5px solid rgba(240,48,80,.2)}}
.hi{{font-size:40px;line-height:1}}
.hv{{font-size:17px;font-weight:700;margin-bottom:3px}}
.hero.go .hv{{color:var(--bull)}} .hero.wait .hv{{color:var(--neu)}} .hero.stop .hv{{color:var(--bear)}}
.he{{font-size:12px;color:var(--m);line-height:1.6;max-width:360px}} .he strong{{color:var(--t)}}
.hr{{text-align:right}} .hs{{font-family:var(--mono);font-size:34px;font-weight:700;line-height:1}}
.hero.go .hs{{color:var(--bull)}} .hero.wait .hs{{color:var(--neu)}} .hero.stop .hs{{color:var(--bear)}}
.hsl{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.1em;color:var(--m);margin-top:2px}}
.hall{{font-family:var(--mono);font-size:12px;font-weight:700;margin-top:5px;color:{alloc_col}}}
.hkl{{font-family:var(--mono);font-size:9px;color:var(--m);margin-top:2px}}
.meta-row{{display:flex;gap:7px;margin-bottom:9px;flex-wrap:wrap}}
.meta-tag{{font-family:var(--mono);font-size:9px;padding:4px 11px;border-radius:20px;border:1px solid var(--b);background:var(--bg2);color:var(--m)}}
.meta-tag.regime-bull{{color:var(--bull);background:rgba(0,200,120,.07);border-color:rgba(0,200,120,.2)}}
.meta-tag.regime-neutral{{color:var(--neu);background:rgba(240,165,0,.06);border-color:rgba(240,165,0,.15)}}
.meta-tag.regime-fear{{color:var(--bear);background:rgba(240,48,80,.07);border-color:rgba(240,48,80,.18)}}
.meta-tag.season-strong{{color:var(--bull);background:rgba(0,200,120,.06);border-color:rgba(0,200,120,.15)}}
.meta-tag.season-weak{{color:var(--bear);background:rgba(240,48,80,.06);border-color:rgba(240,48,80,.15)}}
.ew{{border-radius:9px;padding:12px 17px;margin-bottom:9px;display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center}}
.ew.a{{background:rgba(167,139,250,.05);border:1.5px solid rgba(167,139,250,.3);animation:bp 2.5s ease-in-out infinite}}
.ew.i{{background:rgba(0,200,120,.03);border:1px solid rgba(0,200,120,.12)}}
@keyframes bp{{0%,100%{{border-color:rgba(167,139,250,.3)}}50%{{border-color:rgba(167,139,250,.6)}}}}
.ewi{{font-size:26px}} .ewt{{font-size:13px;font-weight:600;margin-bottom:3px}}
.ew.a .ewt{{color:var(--purple)}} .ew.i .ewt{{color:var(--bull)}}
.ewd{{font-size:11px;color:var(--m);line-height:1.6}} .ewd strong{{color:var(--t)}}
.ewr{{text-align:right;flex-shrink:0}} .ewp{{font-family:var(--mono);font-size:24px;font-weight:700}}
.ew.a .ewp{{color:var(--purple)}} .ew.i .ewp{{color:var(--bull)}}
.ewpl{{font-family:var(--mono);font-size:8px;color:var(--m);text-transform:uppercase;letter-spacing:.1em;margin-top:2px}}
.ig{{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-bottom:10px}}
.ind{{background:var(--bg2);border:1px solid var(--b);border-radius:9px;padding:11px 12px;position:relative;overflow:hidden}}
.ind::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;border-radius:2px 2px 0 0}}
.ind.go::before{{background:var(--bull)}} .ind.wait::before{{background:var(--neu)}} .ind.stop::before{{background:var(--bear)}}
.it{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:5px}}
.in{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.1em;color:var(--m)}}
.is{{font-family:var(--mono);font-size:8px;padding:2px 7px;border-radius:3px;white-space:nowrap}}
.s-go{{background:rgba(0,200,120,.1);color:var(--bull);border:1px solid rgba(0,200,120,.2)}}
.s-wait{{background:rgba(240,165,0,.08);color:var(--neu);border:1px solid rgba(240,165,0,.15)}}
.s-stop{{background:rgba(240,48,80,.09);color:var(--bear);border:1px solid rgba(240,48,80,.15)}}
.iv{{font-family:var(--mono);font-size:18px;font-weight:700;line-height:1;margin-bottom:2px}}
.ind.go .iv{{color:var(--bull)}} .ind.wait .iv{{color:var(--neu)}} .ind.stop .iv{{color:var(--bear)}}
.id{{font-size:10px;color:var(--m);line-height:1.5}} .id b{{color:var(--t)}}
.pr{{height:2px;background:var(--d);border-radius:2px;margin:5px 0 3px;overflow:hidden}}
.pf{{height:100%;border-radius:2px}}
.bt-box{{display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:10px;background:var(--bg2);border:1px solid var(--b);border-radius:7px;padding:9px 14px;margin-bottom:9px;flex-wrap:wrap}}
.bt-l{{color:var(--m)}} .bt-s{{color:var(--m)}} .bt-n{{color:var(--m);font-size:9px}}
.stitle{{font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.16em;color:var(--m);padding:8px 0 7px;border-top:1px solid var(--b);margin-top:4px;display:flex;align-items:center;gap:8px}}
.stitle::after{{content:'';flex:1;height:1px;background:var(--b)}}
.sgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(185px,1fr));gap:8px;margin-bottom:10px}}
.sc{{background:var(--bg2);border:1px solid var(--b);border-radius:9px;padding:11px 12px;position:relative;overflow:hidden}}
.sc::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
.sc.go::before{{background:var(--bull)}} .sc.wait::before{{background:var(--neu)}} .sc.stop::before{{background:var(--bear)}} .sc.err{{opacity:.6}}
.sc-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:5px}}
.st{{font-family:var(--mono);font-size:13px;font-weight:700;color:var(--t)}} .sn{{font-size:10px;color:var(--m)}}
.ssig{{font-family:var(--mono);font-size:8px;padding:2px 8px;border-radius:3px;white-space:nowrap;align-self:flex-start}}
.sp{{font-family:var(--mono);font-size:16px;font-weight:700;color:var(--t);margin-bottom:6px}}
.sg4{{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:6px}}
.si{{background:var(--bg3);border-radius:5px;padding:4px 6px}}
.sl2{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--m);margin-bottom:2px}}
.sv{{font-family:var(--mono);font-size:11px;font-weight:700}}
.rsi-lo{{color:var(--bull)}} .rsi-hi{{color:var(--bear)}}
.sb-w{{height:3px;background:var(--d);border-radius:2px;overflow:hidden;margin-bottom:5px}}
.sb{{height:100%;border-radius:2px}}
.scr{{font-family:var(--mono);font-size:9px;color:var(--m)}}
.se{{font-family:var(--mono);font-size:9px;color:var(--bear)}}
.g2{{display:grid;grid-template-columns:3fr 2fr;gap:9px;margin-bottom:9px}}
.panel{{background:var(--bg2);border:1px solid var(--b);border-radius:9px;padding:12px 14px}}
.pt{{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.14em;color:var(--m);margin-bottom:10px;display:flex;align-items:center;gap:6px}}
.pt::after{{content:'';flex:1;height:1px;background:var(--b)}}
.badge{{font-family:var(--mono);font-size:8px;padding:2px 7px;border-radius:3px}}
.bb{{background:rgba(0,200,120,.08);color:var(--bull);border:1px solid rgba(0,200,120,.16)}}
.bp{{background:rgba(167,139,250,.08);color:var(--purple);border:1px solid rgba(167,139,250,.16)}}
.pb{{width:100%;border-collapse:collapse;font-size:11px}}
.pb th{{padding:6px 8px;font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--m);border-bottom:1px solid var(--b);text-align:left}}
.pb td{{padding:6px 8px;border-bottom:1px solid rgba(22,32,48,.6);vertical-align:top;line-height:1.5}}
.pb tr:last-child td{{border-bottom:none}}
.ga{{color:var(--bull);font-weight:600}} .wa{{color:var(--neu);font-weight:600}}
.ba{{color:var(--bear);font-weight:600}} .ea{{color:var(--purple);font-weight:600}}
.eb{{background:rgba(240,165,0,.05);border:1px solid rgba(240,165,0,.18);border-radius:7px;padding:8px 12px;margin-bottom:8px;font-family:var(--mono);font-size:9px;color:var(--m)}}
.footer{{margin-top:14px;padding-top:10px;border-top:1px solid var(--b);font-family:var(--mono);font-size:8.5px;color:var(--m);display:flex;justify-content:space-between;line-height:1.9;gap:20px}}
a{{color:#64a0ff}}
@media(max-width:900px){{
  .hero,.g2,.ig{{grid-template-columns:1fr}}
  .ew{{grid-template-columns:1fr}}
  .ig{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
</head>
<body>
<div class="w">
<div class="hdr">
  <div>
    <h1>Befekteto Dashboard <em>v3 · World-Class</em></h1>
    <div class="ab"><span class="dot"></span> Automatikusan frissitve: {today}</div>
  </div>
  <div style="text-align:right">
    <div class="sb2">⬤ {st_txt}</div>
    <div class="hm">Kovetkezo: <b>pentek, {next_fri}</b> · {log_data['success_count']}/13 forras</div>
  </div>
</div>

{errh}
{bt_html}

<div class="meta-row">
  <div class="meta-tag {regime_css}">{regime_label}</div>
  <div class="meta-tag {season_css}">{season['seasonLabel']}</div>
  <div class="meta-tag">CBOE Skew: {skew} {'EMELKEDETT' if data.get('skewElevated') else 'OK'}</div>
  <div class="meta-tag">Rec.prob: {rec}%</div>
  <div class="meta-tag">CNN F&G: {cnn} ({cnnR})</div>
</div>

<div class="hero {sig}">
  <div class="hi">{si}</div>
  <div>
    <div class="hv">{sv}</div>
    <div class="he">{se}</div>
  </div>
  <div class="hr">
    <div class="hs">{es}/100</div>
    <div class="hsl">Belepesi score</div>
    <div class="hall">Ajanlott allokacio: {alloc}% SPX / {100-alloc}% cash</div>
    <div class="hkl">Kelly-kriterium · {kelly['kellyLabel']}</div>
  </div>
</div>

<div class="ew {'a' if ca else 'i'}">
  <div class="ewi">{'⚠️' if ca else '✅'}</div>
  <div>
    <div class="ewt">{'KORREKCIO FIGYELMEZTETO – ' + str(cp) + '% valoszinuseg' if ca else 'Nincs korrekcios figyelmezteto – piac egeszseg'}</div>
    <div class="ewd">{'Ha pozicioban vagy: <strong>merlegel reszleges kilepest.</strong> Visszavasarlas: SPX -10% + VIX csokkeni kezd + score >= 50.' if ca else 'VIX normalis, EPS pozitiv. Ha pozicioban vagy: <strong>ulj nyugodtan.</strong>'}</div>
  </div>
  <div class="ewr">
    <div class="ewp">{cp}%</div>
    <div class="ewpl">korrekció esélye</div>
  </div>
</div>

<div class="ig">{inds}</div>

<div class="stitle">Sajat reszvenyek – belepesi score es korrekciokockazat</div>
<div class="sgrid">{stk}</div>

<div class="g2">
  <div class="panel">
    <div class="pt">Belepesi score + korrekciok historikus
      <span class="badge bb">>=65=vegyel</span>
      <span class="badge bp">>=60%=figyelj</span>
    </div>
    <canvas id="ch" height="155"></canvas>
  </div>
  <div class="panel">
    <div class="pt">Befektetoi playbook + Kelly allokacio</div>
    <table class="pb">
      <thead><tr><th></th><th>Helyzet</th><th>Teendo</th><th>Allok.</th></tr></thead>
      <tbody>
        <tr><td>🟢</td><td>Score>=65, korr.&lt;30%</td><td class="ga">FEKTESS BE</td><td class="ga">60-80%</td></tr>
        <tr><td>🟡</td><td>Score 40-65</td><td class="wa">FELEZD MEG</td><td class="wa">35-55%</td></tr>
        <tr><td>🔴</td><td>Score&lt;40</td><td class="ba">TARTSD VISSZA</td><td class="ba">0-25%</td></tr>
        <tr><td>🟣</td><td>Korr.>=60%</td><td class="ea">KILEPES</td><td class="ea">0-15%</td></tr>
        <tr><td>🔄</td><td>Piac -10%+score>=50</td><td class="ga">VISSZAVASAROL</td><td class="ga">55-75%</td></tr>
      </tbody>
    </table>
    <div style="margin-top:9px;background:rgba(167,139,250,.04);border:1px solid rgba(167,139,250,.16);border-radius:6px;padding:9px 11px;font-family:var(--mono);font-size:8.5px;color:var(--m);line-height:1.8">
      <b style="color:var(--purple)">Kilepesi trigger (4+ aktiv):</b><br>
      VIX&gt;25 emelkedo · EPS lefordul · Hozamgorbe invertál<br>
      Breadth&lt;45% · HY&gt;4.5% · Rec.prob&gt;20% · SKEW&gt;150
    </div>
  </div>
</div>

<div class="footer">
  <div>
    yfinance · FRED API · CBOE Skew · AAII · CNN F&G · S&P Global · GitHub Actions: pentek 20:00<br>
    Hibak: <a href="error_log.json">error_log.json</a> · v3 · Adaptiv sullyozas · Kelly · 13 forras
  </div>
  <div style="text-align:right">
    Buy &amp; hold timing eszkoz · Nem befektetesi tanacsadas
  </div>
</div>
</div>

<script>
Chart.defaults.color = '#3a5068';
Chart.defaults.font.family = "'JetBrains Mono', monospace";
Chart.defaults.font.size = 9;
const G = 'rgba(22,32,48,.9)';
const hD = {hd}, hS = {hs}, hC = {hc};
new Chart(document.getElementById('ch'), {{
  type: 'line',
  data: {{ labels: hD, datasets: [
    {{ label: 'Belepesi score', data: hS, borderColor: '#00c878', borderWidth: 2,
       pointRadius: 3,
       pointBackgroundColor: hS.map(s => s>=65 ? '#00c878' : s>=40 ? '#f0a500' : '#f03050'),
       tension: .3, fill: false }},
    {{ label: 'Korrekció %', data: hC, borderColor: 'rgba(167,139,250,.7)',
       borderWidth: 1.5, pointRadius: 0, tension: .3, fill: false, borderDash: [4,3] }},
    {{ label: 'Küszöb (65)', data: Array(hD.length).fill(65), type: 'line',
       borderColor: 'rgba(0,200,120,.2)', borderWidth: 1, borderDash: [3,3],
       pointRadius: 0, fill: false }},
  ]}},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ display: true, position: 'top', align: 'end',
        labels: {{ boxWidth: 12, boxHeight: 1, padding: 10, color: '#3a5068' }} }},
      tooltip: {{ backgroundColor: '#080c14', borderColor: '#162030', borderWidth: 1 }}
    }},
    scales: {{
      x: {{ grid: {{ color: G }}, ticks: {{ maxTicksLimit: 8, maxRotation: 0 }} }},
      y: {{ grid: {{ color: G }}, min: 0, max: 100 }}
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  OK  Dashboard -> {OUTPUT_HTML}")

# ── MAIN ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print("\n" + "="*58)
    print("  BEFEKTETO DASHBOARD v3 – World-Class Frissites")
    print("="*58 + "\n")

    data = {}
    print("  SPX...");                 data.update(safe(fetch_spx, {"spx":6700,"spxMA200":6490,"spxAboveMA":1.5,"spxFromHigh":-1,"spxChg":0,"realizedVol":15}, "SPX"))
    print("  VIX...");                 data.update(safe(fetch_vix, {"vix":16,"vixTrend":0,"vixRising":False,"vixAvg4w":16}, "VIX"))
    print("  CBOE Skew...");           data.update(safe(fetch_skew, {"skew":130,"skewElevated":False}, "Skew"))
    print("  EPS revision...");        data.update(safe(fetch_eps_score, {"epsScore":65,"epsTrend":0,"epsRaw":[]}, "EPS"))
    print("  Forward P/E...");         data.update(safe(lambda: fetch_valuation(data.get("spx", 6700)), {"forwardPE":20,"valScore":10,"valLabel":"FAIR"}, "Valuation"))
    print("  Hozamgorbe (FRED)...");   data.update(safe(fetch_yield_curve, {"yieldCurve":20}, "Hozamgorbe"))
    print("  HY Spread (FRED)...");    data.update(safe(fetch_hy_spread, {"hySpread":3.5}, "HY Spread"))
    print("  Recesszio (FRED)...");    data.update(safe(fetch_recession_prob, {"recProb":5.0}, "Recession"))
    print("  Fed Liq (FRED)...");      data.update(safe(fetch_fed_liquidity, {"fedNetLiq":5000,"fedBalance":8000}, "Fed Liq"))
    print("  Breadth (~30mp)...");     data.update(safe(fetch_breadth, {"breadth":65}, "Breadth"))
    print("  Put/Call (CBOE)...");     data.update(safe(fetch_put_call, {"putCall":0.85}, "Put/Call"))
    print("  AAII...");                data.update(safe(fetch_aaii, {"aaiiNet":10,"aaiiB":40,"aaiiBear":30}, "AAII"))
    print("  CNN Fear & Greed...");    data.update(safe(fetch_cnn_fear_greed, {"cnnFG":50,"cnnFGRating":"Neutral"}, "CNN F&G"))

    print("\n  Reszvenyek...")
    stocks = []
    for t, n in MY_STOCKS:
        s = safe(lambda t=t, n=n: fetch_stock(t, n), {"ticker":t,"name":n,"error":"Hiba"}, t)
        if s is not None:
            stocks.append(s)

    regime   = detect_regime(data)
    es       = calc_entry_score(data)
    cp       = calc_corr_prob(data)
    kelly    = calc_kelly_allocation(es, cp, regime)
    season   = calc_seasonality()
    log_data = save_error_log()
    history  = load_history()
    history  = save_history(history, data, es, cp, regime, kelly)
    generate_html(data, es, cp, history, stocks, log_data, kelly, season, regime)

    print(f"\n  Score: {es}/100  Korrekcio: {cp}%")
    print(f"  Rezsim: {regime}  Kelly: {kelly['kellyAlloc']}% SPX")
    print(f"  Hibak: {len(errors)}/13 forras\n")

    if not args.no_browser:
        import subprocess, platform
        if platform.system() == "Windows":
            os.startfile(OUTPUT_HTML)
        elif platform.system() == "Darwin":
            subprocess.run(["open", OUTPUT_HTML])
        else:
            subprocess.run(["xdg-open", OUTPUT_HTML])

    print("  KESZ!\n" + "="*58 + "\n")
    if len(errors) >= 11:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err = {
            "last_run": datetime.datetime.now().isoformat(),
            "status": "CRASHED",
            "errors": [{"source": "FATAL", "error": str(e),
                        "traceback": traceback.format_exc()[:800]}],
            "success_count": 0
        }
        with open(ERROR_LOG, "w", encoding="utf-8") as f:
            json.dump(err, f, indent=2, ensure_ascii=False)
        print(f"\nFATAL ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)



