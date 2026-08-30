"""A completed-session scan survives navigating away from the page.

Streamlit reruns the whole script on every interaction, and `st.button` is True
only on the run that follows the click. Both scan pages were written as "if not
button: return", so visiting another page and coming back discarded a result
that had taken two minutes to produce -- and rerunning it could not have
changed the answer, because it reads daily bars that are already final.

What these tests protect is the line between that and actually going stale. A
stored scan must be dropped when the thresholds change, because those rows were
produced by a different rule; it must be kept, and labelled, when a newer
session has closed, because the rows are still exactly what the rule said about
the session they name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from dashboard import scan_memory
from dashboard.scan_memory import (
    RememberedScan,
    config_digest,
    forget,
    recall,
    remember,
    scan_caption,
)


#: Captured before the autouse fixture below replaces it, so the one test that
#: is about the real lookup can still reach it.
_REAL_LATEST_SESSION = scan_memory._latest_completed_session


@dataclass
class _Config:
    turnover: float = 2_000_000.0
    volume_multiple: float = 2.5


class _Result:
    def __init__(self, session_date="2026-08-30", count=2):
        self.session_date = session_date
        self.count = count


@pytest.fixture(autouse=True)
def session_state(monkeypatch):
    """A dict standing in for st.session_state, which needs a running app."""

    state = {}
    monkeypatch.setattr(scan_memory.st, "session_state", state)
    return state


@pytest.fixture(autouse=True)
def known_session(monkeypatch):
    """Pin the completed session; otherwise these read the live calendar."""

    monkeypatch.setattr(scan_memory, "_latest_completed_session",
                        lambda: "2026-08-30")


# --- surviving the trip to another page --------------------------------------

def test_a_scan_is_recalled_after_navigating_away():
    config, result = _Config(), _Result()
    remember("confirmed_breakout", result, config)
    assert recall("confirmed_breakout", config).result is result


def test_nothing_stored_recalls_nothing():
    assert recall("confirmed_breakout", _Config()) is None


def test_two_pages_do_not_share_a_memory():
    remember("confirmed_breakout", _Result(count=2), _Config())
    remember("swing_breakout", _Result(count=7), _Config())
    assert recall("confirmed_breakout", _Config()).result.count == 2
    assert recall("swing_breakout", _Config()).result.count == 7


def test_a_recalled_scan_carries_when_it_ran():
    remember("confirmed_breakout", _Result(), _Config())
    assert isinstance(recall("confirmed_breakout", _Config()).scanned_at, datetime)


def test_forget_drops_it():
    remember("confirmed_breakout", _Result(), _Config())
    forget("confirmed_breakout")
    assert recall("confirmed_breakout", _Config()) is None


def test_anything_can_be_stored_not_only_a_result_object():
    """The swing page stores (result, failures): the unreadable names are part
    of the answer, and a page recalled without them under-reports how much of
    the universe went unseen."""

    payload = (_Result(), {"AALR": "no history"})
    remember("swing_breakout", payload, _Config(), session_date="2026-08-30")
    recalled = recall("swing_breakout", _Config())
    assert recalled.result is payload
    assert recalled.session_date == "2026-08-30"


# --- when it must NOT be recalled --------------------------------------------

def test_changed_thresholds_discard_the_scan():
    """Those rows were produced by a different rule."""

    remember("confirmed_breakout", _Result(), _Config(volume_multiple=2.5))
    assert recall("confirmed_breakout", _Config(volume_multiple=3.0)) is None


def test_a_discarded_scan_is_not_left_behind(session_state):
    remember("confirmed_breakout", _Result(), _Config(volume_multiple=2.5))
    recall("confirmed_breakout", _Config(volume_multiple=3.0))
    assert session_state == {}


def test_the_digest_ignores_field_order_but_not_values():
    assert config_digest(_Config(2_000_000.0, 2.5)) == config_digest(_Config(2_000_000.0, 2.5))
    assert config_digest(_Config(2_000_000.0, 2.5)) != config_digest(_Config(2_000_001.0, 2.5))


def test_a_mapping_config_also_digests():
    assert config_digest({"b": 2, "a": 1}) == config_digest({"a": 1, "b": 2})


# --- when it is kept but no longer current -----------------------------------

def test_a_newer_session_marks_the_scan_stale(monkeypatch):
    remember("confirmed_breakout", _Result(session_date="2026-08-26"), _Config())
    monkeypatch.setattr(scan_memory, "_latest_completed_session",
                        lambda: "2026-08-30")
    recalled = recall("confirmed_breakout", _Config())
    assert recalled is not None, "a stale scan is kept, not discarded"
    assert recalled.is_stale
    assert "2026-08-30" in scan_caption(recalled)


def test_the_same_session_is_not_stale():
    remember("confirmed_breakout", _Result(session_date="2026-08-30"), _Config())
    assert recall("confirmed_breakout", _Config()).is_stale is False


def test_an_unknown_session_is_not_reported_as_stale(monkeypatch):
    """Unknown is not stale.

    The completed session comes from a calendar lookup that can fail. Warning
    about staleness on no evidence, on every rerun, is worse than saying
    nothing.
    """

    remember("confirmed_breakout", _Result(session_date="2026-08-26"), _Config())
    monkeypatch.setattr(scan_memory, "_latest_completed_session", lambda: None)
    assert recall("confirmed_breakout", _Config()).is_stale is False


def test_a_failing_calendar_does_not_break_the_page(monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("core.egx_calendar.effective_holidays", _explode)
    assert _REAL_LATEST_SESSION() is None


def test_the_caption_names_the_session_it_describes():
    remembered = RememberedScan(result=_Result(), scanned_at=datetime(2026, 8, 30, 19, 14),
                                session_date="2026-08-30", latest_session="2026-08-30")
    caption = scan_caption(remembered)
    assert "19:14" in caption and "2026-08-30" in caption
