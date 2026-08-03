"""ORB Shadow orchestrator — unattended daily workflow, Research Only.

Runs the whole Shadow day so the operator does not hand-run `--follow`,
`--list-runs`, `--reconstruct` and the comparison every session:

    pre-session checks -> wait -> Lane A live -> stop at 14:15 ->
    locate the live run -> Lane B reconstruction -> cross-run comparison ->
    Markdown report

It **drives the existing Phase 2C services directly**. It does not shell out to
`run_orb_shadow_session.py` and parse console text: a scraped exit code cannot
tell you whether Lane A committed, and a parsed run id is exactly the manual
step this is meant to remove.

It never starts Rubix, never authenticates, never opens a websocket, never
manages a process, and never emits a BUY, SELL, order or position instruction.
Production execution is disabled and no state in this workflow can represent a
trade.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
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
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.session import OrbSessionClassifier
from scalping_orb.shadow_calendar import (
    TradingDayStatus,
    load_trading_calendar,
)
from scalping_orb.shadow_orchestrator import (
    FullSessionCriteria,
    LeaseUnavailable,
    OrchestratorFailure,
    OrchestratorHealthSample,
    OrchestratorState,
    OrchestratorTransition,
    OrchestratorVerdict,
    assert_legal_transition,
    lease_scope_for,
    machine_identity,
    new_instance_id,
    process_rss_bytes,
)
from scalping_orb.shadow_service import (
    ComparisonReason,
    ShadowRunSelectionError,
    live_records_from_rows,
    select_live_run,
)
from scalping_orb.shadow_source import SOURCE_TABLE, ShadowSourceUnavailable
from scalping_orb.strategy_config import ENGINE_VERSION, OrbStrategyConfig

import scripts.run_orb_shadow_session as session_runner


BANNER = """
================================================================
  ORB SHADOW ORCHESTRATOR — RESEARCH ONLY
  PRODUCTION EXECUTION DISABLED
----------------------------------------------------------------
  Drives observation, reconstruction and reporting only. It emits
  no BUY, SELL, order, position or alert, and never starts, stops
  or configures the Rubix collector.
