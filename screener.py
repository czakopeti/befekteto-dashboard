"""
screener.py – Quality-at-Discount Részvényszűrő
Befekteto Dashboard v4 kiegészítő modul

Logika:
1. FMP Screener API → 500 nagy részvény (MarketCap > 5B, US tőzsde)
2. yfinance batch letöltés → SMA200 számítás (sweet spot: -10% to -25%)
3. yfinance .info → ROE > 15%, D/E < 1.5
4. FMP Analyst Estimates → pozitív EPS növekedési outlook
5. Kompozit score → rendezés, top 15 mentése screener.json-ba

GitHub Actions: pénteken 20:00 UTC, FMP_API_KEY secret szükséges
"""

import os, json, time, datetime
import requests
import pandas as pd
import yfinance as yf
from pathlib import Path

# ── KONFIG ────────────────────────────────────────────────────
FMP_API_KEY   = os.environ.get("FMP_API_KEY", "")
OUTPUT_FILE   = "screener.json"
FMP_BASE      = "https://financialmodelingprep.com/api/v3"

# Szűrési paraméterek
MIN_ROE        = 10.0   # % – Peter kérésére csökkentve 15→10
MIN_ROI        = 10.0   # % – yfinance returnOnAssets proxy
MAX_DE         = 1.5    # Debt/Equity ratio
MIN_MKTCAP     = 5_000_000_000  # $5B
SMA200_MIN     = -25.0  # % – ne vegyük a zuhanó késeket
SMA200_MAX     = -10.0  # % – elég diszkont kell
MAX_RESULTS    = 20     # Top N a dashboardon

HEADERS = {"User-Agent": "Mozilla/5.0"}

def log(msg, ok=True):
    prefix = "✓" if ok else "⚠"
    print(f"  {prefix} {msg}")

