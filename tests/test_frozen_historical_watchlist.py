"""Phase 3A lifecycle tests for immutable historical scalping watchlists."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import inspect
import sqlite3
import threading

import pandas as pd
import pytest

from scalping_expected_range.config import DailyHistoricalSelectionConfig
from scalping_expected_range.frozen_watchlist import (
    CONFIG_VERSION_MISMATCH,
    EODHD_DAILY,
    FAILED,
    GENERATION_FAILED,
    HARD_ELIGIBLE,
    INSUFFICIENT_DAILY_HISTORY,
    NO_ELIGIBLE_SYMBOLS,
    PROVENANCE_REJECTED,
    READY,
    SOURCE_FINGERPRINT_CHANGED,
    SOURCE_UNAVAILABLE,
    WATCHLIST_NOT_GENERATED,
    WATCHLIST_READY,
    FrozenHistoricalWatchlistService,
    FrozenWatchlistRepository,
    fingerprint_histories,
    fingerprint_universe,
    make_watchlist_identity,
    previous_trading_session,
    resolve_target_session,
    validated_eodhd_symbols,
)


CUTOFF = date(2026, 7, 27)
TARGET = date(2026, 7, 28)
GENERATED = "2026-07-27T15:00:00+00:00"


class _Calendar:
    def __init__(self, holidays=()):
        self.holidays = set(holidays)

    def is_trading_day(self, day):
        return day.weekday() in {0, 1, 2, 3, 6} and day not in self.holidays

    def next_trading_session(self, day):
        candidate = day + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate


def _history(value=3.0, *, end="2026-07-27", n=60, volume_safe=True):
    values = [value - 0.2, value, value + 0.2, value - 0.1, value + 0.1]
    ranges = (values * ((n + 4) // 5))[:n]
    index = pd.date_range(end=end, periods=n, freq="D")
    frame = pd.DataFrame(
        {
            "Open": 100.0,
            "High": [100.0 + item / 2 for item in ranges],
            "Low": [100.0 - item / 2 for item in ranges],
            "Close": 100.0,
            "Volume": 1_000_000.0,
        },
        index=index,
    )
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "effective_provider": "eodhd",
        "source_provider": EODHD_DAILY,
        "interval": "1d",
        "price_series": "SPLIT_ADJUSTED",
        "raw_adjusted_mode": "SPLIT_ADJUSTED_OHLC_EVENT_SPECIFIC_VOLUME",
        "normalization_data_cutoff": str(end),
        "volume_safe_for_lookback": volume_safe,
        "fallback_used": False,
        "yahoo_network_used": False,
        "corporate_action_policy_version": "test",
    }
    return frame


def _histories(count=25):
    return {
        f"S{index:03d}": _history(4.0 + index / 100.0)
        for index in range(count)
    }


def _service(tmp_path, **kwargs):
    return FrozenHistoricalWatchlistService(
        database_path=tmp_path / "watchlists.db",
        calendar=_Calendar(),
        **kwargs,
    )


def _prepare(service, histories=None, **kwargs):
    kwargs.setdefault("generated_at", GENERATED)
    kwargs.setdefault("generation_run_id", "RUN-TEST")
    return service.prepare_for_session(
        TARGET,
        histories=histories or _histories(),
        **kwargs,
    )


def test_validated_manifest_resolves_accepted_225_symbol_universe():
    symbols = validated_eodhd_symbols()

    assert len(symbols) == 225
    assert "EGX30ETF" not in symbols
    assert "RAYA" in symbols


def test_watchlist_identity_is_deterministic_and_includes_top_n():
    config = DailyHistoricalSelectionConfig()
    first = make_watchlist_identity(
        target_session_date=TARGET,
        historical_data_cutoff=CUTOFF,
        config=config,
        source_fingerprint="sha256:source",
        universe_fingerprint="sha256:universe",
        top_n=20,
    )
    second = make_watchlist_identity(
        target_session_date=TARGET,
        historical_data_cutoff=CUTOFF,
        config=config,
        source_fingerprint="sha256:source",
        universe_fingerprint="sha256:universe",
        top_n=20,
    )
    changed = make_watchlist_identity(
        target_session_date=TARGET,
        historical_data_cutoff=CUTOFF,
        config=config,
        source_fingerprint="sha256:source",
        universe_fingerprint="sha256:universe",
        top_n=10,
    )

    assert first == second
    assert first.watchlist_id.startswith("FHW-")
    assert changed.watchlist_id != first.watchlist_id


def test_history_and_universe_fingerprints_are_order_invariant():
    histories = {"B": _history(3.1), "A": _history(2.9)}

    first = fingerprint_histories(
        histories, data_cutoff=CUTOFF, lookback_sessions=60
    )
    second = fingerprint_histories(
        dict(reversed(list(histories.items()))),
        data_cutoff=CUTOFF,
        lookback_sessions=60,
    )

    assert first == second
    assert fingerprint_universe(["B", "A"]) == fingerprint_universe(["A", "B"])


def test_idempotent_generation_calculates_once(tmp_path):
    calls = []

    from scalping_expected_range.daily_historical_selection import (
        build_frozen_daily_watchlist,
    )

    def builder(*args, **kwargs):
        calls.append("build")
        return build_frozen_daily_watchlist(*args, **kwargs)

    service = _service(tmp_path, snapshot_builder=builder)

    first = _prepare(service)
    second = _prepare(service)

    assert first.status == second.status == WATCHLIST_READY
    assert first.record.header["watchlist_id"] == second.record.header["watchlist_id"]
    assert second.reused is True
    assert calls == ["build"]


def test_atomic_publication_writes_complete_ready_record(tmp_path):
    service = _service(tmp_path)
    result = _prepare(service)
    integrity = service.repository.integrity_check()

    assert result.status == WATCHLIST_READY
    assert result.record.header["status"] == READY
    assert result.record.header["eligible_count"] == 25
    assert result.record.header["displayed_count"] == 20
    assert len(result.record.members) == 25
    assert integrity["journal_mode"] == "WAL"
    assert integrity["foreign_keys"] is True
    assert integrity["status"] == "ok"


def test_interrupted_generation_is_failed_and_never_ready(tmp_path):
    def interrupt(*_args):
        raise RuntimeError("simulated interruption")

    service = _service(tmp_path, publication_hook=interrupt)
    result = _prepare(service)
    stored = service.repository.latest_for_session(TARGET.isoformat())

    assert result.status == GENERATION_FAILED
    assert stored.header["status"] == FAILED
    assert stored.header["failure_code"] == GENERATION_FAILED
    assert stored.members == ()
    assert service.get_for_session(TARGET).status == GENERATION_FAILED


def test_duplicate_generation_is_claimed_before_second_builder_starts(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    from scalping_expected_range.daily_historical_selection import (
        build_frozen_daily_watchlist,
    )

    def builder(*args, **kwargs):
        calls.append("build")
        entered.set()
        assert release.wait(5)
        return build_frozen_daily_watchlist(*args, **kwargs)

    first_service = _service(tmp_path, snapshot_builder=builder)
    second_service = FrozenHistoricalWatchlistService(
        database_path=tmp_path / "watchlists.db",
        calendar=_Calendar(),
        snapshot_builder=builder,
    )
    holder = {}
    thread = threading.Thread(
        target=lambda: holder.setdefault("result", _prepare(first_service))
    )
    thread.start()
    assert entered.wait(5)

    duplicate = _prepare(second_service)
    release.set()
    thread.join(10)

    assert duplicate.status == WATCHLIST_NOT_GENERATED
    assert duplicate.reused is True
    assert holder["result"].status == WATCHLIST_READY
    assert calls == ["build"]


def test_ready_header_and_members_are_sql_immutable(tmp_path):
    service = _service(tmp_path)
    result = _prepare(service)
    watchlist_id = result.record.header["watchlist_id"]

    with service.repository.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="completed.*immutable"):
            connection.execute(
                "UPDATE watchlist_headers SET eligible_count=0 WHERE watchlist_id=?",
                (watchlist_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="headers are immutable"):
            connection.execute(
                "DELETE FROM watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="members are immutable"):
            connection.execute(
                """UPDATE watchlist_members SET historical_score=0
                   WHERE watchlist_id=? AND historical_rank=1""",
                (watchlist_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="members are immutable"):
            connection.execute(
                """DELETE FROM watchlist_members
                   WHERE watchlist_id=? AND historical_rank=1""",
                (watchlist_id,),
            )


def test_complete_eligible_universe_and_top_n_marker_are_stored(tmp_path):
    result = _prepare(_service(tmp_path), top_n=7)

    assert len(result.record.members) == 25
    assert [row["historical_rank"] for row in result.record.members] == list(
        range(1, 26)
    )
    assert [row["historical_rank"] for row in result.record.displayed] == list(
        range(1, 8)
    )
    assert all(row["eligibility_status"] == HARD_ELIGIBLE for row in result.record.members)


def test_top_n_change_does_not_change_scores_or_eligible_universe(tmp_path):
    service = _service(tmp_path)
    five = _prepare(service, top_n=5)
    ten = _prepare(service, top_n=10, generation_run_id="RUN-TEN")

    scores_five = {
        row["symbol"]: row["historical_score"] for row in five.record.members
    }
    scores_ten = {
        row["symbol"]: row["historical_score"] for row in ten.record.members
    }
    assert five.record.header["watchlist_id"] != ten.record.header["watchlist_id"]
    assert scores_five == scores_ten
    assert len(five.record.members) == len(ten.record.members) == 25
    assert len(five.record.displayed) == 5
    assert len(ten.record.displayed) == 10


def test_d1_cutoff_excludes_current_day_and_d_becomes_usable_for_d_plus_1(tmp_path):
    histories = _histories()
    with_day_d = {}
    for symbol, frame in histories.items():
        current = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [190.0],
                "Low": [10.0],
                "Close": [150.0],
                "Volume": [999_000_000.0],
            },
            index=[pd.Timestamp("2026-07-28")],
        )
        changed = pd.concat([frame, current])
        changed.attrs = dict(frame.attrs)
        with_day_d[symbol] = changed

    service = _service(tmp_path)
    baseline = _prepare(service, histories=histories)
    same_session = _prepare(
        service,
        histories=with_day_d,
        generation_run_id="RUN-CURRENT-DAY",
    )
    next_session = service.prepare_for_session(
        "2026-07-29",
        histories=with_day_d,
        generated_at=GENERATED,
        generation_run_id="RUN-D-PLUS-ONE",
    )

    assert baseline.record.header["historical_data_cutoff"] == "2026-07-27"
    assert same_session.record.header["watchlist_id"] == baseline.record.header["watchlist_id"]
    assert [row["symbol"] for row in same_session.record.members] == [
        row["symbol"] for row in baseline.record.members
    ]
    assert next_session.record.header["historical_data_cutoff"] == "2026-07-28"
    assert next_session.record.header["watchlist_id"] != baseline.record.header["watchlist_id"]


def test_missing_latest_session_is_disclosed_without_fabricating_a_bar(tmp_path):
    histories = {
        symbol: _history(4.0 + index / 100.0, end="2026-07-26")
        for index, symbol in enumerate(_histories())
    }

    result = _prepare(_service(tmp_path), histories=histories)

    assert result.status == WATCHLIST_READY
    assert result.record.header["historical_data_cutoff"] == "2026-07-27"
    assert all(
        member["latest_session"] == "2026-07-26"
        for member in result.record.members
    )


def test_incomplete_latest_session_is_excluded(tmp_path):
    histories = {
        f"S{index:03d}": _history(4.0 + index / 100.0, n=61)
        for index in range(25)
    }
    changed = {}
    for symbol, frame in histories.items():
        incomplete = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [180.0],
                "Low": [20.0],
                "Close": [140.0],
                "Volume": [900_000_000.0],
                "Complete": [False],
            },
            index=[pd.Timestamp("2026-07-27")],
        )
        base = frame.iloc[:-1].copy()
        base["Complete"] = True
        combined = pd.concat([base, incomplete])
        combined.attrs = dict(frame.attrs)
        changed[symbol] = combined

    result = _prepare(_service(tmp_path), histories=changed)

    assert result.status == WATCHLIST_READY
    assert all(
        member["latest_session"] == "2026-07-26"
        for member in result.record.members
    )


def test_stale_cache_fails_closed_without_ready_watchlist(tmp_path):
    histories = {
        symbol: _history(4.0 + index / 100.0, end="2026-07-01")
        for index, symbol in enumerate(_histories())
    }
    service = _service(tmp_path)

    result = service.prepare_for_session(
        "2026-07-28",
        histories=histories,
        generated_at=GENERATED,
        generation_run_id="STALE",
    )
    stored = service.repository.latest_for_session("2026-07-28")

    assert result.status == SOURCE_UNAVAILABLE
    assert stored.header["status"] == FAILED
    assert stored.members == ()


def test_weekend_holiday_and_timezone_target_resolution():
    holiday = date(2026, 7, 23)
    calendar = _Calendar({holiday})

    assert resolve_target_session("2026-07-31", calendar=calendar) == date(
        2026, 8, 2
    )
    assert previous_trading_session("2026-08-02", calendar=calendar) == date(
        2026, 7, 30
    )
    assert resolve_target_session(holiday, calendar=calendar) == date(
        2026, 7, 26
    )
    assert previous_trading_session("2026-07-26", calendar=calendar) == date(
        2026, 7, 22
    )
    utc_boundary = datetime(2026, 7, 27, 21, 30, tzinfo=timezone.utc)
    assert resolve_target_session(now=utc_boundary, calendar=calendar) == TARGET


@pytest.mark.parametrize("provider", ["yahoo", "rubix", "unknown"])
def test_non_eodhd_and_unknown_provenance_are_rejected(tmp_path, provider):
    frame = _history()
    frame.attrs["market_data"]["provider"] = provider
    frame.attrs["market_data"]["effective_provider"] = provider

    result = _prepare(_service(tmp_path), histories={"X": frame})

    assert result.status == PROVENANCE_REJECTED
    assert service_db_headers(tmp_path) == 0


def test_missing_provider_metadata_is_rejected(tmp_path):
    frame = _history()
    frame.attrs = {}

    result = _prepare(_service(tmp_path), histories={"X": frame})

    assert result.status == PROVENANCE_REJECTED


def test_missing_member_source_fingerprint_fails_closed(tmp_path):
    from scalping_expected_range.daily_historical_selection import (
        FrozenDailyWatchlist,
        build_frozen_daily_watchlist,
    )

    def builder(*args, **kwargs):
        snapshot = build_frozen_daily_watchlist(*args, **kwargs)
        altered = tuple(
            replace(
                result,
                source_data_fingerprint=None,
            )
            if result.eligible
            else result
            for result in snapshot.results
        )
        return FrozenDailyWatchlist(
            snapshot.source_provider,
            snapshot.metric_version,
            snapshot.config_version,
            snapshot.data_cutoff,
            snapshot.generated_at,
            snapshot.snapshot_id,
            altered,
            snapshot.candidate_symbols,
        )

    result = _prepare(_service(tmp_path, snapshot_builder=builder))

    assert result.status == GENERATION_FAILED
    assert result.record is None
    stored = FrozenWatchlistRepository(
        tmp_path / "watchlists.db"
    ).latest_for_session(TARGET.isoformat())
    assert stored.header["status"] == FAILED
    assert stored.members == ()


def test_60_30_zone_confidence_and_provenance_fields_are_persisted(tmp_path):
    member = _prepare(_service(tmp_path)).record.members[0]

    assert member["valid_sessions_primary"] == 60
    assert member["valid_sessions_recent"] == 30
    assert member["recent_confirmation_status"]
    assert (
        member["zone_confidence_label"]
        == "STABLE_RANGE_BOUND_CANDIDATE"
    )
    assert member["source"] == EODHD_DAILY
    assert member["source_fingerprint"].startswith("sha256:")
    assert member["metric_version"] == "RANGE_BOUND_HISTORICAL_SELECTION_V1"
    assert member["data_cutoff"] == "2026-07-27"
    assert member["range_bound_score"] == member["historical_score"]
    assert member["support_zone_low"] <= member["support_zone_high"]
    assert (
        member["support_zone_high"]
        < member["resistance_zone_low"]
        <= member["resistance_zone_high"]
    )
    assert member["channel_direction"] == "HORIZONTAL"
    assert member["channel_width_percent"] >= 3.0
    assert member["range_bound_status"] == "STABLE_RANGE_BOUND_CANDIDATE"
    assert "HORIZONTAL_CHANNEL_CONFIRMED" in member[
        "range_bound_reasons"
    ]


def test_nine_unresolved_volume_symbols_remain_excluded(tmp_path):
    histories = _histories(20)
    for index in range(9):
        histories[f"V{index}"] = _history(
            3.0 + index / 100.0, volume_safe=False
        )

    result = _prepare(_service(tmp_path), histories=histories)
    symbols = {row["symbol"] for row in result.record.members}

    assert result.record.header["eligible_count"] == 20
    assert not symbols.intersection({f"V{index}" for index in range(9)})


def test_refresh_navigation_and_second_observer_reuse_same_ready_id(tmp_path):
    first = _service(tmp_path)
    prepared = _prepare(first)
    second_observer = FrozenHistoricalWatchlistService(
        database_path=tmp_path / "watchlists.db", calendar=_Calendar()
    )
    navigation_return = FrozenHistoricalWatchlistService(
        database_path=tmp_path / "watchlists.db", calendar=_Calendar()
    )

    refresh = first.get_for_session(TARGET)
    observed = second_observer.get_for_session(TARGET)
    returned = navigation_return.get_for_session(TARGET)
    ids = {
        prepared.record.header["watchlist_id"],
        refresh.record.header["watchlist_id"],
        observed.record.header["watchlist_id"],
        returned.record.header["watchlist_id"],
    }

    assert ids == {prepared.record.header["watchlist_id"]}
    assert refresh.reused and observed.reused and returned.reused


def test_live_rubix_values_cannot_change_identity_membership_or_order(tmp_path):
    histories = _histories()
    service = _service(tmp_path)
    first = _prepare(service, histories=histories)
    changed = {}
    for symbol, frame in histories.items():
        clone = frame.copy()
        clone.attrs = dict(frame.attrs)
        clone.attrs["rubix_live"] = {
            "current_price": 9999,
            "change_percent": 88,
            "current_volume": 999_999_999,
            "rvol": 50,
            "spread": 0.001,
        }
        changed[symbol] = clone

    second = _prepare(
        service,
        histories=changed,
        generation_run_id="RUN-LIVE-MUTATION",
    )

    assert second.record.header["watchlist_id"] == first.record.header["watchlist_id"]
    assert [row["symbol"] for row in second.record.members] == [
        row["symbol"] for row in first.record.members
    ]
    assert all(
        "current" not in key and "rubix" not in key
        for row in second.record.members
        for key in row
    )


def test_get_rejects_config_mismatch_and_source_fingerprint_change(tmp_path):
    service = _service(tmp_path)
    prepared = _prepare(service)
    changed_config = replace(
        DailyHistoricalSelectionConfig(),
        config_version="DAILY_HISTORICAL_SELECTION_CONFIG_V3_TEST",
    )
    mismatch_service = FrozenHistoricalWatchlistService(
        database_path=tmp_path / "watchlists.db",
        calendar=_Calendar(),
        config=changed_config,
    )

    assert mismatch_service.get_for_session(TARGET).status == CONFIG_VERSION_MISMATCH
    assert (
        service.get_for_session(
            TARGET, expected_source_fingerprint="sha256:different"
        ).status
        == SOURCE_FINGERPRINT_CHANGED
    )
    assert prepared.status == WATCHLIST_READY


def test_research_rebuild_requires_confirmation_and_preserves_same_identity(tmp_path):
    service = _service(tmp_path)
    denied = service.rebuild_for_research(
        TARGET, histories=_histories(), authorized=False
    )
    first = service.rebuild_for_research(
        TARGET,
        histories=_histories(),
        authorized=True,
        generated_at=GENERATED,
        generation_run_id="RESEARCH-1",
    )
    second = service.rebuild_for_research(
        TARGET,
        histories=_histories(),
        authorized=True,
        generated_at="2026-07-27T16:00:00+00:00",
        generation_run_id="RESEARCH-2",
    )

    assert denied.status == WATCHLIST_NOT_GENERATED
    assert first.status == second.status == WATCHLIST_READY
    assert first.record.header["watchlist_id"] == second.record.header["watchlist_id"]
    assert first.record.header["generation_reason"] == "AUTHORIZED_RESEARCH_REBUILD"


def test_changed_completed_source_creates_new_identity_and_preserves_prior_ready(tmp_path):
    service = _service(tmp_path)
    histories = _histories()
    first = _prepare(service, histories=histories)
    changed = {symbol: frame.copy() for symbol, frame in histories.items()}
    for symbol, frame in changed.items():
        frame.attrs = dict(histories[symbol].attrs)
    changed["S000"].loc[pd.Timestamp("2026-07-27"), "High"] = 109.0

    second = _prepare(
        service,
        histories=changed,
        generated_at="2026-07-27T16:00:00+00:00",
        generation_run_id="SOURCE-CHANGED",
    )

    assert first.status == second.status == WATCHLIST_READY
    assert first.record.header["watchlist_id"] != second.record.header["watchlist_id"]
    assert (
        first.record.header["source_fingerprint"]
        != second.record.header["source_fingerprint"]
    )
    with service.repository.connect() as connection:
        ready = connection.execute(
            """SELECT COUNT(*) FROM watchlist_headers
               WHERE target_session_date=? AND status='READY'""",
            (TARGET.isoformat(),),
        ).fetchone()[0]
    assert ready == 2
    assert service.repository.get_by_id(
        first.record.header["watchlist_id"]
    ).header["status"] == READY


def test_empty_and_insufficient_inputs_return_typed_states(tmp_path):
    service = _service(tmp_path)

    unavailable = service.prepare_for_session(TARGET, histories={})
    insufficient = service.prepare_for_session(
        TARGET,
        histories={"SHORT": _history(n=10)},
        generated_at=GENERATED,
        generation_run_id="SHORT",
    )

    assert unavailable.status == SOURCE_UNAVAILABLE
    assert insufficient.status == INSUFFICIENT_DAILY_HISTORY


def test_no_eligible_symbols_returns_typed_state_without_ready_members(tmp_path):
    service = _service(tmp_path)
    low_range = {
        f"L{index}": _history(0.5) for index in range(5)
    }

    result = _prepare(service, histories=low_range)
    stored = service.repository.latest_for_session(TARGET.isoformat())

    assert result.status == NO_ELIGIBLE_SYMBOLS
    assert stored.header["status"] == FAILED
    assert stored.members == ()


def test_failure_detail_is_sanitized(tmp_path):
    def explode(*_args, **_kwargs):
        raise RuntimeError("api_token=SECRET-VALUE\nprivate line")

    service = _service(tmp_path, snapshot_builder=explode)
    result = _prepare(service)
    stored = service.repository.latest_for_session(TARGET.isoformat())

    assert result.status == GENERATION_FAILED
    assert "SECRET-VALUE" not in result.detail
    assert "SECRET-VALUE" not in stored.header["failure_detail"]
    assert "\n" not in stored.header["failure_detail"]


def test_watchlist_comparison_reports_overlap_turnover_and_rank_change(tmp_path):
    service = _service(tmp_path)
    first = _prepare(service, top_n=5)
    changed = _histories()
    changed["S024"] = _history(8.0)
    second = service.prepare_for_session(
        "2026-07-29",
        histories=changed,
        top_n=5,
        generated_at=GENERATED,
        generation_run_id="COMPARE-2",
    )

    comparison = service.compare_watchlists(
        first.record.header["watchlist_id"],
        second.record.header["watchlist_id"],
    )

    assert comparison.previous_target_session == "2026-07-28"
    assert comparison.current_target_session == "2026-07-29"
    assert comparison.current_data_cutoff == "2026-07-28"
    assert comparison.top_n_turnover >= 0
    assert (
        len(comparison.candidate_overlap)
        + len(comparison.additions)
        == 5
    )
    assert all("rank_change" in row for row in comparison.rank_changes)


def test_service_has_no_legacy_live_or_execution_dependency():
    import scalping_expected_range.frozen_watchlist as module

    source = inspect.getsource(module).lower()
    forbidden = (
        "range_scanner",
        "legacy ers",
        "rubix_live_market",
        "broker_order",
        "place_order",
        "production_enabled = true",
    )

    assert all(term not in source for term in forbidden)


def test_historical_table_contains_no_current_or_live_columns(tmp_path):
    from dashboard.scalping import _historical_watchlist_frame

    record = _prepare(_service(tmp_path)).record
    frame = _historical_watchlist_frame(record, displayed_only=True)

    assert len(frame) == 20
    assert {
        "rank",
        "symbol",
        "range_bound_score",
        "historical_score",
        "buy_zone_support",
        "sell_zone_resistance",
        "channel_width_percent",
        "channel_direction",
        "channel_stability",
        "containment",
        "primary_60_state",
        "recent_30_state",
        "historical_explanation",
    } <= set(frame.columns)
    assert not any(
        token in column.lower()
        for column in frame.columns
        for token in ("current", "rubix", "rvol", "spread", "vwap")
    )


def test_historical_panel_renders_ready_record_without_recalculation(tmp_path):
    from streamlit.testing.v1 import AppTest

    prepared = _prepare(_service(tmp_path))
    database = (tmp_path / "watchlists.db").as_posix()
    app = AppTest.from_string(
        f"""
