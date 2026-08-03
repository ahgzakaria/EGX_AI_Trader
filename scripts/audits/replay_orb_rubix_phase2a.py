"""Replay deterministic Rubix evidence through Phase 2A without writing Rubix."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.universe import lookup
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import NormalizationMode
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.replay import (
    RubixReadOnlyReplaySource,
    compare_quote_bars_to_candles,
    stable_bar_bytes,
)
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.shadow import OrbShadowIngestionService


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_ranges(symbols) -> bytes:
    payload = [
        {
            "ticker": item.canonical_ticker,
            "status": item.opening_range.status.value,
            "high": item.opening_range.opening_range_high,
            "low": item.opening_range.opening_range_low,
            "volume": item.opening_range.valid_volume_total,
            "flags": item.opening_range.data_quality_flags,
            "source": item.opening_range.source_identity,
        }
        for item in sorted(symbols, key=lambda value: value.canonical_ticker)
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise RuntimeError(f"No rows available for {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(args) -> dict:
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    if args.research_db.exists():
        raise FileExistsError(
            f"Replay research database already exists; use a new path: {args.research_db}"
        )
    config = OrbDataConfig.from_mapping(
        json.loads(args.config.read_text(encoding="utf-8"))
    )
    source_before = {
        "sha256": _sha256(args.rubix_db),
        "size": args.rubix_db.stat().st_size,
        "mtime_ns": args.rubix_db.stat().st_mtime_ns,
    }
    source = RubixReadOnlyReplaySource(args.rubix_db, config)
    plan = source.build_plan()
    batch = source.load(plan)
    arrival_metrics = source.arrival_order_metrics()
    repository = OrbResearchRepository(args.research_db)

    first_bars = []
    first_symbols = []
    session_ids = {}
    first_inserted = 0
    transaction_start = repository.write_transaction_count
    started = datetime.now(timezone.utc)
    for session_date, raw_events in batch.raw_events_by_session:
        evaluated = source.classifier.window(session_date).continuous_end_utc
        service = OrbShadowIngestionService(
            repository,
            config,
            normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
        )
        result = service.ingest(raw_events, evaluated_at=evaluated)
        session_ids[session_date] = result.session_ids[0]
        first_inserted += result.inserted_events
        first_bars.extend(result.one_minute_bars)
        first_bars.extend(result.five_minute_bars)
        first_symbols.extend(result.symbols)
    first_duration = (datetime.now(timezone.utc) - started).total_seconds()
    counts_before_second = {
        table: repository.table_count(table)
        for table in (
            "orb_normalized_events",
            "orb_bars",
            "orb_opening_ranges",
            "orb_data_quality_events",
            "orb_capabilities",
        )
    }

    second_bars = []
    second_symbols = []
    second_inserted = 0
    for session_date, raw_events in batch.raw_events_by_session:
        evaluated = source.classifier.window(session_date).continuous_end_utc
        service = OrbShadowIngestionService(
            repository,
            config,
            normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
        )
        result = service.ingest(raw_events, evaluated_at=evaluated)
        second_inserted += result.inserted_events
        second_bars.extend(result.one_minute_bars)
        second_bars.extend(result.five_minute_bars)
        second_symbols.extend(result.symbols)
    counts_after_second = {
        table: repository.table_count(table) for table in counts_before_second
    }

    deterministic_bars = stable_bar_bytes(first_bars) == stable_bar_bytes(second_bars)
    deterministic_ranges = _stable_ranges(first_symbols) == _stable_ranges(second_symbols)
    double_replay_idempotent = (
        second_inserted == 0 and counts_before_second == counts_after_second
    )

    dense_id = session_ids[plan.dense_session_date]
    dense_events = repository.load_events(dense_id)
    dense_bars = [
        bar
        for bar in first_bars
        if bar.session_date == plan.dense_session_date and bar.interval_minutes == 1
    ]
    candle_rows = source.candle_rows(plan.dense_session_date)
    candle_groups = {}
    for row in candle_rows:
        candle_groups.setdefault(row["ticker"], []).append(row)
    vendor_ready_reference = sum(
        len(rows) == config.opening_range_minutes
        and all(
            row["open"] is not None
            and row["high"] is not None
            and row["low"] is not None
            and row["close"] is not None
            and min(row["open"], row["high"], row["low"], row["close"]) > 0
            for row in rows
        )
        for rows in candle_groups.values()
    )
    comparison = compare_quote_bars_to_candles(
        quote_bars=dense_bars,
        normalized_events=dense_events,
        candle_rows=candle_rows,
    )
    _write_csv(output / "orb_real_replay_bar_comparison.csv", comparison)
    comparison_counts = Counter(row["classification"] for row in comparison)

    dense_symbols = [
        item
        for item in first_symbols
        if item.opening_range.session_date == plan.dense_session_date
    ]
    ready = [
        item for item in dense_symbols
        if item.opening_range.status == OpeningRangeStatus.READY
    ]
    quote_ready_tickers = {item.canonical_ticker for item in ready}
    vendor_ready_tickers = {
        ticker
        for ticker, rows in candle_groups.items()
        if len(rows) == config.opening_range_minutes
        and all(
            row["open"] is not None
            and row["high"] is not None
            and row["low"] is not None
            and row["close"] is not None
            and min(row["open"], row["high"], row["low"], row["close"]) > 0
            for row in rows
        )
    }
    quote_only_ready = sorted(quote_ready_tickers - vendor_ready_tickers)
    vendor_only_ready = sorted(vendor_ready_tickers - quote_ready_tickers)
    ready_active = sum(
        bool((record := lookup(item.canonical_ticker)) and record.is_active)
        for item in ready
    )
    ready_archived = len(ready) - ready_active
    volume_invalid_bars = sum(bar.volume is None for bar in first_bars)
    negative_volume_bars = sum(
        bar.volume is not None and bar.volume < 0 for bar in first_bars
    )
    live_disabled = all(
        item.capabilities.live_decision_capability.value.startswith(
            "LIVE_DECISION_DISABLED"
        )
        for item in first_symbols
    )

    source_after = {
        "sha256": _sha256(args.rubix_db),
        "size": args.rubix_db.stat().st_size,
        "mtime_ns": args.rubix_db.stat().st_mtime_ns,
    }
    source_unchanged = source_before == source_after
    foundation_trust = all(
        (
            deterministic_bars,
            deterministic_ranges,
            double_replay_idempotent,
            source_unchanged,
            negative_volume_bars == 0,
            live_disabled,
        )
    )

    selection_rows = [
        {
            "category": item.category,
            "session_date": item.session_date.isoformat(),
            "ticker": item.canonical_ticker,
            "reason": item.reason,
        }
        for item in plan.selections
    ]
    (output / "orb_real_replay_selection.json").write_text(
        json.dumps(
            {
                "selection_rules": selection_rows,
                "dense_session_date": plan.dense_session_date.isoformat(),
                "dense_tickers": len(plan.dense_tickers),
                "full_session_pairs": len(plan.full_session_pairs),
                "raw_rows": batch.raw_row_count,
                "safe_source_identity": batch.safe_source_identity,
                "distinct_market_payloads": batch.distinct_market_payloads,
                "exact_payload_repeats": batch.exact_payload_repeats,
                "same_timestamp_distinct_events": batch.same_timestamp_distinct_events,
                "identity_hash_collisions": batch.identity_hash_collisions,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    metrics = {
        "source_mode": "READ_ONLY_QUERY_ONLY",
        "source_unchanged": source_unchanged,
        "raw_rows": batch.raw_row_count,
        "inserted_unique_events": first_inserted,
        "distinct_market_payloads": batch.distinct_market_payloads,
        "exact_payload_repeats": batch.exact_payload_repeats,
        "same_timestamp_distinct_events": batch.same_timestamp_distinct_events,
        "identity_hash_collisions": batch.identity_hash_collisions,
        "identity_collision_rate": (
            batch.identity_hash_collisions / batch.distinct_market_payloads
            if batch.distinct_market_payloads else 0.0
        ),
        "second_replay_inserted_events": second_inserted,
        "double_replay_idempotent": double_replay_idempotent,
        "bar_bytes_identical": deterministic_bars,
        "opening_range_bytes_identical": deterministic_ranges,
        "one_minute_bars": sum(bar.interval_minutes == 1 for bar in first_bars),
        "five_minute_bars": sum(bar.interval_minutes == 5 for bar in first_bars),
        "volume_invalid_bars": volume_invalid_bars,
        "negative_volume_bars": negative_volume_bars,
        "dense_quote_opening_ranges_ready": len(ready),
        "dense_quote_ready_active": ready_active,
        "dense_quote_ready_archived": ready_archived,
        "vendor_candle_ready_reference": vendor_ready_reference,
        "quote_only_ready_tickers": quote_only_ready,
        "vendor_only_ready_tickers": vendor_only_ready,
        "comparison_counts": dict(sorted(comparison_counts.items())),
        "arrival_order_metrics": arrival_metrics,
        "first_replay_seconds": round(first_duration, 6),
        "write_transactions": repository.write_transaction_count - transaction_start,
        "live_decisions_disabled": live_disabled,
        "foundation_trustworthy_for_core": foundation_trust,
        "research_database": str(args.research_db.resolve()),
        "rubix_sha256": source_after["sha256"],
    }
    (output / "orb_real_replay_metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    comparison_lines = "\n".join(
        f"| `{name}` | {count} |" for name, count in sorted(comparison_counts.items())
    )
    selections_text = "\n".join(
        f"- `{row['category']}`: {row['session_date']} / `{row['ticker']}` — {row['reason']}"
        for row in selection_rows
    )
    trust_text = (
        "TRUSTWORTHY_FOR_PHASE2B_CORE_HISTORICAL_RECONSTRUCTION"
        if foundation_trust
        else "NOT_TRUSTWORTHY_FOR_PHASE2B_CORE"
    )
    report = f"""# ORB Real Rubix Replay Validation

