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

CACHE_DIR           = Path("data/cache")
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
    """
    TTM ratiok + 4 eves tortenet yfinance-bol.
    Megfelel az eredeti FMP-alapu fetch_fundamentals() kimenetevel.
    """
    try:
        t    = yf.Ticker(symbol)
        info = t.info or {}

        # --- TTM ratios info-bol ---
        gross_margin    = info.get("grossMargins")
        op_margin       = info.get("operatingMargins")
        net_margin      = info.get("profitMargins")
        roa             = info.get("returnOnAssets")
        roe             = info.get("returnOnEquity")
        de              = info.get("debtToEquity")
        revenue_ttm     = info.get("totalRevenue")
        total_assets    = info.get("totalAssets")
        payout_ratio    = info.get("payoutRatio")
        rd_raw          = info.get("researchAndDevelopment")
        fcf_raw         = info.get("freeCashflow")
        ocf_raw         = info.get("operatingCashflow")
        ebitda          = info.get("ebitda")

        # GP/A = gross_margin * (revenue / total_assets)
        gp_to_assets = None
        if gross_margin and revenue_ttm and total_assets and total_assets > 0:
            gp_to_assets = gross_margin * (revenue_ttm / total_assets)

        # ROCE proxy = EBITDA / Total Assets
        roce = None
        if ebitda and total_assets and total_assets > 0:
            roce = ebitda / total_assets

        # FCF margin + FCF/OCF
        fcf_margin = None
        fcf_to_ocf = None
        if fcf_raw and revenue_ttm and revenue_ttm > 0:
            fcf_margin = fcf_raw / revenue_ttm
        if fcf_raw and ocf_raw and ocf_raw != 0:
            fcf_to_ocf = fcf_raw / ocf_raw

        # R&D intensity
        rd_intensity = None
        if rd_raw and revenue_ttm and revenue_ttm > 0:
            rd_intensity = rd_raw / revenue_ttm

        # Interest coverage (EBITDA / Interest)
        interest_coverage = None
        interest_exp = info.get("interestExpense")
        if ebitda and interest_exp and interest_exp < 0:
            interest_coverage = ebitda / abs(interest_exp)
        elif ebitda and interest_exp and interest_exp > 0:
            interest_coverage = ebitda / interest_exp

        # D/E normalizalas (yfinance 100x-os skalan adja)
        debt_to_equity = None
        if de is not None:
            debt_to_equity = de / 100.0 if de > 10 else de

        # --- Historikus novekedesi adatok (4 ev) ---
        rev_cagr_3y                  = None
        gm_trend_3y                  = None
        op_margin_trend_2y           = None
        operating_income_positive_count = 0

        try:
            inc = t.income_stmt  # negyedeves
            if inc is not None and not inc.empty:
                # Annual proxy: utolso 4 sor (yearly)
                ann = t.financials  # eves P&L
                if ann is not None and not ann.empty and ann.shape[1] >= 4:
                    cols = ann.columns.tolist()

                    def safe(df, row, col):
                        try:
                            v = df.loc[row, col]
                            return float(v) if pd.notna(v) else None
                        except Exception:
                            return None

                    rev0 = safe(ann, "Total Revenue", cols[0])
                    rev3 = safe(ann, "Total Revenue", cols[3]) if len(cols) > 3 else None
                    if rev0 and rev3 and rev3 > 0:
                        rev_cagr_3y = (rev0 / rev3) ** (1/3) - 1

                    gp0 = safe(ann, "Gross Profit", cols[0])
                    gp2 = safe(ann, "Gross Profit", cols[2]) if len(cols) > 2 else None
                    r0  = safe(ann, "Total Revenue", cols[0])
                    r2  = safe(ann, "Total Revenue", cols[2]) if len(cols) > 2 else None
                    if gp0 and gp2 and r0 and r2 and r0 > 0 and r2 > 0:
                        gm_trend_3y = (gp0/r0) - (gp2/r2)

                    oi0 = safe(ann, "Operating Income", cols[0])
                    oi1 = safe(ann, "Operating Income", cols[1]) if len(cols) > 1 else None
                    if oi0 and oi1 and r0 and r0 > 0:
                        r1 = safe(ann, "Total Revenue", cols[1])
                        if r1 and r1 > 0:
                            op_margin_trend_2y = (oi0/r0) - (oi1/r1)

                    # Op income positive count (utolso 4 ev)
                    for c in cols[:4]:
                        oi = safe(ann, "Operating Income", c)
                        if oi and oi > 0:
                            operating_income_positive_count += 1
        except Exception:
            pass

        # Ha nincs bevatel adat -> broken
        if not revenue_ttm or revenue_ttm <= 0:
            return None

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
            "gm_trend_3y": gm_trend_3y,
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

    stale = [s for s in universe if is_stale(fundamentals.get(s))]
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
