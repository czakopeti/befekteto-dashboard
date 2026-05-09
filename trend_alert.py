#!/usr/bin/env python3
"""
trend_alert.py v2 – Havi AF Fordulat Alert
============================================
BACKTEST EREDMENYEK ALAPJAN FRISSITVE (1164 signal, 2005-2025):

Legfontosabb tanulsagok:
- Havi AF fordulat: 68% win rate 12 honapra (erős edge)
- LEGJOBB lila periodus: 4-8 ho (78% win, avg +21.9%)
- 9+ ho lila GYENGEBB (64% win) – strukturalisan serult cegek keverednek
- Rovid lila (1-3 ho): 76% win – gyors korrekciok utan is jó
- Makro szuro: dashboard score >=55 (NEM SPX SMA200 – az lagging)
- 2007/2008/2021/2022 kudarcok mind dashboard score-al kiszurhatok

Kategoriák (backtest alapjan):
  EROS    = 4-8 ho lila + EPS UP → legjobb arany szoru (78% win)
  FIGYEL  = 1-3 ho lila + EPS UP → szintén jo (76% win)
  OVATOSH = 9+ ho lila + EPS UP → gyengebb, hosszu konszolidacio utan (64% win)
  KORAI   = AF meg lila de kozelit 0-hoz → elore jelzes
  MEGEROSITES = 1-2 honapja fordult, erosodik
"""

import os, json, time, datetime, requests
import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path

NTFY_TOPIC      = os.environ.get("NTFY_TOPIC", "")
MACRO_THRESHOLD = 55
OUTPUT_FILE     = "trend_results.json"
HISTORY_FILE    = "history.json"

UNIVERSE = [
    # Peter portfolioja
    "TSLA","CRWD","DDOG","MSFT","AAPL","NVDA","IONQ","MA","MSTR","AMZN",
    # Semiconductor
    "AMD","INTC","MU","AVGO","QCOM","AMAT","LRCX","KLAC","MRVL","ON","TXN",
    # Cloud/AI
    "META","GOOGL","CRM","NOW","ORCL","WDAY","SNOW","NET","PLTR","HUBS","ADBE",
    # Cybersecurity
    "PANW","ZS","FTNT","OKTA","S","CHKP","TENB","CYBR",
    # Space/Defense
    "RKLB","LUNR","ASTS","KTOS","RTX","LMT","AXON",
    # Biotech
    "LLY","ABBV","REGN","VRTX","GILD","AMGN",
    # Fintech
    "PYPL","COIN","AFRM","SOFI","HOOD","NU","TOST","V","AXP",
    # S&P500 core
    "JPM","UNH","HD","PG","JNJ","COST","MRK","BAC","ACN",
    "TMO","ABT","MCD","LIN","GE","DHR","CAT","ISRG","INTU",
    "BKNG","GS","BLK","SYK","SBUX","TJX","MDT","MMC","CB","AMT",
    "LOW","TGT","ROST","NFLX","SPGI","CME","ZTS",
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))

def log(msg): print(f"  {msg}")

def send_ntfy(title, message, priority="default"):
    import re
    t = re.sub(r'[^\x00-\x7F]+', '', title).strip() or "Trend Alert"
    if not NTFY_TOPIC:
        print(f"[NTFY] {t}: {message[:120]}")
        return
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": t, "Priority": priority,
                     "Content-Type": "text/plain; charset=utf-8"}, timeout=10)
        print(f"[NTFY OK] {t}")
    except Exception as e:
        print(f"[NTFY HIBA] {e}")

def get_macro_score():
    try:
        if Path(HISTORY_FILE).exists():
            with open(HISTORY_FILE) as f:
                hist = json.load(f)
            if hist:
                score = hist[-1].get("entryScore", 0)
                log(f"Dashboard score: {score}/100")
                return score
    except Exception as e:
        log(f"Score olvasas hiba: {e}")
    return 50

