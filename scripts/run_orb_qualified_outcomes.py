"""Measure signals against their own persisted stop and target levels (v2).

    python scripts/run_orb_qualified_outcomes.py \
        --shadow-dir data/research/orb_full_shadow \
        --rubix-db  data/rubix_live_market.db

Only signals carrying an `orb_signal_qualification` row are measured. The 34
historical signals have none, are not given one, and remain in the v1 table
under `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED`.

Read-only against both evidence sources: shadow session databases open
`mode=ro&immutable=1` because they are finished files; the collector database
opens `mode=ro` with `PRAGMA query_only=ON` and never `immutable=1`, because
Rubix is usually still writing to it. The only file written is the outcome
store. Nothing is started, stopped, scheduled or ordered.
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
from scalping_orb.performance.outcome_store import QualifiedOutcomeStore
from scalping_orb.performance.qualified_outcomes import (
    OutcomeQuality,
    OutcomeStatus,
    discover_qualified_signals,
    measure_qualified_signal,
)
from scalping_orb.performance.signal_outcomes import (
    OutcomeMeasurementConfig,
    SignalDiscoveryError,
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


def session_date_of(path: Path) -> date:
    marker = "orb_full_shadow_"
    if not path.stem.startswith(marker):
        raise ValueError(f"cannot infer a session date from {path.name}")
    return date.fromisoformat(path.stem[len(marker):])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-dir", type=Path, default=DEFAULT_SHADOW_DIR)
    parser.add_argument("--shadow-db", type=Path, action="append", default=[])
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--rubix-db", type=Path, default=DEFAULT_RUBIX_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--maximum-observation-gap-seconds", type=float, default=300.0)
    parser.add_argument("--maximum-tail-gap-seconds", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    shadow_dir = _resolve(args.shadow_dir)
    rubix_db = _resolve(args.rubix_db)
    output_db = _resolve(args.output_db)
    if not rubix_db.is_file():
        parser.error(f"price source not found: {rubix_db}")

    if args.shadow_db:
        databases = [_resolve(path) for path in args.shadow_db]
    elif args.session:
        databases = [shadow_dir / f"orb_full_shadow_{value}.db" for value in args.session]
    else:
        databases = sorted(shadow_dir.glob("orb_full_shadow_*.db"))
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
    store = None if args.dry_run else QualifiedOutcomeStore(output_db)
    if store is not None:
        store.initialize()

    price_source = open_read_only(rubix_db, immutable=False)
    summary: list[dict] = []
    status_totals: dict[str, int] = {}
    quality_totals: dict[str, int] = {}
    skipped_unqualified = 0
    try:
        for path in databases:
            session = session_date_of(path)
            boundary = session_end_utc(session, data_config)
            shadow = open_read_only(path, immutable=True)
            try:
                discovered = discover_qualified_signals(shadow)
            except (SignalDiscoveryError, sqlite3.Error) as error:
                print(f"[skip] {path.name}: {error}")
                continue
            finally:
                shadow.close()

            if not discovered:
                skipped_unqualified += 1
                print(
                    f"\n=== {session.isoformat()}  0 qualified signals "
                    f"(no orb_signal_qualification rows — nothing is reconstructed)"
                )
                continue

            outcomes = []
            for signal, qualification in discovered:
                observations = read_observations(
                    price_source,
                    signal.canonical_ticker,
                    start_utc=signal.detection_timestamp_utc,
                    end_utc=boundary,
                )
                outcomes.append(
                    measure_qualified_signal(
                        signal,
                        qualification,
                        observations,
                        session_end=boundary,
                        config=measurement_config,
                    )
                )

            if store is not None and outcomes:
                store.record_qualified(
                    outcomes,
                    session_date=session,
                    lane_a_run_id=discovered[0][0].lane_a_run_id,
                    shadow_database=path,
                    price_source_database=rubix_db,
                    session_end_utc=boundary,
                    config=measurement_config,
                )

            print(f"\n=== {session.isoformat()}  ({len(outcomes)} qualified signals)")
            print(
                f"  {'sym':<7}{'trigger':>9}{'stop':>9}{'T1':>9}{'T2':>9}"
                f"{'entry':>9}{'proxyR':>8}  outcome / evidence"
            )
            for outcome in outcomes:
                q = outcome.qualification
                status_totals[outcome.outcome_status.value] = (
                    status_totals.get(outcome.outcome_status.value, 0) + 1
                )
                key = outcome.outcome_measurement_quality.value
                quality_totals[key] = quality_totals.get(key, 0) + 1
                fmt = lambda v: "-" if v is None else f"{v:.3f}"
                print(
                    f"  {outcome.signal.canonical_ticker:<7}"
                    f"{fmt(q.trigger_price):>9}{fmt(q.proposed_stop):>9}"
                    f"{fmt(q.target_1):>9}{fmt(q.target_2):>9}"
                    f"{fmt(outcome.entry_price_proxy):>9}"
                    f"{fmt(outcome.proxy_r_multiple):>8}"
                    f"  {outcome.outcome_status.value} / "
                    f"{outcome.outcome_evidence_source.value}"
                )
                summary.append(
                    {
                        "session_date": outcome.signal.session_date.isoformat(),
                        "canonical_ticker": outcome.signal.canonical_ticker,
                        "detection_at_utc": (
                            outcome.signal.detection_timestamp_utc.isoformat()
                        ),
                        "trigger_price": q.trigger_price,
                        "proposed_stop": q.proposed_stop,
                        "target_1": q.target_1,
                        "target_2": q.target_2,
                        "entry_price_proxy": outcome.entry_price_proxy,
                        "outcome_status": outcome.outcome_status.value,
                        "outcome_evidence_source": (
                            outcome.outcome_evidence_source.value
                        ),
                        "outcome_measurement_quality": (
                            outcome.outcome_measurement_quality.value
                        ),
                        "stop_reached": outcome.stop_reached,
                        "target_1_reached": outcome.target_1_reached,
                        "target_2_reached": outcome.target_2_reached,
                        "measurement_return_pct": outcome.measurement_return_pct,
                        "proxy_r_multiple": outcome.proxy_r_multiple,
                        "session_end_percent": outcome.session_end_percent,
                    }
                )
    finally:
        price_source.close()

    print("\n=== outcome status")
    for status in OutcomeStatus:
        print(f"  {status.value:<46}{status_totals.get(status.value, 0)}")
    print("\n=== measurement quality")
    for quality in OutcomeQuality:
        print(f"  {quality.value:<46}{quality_totals.get(quality.value, 0)}")
    print(f"\n  {'TOTAL QUALIFIED SIGNALS':<46}{len(summary)}")
    if skipped_unqualified:
        print(
            f"  {'sessions with no qualification rows':<46}{skipped_unqualified}\n"
            "  Those signals predate qualification persistence. They are not "
            "backfilled and\n  keep TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED in the v1 table."
        )
    print(
        "\n  trigger_price is the engine's confirmed level; entry_price_proxy is the "
        "first\n  observable quote at or after detection. Neither is an order fill — "
        "no order was\n  placed. proxy_r_multiple is a measurement proxy, not a "
        "realized R, and position-\n  level profit and loss stays undefined because no "
        "execution model exists."
    )
    if not args.dry_run:
        print(f"\n  written to {output_db}")

    if args.json:
        print(
            json.dumps(
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "measurement_config": measurement_config.as_dict(),
                    "outcome_status_counts": status_totals,
                    "quality_counts": quality_totals,
                    "signals": summary,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