================================================================
""".strip()

_STOP = {"value": False, "reason": ""}


def _request_stop(signum, _frame) -> None:
    _STOP["value"] = True
    _STOP["reason"] = f"SIGNAL_{signum}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _orchestrator_run_id(session_date: date, started: datetime, source_identity: str) -> str:
    return hashlib.sha256(
        f"ORCH|{session_date.isoformat()}|{started.isoformat()}|{source_identity}".encode(
            "utf-8"
        )
    ).hexdigest()


class ShadowOrchestrator:
    """One unattended session. Owns the lease, the state machine and the report."""

    def __init__(
        self,
        args,
        *,
        now: datetime | None = None,
        clock=None,
        sleeper=None,
    ):
        """``clock`` and ``sleeper`` are injectable so a test never really waits.

        A mutation once let a test reach the real wait loop and four pytest
        processes slept toward a future market open until they were killed. A
        wait that can only be escaped by wall-clock time is untestable, so both
        the clock and the sleeper are seams.
        """

        self.args = args
        self._clock = clock or _utc_now
        self._sleeper = sleeper or time_module.sleep
        self.data_config = OrbDataConfig()
        self.strategy_config = OrbStrategyConfig(data=self.data_config)
        self.classifier = OrbSessionClassifier(self.data_config)
        self.zone = ZoneInfo(self.data_config.timezone)

        self.started_at = now or self._clock()
        self.session_date = args.session_date or self.classifier.session_date(
            self.started_at
        )
        self.window = self.classifier.window(self.session_date)

        self.source_path = Path(args.rubix_db_path)
        self.source_identity = session_runner._path_identity(self.source_path)
        self.research_db_path = self._research_db_path()
        self.log_dir = self._log_dir()
        self.report_dir = self._report_dir()

        self.calendar = load_trading_calendar(
            overrides_path=Path(args.calendar_overrides)
            if args.calendar_overrides
            else None,
            require_holidays=not args.allow_empty_calendar,
            allow_empty_calendar=bool(args.allow_empty_calendar),
        )
        self.calendar_decision = self.calendar.classify(self.session_date)

        self.orchestrator_run_id = _orchestrator_run_id(
            self.session_date, self.started_at, self.source_identity
        )
        self.instance_id = new_instance_id()
        self.lease_scope = lease_scope_for(self.session_date, self.source_identity)
        self.lease = None

        self.state = OrchestratorState.SCHEDULED
        self.sequence = 0
        self.repository: OrbResearchRepository | None = None
        self.live_run_id: str | None = None
        self.reconstruction_run_id: str | None = None
        self.live_summary: dict | None = None
        self.reconstruction_summary: dict | None = None
        self.failures: list[OrchestratorFailure] = []
        self._pending_transitions: list[OrchestratorTransition] = []
        self.criteria = FullSessionCriteria()

    # -- generated paths ---------------------------------------------------

    def _research_db_path(self) -> Path:
        """Per-session path, pinned inside the configured research root.

        The filename is generated, never operator-supplied, and the resolved
        result is asserted to remain under the resolved root. That is what stops
        a `..` root, a symlink or a Windows junction from silently relocating
        the write target next to a production database.
        """

        root = Path(self.args.research_root)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        resolved_root = root.resolve()
        candidate = (resolved_root / f"orb_full_shadow_{self.session_date.isoformat()}.db")
        resolved = candidate.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as error:
            raise ValueError(
                f"research path {resolved} escapes its root {resolved_root}"
            ) from error
        return resolved

    def _log_dir(self) -> Path:
        return PROJECT_ROOT / "logs" / "orb_shadow" / self.session_date.isoformat()

    def _report_dir(self) -> Path:
        return (
            PROJECT_ROOT / "reports" / "audits" / "strategies" / "orb_first_pullback"
            / "full_shadow" / self.session_date.isoformat()
        )

    # -- plumbing ----------------------------------------------------------

    def log(self, message: str) -> None:
        stamp = self._clock().isoformat(timespec="seconds")
        line = f"[{stamp}] {self.state.value:26s} {message}"
        print(line, flush=True)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with (self.log_dir / "orchestrator.log").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(line + "\n")
        except OSError:
            pass  # logging must never break the workflow


    def _flush_pending_transitions(self) -> None:
        """Write transitions buffered before the research database existed."""

        if not self._pending_transitions or self.repository is None:
            return
        pending, self._pending_transitions = self._pending_transitions, []
        for item in pending:
            self.repository.record_orchestrator_transition(
                self.orchestrator_run_id, item
            )

    def move(
        self,
        new_state: OrchestratorState,
        reason: str,
        *,
        error_detail: str | None = None,
        source_cursor_id: int | None = None,
    ) -> None:
        assert_legal_transition(self.state, new_state)
        transition = OrchestratorTransition(
            sequence_index=self.sequence,
            session_date=self.session_date,
            prior_state=self.state,
            new_state=new_state,
            occurred_at_utc=self._clock(),
            reason=reason,
            config_identity=self.data_config.fingerprint,
            live_run_id=self.live_run_id,
            reconstruction_run_id=self.reconstruction_run_id,
            source_cursor_id=source_cursor_id,
            heartbeat_at_utc=self._clock(),
            error_detail=error_detail,
        )
        if self.repository is None:
            # The first transitions happen before the destination is known to be
            # safe. Buffer rather than drop them: a workflow history with a hole
            # at the start cannot be audited.
            self._pending_transitions.append(transition)
        else:
            self._flush_pending_transitions()
            self.repository.record_orchestrator_transition(
                self.orchestrator_run_id, transition
            )
            self.repository.update_orchestrator_run(
                self.orchestrator_run_id,
                state=new_state,
                live_run_id=self.live_run_id,
                reconstruction_run_id=self.reconstruction_run_id,
            )
        self.sequence += 1
        self.state = new_state
        self.log(f"-> {new_state.value}: {reason}")

    def fail(self, code: str, detail: str, *, recoverable: bool) -> None:
        failure = OrchestratorFailure(
            occurred_at_utc=self._clock(), state=self.state,
            failure_code=code, detail=detail[:500], recoverable=recoverable,
        )
        self.failures.append(failure)
        if self.repository is not None:
            self.repository.record_orchestrator_failure(
                self.orchestrator_run_id, failure
            )
        self.log(f"FAILURE {code}: {detail[:200]}")

    # -- pre-session -------------------------------------------------------

    def pre_session_checks(self) -> bool:
        """Every check that must pass before a single row is read."""

        self.move(OrchestratorState.PRE_SESSION_CHECK, "running pre-session checks")

        # 1-2. Trading day, fail closed.
        decision = self.calendar_decision
        self.log(
            f"calendar {self.calendar.source} ({self.calendar.identity}) -> "
            f"{decision.status.value}: {decision.reason}"
        )
        if decision.status is TradingDayStatus.CALENDAR_UNAVAILABLE:
            self.fail("CALENDAR_UNAVAILABLE", decision.reason, recoverable=False)
            self.move(
                OrchestratorState.SKIPPED_NON_TRADING_DAY,
                "calendar unavailable; refusing to assume the market is open",
            )
            return False
        if not decision.is_trading_day:
            self.move(
                OrchestratorState.SKIPPED_NON_TRADING_DAY,
                f"{decision.status.value}: {decision.reason}",
            )
            return False

        # 3-5. Source exists, opens read-only, query_only confirmed.
        try:
            reader = session_runner.ShadowSourceReader(
                self.source_path, self.data_config
            )
            with reader.connect() as connection:
                query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
                max_id = connection.execute(
                    f"SELECT max(id) FROM {SOURCE_TABLE}"
                ).fetchone()[0]
                latest = connection.execute(
                    f"SELECT max(received_at) FROM {SOURCE_TABLE}"
                ).fetchone()[0]
        except ShadowSourceUnavailable as error:
            self.fail("SOURCE_UNAVAILABLE", str(error), recoverable=True)
            self.move(OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE, str(error))
            return False
        if query_only != 1:
            self.fail("SOURCE_NOT_QUERY_ONLY", "query_only was not enabled", recoverable=False)
            self.move(OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE, "query_only not enabled")
            return False
        self.log(f"source read-only OK: max_id={max_id} latest_received={latest}")

        # 6. Collector health, from file and cursor progress.
        if max_id is None or int(max_id or 0) <= 0:
            self.fail(
                "COLLECTOR_NO_ROWS", "source contains no quote rows", recoverable=True
            )
            self.move(
                OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE, "source has no rows"
            )
            return False

        # 7. Destination independent and safe.
        try:
            session_runner._assert_distinct_databases(
                self.source_path, self.research_db_path
            )
            self.research_db_path.parent.mkdir(parents=True, exist_ok=True)
            self.repository = OrbResearchRepository(self.research_db_path)
        except ValueError as error:
            self.fail("UNSAFE_DESTINATION", str(error), recoverable=False)
            self.move(OrchestratorState.SESSION_FAILED, str(error))
            return False

        # 9. Register the run, then take the lease (8) atomically.
        self.repository.start_orchestrator_run(
            {
                "orchestrator_run_id": self.orchestrator_run_id,
                "session_date": self.session_date,
                "state": self.state,
                "started_at_utc": self.started_at,
                "calendar_identity": self.calendar.identity,
                "calendar_status": decision.status,
                "config_identity": self.data_config.fingerprint,
                "strategy_fingerprint": self.strategy_config.strategy_fingerprint,
                "source_path_identity": self.source_identity,
                "active_universe_only": True,
            }
        )
        # The run row now exists, so buffered transitions have somewhere to go.
        # Flushed here rather than on the next `move`, because pre-session
        # checks may finish without another transition.
        self._flush_pending_transitions()
        try:
            self.lease = self.repository.acquire_orchestrator_lease(
                lease_scope=self.lease_scope,
                instance_id=self.instance_id,
                orchestrator_run_id=self.orchestrator_run_id,
                session_date=self.session_date,
                process_id=os.getpid(),
                machine_identity=machine_identity(),
                now=self._clock(),
                lease_seconds=self.args.lease_seconds,
            )
        except LeaseUnavailable as error:
            self.fail("DUPLICATE_INSTANCE", str(error), recoverable=False)
            self.move(OrchestratorState.SKIPPED_DUPLICATE_INSTANCE, str(error))
            return False
        self.log(f"lease acquired: instance={self.instance_id[:12]} pid={os.getpid()}")

        # 10. Pre-session health report.
        self.repository.record_orchestrator_health(
            self.orchestrator_run_id,
            OrchestratorHealthSample(
                observed_at_utc=self._clock(), state=self.state,
                source_cursor_id=int(max_id), process_rss_bytes=process_rss_bytes(),
            ),
        )
        return True

    # -- live lane ---------------------------------------------------------

    def wait_for_start(self) -> bool:
        """Hold until the configured live-start instant, heart-beating the lease."""

        start_at = self._live_start_instant()
        if self._clock() >= start_at:
            self.move(
                OrchestratorState.LIVE_SHADOW_STARTING,
                f"start instant {start_at.isoformat()} already passed",
            )
            return True
        self.move(
            OrchestratorState.WAITING_FOR_START,
            f"waiting until {start_at.isoformat()}",
        )
        # Bounded: a clock that never advances must not spin forever.
        deadline_iterations = max(1, int(self.args.maximum_wait_iterations))
        iterations = 0
        while self._clock() < start_at:
            if _STOP["value"]:
                self.move(OrchestratorState.SESSION_FAILED, _STOP["reason"] or "stopped")
                return False
            iterations += 1
            if iterations > deadline_iterations:
                self.fail(
                    "WAIT_LOOP_EXCEEDED",
                    f"waited {iterations} iterations without reaching "
                    f"{start_at.isoformat()}",
                    recoverable=False,
                )
                self.move(OrchestratorState.SESSION_FAILED, "wait loop exceeded")
                return False
            self.repository.heartbeat_orchestrator_lease(
                self.lease_scope, self.instance_id,
                now=self._clock(), lease_seconds=self.args.lease_seconds,
            )
            self._sleeper(min(self.args.wait_poll_seconds, 30.0))
        self.move(OrchestratorState.LIVE_SHADOW_STARTING, "start instant reached")
        return True

    def _live_start_instant(self) -> datetime:
        """Default: `--start-lead-minutes` before the continuous open."""

        return self.window.continuous_start_utc - timedelta(
            minutes=float(self.args.start_lead_minutes)
        )

    def run_live_lane(self) -> bool:
        """Drive the existing `--follow` workflow in-process."""

        argv = [
            "--follow",
            "--rubix-db-path", str(self.source_path),
            "--research-db-path", str(self.research_db_path),
            "--session-date", self.session_date.isoformat(),
            "--poll-seconds", str(self.args.poll_seconds),
            "--lateness-grace-seconds", str(self.args.lateness_grace_seconds),
            "--allowed-polling-gap-seconds", str(self.args.allowed_polling_gap_seconds),
            "--minimum-heartbeats", str(self.args.minimum_heartbeats),
            "--minimum-exchange-minutes", str(self.args.minimum_exchange_minutes),
            "--active-universe-only",
            "--stop-at-continuous-end",
            "--max-runtime-seconds", str(self.args.max_runtime_seconds),
            "--output-dir", str(self.report_dir),
        ]
        self.move(OrchestratorState.LIVE_SHADOW_RUNNING, "Lane A observation started")
        try:
            runner = session_runner.ShadowRunner(session_runner.parse_args(argv))
            self.live_summary = runner.run()
        except Exception as error:  # noqa: BLE001 - must not escape unattended
            self.fail("LIVE_LANE_FAILED", f"{type(error).__name__}: {error}", recoverable=True)
            self.move(OrchestratorState.LIVE_SHADOW_FAILED, "Lane A raised")
            return False

        self.live_run_id = self.live_summary["run_id"]
        classification = self.live_summary["session_classification"]
        self.move(
            OrchestratorState.LIVE_SHADOW_STOPPING,
            f"Lane A stopped: {self.live_summary['stop_reason']}",
        )
        self.repository.update_orchestrator_run(
            self.orchestrator_run_id, session_classification=classification
        )
        graceful = self.live_summary["stop_reason"] in {
            "CONTINUOUS_END_REACHED", "MAX_RUNTIME_REACHED", "SINGLE_BATCH",
            "RECONSTRUCTION_COMPLETE",
        }
        self.criteria = replace(
            self.criteria,
            started_before_session_start=bool(
                self.live_summary["runner_started_before_open"]
            ),
            covered_through_continuous_end=(
                self.live_summary["stop_reason"] == "CONTINUOUS_END_REACHED"
            ),
            opening_range_observed_live=self.live_summary["opening_ranges_ready"] > 0,
            cursor_progressed=self.live_summary["source_rows_read"] > 0,
            graceful_shutdown=graceful,
            lane_a_persisted=self.live_summary["live_evaluations"] > 0,
            sufficient_heartbeat_coverage=(
                self.live_summary["cycles"] >= self.args.minimum_heartbeats
            ),
            no_excessive_polling_outage=(
                "POLLING_OUTAGE_EXCEEDED_LIMIT"
                not in self.live_summary["classification_reasons"]
            ),
            sufficient_exchange_minute_coverage=(
                "INSUFFICIENT_EXCHANGE_MINUTE_COVERAGE"
                not in self.live_summary["classification_reasons"]
            ),
        )
        if classification == "FULL_SHADOW_SESSION":
            self.move(OrchestratorState.LIVE_SHADOW_COMPLETE, "classified FULL by the runner")
        else:
            self.move(
                OrchestratorState.LIVE_SHADOW_PARTIAL,
                f"classified {classification}: "
                f"{';'.join(self.live_summary['classification_reasons']) or 'n/a'}",
            )
        return True

    # -- post-session ------------------------------------------------------

    def run_post_session(self) -> bool:
        """Locate the live run, reconstruct, compare — with no manual run id."""

        self.move(OrchestratorState.RECONSTRUCTION_STARTING, "locating the live run")
        try:
            chosen = select_live_run(
                self.repository.find_shadow_runs(session_date=self.session_date),
                explicit_run_id=self.live_run_id,
                session_date=self.session_date,
                source_path_identity=self.source_identity,
                config_identity=self.data_config.fingerprint,
            )
        except ShadowRunSelectionError as error:
            self.fail("NO_ELIGIBLE_LIVE_RUN", str(error), recoverable=True)
            self.move(OrchestratorState.SESSION_FAILED, str(error))
            return False
        self.live_run_id = chosen["run_id"]
        self.log(
            f"live run selected automatically: {self.live_run_id[:16]} "
            f"(lane A rows={chosen['lane_a_rows']})"
        )

        argv = [
            "--reconstruct",
            "--rubix-db-path", str(self.source_path),
            "--research-db-path", str(self.research_db_path),
            "--session-date", self.session_date.isoformat(),
            "--lateness-grace-seconds", str(self.args.lateness_grace_seconds),
            "--active-universe-only",
            "--compare-live-run-id", self.live_run_id,
            "--output-dir", str(self.report_dir),
        ]
        self.move(OrchestratorState.RECONSTRUCTION_RUNNING, "Lane B reconstruction started")
        try:
            runner = session_runner.ShadowRunner(session_runner.parse_args(argv))
            self.reconstruction_summary = runner.run()
        except Exception as error:  # noqa: BLE001
            self.fail(
                "RECONSTRUCTION_FAILED", f"{type(error).__name__}: {error}", recoverable=True
            )
            self.move(OrchestratorState.SESSION_FAILED, "reconstruction raised")
            return False

        self.reconstruction_run_id = self.reconstruction_summary["run_id"]
        self.criteria = replace(self.criteria, reconstruction_completed=True)
        self.move(OrchestratorState.RECONSTRUCTION_COMPLETE, "Lane B complete")

        rows = self.repository.load_cross_run_comparison(
            self.live_run_id, self.reconstruction_run_id
        )
        if not rows:
            self.fail(
                "COMPARISON_EMPTY",
                "cross-run comparison produced no rows",
                recoverable=True,
            )
            self.move(OrchestratorState.SESSION_FAILED, "comparison produced no rows")
            return False
        self.criteria = replace(self.criteria, cross_run_comparison_completed=True)
        self.move(
            OrchestratorState.COMPARISON_COMPLETE, f"{len(rows)} comparison rows"
        )
        return True

    # -- report ------------------------------------------------------------

    def write_report(self) -> bool:
        try:
            path = self._render_report()
        except OSError as error:
            self.fail("REPORT_FAILED", str(error), recoverable=True)
            self.move(OrchestratorState.SESSION_FAILED, "report generation failed")
            return False
        self.criteria = replace(self.criteria, report_completed=True)
        self.move(OrchestratorState.REPORT_COMPLETE, f"report written: {path.name}")
        return True

    def _render_report(self) -> Path:
        rows = self.repository.load_cross_run_comparison(
            self.live_run_id, self.reconstruction_run_id
        )
        taxonomy: dict[str, int] = {}
        for row in rows:
            taxonomy[row["difference_reason"]] = (
                taxonomy.get(row["difference_reason"], 0) + 1
            )
        lane_a = self.repository.load_shadow_live_states(self.live_run_id)
        lane_b = self.repository.load_shadow_reconstruction_states(
            self.reconstruction_run_id
        )
        a_counts: dict[str, int] = {}
        for row in lane_a:
            a_counts[row["final_state"]] = a_counts.get(row["final_state"], 0) + 1
        b_counts: dict[str, int] = {}
        for row in lane_b:
            b_counts[row["final_state"]] = b_counts.get(row["final_state"], 0) + 1

        live = self.live_summary or {}
        lag = live.get("receive_lag") or {}
        verdict = self.criteria.verdict(
            live_failed=self.state is OrchestratorState.LIVE_SHADOW_FAILED
        )
        failed = self.criteria.failures()

        def table(mapping: dict[str, int]) -> str:
            if not mapping:
                return "| _(none)_ | 0 |"
            return "\n".join(
                f"| `{key}` | {value} |"
                for key, value in sorted(mapping.items(), key=lambda kv: -kv[1])
            )

        body = f"""# ORB Full Shadow Session Report — {self.session_date.isoformat()}

