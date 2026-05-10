#!/usr/bin/env python3
"""
options_update.py v2 – Opciós Dashboard
========================================
Javítások (review alapján):
  - IV Percentile (robusztusabb mint IV Rank)
  - GEX bekötése history.json-ból
  - SKEW helyes értelmezése (nem "bear" hanem "put drága")
  - Backwardation banner
  - Safe wrapper minden hívásra
  - Strategy Suggester: GEX + backwardation erőssége is benne
  - Döntési mátrix szöveggel
"""

import os, json, datetime, time, random
import yfinance as yf
import pandas as pd
import numpy as np
from pathlib import Path

OUTPUT_HTML  = "options.html"
IV_HIST_FILE = "iv_history.json"
HISTORY_FILE = "history.json"

WATCHLIST = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA",
    "CRWD","DDOG","PLTR","RKLB","IONQ","MA","MSTR",
    "AMD","INTC","MU","AVGO","NFLX","ADBE","ORCL",
    "NOW","CRM","FTNT","PANW","ZS","LLY","TMO","TJX",
]

def log(msg): print(f"  {msg}")

def safe(fn, fallback, label=""):
    try:
        return fn()
    except Exception as e:
        log(f"HIBA ({label}): {str(e)[:60]}")
        return fallback

def load_json(path):
    try:
        return json.loads(Path(path).read_text()) if Path(path).exists() else {}
    except Exception:
        return {}

def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str))

# ── DASHBOARD STATE ──────────────────────────────────────────
def get_dashboard_state():
    hist = load_json(HISTORY_FILE)
    if hist and isinstance(hist, list) and hist:
        l = hist[-1]
        return {
            "score":    l.get("entryScore", 50),
            "playbook": l.get("playbook", "WAIT"),
            "date":     l.get("date", "?"),
            "gex":      l.get("gex", 5),        # GEX a history-ból
            "af_bear":  l.get("afBear", True),  # AF lila-e
        }
    return {"score": 50, "playbook": "WAIT", "date": "?", "gex": 5, "af_bear": False}

# ── VIX TERM STRUCTURE ───────────────────────────────────────
def fetch_vix_term():
    def _fetch():
        tickers = {"VIX":"^VIX", "VIX3M":"^VIX3M", "VIX6M":"^VIX6M"}
        vals = {}
        for name, sym in tickers.items():
            h = yf.Ticker(sym).history(period="1y")
            if not h.empty:
                vals[name]              = round(float(h["Close"].iloc[-1]), 2)
                vals[f"{name}_52w_hi"]  = round(float(h["Close"].max()), 2)
                vals[f"{name}_52w_lo"]  = round(float(h["Close"].min()), 2)
                # IV Percentile: napok %-a ahol IV < jelenlegi
                cur = vals[name]
                vals[f"{name}_pct"] = round((h["Close"] < cur).mean() * 100)

        v  = vals.get("VIX", 20)
        v3 = vals.get("VIX3M", 22)
        v6 = vals.get("VIX6M", 23)
        r3 = round(v / v3, 3) if v3 > 0 else 1.0
        r6 = round(v / v6, 3) if v6 > 0 else 1.0

        backwardation       = r3 > 1.0
        strong_backwardation = r3 > 1.10

        vix_lo = vals.get("VIX_52w_lo", 12)
        vix_hi = vals.get("VIX_52w_hi", 40)
        vix_rank = round((v - vix_lo) / (vix_hi - vix_lo) * 100) if vix_hi > vix_lo else 50
        vix_pct  = vals.get("VIX_pct", 50)

        # IV drága vagy olcsó (Percentile alapján – robusztusabb)
        iv_cheap = vix_pct < 30
        iv_rich  = vix_pct > 70

        if strong_backwardation:
            term_desc = f"ERŐS BACKWARDATION ({r3:.3f}) – PÁNIK! Minden long stratégia veszélyes"
            term_sig  = "strong_back"
        elif backwardation:
            term_desc = f"Backwardation ({r3:.3f}) – azonnali félelem, kerüld az új pozíciókat"
            term_sig  = "backwardation"
        elif r3 < 0.88:
            term_desc = f"Erős Contango ({r3:.3f}) – piac teljesen nyugodt"
            term_sig  = "contango"
        else:
            term_desc = f"Enyhe Contango ({r3:.3f}) – normál állapot"
            term_sig  = "normal"

        vals.update({
            "ratio_3m": r3, "ratio_6m": r6,
            "vix_rank": vix_rank, "vix_pct": vix_pct,
            "iv_cheap": iv_cheap, "iv_rich": iv_rich,
            "backwardation": backwardation,
            "strong_back": strong_backwardation,
            "term_desc": term_desc, "term_sig": term_sig,
        })
        return vals

    return safe(_fetch, {
        "VIX":20,"VIX3M":22,"VIX6M":23,"ratio_3m":0.9,
        "vix_rank":40,"vix_pct":40,"iv_cheap":False,"iv_rich":False,
        "backwardation":False,"strong_back":False,
        "term_desc":"Nincs adat","term_sig":"normal"
    }, "VIX term")

# ── SKEW ─────────────────────────────────────────────────────
def fetch_skew():
    def _fetch():
        h = yf.Ticker("^SKEW").history(period="1y")
        if h.empty:
            return None
        cur  = round(float(h["Close"].iloc[-1]), 1)
        hi   = round(float(h["Close"].max()), 1)
        lo   = round(float(h["Close"].min()), 1)
        pct  = round((h["Close"] < cur).mean() * 100)

        if cur > 145:
            # FONTOS: nem "bear" hanem "put eladás lehetőség"
            sig  = "put_expensive"
            desc = (f"Extrém ({cur}) – OTM putok nagyon drágák! "
                    f"Intézmények védekeznek. Credit spread / Ratio spread jó lehet.")
            trade_hint = "Put Spread eladás: az OTM put prémium extrém magas → Credit spread kedvező"
        elif cur > 135:
            sig  = "elevated"
            desc = f"Magas ({cur}) – tail risk nőtt, a put védelmi prémium megemelkedett"
            trade_hint = "Magas put prémium → Covered Put eladás/Credit spread mérlegelhető"
        elif cur > 120:
            sig  = "normal"
            desc = f"Normál ({cur}) – nincs különleges félelem"
            trade_hint = "Normál piaci állapot"
        else:
            sig  = "low"
            desc = f"Alacsony ({cur}) – önelégültség, put védelmet érdemes venni"
            trade_hint = "Olcsó put → biztosítást venni érdemes"

        return {
            "skew": cur, "skew_hi": hi, "skew_lo": lo,
            "skew_pct": pct, "skew_sig": sig,
            "skew_desc": desc, "trade_hint": trade_hint,
        }
    return safe(_fetch, {
        "skew":130,"skew_hi":150,"skew_lo":110,"skew_pct":50,
        "skew_sig":"normal","skew_desc":"Nincs adat","trade_hint":"–"
    }, "SKEW")