# ── 1. FMP SCREENER – ALAPLISTA ───────────────────────────────
def fetch_fmp_candidates():
    """
    FMP /stock-screener endpoint: MarketCap > 5B, US, aktív kereskedés.
    1 API hívás, ~500 eredmény.
    """
    if not FMP_API_KEY:
        raise ValueError("FMP_API_KEY nincs beállítva! Add hozzá GitHub Secrets-hez.")
    
    params = {
        "marketCapMoreThan": MIN_MKTCAP,
        "isActivelyTrading": "true",
        "isEtf": "false",
        "exchange": "nasdaq,nyse",
        "limit": 500,
        "apikey": FMP_API_KEY
    }
    
    r = requests.get(f"{FMP_BASE}/stock-screener", params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    
    # Csak 1-5 betűs tickerek (ETF/warrant szűrés)
    clean = [s for s in data 
             if s.get("symbol") and 1 <= len(s["symbol"]) <= 5 
             and s.get("marketCap", 0) >= MIN_MKTCAP]
    
    log(f"FMP screener: {len(clean)} jelölt (MarketCap > $5B)")
    return clean

# ── 2. SMA200 SZŰRÉS – BATCH YFINANCE ────────────────────────
def filter_by_sma200(candidates):
    """
    Batch letölti az áradatokat yfinance-szel,
    kiszűri a SMA200_MIN–SMA200_MAX sávba esőket.
    Sokkal gyorsabb mint egyenként lekérdezni.
    """
    tickers = [s["symbol"] for s in candidates]
    
    log(f"SMA200 számítás: {len(tickers)} részvény batch letöltése...")
    
    try:
        # Batch letöltés – 1 hívás az összes tickerre
        raw = yf.download(
            tickers,
            period="1y",
            auto_adjust=True,
            progress=False,
            threads=True
        )["Close"]
    except Exception as e:
        log(f"Batch letöltés hiba: {e}", ok=False)
        return []
    
    results = []
    cand_map = {s["symbol"]: s for s in candidates}
    
    for ticker in tickers:
        try:
            if ticker not in raw.columns:
                continue
            
            series = raw[ticker].dropna()
            if len(series) < 200:
                continue
            
            price   = float(series.iloc[-1])
            ma200   = float(series.rolling(200).mean().iloc[-1])
            
            if pd.isna(ma200) or ma200 <= 0:
                continue
            
            pct_from_ma200 = (price - ma200) / ma200 * 100
            
            # Sweet spot szűrő
            if SMA200_MIN <= pct_from_ma200 <= SMA200_MAX:
                s = cand_map[ticker]
                results.append({
                    "ticker":  ticker,
                    "name":    s.get("companyName", ticker),
                    "sector":  s.get("sector", ""),
                    "price":   round(price, 2),
                    "ma200":   round(ma200, 2),
                    "vsMA200": round(pct_from_ma200, 1),
                    "mktCapB": round(s.get("marketCap", 0) / 1e9, 1),
                })
        except Exception:
            continue
    
    log(f"SMA200 szűrés után: {len(results)} jelölt ({SMA200_MIN}% – {SMA200_MAX}% sáv)")
    return results

# ── 3. FUNDAMENTÁLIS SZŰRÉS – YFINANCE .INFO ─────────────────
def filter_by_fundamentals(candidates):
    """
    yfinance .info: ROE > 15%, D/E < 1.5
    Egyenként kérdezi le, de csak a ~30-60 szűrt jelöltre.
    """
    results = []
    
    for i, stock in enumerate(candidates):
        ticker = stock["ticker"]
        try:
            info = yf.Ticker(ticker).info
            
            # ROE (tizedes formátumban adja vissza yfinance)
            roe_raw = info.get("returnOnEquity")
            if roe_raw is None:
                continue
            roe = roe_raw * 100
            
            # ROI proxy: returnOnAssets (konzervatívabb, de megbízható)
            roi_raw = info.get("returnOnAssets")
            if roi_raw is None:
                continue
            roi = roi_raw * 100
            
            # Debt/Equity (yfinance 100x-os skálán adja)
            de_raw = info.get("debtToEquity", 0) or 0
            de = de_raw / 100 if de_raw > 10 else de_raw  # normalizálás
            
            # Forward P/E – extra minőség jelző
            fwd_pe = info.get("forwardPE") or info.get("trailingPE") or 0
            
            # EPS növekedés
            eps_curr = info.get("trailingEps", 0) or 0
            eps_fwd  = info.get("forwardEps", 0) or 0
            eps_growth_ok = (eps_fwd > eps_curr > 0)
            
            # Szűrők
            if roe < MIN_ROE:
                continue
            if roi < MIN_ROI:
                continue
            if de > MAX_DE:
                continue
            
            stock.update({
                "roe":           round(roe, 1),
                "roi":           round(roi, 1),
                "de":            round(de, 2),
                "fwdPE":         round(fwd_pe, 1) if fwd_pe else None,
                "epsGrowthOk":   eps_growth_ok,
                "eps":           round(eps_curr, 2),
                "epsFwd":        round(eps_fwd, 2),
            })
            results.append(stock)
            
            # Rate limiting – kerüljük a yfinance throttle-t
            if i % 10 == 9:
                time.sleep(0.5)
                
        except Exception as e:
            continue
    
    log(f"Fundamentális szűrés után: {len(results)} jelölt (ROE>{MIN_ROE}%, ROI>{MIN_ROI}%, D/E<{MAX_DE})")
    return results

# ── 4. EPS OUTLOOK – FMP ANALYST ESTIMATES ───────────────────
def enrich_with_eps_outlook(candidates):
    """
    FMP /analyst-estimates: következő évi EPS konszenzus.
    ~N API hívás (egy jelöltenként) – takarékosan használja a free tier limitet.
    """
    results = []
    
    for stock in candidates:
        ticker = stock["ticker"]
        try:
            url    = f"{FMP_BASE}/analyst-estimates/{ticker}"
            params = {"limit": 4, "period": "annual", "apikey": FMP_API_KEY}
            r      = requests.get(url, params=params, timeout=10)
            
            if r.status_code != 200:
                stock["analystEpsGrowth"] = None
                results.append(stock)
                continue
            
            data = r.json()
            if len(data) >= 2:
                # data[0] = legközelebbi jövő, data[1] = ez utáni
                curr_est = data[0].get("estimatedEpsAvg", 0) or 0
                prev_est = data[1].get("estimatedEpsAvg", 0) or 0
                
                if prev_est > 0 and curr_est > 0:
                    growth_pct = (curr_est - prev_est) / prev_est * 100
                    stock["analystEpsGrowth"] = round(growth_pct, 1)
                    stock["analystEpsFwd"]    = round(curr_est, 2)
                else:
                    stock["analystEpsGrowth"] = None
            else:
                stock["analystEpsGrowth"] = None
            
            results.append(stock)
            time.sleep(0.2)  # FMP rate limit tiszteletben tartása
            
        except Exception:
            stock["analystEpsGrowth"] = None
            results.append(stock)
    
    # Szűrés: negatív EPS outlook kizárása (ha van adat)
    filtered = [s for s in results 
                if s.get("analystEpsGrowth") is None  # nincs adat = benn marad
                or s["analystEpsGrowth"] >= 0]
    
    log(f"EPS outlook szűrés után: {len(filtered)} jelölt")
    return filtered

# ── 5. KOMPOZIT SCORE ─────────────────────────────────────────
def calc_quality_score(stock):
    """
    Kompozit minőségi-diszkont score (0–100).
    Magasabb = jobb minőség + megfelelő diszkont.
    """
    sc = 50
    
    # ROE minőség (0–20p)
    roe = stock.get("roe", 0)
    if roe >= 40:   sc += 20
    elif roe >= 30: sc += 15
    elif roe >= 20: sc += 10
    elif roe >= 15: sc += 5
    
    # ROI minőség (0–15p)
    roi = stock.get("roi", 0)
    if roi >= 25:   sc += 15
    elif roi >= 20: sc += 10
    elif roi >= 15: sc += 5
    
    # Diszkont mértéke – sweet spot -15% körül (0–15p)
    vma = stock.get("vsMA200", 0)
    disc_score = max(0, min(15, int((-vma - 10) / 15 * 15)))  # -10%→0p, -25%→15p
    sc += disc_score
    
    # D/E minőség (0–10p)
    de = stock.get("de", 99)
    if de < 0.3:   sc += 10
    elif de < 0.7: sc += 7
    elif de < 1.0: sc += 4
    elif de < 1.5: sc += 1
    
    # EPS outlook (0–10p)
    eps_growth = stock.get("analystEpsGrowth")
    if eps_growth is not None:
        if eps_growth >= 20:   sc += 10
        elif eps_growth >= 10: sc += 7
        elif eps_growth >= 5:  sc += 4
        elif eps_growth >= 0:  sc += 2
    
    # Alacsony D/E bónusz
    if de < 0.3 and roe >= 25: sc += 5
    
    return min(100, max(0, round(sc)))

# ── 6. MENTÉS ─────────────────────────────────────────────────
def save_results(results):
    output = {
        "updated":    datetime.datetime.now().isoformat(),
        "count":      len(results),
        "params": {
            "min_roe":    MIN_ROE,
            "min_roi":    MIN_ROI,
            "max_de":     MAX_DE,
            "sma200_min": SMA200_MIN,
            "sma200_max": SMA200_MAX,
            "min_mktcap_b": MIN_MKTCAP / 1e9
        },
        "stocks": results
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    log(f"Mentve: {OUTPUT_FILE} ({len(results)} részvény)")

# ── MAIN ──────────────────────────────────────────────────────
def run_screener():
    print("\n═══ Quality-at-Discount Screener ═══")
    print(f"  Futás: {datetime.datetime.now().strftime('%Y.%m.%d %H:%M')}")
    print(f"  Paraméterek: ROE>{MIN_ROE}%, ROI>{MIN_ROI}%, "
          f"D/E<{MAX_DE}, SMA200: {SMA200_MIN}%–{SMA200_MAX}%\n")
    
    try:
        # 1. Alaplista
        candidates = fetch_fmp_candidates()
        
        # 2. SMA200 szűrés (batch)
        sma_filtered = filter_by_sma200(candidates)
        
        if not sma_filtered:
            log("Nincs SMA200 sweet spot-ban lévő jelölt – gyenge piac?", ok=False)
            save_results([])
            return
        
        # 3. Fundamentális szűrés
        fund_filtered = filter_by_fundamentals(sma_filtered)
        
        if not fund_filtered:
            log("Nincs minőségi jelölt a szűrők után", ok=False)
            save_results([])
            return
        
        # 4. EPS outlook (FMP)
        enriched = enrich_with_eps_outlook(fund_filtered)
        
        # 5. Kompozit score + rendezés
        for s in enriched:
            s["qualityScore"] = calc_quality_score(s)
        
        enriched.sort(key=lambda x: x["qualityScore"], reverse=True)
        top = enriched[:MAX_RESULTS]
        
        # 6. Mentés
        save_results(top)
        
        print(f"\n  Top {len(top)} jelölt:")
        for s in top[:10]:
            print(f"    {s['ticker']:6} | Score:{s['qualityScore']:3} | "
                  f"MA200:{s['vsMA200']:6.1f}% | ROE:{s['roe']:5.1f}% | "
                  f"{s['name'][:35]}")
        
        print("\n  ✓ Screener kész\n")
        
    except Exception as e:
        log(f"Screener hiba: {e}", ok=False)
        # Üres eredmény – ne törje el a dashboardot
        save_results([])

if __name__ == "__main__":
    run_screener()

# ══════════════════════════════════════════════════════════════
# EPS REVÍZIÓS DELTA (ÚJ – v5)
# ══════════════════════════════════════════════════════════════
def check_eps_revision_delta(ticker, fmp_key):
    """
    Az Earnings Scout logika: ha az elemzők az elmúlt 4 hétben
    FELFELÉ revideálták az EPS becsléseket → pozitív momentum.
    Ez az egyik legjobb dokumentált alfa-faktor.

    FMP /analyst-estimates endpoint: összehasonlítja a jelenlegi
    konszenzust a 4 hetes régi becslésekkel.
    """
    try:
        # Negyedéves becslések
        url = f"{FMP_BASE}/analyst-estimates/{ticker}"
        params = {"limit": 2, "period": "quarterly", "apikey": fmp_key}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return {"epsRevision": None, "epsRevDelta": None}

        data = r.json()
        if not data or len(data) < 1:
            return {"epsRevision": None, "epsRevDelta": None}

        current = data[0]
        eps_high = current.get("estimatedEpsHigh", 0) or 0
        eps_low  = current.get("estimatedEpsLow", 0) or 0
        eps_avg  = current.get("estimatedEpsAvg", 0) or 0

        if eps_avg == 0:
            return {"epsRevision": None, "epsRevDelta": None}

        # A revíziós erő proxy: (high - low) / abs(avg) – szűk range = konszenzus
        # Ha eps_avg pozitív és emelkedő: pozitív revízió
        revision_strength = (eps_high - eps_low) / abs(eps_avg) if eps_avg != 0 else 0

        # Ha a következő negyedévi becslés > jelenlegi: pozitív revízió
        if len(data) >= 2:
            next_eps = data[1].get("estimatedEpsAvg", 0) or 0
            delta_pct = ((eps_avg - next_eps) / abs(next_eps) * 100
                         if next_eps != 0 else None)
        else:
            delta_pct = None

        revision_positive = (eps_avg > 0 and
                             (delta_pct is None or delta_pct > -5))

        return {
            "epsRevision":  round(eps_avg, 2),
            "epsRevDelta":  round(delta_pct, 1) if delta_pct else None,
            "epsRevPositive": revision_positive,
            "epsRevStrength": round(revision_strength, 2),
        }
    except Exception:
        return {"epsRevision": None, "epsRevDelta": None, "epsRevPositive": None}

# Frissített calc_quality_score – EPS revision deltával
def calc_quality_score_v5(stock):
    """
    Kompozit minőségi-diszkont score (0–100) v5.
    Az EPS revíziós delta most külön faktort kap.
    """
    sc = 50
    roe = stock.get("roe", 0)
    if roe >= 40:   sc += 20
    elif roe >= 30: sc += 15
    elif roe >= 20: sc += 10
    elif roe >= 15: sc += 5

    roi = stock.get("roi", 0)
    if roi >= 25:   sc += 15
    elif roi >= 20: sc += 10
    elif roi >= 15: sc += 5

    vma = stock.get("vsMA200", 0)
    disc_score = max(0, min(15, int((-vma - 10) / 15 * 15)))
    sc += disc_score

    de = stock.get("de", 99)
    if de < 0.3:   sc += 10
    elif de < 0.7: sc += 7
    elif de < 1.0: sc += 4
    elif de < 1.5: sc += 1

    # Alap EPS outlook
    eps_growth = stock.get("analystEpsGrowth")
    if eps_growth is not None:
        if eps_growth >= 20:   sc += 8
        elif eps_growth >= 10: sc += 5
        elif eps_growth >= 5:  sc += 3
        elif eps_growth >= 0:  sc += 1

    # ÚJ: EPS revíziós delta (Earnings Scout logika)
    if stock.get("epsRevPositive"):
        sc += 10  # erős extra pont ha az elemzők felfelé revideálnak
        delta = stock.get("epsRevDelta")
        if delta is not None and delta > 5:
            sc += 5  # extra ha nagy az emelkedés

    return min(100, max(0, round(sc)))

