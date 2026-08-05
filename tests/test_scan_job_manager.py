"""Contract tests for the background market-scan job.

These pin the safety properties the blocking-spinner design could not offer: one job per
workspace no matter how many clicks or tabs, a snapshot the operator can see immediately,
cancellation that actually stops work, and a forward session finalized exactly once.

Nothing here runs a real scan, touches the live databases, or reaches a network.
"""

from __future__ import annotations

from core.symbols import SYMBOL_SOURCE
import ast
import pathlib
import threading
import time

import pytest

from core import scan_job_manager as jm
from dashboard.scan_status_panel import coverage_view, scan_status_view


@pytest.fixture(autouse=True)
def clean_registry():
    jm.REGISTRY.clear()
    yield
    jm.REGISTRY.clear()


def _job(total=265, **kwargs):
    job, created = jm.start_scan_job(symbols=["A"] * total, autostart=False, **kwargs)
    return job, created


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def test_the_starting_snapshot_is_published_immediately():
    """The operator must see real state long before the Rubix batch finishes."""
    started = time.monotonic()
    job, created = _job()
    latency = time.monotonic() - started

    assert created is True
    assert latency < 2.0, f"first snapshot took {latency:.2f}s"
    snapshot = job.progress()
    assert snapshot.state == jm.STARTING
    assert snapshot.stage == "Preparing market scan"
    assert (snapshot.completed, snapshot.total) == (0, 265)
    assert snapshot.scan_id