from datetime import timedelta
from dashboard.scalping import _historical_watchlist_panel
from scalping_expected_range.frozen_watchlist import FrozenHistoricalWatchlistService

class Calendar:
    def is_trading_day(self, day):
        return day.weekday() in {{0, 1, 2, 3, 6}}
    def next_trading_session(self, day):
        candidate = day + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

def forbidden_loader(**kwargs):
    raise AssertionError("presentation attempted historical recalculation")

service = FrozenHistoricalWatchlistService(
    database_path={database!r},
    calendar=Calendar(),
    history_loader=forbidden_loader,
)
_historical_watchlist_panel(
    service,
    target_session_date="2026-07-28",
)
"""
    )
    app.run(timeout=30)

    assert not app.exception
    rendered = " ".join(
        str(element.value)
        for collection in (
            app.caption,
            app.info,
            app.success,
            app.warning,
            app.markdown,
        )
        for element in collection
    )
    assert prepared.record.header["watchlist_id"] in rendered
    assert "FROZEN / IMMUTABLE" in rendered
    assert "completed EODHD Daily history" in rendered
    assert "High volatility but descending channel" in rendered
    assert "Production: DISABLED" in rendered
    assert "Research Only" in rendered
    assert (
        f"{prepared.record.header['displayed_count']} candidates found out "
        f"of maximum {prepared.record.header['top_n']}"
        in rendered
    )
    assert len(app.dataframe) >= 2
    assert [button.label for button in app.button] == [
        "Load frozen watchlist",
        "Run Research Rebuild",
    ]
    assert app.button[1].disabled is True


def test_empty_historical_panel_does_not_generate_on_render(tmp_path):
    from streamlit.testing.v1 import AppTest

    database = (tmp_path / "empty-watchlists.db").as_posix()
    app = AppTest.from_string(
        f"""
