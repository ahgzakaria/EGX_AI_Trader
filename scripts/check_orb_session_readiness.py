"""Pre-flight gate for an ORB Lane A session. Read-only; refuses, never fixes.

Run this BEFORE starting a live session. It answers one question — is it worth
starting at all — and exits non-zero when the answer is no, so a launcher can
stop rather than spend a trading day producing `DATA_UNAVAILABLE`.

Why it exists: on 2026-08-13 the machine clock was ~2s behind the exchange
feed at the open. Every quote arrived with `received_at` EARLIER than
`market_timestamp`, which the pipeline correctly treats as clock skew rather
than freshness (`WatermarkView.live_evidence_fresh`). No bar in the opening
range window ever became operationally final, the range never reached its
required 100% coverage, and all 224 symbols evaluated to
`OPENING_RANGE_NOT_READY` for the whole session. The raw data was perfect —
15/15 minutes, hundreds of priced quotes per minute. Only the local clock was
wrong, and by the time that was visible the opening range had passed and was
unrecoverable: `received_at` is written into the collector's rows and correcting
it afterwards would be falsifying evidence.

The check is deliberately dumb and fast. It reads recent quotes, measures
`received_at - market_timestamp`, and refuses on a negative median. It fixes
nothing: the clock is a system setting and belongs to the operator.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import statistics
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_RUBIX_DB = "data/rubix_live_market.db"

#: A negative median lag means the feed's clock is ahead of ours. The pipeline
#: rejects those events, so starting a session in this state wastes the day.
MINIMUM_MEDIAN_LAG_SECONDS = 0.0

#: Any negative sample is worth reporting even when the median is positive: it
#: means the two clocks are close enough to cross under jitter.
WARN_NEGATIVE_FRACTION = 0.05

#: A quote older than this suggests the collector is not actually running.
MAXIMUM_FEED_AGE_SECONDS = 120.0


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def measure(path: Path, sample: int):
    """Return the readiness facts, or raise for an unusable source."""

    connection = _connect(path)
    try:
        row = connection.execute("SELECT max(id) FROM quotes").fetchone()
        if not row or row[0] is None:
            raise RuntimeError("no quotes in the Rubix database")
        highest = int(row[0])
        rows = connection.execute(
            "SELECT market_timestamp, received_at FROM quotes "
            "WHERE id BETWEEN ? AND ?",
            (highest - sample, highest),
        ).fetchall()
    finally:
        connection.close()

    lags = []
    latest_market = None
    for market_text, received_text in rows:
        market, received = _parse(market_text), _parse(received_text)
        if market is None or received is None:
            continue
        lags.append((received - market).total_seconds())
        if latest_market is None or market > latest_market:
            latest_market = market
    if not lags:
        raise RuntimeError("no timestamped quotes to measure")

    negative = sum(1 for lag in lags if lag < 0)
    now = datetime.now(timezone.utc)
    return {
        "samples": len(lags),
        "median_lag": statistics.median(lags),
        "minimum_lag": min(lags),
        "maximum_lag": max(lags),
        "negative_count": negative,
        "negative_fraction": negative / len(lags),
        "latest_market_timestamp": latest_market,
        "feed_age_seconds": (
            (now - latest_market).total_seconds() if latest_market else None
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rubix-db-path", default=DEFAULT_RUBIX_DB)
    parser.add_argument("--sample", type=int, default=800)
    parser.add_argument(
        "--require-fresh-feed",
        action="store_true",
        help=(
            "also fail when the newest quote is older than "
            f"{MAXIMUM_FEED_AGE_SECONDS:.0f}s. Use during market hours only; "
            "before the open the feed is legitimately quiet."
        ),
    )
    args = parser.parse_args(argv)

    path = Path(args.rubix_db_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        print(f"FAIL  Rubix database not found: {path}")
        return 2

    try:
        facts = measure(path, args.sample)
    except (RuntimeError, sqlite3.Error) as error:
        print(f"FAIL  {error}")
        return 2

    age = facts["feed_age_seconds"]
    print(f"samples              {facts['samples']}")
    print(f"median lag           {facts['median_lag']:+.2f}s")
    print(f"lag range            {facts['minimum_lag']:+.2f}s .. "
          f"{facts['maximum_lag']:+.2f}s")
    print(f"negative samples     {facts['negative_count']} "
          f"({facts['negative_fraction'] * 100:.0f}%)")
    print(f"newest quote         {facts['latest_market_timestamp']}")
    print(f"feed age             {age:.0f}s" if age is not None else "feed age  n/a")
    print()

    failures = []
    if facts["median_lag"] < MINIMUM_MEDIAN_LAG_SECONDS:
        failures.append(
            f"median lag {facts['median_lag']:+.2f}s is negative — the machine "
            "clock is behind the exchange feed. Every quote will be treated as "
            "clock skew, no opening-range bar will become final, and the whole "
            "session will report OPENING_RANGE_NOT_READY.\n"
            "        Fix (elevated): w32tm /resync /force"
        )
    if args.require_fresh_feed and age is not None and age > MAXIMUM_FEED_AGE_SECONDS:
        failures.append(
            f"newest quote is {age:.0f}s old — the collector does not look like "
            "it is running."
        )

    if failures:
        for failure in failures:
            print(f"FAIL  {failure}")
        return 1

    if facts["negative_fraction"] > WARN_NEGATIVE_FRACTION:
        print(f"WARN  {facts['negative_fraction'] * 100:.0f}% of samples are "
              "negative. The clocks are close enough to cross; resync before "
              "the open.")
    print("PASS  clock and feed are fit to start a session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
