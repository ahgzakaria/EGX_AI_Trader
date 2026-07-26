"""Three-layer configuration and runtime state for the Rubix production launcher.

The launcher used to keep everything in one tracked file, so simply opening and closing
it rewrote ``config/rubix_launcher_settings.json`` — window position, a new launch record,
reordered keys — and left a permanent modification in Git. Runtime state is not source
code and must never dirty the repository.

Three layers, each with an explicit allow-list:

  1. ``config/rubix_launcher_settings.json``      TRACKED application defaults.
     Read-only at runtime. Nothing in this module ever writes to it.
  2. ``config/rubix_launcher_settings.local.json``  IGNORED local user/machine settings.
     Persistent choices that must survive a restart but must not enter Git
     (executable/database paths, last-used folder, theme, port).
  3. ``data/runtime/rubix_launcher_state.json``   IGNORED volatile session state.
     Window geometry and the recent-launch history.

Effective settings resolve defaults → local overrides → environment (only where the
launcher already supported an environment variable). Runtime state is kept separate and
is never merged into settings: an unknown key in any file is dropped rather than silently
becoming a setting.

Writes reuse the project's existing ``write_json_atomic`` (temp file in the destination
directory, flush + fsync, then ``os.replace``) and the existing cross-platform advisory
lock, so an interrupted or concurrent write can never leave a partial destination.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:                   # pragma: no cover - import guard
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import (  # noqa: E402 - project root above
    _lock_exclusive,
    _unlock,
    write_json_atomic,
)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

DEFAULTS_PATH = PROJECT_ROOT / "config" / "rubix_launcher_settings.json"
LOCAL_OVERRIDES_PATH = PROJECT_ROOT / "config" / "rubix_launcher_settings.local.json"
RUNTIME_STATE_PATH = PROJECT_ROOT / "data" / "runtime" / "rubix_launcher_state.json"

# --------------------------------------------------------------------------- #
# Allow-lists — an unknown key never becomes a setting or runtime value
# --------------------------------------------------------------------------- #

# Persistent settings. These may appear in the tracked defaults (as a documented default)
# and/or in the ignored local file (as this machine's choice).
SETTING_KEYS = (
    "adapter_path",       # user-selected Rubix feed directory (machine specific)
    "database_path",      # Rubix database location (machine specific; env-overridable)
    "last_auth_folder",   # directory the auth-file dialog opens in
    "theme",              # UI theme the user picked
    "streamlit_port",     # dashboard port
)

# Volatile session state written while the launcher runs.
RUNTIME_KEYS = (
    "window_geometry",
    "recent_launches",
)

# Settings the launcher already resolved from the environment before this split; the
# precedence order (defaults → local → environment) preserves that behaviour exactly.
ENVIRONMENT_OVERRIDES = {"database_path": "RUBIX_DB_PATH"}

RECENT_LAUNCH_LIMIT = 10                 # unchanged from the legacy launcher
DEFAULT_WINDOW_GEOMETRY = "1080x820"     # unchanged fallback used by the launcher

_LOCK_SUFFIX = ".lock"


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _read_json_dict(path) -> tuple[dict, str]:
    """Return ``(payload, warning)``. A malformed or unreadable file yields ``({}, msg)``.

    The warning names the file and the failure kind only — never file contents.
    """
    target = Path(path)
    if not target.is_file():
        return {}, ""
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {}, f"{target.name} could not be read ({type(error).__name__})"
    if not isinstance(payload, dict):
        return {}, f"{target.name} is not a JSON object"
    return payload, ""


def _select(payload: dict, keys) -> dict:
    return {key: payload[key] for key in keys if key in payload}


def load_default_settings(path=None) -> dict:
    """Tracked application defaults. Allow-listed; never written by this module."""
    payload, _warning = _read_json_dict(path or DEFAULTS_PATH)
    return _select(payload, SETTING_KEYS)


def load_local_overrides(path=None) -> dict:
    """Ignored local user/machine settings. Allow-listed."""
    payload, _warning = _read_json_dict(path or LOCAL_OVERRIDES_PATH)
    return _select(payload, SETTING_KEYS)


def resolve_effective_settings(*, defaults_path=None, local_path=None, env=None) -> dict:
    """Effective settings: defaults, then local overrides, then environment.

    Runtime state is deliberately absent — it can never override a setting.
    """
    environment = os.environ if env is None else env
    settings = load_default_settings(defaults_path)
    settings.update(load_local_overrides(local_path))
    for key, variable in ENVIRONMENT_OVERRIDES.items():
        value = str(environment.get(variable, "") or "").strip()
        if value:
            settings[key] = value
    return settings


def _quarantine_malformed(target: Path) -> str:
    """Rename a malformed runtime file for diagnosis instead of deleting it."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine = target.with_name(f"{target.name}.malformed-{stamp}")
    try:
        os.replace(target, quarantine)
    except OSError:
        return f"{target.name} is malformed and could not be renamed"
    return f"{target.name} was malformed and was preserved as {quarantine.name}"


