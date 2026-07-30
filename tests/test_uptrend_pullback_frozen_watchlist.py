"""Frozen-watchlist tests for UPTREND_PULLBACK_SCALPING_V1.

The frozen list is built from D-1 completed daily history, is namespaced by its
own strategy identity, and cannot be mutated once published — so nothing that
happens during the live session (including Rubix movement) can reorder it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import sqlite3

import numpy as np
import pandas as pd
import pytest

from scalping_uptrend_pullback.config import UptrendPullbackSelectionConfig
from scalping_uptrend_pullback.frozen_watchlist import (
    CONFIG_VERSION_MISMATCH,
    FrozenUptrendWatchlistService,
    NO_ELIGIBLE_SYMBOLS,
    PROVENANCE_REJECTED,
    SOURCE_FINGERPRINT_CHANGED,
    SOURCE_UNAVAILABLE,
    UptrendWatchlistRepository,
    WATCHLIST_NOT_GENERATED,
    WATCHLIST_READY,
    make_uptrend_watchlist_identity,
    previous_trading_session,
)
from scalping_uptrend_pullback.states import (
    STRATEGY_IDENTITY,
    UPTREND_NEAR_SUPPORT,
)


TARGET = date(2026, 7, 28)
CUTOFF = date(2026, 7, 27)
GENERATED = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)


class _StubCalendar:
    """Every weekday trades — keeps these tests off the EGX holiday file."""

    def is_trading_day(self, day):
        return True

    def next_trading_session(self, day):
        return day


def _frame(closes, *, volume=500_000, provider="eodhd"):
    closes = np.asarray(closes, dtype=float)
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": float(volume),
        },
        index=pd.bdate_range(end=CUTOFF.isoformat(), periods=len(closes)),
    )
    frame.attrs["market_data"] = {
        "provider": provider,
        "volume_safe_for_lookback": True,
    }
    return frame


def _candidate_closes():
    shelf = list(20.0 + 0.12 * np.sin(np.arange(22)))
    return (
        shelf
        + list(np.linspace(20.25, 21.60, 18))
        + list(np.linspace(21.55, 20.62, 12))
        + list(np.linspace(20.66, 20.95, 8))
    )


def _downtrend_closes():
    return list(np.linspace(26.0, 19.0, 60))


def _histories():
    return {
        "AAA": _frame(_candidate_closes()),
        "BBB": _frame([value * 1.5 for value in _candidate_closes()]),
        "CCC": _frame(_downtrend_closes()),
    }


def _service(tmp_path, **overrides):
    config = replace(UptrendPullbackSelectionConfig(), **overrides)
    return FrozenUptrendWatchlistService(
        database_path=tmp_path / "uptrend.db",
        config=config,
        calendar=_StubCalendar(),
    )


def _prepare(service, **kwargs):
    return service.prepare_for_session(
        TARGET,
        histories=_histories(),
        generated_at=GENERATED,
        generation_run_id="run-1",
        **kwargs,
    )


# --- Identity ---------------------------------------------------------------


def test_identity_is_namespaced_by_the_strategy():
    identity = make_uptrend_watchlist_identity(
        target_session_date=TARGET,
        historical_data_cutoff=CUTOFF,
        config=UptrendPullbackSelectionConfig(),
        source_fingerprint="sha256:aaa",
        universe_fingerprint="sha256:bbb",
        candidate_limit=20,
    )

    assert identity.strategy_identity == "UPTREND_PULLBACK_SCALPING_V1"
    assert identity.watchlist_id.startswith("UPS-")
    assert identity.historical_data_cutoff < identity.target_session_date


def test_identity_requires_a_cutoff_strictly_before_the_session():
    with pytest.raises(ValueError, match="D-1"):
        make_uptrend_watchlist_identity(
            target_session_date=TARGET,
            historical_data_cutoff=TARGET,
            config=UptrendPullbackSelectionConfig(),
            source_fingerprint="sha256:aaa",
            universe_fingerprint="sha256:bbb",
            candidate_limit=20,
        )


def test_the_selector_never_shares_the_range_bound_store():
    from scalping_expected_range import frozen_watchlist as range_bound
    from scalping_uptrend_pullback import frozen_watchlist as uptrend

    assert uptrend.DEFAULT_DATABASE_PATH != range_bound.DEFAULT_DATABASE_PATH
    assert (
        uptrend.DATABASE_PATH_ENVIRONMENT_KEY
        != "SCALPING_HISTORICAL_WATCHLIST_DB"
    )
    assert "uptrend_watchlist_headers" in uptrend.SCHEMA
    assert "uptrend_watchlist_headers" not in range_bound.SCHEMA


def test_cutoff_is_the_previous_trading_session(tmp_path):
    service = _service(tmp_path)

    target, cutoff = service.target_and_cutoff(TARGET)

    assert target == TARGET
    assert cutoff == previous_trading_session(TARGET, calendar=_StubCalendar())
    assert cutoff < target


# --- Publication ------------------------------------------------------------


def test_prepare_publishes_a_ready_watchlist(tmp_path):
    service = _service(tmp_path)

    result = _prepare(service)

    assert result.status == WATCHLIST_READY
    assert result.record.header["status"] == "READY"
    assert result.record.header["strategy_identity"] == STRATEGY_IDENTITY
    assert result.record.header["historical_data_cutoff"] == CUTOFF.isoformat()
    assert result.record.header["snapshot_id"]
    assert result.record.members


def test_published_members_carry_the_research_contract(tmp_path):
    service = _service(tmp_path)

    member = _prepare(service).record.members[0]

    assert member["candidate_state"] == UPTREND_NEAR_SUPPORT
    assert member["strategy_identity"] == STRATEGY_IDENTITY
    assert member["source"] == "EODHD_DAILY"
    assert member["data_cutoff"] == CUTOFF.isoformat()
    assert member["source_fingerprint"].startswith("sha256:")
    assert member["first_touch_order_available"] is False
    assert member["ema5"] > member["ema10"]
    assert member["ema5_slope"] > 0 and member["ema10_slope"] > 0
    assert (
        member["invalidation_level"]
        <= member["support_zone_lower"]
        <= member["support_zone_centre"]
        <= member["support_zone_upper"]
    )
    assert member["last_close"] > member["invalidation_level"]
    assert len(member["support_sources"]) >= 2
    assert member["deterministic_reasons"]


def test_ineligible_symbols_are_not_published(tmp_path):
    service = _service(tmp_path)

    symbols = {member["symbol"] for member in _prepare(service).record.members}

    assert "CCC" not in symbols


def test_candidate_limit_is_a_maximum_not_a_quota(tmp_path):
    service = _service(tmp_path)

    record = _prepare(service, candidate_limit=20).record

    assert record.header["candidate_limit"] == 20
    assert 0 < len(record.displayed) < 20
    assert len(record.displayed) == len(record.members)


def test_no_eligible_symbol_fails_honestly(tmp_path):
    service = _service(tmp_path)

    result = service.prepare_for_session(
        TARGET,
        histories={"CCC": _frame(_downtrend_closes())},
        generated_at=GENERATED,
    )

    assert result.status == NO_ELIGIBLE_SYMBOLS
    assert result.record is None


def test_non_eodhd_history_is_rejected_before_any_write(tmp_path):
    service = _service(tmp_path)

    result = service.prepare_for_session(
        TARGET,
        histories={"AAA": _frame(_candidate_closes(), provider="yahoo")},
        generated_at=GENERATED,
    )

    assert result.status == PROVENANCE_REJECTED
    assert service.get_for_session(TARGET).status == WATCHLIST_NOT_GENERATED


def test_empty_universe_is_source_unavailable(tmp_path):
    service = _service(tmp_path)

    result = service.prepare_for_session(
        TARGET, histories={}, generated_at=GENERATED
    )

    assert result.status == SOURCE_UNAVAILABLE


# --- Immutability and freezing ----------------------------------------------


def test_repeated_preparation_reuses_the_frozen_snapshot(tmp_path):
    service = _service(tmp_path)

    first = _prepare(service)
    second = service.prepare_for_session(
        TARGET,
        histories=_histories(),
        generated_at=datetime(2026, 7, 28, 9, 30, tzinfo=timezone.utc),
        generation_run_id="run-2",
    )

    assert second.status == WATCHLIST_READY
    assert second.reused is True
    assert (
        second.record.header["watchlist_id"]
        == first.record.header["watchlist_id"]
    )
    assert [member["symbol"] for member in second.record.members] == [
        member["symbol"] for member in first.record.members
    ]
    assert [member["total_score"] for member in second.record.members] == [
        member["total_score"] for member in first.record.members
    ]


def test_published_rows_cannot_be_mutated(tmp_path):
    service = _service(tmp_path)
    watchlist_id = _prepare(service).record.header["watchlist_id"]
    repository = service.repository

    with repository.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE uptrend_watchlist_members SET total_score=99 "
                "WHERE watchlist_id=?",
                (watchlist_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM uptrend_watchlist_members WHERE watchlist_id=?",
                (watchlist_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE uptrend_watchlist_headers SET status='FAILED' "
                "WHERE watchlist_id=?",
                (watchlist_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM uptrend_watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            )


def test_a_foreign_strategy_identity_cannot_be_stored(tmp_path):
    repository = UptrendWatchlistRepository(tmp_path / "uptrend.db")

    with repository.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO uptrend_watchlist_headers (
                   watchlist_id, identity_key, strategy_identity, schema_version,
                   target_session_date, historical_data_cutoff, provider,
                   lookback_sessions, minimum_valid_sessions, metric_version,
                   config_version, source_fingerprint,
                   source_fingerprint_status, universe_fingerprint,
                   candidate_limit, generated_at, generation_run_id, status,
                   generation_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "UPS-foreign",
                    "foreign",
                    "RANGE_BOUND_HISTORICAL_SELECTION_V1",
                    1,
                    TARGET.isoformat(),
                    CUTOFF.isoformat(),
                    "EODHD_DAILY",
                    60,
                    40,
                    "m",
                    "c",
                    "sha256:a",
                    "SOURCE_FINGERPRINT_VERIFIED",
                    "sha256:b",
                    20,
                    GENERATED.isoformat(),
                    "run",
                    "GENERATING",
                    "TEST",
                ),
            )


def test_reading_is_unaffected_by_later_price_movement(tmp_path):
    """Rubix moves intraday; the frozen daily snapshot must not follow it."""

    service = _service(tmp_path)
    published = _prepare(service).record

    moved = _histories()
    moved["CCC"] = _frame([value * 1.4 for value in _candidate_closes()])
    after_movement = service.prepare_for_session(
        TARGET, histories=moved, generated_at=GENERATED
    )
    reread = service.get_for_session(TARGET)

    assert reread.status == WATCHLIST_READY
    assert [member["symbol"] for member in reread.record.members] == [
        member["symbol"] for member in published.members
    ]
    # A different universe fingerprint is a different identity, never an
    # in-place edit of the session that is already frozen.
    assert after_movement.record.header["watchlist_id"] != (
        published.header["watchlist_id"]
    )


# --- Read-side validation ---------------------------------------------------


def test_reader_rejects_a_stored_snapshot_from_another_config(tmp_path):
    service = _service(tmp_path)
    _prepare(service)

    retuned = FrozenUptrendWatchlistService(
        database_path=tmp_path / "uptrend.db",
        config=replace(
            UptrendPullbackSelectionConfig(),
            config_version="UPTREND_PULLBACK_SCALPING_CONFIG_V2",
        ),
        calendar=_StubCalendar(),
    )

    assert retuned.get_for_session(TARGET).status == CONFIG_VERSION_MISMATCH


def test_reader_rejects_a_changed_source_fingerprint(tmp_path):
    service = _service(tmp_path)
    _prepare(service)

    result = service.get_for_session(
        TARGET, expected_source_fingerprint="sha256:different"
    )

    assert result.status == SOURCE_FINGERPRINT_CHANGED


def test_latest_ready_returns_the_frozen_record(tmp_path):
    service = _service(tmp_path)
    _prepare(service)

    result = service.get_latest_ready()

    assert result.status == WATCHLIST_READY
    assert result.record.header["strategy_identity"] == STRATEGY_IDENTITY


def test_repository_reports_its_own_integrity(tmp_path):
    repository = UptrendWatchlistRepository(tmp_path / "uptrend.db")

    report = repository.integrity_check()

    assert report["status"] == "ok"
    assert report["journal_mode"] == "WAL"
    assert report["strategy_identity"] == STRATEGY_IDENTITY
