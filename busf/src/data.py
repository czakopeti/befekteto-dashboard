"""FMP-backed data layer with on-disk cache.

Strategy for the free FMP tier (250 calls/day):
- Fundamentals (ratios-ttm + income history) cached 30 days.
- Per weekly run, only stale tickers are re-fetched.
- Prices/market caps refreshed weekly via batch /quote endpoint.
- MAX_FETCHES_PER_RUN caps API usage to stay safely under the daily limit.

Bootstrap: first 4-6 runs populate the cache. Use workflow_dispatch to run
manually a few times right after first deploy.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


FMP_KEY = os.environ.get("FMP_API_KEY", "")
BASE = "https://financialmodelingprep.com/api/v3"

CACHE_DIR = Path("data/cache")
FUNDAMENTALS_CACHE = CACHE_DIR / "fundamentals.json"
PROFILES_CACHE = CACHE_DIR / "profiles.json"

CACHE_TTL_DAYS = 30
MAX_FETCHES_PER_RUN = 100  # 100 tickers × 2 calls = 200 calls, +50 for prices/profiles
RATE_LIMIT_PAUSE = 0.3     # seconds between calls to be polite

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_FALLBACK_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


# ---------- Universe ----------

def get_universe() -> list[str]:
    """Fetch S&P 500 tickers. Tries Wikipedia first, then a GitHub CSV fallback."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }

    # Primary: Wikipedia
    try:
        resp = requests.get(SP500_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        df = tables[0]
        tickers = df["Symbol"].astype(str).tolist()
        if len(tickers) > 400:
            return tickers
    except Exception as e:
        print(f"[universe] Wikipedia failed: {e}")

    # Fallback: datasets/s-and-p-500-companies on GitHub
    try:
        resp = requests.get(SP500_FALLBACK_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        tickers = df["Symbol"].astype(str).tolist()
        if len(tickers) > 400:
            print(f"[universe] Using fallback CSV: {len(tickers)} tickers")
            return tickers
    except Exception as e:
        print(f"[universe] Fallback failed: {e}")

    print("[universe] All sources failed")
    return []


# ---------- Cache I/O ----------

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
    """Returns (fundamentals_cache, profiles_cache)."""
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


# ---------- FMP HTTP ----------

class RateLimitExceeded(Exception):
    pass


def _fmp_get(path: str, params: Optional[dict] = None) -> Optional[list | dict]:
    """GET an FMP endpoint with retries. Returns parsed JSON or None on failure."""
    if not FMP_KEY:
        raise RuntimeError("FMP_API_KEY env var not set")

    full_params = dict(params or {})
    full_params["apikey"] = FMP_KEY
    url = f"{BASE}/{path}"

    for attempt in range(3):
        try:
            r = requests.get(url, params=full_params, timeout=15)
            if r.status_code == 429:
                # Daily limit hit — bail out, do not burn retries
                raise RateLimitExceeded(f"429 on {path}")
            if r.status_code == 403:
                # Endpoint not on free tier — log and skip
                print(f"[fmp] 403 (forbidden / not on free tier): {path}")
                return None
            r.raise_for_status()
            time.sleep(RATE_LIMIT_PAUSE)
            return r.json()
        except RateLimitExceeded:
            raise
        except Exception as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"[fmp] {path} failed after retries: {e}")
            return None
    return None


# ---------- Fetchers ----------

def fetch_fundamentals(symbol: str) -> Optional[dict]:
    """Fetch ratios-ttm + income statement history for one ticker.

    Returns a flat dict ready for scoring, or None if data is unusable.
    Costs 2 FMP API calls.
    """
    ratios = _fmp_get(f"ratios-ttm/{symbol}")
    if not ratios or not isinstance(ratios, list) or not ratios:
        return None
    r = ratios[0]

    income = _fmp_get(f"income-statement/{symbol}", {"limit": 5, "period": "annual"})
    if not income or not isinstance(income, list):
        income = []

    # --- TTM ratios from ratios-ttm ---
    gross_margin = r.get("grossProfitMarginTTM")
    asset_turnover = r.get("assetTurnoverTTM")
    gp_to_assets = (
        gross_margin * asset_turnover
        if (gross_margin is not None and asset_turnover is not None) else None
    )

    op_margin = r.get("operatingProfitMarginTTM")
    net_margin = r.get("netProfitMarginTTM")
    roa = r.get("returnOnAssetsTTM")
    roe = r.get("returnOnEquityTTM")
    roce = r.get("returnOnCapitalEmployedTTM")
    debt_to_equity = r.get("debtEquityRatioTTM")
    interest_coverage = r.get("interestCoverageTTM")

    # FCF margin = FCF/share / Revenue/share
    fcf_per_share = r.get("freeCashFlowPerShareTTM")
    rev_per_share = r.get("revenuePerShareTTM")
    fcf_margin = (
        fcf_per_share / rev_per_share
        if (fcf_per_share and rev_per_share and rev_per_share != 0) else None
    )
    fcf_to_ocf = r.get("freeCashFlowOperatingCashFlowRatioTTM")
    payout_ratio = r.get("payoutRatioTTM")

    # --- Growth + margin trend from income history (latest first) ---
    rev_cagr_3y = None
    gm_trend_3y = None
    op_margin_trend_2y = None
    rd_intensity = None
    revenue_ttm = None
    operating_income_positive_count = 0

    if income:
        # latest revenue
        revenue_ttm = income[0].get("revenue")

        # 3y revenue CAGR (need at least 4 annual reports)
        if len(income) >= 4:
            rev_now = income[0].get("revenue")
            rev_3y_ago = income[3].get("revenue")
            if rev_now and rev_3y_ago and rev_now > 0 and rev_3y_ago > 0:
                rev_cagr_3y = (rev_now / rev_3y_ago) ** (1 / 3) - 1

        # Gross margin trend (latest minus 3y ago)
        if len(income) >= 3:
            try:
                gm_now = (income[0].get("grossProfit") or 0) / (income[0].get("revenue") or 1)
                gm_3y = (income[2].get("grossProfit") or 0) / (income[2].get("revenue") or 1)
                if gm_now > 0 and gm_3y > 0:
                    gm_trend_3y = gm_now - gm_3y
            except Exception:
                pass

        # Op margin trend over last 2 years (latest minus 2y ago)
        if len(income) >= 2:
            try:
                op_now = income[0].get("operatingIncomeRatio")
                op_2y = income[1].get("operatingIncomeRatio")
                if op_now is not None and op_2y is not None:
                    op_margin_trend_2y = op_now - op_2y
            except Exception:
                pass

        # R&D intensity (latest year)
        rd = income[0].get("researchAndDevelopmentExpenses")
        rev = income[0].get("revenue")
        if rd and rev and rev > 0:
            rd_intensity = rd / rev

        # Operating income positive count (last 4 years)
        for y in income[:4]:
            if (y.get("operatingIncome") or 0) > 0:
                operating_income_positive_count += 1

    return {
        "symbol": symbol,
        "fetched": datetime.now(timezone.utc).isoformat(),
        # Quality gate signals
        "revenue_ttm": revenue_ttm,
        "gp_to_assets": gp_to_assets,
        "op_margin_ttm": op_margin,
        "debt_to_equity": debt_to_equity,
        "operating_income_positive_count": operating_income_positive_count,
        # Profitability
        "gross_margin_ttm": gross_margin,
        "net_margin_ttm": net_margin,
        "roa_ttm": roa,
        "roe_ttm": roe,
        "roce_ttm": roce,
        # Cash quality
        "fcf_margin": fcf_margin,
        "fcf_to_ocf": fcf_to_ocf,
        # Growth
        "rev_cagr_3y": rev_cagr_3y,
        "gm_trend_3y": gm_trend_3y,
        "op_margin_trend_2y": op_margin_trend_2y,
        "rd_intensity": rd_intensity,
        # Risk
        "interest_coverage": interest_coverage,
        # Capital allocation
        "payout_ratio": payout_ratio,
    }


def fetch_profile(symbol: str) -> Optional[dict]:
    """Fetch sector/industry/name. Cached separately, refreshed only if missing."""
    p = _fmp_get(f"profile/{symbol}")
    if p and isinstance(p, list) and p:
        prof = p[0]
        return {
            "symbol": symbol,
            "name": prof.get("companyName") or symbol,
            "sector": prof.get("sector") or "Unknown",
            "industry": prof.get("industry") or "Unknown",
            "fetched": datetime.now(timezone.utc).isoformat(),
        }
    return None


def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict]:
    """Batch quote endpoint. Returns {symbol: {price, marketCap, ...}}.

    Chunks symbols to ~50 per request to avoid URL length issues.
    """
    out: dict[str, dict] = {}
    chunk_size = 50
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        result = _fmp_get(f"quote/{','.join(chunk)}")
        if result and isinstance(result, list):
            for q in result:
                sym = q.get("symbol")
                if sym:
                    out[sym] = {
                        "price": q.get("price"),
                        "market_cap": q.get("marketCap"),
                        "volume": q.get("volume"),
                    }
    return out