**Research Only. Production execution disabled.** Nothing in this report is a
trade signal, a recommendation, or a performance claim.

## Verdict

# `{verdict.value}`

| | |
|---|---|
| Runner classification | `{live.get('session_classification', 'n/a')}` |
| Orchestrator run | `{self.orchestrator_run_id[:16]}` |
| Live run (Lane A) | `{(self.live_run_id or 'n/a')[:16]}` |
| Reconstruction run (Lane B) | `{(self.reconstruction_run_id or 'n/a')[:16]}` |
| Calendar | `{self.calendar.source}` / `{self.calendar.identity}` ({self.calendar_decision.status.value}) |
| Universe filter | `ACTIVE_UNIVERSE_ONLY` |

{"**Unmet FULL criteria:** " + ", ".join(f"`{name}`" for name in failed) if failed else "All FULL criteria met."}

## Runner timing

| | |
|---|---|
| Orchestrator started | {self.started_at.isoformat()} |
| Started before 10:00 Cairo | **{live.get('runner_started_before_open', False)}** |
| Stop reason | `{live.get('stop_reason', 'n/a')}` |
| Cycles | {live.get('cycles', 0)} |

## Source health and coverage

| Measure | Value |
|---|---|
| Source rows read | {live.get('source_rows_read', 0):,} |
| Normalized events | {live.get('normalized_events', 0):,} |
| Session loads | {live.get('session_loads_total', 0)} |
| Symbols observed | {live.get('symbols_observed', 0)} |
| Operationally eligible symbols | {live.get('operationally_eligible_symbols', 0)} |
| Symbols withheld by universe filter | {live.get('symbols_withheld_by_universe_filter', 0)} |
| Completed 1-minute bars | {live.get('completed_one_minute_bars', 0):,} |
| Completed 5-minute bars | {live.get('completed_five_minute_bars', 0):,} |
| Opening ranges READY | {live.get('opening_ranges_ready', 0)} |

