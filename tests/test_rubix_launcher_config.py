"""Tests for the Rubix launcher's three-layer config / runtime-state separation.

Every test writes to ``tmp_path`` only. The production paths are asserted to stay
untouched, and no test may append to the real recent-launch history.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services import rubix_launcher_config as config

PRODUCTION_PATHS = (config.DEFAULTS_PATH, config.LOCAL_OVERRIDES_PATH,
                    config.RUNTIME_STATE_PATH)


@pytest.fixture(autouse=True)
def _production_paths_are_untouched():
    """Fail loudly if any test writes to a real launcher file."""
    before = {path: (path.read_bytes() if path.is_file() else None)
              for path in PRODUCTION_PATHS}
    yield
    for path, snapshot in before.items():
        now = path.read_bytes() if path.is_file() else None
        assert now == snapshot, f"test modified production file {path}"


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _legacy_payload() -> dict:
    """A legacy mixed file: defaults + machine settings + volatile runtime state."""
    return {
        "adapter_path": r"C:\machine\rubix_feed",
        "database_path": r"D:\machine\rubix_live_market.db",
        "last_auth_folder": r"C:\secure-temp",
        "window_geometry": "1080x820+540+85",
        "theme": "System",
        "streamlit_port": 8501,
        "recent_launches": [
            {"time": f"2026-07-2{i}T08:00:00+00:00", "mode": "rubix", "result": "ready"}
            for i in range(1, 4)
        ],
    }


# --------------------------------------------------------------------------- #
# Layer 1 + 2: settings resolution
# --------------------------------------------------------------------------- #

def test_tracked_defaults_load_correctly(tmp_path):
    defaults = _write(tmp_path / "defaults.json",
                      {"theme": "System", "streamlit_port": 8501})
    assert config.load_default_settings(defaults) == {"theme": "System",
                                                     "streamlit_port": 8501}


def test_unknown_keys_never_become_settings(tmp_path):
    defaults = _write(tmp_path / "defaults.json",
                      {"streamlit_port": 8501, "rogue_key": "x", "_comment": "doc"})
    loaded = config.load_default_settings(defaults)
    assert loaded == {"streamlit_port": 8501}
    assert "rogue_key" not in loaded and "_comment" not in loaded


def test_local_overrides_take_precedence_over_defaults(tmp_path):
    defaults = _write(tmp_path / "defaults.json",
                      {"theme": "System", "streamlit_port": 8501})
    local = _write(tmp_path / "local.json",
                   {"theme": "clam", "streamlit_port": 8599})
    effective = config.resolve_effective_settings(defaults_path=defaults,
                                                 local_path=local, env={})
    assert effective["theme"] == "clam"
    assert effective["streamlit_port"] == 8599


def test_environment_overrides_take_precedence_where_supported(tmp_path):
    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    local = _write(tmp_path / "local.json", {"database_path": r"D:\local\rubix.db"})
    effective = config.resolve_effective_settings(
        defaults_path=defaults, local_path=local,
        env={"RUBIX_DB_PATH": r"D:\env\rubix.db"})
    assert effective["database_path"] == r"D:\env\rubix.db"
    # An empty environment variable must not erase the local choice.
    effective = config.resolve_effective_settings(defaults_path=defaults,
                                                 local_path=local,
                                                 env={"RUBIX_DB_PATH": "  "})
    assert effective["database_path"] == r"D:\local\rubix.db"


def test_missing_files_resolve_to_empty_settings(tmp_path):
    effective = config.resolve_effective_settings(
        defaults_path=tmp_path / "absent.json", local_path=tmp_path / "absent2.json",
        env={})
    assert effective == {}


# --------------------------------------------------------------------------- #
# Layer 3: runtime state stays separate
# --------------------------------------------------------------------------- #

def test_runtime_state_is_separate_from_settings(tmp_path):
    runtime = _write(tmp_path / "state.json", {
        "window_geometry": "900x700+10+10",
        "recent_launches": [{"time": "t", "mode": "rubix", "result": "ready"}],
        # A settings key in the runtime file must never leak into settings.
        "streamlit_port": 9999,
    })
    state, warning = config.load_runtime_state(runtime)
    assert warning == ""
    assert state["window_geometry"] == "900x700+10+10"
    assert len(state["recent_launches"]) == 1
    assert "streamlit_port" not in state

    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    effective = config.resolve_effective_settings(
        defaults_path=defaults, local_path=tmp_path / "absent.json", env={})
    assert effective["streamlit_port"] == 8501       # runtime file cannot override it


def test_saving_geometry_writes_only_the_runtime_file(tmp_path):
    runtime = tmp_path / "state.json"
    local = _write(tmp_path / "local.json", {"theme": "clam"})
    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    before = (defaults.read_bytes(), local.read_bytes())

    config.save_window_geometry("1200x900+64+64", runtime)

    assert json.loads(runtime.read_text(encoding="utf-8"))["window_geometry"] == \
        "1200x900+64+64"
    assert (defaults.read_bytes(), local.read_bytes()) == before


def test_appending_a_launch_writes_only_the_runtime_file(tmp_path):
    runtime = tmp_path / "state.json"
    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    local = _write(tmp_path / "local.json", {"theme": "clam"})
    before = (defaults.read_bytes(), local.read_bytes())

    config.append_recent_launch("rubix", "ready", runtime)
    config.append_recent_launch("research", "ready", runtime)

    payload = json.loads(runtime.read_text(encoding="utf-8"))
    assert [r["mode"] for r in payload["recent_launches"]] == ["rubix", "research"]
    assert [r["result"] for r in payload["recent_launches"]] == ["ready", "ready"]
    assert set(payload) == {"window_geometry", "recent_launches"}
    assert (defaults.read_bytes(), local.read_bytes()) == before


def test_recent_launch_retention_and_order_are_unchanged(tmp_path):
    runtime = tmp_path / "state.json"
    for index in range(config.RECENT_LAUNCH_LIMIT + 5):
        config.append_recent_launch("rubix", f"r{index}", runtime)
    recent = json.loads(runtime.read_text(encoding="utf-8"))["recent_launches"]
    assert len(recent) == config.RECENT_LAUNCH_LIMIT == 10
    # Oldest trimmed from the front; newest last — same as the legacy launcher.
    assert recent[0]["result"] == "r5"
    assert recent[-1]["result"] == f"r{config.RECENT_LAUNCH_LIMIT + 4}"


def test_appending_a_launch_preserves_the_saved_geometry(tmp_path):
    runtime = tmp_path / "state.json"
    config.save_window_geometry("1000x800+5+5", runtime)
    config.append_recent_launch("rubix", "ready", runtime)
    payload = json.loads(runtime.read_text(encoding="utf-8"))
    assert payload["window_geometry"] == "1000x800+5+5"


def test_loading_configuration_never_appends_a_launch_record(tmp_path):
    runtime = _write(tmp_path / "state.json", {"window_geometry": "900x700+0+0",
                                               "recent_launches": []})
    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    config.resolve_effective_settings(defaults_path=defaults,
                                      local_path=tmp_path / "absent.json", env={})
    config.load_runtime_state(runtime)
    assert json.loads(runtime.read_text(encoding="utf-8"))["recent_launches"] == []


# --------------------------------------------------------------------------- #
# Local settings writes
# --------------------------------------------------------------------------- #

def test_user_settings_write_only_to_the_local_override_file(tmp_path):
    local = tmp_path / "local.json"
    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    runtime = _write(tmp_path / "state.json", {"window_geometry": "900x700+0+0",
                                               "recent_launches": []})
    before = (defaults.read_bytes(), runtime.read_bytes())

    saved = config.save_local_overrides({
        "adapter_path": r"C:\feed", "theme": "clam", "streamlit_port": 8599,
        # Runtime and unknown keys must be dropped, not persisted here.
        "window_geometry": "1x1+0+0", "recent_launches": [{"mode": "x"}], "rogue": 1,
    }, local)

    assert set(saved) == {"adapter_path", "theme", "streamlit_port"}
    on_disk = json.loads(local.read_text(encoding="utf-8"))
    assert "window_geometry" not in on_disk and "recent_launches" not in on_disk
    assert "rogue" not in on_disk
    assert (defaults.read_bytes(), runtime.read_bytes()) == before


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #

def test_legacy_mixed_file_migrates_without_data_loss(tmp_path):
    legacy = _write(tmp_path / "legacy.json", _legacy_payload())
    defaults = _write(tmp_path / "defaults.json", {"theme": "System",
                                                  "streamlit_port": 8501})
    local, runtime = tmp_path / "local.json", tmp_path / "state.json"

    summary = config.migrate_legacy_settings(legacy_path=legacy, defaults_path=defaults,
                                             local_path=local, runtime_path=runtime)

    assert summary["runtime_migrated"] is True
    state = json.loads(runtime.read_text(encoding="utf-8"))
    source = _legacy_payload()
    assert state["window_geometry"] == source["window_geometry"]
    assert state["recent_launches"] == source["recent_launches"]

    persisted = json.loads(local.read_text(encoding="utf-8"))
    assert persisted["adapter_path"] == source["adapter_path"]
    assert persisted["database_path"] == source["database_path"]
    assert persisted["last_auth_folder"] == source["last_auth_folder"]
    # Values equal to the tracked defaults are not duplicated into the local file.
    assert "theme" not in persisted and "streamlit_port" not in persisted
    # Runtime keys never appear in the settings layer, and vice versa.
    assert not {"window_geometry", "recent_launches"} & set(persisted)
    assert not set(config.SETTING_KEYS) & set(state)


def test_migration_never_rewrites_the_tracked_defaults_file(tmp_path):
    legacy = _write(tmp_path / "legacy.json", _legacy_payload())
    defaults = _write(tmp_path / "defaults.json", {"theme": "System",
                                                   "streamlit_port": 8501})
    before = defaults.read_bytes()
    config.migrate_legacy_settings(legacy_path=legacy, defaults_path=defaults,
                                   local_path=tmp_path / "local.json",
                                   runtime_path=tmp_path / "state.json")
    assert defaults.read_bytes() == before


def test_migration_is_idempotent(tmp_path):
    legacy = _write(tmp_path / "legacy.json", _legacy_payload())
    defaults = _write(tmp_path / "defaults.json", {"theme": "System",
                                                   "streamlit_port": 8501})
    local, runtime = tmp_path / "local.json", tmp_path / "state.json"

    config.migrate_legacy_settings(legacy_path=legacy, defaults_path=defaults,
                                   local_path=local, runtime_path=runtime)
    first = (runtime.read_bytes(), local.read_bytes(), defaults.read_bytes())

    for _ in range(3):
        summary = config.migrate_legacy_settings(legacy_path=legacy,
                                                 defaults_path=defaults,
                                                 local_path=local, runtime_path=runtime)
        assert summary["runtime_migrated"] is False
        assert "runtime state already present" in summary["skipped"]

    assert (runtime.read_bytes(), local.read_bytes(), defaults.read_bytes()) == first
    state = json.loads(runtime.read_text(encoding="utf-8"))
    assert len(state["recent_launches"]) == len(_legacy_payload()["recent_launches"])


def test_migration_does_not_overwrite_a_later_user_choice(tmp_path):
    legacy = _write(tmp_path / "legacy.json", _legacy_payload())
    defaults = _write(tmp_path / "defaults.json", {"streamlit_port": 8501})
    local = _write(tmp_path / "local.json", {"adapter_path": r"C:\chosen\later"})
    config.migrate_legacy_settings(legacy_path=legacy, defaults_path=defaults,
                                   local_path=local, runtime_path=tmp_path / "s.json")
    assert json.loads(local.read_text(encoding="utf-8"))["adapter_path"] == \
        r"C:\chosen\later"


def test_migration_keeps_unknown_user_settings_out_of_the_layers(tmp_path):
    payload = _legacy_payload()
    payload["unrecognised_user_key"] = "kept in the legacy backup only"
    legacy = _write(tmp_path / "legacy.json", payload)
    local, runtime = tmp_path / "local.json", tmp_path / "state.json"
    config.migrate_legacy_settings(legacy_path=legacy,
                                   defaults_path=tmp_path / "defaults.json",
                                   local_path=local, runtime_path=runtime)
    assert "unrecognised_user_key" not in json.loads(local.read_text(encoding="utf-8"))
    assert "unrecognised_user_key" not in json.loads(runtime.read_text(encoding="utf-8"))
    # The original legacy file (the preserved backup) is never modified.
    assert json.loads(legacy.read_text(encoding="utf-8")) == payload


def test_migration_of_an_absent_legacy_file_is_a_noop(tmp_path):
    summary = config.migrate_legacy_settings(legacy_path=tmp_path / "absent.json",
                                             defaults_path=tmp_path / "d.json",
                                             local_path=tmp_path / "l.json",
                                             runtime_path=tmp_path / "s.json")
    assert summary["runtime_migrated"] is False
    assert not (tmp_path / "l.json").exists() and not (tmp_path / "s.json").exists()


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #

def test_malformed_runtime_json_does_not_crash_and_is_quarantined(tmp_path):
    runtime = tmp_path / "state.json"
    runtime.write_text("{ this is not json", encoding="utf-8")

    state, warning = config.load_runtime_state(runtime)

    assert state == config.runtime_defaults()
    assert warning and "malformed" in warning.lower()
    assert not runtime.exists()                       # renamed, not deleted
    preserved = list(tmp_path.glob("state.json.malformed-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "{ this is not json"


def test_malformed_runtime_warning_carries_no_file_contents(tmp_path):
    runtime = tmp_path / "state.json"
    runtime.write_text('{"token": "super-secret-value", ', encoding="utf-8")
    _state, warning = config.load_runtime_state(runtime)
    assert "super-secret-value" not in warning and "token" not in warning


def test_runtime_state_survives_a_non_dict_payload(tmp_path):
    runtime = _write(tmp_path / "state.json", {})
    runtime.write_text("[1, 2, 3]", encoding="utf-8")
    state, warning = config.load_runtime_state(runtime)
    assert state == config.runtime_defaults()
    assert "not a JSON object" in warning or "malformed" in warning.lower()


def test_missing_runtime_directory_is_created_safely(tmp_path):
    runtime = tmp_path / "deep" / "nested" / "state.json"
    config.save_window_geometry("1000x800+1+1", runtime)
    assert runtime.is_file()
    assert json.loads(runtime.read_text(encoding="utf-8"))["window_geometry"] == \
        "1000x800+1+1"


def test_interrupted_write_leaves_no_partial_destination(tmp_path, monkeypatch):
    runtime = tmp_path / "state.json"
    config.save_runtime_state({"window_geometry": "900x700+0+0",
                               "recent_launches": []}, runtime)
    good = runtime.read_bytes()

    real_replace = os.replace

    def _fail_replace(src, dst):
        raise OSError("simulated interruption before the atomic replace")

    monkeypatch.setattr(os, "replace", _fail_replace)
    with pytest.raises(OSError):
        config.save_window_geometry("1x1+0+0", runtime)
    monkeypatch.setattr(os, "replace", real_replace)

    # The destination still holds the last complete document, and no temp file lingers.
    assert runtime.read_bytes() == good
    assert not list(tmp_path.glob("state.json.tmp*"))


def test_concurrent_writers_never_produce_a_partial_file(tmp_path):
    """Regression: a PID-derived temp name made two threads collide on the same file."""
    runtime = tmp_path / "state.json"
    config.save_runtime_state(config.runtime_defaults(), runtime)
    import threading

    errors: list[BaseException] = []

    def worker(index):
        try:
            for _ in range(10):
                config.append_recent_launch("rubix", f"w{index}", runtime)
        except BaseException as error:       # noqa: BLE001 - surfaced as a test failure
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent writers raised: {errors!r}"
    payload = json.loads(runtime.read_text(encoding="utf-8"))   # parses => not partial
    assert len(payload["recent_launches"]) == config.RECENT_LAUNCH_LIMIT
    assert set(payload) == {"window_geometry", "recent_launches"}
    assert not list(tmp_path.glob("state.json.tmp*"))


# --------------------------------------------------------------------------- #
# Production paths and posture
# --------------------------------------------------------------------------- #

def test_production_paths_are_the_documented_three_layers():
    assert config.DEFAULTS_PATH.name == "rubix_launcher_settings.json"
    assert config.DEFAULTS_PATH.parent.name == "config"
    assert config.LOCAL_OVERRIDES_PATH.name == "rubix_launcher_settings.local.json"
    assert config.LOCAL_OVERRIDES_PATH.parent.name == "config"
    assert config.RUNTIME_STATE_PATH.name == "rubix_launcher_state.json"
    assert config.RUNTIME_STATE_PATH.parent.name == "runtime"
    assert config.RUNTIME_STATE_PATH.parent.parent.name == "data"


def test_tracked_defaults_file_contains_no_runtime_or_machine_state():
    payload = json.loads(config.DEFAULTS_PATH.read_text(encoding="utf-8"))
    for key in config.RUNTIME_KEYS:
        assert key not in payload, f"{key} must not be tracked"
    for key in ("adapter_path", "database_path", "last_auth_folder"):
        assert key not in payload, f"{key} is machine specific and must not be tracked"


def test_no_module_writes_the_tracked_defaults_file():
    """Static guarantee: the config module never passes DEFAULTS_PATH to a writer."""
    source = Path(config.__file__).read_text(encoding="utf-8")
    for writer in ("write_json_atomic(DEFAULTS_PATH", "save_runtime_state(.*DEFAULTS",
                   "save_local_overrides(.*DEFAULTS"):
        assert writer not in source
    launcher = (Path(config.__file__).resolve().parents[1] / "scripts"
                / "launch_rubix_production.py").read_text(encoding="utf-8")
    # The launcher may reference the tracked path, but never hand it to a writer.
    assert "save_launcher_preferences(" not in launcher
    assert "LAUNCHER_CONFIG)" not in launcher


def test_launcher_uses_the_three_layer_api():
    launcher = (Path(config.__file__).resolve().parents[1] / "scripts"
                / "launch_rubix_production.py").read_text(encoding="utf-8")
    for symbol in ("resolve_effective_settings", "load_runtime_state",
                   "append_recent_launch", "save_window_geometry",
                   "save_local_overrides", "migrate_legacy_settings"):
        assert symbol in launcher, f"launcher must use {symbol}"
