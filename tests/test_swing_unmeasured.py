"""Swing Breakout says how much of its own table was never measured.

Three columns leave a blank cell when nothing was measured: the round-trip
cost, the session's trade count, and the buy share. Each said so in its own
column tooltip -- "Blank means too few measured sessions to say, never that it
is cheap" -- which is the right sentence in the wrong place, because a reader
only hovers a column they have already decided to trust.

The cost is the one that matters. A blank there is not a cheap name; it is a
name whose cost nobody has measured, and assuming zero friction is the specific
mistake that once made thirteen losing signals look profitable.

Presentation only.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from dashboard import swing_signals
from dashboard.swing_signals import _UNMEASURED_COLUMNS, _unmeasured_note


@pytest.fixture
def drawn(monkeypatch):
    written = []
    monkeypatch.setattr(swing_signals.st, "markdown",
                        lambda body, **kwargs: written.append(body))
    return written


def candidates(**overrides):
    """Four candidates; every measured column full unless overridden."""
    frame = pd.DataFrame({
        "Ticker": ["MPCO", "ORAS", "ETEL", "COMI"],
        "Close": [2.6, 188.5, 37.2, 84.0],
        "Round trip %": [0.904, 1.650, 0.512, 0.463],
        "Trades": [412, 1208, 655, 3011],
        "Buy share": [0.62, 0.48, 0.55, 0.71],
    })
    for column, values in overrides.items():
        frame[column] = values
    return frame


def counts(markup):
    """{label: (missing, total)} read back out of the rendered chips."""
    return {label: (int(a), int(b)) for label, a, b in
            re.findall(r">([^<>:]+): (\d+) / (\d+)<", markup)}


def test_a_missing_cost_is_counted_on_the_page(drawn):
    _unmeasured_note(candidates(**{"Round trip %": [0.904, None, None, 0.463]}))
    assert drawn, "nothing was rendered"
    assert counts(drawn[-1])["تكلفة غير مقاسة · cost"] == (2, 4)


def test_the_note_says_a_blank_is_not_a_zero(drawn):
    _unmeasured_note(candidates(**{"Round trip %": [None, None, None, None]}))
    assert "لم تُقَس" in drawn[-1]
    assert "صفر" in drawn[-1]


def test_an_unmeasured_count_wears_the_unknown_violet(drawn):
    """Not grey. Grey is what a measured-and-unremarkable value wears."""
    _unmeasured_note(candidates(**{"Trades": [412, None, 655, 3011]}))
    assert "#c084fc" in drawn[-1]


@pytest.mark.parametrize("column, label", sorted(_UNMEASURED_COLUMNS.items()))
def test_every_column_that_can_be_blank_is_counted(drawn, column, label):
    _unmeasured_note(candidates(**{column: [None] * 4}))
    assert counts(drawn[-1])[label] == (4, 4)


def test_several_gaps_are_reported_separately(drawn):
    _unmeasured_note(candidates(**{
        "Round trip %": [0.904, None, None, 0.463],
        "Trades": [None, None, None, 3011],
        "Buy share": [0.62, 0.48, 0.55, 0.71],
    }))
    reported = counts(drawn[-1])
    assert reported["تكلفة غير مقاسة · cost"] == (2, 4)
    assert reported["صفقات الجلسة غير مقاسة · trades"] == (3, 4)
    assert "حصة الشراء" not in " ".join(reported)


def test_a_fully_measured_table_says_nothing(drawn):
    """The note is a warning, not a permanent decoration."""
    _unmeasured_note(candidates())
    assert not drawn


def test_a_frame_without_those_columns_is_not_an_error(drawn):
    _unmeasured_note(pd.DataFrame({"Ticker": ["MPCO"], "Close": [2.6]}))
    assert not drawn


def test_an_empty_candidate_list_does_not_divide_by_zero(drawn):
    _unmeasured_note(pd.DataFrame(columns=["Ticker", "Round trip %"]))
    assert not drawn


def test_the_frame_is_not_mutated():
    frame = candidates(**{"Round trip %": [0.904, None, None, 0.463]})
    before = frame.copy(deep=True)
    _unmeasured_note(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_the_note_is_rendered_with_the_table(drawn, monkeypatch):
    """It has to be beside the blanks it explains, not in a tooltip."""
    import inspect

    source = inspect.getsource(swing_signals.show_swing_signals)
    assert "_unmeasured_note(candidates)" in source
    assert source.index("st.dataframe(") < source.index("_unmeasured_note(")