# ── IV TRACKER – Percentile alapú ────────────────────────────
def update_iv_history():
    iv_hist = load_json(IV_HIST_FILE)
    if not isinstance(iv_hist, dict):
        iv_hist = {}
    today = datetime.date.today().isoformat()
    result = {}

    for i, ticker in enumerate(WATCHLIST):
        def _fetch_iv(t=ticker):
            info  = yf.Ticker(t).info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if not price:
                return None
            exps = yf.Ticker(t).options
            if not exps:
                return None
            chain  = yf.Ticker(t).option_chain(exps[0])
            calls  = chain.calls
            atm_k  = min(calls["strike"], key=lambda x: abs(x - price))
            row    = calls[calls["strike"] == atm_k]
            if row.empty:
                return None
            return round(float(row["impliedVolatility"].iloc[0]) * 100, 1)

        iv = safe(_fetch_iv, None, f"IV/{ticker}")
        if iv:
            if ticker not in iv_hist:
                iv_hist[ticker] = {}
            iv_hist[ticker][today] = iv

            hist_vals = sorted(iv_hist[ticker].values())
            n = len(hist_vals)

            if n >= 8:
                # IV Percentile: napok %-a ahol IV < jelenlegi (robusztusabb)
                iv_pct  = round(sum(1 for v in hist_vals if v < iv) / n * 100)
                iv_rank = round((iv - hist_vals[0]) / (hist_vals[-1] - hist_vals[0]) * 100) if hist_vals[-1] > hist_vals[0] else 50
                iv_cheap = iv_pct < 30
                iv_rich  = iv_pct > 70
                iv_beta  = False
            elif n >= 3:
                # Fallback: IV Rank (béta – kevés adat)
                iv_rank = round((iv - hist_vals[0]) / (hist_vals[-1] - hist_vals[0]) * 100) if hist_vals[-1] > hist_vals[0] else 50
                iv_pct  = iv_rank  # proxy
                iv_cheap = iv_rank < 30
                iv_rich  = iv_rank > 70
                iv_beta  = True   # jelöljük hogy béta adat
            else:
                iv_pct = iv_rank = None
                iv_cheap = iv_rich = iv_beta = False

            result[ticker] = {
                "iv": iv, "iv_pct": iv_pct, "iv_rank": iv_rank,
                "iv_cheap": iv_cheap, "iv_rich": iv_rich,
                "iv_beta": iv_beta if n >= 3 else False,
                "weeks": n,
            }

        time.sleep(random.uniform(0.3, 0.7))
        if (i+1) % 10 == 0:
            log(f"  {i+1}/{len(WATCHLIST)} IV kész")

    save_json(IV_HIST_FILE, iv_hist)
    return result

# ── IMPLIED MOVE ─────────────────────────────────────────────
def calc_implied_move(ticker):
    def _fetch(t=ticker):
        info  = yf.Ticker(t).info or {}
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None
        ed = info.get("earningsDate")
        if isinstance(ed, (list, tuple)) and ed:
            ed = ed[0]
        if not ed:
            return None
        ed_ts = pd.Timestamp(ed)
        if hasattr(ed_ts, "tz") and ed_ts.tz:
            ed_ts = ed_ts.tz_localize(None)
        days = (ed_ts - pd.Timestamp.now()).days
        if not (1 <= days <= 35):
            return None

        exps = yf.Ticker(t).options
        if not exps:
            return None

        # Legközelebbi lejárat AZ EARNINGS UTÁN
        target = None
        for exp in exps:
            if pd.Timestamp(exp) >= ed_ts:
                target = exp
                break
        if not target:
            target = exps[0]

        chain = yf.Ticker(t).option_chain(target)
        calls = chain.calls
        puts  = chain.puts

        atm_k     = min(calls["strike"], key=lambda x: abs(x - price))
        call_row  = calls[calls["strike"] == atm_k]
        put_row   = puts[puts["strike"] == atm_k]
        if call_row.empty or put_row.empty:
            return None

        atm_call = float(call_row["lastPrice"].iloc[0])
        atm_put  = float(put_row["lastPrice"].iloc[0])
        straddle  = atm_call + atm_put
        impl_pct  = round(straddle / price * 100, 1)
        atm_iv    = round(float(call_row["impliedVolatility"].iloc[0]) * 100, 1)

        return {
            "ticker": t, "price": round(price, 2),
            "days": days, "exp": target,
            "impl_pct": impl_pct, "atm_iv": atm_iv,
            "straddle": round(straddle, 2),
        }
    return safe(_fetch, None, f"ImplMove/{ticker}")

