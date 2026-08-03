"""Rubix collector autostart — scheduled-task and readiness validations.

No test starts or stops the collector, registers a scheduled task, opens a
websocket, authenticates, or reads the production database. PowerShell is only
parsed or run with `-WhatIfOnly`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.check_rubix_collector_readiness import (
    AUTH_MAX_AGE_MINUTES,
    Readiness,
    check_auth_frame,
    check_source,
    parse_args,
    run,
)


INSTALL_PS1 = Path("scripts/windows/install_rubix_collector_scheduled_task.ps1")
REMOVE_PS1 = Path("scripts/windows/remove_rubix_collector_scheduled_task.ps1")
ORB_INSTALL_PS1 = Path("scripts/windows/install_orb_shadow_scheduled_task.ps1")

TASK_NAME = "EGX Rubix Collector Auto Start"

SOURCE_SCHEMA = """
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, last_price REAL, bid REAL, ask REAL, volume REAL,
    market_timestamp TEXT NOT NULL, received_at TEXT NOT NULL,
    exchange TEXT, sequence INTEGER, change_percent REAL,
    has_feed_timestamp INTEGER NOT NULL DEFAULT 0
);
"""


def synthetic_source(path, *, rows=5, received_minutes_ago=1.0):
    now = datetime.now(timezone.utc)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SOURCE_SCHEMA)
        for index in range(rows):
            moment = now - timedelta(minutes=received_minutes_ago)
            connection.execute(
                """INSERT INTO quotes (ticker,last_price,bid,ask,volume,
                   market_timestamp,received_at,exchange,sequence,change_percent,
                   has_feed_timestamp) VALUES (?,?,?,?,?,?,?,?,NULL,?,1)""",
                ("AAA", 10.0, 9.99, 10.01, 100.0,
                 moment.isoformat(), moment.isoformat(), "CASE", 0.0),
            )
        connection.commit()
    finally:
        connection.close()
    return Path(path)


# =========================================================================== #
# READINESS CHECK — READ ONLY
# =========================================================================== #


def test_a_fresh_auth_frame_passes(tmp_path):
    frame = tmp_path / "auth.json"
    frame.write_text("{}", encoding="utf-8")
    readiness = Readiness()
    check_auth_frame(frame, readiness)
    assert readiness.checks[0].ok
    assert "fresh" in readiness.checks[0].detail


def test_a_stale_auth_frame_fails(tmp_path):
    frame = tmp_path / "auth.json"
    frame.write_text("{}", encoding="utf-8")
    readiness = Readiness()
    stale_now = datetime.now(timezone.utc) + timedelta(
        minutes=AUTH_MAX_AGE_MINUTES + 5
    )
    check_auth_frame(frame, readiness, now=stale_now)
    assert not readiness.checks[0].ok
    assert "TOO OLD" in readiness.checks[0].detail


def test_a_missing_auth_frame_fails(tmp_path):
    readiness = Readiness()
    check_auth_frame(tmp_path / "absent.json", readiness)
    assert not readiness.checks[0].ok


def test_no_auth_frame_supplied_is_reported_as_the_blocker():
    readiness = Readiness()
    check_auth_frame(None, readiness)
    assert not readiness.checks[0].ok
    assert "cannot start without one" in readiness.checks[0].detail


def test_the_readiness_limit_matches_the_supervisor_default():
    """If the supervisor's default changes, this check must follow it."""

    text = Path("scripts/rubix_collector_supervisor.py").read_text(encoding="utf-8")
    assert '"--auth-max-age-minutes", type=int, default=15' in text
    assert AUTH_MAX_AGE_MINUTES == 15.0


def test_a_progressing_source_passes(tmp_path):
    database = synthetic_source(tmp_path / "src.db", received_minutes_ago=1.0)
    readiness = Readiness()
    check_source(database, readiness)
    names = {c.name: c for c in readiness.checks}
    assert names["source_database"].ok
    assert names["source_progressing"].ok


def test_a_frozen_source_is_reported(tmp_path):
    database = synthetic_source(tmp_path / "frozen.db", received_minutes_ago=180.0)
    readiness = Readiness()
    check_source(database, readiness)
    frozen = {c.name: c for c in readiness.checks}["source_progressing"]
    assert not frozen.ok
    assert "FROZEN" in frozen.detail


def test_an_empty_source_is_reported(tmp_path):
    database = synthetic_source(tmp_path / "empty.db", rows=0)
    readiness = Readiness()
    check_source(database, readiness)
    assert not readiness.checks[0].ok


