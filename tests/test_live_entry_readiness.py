"""Phase 3B live-readiness, no-chase, batch-read and invariance tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
import sqlite3

import pytest

from scalping_expected_range.frozen_watchlist import StoredWatchlist
from scalping_expected_range.live_readiness import (
    BREAKOUT_UNCONFIRMED,
    CLOSING_AUCTION,
    CLOSING_AUCTION_NO_NEW_ENTRY,
    CONTINUOUS_TRADING,
    ENTRY_READY_RESEARCH_ONLY,
    ENTRY_TRIGGER_FORMING,
    HISTORICAL_CANDIDATE_WAITING,
    LIQUIDITY_INSUFFICIENT,
    LIVE_DATA_STALE,
    LIVE_DATA_UNAVAILABLE,
    LIVE_METRIC_VERSION,
    MOVE_EXTENDED_DO_NOT_CHASE,
    OPENING_RANGE_FORMING,
    OPPORTUNITY_INVALIDATED,
    POST_CLOSE,
    PRE_OPEN,
    PRE_OPEN_WAIT,
    PULLBACK_CONFIRMATION_REQUIRED,
    SESSION_CLOSED,
    SPREAD_TOO_WIDE,
    TARGET_OUTSIDE_TYPICAL_ZONE,
    VWAP_AVAILABLE,
    VWAP_UNAVAILABLE,
    WATCHLIST_NOT_READY,
    LiveEntryReadinessEngine,
    LiveReadinessConfig,
    RubixBatchSnapshot,
    RubixLiveBatchReader,
    RubixMinuteBar,
    RubixQuote,
    RubixSymbolSnapshot,
    live_session_phase,
)


UTC = timezone.utc
TARGET = date(2026, 7, 28)
WATCHLIST_ID = "FHW-384cf17b13194e43370bfabc"
CONTINUOUS_TIME = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


def _member(symbol, rank, *, displayed=True, upper=5.0, daily_range=5.0):
    return {
        "watchlist_id": WATCHLIST_ID,
        "symbol": symbol,
        "historical_rank": rank,
        "scoreable_rank": rank,
        "displayed_candidate": displayed,
        "historical_score": 90.0 - rank,
        "primary_historical_score": 90.0 - rank,
        "movement_potential_score": 88.0,
        "range_stability_score": 82.0,
        "upper_zone_consistency_score": 80.0,
        "lower_zone_consistency_score": 75.0,
        "combined_zone_consistency_score": 78.0,
        "zone_confidence_label": "HIGH",
        "liquidity_score": 85.0,
        "median_daily_range": daily_range,
        "normal_range_lower": 3.5,
        "normal_range_upper": 6.0,
        "range_hit_2pct_frequency": 0.8,
        "median_upper_excursion": upper,
        "median_lower_excursion": -2.0,
        "valid_sessions_primary": 60,
        "valid_sessions_recent": 30,
        "primary_readiness_status": "READY",
        "recent_confirmation_status": "CONFIRMED",
        "recent_penalty": 0.0,
        "eligibility_status": "HARD_ELIGIBLE",
        "exclusion_reason": None,
        "source": "EODHD_DAILY",
        "source_fingerprint": "sha256:" + symbol.lower() * 8,
        "latest_session": "2026-07-27",
        "data_cutoff": "2026-07-27",
        "metric_version": "DAILY_HISTORICAL_SELECTION_V2",
        "config_version": "DAILY_HISTORICAL_SELECTION_CONFIG_V2",
        "historical_explanation": "deterministic",
    }


def _record(*, upper=5.0, daily_range=5.0):
    members = (
        _member("RAYA", 1, upper=upper, daily_range=daily_range),
        _member("OCDI", 2, upper=upper, daily_range=daily_range),
        _member(
            "NONCAND",
            3,
            displayed=False,
            upper=upper,
            daily_range=daily_range,
        ),
    )
    return StoredWatchlist(
        {
            "watchlist_id": WATCHLIST_ID,
            "status": "READY",
            "target_session_date": TARGET.isoformat(),
            "historical_data_cutoff": "2026-07-27",
            "eligible_count": 179,
            "displayed_count": 2,
            "top_n": 2,
        },
        members,
    )


def _bars(
    symbol,
    *,
    pattern="waiting",
    count=30,
    volume=10_000.0,
    start=datetime(2026, 7, 28, 7, 0, tzinfo=UTC),
):
    bars = []
    for index in range(count):
        minute = start + timedelta(minutes=index)
        close = 100.0 + (index % 3) * 0.02
        high = close + 0.05
        low = close - 0.05
        if index == 4:
            high = 101.0
            close = 100.6
        if pattern in {"unconfirmed", "confirmed", "ready"} and index == 16:
            close, high, low = 101.20, 101.25, 101.05
        if pattern in {"confirmed", "ready"} and index == 17:
            close, high, low = 101.25, 101.30, 101.21
        if pattern == "ready" and index == 18:
            close, high, low = 101.04, 101.12, 100.98
        if pattern == "ready" and index > 18:
            close, high, low = 101.20, 101.25, 101.05
        bars.append(
            RubixMinuteBar(
                symbol,
                minute,
                close,
                high,
                low,
                close,
                volume,
                3,
            )
        )
    return tuple(bars)


def _quote(
    symbol,
    *,
    price=100.2,
    bid=100.15,
    ask=100.25,
    volume=1_000_000,
    timestamp=CONTINUOUS_TIME - timedelta(seconds=15),
):
    return RubixQuote(
        symbol,
        price,
        bid,
        ask,
        volume,
        0.2,
        timestamp,
        timestamp + timedelta(seconds=1),
    )


def _snapshot(
    *,
    pattern="waiting",
    price=100.2,
    bid=100.15,
    ask=100.25,
    volume=1_000_000,
    timestamp=CONTINUOUS_TIME - timedelta(seconds=15),
    bars_count=30,
    bar_volume=10_000,
    include_quote=True,
):
    by_symbol = {}
    for symbol in ("RAYA", "OCDI"):
        by_symbol[symbol] = RubixSymbolSnapshot(
            symbol,
            (
                _quote(
                    symbol,
                    price=price,
                    bid=bid,
                    ask=ask,
                    volume=volume,
                    timestamp=timestamp,
                )
                if include_quote
                else None
            ),
            _bars(
                symbol,
                pattern=pattern,
                count=bars_count,
                volume=bar_volume,
            ),
        )
    return RubixBatchSnapshot(
        TARGET.isoformat(),
        ("RAYA", "OCDI"),
        by_symbol,
        (CONTINUOUS_TIME - timedelta(seconds=15)).isoformat(),
        1,
        2,
        1.5,
    )


class _Reader:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or _snapshot()
        self.calls = []

    def load(self, symbols, **kwargs):
        self.calls.append((tuple(symbols), kwargs))
        return self.snapshot


def _evaluate(snapshot=None, *, record=None, at=CONTINUOUS_TIME):
    reader = _Reader(snapshot)
    result = LiveEntryReadinessEngine(reader).evaluate(
        record or _record(),
        evaluated_at=at,
        live_snapshot=snapshot,
    )
    return result, reader


def test_watchlist_not_ready_fails_closed_without_rubix_scan():
    reader = _Reader()
    result = LiveEntryReadinessEngine(reader).evaluate(
        None, evaluated_at=CONTINUOUS_TIME
    )

    assert result.status == WATCHLIST_NOT_READY
    assert result.results == ()
    assert reader.calls == []


def test_only_frozen_top_n_candidates_are_evaluated():
    result, _ = _evaluate(_snapshot())

    assert result.symbols_requested == ("RAYA", "OCDI")
    assert [item.symbol for item in result.results] == ["RAYA", "OCDI"]
    assert "NONCAND" not in result.symbols_requested


def test_complete_eligible_universe_is_not_scanned_by_default():
    record = _record()
    assert len(record.members) == 3 and len(record.displayed) == 2

    result, _ = _evaluate(_snapshot(), record=record)

    assert len(result.results) == 2


def test_historical_rank_and_scores_remain_unchanged():
    record = _record()
    before = tuple(dict(member) for member in record.members)

    result, _ = _evaluate(
        _snapshot(pattern="ready", price=101.2),
        record=record,
    )

    assert tuple(record.members) == before
    assert [(item.historical_rank, item.historical_score) for item in result.results] == [
        (1, 89.0),
        (2, 88.0),
    ]


def test_non_candidate_plus_five_percent_remains_excluded():
    snapshot = _snapshot(price=105.0)
    snapshot.by_symbol["NONCAND"] = RubixSymbolSnapshot(
        "NONCAND",
        _quote("NONCAND", price=105.0),
        _bars("NONCAND"),
    )

    result, _ = _evaluate(snapshot)

    assert "NONCAND" not in [item.symbol for item in result.results]


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 7, 28, 6, 59, tzinfo=UTC), PRE_OPEN),
        (
            datetime(2026, 7, 28, 11, 20, tzinfo=UTC),
            CLOSING_AUCTION,
        ),
        (datetime(2026, 7, 28, 11, 25, tzinfo=UTC), POST_CLOSE),
    ],
)
def test_session_phase_boundaries(moment, expected):
    assert live_session_phase(moment, TARGET, holidays=()) == expected


def test_pre_open_wait_skips_database_read():
    reader = _Reader()
    result = LiveEntryReadinessEngine(reader).evaluate(
        _record(),
        evaluated_at=datetime(2026, 7, 28, 6, 30, tzinfo=UTC),
    )

    assert {item.live_state for item in result.results} == {PRE_OPEN_WAIT}
    assert {item.session_phase for item in result.results} == {PRE_OPEN}
    assert reader.calls == []


def test_opening_range_forming_before_fifteen_minutes():
    at = datetime(2026, 7, 28, 7, 10, tzinfo=UTC)
    snapshot = _snapshot(
        timestamp=at - timedelta(seconds=10),
        bars_count=9,
    )

    result, _ = _evaluate(snapshot, at=at)

    assert {item.live_state for item in result.results} == {
        OPENING_RANGE_FORMING
    }
    assert {item.session_phase for item in result.results} == {
        CONTINUOUS_TRADING
    }


def test_entry_trigger_forming_near_opening_range_high():
    result, _ = _evaluate(_snapshot(price=100.9))

    assert {item.live_state for item in result.results} == {
        ENTRY_TRIGGER_FORMING
    }


def test_historical_candidate_waiting_away_from_trigger():
    result, _ = _evaluate(_snapshot(price=100.2))

    assert {item.live_state for item in result.results} == {
        HISTORICAL_CANDIDATE_WAITING
    }


def test_breakout_unconfirmed_requires_multiple_candle_closes():
    result, _ = _evaluate(
        _snapshot(pattern="unconfirmed", price=101.2)
    )

    assert {item.live_state for item in result.results} == {
        BREAKOUT_UNCONFIRMED
    }


def test_confirmed_breakout_requires_pullback_confirmation():
    result, _ = _evaluate(
        _snapshot(pattern="confirmed", price=101.25)
    )

    assert {item.live_state for item in result.results} == {
        PULLBACK_CONFIRMATION_REQUIRED
    }


def test_entry_ready_is_research_only_after_breakout_and_retest():
    result, _ = _evaluate(_snapshot(pattern="ready", price=101.2))

    assert {item.live_state for item in result.results} == {
        ENTRY_READY_RESEARCH_ONLY
    }
    assert all(item.readiness_score >= 70 for item in result.results)
    assert all(item.live_metric_version == LIVE_METRIC_VERSION for item in result.results)


def test_move_extended_do_not_chase_for_plus_five_percent_mover():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=105.0),
        record=_record(upper=3.0, daily_range=4.0),
    )

    assert {item.live_state for item in result.results} == {
        MOVE_EXTENDED_DO_NOT_CHASE
    }
    assert all(item.readiness_score is None for item in result.results)
    assert all(item.no_chase_reason for item in result.results)


def test_projected_target_outside_typical_zone_is_rejected():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=100.5),
        record=_record(upper=1.5, daily_range=5.0),
    )

    assert {item.live_state for item in result.results} == {
        TARGET_OUTSIDE_TYPICAL_ZONE
    }
    assert all(item.historical_zone_feasible is False for item in result.results)


def test_spread_too_wide_blocks_score():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=101.2, bid=100.0, ask=101.0)
    )

    assert {item.live_state for item in result.results} == {SPREAD_TOO_WIDE}
    assert all(item.readiness_score is None for item in result.results)


def test_crossed_bid_ask_is_untrustworthy_and_blocks_readiness():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=101.2, bid=101.4, ask=101.0)
    )

    assert {item.live_state for item in result.results} == {
        LIVE_DATA_UNAVAILABLE
    }
    assert all(item.current_spread_percent is None for item in result.results)


def test_liquidity_insufficient_blocks_score():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=101.2, volume=10_000)
    )

    assert {item.live_state for item in result.results} == {
        LIQUIDITY_INSUFFICIENT
    }


def test_live_data_stale_blocks_score():
    result, _ = _evaluate(
        _snapshot(
            pattern="ready",
            price=101.2,
            timestamp=CONTINUOUS_TIME - timedelta(minutes=5),
        )
    )

    assert {item.live_state for item in result.results} == {LIVE_DATA_STALE}


def test_live_data_unavailable_when_quote_missing():
    result, _ = _evaluate(_snapshot(include_quote=False))

    assert {item.live_state for item in result.results} == {
        LIVE_DATA_UNAVAILABLE
    }


def test_sparse_opening_structure_is_unavailable_not_fabricated():
    snapshot = _snapshot(bars_count=30)
    sparse = {
        symbol: replace(
            item,
            continuous_bars=tuple(
                bar
                for index, bar in enumerate(item.continuous_bars)
                if index >= 10
            ),
        )
        for symbol, item in snapshot.by_symbol.items()
    }
    result, _ = _evaluate(replace(snapshot, by_symbol=sparse))

    assert {item.live_state for item in result.results} == {
        LIVE_DATA_UNAVAILABLE
    }
    assert all(item.readiness_score is None for item in result.results)


def test_opportunity_invalidated_below_opening_range_low():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=99.0)
    )

    assert {item.live_state for item in result.results} == {
        OPPORTUNITY_INVALIDATED
    }


def test_closing_auction_blocks_new_entry_and_keeps_score_none():
    at = datetime(2026, 7, 28, 11, 20, tzinfo=UTC)
    snapshot = _snapshot(
        pattern="ready",
        price=101.2,
        timestamp=datetime(2026, 7, 28, 11, 14, 50, tzinfo=UTC),
    )

    result, _ = _evaluate(snapshot, at=at)

    assert {item.live_state for item in result.results} == {
        CLOSING_AUCTION_NO_NEW_ENTRY
    }
    assert {item.session_phase for item in result.results} == {
        CLOSING_AUCTION
    }
    assert all(item.readiness_score is None for item in result.results)


def test_post_close_blocks_new_entry():
    at = datetime(2026, 7, 28, 11, 30, tzinfo=UTC)
    result, _ = _evaluate(_snapshot(), at=at)

    assert {item.live_state for item in result.results} == {SESSION_CLOSED}
    assert {item.session_phase for item in result.results} == {POST_CLOSE}


def test_vwap_uses_minute_typical_price_and_volume():
    result, _ = _evaluate(_snapshot(pattern="ready", price=101.2))
    item = result.results[0]

    expected = sum(
        ((bar.high + bar.low + bar.close) / 3) * bar.volume
        for bar in _bars("RAYA", pattern="ready")
    ) / sum(bar.volume for bar in _bars("RAYA", pattern="ready"))
    assert item.vwap_state == VWAP_AVAILABLE
    assert item.session_vwap == pytest.approx(expected)


def test_vwap_unavailable_reweights_without_substituting_average():
    result, _ = _evaluate(
        _snapshot(pattern="ready", price=101.2, bar_volume=0)
    )

    assert {item.live_state for item in result.results} == {
        ENTRY_READY_RESEARCH_ONLY
    }
    assert all(item.vwap_state == VWAP_UNAVAILABLE for item in result.results)
    assert all(item.session_vwap is None for item in result.results)


def test_target_stop_and_cost_adjusted_reward_are_explicit():
    result, _ = _evaluate(
        _snapshot(
            pattern="ready",
            price=101.2,
            bid=101.15,
            ask=101.25,
        )
    )
    item = result.results[0]

    assert item.target_price == pytest.approx(103.224)
    assert item.stop_price == pytest.approx(99.176)
    assert item.gross_reward_percent == 2.0
    assert item.gross_risk_percent == 2.0
    assert item.expected_net_reward_percent < item.gross_reward_percent


def test_range_consumption_is_explicitly_labelled_proxy():
    result, _ = _evaluate(_snapshot())
    item = result.results[0]

    assert item.historical_range_consumption is not None
    assert "indicative proxy" in " ".join(item.explanations)
    assert "Exact Remaining" not in " ".join(item.explanations)


def _create_rubix_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (
              id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, last_price REAL,
              bid REAL, ask REAL, volume REAL, market_timestamp TEXT NOT NULL,
              received_at TEXT NOT NULL, exchange TEXT, sequence INTEGER,
              change_percent REAL, has_feed_timestamp INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_quotes_ticker_time
              ON quotes(ticker,market_timestamp);
            CREATE TABLE candles_1m (
              ticker TEXT NOT NULL, minute TEXT NOT NULL, open REAL NOT NULL,
              high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
              volume REAL NOT NULL DEFAULT 0, updates INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY(ticker,minute)
            );
            CREATE TABLE feed_metrics (
              id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
              event TEXT NOT NULL, ticker TEXT, value REAL, detail TEXT
            );
            """
        )
        for symbol, price in (("RAYA", 101.2), ("OCDI", 101.2), ("NONCAND", 105.0)):
            for bar in _bars(symbol, pattern="ready"):
                connection.execute(
                    "INSERT INTO candles_1m VALUES (?,?,?,?,?,?,?,?)",
                    (
                        symbol,
                        bar.minute.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.updates,
                    ),
                )
            stamp = (CONTINUOUS_TIME - timedelta(seconds=15)).isoformat()
            connection.execute(
                """
                INSERT INTO quotes
                (ticker,last_price,bid,ask,volume,market_timestamp,received_at,
                 exchange,sequence,change_percent,has_feed_timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol,
                    price,
                    price - 0.05,
                    price + 0.05,
                    1_000_000,
                    stamp,
                    stamp,
                    "CASE",
                    None,
                    0.2,
                    1,
                ),
            )


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_batch_reader_uses_one_connection_two_queries_and_top_n_only(tmp_path):
    database = tmp_path / "rubix.db"
    _create_rubix_database(database)
    before = _sha256(database)
    reader = RubixLiveBatchReader(database)

    snapshot = reader.load(
        ("RAYA", "OCDI"),
        target_session_date=TARGET,
        evaluated_at=CONTINUOUS_TIME,
    )

    assert snapshot.symbols == ("RAYA", "OCDI")
    assert set(snapshot.by_symbol) == {"RAYA", "OCDI"}
    assert snapshot.connection_count == 1
    assert snapshot.query_count == 2
    assert _sha256(database) == before


def test_batch_reader_excludes_auction_candles_and_freezes_at_1415(tmp_path):
    database = tmp_path / "rubix.db"
    _create_rubix_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO candles_1m VALUES (?,?,?,?,?,?,?,?)",
            (
                "RAYA",
                "2026-07-28T11:17:00+00:00",
                101.0,
                110.0,
                90.0,
                109.0,
                1_000_000,
                10,
            ),
        )
    reader = RubixLiveBatchReader(database)

    snapshot = reader.load(
        ("RAYA",),
        target_session_date=TARGET,
        evaluated_at=datetime(2026, 7, 28, 11, 20, tzinfo=UTC),
    )

    bars = snapshot.by_symbol["RAYA"].continuous_bars
    assert max(bar.minute for bar in bars) < datetime(
        2026, 7, 28, 11, 15, tzinfo=UTC
    )
    assert max(bar.high for bar in bars) < 110


def test_batch_reader_uses_direct_ticker_predicates():
    source = inspect.getsource(RubixLiveBatchReader.load)

    assert "ticker IN" in source
    assert "UPPER(ticker)" not in source
    assert "mode=ro" in inspect.getsource(RubixLiveBatchReader._connect)


def test_second_observer_sees_same_inputs_and_results(tmp_path):
    database = tmp_path / "rubix.db"
    _create_rubix_database(database)
    first = LiveEntryReadinessEngine(RubixLiveBatchReader(database)).evaluate(
        _record(), evaluated_at=CONTINUOUS_TIME
    )
    second = LiveEntryReadinessEngine(RubixLiveBatchReader(database)).evaluate(
        _record(), evaluated_at=CONTINUOUS_TIME
    )

    assert first.watchlist_id == second.watchlist_id == WATCHLIST_ID
    assert first.rubix_data_cutoff == second.rubix_data_cutoff
    assert first.results == second.results


def test_changing_one_quote_changes_only_that_candidates_readiness():
    first, _ = _evaluate(_snapshot(pattern="ready", price=101.2))
    changed_snapshot = _snapshot(pattern="ready", price=101.2)
    changed = dict(changed_snapshot.by_symbol)
    changed["RAYA"] = replace(
        changed["RAYA"],
        quote=_quote("RAYA", price=105.0),
    )
    changed_snapshot = replace(changed_snapshot, by_symbol=changed)
    second, _ = _evaluate(changed_snapshot)

    assert first.results[1] == second.results[1]
    assert first.results[0] != second.results[0]
    assert second.results[0].live_state == MOVE_EXTENDED_DO_NOT_CHASE


def test_refresh_retains_watchlist_id_and_never_rebuilds_history():
    reader = _Reader(_snapshot())
    engine = LiveEntryReadinessEngine(reader)
    record = _record()

    first = engine.evaluate(record, evaluated_at=CONTINUOUS_TIME)
    second = engine.evaluate(record, evaluated_at=CONTINUOUS_TIME)

    assert first.watchlist_id == second.watchlist_id == WATCHLIST_ID
    assert len(reader.calls) == 2


def test_engine_has_no_eodhd_yahoo_broker_or_mutation_dependency():
    source = inspect.getsource(
        __import__(
            "scalping_expected_range.live_readiness",
            fromlist=["placeholder"],
        )
    ).lower()

    assert "load_eodhd" not in source
    assert "yahoo_provider" not in source
    assert "broker" not in source
    assert "paper_trade" not in source
    assert "insert into watchlist" not in source
    assert "update watchlist" not in source


def test_production_remains_disabled_and_no_order_fields_exist():
    result, _ = _evaluate(_snapshot(pattern="ready", price=101.2))
    fields = set(result.results[0].__dataclass_fields__)

    assert "order" not in fields
    assert "quantity" not in fields
    assert "portfolio" not in fields
    assert all(item.live_state.endswith("RESEARCH_ONLY") for item in result.results)


def test_config_rejects_non_one_hundred_percent_weights():
    with pytest.raises(ValueError, match="total 100"):
        LiveReadinessConfig(structure_weight=34.0)


def test_live_monitor_frame_keeps_historical_and_live_fields_separate():
    from dashboard.scalping import _live_readiness_frame

    result, _ = _evaluate(_snapshot(pattern="ready", price=101.2))
    frame = _live_readiness_frame(result)

    assert list(frame["Historical Rank (Frozen)"]) == [1, 2]
    assert {
        "Live Readiness",
        "Live State",
        "Historical Range Consumption",
        "Opening Range",
        "VWAP State",
        "No-Chase Warning",
    } <= set(frame.columns)
    assert "Historical Score" not in frame.columns


def test_live_fragment_cannot_rebuild_or_write_historical_watchlist():
    from dashboard.scalping import _live_entry_monitor_fragment

    source = inspect.getsource(_live_entry_monitor_fragment.__wrapped__).lower()

    assert "prepare_for_session" not in source
    assert "rebuild_for_research" not in source
    assert "eodhd" not in source
    assert "yahoo" not in source
    assert "insert" not in source
    assert "update watchlist" not in source


def test_live_monitor_follows_frozen_history_without_legacy_scan():
    from dashboard.scalping import show_scalping_dashboard

    source = inspect.getsource(show_scalping_dashboard)
    historical = source.index("service.get_for_session(target)")
    tabs = source.index("range_tab, uptrend_tab, live_tab")
    live = source.index("تحديث المتابعة اللحظية")

    assert historical < tabs < live
    assert "_run_scan" not in source
    assert "_live_entry_monitor_panel" not in source


def test_watchlist_absence_panel_does_not_call_live_engine():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        """
from dashboard.scalping import _live_entry_monitor_panel
from scalping_expected_range.frozen_watchlist import (
    WATCHLIST_NOT_GENERATED,
    WatchlistServiceResult,
)

class ForbiddenEngine:
    def evaluate(self, *args, **kwargs):
        raise AssertionError("live engine called without READY watchlist")

_live_entry_monitor_panel(
    WatchlistServiceResult(WATCHLIST_NOT_GENERATED),
    engine=ForbiddenEngine(),
)
"""
    )
    app.run(timeout=30)

    assert not app.exception
    warnings = " ".join(str(element.value) for element in app.warning)
    assert "WATCHLIST_NOT_READY" in warnings
    assert "will not scan the full market" in warnings
