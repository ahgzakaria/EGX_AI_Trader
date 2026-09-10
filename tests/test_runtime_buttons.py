"""The start and stop buttons: one start path, no duplicates, no silent close.

These guard the three things that actually went wrong while building them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = {"stop": Path("scripts/stop_everything.ps1")}
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


def test_start_delegates_to_the_launcher_the_project_already_has():
    """START.cmd must not become a second way to bring the app up.

    Its first version opened Assisted Start -- the window a scheduled task
    used, watching a fixed path with no Browse -- rather than the launcher a
    person drives by hand. The names have changed twice since; the rule has
    not. It delegates to scripts/start_egx_ai_trader.bat, which validates the
    environment and opens the window, and it drives nothing itself.
    """
    start = Path("START.cmd").read_text(encoding="utf-8")
    assert "start_egx_ai_trader.bat" in start
    for reinvented in ("launch_dashboard.py", "streamlit", "python.exe"):
        assert reinvented not in start, (
            f"START.cmd must delegate, not drive {reinvented} itself"
        )


def test_the_launcher_batch_it_delegates_to_still_exists():
    """A wrapper pointing at a file that moved is a button that does nothing."""
    batch = Path("scripts/start_egx_ai_trader.bat")
    assert batch.is_file()
    text = batch.read_text(encoding="utf-8")
    assert "launch_dashboard.py" in text
