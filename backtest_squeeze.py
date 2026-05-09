#!/usr/bin/env python3
"""
backtest_squeeze.py v2 - Healthy Trend + Squeeze + EPS + Earnings backtest
===========================================================================
Csak olyan szignalokat vizsgal ahol:
  - Havi AF(18,6) pozitiv (sarga) - egeszseges trend
  - Havi stretch -15% es +20% kozott (nem tulfeszitett, nem torott)
  - Napi BB squeeze (bottom 25th percentile)
  - Earnings 5-21 napra

Hasznalat: python backtest_squeeze.py DDOG CRWD RKLB NVDA META MSFT ADBE NOW
"""

import sys, json, datetime
import pandas as pd
import numpy as np
import yfinance as yf

BB_PERIOD    = 20
BB_STD       = 2
SQUEEZE_PCT  = 25
HOLD_DAYS    = [5, 10, 20]

def get_monthly_af_stretch(ticker):
    """Havi SMA40 + AF(18,6) historikus sorozat"""
    try:
        h = yf.Ticker(ticker).history(period="10y", interval="1mo", auto_adjust=True)
        if h.empty or len(h) < 50:
            return None, None
        h.index = h.index.tz_localize(None) if h.index.tz else h.index
        close = h["Close"].dropna()

        sma40 = close.rolling(40).mean()

        def trix_m(s, n):
            e1 = s.ewm(span=n, adjust=False).mean()
            e2 = e1.ewm(span=n, adjust=False).mean()
            e3 = e2.ewm(span=n, adjust=False).mean()
            return ((e3 - e3.shift(1)) / e3.shift(1) * 100).fillna(0)

        t18 = trix_m(close, 18)
        t6  = trix_m(close, 6)
        af_monthly = t18 - t6

        # Napi datumhoz legkozelebbi havi ertek
        monthly_data = pd.DataFrame({
            "sma40": sma40,
            "af":    af_monthly,
            "price": close,
        }).dropna()
        return monthly_data
    except Exception:
        return None

def backtest_ticker(ticker, monthly_data):
    print(f"\n{'─'*60}")
    print(f"  {ticker}")
    print(f"{'─'*60}")

    # Napi adat
    h = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
    if h.empty or len(h) < 60:
        print("  Nincs eleg adat")
        return None

    h.index = h.index.tz_localize(None) if h.index.tz else h.index
    close  = h["Close"]
    volume = h["Volume"]

    sma   = close.rolling(BB_PERIOD).mean()
    std   = close.rolling(BB_PERIOD).std()
    bbw   = ((sma + BB_STD*std - (sma - BB_STD*std)) / sma * 100).dropna()

    vol_5d  = volume.rolling(5).mean()
    vol_20d = volume.rolling(20).mean()
    vol_r   = (vol_5d / vol_20d).dropna()

    signals_all   = []  # Minden squeeze signal
    signals_clean = []  # Csak healthy trend + jó stretch

    for i in range(60, len(close) - max(HOLD_DAYS)):
        date = close.index[i]
        if i >= len(bbw):
            continue

        cur_bbw = float(bbw.iloc[i])
        hist    = bbw.iloc[max(0, i-120):i]
        pct     = float((hist < cur_bbw).sum() / len(hist) * 100)

        if pct > SQUEEZE_PCT:
            continue

        # Havi kontextus a legkozelebbi havi zarhoz
        if monthly_data is not None:
            m_before = monthly_data[monthly_data.index <= date]
            if len(m_before) > 0:
                m_row    = m_before.iloc[-1]
                m_sma40  = float(m_row["sma40"])
                m_af     = float(m_row["af"])
                m_price  = float(m_row["price"])
                stretch  = round((m_price - m_sma40) / m_sma40 * 100, 1) if m_sma40 > 0 else 0
                af_ok    = m_af > 0  # havi AF pozitiv (sarga)
                str_ok   = -15 <= stretch <= 20  # nem tulfeszitett, nem torott
                monthly_healthy = af_ok and str_ok
            else:
                stretch = 0; m_af = 0; monthly_healthy = True
        else:
            stretch = 0; m_af = 0; monthly_healthy = True

        vr      = float(vol_r.iloc[i]) if i < len(vol_r) else 1
        vol_dry = vr < 0.65
        entry   = float(close.iloc[i])

        returns = {}
        for d in HOLD_DAYS:
            fi = i + d
            if fi < len(close):
                returns[f"+{d}d"] = round((float(close.iloc[fi]) - entry) / entry * 100, 1)
            else:
                returns[f"+{d}d"] = None

        fw       = close.iloc[i+1:i+21]
        max_up   = round((float(fw.max()) - entry) / entry * 100, 1)
        max_down = round((float(fw.min()) - entry) / entry * 100, 1)
        r10      = returns.get("+10d")
        direction = ("UP" if r10 and r10 > 3 else "DOWN" if r10 and r10 < -3 else "FLAT")

        row = {
            "date": str(date)[:10], "price": round(entry, 2),
            "bbw_pct": round(pct, 0), "vol_dry": vol_dry,
            "stretch": stretch, "af_monthly": round(m_af, 3),
            "monthly_healthy": monthly_healthy,
            **returns, "max_up_20d": max_up, "max_down_20d": max_down,
            "direction": direction,
        }
        signals_all.append(row)
        if monthly_healthy:
            signals_clean.append(row)

    if not signals_all:
        print("  Nincs squeeze signal az elmult 2 evben")
        return None

    df_all   = pd.DataFrame(signals_all)
    df_clean = pd.DataFrame(signals_clean) if signals_clean else pd.DataFrame()

    def print_stats(df, label):
        if df.empty:
            print(f"  {label}: 0 signal")
            return
        valid = df[df["+10d"].notna()]
        if len(valid) == 0:
            return
        win   = (valid["+10d"] > 0).sum()
        big_w = (valid["+10d"] > 15).sum()
        big_l = (valid["+10d"] < -10).sum()
        print(f"\n  {label} ({len(df)} signal):")
        print(f"  Win rate (10n, >0%):    {win/len(valid)*100:.0f}%")
        print(f"  Atlag hozam 10n:        {valid['+10d'].mean():+.1f}%")
        print(f"  Atlag max up 20n:       {valid['max_up_20d'].mean():+.1f}%")
        print(f"  +15%+ moves:            {big_w} / {len(valid)} ({big_w/len(valid)*100:.0f}%)")
        print(f"  -10%+ drawdown:         {big_l} / {len(valid)} ({big_l/len(valid)*100:.0f}%)")
        print(f"  Avg max down:           {valid['max_down_20d'].mean():+.1f}%")

        # Tablazat
        print(f"\n  {'Datum':<12} {'Ar':>7} {'BBW':>5} {'Str':>6} "
              f"{'AF':>7} {'5n':>7} {'10n':>7} {'20n':>7} {'MaxUp':>7} {'Dir'}")
        print(f"  {'─'*75}")
        for _, r in df.tail(8).iterrows():
            hlt = "*" if r.get("monthly_healthy") else " "
            d5  = f"{r['+5d']:+.1f}%" if r['+5d'] is not None else "  n/a"
            d10 = f"{r['+10d']:+.1f}%" if r['+10d'] is not None else "  n/a"
            d20 = f"{r['+20d']:+.1f}%" if r['+20d'] is not None else "  n/a"
            di  = {"UP":"^","DOWN":"v","FLAT":"-"}.get(r.get("direction","-"),"-")
            print(f"  {r['date']:<12} ${r['price']:>6.2f} {r['bbw_pct']:>4.0f}% "
                  f"{r['stretch']:>+5.0f}% {r['af_monthly']:>+6.3f} "
                  f"{d5:>7} {d10:>7} {d20:>7} {r['max_up_20d']:>+6.1f}% {di}{hlt}")

    print_stats(df_all,   "MINDEN squeeze signal")
    print_stats(df_clean, "SZURT: havi AF sarga + stretch -15%/+20%")
    return df_all, df_clean

