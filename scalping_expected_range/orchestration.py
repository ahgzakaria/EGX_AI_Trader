"""Shared constants + classifiers for the three-stage live paper orchestration.

Stage A (pre-session snapshot ~09:45), Stage B (live event-driven monitor
10:00-14:15), Stage C (post-session outcome finalizer ~14:40). All Cairo time.
These helpers are disclosure/classification only — they never place an order,
change TP/SL, weights or percentiles.
"""

from __future__ import annotations

from datetime import time
import hashlib
import json
from pathlib import Path

from core.egx_session import AUCTION_END, CONTINUOUS_CLOSE, REGULAR_OPEN

# Declared-in-advance operational tolerances (must NOT be changed after seeing a
# session result).
SNAPSHOT_DEADLINE = REGULAR_OPEN                     # 10:00 — a snapshot after this is LATE
MONITOR_START_TOLERANCE = time(10, 5)               # monitor must be live by 10:05 Cairo
MONITOR_MAX_GAP_SECONDS = 900                       # >15 min unexplained processing gap = DATA_GAP
CONTINUOUS_OPEN = REGULAR_OPEN                       # 10:00
CONTINUOUS_END = CONTINUOUS_CLOSE                    # 14:15
AUCTION_CLOSE = AUCTION_END                          # 14:25

# Snapshot statuses.
SNAPSHOT_VALID = "SNAPSHOT_VALID"
SNAPSHOT_LATE = "SNAPSHOT_LATE"
HISTORY_STALE = "HISTORY_STALE"
HISTORY_UNAVAILABLE = "HISTORY_UNAVAILABLE"
NON_TRADING_DAY = "NON_TRADING_DAY"

# Session classifications (only COMPLETE_FORWARD_SESSION counts toward the 20).
COMPLETE_FORWARD_SESSION = "COMPLETE_FORWARD_SESSION"
PARTIAL_LATE_START = "PARTIAL_LATE_START"
PARTIAL_EARLY_STOP = "PARTIAL_EARLY_STOP"
DATA_GAP = "DATA_GAP"
INVALID_SNAPSHOT = "INVALID_SNAPSHOT"
MONITOR_NOT_RUNNING = "MONITOR_NOT_RUNNING"
PILOT_SESSION = "PILOT_SESSION"
NON_TRADING_DAY_SESSION = "NON_TRADING_DAY"

COUNTED_SESSION_CLASSES = {COMPLETE_FORWARD_SESSION}

# Outcome-horizon maturity statuses.
PENDING = "PENDING"
MATURED = "MATURED"
UNAVAILABLE_SESSION_END = "UNAVAILABLE_SESSION_END"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
INVALID = "INVALID"


def snapshot_status(now_cairo_time, freshness_status, is_trading_day):
    """Classify a pre-session snapshot from its creation time + history freshness."""
    if not is_trading_day:
        return NON_TRADING_DAY
    if freshness_status in ("HISTORY_UNAVAILABLE", "MISSING"):
        return HISTORY_UNAVAILABLE
    if freshness_status in ("HISTORY_STALE",):
        return HISTORY_STALE
    if now_cairo_time >= SNAPSHOT_DEADLINE:
        return SNAPSHOT_LATE
    return SNAPSHOT_VALID


def horizon_status(decision_dt, horizon_minutes, continuous_end_dt, last_quote_dt,
                   now_dt=None):
    """Maturity of one outcome horizon (never finalize a still-forming horizon)."""
    from datetime import timedelta
    horizon_end = decision_dt + timedelta(minutes=horizon_minutes)
    # A continuous-session horizon that reaches past 14:15 cannot mature there.
    if horizon_end > continuous_end_dt:
        return UNAVAILABLE_SESSION_END
    if now_dt is not None and horizon_end > now_dt:
        return PENDING
    if last_quote_dt is None or last_quote_dt < horizon_end:
        # No observed quote reached the horizon end.
        if last_quote_dt is None:
            return DATA_INSUFFICIENT
        # We have quotes but none as late as the horizon end within the session.
        return DATA_INSUFFICIENT
    return MATURED


def classify_session(*, manifest, cursor_last_received_cairo_time, monitor_ran,
                     is_pilot=False, processing_gap_seconds=None):
    """Classify a session's forward-evidence validity. Only COMPLETE counts.

    * manifest: the pre-session manifest dict (or None).
    * cursor_last_received_cairo_time: datetime.time the monitor processed up to
      (Cairo), or None if the monitor never ran.
    * monitor_ran: whether the live monitor recorded any events/state this session.
    """
    if is_pilot:
        return PILOT_SESSION
    if manifest is None:
        return INVALID_SNAPSHOT
    status = manifest.get("snapshot_status")
    if status == NON_TRADING_DAY:
        return NON_TRADING_DAY_SESSION
    if status in (HISTORY_UNAVAILABLE, INVALID_SNAPSHOT, HISTORY_STALE):
        # A snapshot built on stale/unavailable history is not a valid basis for a
        # counted forward session (the expected ranges are off by ≥1 session).
        return INVALID_SNAPSHOT
    if not monitor_ran or cursor_last_received_cairo_time is None:
        return MONITOR_NOT_RUNNING
    if processing_gap_seconds is not None and processing_gap_seconds > MONITOR_MAX_GAP_SECONDS:
        return DATA_GAP
    if status == SNAPSHOT_LATE:
        return PARTIAL_LATE_START
    if cursor_last_received_cairo_time < CONTINUOUS_END:
        return PARTIAL_EARLY_STOP           # coverage did not reach 14:15
    return COMPLETE_FORWARD_SESSION


def settings_hash(path="scalping_expected_range/settings.json"):
    p = Path(path)
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def write_manifest(path, manifest):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def read_manifest(path):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
