"""Read-only Phase 2B Core historical replay over reconstructed intraday bars.

Ingests real Rubix quotes through the Phase 2A pipeline (``mode=ro`` +
``query_only=ON``) into a throwaway research database, then runs the Phase 2B
research engine over the reconstructed completed bars.

It reports state counts and rejection reasons only. It deliberately reports no
win rate, expectancy, profit factor or simulated return: those need an
execution and evaluation phase that does not exist, and quoting them from a
state machine would be a fabricated performance claim.

Never starts Rubix, never authenticates, never uses Yahoo, never writes to a
production database.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scalping_orb.config import OrbDataConfig
from scalping_orb.engine import DailyContext
from scalping_orb.events import NormalizationMode
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.replay import RubixReadOnlyReplaySource
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.shadow import OrbShadowIngestionService
from scalping_orb.states import OrbResearchState
from scalping_orb.strategy_config import EvaluationMode, OrbStrategyConfig
from scalping_orb.strategy_service import OrbResearchService


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(args) -> dict:
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    if args.research_db.exists():
        raise FileExistsError(
            f"Replay research database already exists; use a new path: {args.research_db}"
        )
    data_config = OrbDataConfig.from_mapping(
        json.loads(args.config.read_text(encoding="utf-8"))
    )
    strategy_config = OrbStrategyConfig(data=data_config)

    source_before = _sha256(args.rubix_db)
    source = RubixReadOnlyReplaySource(args.rubix_db, data_config)
    plan = source.build_plan()
    batch = source.load(plan)

    repository = OrbResearchRepository(args.research_db)
    service = OrbResearchService(repository, strategy_config)

    session_rows: list[dict] = []
    candidate_rows: list[dict] = []
    totals: dict[str, int] = {}
    rejections: dict[str, int] = {}
    started = datetime.now(timezone.utc)
    sessions_evaluated = symbols_evaluated = 0
    ranges_ready = volume_assessed = price_only = 0

    for session_date, raw_events in batch.raw_events_by_session:
        window = source.classifier.window(session_date)
        ingestion = OrbShadowIngestionService(
            repository,
            data_config,
            normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
        )
        ingested = ingestion.ingest(
            raw_events, evaluated_at=window.continuous_end_utc
        )
        if not ingested.session_ids:
            continue
        session_id = ingested.session_ids[0]
        sessions_evaluated += 1

        tickers = sorted({item.canonical_ticker for item in ingested.symbols})
        session_states: dict[str, int] = {}
        session_ready = 0
        session_started = datetime.now(timezone.utc)
        loads_before = repository.event_load_count
        rows_before = repository.event_rows_read
        for ticker in tickers:
            evaluation = service.evaluate_and_persist(
                session_id,
                ticker,
                as_of=window.continuous_end_utc,
                evaluation_mode=EvaluationMode.HISTORICAL_REPLAY,
                daily=DailyContext(),  # no D-1 context is asserted in replay
            )
            if evaluation is None:
                continue
            symbols_evaluated += 1
            state = evaluation.final_state.value
            session_states[state] = session_states.get(state, 0) + 1
            totals[state] = totals.get(state, 0) + 1
            for reason in evaluation.rejection_reasons:
                rejections[reason.value] = rejections.get(reason.value, 0) + 1
            if evaluation.final_state is not OrbResearchState.DATA_UNAVAILABLE:
                ranges_ready += 1
            if evaluation.pullback is not None:
                volume_assessed += int(evaluation.pullback.volume_assessed)
                price_only += int(evaluation.pullback.price_only)
            if evaluation.final_state is OrbResearchState.ENTRY_READY_RESEARCH:
                session_ready += 1
            candidate_rows.append(
                {
                    "session_date": session_date.isoformat(),
                    "canonical_ticker": ticker,
                    "final_state": state,
                    "opening_range_revision": evaluation.opening_range_revision,
                    "breakout": "YES" if evaluation.breakout else "NO",
                    "too_extended": (
                        "YES"
                        if evaluation.breakout and evaluation.breakout.too_extended
                        else "NO"
                    ),
                    "pullback": "YES" if evaluation.pullback else "NO",
                    "pullback_price_only": (
                        "YES"
                        if evaluation.pullback and evaluation.pullback.price_only
                        else "NO"
                    ),
                    "reclaim_confirmed": (
                        "YES"
                        if evaluation.reclaim and evaluation.reclaim.confirmed
                        else "NO"
                    ),
                    "rejection_reasons": ";".join(
                        reason.value for reason in evaluation.rejection_reasons
                    ),
                    "transitions": len(evaluation.transitions),
                }
            )
        # Session-level load counters and timing. `event_rows_read` growing as
        # the square of the symbol count is the signature of a per-symbol
        # full-session reload; it should stay close to the event count.
        session_rows.append(
            {
                "session_date": session_date.isoformat(),
                "symbols_evaluated": len(tickers),
                "entry_ready_research": session_ready,
                "evaluate_seconds": round(
                    (datetime.now(timezone.utc) - session_started).total_seconds(), 3
                ),
                "event_loads": repository.event_load_count - loads_before,
                "event_rows_read": repository.event_rows_read - rows_before,
                **{f"state_{k}": v for k, v in sorted(session_states.items())},
            }
        )

    duration = (datetime.now(timezone.utc) - started).total_seconds()

    # Determinism: a second identical replay must add nothing.
    counts_before = {
        table: repository.table_count(table)
        for table in (
            "orb_candidates",
            "orb_state_transitions",
            "orb_breakouts",
            "orb_pullbacks",
            "orb_reclaims",
            "orb_research_setups",
        )
    }
    for session_date, _raw in batch.raw_events_by_session:
        window = source.classifier.window(session_date)
        session_id = None
        with repository.connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM orb_sessions WHERE session_date=?",
                (session_date.isoformat(),),
            ).fetchone()
            session_id = row[0] if row else None
        if session_id is None:
            continue
        for ticker in sorted(
            {row["canonical_ticker"] for row in _tickers(repository, session_id)}
        ):
            service.evaluate_and_persist(
                session_id,
                ticker,
                as_of=window.continuous_end_utc,
                evaluation_mode=EvaluationMode.HISTORICAL_REPLAY,
                daily=DailyContext(),
            )
    counts_after = {
        table: repository.table_count(table) for table in counts_before
    }

    source_after = _sha256(args.rubix_db)
    summary = {
        "status": "SUCCESS",
        "source_mode": "READ_ONLY",
        "rubix_sha256_before": source_before,
        "rubix_sha256_after": source_after,
        "rubix_unchanged": source_before == source_after,
        "sessions_evaluated": sessions_evaluated,
        "symbols_evaluated": symbols_evaluated,
        "state_counts": dict(sorted(totals.items())),
        "rejection_counts": dict(sorted(rejections.items())),
        "volume_assessed": volume_assessed,
        "price_only": price_only,
        "entry_ready_research": totals.get(
            OrbResearchState.ENTRY_READY_RESEARCH.value, 0
        ),
        "replay_seconds": round(duration, 3),
        "event_loads_total": repository.event_load_count,
        "event_rows_read_total": repository.event_rows_read,
        "known_blocker": "REPLAY_SESSION_BATCHING_REQUIRED",
        "idempotent": counts_before == counts_after,
        "counts_first_pass": counts_before,
        "counts_second_pass": counts_after,
        "engine_version": strategy_config.strategy_fingerprint[:16],
        "performance_metrics": "NOT_MEASURED_REQUIRES_EXECUTION_PHASE",
    }

    if session_rows:
        fields = sorted({key for row in session_rows for key in row})
        normalised = [{key: row.get(key, 0) for key in fields} for row in session_rows]
        _write_csv(output / "phase2b_session_states.csv", fields, normalised)
    if candidate_rows:
        _write_csv(
            output / "phase2b_candidates.csv",
            list(candidate_rows[0]),
            candidate_rows,
        )
    (output / "phase2b_core_replay.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _tickers(repository, session_id):
    with repository.connect() as connection:
        return connection.execute(
            "SELECT DISTINCT canonical_ticker FROM orb_normalized_events WHERE session_id=?",
            (session_id,),
        ).fetchall()


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
        default=PROJECT_ROOT / "data" / "research" / "orb_phase2b_core_replay.db",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=(PROJECT_ROOT / "reports" / "audits" / "strategies" /
                 "orb_first_pullback" / "phase2b_core"),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), sort_keys=True))
