#!/usr/bin/env python3
"""
squeeze_scanner.py – Pre-Movement Detector v1
=========================================
Megkeresi a BB Squeeze + Earnings Catalyst + EPS Revízió kombinációját.

Logika:
  STRONG  = Squeeze + EPS revízió ↑ + Earnings 5-21 napon belül
  WATCH   = Squeeze + semleges EPS + Earnings közeledik
  SYMPATHY= Sector leader beat, peer tartja a gaineket (2. hullám)
  AVOID   = Squeeze + EPS ↓ (tájékoztatásként mutatja)

Futás: naponta 08:00 Budapest, GitHub Actions
Output: squeeze_results.json + ntfy push notification
"""

import os, json, time, datetime, requests
import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path

# ── Konfiguráció ────────────────────────────────────────────
NTFY_TOPIC       = os.environ.get("NTFY_TOPIC", "")
OUTPUT_FILE      = "squeeze_results.json"
STATE_FILE       = "squeeze_state.json"

BB_PERIOD        = 20          # Bollinger Band periódus
BB_STD           = 2           # Bollinger Band szórás
SQUEEZE_PERCENTILE = 20        # BB Width < 20. percentile = squeeze
LOOKBACK_DAYS    = 180         # 6 hónap historikus BB Width-hez
EARNINGS_MIN     = 3           # Min napok earnings-ig (túl közel = már benne van az ár)
EARNINGS_MAX     = 21          # Max napok earnings-ig
EPS_REVISION_DAYS = 60         # Hány napos EPS revízió változást néz
VOL_DRY_RATIO    = 0.65        # Volume < 65% of 20d avg = dry-up
HEADERS          = {"User-Agent": "Mozilla/5.0"}

# ── Szektor csoportok (contagion detektáláshoz) ─────────────
SECTORS = {
    "semiconductor": [
        "NVDA","AMD","INTC","MU","QCOM","AMAT","LRCX","KLAC",
        "AVGO","MRVL","ADI","MCHP","TXN","SLAB","SWKS","QRVO",
        "ON","WOLF","ENTG","MKSI","ACLS","ONTO","COHU","RMBS",
    ],
    "cloud_ai": [
        "MSFT","AMZN","GOOGL","META","CRM","NOW","SNOW","DDOG",
        "ORCL","WDAY","HUBS","ZM","TEAM","TWLO","MDB","GTLB",
        "PATH","PLTR","AI","CFLT","DOMO","BOX","DOCN",
    ],
    "cybersecurity": [
        "CRWD","PANW","ZS","FTNT","OKTA","S","CHKP","TENB",
        "RPD","VRNS","QLYS","SSTI","CYBR","OSPN",
    ],
    "space_defense": [
        "RKLB","LUNR","ASTS","KTOS","RTX","LMT","NOC","GD",
        "BA","HII","TDG","AXON","IRDM","SPCE","PL",
    ],
    "biotech_pharma": [
        "LLY","ABBV","REGN","VRTX","GILD","AMGN","BIIB",
        "MRNA","BNTX","INCY","ALNY","RARE","PTGX","RCUS",
    ],
    "fintech": [
        "SQ","PYPL","COIN","AFRM","SOFI","UPST","LC","HOOD",
        "NU","FLYW","BILL","SMAR","TOST","RELY",
    ],
    "ev_energy": [
        "TSLA","RIVN","LCID","GM","F","QS","CHPT",
        "BLNK","EVGO","STEM","ENPH","SEDG","ARRY",
    ],
}

# Teljes universe = minden szektor tag + S&P500 core
SECTOR_TICKERS = list(set(t for tickers in SECTORS.values() for t in tickers))

