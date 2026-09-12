"""A rule that ran and correctly produced nothing does not look broken.

Confirmed Breakout fires about ninety times a year, so most sessions produce
nothing at all. That silence was rendered with ``empty_state`` -- the same
dashed grey box the app uses for a failed search, an unloaded page and a
missing saved list. The commonest outcome on the page wore the clothes of a
fault, every day, until a reader would stop believing either of them.

``quiet_state`` is the measurement happening and its answer being none.

Presentation only.
"""

from __future__ import annotations

import re

import pytest

from dashboard import confirmed_breakout, ui


@pytest.fixture
def drawn(monkeypatch):
    written = []
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kwargs: written.append(body))
    return written


@pytest.fixture(scope="module")
def css():
    captured = []
    original = ui.st.markdown
    ui.st.markdown = lambda body, **kwargs: captured.append(body)
    try:
        ui.apply_global_style()
    finally:
        ui.st.markdown = original
    return captured[0]


# --- the component ------------------------------------------------------------

def test_a_quiet_result_says_what_was_measured_and_how_much(drawn):
    ui.quiet_state("Nothing met all seven conditions",
                   "The rule fires about ninety times a year.",
                   count="0 / 220")
    markup = drawn[-1]
    assert "egx-quiet" in markup
    assert "0 / 220" in markup
    assert "ninety times a year" in markup


def test_the_count_is_optional(drawn):
    ui.quiet_state("Nothing today", "That is the design.")
    assert 'class="count"' not in drawn[-1]


def test_quiet_is_not_the_same_component_as_empty(drawn):
    ui.quiet_state("Nothing met the conditions", "By design.")
    quiet = drawn[-1]
    ui.empty_state("No results", "Change the filter.")
    empty = drawn[-1]
    assert "egx-quiet" in quiet and "egx-quiet" not in empty
    assert "egx-empty" in empty and "egx-empty" not in quiet


def test_quiet_is_solid_where_empty_is_dashed(css):
    """Dashed means absent. A measured none is present."""
    quiet = re.search(r"\.egx-quiet\s*\{(.*?)\}", css, re.S).group(1)
    empty = re.search(r"\.egx-empty\s*\{(.*?)\}", css, re.S).group(1)
    assert "dashed" in empty
    assert "dashed" not in quiet
    assert "var(--green)" in quiet


def test_nothing_injects_markup(drawn):
    ui.quiet_state("<script>a</script>", "<script>b</script>", count="<script>c</script>")
    assert "<script>" not in drawn[-1]


# --- the page uses it ---------------------------------------------------------

def test_confirmed_breakout_reports_a_silent_session_as_quiet_not_empty():
    import inspect

    source = inspect.getsource(confirmed_breakout.show_confirmed_breakout)
    assert "quiet_state(" in source
    # The only empty_state left in the page body would put the two back in the
    # same clothes for the same outcome.
    body = source.split("if result.count:")[1]
    assert "empty_state(" not in body


def test_the_silent_state_carries_the_universe_it_refused():
    import inspect

    source = inspect.getsource(confirmed_breakout.show_confirmed_breakout)
    assert 'count=f"0 / {result.considered}"' in source


# --- the funnel on this page --------------------------------------------------

def test_the_funnel_is_drawn_to_scale(monkeypatch):
    from types import SimpleNamespace as NS

    written = []
    monkeypatch.setattr(confirmed_breakout.st, "markdown",
                        lambda body, **kwargs: written.append(body))
    monkeypatch.setattr(confirmed_breakout, "section_header", lambda *a, **k: None)

    result = NS(funnel={"Liquidity": 27, "LongTermTrend": 11, "Calm": 126,
                        "Breakout": 55, "VolumeConfirmation": 1},
                count=0, considered=220)
    confirmed_breakout._show_funnel(result)
    assert written and 'class="egx-funnel"' in written[-1]
    widths = [float(w) for w in re.findall(r"width:([\d.]+)%", written[-1])]
    assert sum(widths) == pytest.approx(100.0, abs=0.01)
    # Calm refused the most, so it is drawn first.
    assert widths[0] == pytest.approx(126 / 220 * 100, abs=1e-3)


def test_a_gate_label_exists_for_every_condition_the_scan_records():
    from strategy_momentum_breakout.watch import STRUCTURAL_GATES, TRIGGER_GATES

    for gate in (*STRUCTURAL_GATES, *TRIGGER_GATES):
        assert gate in confirmed_breakout.GATE_LABELS
