"""Regression tests for the terminal market-scan result handoff.

The observed defect: a scan finished ``COMPLETED_WITH_GAPS`` (265/265, 194 successful,
71 skipped, 73.2% coverage) and the Dashboard stayed on the progress monitor — banner
still reading "Awaiting scan" / "Loading Rubix", Stop Scan still offered, and none of
the scanner's rows rendered.

Cause: ``st.fragment(run_every=…)`` re-runs ONLY its own body. The surrounding script
had already captured ``active = job.is_active`` as True and returned early, so when the
worker reached a terminal state nothing released the page. These tests pin the release,
its idempotency, and the fact that presentation re-runs no analysis.

Fixtures only — no real scan, provider call, archive or finalization.
"""

from __future__ import annotations

import pathlib

import pytest

from core import scan_job_manager as jm
from core.symbols import SYMBOL_SOURCE
from dashboard.scan_status_panel import coverage_view, scan_status_view


OBSERVED = {"total": 265, "success": 194, "skipped": 71, "failed": 0}


@pytest.fixture(autouse=True)
def clean_registry():
    jm.REGISTRY.clear()
    yield
    jm.REGISTRY.clear()


class _FakeResults(list):
    """Stands in for ScanResults: list-compatible with coverage/failure evidence."""

    def __init__(self, rows, status="COMPLETED_WITH_GAPS"):
        super().__init__(rows)
        self.status = status
        self.coverage = []
        self.failures = []


def _terminal_job(state=jm.COMPLETED_WITH_GAPS, rows=None, with_result=True):
    """A job in a terminal state carrying the counts from the observed defect."""
    job, _ = jm.start_scan_job(symbols=["S"] * OBSERVED["total"], autostart=False)
    breakdown = {"SUCCESS": OBSERVED["success"], "INVALID_HISTORY": 31,
                 "EODHD_CACHE_MISS": 20, "INSUFFICIENT_HISTORY": 19,
                 "EXCLUDED_NON_EQUITY": 1}
    job.publish(state=state, stage="Scan complete",
                completed=OBSERVED["total"], success=OBSERVED["success"],
                skipped=OBSERVED["skipped"], failed=OBSERVED["failed"],
                rubix_overlay_available=OBSERVED["total"],
                rubix_batch_status="RUBIX_BATCH_OK",
                status_breakdown=breakdown)
    job.finalization_started = True
    job.finalization_completed = True
    if with_result:
        job.final_result = _FakeResults(
            rows if rows is not None else [{"Ticker": f"S{i}.CA"} for i in range(3)],
            status=state)
    return job


# --------------------------------------------------------------------------- #
# The state machine underneath the UI
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [jm.COMPLETED, jm.COMPLETED_WITH_GAPS,
                                   jm.CANCELLED, jm.FAILED])
def test_every_terminal_state_reports_the_job_as_inactive(state):
    """``is_active`` is what the page uses to choose Stop-vs-Run; it must be honest."""
    job = _terminal_job(state=state)
    assert job.is_active is False
    assert state in jm.TERMINAL_STATES


@pytest.mark.parametrize("state", [jm.STARTING, jm.PREPARING_RUBIX, jm.SCANNING,
                                   jm.FINALIZING, jm.CANCELLING])
def test_every_active_state_still_reports_the_job_as_active(state):
    job, _ = jm.start_scan_job(symbols=["S"] * 5, autostart=False)
    job.publish(state=state)
    assert job.is_active is True
    assert state in jm.ACTIVE_STATES


def test_the_terminal_job_survives_in_the_registry_for_a_later_observer():
    """A refresh or a second browser must still find the finished job."""
    job = _terminal_job()
    key = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    again = jm.REGISTRY.get(key)
    assert again is job
    assert again.progress().state == jm.COMPLETED_WITH_GAPS
    assert again.final_result is not None


def test_a_terminal_job_does_not_block_a_later_scan():
    _terminal_job()
    fresh, created = jm.start_scan_job(symbols=["S"] * 5, autostart=False)
    assert created is True
    assert fresh.progress().state == jm.STARTING


# --------------------------------------------------------------------------- #
# The handoff itself
# --------------------------------------------------------------------------- #

def test_the_completed_result_is_published_exactly_once(monkeypatch):
    from dashboard import home

    job = _terminal_job()
    session = _Session()
    calls = []

    class _Advisory:
        def analyze(self, results):
            calls.append(results)
            return {"daily_report": "report"}

    monkeypatch.setattr(home, "st", _FakeStreamlit(session))
    monkeypatch.setattr(home, "DecisionSupportService", lambda: _Advisory())
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")

    home._adopt_finished_job(job)
    home._adopt_finished_job(job)      # rerun
    home._adopt_finished_job(job)      # refresh
    home._adopt_finished_job(job)      # second observer

    assert session[home.TERMINAL_CONSUMED_KEY] == job.scan_id
    assert session["results"] is job.final_result
    assert session["live_scan_completed"] is True
    assert len(calls) == 1, "the decision-support snapshot ran more than once"


