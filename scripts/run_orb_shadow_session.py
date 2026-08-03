"""ORB Phase 2C Shadow session runner — Research Only, production disabled.

Reads the existing Rubix collector's output read-only and feeds completed bars
into the unchanged Phase 2B research engine.

It never starts Rubix, never authenticates, never opens a websocket, never
changes a subscription and never manages a process. It is a reader of a file
another process already writes. The production launcher remains the sole owner
of collector lifecycle, and this runner is deliberately not registered with it.

Modes:

  --once         read one incremental batch, persist, exit
  --follow       keep polling until an explicit stop condition
  --reconstruct  deterministic post-session reconstruction (Lane B)

All paths come from arguments or environment. No machine path is hard-coded.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import date, datetime, time as dtime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time as time_module
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scalping_orb.config import OrbDataConfig
from scalping_orb.engine import DailyContext
from scalping_orb.events import (
    CumulativeVolumeTracker,
    NormalizationMode,
    RubixEventNormalizer,
)
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.session import OrbSessionClassifier
from scalping_orb.shadow_service import (
    OrbShadowService,
    SessionClassification,
    ShadowCycleMetrics,
    ShadowLiveStatus,
    ShadowSessionQuality,
    classify_session,
    receive_lag_statistics,
)
from scalping_orb.shadow_snapshot import ShadowSnapshotBuilder
from scalping_orb.shadow_source import (
    SOURCE_TABLE,
    ShadowCursor,
    ShadowSourceReader,
    ShadowSourceUnavailable,
)
from scalping_orb.strategy_config import ENGINE_VERSION, OrbStrategyConfig


RESEARCH_ONLY_BANNER = """
================================================================
  ORB PHASE 2C SHADOW SESSION — RESEARCH ONLY
  PRODUCTION EXECUTION DISABLED
----------------------------------------------------------------
  No order, execution, position, paper trade, alert or dashboard
  surface exists in this runner. The highest state reachable is
  ENTRY_READY_RESEARCH, which is a research candidate and is
  never a buy instruction.
