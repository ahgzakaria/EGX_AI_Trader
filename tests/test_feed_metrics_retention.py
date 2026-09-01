"""Telemetry retention: what ages out, what never does, and what it costs.

Measured on 2026-08-25: ``feed_metrics`` held 44,246,339 rows against
18,249,746 in ``quotes``. Three per-tick events were 43.8 million of them;
everything else together was 439,000. The table carries no index, so counting
its rows took ninety seconds -- and the health snapshot counts them on every
read.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.feed_metrics_retention import (
    HIGH_VOLUME_EVENTS,
    cutoff_timestamp,
    estimate_pending,
    prune,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def database(tmp_path):
    """Old and new rows of every event kind the adapter writes."""
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE feed_metrics (id INTEGER PRIMARY KEY, observed_at TEXT, "
            "event TEXT, ticker TEXT, value REAL, detail TEXT)"
        )
        connection.execute("CREATE TABLE quotes (id INTEGER PRIMARY KEY, ticker TEXT)")
        connection.execute("CREATE TABLE candles_1m (minute TEXT, ticker TEXT)")

        rows = []
        for event in HIGH_VOLUME_EVENTS:
            rows += [(_stamp(30), event, "COMI", 1.0, None) for _ in range(50)]
            rows += [(_stamp(1), event, "COMI", 1.0, None) for _ in range(10)]
        for event in ("stale_start", "heartbeat_sent", "controlled_disconnect",
                      "subscription_batch_sent"):
            rows += [(_stamp(30), event, "COMI", 1.0, None) for _ in range(5)]
        connection.executemany(
            "INSERT INTO feed_metrics(observed_at,event,ticker,value,detail) "
            "VALUES (?,?,?,?,?)", rows)
        connection.executemany("INSERT INTO quotes(ticker) VALUES (?)",
                               [("COMI",) for _ in range(100)])
        connection.commit()
    return path


def _count(path, where="1=1", params=()):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            f"SELECT COUNT(*) FROM feed_metrics WHERE {where}", params).fetchone()[0]


def test_aged_per_tick_rows_are_deleted(database):
    result = prune(database, retention_days=7, now=NOW)

    assert result.finished
    assert result.deleted == 150, "three high-volume events, fifty old rows each"
    assert _count(database, "observed_at < ? AND event IN (?,?,?)",
                  (cutoff_timestamp(7, now=NOW), *HIGH_VOLUME_EVENTS)) == 0


def test_recent_per_tick_rows_survive(database):
    prune(database, retention_days=7, now=NOW)
    assert _count(database, "event = ?", ("latency_ms",)) == 10


def test_rare_operational_events_are_never_deleted_at_any_age(database):
    """They are 439,000 rows against 43.8 million, and they are the record of
    how the feed behaved on the day something went wrong."""
    prune(database, retention_days=7, now=NOW)

    for event in ("stale_start", "heartbeat_sent", "controlled_disconnect",
                  "subscription_batch_sent"):
        assert _count(database, "event = ?", (event,)) == 5, event


def test_quotes_and_candles_are_never_touched(database):
    prune(database, retention_days=7, now=NOW)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 100
        assert connection.execute("SELECT COUNT(*) FROM candles_1m").fetchone()[0] == 0


def test_the_budget_bounds_the_work_and_the_result_says_so(database):
    """A shutdown that took minutes would be a worse problem than the one
    being fixed, so an unfinished pass has to be reported as unfinished."""
    result = prune(database, retention_days=7, batch=1, budget_seconds=0.0, now=NOW)

    assert result.deleted == 0
    assert result.finished is False


def test_an_unfinished_pass_resumes_on_the_next_call(database):
    first = prune(database, retention_days=7, batch=10, budget_seconds=0.05, now=NOW)
    total = first.deleted
    for _ in range(30):
        nxt = prune(database, retention_days=7, batch=10, budget_seconds=0.05, now=NOW)
        total += nxt.deleted
        if nxt.finished:
            break
    assert total == 150


def test_a_missing_database_is_reported_not_raised(tmp_path):
    """This runs on the shutdown path, where an exception costs the checkpoint
    the shutdown exists to perform."""
    result = prune(tmp_path / "gone.db", now=NOW)
    assert result.deleted == 0
    assert "missing" in result.error


def test_a_broken_table_is_reported_not_raised(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()          # no feed_metrics table at all
    result = prune(path, now=NOW)
    assert result.error, "a missing table must surface, not propagate"


def test_the_cutoff_is_utc_and_sorts_like_the_stored_text():
    """observed_at is an ISO-8601 UTC string, compared as text. Zero-padded
    UTC on both sides is what makes lexicographic and chronological agree."""
    cutoff = cutoff_timestamp(7, now=NOW)
    assert cutoff.startswith("2026-08-18T12:00:00")
    assert cutoff > _stamp(30)
    assert cutoff < _stamp(1)


def test_the_estimate_counts_exactly_what_the_prune_would_delete(database):
    pending = estimate_pending(database, retention_days=7, now=NOW)
    assert pending == prune(database, retention_days=7, now=NOW).deleted


def test_retention_is_on_by_default_but_can_be_turned_off():
    from scripts.rubix_collector_supervisor import build_parser

    base = ["--adapter", "a", "--auth-frame-file", "b", "--database", "c"]
    assert build_parser().parse_args(base).prune_telemetry is True
    assert build_parser().parse_args(base + ["--no-prune-telemetry"]).prune_telemetry is False
    assert build_parser().parse_args(base).telemetry_retention_days == 7


def test_pruning_happens_before_the_truncating_checkpoint():
    """The deletes are journalled too. Truncate first and the freed space sits
    in the WAL instead of being reclaimed."""
    import inspect

    from scripts.rubix_collector_supervisor import CollectorSupervisor

    shutdown = inspect.getsource(CollectorSupervisor.run).split("finally", 1)[1]
    assert shutdown.index("prune_telemetry()") < shutdown.index("truncate=True")


# --- the pruner has to actually be called ------------------------------------
#
# It never was. Pruning lived only in the shutdown `finally`, and this
# supervisor is killed rather than asked to stop: the health file has never
# carried `shutdown: true`, and no log in the project has ever contained a
# `telemetry_pruned` event. So the module below ran zero times in production
# while feed_metrics grew from 44,246,339 rows on 2026-08-25 to 55,771,859 on
# 2026-09-01 -- 1.6 million a day, and larger than the quotes table it
# describes. Counting it by event, which health() did on every two-second
# heartbeat, took 150 seconds.

def _run_parts():
    """The loop body and the exit handler, split on the statement itself.

    Splitting on the bare word would also match prose: a comment explaining why
    the exit handler is not enough broke three tests that split this way,
    including one written long before it.
    """

    import inspect

    from scripts.rubix_collector_supervisor import CollectorSupervisor

    source = inspect.getsource(CollectorSupervisor.run)
    loop, _, handler = source.partition("\n        finally:")
    assert handler, "run() no longer has an exit handler"
    return loop, handler


def test_pruning_also_runs_while_the_supervisor_is_alive():
    """Maintenance that only happens on a clean exit is maintenance that only
    happens when it was not needed."""

    loop, _ = _run_parts()
    assert "prune_telemetry()" in loop


def test_pruning_is_tied_to_the_maintenance_interval_not_the_heartbeat():
    """Every heartbeat would be every two seconds. The prune takes a budgeted
    sixty, so it belongs on the maintenance schedule beside the checkpoint."""

    loop, _ = _run_parts()
    maintenance = loop.split("self.args.maintenance_seconds", 1)[1]
    assert "prune_telemetry()" in maintenance


def test_the_shutdown_prune_is_still_there():
    """The periodic call is an addition, not a replacement: a clean exit is
    still the best moment to reclaim, with the collector already stopped."""

    _, handler = _run_parts()
    assert "prune_telemetry()" in handler