def classify_lila(lila_months):
    """
    Backtest szerinti kategoriak:
    4-8 ho: EROS (78% win, avg +21.9%)
    1-3 ho: FIGYEL (76% win, avg +20.6%)
    9+ ho:  OVATOS (64% win, avg +11.2%)
    """
    if 4 <= lila_months <= 8:
        return "EROS", "78% win hist."
    elif 1 <= lila_months <= 3:
        return "FIGYEL", "76% win hist."
    else:
        return "OVATOS", "64% win hist. – hosszu konszolidacio"

def calc_monthly_af(ticker):
    try:
        h = yf.Ticker(ticker).history(
            period="10y", interval="1mo", auto_adjust=True)
        if h.empty or len(h) < 50:
            return None
        h.index = h.index.tz_localize(None) if h.index.tz else h.index
        close = h["Close"].dropna()
        def trix(s, n):
            e1 = s.ewm(span=n, adjust=False).mean()
            e2 = e1.ewm(span=n, adjust=False).mean()
            e3 = e2.ewm(span=n, adjust=False).mean()
            return ((e3 - e3.shift(1)) / e3.shift(1) * 100).fillna(0)
        return (trix(close, 18) - trix(close, 6)).dropna()
    except Exception:
        return None

def get_eps_revision(ticker):
    try:
        trend = yf.Ticker(ticker).eps_trend
        if trend is None or trend.empty:
            return 0, "n/a"
        cur = float(trend.loc["0q","current"]) if "0q" in trend.index else None
        ago = float(trend.loc["0q","60daysAgo"]) if "0q" in trend.index else None
        if cur is None or ago is None or ago == 0:
            return 0, "n/a"
        rev = round((cur - ago) / abs(ago) * 100, 1)
        return rev, ("UP" if rev > 3 else "DOWN" if rev < -3 else "FLAT")
    except Exception:
        return 0, "n/a"

def scan_af_crossings():
    results = {"fordulat": [], "korai": [], "megerosites": []}
    log(f"AF scan: {len(UNIVERSE)} reszvenyen...")

    for i, ticker in enumerate(UNIVERSE):
        try:
            af = calc_monthly_af(ticker)
            if af is None or len(af) < 4:
                continue

            cur  = float(af.iloc[-1])
            prv  = float(af.iloc[-2])
            prv2 = float(af.iloc[-3])

            price_h = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
            if price_h.empty:
                continue
            price = round(float(price_h["Close"].iloc[-1]), 2)

            # Lila periodus hossza
            lila_m = 0
            for j in range(len(af)-2, max(0, len(af)-24), -1):
                if float(af.iloc[j]) < 0:
                    lila_m += 1
                else:
                    break

            category, hist_note = classify_lila(lila_m)

            base = {
                "ticker":     ticker,
                "price":      price,
                "af_cur":     round(cur, 4),
                "af_prv":     round(prv, 4),
                "lila_months":lila_m,
                "category":   category,
                "hist_note":  hist_note,
            }

            # FORDULAT: ez az eles jel
            if cur > 0 and prv <= 0:
                results["fordulat"].append(base)

            # KORAI: meg lila de gyorsan javul, nulla koz
            elif cur < 0 and cur > prv and cur > -0.08 and prv2 < prv:
                base["zero_dist"] = round(abs(cur), 4)
                results["korai"].append(base)

            # MEGEROSITES: 1-2 honapja fordult, erosodik
            elif cur > 0 and prv > 0 and prv2 <= 0 and cur > prv:
                results["megerosites"].append(base)

            if i % 25 == 24:
                log(f"  {i+1}/{len(UNIVERSE)}...")
            time.sleep(0.1)

        except Exception:
            continue

    return results

