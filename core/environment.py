"""Centralized environment/.env loading for EGX AI Trader.

One place loads ``.env`` (project root) so scattered ``load_dotenv()`` calls are
never needed. Real OS environment variables always win (``override=False``), the
loader is idempotent and working-directory independent, and it NEVER prints, logs,
returns, or otherwise exposes the full EODHD token — only a masked status.

Call ``load_project_environment()`` once at application startup (app.py) and once at
the top of standalone scheduled/CLI scripts that do not enter through app.py. Provider
helpers here call it lazily too, so a missed call can never silently drop the token.

This module touches no strategy, scoring, provider selection, or execution logic.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILENAME = ".env"

_loaded = False


def project_root() -> Path:
    return PROJECT_ROOT


def _manual_load(path: Path, override: bool) -> None:
    """Fallback KEY=VALUE parser when python-dotenv is unavailable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = val


def load_project_environment(*, override: bool = False, env_path=None, force: bool = False) -> bool:
    """Load the project ``.env`` once. Returns True if a file was found.

    ``override=False`` keeps real OS environment variables authoritative. Safe to call
    repeatedly (no-op after the first successful load unless ``force=True``). Works from
    any working directory because the path is resolved from this module's location.
    """
    global _loaded
    if _loaded and not force:
        return True
    path = Path(env_path) if env_path else (PROJECT_ROOT / ENV_FILENAME)
    found = path.is_file()
    if found:
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=str(path), override=override)
        except Exception:
            _manual_load(path, override)
    _loaded = True
    return found


def _eodhd_token():
    """Internal only — never returned to callers or logged."""
    return os.getenv("EODHD_API_TOKEN") or os.getenv("EODHD_API_KEY")


def is_eodhd_configured() -> bool:
    """True when a non-empty EODHD token is available (never reveals it)."""
    load_project_environment()
    return bool(_eodhd_token())


def masked_eodhd_token_status() -> dict:
    """Safe, non-secret status of the EODHD token (length + sha256 fingerprint).

    Never includes any character of the token itself — safe to log or display.
    """
    load_project_environment()
    token = _eodhd_token()
    if not token:
        return {"configured": False, "length": 0, "fingerprint": None, "source": None}
    source = "EODHD_API_TOKEN" if os.getenv("EODHD_API_TOKEN") else "EODHD_API_KEY"
    return {
        "configured": True,
        "length": len(token),
        "fingerprint": "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:10],
        "source": source,
    }


def _reset_for_tests():
    """Clear the idempotency flag (tests only)."""
    global _loaded
    _loaded = False