SP500_CORE = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","AVGO","JPM",
    "LLY","UNH","XOM","V","MA","HD","PG","JNJ","COST","ABBV","MRK","BAC",
    "CRM","ORCL","CVX","WMT","NFLX","KO","CSCO","PEP","ACN","TMO","ABT",
    "MCD","IBM","LIN","PM","GE","DHR","TXN","CAT","SPGI","ISRG","INTU",
    "AMAT","NOW","BKNG","GS","AMGN","RTX","SYK","BLK","VRTX","ADI",
    "PANW","DE","GILD","AXP","SBUX","TJX","MDT","SCHW","MMC","CB","AMT",
    "REGN","PLD","ETN","ADBE","WM","ECL","ZTS","ITW","CME","AON","KLAC",
    "LRCX","MCHP","CDNS","SNPS","IDXX","RMD","DXCM","A","WELL","NEE",
    "LOW","TGT","DG","ROST","F","GM","DAL","UAL","MAR","HLT","DIS",
    "CRWD","DDOG","SNOW","NET","MDB","PLTR","COIN","PYPL","SQ","UBER",
    "DASH","ABNB","RKLB","LUNR","ASTS","FTNT","ZS","OKTA","CHKP","PANW",
    "WDAY","TEAM","TWLO","GTLB","PATH","AI","CFLT","SOFI","AFRM","HOOD",
    "TSLA","RIVN","QS","ENPH","SEDG","FCX","NEM","GOLD","CLF","NUE",
]

UNIVERSE = list(set(SP500_CORE + SECTOR_TICKERS))
print(f"Universe: {len(UNIVERSE)} részvény")

# ── Helper függvények ────────────────────────────────────────
def log(msg): print(f"  {msg}")

def load_state():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"sent_alerts": {}, "sector_beats": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def send_ntfy(title, message, priority="default"):
    if not NTFY_TOPIC:
        print(f"[NTFY] {title}: {message[:100]}")
        return
    import re
    title_clean = re.sub(r'[^\x00-\x7F]+', '', title).strip() or "Squeeze Alert"
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title_clean, "Priority": priority,
                     "Content-Type": "text/plain; charset=utf-8"}, timeout=10)
        print(f"[NTFY OK] {title_clean}")
    except Exception as e:
        print(f"[NTFY HIBA] {e}")

# ── 1. BATCH ár letöltés ─────────────────────────────────────
def download_prices(tickers, period="1y"):
    log(f"Letöltés: {len(tickers)} ticker...")
    batches = [tickers[i:i+100] for i in range(0, len(tickers), 100)]
    all_data = {}
    for i, batch in enumerate(batches):
        try:
            raw = yf.download(batch, period=period, auto_adjust=True,
                              progress=False, threads=True)
            if "Close" in raw.columns:
                close = raw["Close"]
                volume = raw["Volume"]
            else:
                close = raw.xs("Close", axis=1, level=0) if ("Close","") not in raw.columns else raw["Close"]
                volume = raw.xs("Volume", axis=1, level=0) if ("Volume","") not in raw.columns else raw["Volume"]
            for t in batch:
                if t in close.columns:
                    all_data[t] = {"close": close[t].dropna(),
                                   "volume": volume[t].dropna()}
            log(f"  Batch {i+1}/{len(batches)}: {len([t for t in batch if t in close.columns])} OK")
        except Exception as e:
            log(f"  Batch {i+1} hiba: {e}")
        time.sleep(0.5)
    return all_data

