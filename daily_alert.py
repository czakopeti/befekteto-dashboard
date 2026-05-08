#!/usr/bin/env python3
"""
Befektető Dashboard – Daily Alert Check
Minden reggel 7:00-kor fut (GitHub Actions)
Csak kritikus változásokat figyel – push notification ntfy.sh-on

Szükséges GitHub Secrets:
  NTFY_TOPIC: az ntfy.sh topic neve (pl. "peter-befekteto-alerts")
  FRED_API_KEY: meglévő

Telepítés:
  1. ntfy.sh app telepítése telefonra
  2. Subscribe a NTFY_TOPIC-ra
  3. daily_alert.yml feltöltése .github/workflows/ mappába
"""
import os, requests, json
import yfinance as yf
import pandas as pd
import datetime

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
FRED_KEY   = os.environ.get("FRED_API_KEY", "")
HEADERS    = {"User-Agent": "Mozilla/5.0"}
STATE_FILE = "alert_state.json"

def send_ntfy(title, message, priority="default", tags=""):
    """Push notification küldése ntfy.sh-on keresztül"""
    if not NTFY_TOPIC:
        print(f"[NTFY] {title}: {message}")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title":    title,
                "Priority": priority,
                "Tags":     tags,
            }, timeout=10
        )
        print(f"[NTFY OK] {title}")
    except Exception as e:
        print(f"[NTFY HIBA] {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def fetch_fred(series, n=3):
    r = requests.get(
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series}&api_key={FRED_KEY}"
        f"&file_type=json&sort_order=desc&limit={n}", timeout=15)
    obs = [o for o in r.json()["observations"] if o["value"] != "."]
    return [float(o["value"]) for o in obs]

def run_alert():
    today = datetime.date.today().strftime("%Y-%m-%d")
    dow   = datetime.date.today().weekday()  # 0=Hétfő, 4=Péntek
    is_friday = (dow == 4)

    state = load_state()
    alerts = []
    summary_parts = []

    # ── SPX + VIX ──────────────────────────────────────────
    spx_h = yf.Ticker("^GSPC").history(period="5d")
    vix_h = yf.Ticker("^VIX").history(period="5d")

    spx   = round(float(spx_h["Close"].iloc[-1]))
    spx_p = round(float(spx_h["Close"].iloc[-2]))
    vix   = round(float(vix_h["Close"].iloc[-1]), 1)
    vix_p = round(float(vix_h["Close"].iloc[-2]), 1)

    spx_chg   = round((spx - spx_p) / spx_p * 100, 1)
    vix_chg   = round((vix - vix_p) / vix_p * 100, 1)
    vix_spike = vix_chg > 20

    summary_parts.append(f"SPX: {spx:,} ({spx_chg:+.1f}%)")
    summary_parts.append(f"VIX: {vix} ({vix_chg:+.1f}%)")

    # ── GEX előjel (Squeeze Metrics) ───────────────────────
    gex_neg = False
    try:
        from io import StringIO
        r = requests.get("https://squeezemetrics.com/monitor/static/DIX.csv",
                        timeout=15, headers=HEADERS)
        if r.status_code == 200:
            df = pd.read_csv(StringIO(r.text))
            df.columns = [c.strip().lower() for c in df.columns]
            gex_col = next((c for c in df.columns if "gex" in c), None)
            if gex_col:
                gex_val = float(df[gex_col].dropna().iloc[-1])
                gex_b   = gex_val / 1e9 if abs(gex_val) > 1e6 else gex_val
                gex_neg = gex_b < 0
                summary_parts.append(f"GEX: ${gex_b:.1f}B {'⚠' if gex_neg else '✓'}")
    except Exception as e:
        print(f"GEX hiba: {e}")

    # ── AF(18,6) ────────────────────────────────────────────
    af_lila = False
    try:
        spx_w = yf.Ticker("^GSPC").history(period="5y", interval="1wk")
        def trix(s, n):
            e1=s.ewm(span=n,adjust=False).mean()
            e2=e1.ewm(span=n,adjust=False).mean()
            e3=e2.ewm(span=n,adjust=False).mean()
            return ((e3-e3.shift(1))/e3.shift(1)*100).fillna(0)
        t18 = trix(spx_w["Close"], 18)
        t6  = trix(spx_w["Close"], 6)
        af  = float(t18.iloc[-1] - t6.iloc[-1])
        af_lila = af < 0
        summary_parts.append(f"AF: {af:.3f} {'🔴 lila' if af_lila else '🟢 sárga'}")
    except Exception as e:
        print(f"AF hiba: {e}")

    # ── Trigger súly gyors becslés ──────────────────────────
    trigger_weight = 0
    if vix_spike:     trigger_weight += 3
    if gex_neg:       trigger_weight += 3
    if af_lila:       trigger_weight += 1
    if spx_chg < -3:  trigger_weight += 1
    summary_parts.append(f"Trigger súly: {trigger_weight}/6")

    # ── RIASZTÁSOK ──────────────────────────────────────────
    prev_vix = state.get("vix", 20)
    prev_gex_neg = state.get("gex_neg", False)
    prev_af_lila = state.get("af_lila", False)

    if vix_spike:
        alerts.append(("🚨 VIX SPIKE!", f"VIX {vix_p} → {vix} (+{vix_chg:.0f}%) – Black Swan trigger!", "urgent", "warning"))

    if gex_neg and not prev_gex_neg:
        alerts.append(("⚡ GEX NEGATÍVBA FORDULT", f"GEX negatív – piaci amplifikáció veszélye! VIX: {vix}", "high", "rotating_light"))

    if af_lila and not prev_af_lila:
        alerts.append(("🔴 AF LILÁBA FORDULT", f"SPX AF(18,6) sárgáról lilára – momentum fordulat! SPX: {spx:,}", "high", "chart_decreasing"))

    if spx_chg < -3:
        alerts.append(("📉 NAGY ESÉS", f"SPX {spx_chg:+.1f}% esett! ({spx:,}) – Figyelj a triggerekre!", "high", "chart_decreasing"))

    if trigger_weight >= 6:
        alerts.append(("🔴 EXIT JEL!", f"Trigger súly: {trigger_weight}/6 – Pozíció csökkentés javasolt! SH/PSQ fedezés!", "urgent", "rotating_light"))

    # ── KÜLDÉS ──────────────────────────────────────────────
    for title, msg, priority, tags in alerts:
        send_ntfy(title, msg, priority, tags)

    # Pénteken mindig jön összefoglaló
    if is_friday or alerts:
        summary = " | ".join(summary_parts)
        footer  = f" | {len(alerts)} riasztás" if alerts else " | Nincs sürgős teendő ✓"
        send_ntfy(
            f"📊 {'PÉNTEK ÖSSZEFOGLALÓ' if is_friday else 'Napi Alert'}",
            summary + footer,
            "default" if not alerts else "high",
            "chart_with_upwards_trend"
        )
        print(f"Összefoglaló: {summary}{footer}")

    # State mentés
    save_state({"date": today, "vix": vix, "spx": spx,
                "gex_neg": gex_neg, "af_lila": af_lila,
                "trigger_weight": trigger_weight})

    print(f"✓ Daily alert check kész – {today}")

if __name__ == "__main__":
    run_alert()
