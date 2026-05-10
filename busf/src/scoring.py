"""Quality gate, BCS composite score (0-100), and lifecycle labeling.

Design:
- Gate: hard binary filter (Novy-Marx GP/A + basic safety + profitability).
- Score: four pillars × 25 points each. Linear normalization with caps.
- Lifecycle: informational label only (Emerging / Scaling / Compounding / Mature).

Missing metrics gracefully award 0 points for that subcomponent rather
than crashing or giving false signals.
"""
from __future__ import annotations

from typing import Optional


# ---------- Quality gate ----------

def passes_quality_gate(m: dict, market_cap: Optional[float]) -> tuple[bool, str]:
    """Hard binary filter. Returns (passes, reason_if_rejected)."""
    if not m or m.get("broken"):
        return False, "no data"

    if not market_cap or market_cap < 2e9:
        return False, f"market_cap {market_cap}"

    rev = m.get("revenue_ttm") or 0
    if rev < 200e6:
        return False, f"revenue {rev}"

    gpa = m.get("gp_to_assets")
    if gpa is None or gpa < 0.20:
        return False, f"gp_to_assets {gpa}"

    op_margin = m.get("op_margin_ttm")
    if op_margin is None or op_margin <= 0:
        return False, f"op_margin {op_margin}"

    pos_count = m.get("operating_income_positive_count") or 0
    if pos_count < 3:
        return False, f"op_income_positive_years {pos_count}/4"

    de = m.get("debt_to_equity")
    if de is not None and de > 2.0:
        return False, f"debt_equity {de}"

    return True, ""


# ---------- Helpers ----------

def _ramp(x: Optional[float], floor: float, ceil: float) -> float:
    """Linear ramp 0..1: x<=floor -> 0, x>=ceil -> 1."""
    if x is None:
        return 0.0
    if x <= floor:
        return 0.0
    if x >= ceil:
        return 1.0
    return (x - floor) / (ceil - floor)


# ---------- Lifecycle ----------

def lifecycle_of(m: dict) -> str:
    g = m.get("rev_cagr_3y") or 0
    roa = m.get("roa_ttm") or 0
    roe = m.get("roe_ttm") or 0
    if g > 0.25:
        return "Emerging"
    if g > 0.10:
        return "Scaling"
    if roa > 0.10 or roe > 0.15:
        return "Compounding"
    return "Mature"


# ---------- Composite Score ----------

def score(m: dict) -> tuple[int, dict]:
    """Composite 0-100 score. Returns (total, pillar_breakdown).

    Four pillars × 25 points:
      1. Profitability — GP/A, ROIC proxy, FCF margin, FCF/OCF quality
      2. Growth quality — Revenue CAGR, gross margin trend, R&D intensity
      3. Capital allocation & cash — operating margin, FCF/OCF, payout discipline
      4. Trap avoidance — penalties for declining margins, leverage stress
    """

    # --- 1. Profitability (max 25) ---
    p = 0.0
    p += _ramp(m.get("gp_to_assets"), 0.20, 0.50) * 10
    # ROCE preferred over ROA when available — ROCE penalizes leverage
    roce = m.get("roce_ttm")
    roa = m.get("roa_ttm")
    p += _ramp(roce if roce is not None else roa, 0.05, 0.25) * 8
    p += _ramp(m.get("fcf_margin"), 0.0, 0.25) * 4
    p += _ramp(m.get("fcf_to_ocf"), 0.5, 0.85) * 3

    # --- 2. Growth quality (max 25) ---
    g = 0.0
    g += _ramp(m.get("rev_cagr_3y"), 0.0, 0.25) * 12
    g += _ramp(m.get("gm_trend_3y"), 0.0, 0.05) * 8
    g += _ramp(m.get("rd_intensity"), 0.02, 0.15) * 5

    # --- 3. Capital allocation & cash (max 25) ---
    c = 0.0
    op_margin = m.get("op_margin_ttm") or 0
    c += _ramp(op_margin, 0.05, 0.30) * 10
    # FCF conversion stability
    c += _ramp(m.get("fcf_to_ocf"), 0.5, 0.85) * 5
    # Payout ratio: prefer 0.1-0.6 (dividends without starving reinvestment)
    payout = m.get("payout_ratio")
    if payout is not None:
        if 0.1 <= payout <= 0.6:
            c += 5
        elif 0 < payout < 0.1 or 0.6 < payout <= 0.85:
            c += 3
        elif payout > 0.85 or payout < 0:
            c += 0
        else:  # 0 = pure reinvestment, fine for high-growth
            c += 4
    else:
        c += 2  # neutral
    # Net margin sanity check
    c += _ramp(m.get("net_margin_ttm"), 0.05, 0.20) * 5

    # --- 4. Trap avoidance (max 25, starts full and gets penalized) ---
    tr = 25.0
    op_trend = m.get("op_margin_trend_2y")
    if op_trend is not None:
        if op_trend < -0.03:
            tr -= 12
        elif op_trend < -0.01:
            tr -= 6
    gm_trend = m.get("gm_trend_3y")
    if gm_trend is not None:
        if gm_trend < -0.03:
            tr -= 8
        elif gm_trend < -0.01:
            tr -= 4
    # Interest coverage stress
    ic = m.get("interest_coverage")
    if ic is not None and ic < 4:
        tr -= 5
    if ic is not None and ic < 2:
        tr -= 5
    tr = max(tr, 0)

    breakdown = {
        "profitability": round(p, 1),
        "growth": round(g, 1),
        "capital_alloc": round(c, 1),
        "trap_avoidance": round(tr, 1),
    }
    total = int(round(min(max(p + g + c + tr, 0), 100)))
    return total, breakdown