# ── 2. BB SQUEEZE számítás ───────────────────────────────────
def calc_squeeze_score(close_series, volume_series):
    """
    Visszaad:
    - squeeze_score: 0-100 (100 = legerősebb squeeze)
    - bbw_pct: BB Width aktuális percentilis (0 = legszűkebb)
    - vol_dry: True ha volume kiszáradt
    - price: aktuális ár
    """
    if len(close_series) < LOOKBACK_DAYS:
        return None

    s = close_series.copy()
    s.index = s.index.tz_localize(None) if s.index.tz else s.index

    sma   = s.rolling(BB_PERIOD).mean()
    std   = s.rolling(BB_PERIOD).std()
    upper = sma + BB_STD * std
    lower = sma - BB_STD * std
    bbw   = ((upper - lower) / sma * 100).dropna()

    if len(bbw) < 60:
        return None

    cur_bbw  = float(bbw.iloc[-1])
    hist_bbw = bbw.iloc[-LOOKBACK_DAYS:]
    pct_rank = float((hist_bbw < cur_bbw).sum() / len(hist_bbw) * 100)

    # Volume dry-up
    v = volume_series.copy()
    v.index = v.index.tz_localize(None) if v.index.tz else v.index
    vol_5d  = float(v.rolling(5).mean().iloc[-1]) if len(v) >= 5 else 0
    vol_20d = float(v.rolling(20).mean().iloc[-1]) if len(v) >= 20 else 1
    vol_dry = (vol_5d / vol_20d < VOL_DRY_RATIO) if vol_20d > 0 else False

    # NR7 – utolsó 7 nap legszűkebb sávja
    if len(close_series) >= 7:
        recent = close_series.tail(10)
        daily_range = (recent.rolling(2).max() - recent.rolling(2).min()).dropna()
        nr7 = (len(daily_range) >= 7 and float(daily_range.iloc[-1]) == float(daily_range.tail(7).min()))
    else:
        nr7 = False

    # Squeeze score: alacsonyabb pct_rank = erősebb squeeze
    squeeze_score = round(100 - pct_rank)
    # Bónuszok
    if vol_dry: squeeze_score = min(100, squeeze_score + 10)
    if nr7:     squeeze_score = min(100, squeeze_score + 5)

    return {
        "squeeze_score": squeeze_score,
        "bbw_pct":       round(pct_rank, 1),  # alacsonyabb = erősebb
        "bbw_current":   round(cur_bbw, 2),
        "vol_dry":       vol_dry,
        "nr7":           nr7,
        "price":         round(float(close_series.iloc[-1]), 2),
        "price_1w":      round(float(close_series.iloc[-6]), 2) if len(close_series) >= 6 else None,
    }

# ── 3. Earnings dátum lekérés ────────────────────────────────
def get_earnings_days_away(ticker):
    """Hány nap múlva van az earnings? None ha nem elérhető."""
    try:
        info = yf.Ticker(ticker).info
        ed   = info.get("earningsDate") or info.get("earningsTimestamp")
        if not ed:
            return None
        if isinstance(ed, (list, tuple)):
            ed = ed[0]
        if isinstance(ed, (int, float)):
            ed = datetime.datetime.fromtimestamp(ed)
        elif isinstance(ed, str):
            ed = pd.Timestamp(ed)
        ed = pd.Timestamp(ed).tz_localize(None) if hasattr(ed, "tz") and ed.tz else pd.Timestamp(ed)
        days = (ed - pd.Timestamp(datetime.datetime.now())).days
        return days if 0 <= days <= 90 else None
    except Exception:
        return None

# ── 4. EPS revízió irány ─────────────────────────────────────
def get_eps_revision(ticker):
    """
    EPS revízió: pozitív = elemzők felfelé revideáltak
    Forrás: yfinance eps_trend (current vs 60d ago)
    """
    try:
        trend = yf.Ticker(ticker).eps_trend
        if trend is None or trend.empty:
            return 0, "n/a"
        current = float(trend.loc["0q", "current"]) if "0q" in trend.index and "current" in trend.columns else None
        ago60   = float(trend.loc["0q", "60daysAgo"]) if "0q" in trend.index and "60daysAgo" in trend.columns else None
        if current is None or ago60 is None or ago60 == 0:
            return 0, "n/a"
        revision_pct = round((current - ago60) / abs(ago60) * 100, 1)
        direction = "UP" if revision_pct > 3 else "DOWN" if revision_pct < -3 else "FLAT"
        return revision_pct, direction
    except Exception:
        return 0, "n/a"

# ── 5. Sector leader beat detektálás ─────────────────────────
def detect_sector_beats(prices_data):
    """
    Ha egy részvény az utolsó 5 napban >12%-ot mozgott egy nap alatt
    ÉS az átlagosnál 3× nagyobb volt a volumen = valószínűleg earnings beat
    """
    beats = {}
    for ticker, data in prices_data.items():
        try:
            close  = data["close"].tail(10)
            volume = data["volume"].tail(10)
            daily_ret = close.pct_change().dropna()
            vol_ratio = (volume / volume.rolling(10).mean()).dropna()

            for i in range(len(daily_ret)):
                ret = float(daily_ret.iloc[i])
                vr  = float(vol_ratio.iloc[i]) if i < len(vol_ratio) else 0
                if ret > 0.12 and vr > 2.5:  # >12% mozgás + 2.5× volume
                    day = str(daily_ret.index[i])[:10]
                    beats[ticker] = {"date": day, "move": round(ret*100, 1)}
                    break
        except Exception:
            continue
    return beats

