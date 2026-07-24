"""Launcher-only regression tests; no trading or provider calculations run."""

from pathlib import Path

import pytest

import scripts.launcher_process_utils as process_utils
import scripts.launch_rubix_production as launcher


class _Process:
    def __init__(self, return_code=None):
        self.returncode = return_code

    def poll(self):
        return self.returncode


def test_corrupt_minimized_geometry_is_replaced_and_centered():
    geometry = process_utils.safe_window_geometry(
        "144x1+-32000+-32000", 1920, 1080
    )
    assert geometry == "1080x820+420+130"


def test_valid_visible_geometry_is_preserved():
    assert (
        process_utils.safe_window_geometry("1100x760+100+80", 1920, 1080)
        == "1100x760+100+80"
    )


def test_stale_pid_file_is_replaced_without_blocking(tmp_path, monkeypatch):
    pid_file = tmp_path / "launcher.pid.json"
    pid_file.write_text('{"pid": 999999, "started_at": "2020-01-01T00:00:00+00:00"}')
    monkeypatch.setattr(process_utils, "process_creation_time", lambda pid: None if pid == 999999 else 123.0)
    record = process_utils.claim_pid_file(pid_file, label="test launcher")
    assert record["pid"] != 999999
    process_utils.release_pid_file(pid_file, record["pid"])
    assert not pid_file.exists()


def test_live_matching_pid_prevents_duplicate(tmp_path, monkeypatch):
    pid_file = tmp_path / "launcher.pid.json"
    pid_file.write_text('{"pid": 123, "process_created_at": 50.0}')
    monkeypatch.setattr(process_utils, "process_creation_time", lambda _pid: 50.0)
    with pytest.raises(process_utils.InstanceAlreadyRunning):
        process_utils.claim_pid_file(pid_file, label="test launcher")


def test_streamlit_wait_returns_only_after_health_probe():
    probes = iter((False, False, True))
    ready, detail = process_utils.wait_for_streamlit(
        8501,
        _Process(),
        timeout=2,
        probe=lambda _port: next(probes),
        sleep=lambda _seconds: None,
    )
    assert ready is True
    assert "ready" in detail.lower()


def test_streamlit_wait_fails_loudly_when_child_exits():
    ready, detail = process_utils.wait_for_streamlit(
        8501,
        _Process(return_code=7),
        timeout=2,
        probe=lambda _port: False,
        sleep=lambda _seconds: None,
    )
    assert ready is False
    assert "code 7" in detail


def test_occupied_unowned_port_is_not_silently_reused(tmp_path, monkeypatch):
    supervisor = launcher.ProductionSupervisor(database=tmp_path / "rubix.db")
    monkeypatch.setattr(launcher, "port_is_open", lambda _port: True)
    monkeypatch.setattr(launcher, "read_pid_record", lambda _path: {})
    with pytest.raises(RuntimeError, match="occupied by another process"):
        supervisor.start_streamlit(8501)


def test_logged_command_redacts_authentication_file_even_with_spaces():
    rendered = launcher._safe_command(
        ["python.exe", "collector.py", "--auth-frame-file", "C:/secure temp/private frame.txt"]
    )
    assert "private frame" not in rendered
    assert "secure temp" not in rendered
    assert "[REDACTED]" in rendered
