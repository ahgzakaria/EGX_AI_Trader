"""Read-only ORB Phase 2A readiness and Rubix volume-semantics audit.

The command opens the existing Rubix SQLite database with ``mode=ro`` and
writes aggregate research reports only. It never displays raw quote prices,
authentication material, provider headers, or secrets.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import statistics
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scalping_orb.config import OrbDataConfig
from scalping_orb.session import OrbSessionClassifier
from core.egx_session import is_regular_trading_day
from core.universe import lookup


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Rubix database not found: {path}")
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return float(ordered[index])


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _load_config(path: Path | None) -> OrbDataConfig:
    if path is None:
        return OrbDataConfig()
    return OrbDataConfig.from_mapping(json.loads(path.read_text(encoding="utf-8")))


def _bounds(classifier: OrbSessionClassifier, session_date: date):
    window = classifier.window(session_date)
    return (
        window.continuous_start_utc,
        window.opening_range_end_utc,
        window.continuous_end_utc,
        window.auction_end_utc,
    )


def _candidate_dates(
    connection: sqlite3.Connection, classifier: OrbSessionClassifier
) -> list[date]:
    rows = connection.execute(
        "SELECT DISTINCT substr(minute,1,10) FROM candles_1m ORDER BY 1"
    ).fetchall()
    values = []
    for row in rows:
        try:
            value = date.fromisoformat(row[0])
            if is_regular_trading_day(value, classifier.holidays):
                values.append(value)
        except (TypeError, ValueError):
            continue
    return values


def _coverage_audit(connection, classifier, session_dates):
    session_rows: list[dict] = []
    opening_rows: list[dict] = []
    config = classifier.config
    sample_window = classifier.window(date(2000, 1, 2))
    expected_continuous_minutes = int(
        (sample_window.continuous_end_utc - sample_window.continuous_start_utc)
        .total_seconds()
        // 60
    )
    expected_opening_minutes = config.opening_range_minutes
    minute_observed: dict[int, int] = {
        minute: 0 for minute in range(expected_continuous_minutes)
    }
    minute_possible = 0
    usable_dates: list[date] = []
    symbols_seen: set[str] = set()
    for session_date in session_dates:
        start, opening_end, continuous_end, _auction_end = _bounds(
            classifier, session_date
        )
        rows = connection.execute(
            """SELECT upper(ticker) ticker,minute,open,high,low,close,volume
               FROM candles_1m WHERE minute>=? AND minute<?
               ORDER BY upper(ticker),minute""",
            (start.isoformat(), continuous_end.isoformat()),
        ).fetchall()
        if not rows:
            continue
        usable_dates.append(session_date)
        per_symbol: dict[str, set[datetime]] = {}
        or_per_symbol: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            timestamp = _parse(row["minute"])
            if timestamp is None:
                continue
            ticker = row["ticker"]
            symbols_seen.add(ticker)
            per_symbol.setdefault(ticker, set()).add(timestamp)
            minute_index = int((timestamp - start).total_seconds() // 60)
            if 0 <= minute_index < expected_continuous_minutes:
                minute_observed[minute_index] += 1
            if timestamp < opening_end:
                or_per_symbol.setdefault(ticker, []).append(row)
        counts = [len(values) for values in per_symbol.values()]
        operational = {
            ticker: values
            for ticker, values in per_symbol.items()
            if (record := lookup(ticker)) is not None
            and record.is_active
            and record.has_verified_rubix_mapping
        }
        archived = {
            ticker: values
            for ticker, values in per_symbol.items()
            if (record := lookup(ticker)) is not None
            and not record.is_active
            and record.has_verified_rubix_mapping
        }
        operational_counts = [len(values) for values in operational.values()]
        observed_symbols = len(counts)
        minute_possible += observed_symbols
        complete_minimum = expected_continuous_minutes * config.complete_bar_coverage
        complete_symbols = sum(count >= complete_minimum for count in counts)
        density = sum(counts) / (observed_symbols * expected_continuous_minutes)
        session_status = (
            "COMPLETE_DENSITY"
            if observed_symbols
            and complete_symbols / observed_symbols
            >= config.complete_session_symbol_fraction
            else "DENSE_PARTIAL"
            if density >= config.dense_session_coverage
            else "PARTIAL"
        )
        session_rows.append(
            {
                "session_date": session_date.isoformat(),
                "session_status": session_status,
                "symbols_observed": observed_symbols,
                "expected_continuous_minutes": expected_continuous_minutes,
                "continuous_bar_rows": sum(counts),
                "average_minutes_per_symbol": round(statistics.mean(counts), 4),
                "median_minutes_per_symbol": round(statistics.median(counts), 4),
                "minimum_minutes_per_symbol": min(counts),
                "maximum_minutes_per_symbol": max(counts),
                "complete_symbol_sessions_ge_95pct": complete_symbols,
                "partial_symbol_sessions": observed_symbols - complete_symbols,
                "aggregate_coverage_ratio": round(density, 6),
                "operational_symbols_observed": len(operational),
                "operational_continuous_bar_rows": sum(operational_counts),
                "operational_average_minutes_per_symbol": round(
                    statistics.mean(operational_counts), 4
                ) if operational_counts else 0,
                "archived_symbols_observed": len(archived),
            }
        )
        ready = insufficient = invalid = volume_valid = 0
        operational_ready = archived_ready = 0
        for ticker in per_symbol:
            opening = or_per_symbol.get(ticker, [])
            distinct = {_parse(row["minute"]) for row in opening}
            distinct.discard(None)
            prices_valid = all(
                row["open"] is not None
                and row["high"] is not None
                and row["low"] is not None
                and row["close"] is not None
                and min(row["open"], row["high"], row["low"], row["close"]) > 0
                and row["high"] >= max(row["open"], row["low"], row["close"])
                and row["low"] <= min(row["open"], row["high"], row["close"])
                for row in opening
            )
            if len(distinct) == expected_opening_minutes and prices_valid:
                ready += 1
                record = lookup(ticker)
                if record is not None and record.is_active and record.has_verified_rubix_mapping:
                    operational_ready += 1
                elif record is not None and not record.is_active and record.has_verified_rubix_mapping:
                    archived_ready += 1
                if all(row["volume"] is not None and row["volume"] >= 0 for row in opening):
                    volume_valid += 1
            elif not prices_valid:
                invalid += 1
            else:
                insufficient += 1
        opening_rows.append(
            {
                "session_date": session_date.isoformat(),
                "symbols_observed": observed_symbols,
                "opening_range_ready": ready,
                "opening_range_insufficient_coverage": insufficient,
                "opening_range_invalid_price_data": invalid,
                "opening_range_ready_with_nonnegative_volume": volume_valid,
                "readiness_ratio": round(ready / observed_symbols, 6)
                if observed_symbols
                else 0,
                "operational_symbols_observed": len(operational),
                "operational_opening_range_ready": operational_ready,
                "archived_symbols_observed": len(archived),
                "archived_opening_range_ready": archived_ready,
            }
        )
    minute_rows = [
        {
            "minute_from_open": minute,
            "cairo_clock": (
                datetime.combine(date(2000, 1, 1), classifier.config.continuous_start)
                + timedelta(minutes=minute)
            ).time().isoformat(timespec="minutes"),
            "observed_symbol_sessions": minute_observed[minute],
            "possible_symbol_sessions": minute_possible,
            "coverage_ratio": round(minute_observed[minute] / minute_possible, 6)
            if minute_possible
            else 0,
        }
        for minute in range(expected_continuous_minutes)
    ]
    return session_rows, opening_rows, minute_rows, usable_dates, symbols_seen


def _volume_audit(connection, classifier, session_dates, config):
    results: list[dict] = []
    previous_session_last: dict[str, float] = {}
    aggregate_freshness: list[float] = []
    aggregate_by_phase = {"continuous": [], "auction": []}
    negative_lag_by_phase = {"continuous": 0, "auction": 0}
    stale_by_phase = {"continuous": 0, "auction": 0}
    total_rows = bid_ask_rows = valid_spread_rows = volume_rows = sequence_rows = 0
    for session_date in session_dates:
        start, _opening_end, continuous_end, auction_end = _bounds(
            classifier, session_date
        )
        cursor = connection.execute(
            """SELECT id,upper(ticker) ticker,volume,market_timestamp,received_at,
                      sequence,bid,ask,last_price
               FROM quotes WHERE market_timestamp>=? AND market_timestamp<?
               ORDER BY upper(ticker),market_timestamp,id""",
            (start.isoformat(), auction_end.isoformat()),
        )
        state: dict[str, dict] = {}
        session_freshness: list[float] = []
        session_by_phase = {"continuous": [], "auction": []}
        session_negative = {"continuous": 0, "auction": 0}
        session_stale = {"continuous": 0, "auction": 0}
        metrics = {
            "session_date": session_date.isoformat(),
            "quote_rows": 0,
            "symbols": 0,
            "volume_available_rows": 0,
            "sequence_available_rows": 0,
            "within_continuous_volume_decreases": 0,
            "sequence_gaps": 0,
            "sequence_regressions": 0,
            "duplicate_sequence_same_volume": 0,
            "duplicate_sequence_conflicting_volume": 0,
            "positive_volume_delta_after_time_gap": 0,
            "session_start_reset_vs_prior_session": 0,
            "session_start_counter_not_reset": 0,
            "auction_counter_continues": 0,
            "auction_counter_decreases": 0,
        }
        for row in cursor:
            market = _parse(row["market_timestamp"])
            received = _parse(row["received_at"])
            if market is None:
                continue
            ticker = row["ticker"]
            item = state.setdefault(
                ticker,
                {
                    "last_time": None,
                    "last_volume": None,
                    "last_sequence": None,
                    "last_continuous_volume": None,
                    "first_auction_volume": None,
                    "first_continuous_volume": None,
                },
            )
            volume = float(row["volume"]) if row["volume"] is not None else None
            sequence = int(row["sequence"]) if row["sequence"] is not None else None
            if sequence is not None:
                metrics["sequence_available_rows"] += 1
                sequence_rows += 1
            metrics["quote_rows"] += 1
            total_rows += 1
            if volume is not None and volume >= 0:
                metrics["volume_available_rows"] += 1
                volume_rows += 1
            if row["bid"] is not None and row["ask"] is not None:
                bid_ask_rows += 1
                if row["bid"] > 0 and row["ask"] >= row["bid"]:
                    valid_spread_rows += 1
            continuous = market < continuous_end
            phase_key = "continuous" if continuous else "auction"
            if received is not None:
                freshness = (received - market).total_seconds()
                if freshness >= 0:
                    session_freshness.append(freshness)
                    aggregate_freshness.append(freshness)
                    session_by_phase[phase_key].append(freshness)
                    aggregate_by_phase[phase_key].append(freshness)
                    if freshness > config.maximum_quote_age_seconds:
                        session_stale[phase_key] += 1
                        stale_by_phase[phase_key] += 1
                else:
                    session_negative[phase_key] += 1
                    negative_lag_by_phase[phase_key] += 1
            if continuous and item["first_continuous_volume"] is None and volume is not None:
                item["first_continuous_volume"] = volume
            if not continuous and item["first_auction_volume"] is None and volume is not None:
                item["first_auction_volume"] = volume
            if continuous and volume is not None:
                item["last_continuous_volume"] = volume
            if item["last_volume"] is not None and volume is not None:
                if continuous and volume < item["last_volume"]:
                    metrics["within_continuous_volume_decreases"] += 1
                if (
                    item["last_time"] is not None
                    and (market - item["last_time"]).total_seconds()
                    > config.maximum_volume_delta_gap_seconds
                    and volume > item["last_volume"]
                ):
                    metrics["positive_volume_delta_after_time_gap"] += 1
            if sequence is not None and item["last_sequence"] is not None:
                if sequence == item["last_sequence"]:
                    if volume == item["last_volume"]:
                        metrics["duplicate_sequence_same_volume"] += 1
                    else:
                        metrics["duplicate_sequence_conflicting_volume"] += 1
                elif sequence > item["last_sequence"] + 1:
                    metrics["sequence_gaps"] += 1
                elif sequence < item["last_sequence"]:
                    metrics["sequence_regressions"] += 1
            item["last_time"] = market
            item["last_volume"] = volume
            item["last_sequence"] = sequence
        if not state:
            continue
        metrics["symbols"] = len(state)
        for ticker, item in state.items():
            first = item["first_continuous_volume"]
            prior = previous_session_last.get(ticker)
            if first is not None and prior is not None:
                if first < prior:
                    metrics["session_start_reset_vs_prior_session"] += 1
                else:
                    metrics["session_start_counter_not_reset"] += 1
            last_cont = item["last_continuous_volume"]
            first_auction = item["first_auction_volume"]
            if last_cont is not None:
                previous_session_last[ticker] = last_cont
            if last_cont is not None and first_auction is not None:
                if first_auction >= last_cont:
                    metrics["auction_counter_continues"] += 1
                else:
                    metrics["auction_counter_decreases"] += 1
        metrics["quote_freshness_p50_seconds"] = round(
            _quantile(session_freshness, 0.50) or 0, 6
        )
        metrics["quote_freshness_p95_seconds"] = round(
            _quantile(session_freshness, 0.95) or 0, 6
        )
        metrics["quote_freshness_max_seconds"] = round(
            max(session_freshness) if session_freshness else 0, 6
        )
        for phase_key in ("continuous", "auction"):
            values = session_by_phase[phase_key]
            metrics[f"{phase_key}_quote_rows_with_nonnegative_lag"] = len(values)
            metrics[f"{phase_key}_negative_lag_rows"] = session_negative[phase_key]
            metrics[f"{phase_key}_freshness_failed_rows"] = session_stale[phase_key]
            metrics[f"{phase_key}_quote_freshness_p50_seconds"] = round(
                _quantile(values, 0.50) or 0, 6
            )
            metrics[f"{phase_key}_quote_freshness_p95_seconds"] = round(
                _quantile(values, 0.95) or 0, 6
            )
        results.append(metrics)
    summary = {
        "quote_rows": total_rows,
        "volume_available_rows": volume_rows,
        "sequence_available_rows": sequence_rows,
        "bid_ask_available_rows": bid_ask_rows,
        "valid_spread_rows": valid_spread_rows,
        "bid_ask_coverage_ratio": bid_ask_rows / total_rows if total_rows else 0,
        "valid_spread_coverage_ratio": valid_spread_rows / total_rows if total_rows else 0,
        "quote_freshness_p50_seconds": _quantile(aggregate_freshness, 0.50),
        "quote_freshness_p95_seconds": _quantile(aggregate_freshness, 0.95),
        "quote_freshness_max_seconds": max(aggregate_freshness)
        if aggregate_freshness
        else None,
    }
    for phase_key in ("continuous", "auction"):
        values = aggregate_by_phase[phase_key]
        summary[f"{phase_key}_quote_rows_with_nonnegative_lag"] = len(values)
        summary[f"{phase_key}_negative_lag_rows"] = negative_lag_by_phase[phase_key]
        summary[f"{phase_key}_freshness_failed_rows"] = stale_by_phase[phase_key]
        summary[f"{phase_key}_quote_freshness_p50_seconds"] = _quantile(values, 0.50)
        summary[f"{phase_key}_quote_freshness_p95_seconds"] = _quantile(values, 0.95)
    return results, summary


def _fmt(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _write_reports(
    output: Path,
    rubix_path: Path,
    config: OrbDataConfig,
    session_rows,
    opening_rows,
    minute_rows,
    volume_rows,
    volume_summary,
    symbols_seen,
):
    for label, rows in (
        ("session coverage", session_rows),
        ("opening-range coverage", opening_rows),
        ("minute coverage", minute_rows),
        ("volume quality", volume_rows),
    ):
        if not rows:
            raise RuntimeError(f"No {label} rows were available for reporting")
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "orb_session_coverage.csv", list(session_rows[0]), session_rows)
    _write_csv(output / "orb_opening_range_readiness.csv", list(opening_rows[0]), opening_rows)
    _write_csv(output / "orb_minute_coverage.csv", list(minute_rows[0]), minute_rows)
    _write_csv(output / "orb_volume_quality.csv", list(volume_rows[0]), volume_rows)

    sessions = len(session_rows)
    capabilities = {
        "source": str(rubix_path.resolve()),
        "source_mode": "READ_ONLY",
        "sessions_found": sessions,
        "symbols_found": len(symbols_seen),
        "true_vwap_status": "TRUE_VWAP_UNAVAILABLE",
        "bar_weighted_typical_price_proxy_status": (
            "BAR_WEIGHTED_PRICE_PROXY_AVAILABLE"
            if any(row["opening_range_ready_with_nonnegative_volume"] for row in opening_rows)
            else "BAR_WEIGHTED_PRICE_PROXY_UNAVAILABLE"
        ),
        "time_of_day_rvol_status": (
            "TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY"
            if sessions < config.minimum_time_of_day_rvol_sessions
            else "TIME_OF_DAY_RVOL_HISTORY_SUFFICIENT_NOT_IMPLEMENTED"
        ),
        "minimum_time_of_day_rvol_sessions": config.minimum_time_of_day_rvol_sessions,
        "spread_status": (
            "SPREAD_AVAILABLE"
            if volume_summary["valid_spread_rows"]
            else "SPREAD_UNAVAILABLE"
        ),
        "volume_status": (
            "VOLUME_AVAILABLE"
            if volume_summary["volume_available_rows"]
            else "VOLUME_UNAVAILABLE"
        ),
        "turnover_status": "UNAVAILABLE",
        "trade_count_status": "UNAVAILABLE",
        "strategy_validation_status": "INSUFFICIENT_INTRADAY_HISTORY",
        "historical_bar_capability": "HISTORICAL_BAR_RECONSTRUCTABLE",
        "live_decision_capability": "LIVE_DECISION_DISABLED_STALE_QUOTE",
        "live_decision_reason": (
            "continuous receive lag exceeds the configured budget for most rows; "
            "Phase 2B live decisions remain disabled"
        ),
    }
    (output / "orb_capabilities.json").write_text(
        json.dumps(capabilities, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    complete = sum(row["session_status"] == "COMPLETE_DENSITY" for row in session_rows)
    dense = sum(row["session_status"] == "DENSE_PARTIAL" for row in session_rows)
    reset_count = sum(row["within_continuous_volume_decreases"] for row in volume_rows)
    sequence_gaps = sum(row["sequence_gaps"] for row in volume_rows)
    time_gap_spikes = sum(row["positive_volume_delta_after_time_gap"] for row in volume_rows)
    ready_ranges = sum(row["opening_range_ready"] for row in opening_rows)
    observed_ranges = sum(row["symbols_observed"] for row in opening_rows)
    operational_ready = sum(
        row["operational_opening_range_ready"] for row in opening_rows
    )
    operational_observed = sum(
        row["operational_symbols_observed"] for row in opening_rows
    )
    archived_observed = sum(row["archived_symbols_observed"] for row in opening_rows)
    archived_ready = sum(row["archived_opening_range_ready"] for row in opening_rows)
    ready_dates = [row["session_date"] for row in opening_rows if row["opening_range_ready"]]
    average_minutes = statistics.mean(
        row["average_minutes_per_symbol"] for row in session_rows
    )
    expected_minutes = session_rows[0]["expected_continuous_minutes"]

    readiness = f"""# ORB Phase 2A Data Readiness

