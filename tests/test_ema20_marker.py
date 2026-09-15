"""The Daily Dashboard's EMA20 marker: shown beside every signal, deciding nothing.

`scripts/research/ema20_entry_gate.py` measured that this strategy's trades
entered at or below EMA20 did markedly better in both eras (PF 2.42 against
1.54). The owner chose to show that as a marker and let the live record confirm
it before any rule changes. These pin the marker's three promises:

* the threshold is the pre-registered one, zero, and the boundary belongs to
  "at or below";
* a distance nobody measured is its own state, never a side and never 0.0%;
* the marker is advisory: blue or grey, never the green of an instruction, and
  the decision on the row is untouched.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from dashboard import home, ui


# --- the side ---------------------------------------------------------------------

@pytest.mark.parametrize("distance, side", [
    (-3.2, ui.EMA20_AT_OR_BELOW), (0.0, ui.EMA20_AT_OR_BELOW), (-0.0, ui.EMA20_AT_OR_BELOW),
    (0.001, ui.EMA20_ABOVE), (4.5, ui.EMA20_ABOVE),
    (None, ui.EMA20_UNMEASURED), (float("nan"), ui.EMA20_UNMEASURED),
    ("", ui.EMA20_UNMEASURED), ("n/a", ui.EMA20_UNMEASURED),
])
def test_the_side_uses_the_pre_registered_threshold(distance, side):
    assert ui.ema20_side(distance) == side


def test_the_threshold_is_the_one_that_was_measured():
    from scripts.research.ema20_entry_gate import GATES

    registered = {label: threshold for label, threshold, _ in GATES}
    assert ui.EMA20_THRESHOLD == registered["ema20_dist <= 0"] == 0.0


# --- the text and the chip --------------------------------------------------------

def test_the_text_carries_the_distance_and_the_side():
    assert ui.ema20_marker_text(-2.34) == "-2.3% · تحت EMA20"
    assert ui.ema20_marker_text(1.8) == "+1.8% · فوق EMA20"


def visible(chip):
    """What the chip shows, without its hover text.

    The hover text explains the measurement and legitimately says "تحت" and
    quotes percentages; the promise is about what is drawn on the chip.
    """
    import re

    return re.sub(r"<[^>]+>", "", re.sub(r'\stitle="[^"]*"', "", chip))


@pytest.mark.parametrize("missing", [None, float("nan"), ""])
def test_an_unmeasured_distance_is_never_drawn_as_a_number(missing):
    assert ui.ema20_marker_text(missing) == "—"
    chip = ui.ema20_marker_html(missing)
    shown = visible(chip)
    assert "%" not in shown and "تحت" not in shown and "فوق" not in shown
    assert shown == "EMA20 —·—"
    assert "var(--unknown)" in chip


def test_the_marker_is_advisory_never_the_colour_of_an_instruction():
    below = ui.ema20_marker_html(-2.0)
    above = ui.ema20_marker_html(2.0)
    assert ui.COLOURS["green"] not in below and ui.COLOURS["green"] not in above
    assert ui.COLOURS["blue"] in below
    assert ui.COLOURS["gray"] in above


def test_the_marker_says_what_it_is_on_hover():
    chip = ui.ema20_marker_html(-1.0)
    assert "title=" in chip
    assert "ليست قاعدة" in ui.EMA20_MARKER_HELP
    assert "2.42" in ui.EMA20_MARKER_HELP and "1.54" in ui.EMA20_MARKER_HELP


def test_the_card_shows_the_marker_beside_the_decision():
    card = ui.opportunity_card(ticker="COMI", signal="BUY", tone="green",
                               marker_html=ui.ema20_marker_html(-2.0))
    assert "تحت EMA20" in card
    assert card.index(">BUY<") < card.index("تحت EMA20")


def test_a_card_without_a_marker_is_unchanged():
    assert "EMA20" not in ui.opportunity_card(ticker="COMI", signal="BUY")


# --- reading the distance off a scan row ------------------------------------------

def scan_row(**overrides):
    row = {"Ticker": "COMI.CA", "Signal": "BUY", "Regime": "BULL", "Price": 92.4,
           "BuyLow": 91.8, "BuyHigh": 92.5, "StopLoss": 89.5, "Target1": 96.5,
           "Target2": 101.0, "RR": 2.45, "Confidence": 88,
           "OperationalStatus": "QUALIFIED_ENTRY",
           "AIFeatures": pd.Series({"EMA20_DIST": -2.34, "RSI": 44.0})}
    row.update(overrides)
    return row


def test_the_distance_is_read_from_the_features_the_scanner_attached():
    assert home._ema20_distance(scan_row()) == pytest.approx(-2.34)


def test_the_distance_falls_back_to_the_last_bar_of_the_data():
    data = pd.DataFrame({"EMA20_DIST": [5.0, 1.25]})
    assert home._ema20_distance(scan_row(AIFeatures=None, Data=data)) == pytest.approx(1.25)


def test_no_features_and_no_data_is_unmeasured():
    assert home._ema20_distance(scan_row(AIFeatures=None)) is None


def test_the_compact_table_carries_the_marker_column():
    frame = home._swing_primary_frame(pd.DataFrame([
        scan_row(), scan_row(Ticker="ESRS.CA", AIFeatures=pd.Series({"EMA20_DIST": 3.0})),
        scan_row(Ticker="ABUK.CA", AIFeatures=None)]))
    assert home.EMA20_COLUMN in home.SWING_PRIMARY_COLUMNS
    assert list(frame[home.EMA20_COLUMN]) == [
        "-2.3% · تحت EMA20", "+3.0% · فوق EMA20", "—"]


def test_the_marker_changes_no_decision():
    source = pd.DataFrame([scan_row()])
    before = source.copy(deep=True)
    frame = home._swing_primary_frame(source)
    assert frame.loc[0, "القرار"] == "BUY"
    assert list(source.columns) == list(before.columns)


# --- the live confirmation ----------------------------------------------------------

def test_the_replay_reads_the_same_field_the_marker_reads():
    from scripts.evaluate_daily_dashboard_buys import ema20_distance

    assert ema20_distance(json.dumps({"EMA20_DIST": -1.5})) == -1.5
    assert ema20_distance(json.dumps({"EMA20_DIST": None})) is None
    assert ema20_distance("not json") is None
    assert ema20_distance(None) is None


def test_the_replay_splits_closed_distinct_trades_at_zero():
    from scripts.evaluate_daily_dashboard_buys import split_by_ema20

    rows = [
        {"outcome": "CLOSED", "repeat_of_open": False, "ema20_dist": -1.0, "net_return_pct": 2.0},
        {"outcome": "CLOSED", "repeat_of_open": False, "ema20_dist": 0.0, "net_return_pct": -1.0},
        {"outcome": "CLOSED", "repeat_of_open": False, "ema20_dist": 2.0, "net_return_pct": -3.0},
        {"outcome": "CLOSED", "repeat_of_open": True, "ema20_dist": -5.0, "net_return_pct": 9.0},
        {"outcome": "OPEN", "repeat_of_open": False, "ema20_dist": -5.0, "net_return_pct": 9.0},
        {"outcome": "CLOSED", "repeat_of_open": False, "ema20_dist": None, "net_return_pct": 1.0},
    ]
    split = split_by_ema20(rows)
    assert split["at/below EMA20"].startswith("n=2 win=50%")
    assert split["above EMA20"].startswith("n=1 win=0%")
    assert split["unmeasured"].startswith("n=1")
