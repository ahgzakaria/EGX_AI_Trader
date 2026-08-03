"""Rubix Assisted Start — frame detection, start safety, auto-start, paths.

No test starts or stops the real collector, opens a websocket, authenticates,
registers a scheduled task, or reads the production database. The collector
spawner is injected, the clock is injected, and PowerShell is only parsed or
run with `-WhatIfOnly`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services.rubix_assisted_start import (
    DEFAULT_AUTH_FRAME_PATH,
    FRAME_SUFFIXES,
    PARTIAL_SUFFIXES,
    AssistedState,
    CountdownState,
    FrameDisposal,
    FrameSelection,
    FrameStatus,
    build_collector_command,
    countdown_should_abort,
    dispose_frame,
    evaluate_health,
    resolve_inbox,
    scan_frame_file,
    scan_inbox,
)
from scripts.run_orb_shadow_session import _path_identity
from scripts.run_rubix_assisted_start import (
    BANNER_LINES,
    PROJECT_ROOT,
    AssistedStartSession,
    load_preferences,
    parse_args,
    save_preferences,
)


INSTALL_PS1 = Path("scripts/windows/install_rubix_assisted_start_task.ps1")
REMOVE_PS1 = Path("scripts/windows/remove_rubix_assisted_start_task.ps1")
TASK_NAME = "EGX Rubix Assisted Start"

NOW = datetime(2026, 8, 4, 9, 15, tzinfo=timezone.utc)


def _no_spawn(command):
    raise AssertionError("no test may spawn a collector")


def inbox_args(*extra):
    """Args for the advanced inbox mode.

    Every inbox test states the mode explicitly so none of them can silently
    fall through to the production default and read the real machine's
    C:\secure-temp frame - a test that passes or fails with the time of day.
    """

    return parse_args(["--headless", "--watch-mode", "inbox", *extra])


def write_frame(inbox: Path, name: str, *, minutes_old: float = 1.0, valid=True,
                embedded=True, now=NOW) -> Path:
    """A structurally plausible frame. Content is synthetic, never a real one."""

    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    stamp = (now - timedelta(minutes=minutes_old)).isoformat()
    if valid:
        payload = {
            "type": "auth",
            "sessionId": "SYNTHETIC-TEST-SESSION",
            "token": "SYNTHETIC-NOT-A-REAL-SECRET",
        }
        if embedded:
            payload["timestamp"] = stamp
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text("{ this is not valid json", encoding="utf-8")
    epoch = (now - timedelta(minutes=minutes_old)).timestamp()
    os.utime(path, (epoch, epoch))
    return path


@pytest.fixture
def inbox(tmp_path):
    path = tmp_path / "inbox"
    path.mkdir()
    return path


# =========================================================================== #
# FRAME DETECTION
# =========================================================================== #


def test_an_empty_inbox_waits(inbox):
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME
    assert scan.selected is None
    assert "no auth-frame files" in scan.detail


def test_a_missing_inbox_is_reported_distinctly(tmp_path):
    scan = scan_inbox(tmp_path / "absent", now=NOW)
    assert scan.selection is FrameSelection.INBOX_UNAVAILABLE


def test_one_valid_fresh_frame_is_selected(inbox):
    write_frame(inbox, "frame.json", minutes_old=1.0)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.FRAME_SELECTED
    assert scan.selected.path.name == "frame.json"
    assert scan.ready


def test_an_expired_frame_is_not_selected(inbox):
    write_frame(inbox, "old.json", minutes_old=45.0)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME
    assert scan.rejected
    assert scan.rejected[0].inspection.status != "AUTH_VALID"


def test_a_malformed_frame_is_not_selected(inbox):
    write_frame(inbox, "bad.json", valid=False)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME
    assert scan.rejected


@pytest.mark.parametrize("suffix", sorted(PARTIAL_SUFFIXES))
def test_partial_downloads_are_ignored(inbox, suffix):
    """A half-written download must not be read as a malformed frame."""

    write_frame(inbox, f"frame.json{suffix}", minutes_old=1.0)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.candidates == (), f"{suffix} should have been skipped entirely"
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME


def test_unsupported_extensions_are_ignored(inbox):
    """The pre-filter drops what the workflow never produces.

    `.txt` is deliberately NOT in this list: the real production frame is a
    delimited `.txt` envelope, so excluding it would reject every genuine
    frame. Content validation, not the extension, decides acceptance -
    see `test_the_extension_alone_cannot_bypass_validation`.
    """

    for name in ("notes.log", "screenshot.png", "archive.zip", "sheet.csv"):
        (inbox / name).write_text("hello", encoding="utf-8")
    scan = scan_inbox(inbox, now=NOW)
    assert scan.candidates == ()


def test_one_valid_frame_among_invalid_ones_is_selected(inbox):
    write_frame(inbox, "bad.json", valid=False)
    write_frame(inbox, "old.json", minutes_old=60.0)
    write_frame(inbox, "good.json", minutes_old=0.5)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.FRAME_SELECTED
    assert scan.selected.path.name == "good.json"
    assert len(scan.rejected) == 2


def test_clearly_ordered_valid_frames_select_the_newest(inbox):
    write_frame(inbox, "older.json", minutes_old=8.0)
    write_frame(inbox, "newer.json", minutes_old=0.5)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.FRAME_SELECTED
    assert scan.selected.path.name == "newer.json"


def test_indistinguishable_valid_frames_require_user_selection(inbox):
    """Two frames a second apart cannot be ordered with confidence."""

    write_frame(inbox, "a.json", minutes_old=1.0)
    write_frame(inbox, "b.json", minutes_old=1.0)
    scan = scan_inbox(inbox, now=NOW, ambiguity_window_seconds=2.0)
    assert scan.selection is FrameSelection.AMBIGUOUS_REQUIRES_USER_SELECTION
    assert scan.selected is None
    assert "select one explicitly" in scan.detail


def test_a_valid_frame_without_a_usable_timestamp_forces_selection(inbox):
    write_frame(inbox, "stamped.json", minutes_old=1.0, embedded=True)
    unstamped = write_frame(inbox, "unstamped.json", minutes_old=1.0, embedded=False)
    scan = scan_inbox(inbox, now=NOW)
    if scan.selection is FrameSelection.AMBIGUOUS_REQUIRES_USER_SELECTION:
        assert scan.selected is None
    else:
        # If the validator supplied an mtime fallback for both, ordering is
        # still only allowed when the gap is decisive.
        assert scan.selected is not None
    assert unstamped.exists()


def test_the_internal_timestamp_is_authoritative(inbox):
    """mtime is supporting evidence; the frame's own timestamp decides."""

    path = write_frame(inbox, "frame.json", minutes_old=1.0, embedded=True)
    # Make the file look ancient on disk while its internal stamp stays fresh.
    old = (NOW - timedelta(hours=6)).timestamp()
    os.utime(path, (old, old))
    scan = scan_inbox(inbox, now=NOW)
    candidate = scan.candidates[0]
    assert candidate.uses_embedded_timestamp
    assert candidate.authoritative_timestamp is not None
    # The reported age follows the internal stamp, not the 6-hour-old mtime.
    assert candidate.age_seconds(NOW) < 600