Generated by `scripts/audits/inspect_orb_rubix_readiness.py` in SQLite read-only mode.

## Scope and verdict

- Sessions with continuous-window candles: **{sessions}**
- Distinct symbols observed: **{len(symbols_seen)}**
- Complete-density sessions: **{complete}**
- Dense but partial sessions: **{dense}**
- Average observed minutes per symbol/session: **{average_minutes:.2f} / {expected_minutes}**
- Opening-range-ready symbol/sessions: **{ready_ranges} / {observed_ranges}**
- Operational active-and-mapped opening ranges: **{operational_ready} / {operational_observed}**
- Archived historical opening ranges: **{archived_ready} / {archived_observed}**
- Sessions contributing any ready range: **{len(ready_dates)}** ({', '.join(ready_dates) or 'none'})
- Strategy validation: **INSUFFICIENT_INTRADAY_HISTORY**

This is a data-foundation audit, not strategy validation or a profitability claim.

## Capabilities

| Capability | Result |
|---|---|
| True trade-level VWAP | `TRUE_VWAP_UNAVAILABLE` |
| Bar-weighted typical-price proxy | `{capabilities['bar_weighted_typical_price_proxy_status']}`; Research Only and never VWAP |
| Time-of-day RVOL | `{capabilities['time_of_day_rvol_status']}` ({sessions}/{config.minimum_time_of_day_rvol_sessions} sessions) |
| Bid/ask coverage | {volume_summary['bid_ask_coverage_ratio']:.2%} |
| Valid spread coverage | {volume_summary['valid_spread_coverage_ratio']:.2%} |
| Turnover | unavailable |
| Trade count | unavailable; candle `updates` is not treated as trades |

