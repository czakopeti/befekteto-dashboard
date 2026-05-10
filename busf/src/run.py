"""BUSF weekly screen — main entry point.

Workflow:
1. Load S&P 500 universe.
2. Refresh stale FMP fundamentals + all quotes (rate-limit aware).
3. Apply quality gate.
4. Score survivors with BCS composite (0-100).
5. Diff against last week's list.
6. Save current_list.json + dated archive.
7. Push notification via ntfy.sh.

Run from repo root: python src/run.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make src/ importable when run from repo root
sys.path.insert(0, str(Path(__file__).parent))

from data import get_universe, refresh_data
from scoring import passes_quality_gate, score, lifecycle_of
from notify import (
    load_previous, save_current, diff_lists,
    format_message, send_ntfy,
)


TOP_N = 30


def fmt_pct(x):
    if x is None:
        return "—"
    return f"{x*100:.0f}%"


def fmt_num(x, digits=2):
    if x is None:
        return "—"
    return f"{x:.{digits}f}"


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== BUSF Screen {today} ===")

    # 1. Universe
    universe = get_universe()
    if not universe:
        print("[run] Universe empty, aborting")
        return 1
    print(f"[run] Universe size: {len(universe)}")

    # 2. Refresh data (rate-limit aware)
    fundamentals, profiles, quotes = refresh_data(universe)

    # 3+4. Gate + score
    scored = []
    rejected_reasons: dict[str, int] = {}
    for sym in universe:
        m = fundamentals.get(sym)
        if not m or m.get("broken"):
            rejected_reasons["no_data"] = rejected_reasons.get("no_data", 0) + 1
            continue

        market_cap = (quotes.get(sym, {}).get("market_cap"))
        passes, reason = passes_quality_gate(m, market_cap)
        if not passes:
            key = reason.split()[0]
            rejected_reasons[key] = rejected_reasons.get(key, 0) + 1
            continue

        total, breakdown = score(m)
        lc = lifecycle_of(m)
        prof = profiles.get(sym, {})

        scored.append({
            "ticker": sym,
            "name": (prof.get("name") or sym)[:40],
            "sector": prof.get("sector", "Unknown"),
            "score": total,
            "lifecycle": lc,
            "price": quotes.get(sym, {}).get("price"),
            "market_cap_b": round(market_cap / 1e9, 1) if market_cap else None,
            # Display-friendly metrics
            "gp_to_assets": fmt_num(m.get("gp_to_assets")),
            "rev_cagr_3y": fmt_pct(m.get("rev_cagr_3y")),
            "fcf_margin": fmt_pct(m.get("fcf_margin")),
            "op_margin": fmt_pct(m.get("op_margin_ttm")),
            "roce": fmt_pct(m.get("roce_ttm")),
            "rd_intensity": fmt_pct(m.get("rd_intensity")),
            "breakdown": breakdown,
        })

    print(f"[run] Passed gate: {len(scored)}")
    print(f"[run] Rejection summary: {rejected_reasons}")

    # Sort and trim
    scored.sort(key=lambda x: -x["score"])
    top = scored[:TOP_N]

    curr = {
        "date": today,
        "universe_size": len(universe),
        "passed_gate": len(scored),
        "top": top,
    }

    # 5. Diff
    prev = load_previous()
    new_entries, dropped, changed = diff_lists(prev, curr)
    print(f"[run] New: {len(new_entries)}, Dropped: {len(dropped)}, Big moves: {len(changed)}")

    # 6. Save
    save_current(curr)

    # 7. Notify
    msg = format_message(curr, new_entries, dropped, changed)
    print("\n" + msg)

    has_changes = bool(new_entries or dropped or changed)
    title = f"BUSF {today}"
    if new_entries:
        title += f" · +{len(new_entries)}"
    if dropped:
        title += f" · -{len(dropped)}"

    topic = os.environ.get("NTFY_TOPIC", "")
    send_ntfy(topic, title, msg, has_changes=has_changes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