## Freshness distribution

| Metric | Seconds |
|---|---|
| median | {lag.get('median')} |
| p90 | {lag.get('p90')} |
| p95 | {lag.get('p95')} |
| % above budget | {lag.get('percent_above_budget')} |
| negative-lag events (clock skew) | {lag.get('negative')} |

## Lane A states (live)

| State | Count |
|---|---|
{table(a_counts)}

## Lane B states (reconstruction, `HISTORICAL_REPLAY`)

| State | Count |
|---|---|
{table(b_counts)}

## Lane A versus Lane B

| Category | Symbols |
|---|---|
{table(taxonomy)}

### `ENTRY_READY_RESEARCH` — RESEARCH ONLY / NOT A BUY SIGNAL / NOT LIVE EXECUTION

| | Lane A (live) | Lane B (reconstruction) |
|---|---|---|
| `ENTRY_READY_RESEARCH` | {a_counts.get('ENTRY_READY_RESEARCH', 0)} | {b_counts.get('ENTRY_READY_RESEARCH', 0)} |
| historical-only | — | {taxonomy.get('HISTORICAL_ONLY', 0)} |
| live freshness rejections | {taxonomy.get('FRESHNESS_REJECTED_LIVE', 0)} | — |
| opening-range revisions | {taxonomy.get('OPENING_RANGE_REVISED', 0)} | — |