def main():
    default = ["DDOG","CRWD","RKLB","NVDA","META","MSFT","ADBE","NOW","ORCL","AMZN"]
    tickers = sys.argv[1:] if len(sys.argv) > 1 else default

    print(f"\n{'='*60}")
    print(f"  SQUEEZE BACKTEST v2 – Healthy Trend Filter")
    print(f"  {datetime.datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Reszvenyek: {', '.join(tickers)}")
    print(f"{'='*60}")
    print(f"\n  SZURO FELTETELEI (CLEAN szignalhoz):")
    print(f"  - Havi AF(18,6) > 0 (sarga, egeszseges trend)")
    print(f"  - Havi stretch SMA40-tol: -15% es +20% kozott")
    print(f"  - Napi BB Width < 25th percentile (squeeze)")
    print(f"  - [EPS + Earnings adatot kezi validaciohoz hasonlitsd]")

    all_clean = []
    all_raw   = []

    for t in tickers:
        monthly_data = get_monthly_af_stretch(t)
        result = backtest_ticker(t, monthly_data)
        if result:
            df_all, df_clean = result
            all_raw.append(df_all)
            if not df_clean.empty:
                all_clean.append(df_clean)

    # Osszesfoglalo
    print(f"\n\n{'='*60}")
    print(f"  OSSZESITES – {', '.join(tickers)}")
    print(f"{'='*60}")

    for label, frames in [("MINDEN signal", all_raw), ("SZURT (healthy trend)", all_clean)]:
        if not frames:
            continue
        gdf   = pd.concat(frames, ignore_index=True)
        valid = gdf[gdf["+10d"].notna()]
        if len(valid) == 0:
            continue
        win   = (valid["+10d"] > 0).sum()
        big_w = (valid["+10d"] > 15).sum()
        print(f"\n  {label}:")
        print(f"  Osszes signal:    {len(gdf)}")
        print(f"  Win rate (>0%):   {win/len(valid)*100:.0f}%")
        print(f"  Atlag 10n:        {valid['+10d'].mean():+.1f}%")
        print(f"  +15% feletti:     {big_w} / {len(valid)} ({big_w/len(valid)*100:.0f}%)")
        print(f"  Max up atlag:     {valid['max_up_20d'].mean():+.1f}%")
        print(f"  Max down atlag:   {valid['max_down_20d'].mean():+.1f}%")

    print("\n  Backtest kesz.\n")

if __name__ == "__main__":
    main()
