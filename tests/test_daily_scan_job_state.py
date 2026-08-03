"""Daily Market Scan: workspace identity, single job, and result adoption.

The observed defect (2026-08-03): one click started a scan that ran to
completion and produced 241/194/47, the page never showed it, and a second
click 93 seconds later started a SECOND concurrent scan of the same universe in
the same process. Both archived — RUN_20260803_223656 and RUN_20260803_223829 —
with identical results.

Two independent causes, both pinned here:

* ``workspace_key_for`` was not deterministic. It resolved a relative universe
  path against the process working directory and silently substituted
  ``"unknown"`` for any configuration read failure, so one render could look up
  a different key than the running job was registered under.
* ``_adopt_finished_job`` marked a job consumed BEFORE checking
  ``final_result``. One observer arriving in the window between the worker's
  ``final_result = result`` and its terminal ``publish`` burned the idempotency
  key while the result was still None, and the finished scan could never be
  adopted afterwards.

No real scan, provider call, archive, sleep or production write happens here.
The clock and the worker are injected; PowerShell, Rubix and scheduled tasks are
never touched.
"""

from __future__ import annotations

import ast
import os
import pathlib
import threading

import pytest

from core import scan_job_manager as jm
from core.symbols import SYMBOL_SOURCE


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The two duplicate runs this work exists to prevent a repeat of. They are
#: historical evidence and are never modified by any test.
DUPLICATE_RUNS = ("RUN_20260803_223656", "RUN_20260803_223829")
OBSERVED = {"total": 241, "success": 194, "failed": 47}


@pytest.fixture(autouse=True)
def clean_registry():
    jm.REGISTRY.clear()
    yield
    jm.REGISTRY.clear()


