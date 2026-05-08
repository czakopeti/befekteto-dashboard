"""
auto_update.py – Befekteto Dashboard v5
Változások v4-hez képest:
- Új 4-szintű playbook: MUST BUY (85+) / ÓVATOS VÉTEL (65-84) / VÁRAKOZÁS (40-64) / VÉDEKEZÉS (<40)
- Extreme Fear modifier (CNN<25 → +1 kategória)
- Indikátor súlyok vizuálisan megjelenítve (KRITIKUS / FONTOS / KIEGÉSZÍTŐ)
- Szektorrotáció jelző (XLK, XLF, XLE, XLV, XLI, XLP ETF relatív erő)
- HUF/USD árfolyam modul
- Riasztás szekció (nagy esések, zuhanó kések, watchlist kiesők)
- EPS revíziós delta szűrő (screener.py-ban)
- Score időzítési fix: árfolyam-visszaerősítés szűrő
- Screener integráció (screener.json beolvasása)
- Navy téma redesign
"""

import json, os, re, sys, datetime, argparse
import requests, pandas as pd, yfinance as yf
from pathlib import Path

# ── KONSTANSOK ───────────────────────────────────────────────
FY26_EPS_EST  = 338.0
PE_FAIR_VALUE = 19.5
FRED_API_KEY  = os.environ.get("FRED_API_KEY", "YOUR_FRED_API_KEY_HERE")
FMP_API_KEY   = os.environ.get("FMP_API_KEY", "")
OUTPUT_HTML   = "index.html"
HISTORY_FILE  = "history.json"
ERROR_LOG     = "error_log.json"
SCREENER_FILE = "screener.json"

# Playbook határok (v5)
SCORE_MUST_BUY   = 85
SCORE_CAUT_BUY   = 65
SCORE_WAIT       = 40
EXTREME_FEAR_CNN = 25
CRON_SCHEDULE    = "0 15 * * 5"  # Péntek 15:00 UTC = 17:00 Budapest (CEST)

# Riasztás szűrők
ALERT_DROP_PCT   = -7.0    # napi esés küszöb
ALERT_TRAP_PCT   = -25.0   # SMA200 alatt "zuhanó kés" küszöb

# Figyelőlista – minőségi cégek (bővíthető)
QUALITY_WATCHLIST = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","BRK-B",
    "V","MA","COST","HD","UNH","JNJ","PG","WMT","KO","PEP",
    "ADBE","CRM","ORCL","INTC","AMD","QCOM","TXN","AVGO",
    "JPM","BAC","GS","BLK","SPGI","MCO",
    "CRWD","DDOG","NET","SNOW","PLTR","PANW",
    "LLY","ABBV","MRK","TMO","ISRG","MDT",
    "NFLX","DIS","SPOT",
    "ACN","INTU","NOW","HUBS",
]

