"""Resolve EODHD split events against the measured Mubasher record.

    python -m scripts.audit_corporate_actions_against_mubasher            # show
    python -m scripts.audit_corporate_actions_against_mubasher --write    # record

An EODHD "split" on EGX is a mix of true share-count events, bonus and
capital-increase events, and provider disagreements, and the volume convention
is not the same for all of them. ``providers/eodhd_volume_adjustment`` therefore
refuses to guess: an event with no validated entry in
``reports/eodhd/corporate_action_reconciliation.csv`` is UNRESOLVED, and a
symbol carrying one inside the volume lookback has its volume withheld.

The 2026-07-23 reconciliation judged nine symbols against **Yahoo**. Yahoo is no
longer a price authority in this project -- the manual queue found it stale or
wrong on ten of the symbols it was used to judge -- and it never covered the
events that are blocking symbols now. This audit uses the measured Mubasher
store instead, and uses it for the question it can actually answer.

**The measurement.** Two independent legs, and an event is resolved only when
both agree:

1. *Price.* EODHD's RAW close across the ex-date. A true share-count event
   divides the raw price by the declared ratio; a bonus or a dividend does not.
   (The router's own EODHD frame is already split-adjusted, so measuring the
   drop on it shows nothing -- it has to be the raw series.)
2. *Volume.* Mubasher's volume divided by EODHD's raw volume, session by
   session on both sides of the ex-date. Mubasher's provenance states its
   prices are split-adjusted, and where it has applied a share-count adjustment
   this ratio is the declared ratio before the event and 1.0 after it. That is
   an independent record saying the share count changed by exactly that
   factor -- which is the fact the volume rule needs.

An event whose two legs disagree, or that Mubasher does not cover, stays
UNRESOLVED. Resolving means having evidence, not having an opinion.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_adjustment import parse_split_ratio    # noqa: E402

RECON = PROJECT_ROOT / "reports" / "eodhd" / "corporate_action_reconciliation.csv"
ACTIONS = PROJECT_ROOT / "data" / "eodhd" / "corporate_actions"
FIELDS = ["symbol", "effective_date", "eodhd_action_type", "eodhd_ratio",
          "raw_split_string", "eodhd_price_drop_factor", "yahoo_price_drop_factor",
          "classified_action", "volume_rule", "evidence"]

#: A raw price that divides by the declared ratio, within this much.
PRICE_TOLERANCE = 0.15
#: The volume ratio is an exact arithmetic adjustment, not a measurement of the
#: market, so it is held to a far tighter bound than the price.
VOLUME_TOLERANCE = 0.01
#: Sessions compared either side of the ex-date.
WINDOW = 5


def _raw_eodhd(base, client):
    """EODHD's RAW daily series. Not the router's frame, which is adjusted."""
    rows = client.eod(f"{base}.EGX", order="a")
    frame = pd.DataFrame([
        {"Date": pd.to_datetime(r["date"]), "close": float(r["close"]),
         "volume": float(r.get("volume") or 0)}
        for r in rows if r.get("close")])
    return frame.set_index("Date").sort_index() if not frame.empty else None


def _mubasher(base, expected):
    from core.mubasher_live_history import READY, mubasher_live_history

    frame, status, _ = mubasher_live_history(base, min_bars=40, not_after=expected)
    return frame if status == READY else None


def _volume_ratios(mubasher, raw, ex_date, window=WINDOW):
    """(before, after) lists of mubasher_volume / eodhd_raw_volume, by session."""
    column = next((c for c in mubasher.columns if c.lower() == "volume"), None)
    if column is None:
        return [], []

    def ratios(index):
        out = []
        for day in index:
            if day not in raw.index:
                continue
            denominator = float(raw.loc[day, "volume"])
            if denominator > 0:
                out.append(float(mubasher.loc[day, column]) / denominator)
        return out

    return (ratios(mubasher.index[mubasher.index < ex_date][-window:]),
            ratios(mubasher.index[mubasher.index >= ex_date][:window]))


def _price_drop(raw, ex_date):
    before = raw[raw.index < ex_date]
    after = raw[raw.index >= ex_date]
    if before.empty or after.empty:
        return None
    b, a = float(before["close"].iloc[-1]), float(after["close"].iloc[0])
    return (b / a) if a > 0 else None


def classify(ratio, price_drop, before, after):
    """``(classified_action, volume_rule, evidence)`` for one event.

    Every branch that does not resolve says which leg was missing, because an
    UNRESOLVED with no stated reason is what the 2026-07-23 file left behind.
    """
    if not before or not after:
        return ("PROVIDER_CLASSIFICATION_DIFFERENCE", "UNRESOLVED",
                "no measured Mubasher volume either side of the ex-date")

    pre, post = statistics.median(before), statistics.median(after)
    # The STEP across the ex-date, not the level after it. The ratio is
    # cumulative -- it carries every later adjustment too -- so an event with a
    # more recent one behind it lands at 1.5625x before and 1.2500x after
    # rather than at 1.25 and 1.0. Testing the level called three genuine
    # events EVENT_SPECIFIC on the first run of this audit.
    step = (pre / post) if post else None
    steps_by = step is not None and abs(step - ratio) / ratio <= VOLUME_TOLERANCE
    price_matches = (price_drop is not None
                     and abs(price_drop - ratio) / ratio <= PRICE_TOLERANCE)
    spread = f"pre x{min(before):.4f}-{max(before):.4f}, post x{min(after):.4f}-{max(after):.4f}"

    if steps_by and price_matches:
        return ("SPLIT", "MULTIPLY_BY_FACTOR",
                f"raw price /{price_drop:.4f} against declared {ratio:.4f}; Mubasher "
                f"volume steps by x{step:.4f} across the ex-date "
                f"({pre:.4f}x EODHD raw before, {post:.4f}x after; {spread})")
    if steps_by:
        drop = "no price evidence" if price_drop is None else f"x{price_drop:.4f}"
        return ("SHARE_COUNT_CHANGE_WITHOUT_PRICE_DROP", "EVENT_SPECIFIC",
                f"Mubasher volume steps by x{step:.4f} across the ex-date but the raw "
                f"price moved {drop} against a declared {ratio:.4f} — the two legs "
                f"disagree, so the convention is not assumed")
    if price_matches:
        return ("SPLIT_PROVIDER_DIFF", "EVENT_SPECIFIC",
                f"raw price /{price_drop:.4f} matches the declared {ratio:.4f}, but "
                f"Mubasher volume shows no share-count adjustment ({spread})")
    drop = "no price evidence" if price_drop is None else f"x{price_drop:.4f}"
    return ("STOCK_DIVIDEND_BONUS", "KEEP_RAW",
            f"neither leg shows a share-count change: raw price {drop} and Mubasher "
            f"volume steps by x{step:.4f} ({spread}), against a declared {ratio:.4f}")


def events(base, since, client):
    """``(ex_date, ratio, raw split string)`` for one symbol, oldest first.

    From the same endpoint ``research_router`` uses, not from the snapshot under
    data/eodhd/corporate_actions: EEII's on-disk file stops in 2023 while the
    policy was blocking it on an event dated 2026-09-03, so an audit reading the
    snapshot could not see the event it exists to resolve.
    """
    rows = client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400) or []
    out = []
    for row in rows or []:
        day = str(row.get("date", ""))[:10]
        if not day or day < since:
            continue
        try:
            out.append((pd.to_datetime(day), parse_split_ratio(row.get("split")),
                        row.get("split")))
        except Exception:                                   # noqa: BLE001
            continue
    return sorted(out)


def existing():
    if not RECON.exists():
        return {}
    frame = pd.read_csv(RECON)
    return {(str(r["symbol"]).upper(), str(r["effective_date"])[:10]): dict(r)
            for _, r in frame.iterrows()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve split events against Mubasher.")
    parser.add_argument("--since", default="2025-01-01",
                        help="only events on or after this date (default 2025-01-01)")
    parser.add_argument("--symbols", nargs="*",
                        help="default: every symbol in the routing tiers")
    parser.add_argument("--write", action="store_true",
                        help="merge the resolved rows into the reconciliation CSV")
    args = parser.parse_args(argv)

    load_project_environment()
    from core.research_router import _expected_completed_session, tier_map
    from providers.eodhd_client import EODHDClient

    symbols = [s.upper() for s in (args.symbols or sorted(tier_map()))]
    expected = _expected_completed_session()
    client = EODHDClient(max_live_calls=120)
    known = existing()
    resolved, unresolved, skipped = [], [], 0

    for base in symbols:
        pending = [e for e in events(base, args.since, client)
                   if (base, e[0].date().isoformat()) not in known]
        if not pending:
            continue
        raw = _raw_eodhd(base, client)
        mubasher = _mubasher(base, expected)
        if raw is None:
            skipped += len(pending)
            continue
        for ex_date, ratio, raw_string in pending:
            drop = _price_drop(raw, ex_date)
            before, after = ([], []) if mubasher is None else _volume_ratios(
                mubasher, raw, ex_date)
            action, rule, evidence = classify(ratio, drop, before, after)
            row = {
                "symbol": base, "effective_date": ex_date.date().isoformat(),
                "eodhd_action_type": "SPLIT", "eodhd_ratio": round(ratio, 6),
                "raw_split_string": raw_string,
                "eodhd_price_drop_factor": None if drop is None else round(drop, 4),
                # The column stays for the file's shape; this audit does not
                # consult Yahoo, and an empty cell says so rather than implying
                # a comparison that was never made.
                "yahoo_price_drop_factor": "",
                "classified_action": action, "volume_rule": rule,
                "evidence": evidence,
            }
            (resolved if rule != "UNRESOLVED" else unresolved).append(row)
            print(f"{base:6} {row['effective_date']}  x{ratio:<9.4f} {rule:<19} {evidence}")

    print(f"\n{len(resolved)} resolved, {len(unresolved)} left unresolved, "
          f"{skipped} skipped (no EODHD series), {client.stats.live_calls} live calls")
    if not args.write:
        print("nothing written — pass --write to record these")
        return 0

    rows = list(known.values()) + resolved + unresolved
    with RECON.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {RECON.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