class _Session(dict):
    """Streamlit session_state stand-in: a dict with attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        self[name] = value


class _FakeStreamlit:
    """Only what the adoption path touches. Renders nothing, sleeps never."""

    def __init__(self, session):
        self.session_state = session
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))


class _Results(list):
    def __init__(self, rows, status="COMPLETED"):
        super().__init__(rows)
        self.status = status
        self.coverage = []
        self.failures = []


def _rows(count=3):
    return [{"Ticker": f"S{index}.CA"} for index in range(count)]


def _job(state=jm.COMPLETED, result=_Results, rows=None, symbols=5):
    job, _ = jm.start_scan_job(symbols=["S"] * symbols, autostart=False)
    if result is not None:
        job.final_result = result(rows if rows is not None else _rows())
    job.publish(state=state, stage="Scan complete", completed=symbols)
    return job


# =========================================================================== #
# WORKSPACE KEY
# =========================================================================== #


def test_the_key_is_identical_from_any_working_directory(tmp_path):
    """The defect: a relative universe path resolved against the CWD."""

    original = os.getcwd()
    try:
        first = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
        os.chdir(tmp_path)
        second = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
        os.chdir(REPO_ROOT)
        third = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    finally:
        os.chdir(original)
    assert first == second == third


def test_a_relative_symbol_source_resolves_from_the_repository_root():
    workspace = jm.resolve_workspace("dashboard", SYMBOL_SOURCE)
    assert pathlib.Path(workspace.source_path).is_absolute()
    assert pathlib.Path(workspace.source_path).is_relative_to(REPO_ROOT)
    assert jm.resolve_symbol_source(SYMBOL_SOURCE) == (REPO_ROOT / SYMBOL_SOURCE)


def test_physical_aliases_of_one_file_produce_one_identity(tmp_path):
    """D: is a junction onto F:, so both spellings are one universe file."""

    target = tmp_path / "real"
    target.mkdir()
    universe = target / "egx_universe.csv"
    universe.write_text("Ticker\nCOMI.CA\n", encoding="utf-8")

    alias = tmp_path / "alias"
    if os.name != "nt":
        pytest.skip("junctions are a Windows mechanism")
    import subprocess

    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                          capture_output=True, text=True)
    if made.returncode != 0 or not alias.exists():
        pytest.skip("junction creation unavailable on this system")

    direct = jm.workspace_key_for("dashboard", universe, provider_mode="rubix")
    through_alias = jm.workspace_key_for("dashboard", alias / "egx_universe.csv",
                                         provider_mode="rubix")
    assert direct == through_alias


def test_two_genuinely_different_universes_do_not_share_a_key(tmp_path):
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    for path in (first, second):
        path.write_text("Ticker\nCOMI.CA\n", encoding="utf-8")   # identical bytes
    assert (jm.workspace_key_for("dashboard", first, provider_mode="rubix")
            != jm.workspace_key_for("dashboard", second, provider_mode="rubix"))


def test_a_configuration_failure_never_becomes_unknown(monkeypatch):
    """The old code swallowed this and produced a second workspace key."""

    import config.settings_manager as settings_module

    class _Broken:
        @property
        def data(self):
            raise OSError("settings file locked")

    monkeypatch.setattr(settings_module, "settings", _Broken())
    with pytest.raises(jm.WorkspaceConfigurationError) as raised:
        jm.resolve_provider_mode("dashboard")
    assert "unknown" not in str(raised.value).lower()
    assert "settings file locked" in str(raised.value)


def test_an_unconfigured_provider_is_refused(monkeypatch):
    import config.settings_manager as settings_module

    monkeypatch.setattr(settings_module, "settings",
                        type("S", (), {"data": {"dashboard_provider": "  "}})())
    with pytest.raises(jm.WorkspaceConfigurationError):
        jm.resolve_provider_mode("dashboard")


def test_an_unknown_purpose_is_refused():
    with pytest.raises(jm.WorkspaceConfigurationError):
        jm.resolve_provider_mode("not_a_purpose")


def test_a_deliberate_provider_change_produces_a_new_key():
    rubix = jm.workspace_key_for("dashboard", SYMBOL_SOURCE, provider_mode="rubix")
    eodhd = jm.workspace_key_for("dashboard", SYMBOL_SOURCE, provider_mode="eodhd")
    assert rubix != eodhd


def test_a_scan_cannot_start_in_an_unidentifiable_workspace(monkeypatch):
    import config.settings_manager as settings_module

    class _Broken:
        @property
        def data(self):
            raise OSError("unreadable")

    monkeypatch.setattr(settings_module, "settings", _Broken())
    with pytest.raises(jm.WorkspaceConfigurationError):
        jm.start_scan_job(SYMBOL_SOURCE, "dashboard", symbols=["A"], autostart=False)
    assert jm.REGISTRY.size() == 0, "a job was created for an unidentified workspace"


def test_the_key_carries_no_volatile_value():
    key = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    assert key == jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    for forbidden in ("token", "secret", "session", "widget", "tab"):
        assert forbidden not in key.lower()


# =========================================================================== #
# CONCURRENCY
# =========================================================================== #


def test_two_simultaneous_creates_return_one_job():
    key = "workspace::concurrency"
    seen, barrier = [], threading.Barrier(8)

    def create():
        barrier.wait()
        seen.append(jm.REGISTRY.create_or_get_active_job(key, total=3))

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == 8
    assert sum(1 for _, created in seen if created) == 1, "more than one job created"
    assert len({job.scan_id for job, _ in seen}) == 1


def test_exactly_one_worker_may_be_claimed():
    job, _ = jm.start_scan_job(symbols=["A"], autostart=False)
    claims, barrier = [], threading.Barrier(6)

    def claim():
        barrier.wait()
        claims.append(job.claim_worker())

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for claimed in claims if claimed) == 1


def test_a_second_click_attaches_instead_of_starting_another_scan():
    started = []

    def runner(*args, **kwargs):
        started.append(1)
        return _Results(_rows())

    first, created_first = jm.start_scan_job(SYMBOL_SOURCE, "dashboard",
                                             symbols=["A"], autostart=False,
                                             runner=runner)
    second, created_second = jm.start_scan_job(SYMBOL_SOURCE, "dashboard",
                                               symbols=["A"], autostart=False,
                                               runner=runner)
    assert created_first is True
    assert created_second is False
    assert second is first
    assert jm.REGISTRY.active_count() == 1


def test_start_scan_job_starts_exactly_one_worker():
    """Two rapid clicks, one worker — enforced by the service, not the button.

    The runner is injected and returns immediately, so no scanner, provider or
    archive is touched and no thread outlives the test.
    """

    runs = []
    entered = threading.Event()
    release = threading.Event()

    def runner(*args, **kwargs):
        runs.append(threading.current_thread().name)
        entered.set()
        # Hold the job ACTIVE until the second click has been made. Without
        # this the worker can finish first, the job goes terminal, and a second
        # job is then created legitimately - which would test nothing.
        release.wait(timeout=10)
        return _Results(_rows())

    first, created_first = jm.start_scan_job(
        SYMBOL_SOURCE, "dashboard", symbols=["A"], runner=runner, autostart=True)
    assert entered.wait(timeout=10), "the worker never started"
    assert first.is_active, "the job should still own the workspace"

    second, created_second = jm.start_scan_job(
        SYMBOL_SOURCE, "dashboard", symbols=["A"], runner=runner, autostart=True)

    assert created_first is True and created_second is False
    assert second is first

    release.set()
    first._worker.join(timeout=10)
    assert not first._worker.is_alive(), "a worker thread outlived the test"
    assert len(runs) == 1, f"{len(runs)} workers ran for one scan"


def test_the_worker_claim_guards_the_autostart_path():
    """Defence in depth, pinned structurally.

    The registry lock already means only one caller sees created=True, so no
    behavioural test can distinguish this guard today. It exists so a future
    caller that starts a worker outside that lock still cannot start a second
    one, and the wiring is asserted rather than left to be quietly removed.
    """

    import inspect

    source = inspect.getsource(jm.start_scan_job)
    autostart = source.split("if autostart")[1].split("return")[0]
    assert "job.claim_worker()" in source.split("if autostart")[0] + "if autostart" + autostart
    assert "threading.Thread(" in autostart
    assert source.index("claim_worker") < source.index("threading.Thread(")


def test_a_claimed_job_cannot_be_started_a_second_time():
    job, _ = jm.start_scan_job(symbols=["A"], autostart=False)
    assert job.claim_worker() is True
    assert job.claim_worker() is False
    assert job.worker_started is True


def test_different_workspaces_run_independently():
    first, _ = jm.REGISTRY.create_or_get_active_job("workspace::one", total=1)
    second, created = jm.REGISTRY.create_or_get_active_job("workspace::two", total=1)
    assert created is True
    assert second is not first
    assert jm.REGISTRY.active_count() == 2


def test_a_completed_job_does_not_block_a_later_scan():
    finished = _job()
    assert finished.claim_result("test") is True
    fresh, created = jm.start_scan_job(SYMBOL_SOURCE, "dashboard",
                                       symbols=["A"], autostart=False)
    assert created is True
    assert fresh is not finished


@pytest.mark.parametrize("state", [jm.CANCELLED, jm.FAILED])
def test_a_failed_or_cancelled_job_reports_itself_honestly(state):
    job = _job(state=state, result=None if state == jm.FAILED else _Results)
    assert job.is_active is False
    assert job.progress().state == state
    if state == jm.FAILED:
        assert job.result_pending is False, "a failed run owes no result"


# =========================================================================== #
# SURVIVING STREAMLIT MODULE RELOAD
# =========================================================================== #


def _evict_and_reimport():
    """Exactly what Streamlit does to a changed local module.

    ``LocalSourcesWatcher`` pops changed modules out of ``sys.modules`` at the
    start of a script run, so the next import rebuilds them from source.
    """

    import importlib
    import sys

    for name in [n for n in sys.modules if n.startswith("core.scan_job_manager")]:
        sys.modules.pop(name)
    return importlib.import_module("core.scan_job_manager")


def test_the_registry_survives_a_streamlit_module_reload():
    """Observed live: a reload orphaned a running scan from the page.

    Streamlit evicted core.scan_job_manager mid-session, the re-import built a
    fresh registry, and the worker thread carried on updating the old one. The
    page then found nothing, re-enabled the Run button, and a second click
    could start a duplicate concurrent scan - the original incident arriving
    through a different door.
    """

    job, created = jm.REGISTRY.create_or_get_active_job("workspace::reload", total=3)
    assert created is True
    registry_id = id(jm.REGISTRY)

    reloaded = _evict_and_reimport()
    try:
        assert id(reloaded.REGISTRY) == registry_id, (
            "the reloaded module built a second registry")
        found = reloaded.REGISTRY.get("workspace::reload")
        assert found is not None, "a running job became invisible after reload"
        assert found.scan_id == job.scan_id
        assert found.is_active
    finally:
        reloaded.REGISTRY.clear()


def test_a_reload_does_not_permit_a_duplicate_scan():
    workspace = jm.workspace_key_for("dashboard", SYMBOL_SOURCE)
    running, created = jm.REGISTRY.create_or_get_active_job(workspace, total=5)
    assert created is True

    reloaded = _evict_and_reimport()
    try:
        again, created_again = reloaded.REGISTRY.create_or_get_active_job(
            workspace, total=5)
        assert created_again is False, "a reload allowed a second concurrent scan"
        assert again.scan_id == running.scan_id
        assert reloaded.REGISTRY.active_count() == 1
    finally:
        reloaded.REGISTRY.clear()


def test_the_registry_is_pinned_to_the_process_not_the_module():
    import sys

    assert getattr(sys, jm._REGISTRY_SLOT, None) is jm.REGISTRY
    # A second call must never mint a replacement.
    assert jm._process_registry() is jm.REGISTRY


# =========================================================================== #
# SESSION STATE AND RECOVERY
# =========================================================================== #


def _page(monkeypatch, session=None):
    from dashboard import home

    session = session if session is not None else _Session()
    fake = _FakeStreamlit(session)
    monkeypatch.setattr(home, "st", fake)
    return home, session, fake


def test_the_workspace_key_survives_reruns(monkeypatch):
    home, session, _ = _page(monkeypatch)
    first, error = home.resolve_page_workspace()
    assert error is None
    stored = session[home.WORKSPACE_KEY]
    for _ in range(5):                       # further reruns
        again, error = home.resolve_page_workspace()
        assert error is None
        assert again.key == first.key
        assert session[home.WORKSPACE_KEY] == stored


def test_a_configuration_failure_is_surfaced_not_papered_over(monkeypatch):
    home, _, _ = _page(monkeypatch)
    import config.settings_manager as settings_module

    class _Broken:
        @property
        def data(self):
            raise OSError("settings unavailable")

    monkeypatch.setattr(settings_module, "settings", _Broken())
    workspace, error = home.resolve_page_workspace()
    assert workspace is None
    assert error and "settings unavailable" in error


def test_an_active_job_is_rediscovered_after_a_rerun(monkeypatch):
    home, session, _ = _page(monkeypatch)
    workspace, _ = home.resolve_page_workspace()
    running, _ = jm.REGISTRY.create_or_get_active_job(workspace.key, total=5)

    # Session state wiped, as after a browser refresh.
    session.clear()
    home.resolve_page_workspace()
    found = home.discover_job(session[home.WORKSPACE_KEY])
    assert found is running
    assert found.is_active


def test_a_finished_unconsumed_job_is_rediscovered_after_a_refresh(monkeypatch):
    home, session, _ = _page(monkeypatch)
    workspace, _ = home.resolve_page_workspace()
    job, _ = jm.REGISTRY.create_or_get_active_job(workspace.key, total=3)
    job.final_result = _Results(_rows())
    job.publish(state=jm.COMPLETED, stage="Scan complete", completed=3)

    session.clear()
    home.resolve_page_workspace()
    found = home.discover_job(session[home.WORKSPACE_KEY])
    assert found is job
    assert jm.REGISTRY.unconsumed_job(workspace.key) is job
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")
    monkeypatch.setattr(home, "DecisionSupportService",
                        lambda: type("A", (), {"analyze": lambda s, r: {}})())
    assert home._adopt_finished_job(found) is True
    assert session["results"] is job.final_result


def test_session_state_loss_does_not_start_a_duplicate_scan(monkeypatch):
    home, session, _ = _page(monkeypatch)
    workspace, _ = home.resolve_page_workspace()
    running, _ = jm.REGISTRY.create_or_get_active_job(workspace.key, total=5)

    session.clear()                                  # total session-state loss
    home.resolve_page_workspace()
    home.discover_job(session[home.WORKSPACE_KEY])
    again, created = jm.start_scan_job(SYMBOL_SOURCE, "dashboard",
                                       symbols=["A"] * 5, autostart=False)
    assert created is False
    assert again is running
    assert jm.REGISTRY.active_count() == 1


# =========================================================================== #
# RESULT ADOPTION
# =========================================================================== #


def test_a_terminal_job_without_its_result_yet_is_not_consumed(monkeypatch):
    """The exact race that lost a completed scan."""

    home, session, _ = _page(monkeypatch)
    job = _job(state=jm.COMPLETED, result=None)      # result not published yet
    assert job.result_pending is True

    assert home._adopt_finished_job(job) is False
    assert job.result_consumed is False
    assert "results" not in session


def test_the_registry_refuses_to_claim_a_result_that_is_not_there():
    """The invariant lives in the job, not only in the page that calls it.

    Tested directly so a future caller cannot reintroduce the defect by
    skipping the page's guard.
    """

    job = _job(state=jm.COMPLETED, result=None)
    assert job.claim_result("observer") is False
    assert job.result_consumed is False

    job.final_result = _Results(_rows())
    assert job.claim_result("observer") is True
    assert job.result_consumed is True
    assert job.claim_result("another") is False, "claimed twice"


def test_an_active_job_refuses_the_claim_outright():
    job, _ = jm.start_scan_job(symbols=["A"] * 3, autostart=False)
    job.final_result = _Results(_rows())          # published early
    assert job.is_active
    assert job.claim_result("observer") is False


def test_only_one_of_many_observers_claims_the_result():
    job = _job()
    claims, barrier = [], threading.Barrier(8)

    def claim():
        barrier.wait()
        claims.append(job.claim_result("observer"))

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for claimed in claims if claimed) == 1


def test_a_result_arriving_later_is_still_adopted(monkeypatch):
    home, session, _ = _page(monkeypatch)
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")
    monkeypatch.setattr(home, "DecisionSupportService",
                        lambda: type("A", (), {"analyze": lambda s, r: {}})())

    job = _job(state=jm.COMPLETED, result=None)
    for _ in range(3):                               # early polls
        assert home._adopt_finished_job(job) is False

    job.final_result = _Results(_rows())             # worker publishes
    assert home._adopt_finished_job(job) is True
    assert session["results"] is job.final_result
    assert session[home.LAST_ADOPTED_JOB_KEY] == job.scan_id
    assert job.result_consumed is True


def test_a_result_is_adopted_exactly_once(monkeypatch):
    home, session, _ = _page(monkeypatch)
    calls = []
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")
    monkeypatch.setattr(
        home, "DecisionSupportService",
        lambda: type("A", (), {"analyze": lambda s, r: calls.append(r) or {}})())

    job = _job()
    assert home._adopt_finished_job(job) is True
    for _ in range(4):                               # rerun, refresh, observer
        assert home._adopt_finished_job(job) is False
    assert len(calls) == 1
    assert session["results"] is job.final_result


def test_a_second_observer_still_sees_the_rows_without_repeating_work(monkeypatch):
    from dashboard import home

    calls = []
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")
    monkeypatch.setattr(
        home, "DecisionSupportService",
        lambda: type("A", (), {"analyze": lambda s, r: calls.append(r) or {}})())

    job = _job()
    first = _Session()
    monkeypatch.setattr(home, "st", _FakeStreamlit(first))
    assert home._adopt_finished_job(job) is True

    second = _Session()                              # another browser session
    monkeypatch.setattr(home, "st", _FakeStreamlit(second))
    assert home._adopt_finished_job(job) is False
    assert second["results"] is job.final_result, "a second observer saw nothing"
    assert len(calls) == 1


def test_an_active_job_is_never_adopted(monkeypatch):
    home, session, _ = _page(monkeypatch)
    job, _ = jm.start_scan_job(symbols=["A"] * 3, autostart=False)
    assert job.is_active
    assert home._adopt_finished_job(job) is False
    assert "results" not in session


def test_a_failed_run_is_settled_without_publishing_an_empty_scan(monkeypatch):
    home, session, _ = _page(monkeypatch)
    job = _job(state=jm.FAILED, result=None)
    assert job.result_pending is False, "a failed run must not be waited on forever"
    assert home._adopt_finished_job(job) is True
    assert "results" not in session
    assert session["live_scan_completed"] is False
    assert job.result_consumed is True


def test_a_persistent_terminal_without_result_is_reported(monkeypatch):
    home, session, fake = _page(monkeypatch)
    job = _job(state=jm.COMPLETED, result=None)
    for _ in range(home.MAX_PENDING_RESULT_POLLS + 2):
        home._adopt_finished_job(job)
    assert "scan_pending_result_warning" in session
    assert job.scan_id in session["scan_pending_result_warning"]
    assert job.result_consumed is False, "a stall must never consume the result"


def test_the_pending_counter_resets_once_the_result_arrives(monkeypatch):
    home, session, _ = _page(monkeypatch)
    monkeypatch.setattr(home, "_archive_warning", lambda result: "")
    monkeypatch.setattr(home, "DecisionSupportService",
                        lambda: type("A", (), {"analyze": lambda s, r: {}})())
    job = _job(state=jm.COMPLETED, result=None)
    home._adopt_finished_job(job)
    assert session[home.PENDING_RESULT_POLLS_KEY] == 1
    job.final_result = _Results(_rows())
    home._adopt_finished_job(job)
    assert session[home.PENDING_RESULT_POLLS_KEY] == 0


def test_adoption_runs_no_analysis_provider_or_archive_work():
    from dashboard import home

    source = pathlib.Path(home.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    for forbidden in ("scan_symbols(", "load_history(", "process_scan(",
                      "DatasetArchive(", "finalize("):
        assert forbidden not in code


# =========================================================================== #
# PAGE WIRING
# =========================================================================== #


def _page_source():
    from dashboard import home

    return pathlib.Path(home.__file__).read_text(encoding="utf-8")


def test_the_page_resolves_the_workspace_once_and_reuses_it():
    source = _page_source()
    assert "resolve_page_workspace()" in source
    body = source.split("def show_dashboard(")[1]
    assert body.count("job_manager.workspace_key_for(") == 0, (
        "the page recomputes the key instead of reusing the stored one")
    assert "st.session_state[WORKSPACE_KEY]" in body


def test_the_page_reads_job_state_once_per_render():
    """Three separate is_active reads let one render disagree with itself."""

    body = _page_source().split("def show_dashboard(")[1]
    assert body.count("job.is_active") <= 1


def test_the_button_is_disabled_while_a_result_is_pending():
    body = _page_source().split("def show_dashboard(")[1]
    assert "disabled=active or awaiting_result or scan_completed" in body


def test_the_poller_stays_mounted_until_the_result_is_available():
    """Asserted at the mount site, not anywhere the phrase happens to appear.

    A page that stopped polling at the first terminal reading is exactly how a
    finished scan stayed invisible until the operator clicked again.
    """

    body = _page_source().split("def _render_scan_job(")[1]
    mount = body.split("st.fragment(_polling_body")[0]
    condition = mount.rstrip().splitlines()[-1].strip()
    assert condition == "if job.is_active or job.result_pending:", condition


def test_the_poller_does_not_release_the_page_before_the_result_lands():
    body = _page_source().split("def _polling_body(")[1].split("def ")[0]
    assert "if job.is_active or job.result_pending:" in body
    assert body.index("return") < body.index('st.rerun(scope="app")')


def test_the_page_shows_the_running_job_identity():
    source = _page_source()
    panel = source.split("def _render_scan_job(")[1]
    assert "job `" in panel and "started" in panel
    assert "processed" in panel and "successful" in panel and "failed" in panel


def test_the_page_never_returns_early_while_a_result_is_still_owed():
    body = _page_source().split("def show_dashboard(")[1]
    assert "if active or job.result_pending:" in body


# =========================================================================== #
# HISTORICAL EVIDENCE AND REGRESSION BOUNDARIES
# =========================================================================== #


def test_nothing_in_the_fix_writes_to_archived_runs():
    """The two duplicate runs are evidence; no code path here may touch them.

    Checked portably rather than by looking for the directories: ``reports/`` is
    untracked, so a worktree legitimately has none, and a test that skipped
    there would assert nothing on the machine that actually holds the evidence.
    """

    # Production files only. This module names the runs deliberately, so
    # scanning it here would only match the guard's own vocabulary.
    changed = (REPO_ROOT / "core" / "scan_job_manager.py",
               REPO_ROOT / "dashboard" / "home.py")
    for path in changed:
        source = path.read_text(encoding="utf-8")
        code = chr(10).join(line for line in source.splitlines()
                            if not line.strip().startswith("#"))
        for destructive in ("shutil.rmtree", "os.remove", "unlink(",
                            "RUN_20260803_223656/", "RUN_20260803_223829/"):
            assert destructive not in code, f"{path.name} may modify archived runs"


def test_the_duplicate_runs_stay_addressable_as_evidence():
    """Their identifiers are recorded in the code that exists because of them."""

    audit_source = (REPO_ROOT / "tests" / "test_daily_scan_job_state.py").read_text(
        encoding="utf-8")
    for run in DUPLICATE_RUNS:
        assert run in audit_source


def _module_tree():
    return ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))


def _nodes_outside(function_names):
    """Every AST node in this module except the named guard functions.

    A guard that greps its own source matches the very words it forbids. These
    walk the syntax tree instead, skipping the guards themselves, so they see
    what the tests actually *do*.
    """
    tree = _module_tree()
    skipped = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name in function_names]
    excluded = {id(child) for parent in skipped for child in ast.walk(parent)}
    return [node for node in ast.walk(tree) if id(node) not in excluded]


def test_nothing_here_touches_rubix_orb_or_a_scheduled_task():
    """No test in this module reaches a collector, an ORB task or production data."""

    forbidden = ("Start-ScheduledTask", "rubix_collector_supervisor",
                 "run_orb_shadow", "rubix_live_market.db")
    literals = [node.value for node in _nodes_outside(
        {"test_nothing_here_touches_rubix_orb_or_a_scheduled_task"})
        if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    for text in literals:
        for banned in forbidden:
            assert banned not in text, f"a test references {banned}"


def test_no_test_here_sleeps_or_starts_a_real_worker():
    """Every job is created with autostart disabled, and nothing sleeps.

    A real worker would run the actual scanner; a real sleep would leave a
    pytest process alive after the run. Both are checked structurally.
    """

    guard = {"test_no_test_here_sleeps_or_starts_a_real_worker"}
    for node in _nodes_outside(guard):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name.endswith("start_scan_job"):
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            assert "autostart" in keywords, f"{name} without an explicit autostart"
            value = keywords["autostart"]
            assert isinstance(value, ast.Constant), f"{name} has a computed autostart"
            if value.value is not False:
                # Autostart is allowed only against an injected runner, which
                # returns immediately and touches no scanner or provider.
                assert "runner" in keywords, (
                    f"{name} would start the real scanner")
        assert not name.endswith("sleep"), f"{name} would sleep in a test"
