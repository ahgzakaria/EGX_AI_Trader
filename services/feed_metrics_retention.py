r"""Keep the collector's telemetry from outgrowing the market data.

Measured on 2026-08-25: ``feed_metrics`` held 44,246,339 rows against
18,249,746 in ``quotes``. The telemetry was two and a half times the data it
describes, carried no index, and had accumulated for forty-two days. Counting
its rows took ninety seconds, and the health snapshot does exactly that on
every read.

Two decisions shape what this deletes.

**Only the per-tick events age out.** Three of them account for 43.8 million of
the 44.2 million rows -- ``latency_ms`` and ``update_interval_ms``, written once
per quote each, and ``duplicate_message``. Everything else together is 439,000
rows: stalls, heartbeats, reconnects, subscription batches. Those are the record
of how the feed behaved on the day something went wrong, they cost almost
nothing to keep, and they are never deleted here at any age.

**The work is bounded, not complete.** Deleting forty million rows in one
statement would hold a write lock for minutes and inflate the very journal this
is meant to relieve. Each call deletes in batches until its time budget runs
out and reports whether it finished; the next call resumes. A first pass will
not catch up, and does not need to.

Nothing here touches ``quotes`` or ``candles_1m``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time

#: Events written once per quote. These are the volume, and the only rows that
#: age out. Anything not named here is kept regardless of age.
HIGH_VOLUME_EVENTS = ("latency_ms", "update_interval_ms", "duplicate_message")

#: How much history to keep for those events.
DEFAULT_RETENTION_DAYS = 7

#: Rows per transaction. Small enough that the write lock is never held long
#: beside a live collector, large enough that the per-statement overhead does
#: not dominate.
DEFAULT_BATCH = 20_000

#: Default ceiling on one call. Shutdown must stay quick even on the first run.
DEFAULT_BUDGET_SECONDS = 60.0


@dataclass
class PruneResult:
    """What one call actually did. Every field is observed, never assumed."""

    deleted: int = 0
    batches: int = 0
    finished: bool = False
    seconds: float = 0.0
    cutoff: str = ""
    error: str = ""

    def as_log_fields(self) -> dict:
        return {
            "deleted": self.deleted,
            "batches": self.batches,
            "finished": self.finished,
            "seconds": round(self.seconds, 1),
            "cutoff": self.cutoff,
            **({"error": self.error} if self.error else {}),
        }


def cutoff_timestamp(days: int, *, now: datetime | None = None) -> str:
    """The ISO instant before which per-tick telemetry is dropped.

    Compared as text against ``observed_at``, which the adapter writes as an
    ISO-8601 UTC string. Both sides are UTC and both are zero-padded, so a
    lexicographic comparison and a chronological one agree.
    """
    moment = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    return moment.astimezone(timezone.utc).isoformat()


def prune(
    database: Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    batch: int = DEFAULT_BATCH,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    now: datetime | None = None,
) -> PruneResult:
    """Delete aged per-tick telemetry, in batches, within a time budget.

    Returns rather than raises. This runs on the supervisor's shutdown path,
    where an exception would cost the checkpoint that the shutdown exists to
    perform.
    """
    started = time.monotonic()
    result = PruneResult(cutoff=cutoff_timestamp(retention_days, now=now))

    if not Path(database).is_file():
        result.error = "database is missing"
        result.seconds = time.monotonic() - started
        return result

    placeholders = ",".join("?" for _ in HIGH_VOLUME_EVENTS)
    # rowid is the primary key here, so selecting ids first and deleting by
    # them keeps each statement's work proportional to the batch rather than
    # to the 44 million rows behind it.
    select = (
        f"SELECT id FROM feed_metrics "
        f"WHERE event IN ({placeholders}) AND observed_at < ? "
        f"LIMIT ?"
    )

    try:
        connection = sqlite3.connect(database, timeout=30)
    except sqlite3.Error as error:
        result.error = str(error)
        result.seconds = time.monotonic() - started
        return result

    try:
        while time.monotonic() - started < budget_seconds:
            rows = connection.execute(
                select, (*HIGH_VOLUME_EVENTS, result.cutoff, batch)
            ).fetchall()
            if not rows:
                result.finished = True
                break
            ids = [row[0] for row in rows]
            connection.execute(
                f"DELETE FROM feed_metrics WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            connection.commit()
            result.deleted += len(ids)
            result.batches += 1
    except sqlite3.Error as error:
        # A partial prune is a fine outcome; the next call continues. What must
        # not happen is the caller losing its own work over this.
        result.error = str(error)
    finally:
        connection.close()
        result.seconds = time.monotonic() - started

    return result


def estimate_pending(database: Path, *, retention_days: int = DEFAULT_RETENTION_DAYS,
                     now: datetime | None = None) -> int:
    """How many rows are still eligible. Read-only; for reporting only.

    On an unpruned table this counts through tens of millions of rows without
    an index and is slow -- which is itself the reason the pruning exists.
    """
    if not Path(database).is_file():
        return 0
    placeholders = ",".join("?" for _ in HIGH_VOLUME_EVENTS)
    uri = f"file:{Path(database).as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=60)
    try:
        connection.execute("PRAGMA query_only=ON")
        return connection.execute(
            f"SELECT COUNT(*) FROM feed_metrics "
            f"WHERE event IN ({placeholders}) AND observed_at < ?",
            (*HIGH_VOLUME_EVENTS, cutoff_timestamp(retention_days, now=now)),
        ).fetchone()[0]
    finally:
        connection.close()