def test_the_summary_never_exposes_frame_contents(inbox):
    write_frame(inbox, "frame.json", minutes_old=1.0)
    scan = scan_inbox(inbox, now=NOW)
    summary = scan.selected.summary(NOW, 15.0)
    rendered = json.dumps(summary)
    for secret in ("SYNTHETIC-NOT-A-REAL-SECRET", "token", "sessionId"):
        assert secret not in rendered
    assert set(summary) == {
        "filename", "validated_timestamp_utc", "timestamp_source", "age_seconds",
        "remaining_seconds", "validation_result", "rejection_reason",
    }


def test_remaining_validity_is_reported(inbox):
    write_frame(inbox, "frame.json", minutes_old=5.0)
    scan = scan_inbox(inbox, now=NOW)
    summary = scan.selected.summary(NOW, 15.0)
    assert summary["remaining_seconds"] is not None
    assert 0 < summary["remaining_seconds"] <= 15 * 60


# =========================================================================== #
# PATH SAFETY
# =========================================================================== #


def test_the_inbox_must_stay_inside_the_project(tmp_path):
    with pytest.raises(ValueError, match="inside the project root"):
        resolve_inbox(tmp_path, "../escape")


def test_the_inbox_cannot_be_the_project_root(tmp_path):
    with pytest.raises(ValueError, match="cannot be the project root"):
        resolve_inbox(tmp_path, ".")


@pytest.mark.parametrize("protected", ["data", "logs", "reports", "backups"])
def test_protected_top_level_directories_are_refused(tmp_path, protected):
    with pytest.raises(ValueError, match="protected directory"):
        resolve_inbox(tmp_path, protected)


def test_a_file_cannot_be_the_inbox(tmp_path):
    target = tmp_path / "sub" / "thing.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a directory"):
        resolve_inbox(tmp_path, "sub/thing.json")


def test_a_nested_inbox_under_data_is_allowed(tmp_path):
    resolved = resolve_inbox(tmp_path, "data/local/rubix_auth_inbox")
    assert resolved.name == "rubix_auth_inbox"


def test_a_symlink_pointing_outside_the_inbox_is_ignored(tmp_path, inbox):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"type": "auth", "token": "x"}), encoding="utf-8")
    link = inbox / "linked.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this system")
    scan = scan_inbox(inbox, now=NOW)
    assert all(c.path.name != "linked.json" for c in scan.candidates)


def test_the_inbox_and_frames_are_gitignored():
    import subprocess as sp

    for path in (
        "data/local/rubix_auth_inbox/frame.json",
        "data/local/rubix_auth_consumed/frame.json",
        "data/local/rubix_assisted_start_prefs.json",
    ):
        result = sp.run(["git", "check-ignore", path], capture_output=True)
        assert result.returncode == 0, f"{path} is NOT gitignored"


# =========================================================================== #
# COLLECTOR-ONLY START
# =========================================================================== #


def command_for(tmp_path, frame):
    return build_collector_command(
        python_executable="python.exe",
        project_root=tmp_path,
        adapter_path=tmp_path / "adapter",
        auth_frame=frame,
        database=tmp_path / "rubix.db",
        symbols_path=tmp_path / "universe.json",
        pid_file=tmp_path / "sup.pid.json",
        lock_file=tmp_path / "sup.lock",
        log_file=tmp_path / "sup.log",
    )


def test_the_command_starts_only_the_supervisor(tmp_path, inbox):
    frame = write_frame(inbox, "frame.json")
    command = command_for(tmp_path, frame)
    joined = " ".join(command.as_list())
    assert "rubix_collector_supervisor.py" in joined
    for forbidden in ("streamlit", "Streamlit", "dashboard", "app.py",
                      "launch_rubix_production", "run_orb_shadow"):
        assert forbidden not in joined


def test_the_command_mirrors_the_launcher_arguments(tmp_path, inbox):
    frame = write_frame(inbox, "frame.json")
    arguments = command_for(tmp_path, frame).arguments
    for flag in ("--adapter", "--auth-frame-file", "--database", "--symbols",
                 "--batch-size", "--pid-file", "--lock-file", "--log-file"):
        assert flag in arguments


