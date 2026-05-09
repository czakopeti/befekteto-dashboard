"""
screener_v2.py – Quality-at-Discount Részvényszűrő
FMP NÉLKÜL – teljes yfinance alapú

Logika:
1. Fix S&P500 ticker lista (500 részvény, FMP-től független)
2. yfinance batch SMA200 számítás
3. yfinance .info fundamentális szűrés
4. Kompozit score + rendezés
"""

import os, json, time, datetime
import pandas as pd
import yfinance as yf
from pathlib import Path

OUTPUT_FILE   = "screener.json"
MIN_ROE       = 10.0
MIN_ROI       = 8.0   # ROA proxy – kicsit lazabb mint ROE
MAX_DE        = 1.5
MIN_MKTCAP    = 5_000_000_000
SMA200_MIN    = -30.0
SMA200_MAX    = -5.0
MAX_RESULTS   = 20

def log(msg, ok=True):
    print(f"  {'✓' if ok else '⚠'} {msg}")

# S&P 500 top 300 – megbízható yfinance coverage
SP500 = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","BRK-B","AVGO",
    "JPM","LLY","UNH","XOM","V","MA","HD","PG","JNJ","COST",
    "ABBV","MRK","BAC","CRM","ORCL","CVX","WMT","NFLX","KO","CSCO",
    "PEP","ACN","TMO","ABT","MCD","IBM","LIN","PM","GE","DHR",
    "TXN","CAT","SPGI","ISRG","INTU","AMAT","NOW","BKNG","GS","AMGN",
    "RTX","SYK","BLK","VRTX","ADI","ELV","PANW","DE","GILD","AXP",
    "SBUX","TJX","MDT","SCHW","MMC","CB","AMT","REGN","PLD","ETN",
    "SO","DUK","EOG","MPC","PSX","SLB","OKE","WM","ECL","ZTS",
    "ITW","CME","AON","FCX","APH","CDNS","SNPS","KLAC","LRCX","MCHP",
    "USB","PNC","TFC","COF","AIG","MET","AFL","ALL","PRU","HUM",
    "CI","CVS","MCK","CAH","ABC","DGX","LH","MTD","IDXX","DXCM",
    "A","BDX","EW","STE","BSX","BAX","HOLX","PODD","ALGN","RMD",
    "WELL","VTR","ARE","EQR","AVB","ESS","MAA","UDR","CPT","NXE",
    "NEE","AEP","EXC","XEL","WEC","D","ED","PCG","EIX","PPL",
    "NEM","FCX","GOLD","AA","CLF","NUE","STLD","RS","ATI","CRS",
    "LOW","TGT","DG","DLTR","BBY","ROST","TJX","GAP","M","KSS",
    "F","GM","APTV","BWA","LEA","MGA","ALV","VC","DAN","TEN",
    "DAL","UAL","AAL","LUV","JBLU","HA","ALK","SAVE","SKYW","MESA",
    "MAR","HLT","IHG","H","WH","CHH","RCL","CCL","NCLH","VAC",
    "DIS","PARA","WBD","FOX","FOXA","LGF-A","AMC","CNK","IMAX","LYV",
    "CRWD","DDOG","SNOW","NET","MDB","GTLB","PATH","AI","PLTR","HOOD",
    "SQ","PYPL","COIN","AFRM","SOFI","UPST","LC","OPEN","RDFN","Z",
    "UBER","LYFT","DASH","ABNB","VRBO","EXPE","BKNG","TRIP","PCLN","HTHT",
    "CCI","AMT","SBAC","UNIT","IIPR","MPW","VNO","SLG","BXP","KIM",
    "WFC","C","MS","GS","BAC","DB","CS","UBS","HSBC","TD",
    "ADBE","WORK","ZM","DOCU","OKTA","TWLO","ZI","HUBS","DOMO","BOX",
    "VMW","HPE","DELL","HPQ","NTAP","STX","WDC","MU","QCOM","MRVL",
]
# Deduplikálás
SP500 = list(dict.fromkeys(SP500))

def calc_sma200_batch():
    """Batch SMA200 számítás yfinance-szel"""
    log(f"SMA200 számítás: {len(SP500)} részvény letöltése...")
    try:
        raw = yf.download(
            SP500, period="1y", auto_adjust=True,
            progress=False, threads=True
        )["Close"]
    except Exception as e:
        log(f"Batch letöltés hiba: {e}", ok=False)
        return []

    results = []
    for ticker in SP500:
        try:
            if ticker not in raw.columns:
                continue
            s = raw[ticker].dropna()
            if len(s) < 200:
                continue
            price  = float(s.iloc[-1])
            ma200  = float(s.rolling(200).mean().iloc[-1])
            if pd.isna(ma200) or ma200 <= 0:
                continue
            pct = round((price - ma200) / ma200 * 100, 1)
            if SMA200_MIN <= pct <= SMA200_MAX:
                results.append({
                    "ticker":   ticker,
                    "price":    round(price, 2),
                    "vsMA200":  pct,
                    "name":     ticker,
                })
        except Exception:
            continue

    log(f"SMA200 szűrés: {len(results)} részvény a {SMA200_MIN}%–{SMA200_MAX}% sávban")
    return results