def test_a_cancelled_run_is_never_published_as_a_recorded_session(monkeypatch):
    from dashboard import home

    job = _terminal_job(state=jm.CANCELLED, rows=[{"Ticker": "PARTIAL.CA"}])
    job.request_cancel()
    session = _Session()
    calls = []
    monkeypatch.setattr(home, "st", _FakeStreamlit(session))
    monkeypatch.setattr(home, "DecisionSupportService",
                        lambda: type("A", (), {"analyze": lambda s, r: calls.append(r)})())
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")

    home._adopt_finished_job(job)
    assert session["live_scan_completed"] is False
    assert calls == [], "a cancelled run triggered downstream completed-run work"
    assert session["results"] is job.final_result   # partial diagnostics retained


def test_a_job_without_a_result_publishes_nothing(monkeypatch):
    from dashboard import home

    job = _terminal_job(state=jm.FAILED, with_result=False)
    session = _Session()
    monkeypatch.setattr(home, "st", _FakeStreamlit(session))
    home._adopt_finished_job(job)
    assert "results" not in session
    assert session[home.TERMINAL_CONSUMED_KEY] == job.scan_id


def test_the_handoff_performs_no_analysis_provider_or_archive_work():
    """Presentation must not re-run the scan, contact a provider, or finalize again."""
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    for forbidden in ("scan_symbols(", "load_history(", "process_scan(",
                      "DatasetArchive(", "finalize("):
        assert forbidden not in code, f"the page performs {forbidden}"


# --------------------------------------------------------------------------- #
# Page wiring: the release, the buttons, the ordering
# --------------------------------------------------------------------------- #

def test_the_fragment_releases_the_page_once_on_the_terminal_transition():
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    assert 'st.rerun(scope="app")' in source, "the fragment never releases the page"
    assert home.TERMINAL_RERUN_KEY in source
    # guarded by scan_id so ticks/reruns/observers cannot repeat it
    assert f"st.session_state.get({home.TERMINAL_RERUN_KEY!s}" in source or \
        "st.session_state.get(TERMINAL_RERUN_KEY) != job.scan_id" in source


def test_results_are_published_before_the_page_returns_for_active_jobs():
    """The early return must be reachable only while the job is genuinely active."""
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    body = source.split("if job is not None:")[1].split("archive_warning")[0]
    assert "if not active:" in body
    assert "_adopt_finished_job(job)" in body
    assert "_render_terminal_summary(job)" in body
    # the adoption line precedes the early return
    assert body.index("_adopt_finished_job(job)") < body.index("return")


def test_stop_is_offered_and_run_disabled_only_while_active():
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    # The Run button is disabled while the job is active AND through the window
    # where it is terminal but has not published its result yet - clicking then
    # is what started a duplicate concurrent scan.
    assert "disabled=active or awaiting_result or scan_completed" in source
    assert "if active:" in source and "Stop Scan" in source
    # Stop lives in the active branch only
    status_block = source.split("with status_col:")[1].split("if job is not None:")[0]
    assert "Stop Scan" in status_block.split("else:")[0]
    assert "Stop Scan" not in status_block.split("else:")[1]


def test_terminal_progress_is_collapsed_and_does_not_replace_results():
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    assert 'st.expander(f"Scan progress' in source
    assert "expanded=False" in source


@pytest.mark.parametrize("state,headline", [
    (jm.COMPLETED, "Scan complete"),
    (jm.COMPLETED_WITH_GAPS, "Scan complete with coverage gaps"),
    (jm.CANCELLED, "Scan cancelled — partial diagnostics only"),
    (jm.FAILED, "Scan failed"),
])
def test_each_terminal_state_has_an_honest_headline(state, headline):
    from dashboard import home

    assert home.TERMINAL_HEADLINES[state][1] == headline


# --------------------------------------------------------------------------- #
# The banner after completion
# --------------------------------------------------------------------------- #

def test_the_completed_banner_no_longer_says_awaiting_scan_or_loading_rubix():
    """The exact strings visible in the defect screenshot must be gone."""
    job = _terminal_job()
    view = scan_status_view(job.progress(), expected_session="2026-07-27",
                            result_metadata={"latest_completed_candle": "2026-07-27"})
    assert view["latest_completed_candle"] == "2026-07-27"
    assert view["latest_completed_candle"] != "Awaiting scan"
    assert view["live_overlay"] != "Loading Rubix"
    assert view["live_overlay"] == "Rubix Fresh"
    assert view["historical_source"] == "EODHD cache"


