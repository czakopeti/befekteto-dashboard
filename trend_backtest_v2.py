#!/usr/bin/env python3
"""
trend_backtest_v2.py – Nagymintás havi AF fordulat backtest
============================================================
~150 reszvenyen, 2005-tol (max elerheto adat) vizsgalja:
Ha a havi AF(18,6) lilаbol sargara fordul, mi lesz
az atlagos hozam 1/3/6/12 honappal kesobb?

Szurok:
  - Makro rezs (SPX SMA200 felett / alatt)
  - Lila periodus hossza (rovid 1-5 ho / hosszu 6+ ho)
  - SMA200 pozicio a fordulat idejen

Hasznalat: python trend_backtest_v2.py
"""

import sys, json, datetime, time
import pandas as pd
import numpy as np
import yfinance as yf

HOLD_MONTHS = [1, 3, 6, 12]
MIN_HISTORY = 60   # minimum havi adatpont (~5 ev)

# ── 150+ reszvenyes universe, csak 2005 elotti IPO-k ────────
UNIVERSE = [
    # Mega cap tech
    "AAPL","MSFT","GOOGL","AMZN","NVDA","INTC","CSCO","IBM","ORCL","QCOM",
    "TXN","AMAT","ADI","KLAC","LRCX","MCHP","MRVL","SWKS","QRVO","ON",
    # Platform / software
    "ADBE","INTU","CRM","NFLX","EBAY","PYPL","BKNG","PCLN","TRIP",
    # Semiconductor / hardware
    "AMD","MU","STX","WDC","NTAP","JNPR","FFIV","VRSN","AKAM",
    # Financials
    "JPM","GS","MS","BAC","WFC","C","BRK-B","AXP","V","MA",
    "BLK","SCHW","CME","ICE","SPGI","MCO",
    # Healthcare / pharma
    "JNJ","PFE","MRK","ABBV","ABT","TMO","DHR","MDT","BSX","SYK",
    "ISRG","BDX","ZTS","REGN","GILD","AMGN","BIIB","CELG","VRTX",
    # Consumer
    "WMT","COST","HD","LOW","TGT","MCD","SBUX","NKE","DIS","CMCSA",
    "KO","PEP","PG","CL","KMB","CHD","MO","PM","EL","ULTA",
    # Industrials
    "GE","HON","MMM","CAT","DE","EMR","ETN","ROK","FTV","CARR",
    "UPS","FDX","CSX","NSC","UNP","LMT","RTX","NOC","GD","BA",
    # Energy
    "XOM","CVX","SLB","HAL","PSX","MPC","VLO","EOG","PXD","OXY",
    # Real estate / utilities
    "AMT","PLD","O","SPG","NEE","DUK","SO","D","EXC","AEP",
    # Communications
    "T","VZ","TMUS","NFLX","CHTR","DISH",
    # Growth 2005+
    "PCLN","EXPE","TRIP","YELP","ZG",
    # Cyclicals
    "F","GM","COF","DFS","SYF","ALLY",
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))

def trix(series, n):
    e1 = series.ewm(span=n, adjust=False).mean()
    e2 = e1.ewm(span=n, adjust=False).mean()
    e3 = e2.ewm(span=n, adjust=False).mean()
    return ((e3 - e3.shift(1)) / e3.shift(1) * 100).fillna(0)

def get_spx_regime():
    """
    SPX SMA200 alapu makro rezs:
    bull = SPX > SMA200 havi zarnal
    bear = SPX < SMA200 havi zarnal
    """
    try:
        h = yf.Ticker("^GSPC").history(period="max", interval="1mo", auto_adjust=True)
        h.index = h.index.tz_localize(None) if h.index.tz else h.index
        close = h["Close"].dropna()
        sma200 = close.rolling(200).mean()
        regime = pd.Series(
            np.where(close > sma200, "bull", "bear"),
            index=close.index
        )
        return regime
    except Exception:
        return None

