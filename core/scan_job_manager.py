"""Background market-scan job: state, progress, cancellation and a safe registry.

The scan used to run as one blocking call inside ``st.spinner(...)``, so the operator
saw an indefinite spinner for fifteen minutes, could not stop it, and a browser rerun
could start a second scan over the same workspace.

This module owns the job lifecycle instead. A worker thread runs the existing scanner
and publishes an immutable progress snapshot; the Streamlit page only ever *reads* that
snapshot. The separation is deliberate and load-bearing:

  * the worker never touches ``st.*`` or ``session_state`` — Streamlit's script context
    belongs to the page thread, and calling into it from a worker is undefined;
  * the registry is guarded by one lock and exposes a single atomic
    ``create_or_get_active_job``, so a double click, a rerun, a reopened page and a
    second browser all attach to the SAME job rather than racing to create one;
  * finalization is guarded by an idempotency flag keyed on ``scan_id``, so an immutable
    forward session is written exactly once and never by a rerun.

Nothing here computes an indicator, makes a trading decision, or changes a value.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import logging
import threading
import time
import uuid
from core.symbols import SYMBOL_SOURCE

logger = logging.getLogger(__name__)

# -- job states -------------------------------------------------------------- #

STARTING = "STARTING"
PREPARING_RUBIX = "PREPARING_RUBIX"
SCANNING = "SCANNING"
FINALIZING = "FINALIZING"
CANCELLING = "CANCELLING"
CANCELLED = "CANCELLED"
COMPLETED = "COMPLETED"
COMPLETED_WITH_GAPS = "COMPLETED_WITH_GAPS"
FAILED = "FAILED"

#: States in which a job still owns its workspace; a new scan may not be created.
ACTIVE_STATES = frozenset({STARTING, PREPARING_RUBIX, SCANNING, FINALIZING, CANCELLING})
TERMINAL_STATES = frozenset({CANCELLED, COMPLETED, COMPLETED_WITH_GAPS, FAILED})

# -- progress events emitted by the scanner ---------------------------------- #

SCAN_STARTING = "SCAN_STARTING"
RUBIX_PREPARING = "RUBIX_PREPARING"
RUBIX_READY = "RUBIX_READY"
SYMBOL_STARTING = "SYMBOL_STARTING"
SYMBOL_COMPLETED = "SYMBOL_COMPLETED"
SYMBOL_FAILED = "SYMBOL_FAILED"
FINALIZATION_STARTING = "FINALIZATION_STARTING"
SCAN_COMPLETED = "SCAN_COMPLETED"
SCAN_CANCELLED = "SCAN_CANCELLED"
SCAN_FAILED = "SCAN_FAILED"


# Progress and coverage answer different questions and must not be conflated.
#
#   progress % = completed / total          — how far through the universe we are
#   coverage % = success   / total          — how much of the universe was analysed
#
# A SKIPPED symbol is a data-coverage gap: the evidence for that symbol does not support
# analysis. A FAILED symbol is a provider or software failure: the system could not do
# its job. Both count toward ``completed``; only the distinction between them tells the
# operator whether to fix data or fix infrastructure.
SKIPPED_STATUSES = frozenset({
    "INVALID_HISTORY",
    "INSUFFICIENT_HISTORY",
    "EODHD_CACHE_MISS",
    "EXCLUDED_NON_EQUITY",
    "SYMBOL_NOT_MAPPED",
    "VOLUME_POLICY_UNRESOLVED",
    "RUBIX_QUOTE_MISSING",
})
FAILED_STATUSES = frozenset({
    "EODHD_TIMEOUT",
    "EODHD_CONNECTION_FAILED",
    "EODHD_RATE_LIMITED",
    "EODHD_AUTH_FAILED",
    "EODHD_PROVIDER_UNAVAILABLE",
    "EODHD_REFRESH_FAILED",
    "RUBIX_DB_BUSY",
    "RUBIX_OVERLAY_UNAVAILABLE",
    "INTERNAL_ERROR",
})


def outcome_bucket(status):
    """``"success"`` / ``"skipped"`` / ``"failed"`` for one typed symbol status."""
    text = str(status or "")
    if text == "SUCCESS":
        return "success"
    if text in SKIPPED_STATUSES:
        return "skipped"
    if text in FAILED_STATUSES:
        return "failed"
    return "failed"          # an unknown status is a failure, never a silent skip


@dataclass(frozen=True)
class ScanProgress:
    """An immutable snapshot the UI can render without holding any lock.

    ``completed == success + skipped + failed`` holds for every published snapshot.
    """

    scan_id: str = ""
    state: str = STARTING
    stage: str = "Preparing market scan"
    total: int = 0
    completed: int = 0
    current_symbol: str = ""
    last_completed_symbol: str = ""
    success: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    last_symbol_seconds: float = 0.0
    estimated_remaining_seconds: float = None
    coverage_percent: float = 0.0
    eodhd_cache_hits: int = 0
    eodhd_cache_misses: int = 0
    eodhd_refresh_attempts: int = 0
    eodhd_refresh_successes: int = 0
    rubix_overlay_available: int = 0
    rubix_overlay_missing: int = 0
    rubix_batch_status: str = ""
    status_breakdown: dict = field(default_factory=dict)
    circuit_breaker_state: str = "CLOSED"

    @property
    def counts_balance(self) -> bool:
        return self.completed == self.success + self.skipped + self.failed


class CircuitBreaker:
    """Opens only for BROAD provider failures, never for per-symbol data conditions.

    A cache miss, insufficient history, invalid history, an excluded non-equity or an
    unmapped ticker says nothing about provider health, so none of them are counted. An
    authentication rejection is conclusive on its own and opens the breaker immediately.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"

    def __init__(self, threshold=3):
        self.threshold = int(threshold)
        self.consecutive_failures = 0
        self.state = self.CLOSED
        self.reason = ""
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self.state == self.OPEN

    def record_broad_failure(self, error):
        """Count one provider-health failure. Returns True when the breaker is open."""
        from providers.eodhd_client import EODHDAuthFailed

        with self._lock:
            if isinstance(error, EODHDAuthFailed):
                self.state = self.OPEN
                self.reason = "EODHD rejected the credential"
                return True
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.threshold:
                self.state = self.OPEN
                self.reason = (f"{self.consecutive_failures} consecutive EODHD "
                               f"provider failures")
            return self.is_open

    def record_success(self):
        with self._lock:
            self.consecutive_failures = 0


