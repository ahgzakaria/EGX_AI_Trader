"""The sector refresh is scheduled, not merely schedulable.

`scripts/refresh_sector_flow.py` was written for Task Scheduler and says so in
its own docstring. It was never registered, and its opening paragraph describes
the failure that then happened to it: "a feature that needs a manual rebuild to
stay current goes stale silently". On 2026-08-30 the store's newest complete
session was 2026-08-26 and its last build was three days old.

These tests are about the installer, not about Windows. They assert the
properties that make an hourly poll safe to leave running unattended -- the ones
whose absence would only be discovered by a store two writers had corrupted, or
by a twenty-minute rebuild paid once an hour while a provider caught up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INSTALL_PS1 = Path("scripts/windows/install_sector_flow_refresh_task.ps1")
REMOVE_PS1 = Path("scripts/windows/remove_sector_flow_refresh_task.ps1")
REFRESH_PY = Path("scripts/refresh_sector_flow.py")

TASK_NAME = "EGX Sector Flow Refresh"


@pytest.fixture(scope="module")
def install_text():
    return INSTALL_PS1.read_text(encoding="utf-8")


def test_both_scripts_exist():
    assert INSTALL_PS1.is_file() and REMOVE_PS1.is_file()


def test_the_installer_targets_the_refresh_not_the_manual_rebuild(install_text):
    """build_sector_flow.py is the twenty-minute unconditional rebuild.

    Scheduling that one hourly would rebuild the whole history every hour. The
    refresh wrapper is the one with the skip.
    """

    # The target line, not the prose: the docstring quotes the refresh
    # script's own rationale, which names build_sector_flow.py.
    target = [line for line in install_text.splitlines()
              if line.strip().startswith("$script =")]
    assert target, "the installer must resolve a script path"
    assert "refresh_sector_flow.py" in target[0]
    assert "build_sector_flow.py" not in target[0]


def test_it_runs_only_on_trading_days(install_text):
    for day in ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"):
        assert day in install_text
    assert "Friday" not in install_text and "Saturday" not in install_text


def test_it_polls_rather_than_firing_once(install_text):
    """The candle appears when the 15:45 finalizer builds it, and that job's
    duration varies. A single fixed time is a guess about another job."""

    assert "RepetitionInterval" in install_text
    assert "New-TimeSpan -Hours 1" in install_text


def test_it_starts_after_the_finalizer_builds_the_candle(install_text):
    """16:00, not 15:30: a poll before 15:45 is guaranteed to find nothing."""

    assert '$StartTime = "16:00"' in install_text


def test_overlapping_runs_are_refused(install_text):
    """Two rebuilds writing one store is the failure hourly polling could cause
    and that nothing else would catch."""

    assert "-MultipleInstances IgnoreNew" in install_text


def test_the_time_limit_outlasts_a_real_rebuild(install_text):
    """Forty minutes was too tight, measured rather than argued.

    A rebuild takes about nine minutes on an idle machine, so forty looked
    generous. On 2026-08-31 the 16:00 run was still going at 16:40 with a test
    suite running beside it, and was killed -- while its Python child survived
    and completed the build at 16:48. The store advanced and the task reported
    SCHED_S_TASK_TERMINATED, which is exactly what a genuinely lost build also
    reports.

    The limit is there to stop a hung run, not to adjudicate a slow one.
    """

    assert "-ExecutionTimeLimit (New-TimeSpan -Minutes 90)" in install_text


def test_a_missed_day_is_caught_up(install_text):
    assert "-StartWhenAvailable" in install_text


def test_the_log_is_appended_as_readable_utf8(install_text):
    """PowerShell redirection defaults to UTF-16, and a Python process with no
    console falls back to the ANSI code page. Both halves are pinned."""

    assert "-Append -Encoding utf8" in install_text
    assert 'PYTHONIOENCODING = "utf-8"' in install_text


def test_it_refuses_to_register_against_missing_files(install_text):
    """Checked before registering rather than discovered at 16:00 on a Sunday."""

    assert "refresh script" in install_text
    assert "throw" in install_text


def test_it_can_be_previewed_without_registering(install_text):
    assert "$WhatIfOnly" in install_text
    assert "nothing was registered" in install_text


def test_both_scripts_name_the_same_task(install_text):
    assert f'$TaskName = "{TASK_NAME}"' in install_text
    assert f'$TaskName = "{TASK_NAME}"' in REMOVE_PS1.read_text(encoding="utf-8")


def test_removal_leaves_the_data_alone():
    """A schedule and the history it maintains are different things."""

    text = REMOVE_PS1.read_text(encoding="utf-8")
    assert "Unregister-ScheduledTask" in text
    assert "left untouched" in text
    assert "Remove-Item" not in text


def test_the_installer_places_no_orders(install_text):
    assert "No orders, no signals." in install_text


def test_the_refresh_script_prints_ascii():
    """Its stdout is captured by the task.

    An em dash in a log line arrived as a stray letter, because a Python
    process launched by Task Scheduler has no console and falls back to the
    ANSI code page. PYTHONIOENCODING now pins that, and keeping the messages
    ASCII means the log survives even where it does not.
    """

    assert REFRESH_PY.read_text(encoding="utf-8").isascii()
