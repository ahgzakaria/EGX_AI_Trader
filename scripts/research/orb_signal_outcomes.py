r"""What actually happened after each ORB signal.

Not how many signals fired -- what the price did afterwards. The complaint this
answers is "the signals never do anything", and the way to test it is to walk
the live quote stream forward from each signal and record which level, if any,
the price reached before the session closed.

Three measurement decisions, all of which have been got wrong here before:

* **The clock starts at the signal, not at the opening range.** An earlier pass
  scored every signal from the 10:15 close of the opening range, which meant a
  signal detected at 11:36 was judged against ticks from two hours before it
  existed. Thirty-four of thirty-five "stopped out" against prices that
  predated their own signal. Every walk below starts at ``first_detected_utc``.

* **First touch wins.** A signal that reaches its target and later falls to its
  stop is a winner; the reverse is a loser. Scoring on the closing price would
  call both of them something else.

* **"Neither" is a result, not missing data.** A signal whose price never
  reaches either level is the outcome the complaint is about, and it is counted
  and reported as its own category rather than dropped.

The excursions are what diagnose it. If the best price a signal ever saw sits
far short of its target while its worst never approached its stop, then nothing
is wrong with the detection -- the levels are simply further away than the stock
moves in the time available.

    venv\Scripts\python.exe scripts\research\orb_signal_outcomes.py
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import statistics
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.orb_daily_assistant import discover_sessions, load_daily_report

RUBIX_DATABASE = PROJECT_ROOT / "data" / "rubix_live_market.db"

#: Continuous trading ends 14:15 Cairo, which is 11:15 UTC. Nothing after that
#: is a fill you could have taken on the terms the signal proposed.
SESSION_END_UTC_HOUR = 11
SESSION_END_UTC_MINUTE = 15


def open_quotes(path: Path) -> sqlite3.Connection:
    """Read-only, and enforced. This database belongs to the collector."""
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60)
    connection.execute("PRAGMA query_only=ON")
    return connection


def walk(connection, ticker: str, start_utc: datetime, session_date: str,
         trigger: float, stop: float, target_1: float, target_2: float) -> dict:
    """Follow one signal forward through the tape.

    Returns the first level touched and how far price travelled either way.
    ``last_price`` is the traded price, so touching a level means the market
    actually printed there -- not that a quote was merely showing it.
    """
    end = f"{session_date}T{SESSION_END_UTC_HOUR:02d}:{SESSION_END_UTC_MINUTE:02d}:00"
    rows = connection.execute(
        "SELECT market_timestamp, last_price FROM quotes "
        "WHERE ticker = ? AND market_timestamp >= ? AND market_timestamp < ? "
        "ORDER BY market_timestamp, id",
        (ticker, start_utc.isoformat(), end),
    ).fetchall()

    result = {
        "ticker": ticker, "ticks": len(rows), "outcome": "NO_TICKS",
        "best": None, "worst": None, "minutes_to_outcome": None,
    }
    if not rows:
        return result

    prices = [row[1] for row in rows if row[1] is not None]
    if not prices:
        return result
    result["best"] = max(prices)
    result["worst"] = min(prices)
    result["outcome"] = "NEITHER"

    for stamp, price in rows:
        if price is None:
            continue
        touched = None
        # Stop first in the comparison order, because a bar that spans both is
        # ambiguous and calling it a win would flatter the result.
        if price <= stop:
            touched = "STOPPED"
        elif price >= target_2:
            touched = "TARGET_2"
        elif price >= target_1:
            touched = "TARGET_1"
        if touched:
            result["outcome"] = touched
            moment = datetime.fromisoformat(stamp)
            result["minutes_to_outcome"] = (moment - start_utc).total_seconds() / 60
            break
    return result


def percent(value: float, base: float) -> float:
    return (value - base) / base * 100.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default=str(RUBIX_DATABASE))
    args = parser.parse_args()

    _, sessions = discover_sessions()
    connection = open_quotes(Path(args.database))

    every = []
    try:
        for session_date, path in sorted(sessions):
            try:
                report = load_daily_report(path)
            except Exception:
                continue                      # schema older than the levels
            if report.levels_available_count == 0:
                continue

            print(f"\n{session_date}  ({report.signal_count} signals)")
            print(f"  {'ticker':<8} {'at':<6} {'entry':>8} {'stop%':>7} "
                  f"{'T1%':>6} {'outcome':<9} {'best%':>7} {'worst%':>7} {'min':>5}")
            for signal in report.signals:
                if signal.trigger_price is None or signal.first_detected_utc is None:
                    continue
                walked = walk(
                    connection, signal.canonical_ticker, signal.first_detected_utc,
                    str(session_date), signal.trigger_price, signal.proposed_stop,
                    signal.target_1, signal.target_2,
                )
                entry = signal.trigger_price
                row = {
                    "session": str(session_date),
                    "ticker": signal.canonical_ticker,
                    "outcome": walked["outcome"],
                    "stop_percent": percent(signal.proposed_stop, entry),
                    "target_percent": percent(signal.target_1, entry),
                    "best_percent": percent(walked["best"], entry) if walked["best"] else None,
                    "worst_percent": percent(walked["worst"], entry) if walked["worst"] else None,
                    "minutes": walked["minutes_to_outcome"],
                    "ticks": walked["ticks"],
                    "cost_percent": signal.total_cost_percent,
                }
                every.append(row)
                print(f"  {row['ticker']:<8} "
                      f"{signal.detection_time_label:<6} {entry:>8.3f} "
                      f"{row['stop_percent']:>7.2f} {row['target_percent']:>6.2f} "
                      f"{row['outcome']:<9} "
                      f"{(f'{row['best_percent']:.2f}' if row['best_percent'] is not None else '-'):>7} "
                      f"{(f'{row['worst_percent']:.2f}' if row['worst_percent'] is not None else '-'):>7} "
                      f"{(f'{row['minutes']:.0f}' if row['minutes'] is not None else '-'):>5}")
    finally:
        connection.close()

    if not every:
        print("\nNo signal carried both a level and a detection time.")
        return 1

    print(f"\n{'=' * 72}\n{len(every)} signals across "
          f"{len(set(r['session'] for r in every))} sessions\n")

    counts = Counter(row["outcome"] for row in every)
    for outcome in ("TARGET_2", "TARGET_1", "STOPPED", "NEITHER", "NO_TICKS"):
        if counts[outcome]:
            print(f"  {outcome:<10} {counts[outcome]:>3}  "
                  f"({counts[outcome] / len(every) * 100:4.0f}%)")

    priced = [r for r in every if r["best_percent"] is not None]
    if priced:
        print(f"\n  median stop distance      {statistics.median(r['stop_percent'] for r in priced):+6.2f}%")
        print(f"  median target distance    {statistics.median(r['target_percent'] for r in priced):+6.2f}%")
        print(f"  median best price reached {statistics.median(r['best_percent'] for r in priced):+6.2f}%")
        print(f"  median worst price reached{statistics.median(r['worst_percent'] for r in priced):+6.2f}%")
        costs = [r["cost_percent"] for r in priced if r["cost_percent"] is not None]
        if costs:
            print(f"  median round-trip cost    {statistics.median(costs):+6.2f}%")

        # The diagnosis, if there is one: a target the stock never approaches
        # is a target, not a detection problem.
        reached = sum(1 for r in priced if r["best_percent"] >= r["target_percent"])
        touched_stop = sum(1 for r in priced if r["worst_percent"] <= r["stop_percent"])
        print(f"\n  ever reached the target   {reached:>3}/{len(priced)}")
        print(f"  ever reached the stop     {touched_stop:>3}/{len(priced)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
