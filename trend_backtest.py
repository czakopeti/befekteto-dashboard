#!/usr/bin/env python3
"""
trend_backtest.py - Havi AF fordulat trend backtest
=====================================================
Kerdes: ha a havi AF(18,6) lilаbol sargara fordul,
mi lesz az atlagos hozam 1, 3, 6, 12 honappal kesobb?

Hasznalat: python trend_backtest.py NVDA META DDOG CRWD MSFT AAPL AMD TSLA
"""

import sys, datetime
import pandas as pd
import numpy as np
import yfinance as yf

HOLD_MONTHS = [1, 3, 6, 12]

def trix(series, n):
    e1 = series.ewm(span=n, adjust=False).mean()
    e2 = e1.ewm(span=n, adjust=False).mean()
    e3 = e2.ewm(span=n, adjust=False).mean()
    return ((e3 - e3.shift(1)) / e3.shift(1) * 100).fillna(0)

def backtest_af_crossings(ticker):
    print(f"\n{'─'*60}")
    print(f"  {ticker} - Havi AF fordulat backtest")
    print(f"{'─'*60}")

    # Havi adat - 15 ev
    h = yf.Ticker(ticker).history(period="15y", interval="1mo", auto_adjust=True)
    if h.empty or len(h) < 60:
        print("  Nincs eleg adat")
        return None

    h.index = h.index.tz_localize(None) if h.index.tz else h.index
    close = h["Close"].dropna()
    vol   = h["Volume"].dropna()

    t18 = trix(close, 18)
    t6  = trix(close, 6)
    af  = t18 - t6

    sma200_daily = None
    try:
        hd = yf.Ticker(ticker).history(period="15y", auto_adjust=True)
        if not hd.empty:
            hd.index = hd.index.tz_localize(None) if hd.index.tz else hd.index
            sma200_daily = hd["Close"].rolling(200).mean()
    except Exception:
        pass

    signals = []

    for i in range(3, len(af) - max(HOLD_MONTHS)):
        cur = float(af.iloc[i])
        prv = float(af.iloc[i-1])

        # Lila -> sarga fordulat (zero crossing felfelé)
        if not (cur > 0 and prv <= 0):
            continue

        date  = af.index[i]
        price = float(close.iloc[i])

        # Mélység: mennyire volt lila elotte? (momentum)
        lila_depth = abs(float(af.iloc[i-1]))
        lila_months = sum(1 for j in range(max(0,i-12), i) if float(af.iloc[j]) < 0)

        # SMA200 pozicio a fordulat napjan
        sma200_pos = None
        if sma200_daily is not None:
            m_before = sma200_daily[sma200_daily.index <= date]
            if len(m_before) > 0:
                sma200_val = float(m_before.iloc[-1])
                close_daily = hd["Close"][hd.index.tz_localize(None) if hd.index.tz else hd.index <= date]
                if len(close_daily) > 0:
                    price_daily = float(close_daily.iloc[-1])
                    sma200_pos = round((price_daily - sma200_val) / sma200_val * 100, 1)

        # Hozamok
        returns = {}
        for m in HOLD_MONTHS:
            fi = i + m
            if fi < len(close):
                fp = float(close.iloc[fi])
                returns[f"+{m}mo"] = round((fp - price) / price * 100, 1)
            else:
                returns[f"+{m}mo"] = None

        # Max drawdown az elso 3 honapban
        fw3 = close.iloc[i+1:i+4]
        max_dd = round((float(fw3.min()) - price) / price * 100, 1) if len(fw3) > 0 else 0

        signals.append({
            "date":        str(date)[:7],
            "price":       round(price, 2),
            "lila_depth":  round(lila_depth, 3),
            "lila_months": lila_months,
            "sma200_pos":  sma200_pos,
            **returns,
            "max_dd_3mo":  max_dd,
        })

    if not signals:
        print("  Nincs AF fordulat az adott idoszakban")
        return None

    df = pd.DataFrame(signals)

    # Tablazat
    print(f"\n  {len(df)} lila→sarga AF fordulat az elmult 15 evben:\n")
    print(f"  {'Datum':<9} {'Ar':>8} {'Lila':>6} {'SMA200':>7} "
          f"{'1mo':>7} {'3mo':>7} {'6mo':>7} {'12mo':>7} {'MaxDD':>7}")
    print(f"  {'─'*65}")

    for _, r in df.iterrows():
        s200 = f"{r['sma200_pos']:+.0f}%" if r['sma200_pos'] is not None else "  n/a"
        m1  = f"{r['+1mo']:+.0f}%" if r['+1mo'] is not None else "  n/a"
        m3  = f"{r['+3mo']:+.0f}%" if r['+3mo'] is not None else "  n/a"
        m6  = f"{r['+6mo']:+.0f}%" if r['+6mo'] is not None else "  n/a"
        m12 = f"{r['+12mo']:+.0f}%" if r['+12mo'] is not None else "  n/a"
        print(f"  {r['date']:<9} ${r['price']:>7.2f} {r['lila_months']:>4}ho "
              f"{s200:>7} {m1:>7} {m3:>7} {m6:>7} {m12:>7} "
              f"{r['max_dd_3mo']:>+6.1f}%")

    # Statisztikak
    print(f"\n  STATISZTIKA:")
    for col, label in [("+1mo","1 honap"), ("+3mo","3 honap"),
                       ("+6mo","6 honap"), ("+12mo","12 honap")]:
        v = df[df[col].notna()][col]
        if len(v) == 0:
            continue
        win  = (v > 0).sum()
        big  = (v > 20).sum()
        avg  = v.mean()
        med  = v.median()
        print(f"  {label:10}: win {win/len(v)*100:.0f}% | "
              f"atlag {avg:+.1f}% | median {med:+.1f}% | "
              f">20%: {big}/{len(v)} ({big/len(v)*100:.0f}%)")

    # Szuro: SMA200 felett fordul (erossebb jel)
    df_above = df[df["sma200_pos"].notna() & (df["sma200_pos"] > -5)]
    if len(df_above) >= 3:
        print(f"\n  SZURVE: SMA200 kozeleben vagy felette fordult ({len(df_above)} signal):")
        for col, label in [("+3mo","3 honap"), ("+6mo","6 honap"), ("+12mo","12 honap")]:
            v = df_above[df_above[col].notna()][col]
            if len(v) == 0:
                continue
            win = (v > 0).sum()
            big = (v > 20).sum()
            print(f"  {label:10}: win {win/len(v)*100:.0f}% | "
                  f"atlag {v.mean():+.1f}% | >20%: {big}/{len(v)}")

    # Szuro: hosszu lila utan fordul (>6 ho)
    df_deep = df[df["lila_months"] >= 6]
    if len(df_deep) >= 3:
        print(f"\n  SZURVE: 6+ honapig lila volt, majd fordult ({len(df_deep)} signal):")
        for col, label in [("+3mo","3 honap"), ("+6mo","6 honap"), ("+12mo","12 honap")]:
            v = df_deep[df_deep[col].notna()][col]
            if len(v) == 0:
                continue
            win = (v > 0).sum()
            big = (v > 20).sum()
            print(f"  {label:10}: win {win/len(v)*100:.0f}% | "
                  f"atlag {v.mean():+.1f}% | >20%: {big}/{len(v)}")

    return df