def backtest_ticker(ticker, spx_regime=None):
    try:
        h = yf.Ticker(ticker).history(
            period="max", interval="1mo", auto_adjust=True)
        if h.empty or len(h) < MIN_HISTORY:
            return []
        h.index = h.index.tz_localize(None) if h.index.tz else h.index
        close = h["Close"].dropna()

        # Csak 2005-tol
        close = close[close.index >= pd.Timestamp("2005-01-01")]
        if len(close) < 40:
            return []

        t18 = trix(close, 18)
        t6  = trix(close, 6)
        af  = t18 - t6

        # SMA200 havi
        sma200_m = close.rolling(40).mean()  # 40 havi ~SMA40 mint proxy

        signals = []

        for i in range(3, len(af) - max(HOLD_MONTHS)):
            cur  = float(af.iloc[i])
            prv  = float(af.iloc[i-1])

            # Lila->sarga fordulat
            if not (cur > 0 and prv <= 0):
                continue

            date  = af.index[i]
            price = float(close.iloc[i])

            # Lila periodus hossza
            lila_m = 0
            for j in range(i-1, max(0, i-24), -1):
                if float(af.iloc[j]) < 0:
                    lila_m += 1
                else:
                    break

            # SMA200 pozicio
            sma_val = float(sma200_m.iloc[i]) if not pd.isna(sma200_m.iloc[i]) else 0
            sma_pos = round((price - sma_val) / sma_val * 100, 1) if sma_val > 0 else 0

            # Makro rezs
            regime = "unknown"
            if spx_regime is not None:
                r_before = spx_regime[spx_regime.index <= date]
                if len(r_before) > 0:
                    regime = r_before.iloc[-1]

            # Hozamok
            returns = {}
            for m in HOLD_MONTHS:
                fi = i + m
                if fi < len(close):
                    fp = float(close.iloc[fi])
                    returns[f"+{m}mo"] = round((fp - price) / price * 100, 1)
                else:
                    returns[f"+{m}mo"] = None

            # Max drawdown elso 3 honapban
            fw3 = close.iloc[i+1:i+4]
            maxdd = round((float(fw3.min())-price)/price*100, 1) if len(fw3) > 0 else 0

            signals.append({
                "ticker":      ticker,
                "date":        str(date)[:7],
                "price":       price,
                "lila_months": lila_m,
                "sma_pos":     sma_pos,
                "regime":      regime,
                **returns,
                "maxdd_3mo":   maxdd,
            })

        return signals

    except Exception as e:
        return []

def print_stats(df, label, min_count=5):
    if df.empty or len(df) < min_count:
        print(f"  {label}: {len(df)} signal (tul keves)")
        return
    print(f"\n  {label} ({len(df)} signal):")
    for col, lbl in [("+1mo","1 ho"),("+3mo","3 ho"),
                     ("+6mo","6 ho"),("+12mo","12 ho")]:
        v = df[df[col].notna()][col]
        if len(v) == 0:
            continue
        win  = (v > 0).sum()
        b20  = (v > 20).sum()
        b30  = (v > 30).sum()
        l20  = (v < -20).sum()
        pct25 = v.quantile(0.25)
        pct75 = v.quantile(0.75)
        print(f"  {lbl:6}: win {win/len(v)*100:3.0f}% | "
              f"avg {v.mean():+5.1f}% | med {v.median():+5.1f}% | "
              f"p25-p75: {pct25:+.0f}%/{pct75:+.0f}% | "
              f">20%: {b20}/{len(v)} ({b20/len(v)*100:.0f}%) | "
              f">30%: {b30}/{len(v)} ({b30/len(v)*100:.0f}%) | "
              f"<-20%: {l20}/{len(v)} ({l20/len(v)*100:.0f}%)")