def enrich_fundamentals(candidates):
    """ROE, ROI, D/E, piaci kapitalizáció yfinance .info-ból"""
    results = []
    dropped = {"roe":0,"roi":0,"de":0,"mktcap":0,"no_data":0}
    WATCH = {"MSFT","AAPL","NVDA","MA","V","GOOGL","META","CRWD","DDOG","TSLA"}

    for i, stock in enumerate(candidates):
        ticker = stock["ticker"]
        try:
            info = yf.Ticker(ticker).info

            mktcap = info.get("marketCap", 0) or 0
            if mktcap < MIN_MKTCAP:
                dropped["mktcap"] += 1
                continue

            roe_raw = info.get("returnOnEquity")
            roi_raw = info.get("returnOnAssets")
            if roe_raw is None or roi_raw is None:
                dropped["no_data"] += 1
                continue

            roe = roe_raw * 100
            roi = roi_raw * 100
            de_raw = info.get("debtToEquity", 0) or 0
            de = de_raw / 100 if de_raw > 10 else de_raw

            if roe < MIN_ROE:
                dropped["roe"] += 1
                if ticker in WATCH:
                    log(f"  {ticker}: kiesett ROE={roe:.1f}% (min:{MIN_ROE}%)", ok=False)
                continue
            if roi < MIN_ROI:
                dropped["roi"] += 1
                if ticker in WATCH:
                    log(f"  {ticker}: kiesett ROI={roi:.1f}% (min:{MIN_ROI}%)", ok=False)
                continue
            if de > MAX_DE:
                dropped["de"] += 1
                if ticker in WATCH:
                    log(f"  {ticker}: kiesett D/E={de:.2f} (max:{MAX_DE})", ok=False)
                continue

            fwd_pe   = info.get("forwardPE") or info.get("trailingPE") or 0
            eps_curr = info.get("trailingEps", 0) or 0
            eps_fwd  = info.get("forwardEps", 0) or 0
            name     = info.get("shortName", ticker)
            sector   = info.get("sector", "")
            analyst_target = info.get("targetMeanPrice", 0) or 0
            eps_rev_pos = (eps_fwd > eps_curr > 0)

            # Quality score
            sc = 0
            if roe >= 40:   sc += 25
            elif roe >= 25: sc += 18
            elif roe >= 15: sc += 10
            elif roe >= 10: sc += 5
            if roi >= 20:   sc += 15
            elif roi >= 12: sc += 10
            elif roi >= 8:  sc += 5
            disc = abs(stock["vsMA200"])
            if disc >= 20:  sc += 20
            elif disc >= 15:sc += 15
            elif disc >= 10:sc += 10
            elif disc >= 5: sc += 5
            if de < 0.3:    sc += 10
            elif de < 0.7:  sc += 7
            elif de < 1.2:  sc += 3
            if eps_rev_pos: sc += 10
            if analyst_target > 0 and stock["price"] > 0:
                upside = (analyst_target - stock["price"]) / stock["price"] * 100
                if upside > 20:  sc += 20
                elif upside > 10:sc += 12
                elif upside > 0: sc += 5

            stock.update({
                "name":               name,
                "sector":             sector,
                "roe":                round(roe, 1),
                "roi":                round(roi, 1),
                "de":                 round(de, 2),
                "qualityScore":       sc,
                "analystEpsGrowth":   round((eps_fwd - eps_curr) / max(abs(eps_curr), 0.01) * 100, 1) if eps_curr else 0,
                "epsRevPositive":     eps_rev_pos,
                "analystPriceTarget": round(analyst_target, 2),
            })
            results.append(stock)
            if ticker in WATCH:
                log(f"  {ticker}: MEGFELELT – ROE:{roe:.1f}% ROI:{roi:.1f}% D/E:{de:.2f} MA200:{stock['vsMA200']:.1f}%")

            if i % 5 == 4:
                time.sleep(0.3)

        except Exception as e:
            dropped["no_data"] += 1
            continue

    log(f"Fundamentális szűrés: {len(results)} megfelelt | "
        f"ROE:{dropped['roe']} ROI:{dropped['roi']} D/E:{dropped['de']} "
        f"MktCap:{dropped['mktcap']} Nincs adat:{dropped['no_data']}")
    return results

def save_results(stocks):
    out = {
        "stocks":  stocks,
        "updated": datetime.datetime.now().isoformat(),
        "count":   len(stocks),
        "params": {
            "sma200_min": SMA200_MIN,
            "sma200_max": SMA200_MAX,
            "min_roe":    MIN_ROE,
            "max_de":     MAX_DE,
        }
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(out, f, indent=2)
    log(f"Mentve: {OUTPUT_FILE} ({len(stocks)} részvény)")

def run_screener():
    print("\n═══ Quality-at-Discount Screener v2 (yfinance) ═══")
    print(f"  Futás: {datetime.datetime.now().strftime('%Y.%m.%d %H:%M')}")
    print(f"  Szűrők: ROE>{MIN_ROE}% | ROI>{MIN_ROI}% | D/E<{MAX_DE} | SMA200: {SMA200_MIN}%–{SMA200_MAX}%\n")

    sma_filtered = calc_sma200_batch()
    if not sma_filtered:
        log("0 részvény a diszkont zónában – bull piac", ok=False)
        save_results([])
        return

    enriched = enrich_fundamentals(sma_filtered)
    if not enriched:
        log("0 minőségi jelölt a szűrők után", ok=False)
        save_results([])
        return

    enriched.sort(key=lambda x: x["qualityScore"], reverse=True)
    top = enriched[:MAX_RESULTS]
    save_results(top)

    print(f"\n  Top {len(top)} jelölt:")
    for s in top[:10]:
        print(f"    {s['ticker']:6} | Score:{s['qualityScore']:3} | "
              f"MA200:{s['vsMA200']:+.1f}% | ROE:{s['roe']:.1f}% | {s['name'][:30]}")
    print("\n  ✓ Kész\n")

if __name__ == "__main__":
    run_screener()