# ── STRATEGY SUGGESTER v2 ────────────────────────────────────
def suggest_strategy(score, vix_pct, skew_sig, term_sig, gex_positive, vix_val=20):
    """
    Teljes döntési fa – több stratégia egyszerre, konkrét paraméterekkel.
    """
    strategies = []
    iv_cheap   = vix_pct < 30
    iv_rich    = vix_pct > 70
    iv_mid     = not iv_cheap and not iv_rich
    back       = term_sig in ("backwardation", "strong_back")
    strong_b   = term_sig == "strong_back"
    put_exp    = skew_sig in ("put_expensive", "elevated")
    gex_neg    = not gex_positive

    # Delta szint VIX alapján
    if vix_pct < 20:
        call_delta = "OTM (35-45 delta)"
        call_why   = "alacsony IV → gamma robbanás"
    elif vix_pct > 70:
        call_delta = "ITM (70-80 delta)"
        call_why   = "magas IV → theta védelme"
    else:
        call_delta = "ATM (50-65 delta)"
        call_why   = "normál volatilitás"

    EXIT_RULE = "Exit: 50% profit VAGY 21 DTE előtt · Stop: -50% prémium"

    # ── AZONNALI TILTÁS: Erős Backwardation ──────────────────
    if strong_b:
        return [{
            "name": "STOP – Ne nyiss új pozíciót",
            "desc": "Erős VIX Backwardation aktív. Minden long stratégia szünetel.",
            "params": "Ha van nyitott long: szoros stop-loss vagy azonnali zárás",
            "exit": "Amíg VIX/VIX3M ratio < 1.0 marad",
            "risk": "Korlátlan ha bent maradsz",
            "reward": "Tőke megőrzés", "rating": 5, "color": "#f04060",
        }]

    # ── MUST BUY (75+) ───────────────────────────────────────
    if score >= 75:
        if iv_cheap and not gex_neg:
            strategies.append({
                "name": "Long Call",
                "desc": f"Erős makro + olcsó opció = legjobb kombináció. {call_delta} ({call_why}).",
                "params": f"SPY/QQQ vagy top részvény · {call_delta} · 45-60 nap · Méret: max 5% portfólió",
                "exit": EXIT_RULE,
                "risk": "Prémium (alacsony)", "reward": "Korlátlan",
                "rating": 5, "color": "#00d488",
            })
            strategies.append({
                "name": "Bull Call Spread",
                "desc": "Konzervatívabb változat kisebb tőkeigénnyel.",
                "params": f"Buy ATM call · Sell 5-10% OTM call · Ugyanolyan lejárat (45 nap) · Méret: 3% portfólió",
                "exit": "Zárd 50% profit · Ha ATM közelit az alsó szárnyhoz: zárj",
                "risk": "Nettó debit (fix)", "reward": "Spread szélessége",
                "rating": 4, "color": "#00d488",
            })
        if iv_rich or iv_mid:
            strategies.append({
                "name": "Cash-Secured Put",
                "desc": "Bullish + magas prémium = ideális CSP. Ha lehívnak, jó áron veszed.",
                "params": f"Sell 5-8% OTM put · 20-30 nap lejárat · Cash fedezet teljes sztrájk értékre · Méret: max 10% portfólió",
                "exit": "Zárd 50% profit · Ha részvény sztrájk alá: rollover vagy átveszi",
                "risk": "Részvény vásárlás diszkonton", "reward": f"Prémium (IV%: {vix_pct}%)",
                "rating": 5 if iv_rich else 3, "color": "#00d488",
            })
        if put_exp:
            strategies.append({
                "name": "Ratio Bull Spread",
                "desc": f"SKEW magas → OTM put prémium extrém. Net credit stratégia.",
                "params": "Buy 1 ATM call · Sell 2 OTM call (20-25% feljebb) · 30 nap · Net credit szükséges",
                "exit": "Zárd ha ATM eléri az eladott callok szintjét",
                "risk": "Ha nagyon felmegy: veszteség a felső szárny felett",
                "reward": "Net credit (put skew-ból)", "rating": 3, "color": "#7dd3fc",
            })

    # ── ÓVATOS (50-74) ───────────────────────────────────────
    elif score >= 50:
        if iv_rich and not gex_neg:
            strategies.append({
                "name": "Iron Condor",
                "desc": f"Oldalazó piac + drága opció. GEX pozitív → biztonságos.",
                "params": f"Sell ±5-8% OTM call + put · Buy ±10-12% OTM védelem · 25-35 nap · Méret: 5% portfólió",
                "exit": "Zárd 50% profit · Ha VIX emelkedik 20%: azonnal zárd a vesztes szárnyat",
                "risk": "Belső szárnyak közt korlátolt", "reward": f"Nettó prémium (IV%: {vix_pct}%)",
                "rating": 5, "color": "#7dd3fc",
            })
            strategies.append({
                "name": "Covered Call",
                "desc": "Ha van részvényed: rendszeres prémium bevétel.",
                "params": "Sell 0.25-0.30 delta call · 20-30 nap · Minden lejárat után rollover · Méret: 1 call / 100 részvény",
                "exit": "Lejáratig tartsd vagy 80% profit esetén korai zárás",
                "risk": "Felső oldal lezárva a sztrájknál",
                "reward": "Havi ~1-2% bevétel", "rating": 4, "color": "#7dd3fc",
            })
        if iv_cheap and not gex_neg:
            strategies.append({
                "name": "Bull Call Spread (óvatos méret)",
                "desc": "Bizonytalan makro de olcsó opció. Kis méret indokolt.",
                "params": f"Buy ATM call · Sell OTM call · 45 nap · Méret: max 2% portfólió",
                "exit": EXIT_RULE,
                "risk": "Nettó debit (kis)", "reward": "Spread szélessége",
                "rating": 3, "color": "#f0a500",
            })
        if put_exp:
            strategies.append({
                "name": "Bull Put Spread (Credit Spread)",
                "desc": f"SKEW magas → OTM put prémium magas. Net credit fogadás hogy piac nem esik.",
                "params": f"Sell 5-8% OTM put · Buy 10-12% OTM put · 20-30 nap · Méret: 3-5% portfólió",
                "exit": "Zárd 50% profit · Ha alá kerül az eladott put: zárj",
                "risk": "Spread szélessége – prémium", "reward": "Net credit",
                "rating": 4, "color": "#7dd3fc",
            })

    # ── VÉDEKEZÉS (<50) ──────────────────────────────────────
    else:
        if iv_cheap:
            strategies.append({
                "name": "Protective Put – MOST VEGYÉL",
                "desc": "Gyenge makro + olcsó opció = filléres biztosítás. Most van alkalom.",
                "params": "SPY vagy QQQ · 5-10% OTM put · 60-90 nap · Méret: 1-2% portfólió = teljes védelem",
                "exit": "Tartsd amíg score 65+ vagy korrekcióig. Ha 200%+ profit: vegyed lejjebb",
                "risk": "Prémium (ALACSONY MOST!)", "reward": "Portfólió teljes védelme",
                "rating": 5, "color": "#f04060",
            })
            strategies.append({
                "name": "Long Put (SPY/QQQ)",
                "desc": "Közvetlen bearish fogadás az indexen. Alacsony IV = olcsó belépő.",
                "params": f"SPY/QQQ ATM put · {call_delta.replace('call','put')} · 30-60 nap · Méret: 2-3% portfólió",
                "exit": EXIT_RULE,
                "risk": "Prémium (alacsony most!)", "reward": "Korlátlan esésre",
                "rating": 4, "color": "#f04060",
            })
        elif iv_rich:
            strategies.append({
                "name": "Bear Put Spread",
                "desc": "Bearish bet de drága IV → spread csökkenti a prémiumot.",
                "params": "Buy ATM put · Sell 5-8% OTM put · 30 nap · Méret: 3% portfólió",
                "exit": "Zárd 50% profit · Ha visszamegy ATM fölé: zárj",
                "risk": "Nettó debit (spread – kisebb)", "reward": "Spread szélessége",
                "rating": 4, "color": "#f04060",
            })
        if gex_neg:
            strategies.append({
                "name": "Csökkentsd a long kitettséget",
                "desc": "GEX negatív: a market makerek eladni kénytelenek ha esik. Esések felerősödnek.",
                "params": "Csökkentsd a részvény pozíciókat 30-50%-kal · Iron Condor TILOS · Csak védekező stratégiák",
                "exit": "Amíg GEX negatív marad",
                "risk": "–", "reward": "Tőke megőrzés",
                "rating": 5, "color": "#f04060",
            })

    # Backwardation figyelmeztetés (ha nem strong)
    if back and not strong_b:
        strategies.insert(0, {
            "name": "Backwardation – Óvatos pozicionálás",
            "desc": "VIX > VIX3M: azonnali félelem. Csökkentett méret, csak prémium-gyűjtő stratégiák.",
            "params": "Ne nyiss új long spekulatív pozíciót · Covered Call és CSP rendben",
            "exit": "Amíg VIX/VIX3M < 1.0 marad",
            "risk": "Új long pozíciók veszélyesek", "reward": "–",
            "rating": 5, "color": "#f0a500",
        })

    # GEX negatív globális figyelmeztetés
    if gex_neg and not any("Iron Condor TILOS" in s.get("params","") for s in strategies):
        strategies.append({
            "name": "GEX Negatív – Iron Condor TILOS",
            "desc": "Negatív GEX = volatilitás amplifikátor. Oldalazó stratégiák veszélyesek.",
            "params": "Zárj minden Iron Condort · Csak directional vagy védekező stratégiák",
            "exit": "Amíg GEX pozitívba fordul",
            "risk": "Ha bent maradsz ICban: nagy veszteség", "reward": "–",
            "rating": 5, "color": "#f04060",
        })

    strategies.sort(key=lambda x: x["rating"], reverse=True)
    return strategies[:5]


    """
    Teljes döntési fa: score + IV Percentile + SKEW + Term Structure + GEX
    """
    strategies = []
    iv_cheap = vix_pct < 30
    iv_rich  = vix_pct > 70
    backwardation     = term_sig in ("backwardation", "strong_back")
    strong_back       = term_sig == "strong_back"
    put_expensive     = skew_sig in ("put_expensive", "elevated")
    gex_negative      = not gex_positive

    # ── BACKWARDATION TRUMP CARD ─────────────────────────────
    if strong_back:
        return [{
            "name": "🚨 STOP – Minden long stratégia szünetel",
            "desc": "Erős Backwardation: a piac MOST fél. Ne nyiss új opciós pozíciót. "
                    "Ha van nyitott long-od, fontold meg a zárást vagy stop-loss szigorítást.",
            "risk": "Korlátlan ha bent maradsz", "reward": "Tőke megőrzés",
            "rating": 5, "color": "#f04060",
        }, {
            "name": "Protective Put (ha még nincs)",
            "desc": "Ha eddig nem volt védelmed, most még nem késő OTM put-ot venni – "
                    "bár drága, de a védelem megéri a prémiumot.",
            "risk": "Magas prémium", "reward": "Portfólió védelem",
            "rating": 4, "color": "#f04060",
        }]

    if backwardation:
        strategies.append({
            "name": "Csökkentett méret / Várakozás",
            "desc": "Backwardation aktív. Amíg tart, kerüld az új long spekulatív pozíciókat. "
                    "Covered Call és Cash-Secured Put rendben, mert prémiumot KAPSZ.",
            "risk": "–", "reward": "Kisebb kockázat",
            "rating": 5, "color": "#f0a500",
        })

    # ── MUST BUY (75+) ───────────────────────────────────────
    if score >= 75:
        if iv_cheap and not gex_negative:
            strategies.append({
                "name": "Long Call",
                "desc": ("Ideális kombináció: erős makro + olcsó opció. "
                         + ("OTM 40-50 delta (gamma robbanás – alacsony IV)" if vix_pct < 20
                            else "ITM/ATM 60-75 delta (kisebb theta – magas IV)")
                         + ". 45-60 nap. Exit: 50% profit vagy 21 DTE."),
                "risk": "Prémium (alacsony most)", "reward": "Korlátlan",
                "rating": 5, "color": "#00d488",
            })
            strategies.append({
                "name": "Bull Call Spread",
                "desc": "Konzervatívabb változat: buy ATM call, sell OTM call ugyanolyan lejáratra. "
                        "Kisebb tőkeigény, korlátolt nyereség.",
                "risk": "Nettó debit", "reward": "Spread width",
                "rating": 4, "color": "#00d488",
            })
        elif iv_rich:
            strategies.append({
                "name": "Cash-Secured Put (CSP)",
                "desc": "Bullish bet + drága prémium = tökéletes CSP. "
                        "Adj el OTM put-ot support szinten. Ha lehívnak, jó áron veszed a részvényt.",
                "risk": "Részvény vásárlás kötelezettség", "reward": f"Magas prémium (IV: {vix_pct}%)",
                "rating": 5, "color": "#00d488",
            })
        if put_expensive:
            strategies.append({
                "name": "Ratio Bull Spread",
                "desc": f"SKEW magas – a put prémium extrém drága. "
                        "Buy 1 ATM call, sell 2 OTM call → net credit vagy kis debit. "
                        "Profitál ha a piac felfelé megy, de nem robbanásszerűen.",
                "risk": "Ha piac nagyon felmegy: veszteség a fölső szárnyak felett",
                "reward": "Net credit a put skew-ból",
                "rating": 3, "color": "#7dd3fc",
            })

    # ── ÓVATOS (50-74) ───────────────────────────────────────
    elif score >= 50:
        if iv_rich and not gex_negative:
            strategies.append({
                "name": "Iron Condor",
                "desc": "Oldalazó piac + drága opció = klasszikus Iron Condor. "
                        "Sell OTM call + sell OTM put, mindkettőre vegyél távolabb védelmet. "
                        "⚠ TILOS ha GEX negatív!",
                "risk": "Korlátolt (belső szárny)", "reward": f"Nettó prémium ({vix_pct}% IV)",
                "rating": 5 if not gex_negative else 1, "color": "#7dd3fc",
            })
            strategies.append({
                "name": "Covered Call",
                "desc": "Ha van részvényed: adj el ATM vagy OTM call-t ellene. "
                        "Magas IV = magas prémium bevétel havonta.",
                "risk": "Felső oldal lezárva", "reward": "Call prémium",
                "rating": 4, "color": "#7dd3fc",
            })
        elif iv_cheap:
            strategies.append({
                "name": "Kis Long Call (paper trade méretben)",
                "desc": "Bizonytalan makro + olcsó opció. Kis méretben long call rendben, "
                        "de ne tegyél fel sokat amíg a score nem megy 75 fölé.",
                "risk": "Kis prémium", "reward": "Korlátlan (kis méret)",
                "rating": 3, "color": "#f0a500",
            })
        if put_expensive:
            strategies.append({
                "name": "Bull Put Spread (Credit Spread)",
                "desc": f"SKEW {skew_sig}: az OTM put prémium magas. "
                        "Sell OTM put, buy mélyebb OTM put → net credit. "
                        "Bullish/semleges fogadás magas prémiummal.",
                "risk": "Spread width – prémium", "reward": "Net credit",
                "rating": 4, "color": "#7dd3fc",
            })

    # ── VÉDEKEZÉS (<50) ──────────────────────────────────────
    else:
        if iv_cheap:
            strategies.append({
                "name": "🛡️ Protective Put – MOST VEGYÉL!",
                "desc": "KRITIKUS: Gyenge makro + olcsó opció = filléres biztosítás. "
                        "Vegyél SPX vagy SPY OTM put-ot (5-10% OTM, 60-90 nap). "
                        "Amikor mindenki nyugodt (alacsony IV), a katasztrófa biztosítás olcsó.",
                "risk": "Kis prémium (most olcsó!)", "reward": "Teljes védelmet nyújt esnél",
                "rating": 5, "color": "#f04060",
            })
            strategies.append({
                "name": "Long Put (SPY/QQQ)",
                "desc": "Közvetlen bearish bet az indexen. OTM put 30-60 nap. "
                        "Alacsony IV = olcsó belépő a short pozícióhoz.",
                "risk": "Prémium (alacsony most!)", "reward": "Korlátlan esésre",
                "rating": 4, "color": "#f04060",
            })
        elif iv_rich:
            strategies.append({
                "name": "Bear Put Spread",
                "desc": "Bearish bet de IV drága → spread-del csökkentjük a prémiumot. "
                        "Buy ATM put, sell OTM put. Korlátolt de olcsóbb bearish pozíció.",
                "risk": "Nettó debit (kisebb)", "reward": "Spread width",
                "rating": 4, "color": "#f04060",
            })
        if gex_negative:
            strategies.append({
                "name": "Csökkentsd a részvény pozíciót",
                "desc": "GEX negatív + gyenge score = a market makerek eladni kénytelenek ha esik. "
                        "Az esések felerősödnek. Csökkentsd a long kitettséget.",
                "risk": "–", "reward": "Tőke megőrzés",
                "rating": 5, "color": "#f04060",
            })

    # GEX negatív figyelmeztetés (mindig)
    if gex_negative and not any(s["name"].startswith("Csökkentsd") for s in strategies):
        strategies.append({
            "name": "⚠ GEX Negatív – Iron Condor TILOS",
            "desc": "Negatív GEX rezsimben az oldalazó stratégiák (Iron Condor, Short Straddle) "
                    "veszélyesek mert az opciós piac felerősíti az árfolyammozgásokat.",
            "risk": "Ha bent vagy: zárj!", "reward": "Kockázat csökkentés",
            "rating": 5, "color": "#f04060",
        })

    strategies.sort(key=lambda x: x["rating"], reverse=True)
    return strategies[:5]