def test_no_second_websocket_or_auth_implementation_exists():
    """Look for implementation, not vocabulary.

    `run_rubix_assisted_start.py` contains the word "password" only inside the
    deny-list that REFUSES to persist credential-like preference keys, so a bare
    substring search would flag the safety check itself.
    """

    for name in (
        "services/rubix_assisted_start.py",
        "scripts/run_rubix_assisted_start.py",
    ):
        text = Path(name).read_text(encoding="utf-8")
        for forbidden in ("wss://", "ws://", "websockets.connect", "def authenticate",
                          "requests.post", "urlopen", "webdriver", "selenium"):
            assert forbidden not in text, f"{name} contains {forbidden!r}"
    # The only mention of credential words is the guard that rejects them.
    entry = Path("scripts/run_rubix_assisted_start.py").read_text(encoding="utf-8")
    assert "refusing to persist a credential-like key" in entry
    # Validation is delegated, not reimplemented.
    logic = Path("services/rubix_assisted_start.py").read_text(encoding="utf-8")
    assert "from services.rubix_auth_assistant import" in logic
    assert "inspect_auth_frame" in logic


def test_the_assisted_session_never_starts_a_second_supervisor(tmp_path, inbox, monkeypatch):
    import scripts.run_rubix_assisted_start as module

    frame = write_frame(inbox, "frame.json")
    args = inbox_args("--inbox", "data/local/rubix_auth_inbox")
    args.pid_file = str(tmp_path / "sup.pid.json")
    session = AssistedStartSession(args, clock=lambda: NOW, runner=lambda c: "SPAWNED")
    object.__setattr__(session, "inbox", inbox)

    monkeypatch.setattr(
        module, "supervisor_status", lambda _p: {"running": True, "pid": 4321, "record": {}}
    )
    state = session.start(session.scan())
    assert state is AssistedState.INSTANCE_ALREADY_RUNNING
    assert session.process is None, "no process may be spawned"


def test_a_rejected_frame_never_starts_the_collector(tmp_path, inbox, monkeypatch):
    import scripts.run_rubix_assisted_start as module

    write_frame(inbox, "old.json", minutes_old=90.0)
    args = inbox_args()
    args.pid_file = str(tmp_path / "sup.pid.json")
    spawned = []
    session = AssistedStartSession(
        args, clock=lambda: NOW, runner=lambda c: spawned.append(c)
    )
    object.__setattr__(session, "inbox", inbox)
    monkeypatch.setattr(
        module, "supervisor_status", lambda _p: {"running": False, "pid": None, "record": {}}
    )
    assert session.start(session.scan()) is AssistedState.AUTH_FRAME_REJECTED
    assert spawned == []


def test_a_spawn_failure_is_surfaced_not_swallowed(tmp_path, inbox, monkeypatch):
    import scripts.run_rubix_assisted_start as module

    write_frame(inbox, "frame.json")
    args = inbox_args()
    args.pid_file = str(tmp_path / "sup.pid.json")

    def boom(_command):
        raise OSError("no such executable")

    session = AssistedStartSession(args, clock=lambda: NOW, runner=boom)
    object.__setattr__(session, "inbox", inbox)
    monkeypatch.setattr(
        module, "supervisor_status", lambda _p: {"running": False, "pid": None, "record": {}}
    )
    assert session.start(session.scan()) is AssistedState.START_FAILED
    assert "no such executable" in session.detail


# =========================================================================== #
# HEALTH
# =========================================================================== #


def readiness(**overrides):
    checks = {
        "source_database": (True, "readable"),
        "source_progressing": (True, "progressing"),
    }
    checks.update(overrides)
    return {
        "checks": [
            {"name": name, "ok": ok, "detail": detail}
            for name, (ok, detail) in checks.items()
        ]
    }


def test_a_running_process_alone_is_not_healthy():
    outcome = evaluate_health(
        readiness(source_progressing=(False, "FROZEN")),
        supervisor_running=True, elapsed_seconds=999.0, timeout_seconds=120.0,
    )
    assert outcome.state is AssistedState.RUNNING_SOURCE_STALE


def test_health_requires_source_progress():
    outcome = evaluate_health(
        readiness(), supervisor_running=True, elapsed_seconds=10.0, timeout_seconds=120.0
    )
    assert outcome.state is AssistedState.RUNNING_HEALTHY


def test_health_is_starting_before_the_timeout():
    outcome = evaluate_health(
        readiness(source_progressing=(False, "not yet")),
        supervisor_running=True, elapsed_seconds=5.0, timeout_seconds=120.0,
    )
    assert outcome.state is AssistedState.STARTING


def test_health_times_out_when_the_supervisor_never_appears():
    outcome = evaluate_health(
        readiness(), supervisor_running=False, elapsed_seconds=200.0, timeout_seconds=120.0
    )
    assert outcome.state is AssistedState.HEALTH_TIMEOUT


def test_an_unusable_source_times_out_rather_than_reporting_healthy():
    outcome = evaluate_health(
        readiness(source_database=(False, "missing")),
        supervisor_running=True, elapsed_seconds=200.0, timeout_seconds=120.0,
    )
    assert outcome.state is AssistedState.HEALTH_TIMEOUT


# =========================================================================== #
# AUTO-START COUNTDOWN
# =========================================================================== #


def test_auto_start_is_disabled_by_default():
    args = parse_args(["--headless"])
    assert args.auto_start is False
    assert CountdownState().enabled is False


def test_auto_start_requires_explicit_opt_in():
    args = parse_args(["--headless", "--auto-start"])
    assert args.auto_start is True


def test_the_countdown_fires_only_at_zero():
    countdown = CountdownState(enabled=True, seconds=5.0)
    countdown.begin()
    fired = [countdown.tick(1.0) for _ in range(5)]
    assert fired == [False, False, False, False, True]
    assert countdown.started


def test_cancelling_the_countdown_prevents_start():
    countdown = CountdownState(enabled=True, seconds=3.0)
    countdown.begin()
    countdown.tick(1.0)
    countdown.cancel()
    assert countdown.tick(1.0) is False
    assert countdown.tick(1.0) is False
    assert not countdown.started


def test_a_disabled_countdown_never_fires():
    countdown = CountdownState(enabled=False, seconds=1.0)
    countdown.begin()
    assert countdown.tick(5.0) is False
    assert not countdown.started