@dataclass
class ScanJob:
    """One background scan. Every mutation goes through the job's own lock."""

    scan_id: str
    workspace_key: str
    total: int = 0
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    cancellation_event: threading.Event = field(default_factory=threading.Event)
    final_result: object = None
    sanitized_error: str = ""
    finalization_started: bool = False
    finalization_completed: bool = False
    #: Set once the page has stored this job's result. The registry never clears
    #: it: a result adopted is a result that can never be silently re-adopted.
    result_consumed_by: str = ""
    _worker_claimed: bool = False
    _progress: ScanProgress = None
    _worker: threading.Thread = None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _started_monotonic: float = 0.0
    _finished_monotonic: float = 0.0
    _context: object = None
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    # -- progress ----------------------------------------------------------- #

    def progress(self) -> ScanProgress:
        """A snapshot. Immutable, so the caller may hold it as long as it likes."""
        with self._lock:
            return self._progress

    def publish(self, **changes):
        """Replace the snapshot atomically, recomputing derived fields."""
        with self._lock:
            current = self._progress
            elapsed = (time.monotonic() - self._started_monotonic
                       if self._started_monotonic else 0.0)
            merged = replace(current, elapsed_seconds=elapsed, **changes)
            completed, total = merged.completed, merged.total
            coverage = (100.0 * merged.success / total) if total else 0.0
            remaining = None
            if completed and total and completed < total and elapsed > 0:
                remaining = (elapsed / completed) * (total - completed)
            self._progress = replace(merged, coverage_percent=round(coverage, 1),
                                     estimated_remaining_seconds=remaining)
            return self._progress

    # -- state -------------------------------------------------------------- #

    @property
    def state(self) -> str:
        with self._lock:
            return self._progress.state

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def request_cancel(self):
        """Set cancellation and move to CANCELLING. Safe to call repeatedly."""
        with self._lock:
            self.cancellation_event.set()
            if self._progress.state in ACTIVE_STATES:
                self.publish(state=CANCELLING, stage="Stopping scan")

    @property
    def cancelled(self) -> bool:
        return self.cancellation_event.is_set()

    def claim_worker(self) -> bool:
        """Claim the right to run this job's worker. True for exactly one caller.

        ``create_or_get_active_job`` already returns ``created=True`` to one
        caller only, but the worker starts *outside* that lock. This makes the
        single-worker invariant a property of the job itself rather than of one
        call site, so no future caller can start a second thread for one scan.
        """
        with self._lock:
            if self._worker_claimed:
                return False
            self._worker_claimed = True
            return True

    @property
    def worker_started(self) -> bool:
        with self._lock:
            return self._worker_claimed

    @property
    def result_available(self) -> bool:
        """A terminal job whose result is actually here.

        Terminal is not the same as ready: the worker publishes its state after
        assigning ``final_result``, but a reader that arrives between those two
        moments must wait rather than conclude the result is missing.
        """
        return (not self.is_active) and self.final_result is not None

    @property
    def result_consumed(self) -> bool:
        with self._lock:
            return bool(self.result_consumed_by)

    def claim_result(self, observer) -> bool:
        """Claim the right to adopt this result. True for exactly one observer.

        Refuses while the result is absent, so a reader that arrives early can
        retry on its next poll instead of permanently consuming a scan that had
        not finished publishing. That ordering is the whole fix: the previous
        code marked the job consumed *before* looking at ``final_result``, and a
        single early call destroyed the run's visibility for good.
        """
        with self._lock:
            state = self._progress.state
            if state in ACTIVE_STATES:
                return False
            # FAILED means the worker raised before producing anything, so there
            # is nothing to wait for. Every other terminal state owes a result.
            if self.final_result is None and state != FAILED:
                return False
            if self.result_consumed_by:
                return False
            self.result_consumed_by = str(observer)
            return True

    @property
    def result_pending(self) -> bool:
        """Terminal, no result yet, and a result is still owed.

        This is the window the old page fell into: the worker assigns
        ``final_result`` and only then publishes its terminal state, so a reader
        that samples between the two sees a finished job carrying nothing. It
        must wait, never conclude the scan produced no rows.
        """
        with self._lock:
            state = self._progress.state
        return (state not in ACTIVE_STATES
                and self.final_result is None
                and state != FAILED)

    def begin_finalization(self) -> bool:
        """Claim the right to finalize. Returns True for exactly one caller."""
        with self._lock:
            if self.finalization_started or self.cancelled:
                return False
            self.finalization_started = True
            return True

    # -- owned resources ----------------------------------------------------- #

    def attach_context(self, context):
        """Record the scan context so the worker's ``finally`` can always close it."""
        with self._lock:
            self._context = context

    def close_context(self):
        """Close the scan context on every exit path. Safe to call repeatedly."""
        with self._lock:
            context = self._context
            self._context = None
        if context is not None:
            try:
                context.close()
            except Exception:
                logger.debug("scan context close failed", exc_info=True)


