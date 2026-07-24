"""Tests for the centralized .env loader (core/environment.py).

No real token is ever used; a synthetic value proves loading, OS priority, and that
the token is never leaked in status output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import core.environment as env

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAKE = "TESTTOKEN_ABCDEF0123456789"


def _write_env(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_env_file_loads(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    env._reset_for_tests()
    p = _write_env(tmp_path, f"EODHD_API_TOKEN={FAKE}\n")
    assert env.load_project_environment(env_path=p, force=True) is True
    assert os.getenv("EODHD_API_TOKEN") == FAKE
    assert env.is_eodhd_configured() is True


def test_os_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EODHD_API_TOKEN", "REAL_OS_VALUE")
    env._reset_for_tests()
    p = _write_env(tmp_path, f"EODHD_API_TOKEN={FAKE}\n")
    env.load_project_environment(env_path=p, force=True)          # override=False
    assert os.getenv("EODHD_API_TOKEN") == "REAL_OS_VALUE"        # OS priority preserved


def test_missing_env_file_is_safe(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    env._reset_for_tests()
    assert env.load_project_environment(env_path=tmp_path / "nope.env", force=True) is False


def test_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    env._reset_for_tests()
    _write_env(tmp_path, "SOMETHING_ELSE=1\n")
    env.load_project_environment(env_path=tmp_path / ".env", force=True)
    assert env.is_eodhd_configured() is False
    status = env.masked_eodhd_token_status()
    assert status["configured"] is False and status["fingerprint"] is None


def test_repeated_loading_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    env._reset_for_tests()
    p = _write_env(tmp_path, f"EODHD_API_TOKEN={FAKE}\n")
    env.load_project_environment(env_path=p, force=True)
    # a second (non-forced) call must not raise and must keep the value
    env.load_project_environment()
    env.load_project_environment()
    assert os.getenv("EODHD_API_TOKEN") == FAKE


def test_masked_status_never_leaks_token(tmp_path, monkeypatch):
    monkeypatch.setenv("EODHD_API_TOKEN", FAKE)
    env._reset_for_tests()
    status = env.masked_eodhd_token_status()
    blob = repr(status)
    assert FAKE not in blob                          # full token never present
    assert status["length"] == len(FAKE)
    assert status["fingerprint"].startswith("sha256:")
    assert status["source"] == "EODHD_API_TOKEN"


def test_loads_from_different_working_directory(tmp_path, monkeypatch):
    # The loader resolves the project root from its own file, not the CWD.
    monkeypatch.delenv("EODHD_API_TOKEN", raising=False)
    env._reset_for_tests()
    p = _write_env(tmp_path, f"EODHD_API_TOKEN={FAKE}\n")
    monkeypatch.chdir(tmp_path.parent)               # unrelated CWD
    assert env.load_project_environment(env_path=p, force=True) is True
    assert os.getenv("EODHD_API_TOKEN") == FAKE


def test_env_is_git_ignored():
    # the real .env must remain ignored by Git so the token is never committed
    result = subprocess.run(["git", "check-ignore", ".env"], cwd=PROJECT_ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == ".env"


def test_no_token_in_subprocess_output(monkeypatch):
    # a child process that loads env + prints status must not emit the token
    code = ("import core.environment as e;"
            "print(e.masked_eodhd_token_status());"
            "print(e.is_eodhd_configured())")
    proc = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                          capture_output=True, text=True,
                          env={**os.environ, "EODHD_API_TOKEN": FAKE})
    assert FAKE not in proc.stdout and FAKE not in proc.stderr