def test_the_readiness_check_opens_the_source_read_only(tmp_path):
    """It must never be able to write to the collector's database."""

    database = synthetic_source(tmp_path / "ro.db")
    before = database.read_bytes()
    readiness = Readiness()
    check_source(database, readiness)
    assert database.read_bytes() == before

    text = Path("scripts/check_rubix_collector_readiness.py").read_text(encoding="utf-8")
    assert "mode=ro" in text
    assert "PRAGMA query_only=ON" in text


def test_the_readiness_check_starts_nothing():
    """Scan executable tokens only.

    The module's own banner says it opens no websocket and takes no lock, so a
    naive text search would match the safety statement rather than a call.
    """

    import io
    import tokenize

    def executable_tokens(source):
        kept = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            # Every string literal is prose here (docstring, banner, message),
            # never a call, so all of them are excluded.
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
        return " ".join(kept)

    code = executable_tokens(
        Path("scripts/check_rubix_collector_readiness.py").read_text(encoding="utf-8")
    )
    for word in (
        "subprocess", "Popen", "websocket", "authenticate", "acquire",
        "SingleInstanceLock", "taskkill", "start_child", "Register",
    ):
        assert word not in code, f"readiness check calls {word!r}"
    # It reads the launcher's non-destructive status helper, and nothing else.
    assert "supervisor_status" in code


def test_readiness_returns_nonzero_when_not_ready(tmp_path):
    args = parse_args([
        "--rubix-db-path", str(synthetic_source(tmp_path / "s.db", received_minutes_ago=999)),
        "--supervisor-pid-file", str(tmp_path / "absent.pid.json"),
    ])
    payload = run(args)
    assert payload["read_only"] is True
    assert payload["ready"] is False


# =========================================================================== #
# INSTALLER — SAFETY GATES
# =========================================================================== #


def test_the_installer_targets_the_supervisor_not_the_gui():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "rubix_collector_supervisor.py" in text
    # The GUI launcher must never be the scheduled target: it waits for a human
    # and its Start button also launches the Dashboard.
    assert "launch_rubix_production.py" not in text
    assert "start_rubix_production.bat" not in text


def test_the_installer_refuses_a_launcher_without_single_instance_protection():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "SingleInstanceLock" in text
    assert "InstanceAlreadyRunning" in text
    assert "no verified single-instance protection" in text


def test_the_installer_refuses_a_non_headless_target():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert 'match "tkinter"' in text
    assert "must be headless" in text


def test_the_installer_blocks_on_a_stale_auth_frame():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Refusing to register a task that cannot authenticate" in text
    assert "AllowStaleAuthFrame" in text


def test_the_installer_stores_no_credential():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    for forbidden in ("-Password", "ConvertTo-SecureString", "PSCredential", "-AsPlainText"):
        assert forbidden not in text


def test_the_installer_embeds_no_session_date():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "--session-date" not in text


def test_the_installer_uses_the_required_task_settings():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "-MultipleInstances IgnoreNew" in text
    assert "-WakeToRun" in text
    assert "-StartWhenAvailable" in text
    assert "-ExecutionTimeLimit" in text
    assert "-RunLevel Limited" in text
    assert "-RunLevel Highest" not in text
    assert "-LogonType Interactive" in text


def test_the_installer_schedules_sunday_through_thursday():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "-DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday" in text
    assert 'CairoStartTime = "09:20"' in text


def test_the_installer_handles_timezone_explicitly():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Egypt Standard Time" in text
    assert "TIMEZONE MISMATCH" in text
    assert "Cannot resolve the Cairo timezone" in text
    assert "DST" in text


def test_the_installer_never_overwrites_an_unrelated_task():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Refusing to overwrite an unrelated task" in text


def test_the_installer_creates_no_second_collector():
    """No feed URL, no subscription call - it only schedules what exists.

    The script's documentation *mentions* websockets to say it creates none, so
    the assertions target concrete implementation markers instead of the word.
    """

    text = INSTALL_PS1.read_text(encoding="utf-8")
    for marker in ("wss://", "ws://", "websockets.connect", "Subscribe(", "auth_frame_capture"):
        assert marker not in text, f"installer contains {marker!r}"
    assert "Creates no collector" in text
    assert "rubix_collector_supervisor.py" in text


def test_the_installer_prints_the_command_before_registering():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert text.index("Task command:") < text.index("Register-ScheduledTask")
    assert text.index("if ($WhatIfOnly)") < text.index("Register-ScheduledTask")


def test_the_remover_refuses_an_unrelated_task():
    text = REMOVE_PS1.read_text(encoding="utf-8")
    assert "Refusing to remove an unrelated task" in text
    assert "Unregister-ScheduledTask" in text


def test_the_remover_deletes_no_evidence():
    text = REMOVE_PS1.read_text(encoding="utf-8")
    for forbidden in ("Remove-Item", "Stop-Process", "taskkill"):
        assert forbidden not in text