# Szektorrotáció ETF-ek
SECTOR_ETFS = {
    "Tech":   "XLK", "Pénzügy": "XLF", "Energia": "XLE",
    "Egész.": "XLV", "Ipar":    "XLI", "Def.fogy.":"XLP",
    "Comm.":  "XLC", "RE":      "XLRE","Anyag":   "XLB",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
errors  = []

def log(msg, ok=True): print(f"  {'OK' if ok else 'WW'} {msg}")

def safe(fn, fallback, label):
    try:
        r = fn(); log(f"{label}: OK"); return r
    except Exception as e:
        log(f"{label} HIBA ({str(e)[:80]}) -> fallback", ok=False)
        errors.append({"source": label, "error": str(e)[:120],
                       "time": datetime.datetime.now().isoformat()})
        return fallback

# ══════════════════════════════════════════════════════════════
# ALAP ADATOK
# ══════════════════════════════════════════════════════════════
def fetch_spx():
    h = yf.Ticker("^GSPC").history(period="1y")
    p     = float(h["Close"].iloc[-1])
    ma50  = float(h["Close"].rolling(50).mean().iloc[-1])
    ma200 = float(h["Close"].rolling(200).mean().iloc[-1])
    prev  = float(h["Close"].iloc[-2])
    ath   = float(h["Close"].max())
    ma5   = float(h["Close"].rolling(5).mean().iloc[-1])
    ma5p  = float(h["Close"].rolling(5).mean().iloc[-2])
    price_recovering = p > ma5 and ma5 > ma5p

    # SMA40 havi – a rubber band alap (hosszú táv)
    try:
        hm = yf.Ticker("^GSPC").history(period="15y", interval="1mo")
        sma40_monthly = float(hm["Close"].rolling(40).mean().iloc[-1])
    except Exception:
        sma40_monthly = p * 0.79  # fallback: ~-21% diszkont becslés

    # Rubber Band feszítés
    rb_stretch = round((p - sma40_monthly) / sma40_monthly * 100, 1)

    # Rubber band score penalty (CSAK ha AF is negatív – ezért pass-oljuk, score-ban combináljuk)
    if rb_stretch > 40:   rb_penalty = -20; rb_cat = "Extrém feszítés"
    elif rb_stretch > 30: rb_penalty = -15; rb_cat = "Erősen feszített"
    elif rb_stretch > 20: rb_penalty = -8;  rb_cat = "Feszített"
    elif rb_stretch > 10: rb_penalty = -3;  rb_cat = "Enyhe feszítés"
    elif rb_stretch >= 0: rb_penalty = 0;   rb_cat = "Normál"
    elif rb_stretch > -15:rb_penalty = 5;   rb_cat = "Trendnél (diszkont)"
    else:                 rb_penalty = 10;  rb_cat = "Mély diszkont"

    # Target levels
    rb_target    = round(sma40_monthly)
    rb_overshoot = round(sma40_monthly * 0.85)

    return {
        "spx": round(p), "spxMA200": round(ma200), "spxMA50": round(ma50),
        "spxChg": round((p-prev)/prev*100, 2),
        "spxAboveMA": round((p-ma200)/ma200*100, 1),
        "spxFromHigh": round((p-ath)/ath*100, 1),
        "priceRecovering": price_recovering,
        "sma40Monthly":  round(sma40_monthly, 1),
        "rbStretch":     rb_stretch,
        "rbPenalty":     rb_penalty,
        "rbCat":         rb_cat,
        "rbTarget":      rb_target,
        "rbOvershoot":   rb_overshoot,
    }

def fetch_vix():
    h = yf.Ticker("^VIX").history(period="30d")
    c  = float(h["Close"].iloc[-1])
    p  = float(h["Close"].iloc[-2])
    p5 = float(h["Close"].iloc[-5]) if len(h) >= 5 else p
    # VIX spike: egynapos >20% ugrás = Black Swan előjel
    vix_spike_1d  = (c - p) / p * 100 if p > 0 else 0
    vix_spike_5d  = (c - p5) / p5 * 100 if p5 > 0 else 0
    vix_black_swan = vix_spike_1d > 20   # KILÉPÉSI TRIGGER
    return {
        "vix":          round(c, 1),
        "vixTrend":     round(c - p, 1),
        "vixRising":    c > p,
        "vixSpike1d":   round(vix_spike_1d, 1),
        "vixSpike5d":   round(vix_spike_5d, 1),
        "vixBlackSwan": vix_black_swan,
    }

def fetch_fred_series(series_id, n=15):
    if FRED_API_KEY in ("YOUR_FRED_API_KEY_HERE", "", None):
        raise ValueError("FRED API kulcs nincs beállítva!")
    d = requests.get(
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&file_type=json&sort_order=desc&limit={n}", timeout=15).json()
    obs = [o for o in d["observations"] if o["value"] != "."]
    return [float(o["value"]) for o in obs]

def fetch_hy_spread():
    return {"hySpread": round(fetch_fred_series("BAMLH0A0HYM2")[0], 2)}

# ══════════════════════════════════════════════════════════════
# PUT/CALL RATIO (ÚJ – profi kontrarian jelző)
# ══════════════════════════════════════════════════════════════
def fetch_pcr():
    """
    CBOE Total Put/Call Ratio – ingyenes napi adat.
    PCR > 1.2 = Extreme Fear → kontrarian vétel
    PCR < 0.7 = Eufória → figyelj, tetőközelben lehet a piac
    PCR 0.7–0.9 = Normál
    """
    try:
        # CBOE CSV endpoint
        url = "https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/equitypc.csv"
        r = requests.get(url, timeout=15, headers=HEADERS)
        if r.status_code == 200:
            lines = r.text.strip().split('\n')
            # Utolsó sor az aktuális nap
            for line in reversed(lines):
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    try:
                        pcr = float(parts[1])
                        if 0.3 <= pcr <= 3.0:
                            pcr_prev_lines = [l for l in lines[-20:] if l.strip() and l[0].isdigit()]
                            pcr_20d = sum(float(l.split(',')[1]) for l in pcr_prev_lines[-20:]
                                          if len(l.split(','))>=2) / max(1, len(pcr_prev_lines[-20:]))
                            pcr_sig = ("bull" if pcr > 1.1 else "bear" if pcr < 0.65 else "wait")
                            pcr_desc = (f"Extreme Fear (>{pcr:.2f}) – kontrarian vétel" if pcr > 1.1
                                        else f"Eufória (<{pcr:.2f}) – figyelj, tető közelben" if pcr < 0.65
                                        else f"Normál ({pcr:.2f})")
                            return {"pcr": round(pcr, 2), "pcr20d": round(pcr_20d, 2),
                                    "pcrSignal": pcr_sig, "pcrDesc": pcr_desc}
                    except ValueError:
                        continue
    except Exception as e:
        log(f"PCR hiba: {e}", ok=False)

    # Fallback: yfinance ^CPCE ticker
    try:
        import yfinance as yf
        h = yf.Ticker("^CPCE").history(period="25d")
        if not h.empty:
            pcr = float(h["Close"].iloc[-1])
            pcr20 = float(h["Close"].mean())
            pcr_sig = "bull" if pcr > 1.0 else "bear" if pcr < 0.55 else "wait"
            return {"pcr": round(pcr,2), "pcr20d": round(pcr20,2),
                    "pcrSignal": pcr_sig,
                    "pcrDesc": f"Equity PCR: {pcr:.2f} (20d átlag: {pcr20:.2f})"}
    except Exception:
        pass

    return {"pcr": 0.85, "pcr20d": 0.85, "pcrSignal": "wait", "pcrDesc": "Nincs adat (fallback: 0.85)"}

# ══════════════════════════════════════════════════════════════
# YIELD CURVE DE-INVERSION SEBESSÉG (ÚJ)
# ══════════════════════════════════════════════════════════════
def fetch_yield_deinversion():
    """
    A profi indikátor: nem csak az inverzió, hanem a RE-STEEPENING sebesség.
    Ha a görbe gyorsan pozitívba fordul (de-inversion) → Fed pánikszerűen vág
    → Ez a legveszélyesebb piaci helyzet, nem maga az inverzió.
    """
    try:
        vals = fetch_fred_series("T10Y2Y", n=26)  # 26 hónap
        cur   = vals[0] * 100   # bp
        m1    = vals[1] * 100 if len(vals)>1 else cur
        m3    = vals[3] * 100 if len(vals)>3 else cur
        m6    = vals[6] * 100 if len(vals)>6 else cur

        speed_1m = round(cur - m1, 1)   # bp / hó
        speed_3m = round(cur - m3, 1)   # bp / 3hó
        was_inverted = m6 < 0           # 6 hónapja invertált volt?

        # Veszélyes: gyors re-steepening + előtte invertált volt
        rapid_reinversion = (was_inverted and cur > 0 and speed_3m > 30)
        dangerous = rapid_reinversion

        if dangerous:
            yi_sig  = "bear"
            yi_desc = f"⚠ GYORS RE-STEEPENING! ({speed_3m:+.0f}bp/3hó) – Fed pánikkamata vágás jele"
        elif cur < -25:
            yi_sig  = "bear"
            yi_desc = f"Mélyen invertált ({cur:+.0f}bp) – recessziós jel"
        elif cur < 0:
            yi_sig  = "wait"
            yi_desc = f"Enyhe inverzió ({cur:+.0f}bp)"
        elif speed_3m > 20 and was_inverted:
            yi_sig  = "wait"
            yi_desc = f"Re-steepening ({cur:+.0f}bp, +{speed_3m:.0f}bp/3hó) – figyelj"
        else:
            yi_sig  = "bull"
            yi_desc = f"Normál ({cur:+.0f}bp, trend: {speed_1m:+.0f}bp/hó)"

        return {
            "yieldCurve": round(cur), "yieldTrend": round(speed_1m),
            "yieldSpeed3m": speed_3m, "yieldWasInv": was_inverted,
            "yieldDangerous": dangerous,
            "yieldSignal": yi_sig, "yieldDesc": yi_desc,
        }
    except Exception as e:
        return {"yieldCurve": 20, "yieldTrend": 0, "yieldSpeed3m": 0,
                "yieldWasInv": False, "yieldDangerous": False,
                "yieldSignal": "wait", "yieldDesc": "Nincs adat"}

# ══════════════════════════════════════════════════════════════
# McCLELLAN SUMMATION INDEX (ÚJ)
# A piaci szélesség gyorsulása – "szívverés" mutató
# ══════════════════════════════════════════════════════════════
def fetch_mcclellan():
    """
    McClellan Summation Index az S&P 500 advance-decline adatokból.
    - Summation > 0 és emelkedő: egészséges bull piac
    - Summation nulla alá süllyed: szinte minden nagy esést jelzett előre
    - Oscillator divergencia: index új csúcson, de summation csökken = veszély
    """
    try:
        import yfinance as yf
        # NYSE A/D proxyk
        adv = yf.Ticker("^ADVN").history(period="1y")["Close"].dropna()
        dec = yf.Ticker("^DECN").history(period="1y")["Close"].dropna()
        if len(adv) < 40 or len(dec) < 40:
            raise ValueError("Nincs elég A/D adat")

        # Közös dátumokra igazítás
        df = pd.DataFrame({"adv": adv, "dec": dec}).dropna()
        ad_line = df["adv"] - df["dec"]

        # McClellan Oscillator = EMA19 - EMA39 az A-D különbségen
        ema19 = ad_line.ewm(span=19, adjust=False).mean()
        ema39 = ad_line.ewm(span=39, adjust=False).mean()
        oscillator = ema19 - ema39

        # Summation Index = kumulatív összeg
        summation = oscillator.cumsum()

        cur  = float(summation.iloc[-1])
        prev = float(summation.iloc[-5]) if len(summation) >= 5 else cur
        osc  = float(oscillator.iloc[-1])
        trend = "bull" if cur > prev and cur > 0 else "bear" if cur < prev and cur < 0 else "wait"
        zero_cross_down = cur < 0 and prev > 0
        zero_cross_up   = cur > 0 and prev < 0

        if zero_cross_down:
            sig  = "bear"
            desc = f"⚠ NULLA ALÁ BUKOTT ({cur:.0f}) – komoly korrekció jele!"
        elif zero_cross_up:
            sig  = "bull"
            desc = f"✓ Nulla fölé emelkedett ({cur:.0f}) – bull erő visszatér"
        elif cur > 500:
            sig  = "bull"
            desc = f"Erős ({cur:.0f}) – egészséges szélesség"
        elif cur > 0:
            sig  = "bull"
            desc = f"Pozitív ({cur:.0f}) – bull piac"
        elif cur > -500:
            sig  = "wait"
            desc = f"Negatív ({cur:.0f}) – gyengülő szélesség"
        else:
            sig  = "bear"
            desc = f"Mélyen negatív ({cur:.0f}) – medve piac"

        return {"mcSum": round(cur), "mcOsc": round(osc), "mcSignal": sig,
                "mcDesc": desc, "mcTrend": trend,
                "mcZeroCrossDown": zero_cross_down, "mcZeroCrossUp": zero_cross_up}

    except Exception as e:
        log(f"McClellan hiba: {e}", ok=False)
        return {"mcSum": 0, "mcOsc": 0, "mcSignal": "wait",
                "mcDesc": "Nincs adat", "mcTrend": "wait",
                "mcZeroCrossDown": False, "mcZeroCrossUp": False}

# ══════════════════════════════════════════════════════════════
# GLOBAL LIQUIDITY PROXY (ÚJ)
# Fed mérleg + TGA egyenleg = nettó likviditás
# ══════════════════════════════════════════════════════════════
def fetch_global_liquidity():
    """
    TRUE NET LIQUIDITY PROXY (v5.2)
    Képlet: Fed Mérleg - TGA - RRP (Reverse Repo)

    Ez a "második szint" amit a profik figyelnek:
    - WALCL   = Fed mérlegfőösszeg
    - WTREGEN = Treasury General Account (ha elszívja a pénzt → bearish)
    - RRPONTSYD = Overnight Reverse Repo (ha bankok parkoltatják → bearish)

    Hiába nő az M2, ha TGA + RRP elszívja → piac esik.
    Ez a mutató heti szinten VEZETI az SPX-et.
    """
    try:
        walcl_v = fetch_fred_series("WALCL",    n=16)  # Fed mérleg (heti)
        tga_v   = fetch_fred_series("WTREGEN",  n=16)  # TGA (heti)
        rrp_v   = fetch_fred_series("RRPONTSYD",n=16)  # Reverse Repo (napi→heti)

        fed_now = walcl_v[0]; fed_4w = walcl_v[4] if len(walcl_v)>4 else fed_now
        tga_now = tga_v[0];   tga_4w = tga_v[4]   if len(tga_v)>4  else tga_now
        rrp_now = rrp_v[0];   rrp_4w = rrp_v[4]   if len(rrp_v)>4  else rrp_now

        # TRUE NET LIQUIDITY = Fed - TGA - RRP
        net_liq = round(fed_now - tga_now - rrp_now)
        net_4w  = round(fed_4w  - tga_4w  - rrp_4w)
        chg_4w  = round(net_liq - net_4w)
        chg_pct = round(chg_4w / abs(net_4w) * 100, 1) if net_4w != 0 else 0

        # RRP külön figyelő – ha magas, bankok nem hiteleznek
        rrp_high = rrp_now > 500  # 500B$ felett aggasztó

        trend = ("bull" if chg_4w > 100 else "bear" if chg_4w < -100 else "wait")

        if trend == "bear" and chg_4w < -500:
            sig  = "bear"
            desc = (f"⚠ GYORS SZŰKÜLÉS ({chg_4w:+.0f}B$) – "
                    f"TGA:{tga_now:.0f}B$ + RRP:{rrp_now:.0f}B$ elszívja a likviditást!")
        elif trend == "bear":
            sig  = "wait"
            desc = f"Enyhe szűkülés ({chg_4w:+.0f}B$ / 4hét) | RRP: {rrp_now:.0f}B$"
        elif trend == "bull":
            sig  = "bull"
            rrp_note = f" | RRP csökken ✓" if rrp_now < rrp_4w else ""
            desc = f"Bővülő ({chg_4w:+.0f}B$ / 4hét) – üzemanyag áramlik{rrp_note}"
        else:
            sig  = "wait"
            desc = f"Stabil (nettó: {net_liq:,.0f}B$) | TGA: {tga_now:.0f}B$ | RRP: {rrp_now:.0f}B$"

        return {
            "netLiq": net_liq, "netLiqChg4w": chg_4w, "netLiqChgPct": chg_pct,
            "fedBal": round(fed_now), "tgaBal": round(tga_now), "rrpBal": round(rrp_now),
            "rrpHigh": rrp_high,
            "liqSignal": sig, "liqDesc": desc, "liqTrend": trend,
        }

    except Exception as e:
        log(f"Global Liquidity hiba: {e}", ok=False)
        return {"netLiq": 0, "netLiqChg4w": 0, "netLiqChgPct": 0,
                "fedBal": 0, "tgaBal": 0, "rrpBal": 0, "rrpHigh": False,
                "liqSignal": "wait", "liqDesc": "Nincs adat", "liqTrend": "wait"}

# ══════════════════════════════════════════════════════════════
# DIX + GEX – Dark Pool Index + Gamma Exposure (ÚJ)
# Forrás: squeezemetrics.com – INGYENES CSV
# ══════════════════════════════════════════════════════════════
def fetch_dix_gex():
    """
    DIX (Dark Index): intézményi dark pool vételi aktivitás
    - DIX > 45%: smart money vásárol (bullish)
    - DIX < 40%: smart money elad/disztribúál (bearish)
    - DIX csökken miközben SPX emelkedik: DIVERGENCIA – veszélyes!

    GEX (Gamma Exposure): market maker gamma pozíció
    - GEX > 0: stabilizáló hatás (market makerek eladnak emelkedésnél)
    - GEX < 0: destabilizáló hatás (market makerek vesznek esésnél → amplifikáció!)
    """
    try:
        url = "https://squeezemetrics.com/monitor/static/DIX.csv"
        r   = requests.get(url, timeout=20, headers=HEADERS)
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")

        from io import StringIO
        df = pd.read_csv(StringIO(r.text), parse_dates=["date"])
        df = df.sort_values("date").tail(30)

        dix_cur  = float(df["dix"].iloc[-1]) * 100   # % formátumba
        gex_cur  = float(df["gex"].iloc[-1])          # milliárd USD
        dix_20d  = float(df["dix"].tail(20).mean()) * 100

        # DIX trend
        dix_1w   = float(df["dix"].iloc[-5]) * 100 if len(df) >= 5 else dix_cur
        dix_falling = dix_cur < dix_1w - 2  # 2%+ csökkenés

        if dix_cur > 46 and not dix_falling:
            dix_sig  = "bull"
            dix_desc = f"Intézményi vétel ({dix_cur:.1f}%) – smart money aktív"
        elif dix_cur < 40 or dix_falling:
            dix_sig  = "bear"
            dix_desc = f"Intézményi disztribúció ({dix_cur:.1f}%) – smart money elad!"
        else:
            dix_sig  = "wait"
            dix_desc = f"Semleges ({dix_cur:.1f}%, 20d: {dix_20d:.1f}%)"

        gex_sig  = "wait" if gex_cur > 0 else "bear"
        gex_desc = (f"Pozitív GEX ({gex_cur/1e9:.1f}B$) – stabilizáló" if gex_cur > 0
                    else f"⚠ NEGATÍV GEX ({gex_cur/1e9:.1f}B$) – amplifikáló esés lehetséges!")

        return {"dix": round(dix_cur, 1), "dix20d": round(dix_20d, 1),
                "gex": gex_cur, "dixSignal": dix_sig, "dixDesc": dix_desc,
                "gexSignal": gex_sig, "gexDesc": gex_desc,
                "dixFalling": dix_falling}

    except Exception as e:
        log(f"DIX/GEX hiba: {e}", ok=False)
        return {"dix": 43.0, "dix20d": 43.0, "gex": 5e9,
                "dixSignal": "wait", "dixDesc": "Nincs adat (fallback)",
                "gexSignal": "wait", "gexDesc": "Nincs adat",
                "dixFalling": False}

# ══════════════════════════════════════════════════════════════
# COT SMART MONEY PROXY (ÚJ)
# CFTC Commitment of Traders – Large Speculators nettó pozíció
# ══════════════════════════════════════════════════════════════
def fetch_cot_smart_money():
    """
    CFTC COT report: a nagy spekulánsok (hedge fundok) nettó S&P500 futures pozíciója
    - Nettó long: smart money bullish
    - Nettó short: smart money bearish / fedezi magát

    Forrás: CFTC ingyenes API
    """
    try:
        # CFTC API – S&P 500 E-mini futures (kód: 13874A)
        url  = "https://publicreporting.cftc.gov/api/odata/v1/MarketsAndPrices"
        params = {
            "$filter": "marketAndExchangeNames eq 'E-MINI S&P 500 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE'",
            "$orderby": "reportDate desc",
            "$top": "8",
            "$select": "reportDate,noncommercialLong,noncommercialShort,changeInNoncommercialLong,changeInNoncommercialShort"
        }
        r = requests.get(url, params=params, timeout=20, headers=HEADERS)
        if r.status_code != 200:
            raise ValueError(f"CFTC API {r.status_code}")

        data = r.json().get("value", [])
        if not data:
            raise ValueError("Üres CFTC adat")

        latest = data[0]
        net_long  = int(latest.get("noncommercialLong", 0))
        net_short = int(latest.get("noncommercialShort", 0))
        net       = net_long - net_short

        # Historikus átlag a 8 hétből
        nets = [int(d.get("noncommercialLong", 0)) - int(d.get("noncommercialShort", 0))
                for d in data]
        avg_net = sum(nets) / len(nets) if nets else net
        z_score = (net - avg_net) / max(1, abs(avg_net) * 0.3)  # normalizált eltérés

        if z_score > 1.0:
            sig  = "bull"
            desc = f"Smart money LONG ({net:+,.0f} nettó) – bikapiaci pozicionálás"
        elif z_score < -1.0:
            sig  = "bear"
            desc = f"Smart money SHORT ({net:+,.0f} nettó) – óvatosság/fedezés!"
        else:
            sig  = "wait"
            desc = f"Semleges ({net:+,.0f} nettó, z-score: {z_score:.1f})"

        return {"cotNet": net, "cotAvg": round(avg_net), "cotZScore": round(z_score, 1),
                "cotSignal": sig, "cotDesc": desc,
                "cotDate": latest.get("reportDate","")[:10]}

    except Exception as e:
        log(f"COT hiba: {e}", ok=False)
        # Fallback: semleges érték
        return {"cotNet": 0, "cotAvg": 0, "cotZScore": 0.0,
                "cotSignal": "wait", "cotDesc": "Nincs adat (CFTC fallback)",
                "cotDate": ""}

def fetch_forward_pe(spx_price):
    pe = round(spx_price / FY26_EPS_EST, 1)
    val_score = round(max(0, min(30, (PE_FAIR_VALUE+1.5-pe)/6*30)))
    label = ("ALULÉRTÉKELT" if pe<18 else "FAIR" if pe<21
             else "TÚLÉRTÉKELT" if pe<25 else "EXTRÉM DRÁGA")
    return {"forwardPE": pe, "valScore": val_score, "valLabel": label}

# ══════════════════════════════════════════════════════════════
# HUF/USD ÁRFOLYAM (ÚJ)
# ══════════════════════════════════════════════════════════════
def fetch_huf_usd():
    try:
        h = yf.Ticker("HUF=X").history(period="30d")
        if len(h) < 2:
            raise ValueError("Nincs HUF adat")
        rate     = float(h["Close"].iloc[-1])    # HUF per USD
        rate_1w  = float(h["Close"].iloc[-5]) if len(h)>=5 else rate
        rate_1m  = float(h["Close"].iloc[0])
        chg_1w   = round((rate - rate_1w) / rate_1w * 100, 2)
        chg_1m   = round((rate - rate_1m) / rate_1m * 100, 2)
        # Erősödő forint = USD drágul (nagyobb szám = gyengébb forint)
        usd_per_huf = round(1 / rate * 1000, 4) if rate > 0 else 0
        trend = "erősödő" if chg_1w < -0.5 else "gyengülő" if chg_1w > 0.5 else "stabil"
        return {
            "hufRate":   round(rate, 1),     # Ft / USD
            "hufChg1w":  chg_1w,
            "hufChg1m":  chg_1m,
            "hufTrend":  trend,
            "hufSignal": "wait",
        }
    except Exception as e:
        return {"hufRate": 0, "hufChg1w": 0, "hufChg1m": 0,
                "hufTrend": "nincs adat", "hufSignal": "wait"}

# ══════════════════════════════════════════════════════════════
# SZEKTORROTÁCIÓ (ÚJ)
# ══════════════════════════════════════════════════════════════
def fetch_sector_rotation():
    try:
        tickers = list(SECTOR_ETFS.values())
        raw = yf.download(tickers, period="3mo",
                          auto_adjust=True, progress=False, threads=True)["Close"]
        results = {}
        for name, etf in SECTOR_ETFS.items():
            if etf not in raw.columns:
                continue
            s = raw[etf].dropna()
            if len(s) < 20:
                continue
            perf_1m = round((float(s.iloc[-1]) / float(s.iloc[-20]) - 1) * 100, 1)
            perf_3m = round((float(s.iloc[-1]) / float(s.iloc[0])   - 1) * 100, 1)
            results[name] = {"etf": etf, "perf1m": perf_1m, "perf3m": perf_3m}

        if not results:
            return {"sectors": [], "leading": "", "lagging": ""}

        sorted_sectors = sorted(results.items(), key=lambda x: x[1]["perf1m"], reverse=True)
        sectors_list = [{"name": k, **v} for k, v in sorted_sectors]
        return {
            "sectors": sectors_list,
            "leading": sectors_list[0]["name"] if sectors_list else "",
            "lagging": sectors_list[-1]["name"] if sectors_list else "",
        }
    except Exception as e:
        return {"sectors": [], "leading": "", "lagging": ""}

# ══════════════════════════════════════════════════════════════
# RIASZTÁS SZEKCIÓ (ÚJ)
# ══════════════════════════════════════════════════════════════
def fetch_alerts():
    alerts = []
    try:
        tickers = QUALITY_WATCHLIST[:40]  # limitálás API rate-re
        raw = yf.download(
            tickers, period="1y",
            auto_adjust=True, progress=False, threads=True
        )["Close"]

        for ticker in tickers:
            if ticker not in raw.columns:
                continue
            s = raw[ticker].dropna()
            if len(s) < 200:
                continue

            price   = float(s.iloc[-1])
            prev    = float(s.iloc[-2])
            ma200   = float(s.rolling(200).mean().iloc[-1])
            chg_day = round((price - prev) / prev * 100, 1)
            vs_ma200= round((price - ma200) / ma200 * 100, 1)

            # 1. Nagy napi esés
            if chg_day <= ALERT_DROP_PCT:
                alerts.append({
                    "type":    "drop",
                    "ticker":  ticker,
                    "value":   chg_day,
                    "label":   "NAGY ESÉS",
                    "desc":    f"Napi {chg_day:.1f}% – vizsgáld meg az okát",
                    "date":    datetime.date.today().isoformat(),
                })

            # 2. Zuhanó kés: screener watchlistből kiesett (-25% alá)
            if vs_ma200 <= ALERT_TRAP_PCT:
                alerts.append({
                    "type":    "trap",
                    "ticker":  ticker,
                    "value":   vs_ma200,
                    "label":   "ZUHANÓ KÉS",
                    "desc":    f"SMA200 alatt {vs_ma200:.1f}% – screenerből kiesett",
                    "date":    datetime.date.today().isoformat(),
                })

        # Rendezés: nagy esések előre
        alerts.sort(key=lambda x: x["value"])
        return alerts[:10]  # max 10 riasztás

    except Exception as e:
        log(f"Riasztás fetch hiba: {e}", ok=False)
        return []

# ══════════════════════════════════════════════════════════════
# MEGLÉVŐ TA FUNKCIÓK (változatlan v4-ből)
# ══════════════════════════════════════════════════════════════
def fetch_ta_spx():
    h = yf.Ticker("^GSPC").history(period="6mo")
    close = h["Close"]
    # VIX Term Structure
    try:
        vix_s = float(yf.Ticker("^VIX").history(period="2d")["Close"].iloc[-1])
        vix3m = float(yf.Ticker("^VIX3M").history(period="2d")["Close"].iloc[-1])
        tr = round(vix_s/vix3m, 3)
        t_sig = "bull" if tr<0.9 else "wait" if tr<1.0 else "bear"
        t_desc = ("Contango – nyugodt" if tr<0.9 else "Flat – figyelem" if tr<1.0 else "Backwardation – PÁNIK!")
    except Exception:
        tr=0.95; t_sig="wait"; t_desc="Nincs adat"
    # MACD
    e12=close.ewm(span=12).mean(); e26=close.ewm(span=26).mean()
    macd=e12-e26; sig=macd.ewm(span=9).mean(); hist=macd-sig
    h_c=float(hist.iloc[-1]); h_p=float(hist.iloc[-2]); h_p2=float(hist.iloc[-3])
    m_rising=h_c>h_p; m_accel=(h_c-h_p)>(h_p-h_p2)
    m_sig="bull" if m_rising and m_accel else "wait" if m_rising else "bear"
    m_desc="Momentum épül" if m_sig=="bull" else "Emelkedik" if m_rising else "Gyengül"
    # Bollinger
    bb_m=close.rolling(20).mean(); bb_s=close.rolling(20).std()
    bb_up=bb_m+2*bb_s; bb_lo=bb_m-2*bb_s
    bw_c=float((bb_up.iloc[-1]-bb_lo.iloc[-1])/bb_m.iloc[-1]*100)
    bw_a=float(((bb_up-bb_lo).iloc[-20:]/bb_m.iloc[-20:]).mean()*100)
    squeeze=bw_c<bw_a*0.85
    bb_desc="SQUEEZE – nagy mozgás közeleg!" if squeeze else f"Széles ({bw_c:.1f}%)"
    # RSI + divergencia
    delta=close.diff(); gain=delta.clip(lower=0).rolling(14).mean()
    loss=(-delta.clip(upper=0)).rolling(14).mean()
    rsi=100-(100/(1+gain/loss))
    r_c=float(rsi.iloc[-1]); r_5=float(rsi.iloc[-5])
    p_up=close.iloc[-1]>close.iloc[-5]; r_up=r_c>r_5
    bull_div=not p_up and r_up and r_c<40; bear_div=p_up and not r_up and r_c>60
    r_sig=("bull" if bull_div else "stop" if bear_div
           else "wait" if r_c>75 else "go" if r_c<35 else "wait")
    r_desc=("Bullish divergencia!" if bull_div else "Bearish divergencia!" if bear_div
            else f"RSI:{r_c:.0f} – túlvett" if r_c>75 else f"RSI:{r_c:.0f} – normális")
    # CNN F&G
    cnn=50; cnn_r="Neutral"
    try:
        r2=requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata/",
                        headers={**HEADERS,"Referer":"https://www.cnn.com/"},timeout=15)
        d=r2.json(); cnn=round(float(d["fear_and_greed"]["score"]))
        cnn_r=d["fear_and_greed"]["rating"]
    except Exception: pass

    # SPX AF(18,6) – TRIX heti timeframe (ÚJ – Peter chartja alapján)
    try:
        spx_w = yf.Ticker("^GSPC").history(period="5y", interval="1wk")["Close"].dropna()
        def trix_w(s, n):
            e1=s.ewm(span=n,adjust=False).mean()
            e2=e1.ewm(span=n,adjust=False).mean()
            e3=e2.ewm(span=n,adjust=False).mean()
            return ((e3-e3.shift(1))/e3.shift(1)*100).fillna(0)
        t18 = trix_w(spx_w, 18); t6 = trix_w(spx_w, 6)
        af_cur  = float(t18.iloc[-1] - t6.iloc[-1])
        af_prev = float(t18.iloc[-2] - t6.iloc[-2])
        af_prev2= float(t18.iloc[-3] - t6.iloc[-3])
        af_turning_bull = af_cur > 0 and af_prev <= 0   # liláról sárgára fordul
        af_turning_bear = af_cur < 0 and af_prev >= 0   # sárgáról lilára fordul
        af_positive = af_cur > 0
        if af_turning_bull:
            af_sig="bull"; af_desc=f"Liláról SÁRGÁRA fordult (+{af_cur:.3f}) – VÉTELI TRIGGER!"
        elif af_turning_bear:
            af_sig="bear"; af_desc=f"Sárgáról LILÁRA fordult ({af_cur:.3f}) – KILÉPÉSI FIGYELMEZTETÉS!"
        elif af_positive:
            af_sig="bull"; af_desc=f"Sárga oszlop ({af_cur:.3f}) – bullish momentum"
        else:
            af_sig="bear"; af_desc=f"Lila oszlop ({af_cur:.3f}) – gyengülő momentum"
    except Exception:
        af_cur=0; af_sig="wait"; af_desc="Nincs adat"; af_turning_bull=False; af_turning_bear=False

    return {
        "termRatio":tr,"termSignal":t_sig,"termDesc":t_desc,
        "macdHist":round(h_c,2),"macdSignal":m_sig,"macdDesc":m_desc,
        "bbSqueeze":squeeze,"bbDesc":bb_desc,"bbWidth":round(bw_c,1),
        "rsiSPX":round(r_c,1),"rsiSignal":r_sig,"rsiDesc":r_desc,
        "rsiDiv":"bull" if bull_div else "bear" if bear_div else "none",
        "cnnFG":cnn,"cnnFGRating":cnn_r,
        "afCur":round(af_cur,4),"afSignal":af_sig,"afDesc":af_desc,
        "afTurningBull":af_turning_bull,"afTurningBear":af_turning_bear,
    }

def fetch_medium_term():
    # Copper/Gold
    try:
        cu=float(yf.Ticker("HG=F").history(period="5d")["Close"].dropna().iloc[-1])
        au=float(yf.Ticker("GC=F").history(period="5d")["Close"].dropna().iloc[-1])
        cg=round(cu/au,6)
        cu4w=float(yf.Ticker("HG=F").history(period="30d")["Close"].dropna().iloc[0])
        au4w=float(yf.Ticker("GC=F").history(period="30d")["Close"].dropna().iloc[0])
        cg4w=cu4w/au4w
        cg_t="bull" if cg>cg4w*1.01 else "bear" if cg<cg4w*0.99 else "wait"
        cg_d=("Emelkedő – bővülési jel" if cg_t=="bull"
              else "Csökkenő – lassulási jel" if cg_t=="bear" else "Stabil")
    except Exception:
        cg=0.000070; cg_t="wait"; cg_d="Nincs adat"
    # ISM New Orders
    try:
        ism_v=fetch_fred_series("NAPMNO"); ism=round(ism_v[0],1)
        ism_p=round(ism_v[1],1) if len(ism_v)>1 else ism
        i_sig="bull" if ism>55 else "bear" if ism<48 else "wait"
        i_d=(f"{ism} – Bővülés" if ism>55 else f"{ism} – Zsugorodás" if ism<48
             else f"{ism} – Semleges ({'emelk.' if ism>ism_p else 'csokkeno'})")
    except Exception:
        ism=50; i_sig="wait"; i_d="Nincs adat"
    # Golden/Death Cross
    try:
        spx_c=yf.Ticker("^GSPC").history(period="1y")["Close"]
        ma50=float(spx_c.rolling(50).mean().iloc[-1])
        ma200=float(spx_c.rolling(200).mean().iloc[-1])
        ma50_4=float(spx_c.rolling(50).mean().iloc[-20])
        c_sig="bull" if ma50>ma200 else "bear"
        c_d=("Golden Cross – bullish trend" if c_sig=="bull" and ma50>ma50_4
             else "MA50>MA200 de lassul" if c_sig=="bull" else "Death Cross – bearish")
    except Exception:
        c_sig="wait"; c_d="Nincs adat"
    # Breadth – kétféle (MA50 + MA200, divergencia figyeléssel)
    try:
        SAMPLE=["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","JPM","UNH","V",
                "XOM","JNJ","PG","MA","HD","AVGO","CVX","MRK","ABBV","KO",
                "PEP","COST","WMT","BAC","TMO","LLY","ORCL","NFLX","AMD","CRM",
                "ACN","DHR","TXN","NEE","PM","MDT","HON","QCOM","UPS","AMGN",
                "CAT","BMY","LOW","SBUX","GS","BLK","ISRG","SYK","GILD","SPGI"]
        dat=yf.download(SAMPLE,period="1y",auto_adjust=True,progress=False,threads=True)["Close"]
        ab50=ab200=tot=0
        for col in dat.columns:
            s2=dat[col].dropna()
            if len(s2)<200: continue
            m50=s2.rolling(50).mean().iloc[-1]; m200=s2.rolling(200).mean().iloc[-1]
            p=s2.iloc[-1]
            if pd.notna(m50) and pd.notna(m200) and pd.notna(p):
                tot+=1
                if p>m50:  ab50+=1
                if p>m200: ab200+=1
        br50  = round(ab50/tot*100)  if tot>0 else 50
        br200 = round(ab200/tot*100) if tot>0 else 50

        # Divergencia: SPX emelkedett de MA200 breadth csökkent
        # (proxy: ha br200 < br50 - 15, disztribúció van)
        dist_warning = br200 < (br50 - 15) and br200 < 55
        div_desc = (" ⚠ DIVERGENCIA – SPX tartja magát de belső gyengülés!" if dist_warning else "")
    except Exception:
        br50=50; br200=50; dist_warning=False; div_desc=""
    return {
        "cgRatio":cg,"cgTrend":cg_t,"cgDesc":cg_d,
        "ismNewOrders":ism,"ismSignal":i_sig,"ismDesc":i_d,
        "crossSignal":c_sig,"crossDesc":c_d,
        "breadth":br50,"breadth200":br200,
        "breadthDiv":dist_warning,"breadthDivDesc":div_desc,
    }

def fetch_long_term():
    # LEI
    try:
        lei_v=fetch_fred_series("USSLIND"); lei=round(lei_v[0],2)
        l3=round(lei_v[2],2) if len(lei_v)>2 else lei
        l6=round(lei_v[5],2) if len(lei_v)>5 else lei
        lc3=round(lei-l3,2); lc6=round(lei-l6,2)
        l_sig=("bull" if lc3>0 and lc6>0 else "bear" if lc3<0 and lc6<0 else "wait")
        l_d=(f"Emelkedő – bővülés jön ({lc3:+.2f}/3h)" if l_sig=="bull"
             else f"Csökkenő – lassulás ({lc3:+.2f}/3h)" if l_sig=="bear"
             else f"Vegyes ({lc3:+.2f}/3h)")
    except Exception:
        lei=100; l_sig="wait"; l_d="Nincs adat"; lc3=0
    # M2
    try:
        m2_v=fetch_fred_series("M2SL")
        m2_yoy=round((m2_v[0]/m2_v[11]-1)*100,1) if len(m2_v)>=12 else 4.0
        m_sig="bull" if m2_yoy>5 else "wait" if m2_yoy>0 else "bear"
        m_d=(f"+{m2_yoy}% YoY – bőséges likv." if m2_yoy>5
             else f"+{m2_yoy}% YoY – semleges" if m2_yoy>0 else f"{m2_yoy}% YoY – szűkülő")
    except Exception:
        m2_yoy=4; m_sig="wait"; m_d="Nincs adat"
    # UMich
    try:
        umi_v=fetch_fred_series("UMCSENT"); umi=round(umi_v[0],1)
        umi_p=round(umi_v[2],1) if len(umi_v)>2 else umi
        u_t="emelkedő" if umi>umi_p else "csökkenő"
        u_sig="bull" if umi>80 else "bear" if umi<60 else "wait"
        u_d=f"{umi} – {u_t}"
    except Exception:
        umi=70; u_sig="wait"; u_d="Nincs adat"
    # Hozamgörbe – de-inversion sebességgel
    try:
        yi = fetch_yield_deinversion()
        yld      = yi["yieldCurve"]
        ylt      = yi["yieldTrend"]
        yld_data = yi
    except Exception:
        yld = 20; ylt = 0
        yld_data = {"yieldCurve":20,"yieldTrend":0,"yieldSpeed3m":0,
                    "yieldWasInv":False,"yieldDangerous":False,
                    "yieldSignal":"wait","yieldDesc":"Nincs adat"}
    # Rec. prob
    try:
        rec=round(fetch_fred_series("RECPROUSM156N")[0],1)
    except Exception:
        rec=5.0
    return {
        "leiCur":lei,"leiSignal":l_sig,"leiDesc":l_d,"leiChg3":lc3,
        "m2Yoy":m2_yoy,"m2Signal":m_sig,"m2Desc":m_d,
        "umiCur":umi,"umiSignal":u_sig,"umiDesc":u_d,
        "yieldCurve":yld,"yieldTrend":ylt,"recProb":rec,
        **yld_data,
    }

# ══════════════════════════════════════════════════════════════
# REZSIM + SCORE (v5 – frissített határok)
# ══════════════════════════════════════════════════════════════
def detect_regime(base, now, lng):
    vix=base.get("vix",18); cnn=now.get("cnnFG",50)
    rec=lng.get("recProb",5); yld=lng.get("yieldCurve",20)
    lei=lng.get("leiSignal","wait")
    if isinstance(cnn,(int,float)) and cnn<30 and vix>22: return "extreme_fear"
    if isinstance(rec,(int,float)) and rec>15 and yld<0:  return "recession_watch"
    if lei=="bear" and isinstance(rec,(int,float)) and rec>10: return "slowdown"
    fear=0
    if vix>25: fear+=2
    if vix>35: fear+=2
    if isinstance(rec,(int,float)) and rec>15: fear+=2
    if yld<-10: fear+=2
    return "fear" if fear>=5 else "neutral" if fear>=2 else "bull"

def sv(signal):
    return {"bull":1,"go":1,"wait":0,"neutral":0,"bear":-1,"stop":-1}.get(signal,0)

def calc_entry_score(now, mid, lng, base, smart=None):
    if smart is None: smart = {}
    vix=base.get("vix",18); yld=lng.get("yieldCurve",20)
    hy=base.get("hySpread",3.5); pe=base.get("forwardPE",20)
    rec=lng.get("recProb",5); cnn=now.get("cnnFG",50)
    regime=detect_regime(base,now,lng)
    price_ok = base.get("priceRecovering", True)
    s=50
    # MOST szekció
    term_v=sv(now.get("termSignal","wait")); macd_v=sv(now.get("macdSignal","wait"))
    rsi_v=sv(now.get("rsiSignal","wait"))
    cnn_v=(2 if cnn<25 else 1 if cnn<40 else 0 if cnn<60 else -1)
    af_v = (2 if now.get("afTurningBull") else -2 if now.get("afTurningBear")
            else 1 if now.get("afSignal")=="bull" else -1)
    # PCR – Put/Call Ratio (ÚJ profi kontrarian jelző)
    pcr = base.get("pcr", 0.85)
    pcr_v = (2 if pcr > 1.1 else -2 if pcr < 0.65 else 0)
    now_s=term_v*5+macd_v*5+rsi_v*4+cnn_v*6+af_v*7+pcr_v*6  # PCR súlya: 6
    if regime=="extreme_fear": now_s+=cnn_v*6
    s+=now_s
    # 3-6 hónap
    cg_v=sv(mid.get("cgTrend","wait")); ism_v2=sv(mid.get("ismSignal","wait"))
    br=mid.get("breadth",50); br_v=(2 if br>65 else -1 if br<40 else 0)
    crs_v=sv(mid.get("crossSignal","wait"))
    s+=cg_v*7+ism_v2*8+br_v*5+crs_v*5
    # 6-18 hónap
    lei_v=sv(lng.get("leiSignal","wait")); m2_v=sv(lng.get("m2Signal","wait"))
    umi_v=sv(lng.get("umiSignal","wait"))
    yld_v=(2 if yld>25 else 1 if yld>0 else -1 if yld<-10 else 0)
    s+=lei_v*9+m2_v*7+umi_v*5+yld_v*4
    # Makro korrekciók
    s+=(3 if vix<16 else 1 if vix<22 else -2 if vix<28 else -5)
    s+=(3 if hy<3.0 else 1 if hy<3.8 else -3 if hy>4.5 else 0)
    s+=(3 if pe<18 else 1 if pe<21 else -2 if pe>24 else 0)
    if isinstance(rec,(int,float)):
        s+=(0 if rec<5 else -3 if rec<12 else -8 if rec<20 else -15)

    # ── RUBBER BAND FESZÍTÉS (v5.2 ÚJ) ─────────────────────
    rb_stretch  = base.get("rbStretch", 0)
    rb_penalty  = base.get("rbPenalty", 0)
    af_negative = now.get("afSignal","wait") == "bear"
    af_turn_b   = now.get("afTurningBear", False)
    # Penalty CSAK ha AF is negatívba fordult vagy fordul
    if af_negative or af_turn_b:
        s += rb_penalty  # negatív szám = csökkenti a score-t
        if rb_penalty < 0:
            log(f"  🎯 Rubber Band penalty: {rb_penalty} (feszítés: {rb_stretch}% + AF negatív)")

    # ── CSÚCSCSAPDA KORREKCIÓ (v5.2 JAVÍTOTT) ───────────────
    # Fix 1: CNN threshold 70→60 (az egész Greed zóna veszélyes)
    # Fix 2: RRP magas esetén CNN-től függetlenül is érvényes
    spx_above = base.get("spxAboveMA", 0)
    rrp_high  = smart.get("rrpBal", 0) > 800  # RRP > 800B$
    cnn_greed = isinstance(cnn,(int,float)) and cnn > 60  # 70→60 javítva

    if spx_above > 8 and pe > 23 and cnn_greed:
        s -= 15
        log("  ⚠ Csúcskorrekció: -15 (SPX>8% MA200 + P/E>23 + CNN>60)")
    elif spx_above > 8 and pe > 23 and rrp_high:
        s -= 10  # RRP-alapú penalty CNN nélkül is
        log(f"  ⚠ RRP penalty: -10 (RRP {smart.get('rrpBal',0):.0f}B$ > 800B$)")

    # ── YIELD CURVE DE-INVERSION VESZÉLY ─────────────────────
    if lng.get("yieldDangerous", False):
        s -= 20
        log("  🔴 YIELD DE-INVERSION VESZÉLY: -20 pont")

    # ── SMART MONEY RÉTEG (v5.1) ─────────────────────────────
    # McClellan Summation
    mc_sig = smart.get("mcSignal","wait")
    mc_v = sv(mc_sig)
    if smart.get("mcZeroCrossDown"): mc_v = -3
    elif smart.get("mcZeroCrossUp"):  mc_v = 3
    s += mc_v * 6
    # Global Liquidity (legmagasabb smart money súly)
    liq_v = sv(smart.get("liqSignal","wait"))
    s += liq_v * 8
    # DIX Dark Pool
    dix_v = sv(smart.get("dixSignal","wait"))
    if smart.get("dixFalling"): dix_v -= 1
    s += dix_v * 7
    # COT Smart Money
    cot_z = smart.get("cotZScore", 0.0)
    cot_v = (2 if cot_z>1.5 else 1 if cot_z>0.5 else -2 if cot_z<-1.5 else -1 if cot_z<-0.5 else 0)
    s += cot_v * 5
    # v5: árfolyam-visszaerősítés
    if not price_ok and s > 70:
        s = min(s, 72)
    month = datetime.date.today().month
    if month in [5, 6, 7, 8] and s > 65:
        s = round(s * 0.95)
    return min(100, max(0, round(s)))

def calc_corr_prob(now, mid, lng, base):
    vix=base.get("vix",18); vixT=base.get("vixTrend",0)
    hy=base.get("hySpread",3.5); spxA=base.get("spxAboveMA",2)
    rec=lng.get("recProb",5); yld=lng.get("yieldCurve",20)
    lei=lng.get("leiSignal","wait"); term=now.get("termSignal","wait")
    macd=now.get("macdSignal","wait"); rsi_d=now.get("rsiDiv","none")
    cg=mid.get("cgTrend","wait"); regime=detect_regime(base,now,lng)
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

def calc_kelly_v5(es, cp, regime="bull", smart=None, base=None, now=None, mid=None):
    """
    v5.3: Half-Kelly + explicit vétó logika
    
    Half-Kelly: a matematikailag optimális Kelly felét használjuk.
    Indok: Full Kelly maximalizálja a log-hozamot, de a volatilitás
    duplájával. Half-Kelly a hozam ~87%-át hozza, feleannyi kockázattal.
    
    Vétó-logika (hard cap-ek):
    1. GEX negatív + VIX spike >20% → max score 35 → VÉDEKEZÉS kényszer
    2. Net Liquidity 4 hetes csökkenés < -500B$ → max score 65
    3. McClellan nulla alá + breadth divergencia → max score 55
    """
    if smart is None: smart = {}
    if base  is None: base  = {}
    if now   is None: now   = {}
    if mid   is None: mid   = {}

    # ── VÉTÓ LOGIKA – hard cap-ek ────────────────────────────
    effective_es = es
    veto_reasons = []

    # Vétó 1: GEX negatív + VIX spike → azonnali védekezés
    gex_neg   = smart.get("gex", 1) < 0
    vix_spike = base.get("vixBlackSwan", False) or base.get("vixSpike1d", 0) > 15
    if gex_neg and vix_spike:
        effective_es = min(effective_es, 35)
        veto_reasons.append("⚡ GEX negatív + VIX spike → max 35")

    # Vétó 2: Net Liquidity gyors szűkülés
    liq_chg = smart.get("netLiqChg4w", 0)
    if liq_chg < -500:
        effective_es = min(effective_es, 65)
        veto_reasons.append(f"💧 Net Liq szűkül ({liq_chg:+.0f}B$) → max 65")

    # Vétó 3: McClellan nulla alá + breadth divergencia
    mc_below  = smart.get("mcSum", 0) < 0 or smart.get("mcZeroCrossDown", False)
    br_div    = mid.get("breadthDiv", False)
    if mc_below and br_div:
        effective_es = min(effective_es, 55)
        veto_reasons.append("📉 McClellan < 0 + Breadth div. → max 55")

    # Vétó 4: SPX 52 hetes csúcs közelén + divergencia büntetés
    spx_near_high = base.get("spxFromHigh", -10) > -3  # -3% = csúcs közelén
    mc_declining  = smart.get("mcTrend", "wait") == "bear"
    br200_low     = mid.get("breadth200", 60) < 50
    if spx_near_high and mc_declining and br200_low:
        effective_es = min(effective_es, effective_es - 15)
        veto_reasons.append("🔝 SPX csúcs + McClellan csökken + Breadth gyenge → -15")

    effective_es = max(0, min(100, round(effective_es)))

    veto_active = len(veto_reasons) > 0
    veto_txt    = " | ".join(veto_reasons) if veto_reasons else ""

    # ── HALF-KELLY SZÁMÍTÁS ──────────────────────────────────
    # Allokáció = teljes Kelly / 2
    # Ez csökkenti a volatilitást ~50%-kal, a hozam ~87%-a megmarad

    if regime == "extreme_fear" and effective_es >= 40:
        full_kelly = max(35, min(65, round((effective_es + 15) * 0.5)))
        half_kelly = round(full_kelly * 0.5)
        return {"kellyAlloc": half_kelly, "kellyCash": 100 - half_kelly,
                "kellyFull": full_kelly,
                "kellyLabel": f"Extreme Fear – Half-Kelly: {half_kelly}% (full: {full_kelly}%)",
                "playbook": "extreme_fear",
                "vetoActive": veto_active, "vetoReasons": veto_txt,
                "effectiveScore": effective_es}

    if effective_es >= SCORE_MUST_BUY:
        full_kelly = max(70, min(85, round(effective_es * 0.9)))
        half_kelly = round(full_kelly * 0.5)
        return {"kellyAlloc": half_kelly, "kellyCash": 100 - half_kelly,
                "kellyFull": full_kelly,
                "kellyLabel": f"MUST BUY – Half-Kelly: {half_kelly}% (full: {full_kelly}%)",
                "playbook": "must_buy",
                "vetoActive": veto_active, "vetoReasons": veto_txt,
                "effectiveScore": effective_es}

    if effective_es >= SCORE_CAUT_BUY:
        full_kelly = max(35, min(55, round(effective_es * 0.65)))
        half_kelly = round(full_kelly * 0.5)
        return {"kellyAlloc": half_kelly, "kellyCash": 100 - half_kelly,
                "kellyFull": full_kelly,
                "kellyLabel": f"Óvatos vétel – Half-Kelly: {half_kelly}% (full: {full_kelly}%)",
                "playbook": "caut_buy",
                "vetoActive": veto_active, "vetoReasons": veto_txt,
                "effectiveScore": effective_es}

    if effective_es >= SCORE_WAIT:
        full_kelly = max(10, min(25, round(effective_es * 0.35)))
        half_kelly = round(full_kelly * 0.5)
        return {"kellyAlloc": half_kelly, "kellyCash": 100 - half_kelly,
                "kellyFull": full_kelly,
                "kellyLabel": "Várakozás – csak tartás",
                "playbook": "wait",
                "vetoActive": veto_active, "vetoReasons": veto_txt,
                "effectiveScore": effective_es}

    full_kelly = max(0, min(10, round(effective_es * 0.2)))
    half_kelly = round(full_kelly * 0.5)
    return {"kellyAlloc": half_kelly, "kellyCash": 100 - half_kelly,
            "kellyFull": full_kelly,
            "kellyLabel": "Védekezés – minimális kitettség",
            "playbook": "defense",
            "vetoActive": veto_active, "vetoReasons": veto_txt,
            "effectiveScore": effective_es}

def calc_seasonality():
    m=datetime.date.today().month
    mn={1:"jan",2:"feb",3:"már",4:"ápr",5:"máj",6:"jún",
        7:"júl",8:"aug",9:"szept",10:"okt",11:"nov",12:"dec"}
    if m==9:   return {"seasonLabel":"Szeptember – leggyengébb hónap","seasonStrength":"weak"}
    elif m in [5,6,7,8]: return {"seasonLabel":f"Gyenge szezon ({mn[m]}) – Sell in May","seasonStrength":"weak"}
    elif m in [11,12,1,2,3,4]: return {"seasonLabel":f"Erős szezon ({mn[m]}) – Nov-Apr avg +7.5%","seasonStrength":"strong"}
    return {"seasonLabel":f"Semleges ({mn[m]})","seasonStrength":"neutral"}

# ══════════════════════════════════════════════════════════════
# HISTORY + SCREENER LOADER
# ══════════════════════════════════════════════════════════════
def load_history():
    if Path(HISTORY_FILE).exists():
        with open(HISTORY_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return []

def load_screener_data():
    if Path(SCREENER_FILE).exists():
        try:
            with open(SCREENER_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {"stocks":[],"updated":None,"count":0,"params":{}}

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
       "errors":errors,"success_count":14-len(errors)}
    with open(ERROR_LOG,"w",encoding="utf-8") as f:
        json.dump(d,f,indent=2,ensure_ascii=False)
    return d


# ══════════════════════════════════════════════════════════════
# HTML GENERÁLÁS (v5 – teljes navy redesign)
# ══════════════════════════════════════════════════════════════
def generate_html(base, now, mid, lng, es, cp, history, alerts,
                  log_data, kelly, season, regime, screener_data, sectors, huf, smart=None):
    if smart is None: smart = {}

    today      = datetime.date.today().strftime("%Y. %B %d.")
    nfd        = (4 - datetime.date.today().weekday()) % 7 or 7
    next_fri   = (datetime.date.today() + datetime.timedelta(days=nfd)).strftime("%B %d.")
    sc_col     = "#00d488" if log_data["status"]=="OK" else "#f0a500" if log_data["status"]=="PARTIAL" else "#f04060"
    st_txt     = "Minden forrás OK" if not errors else f"{len(errors)} forrás fallback"
    src_count  = 14 - len(errors)
    alloc      = kelly["kellyAlloc"]
    playbook   = kelly.get("playbook","wait")
    vix=base.get("vix",18); yld=lng.get("yieldCurve",20)
    rec=lng.get("recProb",5); cnn=now.get("cnnFG",50)
    hy=base.get("hySpread",3.5); pe=base.get("forwardPE",20)

    # Extreme Fear modifier aktív?
    ef_active = isinstance(cnn,(int,float)) and cnn < EXTREME_FEAR_CNN

    # Playbook fő üzenet
    pb_msgs = {
        "must_buy":     ("MUST BUY – Teljes belépés", f"DCA módban telepítsd a tőkét 2 héten át. Kelly: <strong>{alloc}%</strong> SPX."),
        "caut_buy":     ("ÓVATOS VÉTEL – Részleges pozíció", f"Pozíció építés óvatosan. Ajánlott allokáció: <strong>{alloc}%</strong> SPX."),
        "wait":         ("VÁRAKOZÁS – Csak tartás", f"Meglévő pozíciókat tartsd, új vásárlás <strong>nem</strong> javasolt. Cash gyűjtés a következő 65+ score-ra."),
        "defense":      ("VÉDEKEZÉS – Minimális kitettség", f"Ne vegyél semmit. Cash megőrzés. Allokáció max: <strong>{alloc}%</strong>."),
        "extreme_fear": ("EXTREME FEAR – Kontrarian vétel!", f"Vér folyik az utcán. CNN F&G: {cnn}. Kontrarian belépés: <strong>{alloc}%</strong> SPX."),
    }
    sig_title, sig_desc = pb_msgs.get(playbook, pb_msgs["wait"])

    pb_colors = {"must_buy":"#00d488","caut_buy":"#4da6ff","wait":"#f0a500","defense":"#f04060","extreme_fear":"#a78bfa"}
    pb_col = pb_colors.get(playbook,"#f0a500")

    # Backtest
    bt_html=""
    if len(history)>=4:
        s0=history[0].get("spx",0)
        if s0 and s0>0:
            inv=False; sv3=100.0; bh=100.0
            for i in range(1,len(history)):
                ph=history[i-1]; ch=history[i]
                sp=ph.get("spx",1); r=(ch.get("spx",sp)-sp)/sp if sp else 0
                if ph.get("corrProb",0)>=60: inv=False
                elif ph.get("entryScore",0)>=SCORE_CAUT_BUY: inv=True
                sv3*=(1+r*(1 if inv else 0)); bh*=(1+r)
            bts=round(sv3-100,1); btb=round(bh-100,1)
            btc="#00d488" if bts>=btb else "#f0a500"
            bt_html=(f'<div class="bt-box"><span class="bt-l">Historikus (signal követése):</span>'
                     f'<span style="color:{btc};font-weight:700">Stratégia: {bts:+.1f}%</span>'
                     f'<span class="bt-s">vs</span><span>Buy&Hold: {btb:+.1f}%</span>'
                     f'<span class="bt-n">({len(history)} hét)</span></div>')

    # Indikátor HTML segédfüggvény (v5: súlysávok + imp. badge + 5 kategória)
    def ind(cls, name, val, desc, weight=5, imp="FONTOS", cat5=None):
        col={"go":"#00d488","bull":"#00d488","wait":"#f0a500","neutral":"#f0a500",
             "bear":"#f04060","stop":"#f04060"}.get(cls,"#f0a500")
        bt={"go":"BULL","bull":"BULL","wait":"NEU","neutral":"NEU","bear":"BEAR","stop":"BEAR"}.get(cls,"NEU")
        imp_col={"KRITIKUS":"#f04060","FONTOS":"#f0a500","KIEGÉSZÍTŐ":"#4da6ff"}.get(imp,"#f0a500")
        imp_bg ={"KRITIKUS":"#f0406015","FONTOS":"#f0a50015","KIEGÉSZÍTŐ":"#4da6ff15"}.get(imp,"#f0a50015")
        wt_pct = round(weight/9*100)
        wt_col ={"KRITIKUS":"#f04060","FONTOS":"#f0a500","KIEGÉSZÍTŐ":"#4da6ff"}.get(imp,"#f0a500")
        sig_col={"BULL":"#00d488","NEU":"#f0a500","BEAR":"#f04060"}.get(bt,"#f0a500")

        # 5 kategória display
        cat5_html = ""
        if cat5:
            cur = cat5.get("cur", 3)       # 1-5
            avg = cat5.get("avg", "")      # referencia átlag
            refs= cat5.get("refs", [])     # 5 határérték leírás
            lbl = cat5.get("labels", ["Nagyon gyenge","Gyenge","Közepes","Erős","Nagyon erős"])
            cats_colors = ["#f04060","#f08040","#f0a500","#7dd3fc","#00d488"]
            cur_color = cats_colors[cur-1] if 1<=cur<=5 else "#f0a500"
            cat5_html = '<div class="c5-wrap">'
            for i in range(1,6):
                active_style = f"background:{cats_colors[i-1]};color:#000;" if i==cur else f"border:1px solid {cats_colors[i-1]}40;color:{cats_colors[i-1]};"
                title = refs[i-1] if i <= len(refs) else lbl[i-1]
                cat5_html += f'<div class="c5-box" style="{active_style}" title="{title}">{i}</div>'
            cat5_html += f'<span class="c5-lbl" style="color:{cur_color}">{lbl[cur-1]}</span>'
            if avg:
                cat5_html += f'<span class="c5-avg">· átlag: {avg}</span>'
            cat5_html += '</div>'

        return (f'<div class="ind">'
                f'<div class="ind-left">'
                f'<div class="ind-name">{name}'
                f'<span class="imp-pill" style="color:{imp_col};background:{imp_bg}">{imp}</span></div>'
                f'<div class="ind-val" style="color:{col}">{val}</div>'
                f'<div class="ind-sub">{desc}</div>'
                f'{cat5_html}'
                f'</div>'
                f'<div class="ind-weight">'
                f'<div class="wt-label">súly: {weight}/9</div>'
                f'<div class="wt-bar"><div class="wt-fill" style="width:{wt_pct}%;background:{wt_col}"></div></div>'
                f'</div>'
                f'<div class="sig-badge" style="color:{sig_col};background:{sig_col}18;border:1px solid {sig_col}30">{bt}</div>'
                f'</div>')

    cnn_desc = f"{now.get('cnnFGRating','?')}"
    if isinstance(cnn,(int,float)) and cnn<25:
        cnn_desc += " · <b style='color:#a78bfa'>EXTREME FEAR AKTÍV!</b>"

    pcr_val  = base.get("pcr", 0.85)
    pcr_sig  = base.get("pcrSignal","wait")
    pcr_desc = base.get("pcrDesc","?")
    pcr_cat  = (5 if pcr_val>1.2 else 4 if pcr_val>1.0 else 3 if pcr_val>0.8
                else 2 if pcr_val>0.65 else 1)

    # AF heti trend
    af_cur       = now.get("afCur", 0)
    af_sig       = now.get("afSignal","wait")
    af_desc      = now.get("afDesc","?")
    af_turn_bull = now.get("afTurningBull", False)
    af_turn_bear = now.get("afTurningBear", False)
    af_trend_html = (" 🟢 <b>Forduló: lila→sárga</b>" if af_turn_bull
                     else " 🔴 <b>Forduló: sárga→lila</b>" if af_turn_bear else "")

    # Fontossági sorrendben: AF(7) > PCR(6) = CNN(6) > MACD(5) > VIX Term(5) > RSI(4) > BB(3) > VIX(3)
    rsi_c   = now.get("rsiSPX", 50)
    rsi_cat = (1 if rsi_c>80 else 2 if rsi_c>70 else 3 if rsi_c>40 else 4 if rsi_c>30 else 5)
    vix_cat = (5 if vix<14 else 4 if vix<18 else 3 if vix<22 else 2 if vix<28 else 1)
    cnn_cat = (1 if isinstance(cnn,(int,float)) and cnn>75
               else 5 if isinstance(cnn,(int,float)) and cnn<25
               else 2 if isinstance(cnn,(int,float)) and cnn>60
               else 4 if isinstance(cnn,(int,float)) and cnn<40 else 3)
    af_cat  = (5 if af_turn_bull else 4 if af_cur>0.1
               else 1 if af_turn_bear else 2 if af_cur<-0.1 else 3)

    now_html="".join([
        ind(now.get("afSignal","wait"),
            f"SPX AF (18,6) – heti TRIX{af_trend_html}",
            str(round(af_cur,4)), af_desc, weight=7, imp="KRITIKUS",
            cat5={"cur":af_cat,"avg":"0.0",
                  "refs":["Lila, eső (-0.3<)","Lila, gyenge (-0.1 – -0.3)",
                          "Semleges (0 körül)","Sárga, gyenge (0–0.1)","Sárga, erős (>0.1) / forduló"]}),

        ind(pcr_sig, "Put/Call Ratio (PCR) – profi kontrarian",
            str(pcr_val), pcr_desc, weight=6, imp="KRITIKUS",
            cat5={"cur":pcr_cat,"avg":"0.85",
                  "refs":["Eufória (<0.65) – tető közelben, BEARISH!","Alacsony (0.65–0.75)",
                          "Normál (0.75–1.0)","Magas (1.0–1.2) – félelem",
                          "Extreme Fear (>1.2) – kontrarian vétel!"]}),

        ind("go" if isinstance(cnn,(int,float)) and cnn<30 else
            "bear" if isinstance(cnn,(int,float)) and cnn>70 else "wait",
            "CNN Fear & Greed", str(cnn), cnn_desc, weight=6, imp="FONTOS",
            cat5={"cur":cnn_cat,"avg":"50",
                  "refs":["Extreme Fear (<25) – kontrarian vétel","Fear (25–40)","Neutral (40–60)",
                          "Greed (60–75)","Extreme Greed (>75) – körültekintés"]}),

        ind(now.get("macdSignal","wait"), "MACD Hisztogram",
            str(now.get("macdHist","?")), now.get("macdDesc","?"), weight=5, imp="FONTOS",
            cat5={"cur":(5 if now.get("macdSignal")=="bull" else 2 if now.get("macdSignal")=="bear" else 3),
                  "avg":"0",
                  "refs":["Csökkenő, negatív","Negatív, lassul","Nulla körül",
                          "Pozitív, lassul","Pozitív, gyorsuló – erős momentum"]}),

        ind(now.get("termSignal","wait"), "VIX Term Structure (VIX/VIX3M)",
            str(now.get("termRatio","?")), now.get("termDesc","?"), weight=5, imp="FONTOS",
            cat5={"cur":(5 if now.get("termRatio",1)<0.85 else 4 if now.get("termRatio",1)<0.92
                         else 3 if now.get("termRatio",1)<0.97 else 2 if now.get("termRatio",1)<1.0 else 1),
                  "avg":"0.95",
                  "refs":["Backwardation >1.0 – pánik!","Lapos 0.97–1.0",
                          "Enyhe contango 0.92–0.97","Normál contango 0.85–0.92","Mély contango <0.85 – nyugalom"]}),

        ind(now.get("rsiSignal","wait"), "RSI + Divergencia (SPX)",
            str(now.get("rsiSPX","?")), now.get("rsiDesc","?"), weight=4, imp="FONTOS",
            cat5={"cur":rsi_cat,"avg":"50",
                  "refs":["Túlvett + bearish div. (>80)","Magas (70–80)","Normál (40–70)",
                          "Alacsony (30–40)","Túladott + bullish div. (<30)"]}),

        ind("wait" if now.get("bbSqueeze") else "neutral", "Bollinger Squeeze (SPX)",
            f"{now.get('bbWidth','?')}%", now.get("bbDesc","?"), weight=3, imp="KIEGÉSZÍTŐ",
            cat5={"cur":(5 if now.get("bbSqueeze") else 3),"avg":"~15%",
                  "refs":["Nagyon széles (>25%)","Széles (20–25%)","Normál (12–20%)",
                          "Szűk (8–12%)","SQUEEZE (<8%) – nagy mozgás közeleg"]}),

        ind("go" if vix<18 else "bear" if vix>25 else "wait", "VIX",
            str(vix), f"Heti: {base.get('vixTrend',0):+.1f}", weight=3, imp="KIEGÉSZÍTŐ",
            cat5={"cur":vix_cat,"avg":"20",
                  "refs":["Pánik (>28)","Félelem (22–28)","Normál (18–22)",
                          "Nyugodt (14–18)","Nagyon alacsony (<14) – önelégültség"]}),
    ])

    br=mid.get("breadth",50)
    ism=mid.get("ismNewOrders",50)
    hy=base.get("hySpread",3.5)
    pe=base.get("forwardPE",20)

    ism_cat  = (5 if ism>58 else 4 if ism>53 else 3 if ism>48 else 2 if ism>44 else 1)
    cg_cat   = (5 if mid.get("cgTrend")=="bull" else 2 if mid.get("cgTrend")=="bear" else 3)
    br_cat   = (5 if br>70 else 4 if br>60 else 3 if br>45 else 2 if br>35 else 1)
    pe_cat   = (5 if pe<17 else 4 if pe<20 else 3 if pe<22 else 2 if pe<25 else 1)
    hy_cat   = (5 if hy<2.5 else 4 if hy<3.2 else 3 if hy<3.8 else 2 if hy<4.5 else 1)
    cross_cat= (5 if mid.get("crossSignal")=="bull" else 2)

    # Fontossági sorrend: ISM(8) > Copper/Gold(7) > Breadth(5) > Cross(5) > P/E(4) > HY(4)
    mid_html="".join([
        ind(mid.get("ismSignal","wait"), "ISM New Orders",
            str(ism), mid.get("ismDesc","?"), weight=8, imp="KRITIKUS",
            cat5={"cur":ism_cat,"avg":"50",
                  "refs":["Mély zsugorodás (<44)","Zsugorodás (44–48)",
                          "Semleges (48–53)","Bővülés (53–58)","Erős bővülés (>58)"]}),

        ind(mid.get("cgTrend","wait"), "Copper / Gold arány",
            str(mid.get("cgRatio","?")), mid.get("cgDesc","?"), weight=7, imp="FONTOS",
            cat5={"cur":cg_cat,"avg":"~0.00130",
                  "refs":["Esik, tartósan alacsony","Csökkenő","Stabil","Enyhe emelkedés","Erősen emelkedő – bővülési jel"]}),

        ind("go" if br>65 else "bear" if br<40 else "wait",
            "Piaci Breadth (% > MA50)", f"{br}%",
            "Széles rally" if br>65 else "Szűkülő – figyelem" if br<40 else "Vegyes",
            weight=5, imp="FONTOS",
            cat5={"cur":br_cat,"avg":"55%",
                  "refs":["Nagyon szűk (<35%) – csak néhány vezet","Szűk (35–45%)",
                          "Közepes (45–60%)","Széles (60–70%)","Nagyon széles (>70%) – egészséges rally"]}),

        ind(mid.get("crossSignal","wait"), "Golden / Death Cross",
            "MA50 vs MA200", mid.get("crossDesc","?"), weight=5, imp="FONTOS",
            cat5={"cur":cross_cat,"avg":"–",
                  "refs":["Death Cross, eső","Death Cross, stabil","Közel egymáshoz",
                          "Golden Cross, lassul","Golden Cross, gyorsuló"]}),

        ind("go" if pe<18 else "bear" if pe>24 else "wait",
            "Forward P/E", f"{pe}x", base.get("valLabel","?"), weight=4, imp="FONTOS",
            cat5={"cur":pe_cat,"avg":"18–19x (historikus)",
                  "refs":["Nagyon drága (>25x)","Drága (22–25x)","Fair (19–22x)",
                          "Kedvező (17–19x)","Olcsó (<17x)"]}),

        ind("go" if hy<3.5 else "bear" if hy>4.5 else "wait",
            "HY Credit Spread", f"{hy}%",
            "Szűk – nincs stresszjel" if hy<3.5 else "Krízis jel!" if hy>4.5 else "Emelkedő – figyelj",
            weight=4, imp="FONTOS",
            cat5={"cur":hy_cat,"avg":"~3.5%",
                  "refs":["Krízis (>5%)","Stressz (4.5–5%)","Normál (3.2–4.5%)",
                          "Alacsony (2.5–3.2%)","Nagyon szűk (<2.5%) – optimizmus"]}),
    ])

    lei_cat = (5 if lng.get("leiSignal")=="bull" and lng.get("leiChg3",0)>0.3
               else 4 if lng.get("leiSignal")=="bull"
               else 2 if lng.get("leiSignal")=="bear" else 3)
    m2_cat  = (5 if lng.get("m2Yoy",0)>7 else 4 if lng.get("m2Yoy",0)>4
               else 3 if lng.get("m2Yoy",0)>1 else 2 if lng.get("m2Yoy",0)>-1 else 1)
    rec_cat = (5 if isinstance(rec,(int,float)) and rec<3
               else 4 if isinstance(rec,(int,float)) and rec<8
               else 3 if isinstance(rec,(int,float)) and rec<15
               else 2 if isinstance(rec,(int,float)) and rec<25 else 1)
    umi_cat = (1 if lng.get("umiCur",70)<55 else 2 if lng.get("umiCur",70)<65
               else 3 if lng.get("umiCur",70)<80 else 4 if lng.get("umiCur",70)<90 else 5)
    yld_cat = (1 if yld<-25 else 2 if yld<0 else 3 if yld<25 else 4 if yld<75 else 5)

    # Fontossági sorrend: LEI(9) > M2(7) > Rec.prob(6) > UMich(5) > Hozamgörbe(4)
    lng_html="".join([
        ind(lng.get("leiSignal","wait"), "Conference Board LEI",
            str(lng.get("leiCur","?")), lng.get("leiDesc","?"), weight=9, imp="KRITIKUS",
            cat5={"cur":lei_cat,"avg":"~100",
                  "refs":["Csökkenő, meredeken – recesszió előtt","Csökkenő, lassul",
                          "Stabil","Emelkedő, lassan","Emelkedő, gyorsuló – bővülés jön"]}),

        ind(lng.get("m2Signal","wait"), "M2 Pénzkínálat YoY",
            f"{lng.get('m2Yoy','?')}%", lng.get("m2Desc","?"), weight=7, imp="KRITIKUS",
            cat5={"cur":m2_cat,"avg":"+4–5% (normál)",
                  "refs":["Zsugorodó (<-1%)","Stagnáló (-1–1%)","Mérsékelt (1–4%)",
                          "Normál (4–7%)","Bőséges (>7%) – inflációs kockázat"]}),

        ind("go" if isinstance(rec,(int,float)) and rec<5 else
            "bear" if isinstance(rec,(int,float)) and rec>20 else "wait",
            "Recessziós val. (NY Fed)", f"{rec}%",
            "Alacsony" if isinstance(rec,(int,float)) and rec<5 else
            "MAGAS!" if isinstance(rec,(int,float)) and rec>20 else "Közepes",
            weight=6, imp="KRITIKUS",
            cat5={"cur":rec_cat,"avg":"~5%",
                  "refs":["Magas kockázat (>25%)","Emelkedett (15–25%)","Mérsékelt (8–15%)",
                          "Alacsony (3–8%)","Nagyon alacsony (<3%)"]}),

        ind(lng.get("umiSignal","wait"), "Consumer Expectations (UMich)",
            str(lng.get("umiCur","?")), lng.get("umiDesc","?"), weight=5, imp="FONTOS",
            cat5={"cur":umi_cat,"avg":"~75",
                  "refs":["Nagyon gyenge (<55) – bizalomvesztés","Gyenge (55–65)",
                          "Normál (65–80)","Erős (80–90)","Nagyon erős (>90)"]}),

        ind(lng.get("yieldSignal","wait"),
            "Hozamgörbe 10Y–2Y + De-inversion sebesség",
            f"{'+' if yld>0 else ''}{yld} bp",
            lng.get("yieldDesc","?") + (f" | Re-steep: {lng.get('yieldSpeed3m',0):+.0f}bp/3hó" if lng.get('yieldSpeed3m') else ""),
            weight=5, imp="KRITIKUS" if lng.get("yieldDangerous") else "FONTOS",
            cat5={"cur":yld_cat,"avg":"+50–100 bp (normál)",
                  "refs":["Mélyen invertált (<-25bp)","Enyhe inverzió (-25–0bp)",
                          "Lapos (0–25bp)","Normál (25–75bp)",
                          "Meredek (>75bp) – bővülés"]}),
    ])

    # Kilépési triggerek
    triggers = [
        ("VIX term backwardation",  now.get("termSignal","wait")=="bear"),
        ("LEI csökkenő",            lng.get("leiSignal","wait")=="bear"),
        ("Bearish RSI div.",        now.get("rsiDiv","none")=="bear"),
        ("AF sárgáról lilára",      now.get("afTurningBear", False)),
        ("HY spread > 4.5%",        hy > 4.5),
        ("Yield de-inversion ⚠",    lng.get("yieldDangerous", False)),
        ("PCR eufória < 0.65",      base.get("pcr", 0.85) < 0.65),
        ("Rec.prob > 20%",          isinstance(rec,(int,float)) and rec > 20),
        ("GEX negatív 🔴",          smart.get("gex", 1) < 0),
        ("VIX spike >20%/nap 🚨",   base.get("vixBlackSwan", False)),
        ("Breadth divergencia",     mid.get("breadthDiv", False)),
        ("Net liq. gyors szűkülés", smart.get("netLiqChg4w", 0) < -500),
    ]
    active_cnt = sum(1 for _,a in triggers if a)
    # 12 trigger van — 4+ aktív = EXIT (arányos maradt)
    exit_threshold = 4
    trig_html  = "".join([
        f'<div class="trig {"on" if a else "off"}">'
        f'<div class="trig-dot {"on" if a else "off"}"></div>'
        f'<div class="trig-txt {"on" if a else "off"}">{n}</div>'
        f'</div>' for n,a in triggers
    ])

    # Playbook szintek
    pb_levels = [
        (85,  100, "#00d488", "MUST BUY",      "70–85%", "must_buy"),
        (65,  84,  "#4da6ff", "ÓVATOS VÉTEL",  "35–55%", "caut_buy"),
        (40,  64,  "#f0a500", "VÁRAKOZÁS",     "10–25%", "wait"),
        (0,   39,  "#f04060", "VÉDEKEZÉS",     "0–10%",  "defense"),
    ]
    pb_html = ""
    for lo, hi, col, lbl, alloc_r, key in pb_levels:
        is_active = (playbook==key) or (playbook=="extreme_fear" and key=="wait" and ef_active)
        active_cls = "pb-active" if is_active else ""
        now_tag    = f'<span class="pb-now-tag">← MOST · {es}</span>' if is_active else ""
        score_range = (f"85–100" if lo==85 else f"65–84" if lo==65
                       else f"40–64" if lo==40 else f"&lt; 40")
        pb_html += (
            f'<div class="pb-row {active_cls}" style="border-left:4px solid {col}40;{"background:"+col+"10;border-color:"+col+"40" if is_active else ""}">'
            f'<div class="pb-score" style="color:{col}">{score_range}</div>'
            f'<div class="pb-body">'
            f'<div class="pb-lbl" style="color:{col}">{lbl} {now_tag}</div>'
            f'<div class="pb-alloc-r">Allokáció: <strong>{alloc_r}</strong></div>'
            f'</div>'
            f'<div class="pb-act" style="color:{col};background:{col}15;border:1px solid {col}30">'
            f'{"CSELEKEDJ" if is_active else "—"}'
            f'</div>'
            f'</div>'
        )

    # HUF szekció
    huf_rate  = huf.get("hufRate", 0)
    huf_trend = huf.get("hufTrend","?")
    huf_1w    = huf.get("hufChg1w",0)
    huf_1m    = huf.get("hufChg1m",0)
    huf_col   = "#00d488" if huf_trend=="erősödő" else "#f04060" if huf_trend=="gyengülő" else "#f0a500"

    # HUF 5 kategória: érdemes-e most dollárt venni?
    # Alacsony HUF/USD szám = erős forint = olcsóbb USD vásárlás
    if huf_rate > 0:
        if huf_rate < 280:   huf_cat = 1  # nagyon erős forint – most drágán vennél dollárt
        elif huf_rate < 300: huf_cat = 2  # erős forint
        elif huf_rate < 320: huf_cat = 3  # közepes
        elif huf_rate < 350: huf_cat = 4  # gyenge forint – olcsó USD
        else:               huf_cat = 5  # nagyon gyenge forint – jó USD vétel ár
    else:
        huf_cat = 3

    huf_buy_signal = ("✓ ÉRDEMES dollárt venni" if huf_rate > 330
                      else "⚠ Kivárhatod" if huf_rate > 310
                      else "✗ Drága most – várj gyengébb forintra")
    huf_buy_col = ("#00d488" if huf_rate > 330 else "#f0a500" if huf_rate > 310 else "#f04060")

    # HUF 5 kategória boxok generálása
    cat_colors = ["#f04060","#f08040","#f0a500","#7dd3fc","#00d488"]
    huf_cat_boxes = ""
    for i in range(1, 6):
        cc = cat_colors[i-1]
        if i == huf_cat:
            huf_cat_boxes += f'<div class="c5-box" style="background:{cc};color:#000">{i}</div>'
        else:
            huf_cat_boxes += f'<div class="c5-box" style="border:1px solid {cc}40;color:{cc}">{i}</div>'

    huf_html = (
        f'<div class="huf-box">'
        f'<div class="huf-rate">{huf_rate} <span class="huf-unit">Ft/USD</span></div>'
        f'<div class="huf-trend" style="color:{huf_col}">{huf_trend} '
        f'(1h: {huf_1w:+.2f}% · 1hó: {huf_1m:+.1f}%)</div>'
        f'<div class="c5-wrap" style="margin-top:8px">'
        f'{huf_cat_boxes}'
        f'<span class="c5-avg">USD vétel ár</span></div>'
        f'<div style="font-size:10px;font-family:var(--mono);margin-top:6px;color:{huf_buy_col}">'
        f'{huf_buy_signal}</div>'
        f'<div style="font-size:9px;color:var(--mut);margin-top:4px">'
        f'1=Nagyon erős Ft (drága USD) · 5=Gyenge Ft (olcsó USD)</div>'
        f'</div>'
    )

    # Rubber Band HTML
    rb_stretch  = base.get("rbStretch", 0)
    rb_sma40    = base.get("sma40Monthly", 0)
    rb_target   = base.get("rbTarget", 0)
    rb_overshoot= base.get("rbOvershoot", 0)
    rb_cat      = base.get("rbCat", "?")
    rb_col      = ("#f04060" if rb_stretch > 30 else "#f0a500" if rb_stretch > 20
                   else "#f0c040" if rb_stretch > 10 else "#00d488" if rb_stretch < 0 else "#5b9cf0")
    rb_needle   = min(95, max(5, round(50 + rb_stretch * 1.2)))  # 0-100% pozíció a gauge-en
    af_txt      = ("🟢 Sárga – gumi tart" if now.get("afSignal")=="bull"
                   else "🔴 Lila fordulat – TRIGGER!" if now.get("afTurningBear")
                   else "🟡 Semleges")
    spx_price   = base.get("spx", 0)
    if rb_target > 0 and spx_price > 0:
        downside_target   = round((rb_target - spx_price) / spx_price * 100, 1)
        downside_overshoot= round((rb_overshoot - spx_price) / spx_price * 100, 1)
    else:
        downside_target = downside_overshoot = 0

    rb_html = (
        f'<div class="rb-stretch-bar" style="margin-bottom:10px">'
        f'<div style="height:10px;border-radius:5px;overflow:hidden;background:linear-gradient(to right,#1d9e75,#5dcaa5,#f0c040,#ef9f27,#e24b4a,#8b0000)">'
        f'<div style="position:relative">'
        f'<div style="position:absolute;left:{rb_needle}%;top:-3px;width:4px;height:16px;background:white;border-radius:2px;transform:translateX(-50%);box-shadow:0 0 6px #0006"></div>'
        f'</div></div>'
        f'<div style="display:flex;justify-content:space-between;font-size:8px;color:var(--mut);font-family:var(--mono);margin-top:4px">'
        f'<span>Mély diszkont (&lt;-15%)</span><span>Trendnél (0%)</span>'
        f'<span style="color:{rb_col};font-weight:700">{rb_stretch:+.1f}% MOST</span>'
        f'<span>Extrém (+40%+)</span></div></div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px">'
        f'<div class="huf-box"><div class="huf-rate" style="font-size:16px">{spx_price:,}</div>'
        f'<div style="font-size:9px;color:var(--mut)">SPX most</div></div>'
        f'<div class="huf-box"><div class="huf-rate" style="font-size:16px;color:#5b9cf0">{rb_sma40:,.0f}</div>'
        f'<div style="font-size:9px;color:var(--mut)">SMA40 havi trend</div></div>'
        f'<div class="huf-box"><div class="huf-rate" style="font-size:16px;color:{rb_col}">{rb_stretch:+.1f}%</div>'
        f'<div style="font-size:9px;color:var(--mut)">Feszítés ({rb_cat})</div></div>'
        f'</div>'
        f'<div style="padding:8px 11px;background:var(--c2);border-radius:8px;border:1px solid var(--brd);margin-bottom:8px">'
        f'<div style="font-size:9px;color:var(--mut);margin-bottom:5px;font-family:var(--mono)">AF(18,6) szignál: {af_txt}</div>'
        f'<div style="font-size:10px;color:var(--sub);line-height:1.5">'
        f'Ha AF negatívba fordul → SMA40 visszatérés: <span style="color:#f04060;font-family:var(--mono)">'
        f'{rb_target:,} ({downside_target:+.1f}%)</span> · '
        f'Historikus túllövés: <span style="color:#f04060;font-family:var(--mono)">'
        f'{rb_overshoot:,} ({downside_overshoot:+.1f}%)</span></div></div>'
        f'<div style="font-size:9px;color:var(--mut);font-family:var(--mono)">⚡ Penalty a score-ban: '
        f'<strong style="color:{rb_col}">{base.get("rbPenalty",0):+d} pont</strong> '
        f'(csak ha AF is negatív)</div>'
    )

    # Szektorrotáció
    sec_list = sectors.get("sectors",[])
    sec_html = ""
    if sec_list:
        for s in sec_list[:8]:
            p1m = s.get("perf1m",0)
            col = "#00d488" if p1m>2 else "#f04060" if p1m<-2 else "#f0a500"
            bar_w = min(100, abs(p1m)*5)
            bar_dir = "right" if p1m>=0 else "left"
            sec_html += (
                f'<div class="sec-row">'
                f'<div class="sec-name">{s["name"]}</div>'
                f'<div class="sec-bar-wrap">'
                f'<div class="sec-bar" style="width:{bar_w}%;background:{col};float:{bar_dir}"></div>'
                f'</div>'
                f'<div class="sec-val" style="color:{col}">{p1m:+.1f}%</div>'
                f'</div>'
            )

    # Riasztások
    alert_cards = ""
    for a in alerts[:8]:
        t = a["type"]
        col   = {"drop":"#f04060","trap":"#a78bfa","watch":"#f0a500"}.get(t,"#f0a500")
        badge = {"drop":"NAGY ESÉS","trap":"ZUHANÓ KÉS","watch":"FIGYELŐ"}.get(t,"?")
        val_str = f'{a["value"]:+.1f}%'
        alert_cards += (
            f'<div class="al-card" style="border-color:{col}25">'
            f'<div class="al-left"><div class="al-ticker" style="color:{col}">{a["ticker"]}</div>'
            f'<div class="al-type" style="color:{col};background:{col}15">{badge}</div></div>'
            f'<div class="al-info"><div class="al-desc">{a["desc"]}</div></div>'
            f'<div class="al-right"><div class="al-val" style="color:{col}">{val_str}</div>'
            f'<div class="al-date">{a.get("date","")[-5:]}</div></div>'
            f'</div>'
        )
    if not alerts:
        alert_cards = '<div class="al-empty">✓ Nincs aktív riasztás</div>'

    # Watchlist kártyák (screener)
    sc_stocks = screener_data.get("stocks",[])
    sc_updated = screener_data.get("updated","")[:10] if screener_data.get("updated") else "–"
    sc_html = ""
    for s in sc_stocks[:16]:
        sc     = s.get("qualityScore",50)
        vma    = s.get("vsMA200",0)
        roe    = s.get("roe",0)
        price  = s.get("price",0)
        pt     = s.get("analystPriceTarget")   # elemzői célár
        eps_rev= s.get("epsRevPositive")
        eps_g  = s.get("analystEpsGrowth")
        # Egységes színrendszer fontossági sorrendben
        sc_c   = "#00d488" if sc>=75 else "#f0a500" if sc>=60 else "#4da6ff"
        roe_c  = "#00d488" if roe>=30 else "#7dd3fc" if roe>=20 else "#f0a500"
        # Elemzői célár upside
        upside_html = ""
        if pt and price > 0:
            upside = (pt - price) / price * 100
            up_c   = "#00d488" if upside>15 else "#f0a500" if upside>5 else "#f04060"
            upside_html = f'<div class="wl-row"><span>1Y cél</span><span style="color:{up_c}">${pt:.0f} ({upside:+.0f}%)</span></div>'
        # Earnings Scout badge
        es_badge = ""
        if eps_rev is True:
            es_badge = '<div class="wl-es-bull">📈 EPS revízió ↑</div>'
        elif eps_rev is False:
            es_badge = '<div class="wl-es-bear">📉 EPS revízió ↓</div>'
        # EPS growth
        eps_g_html = ""
        if eps_g is not None:
            eg_c = "#00d488" if eps_g>=10 else "#f0a500" if eps_g>=0 else "#f04060"
            eps_g_html = f'<div class="wl-row"><span>EPS növ.</span><span style="color:{eg_c}">{eps_g:+.1f}%</span></div>'

        sc_html += (
            f'<div class="wl-card" style="border-top:2px solid {sc_c}">'
            f'<div class="wl-top"><span class="wl-tk">{s["ticker"]}</span>'
            f'<span class="wl-sc" style="color:{sc_c}">{sc}</span></div>'
            f'<div class="wl-nm">{s.get("name","")[:22]}</div>'
            f'<div class="wl-row"><span>Ár</span><span style="color:var(--text);font-weight:600">${price:,.2f}</span></div>'
            f'<div class="wl-row"><span>MA200</span><span style="color:#f0a500">{vma:+.1f}%</span></div>'
            f'<div class="wl-row"><span>ROE</span><span style="color:{roe_c}">{roe:.0f}%</span></div>'
            f'<div class="wl-row"><span>D/E</span><span>{s.get("de",0):.2f}</span></div>'
            f'{upside_html}'
            f'{eps_g_html}'
            f'{es_badge}'
            f'<div class="wl-bar"><div class="wl-fill" style="width:{sc}%;background:{sc_c}"></div></div>'
            f'</div>'
        )
    if not sc_html:
        sc_html = '<div class="wl-empty">Screener még nem futott – pénteken frissül</div>'

    # History chart adatok
    hd=json.dumps([h["date"] for h in history[-24:]])
    hs=json.dumps([h.get("entryScore",50) for h in history[-24:]])
    hc=json.dumps([h.get("corrProb",20) for h in history[-24:]])

    # Regime label
    regime_map={"bull":"Bikapiaci","neutral":"Semleges","fear":"Félelmi",
                "extreme_fear":"EXTREME FEAR","recession_watch":"Recesszió Figyelő",
                "slowdown":"Lassulás jel"}
    rl = regime_map.get(regime, regime)

    # Smart Money HTML
    def sm_row(sig, name, val, desc, badge_extra=""):
        col = {"bull":"#00d488","wait":"#f0a500","bear":"#f04060"}.get(sig,"#f0a500")
        bt  = {"bull":"BULL","wait":"NEU","bear":"BEAR"}.get(sig,"NEU")
        return (f'<div class="sm-row">' +
                f'<div class="sm-left"><div class="sm-name">{name}</div>' +
                f'<div class="sm-val" style="color:{col}">{val}</div>' +
                f'<div class="sm-desc">{desc}{badge_extra}</div></div>' +
                f'<div class="sm-badge" style="color:{col};background:{col}18;border:1px solid {col}30">{bt}</div>' +
                f'</div>')

    mc_sum    = smart.get("mcSum", 0)
    mc_sig    = smart.get("mcSignal","wait")
    mc_zcd    = smart.get("mcZeroCrossDown", False)
    mc_zcu    = smart.get("mcZeroCrossUp", False)
    mc_badge  = (" <b style='color:#f04060'>⚠ NULLA ALÁ BUKOTT</b>" if mc_zcd
                 else " <b style='color:#00d488'>✓ NULLA FÖLÉ</b>" if mc_zcu else "")

    net_liq   = smart.get("netLiq", 0)
    liq_chg   = smart.get("netLiqChg4w", 0)
    liq_sig   = smart.get("liqSignal","wait")

    dix_v2    = smart.get("dix", 43.0)
    gex_v     = smart.get("gex", 0)
    dix_sig   = smart.get("dixSignal","wait")
    gex_sig   = smart.get("gexSignal","wait")
    dix_fall  = smart.get("dixFalling", False)
    dix_badge = (" <b style='color:#f04060'>↓ CSÖKKEN – disztribúció!</b>" if dix_fall else "")
    gex_col   = "#f04060" if gex_v < 0 else "#00d488"

    cot_net   = smart.get("cotNet", 0)
    cot_z     = smart.get("cotZScore", 0.0)
    cot_sig   = smart.get("cotSignal","wait")

    smart_html = "".join([
        sm_row(liq_sig, "Globális Likviditás (Fed – TGA)",
               f"${net_liq:,.0f}B nettó",
               smart.get("liqDesc","?") + f" | Változás: {liq_chg:+.0f}B$ / 4hét"),
        sm_row(mc_sig, "McClellan Summation Index",
               f"{mc_sum:+,.0f}",
               smart.get("mcDesc","?"), mc_badge),
        sm_row(dix_sig, "DIX – Dark Pool Index",
               f"{dix_v2:.1f}%",
               smart.get("dixDesc","?") + dix_badge),
        sm_row(gex_sig, "GEX – Gamma Exposure",
               f"${gex_v/1e9:.1f}B",
               smart.get("gexDesc","?")),
        sm_row(cot_sig, "COT Smart Money (Large Speculators)",
               f"{cot_net:+,.0f} kontraktus",
               smart.get("cotDesc","?") + f" | Z-score: {cot_z:+.1f}"),
    ])

        # ── HTML ──────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="hu"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Befektetoi Dashboard v5 – {today}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');
:root{{
  --bg:#0b1525;--bg2:#0e1b30;--card:#121f38;--c2:#172542;
  --brd:#1d2f4a;--brd2:#243860;
  --text:#dce8f5;--sub:#8aabcc;--mut:#4e6f8f;
  --bull:#00d488;--bear:#f04060;--neu:#f0a500;--info:#4da6ff;--vio:#a78bfa;
  --mono:'JetBrains Mono',monospace;--sans:'Inter',sans-serif;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;padding:14px;min-height:100vh}}

/* STATUS BAR */
.sbar{{display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
.sp{{font-family:var(--mono);font-size:9.5px;padding:3px 10px;border-radius:99px;border:1px solid}}
.sp-ok{{color:var(--bull);border-color:#00d48830;background:#00d48810}}
.sp-warn{{color:var(--neu);border-color:#f0a50030;background:#f0a50010}}
.sp-info{{color:var(--sub);border-color:var(--brd);background:var(--card)}}
.sbar-r{{margin-left:auto;font-size:10px;color:var(--mut);font-family:var(--mono)}}

/* FŐ SCORE */
.main{{background:linear-gradient(135deg,var(--card) 0%,var(--c2) 100%);border:1px solid var(--brd2);border-radius:14px;padding:18px;margin-bottom:12px;display:grid;grid-template-columns:1fr auto 1fr;gap:16px;align-items:center}}
.m-left .m-signal{{font-size:16px;font-weight:700;color:var(--text);margin-bottom:6px}}
.m-left .m-desc{{font-size:11px;color:var(--sub);line-height:1.5}}
.m-center{{text-align:center}}
.m-num{{font-family:var(--mono);font-size:58px;font-weight:700;line-height:1;color:{pb_col}}}
.m-lbl{{font-size:9.5px;color:var(--mut);margin-top:3px;font-family:var(--mono)}}
.m-prog{{height:5px;background:var(--brd2);border-radius:3px;overflow:hidden;margin-top:8px}}
.m-prog-fill{{height:100%;border-radius:3px;background:linear-gradient(90deg,{pb_col},{pb_col}aa)}}
.m-right{{text-align:right}}
.m-alloc-lbl{{font-size:10px;color:var(--mut);margin-bottom:4px}}
.m-alloc-num{{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--text)}}
.m-corr{{margin-top:8px;padding:5px 10px;background:#f0406010;border:1px solid #f0406025;border-radius:7px;font-size:10px;color:#f08090;font-family:var(--mono)}}
.m-tags{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px;justify-content:flex-start}}
.m-tag{{font-size:9px;font-family:var(--mono);padding:2px 8px;border-radius:99px;border:1px solid}}

/* SECTION */
.sec{{background:var(--card);border:1px solid var(--brd);border-radius:12px;padding:15px;margin-bottom:12px}}
.sec-title{{font-size:10px;font-family:var(--mono);color:var(--mut);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.sec-badge{{font-size:9px;padding:2px 8px;border-radius:99px;border:1px solid;font-weight:400}}

/* PLAYBOOK */
.pb-row{{display:grid;grid-template-columns:70px 1fr auto;align-items:center;gap:10px;padding:9px 11px;border-radius:9px;background:var(--c2);border:1px solid var(--brd);margin-bottom:6px;transition:.15s}}
.pb-active{{box-shadow:0 0 0 1px #f0a50040}}
.pb-score{{font-family:var(--mono);font-size:12px;font-weight:700}}
.pb-lbl{{font-size:11px;font-weight:600;margin-bottom:2px}}
.pb-alloc-r{{font-size:10px;color:var(--mut);font-family:var(--mono)}}
.pb-now-tag{{font-size:9px;font-family:var(--mono);color:#f0a500;background:#f0a50015;padding:1px 6px;border-radius:4px;margin-left:6px;border:1px solid #f0a50030}}
.pb-act{{font-size:10px;font-family:var(--mono);font-weight:700;padding:4px 10px;border-radius:6px;white-space:nowrap}}

/* EXTREME FEAR MODIFIER */
.ef-box{{background:#a78bfa0e;border:1px solid #a78bfa25;border-radius:9px;padding:10px 13px;margin-bottom:10px;font-size:10px;color:var(--sub);display:flex;gap:8px;align-items:flex-start}}
.ef-icon{{font-size:16px;flex-shrink:0}}
.ef-txt strong{{color:#a78bfa}}

/* TRIGGEREK */
.trig-wrap{{padding:10px 12px;background:var(--c2);border-radius:9px;border:1px solid var(--brd);margin-top:8px}}
.trig-title{{font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:7px;letter-spacing:1px}}
.trig-grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}
.trig{{display:flex;align-items:center;gap:7px;padding:6px 9px;border-radius:7px;border:1px solid}}
.trig.on{{border-color:#f0406040;background:#f0406010}}
.trig.off{{border-color:var(--brd);background:transparent}}
.trig-dot{{width:7px;height:7px;border-radius:50%;flex-shrink:0}}
.trig-dot.on{{background:#f04060;box-shadow:0 0 5px #f04060}}
.trig-dot.off{{background:var(--mut)}}
.trig-txt{{font-size:9.5px;font-family:var(--mono)}}
.trig-txt.on{{color:#f08090}}
.trig-txt.off{{color:var(--mut)}}
.trig-status{{font-size:10px;color:var(--mut);margin-top:7px;font-family:var(--mono)}}

/* INDIKÁTOROK */
.ind{{display:grid;grid-template-columns:1fr 80px 60px;align-items:center;gap:10px;padding:9px 11px;border-radius:9px;background:var(--c2);border:1px solid var(--brd);margin-bottom:6px}}
.ind-left{{}}
.ind-name{{font-size:11px;color:var(--text);font-weight:500;margin-bottom:2px;display:flex;align-items:center;flex-wrap:wrap;gap:4px}}
.ind-val{{font-size:13px;font-family:var(--mono);font-weight:700}}
.ind-sub{{font-size:9px;color:var(--mut);margin-top:2px}}
.imp-pill{{font-size:8px;padding:1px 5px;border-radius:3px;font-weight:500}}
.ind-weight{{display:flex;flex-direction:column;align-items:flex-end;gap:3px}}
.wt-label{{font-size:8px;color:var(--mut);font-family:var(--mono)}}
.wt-bar{{width:60px;height:4px;background:var(--brd2);border-radius:2px;overflow:hidden}}
.wt-fill{{height:100%;border-radius:2px}}
.sig-badge{{font-size:9px;font-family:var(--mono);font-weight:700;padding:3px 8px;border-radius:5px;text-align:center}}
.ind-horizon{{font-size:10px;font-family:var(--mono);padding:3px 9px;border-radius:6px;background:#4da6ff12;border:1px solid #4da6ff25;color:var(--info)}}

/* HUF */
.huf-box{{padding:10px 14px;background:var(--c2);border:1px solid var(--brd);border-radius:9px}}
.huf-rate{{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--text)}}
.huf-unit{{font-size:12px;color:var(--mut);font-weight:400}}
.huf-trend{{font-size:11px;margin-top:3px;font-family:var(--mono)}}

/* SZEKTORROTÁCIÓ */
.sec-row{{display:grid;grid-template-columns:70px 1fr 45px;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--brd)}}
.sec-row:last-child{{border-bottom:none}}
.sec-name{{font-size:10px;color:var(--sub);font-family:var(--mono)}}
.sec-bar-wrap{{height:6px;background:var(--brd2);border-radius:3px;overflow:hidden}}
.sec-bar{{height:100%;border-radius:3px}}
.sec-val{{font-family:var(--mono);font-size:10px;text-align:right;font-weight:700}}

/* SMART MONEY */
.sm-row{{display:grid;grid-template-columns:1fr auto;align-items:center;gap:10px;padding:9px 11px;border-radius:9px;background:var(--c2);border:1px solid var(--brd);margin-bottom:6px}}
.sm-left{{}}
.sm-name{{font-size:10px;color:var(--sub);font-weight:600;margin-bottom:2px}}
.sm-val{{font-size:14px;font-family:var(--mono);font-weight:700;margin-bottom:2px}}
.sm-desc{{font-size:9.5px;color:var(--mut);line-height:1.4}}
.sm-badge{{font-size:9px;font-family:var(--mono);font-weight:700;padding:4px 10px;border-radius:5px;white-space:nowrap}}

/* RIASZTÁSOK */
.al-card{{display:grid;grid-template-columns:60px 1fr auto;align-items:center;gap:10px;padding:9px 11px;border-radius:9px;background:var(--c2);border:1px solid;margin-bottom:6px}}
.al-left{{text-align:center}}
.al-ticker{{font-family:var(--mono);font-size:13px;font-weight:700}}
.al-type{{font-size:8px;font-family:var(--mono);padding:2px 5px;border-radius:3px;margin-top:3px;display:inline-block}}
.al-desc{{font-size:10px;color:var(--sub)}}
.al-val{{font-family:var(--mono);font-size:13px;font-weight:700;text-align:right}}
.al-date{{font-size:9px;color:var(--mut);text-align:right;margin-top:2px}}
.al-empty{{font-size:10px;color:var(--mut);font-family:var(--mono);padding:12px;text-align:center;background:var(--c2);border-radius:7px}}

/* WATCHLIST */
.wl-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:8px}}
.wl-card{{background:var(--c2);border:1px solid var(--brd);border-radius:9px;padding:10px;border-top:2px solid var(--bull)}}
.wl-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px}}
.wl-tk{{font-family:var(--mono);font-size:13px;font-weight:700}}
.wl-sc{{font-family:var(--mono);font-size:12px;font-weight:700}}
.wl-nm{{font-size:9px;color:var(--mut);margin-bottom:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.wl-row{{display:flex;justify-content:space-between;font-size:9.5px;font-family:var(--mono);margin-bottom:2px;color:var(--mut)}}
.wl-bar{{height:3px;background:var(--brd);border-radius:2px;margin-top:6px;overflow:hidden}}
.wl-fill{{height:100%;border-radius:2px}}
.wl-empty{{font-size:10px;color:var(--mut);font-family:var(--mono);padding:14px;text-align:center;background:var(--c2);border-radius:7px}}
.wl-pills{{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}}
.wl-pill{{font-size:9px;font-family:var(--mono);padding:2px 8px;border-radius:99px;border:1px solid var(--brd);background:var(--c2);color:var(--mut)}}
.wl-pill-disc{{color:var(--neu);border-color:#f0a50030;background:#f0a50010}}
.wl-es-bull{{font-size:8.5px;font-family:var(--mono);color:#00d488;background:#00d48812;padding:2px 6px;border-radius:4px;margin-top:4px;display:inline-block}}
.wl-es-bear{{font-size:8.5px;font-family:var(--mono);color:#f04060;background:#f0406012;padding:2px 6px;border-radius:4px;margin-top:4px;display:inline-block}}

/* 5 KATEGÓRIA */
.c5-wrap{{display:flex;align-items:center;gap:4px;margin-top:5px;flex-wrap:wrap}}
.c5-box{{width:20px;height:20px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:9px;font-family:var(--mono);font-weight:700;cursor:default;transition:.15s}}
.c5-box:hover{{transform:scale(1.15)}}
.c5-lbl{{font-size:9px;font-family:var(--mono);margin-left:4px}}
.c5-avg{{font-size:8.5px;color:var(--mut);margin-left:6px}}

/* SCORE TOOLTIP */
.wl-score-info{{font-size:9px;color:var(--mut);padding:6px 10px;background:var(--c2);border-radius:6px;margin-bottom:10px;border-left:2px solid #00d48840;line-height:1.5}}

/* CHART */
.chart-box{{padding:14px;background:var(--c2);border-radius:9px;margin-bottom:8px}}

/* BACKTEST */
.bt-box{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:9px 12px;background:var(--c2);border-radius:8px;font-size:11px;font-family:var(--mono)}}
.bt-l{{color:var(--mut)}}
.bt-s{{color:var(--mut)}}
.bt-n{{color:var(--mut)}}

/* FOOTER */
.footer{{font-size:9px;color:var(--mut);font-family:var(--mono);display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;padding-top:10px;border-top:1px solid var(--brd)}}
</style></head>
<body>

<!-- STATUS BAR -->
<div class="sbar">
  <span class="sp {"sp-ok" if not errors else "sp-warn"}">⬤ {st_txt} · {src_count}/14 forrás</span>
  <span class="sp sp-info">Frissítve: {today}</span>
  <span class="sp sp-info">Rezsim: {rl}</span>
  {"<span class='sp' style='color:#f0a500;border-color:#f0a50030;background:#f0a50010'>"+season.get('seasonLabel','')+"</span>" if season else ""}
  <div class="sbar-r">Következő: péntek, {next_fri} · 20:00</div>
</div>

<!-- FŐ SCORE -->
<div class="main">
  <div class="m-left">
    <div class="m-signal">{sig_title}</div>
    <div class="m-desc">{sig_desc}</div>
    <div class="m-tags">
      {"<span class='m-tag' style='color:#a78bfa;border-color:#a78bfa40;background:#a78bfa15'>😱 EXTREME FEAR AKTÍV – +1 kategória</span>" if ef_active else ""}
      <span class="m-tag" style="color:var(--sub);border-color:var(--brd)">SPX: {base.get('spx',0):,} ({base.get('spxChg',0):+.2f}%)</span>
      <span class="m-tag" style="color:var(--sub);border-color:var(--brd)">VIX: {vix}</span>
    </div>
  </div>
  <div class="m-center">
    <div class="m-num">{es}</div>
    <div class="m-lbl">KOMPOZIT BELÉPÉSI SCORE / 100</div>
    <div class="m-prog"><div class="m-prog-fill" style="width:{es}%"></div></div>
    {f'<div style="font-size:9px;margin-top:5px;color:#f04060;font-family:var(--mono)">Effektív score: {kelly.get("effectiveScore",es)} (vétó aktív)</div>' if kelly.get("vetoActive") else ''}
  </div>
  <div class="m-right">
    <div class="m-alloc-lbl">Half-Kelly allokáció</div>
    <div class="m-alloc-num">{alloc}%</div>
    <div style="font-size:10px;color:var(--mut);font-family:var(--mono);margin-top:2px">
      Full Kelly: <span style="text-decoration:line-through;color:var(--mut)">{kelly.get('kellyFull', alloc*2)}%</span>
      → Half: <strong style="color:var(--text)">{alloc}%</strong>
    </div>
    <div style="font-size:10px;color:var(--mut);font-family:var(--mono)">{100-alloc}% cash tartalék</div>
    <div style="font-size:9px;color:var(--mut);margin-top:3px">{kelly.get('kellyLabel','')}</div>
    <div class="m-corr">Korrekció esélye: <strong style="color:#f04060">{cp}%</strong></div>
    {f'<div style="font-size:9px;margin-top:5px;padding:4px 8px;background:#f0406015;border:1px solid #f0406030;border-radius:5px;color:#f08090;font-family:var(--mono)">⚡ Vétó: {kelly.get("vetoReasons","")}</div>' if kelly.get("vetoActive") else ''}
  </div>
</div>

<!-- PLAYBOOK -->
<div class="sec">
  <div class="sec-title">
    <span>Befektetői Playbook</span>
    <span class="sec-badge" style="color:{pb_col};border-color:{pb_col}40">Aktív: {playbook.replace("_"," ").upper()}</span>
  </div>
  {pb_html}
  {"<div class='ef-box'><div class='ef-icon'>😱</div><div class='ef-txt'><strong>Extreme Fear módosító aktív</strong> – CNN F&G: "+str(cnn)+" (&lt;25). A score egy kategóriával feljebb lép. Kontrarian vételi lehetőség.</div></div>" if ef_active else ""}
  <div class="trig-wrap">
    <div class="trig-title">KILÉPÉSI TRIGGEREK – 4+ AKTÍV ESETÉN AZONNALI POZÍCIÓ CSÖKKENTÉS</div>
    <div class="trig-grid">{trig_html}</div>
    <div class="trig-status">Aktív triggerek: <strong style="color:{"#f04060" if active_cnt>=4 else "#f0a500" if active_cnt>=2 else "#00d488"}">{active_cnt}</strong> / 12 — {"🔴 EXIT JEL!" if active_cnt>=4 else "⚠ Figyelj" if active_cnt>=2 else "✓ Biztonságos"}</div>
  </div>
</div>

<!-- MOST: 1-4 HÉT -->
<div class="sec">
  <div class="sec-title"><span class="ind-horizon">MOST · 1–4 HÉT</span><span>Technikai + hangulat</span></div>
  {now_html}
</div>

<!-- 3-6 HÓNAP -->
<div class="sec">
  <div class="sec-title"><span class="ind-horizon">3–6 HÓNAP</span><span>Gazdasági ciklus</span></div>
  {mid_html}
</div>

<!-- 6-18 HÓNAP -->
<div class="sec">
  <div class="sec-title"><span class="ind-horizon">6–18 HÓNAP</span><span>Makro előrejelzők</span></div>
  {lng_html}
</div>

<!-- RUBBER BAND INDICATOR -->
<div class="sec" style="margin-bottom:12px;border-color:#f0a50025">
  <div class="sec-title">
    <span>🎯 Rubber Band – SPX vs SMA40 havi</span>
    <span class="sec-badge" style="color:{('#f04060' if base.get('rbStretch',0)>30 else '#f0a500' if base.get('rbStretch',0)>20 else '#00d488')};border-color:{('#f0406030' if base.get('rbStretch',0)>30 else '#f0a50030' if base.get('rbStretch',0)>20 else '#00d48830')}">{base.get('rbCat','?')} · {base.get('rbStretch',0):+.1f}%</span>
  </div>
  {rb_html}
</div>

<!-- HUF/USD + SZEKTORROTÁCIÓ (2 oszlop) -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
  <div class="sec" style="margin-bottom:0">
    <div class="sec-title">HUF/USD árfolyam</div>
    {huf_html}
    <div style="font-size:9px;color:var(--mut);margin-top:8px;font-family:var(--mono)">1 hónapos változás: {huf.get("hufChg1m",0):+.1f}%</div>
  </div>
  <div class="sec" style="margin-bottom:0">
    <div class="sec-title">Szektorrotáció (1 hónapos telj.)</div>
    {sec_html if sec_html else '<div class="al-empty">Nincs szektoradat</div>'}
  </div>
</div>

<!-- CHART -->
<div class="sec">
  <div class="sec-title">Kompozit Score + Korrekciós kockázat historikus</div>
  {bt_html}
  <div class="chart-box">
    <canvas id="histChart" height="160"></canvas>
  </div>
</div>


<!-- SMART MONEY RÉTEG -->
<div class="sec" style="border-color:#a78bfa25">
  <div class="sec-title">
    <span>🧠 Smart Money Réteg</span>
    <span class="sec-badge" style="color:#a78bfa;border-color:#a78bfa30">McClellan · Globális Likviditás · Dark Pool (DIX/GEX) · COT</span>
  </div>
  {smart_html}
</div>

<!-- RIASZTÁSOK -->
<div class="sec" style="border-color:#f0406020">
  <div class="sec-title">
    <span>⚡ Piaci Riasztások</span>
    <span class="sec-badge" style="color:#f04060;border-color:#f0406030">Nagy esések · Zuhanó kések · Figyelők</span>
  </div>
  {alert_cards}
</div>

<!-- WATCHLIST -->
<div class="sec" style="border-color:#00d48820">
  <div class="sec-title">
    <span>🔍 Quality-at-Discount Watchlist</span>
    <span class="sec-badge" style="color:#00d488;border-color:#00d48830">{screener_data.get('count',0)} jelölt · {sc_updated}</span>
  </div>
  <div class="wl-pills">
    <span class="wl-pill">ROE &gt;10%</span>
    <span class="wl-pill">D/E &lt;1.5</span>
    <span class="wl-pill wl-pill-disc">SMA200: -10% – -25%</span>
    <span class="wl-pill">EPS+ outlook</span>
    <span class="wl-pill">MktCap &gt;$5B</span>
  </div>
  <div class="wl-score-info">
    📊 <strong>Quality Score (0–100)</strong> – Összetett mutató: ROE minőség + ROI + SMA200 diszkont mértéke + D/E alacsony értéke + EPS növekedés + Earnings Scout revíziós delta. <strong>75+</strong> = erős jelölt · <strong>60–74</strong> = figyelemre méltó · <strong>&lt;60</strong> = gyengébb pozíció
  </div>
  <div class="wl-grid">{sc_html}</div>
</div>

<!-- FOOTER -->
<div class="footer">
  <span>yfinance · FRED API · CNN F&G · FMP Screener</span>
  <span>GitHub Actions: péntek 20:00</span>
  <span>v5 · 3 idóhorizont · 4-szintű playbook · Kelly · EPS delta</span>
  <span style="margin-left:auto">Nem befektetési tanácsadás</span>
</div>

<script>
const hd={hd}, hs={hs}, hc={hc};
if(hd.length>1){{
  const ctx=document.getElementById("histChart").getContext("2d");
  new Chart(ctx,{{
    type:"line",
    data:{{
      labels:hd,
      datasets:[
        {{label:"Score",data:hs,borderColor:"#00d488",backgroundColor:"#00d48812",
          borderWidth:2,pointBackgroundColor:"#00d488",pointRadius:4,tension:.3}},
        {{label:"Korr.%",data:hc,borderColor:"#a78bfa",backgroundColor:"transparent",
          borderWidth:1.5,borderDash:[4,3],pointRadius:3,pointBackgroundColor:"#a78bfa",tension:.3}},
      ]
    }},
    options:{{
      responsive:true,maintainAspectRatio:false,
      scales:{{
        x:{{ticks:{{color:"#4e6f8f",font:{{size:9}}}},grid:{{color:"#1d2f4a"}}}},
        y:{{min:0,max:100,ticks:{{color:"#4e6f8f",font:{{size:9}}}},grid:{{color:"#1d2f4a"}}}},
      }},
      plugins:{{
        legend:{{labels:{{color:"#8aabcc",font:{{size:10}}}}}},
        annotation:{{annotations:{{
          line65:{{type:"line",yMin:{SCORE_CAUT_BUY},yMax:{SCORE_CAUT_BUY},borderColor:"#4da6ff50",borderWidth:1,borderDash:[4,4]}},
          line85:{{type:"line",yMin:{SCORE_MUST_BUY},yMax:{SCORE_MUST_BUY},borderColor:"#00d48850",borderWidth:1,borderDash:[4,4]}},
        }}}},
      }}
    }}
  }});
}}
</script>
</body></html>"""
    return html

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    print(f"\n{'='*50}")
    print(f"  Befekteto Dashboard v5 – {datetime.date.today()}")
    print(f"{'='*50}\n")

    hist = load_history()
    screener_data = load_screener_data()

    base    = safe(fetch_spx,             {"spx":0,"spxMA200":0,"spxMA50":0,"spxChg":0,"spxAboveMA":0,"spxFromHigh":0,"priceRecovering":True}, "SPX")
    base.update(safe(fetch_vix,           {"vix":18,"vixTrend":0}, "VIX"))
    base.update(safe(fetch_hy_spread,     {"hySpread":3.5}, "HY Spread"))
    base.update(safe(fetch_pcr,           {"pcr":0.85,"pcr20d":0.85,"pcrSignal":"wait","pcrDesc":"Fallback"}, "PCR"))

    spx_p = base.get("spx", 4500)
    base.update(safe(lambda: fetch_forward_pe(spx_p), {"forwardPE":20,"valScore":15,"valLabel":"FAIR"}, "Forward P/E"))

    now  = safe(fetch_ta_spx,             {"termRatio":0.95,"termSignal":"wait","termDesc":"?","macdHist":0,"macdSignal":"wait","macdDesc":"?","bbSqueeze":False,"bbDesc":"?","bbWidth":10,"rsiSPX":50,"rsiSignal":"wait","rsiDesc":"?","rsiDiv":"none","cnnFG":50,"cnnFGRating":"Neutral","afCur":0,"afSignal":"wait","afDesc":"?","afTurningBull":False,"afTurningBear":False}, "TA SPX")
    mid  = safe(fetch_medium_term,        {"cgRatio":0.00007,"cgTrend":"wait","cgDesc":"?","ismNewOrders":50,"ismSignal":"wait","ismDesc":"?","crossSignal":"wait","crossDesc":"?","breadth":50}, "Medium Term")
    lng  = safe(fetch_long_term,          {"leiCur":100,"leiSignal":"wait","leiDesc":"?","leiChg3":0,"m2Yoy":4,"m2Signal":"wait","m2Desc":"?","umiCur":70,"umiSignal":"wait","umiDesc":"?","yieldCurve":20,"yieldTrend":0,"recProb":5,"yieldSpeed3m":0,"yieldWasInv":False,"yieldDangerous":False,"yieldSignal":"wait","yieldDesc":"?"}, "Long Term")
    huf  = safe(fetch_huf_usd,            {"hufRate":0,"hufChg1w":0,"hufChg1m":0,"hufTrend":"nincs adat","hufSignal":"wait"}, "HUF/USD")
    sectors = safe(fetch_sector_rotation, {"sectors":[],"leading":"","lagging":""}, "Szektorrotáció")
    alerts  = safe(fetch_alerts,          [], "Riasztások")

    # Smart Money indikátorok (ÚJ v5.1)
    smart = {}
    smart.update(safe(fetch_mcclellan,        {"mcSum":0,"mcOsc":0,"mcSignal":"wait","mcDesc":"Nincs adat","mcTrend":"wait","mcZeroCrossDown":False,"mcZeroCrossUp":False}, "McClellan"))
    smart.update(safe(fetch_global_liquidity, {"netLiq":0,"netLiqChg4w":0,"netLiqChgPct":0,"fedBal":0,"tgaBal":0,"liqSignal":"wait","liqDesc":"Nincs adat","liqTrend":"wait"}, "Global Liquidity"))
    smart.update(safe(fetch_dix_gex,          {"dix":43.0,"dix20d":43.0,"gex":5e9,"dixSignal":"wait","dixDesc":"Nincs adat","gexSignal":"wait","gexDesc":"Nincs adat","dixFalling":False}, "DIX/GEX"))
    smart.update(safe(fetch_cot_smart_money,  {"cotNet":0,"cotAvg":0,"cotZScore":0.0,"cotSignal":"wait","cotDesc":"Nincs adat","cotDate":""}, "COT"))

    regime  = detect_regime(base, now, lng)
    es      = calc_entry_score(now, mid, lng, base, smart)
    cp      = calc_corr_prob(now, mid, lng, base)
    kelly   = calc_kelly_v5(es, cp, regime, smart=smart, base=base, now=now, mid=mid)
    season  = calc_seasonality()

    hist    = save_history(hist, base, now, mid, lng, es, cp, regime, kelly)
    log_d   = save_error_log()

    log(f"\n  📊 Score: {es}/100 | Playbook: {kelly.get('playbook','?').upper()} | Korr.: {cp}% | Allokáció: {kelly.get('kellyAlloc')}%\n")

    html_content = generate_html(
        base=base, now=now, mid=mid, lng=lng,
        es=es, cp=cp, history=hist, alerts=alerts,
        log_data=log_d, kelly=kelly, season=season,
        regime=regime, screener_data=screener_data,
        sectors=sectors, huf=huf, smart=smart
    )

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)
    log(f"HTML mentve: {OUTPUT_HTML}")
    print(f"\n  ✓ Dashboard v5 kész!\n")

if __name__ == "__main__":
    main()