# ── 6. Sympathy continuation check ─────────────────────────
def check_sympathy_hold(ticker, prices_data, beat_day, beat_move_pct):
    """
    Sympathy check: ha egy peer +5%-ot ment a sector leader earnings napján,
    és az utóbbi 2 napban nem adta vissza >50%-át → continuation setup
    """
    try:
        data  = prices_data.get(ticker)
        if not data:
            return False, 0
        close = data["close"].tail(10)
        beat  = pd.Timestamp(beat_day)
        post  = close[close.index > beat]
        if len(post) < 2:
            return False, 0
        # Sympathy nap hozama
        symp_ret = float((post.iloc[0] - close[close.index <= beat].iloc[-1])
                         / close[close.index <= beat].iloc[-1] * 100)
        if symp_ret < 3:
            return False, 0
        # Megtartotta-e a gain >60%-át?
        current = float(post.iloc[-1])
        base    = float(close[close.index <= beat].iloc[-1])
        actual_gain = (current - base) / base * 100
        held_pct = actual_gain / symp_ret * 100 if symp_ret > 0 else 0
        held = held_pct > 60
        return held, round(symp_ret, 1)
    except Exception:
        return False, 0

# ── FŐ FUTÁS ─────────────────────────────────────────────────
def run_scanner():
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"  Squeeze Scanner – {today}")
    print(f"{'='*60}\n")

    state = load_state()

    # 1. Ár letöltés
    prices = download_prices(UNIVERSE, period="1y")
    log(f"Adat: {len(prices)}/{len(UNIVERSE)} ticker OK\n")

    # 2. Sector beat detektálás
    sector_beats = detect_sector_beats(prices)
    if sector_beats:
        log(f"Sector beats az utolsó 5 napban: {list(sector_beats.keys())}")

    results = []

    # 3. Squeeze scan
    log("BB Squeeze számítás...")
    squeeze_stocks = []
    for ticker, data in prices.items():
        sq = calc_squeeze_score(data["close"], data["volume"])
        if sq and sq["squeeze_score"] >= 60:  # csak erős squeeze
            squeeze_stocks.append((ticker, sq))

    squeeze_stocks.sort(key=lambda x: x[1]["squeeze_score"], reverse=True)
    log(f"Squeeze jelöltek (score≥60): {len(squeeze_stocks)}")

    # 4. Earnings + EPS revízió szűrés
    log("Earnings dátum + EPS revízió check...")
    for ticker, sq in squeeze_stocks[:80]:  # top 80 squeeze-ből
        try:
            days = get_earnings_days_away(ticker)
            if days is None or not (EARNINGS_MIN <= days <= EARNINGS_MAX):
                continue

            eps_rev, eps_dir = get_eps_revision(ticker)
            time.sleep(0.15)  # rate limit

            # Besorolás
            if eps_dir == "UP" and sq["squeeze_score"] >= 70:
                rating = "STRONG"
            elif eps_dir == "DOWN":
                rating = "AVOID"
            elif sq["squeeze_score"] >= 75:
                rating = "WATCH"
            else:
                rating = "WATCH"

            # Szektorhoz tartozás
            sector = next((s for s, tickers in SECTORS.items()
                          if ticker in tickers), "egyéb")

            results.append({
                "ticker":        ticker,
                "rating":        rating,
                "sector":        sector,
                "squeeze_score": sq["squeeze_score"],
                "bbw_pct":       sq["bbw_pct"],
                "vol_dry":       sq["vol_dry"],
                "nr7":           sq["nr7"],
                "price":         sq["price"],
                "price_1w":      sq["price_1w"],
                "earnings_days": days,
                "eps_revision":  eps_rev,
                "eps_direction": eps_dir,
                "type":          "earnings_setup",
            })
        except Exception as e:
            continue

    # 5. Sympathy continuation
    log("Sympathy continuation check...")
    for beat_ticker, beat_info in sector_beats.items():
        beat_sector = next((s for s, ticks in SECTORS.items()
                           if beat_ticker in ticks), None)
        if not beat_sector:
            continue
        peers = [t for t in SECTORS[beat_sector] if t != beat_ticker]
        for peer in peers:
            if peer not in prices:
                continue
            held, symp_ret = check_sympathy_hold(
                peer, prices, beat_info["date"], beat_info["move"])
            if not held or symp_ret < 3:
                continue
            # Van-e saját earnings közel?
            days = get_earnings_days_away(peer)
            eps_rev, eps_dir = get_eps_revision(peer)
            time.sleep(0.1)

            sq = calc_squeeze_score(prices[peer]["close"], prices[peer]["volume"])
            results.append({
                "ticker":        peer,
                "rating":        "SYMPATHY",
                "sector":        beat_sector,
                "squeeze_score": sq["squeeze_score"] if sq else 50,
                "bbw_pct":       sq["bbw_pct"] if sq else 50,
                "vol_dry":       sq["vol_dry"] if sq else False,
                "nr7":           False,
                "price":         sq["price"] if sq else 0,
                "price_1w":      sq["price_1w"] if sq else 0,
                "earnings_days": days,
                "eps_revision":  eps_rev,
                "eps_direction": eps_dir,
                "type":          "sympathy",
                "catalyst":      beat_ticker,
                "catalyst_move": beat_info["move"],
                "sympathy_held": symp_ret,
            })

    # 6. Rendezés
    priority = {"STRONG": 0, "SYMPATHY": 1, "WATCH": 2, "AVOID": 3}
    results.sort(key=lambda x: (priority.get(x["rating"], 4),
                                 -x["squeeze_score"]))

    # 7. Mentés
    output = {"date": today, "results": results,
               "count": len(results), "sector_beats": sector_beats}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    log(f"\nMentve: {OUTPUT_FILE} ({len(results)} jelölt)")

    # 8. NTFY alert
    strong  = [r for r in results if r["rating"] == "STRONG"]
    sympathy = [r for r in results if r["rating"] == "SYMPATHY"]
    watch   = [r for r in results if r["rating"] == "WATCH"]
    avoid   = [r for r in results if r["rating"] == "AVOID"]

    if not results:
        send_ntfy("Squeeze Scanner", f"Nincs setup ma ({today}). Piac: csendes.", "min")
        return

    lines = [f"SQUEEZE + EARNINGS SETUP – {today}\n"]

    if strong:
        lines.append("STRONG (EPS rev + squeeze):")
        for r in strong[:4]:
            ep = f"E:{r['earnings_days']}n" if r.get("earnings_days") else ""
            lines.append(f"  {r['ticker']:6} ${r['price']} | "
                        f"Squeeze:{r['squeeze_score']} | "
                        f"EPS:{r['eps_revision']:+.0f}% | {ep}")

    if sympathy:
        lines.append("\nSYMPATHY (2. hullam):")
        for r in sympathy[:3]:
            lines.append(f"  {r['ticker']:6} ${r['price']} | "
                        f"Catalyst: {r.get('catalyst','')} "
                        f"+{r.get('catalyst_move',0):.0f}% | "
                        f"Tartja: +{r.get('sympathy_held',0):.1f}%")

    if watch:
        tickers = ", ".join(r["ticker"] for r in watch[:5])
        lines.append(f"\nWATCH: {tickers}")

    if avoid:
        tickers = ", ".join(r["ticker"] for r in avoid[:3])
        lines.append(f"AVOID (EPS ↓): {tickers}")

    lines.append(f"\n* irany ismeretlen, csak a mozgas valoszinusege magas")

    msg = "\n".join(lines)
    priority_level = "high" if strong or sympathy else "default"
    send_ntfy("Squeeze Scanner", msg, priority_level)

    # Összefoglaló
    print(f"\n  STRONG: {len(strong)} | SYMPATHY: {len(sympathy)} | "
          f"WATCH: {len(watch)} | AVOID: {len(avoid)}")
    if strong:
        print("  TOP STRONG:")
        for r in strong[:5]:
            print(f"    {r['ticker']:6} | Score:{r['squeeze_score']} | "
                  f"EPS:{r['eps_revision']:+.0f}% | "
                  f"Earnings: {r.get('earnings_days','?')} nap")

    save_state(state)

if __name__ == "__main__":
    run_scanner()