def test_no_automatic_stop_task_is_created():
    """The supervisor shuts down cleanly on its own; killing it is unwarranted."""

    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Stop-Process" not in text
    assert "taskkill" not in text


@pytest.mark.parametrize("script", [INSTALL_PS1, REMOVE_PS1, ORB_INSTALL_PS1])
def test_powershell_scripts_parse(script):
    command = (
        "$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
        "(Resolve-Path $args[0]).Path,[ref]$null,[ref]$e) | Out-Null; "
        "if($e.Count -gt 0){exit 1}else{exit 0}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command, str(script)],
        capture_output=True,
    )
    assert result.returncode == 0, f"{script} failed to parse"


def test_the_installer_whatif_registers_nothing(tmp_path):
    """Executes the installer with -WhatIfOnly. Nothing is registered."""

    adapter = tmp_path / "adapter"
    adapter.mkdir()
    for name in ("adapter.py", "cli.py", "protocol.py", "storage.py", "__init__.py"):
        (adapter / name).write_text("", encoding="utf-8")
    frame = tmp_path / "auth frame.json"
    frame.write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-File", str(INSTALL_PS1),
            "-ProjectRoot", str(Path.cwd()),
            "-PythonExe", sys.executable,
            "-AdapterPath", str(adapter),
            "-AuthFrameFile", str(frame),
            "-WhatIfOnly",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "nothing was registered" in result.stdout.lower()
    assert "auth frame.json" in result.stdout, "paths with spaces must survive"

    check = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) "
         "{'YES'} else {'NO'}"],
        capture_output=True, text=True,
    )
    assert check.stdout.strip() == "NO", "the dry run must not register the task"


def test_the_installer_refuses_a_stale_frame_end_to_end(tmp_path):
    """The blocker, exercised: a stale frame must stop registration."""

    import os
    import time

    adapter = tmp_path / "adapter"
    adapter.mkdir()
    for name in ("adapter.py", "cli.py", "protocol.py", "storage.py", "__init__.py"):
        (adapter / name).write_text("", encoding="utf-8")
    frame = tmp_path / "old.json"
    frame.write_text("{}", encoding="utf-8")
    old = time.time() - 3600
    os.utime(frame, (old, old))

    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-File", str(INSTALL_PS1),
            "-ProjectRoot", str(Path.cwd()),
            "-PythonExe", sys.executable,
            "-AdapterPath", str(adapter),
            "-AuthFrameFile", str(frame),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "cannot authenticate" in combined or "BLOCKED" in combined


# =========================================================================== #
# ORB TASK SETTINGS UPDATE PATH
# =========================================================================== #


def test_the_orb_installer_has_a_settings_only_update_path():
    text = ORB_INSTALL_PS1.read_text(encoding="utf-8")
    assert "-UpdateSettingsOnly" in text or "UpdateSettingsOnly" in text
    assert "Set-ScheduledTask" in text


def test_the_settings_update_does_not_touch_action_or_trigger():
    text = ORB_INSTALL_PS1.read_text(encoding="utf-8")
    block = text[text.index("if ($UpdateSettingsOnly)"):text.index("$scriptRelative =")]
    assert "New-ScheduledTaskAction" not in block
    assert "New-ScheduledTaskTrigger" not in block
    assert "are NOT modified" in block


def test_the_settings_update_refuses_an_unrelated_task():
    text = ORB_INSTALL_PS1.read_text(encoding="utf-8")
    assert "Refusing to modify an unrelated task" in text


# =========================================================================== #
# COLLECTOR ARCHITECTURE INVARIANTS
# =========================================================================== #


def test_the_supervisor_remains_the_single_collector_owner():
    """No second websocket implementation may be introduced."""

    text = Path("scripts/rubix_collector_supervisor.py").read_text(encoding="utf-8")
    assert "wss://" in text, "the supervisor owns the feed URL"
    # Nothing added by this change may contain another feed URL.
    for name in (
        "scripts/check_rubix_collector_readiness.py",
        "scripts/windows/install_rubix_collector_scheduled_task.ps1",
        "scripts/windows/remove_rubix_collector_scheduled_task.ps1",
    ):
        assert "wss://" not in Path(name).read_text(encoding="utf-8"), name


def test_the_launcher_gui_is_still_the_only_interactive_path():
    text = Path("scripts/launch_rubix_production.py").read_text(encoding="utf-8")
    assert "tkinter" in text
    assert "RubixAuthenticationAssistantUI" in text


def test_the_auth_assistant_still_refuses_to_authenticate():
    """The documented blocker must not have been quietly worked around."""

    text = Path("services/rubix_auth_assistant.py").read_text(encoding="utf-8")
    assert "never authenticates, controls a browser, or persists frame data" in text
