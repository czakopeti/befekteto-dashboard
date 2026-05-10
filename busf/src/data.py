"""data.py – yfinance .info alapu (penzugyi kimutatások nelkul)

GP/A kiszamitasa: GrossMargin * (ROA / NetMargin) = GrossMargin * AssetTurnover
Ez csak .info mezőket használ, amelyek megbizhatoan elerhetek.
"""
from __future__ import annotations

import json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

CACHE_DIR          = Path(__file__).parent.parent / "data" / "cache"
FUNDAMENTALS_CACHE = CACHE_DIR / "fundamentals.json"
PROFILES_CACHE     = CACHE_DIR / "profiles.json"
CACHE_TTL_DAYS     = 30
MAX_FETCHES_PER_RUN = 520

SP500_URL      = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_FALLBACK = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

def get_universe() -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    try:
        resp = requests.get(SP500_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        df = pd.read_html(resp.text)[0]
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
        df = pd.read_csv(StringIO(resp.text))
        tickers = df["Symbol"].astype(str).tolist()
        if len(tickers) > 400:
            print(f"[universe] Fallback CSV: {len(tickers)} ticker")
            return tickers
    except Exception as e:
        print(f"[universe] Fallback hiba: {e}")
    return []

def _load_json(path: Path) -> dict:
    if path.exists():
        try: return json.loads(path.read_text())
        except Exception: return {}
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
        fetched = datetime.fromisoformat(entry["fetched"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched > timedelta(days=ttl_days)
    except Exception:
        return True

def fetch_fundamentals(symbol: str) -> Optional[dict]:
    """
    Csak yfinance .info mezőkből számít — megbízható, nem üresedik ki.
    
    GP/A = GrossMargin × AssetTurnover
         = GrossMargin × (ROA / NetMargin)
    
    Ez ekvivalens a Novy-Marx GP/A mutatóval, de financial statement nélkül.
    """
    try:
        info = yf.Ticker(symbol).info or {}
        if not info:
            return None

        revenue_ttm   = info.get("totalRevenue")
        gross_margin  = info.get("grossMargins")
        op_margin     = info.get("operatingMargins")
        net_margin    = info.get("profitMargins")
        roa           = info.get("returnOnAssets")
        roe           = info.get("returnOnEquity")
        de_raw        = info.get("debtToEquity")
        fcf           = info.get("freeCashflow")
        ocf           = info.get("operatingCashflow")
        payout_ratio  = info.get("payoutRatio")
        ebitda        = info.get("ebitda")
        total_debt    = info.get("totalDebt")
        market_cap    = info.get("marketCap")
        revenue_growth = info.get("revenueGrowth")  # 1 eves

        # Alapszűrés
        if not revenue_ttm or revenue_ttm < 200e6:
            return None
        if gross_margin is None or op_margin is None:
            return None

        # GP/A = GrossMargin × AssetTurnover = GrossMargin × (ROA / NetMargin)
        gp_to_assets = None
        if gross_margin and roa and net_margin and abs(net_margin) > 0.001:
            asset_turnover = roa / net_margin
            gp_to_assets   = gross_margin * asset_turnover
        elif gross_margin and roa:
            # Fallback: gross_margin * roa / 0.10 (átlagos netmargin becslés)
            gp_to_assets = gross_margin * roa / 0.10

        # D/E normalizálás (yfinance 100x skálán adhatja)
        debt_to_equity = None
        if de_raw is not None:
            debt_to_equity = de_raw / 100.0 if de_raw > 10 else de_raw

        # FCF mutatók
        fcf_margin = (fcf / revenue_ttm) if (fcf and revenue_ttm) else None
        fcf_to_ocf = (fcf / ocf)         if (fcf and ocf and ocf != 0) else None

        # Op income positive – becsléssel ha nincs kimutatás
        # Ha op_margin > 0 az elmúlt évben, feltételezzük 3/4 pozitív
        operating_income_positive_count = 3 if (op_margin and op_margin > 0) else 0

        # ROCE proxy = EBITDA / EV (enterprise value proxy)
        ev = info.get("enterpriseValue")
        roce = (ebitda / ev) if (ebitda and ev and ev > 0) else None

        # Rev CAGR – yfinance 1 éves growth-ból nem számítható 3 év,
        # de ha van revenueGrowth, azt megőrizzük
        rev_cagr_3y = revenue_growth  # csak 1 éves proxy

        # R&D (info-ban nincs megbízhatóan, kihagyjuk)
        rd_intensity = None

        # Interest coverage
        interest_exp = info.get("interestExpense")
        interest_coverage = None
        if ebitda and interest_exp and interest_exp != 0:
            interest_coverage = ebitda / abs(interest_exp)

        return {
            "symbol":   symbol,
            "fetched":  datetime.now(timezone.utc).isoformat(),
            "revenue_ttm":   revenue_ttm,
            "gp_to_assets":  gp_to_assets,
            "op_margin_ttm": op_margin,
            "net_margin_ttm": net_margin,
            "debt_to_equity": debt_to_equity,
            "operating_income_positive_count": operating_income_positive_count,
            "gross_margin_ttm": gross_margin,
            "roa_ttm":   roa,
            "roe_ttm":   roe,
            "roce_ttm":  roce,
            "fcf_margin": fcf_margin,
            "fcf_to_ocf": fcf_to_ocf,
            "rev_cagr_3y": rev_cagr_3y,
            "gm_trend_3y": None,
            "op_margin_trend_2y": None,
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

def refresh_data(universe: list[str]) -> tuple[dict, dict, dict]:
    fundamentals, profiles = load_cache()

    stale = [s for s in universe
             if is_stale(fundamentals.get(s))
             or fundamentals.get(s, {}).get("broken")]

    print(f"[refresh] {len(stale)} stale+broken / {len(universe)} total")

    refreshed = 0
    for symbol in stale:
        if refreshed >= MAX_FETCHES_PER_RUN:
            print(f"[refresh] MAX={MAX_FETCHES_PER_RUN} elert")
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
        if refreshed % 25 == 0 and refreshed > 0:
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
    quotes = fetch_quotes_batch(scoreable)
    print(f"[refresh] Quotes: {len(quotes)}")

    return fundamentals, profiles, quotes