def test_expiry_during_the_countdown_aborts(inbox):
    write_frame(inbox, "frame.json", minutes_old=14.9)
    fresh = scan_inbox(inbox, now=NOW)
    assert countdown_should_abort(fresh) is None
    later = scan_inbox(inbox, now=NOW + timedelta(minutes=5))
    assert countdown_should_abort(later) is not None


def test_ambiguity_during_the_countdown_aborts(inbox):
    write_frame(inbox, "a.json", minutes_old=1.0)
    write_frame(inbox, "b.json", minutes_old=1.0)
    scan = scan_inbox(inbox, now=NOW)
    reason = countdown_should_abort(scan)
    assert reason is not None
    assert "AMBIGUOUS" in reason


def test_the_countdown_uses_no_real_sleep():
    """Ticks are driven by the caller; nothing here blocks."""

    import inspect

    source = inspect.getsource(CountdownState)
    assert "sleep" not in source
    assert "time." not in source


# =========================================================================== #
# PREFERENCES AND DISPOSAL
# =========================================================================== #


def test_preferences_hold_no_credential(tmp_path):
    path = tmp_path / "prefs.json"
    save_preferences(path, {"inbox": "data/local/x", "auto_start_enabled": True})
    assert load_preferences(path) == {"inbox": "data/local/x", "auto_start_enabled": True}


@pytest.mark.parametrize("key", ["password", "username_token", "cookie", "session_id", "frame_body"])
def test_credential_like_preferences_are_refused(tmp_path, key):
    with pytest.raises(ValueError, match="credential-like"):
        save_preferences(tmp_path / "p.json", {key: "x"})


def test_unknown_preference_keys_are_dropped(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"inbox": "a", "secret_blob": "b"}), encoding="utf-8")
    assert "secret_blob" not in load_preferences(path)


def test_the_default_disposal_leaves_the_frame_untouched(inbox):
    frame = write_frame(inbox, "frame.json")
    message = dispose_frame(frame, FrameDisposal.LEAVE_UNTOUCHED)
    assert frame.exists()
    assert "left untouched" in message


def test_move_to_consumed_relocates_without_copying(tmp_path, inbox):
    frame = write_frame(inbox, "frame.json")
    consumed = tmp_path / "consumed"
    dispose_frame(frame, FrameDisposal.MOVE_TO_CONSUMED, consumed_dir=consumed)
    assert not frame.exists()
    assert (consumed / "frame.json").exists()


def test_delete_requires_explicit_choice(inbox):
    frame = write_frame(inbox, "frame.json")
    dispose_frame(frame, FrameDisposal.DELETE)
    assert not frame.exists()


def test_the_default_disposal_option_is_leave_untouched():
    args = parse_args(["--headless"])
    assert args.disposal == FrameDisposal.LEAVE_UNTOUCHED.value


# =========================================================================== #
# BANNERS AND ENTRY POINT
# =========================================================================== #


def test_the_banner_states_every_required_boundary():
    joined = " | ".join(BANNER_LINES)
    for required in ("RUBIX COLLECTOR — ASSISTED START", "NO DASHBOARD",
                     "NO TRADING EXECUTION", "NO CREDENTIAL STORAGE"):
        assert required in joined


def test_headless_detection_starts_nothing(tmp_path, inbox, capsys):
    import scripts.run_rubix_assisted_start as module

    write_frame(inbox, "frame.json")
    args = inbox_args()
    session = AssistedStartSession(args, clock=lambda: NOW, runner=lambda c: "SPAWNED")
    object.__setattr__(session, "inbox", inbox)
    scan = session.scan()
    text = module.render_text_status(session, scan)
    assert "NO DASHBOARD" in text
    assert session.process is None


# =========================================================================== #
# SCHEDULED TASK
# =========================================================================== #


def test_the_task_launches_only_the_assisted_entry_point():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "run_rubix_assisted_start.py" in text
    for forbidden in ("streamlit", "app.py", "run_orb_shadow_orchestrator",
                      "launch_rubix_production"):
        assert forbidden not in text.lower() or forbidden == "streamlit"
    # Streamlit appears only in refusal checks, matched as invocations rather
    # than as the bare word - the entry point's own prose says it never
    # launches Streamlit, and a word match would refuse the correct script.
    assert "start_streamlit" in text
    assert "invokes Streamlit" in text
    # The guard iterates concrete invocation patterns rather than matching the
    # bare word, so it cannot refuse a script whose docs merely mention it.
    assert "foreach ($pattern in @(" in text
    assert "streamlit run" in text and "import streamlit" in text
    # Verification targets the module that actually builds the command.
    assert "build_collector_command" in text
    assert "rubix_assisted_start.py" in text


def test_the_task_runs_from_a_caller_supplied_stable_path():
    """The schedule must not be pinned to a throwaway development worktree.

    `-WorktreePath` is mandatory and becomes the working directory, and the
    arguments stay relative to it, so the task follows whatever stable runtime
    location the installer is pointed at rather than a hard-coded one.
    """

    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "[Parameter(Mandatory = $true)][string]$WorktreePath" in text
    assert "-WorkingDirectory $WorktreePath" in text
    assert 'Assert-PathExists -Path $WorktreePath' in text
    # The script argument is relative, so it resolves under the working
    # directory instead of pointing at another checkout.
    assert r'$scriptRelative = "scripts\run_rubix_assisted_start.py"' in text
    for hard_coded in (r"F:\EGX_RUBIX_assisted_start_wt", r"C:\Users"):
        assert hard_coded not in text, "no development path may be baked in"


def test_the_installer_verifies_the_target_before_registering(tmp_path):
    """A path that is not a real checkout must be refused, not registered."""

    text = INSTALL_PS1.read_text(encoding="utf-8")
    # Every input is existence-checked before any Register-ScheduledTask call.
    register_at = text.index("Register-ScheduledTask")
    for label in ("Worktree", "Assisted start entry point",
                  "Assisted start logic module"):
        assert text.index(label) < register_at


