"""One run of the radar: build, save, record, grade. Called by the script and the page.

Two callers each assembling "a run" is how the page comes to record a list the
script would not have, so both go through ``run``.
"""

from __future__ import annotations

from t0_radar import forward, radar


def run(database=None, on_error=None):
    """Build the list for the last session, save it, record it, grade the old ones."""

    failures = {}

    def failed(symbol, reason):
        failures.setdefault(symbol, reason)
        if on_error is not None:
            on_error(symbol, reason)

    result = radar.build(on_error=failed)
    path = radar.write_csv(result)
    store = forward.RadarStore(database) if database else forward.RadarStore()
    recorded = store.record(result)
    graded = forward.grade_pending(store)
    summary = {
        "status": "OK" if result.session_date else "NO_SESSION",
        "session_date": result.session_date,
        "candidates": int(len(result.candidates)),
        "symbols_considered": result.provenance.get("symbols_considered"),
        "unreadable": len(failures),
        "t0_enforced": result.provenance.get("t0_enforced"),
        "t0_eligible_count": result.provenance.get("t0_eligible_count"),
        "cost_measured_through": result.provenance.get("cost_measured_through"),
        "funnel": result.funnel,
        "csv": str(path),
        "recorded": recorded["written"],
        "already_recorded": recorded["already_recorded"],
        "graded": graded,
        "record": store.report(),
    }
    return result, summary
