"""The start and stop buttons: one start path, no duplicates, no silent close.

These guard the three things that actually went wrong while building them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = {
    "start": Path("scripts/start_everything.ps1"),
    "stop": Path("scripts/stop_everything.ps1"),
}
WRAPPERS = [Path("START.cmd"), Path("STOP.cmd")]


@pytest.fixture(params=sorted(SCRIPTS), ids=sorted(SCRIPTS))
def script(request):
    return SCRIPTS[request.param].read_text(encoding="utf-8")


def test_every_exit_holds_the_window_open(script):
    """A bare ``exit`` closes the console before the result can be read.

    The first version pasted the hold before each top-level ``exit`` and
    silently missed the ones indented inside an ``if`` -- including the stop
    script's success path, the one that runs on a normal day. Measured through
    the shortcut, that window was gone in under six seconds.
    """
    bare = [
        (number, line)
        for number, line in enumerate(script.splitlines(), 1)
        if re.match(r"^\s*exit \d+\s*$", line)
    ]
    # Exactly one: the `exit $code` inside Complete-Run is `$code`, not a digit,
    # so any numeric bare exit here is a path that skips the hold.
    assert not bare, f"these exits skip the window hold: {bare}"
    assert "function Complete-Run" in script
    assert "IsInputRedirected" in script


def test_an_automated_run_is_never_left_waiting_for_a_keypress(script):
    """The hold must be conditional, or a scheduled run hangs forever."""
    assert "-not [Console]::IsInputRedirected" in script


@pytest.mark.parametrize("path", list(SCRIPTS.values()) + WRAPPERS,
                         ids=lambda p: p.name)
def test_the_button_scripts_are_pure_ascii(path):
    """Windows PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI.

    One non-ASCII character in a string makes it a parse error -- which is how
    a scheduled script once failed while running fine in the dev shell.
    """
    offenders = [
        (number, line)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(ord(char) > 127 for char in line)
    ]
    assert not offenders, f"non-ASCII in {path}: {offenders[:3]}"


def test_start_uses_the_scheduled_task_s_own_launcher():
    """One start path, not two.

    A second way to start the collector would drift from the 09:45 task's, and
    the drift surfaces as a duplicate collector or a morning where nothing runs.
    """
    start = SCRIPTS["start"].read_text(encoding="utf-8")
    assert "run_rubix_assisted_start.py" in start
    assert "--auto-start" in start
    assert "rubix_collector_supervisor" not in start, (
        "the collector must be started by Assisted Start, never directly"
    )


def test_start_refuses_to_open_a_second_of_anything():
    """Two assisted-start windows both fire when the frame lands.

    The lock stops the second collector, but it stops it by failing -- logging
    supervisor_duplicate_blocked at 09:10, the one moment nothing should look
    wrong.
    """
    start = SCRIPTS["start"].read_text(encoding="utf-8")
    assert "Get-AssistedStartProcess" in start
    assert "Not opening another" in start
    assert "already up" in start


def test_an_unknown_collector_state_is_not_treated_as_stopped():
    """Starting on an unreadable answer is how two supervisors end up sharing
    one database."""
    start = SCRIPTS["start"].read_text(encoding="utf-8")
    assert 'Write-Host "`nCollector state could not be read, so it was NOT started."' in start


def test_stop_asks_before_it_kills():
    stop = SCRIPTS["stop"].read_text(encoding="utf-8")
    assert "stop_requested.flag" in stop
    assert "GraceSeconds" in stop
    # It must judge cleanliness by the log, not by the process disappearing.
    assert "supervisor_shutdown" in stop


def test_the_status_probe_reuses_the_project_s_own_checks():
    """A launcher with its own idea of "running" will eventually disagree with
    the collector's."""
    probe = Path("scripts/runtime_status.py").read_text(encoding="utf-8")
    assert "from scripts.launcher_process_utils import supervisor_status" in probe
    assert "scan_frame_file" in probe
    assert "scan.ready" in probe, "re-deriving the verdict is a second copy of the rule"


def test_the_probe_answers_even_when_it_cannot_tell():
    """None means "could not tell" and must never be read as "no"."""
    from scripts.runtime_status import auth_frame_state, collector_state

    missing = Path("does-not-exist-anywhere.json")
    assert collector_state(missing)["running"] is False
    frame = auth_frame_state(Path("also-missing.txt"))
    assert frame["usable"] in (False, None)
    assert frame["reason"]