> **Reconstructed results were not necessarily actionable live.** A Lane B
> state was computed with evidence that arrived later, and possibly against a
> revised opening range. `FRESHNESS_REJECTED_LIVE` counts the symbols where
> live could not reach the state at all. A `ENTRY_READY_RESEARCH` appearing
> only in Lane B is **not** a signal that was available during the session.
>
> Every `ENTRY_READY_RESEARCH` above, in **either** lane, is
> **RESEARCH ONLY**, **NOT A BUY SIGNAL** and **NOT LIVE EXECUTION**. Nothing in
> this workflow can place, size or route an order.

## Operational failures

{chr(10).join(f"- `{f.failure_code}` ({'recoverable' if f.recoverable else 'terminal'}): {f.detail}" for f in self.failures) or "_None recorded._"}

## FULL criteria

| Criterion | Met |
|---|---|
""" + "\n".join(
            f"| `{name}` | {'yes' if getattr(self.criteria, name) else '**no**'} |"
            for name in self.criteria.__dataclass_fields__  # type: ignore[attr-defined]
        ) + """

No profitability, win rate, expectancy, position size or order instruction is
reported, because none exists.
"""
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / "FULL_SHADOW_SESSION_REPORT.md"
        path.write_text(body, encoding="utf-8")
        (self.report_dir / "orchestrator_status.json").write_text(
            json.dumps(self.status_payload(verdict), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def status_payload(self, verdict: OrchestratorVerdict | None = None) -> dict:
        resolved = verdict or self.criteria.verdict(
            live_failed=self.state is OrchestratorState.LIVE_SHADOW_FAILED
        )
        return {
            "research_only": True,
            "production_disabled": True,
            "orchestrator_run_id": self.orchestrator_run_id,
            "session_date": self.session_date.isoformat(),
            "state": self.state.value,
            "final_verdict": resolved.value,
            "calendar_status": self.calendar_decision.status.value,
            "calendar_identity": self.calendar.identity,
            "live_run_id": self.live_run_id,
            "reconstruction_run_id": self.reconstruction_run_id,
            "unmet_full_criteria": list(self.criteria.failures()),
            "failures": [
                {"code": f.failure_code, "recoverable": f.recoverable, "detail": f.detail}
                for f in self.failures
            ],
            "research_db_path": str(self.research_db_path),
            "report_dir": str(self.report_dir),
        }

    # -- drive -------------------------------------------------------------

    def run(self) -> dict:
        try:
            if not self.pre_session_checks():
                return self._finish()
            if not self.args.retry_post_session:
                if not self.wait_for_start():
                    return self._finish()
                if not self.run_live_lane():
                    # A failed live lane may still have committed rows.
                    if not self.args.continue_after_live_failure:
                        return self._finish()
            if not self.run_post_session():
                return self._finish()
            if not self.write_report():
                return self._finish()
            self.move(OrchestratorState.SESSION_COMPLETE, "workflow complete")
            return self._finish()
        finally:
            if self.repository is not None and self.lease is not None:
                self.repository.release_orchestrator_lease(
                    self.lease_scope, self.instance_id, now=self._clock()
                )

    def _finish(self) -> dict:
        verdict = self.criteria.verdict(
            live_failed=self.state
            in (OrchestratorState.LIVE_SHADOW_FAILED, OrchestratorState.SESSION_FAILED)
        )
        if self.repository is not None:
            self.repository.update_orchestrator_run(
                self.orchestrator_run_id,
                finished_at_utc=self._clock(),
                final_verdict=verdict,
                state=self.state,
            )
        payload = self.status_payload(verdict)
        self.log(f"final verdict: {verdict.value}")
        return payload


def show_status(args) -> int:
    """Read-only. Starts nothing, takes no lease, evaluates nothing."""

    orchestrator = ShadowOrchestrator(args)
    print(BANNER)
    path = orchestrator.research_db_path
    print(f"  session date   : {orchestrator.session_date.isoformat()}")
    print(f"  research db    : {path}")
    print(f"  calendar       : {orchestrator.calendar.source} "
          f"-> {orchestrator.calendar_decision.status.value}")
    if not path.is_file():
        print("  status         : NO_RUN_RECORDED (research database does not exist)")
        return 0
    repository = OrbResearchRepository(path)
    runs = repository.find_orchestrator_runs(session_date=orchestrator.session_date)
    if not runs:
        print("  status         : NO_RUN_RECORDED")
        return 0
    for run in runs:
        print()
        print(f"  orchestrator run : {run['orchestrator_run_id'][:16]}")
        print(f"  state            : {run['state']}")
        print(f"  verdict          : {run['final_verdict'] or '(in progress)'}")
        print(f"  classification   : {run['session_classification'] or '-'}")
        print(f"  live run         : {(run['live_run_id'] or '-')[:16]}")
        print(f"  reconstruction   : {(run['reconstruction_run_id'] or '-')[:16]}")
        print(f"  started / ended  : {run['started_at_utc']} / {run['finished_at_utc'] or '-'}")
        failures = repository.load_orchestrator_failures(run["orchestrator_run_id"])
        for failure in failures:
            print(f"    failure: {failure['failure_code']} - {failure['detail'][:80]}")
    lease = repository.load_orchestrator_lease(
        lease_scope_for(orchestrator.session_date, orchestrator.source_identity)
    )
    if lease is not None:
        active = lease.is_active(_utc_now())
        print()
        print(f"  lease            : {'ACTIVE' if active else 'free/expired'} "
              f"(instance {lease.instance_id[:12]}, pid {lease.process_id})")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rubix-db-path",
        default=os.environ.get("ORB_RUBIX_DB_PATH"),
        help="Rubix collector database, opened mode=ro (required)",
    )
    parser.add_argument(
        "--research-root",
        default=os.environ.get("ORB_RESEARCH_ROOT", "data/research/orb_full_shadow"),
        help="directory for per-session research databases",
    )
    parser.add_argument("--session-date", type=lambda v: date.fromisoformat(v), default=None)
    parser.add_argument("--calendar-overrides", default=None)
    parser.add_argument(
        "--allow-empty-calendar",
        action="store_true",
        help="accept a holiday calendar with no entries (otherwise fail closed)",
    )
    parser.add_argument("--start-lead-minutes", type=float, default=15.0)
    parser.add_argument("--wait-poll-seconds", type=float, default=30.0)
    parser.add_argument(
        "--maximum-wait-iterations",
        type=int,
        default=2000,
        help="hard cap on pre-start wait cycles; a stopped clock cannot spin forever",
    )
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--lateness-grace-seconds", type=float, default=90.0)
    parser.add_argument("--allowed-polling-gap-seconds", type=float, default=300.0)
    parser.add_argument("--minimum-heartbeats", type=int, default=60)
    parser.add_argument("--minimum-exchange-minutes", type=int, default=200)
    parser.add_argument("--max-runtime-seconds", type=float, default=21600.0)
    parser.add_argument("--lease-seconds", type=float, default=600.0)
    parser.add_argument("--status", action="store_true", help="read-only status; starts nothing")
    parser.add_argument("--resume", action="store_true", help="continue using committed state")
    parser.add_argument(
        "--retry-post-session",
        action="store_true",
        help="retry reconstruction/comparison/report only; never creates Lane A",
    )
    parser.add_argument("--continue-after-live-failure", action="store_true")
    parser.add_argument(
        "--no-network",
        action="store_true",
        default=True,
        help="default and only supported behaviour; no provider is ever contacted",
    )
    args = parser.parse_args(argv)
    if not args.rubix_db_path:
        parser.error("--rubix-db-path (or ORB_RUBIX_DB_PATH) is required")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.status:
        return show_status(args)
    print(BANNER)
    for received in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if received is not None:
            try:
                signal.signal(received, _request_stop)
            except (ValueError, OSError):  # pragma: no cover
                pass
    summary = ShadowOrchestrator(args).run()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["state"] in {
        OrchestratorState.SESSION_COMPLETE.value,
        OrchestratorState.SKIPPED_NON_TRADING_DAY.value,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