#: A finished job is kept only long enough for the page to render its result.
TERMINAL_JOB_TTL_SECONDS = 45 * 60
MAX_RETAINED_TERMINAL_JOBS = 15


class ScanJobRegistry:
    """Process-level registry. One active job per workspace, created atomically.

    Terminal jobs are retained briefly so a reopened page can still show the last
    result, then evicted — by age first, then by count — so neither worker threads nor
    finished result objects accumulate for the life of the process. An ACTIVE job is
    never evicted, whatever its age.
    """

    def __init__(self, terminal_ttl_seconds=TERMINAL_JOB_TTL_SECONDS,
                 max_terminal_jobs=MAX_RETAINED_TERMINAL_JOBS):
        self._jobs = {}
        self._lock = threading.Lock()
        self.terminal_ttl_seconds = float(terminal_ttl_seconds)
        self.max_terminal_jobs = int(max_terminal_jobs)

    def _evict_locked(self):
        """Drop expired then surplus TERMINAL jobs. Caller holds the lock."""
        now = time.monotonic()
        terminal = [(key, job) for key, job in self._jobs.items() if not job.is_active]
        for key, job in terminal:
            finished = job._finished_monotonic
            if finished and (now - finished) > self.terminal_ttl_seconds:
                self._jobs.pop(key, None)
        terminal = [(key, job) for key, job in self._jobs.items() if not job.is_active]
        if len(terminal) > self.max_terminal_jobs:
            terminal.sort(key=lambda item: item[1]._finished_monotonic or 0.0)
            for key, _ in terminal[:len(terminal) - self.max_terminal_jobs]:
                self._jobs.pop(key, None)

    def create_or_get_active_job(self, workspace_key, total=0):
        """Return ``(job, created)``.

        The check and the insert happen under ONE lock, so two threads racing on the
        same workspace cannot both create a job. A double click, a Streamlit rerun and
        a second browser session all receive the existing job with ``created=False``.
        """
        with self._lock:
            existing = self._jobs.get(workspace_key)
            if existing is not None and existing.is_active:
                return existing, False
            self._evict_locked()
            now = datetime.now(timezone.utc).isoformat()
            job = ScanJob(scan_id=uuid.uuid4().hex[:12], workspace_key=str(workspace_key),
                          total=int(total), created_at=now)
            job._started_monotonic = time.monotonic()
            job._progress = ScanProgress(
                scan_id=job.scan_id, state=STARTING, stage="Preparing market scan",
                total=int(total), completed=0)
            self._jobs[workspace_key] = job
            return job, True

    def get(self, workspace_key):
        with self._lock:
            return self._jobs.get(workspace_key)

    def active_job(self, workspace_key):
        """The job still owning this workspace, or None."""
        with self._lock:
            job = self._jobs.get(workspace_key)
            return job if job is not None and job.is_active else None

    def unconsumed_job(self, workspace_key):
        """A finished job whose result nobody has adopted yet, or None.

        This is what lets a refreshed page recover a completed scan instead of
        offering to run it again.
        """
        with self._lock:
            job = self._jobs.get(workspace_key)
        if job is None or job.is_active or job.result_consumed:
            return None
        return job

    def active_count(self):
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.is_active)

    def size(self):
        with self._lock:
            return len(self._jobs)

    def clear(self, workspace_key=None):
        """Drop jobs. Tests use this; the app does not need it."""
        with self._lock:
            if workspace_key is None:
                self._jobs.clear()
            else:
                self._jobs.pop(workspace_key, None)