from datetime import timedelta
from dashboard.scalping import _historical_watchlist_panel
from scalping_expected_range.frozen_watchlist import FrozenHistoricalWatchlistService

class Calendar:
    def is_trading_day(self, day):
        return day.weekday() in {{0, 1, 2, 3, 6}}
    def next_trading_session(self, day):
        candidate = day + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

def forbidden_loader(**kwargs):
    raise AssertionError("render triggered generation")

service = FrozenHistoricalWatchlistService(
    database_path={database!r},
    calendar=Calendar(),
    history_loader=forbidden_loader,
)
_historical_watchlist_panel(service)
"""
    )
    app.run(timeout=30)

    assert not app.exception
    assert service_db_headers_at(tmp_path / "empty-watchlists.db") == 0
    info = " ".join(str(element.value) for element in app.info)
    assert "No frozen historical watchlist" in info


def test_dashboard_reads_frozen_history_without_legacy_scan_or_generation():
    from dashboard.scalping import show_scalping_dashboard

    source = inspect.getsource(show_scalping_dashboard)

    assert "service.get_for_session(target)" in source
    assert source.index("تجهيز قائمة السكالبنج") < source.index(
        "range_tab, uptrend_tab, live_tab"
    )
    assert "_run_scan" not in source
    assert "rebuild_for_research" not in source

    panel_source = inspect.getsource(
        __import__(
            "dashboard.scalping", fromlist=["_historical_watchlist_panel"]
        )._historical_watchlist_panel
    )
    assert "prepare_for_session" not in panel_source
    assert "_run_scan" not in panel_source
    assert "RangeScanner" not in panel_source


def service_db_headers(tmp_path):
    path = tmp_path / "watchlists.db"
    if not path.exists():
        return 0
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM watchlist_headers"
        ).fetchone()[0]


def service_db_headers_at(path):
    if not path.exists():
        return 0
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM watchlist_headers"
        ).fetchone()[0]