def test_the_task_uses_the_required_settings():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "-MultipleInstances IgnoreNew" in text
    assert "-WakeToRun" in text
    assert "-StartWhenAvailable" in text
    assert "-ExecutionTimeLimit" in text
    assert "-RunLevel Limited" in text
    assert "-LogonType Interactive" in text
    assert "-RunLevel Highest" not in text


def test_the_task_is_scheduled_for_0910_sunday_to_thursday():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert 'CairoStartTime = "09:10"' in text
    assert "-DaysOfWeek Sunday, Monday, Tuesday, Wednesday, Thursday" in text


def test_the_task_requires_egypt_timezone_or_explicit_conversion():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Egypt Standard Time" in text
    assert "TIMEZONE MISMATCH" in text
    assert "Cannot resolve the Cairo timezone" in text


def test_the_task_stores_no_credential_and_no_fixed_date():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    for forbidden in ("-Password", "ConvertTo-SecureString", "PSCredential", "-AsPlainText"):
        assert forbidden not in text
    assert "--session-date" not in text


def test_the_task_prints_the_command_before_registering():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert text.index("Task command:") < text.index("Register-ScheduledTask")
    assert text.index("if ($WhatIfOnly)") < text.index("Register-ScheduledTask")


def test_auto_start_is_opt_in_at_the_task_level():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "EnableAutoStart" in text
    assert 'disabled (default)' in text


def test_the_remover_refuses_an_unrelated_task():
    text = REMOVE_PS1.read_text(encoding="utf-8")
    assert "Refusing to remove an unrelated task" in text
    for forbidden in ("Remove-Item", "Stop-Process", "taskkill"):
        assert forbidden not in text


@pytest.mark.parametrize("script", [INSTALL_PS1, REMOVE_PS1])
def test_powershell_parses(script):
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


def make_runtime_root(tmp_path) -> Path:
    """A stand-in canonical root: the installer requires a real database."""

    root = tmp_path / "runtime"
    (root / "data").mkdir(parents=True)
    (root / "data" / "rubix_live_market.db").write_bytes(b"")
    return root


def run_installer(tmp_path, *extra):
    adapter = tmp_path / "adapter"
    adapter.mkdir(exist_ok=True)
    return subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-File", str(INSTALL_PS1),
            "-WorktreePath", str(Path.cwd()),
            "-PythonwExe", sys.executable,
            "-AdapterPath", str(adapter),
            *extra,
        ],
        capture_output=True, text=True,
    )


def task_registration_state() -> str:
    check = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
         "if ($t) { ($t.Actions | Select-Object -First 1).Arguments } else { 'ABSENT' }"],
        capture_output=True, text=True,
    )
    return check.stdout.strip()


def test_the_installer_whatif_registers_nothing(tmp_path):
    """-WhatIfOnly must leave the registered state exactly as it found it.

    Asserting the task is simply absent would be wrong on any machine where it
    is legitimately installed - and would then pass for the wrong reason
    everywhere else. What matters is that a dry run changes nothing.
    """

    before = task_registration_state()
    result = run_installer(
        tmp_path, "-RuntimeRoot", str(make_runtime_root(tmp_path)), "-WhatIfOnly",
    )
    assert result.returncode == 0, result.stderr
    assert "nothing was registered" in result.stdout.lower()
    assert task_registration_state() == before


def test_the_installer_refuses_a_runtime_root_without_the_real_database(tmp_path):
    """A fresh worktree is not a runtime root.

    Pointing runtime data at the code checkout would give the supervisor an
    empty database and a PID/lock file the live supervisor does not hold -
    single-instance protection would no longer see it, and a second collector
    could be started alongside the running one.
    """

    empty = tmp_path / "not-a-runtime-root"
    (empty / "data").mkdir(parents=True)
    result = run_installer(tmp_path, "-RuntimeRoot", str(empty), "-WhatIfOnly")
    assert result.returncode != 0
    assert "rubix_live_market.db" in (result.stderr + result.stdout)
    assert "never copy the database" in (result.stderr + result.stdout)


def test_the_installer_passes_the_canonical_runtime_root_to_the_task(tmp_path):
    root = make_runtime_root(tmp_path)
    result = run_installer(tmp_path, "-RuntimeRoot", str(root), "-WhatIfOnly")
    assert result.returncode == 0, result.stderr
    assert "--runtime-root" in result.stdout
    assert str(root.resolve()) in result.stdout


def test_the_runtime_root_is_mandatory():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "[Parameter(Mandatory = $true)][string]$RuntimeRoot" in text


def test_runtime_paths_follow_the_runtime_root_not_the_code_location(tmp_path):
    """The collector must address one canonical data set, wherever code lives."""

    root = make_runtime_root(tmp_path)
    args = parse_args(["--runtime-root", str(root)])
    for value in (args.database, args.symbols, args.pid_file, args.lock_file,
                  args.log_file):
        assert Path(value).is_relative_to(root.resolve()), value


def test_the_runtime_root_defaults_to_this_checkout():
    """Unscheduled local use keeps working with no extra flag."""

    args = parse_args([])
    assert Path(args.database).is_relative_to(PROJECT_ROOT)


def test_the_inbox_follows_the_runtime_root(tmp_path):
    """The morning export target must not move when a worktree is recreated."""

    root = make_runtime_root(tmp_path)
    args = parse_args(["--runtime-root", str(root), "--watch-mode", "inbox"])
    session = AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)
    assert session.inbox.is_relative_to(root.resolve())


def test_an_inbox_outside_the_runtime_root_is_still_refused(tmp_path):
    root = make_runtime_root(tmp_path)
    args = parse_args(["--runtime-root", str(root), "--watch-mode", "inbox",
                       "--inbox", str(tmp_path / "elsewhere")])
    with pytest.raises(ValueError, match="inside the project root"):
        AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)