def main():
    print(f"\n{'='*65}")
    print(f"  HAVI AF FORDULAT NAGYMINTÁS BACKTEST")
    print(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Universe: {len(UNIVERSE)} reszveny | Idoszak: 2005-tol")
    print(f"{'='*65}\n")

    # SPX rezs letoltes
    print("  SPX makro rezs szamitas...")
    spx_regime = get_spx_regime()

    # Batch scan
    all_signals = []
    errors = 0

    for i, ticker in enumerate(UNIVERSE):
        sigs = backtest_ticker(ticker, spx_regime)
        all_signals.extend(sigs)
        if (i+1) % 10 == 0:
            print(f"  {i+1:3}/{len(UNIVERSE)} | "
                  f"Eddig: {len(all_signals)} signal | "
                  f"Hibas: {errors}")
        time.sleep(0.15)

    if not all_signals:
        print("  Nincs adat!")
        return

    df = pd.DataFrame(all_signals)
    print(f"\n  Osszes signal: {len(df)}")
    print(f"  Idohatai: {df['date'].min()} – {df['date'].max()}")
    print(f"  Erintett reszvenyek: {df['ticker'].nunique()}")

    print(f"\n\n{'='*65}")
    print(f"  1. TELJES MINTA")
    print(f"{'='*65}")
    print_stats(df, "Minden fordulat")

    print(f"\n\n{'='*65}")
    print(f"  2. MAKRO REZS SZERINTI BONTAS")
    print(f"{'='*65}")
    for regime in ["bull","bear","unknown"]:
        dfr = df[df["regime"]==regime]
        print_stats(dfr, f"Fordulat {regime.upper()} piacan")

    print(f"\n\n{'='*65}")
    print(f"  3. LILA PERIODUS HOSSZA SZERINTI BONTAS")
    print(f"{'='*65}")
    print_stats(df[df["lila_months"] <= 3],  "Rovid lila (1-3 ho)")
    print_stats(df[(df["lila_months"] >= 4) &
                   (df["lila_months"] <= 8)], "Kozepes lila (4-8 ho)")
    print_stats(df[df["lila_months"] >= 9],  "Hosszu lila (9+ ho)")

    print(f"\n\n{'='*65}")
    print(f"  4. KOMBINALT SZURO: BULL MAKRO + 6+ HO LILA")
    print(f"{'='*65}")
    df_best = df[(df["regime"]=="bull") & (df["lila_months"]>=6)]
    print_stats(df_best, "Bull makro + 6+ ho lila")

    df_best2 = df[(df["regime"]=="bull") & (df["lila_months"]>=6) &
                  (df["sma_pos"].between(-20, 20))]
    print_stats(df_best2, "Bull makro + 6+ ho lila + SMA200 kozel")

    print(f"\n\n{'='*65}")
    print(f"  5. WORST CASE: BEAR PIAC FORDULATOK")
    print(f"{'='*65}")
    df_bear = df[df["regime"]=="bear"]
    print_stats(df_bear, "Fordulat BEAR piacan (el kell kerulni)")

    print(f"\n\n{'='*65}")
    print(f"  6. EVENKENTI BONTAS (ellenorzes: 2008, 2022)")
    print(f"{'='*65}")
    df["year"] = df["date"].str[:4]
    for year in sorted(df["year"].unique()):
        dfy = df[df["year"]==year]
        v12 = dfy[dfy["+12mo"].notna()]["+12mo"]
        if len(v12) == 0:
            continue
        win = (v12>0).sum()
        print(f"  {year}: {len(dfy):3} signal | "
              f"12ho win: {win/len(v12)*100:3.0f}% | "
              f"avg: {v12.mean():+5.1f}% | "
              f"SPX rezs: {dfy['regime'].mode()[0]}")

    # Mentes
    summary = {
        "date": datetime.datetime.now().isoformat(),
        "universe_size": len(UNIVERSE),
        "total_signals": len(df),
        "date_range": f"{df['date'].min()} – {df['date'].max()}",
        "overall_12mo_win": round((df[df["+12mo"].notna()]["+12mo"]>0).mean()*100,1),
        "bull_6plus_12mo_win": round(
            (df_best[df_best["+12mo"].notna()]["+12mo"]>0).mean()*100,1)
            if len(df_best) > 0 else 0,
    }
    with open("backtest_results.json","w") as f:
        json.dump({"summary": summary, "signals": df.to_dict("records")}, f)

    print(f"\n\n  ✓ Backtest kesz – eredmeny: backtest_results.json")
    print(f"  Osszes signal: {len(df)} | "
          f"12ho overall win: {summary['overall_12mo_win']}% | "
          f"Best filter win: {summary['bull_6plus_12mo_win']}%\n")

if __name__ == "__main__":
    main()
