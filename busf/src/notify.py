"""Diff this week's screen against last week's, and push via ntfy.sh.

ntfy.sh is free, no account, no API key. Pick a topic name (use something
unguessable like 'busf-screen-x9q3z'), install the ntfy app, subscribe.
Set NTFY_TOPIC as a GitHub Actions secret.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests


LIST_PATH = Path("data/current_list.json")
HISTORY_DIR = Path("data/history")


def load_previous() -> dict:
    if LIST_PATH.exists():
        try:
            return json.loads(LIST_PATH.read_text())
        except Exception:
            return {"top": []}
    return {"top": []}


def save_current(data: dict) -> None:
    LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIST_PATH.write_text(json.dumps(data, indent=2))

    # Also archive a dated copy
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    archive = HISTORY_DIR / f"{data['date']}.json"
    archive.write_text(json.dumps(data, indent=2))


def diff_lists(prev: dict, curr: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (new_entries, dropped, changed_score) lists.

    new_entries: in current but not in previous
    dropped: in previous but not in current
    changed_score: in both, but score moved by >= 5 points
    """
    prev_map = {x["ticker"]: x for x in prev.get("top", [])}
    curr_map = {x["ticker"]: x for x in curr.get("top", [])}

    new_entries = [curr_map[t] for t in curr_map if t not in prev_map]
    dropped = [prev_map[t] for t in prev_map if t not in curr_map]

    changed = []
    for t in curr_map:
        if t in prev_map:
            delta = curr_map[t]["score"] - prev_map[t]["score"]
            if abs(delta) >= 5:
                changed.append({**curr_map[t], "delta": delta})
    return new_entries, dropped, changed


def format_message(curr: dict, new_entries: list[dict],
                   dropped: list[dict], changed: list[dict]) -> str:
    lines = [
        f"BUSF Screen — {curr['date']}",
        f"Universe: {curr['universe_size']} | Passed gate: {curr['passed_gate']}",
        "",
    ]

    if new_entries:
        lines.append(f"NEW ({len(new_entries)}):")
        for e in sorted(new_entries, key=lambda x: -x["score"]):
            lines.append(f"  + {e['ticker']:<6} {e['score']:>3}  [{e['lifecycle'][:4]}] {e['name'][:24]}")
        lines.append("")

    if dropped:
        lines.append(f"DROPPED ({len(dropped)}):")
        for e in dropped:
            lines.append(f"  - {e['ticker']:<6} (was {e['score']})")
        lines.append("")

    if changed:
        lines.append(f"BIG MOVES ({len(changed)}):")
        for e in sorted(changed, key=lambda x: -abs(x["delta"])):
            sign = "+" if e["delta"] > 0 else ""
            lines.append(f"  ~ {e['ticker']:<6} {e['score']:>3} ({sign}{e['delta']})")
        lines.append("")

    lines.append("TOP 15:")
    for m in curr["top"][:15]:
        lines.append(
            f"  {m['ticker']:<6} {m['score']:>3}  [{m['lifecycle'][:4]}] "
            f"GP/A {m['gp_to_assets']:>4}  Rev3y {m['rev_cagr_3y']:>5}%  "
            f"{m['name'][:22]}"
        )

    return "\n".join(lines)


def send_ntfy(topic: str, title: str, message: str, has_changes: bool = False) -> bool:
    """Send push via ntfy.sh. Returns True on 200."""
    if not topic:
        print("[notify] NTFY_TOPIC not set, skipping push")
        return False
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high" if has_changes else "default",
                "Tags": "chart_with_upwards_trend",
            },
            timeout=10,
        )
        ok = resp.status_code == 200
        print(f"[notify] ntfy status={resp.status_code}")
        return ok
    except Exception as e:
        print(f"[notify] failed: {e}")
        return False