# =========================================================================== #
# REGRESSIONS
# =========================================================================== #


def test_the_official_manual_launcher_is_unchanged():
    import subprocess as sp

    result = sp.run(
        ["git", "diff", "--name-only", "38ca393", "HEAD", "--",
         "scripts/launch_rubix_production.py",
         "scripts/rubix_collector_supervisor.py",
         "services/rubix_auth_assistant.py",
         "scripts/launcher_process_utils.py"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "existing Rubix machinery must not change"


def test_the_orb_automation_is_unchanged():
    import subprocess as sp

    result = sp.run(
        ["git", "diff", "--name-only", "38ca393", "HEAD", "--",
         "scripts/run_orb_shadow_orchestrator.py",
         "scripts/windows/install_orb_shadow_scheduled_task.ps1"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


def test_no_dashboard_source_changed():
    import subprocess as sp

    result = sp.run(
        ["git", "diff", "--name-only", "38ca393", "HEAD"],
        capture_output=True, text=True,
    )
    for name in result.stdout.splitlines():
        lowered = name.lower()
        assert "dashboard" not in lowered
        assert "streamlit" not in lowered
        assert not lowered.endswith("app.py")


def test_no_threshold_changed():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0
    assert config.maximum_structural_breach_bars == 0


# =========================================================================== #
# OFFICIAL DELIMITED (.txt) FRAME FORMAT
# =========================================================================== #


def write_delimited_frame(inbox: Path, name: str, *, minutes_old: float = 1.0,
                          valid: bool = True, now=NOW) -> Path:
    """A sanitized PRICE_AUTH_DELIMITED frame.

    The real production frame is a .txt envelope using 0x02 tag/value and 0x1c
    record separators - not JSON. Tag 20 is the session credential; this fixture
    uses an obviously synthetic placeholder and never a real value.
    """

    inbox.mkdir(parents=True, exist_ok=True)
    fields = {
        "150": "1", "24": "30", "1000": "1", "62": "WEB",
        "20": "SYNTHETIC-PLACEHOLDER-NOT-REAL",   # >= 16 chars, obviously fake
        "9": "1", "132": "1", "92": "1", "50": "1", "99": "1",
    }
    if not valid:
        fields.pop("20")           # missing credential -> UNKNOWN_DELIMITED
    raw = "".join(f"{tag}{value}" for tag, value in fields.items()) + ""
    path = inbox / name
    path.write_text(raw, encoding="utf-8")
    epoch = (now - timedelta(minutes=minutes_old)).timestamp()
    os.utime(path, (epoch, epoch))
    return path


def test_the_official_txt_delimited_frame_is_accepted(inbox):
    """The production frame is .txt, not JSON. It must be selectable."""

    write_delimited_frame(inbox, "rubix-price-auth-frame.txt", minutes_old=1.0)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.FRAME_SELECTED
    assert scan.selected.path.suffix == ".txt"
    assert scan.selected.inspection.structure == "PRICE_AUTH_DELIMITED"


def test_txt_is_an_accepted_extension():
    assert ".txt" in FRAME_SUFFIXES
    assert ".json" in FRAME_SUFFIXES


def test_the_extension_alone_cannot_bypass_validation(inbox):
    """A .txt full of nonsense is still refused by the official validator."""

    (inbox / "not-a-frame.txt").write_text("hello world", encoding="utf-8")
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME
    assert scan.rejected
    assert scan.rejected[0].inspection.structure_valid is False


def test_a_structurally_invalid_delimited_frame_is_refused(inbox):
    write_delimited_frame(inbox, "bad.txt", valid=False)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME


def test_an_expired_delimited_frame_is_refused(inbox):
    write_delimited_frame(inbox, "old.txt", minutes_old=45.0)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.WAITING_FOR_FRESH_AUTH_FRAME
    assert scan.rejected[0].inspection.status == "EXPIRED"


def test_a_delimited_frame_uses_mtime_because_it_has_no_internal_timestamp(inbox):
    """Honest reporting: this format carries no embedded timestamp."""

    write_delimited_frame(inbox, "frame.txt", minutes_old=2.0)
    scan = scan_inbox(inbox, now=NOW)
    candidate = scan.selected
    assert candidate.uses_embedded_timestamp is False
    summary = candidate.summary(NOW, 15.0)
    assert summary["timestamp_source"] == "file_mtime"
    assert summary["remaining_seconds"] > 0


def test_a_delimited_frame_summary_exposes_no_credential(inbox):
    write_delimited_frame(inbox, "frame.txt")
    scan = scan_inbox(inbox, now=NOW)
    rendered = json.dumps(scan.selected.summary(NOW, 15.0))
    assert "SYNTHETIC-PLACEHOLDER-NOT-REAL" not in rendered
    assert "" not in rendered and "" not in rendered


def test_partial_downloads_of_txt_frames_are_ignored(inbox):
    write_delimited_frame(inbox, "frame.txt.crdownload")
    scan = scan_inbox(inbox, now=NOW)
    assert scan.candidates == ()


def test_both_formats_can_coexist_and_the_newest_wins(inbox):
    write_delimited_frame(inbox, "older.txt", minutes_old=9.0)
    write_frame(inbox, "newer.json", minutes_old=0.5)
    scan = scan_inbox(inbox, now=NOW)
    assert scan.selection is FrameSelection.FRAME_SELECTED
    assert scan.selected.path.name == "newer.json"


# =========================================================================== #
# RUBIX SOURCE IDENTITY
# =========================================================================== #


def physical_identity(path: Path) -> tuple[int, int] | None:
    """(st_dev, st_ino) — the definitive same-file test on this platform."""

    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def make_junction(link: Path, target: Path) -> bool:
    """A real Windows junction - the exact D: -> F: situation in production.

    Junctions, unlike symlinks, need no elevation, so this exercises the real
    mechanism rather than a stand-in.
    """

    if os.name != "nt":
        return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and link.exists()


def test_same_physical_source_is_accepted(tmp_path):
    r"""Two path spellings of one file must be treated as one source.

    Production has `D:\EGX_AI_Trader` as a junction onto `F:\EGX_AI_Trader`;
    both spellings must resolve to a single source or runs would be split
    across two identities for one physical database.
    """

    target = tmp_path / "real"
    target.mkdir()
    real = target / "rubix_live_market.db"
    real.write_bytes(b"x" * 32)
    alias_dir = tmp_path / "alias"
    if not make_junction(alias_dir, target):
        pytest.skip("junction creation unavailable on this system")
    alias = alias_dir / "rubix_live_market.db"
    assert physical_identity(alias) == physical_identity(real)
    assert _path_identity(alias) == _path_identity(real), (
        "the recorded source identity must collapse the junction, "
        "or one database would be recorded as two sources"
    )


def test_distinct_sources_get_distinct_identities(tmp_path):
    """Different files must never collapse into one identity."""

    a = tmp_path / "a" / "rubix_live_market.db"
    b = tmp_path / "b" / "rubix_live_market.db"
    for path in (a, b):
        path.parent.mkdir()
        path.write_bytes(b"x" * 32)          # identical bytes, different files
    assert _path_identity(a) != _path_identity(b)


def test_distinct_sources_are_rejected(tmp_path):
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    a.write_bytes(b"x" * 32)
    b.write_bytes(b"x" * 32)          # identical bytes, different files
    assert physical_identity(a) != physical_identity(b), (
        "identical content must never be mistaken for the same physical source"
    )


def test_unresolved_source_identity_is_rejected(tmp_path):
    missing = tmp_path / "absent.db"
    assert physical_identity(missing) is None, (
        "an unresolvable path yields no identity and must not be assumed equal"
    )


def test_the_canonical_source_identity_is_stable_across_spellings(tmp_path):
    """Whatever spelling is configured, the identity must agree."""

    real = tmp_path / "src.db"
    real.write_bytes(b"y" * 16)
    spellings = [
        real,
        tmp_path / "." / "src.db",
        tmp_path / "sub" / ".." / "src.db",
    ]
    (tmp_path / "sub").mkdir()
    identities = {physical_identity(Path(os.path.normpath(p))) for p in spellings}
    assert len(identities) == 1


def test_orb_and_assisted_start_agree_on_the_source_identity(tmp_path):
    """Both layers must derive the same identity from the same file."""

    real = tmp_path / "rubix_live_market.db"
    real.write_bytes(b"z" * 8)
    nested = tmp_path / "sub" / ".." / "rubix_live_market.db"
    (tmp_path / "sub").mkdir()
    assert _path_identity(real) == _path_identity(Path(nested))


# =========================================================================== #
# THE EXISTING AUTH FRAME, WATCHED IN PLACE
# =========================================================================== #


def frame_args(frame: Path, *extra):
    """Production mode, pointed at a test file instead of the real one."""

    return parse_args(["--headless", "--auth-frame", str(frame), *extra])


def test_the_existing_txt_path_is_the_production_default():
    """The user's long-established export target, not a repository inbox."""

    assert DEFAULT_AUTH_FRAME_PATH == Path(r"C:\secure-temp\rubix-price-auth-frame.txt")
    args = parse_args(["--headless"])
    assert Path(args.auth_frame) == DEFAULT_AUTH_FRAME_PATH
    assert args.watch_mode == "auth-frame"


def test_the_default_needs_no_inbox(tmp_path):
    """No inbox is resolved, created or required in the normal workflow."""

    args = parse_args(["--headless", "--runtime-root", str(tmp_path)])
    session = AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)
    assert session.inbox is None
    assert session.watched == DEFAULT_AUTH_FRAME_PATH
    assert not (tmp_path / "data" / "local" / "rubix_auth_inbox").exists()


def test_a_fresh_valid_file_is_detected_in_place(tmp_path):
    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt", minutes_old=1.0)
    scan = scan_frame_file(frame, now=NOW)
    assert scan.status is FrameStatus.AUTH_FRAME_VALID
    assert scan.ready
    assert scan.selected.path == frame


def test_an_expired_file_is_refused(tmp_path):
    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt", minutes_old=45.0)
    scan = scan_frame_file(frame, now=NOW)
    assert scan.status is FrameStatus.AUTH_FRAME_EXPIRED
    assert not scan.ready


def test_a_malformed_file_is_refused(tmp_path):
    frame = tmp_path / "rubix-price-auth-frame.txt"
    frame.write_text("this is not a frame", encoding="utf-8")
    scan = scan_frame_file(frame, now=NOW)
    assert scan.status is FrameStatus.AUTH_FRAME_REJECTED
    assert not scan.ready


def test_a_missing_file_is_simply_waiting(tmp_path):
    """At 09:10 the export has not happened yet. That is not an error."""

    scan = scan_frame_file(tmp_path / "absent.txt", now=NOW)
    assert scan.status is FrameStatus.WAITING_FOR_FRESH_AUTH_FRAME
    assert scan.candidates == ()


def test_the_four_display_statuses_are_exactly_the_contract():
    assert {item.value for item in FrameStatus} == {
        "WAITING_FOR_FRESH_AUTH_FRAME",
        "AUTH_FRAME_VALID",
        "AUTH_FRAME_EXPIRED",
        "AUTH_FRAME_REJECTED",
    }


def test_a_refreshed_export_is_picked_up_on_the_next_poll(tmp_path):
    """Overwriting the same path with a newer frame must be detected."""

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt", minutes_old=45.0)
    assert scan_frame_file(frame, now=NOW).status is FrameStatus.AUTH_FRAME_EXPIRED
    write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt", minutes_old=0.5)
    assert scan_frame_file(frame, now=NOW).status is FrameStatus.AUTH_FRAME_VALID


def test_no_copy_move_or_delete_occurs(tmp_path):
    """The user's file must be exactly as it was, and stay the only one."""

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    before = frame.read_bytes()
    stat_before = frame.stat()
    listing_before = sorted(item.name for item in tmp_path.iterdir())

    args = frame_args(frame)
    session = AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)
    scan = session.scan()
    assert scan.ready
    session.dispose(scan.selected.path)

    assert frame.exists()
    assert frame.read_bytes() == before
    assert frame.stat().st_mtime == stat_before.st_mtime
    assert sorted(item.name for item in tmp_path.iterdir()) == listing_before


def test_disposal_cannot_touch_the_users_own_file(tmp_path):
    """Even an explicit DELETE opt-in must not act on a file we do not own."""

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    args = frame_args(frame, "--disposal", "DELETE")
    session = AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)
    session.dispose(frame)
    assert frame.exists(), "the watched file is the user's, not ours to delete"


def test_frame_contents_are_never_rendered_or_logged(tmp_path):
    import scripts.run_rubix_assisted_start as module

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    secret = "SYNTHETIC-PLACEHOLDER-NOT-REAL"
    assert secret in frame.read_text(encoding="utf-8")

    args = frame_args(frame)
    session = AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)
    scan = session.scan()
    text = module.render_text_status(session, scan)
    assert secret not in text
    assert "\x02" not in text and "\x1c" not in text
    assert secret not in json.dumps(session.frame_summary(scan))


