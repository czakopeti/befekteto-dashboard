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
def suggest_strategy(score, vix_pct, skew_sig, term_sig, gex_positive):
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

# ── HTML GENERATOR ────────────────────────────────────────────
def generate_html(state, vix_data, skew_data, impl_moves, iv_data, strategies):
    today = datetime.datetime.now().strftime("%Y. %B %d.")
    score    = state["score"]
    playbook = state["playbook"]
    gex_pos  = state.get("gex", 5) > 0

    vix      = vix_data.get("VIX", 20)
    vix3m    = vix_data.get("VIX3M", 22)
    vix6m    = vix_data.get("VIX6M", 23)
    vrank    = vix_data.get("vix_rank", 50)
    vpct     = vix_data.get("vix_pct", 50)
    skew_v   = skew_data.get("skew", 130)
    back     = vix_data.get("backwardation", False)
    strong_b = vix_data.get("strong_back", False)
    term_sig = vix_data.get("term_sig", "normal")

    score_c = "#00d488" if score >= 65 else "#f04060" if score < 40 else "#f0a500"
    vix_c   = "#f04060" if vpct > 70 else "#00d488" if vpct < 30 else "#f0a500"
    skew_c  = "#f04060" if skew_v > 145 else "#f0a500" if skew_v > 130 else "#00d488"
    term_c  = "#f04060" if back else "#00d488"

    # Backwardation banner
    back_banner = ""
    if strong_b:
        back_banner = """<div style="background:#f0406020;border:2px solid #f04060;
            border-radius:10px;padding:14px 18px;margin-bottom:20px;
            display:flex;align-items:center;gap:12px">
          <div style="font-size:24px">🚨</div>
          <div>
            <div style="font-size:13px;font-weight:700;color:#f04060">VIX BACKWARDATION AKTÍV</div>
            <div style="font-size:10px;color:#f04060;margin-top:3px">
              VIX > VIX3M – azonnali pánik az opciós piacon. Minden long stratégia szünetel.
              Ne nyiss új pozíciót amíg ez fennáll.
            </div>
          </div>
        </div>"""
    elif back:
        back_banner = """<div style="background:#f0a50015;border:1px solid #f0a500;
            border-radius:10px;padding:10px 16px;margin-bottom:16px">
          <span style="color:#f0a500;font-weight:700">⚠ Backwardation – </span>
          <span style="color:#f0a500;font-size:11px">Óvatos pozicionálás. Spekulatív long-ok kerülendők.</span>
        </div>"""

    # VIX term bars
    max_v  = max(vix, vix3m, vix6m) * 1.2
    tbars = ""
    for val, lbl in [(vix,"VIX\n30n"),(vix3m,"VIX3M\n3hó"),(vix6m,"VIX6M\n6hó")]:
        pct = round(val / max_v * 100)
        c   = "#f04060" if val > 25 else "#f0a500" if val > 18 else "#00d488"
        tbars += f"""<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px">
          <div style="font-size:14px;font-weight:700;color:{c}">{val}</div>
          <div style="width:100%;height:55px;background:var(--c2);border-radius:4px;position:relative;overflow:hidden">
            <div style="position:absolute;bottom:0;width:100%;height:{pct}%;background:{c}25;border-top:2px solid {c}"></div>
          </div>
          <div style="font-size:9px;color:var(--mut);text-align:center;font-family:var(--mono);white-space:pre">{lbl}</div>
        </div>"""

    # Implied moves
    im_rows = ""
    for m in (impl_moves or []):
        c = "#f0a500" if m["impl_pct"] > 15 else "#4da6ff"
        im_rows += f"""<tr>
          <td style="font-weight:700;color:var(--fg)">{m['ticker']}</td>
          <td>${m['price']:.2f}</td>
          <td style="color:{c};font-weight:700">±{m['impl_pct']:.1f}%</td>
          <td>{m['days']} nap</td>
          <td>{m['atm_iv']:.0f}%</td>
          <td>${m['straddle']:.2f}</td></tr>"""
    if not im_rows:
        im_rows = '<tr><td colspan="6" style="color:var(--mut);text-align:center;padding:14px">Nincs közelgő earnings a watchlisten (30 napon belül)</td></tr>'

    # IV tracker
    iv_rows = ""
    for t, d in sorted(iv_data.items()):
        iv   = d.get("iv", 0)
        ivp  = d.get("iv_pct")
        wk   = d.get("weeks", 1)
        beta = d.get("iv_beta", False)
        if ivp is not None:
            c   = "#f04060" if ivp > 70 else "#00d488" if ivp < 30 else "#f0a500"
            tip = "Eladj" if ivp > 70 else "Végy" if ivp < 30 else "–"
            beta_lbl = ' <span style="font-size:8px;color:#f0a500">(béta)</span>' if beta else ''
            pct_str = f'<span style="color:{c};font-weight:700">{ivp}%</span>' + beta_lbl
            bar = f'<div style="height:3px;background:var(--c2);border-radius:2px;margin-top:3px"><div style="height:3px;width:{ivp}%;background:{c};border-radius:2px"></div></div>'
        else:
            c = "var(--mut)"; tip = "–"
            pct_str = f'<span style="color:var(--mut);font-size:9px">{wk} hét – gyűlik</span>'
            bar = ""
        iv_rows += f"""<tr>
          <td style="font-weight:700;color:var(--fg)">{t}</td>
          <td style="color:var(--sub)">{iv:.0f}%</td>
          <td>{pct_str}{bar}</td>
          <td style="color:{c};font-size:10px">{tip}</td></tr>"""

    # Strategy cards
    sc_html = ""
    for s in strategies:
        stars = "★" * s["rating"] + "☆" * (5 - s["rating"])
        sc_html += f"""<div style="background:var(--card);border:1px solid {s['color']}25;
            border-left:3px solid {s['color']};border-radius:10px;
            padding:14px 16px;margin-bottom:10px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
            <div style="font-size:12px;font-weight:700;color:{s['color']};font-family:var(--mono)">{s['name']}</div>
            <div style="color:{s['color']};font-size:11px;opacity:0.7">{stars}</div>
          </div>
          <div style="font-size:10.5px;color:var(--sub);margin-bottom:7px;line-height:1.5">{s['desc']}</div>
          <div style="display:flex;gap:20px;font-size:9px;font-family:var(--mono)">
            <span>Kockázat: <span style="color:#f04060">{s['risk']}</span></span>
            <span>Hozam: <span style="color:#00d488">{s['reward']}</span></span>
          </div></div>"""

    # Decision matrix
    dm_rows = generate_decision_matrix(score, vpct, skew_v, term_sig, gex_pos)

    return f"""<!DOCTYPE html>
<html lang="hu"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opciós Dashboard – {today}</title>
<style>
:root{{--bg:#0d0f14;--card:#151820;--c2:#1e2230;--brd:#2a2e3d;
      --fg:#e8ecf5;--sub:#8b91a8;--mut:#555b72;
      --mono:'JetBrains Mono','Courier New',monospace}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--fg);
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}}
.wrap{{max-width:960px;margin:0 auto;padding:20px 16px}}
h2{{font-size:10px;font-family:var(--mono);color:var(--mut);letter-spacing:2px;
    text-transform:uppercase;margin:24px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--brd)}}
.g4{{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px}}
.card{{background:var(--card);border:1px solid var(--brd);border-radius:12px;padding:16px}}
.nav{{display:flex;gap:10px;margin-bottom:20px}}
.nav a{{font-size:10px;font-family:var(--mono);color:var(--mut);text-decoration:none;
        padding:5px 12px;border:1px solid var(--brd);border-radius:20px}}
.nav .act{{color:#4da6ff;border-color:#4da6ff30;background:#4da6ff08}}
table{{width:100%;border-collapse:collapse}}
th{{font-size:9px;font-family:var(--mono);color:var(--mut);text-align:left;padding:6px 8px;border-bottom:1px solid var(--brd)}}
td{{padding:8px 8px;border-bottom:1px solid #1e223080;font-size:11px;color:var(--sub)}}
tr:hover td{{background:var(--c2)}}
@media(max-width:600px){{.g4{{grid-template-columns:1fr 1fr}}}}
</style></head><body><div class="wrap">

<div class="nav">
  <a href="index.html">📊 Fő Dashboard</a>
  <a href="options.html" class="act">⚡ Opciós Modul</a>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px">
  <div>
    <div style="font-size:20px;font-weight:700">Opciós Dashboard</div>
    <div style="font-size:10px;color:var(--mut);font-family:var(--mono)">{today}</div>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:10px;color:var(--mut)">Makro score:</div>
    <div style="font-size:28px;font-weight:900;color:{score_c}">{score}</div>
    <div style="padding:3px 10px;border-radius:20px;font-size:10px;font-family:var(--mono);
                font-weight:600;color:{score_c};background:{score_c}15;border:1px solid {score_c}30">{playbook}</div>
    <div style="padding:3px 10px;border-radius:20px;font-size:10px;font-family:var(--mono);
                color:{'#00d488' if gex_pos else '#f04060'};
                background:{'#00d48812' if gex_pos else '#f0406012'};
                border:1px solid {'#00d48830' if gex_pos else '#f0406030'}">
      GEX {"+" if gex_pos else "–"}</div>
  </div>
</div>

{back_banner}

<h2>Volatilitás Rezsim</h2>
<div class="g4">
  <div class="card" style="border-color:{vix_c}25">
    <div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:4px">IV PERCENTILE (52 HÉT)</div>
    <div style="font-size:30px;font-weight:900;color:{vix_c}">{vpct}%</div>
    <div style="height:4px;background:var(--c2);border-radius:2px;margin:6px 0">
      <div style="height:4px;width:{vpct}%;background:{vix_c};border-radius:2px"></div></div>
    <div style="font-size:10px;color:var(--sub)">
      {"DRÁGA – opció ELADÁS kedvező" if vpct > 70 else "OLCSÓ – opció VÉTEL kedvező" if vpct < 30 else "Normál IV szint"}</div>
    <div style="font-size:8px;color:var(--mut);margin-top:4px;font-family:var(--mono)">VIX Rank: {vrank}%</div>
  </div>
  <div class="card" style="border-color:{skew_c}25">
    <div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:4px">SKEW INDEX</div>
    <div style="font-size:30px;font-weight:900;color:{skew_c}">{skew_v}</div>
    <div style="font-size:10px;color:var(--sub);margin-top:6px;line-height:1.4">{skew_data.get('skew_desc','?')}</div>
    <div style="font-size:9px;color:var(--mut);margin-top:6px;font-family:var(--mono)">
      {skew_data.get('trade_hint','–')}</div>
  </div>
  <div class="card" style="border-color:{term_c}25">
    <div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:6px">VIX TERM STRUCTURE</div>
    <div style="font-size:11px;font-weight:700;color:{term_c};margin-bottom:8px">
      {"🔴 BACKWARDATION" if back else "🟢 Contango"}</div>
    <div style="display:flex;gap:8px;align-items:flex-end;height:65px">{tbars}</div>
    <div style="font-size:9px;color:var(--mut);margin-top:6px">{vix_data.get('term_desc','?')}</div>
  </div>
  <div class="card">
    <div style="font-size:9px;font-family:var(--mono);color:var(--mut);margin-bottom:4px">VIX / VIX3M ARÁNY</div>
    <div style="font-size:30px;font-weight:900;color:{term_c}">{vix_data.get('ratio_3m',0.9):.3f}</div>
    <div style="font-size:10px;color:var(--sub);margin-top:6px">
      Kritikus szint: <strong>1.0</strong> felett = Backwardation</div>
    <div style="font-size:9px;color:var(--mut);margin-top:6px;font-family:var(--mono)">
      {"⚠ FELETT VAGYUNK" if back else "✓ Normál tartomány"}</div>
  </div>
</div>

<h2>Stratégia Javaslat</h2>
<div style="padding:10px 14px;background:var(--c2);border-radius:8px;margin-bottom:12px;
            font-size:9px;color:var(--mut);font-family:var(--mono)">
  Bemenet: Score <strong style="color:{score_c}">{score}</strong> · 
  IV Pct <strong style="color:{vix_c}">{vpct}%</strong> · 
  SKEW <strong style="color:{skew_c}">{skew_v}</strong> · 
  Term: <strong style="color:{term_c}">{"Backwardation" if back else "Contango"}</strong> · 
  GEX: <strong style="color:{'#00d488' if gex_pos else '#f04060'}">{"Pozitív" if gex_pos else "NEGATÍV"}</strong>
</div>
{sc_html}

<h2>Döntési Mátrix – Pontosan Mit, Mikor, Hogyan</h2>
<div class="card">
  <table>
    <thead><tr>
      <th>Score</th><th>IV</th><th>Stratégia</th>
      <th>Időzítés / Lejárat</th><th>Pozícióméret / Exit</th>
    </tr></thead>
    <tbody>{dm_rows}</tbody>
  </table>
  <div style="margin-top:10px;padding:10px;background:var(--c2);border-radius:6px;
              font-size:9px;color:var(--mut);font-family:var(--mono);line-height:1.7">
    ★ Zöld sor = jelenlegi piaci állapothoz legjobb stratégia · 
    Lejárat: 30-45 nap optimális a theta-decay szempontjából · 
    Méretezés: max 5% portfólióból egy opciós pozícióra · 
    Általános exit: 50% profit VAGY 21 nap a lejárat előtt
  </div>
</div>

<h2>Implied Move – Közelgő Earnings</h2>
<div class="card">
  <table>
    <thead><tr><th>Ticker</th><th>Ár</th><th>Implied ±%</th>
    <th>Earnings</th><th>ATM IV</th><th>Straddle ár</th></tr></thead>
    <tbody>{im_rows}</tbody>
  </table>
  <div style="margin-top:8px;font-size:9px;color:var(--mut);font-family:var(--mono)">
    Implied Move = ATM Straddle / Részvény ár. Ha a részvény historikusan többet mozog → opció alulárazott (venni jobb).
  </div>
</div>

<h2>IV Percentile Tracker</h2>
<div class="card">
  <div style="font-size:9px;color:var(--mut);margin-bottom:10px;font-family:var(--mono)">
    IV Percentile: az idő hány %-ában volt alacsonyabb az IV a mostaninál. 
    8+ hét után megbízható. Jelenleg elegendő adat: {len([v for v in iv_data.values() if (v.get('weeks',0) or 0) >= 8])} részvény.
  </div>
  <table>
    <thead><tr><th>Ticker</th><th>Aktuális IV</th><th>IV Percentile</th><th>Javaslat</th></tr></thead>
    <tbody>{iv_rows}</tbody>
  </table>
</div>

<div style="margin-top:20px;padding:12px;background:var(--c2);border-radius:8px;
            font-size:9px;color:var(--mut);font-family:var(--mono);line-height:1.8">
  Adatforrások: CBOE (^SKEW, ^VIX via yfinance) · Options chains: yfinance · GEX: SqueezeMetrics<br>
  ⚠ Nem befektetési tanács. Az opciók tőkeáttétes, komplex instrumentumok. Értsd meg a kockázatot mielőtt kereskedsz.
</div>
</div></body></html>"""

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
        vix_data.get("term_sig","normal"), gex_pos)

    log("HTML generálás...")
    html = generate_html(state, vix_data, skew_data, impl_moves, iv_data, strats)
    Path(OUTPUT_HTML).write_text(html, encoding="utf-8")

    print(f"\n  ✓ Kész! VIX: {vix_data.get('VIX',20)} | IV%: {vix_data.get('vix_pct',50)} | SKEW: {skew_data.get('skew',130)}\n")

if __name__ == "__main__":
    main()