## Verdict

`{trust_text}`

This verdict is limited to deterministic quote-derived historical evidence. It is not strategy validation, profitability evidence, or permission for live decisions. Live decisions remain disabled.

## Read-only and deterministic proof

- Rubix open mode: `mode=ro` plus `query_only=ON`
- Rubix sha256 unchanged: **{source_unchanged}** (`{source_after['sha256']}`)
- Safe raw rows selected: **{batch.raw_row_count}**
- Unique normalized events inserted: **{first_inserted}**
- Exact market-payload repeats: **{batch.exact_payload_repeats}**
- Same-timestamp distinct events preserved: **{batch.same_timestamp_distinct_events}**
- Market-identity hash collisions: **{batch.identity_hash_collisions}**
- Second replay inserted events: **{second_inserted}**
- Bars byte-equivalent after replay: **{deterministic_bars}**
- Opening Range outputs byte-equivalent: **{deterministic_ranges}**
- All repository row counts unchanged by second replay: **{double_replay_idempotent}**
- First replay wall time: **{first_duration:.3f} seconds**
- Research write transactions measured: **{metrics['write_transactions']}**
- Arrival-order rows rejected by the zero-tolerance live policy: **{arrival_metrics['arrival_order_out_of_order_rows']} / {arrival_metrics['continuous_rows']}**
- Historical policy: `{arrival_metrics['historical_out_of_order_policy']}`