def test_a_refreshing_scan_reports_bounded_refreshes_in_the_final_banner():
    job = _terminal_job()
    job.publish(eodhd_refresh_successes=4)
    view = scan_status_view(job.progress())
    assert view["historical_source"] == "EODHD cache + bounded refreshes"


def test_the_final_banner_never_names_yahoo():
    job = _terminal_job()
    job.publish(rubix_overlay_available=0, rubix_batch_status="RUBIX_DB_UNAVAILABLE")
    view = scan_status_view(job.progress())
    assert view["live_overlay"] == "Rubix Unavailable"
    assert view["historical_source"] == "EODHD cache"
    for value in view.values():
        assert "yahoo" not in str(value).lower()
        assert "YAHOO_FALLBACK" not in str(value)


# --------------------------------------------------------------------------- #
# Counts the completed UI renders
# --------------------------------------------------------------------------- #

def test_the_observed_scan_counts_render_correctly():
    job = _terminal_job()
    snapshot = job.progress()
    view = coverage_view(snapshot)

    assert (snapshot.completed, snapshot.total) == (265, 265)
    assert (snapshot.success, snapshot.skipped, snapshot.failed) == (194, 71, 0)
    assert view["coverage_percent"] == 73.2
    assert view["progress_percent"] == 100.0
    assert view["has_gaps"] is True
    assert snapshot.counts_balance
    assert sum(snapshot.status_breakdown.values()) == 265


def test_the_typed_breakdown_survives_into_the_terminal_snapshot():
    job = _terminal_job()
    breakdown = job.progress().status_breakdown
    assert breakdown["SUCCESS"] == 194
    assert breakdown["INVALID_HISTORY"] == 31
    assert breakdown.get("INTERNAL_ERROR", 0) == 0


def test_the_typed_breakdown_is_rendered_as_readable_text():
    """Streamlit draws dataframes to a canvas, where the reasons for the 71 skipped
    symbols would be invisible to a reader scanning the page — and unassertable."""
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    summary = source.split("def _render_terminal_summary")[1].split("\ndef ")[0]
    assert "st.markdown(" in summary
    assert "st.dataframe(" not in summary, "typed reasons hidden in a canvas grid"
    assert "status_breakdown" in summary


def test_the_completed_page_references_no_undefined_names():
    """``show_dashboard`` renders top to bottom in one pass: a name that only existed
    on the old active-scan path raises NameError and blanks every result below it."""
    import ast
    import builtins
    from dashboard import home

    tree = ast.parse(pathlib.Path(home.__file__).read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "show_dashboard")

    # Every binding form: plain and tuple-unpacking assignment, ``for`` targets,
    # comprehensions and ``with … as`` all store through a Name in Store context.
    assigned = {node.id for node in ast.walk(function)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    assigned |= {name.asname or name.name.split(".")[0]
                 for node in ast.walk(function)
                 if isinstance(node, (ast.Import, ast.ImportFrom)) for name in node.names}
    assigned |= {node.name for node in ast.walk(function)
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assigned |= {argument.arg for node in ast.walk(function)
                 if isinstance(node, (ast.FunctionDef, ast.Lambda))
                 for argument in node.args.args + node.args.kwonlyargs}
    assigned |= {handler.name for node in ast.walk(function)
                 if isinstance(node, ast.Try) for handler in node.handlers if handler.name}

    known = assigned | set(vars(home)) | set(vars(builtins))
    unresolved = sorted({node.id for node in ast.walk(function)
                         if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                         and node.id not in known})
    assert not unresolved, f"show_dashboard reads undefined names: {unresolved}"


def test_the_live_quote_tile_reads_the_rows_the_scan_produced():
    """It used to read a provider summary computed only on the pre-scan path."""
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    assert "provider_summary" not in source
    tile = source.split('"Live quote source"')[0].rsplit("context[2]", 1)[0]
    assert "LiveProvider" in tile.rsplit("live_providers", 2)[0] or \
        "LiveProvider" in source.split('"Live quote source"')[0][-400:]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class _FakeStreamlit:
    """Minimal recording stand-in; only what ``_adopt_finished_job`` touches.

    ``session_state`` IS the mapping the test inspects, so writes are observable.
    """

    def __init__(self, session):
        object.__setattr__(self, "session_state", session)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Session(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        self[name] = value