#: The process-level registry. Thread objects live here and are never serialized into
#: session state or any git-tracked file.
REGISTRY = ScanJobRegistry()


def _repository_root():
    """The resolved repository root this process is running from."""
    from pathlib import Path
    return str(Path(__file__).resolve().parents[1])


class WorkspaceConfigurationError(RuntimeError):
    """The scan workspace could not be identified with confidence.

    Raised instead of guessing. A workspace key that silently degrades to a
    placeholder is worse than no key at all: two pages would compute different
    keys for the same workspace, the page would lose its running job, and a
    second click would start a duplicate concurrent scan. That is exactly what
    happened on 2026-08-03 (RUN_20260803_223656 and RUN_20260803_223829).
    """


def _physical_identity(path):
    """A stable identity for one file, independent of how it is spelled.

    ``D:\\EGX_AI_Trader`` is a junction onto ``F:\\EGX_AI_Trader``, so the same
    universe file has two path spellings. Windows file identity
    (``st_dev``/``st_ino``) collapses them; ``Path.resolve()`` alone would not if
    only one spelling were ever used. Falls back to the resolved path when the
    file cannot be stat-ed, which keeps a missing universe from being silently
    equal to every other missing universe.
    """
    from pathlib import Path

    resolved = Path(path)
    try:
        stat = resolved.stat()
    except OSError:
        return "path:" + str(resolved)
    return "file:{}:{}".format(stat.st_dev, stat.st_ino)


def repository_root():
    """The resolved repository root this module belongs to.

    Derived from this file's location, never from the process working directory,
    so a ``chdir`` anywhere in the app cannot move it.
    """
    from pathlib import Path

    return Path(__file__).resolve().parents[1]


def resolve_symbol_source(source=SYMBOL_SOURCE):
    """Resolve a possibly relative universe path against the repository root.

    ``SYMBOL_SOURCE`` is relative (``data/universe/egx_universe.csv``). Resolving
    it with ``Path(source).resolve()`` anchors it to the *current working
    directory*, so any ``chdir`` in the process silently produces a different
    workspace key. Anchoring to the repository root removes that dependency
    entirely.
    """
    from pathlib import Path

    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = repository_root() / candidate
    try:
        return candidate.resolve()
    except OSError:                       # pragma: no cover - unresolvable path
        return candidate


