"""Gate Results: four outcomes, and none of them can be mistaken for a pass.

This is the only panel in the product that shows *why* a decision came out the
way it did, gate by gate. It was a two-column dataframe whose Result column was
coloured by a substring test -- green if the text contained "PASS", red if it
contained "FAIL" or "LOW", one grey for everything else -- so a gate that could
not run and a gate that was never reached were the same grey, and the green and
red were #059669 and #dc2626: Tailwind's defaults, not this project's palette.

The engine writes four things into a trace, and they are four different facts:

    PASS         the check ran and was satisfied
    FAIL         the check ran and was not
    UNAVAILABLE  the check ran and could not be computed
    N/A          an earlier gate stopped the chain; this one never ran

Presentation only.
"""

from __future__ import annotations

import re

import pytest

from dashboard import stock_details, ui
from dashboard.stock_details import _gate_state, _render_gates
from dashboard.ui import (GATE_FAIL, GATE_NOT_REACHED, GATE_PASS,
                          GATE_UNAVAILABLE)


@pytest.fixture
def drawn(monkeypatch):
    written = []
    monkeypatch.setattr(stock_details.st, "markdown",
                        lambda body, **kwargs: written.append(body))
    monkeypatch.setattr(stock_details.st, "caption", lambda *a, **k: None)
    return written


def classes(markup):
    return re.findall(r'class="egx-gate (\w+)"', markup)


# --- the four outcomes --------------------------------------------------------

@pytest.mark.parametrize("written, state", [
    ("PASS", GATE_PASS), ("pass", GATE_PASS),
    ("FAIL", GATE_FAIL), ("fail", GATE_FAIL),
    ("UNAVAILABLE", GATE_UNAVAILABLE),
    ("N/A", GATE_NOT_REACHED), ("", GATE_NOT_REACHED),
])
def test_every_spelling_the_engine_writes_maps_to_its_own_outcome(written, state):
    assert _gate_state(written) == state


@pytest.mark.parametrize("value", ["LOW", "weird", None, 0, "PASSED?", "maybe"])
def test_anything_unrecognised_is_unavailable_and_never_a_pass(value):
    """A gate whose result nobody can interpret must not read as satisfied."""
    assert _gate_state(value) == GATE_UNAVAILABLE


def test_not_reached_is_not_the_same_fact_as_unavailable():
    assert GATE_NOT_REACHED != GATE_UNAVAILABLE
    assert _gate_state("N/A") != _gate_state("UNAVAILABLE")


def test_the_two_no_result_outcomes_look_alike_and_read_apart(drawn):
    """Both are "no result" to the eye. The words say which."""
    _render_gates({"A": "UNAVAILABLE", "B": "N/A"})
    markup = drawn[-1]
    assert classes(markup) == ["na", "na"]
    assert "UNAVAILABLE" in markup and "NOT REACHED" in markup


# --- the panel ----------------------------------------------------------------

def test_a_full_trace_renders_one_chip_per_gate(drawn):
    trace = {"MarketAnalyzer": "PASS", "MarketRegime": "PASS", "Trend": "FAIL",
             "Momentum": "PASS", "Volume": "FAIL", "Risk": "PASS",
             "QualityFilter": "PASS", "CandleConfirmation": "PASS"}
    _render_gates(trace)
    assert len(classes(drawn[-1])) == len(trace)
    assert classes(drawn[-1]).count("fail") == 2


def test_every_gate_is_named_beside_its_outcome(drawn):
    _render_gates({"QualityFilter": "FAIL"})
    assert "QualityFilter FAIL" in drawn[-1]


def test_a_blocked_chain_shows_no_passes_it_did_not_earn(drawn):
    """What the engine writes when the market gate stops everything."""
    _render_gates({"MarketAnalyzer": "FAIL", "MarketRegime": "N/A",
                   "Trend": "N/A", "Momentum": "N/A", "Volume": "N/A",
                   "Risk": "N/A", "QualityFilter": "N/A",
                   "CandleConfirmation": "N/A"})
    assert "pass" not in classes(drawn[-1])
    assert classes(drawn[-1]).count("na") == 7


def test_the_panel_counts_how_many_gates_carry_no_result(monkeypatch):
    captions = []
    monkeypatch.setattr(stock_details.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(stock_details.st, "caption",
                        lambda body, **kwargs: captions.append(body))
    _render_gates({"A": "PASS", "B": "N/A", "C": "UNAVAILABLE"})
    assert captions and "2 of 3" in captions[0]
    assert "Neither is a pass" in captions[0]


def test_a_clean_trace_says_nothing_extra(monkeypatch):
    captions = []
    monkeypatch.setattr(stock_details.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(stock_details.st, "caption",
                        lambda body, **kwargs: captions.append(body))
    _render_gates({"A": "PASS", "B": "FAIL"})
    assert not captions


def test_a_gate_name_cannot_inject_markup(drawn):
    _render_gates({"<script>alert(1)</script>": "PASS"})
    assert "<script>" not in drawn[-1]


# --- the colours it used to use ------------------------------------------------

def test_the_panel_no_longer_carries_tailwinds_palette():
    """Checked against the code, not the prose: the docstring that records the
    removal names the very hexes it removed."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(stock_details))
    for node in ast.walk(tree):                  # drop every docstring
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    code = ast.unparse(tree)
    for drifted in ("#059669", "#dc2626", "#64748b"):
        assert drifted not in code


def test_the_gate_colours_come_from_the_design_system(monkeypatch):
    captured = []
    original = ui.st.markdown
    ui.st.markdown = lambda body, **kwargs: captured.append(body)
    try:
        ui.apply_global_style()
    finally:
        ui.st.markdown = original
    for state, token in (("pass", "--green"), ("fail", "--red"), ("na", "--amber")):
        rule = re.search(rf"\.egx-gate\.{state}\s*\{{(.*?)\}}", captured[0], re.S)
        assert rule and token in rule.group(1)


# --- charts take their colours from the same palette ---------------------------

def test_no_dashboard_chart_picks_its_own_colour():
    """CSS variables cannot reach a chart library, so charts were choosing:
    #2563eb, #0891b2, #dc2626, #64748b. The same "grey" on two pages was two
    greys, and a drawdown chart was Tailwind red beside a design-system legend.
    """
    import re
    from pathlib import Path

    offenders = []
    for path in sorted(Path("dashboard").glob("*.py")):
        if path.name == "ui.py":
            continue
        for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""\bcolor\s*=\s*["']#[0-9a-fA-F]{6}["']""", line):
                offenders.append(f"{path.name}:{line_no} {line.strip()[:60]}")
    assert not offenders, "charts with a hardcoded colour: " + "; ".join(offenders)


def test_the_palette_is_available_to_python_as_literal_hex():
    from dashboard.ui import COLOURS, _TONE_COLOUR

    for name, rgb in _TONE_COLOUR.items():
        assert COLOURS[name] == "#{:02x}{:02x}{:02x}".format(*rgb)
    assert COLOURS["green"] == "#3ddc97"
    assert COLOURS["red"] == "#ff5c6c"
