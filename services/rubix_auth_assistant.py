"""Safe, local helpers for the manual Rubix authentication-frame workflow.

This module never authenticates, controls a browser, or persists frame data.  It
only validates a user-selected temporary JSON file in memory and returns safe
metadata suitable for a non-technical launcher UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Callable


AUTH_VALID = "VALID"
AUTH_EXPIRED = "EXPIRED"
AUTH_INVALID = "INVALID"
AUTH_MISSING = "MISSING"
MAX_AUTH_BYTES = 1_000_000
DEFAULT_MAX_AGE_MINUTES = 15

SAFE_CONFIG_KEYS = {
    "adapter_path",
    "database_path",
    "last_auth_folder",
    "window_geometry",
    "theme",
    "streamlit_port",
    "recent_launches",
}


@dataclass(frozen=True)
class AuthFrameInspection:
    """Secret-free result of inspecting one temporary auth-frame file."""

    status: str
    message: str
    path: str | None = None
    filename: str | None = None
    age_minutes: float | None = None
    size_bytes: int | None = None
    structure: str | None = None

    @property
    def valid(self) -> bool:
        return self.status == AUTH_VALID


@dataclass(frozen=True)
class PreflightItem:
    name: str
    ok: bool
    message: str


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _frame_object(payload):
    """Return the single JSON object without retaining or exposing its values."""

    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        return payload[0]
    return None


def _classify_structure(frame: dict) -> tuple[bool, str, str]:
    """Recognize the price-channel authentication envelope, not its secrets."""

    normalized = {str(key).strip().upper(): value for key, value in frame.items()}
    message_type = normalized.get("MT")

    if message_type in (0, "0"):
        return False, "HEARTBEAT", "The selected file contains a heartbeat, not authentication."
    if "HED" in normalized or "DAT" in normalized:
        return False, "METADATA", "The selected file contains market metadata, not authentication."
    if message_type in (10, "10") or "PRM" in normalized:
        return False, "SUBSCRIPTION", "The selected file contains a subscription message, not authentication."

    auth_keys = {
        "AUTH", "AUTHORIZATION", "TOKEN", "TKN", "SESSION", "SESSIONID",
        "SESSION_ID", "USER", "USERID", "USER_ID", "LOGIN",
    }
    # Rubix price authentication uses MT=-1.  Auth-like field names are also
    # accepted to remain compatible with documented adapter envelope variants.
    if message_type in (-1, "-1") or auth_keys.intersection(normalized):
        return True, "PRICE_AUTH", "Fresh Rubix price authentication frame."
    return False, "UNKNOWN", "The file is not a Rubix price authentication frame."


def _classify_delimited_structure(raw: str) -> tuple[bool, str, str]:
    """Validate the documented opaque Rubix price-auth envelope safely.

    Rubix price authentication is normally not JSON. Fields use tag/value
    separator 0x02 and record separator 0x1c. Only tag names and safe constant
    fields are inspected; credential values are never returned.
    """

    if "\x02" not in raw or "\x1c" not in raw:
        return False, "UNKNOWN", "The selected file is neither JSON nor a Rubix price authentication frame."
    parsed: dict[str, str] = {}
    for field in raw.split("\x1c"):
        if not field:
            continue
        tag, separator, value = field.partition("\x02")
        if not separator or not tag.isdigit() or not value:
            return False, "INVALID_DELIMITED", "The file has an incomplete Rubix authentication structure."
        parsed[tag] = value

    # These tags and safe constant values identify the price-channel request.
    # Tag 20 is the sensitive session credential; only its presence/minimum
    # length is checked, and its value is discarded with the local payload.
    required = {"150", "20", "24", "1000", "9", "132", "92", "50", "62", "99"}
    constants_match = (
        parsed.get("150") == "1"
        and parsed.get("24") == "30"
        and parsed.get("1000") == "1"
        and parsed.get("62", "").upper() == "WEB"
    )
    credential_present = len(parsed.get("20", "")) >= 16
    if required.issubset(parsed) and constants_match and credential_present:
        return True, "PRICE_AUTH_DELIMITED", "Fresh Rubix price authentication frame."
    return False, "UNKNOWN_DELIMITED", "The file is not a Rubix price authentication frame."


def inspect_auth_frame(
    path,
    *,
    now: datetime | None = None,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    max_size_bytes: int = MAX_AUTH_BYTES,
) -> AuthFrameInspection:
    """Validate an auth file in memory and return only non-secret metadata."""

    raw_path = str(path or "").strip()
    if not raw_path:
        return AuthFrameInspection(AUTH_MISSING, "No authentication file selected.")
    # A path chosen with Browse contains a separator.  This prevents accidentally
    # pasting sensitive frame contents into the visible path field.
    if not any(separator in raw_path for separator in ("\\", "/")):
        return AuthFrameInspection(
            AUTH_MISSING,
            "Select the authentication file with Browse; do not paste its contents.",
        )

    frame_path = Path(raw_path).expanduser()
    if not frame_path.is_file():
        return AuthFrameInspection(AUTH_MISSING, "The selected authentication file does not exist.")
    try:
        resolved = frame_path.resolve()
        stat = resolved.stat()
    except OSError:
        return AuthFrameInspection(AUTH_INVALID, "The selected authentication file cannot be read.")

    size = int(stat.st_size)
    safe = {
        "path": str(resolved),
        "filename": resolved.name,
        "size_bytes": size,
    }
    if size <= 0:
        return AuthFrameInspection(AUTH_INVALID, "The selected authentication file is empty.", **safe)
    if size > int(max_size_bytes):
        return AuthFrameInspection(AUTH_INVALID, "The selected authentication file is unexpectedly large.", **safe)

    age = (_utc(now) - datetime.fromtimestamp(stat.st_mtime, timezone.utc)).total_seconds() / 60
    safe["age_minutes"] = round(age, 2)
    if age < -1:
        return AuthFrameInspection(AUTH_EXPIRED, "The selected file has an invalid future timestamp.", **safe)
    if age > float(max_age_minutes):
        return AuthFrameInspection(AUTH_EXPIRED, "The selected authentication file is expired.", **safe)

    raw_payload = None
    payload = None
    frame = None
    try:
        # Contents exist only in local variables for structure validation.  No
        # result, exception, report, configuration, or log includes them.
        with resolved.open("r", encoding="utf-8-sig") as handle:
            raw_payload = handle.read()
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            valid, structure, message = _classify_delimited_structure(raw_payload)
            return AuthFrameInspection(
                AUTH_VALID if valid else AUTH_INVALID,
                message,
                structure=structure,
                **safe,
            )
        frame = _frame_object(payload)
        if frame is None:
            return AuthFrameInspection(
                AUTH_INVALID,
                "The authentication file must contain one JSON object.",
                structure="INVALID_JSON_STRUCTURE",
                **safe,
            )
        valid, structure, message = _classify_structure(frame)
        return AuthFrameInspection(
            AUTH_VALID if valid else AUTH_INVALID,
            message,
            structure=structure,
            **safe,
        )
    except (OSError, UnicodeError):
        return AuthFrameInspection(AUTH_INVALID, "The selected authentication file cannot be read.", **safe)
    finally:
        # Make the intended lifetime explicit.  The external collector owns the
        # selected file path and reads it independently when connecting.
        frame = None
        payload = None
        raw_payload = None


def validate_auth_frame_or_raise(path, **kwargs) -> Path:
    inspection = inspect_auth_frame(path, **kwargs)
    if not inspection.valid:
        raise ValueError(inspection.message)
    return Path(inspection.path).resolve()


def _database_path_check(path) -> tuple[bool, str]:
    if not str(path or "").strip():
        return False, "Choose a production database path."
    target = Path(path).expanduser()
    if target.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        return False, "Database path must end in .db, .sqlite, or .sqlite3."
    parent = target.resolve().parent
    if not parent.exists():
        return False, "The database folder does not exist yet."
    if not os.access(parent, os.W_OK):
        return False, "The database folder is not writable."
    return True, "Database path is ready."


def run_preflight(
    project_root,
    adapter_path,
    database_path,
    auth_path,
    port,
    *,
    now: datetime | None = None,
    module_available: Callable[[str], bool] | None = None,
) -> list[PreflightItem]:
    """Run read-only startup checks and return friendly, ordered results."""

    root = Path(project_root)
    available = module_available or (lambda name: importlib.util.find_spec(name) is not None)
    inspection = inspect_auth_frame(auth_path, now=now)
    db_ok, db_message = _database_path_check(database_path)
    adapter = Path(adapter_path).expanduser()
    adapter_files = ("adapter.py", "cli.py", "protocol.py", "storage.py", "__init__.py")
    adapter_ok = adapter.is_dir() and all((adapter / name).is_file() for name in adapter_files)
    venv_python = root / "venv" / "Scripts" / "python.exe"
    try:
        port_value = int(port)
        port_ok = 1 <= port_value <= 65535
    except (TypeError, ValueError):
        port_ok = False

    return [
        PreflightItem("Python", sys.version_info >= (3, 10), f"Python {sys.version_info.major}.{sys.version_info.minor} is available."),
        PreflightItem("Virtual Environment", venv_python.is_file(), "Project virtual environment found." if venv_python.is_file() else "Project virtual environment was not found."),
        PreflightItem("SQLite", sqlite3.sqlite_version_info >= (3, 0), f"SQLite {sqlite3.sqlite_version} is available."),
        PreflightItem("Rubix Database Path", db_ok, db_message),
        PreflightItem("Authentication File", inspection.valid, inspection.message),
        PreflightItem("File Age", inspection.valid, f"File age is {max(0.0, inspection.age_minutes or 0):.1f} minutes." if inspection.valid else inspection.message),
        PreflightItem("Launcher Configuration", port_ok, "Launcher port is valid." if port_ok else "Choose a port from 1 to 65535."),
        PreflightItem("Collector", adapter_ok and (root / "scripts" / "rubix_collector_supervisor.py").is_file(), "Rubix collector is ready." if adapter_ok else "Rubix collector files are incomplete."),
        PreflightItem("Streamlit", bool(available("streamlit")), "Streamlit is available." if available("streamlit") else "Streamlit is not installed in this environment."),
    ]


def load_launcher_preferences(path) -> dict:
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in SAFE_CONFIG_KEYS if key in payload}


def save_launcher_preferences(values: dict, path) -> dict:
    """Atomically persist only explicitly approved, non-secret preferences."""

    safe = {key: values[key] for key in SAFE_CONFIG_KEYS if key in values}
    recent = safe.get("recent_launches", [])
    safe["recent_launches"] = recent[-10:] if isinstance(recent, list) else []
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    return safe


def safe_inspection_dict(inspection: AuthFrameInspection) -> dict:
    """Expose safe metadata for tests/UI without ever exposing JSON contents."""

    return asdict(inspection)
