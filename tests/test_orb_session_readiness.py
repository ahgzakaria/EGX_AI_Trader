"""The pre-flight gate must refuse the exact condition that lost 2026-08-13.

One invariant, tested against a synthetic feed rather than the live one: a
machine clock behind the exchange produces a negative median lag, and the gate
must fail on it. Everything else in the script is printing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from scripts import check_orb_session_readiness as readiness


def _feed(path, *, lag_seconds, count=200):
    """A Rubix-shaped quotes table whose receive lag is exactly `lag_seconds`."""

    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT, market_timestamp TEXT, received_at TEXT)"
    )
    base = datetime.now(timezone.utc) - timedelta(seconds=30)
    for index in range(count):
        market = base + timedelta(seconds=index * 0.1)
        received = market + timedelta(seconds=lag_seconds)
        connection.execute(
            "INSERT INTO quotes (ticker, market_timestamp, received_at) "
            "VALUES (?,?,?)",
            ("MFPC", market.isoformat(), received.isoformat()),
        )
    connection.commit()
    connection.close()
    return path


def test_gate_refuses_a_clock_behind_the_exchange(tmp_path):
    """The 2026-08-13 failure: received_at earlier than market_timestamp.

    The pipeline reads that as clock skew and discards the events, so no
    opening-range bar becomes final and the session is dead on arrival. The
    gate has to catch it before the session starts, not after.
    """

    database = _feed(tmp_path / "rubix.db", lag_seconds=-0.60)
    assert readiness.main(["--rubix-db-path", str(database)]) == 1


def test_gate_passes_a_healthy_clock(tmp_path):
    database = _feed(tmp_path / "rubix.db", lag_seconds=+1.41)
    assert readiness.main(["--rubix-db-path", str(database)]) == 0


def test_gate_reports_the_measured_direction(tmp_path):
    negative = readiness.measure(_feed(tmp_path / "a.db", lag_seconds=-0.60), 800)
    positive = readiness.measure(_feed(tmp_path / "b.db", lag_seconds=+1.41), 800)

    assert negative["median_lag"] == pytest.approx(-0.60, abs=0.05)
    assert negative["negative_fraction"] == pytest.approx(1.0)
    assert positive["median_lag"] == pytest.approx(1.41, abs=0.05)
    assert positive["negative_fraction"] == 0.0


def test_missing_database_fails_rather_than_raises(tmp_path):
    assert readiness.main(["--rubix-db-path", str(tmp_path / "absent.db")]) == 2


def test_stale_feed_only_fails_when_explicitly_required(tmp_path):
    """Before the open a quiet feed is normal, so staleness is opt-in."""

    connection = sqlite3.connect(tmp_path / "old.db")
    connection.execute(
        "CREATE TABLE quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT, market_timestamp TEXT, received_at TEXT)"
    )
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    for index in range(50):
        market = old + timedelta(seconds=index)
        connection.execute(
            "INSERT INTO quotes (ticker, market_timestamp, received_at) "
            "VALUES (?,?,?)",
            ("MFPC", market.isoformat(),
             (market + timedelta(seconds=1.2)).isoformat()),
        )
    connection.commit()
    connection.close()

    path = str(tmp_path / "old.db")
    assert readiness.main(["--rubix-db-path", path]) == 0
    assert readiness.main(["--rubix-db-path", path, "--require-fresh-feed"]) == 1