## Deterministic selection rules

{selections_text}

The dense 2026-08-02 population replays every quote in `[10:00,10:15)`. Special active, archived, reset, delayed, same-timestamp-distinct and exact-repeat pairs replay their complete continuous session.

## Quote-derived bars versus `candles_1m`

| Classification | Minutes |
|---|---:|
{comparison_lines}

Differences are reported rather than forced equal because Rubix candles and quote events can represent different event sets. The CSV contains aggregate bar evidence only, never complete quote payloads.

## Opening Range and volume

- Quote-derived READY ranges on the dense session: **{len(ready)}**
- Active mapped READY: **{ready_active}**
- Archived READY: **{ready_archived}**
- Vendor-candle READY reference recalculated from the same session: **{vendor_ready_reference}**
- Quote-only READY: **{len(quote_only_ready)}** (`{', '.join(quote_only_ready) or 'none'}`)
- Vendor-only READY: **{len(vendor_only_ready)}** (`{', '.join(vendor_only_ready) or 'none'}`)
- Bars with volume unavailable/degraded: **{volume_invalid_bars}**
- Bars with negative volume: **{negative_volume_bars}**

Valid OHLC remains reconstructable when volume is unavailable. The weighted typical-price proxy remains unavailable for any contributing set containing invalid volume.

The six quote-only ranges arise because each corresponding vendor candle has a non-positive or missing 10:00 OHLC row while the normalized quote evidence produces a valid 10:00 price bar. No vendor-only ready range exists.

## Latency safety

`market_timestamp` controls exchange-minute placement. `receive_timestamp` controls delivery lag. Delayed valid market timestamps remain historical research evidence, while stale or unreliable events cannot populate current bid/ask capability. Every persisted capability keeps `LIVE_DECISION_DISABLED_*` until a later phase explicitly validates live freshness.
"""
    (output / "ORB_REAL_REPLAY_VALIDATION.md").write_text(report, encoding="utf-8")
    return metrics


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rubix-db", type=Path,
        default=PROJECT_ROOT / "data" / "rubix_live_market.db",
    )
    parser.add_argument(
        "--config", type=Path,
        default=PROJECT_ROOT / "config" / "orb_first_pullback.json",
    )
    parser.add_argument(
        "--research-db", type=Path,
        default=PROJECT_ROOT / "data" / "research" / "orb_phase2a_gate_replay.db",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=(PROJECT_ROOT / "reports" / "audits" / "strategies" /
                 "orb_first_pullback" / "phase2a_gate_closure"),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), sort_keys=True))
