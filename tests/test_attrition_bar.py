"""How a universe narrows to a handful, drawn to scale rather than listed.

The gate funnel was two tables of ``(gate, count)`` side by side -- the
explanatory heart of Breakout Watch as a list of numbers, where 135 refusals
and 5 survivors take up the same room on the screen.

It is one proportional bar now, and deliberately **not** the cascade of
shrinking stages the design sketches. ``watch.py`` records which gate refused
each symbol *first*, so every symbol is counted exactly once and the stages are
a partition of the universe, not a sequence through it. Drawing
230 → 209 → 74 → 28 would claim an ordering these numbers do not carry.

Presentation only.
"""

from __future__ import annotations

import re

import pytest

from dashboard import breakout_watch
from dashboard.breakout_watch import GATE_LABELS
from dashboard.ui import attrition_bar


def widths(markup):
    return [float(w) for w in re.findall(r'width:([\d.]+)%', markup)]


def legend(markup):
    """{label: (count, percent)} read back out of the rendered legend."""
    rows = re.findall(r'</i>([^<]+)</div><div class="n[^"]*">([\d,]+)</div>'
                      r'<div class="p">([\d.]+)%</div>', markup)
    return {label.strip(): (int(n.replace(",", "")), float(p)) for label, n, p in rows}


STAGES = [("liquidity", 135), ("calm", 46), ("out of reach", 16), ("quiet", 0)]


# --- the bar ------------------------------------------------------------------

def test_every_segment_is_its_real_share_of_the_universe():
    markup = attrition_bar(STAGES, total=230)
    assert widths(markup) == pytest.approx(
        [135 / 230 * 100, 46 / 230 * 100, 16 / 230 * 100, 33 / 230 * 100], abs=1e-3)


def test_the_widths_add_up_to_the_whole_bar():
    """Within what three decimal places can express: each segment rounds by up
    to 0.0005, so a bar of N segments can miss by N times that."""
    assert sum(widths(attrition_bar(STAGES, total=230))) == pytest.approx(100.0, abs=0.01)


def test_the_survivors_are_what_is_left_and_are_marked_apart():
    markup = attrition_bar(STAGES, total=230)
    assert 'class="survived"' in markup
    assert legend(markup)["survived"] == (33, pytest.approx(14.3, abs=0.1))


def test_the_largest_refusal_is_drawn_first():
    """The question the bar answers is what removed most of the universe."""
    markup = attrition_bar([("small", 5), ("largest", 200), ("middle", 20)], total=230)
    assert widths(markup)[0] == pytest.approx(200 / 230 * 100, abs=1e-3)


def test_a_gate_that_refused_nobody_leaves_no_segment_but_keeps_its_row():
    """A quiet gate is a fact about the week. A legend that omits it reads as
    a gate that does not exist."""
    markup = attrition_bar(STAGES, total=230)
    assert len(widths(markup)) == 4          # three refusals + survivors, not five
    assert legend(markup)["quiet"] == (0, 0.0)


def test_with_no_survivors_the_bar_is_all_refusals():
    markup = attrition_bar([("liquidity", 200), ("calm", 30)], total=230)
    assert 'class="survived"' not in markup
    assert legend(markup)["survived"] == (0, 0.0)


def test_an_empty_universe_draws_nothing_rather_than_dividing_by_zero():
    assert attrition_bar([], total=0) == ""
    assert attrition_bar([("liquidity", 0)], total=0) == ""


def test_the_total_defaults_to_what_was_refused():
    markup = attrition_bar([("liquidity", 100), ("calm", 100)])
    assert legend(markup)["survived"] == (0, 0.0)
    assert sum(widths(markup)) == pytest.approx(100.0, abs=1e-3)


def test_more_refused_than_the_universe_does_not_produce_negative_survivors():
    markup = attrition_bar([("liquidity", 300)], total=230)
    assert legend(markup)["survived"][0] == 0


def test_a_gate_name_cannot_inject_markup():
    assert "<script>" not in attrition_bar([("<script>x</script>", 5)], total=10)


# --- the page's funnel --------------------------------------------------------

@pytest.fixture
def drawn(monkeypatch):
    written = []
    monkeypatch.setattr(breakout_watch.st, "markdown",
                        lambda body, **kwargs: written.append(body))
    monkeypatch.setattr(breakout_watch, "section_header", lambda *a, **k: None)
    monkeypatch.setattr(breakout_watch.st, "caption", lambda *a, **k: None)
    return written


def test_every_gate_the_scan_can_record_has_a_readable_label():
    from strategy_momentum_breakout.watch import STRUCTURAL_GATES

    for gate in STRUCTURAL_GATES:
        assert gate in GATE_LABELS
    for gate in ("AboveTrigger", "OutOfReach", "InvalidRisk",
                 "InsufficientHistory", "Unusable"):
        assert gate in GATE_LABELS
    for label in GATE_LABELS.values():
        assert "·" in label, "each label carries both languages"


def test_the_funnel_counts_the_universe_as_refusals_plus_candidates(drawn):
    funnel = {"Liquidity": 135, "Calm": 46, "OutOfReach": 16, "AboveTrigger": 21}
    breakout_watch._render_funnel(funnel, candidates=12)
    assert drawn
    assert legend(drawn[-1])["مرشح · approaching the trigger"][0] == 12
    # 135 + 46 + 16 + 21 + 12 = 230
    assert sum(widths(drawn[-1])) == pytest.approx(100.0, abs=0.01)


def test_an_unknown_funnel_key_is_ignored_rather_than_mislabelled(drawn):
    breakout_watch._render_funnel({"Liquidity": 10, "SomethingNew": 999}, candidates=5)
    assert "SomethingNew" not in drawn[-1]
    assert legend(drawn[-1])["مرشح · approaching the trigger"][0] == 5


def test_a_week_with_no_candidates_still_draws_the_funnel(drawn):
    """It is the only thing on screen that says why the list is empty."""
    breakout_watch._render_funnel({"Liquidity": 200, "Calm": 30}, candidates=0)
    assert drawn and 'class="egx-funnel"' in drawn[-1]