def main():
    default = ["NVDA","META","DDOG","CRWD","MSFT","AAPL","AMD","TSLA","AMZN","GOOGL"]
    tickers = sys.argv[1:] if len(sys.argv) > 1 else default

    print(f"\n{'='*60}")
    print(f"  HAVI AF FORDULAT TREND BACKTEST")
    print(f"  {datetime.datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Reszvenyek: {', '.join(tickers)}")
    print(f"{'='*60}")
    print(f"\n  KERDES: Ha a havi AF(18,6) lila->sargara fordul,")
    print(f"  mi lesz az atlagos hozam 1/3/6/12 honappal kesobb?")

    all_dfs = []
    for t in tickers:
        df = backtest_af_crossings(t)
        if df is not None:
            df["ticker"] = t
            all_dfs.append(df)

    if not all_dfs:
        print("\n  Nincs adat.")
        return

    print(f"\n\n{'='*60}")
    print(f"  OSSZESITES – {len(all_dfs)} reszveny, minden fordulat")
    print(f"{'='*60}")

    gdf = pd.concat(all_dfs, ignore_index=True)
    for col, label in [("+1mo","1 honap"),("+3mo","3 honap"),
                       ("+6mo","6 honap"),("+12mo","12 honap")]:
        v = gdf[gdf[col].notna()][col]
        if len(v) == 0:
            continue
        win = (v > 0).sum()
        big = (v > 20).sum()
        print(f"  {label:10}: win {win/len(v)*100:.0f}% | "
              f"atlag {v.mean():+.1f}% | median {v.median():+.1f}% | "
              f">20%: {big}/{len(v)} ({big/len(v)*100:.0f}%)")

    # Legjobb kombinacio: SMA200 kozel + hosszu lila
    df_best = gdf[
        gdf["sma200_pos"].notna() &
        (gdf["sma200_pos"] > -10) &
        (gdf["lila_months"] >= 3)
    ]
    if len(df_best) >= 5:
        print(f"\n  LEGJOBB SZURO: SMA200 kozel (-10%/+20%) + 3+ ho lila ({len(df_best)} signal):")
        for col, label in [("+3mo","3 honap"),("+6mo","6 honap"),("+12mo","12 honap")]:
            v = df_best[df_best[col].notna()][col]
            if len(v) == 0:
                continue
            win = (v > 0).sum()
            big = (v > 20).sum()
            print(f"  {label:10}: win {win/len(v)*100:.0f}% | "
                  f"atlag {v.mean():+.1f}% | >20%: {big}/{len(v)} ({big/len(v)*100:.0f}%)")

    print(f"\n  Backtest kesz. Futtasd tobbre is: python trend_backtest.py RKLB IONQ PLTR NET SNOW\n")

if __name__ == "__main__":
    main()
