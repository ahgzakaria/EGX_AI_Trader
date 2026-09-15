"""What a scheduled task has to be, checked against the files that install it.

Everything about the Rubix daily finalizer that this file used to test went
with the feed on 2026-09-10. These outlived it because none of them are about
any one task: they are the properties that made a scheduled job either
trustworthy or invisible on this machine, each written after the failure that
taught it.

* A verifier that names an installer file that no longer exists silently stops
  checking that task.
* A check that cannot determine an answer must not report a pass.
* A daily check whose output does not reach its log looks exactly like a check
  that keeps passing -- Write-Host does not travel down a 2>&1 pipe.
* A comment inside a PowerShell line continuation parses and then fails at
  runtime, after the installer has already unregistered the task.
* $PSScriptRoot is empty inside a param() block under Windows PowerShell 5.1
  and populated under pwsh 7, so a default built from it works every time it is
  tested by hand and fails on the machine.
* A task registered Interactive rather than S4U puts a black console on the
  desktop on every fire -- about twenty-three a day across the set.
"""

from __future__ import annotations

import pytest


def test_every_task_the_verifier_declares_has_an_installer():
    """The verifier names installer files; a rename must not silently unhook one."""

    import re
    from pathlib import Path

    windows = Path("scripts/windows")
    text = (windows / "verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    declared = re.findall(r'Installer = "([^"]+)"', text)

    # Three, since the Rubix trio retired on 2026-09-10, the gap recorder moved
    # into RUN_DAILY.bat the same day, and the ORB morning run -- every step of
    # which read the retired feed -- was retired on 2026-09-15. The bound exists
    # so a table gutted by a bad edit is caught, not so the set can never
    # shrink -- what makes a shrink legitimate is that the tasks are declared
    # retired below rather than simply dropped.
    assert len(declared) >= 3, f"expected the full task set, found {declared}"
    missing = [name for name in declared if not (windows / name).is_file()]
    assert not missing, f"verifier points at installers that do not exist: {missing}"

    # A retired task must stay named. Deleting its row without saying so turns
    # a deliberately disabled job into an unexplained stray every morning.
    for retired in ("EGX Rubix Daily Finalizer", "EGX Rubix Assisted Start",
                    "EGX Rubix Supervisor Watchdog", "EGX Gap Forward Recorder",
                    "EGX ORB Full Shadow Automation"):
        assert retired in text, f"{retired} was dropped rather than retired"
    assert "$retired = @(" in text


def test_the_verifier_never_reports_an_undetermined_check_as_a_pass():
    """A check that quietly succeeds when it failed to look is the defect itself."""

    from pathlib import Path

    text = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    assert '$expect = "UNKNOWN"' in text
    assert '"UNKNOWN" }' in text, "UNKNOWN must be its own verdict, not folded into OK"


# --- the daily check has to log what it found ------------------------------
#
# Its first captured run logged the comparison table and nothing else. The
# header, the undeclared-task block and the closing verdict all go through
# Write-Host, which does not travel down a 2>&1 pipe -- so the one part that
# always looks fine survived and every finding was dropped.

def test_the_verification_task_captures_every_stream():
    from pathlib import Path

    text = Path("scripts/windows/install_scheduled_tasks_verification_task.ps1"
                ).read_text(encoding="utf-8")
    command = text.partition("$command = ")[2].partition("$encoded")[0]
    assert "*>&1" in command, "Write-Host output is lost without *>&1"
    assert "2>&1" not in command, "2>&1 drops the findings and keeps the table"
    assert "exit $rc" in command, "the verdict must reach the log and the task result"


def test_the_verifier_declares_itself():
    """An undeclared task is reported, so a checker missing from its own list
    would flag itself every morning until people stopped reading it."""

    from pathlib import Path

    text = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    assert "EGX Scheduled Tasks Verification" in text
    assert "install_scheduled_tasks_verification_task.ps1" in text


def test_the_verifier_stays_read_only():
    """It is scheduled unattended against the whole task set."""

    from pathlib import Path

    verifier = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    for forbidden in ("Register-ScheduledTask", "Unregister-ScheduledTask",
                      "Set-ScheduledTask", "Start-ScheduledTask"):
        assert forbidden not in verifier, (
            forbidden + " would make the 09:00 check change things unattended")

    installer = Path("scripts/windows/install_scheduled_tasks_verification_task.ps1"
                     ).read_text(encoding="utf-8")
    assert "no longer a read-only check" in installer, (
        "the installer must refuse a verifier that grew a mutating call")


# --- a comment inside a line continuation ----------------------------------
#
# PowerShell parses this and then does not run it:
#
#     $action = New-ScheduledTaskAction -Execute "powershell.exe" `
#         # a comment
#         -Argument "..."
#
# The backtick continuation ends at the comment, so -Argument becomes its own
# statement and the call fails at runtime with "The term '-Argument' is not
# recognized". Parser::ParseFile reports no error, which is how I convinced
# myself six installers were fine after breaking all six -- and the one I ran
# had already unregistered its task before the failure, so it deleted a task
# and installed nothing.

def test_no_installer_puts_a_comment_inside_a_line_continuation():
    from pathlib import Path

    offenders = []
    for script in sorted(Path("scripts/windows").glob("*.ps1")):
        lines = script.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines[:-1]):
            if line.rstrip().endswith("`") and lines[n + 1].lstrip().startswith("#"):
                offenders.append(f"{script.name}:{n + 1}")
    assert not offenders, (
        "a comment after a backtick ends the continuation; these parse but do "
        f"not run: {offenders}")


def test_the_scheduled_powershell_tasks_do_not_open_a_window():
    """Every fire opened a console on the desktop -- about twenty-three a day."""

    from pathlib import Path

    windows = Path("scripts/windows")
    for name in ("install_sector_flow_refresh_task.ps1",
                 "install_confirmed_breakout_forward_task.ps1",
                 "install_gap_forward_task.ps1",
                 "install_scheduled_tasks_verification_task.ps1",
                 "install_orb_full_shadow_task.ps1"):
        text = (windows / name).read_text(encoding="utf-8")
        argument = [row for row in text.splitlines() if "-Argument" in row]
        assert argument, name
        assert any("-WindowStyle Hidden" in row for row in argument), (
            name + " launches powershell.exe with a visible console")


# --- a param default cannot use $PSScriptRoot -------------------------------
#
# Windows PowerShell 5.1 does not populate $PSScriptRoot while binding the param
# block, so a default built from it binds an empty string and the script dies on
# its own first line with "Cannot bind argument to parameter 'Path'". pwsh 7
# does populate it, which is why every one of these passed each time I ran them
# and failed the first time someone used powershell -File.

def test_no_script_builds_a_param_default_from_psscriptroot():
    from pathlib import Path

    offenders = []
    for script in sorted(Path("scripts/windows").glob("*.ps1")):
        text = script.read_text(encoding="utf-8")
        if "param(" not in text:
            continue
        block = text.partition("param(")[2].partition("\n)")[0]
        if "PSScriptRoot" in block:
            offenders.append(script.name)
    assert not offenders, (
        "these bind an empty path under Windows PowerShell 5.1; resolve it in "
        f"the body instead: {offenders}")