================================================================
""".strip()


_STOP_REQUESTED = {"value": False, "reason": ""}


def _request_stop(signum, _frame) -> None:
    """Graceful Ctrl+C: finish the cycle in flight, commit, then exit."""

    _STOP_REQUESTED["value"] = True
    _STOP_REQUESTED["reason"] = f"SIGNAL_{signum}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _path_identity(path: Path) -> str:
    """Identify the source without storing a machine path in the database."""

    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]


def _run_id(session_date: date, started: datetime, source_identity: str, mode: str) -> str:
    return hashlib.sha256(
        f"{session_date.isoformat()}|{started.isoformat()}|{source_identity}|{mode}".encode(
            "utf-8"
        )
    ).hexdigest()


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def _assert_distinct_databases(source: Path, destination: Path) -> None:
    """The research target may never be the source, by any spelling of it.

    Resolving both catches `..`, symlinks and case differences. The repository
    has its own deny-list and content probe, but that only knows the *known*
    production filenames — it would not catch a renamed copy of the collector
    output being handed in as a write target.
    """

    resolved_source = source.resolve()
    resolved_destination = destination.resolve()
    if resolved_source == resolved_destination:
        raise ValueError(
            "research destination must not be the Rubix source database: "
            f"{resolved_source}"
        )
    # Also refuse the source's sidecar WAL/SHM files.
    for suffix in ("-wal", "-shm"):
        if resolved_destination == Path(str(resolved_source) + suffix):
            raise ValueError(
                f"research destination must not be a source sidecar file: "
                f"{resolved_destination}"
            )


class ShadowRunner:
    """Owns the cycle loop, the cursor and the two lanes."""

    def __init__(self, args):
        self.args = args
        _assert_distinct_databases(
            Path(args.rubix_db_path), Path(args.research_db_path)
        )
        self.data_config = (
            OrbDataConfig.from_mapping(
                json.loads(Path(args.config).read_text(encoding="utf-8"))
            )
            if args.config
            else OrbDataConfig()
        )
        self.strategy_config = OrbStrategyConfig(data=self.data_config)
        self.classifier = OrbSessionClassifier(self.data_config)
        self.zone = ZoneInfo(self.data_config.timezone)
        self.reader = ShadowSourceReader(
            args.rubix_db_path, self.data_config, batch_size=args.batch_size
        )
        self.repository = OrbResearchRepository(args.research_db_path)
        self.service = OrbShadowService(
            self.strategy_config, lateness_grace_seconds=args.lateness_grace_seconds
        )
        self.builder = ShadowSnapshotBuilder(
            self.data_config, lateness_grace_seconds=args.lateness_grace_seconds
        )
        self.normalizer = RubixEventNormalizer(
            self.data_config,
            holidays=(),
            mapping_validator=lambda canonical, rubix: bool(canonical and rubix),
            mode=NormalizationMode.LIVE
            if not args.reconstruct
            else NormalizationMode.HISTORICAL_REPLAY,
        )
        self.volume_tracker = CumulativeVolumeTracker(self.data_config)

        self.session_date = args.session_date or self.classifier.session_date(_utc_now())
        self.started_at = _utc_now()
        self.source_identity = _path_identity(Path(args.rubix_db_path))
        self.mode = (
            "RECONSTRUCT" if args.reconstruct else ("FOLLOW" if args.follow else "ONCE")
        )
        self.run_id = _run_id(
            self.session_date, self.started_at, self.source_identity, self.mode
        )

        window = self.classifier.window(self.session_date)
        self.window = window
        self.started_before_open = self.started_at <= window.continuous_start_utc

        # Accumulators
        self.all_events: list = []
        self.all_quality: list = []
        self.live_records: list = []
        self.cycles: list[ShadowCycleMetrics] = []
        self.previous_final_keys = frozenset()
        self.polling_failures = 0
        self.maximum_polling_gap = 0.0
        self.heartbeats = 0
        self.session_loads_total = 0
        self.rows_read_total = 0

    # -- one cycle ---------------------------------------------------------

    def run_cycle(self, cursor: ShadowCursor, index: int) -> tuple[ShadowCursor, bool]:
        started = _utc_now()
        failure = None
        try:
            batch = self.reader.read_batch(cursor, session_date=self.session_date)
        except ShadowSourceUnavailable as error:
            self.polling_failures += 1
            failure = str(error)[:200]
            batch = None

        if batch is None:
            finished = _utc_now()
            metrics = ShadowCycleMetrics(
                cycle_id=hashlib.sha256(f"{self.run_id}|{index}".encode()).hexdigest(),
                cycle_index=index,
                started_at_utc=started,
                finished_at_utc=finished,
                cursor_low_source_id=cursor.last_source_id,
                cursor_high_source_id=cursor.last_source_id,
                source_rows_read=0,
                normalized_events=0,
                session_loads=0,
                symbols_in_snapshot=0,
                symbols_evaluated=0,
                snapshot_identity="",
                live_status=ShadowLiveStatus.LIVE_SHADOW_SOURCE_UNAVAILABLE,
                duration_seconds=(finished - started).total_seconds(),
                source_failure=failure,
            )
            self.cycles.append(metrics)
            self.repository.persist_shadow_cycle(self.run_id, metrics, ())
            return cursor, False

        self.rows_read_total += batch.rows_read
        if batch.empty and batch.rows_read == 0:
            finished = _utc_now()
            self.repository.record_shadow_heartbeat(
                self.run_id,
                observed_at_utc=finished,
                live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY.value,
                last_source_id=cursor.last_source_id,
                latest_market_timestamp_utc=cursor.last_market_timestamp_utc,
                observed_receive_lag_seconds=None,
            )
            self.heartbeats += 1
            return batch.cursor_after, batch.exhausted

        # Normalize once, dedupe once.
        normalized = self.normalizer.normalize_many(
            batch.quotes, evaluated_at=_utc_now()
        )
        enriched = self.volume_tracker.apply_many(normalized.events)
        self.all_events.extend(enriched)
        self.all_quality.extend(normalized.quality_events)

        # ONE session load per cycle. This is the batching contract.
        self.session_loads_total += 1
        snapshot = self.builder.build(
            self.session_date,
            self.all_events,
            as_of=_utc_now(),
            cursor_low_source_id=batch.cursor_before.last_source_id,
            cursor_high_source_id=batch.cursor_after.last_source_id,
            quality_events=self.all_quality,
            source_rows_observed=self.rows_read_total,
            previous_final_bar_keys=self.previous_final_keys,
        )
        self.previous_final_keys = ShadowSnapshotBuilder.final_bar_keys(snapshot)

        # Only symbols with a newly final bar are re-evaluated.
        #
        # Reconstruction writes NO Lane A rows. Lane A records what was
        # knowable at an actual observation time; a post-session reconstruct
        # run never observed anything live, so manufacturing Lane A rows from
        # it would fabricate live history and could overwrite a real run's
        # account of the session.
        if self.args.reconstruct:
            records = ()
        else:
            records = self.service.evaluate_live(snapshot)
            self.live_records.extend(records)

        finished = _utc_now()
        metrics = ShadowCycleMetrics(
            cycle_id=hashlib.sha256(f"{self.run_id}|{index}".encode()).hexdigest(),
            cycle_index=index,
            started_at_utc=started,
            finished_at_utc=finished,
            cursor_low_source_id=batch.cursor_before.last_source_id,
            cursor_high_source_id=batch.cursor_after.last_source_id,
            source_rows_read=batch.rows_read,
            normalized_events=len(enriched),
            session_loads=1,
            symbols_in_snapshot=len(snapshot.tickers),
            symbols_evaluated=len(records),
            snapshot_identity=snapshot.snapshot_identity,
            live_status=self.service.live_status_for(snapshot),
            duration_seconds=(finished - started).total_seconds(),
        )
        self.cycles.append(metrics)
        # Cycle metrics, Lane A states and the cursor commit together.
        self.repository.persist_shadow_cycle(
            self.run_id, metrics, records, cursor=batch.cursor_after
        )
        self.heartbeats += 1
        return batch.cursor_after, batch.exhausted

    # -- drive -------------------------------------------------------------

    def run(self) -> dict:
        self.repository.start_shadow_run(
            self.run_id,
            self.session_date,
            mode=self.mode,
            started_at_utc=self.started_at,
            runner_started_before_open=self.started_before_open,
            source_path_identity=self.source_identity,
            config_identity=self.data_config.fingerprint,
            strategy_fingerprint=self.strategy_config.strategy_fingerprint,
            engine_version=ENGINE_VERSION,
        )
        cursor = self.repository.load_shadow_cursor(self.run_id, SOURCE_TABLE)
        if cursor is None:
            low, _high = self.reader.source_id_bounds_for_session(self.session_date)
            cursor = ShadowCursor(
                source_table=SOURCE_TABLE, last_source_id=max(0, low - 1)
            )

        index = 0
        stop_reason = "COMPLETED"
        deadline = (
            self.started_at + timedelta(seconds=self.args.max_runtime_seconds)
            if self.args.max_runtime_seconds
            else None
        )
        last_cycle_at = _utc_now()
        while True:
            cursor, exhausted = self.run_cycle(cursor, index)
            index += 1
            gap = (_utc_now() - last_cycle_at).total_seconds()
            self.maximum_polling_gap = max(self.maximum_polling_gap, gap)
            last_cycle_at = _utc_now()

            if self.args.reconstruct:
                # Drain the whole session, then stop.
                if exhausted:
                    stop_reason = "RECONSTRUCTION_COMPLETE"
                    break
                continue
            if not self.args.follow:
                stop_reason = "SINGLE_BATCH"
                break
            if _STOP_REQUESTED["value"]:
                stop_reason = _STOP_REQUESTED["reason"] or "STOP_REQUESTED"
                break
            if deadline and _utc_now() >= deadline:
                stop_reason = "MAX_RUNTIME_REACHED"
                break
            if (
                self.args.stop_at_continuous_end
                and _utc_now() >= self.window.continuous_end_utc
            ):
                stop_reason = "CONTINUOUS_END_REACHED"
                break
            if exhausted:
                time_module.sleep(max(0.0, float(self.args.poll_seconds)))

        return self.finalize(stop_reason, cursor)

    # -- finalize ----------------------------------------------------------

    def finalize(self, stop_reason: str, cursor: ShadowCursor) -> dict:
        finished = _utc_now()

        # Lane B over everything accumulated.
        snapshot = self.builder.build(
            self.session_date,
            self.all_events,
            as_of=finished,
            cursor_low_source_id=0,
            cursor_high_source_id=cursor.last_source_id,
            quality_events=self.all_quality,
            source_rows_observed=self.rows_read_total,
        )
        reconstruction = self.service.evaluate_reconstruction(snapshot)
        self.repository.persist_shadow_reconstruction(self.run_id, reconstruction)

        comparison = self.service.compare(
            self.session_date, self.live_records, reconstruction
        )
        self.repository.persist_shadow_comparison(self.run_id, comparison)

        lag = receive_lag_statistics(
            self.all_events,
            freshness_budget_seconds=self.data_config.maximum_quote_age_seconds,
        )
        observed_minutes = len(
            {
                event.market_timestamp_utc.replace(second=0, microsecond=0)
                for event in self.all_events
            }
        )
        classification, reasons = classify_session(
            session_date=self.session_date,
            runner_started_utc=self.started_at,
            runner_finished_utc=finished,
            config=self.data_config,
            maximum_polling_gap_seconds=self.maximum_polling_gap,
            allowed_polling_gap_seconds=self.args.allowed_polling_gap_seconds,
            heartbeat_count=self.heartbeats,
            minimum_heartbeats=self.args.minimum_heartbeats,
            cursor_advanced=cursor.last_source_id > 0 and self.rows_read_total > 0,
            opening_ranges_ready=snapshot.quality_summary.opening_ranges_ready,
            observed_exchange_minutes=observed_minutes,
            minimum_exchange_minutes=self.args.minimum_exchange_minutes,
            smoke=self.args.smoke,
        )

        quality = ShadowSessionQuality(
            session_date=self.session_date,
            classification=classification,
            classification_reasons=reasons,
            source_rows_observed=self.rows_read_total,
            normalized_events=len(self.all_events),
            exact_redeliveries_removed=max(
                0, self.rows_read_total - len(self.all_events)
            ),
            same_timestamp_distinct_retained=snapshot.quality_summary.observed_one_minute_slots,
            source_polling_failures=self.polling_failures,
            maximum_polling_gap_seconds=self.maximum_polling_gap,
            median_receive_lag_seconds=lag["median"],
            p90_receive_lag_seconds=lag["p90"],
            p95_receive_lag_seconds=lag["p95"],
            percent_above_freshness_budget=lag["percent_above_budget"],
            negative_lag_events=lag["negative"],
            out_of_order_events=snapshot.quality_summary.out_of_order_events,
            late_events_after_cutoff=snapshot.quality_summary.late_events_after_cutoff,
            active_mapped_symbols=sum(
                1 for value in snapshot.operationally_eligible.values() if value
            ),
            completed_one_minute_bars=snapshot.quality_summary.completed_one_minute_bars,
            completed_five_minute_bars=snapshot.quality_summary.completed_five_minute_bars,
            opening_ranges_ready=snapshot.quality_summary.opening_ranges_ready,
            live_evaluations=len(self.live_records),
            reconstruction_evaluations=len(reconstruction),
            heartbeat_count=self.heartbeats,
            process_runtime_seconds=(finished - self.started_at).total_seconds(),
            detail=snapshot.quality_summary.as_dict(),
        )
        self.repository.persist_shadow_session_quality(self.run_id, quality)
        self.repository.finish_shadow_run(
            self.run_id,
            finished_at_utc=finished,
            stop_reason=stop_reason,
            session_classification=classification.value,
        )

        summary = {
            "status": "SUCCESS",
            "research_only": True,
            "production_disabled": True,
            "run_id": self.run_id,
            "mode": self.mode,
            "source_mode": "READ_ONLY",
            "source_path_identity": self.source_identity,
            "session_date": self.session_date.isoformat(),
            "session_classification": classification.value,
            "classification_reasons": list(reasons),
            "runner_started_before_open": self.started_before_open,
            "stop_reason": stop_reason,
            "cycles": len(self.cycles),
            "session_loads_total": self.session_loads_total,
            "source_rows_read": self.rows_read_total,
            "normalized_events": len(self.all_events),
            "symbols_observed": len(snapshot.tickers),
            "live_evaluations": len(self.live_records),
            "reconstruction_evaluations": len(reconstruction),
            "opening_ranges_ready": snapshot.quality_summary.opening_ranges_ready,
            "completed_one_minute_bars": snapshot.quality_summary.completed_one_minute_bars,
            "completed_five_minute_bars": snapshot.quality_summary.completed_five_minute_bars,
            "receive_lag": lag,
            "comparison_rows": len(comparison),
            "entry_ready_research_live": sum(
                1
                for record in self.live_records
                if record.final_state == "ENTRY_READY_RESEARCH"
            ),
            "entry_ready_research_reconstruction": sum(
                1
                for record in reconstruction
                if record.final_state == "ENTRY_READY_RESEARCH"
            ),
            "performance_metrics": "NOT_MEASURED_REQUIRES_EXECUTION_PHASE",
            "profitability_claim": "NONE",
        }

        output = Path(self.args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "shadow_run_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _write_csv(
            output / "shadow_cycle_metrics.csv",
            [
                {
                    **{
                        k: (v.value if hasattr(v, "value") else v)
                        for k, v in asdict(cycle).items()
                        if not isinstance(v, datetime)
                    },
                    "started_at_utc": cycle.started_at_utc.isoformat(),
                    "finished_at_utc": cycle.finished_at_utc.isoformat(),
                }
                for cycle in self.cycles
            ],
        )
        _write_csv(
            output / "shadow_live_states.csv",
            [
                {
                    "session_date": r.session_date.isoformat(),
                    "canonical_ticker": r.canonical_ticker,
                    "final_state": r.final_state,
                    "live_status": r.live_status.value,
                    "observed_at_utc": r.observed_at_utc.isoformat(),
                    "rejection_reasons": ";".join(r.rejection_reasons),
                }
                for r in self.live_records
            ],
        )
        _write_csv(
            output / "shadow_reconstruction_states.csv",
            [
                {
                    "session_date": r.session_date.isoformat(),
                    "canonical_ticker": r.canonical_ticker,
                    "final_state": r.final_state,
                    "rejection_reasons": ";".join(r.rejection_reasons),
                }
                for r in reconstruction
            ],
        )
        _write_csv(
            output / "shadow_live_vs_reconstruction.csv",
            [
                {
                    "canonical_ticker": r.canonical_ticker,
                    "live_state": r.live_state,
                    "reconstruction_state": r.reconstruction_state,
                    "states_match": int(r.states_match),
                    "opening_range_revised": int(r.opening_range_revised),
                    "difference_reason": r.difference_reason,
                    "evaluable_live": int(r.evaluable_live),
                    "evaluable_historically": int(r.evaluable_historically),
                }
                for r in comparison
            ],
        )
        _write_csv(
            output / "shadow_session_quality.csv",
            [
                {
                    **{
                        k: v
                        for k, v in asdict(quality).items()
                        if not isinstance(v, (dict, tuple, date))
                    },
                    "session_date": quality.session_date.isoformat(),
                    "classification": quality.classification.value,
                    "classification_reasons": ";".join(quality.classification_reasons),
                }
            ],
        )
        return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one batch then exit (default)")
    mode.add_argument("--follow", action="store_true", help="poll until a stop condition")
    mode.add_argument(
        "--reconstruct", action="store_true", help="drain the session (Lane B)"
    )

    parser.add_argument(
        "--rubix-db-path",
        default=os.environ.get("ORB_RUBIX_DB_PATH"),
        help="Rubix collector database, opened mode=ro (required)",
    )
    parser.add_argument(
        "--research-db-path",
        default=os.environ.get(
            "ORB_RESEARCH_DB_PATH", "data/research/orb_shadow_session.db"
        ),
        help="independent ORB research database (write target)",
    )
    parser.add_argument("--config", default=os.environ.get("ORB_DATA_CONFIG"))
    parser.add_argument(
        "--session-date",
        type=lambda value: date.fromisoformat(value),
        default=None,
    )
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--lateness-grace-seconds", type=float, default=90.0)
    parser.add_argument("--allowed-polling-gap-seconds", type=float, default=300.0)
    parser.add_argument("--minimum-heartbeats", type=int, default=60)
    parser.add_argument("--minimum-exchange-minutes", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--stop-at-continuous-end", action="store_true")
    parser.add_argument("--max-runtime-seconds", type=float, default=0.0)
    parser.add_argument("--active-universe-only", action="store_true")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="label the result PARTIAL_SMOKE_SESSION; never closes the full-session blocker",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        default=True,
        help="default and only supported behaviour; no provider is ever contacted",
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
            / "phase2c_shadow"
        ),
    )
    args = parser.parse_args(argv)
    if not args.rubix_db_path:
        parser.error("--rubix-db-path (or ORB_RUBIX_DB_PATH) is required")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    print(RESEARCH_ONLY_BANNER)
    print(f"  source (mode=ro, query_only=ON) : {Path(args.rubix_db_path).resolve()}")
    print(f"  research destination            : {Path(args.research_db_path).resolve()}")
    print(f"  network                         : DISABLED")
    print(f"  collector lifecycle             : NOT MANAGED BY THIS RUNNER")
    print()
    for received in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if received is not None:
            try:
                signal.signal(received, _request_stop)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass
    summary = ShadowRunner(args).run()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