#: Settings key per data purpose. Unknown purposes are a configuration error,
#: not a reason to guess.
PROVIDER_SETTING_BY_PURPOSE = {
    "dashboard": "dashboard_provider",
    "scanner": "scanner_provider",
    "forward_testing": "forward_testing_provider",
}


def resolve_provider_mode(purpose="dashboard"):
    """The configured operational provider for this purpose, or raise.

    The previous implementation swallowed every exception and substituted
    ``"unknown"``. That turned a transient configuration read failure into a
    *different workspace key*, which is how one page lost track of a running
    scan. A workspace whose provider cannot be established is a workspace no
    scan may start in.
    """
    key = PROVIDER_SETTING_BY_PURPOSE.get(str(purpose).strip().lower())
    if key is None:
        raise WorkspaceConfigurationError(
            "unknown scan purpose {!r}; expected one of {}".format(
                purpose, sorted(PROVIDER_SETTING_BY_PURPOSE)))
    try:
        from config.settings_manager import settings

        value = settings.data.get(key, "")
    except Exception as error:
        raise WorkspaceConfigurationError(
            "cannot read {} from settings: {}: {}".format(
                key, type(error).__name__, error)) from error
    text = str(value).strip().lower()
    if not text:
        raise WorkspaceConfigurationError(
            "{} is not configured; a scan workspace cannot be identified".format(key))
    return text


@dataclass(frozen=True)
class ScanWorkspace:
    """Typed identity of ONE market-scan workspace.

    Every field genuinely decides whether two scans would collide: the physical
    repository, the data purpose, the physical universe file and the operational
    provider. Nothing here changes between reruns, so the key is stable by
    construction rather than by convention.

    Deliberately excluded: session ids, tab ids, widget state, wall-clock values
    and the process working directory — anything that varies per rerun would
    defeat duplicate-run prevention, which is the whole point of the key.
    """

    repository_identity: str
    purpose: str
    source_identity: str
    source_path: str
    provider_mode: str

    @property
    def key(self) -> str:
        return "::".join((self.repository_identity, self.purpose,
                          self.source_identity, self.provider_mode))

    def describe(self) -> dict:
        """Display-safe fields for the page. No secret, no path guessing."""
        return {
            "workspace_key": self.key,
            "purpose": self.purpose,
            "universe": self.source_path,
            "provider_mode": self.provider_mode,
        }


def resolve_workspace(purpose="dashboard", source=SYMBOL_SOURCE, provider_mode=None):
    """Build the typed workspace identity, or raise WorkspaceConfigurationError."""
    resolved_source = resolve_symbol_source(source)
    if provider_mode is None:
        provider_mode = resolve_provider_mode(purpose)
    provider_mode = str(provider_mode).strip().lower()
    if not provider_mode:
        raise WorkspaceConfigurationError("provider mode may not be empty")
    return ScanWorkspace(
        repository_identity=_physical_identity(repository_root()),
        purpose=str(purpose).strip().lower(),
        source_identity=_physical_identity(resolved_source),
        source_path=str(resolved_source),
        provider_mode=provider_mode,
    )


def workspace_key_for(purpose="dashboard", source=SYMBOL_SOURCE, provider_mode=None):
    """A stable key identifying ONE market-scan workspace.

    Thin wrapper over :func:`resolve_workspace`; kept because callers and tests
    address the workspace by key. It raises rather than returning a degraded key.
    """
    return resolve_workspace(purpose, source, provider_mode).key


def start_scan_job(source=SYMBOL_SOURCE, purpose="dashboard", *, runner=None,
                   registry=None, symbols=None, autostart=True):
    """Create (or attach to) the job for this workspace and start its worker.

    Returns ``(job, created)``. When ``created`` is False an active scan already owns
    this workspace and NOTHING new is started — that is what makes a double click, a
    rerun and a second tab safe.
    """
    from core.symbols import load_symbols

    registry = registry or REGISTRY
    # Raises WorkspaceConfigurationError rather than starting a scan in a
    # workspace it cannot identify. A guessed key is how two concurrent scans
    # were started against one workspace on 2026-08-03.
    key = workspace_key_for(purpose, source)
    if symbols is None:
        try:
            symbols = load_symbols(source)
        except Exception:
            symbols = []
    job, created = registry.create_or_get_active_job(key, total=len(symbols))
    if not created:
        return job, False

    # The STARTING snapshot already exists at this point, so the page can render real
    # state on its very next run rather than waiting for the Rubix batch.
    if autostart and job.claim_worker():
        worker = threading.Thread(
            target=_run_job, args=(job, source, purpose, runner),
            name=f"scan-{job.scan_id}", daemon=True)
        job._worker = worker
        job.started_at = datetime.now(timezone.utc).isoformat()
        worker.start()
    return job, True