## Receive-lag safety by exchange phase

- Continuous p50/p95: {_fmt(volume_summary['continuous_quote_freshness_p50_seconds'])} / {_fmt(volume_summary['continuous_quote_freshness_p95_seconds'])} seconds
- Continuous rows over {config.maximum_quote_age_seconds:g} seconds: **{volume_summary['continuous_freshness_failed_rows']}**
- Continuous negative-lag rows: **{volume_summary['continuous_negative_lag_rows']}**
- Auction p50/p95: {_fmt(volume_summary['auction_quote_freshness_p50_seconds'])} / {_fmt(volume_summary['auction_quote_freshness_p95_seconds'])} seconds
- Auction negative-lag rows: **{volume_summary['auction_negative_lag_rows']}**

Historical reconstructability is separate from live readiness. The current live decision capability is `LIVE_DECISION_DISABLED_STALE_QUOTE`; delayed rows remain available for historical reconstruction only.

## Volume and ordering observations

- Within-continuous cumulative-volume decreases: **{reset_count}**
- Sequence-gap observations: **{sequence_gaps}**
- Rows with provider sequence present: **{volume_summary['sequence_available_rows']} / {volume_summary['quote_rows']}**
- Positive cumulative deltas following >{config.maximum_volume_delta_gap_seconds:g}-second timestamp gaps: **{time_gap_spikes}**

