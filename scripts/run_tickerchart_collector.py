"""Compatibility entry point for the existing external TickerChart adapter.

The signed session observed in production uses ``/streamhubws/`` while the
earlier audit observed ``/ws/``.  This shim reuses the external collector and
all of its protocol/storage code unchanged, replacing only its overly narrow
URL-path validator with EGX AI Trader's domain-restricted validator.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launch_egx_ai_trader import LauncherError, validate_streamer_url  # noqa: E402


def load_external_adapter(adapter_path):
    directory = Path(adapter_path).expanduser().resolve()
    adapter_file = directory / "adapter.py"
    if not adapter_file.is_file():
        raise LauncherError(f"External adapter not found: {adapter_file}")
    # The external adapter intentionally uses local absolute imports
    # (``protocol``/``storage``), so expose only its own folder for import.
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location(
        "egx_external_tickerchart_adapter", adapter_file
    )
    if spec is None or spec.loader is None:
        raise LauncherError("Unable to load the external TickerChart adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._validate_url = validate_streamer_url
    return module


def main():
    adapter_path = os.getenv("TICKERCHART_ADAPTER_PATH", "").strip()
    if not adapter_path:
        raise SystemExit("TICKERCHART_ADAPTER_PATH is not configured")
    external = load_external_adapter(adapter_path)
    external.main()


if __name__ == "__main__":
    main()