def runtime_defaults() -> dict:
    """Safe starting runtime state."""
    return {"window_geometry": DEFAULT_WINDOW_GEOMETRY, "recent_launches": []}


def load_runtime_state(path=None, *, quarantine=True) -> tuple[dict, str]:
    """Return ``(state, warning)``.

    A malformed file never raises and never crashes the launcher: it is renamed for
    diagnosis (when ``quarantine``), safe defaults are returned, and a sanitized warning
    is reported. Unknown keys are dropped.
    """
    target = Path(path or RUNTIME_STATE_PATH)
    payload, warning = _read_json_dict(target)
    if warning and target.is_file():
        if quarantine:
            warning = _quarantine_malformed(target)
        return runtime_defaults(), warning

    state = runtime_defaults()
    selected = _select(payload, RUNTIME_KEYS)
    geometry = selected.get("window_geometry")
    if isinstance(geometry, str) and geometry.strip():
        state["window_geometry"] = geometry.strip()
    recent = selected.get("recent_launches")
    if isinstance(recent, list):
        state["recent_launches"] = [entry for entry in recent if isinstance(entry, dict)]
    return state, warning


# --------------------------------------------------------------------------- #
# Writing (atomic, advisory-locked)
# --------------------------------------------------------------------------- #

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(target: Path) -> threading.Lock:
    """One process-wide lock per destination path."""
    key = str(Path(target).resolve() if Path(target).parent.exists() else Path(target))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = _THREAD_LOCKS[key] = threading.Lock()
        return lock