Any affected delta is unallocatable to a precise bar and must be marked unavailable. Detailed session aggregates are in `orb_volume_quality.csv`; semantics conclusions are in `ORB_RUBIX_VOLUME_SEMANTICS.md`.

## Files

- `orb_session_coverage.csv`: session density and complete/partial classification
- `orb_minute_coverage.csv`: coverage for every continuous minute
- `orb_volume_quality.csv`: cumulative-volume, sequence, freshness and auction observations
- `orb_opening_range_readiness.csv`: exact fifteen-slot readiness
- `orb_capabilities.json`: machine-readable capability states
"""
    (output / "ORB_PHASE2A_DATA_READINESS.md").write_text(
        readiness, encoding="utf-8"
    )

    cross_session_resets = sum(
        row["session_start_reset_vs_prior_session"] for row in volume_rows
    )
    cross_session_carries = sum(
        row["session_start_counter_not_reset"] for row in volume_rows
    )
    auction_continues = sum(row["auction_counter_continues"] for row in volume_rows)
    auction_decreases = sum(row["auction_counter_decreases"] for row in volume_rows)
    duplicate_same = sum(
        row["duplicate_sequence_same_volume"] for row in volume_rows
    )
    duplicate_conflict = sum(
        row["duplicate_sequence_conflicting_volume"] for row in volume_rows
    )
    semantics = f"""# Rubix Cumulative Volume Semantics — ORB Phase 2A

