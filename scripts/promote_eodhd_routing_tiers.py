"""Promote the proposed routing tiers to the operator-approved ones.

    python -m scripts.promote_eodhd_routing_tiers            # show the diff
    python -m scripts.promote_eodhd_routing_tiers --confirm  # promote it

The review file describes itself as PROPOSED and INACTIVE with every entry
``approved: false``. It was nevertheless the only file the research router
consulted, so regenerating it changed which provider serves which symbol
immediately, with nothing in between. Three regenerations in one session each
went straight to production.

This is the gate that was always described and never built. Once something is
promoted, ``core.research_router`` reads the promoted file and a rebuilt review
is a proposal again. Until then the router falls back to the review file, so a
project that has never promoted behaves exactly as it did before.

Promoting records what was approved and when. It does not evaluate anything --
the evidence audits do that, and this step only says a human looked.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.research_router import ACTIVE_PATH, REVIEW_PATH


def _entries(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    return {str(e["symbol"]).upper(): e for e in data.get("symbols", [])}, data


def diff(review, active):
    """Return (added, removed, changed) between the proposed and approved tiers."""

    added = sorted(set(review) - set(active))
    removed = sorted(set(active) - set(review))
    changed = sorted(
        symbol for symbol in set(review) & set(active)
        if review[symbol].get("tier") != active[symbol].get("tier")
    )
    return added, removed, changed


def promote(review_entries, review_data, approved_by):
    """Return the document to write as the approved tiers."""

    promoted = []
    for entry in review_data.get("symbols", []):
        row = dict(entry)
        row["approved"] = True
        row["approved_on"] = date.today().isoformat()
        row["approved_by"] = approved_by
        promoted.append(row)
    return {
        "schema_version": review_data.get("schema_version", 1),
        "active": True,
        "description": (
            "APPROVED four-tier EODHD routing. This file is the routing "
            "authority read by core.research_router. Regenerating the review "
            "does not change it; promote again to adopt a new proposal."
        ),
        "promoted_at": date.today().isoformat(),
        "promoted_from": REVIEW_PATH.name,
        "review_generated_at": review_data.get("generated_at"),
        "approved_by": approved_by,
        "symbols": promoted,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Promote proposed routing tiers to approved.")
    parser.add_argument("--confirm", action="store_true",
                        help="Write the approved file. Without it, only the diff is shown.")
    parser.add_argument("--approved-by", default="operator")
    args = parser.parse_args(argv)

    review_entries, review_data = _entries(REVIEW_PATH)
    if not review_entries:
        print(f"No proposed tiers at {REVIEW_PATH}. Run scripts/build_eodhd_routing_tiers.py.")
        return 1
    active_entries, _ = _entries(ACTIVE_PATH)

    added, removed, changed = diff(review_entries, active_entries)
    print(f"proposed : {len(review_entries)} symbols ({REVIEW_PATH})")
    print(f"approved : {len(active_entries)} symbols "
          f"({ACTIVE_PATH if active_entries else 'nothing promoted yet'})")
    print(f"  added        : {len(added)} {', '.join(added[:12])}{' …' if len(added) > 12 else ''}")
    print(f"  removed      : {len(removed)} {', '.join(removed[:12])}{' …' if len(removed) > 12 else ''}")
    print(f"  tier changed : {len(changed)}")
    for symbol in changed[:20]:
        print(f"     {symbol:7s} {active_entries[symbol].get('tier'):34s}"
              f" -> {review_entries[symbol].get('tier')}")
    if len(changed) > 20:
        print(f"     … and {len(changed) - 20} more")

    if not args.confirm:
        print()
        print("Nothing written. Re-run with --confirm to promote.")
        return 0

    ACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PATH.write_text(
        json.dumps(promote(review_entries, review_data, args.approved_by), indent=2),
        encoding="utf-8",
    )
    print()
    print(f"promoted {len(review_entries)} symbols -> {ACTIVE_PATH}")
    print("core.research_router now reads the approved file; the review is a proposal again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
