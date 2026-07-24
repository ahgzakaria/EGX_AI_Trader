"""Immutable frozen Yahoo-seed store for EODHD-unsupported symbols.

The operational local route must read a **frozen** historical bootstrap, never the
live ``market_data_cache.sqlite`` that the (now-disabled) daily Yahoo refresh task kept
advancing over the network. This store is that frozen snapshot: a one-time CSV capture
per symbol plus a manifest recording when it was frozen, its row count, date span and a
content hash. After freezing, current research extends each symbol **only** via the
Rubix Daily Bridge — Yahoo never touches these bars again.

Freezing is explicit (``scripts/freeze_yahoo_seed.py``); reading is side-effect-free and
performs no network call.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FROZEN_DIR = Path("data/frozen_yahoo_seed")
MANIFEST_PATH = FROZEN_DIR / "_manifest.json"
COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _base(symbol):
    return str(symbol).strip().upper().split(".")[0]


def _csv_path(symbol):
    return FROZEN_DIR / f"{_base(symbol)}.csv"


def _frame_hash(frame):
    payload = frame.to_csv().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def load_frozen(symbol):
    """Return the frozen seed frame for ``symbol`` (DatetimeIndex 'Date'), or None."""
    path = _csv_path(symbol)
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
    except Exception:
        return None
    if frame.empty:
        return None
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
    frame.index.name = "Date"
    for col in COLUMNS:
        if col not in frame.columns:
            frame[col] = frame.get("Close")
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame[COLUMNS].sort_index()


def freeze_frame(symbol, frame):
    """Write ``frame`` as the immutable frozen seed for ``symbol``. Returns a record."""
    FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    base = _base(symbol)
    out = frame.copy()
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index)).tz_localize(None)
    out.index.name = "Date"
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = out.get("Close")
    out = out[COLUMNS].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.to_csv(_csv_path(base))
    return {
        "symbol": base, "rows": int(len(out)),
        "first_session": out.index[0].date().isoformat() if len(out) else None,
        "last_session": out.index[-1].date().isoformat() if len(out) else None,
        "content_hash": _frame_hash(out),
        "seed_provider": "FROZEN_YAHOO_SEED",
    }


def read_manifest():
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_manifest(records, *, source, note=""):
    FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "source": source, "note": note, "yahoo_network_used": False,
        "symbols": {r["symbol"]: r for r in records},
        "symbol_count": len(records),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def is_frozen(symbol):
    return _csv_path(symbol).exists()