def run_trend_alert():
    today = datetime.date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*55}")
    print(f"  Havi AF Fordulat Alert v2 – {today}")
    print(f"  (Backtest: 1164 signal, 2005-2025)")
    print(f"{'='*55}\n")

    # 1. Makro check
    macro_score = get_macro_score()
    macro_ok = macro_score >= MACRO_THRESHOLD

    if not macro_ok:
        log(f"Score {macro_score} < {MACRO_THRESHOLD} – makro tiltja a belepest")
        send_ntfy("Trend Alert",
            f"Macro score {macro_score}/100 (kell: {MACRO_THRESHOLD}+). "
            f"AF fordulatokat figyelmen kivul hagyjuk.", "min")
        return

    log(f"Makro OK ({macro_score}/100)")

    # 2. Scan
    results = scan_af_crossings()
    fordulat    = results["fordulat"]
    korai       = results["korai"]
    megerosites = results["megerosites"]

    log(f"Fordulat: {len(fordulat)} | Korai: {len(korai)} | "
        f"Megerosites: {len(megerosites)}")

    # 3. EPS revizio
    for r in fordulat:
        eps_rev, eps_dir = get_eps_revision(r["ticker"])
        r["eps_rev"] = eps_rev
        r["eps_dir"] = eps_dir
        time.sleep(0.1)
        cat = r["category"]
        note = r["hist_note"]
        print(f"  {r['ticker']:6} | {r['af_prv']:+.3f}->{r['af_cur']:+.3f} | "
              f"{r['lila_months']}ho lila | {cat} ({note}) | EPS:{eps_dir}")

    # 4. Csoportositas prioritas szerint
    eros     = [r for r in fordulat if r["category"]=="EROS" and r.get("eps_dir")!="DOWN"]
    figyel   = [r for r in fordulat if r["category"]=="FIGYEL" and r.get("eps_dir")!="DOWN"]
    ovatos   = [r for r in fordulat if r["category"]=="OVATOS" and r.get("eps_dir")!="DOWN"]
    avoid    = [r for r in fordulat if r.get("eps_dir")=="DOWN"]

    # 5. Mentes
    with open(OUTPUT_FILE, "w") as f:
        json.dump({
            "date": today, "macro_score": macro_score,
            "eros": eros, "figyel": figyel,
            "ovatos": ovatos, "avoid": avoid,
            "korai": korai[:10], "megerosites": megerosites[:10],
        }, f, indent=2)

    # 6. NTFY
    if not fordulat and not korai:
        send_ntfy("Trend Alert",
            f"Nincs uj AF fordulat. Score: {macro_score}/100. "
            f"Korai: {len(korai)}", "min")
        return

    lines = [f"HAVI AF FORDULAT – {today} | Score: {macro_score}/100\n"]

    if eros:
        lines.append("EROS (4-8 ho lila, hist.78% win):")
        for r in eros[:4]:
            eps = f"EPS:{r.get('eps_dir','?')}"
            lines.append(f"  {r['ticker']:6} ${r['price']} | "
                        f"AF:{r['af_prv']:+.3f}->{r['af_cur']:+.3f} | "
                        f"{r['lila_months']}ho | {eps}")

    if figyel:
        lines.append("\nFIGYEL (1-3 ho lila, hist.76% win):")
        for r in figyel[:3]:
            lines.append(f"  {r['ticker']:6} ${r['price']} | "
                        f"{r['lila_months']}ho | EPS:{r.get('eps_dir','?')}")

    if ovatos:
        tickers = ", ".join(r["ticker"] for r in ovatos[:4])
        lines.append(f"\nOVATOS (9+ ho lila, hist.64% win): {tickers}")

    if korai:
        tickers = ", ".join(r["ticker"] for r in korai[:5])
        lines.append(f"KORAI (0 kozelit): {tickers}")

    if megerosites:
        tickers = ", ".join(r["ticker"] for r in megerosites[:4])
        lines.append(f"MEGEROSITES: {tickers}")

    if avoid:
        tickers = ", ".join(r["ticker"] for r in avoid[:3])
        lines.append(f"AVOID (EPS le): {tickers}")

    lines.append(f"\n* Tarts 6-12 honapig | Stop: -15% a belepestol")

    msg = "\n".join(lines)
    priority = "high" if eros else "default" if figyel else "min"
    send_ntfy("Trend Alert", msg, priority)

    print(f"\n  EROS:{len(eros)} FIGYEL:{len(figyel)} "
          f"OVATOS:{len(ovatos)} AVOID:{len(avoid)} "
          f"KORAI:{len(korai)} MEGEROSITES:{len(megerosites)}")

if __name__ == "__main__":
    run_trend_alert()
