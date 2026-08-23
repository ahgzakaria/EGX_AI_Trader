"""Does the ORB entry beat entering something else at the same moment?

Every construction tested loses, which points at the entry rather than the
exit. This asks the entry the only question that matters on its own terms: at
the instant a signal fired, did the symbol it named go on to do better than
the rest of the market did from that same instant?

That comparison removes everything the entry cannot claim credit for. Market
direction, time of day, and the session's overall strength apply equally to
the signalled symbol and to its peers, so whatever is left is the entry.

Three measures, from the signal's timestamp to the session close:

* **drift** -- where the price actually ended up
* **best** -- the largest favourable excursion, what a perfect exit would get
* **worst** -- the largest adverse excursion, what the stop has to survive

An entry with edge beats its peers on drift, or gets a better best-to-worst
spread. One that matches them on all three is picking names at random with
extra steps.
"""
from __future__ import annotations

import glob
import os
import sqlite3
import statistics
import sys

sys.path.insert(0, ".")

rubix = sqlite3.connect("file:data/rubix_live_market.db?mode=ro", uri=True)
rubix.execute("PRAGMA query_only=ON")


def excursions(ticker: str, since: str):
    """(drift, best, worst) in percent from the first quote at or after `since`."""

    rows = rubix.execute(
        "SELECT last_price FROM quotes WHERE ticker = ? AND market_timestamp >= ? "
        "AND last_price > 0 ORDER BY market_timestamp",
        (ticker, since),
    ).fetchall()
    if len(rows) < 10:
        return None
    prices = [float(r[0]) for r in rows]
    base = prices[0]
    if base <= 0:
        return None
    return (
        (prices[-1] - base) / base * 100.0,
        (max(prices) - base) / base * 100.0,
        (min(prices) - base) / base * 100.0,
    )


def universe_for(day: str):
    """Symbols the engine was watching that session."""

    path = f"data/research/orb_full_shadow/orb_full_shadow_{day}.db"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    try:
        return [r[0] for r in connection.execute(
            "SELECT DISTINCT canonical_ticker FROM orb_shadow_live_states")]
    finally:
        connection.close()


def signals():
    out = []
    for path in sorted(glob.glob("data/research/orb_full_shadow/*.db")):
        day = os.path.basename(path)[-13:-3]
        if day < "2026-07-15":
            continue
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        try:
            if "orb_signal_qualification" not in {r[0] for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                continue
            for r in connection.execute("""
                SELECT q.canonical_ticker, q.detection_at_utc
                FROM orb_signal_qualification q
                JOIN (SELECT canonical_ticker, MIN(detection_at_utc) f
                      FROM orb_signal_qualification WHERE trigger_price IS NOT NULL
                      GROUP BY canonical_ticker) m
                  ON m.canonical_ticker = q.canonical_ticker
                 AND m.f = q.detection_at_utc
                WHERE q.trigger_price IS NOT NULL
                GROUP BY q.canonical_ticker"""):
                out.append((day, r["canonical_ticker"], str(r["detection_at_utc"])))
        finally:
            connection.close()
    return out


def main() -> int:
    rows = signals()
    print(f"{len(rows)} signals across "
          f"{len({d for d, _, _ in rows})} sessions\n")

    universes = {}
    signalled, peers = [], []
    matched = 0

    for day, ticker, detected in rows:
        own = excursions(ticker, detected)
        if own is None:
            continue
        universe = universes.setdefault(day, universe_for(day))
        peer_rows = []
        for other in universe:
            if other == ticker:
                continue
            value = excursions(other, detected)
            if value is not None:
                peer_rows.append(value)
        if len(peer_rows) < 20:
            continue
        matched += 1
        signalled.append(own)
        peers.append((
            statistics.fmean([p[0] for p in peer_rows]),
            statistics.fmean([p[1] for p in peer_rows]),
            statistics.fmean([p[2] for p in peer_rows]),
        ))

    if not matched:
        print("no comparable signals")
        return 1

    print(f"{matched} signals compared against every other symbol the engine")
    print("was watching, measured from the same instant to the session close.\n")
    print(f"{'':<16}{'signalled':>12}{'the rest':>12}{'edge':>10}")
    print("-" * 50)
    for index, name in enumerate(("drift %", "best %", "worst %")):
        mine = statistics.fmean([s[index] for s in signalled])
        theirs = statistics.fmean([p[index] for p in peers])
        print(f"{name:<16}{mine:>12.2f}{theirs:>12.2f}{mine - theirs:>+10.2f}")

    beat = sum(1 for s, p in zip(signalled, peers) if s[0] > p[0])
    print()
    print(f"signals that drifted better than their peers: {beat}/{matched} "
          f"({beat / matched * 100:.0f}%)")
    print("A coin lands on 50%. An entry worth having lands well above it.")

    spread_mine = statistics.fmean([s[1] - s[2] for s in signalled])
    spread_theirs = statistics.fmean([p[1] - p[2] for p in peers])
    print()
    print(f"best-to-worst range: signalled {spread_mine:.2f}%, "
          f"peers {spread_theirs:.2f}%")
    print("A wider range on the same drift means the entry finds movement but")
    print("not direction -- which a stop converts into losses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