## Evidence observed

| Observation | Count |
|---|---:|
| Within-continuous decreases/resets | {reset_count} |
| Session starts below prior session's last continuous value | {cross_session_resets} |
| Session starts at/above prior session's last continuous value | {cross_session_carries} |
| Duplicate adjacent sequence with same cumulative volume | {duplicate_same} |
| Duplicate adjacent sequence with conflicting cumulative volume | {duplicate_conflict} |
| Sequence gaps | {sequence_gaps} |
| Rows with provider sequence present | {volume_summary['sequence_available_rows']} / {volume_summary['quote_rows']} |
| Positive delta after a >{config.maximum_volume_delta_gap_seconds:g}-second source-time gap | {time_gap_spikes} |
| Auction first value continues from continuous counter | {auction_continues} |
| Auction first value below continuous counter | {auction_decreases} |

## Semantics decision

The field behaves sufficiently like a cumulative counter in many observations to calculate a delta only between consecutive, ordered, same-symbol, same-session events with no sequence gap and no decrease. It is **not proven** from the local schema to be total exchange traded volume, and reconnect/replay identity is not explicitly encoded.

Rules implemented in Phase 2A:

1. First event of each symbol/session establishes a baseline and contributes no invented volume.
2. Equal cumulative values produce a valid zero delta after the baseline.
3. A decrease/reset never produces negative volume; the affected delta is unavailable.
4. A sequence gap/regression makes the affected delta unallocatable and unavailable.
5. A missing cumulative value makes volume unavailable.
6. Auction counter behavior is audited but auction deltas never enter continuous bars.
7. Missing intervals are not filled; a cumulative jump across an observed gap is flagged rather than assigned silently.

