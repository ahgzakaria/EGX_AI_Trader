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
FUTURE_CLOCK_SKEW_SECONDS = 60
EMBEDDED_TIMESTAMP_KEYS = {
    "TIMESTAMP",
    "TS",
    "TIME",
    "CREATED_AT",
    "CREATEDAT",
}

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
    structure_valid: bool | None = None
    structure_reason: str | None = None
    current_utc: str | None = None
    current_local: str | None = None
    local_utc_offset: str | None = None
    file_mtime_utc: str | None = None
    file_age_seconds: float | None = None
    file_age_valid: bool | None = None
    file_age_reason: str | None = None
    embedded_timestamp_found: bool = False
    embedded_timestamp_utc: str | None = None
    embedded_age_seconds: float | None = None
    validation_decision: str = "FAIL"
    validation_reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.status == AUTH_VALID


@dataclass(frozen=True)
class PreflightItem:
    name: str
    ok: bool
    message: str
    display_status: str | None = None


def _clock_context(value: datetime | None) -> tuple[datetime, datetime]:
    if value is None:
        current_utc = datetime.now(timezone.utc)
        return current_utc, current_utc.astimezone()
    if value.tzinfo is None:
        current_utc = value.replace(tzinfo=timezone.utc)
        return current_utc, current_utc
    return value.astimezone(timezone.utc), value