# ── DÖNTÉSI MÁTRIX SZÖVEG ────────────────────────────────────
def generate_decision_matrix(score, vix_pct, skew, term_sig, gex_positive):
    backwardation = term_sig in ("backwardation", "strong_back")
    iv_cheap = vix_pct < 30
    iv_rich  = vix_pct > 70

    rows = [
        # score_min, score_max, iv_cheap_ok, iv_rich_ok, gex_pos, back_ok, strategy, timing, action
        (75,100, True, False, True, False,
         "Long Call (ITM, 60-75 delta)",
         "Nyiss 45-60 napra, zárd 50% profit vagy 21 nap előtt",
         "Méret: max 5% portfólióból. Exit: -50% stop"),

        (75,100, False, True, True, False,
         "Cash-Secured Put (OTM, support szintjén)",
         "Adj el a következő erős support alá, 30 nap lejáratra",
         "Prémium legyen >1% pozíció értékének. Ha lehívnak: tartsd a részvényt"),

        (50,74, False, True, True, False,
         "Iron Condor (OTM call + OTM put eladás)",
         "Delta: ±15-20 szárnyak. Lejárat: 30-45 nap",
         "Zárd 50% profit. Ha VIX emelkedik: azonnal zárd a vesztes szárnyat"),

        (50,74, False, True, False, False,
         "Iron Condor TILOS ha GEX negatív!",
         "Várakozás vagy Covered Call csak",
         "GEX negatív = minden irányba nagy mozgás lehetséges"),

        (40,74, False, False, True, False,
         "Covered Call (ha van részvényed)",
         "Adj el OTM call-t 20-30 nap lejáratra, 0.30 delta körül",
         "Bevétel: kb 1-2%/hó. Ha fölé megy: hadd lehívják vagy görgesd"),

        (0,49, True, False, True, False,
         "Protective Put (SPY OTM, 5-10% OTM)",
         "Vegyél 60-90 nap lejáratra, mielőtt drága lesz",
         "Méret: 1-2% portfólióból = teljes védelmet nyújt"),

        (0,49, False, True, False, False,
         "Bear Put Spread (ATM put vétel + OTM put eladás)",
         "Ugyanolyan lejárat, 30-60 nap, 5-10% szélességű spread",
         "Max veszteség = nettó prémium. Max nyereség = spread - prémium"),

        (0,100, False, False, False, True,
         "NEM nyitunk új pozíciót",
         "Backwardation idején: várj amíg VIX/VIX3M visszamegy 1.0 alá",
         "Ha van nyitott pozíció: szoros stop-loss vagy azonnali zárás"),
    ]

    rows_html = ""
    for (smin, smax, ic, ir, gp, back, strat, timing, action) in rows:
        # Aktuálisan aktív sor kiemelése
        score_match = smin <= score <= smax
        iv_match    = (iv_cheap and ic) or (iv_rich and ir) or (not ic and not ir)
        gex_match   = (gex_positive == gp) or (gp is False and True)
        back_match  = (backwardation == back)
        active      = score_match and (iv_cheap == ic or iv_rich == ir)

        row_bg  = "background:#00d48815;border-left:4px solid #00d488;font-weight:600" if active else ""
        strat_c = "#00d488" if active else "var(--sub)"
        active_badge = '<span style="background:#00d48820;color:#00d488;font-size:8px;padding:2px 6px;border-radius:10px;margin-left:6px;font-family:var(--mono)">▶ AKTÍV</span>' if active else ''

        rows_html += f"""
        <tr style="{row_bg}">
          <td style="font-size:10px;color:var(--mut);font-family:var(--mono)">{smin}–{smax}</td>
          <td style="font-size:10px;color:var(--mut)">{"Olcsó" if ic else "Drága" if ir else "Bármely"}</td>
          <td style="font-size:10px;color:{strat_c};font-weight:{'700' if active else '400'}">{strat}{active_badge}</td>
          <td style="font-size:9px;color:var(--mut)">{timing}</td>
          <td style="font-size:9px;color:var(--mut)">{action}</td>
        </tr>"""
    return rows_html