def _run_job(job, source, purpose, runner=None):
    """Worker body. Calls scanner/provider code only — never a Streamlit API."""
    from core.scanner import scan_symbols

    try:
        job.publish(state=PREPARING_RUBIX, stage="Loading Rubix quote overlays")
        execute = runner or scan_symbols
        result = execute(source, data_purpose=purpose,
                         progress=make_progress_reporter(job),
                         cancellation_event=job.cancellation_event,
                         job=job)
        job.final_result = result
        if job.cancelled or getattr(result, "status", "") == CANCELLED:
            job.publish(state=CANCELLED, stage="Scan cancelled")
        else:
            snapshot = job.progress()
            final_state = (COMPLETED if snapshot.success == snapshot.total
                           else COMPLETED_WITH_GAPS)
            job.publish(state=final_state, stage="Scan complete")
    except Exception as error:  # sanitized: the class and message only, no secrets
        logger.exception("Market scan job failed")
        job.sanitized_error = f"{type(error).__name__}: {str(error)[:300]}"
        job.publish(state=FAILED, stage="Scan failed")
    finally:
        # The context is closed on every path — success, cancellation and exception.
        try:
            job.close_context()
        finally:
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job._finished_monotonic = time.monotonic()


def make_progress_reporter(job):
    """Return the callback the scanner emits events through.

    Only safe structured data crosses this boundary: symbols, counts, typed statuses and
    durations. No DataFrame, no provider payload, no credential.
    """

    def report(event, **payload):
        try:
            _apply_event(job, event, payload)
        except Exception:      # progress must never be able to break a scan
            logger.debug("progress event %s failed", event, exc_info=True)

    return report


def _apply_event(job, event, payload):
    snapshot = job.progress()
    if event == SCAN_STARTING:
        job.publish(state=SCANNING, stage="Scanning symbols",
                    total=int(payload.get("total", snapshot.total)))
    elif event == RUBIX_PREPARING:
        job.publish(state=PREPARING_RUBIX, stage="Loading Rubix quote overlays")
    elif event == RUBIX_READY:
        job.publish(state=SCANNING, stage="Scanning symbols",
                    rubix_overlay_available=int(payload.get("available", 0)),
                    rubix_overlay_missing=int(payload.get("missing", 0)),
                    rubix_batch_status=str(payload.get("status", "")))
    elif event == SYMBOL_STARTING:
        job.publish(current_symbol=str(payload.get("symbol", "")))
    elif event in (SYMBOL_COMPLETED, SYMBOL_FAILED):
        status = str(payload.get("status", ""))
        breakdown = dict(snapshot.status_breakdown)
        breakdown[status] = breakdown.get(status, 0) + 1
        bucket = outcome_bucket(status)
        job.publish(
            completed=snapshot.completed + 1,
            success=snapshot.success + (1 if bucket == "success" else 0),
            skipped=snapshot.skipped + (1 if bucket == "skipped" else 0),
            failed=snapshot.failed + (1 if bucket == "failed" else 0),
            last_completed_symbol=str(payload.get("symbol", "")),
            last_symbol_seconds=float(payload.get("seconds", 0.0)),
            current_symbol="",
            status_breakdown=breakdown,
            eodhd_cache_hits=snapshot.eodhd_cache_hits + int(payload.get("cache_hit", 0)),
            eodhd_refresh_attempts=(snapshot.eodhd_refresh_attempts
                                    + int(payload.get("refresh_attempt", 0))),
            circuit_breaker_state=str(payload.get("breaker", snapshot.circuit_breaker_state)),
        )
    elif event == FINALIZATION_STARTING:
        job.publish(state=FINALIZING, stage="Recording forward session")
    elif event == SCAN_CANCELLED:
        job.publish(state=CANCELLING, stage="Stopping scan")
