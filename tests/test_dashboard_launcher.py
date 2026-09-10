"""The launcher opens the dashboard. What it must not do is everything else.

It replaced a 2,501-line window that also drove a price collector -- browsing
for an authentication frame, running a preflight, waiting for a feed to go
healthy, offering deep diagnostics. That feed was retired on 2026-09-10 and its
Start button could no longer succeed.

Each of these pins a mechanism that was written after something went wrong, and
which had to survive the rewrite:

* two launchers meant two Streamlit processes contending for port 8501, and the
  loser's failure looked like a broken app;
* a double-click while the app is open must bring up that app, not kill it;
* a port held by something else must be refused, not reported as ready, or the
  user is handed a URL to somebody else's server;
* readiness means the health endpoint answered, not that a PID exists;
* closing the launcher must not close a dashboard it did not start.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.launch_dashboard as launcher  # noqa: E402


def test_an_already_serving_dashboard_is_reused_not_replaced(monkeypatch, tmp_path):
    """A second double-click must not kill the app you are reading."""

    marker = tmp_path / "pid.json"
    marker.write_text(json.dumps({"pid": 4242, "port": 8501}), encoding="utf-8")
    monkeypatch.setattr(launcher, "STREAMLIT_PID_FILE", marker)
    monkeypatch.setattr(launcher, "port_is_open", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "pid_record_is_current", lambda record: True)
    monkeypatch.setattr(launcher, "streamlit_is_ready", lambda *a, **k: True)

    dashboard = launcher.DashboardProcess(8501)
    assert dashboard.start() == "existing"
    assert dashboard.process is None
    assert dashboard._owned is False


def test_a_port_held_by_something_else_is_refused(monkeypatch, tmp_path):
    """Reporting success here hands the user somebody else's server."""

    marker = tmp_path / "pid.json"
    monkeypatch.setattr(launcher, "STREAMLIT_PID_FILE", marker)
    monkeypatch.setattr(launcher, "port_is_open", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "pid_record_is_current", lambda record: False)

    with pytest.raises(RuntimeError) as excinfo:
        launcher.DashboardProcess(8501).start()
    assert "another process" in str(excinfo.value)


def test_readiness_is_the_health_endpoint_not_a_live_process(monkeypatch):
    """"Started" has to mean the page will load."""

    dashboard = launcher.DashboardProcess(8501)
    dashboard.process = object()
    seen = {}

    def _wait(port, process, timeout, progress=None):
        seen["port"] = port
        return False, "timed out"

    monkeypatch.setattr(launcher, "wait_for_streamlit", _wait)
    ready, detail = dashboard.wait_until_ready(timeout=1)
    assert ready is False and detail == "timed out"
    assert seen["port"] == 8501


def test_a_dashboard_it_did_not_start_is_left_running(monkeypatch):
    """Closing the launcher is not a request to close the app."""

    dashboard = launcher.DashboardProcess(8501)
    dashboard.process = object()
    dashboard._owned = False
    assert dashboard.stop() == "not_owned"
    assert dashboard.process is not None


def test_only_one_launcher_window_at_a_time():
    """Two meant two Streamlit processes fighting for the same port."""

    import inspect

    source = inspect.getsource(launcher.main)
    assert "SingleInstanceLock" in source
    assert "InstanceAlreadyRunning" in source


def test_the_launcher_no_longer_reaches_for_the_retired_feed():
    text = (ROOT / "scripts" / "launch_dashboard.py").read_text(encoding="utf-8")
    for gone in ("rubix_sqlite_provider", "start_collector", "auth_frame",
                 "adapter_path", "RUBIX_DB_PATH", "wait_until_healthy"):
        assert gone not in text, f"the launcher still reaches for {gone}"


def test_it_starts_streamlit_headless_on_the_requested_port():
    import inspect

    source = inspect.getsource(launcher.DashboardProcess.start)
    assert '"-m", "streamlit", "run", "app.py"' in source
    assert "--server.headless" in source
    # Windowed launch, so a double-click leaves no console behind the window.
    assert "CREATE_NO_WINDOW" in source


def test_streamlit_output_is_drained_to_a_file():
    """A full pipe buffer blocks the child process."""

    import inspect

    source = inspect.getsource(launcher.DashboardProcess._drain)
    assert "STREAMLIT_LOG" in source


# --- the files a person actually double-clicks ------------------------------

def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_every_entry_point_opens_the_dashboard_launcher():
    start = _text("START.cmd")
    batch = _text("scripts/start_egx_ai_trader.bat")

    assert "start_egx_ai_trader.bat" in start
    assert "launch_dashboard.py" in batch
    for retired in ("launch_rubix_production", "start_rubix_production",
                    "rubix_collector_supervisor"):
        assert retired not in start, f"START.cmd still names {retired}"
        assert retired not in batch, f"the batch file still names {retired}"


def test_the_desktop_shortcut_the_installer_creates_points_at_it():
    setup = _text("scripts/setup_windows.bat")
    assert "start_egx_ai_trader.bat" in setup
    assert "start_rubix_production.bat" not in setup


def test_the_deprecated_entry_point_redirects_rather_than_diverging():
    """It survives only because two TickerChart scripts import its helpers."""

    import inspect

    import scripts.launch_egx_ai_trader as deprecated

    source = inspect.getsource(deprecated.main)
    assert "launch_dashboard" in source
    assert "--smoke-test" in source, (
        "a machine set up before this still passes it")


def test_the_launcher_check_validates_what_it_needs():
    assert launcher.check_installation() == 0