# ── HTML GENERATOR ────────────────────────────────────────────NEW_FUNC = r'''
def generate_html(state, vix_data, skew_data, impl_moves, iv_data, strategies):
    import datetime as _dt
    today = _dt.datetime.now().strftime("%Y. %B %d.")
    score    = state["score"]
    playbook = state["playbook"]
    gex_pos  = state.get("gex", 5) > 0

    vix   = vix_data.get("VIX", 20)
    vix3m = vix_data.get("VIX3M", 22)
    vix6m = vix_data.get("VIX6M", 23)
    vpct  = vix_data.get("vix_pct", 50)
    vrank = vix_data.get("vix_rank", 50)
    skew_v  = skew_data.get("skew", 130)
    back    = vix_data.get("backwardation", False)
    strong_b = vix_data.get("strong_back", False)
    term_sig = vix_data.get("term_sig", "normal")

    score_c = "#00d488" if score >= 65 else "#f04060" if score < 40 else "#f0a500"
    vix_c   = "#f04060" if vpct > 70 else "#00d488" if vpct < 30 else "#f0a500"
    skew_c  = "#f04060" if skew_v > 145 else "#f0a500" if skew_v > 130 else "#00d488"
    term_c  = "#f04060" if back else "#00d488"
    gex_c   = "#00d488" if gex_pos else "#f04060"

    back_banner = ""
    if strong_b:
        back_banner = ('<div style="background:#f0406015;border:2px solid #f04060;border-radius:10px;'
            'padding:12px 16px;margin-bottom:16px;display:flex;align-items:center;gap:12px">'
            '<div style="font-size:22px">🚨</div>'
            '<div><div style="font-weight:700;color:#f04060;margin-bottom:3px">VIX BACKWARDATION AKTÍV</div>'
            '<div style="font-size:10px;color:#f08090">VIX > VIX3M – pánik az opciós piacon. Minden new long szünetel.</div>'
            '</div></div>')
    elif back:
        back_banner = ('<div style="background:#f0a50010;border:1px solid #f0a50040;border-radius:8px;'
            'padding:9px 14px;margin-bottom:12px;font-size:10px;color:#f0a500">'
            '⚠ <strong>Backwardation</strong> – óvatos pozicionálás.</div>')

    # VIX term bars
    maxv = max(vix, vix3m, vix6m) * 1.2
    tbars = ""
    for val, lbl in [(vix, "VIX\n30n"), (vix3m, "VIX3M\n3hó"), (vix6m, "VIX6M\n6hó")]:
        pct = round(val / maxv * 100)
        c = "#f04060" if val > 25 else "#f0a500" if val > 18 else "#00d488"
        tbars += (f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px">'
            f'<div style="font-size:13px;font-weight:700;color:{c};font-family:var(--mono)">{val}</div>'
            f'<div style="width:100%;height:50px;background:var(--c2);border-radius:4px;position:relative;overflow:hidden">'
            f'<div style="position:absolute;bottom:0;width:100%;height:{pct}%;background:{c}22;border-top:2px solid {c}"></div></div>'
            f'<div style="font-size:8px;color:var(--mut);text-align:center;font-family:var(--mono);white-space:pre">{lbl}</div></div>')

    # Dots helper
    def dots(level):
        level = max(1, min(5, level))
        out = ""
        for i in range(1, 6):
            c = ("#00d488" if i <= level else "var(--brd2)")
            out += f'<div style="width:8px;height:8px;border-radius:50%;background:{c}"></div>'
        return out

    vix_lvl  = round(vpct / 20) + 1
    skew_lvl = (1 if skew_v < 115 else 2 if skew_v < 125 else 3 if skew_v < 135 else 4 if skew_v < 145 else 5)
    term_lvl = (5 if back else 4 if vix_data.get("ratio_3m",0.9) > 0.97 else 3 if vix_data.get("ratio_3m",0.9) > 0.93 else 2)

    # Strategy cards
    sc_html = ""
    for idx, s in enumerate(strategies):
        stars = "★" * s["rating"] + "☆" * (5 - s["rating"])
        badge = (f'<span style="background:{s["color"]}20;color:{s["color"]};font-size:8px;'
                 f'padding:2px 7px;border-radius:99px;font-family:var(--mono);border:1px solid {s["color"]}40">▶ LEGJOBB MOST</span>'
                 if idx == 0 else "")
        sc_html += (f'<div style="background:var(--c2);border:1px solid var(--brd);'
            f'border-left:3px solid {s["color"]};border-radius:9px;padding:12px 14px;margin-bottom:8px">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">'
            f'<div style="font-size:12px;font-weight:700;color:{s["color"]};font-family:var(--mono)">{s["name"]}</div>'
            f'<div style="color:{s["color"]};font-size:10px;opacity:0.6">{stars}</div>{badge}</div>'
            f'<div style="font-size:10px;color:var(--sub);margin-bottom:6px;line-height:1.5">{s["desc"]}</div>'
            f'<div style="background:var(--bg2);border-radius:6px;padding:8px 10px;margin-bottom:6px;'
            f'font-size:9px;font-family:var(--mono);color:var(--sub);line-height:1.5">'
            f'<span style="color:var(--mut)">📋 </span>{s.get("params","–")}</div>'
            f'<div style="display:flex;gap:16px;font-size:9px;font-family:var(--mono);flex-wrap:wrap">'
            f'<span style="color:var(--mut)">Kockázat: <span style="color:#f04060">{s["risk"]}</span></span>'
            f'<span style="color:var(--mut)">Hozam: <span style="color:#00d488">{s["reward"]}</span></span></div>'
            f'<div style="margin-top:5px;font-size:9px;color:var(--mut);font-family:var(--mono)">'
            f'⏱ {s.get("exit","Exit: 50% profit vagy 21 DTE")}</div></div>')

    # Implied moves
    im_rows = ""
    for m in (impl_moves or []):
        c = "#f0a500" if m["impl_pct"] > 15 else "#4da6ff"
        im_rows += (f'<div style="display:grid;grid-template-columns:60px 70px 70px 60px 55px 70px;'
            f'gap:6px;padding:8px 10px;border-bottom:1px solid var(--brd);align-items:center;font-size:11px">'
            f'<div style="font-weight:700;color:var(--text);font-family:var(--mono)">{m["ticker"]}</div>'
            f'<div style="color:var(--sub);font-family:var(--mono)">${m["price"]:.2f}</div>'
            f'<div style="color:{c};font-weight:700;font-family:var(--mono)">±{m["impl_pct"]:.1f}%</div>'
            f'<div style="color:var(--mut)">{m["days"]} nap</div>'
            f'<div style="color:var(--sub);font-family:var(--mono)">{m["atm_iv"]:.0f}%</div>'
            f'<div style="color:var(--mut);font-family:var(--mono)">${m["straddle"]:.2f}</div></div>')
    if not im_rows:
        im_rows = '<div style="padding:16px;text-align:center;color:var(--mut);font-size:11px">Nincs közelgő earnings (30 napon belül)</div>'

    # IV tracker
    iv_rows = ""
    for t, d in sorted(iv_data.items()):
        iv = d.get("iv", 0); ivp = d.get("iv_pct"); wk = d.get("weeks", 1)
        beta = d.get("iv_beta", False)
        if ivp is not None:
            c = "#f04060" if ivp > 70 else "#00d488" if ivp < 30 else "#f0a500"
            tip = "Eladj" if ivp > 70 else "Végy" if ivp < 30 else "–"
            bdg = ' <span style="color:#f0a500;font-size:8px">(béta)</span>' if beta else ""
            pbar = (f'<div style="height:3px;background:var(--brd2);border-radius:2px;margin-top:3px">'
                    f'<div style="height:3px;width:{ivp}%;background:{c};border-radius:2px"></div></div>')
            pct_s = f'<span style="color:{c};font-weight:700;font-family:var(--mono)">{ivp}%</span>{bdg}{pbar}'
        else:
            c = "var(--mut)"; tip = "–"
            pct_s = f'<span style="color:var(--mut);font-size:9px">{wk} hét – gyűlik</span>'
        iv_rows += (f'<div style="display:grid;grid-template-columns:65px 55px 1fr 45px;gap:8px;'
            f'padding:7px 10px;border-bottom:1px solid var(--brd);align-items:center">'
            f'<div style="font-weight:700;color:var(--text);font-family:var(--mono)">{t}</div>'
            f'<div style="color:var(--sub);font-family:var(--mono)">{iv:.0f}%</div>'
            f'<div>{pct_s}</div>'
            f'<div style="color:{c};font-size:9px;font-family:var(--mono)">{tip}</div></div>')

    # Decision matrix
    dm_rows = generate_decision_matrix(score, vpct, skew_v, term_sig, gex_pos)

    CSS = """:root{
  --bg:#0b1525;--bg2:#0e1b30;--card:#121f38;--c2:#172542;
  --brd:#1d2f4a;--brd2:#243860;
  --text:#dce8f5;--sub:#8aabcc;--mut:#4e6f8f;
  --bull:#00d488;--bear:#f04060;--neu:#f0a500;--info:#4da6ff;
  --mono:"JetBrains Mono",monospace;--sans:"Inter",sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;padding:14px;min-height:100vh}
.sbar{display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.sp{font-family:var(--mono);font-size:9.5px;padding:3px 10px;border-radius:99px;border:1px solid}
.sp-ok{color:var(--bull);border-color:#00d48830;background:#00d48810}
.sp-info{color:var(--sub);border-color:var(--brd);background:var(--card)}
.sp-warn{color:var(--neu);border-color:#f0a50030;background:#f0a50010}
.sbar-r{margin-left:auto;font-size:10px;color:var(--mut);font-family:var(--mono)}
.sec{background:var(--card);border:1px solid var(--brd);border-radius:12px;padding:15px;margin-bottom:12px}
.sec-title{font-size:10px;font-family:var(--mono);color:var(--mut);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.g4{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px}
.nav{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.nav a{font-size:10px;font-family:var(--mono);color:var(--mut);text-decoration:none;padding:4px 12px;border:1px solid var(--brd);border-radius:99px}
.nav a:hover{color:var(--text)}
.nav .act{color:var(--info);border-color:#4da6ff30;background:#4da6ff08}
.dm-table{width:100%;border-collapse:collapse;font-size:10px}
.dm-table th{font-size:8px;font-family:var(--mono);color:var(--mut);text-align:left;padding:6px 8px;border-bottom:1px solid var(--brd);white-space:nowrap}
.dm-table td{padding:7px 8px;border-bottom:1px solid #1d2f4a50;color:var(--sub);vertical-align:top;font-size:10px}
.dm-table tr.active td{background:#00d48808;color:var(--text)}
.dm-table tr.active td:nth-child(3){color:#00d488;font-weight:700}
@media(max-width:640px){
  .g4{grid-template-columns:1fr 1fr}
  .dm-table{font-size:9px}
  .dm-table th,.dm-table td{padding:5px 6px}
}"""

    sp_gex_cls = "sp-ok" if gex_pos else "sp-warn"

    return (f'<!DOCTYPE html><html lang="hu"><head>'
        f'<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Opciós Dashboard – {today}</title>'
        f'<style>{CSS}</style></head><body>'
        f'<div class="nav"><a href="index.html">📊 Fő Dashboard</a>'
        f'<a href="options.html" class="act">⚡ Opciós Modul</a></div>'
        f'<div class="sbar">'
        f'<span class="sp sp-ok">⬤ Opciós Dashboard</span>'
        f'<span class="sp sp-info">{today}</span>'
        f'<span class="sp sp-info">Score: {score} – {playbook}</span>'
        f'<span class="sp {sp_gex_cls}">GEX {"+" if gex_pos else "–"}</span>'
        f'<div class="sbar-r">Heti frissítés · Péntek 17:30</div></div>'
        f'{back_banner}'
        f'<div class="sec"><div class="sec-title">Volatilitás Rezsim'
        f'<span style="background:#4da6ff12;color:var(--info);font-size:9px;padding:2px 8px;border-radius:99px;border:1px solid #4da6ff25">SPX opciós piac</span></div>'
        f'<div class="g4">'
        # IV Percentile card
        f'<div style="background:var(--c2);border:1px solid {vix_c}25;border-radius:9px;padding:12px">'
        f'<div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:6px">IV PERCENTILE</div>'
        f'<div style="font-size:26px;font-weight:700;color:{vix_c};font-family:var(--mono)">{vpct}%</div>'
        f'<div style="display:flex;gap:3px;margin:6px 0">{dots(vix_lvl)}</div>'
        f'<div style="font-size:9px;color:var(--sub)">{"DRÁGA – eladni" if vpct>70 else "OLCSÓ – venni" if vpct<30 else "Normál"}</div>'
        f'<div style="height:3px;background:var(--brd2);border-radius:2px;margin-top:6px">'
        f'<div style="height:3px;width:{vpct}%;background:{vix_c};border-radius:2px"></div></div>'
        f'<div style="font-size:8px;color:var(--mut);margin-top:3px;font-family:var(--mono)">VIX Rank: {vrank}%</div></div>'
        # SKEW card
        f'<div style="background:var(--c2);border:1px solid {skew_c}25;border-radius:9px;padding:12px">'
        f'<div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:6px">SKEW INDEX</div>'
        f'<div style="font-size:26px;font-weight:700;color:{skew_c};font-family:var(--mono)">{skew_v}</div>'
        f'<div style="display:flex;gap:3px;margin:6px 0">{dots(skew_lvl)}</div>'
        f'<div style="font-size:9px;color:var(--sub);line-height:1.4">{skew_data.get("skew_desc","?")}</div>'
        f'<div style="font-size:8px;color:var(--mut);margin-top:6px;font-family:var(--mono)">{skew_data.get("trade_hint","–")}</div></div>'
        # Term Structure card
        f'<div style="background:var(--c2);border:1px solid {term_c}25;border-radius:9px;padding:12px">'
        f'<div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:6px">VIX TERM STRUCTURE</div>'
        f'<div style="font-size:11px;font-weight:700;color:{term_c};margin-bottom:8px">{"🔴 BACKWARDATION" if back else "🟢 Contango"}</div>'
        f'<div style="display:flex;gap:6px;align-items:flex-end;height:60px">{tbars}</div>'
        f'<div style="font-size:8px;color:var(--mut);margin-top:6px">{vix_data.get("term_desc","?")}</div></div>'
        # Ratio card
        f'<div style="background:var(--c2);border:1px solid {term_c}25;border-radius:9px;padding:12px">'
        f'<div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:6px">VIX/VIX3M ARÁNY</div>'
        f'<div style="font-size:26px;font-weight:700;color:{term_c};font-family:var(--mono)">{vix_data.get("ratio_3m",0.9):.3f}</div>'
        f'<div style="display:flex;gap:3px;margin:6px 0">{dots(term_lvl)}</div>'
        f'<div style="font-size:9px;color:var(--sub)">Kritikus: <strong>1.0</strong> felett = Backwardation</div>'
        f'<div style="font-size:8px;color:{term_c};margin-top:6px;font-family:var(--mono)">{"⚠ FELETT VAGYUNK" if back else "✓ Normál"}</div></div>'
        f'</div></div>'
        # Strategy
        f'<div class="sec"><div class="sec-title">Stratégia Javaslat'
        f'<span style="background:var(--c2);color:var(--mut);font-size:9px;padding:2px 8px;border-radius:99px;border:1px solid var(--brd)">'
        f'Score {score} · IV {vpct}% · SKEW {skew_v} · {"Back" if back else "Contango"} · GEX {"+" if gex_pos else "–"}</span></div>'
        f'{sc_html}</div>'
        # Decision matrix
        f'<div class="sec"><div class="sec-title">Döntési Mátrix'
        f'<span style="background:#00d48812;color:#00d488;font-size:9px;padding:2px 8px;border-radius:99px;border:1px solid #00d48830">▶ Zöld = aktuális</span></div>'
        f'<div style="overflow-x:auto"><table class="dm-table"><thead><tr>'
        f'<th>Score</th><th>IV</th><th>Stratégia</th><th>Lejárat</th><th>Exit</th>'
        f'</tr></thead><tbody>{dm_rows}</tbody></table></div>'
        f'<div style="margin-top:8px;padding:8px 10px;background:var(--c2);border-radius:6px;'
        f'font-size:9px;color:var(--mut);font-family:var(--mono)">Max 5% portfólió / pozíció · Exit: 50% profit VAGY 21 DTE</div></div>'
        # Implied move
        f'<div class="sec"><div class="sec-title">Implied Move – Közelgő Earnings</div>'
        f'<div style="background:var(--c2);border:1px solid var(--brd);border-radius:8px;overflow:hidden">'
        f'<div style="display:grid;grid-template-columns:60px 70px 70px 60px 55px 70px;gap:6px;'
        f'padding:7px 10px;border-bottom:1px solid var(--brd)">'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">TICKER</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">ÁR</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">IMPLIED ±%</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">NAPOK</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">ATM IV</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">STRADDLE</div>'
        f'</div>{im_rows}</div>'
        f'<div style="margin-top:8px;font-size:9px;color:var(--mut);font-family:var(--mono)">'
        f'ATM Straddle / Ár = piaci várható mozgás ±irányban</div></div>'
        # IV tracker
        f'<div class="sec"><div class="sec-title">IV Percentile Tracker'
        f'<span style="background:var(--c2);color:var(--mut);font-size:9px;padding:2px 8px;border-radius:99px;border:1px solid var(--brd)">'
        f'{len([v for v in iv_data.values() if (v.get("weeks",0) or 0) >= 8])} elegendő adat (8+ hét)</span></div>'
        f'<div style="background:var(--c2);border:1px solid var(--brd);border-radius:8px;overflow:hidden">'
        f'<div style="display:grid;grid-template-columns:65px 55px 1fr 45px;gap:8px;'
        f'padding:7px 10px;border-bottom:1px solid var(--brd)">'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">TICKER</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">IV</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">IV PERCENTILE</div>'
        f'<div style="font-size:8px;color:var(--mut);font-family:var(--mono)">JAVASLAT</div>'
        f'</div>{iv_rows}</div></div>'
        f'<div style="padding:10px 12px;background:var(--card);border:1px solid var(--brd);border-radius:8px;'
        f'font-size:9px;color:var(--mut);font-family:var(--mono);line-height:1.7">'
        f'Adatforrás: CBOE ^SKEW ^VIX ^VIX3M ^VIX6M via yfinance · GEX: dashboard history.json<br>'
        f'⚠ Nem befektetési tanács. Az opciók komplex instrumentumok.</div>'
        f'</body></html>')

