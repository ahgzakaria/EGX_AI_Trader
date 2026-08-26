"""Saving from the settings screen must not delete what it does not show.

The screen builds a proposed payload from its widgets and writes that. The
strategy section was built by copying the saved section and updating it, so keys
without a widget survived. The backtest section was built by replacing the
section wholesale, so they did not.

Three keys added on 2026-08-26 -- `spread_percent`, `max_spread_percent` and
`unmeasured_spread_percent` -- have no widget. Clicking Save would have dropped
all three from config/settings.json, taking the spread charge with them. And
because the dirty check compares the whole dictionary, the payload could never
equal the settings it was compared against: "You have unsaved settings changes"
would have shown permanently, including immediately after saving.

These tests pin the property rather than the three keys, because the next key
added without a widget must survive too.
"""

import pytest

from dashboard.backtest_state import settings_are_dirty


def _saved():
    return {
        "strategy": {"min_rr": 3.0, "an_unmanaged_strategy_key": "keep me"},
        "backtest": {
            "entry_wait_days": 5,
            "commission": 0.001819,
            "slippage": 0.0005,
            "spread_percent": 0.5,
            "max_spread_percent": None,
            "unmeasured_spread_percent": 0.927,
        },
        "ai": {"enabled": True, "min_probability": 60},
    }


def _propose(saved, widget_values, preserve=True):
    """Mirror how the screen assembles its payload, either way."""
    from copy import deepcopy

    proposed = deepcopy(saved)
    if preserve:
        section = dict(saved["backtest"])
        section.update(widget_values)
    else:
        section = dict(widget_values)          # the old wholesale replacement
    proposed["backtest"] = section
    return proposed


WIDGETS = {"entry_wait_days": 5, "commission": 0.001819, "slippage": 0.0005}


def test_the_old_replacement_dropped_the_cost_keys():
    # Documents the defect this test exists for, so the fix is not mistaken
    # for decoration later.
    proposed = _propose(_saved(), WIDGETS, preserve=False)
    for key in ("spread_percent", "max_spread_percent",
                "unmeasured_spread_percent"):
        assert key not in proposed["backtest"]


def test_preserving_keeps_every_key_without_a_widget():
    proposed = _propose(_saved(), WIDGETS)
    for key in ("spread_percent", "max_spread_percent",
                "unmeasured_spread_percent"):
        assert key in proposed["backtest"]
    assert proposed["backtest"]["spread_percent"] == pytest.approx(0.5)
    assert proposed["backtest"]["unmeasured_spread_percent"] == pytest.approx(0.927)


def test_a_none_valued_key_survives_rather_than_being_treated_as_absent():
    # max_spread_percent is None by design. A payload that dropped it because
    # it was falsy would re-enable a filter default nobody chose.
    proposed = _propose(_saved(), WIDGETS)
    assert "max_spread_percent" in proposed["backtest"]
    assert proposed["backtest"]["max_spread_percent"] is None


def test_widget_values_still_win_over_the_saved_ones():
    proposed = _propose(_saved(), {**WIDGETS, "entry_wait_days": 9})
    assert proposed["backtest"]["entry_wait_days"] == 9


def test_an_untouched_screen_is_not_dirty():
    # The visible symptom of the defect: dirty forever, because the payload
    # could never equal the settings it was compared against -- so saving
    # never cleared the warning.
    saved = _saved()
    assert not settings_are_dirty(saved, _propose(saved, WIDGETS))


def test_the_old_replacement_left_the_screen_dirty_forever():
    saved = _saved()
    assert settings_are_dirty(saved, _propose(saved, WIDGETS, preserve=False))


def test_a_real_edit_is_still_detected():
    # The guard must not make the screen blind to genuine changes.
    saved = _saved()
    edited = _propose(saved, {**WIDGETS, "entry_wait_days": 9})
    assert settings_are_dirty(saved, edited)
