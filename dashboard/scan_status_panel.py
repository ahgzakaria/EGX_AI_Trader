"""Honest Dashboard/Market-Scan provider status.

The old banner asked ``summarize_frames([], purpose="dashboard")`` for a provider before
any scan had run. With no observations that helper infers a fallback from **Rubix quote
health**, so a stale live quote — a two-minute-old tick — made the page announce
``local_cache:yahoo`` / ``YAHOO_FALLBACK`` for the *daily history* provider. The scan
never used Yahoo; the banner said it did.

The daily-history provider and the live-quote overlay are two different questions and are
answered separately here:

  * historical source comes from the configured operational provider and, once a scan has
    observed data, from what that scan actually used;
  * the live overlay reports Rubix on its own terms — Fresh, Stale, Partially Available or
    Unavailable — and can never change the historical label.

The frozen Yahoo legacy-backtest route is untouched; it is simply not a current-research
source and is never named as one here.
"""

from __future__ import annotations

# Historical-source labels for the current-research (Swing/Daily) path.
EODHD = "EODHD"
EODHD_CACHE = "EODHD cache"
EODHD_CACHE_PLUS_REFRESH = "EODHD cache + bounded refreshes"

# Live-overlay labels.
RUBIX_NOT_CHECKED = "Rubix status not checked"
RUBIX_LOADING = "Loading Rubix"
RUBIX_FRESH = "Rubix Fresh"
RUBIX_STALE = "Rubix Stale"
RUBIX_PARTIAL = "Rubix Partially Available"
RUBIX_UNAVAILABLE = "Rubix Unavailable"

AWAITING_SCAN = "Awaiting scan"


def _rubix_label(available, total, batch_status="", freshness=""):
    """Describe the live overlay from overlay counts and the batch outcome."""
    if str(batch_status) in ("RUBIX_DB_UNAVAILABLE", "RUBIX_DB_BUSY",
                             "RUBIX_BATCH_TIMEOUT"):
        return RUBIX_UNAVAILABLE
    if not total or available <= 0:
        return RUBIX_UNAVAILABLE
    if available < total:
        return RUBIX_PARTIAL
    return RUBIX_STALE if str(freshness).upper() == "STALE" else RUBIX_FRESH

#: Shown when a finished scan produced no usable session date. Deliberately
#: not the expected session: an unknown actual date is information, and a
#: substituted expected date is a false statement about the prices below.
UNKNOWN_CANDLE = "Unknown — no dated candle"


def scan_status_view(job_progress=None, expected_session=None, result_metadata=None):
    """Return the three status lines for the Market Scan banner.

    ``job_progress`` is a :class:`core.scan_job_manager.ScanProgress` (or ``None`` before
    any scan). ``result_metadata`` carries what a finished scan actually observed.
    """
    from core.scan_job_manager import (
        CANCELLED, COMPLETED, COMPLETED_WITH_GAPS, FAILED, PREPARING_RUBIX, STARTING,
    )

    # No scan has run in this session.
    if job_progress is None:
        return {
            "historical_source": EODHD,
            "latest_completed_candle": AWAITING_SCAN,
            "live_overlay": RUBIX_NOT_CHECKED,
        }

    state = str(job_progress.state)

    if state in (STARTING, PREPARING_RUBIX):
        # No row has been read yet, so no candle date is known. The expected
        # session is a different claim and never appears under this label.
        return {
            "historical_source": EODHD,
            "latest_completed_candle": AWAITING_SCAN,
            "live_overlay": RUBIX_LOADING,
        }

    available = int(job_progress.rubix_overlay_available)
    total = int(job_progress.total or 0)
    overlay = _rubix_label(available, total, job_progress.rubix_batch_status,
                           (result_metadata or {}).get("live_quote_freshness", ""))

    refreshed = int(job_progress.eodhd_refresh_successes or 0)
    historical = EODHD_CACHE_PLUS_REFRESH if refreshed else EODHD_CACHE

    if state in (COMPLETED, COMPLETED_WITH_GAPS, CANCELLED, FAILED):
        candle = (result_metadata or {}).get("latest_completed_candle")
        # Never fall back to the EXPECTED session here. This line is labelled
        # "Latest completed candle", so printing the date the market *should*
        # have published states something the data does not support - which is
        # exactly how 2026-08-03 came to sit above 2026-07-30 prices.
        return {
            "historical_source": historical,
            "latest_completed_candle": str(candle or UNKNOWN_CANDLE),
            "live_overlay": overlay,
        }

    return {
        "historical_source": historical,
        "latest_completed_candle": UNKNOWN_CANDLE,
        "live_overlay": overlay,
    }


def coverage_view(job_progress):
    """Progress and analytical coverage as two DIFFERENT numbers.

    Progress is how far through the universe the scan is; coverage is how much of the
    universe produced an analytical result. A scan that finishes with gaps must never
    render as if it covered everything.
    """
    if job_progress is None:
        return {"progress_percent": 0.0, "coverage_percent": 0.0,
                "success": 0, "skipped": 0, "failed": 0, "total": 0,
                "status_breakdown": {}, "has_gaps": False}
    total = int(job_progress.total or 0)
    completed = int(job_progress.completed or 0)
    return {
        "progress_percent": round(100.0 * completed / total, 1) if total else 0.0,
        "coverage_percent": round(100.0 * int(job_progress.success) / total, 1)
        if total else 0.0,
        "success": int(job_progress.success),
        "skipped": int(job_progress.skipped),
        "failed": int(job_progress.failed),
        "total": total,
        "status_breakdown": dict(job_progress.status_breakdown),
        "has_gaps": bool(job_progress.skipped or job_progress.failed),
    }