def main():
    print("\n" + "="*55)
    print("  Opciós Dashboard v2")
    print(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*55 + "\n")

    state    = safe(get_dashboard_state, {"score":50,"playbook":"WAIT","date":"?","gex":5}, "state")
    vix_data = safe(fetch_vix_term, {"VIX":20,"VIX3M":22,"VIX6M":23,"vix_pct":50,
                    "vix_rank":50,"backwardation":False,"strong_back":False,
                    "term_sig":"normal","term_desc":"?"}, "VIX")
    skew_data = safe(fetch_skew, {"skew":130,"skew_sig":"normal",
                     "skew_desc":"?","trade_hint":"–"}, "SKEW")

    log("Implied Move számítás...")
    impl_moves = []
    for t in WATCHLIST[:15]:
        m = calc_implied_move(t)
        if m:
            impl_moves.append(m)
            log(f"  {t}: ±{m['impl_pct']}%")
    impl_moves.sort(key=lambda x: x["days"])

    log("IV Percentile frissítés...")
    iv_data = safe(update_iv_history, {}, "IV")

    gex_pos = state.get("gex", 5) > 0
    strats  = suggest_strategy(
        state["score"], vix_data.get("vix_pct",50),
        skew_data.get("skew_sig","normal"),
        vix_data.get("term_sig","normal"), gex_pos,
        vix_val=vix_data.get("VIX", 20))

    log("HTML generálás...")
    html = generate_html(state, vix_data, skew_data, impl_moves, iv_data, strats)
    Path(OUTPUT_HTML).write_text(html, encoding="utf-8")

    print(f"\n  ✓ Kész! VIX: {vix_data.get('VIX',20)} | IV%: {vix_data.get('vix_pct',50)} | SKEW: {skew_data.get('skew',130)}\n")

if __name__ == "__main__":
    main()
