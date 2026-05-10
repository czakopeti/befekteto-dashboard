"""data.py – yfinance alapu adatleker (FMP nelkul)

Csere az eredeti FMP-fuggou data.py helyett.
scoring.py es run.py valtozatlanul marad.
"""
from __future__ import annotations

import json, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

CACHE_DIR           = Path(__file__).parent.parent / "data" / "cache"
FUNDAMENTALS_CACHE  = CACHE_DIR / "fundamentals.json"
PROFILES_CACHE      = CACHE_DIR / "profiles.json"
CACHE_TTL_DAYS      = 30
MAX_FETCHES_PER_RUN = 120   # yfinance-nel nincs API limit

SP500_URL      = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_FALLBACK = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

# ── Universe ───────────────────────────────────────────────
def get_universe() -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    try:
        resp = requests.get(SP500_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        df   = pd.read_html(resp.text)[0]
        tickers = df["Symbol"].astype(str).tolist()
        if len(tickers) > 400:
            print(f"[universe] Wikipedia: {len(tickers)} ticker")
            return tickers
    except Exception as e:
        print(f"[universe] Wikipedia hiba: {e}")
    try:
        from io import StringIO
        resp = requests.get(SP500_FALLBACK, headers=headers, timeout=15)
        resp.raise_for_status()
        df   = pd.read_csv(StringIO(resp.text))
        tickers = df["Symbol"].astype(str).tolist()
        if len(tickers) > 400:
            print(f"[universe] Fallback CSV: {len(tickers)} ticker")
            return tickers
    except Exception as e:
        print(f"[universe] Fallback hiba: {e}")
    return []

# ── Cache I/O ──────────────────────────────────────────────
def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}

def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))

def load_cache() -> tuple[dict, dict]:
    return _load_json(FUNDAMENTALS_CACHE), _load_json(PROFILES_CACHE)

def save_cache(fundamentals: dict, profiles: dict) -> None:
    _save_json(FUNDAMENTALS_CACHE, fundamentals)
    _save_json(PROFILES_CACHE, profiles)