def _offset_text(value: datetime) -> str:
    seconds = int((value.utcoffset() or timezone.utc.utcoffset(value)).total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"


def _embedded_datetime(value) -> datetime | None:
    """Convert an explicit timestamp field without inspecting opaque credentials."""

    if isinstance(value, bool):
        return None
    numeric = None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        candidate = value.strip()
        try:
            numeric = float(candidate)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    if numeric is None:
        return None
    # Contemporary Unix milliseconds are around 1e12; Unix seconds are around
    # 1e9. Values outside 2000-2100 are not treated as authentication timestamps.
    seconds = numeric / 1000.0 if abs(numeric) >= 100_000_000_000 else numeric
    if not 946_684_800 <= seconds <= 4_102_444_800:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _find_embedded_timestamp(frame: dict) -> tuple[bool, datetime | None]:
    for key, value in frame.items():
        if str(key).strip().upper() in EMBEDDED_TIMESTAMP_KEYS:
            return True, _embedded_datetime(value)
    return False, None


def _inspection(status: str, message: str, **metadata) -> AuthFrameInspection:
    return AuthFrameInspection(
        status,
        message,
        validation_decision="PASS" if status == AUTH_VALID else "FAIL",
        validation_reason=message,
        **metadata,
    )


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
        return True, "PRICE_AUTH", "Recognized Rubix price authentication frame."
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
        return True, "PRICE_AUTH_DELIMITED", "Recognized Rubix price authentication frame."
    return False, "UNKNOWN_DELIMITED", "The file is not a Rubix price authentication frame."


def inspect_auth_frame(
    path,
    *,
    now: datetime | None = None,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    max_size_bytes: int = MAX_AUTH_BYTES,
) -> AuthFrameInspection:
    """Validate an auth file in memory and return only non-secret metadata."""

    current_utc, current_local = _clock_context(now)
    clock = {
        "current_utc": current_utc.isoformat(),
        "current_local": current_local.isoformat(),
        "local_utc_offset": _offset_text(current_local),
    }
    raw_path = str(path or "").strip()
    if not raw_path:
        message = "No authentication file selected."
        return _inspection(
            AUTH_MISSING,
            message,
            structure_valid=False,
            structure_reason=message,
            file_age_reason="Filesystem age was not checked because no file was selected.",
            **clock,
        )
    # A path chosen with Browse contains a separator.  This prevents accidentally
    # pasting sensitive frame contents into the visible path field.
    if not any(separator in raw_path for separator in ("\\", "/")):
        message = "Select the authentication file with Browse; do not paste its contents."
        return _inspection(
            AUTH_MISSING,
            message,
            path=raw_path,
            structure_valid=False,
            structure_reason=message,
            file_age_reason="Filesystem age was not checked because no file was selected.",
            **clock,
        )

    frame_path = Path(raw_path).expanduser()
    if not frame_path.is_file():
        message = "The selected authentication file does not exist."
        return _inspection(
            AUTH_MISSING,
            message,
            path=raw_path,
            filename=frame_path.name,
            structure_valid=False,
            structure_reason=message,
            file_age_reason="Filesystem age was not checked because the file does not exist.",
            **clock,
        )
    try:
        resolved = frame_path.resolve()
        stat = resolved.stat()
    except OSError:
        message = "The selected authentication file cannot be read."
        return _inspection(
            AUTH_INVALID,
            message,
            path=raw_path,
            filename=frame_path.name,
            structure_valid=False,
            structure_reason=message,
            file_age_reason="Filesystem age was not checked because file metadata is unavailable.",
            **clock,
        )

    size = int(stat.st_size)
    file_mtime_utc = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    age_seconds = (current_utc - file_mtime_utc).total_seconds()
    max_age_seconds = float(max_age_minutes) * 60.0
    file_age_valid = (
        age_seconds >= -float(FUTURE_CLOCK_SKEW_SECONDS)
        and age_seconds <= max_age_seconds
    )
    if age_seconds < -float(FUTURE_CLOCK_SKEW_SECONDS):
        file_age_reason = (
            f"Filesystem last-write time is {abs(age_seconds):.1f} seconds in the "
            f"future; tolerance is {FUTURE_CLOCK_SKEW_SECONDS} seconds."
        )
    elif age_seconds > max_age_seconds:
        file_age_reason = (
            f"Authentication file is expired: filesystem age is {age_seconds:.1f} seconds "
            f"({age_seconds / 60.0:.1f} minutes); maximum is "
            f"{max_age_seconds:.0f} seconds."
        )
    else:
        file_age_reason = (
            f"Filesystem age is {age_seconds:.1f} seconds "
            f"({age_seconds / 60.0:.1f} minutes); within the "
            f"{float(max_age_minutes):g}-minute limit."
        )
    safe = {
        "path": str(resolved),
        "filename": resolved.name,
        "size_bytes": size,
        "age_minutes": round(age_seconds / 60.0, 3),
        "file_mtime_utc": file_mtime_utc.isoformat(),
        "file_age_seconds": round(age_seconds, 3),
        "file_age_valid": file_age_valid,
        "file_age_reason": file_age_reason,
        **clock,
    }
    if size <= 0:
        message = "The selected authentication file is empty."
        return _inspection(
            AUTH_INVALID,
            message,
            structure_valid=False,
            structure_reason=message,
            **safe,
        )
    if size > int(max_size_bytes):
        message = "The selected authentication file is unexpectedly large."
        return _inspection(
            AUTH_INVALID,
            message,
            structure_valid=False,
            structure_reason=message,
            **safe,
        )

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
            status = AUTH_VALID if valid and file_age_valid else (
                AUTH_EXPIRED if valid else AUTH_INVALID
            )
            decision_reason = message if not valid or file_age_valid else file_age_reason
            return _inspection(
                status,
                decision_reason,
                structure=structure,
                structure_valid=valid,
                structure_reason=message,
                **safe,
            )
        frame = _frame_object(payload)
        if frame is None:
            message = "The authentication file must contain one JSON object."
            return _inspection(
                AUTH_INVALID,
                message,
                structure="INVALID_JSON_STRUCTURE",
                structure_valid=False,
                structure_reason=message,
                **safe,
            )
        embedded_found, embedded_utc = _find_embedded_timestamp(frame)
        embedded_age_seconds = (
            round((current_utc - embedded_utc).total_seconds(), 3)
            if embedded_utc is not None
            else None
        )
        valid, structure, message = _classify_structure(frame)
        status = AUTH_VALID if valid and file_age_valid else (
            AUTH_EXPIRED if valid else AUTH_INVALID
        )
        decision_reason = message if not valid or file_age_valid else file_age_reason
        return _inspection(
            status,
            decision_reason,
            structure=structure,
            structure_valid=valid,
            structure_reason=message,
            embedded_timestamp_found=embedded_found,
            embedded_timestamp_utc=embedded_utc.isoformat() if embedded_utc else None,
            embedded_age_seconds=embedded_age_seconds,
            **safe,
        )
    except (OSError, UnicodeError):
        message = "The selected authentication file cannot be read."
        return _inspection(
            AUTH_INVALID,
            message,
            structure_valid=False,
            structure_reason=message,
            **safe,
        )
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


def auth_preflight_items(inspection: AuthFrameInspection) -> tuple[PreflightItem, PreflightItem]:
    """Build independent structure and filesystem-age rows for the launcher."""

    structure_ok = inspection.structure_valid is True
    structure_message = inspection.structure_reason or inspection.message
    auth_item = PreflightItem(
        "Authentication File",
        structure_ok,
        structure_message,
        "PASS" if structure_ok else "FAIL",
    )
    if inspection.file_age_valid is None:
        age_item = PreflightItem(
            "File Age",
            True,
            inspection.file_age_reason or "Filesystem age was not checked.",
            "SKIPPED",
        )
    else:
        age_item = PreflightItem(
            "File Age",
            inspection.file_age_valid,
            inspection.file_age_reason or "Filesystem age check completed.",
            "PASS" if inspection.file_age_valid else "FAIL",
        )
    return auth_item, age_item


def safe_auth_diagnostics(inspection: AuthFrameInspection) -> dict:
    """Return only explicitly approved, secret-free authentication diagnostics."""

    return {
        "current_utc": inspection.current_utc,
        "current_local": inspection.current_local,
        "local_utc_offset": inspection.local_utc_offset,
        "selected_file_path": inspection.path,
        "filesystem_last_write_utc": inspection.file_mtime_utc,
        "filesystem_age_seconds": inspection.file_age_seconds,
        "embedded_timestamp_found": inspection.embedded_timestamp_found,
        "embedded_timestamp_utc": inspection.embedded_timestamp_utc,
        "embedded_age_seconds": inspection.embedded_age_seconds,
        "validation_decision": inspection.validation_decision,
        "validation_reason": inspection.validation_reason,
    }


def format_auth_diagnostics(inspection: AuthFrameInspection) -> str:
    """Render the approved diagnostic fields without authentication contents."""

    return json.dumps(
        safe_auth_diagnostics(inspection),
        ensure_ascii=False,
        sort_keys=True,
    )


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

    auth_items = auth_preflight_items(inspection)
    return [
        PreflightItem("Python", sys.version_info >= (3, 10), f"Python {sys.version_info.major}.{sys.version_info.minor} is available."),
        PreflightItem("Virtual Environment", venv_python.is_file(), "Project virtual environment found." if venv_python.is_file() else "Project virtual environment was not found."),
        PreflightItem("SQLite", sqlite3.sqlite_version_info >= (3, 0), f"SQLite {sqlite3.sqlite_version} is available."),
        PreflightItem("Rubix Database Path", db_ok, db_message),
        *auth_items,
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