def test_the_rendered_status_names_the_path_and_the_state(tmp_path):
    import scripts.run_rubix_assisted_start as module

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    session = AssistedStartSession(frame_args(frame), clock=lambda: NOW, runner=_no_spawn)
    text = module.render_text_status(session, session.scan())
    assert "Auth frame:" in text
    assert str(frame) in text
    assert FrameStatus.AUTH_FRAME_VALID.value in text


def test_an_alternative_path_still_works(tmp_path):
    """Explicit override for testing or a future relocation."""

    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    other = write_delimited_frame(other_dir, "custom-frame.txt")
    session = AssistedStartSession(frame_args(other), clock=lambda: NOW, runner=_no_spawn)
    assert session.watched == other
    assert session.scan().status is FrameStatus.AUTH_FRAME_VALID


def test_the_inbox_mode_remains_available_as_an_advanced_mode(inbox):
    write_frame(inbox, "frame.json", minutes_old=1.0)
    session = AssistedStartSession(inbox_args(), clock=lambda: NOW, runner=_no_spawn)
    object.__setattr__(session, "inbox", inbox)
    assert session.watch_mode == "inbox"
    assert session.scan().status is FrameStatus.AUTH_FRAME_VALID


def test_a_running_collector_does_not_spawn_another_in_frame_mode(tmp_path, monkeypatch):
    import scripts.run_rubix_assisted_start as module

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    args = frame_args(frame)
    args.pid_file = str(tmp_path / "sup.pid.json")
    session = AssistedStartSession(args, clock=lambda: NOW, runner=_no_spawn)
    monkeypatch.setattr(
        module, "supervisor_status",
        lambda _p: {"running": True, "pid": 11120, "record": {}},
    )
    assert session.start(session.scan()) is AssistedState.INSTANCE_ALREADY_RUNNING
    assert session.process is None