def is_stale(entry: Optional[dict], ttl_days: int = CACHE_TTL_DAYS) -> bool:
    if not entry or "fetched" not in entry:
        return True
    try:
        from datetime import timedelta
        fetched = datetime.fromisoformat(entry["fetched"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched > timedelta(days=ttl_days)
    except Exception:
        return True

# ── yfinance adatlekerés ───────────────────────────────────
def fetch_fundamentals(symbol: str) -> Optional[dict]:
    try:
        t    = yf.Ticker(symbol)
        info = t.info or {}

        def safe_val(df, row_names, col=0):
            if df is None or df.empty:
                return None
            names = [row_names] if isinstance(row_names, str) else row_names
            for r in names:
                try:
                    v = df.loc[r].iloc[col]
                    return float(v) if pd.notna(v) else None
                except Exception:
                    continue
            return None

        # Kimutatások – fallback-kel
        inc = t.financials
        if inc is None or inc.empty:
            try: inc = t.income_stmt
            except Exception: inc = None
        bs = t.balance_sheet
        if bs is None or bs.empty:
            try: bs = t.get_balance_sheet()
            except Exception: bs = None
        cf = t.cashflow
        if cf is None or cf.empty:
            try: cf = t.cash_flow
            except Exception: cf = None

        REV = ["Total Revenue","TotalRevenue","Revenue","Net Revenue"]
        GP  = ["Gross Profit","GrossProfit"]
        OI  = ["Operating Income","OperatingIncome","EBIT","Ebit"]

        revenue_ttm  = safe_val(inc, REV) or info.get("totalRevenue")
        gross_profit = safe_val(inc, GP)
        op_income    = safe_val(inc, OI)
        net_income   = safe_val(inc, ["Net Income","NetIncome",
                                       "Net Income Common Stockholders"])
        rd_expense   = safe_val(inc, ["Research And Development",
                                       "Research Development"])
        interest_exp = safe_val(inc, ["Interest Expense","InterestExpense"])

        total_assets = safe_val(bs, ["Total Assets","TotalAssets"]) or info.get("totalAssets")
        total_equity = (safe_val(bs, ["Stockholders Equity",
                                       "Total Equity Gross Minority Interest",
                                       "Total Stockholder Equity"])
                        or info.get("bookValue"))
        total_debt   = safe_val(bs, ["Total Debt","TotalDebt","Long Term Debt"])
        curr_liab    = safe_val(bs, ["Current Liabilities","TotalCurrentLiabilities"])
        op_cf        = safe_val(cf, ["Operating Cash Flow",
                                      "Total Cash From Operating Activities"])
        capex        = safe_val(cf, ["Capital Expenditure","Capital Expenditures"])
        divs         = safe_val(cf, ["Common Stock Dividends Paid","Dividends Paid"])

        if not revenue_ttm or revenue_ttm <= 0:
            return None
        if not total_assets or total_assets <= 0:
            return None

        gross_margin = (gross_profit/revenue_ttm) if gross_profit else info.get("grossMargins")
        op_margin    = (op_income/revenue_ttm)    if op_income   else info.get("operatingMargins")
        net_margin   = (net_income/revenue_ttm)   if net_income  else info.get("profitMargins")

        gp_to_assets = (gross_profit/total_assets) if gross_profit else (
            (gross_margin * revenue_ttm / total_assets) if gross_margin else None)

        roa  = (net_income/total_assets) if net_income else info.get("returnOnAssets")
        roe  = ((net_income/total_equity) if (net_income and total_equity and total_equity>0)
                else info.get("returnOnEquity"))
        ce   = (total_assets - curr_liab) if (total_assets and curr_liab) else None
        roce = ((op_income or 0)/ce) if (op_income and ce and ce>0) else None

        de_raw = info.get("debtToEquity")
        if de_raw is not None:
            debt_to_equity = de_raw/100.0 if de_raw > 10 else de_raw
        elif total_debt and total_equity and total_equity > 0:
            debt_to_equity = total_debt/total_equity
        else:
            debt_to_equity = None

        fcf = None
        if op_cf is not None and capex is not None:
            fcf = op_cf + capex
        elif info.get("freeCashflow"):
            fcf = info.get("freeCashflow")

        fcf_margin = (fcf/revenue_ttm) if (fcf and revenue_ttm) else None
        fcf_to_ocf = (fcf/op_cf)       if (fcf and op_cf and op_cf!=0) else None
        rd_intensity = (rd_expense/revenue_ttm) if (rd_expense and revenue_ttm) else None
        interest_coverage = None
        if op_income and interest_exp and interest_exp != 0:
            interest_coverage = op_income / abs(interest_exp)
        payout_ratio = info.get("payoutRatio")

        # Historikus
        rev_cagr_3y = None; gm_trend_3y = None
        op_margin_trend_2y = None; operating_income_positive_count = 0
        try:
            if inc is not None and not inc.empty and inc.shape[1] >= 4:
                def sv(rnames, ci):
                    if ci >= inc.shape[1]: return None
                    for rn in ([rnames] if isinstance(rnames,str) else rnames):
                        try:
                            v = inc.loc[rn].iloc[ci]
                            return float(v) if pd.notna(v) else None
                        except Exception: continue
                    return None
                r0=sv(REV,0); r3=sv(REV,3)
                if r0 and r3 and r3>0: rev_cagr_3y=(r0/r3)**(1/3)-1
                gp0=sv(GP,0); gp2=sv(GP,2); r2=sv(REV,2)
                if gp0 and gp2 and r0 and r2 and r0>0 and r2>0:
                    gm_trend_3y=(gp0/r0)-(gp2/r2)
                oi0=sv(OI,0); oi1=sv(OI,1); r1=sv(REV,1)
                if oi0 and oi1 and r0 and r1 and r0>0 and r1>0:
                    op_margin_trend_2y=(oi0/r0)-(oi1/r1)
                for ci in range(min(4, inc.shape[1])):
                    v=sv(OI,ci)
                    if v and v>0: operating_income_positive_count+=1
        except Exception:
            pass

        return {
            "symbol": symbol,
            "fetched": datetime.now(timezone.utc).isoformat(),
            "revenue_ttm": revenue_ttm, "gp_to_assets": gp_to_assets,
            "op_margin_ttm": op_margin, "net_margin_ttm": net_margin,
            "debt_to_equity": debt_to_equity,
            "operating_income_positive_count": operating_income_positive_count,
            "gross_margin_ttm": gross_margin, "roa_ttm": roa,
            "roe_ttm": roe, "roce_ttm": roce,
            "fcf_margin": fcf_margin, "fcf_to_ocf": fcf_to_ocf,
            "rev_cagr_3y": rev_cagr_3y, "gm_trend_3y": gm_trend_3y,
            "op_margin_trend_2y": op_margin_trend_2y,
            "rd_intensity": rd_intensity,
            "interest_coverage": interest_coverage,
            "payout_ratio": payout_ratio,
        }
    except Exception as e:
        print(f"[fetch] {symbol} hiba: {e}")
        return None

def fetch_profile(symbol: str) -> Optional[dict]:
    try:
        info = yf.Ticker(symbol).info or {}
        return {
            "symbol":   symbol,
            "name":     info.get("shortName") or info.get("longName") or symbol,
            "sector":   info.get("sector") or "Unknown",
            "industry": info.get("industry") or "Unknown",
            "fetched":  datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None

def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict]:
    out = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info or {}
            price  = info.get("currentPrice") or info.get("regularMarketPrice")
            mktcap = info.get("marketCap")
            if price or mktcap:
                out[sym] = {"price": price, "market_cap": mktcap}
            time.sleep(0.05)
        except Exception:
            continue
    return out

# ── Orchestration ──────────────────────────────────────────
def refresh_data(universe: list[str]) -> tuple[dict, dict, dict]:
    fundamentals, profiles = load_cache()

    stale = [s for s in universe
             if is_stale(fundamentals.get(s))
             or fundamentals.get(s, {}).get("broken")]  # broken = mindig ujra
    print(f"[refresh] {len(stale)} stale / {len(universe)} total")

    refreshed = 0
    for symbol in stale:
        if refreshed >= MAX_FETCHES_PER_RUN:
            print(f"[refresh] MAX_FETCHES_PER_RUN={MAX_FETCHES_PER_RUN} elert")
            break
        data = fetch_fundamentals(symbol)
        if data:
            fundamentals[symbol] = data
            refreshed += 1
        else:
            fundamentals[symbol] = {
                "symbol": symbol,
                "fetched": datetime.now(timezone.utc).isoformat(),
                "broken": True,
            }
        if refreshed % 20 == 0:
            print(f"[refresh] {refreshed} frissitve...")
        time.sleep(0.2)

    print(f"[refresh] Frissitve: {refreshed}")

    missing_profiles = [s for s in universe
                        if s in fundamentals and s not in profiles]
    for sym in missing_profiles[:60]:
        p = fetch_profile(sym)
        if p:
            profiles[sym] = p
        time.sleep(0.1)

    save_cache(fundamentals, profiles)

    scoreable = [s for s in universe
                 if s in fundamentals and not fundamentals[s].get("broken")]
    quotes = fetch_quotes_batch(scoreable[:200])
    print(f"[refresh] Quotes: {len(quotes)}")

    return fundamentals, profiles, quotes