# ---------- Orchestration ----------

def refresh_data(universe: list[str]) -> tuple[dict, dict, dict]:
    """Refresh stale fundamentals + profiles + all prices. Respects rate limits.

    Returns (fundamentals_cache, profiles_cache, quotes).
    """
    fundamentals, profiles = load_cache()
    fetches_used = 0

    # 1. Identify stale or missing fundamentals
    stale = [s for s in universe if is_stale(fundamentals.get(s))]
    print(f"[refresh] {len(stale)} fundamentals stale or missing (of {len(universe)})")

    # 2. Fetch fundamentals (2 calls each), capped at MAX_FETCHES_PER_RUN
    refreshed = 0
    try:
        for symbol in stale:
            if refreshed >= MAX_FETCHES_PER_RUN:
                print(f"[refresh] Reached MAX_FETCHES_PER_RUN={MAX_FETCHES_PER_RUN}, stopping fundamentals")
                break
            data = fetch_fundamentals(symbol)
            fetches_used += 2
            if data:
                fundamentals[symbol] = data
                refreshed += 1
            else:
                # Mark as attempted to avoid hammering the same broken ticker
                fundamentals[symbol] = {
                    "symbol": symbol,
                    "fetched": datetime.now(timezone.utc).isoformat(),
                    "broken": True,
                }
    except RateLimitExceeded:
        print("[refresh] FMP rate limit (429) hit during fundamentals — saving partial state")

    print(f"[refresh] Fundamentals refreshed: {refreshed}, FMP calls used: ~{fetches_used}")

    # 3. Fill in missing profiles for tickers with fundamentals
    missing_profiles = [s for s in universe if s in fundamentals and s not in profiles]
    try:
        for symbol in missing_profiles[:50]:  # limit per run
            p = fetch_profile(symbol)
            if p:
                profiles[symbol] = p
    except RateLimitExceeded:
        print("[refresh] Rate limit hit during profiles")

    # 4. Save before fetching quotes (so we don't lose progress)
    save_cache(fundamentals, profiles)

    # 5. Fetch fresh quotes for tickers we'll actually score
    scoreable = [s for s in universe
                 if s in fundamentals and not fundamentals[s].get("broken")]
    quotes = {}
    try:
        quotes = fetch_quotes_batch(scoreable)
        print(f"[refresh] Quotes fetched for {len(quotes)} tickers")
    except RateLimitExceeded:
        print("[refresh] Rate limit hit during quotes — using stale prices if any")

    return fundamentals, profiles, quotes