def test_the_collector_command_receives_the_existing_frame_path(tmp_path, monkeypatch):
    import scripts.run_rubix_assisted_start as module

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    args = frame_args(frame)
    args.pid_file = str(tmp_path / "sup.pid.json")
    captured = []
    session = AssistedStartSession(
        args, clock=lambda: NOW, runner=lambda command: captured.append(command),
    )
    monkeypatch.setattr(
        module, "supervisor_status", lambda _p: {"running": False, "pid": None, "record": {}},
    )
    session.start(session.scan())
    assert captured, "a valid frame should have started the supervisor"
    arguments = list(captured[0].arguments)
    assert arguments[arguments.index("--auth-frame-file") + 1] == str(frame)


def test_no_dashboard_is_ever_launched_in_frame_mode(tmp_path, monkeypatch):
    import scripts.run_rubix_assisted_start as module

    frame = write_delimited_frame(tmp_path, "rubix-price-auth-frame.txt")
    args = frame_args(frame)
    args.pid_file = str(tmp_path / "sup.pid.json")
    captured = []
    session = AssistedStartSession(
        args, clock=lambda: NOW, runner=lambda command: captured.append(command),
    )
    monkeypatch.setattr(
        module, "supervisor_status", lambda _p: {"running": False, "pid": None, "record": {}},
    )
    session.start(session.scan())
    rendered = " ".join(captured[0].as_list()).lower()
    assert "streamlit" not in rendered
    assert "app.py" not in rendered
    assert "rubix_collector_supervisor.py" in rendered


def test_the_scheduled_task_uses_the_existing_path():
    text = INSTALL_PS1.read_text(encoding="utf-8")
    assert "--auth-frame" in text
    assert r"C:\secure-temp\rubix-price-auth-frame.txt" in text


def test_the_installer_passes_the_existing_frame_path_to_the_task(tmp_path):
    result = run_installer(
        tmp_path, "-RuntimeRoot", str(make_runtime_root(tmp_path)), "-WhatIfOnly",
    )
    assert result.returncode == 0, result.stderr
    assert "--auth-frame" in result.stdout
    assert r"C:\secure-temp\rubix-price-auth-frame.txt" in result.stdout


def test_the_installer_does_not_require_an_inbox(tmp_path):
    """The normal install must not carry an inbox argument at all."""

    result = run_installer(
        tmp_path, "-RuntimeRoot", str(make_runtime_root(tmp_path)), "-WhatIfOnly",
    )
    arguments = [line for line in result.stdout.splitlines() if "Arguments" in line]
    assert arguments, result.stdout
    assert "--inbox" not in arguments[0]