class _FileLock:
    """Serialize writers to one destination: in-process first, then cross-process.

    The launcher writes from the Tk UI thread and from worker threads, and
    ``write_json_atomic`` derives its temp filename from the PID — so two threads in the
    same process would otherwise race on the same temp file. The ``threading.Lock``
    guarantees in-process serialization; the OS lock (via the project's existing
    cross-platform primitives) covers a second launcher process. An OS lock that cannot be
    taken never blocks the write — ``write_json_atomic`` is already atomic, so the worst
    case is last-writer-wins rather than a corrupt or partial file.
    """

    def __init__(self, target: Path):
        self.path = Path(target).with_name(Path(target).name + _LOCK_SUFFIX)
        self._fd = None
        self._thread_lock = _thread_lock_for(target)

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
            _lock_exclusive(self._fd)
        except OSError:
            self._release(locked=False)
        return self

    def __exit__(self, *exc):
        try:
            self._release(locked=True)
        finally:
            if self._thread_lock.locked():
                self._thread_lock.release()
        return False

    def _release(self, *, locked: bool):
        if self._fd is None:
            return
        try:
            if locked:
                _unlock(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None


def save_local_overrides(values: dict, path=None) -> dict:
    """Persist allow-listed local settings. Never touches the tracked defaults file."""
    target = Path(path or LOCAL_OVERRIDES_PATH)
    payload = _select(dict(values or {}), SETTING_KEYS)
    with _FileLock(target):
        write_json_atomic(target, payload)
    return payload


def save_runtime_state(state: dict, path=None) -> dict:
    """Persist allow-listed runtime state. Never touches settings files."""
    target = Path(path or RUNTIME_STATE_PATH)
    payload = _select(dict(state or {}), RUNTIME_KEYS)
    recent = payload.get("recent_launches")
    payload["recent_launches"] = (
        [entry for entry in recent if isinstance(entry, dict)][-RECENT_LAUNCH_LIMIT:]
        if isinstance(recent, list) else [])
    geometry = payload.get("window_geometry")
    payload["window_geometry"] = (geometry.strip() if isinstance(geometry, str)
                                  and geometry.strip() else DEFAULT_WINDOW_GEOMETRY)
    with _FileLock(target):
        write_json_atomic(target, payload)
    return payload


def update_runtime_state(changes: dict, path=None) -> dict:
    """Read-modify-write allow-listed runtime keys under one lock."""
    target = Path(path or RUNTIME_STATE_PATH)
    with _FileLock(target):
        state, _warning = load_runtime_state(target, quarantine=False)
        state.update(_select(dict(changes or {}), RUNTIME_KEYS))
        payload = _select(state, RUNTIME_KEYS)
        recent = payload.get("recent_launches")
        payload["recent_launches"] = (
            [entry for entry in recent if isinstance(entry, dict)][-RECENT_LAUNCH_LIMIT:]
            if isinstance(recent, list) else [])
        write_json_atomic(target, payload)
    return payload


def save_window_geometry(geometry: str, path=None) -> dict:
    """Persist the window geometry to the runtime-state file only."""
    text = str(geometry or "").strip()
    if not text:
        return update_runtime_state({}, path)
    return update_runtime_state({"window_geometry": text}, path)


def append_recent_launch(mode: str, result: str, path=None, *, when=None) -> dict:
    """Append one launch record to the runtime-state file only.

    Ordering, retention (last ten) and the timestamp meaning (UTC ISO-8601, recorded when
    the launch finished) are unchanged from the legacy launcher.
    """
    target = Path(path or RUNTIME_STATE_PATH)
    moment = when or datetime.now(timezone.utc)
    record = {"time": moment.isoformat(), "mode": str(mode), "result": str(result)}
    with _FileLock(target):
        state, _warning = load_runtime_state(target, quarantine=False)
        recent = list(state.get("recent_launches", []))
        recent.append(record)
        payload = {"window_geometry": state.get("window_geometry",
                                                DEFAULT_WINDOW_GEOMETRY),
                   "recent_launches": recent[-RECENT_LAUNCH_LIMIT:]}
        write_json_atomic(target, payload)
    return payload


# --------------------------------------------------------------------------- #
# One-time migration from the legacy mixed file
# --------------------------------------------------------------------------- #

def migrate_legacy_settings(*, legacy_path=None, defaults_path=None, local_path=None,
                            runtime_path=None) -> dict:
    """Split a legacy mixed settings file into the local and runtime layers.

    Idempotent by construction:
      * runtime keys are written only when the runtime file does not yet exist, so a
        second run cannot duplicate or reorder launch history or move the window;
      * local settings are filled in only for keys the local file does not already
        define, so a user's later choice is never overwritten;
      * the tracked defaults file is never rewritten.

    Returns a summary describing what (if anything) was migrated.
    """
    legacy = Path(legacy_path or DEFAULTS_PATH)
    defaults = Path(defaults_path or DEFAULTS_PATH)
    local = Path(local_path or LOCAL_OVERRIDES_PATH)
    runtime = Path(runtime_path or RUNTIME_STATE_PATH)

    summary = {"legacy_file": legacy.name, "runtime_migrated": False,
               "local_keys_migrated": [], "skipped": [], "warning": ""}

    payload, warning = _read_json_dict(legacy)
    if warning:
        summary["warning"] = warning
        return summary
    if not payload:
        summary["skipped"].append("legacy file absent or empty")
        return summary

    # -- volatile runtime state ------------------------------------------------ #
    legacy_runtime = _select(payload, RUNTIME_KEYS)
    if legacy_runtime and not runtime.exists():
        state = runtime_defaults()
        geometry = legacy_runtime.get("window_geometry")
        if isinstance(geometry, str) and geometry.strip():
            state["window_geometry"] = geometry.strip()
        recent = legacy_runtime.get("recent_launches")
        if isinstance(recent, list):
            # Order and retention are preserved exactly; obsolete process ids are not
            # carried over as anything live (the legacy file never stored any).
            state["recent_launches"] = [entry for entry in recent
                                        if isinstance(entry, dict)][-RECENT_LAUNCH_LIMIT:]
        save_runtime_state(state, runtime)
        summary["runtime_migrated"] = True
    elif legacy_runtime:
        summary["skipped"].append("runtime state already present")

    # -- persistent local settings --------------------------------------------- #
    tracked_defaults = load_default_settings(defaults) if defaults != legacy else {}
    existing_local, _ = _read_json_dict(local)
    existing_local = _select(existing_local, SETTING_KEYS)
    merged = dict(existing_local)
    for key in SETTING_KEYS:
        if key not in payload or key in existing_local:
            continue
        value = payload[key]
        # Only a value that actually differs from the tracked default is worth keeping.
        if key in tracked_defaults and tracked_defaults[key] == value:
            continue
        merged[key] = value
        summary["local_keys_migrated"].append(key)
    if summary["local_keys_migrated"]:
        save_local_overrides(merged, local)

    return summary


__all__ = [
    "DEFAULTS_PATH", "LOCAL_OVERRIDES_PATH", "RUNTIME_STATE_PATH",
    "SETTING_KEYS", "RUNTIME_KEYS", "ENVIRONMENT_OVERRIDES",
    "RECENT_LAUNCH_LIMIT", "DEFAULT_WINDOW_GEOMETRY",
    "load_default_settings", "load_local_overrides", "resolve_effective_settings",
    "load_runtime_state", "runtime_defaults", "save_local_overrides",
    "save_runtime_state", "update_runtime_state", "save_window_geometry",
    "append_recent_launch", "migrate_legacy_settings",
]