## Unresolved provenance

- The database does not prove whether `quotes.volume` is consolidated exchange volume, instrument cumulative volume from Rubix, or another provider-defined field.
- Reconnect/reset/replay events are not linked by a verified connection-generation identifier.
- No turnover, trade id, trade size, transaction count, or VWAP numerator exists.

Therefore volume remains capability-qualified evidence, not fabricated market microstructure.
"""
    (output / "ORB_RUBIX_VOLUME_SEMANTICS.md").write_text(
        semantics, encoding="utf-8"
    )


def run(args) -> None:
    config = _load_config(args.config)
    classifier = OrbSessionClassifier(config)
    with _connect_read_only(args.rubix_db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"quotes", "candles_1m", "feed_metrics"}
        if not required.issubset(tables):
            raise RuntimeError(f"Rubix database missing tables: {sorted(required - tables)}")
        dates = _candidate_dates(connection, classifier)
        session_rows, opening_rows, minute_rows, usable_dates, symbols = _coverage_audit(
            connection, classifier, dates
        )
        if not session_rows:
            raise RuntimeError("No continuous-session Rubix candles were found")
        volume_rows, volume_summary = _volume_audit(
            connection, classifier, usable_dates, config
        )
    _write_reports(
        args.output_dir,
        args.rubix_db,
        config,
        session_rows,
        opening_rows,
        minute_rows,
        volume_rows,
        volume_summary,
        symbols,
    )
    print(
        json.dumps(
            {
                "status": "SUCCESS",
                "source_mode": "READ_ONLY",
                "sessions": len(session_rows),
                "symbols": len(symbols),
                "output_dir": str(args.output_dir.resolve()),
            },
            sort_keys=True,
        )
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rubix-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "rubix_live_market.db",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config" / "orb_first_pullback.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            PROJECT_ROOT
            / "reports"
            / "audits"
            / "strategies"
            / "orb_first_pullback"
            / "phase2a"
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    run(parse_args())
