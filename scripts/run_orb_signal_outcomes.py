"""Measure what price did after each ORB `ENTRY_READY_RESEARCH` signal.

    python scripts/run_orb_signal_outcomes.py \
        --shadow-dir data/research/orb_full_shadow \
        --rubix-db  data/rubix_live_market.db

Read-only against both evidence sources. The shadow session databases are
opened `mode=ro&immutable=1` because they are finished files; the collector
database is opened `mode=ro` with `PRAGMA query_only=ON` and *never*
`immutable=1`, because Rubix is usually still writing to it and promising
SQLite that a live file is immutable is how a reader ends up parsing a stale
page it was told would never change.

The only file this script writes is the outcome store it is pointed at. It
starts nothing, stops nothing, triggers no scheduled task, and emits no order.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scalping_orb.config import OrbDataConfig
from scalping_orb.performance.outcome_store import OutcomeStore
from scalping_orb.performance.signal_outcomes import (
    MeasurementQuality,
    OutcomeMeasurementConfig,
    SignalDiscoveryError,
    discover_signals,
    measure_signal,
    read_observations,
    session_end_utc,
)


DEFAULT_SHADOW_DIR = Path("data/research/orb_full_shadow")
DEFAULT_RUBIX_DB = Path("data/rubix_live_market.db")
DEFAULT_OUTPUT_DB = Path("data/research/orb_signal_outcomes/orb_signal_outcomes.db")


def _resolve(value: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def open_read_only(path: Path, *, immutable: bool) -> sqlite3.Connection:
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}{suffix}", uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def shadow_databases(
    directory: Path, sessions: tuple[str, ...], explicit: tuple[Path, ...]
) -> list[Path]:
    if explicit:
        return [_resolve(path) for path in explicit]
    if sessions:
        return [directory / f"orb_full_shadow_{value}.db" for value in sessions]
    return sorted(directory.glob("orb_full_shadow_*.db"))


def session_date_of(path: Path) -> date:
    stem = path.stem
    marker = "orb_full_shadow_"
    if not stem.startswith(marker):
        raise ValueError(f"cannot infer a session date from {path.name}")
    return date.fromisoformat(stem[len(marker):])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-dir", type=Path, default=DEFAULT_SHADOW_DIR)
    parser.add_argument(
        "--shadow-db", type=Path, action="append", default=[],
        help="measure this shadow database (repeatable; overrides --shadow-dir)",
    )
    parser.add_argument(
        "--session", action="append", default=[],
        help="session date YYYY-MM-DD (repeatable)",
    )
    parser.add_argument("--rubix-db", type=Path, default=DEFAULT_RUBIX_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument(
        "--maximum-observation-gap-seconds", type=float, default=300.0
    )
    parser.add_argument("--maximum-tail-gap-seconds", type=float, default=300.0)
    parser.add_argument(
        "--dry-run", action="store_true", help="measure and print without persisting"
    )
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = parser.parse_args(argv)

    shadow_dir = _resolve(args.shadow_dir)
    rubix_db = _resolve(args.rubix_db)
    output_db = _resolve(args.output_db)

    if not rubix_db.is_file():
        parser.error(f"price source not found: {rubix_db}")

    databases = shadow_databases(
        shadow_dir, tuple(args.session), tuple(args.shadow_db)
    )
    missing = [path for path in databases if not path.is_file()]
    if missing:
        parser.error("shadow database not found: " + ", ".join(str(p) for p in missing))
    if not databases:
        parser.error(f"no shadow databases under {shadow_dir}")

    data_config = OrbDataConfig()
    measurement_config = OutcomeMeasurementConfig(
        maximum_observation_gap_seconds=args.maximum_observation_gap_seconds,
        maximum_tail_gap_seconds=args.maximum_tail_gap_seconds,
    )
    store = None if args.dry_run else OutcomeStore(output_db)
    if store is not None:
        store.initialize()

    price_source = open_read_only(rubix_db, immutable=False)
    summary: list[dict] = []
    totals: dict[str, int] = {}
    try:
        for path in databases:
            session = session_date_of(path)
            boundary = session_end_utc(session, data_config)
            shadow = open_read_only(path, immutable=True)
            try:
                signals = discover_signals(shadow)
            except SignalDiscoveryError as error:
                print(f"[skip] {path.name}: {error}")
                continue
            finally:
                shadow.close()

            outcomes = []
            for signal in signals:
                observations = read_observations(
                    price_source,
                    signal.canonical_ticker,
                    start_utc=signal.detection_timestamp_utc,
                    end_utc=boundary,
                )
                outcomes.append(
                    measure_signal(
                        signal,
                        observations,
                        session_end=boundary,
                        config=measurement_config,
                    )
                )

            if store is not None and outcomes:
                store.record(
                    outcomes,
                    session_date=session,
                    lane_a_run_id=signals[0].lane_a_run_id,
                    shadow_database=path,
                    price_source_database=rubix_db,
                    session_end_utc=boundary,
                    config=measurement_config,
                )

            print(f"\n=== {session.isoformat()}  ({len(outcomes)} signals)")
            print(
                f"  session end (continuous close) {boundary.isoformat()}   "
                f"lane A run {signals[0].lane_a_run_id[:16] if signals else '-'}"
            )
            header = (
                f"  {'sym':<7}{'detect UTC':<10}{'entry':>9}{'lag s':>7}"
                f"{'obs':>7}{'MFE%':>8}{'MAE%':>8}{'close%':>8}  quality"
            )
            print(header)
            for outcome in outcomes:
                totals[outcome.measurement_quality.value] = (
                    totals.get(outcome.measurement_quality.value, 0) + 1
                )
                signal = outcome.signal
                detect = signal.detection_timestamp_utc.strftime("%H:%M:%S")
                if outcome.measured:
                    print(
                        f"  {signal.canonical_ticker:<7}{detect:<10}"
                        f"{outcome.entry_price:>9.3f}{outcome.entry_lag_seconds:>7.1f}"
                        f"{outcome.observation_count:>7}"
                        f"{outcome.maximum_favorable_excursion_percent:>8.2f}"
                        f"{outcome.maximum_adverse_excursion_percent:>8.2f}"
                        f"{outcome.session_end_percent:>8.2f}"
                        f"  {outcome.measurement_quality.value}"
                    )
                else:
                    print(
                        f"  {signal.canonical_ticker:<7}{detect:<10}"
                        f"{'-':>9}{'-':>7}{0:>7}{'-':>8}{'-':>8}{'-':>8}"
                        f"  {outcome.measurement_quality.value}"
                    )
                summary.append(
                    {
                        "session_date": signal.session_date.isoformat(),
                        "canonical_ticker": signal.canonical_ticker,
                        "detection_timestamp_utc": (
                            signal.detection_timestamp_utc.isoformat()
                        ),
                        "entry_ready_episode_count": signal.entry_ready_episode_count,
                        "measurement_quality": outcome.measurement_quality.value,
                        "measurement_reasons": [
                            reason.value for reason in outcome.measurement_reasons
                        ],
                        "tp_sl_status": outcome.tp_sl_status.value,
                        "entry_price": outcome.entry_price,
                        "entry_lag_seconds": outcome.entry_lag_seconds,
                        "observation_count": outcome.observation_count,
                        "maximum_observation_gap_seconds": (
                            outcome.maximum_observation_gap_seconds
                        ),
                        "maximum_favorable_excursion_percent": (
                            outcome.maximum_favorable_excursion_percent
                        ),
                        "maximum_adverse_excursion_percent": (
                            outcome.maximum_adverse_excursion_percent
                        ),
                        "session_end_percent": outcome.session_end_percent,
                    }
                )
    finally:
        price_source.close()

    print("\n=== measurement quality")
    for quality in MeasurementQuality:
        print(f"  {quality.value:<24}{totals.get(quality.value, 0)}")
    print(f"  {'TOTAL SIGNALS':<24}{len(summary)}")
    print(
        "\n  Per-signal take-profit and stop-loss levels were never persisted for "
        "these signals;\n  every outcome carries TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED "
        "and no R multiple is reported.\n  These are measurements. They are not a "
        "verdict on the strategy."
    )
    if not args.dry_run:
        print(f"\n  written to {output_db}")

    if args.json:
        print(
            json.dumps(
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "measurement_config": measurement_config.as_dict(),
                    "measurement_config_fingerprint": measurement_config.fingerprint,
                    "quality_counts": totals,
                    "signals": summary,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