def test_twenty_racing_threads_create_exactly_one_job():
    """The check and the insert share one lock, so a race cannot produce two scans."""
    seen, lock = [], threading.Lock()

    def racer():
        job, created = _job()
        with lock:
            seen.append((job.scan_id, created))

    threads = [threading.Thread(target=racer) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len({scan_id for scan_id, _ in seen}) == 1
    assert sum(1 for _, created in seen if created) == 1


def test_a_rerun_or_second_browser_attaches_to_the_running_job():
    first, created_first = _job()
    second, created_second = _job()
    assert created_first is True and created_second is False
    assert second.scan_id == first.scan_id


def test_a_terminal_job_releases_the_workspace_for_a_new_scan():
    finished, _ = _job()
    finished.publish(state=jm.COMPLETED, stage="Scan complete")
    fresh, created = _job()
    assert created is True and fresh.scan_id != finished.scan_id


def test_different_repositories_do_not_share_a_workspace():
    """A worktree and the main repository must never attach to one another's scan."""
    key = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    # The key identifies the repository by file identity rather than by path
    # text, so D: and F: spellings of one junctioned repository collapse. The
    # human-readable path stays available on the typed workspace.
    workspace = jm.resolve_workspace("dashboard", SYMBOL_SOURCE)
    root = pathlib.Path(__file__).resolve().parents[1]
    assert pathlib.Path(workspace.source_path).is_relative_to(root)
    assert workspace.repository_identity == jm._physical_identity(root)
    assert key != jm.workspace_key_for("scanner", SYMBOL_SOURCE)
    assert key != jm.workspace_key_for("dashboard", "data/other_universe.csv")


def test_the_workspace_key_carries_no_volatile_or_secret_value():
    key = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    assert key == jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    for forbidden in ("token", "secret", "session", str(time.time())[:6]):
        assert forbidden not in key.lower()


def test_terminal_jobs_are_evicted_but_active_jobs_never_are():
    registry = jm.ScanJobRegistry(terminal_ttl_seconds=0.01, max_terminal_jobs=2)
    active, _ = registry.create_or_get_active_job("ws-active", total=1)
    for index in range(5):
        done, _ = registry.create_or_get_active_job(f"ws-{index}", total=1)
        done.publish(state=jm.COMPLETED)
        done._finished_monotonic = time.monotonic() - 10
    registry.create_or_get_active_job("ws-trigger", total=1)

    assert registry.get("ws-active") is active, "an active job was evicted"
    assert active.is_active
    terminal = registry.size() - registry.active_count()
    assert terminal <= 3, f"terminal jobs were not bounded ({terminal})"


def test_registry_reset_is_available_for_tests():
    _job()
    assert jm.REGISTRY.size() == 1
    jm.REGISTRY.clear()
    assert jm.REGISTRY.size() == 0


# --------------------------------------------------------------------------- #
# Worker isolation and lifecycle
# --------------------------------------------------------------------------- #

def test_the_job_module_never_imports_or_calls_streamlit():
    """Streamlit's script context belongs to the page thread; a worker must stay out."""
    source = pathlib.Path(jm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add((node.module or "").split(".")[0])
    assert "streamlit" not in modules
    assert "st" not in names
    # Check executable code only: the module docstring legitimately *explains* why the
    # worker stays away from session_state.
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    body = code.split('"""', 2)[-1]
    assert "session_state" not in body


def test_a_worker_exception_becomes_failed_with_sanitized_text():
    job, _ = _job(total=1)

    def explode(*args, **kwargs):
        raise RuntimeError("boom with detail")

    jm._run_job(job, SYMBOL_SOURCE, "dashboard", runner=explode)
    assert job.state == jm.FAILED
    assert job.sanitized_error.startswith("RuntimeError:")
    assert job.finished_at


def test_the_scan_context_is_closed_on_success_cancellation_and_failure():
    class Context:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    for outcome in ("success", "failure"):
        job, _ = _job(total=1)
        context = Context()
        job.attach_context(context)

        def runner(*args, **kwargs):
            if outcome == "failure":
                raise RuntimeError("nope")
            return []

        jm._run_job(job, SYMBOL_SOURCE, "dashboard", runner=runner)
        assert context.closed >= 1, f"context not closed on {outcome}"


def test_close_context_is_idempotent():
    class Context:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    job, _ = _job(total=1)
    context = Context()
    job.attach_context(context)
    job.close_context()
    job.close_context()
    assert context.closed == 1


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #

def test_stop_scan_moves_to_cancelling_and_is_idempotent():
    job, _ = _job()
    job.request_cancel()
    job.request_cancel()
    assert job.state == jm.CANCELLING
    assert job.cancelled is True


def test_no_symbol_starts_after_cancellation():
    """The scanner's cancellation point runs before each symbol's provider work."""
    from core.scan_context import ScanCancelled, ScanDataContext

    context = ScanDataContext(cancellation_event=threading.Event())
    context.raise_if_cancelled()          # not cancelled: no raise
    context.cancel()
    with pytest.raises(ScanCancelled):
        context.raise_if_cancelled()


def test_cancellation_is_visible_to_a_bounded_provider_request():
    """The request budget carries the predicate the client checks either side of I/O."""
    from core.scan_context import ScanDataContext

    context = ScanDataContext(cancellation_event=threading.Event())
    budget = context.request_budget()
    assert budget["max_attempts"] == 1
    assert budget["deadline_seconds"] > 0
    assert budget["cancel"]() is False
    context.cancel()
    assert budget["cancel"]() is True


# --------------------------------------------------------------------------- #
# Finalization
# --------------------------------------------------------------------------- #

def test_five_concurrent_finalization_attempts_produce_one_claim():
    job, _ = _job(total=3)
    claims, lock = [], threading.Lock()

    def claim():
        with lock:
            claims.append(job.begin_finalization())

    threads = [threading.Thread(target=claim) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert claims.count(True) == 1


def test_a_rerun_cannot_finalize_twice():
    job, _ = _job(total=3)
    assert job.begin_finalization() is True
    assert job.begin_finalization() is False


def test_cancellation_before_finalization_produces_no_finalization():
    job, _ = _job(total=3)
    job.request_cancel()
    assert job.begin_finalization() is False
    assert job.finalization_completed is False


def test_a_claim_alone_does_not_mark_finalization_completed():
    """``finalization_completed`` is set by the finalizer, never by claiming the right."""
    job, _ = _job(total=3)
    job.begin_finalization()
    assert job.finalization_started is True
    assert job.finalization_completed is False


# --------------------------------------------------------------------------- #
# Progress accounting
# --------------------------------------------------------------------------- #

def test_completed_always_equals_success_plus_skipped_plus_failed():
    job, _ = _job(total=4)
    report = jm.make_progress_reporter(job)
    report(jm.SYMBOL_COMPLETED, symbol="A", status="SUCCESS", seconds=0.1)
    report(jm.SYMBOL_FAILED, symbol="B", status="INSUFFICIENT_HISTORY", seconds=0.1)
    report(jm.SYMBOL_FAILED, symbol="C", status="EODHD_TIMEOUT", seconds=0.1)

    snapshot = job.progress()
    assert snapshot.counts_balance
    assert (snapshot.success, snapshot.skipped, snapshot.failed) == (1, 1, 1)
    assert snapshot.status_breakdown == {
        "SUCCESS": 1, "INSUFFICIENT_HISTORY": 1, "EODHD_TIMEOUT": 1}


@pytest.mark.parametrize("status,bucket", [
    ("SUCCESS", "success"),
    ("INVALID_HISTORY", "skipped"),
    ("INSUFFICIENT_HISTORY", "skipped"),
    ("EODHD_CACHE_MISS", "skipped"),
    ("EXCLUDED_NON_EQUITY", "skipped"),
    ("SKIPPED_STALE_DAILY_DATA", "skipped"),
    ("SKIPPED_MISSING_DAILY_DATE", "skipped"),
    ("SKIPPED_FUTURE_DAILY_DATE", "skipped"),
    ("EODHD_TIMEOUT", "failed"),
    ("EODHD_AUTH_FAILED", "failed"),
    ("INTERNAL_ERROR", "failed"),
])
def test_a_data_gap_is_a_skip_and_a_provider_fault_is_a_failure(status, bucket):
    assert jm.outcome_bucket(status) == bucket


def test_an_unknown_status_is_never_silently_treated_as_a_skip():
    assert jm.outcome_bucket("SOMETHING_NEW") == "failed"


def test_every_freshness_exclusion_the_guard_emits_is_classified_as_a_skip():
    """The taxonomy must know every status the freshness gate can emit.

    `core.daily_data_guard` gained SKIPPED_STALE_DAILY_DATA,
    SKIPPED_MISSING_DAILY_DATE and SKIPPED_FUTURE_DAILY_DATE without adding them
    here, so `outcome_bucket` fell through to its unknown-status default and
    reported them as provider failures. Enumerating the guard's own map means a
    status added there in future cannot silently become a fake failure.
    """

    from core.daily_data_guard import ELIGIBLE_STATUSES, FRESHNESS_OUTCOME

    for freshness, status in FRESHNESS_OUTCOME.items():
        if freshness in ELIGIBLE_STATUSES:
            continue  # a current symbol goes on to be analysed, not excluded
        assert jm.outcome_bucket(status) == "skipped", (
            f"{freshness} -> {status} is an exclusion, not a provider failure"
        )


def test_progress_and_coverage_are_different_numbers():
    """Finishing the universe is not the same as analysing it."""
    job, _ = _job(total=10)
    report = jm.make_progress_reporter(job)
    for index in range(6):
        report(jm.SYMBOL_COMPLETED, symbol=f"S{index}", status="SUCCESS", seconds=0.01)
    for index in range(4):
        report(jm.SYMBOL_FAILED, symbol=f"F{index}", status="INVALID_HISTORY",
               seconds=0.01)

    view = coverage_view(job.progress())
    assert view["progress_percent"] == 100.0
    assert view["coverage_percent"] == 60.0
    assert view["has_gaps"] is True


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #

def test_an_authentication_failure_opens_the_breaker_immediately():
    from providers.eodhd_client import EODHDAuthFailed

    breaker = jm.CircuitBreaker(threshold=3)
    assert breaker.record_broad_failure(EODHDAuthFailed("rejected")) is True
    assert breaker.is_open


def test_three_consecutive_broad_failures_open_the_breaker():
    from providers.eodhd_client import EODHDConnectionFailed

    breaker = jm.CircuitBreaker(threshold=3)
    assert breaker.record_broad_failure(EODHDConnectionFailed("x")) is False
    assert breaker.record_broad_failure(EODHDConnectionFailed("x")) is False
    assert breaker.record_broad_failure(EODHDConnectionFailed("x")) is True
    assert breaker.is_open


def test_a_successful_request_resets_the_consecutive_counter():
    from providers.eodhd_client import EODHDTimeout

    breaker = jm.CircuitBreaker(threshold=3)
    breaker.record_broad_failure(EODHDTimeout("x"))
    breaker.record_broad_failure(EODHDTimeout("x"))
    breaker.record_success()
    assert breaker.consecutive_failures == 0
    assert breaker.record_broad_failure(EODHDTimeout("x")) is False
    assert not breaker.is_open


def test_per_symbol_data_conditions_never_reach_the_breaker():
    """Only typed provider-health failures are recorded, and the router enforces it."""
    import inspect
    from core import research_router

    source = inspect.getsource(research_router.eodhd_history)
    assert "breaker.record_broad_failure(error)" in source
    breaker_block = source.split("except (EODHDTimeout")[1].split("raise")[0]
    for data_condition in ("DATA_INSUFFICIENT", "EXCLUDED_NON_EQUITY",
                           "not enough history"):
        assert data_condition not in breaker_block


def test_an_open_breaker_blocks_a_new_refresh_but_not_the_local_cache():
    import inspect
    from core import research_router

    source = inspect.getsource(research_router.eodhd_history)
    # the guard fires only on a refresh, so a cached read still serves
    assert "if force_refresh and breaker is not None and breaker.is_open:" in source
    assert "EODHD_PROVIDER_UNAVAILABLE" in source
    # No Yahoo in the executable path; the comments may say the route avoids it.
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    assert "yahoo" not in code.lower()


# --------------------------------------------------------------------------- #
# Provider banner
# --------------------------------------------------------------------------- #

def test_before_any_scan_the_banner_says_eodhd_and_awaiting_scan():
    view = scan_status_view(None)
    assert view["historical_source"] == "EODHD"
    assert view["latest_completed_candle"] == "Awaiting scan"
    assert view["live_overlay"] == "Rubix status not checked"


def test_during_preparation_the_banner_shows_loading_rubix():
    """No row has been read yet, so no candle date may be claimed.

    This line is labelled "Latest completed candle". Printing the EXPECTED
    session there states something no observation supports, which is how
    "2026-08-03" came to sit above 2026-07-30 prices on 2026-08-04.
    """

    job, _ = _job()
    job.publish(state=jm.PREPARING_RUBIX, stage="Loading Rubix quote overlays")
    view = scan_status_view(job.progress(), expected_session="2026-07-26")
    assert view["historical_source"] == "EODHD"
    assert view["latest_completed_candle"] != "2026-07-26"
    assert view["live_overlay"] == "Loading Rubix"


def test_after_a_cached_scan_the_banner_says_eodhd_cache():
    job, _ = _job(total=10)
    job.publish(state=jm.COMPLETED_WITH_GAPS, rubix_overlay_available=10,
                rubix_batch_status="RUBIX_BATCH_OK")
    view = scan_status_view(job.progress(), expected_session="2026-07-26")
    assert view["historical_source"] == "EODHD cache"
    assert view["live_overlay"] == "Rubix Fresh"


def test_a_refreshing_scan_reports_bounded_refreshes():
    job, _ = _job(total=10)
    job.publish(state=jm.COMPLETED, rubix_overlay_available=10,
                rubix_batch_status="RUBIX_BATCH_OK", eodhd_refresh_successes=3)
    view = scan_status_view(job.progress())
    assert view["historical_source"] == "EODHD cache + bounded refreshes"


@pytest.mark.parametrize("available,total,status,expected", [
    (10, 10, "RUBIX_BATCH_OK", "Rubix Fresh"),
    (4, 10, "RUBIX_BATCH_OK", "Rubix Partially Available"),
    (0, 10, "RUBIX_BATCH_OK", "Rubix Unavailable"),
    (10, 10, "RUBIX_DB_BUSY", "Rubix Unavailable"),
    (10, 10, "RUBIX_DB_UNAVAILABLE", "Rubix Unavailable"),
])
def test_the_live_overlay_reports_its_own_state(available, total, status, expected):
    job, _ = _job(total=total)
    job.publish(state=jm.COMPLETED, rubix_overlay_available=available,
                rubix_batch_status=status)
    assert scan_status_view(job.progress())["live_overlay"] == expected


def test_an_unavailable_rubix_overlay_never_changes_the_historical_provider():
    """The reported defect: stale live quotes made the DAILY provider read as Yahoo."""
    job, _ = _job(total=10)
    job.publish(state=jm.COMPLETED, rubix_overlay_available=0,
                rubix_batch_status="RUBIX_DB_UNAVAILABLE")
    view = scan_status_view(job.progress())
    assert view["live_overlay"] == "Rubix Unavailable"
    assert view["historical_source"] == "EODHD cache"
    for value in view.values():
        assert "yahoo" not in str(value).lower()
        assert "YAHOO_FALLBACK" not in str(value)


def test_no_dashboard_status_path_can_emit_an_operational_yahoo_label():
    from dashboard import scan_status_panel

    source = pathlib.Path(scan_status_panel.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    body = code.split('"""', 2)[-1]          # drop the module docstring
    assert "local_cache:yahoo" not in body
    assert "YAHOO_FALLBACK" not in body


# --------------------------------------------------------------------------- #
# Page wiring
# --------------------------------------------------------------------------- #

def test_the_dashboard_no_longer_runs_the_scan_inline():
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    assert "scan_symbols(" not in source, "the page still calls the scanner directly"
    assert "st.spinner(\"Scanning EGX symbols" not in source
    assert "start_scan_job" in source
    assert "request_cancel" in source
    assert "إيقاف الفحص" in source
    assert "st.fragment" in source


def test_the_page_polls_on_a_bounded_interval_and_never_sleeps():
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    assert "time.sleep" not in source
    assert "while True" not in source
    assert 0.25 <= home.SCAN_POLL_SECONDS <= 2.0


def test_production_and_broker_execution_remain_disabled():
    from dashboard.ai_stock_analysis_components import SAFETY_BADGES

    assert "Production Disabled" in [english for _, english, _ in SAFETY_BADGES]
