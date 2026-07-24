"""Tests for the paper-forward-test activation gate + immutable signal store."""

import pytest

from scalping.event_paper_gate import (
    ImmutablePaperSignalStore,
    paper_recording_permitted,
)


def _ok_kwargs(**over):
    base = dict(
        session_phase_valid=True, max_fatal_outage_seconds=250.0,
        event_status="EVENT_DATA_VALID", range_status="RANGE_CONFIRMED",
        quote_age_seconds=30.0, spread_ok=True, liquidity_ok=True,
        entry_valid=True, net_rr=2.0,
    )
    base.update(over)
    return base


def test_gate_permits_only_when_all_pass():
    assert paper_recording_permitted(**_ok_kwargs()).permitted is True


@pytest.mark.parametrize("override,needle", [
    (dict(range_status="RANGE_PARTIAL"), "range status"),
    (dict(max_fatal_outage_seconds=317.0), "fatal continuous outage"),
    (dict(event_status="EVENT_DATA_LIMITED"), "event status"),
    (dict(quote_age_seconds=500.0), "stale quote"),
    (dict(spread_ok=False), "spread"),
    (dict(liquidity_ok=False), "liquidity"),
    (dict(entry_valid=False), "entry"),
    (dict(net_rr=0.5), "net RR"),
    (dict(session_phase_valid=False), "phase-classified"),
])
def test_gate_blocks_on_each_failure(override, needle):
    d = paper_recording_permitted(**_ok_kwargs(**override))
    assert d.permitted is False
    assert any(needle in r for r in d.reasons)


def test_317s_outage_is_blocked_by_the_gate():
    # The exact 2026-07-21 condition: 317s fatal outage -> not permitted.
    d = paper_recording_permitted(**_ok_kwargs(max_fatal_outage_seconds=317.0,
                                               range_status="RANGE_PARTIAL"))
    assert d.permitted is False


def _signal(sid="S1"):
    return {
        "signal_id": sid, "symbol": "COMI.CA", "session_date": "2026-07-21",
        "decision_timestamp": "2026-07-21T11:00:00+00:00", "genuine_events": 3477,
        "connection_status": "HEALTHY", "range_levels": {"high": 140, "low": 138},
        "entry": 138.5, "stop": 137.9, "target1": 139.5, "target2": 140.0,
        "target3": 140.5, "net_rr": 2.1, "spread_percent": 0.06,
        "quote_age_seconds": 20, "event_quality_score": 84.2,
        "range_confidence_score": 78.1, "liquidity_score": 90,
    }


def test_signal_is_immutable(tmp_path):
    store = ImmutablePaperSignalStore(tmp_path / "paper.db")
    store.record_signal(_signal("S1"))
    assert store.count() == 1
    # Re-recording the same id must be refused (never overwrite an original).
    with pytest.raises(ValueError):
        store.record_signal(_signal("S1"))


def test_outcomes_append_without_rewriting_signal(tmp_path):
    store = ImmutablePaperSignalStore(tmp_path / "paper.db")
    store.record_signal(_signal("S1"))
    before = store.signal("S1")
    for h in (1, 3, 5, 10, 20):
        store.record_outcome("S1", h, {"price": 139.0, "mfe": 0.7, "mae": -0.2,
                                       "stop_hit": False, "target_hit": True,
                                       "execution_feasible": True})
    after = store.signal("S1")
    # The original signal row is unchanged.
    assert before == after


def test_outcome_for_unknown_signal_rejected(tmp_path):
    store = ImmutablePaperSignalStore(tmp_path / "paper.db")
    with pytest.raises(ValueError):
        store.record_outcome("NOPE", 5, {"price": 1.0})
