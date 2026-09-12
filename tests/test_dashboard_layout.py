"""The Daily Dashboard's composition, rebuilt to the Stitch design.

Three things the page did that the design does differently:

* seven metric tiles took a third of the screen above the first result, for
  seven readings that are swept across rather than stopped at;
* the headline "top opportunities" section was the same twelve-column table as
  the reference table below it, showing its first ten rows -- the same object
  at two lengths;
* one fact about excluded symbols arrived as a three-line Streamlit warning.

Presentation only: every value still comes from the scan row.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from dashboard import home
from dashboard.ui import (_scale_positions, context_strip, notice,
                          opportunity_card, opportunity_grid)


# --- the context strip ---------------------------------------------------------

def test_the_strip_puts_every_reading_on_one_line():
    markup = context_strip([("شراء BUY", "3", "green", "1%"),
                            ("تجنب AVOID", "97", "red", "46%")])
    assert markup.count('class="c"') == 2
    assert "egx-strip" in markup


def test_a_reading_carries_its_tone_on_the_dot_and_the_value():
    """Against the palette, not a literal: the hex moved once already."""
    from dashboard.ui import COLOURS

    markup = context_strip([("شراء BUY", "3", "green", "")])
    assert markup.count(COLOURS["green"]) == 2      # the dot and the value


def test_a_sub_value_is_optional():
    assert "<small>" not in context_strip([("الجلسة", "2026-09-10", "gray", "")])
    assert "<small>" in context_strip([("شراء", "3", "green", "1%")])


def test_a_count_and_its_share_cannot_read_as_one_number():
    """On screen this read "10952%".

    The count and its share are both numerals in the same tabular face, and
    .2rem of margin between them is not a gap a reader can see. 109 watched and
    52% of the scan rendered as one nine-figure number, 100 avoided and 48% as
    "10048%", and a coverage of 209/225 with 16 excluded as "209/225-16". The
    separator has to be a character in the markup, where it cannot be lost to
    a stylesheet change.
    """
    text = re.sub(r"<[^>]+>", "", context_strip([("متابعة WATCH", "109",
                                                  "amber", "52%")]))
    assert "10952" not in text
    # Both spaces are in the markup. The left one is a non-breaking space
    # rather than a margin, so a stylesheet change cannot close the gap again.
    assert "109 · 52%" in text


def test_a_reading_with_no_share_gains_no_orphan_separator():
    text = re.sub(r"<[^>]+>", "", context_strip([("الجلسة", "2026-09-10",
                                                  "gray", "")]))
    assert "·" not in text


def test_a_reading_cannot_inject_markup():
    markup = context_strip([("<script>a</script>", "<script>b</script>",
                             "green", "<script>c</script>")])
    assert "<script>" not in markup


# --- the trade drawn to scale ---------------------------------------------------

def test_the_band_and_the_close_land_where_the_prices_put_them():
    """The design's own COMI numbers: stop 89.50, band 91.80-92.50, close
    92.40, second target 101.00. The span is 11.50."""
    places = _scale_positions(89.50, 91.80, 92.50, 92.40, 101.00)
    assert places["band_left"] == pytest.approx(20.0, abs=0.1)
    assert places["now"] == pytest.approx(25.2, abs=0.1)
    assert places["risk"] + places["reward"] < 100


@pytest.mark.parametrize("levels", [
    (0, 0, 0, 1120.0, 0),               # a refused name: every level absent
    (None, 91.8, 92.5, 92.4, 101.0),
    (89.5, 91.8, 92.5, 92.4, None),
    (100.0, 95.0, 96.0, 97.0, 90.0),    # target below the stop
    (92.0, 91.8, 92.5, 92.4, 92.0),     # no span at all
])
def test_a_rail_is_never_drawn_from_a_level_that_does_not_exist(levels):
    """A picture of a trade nobody computed."""
    assert _scale_positions(*levels) is None


def test_a_reversed_band_is_read_rather_than_refused():
    low_first = _scale_positions(89.5, 91.8, 92.5, 92.4, 101.0)
    high_first = _scale_positions(89.5, 92.5, 91.8, 92.4, 101.0)
    assert low_first == high_first


def test_the_close_cannot_be_drawn_outside_the_rail():
    places = _scale_positions(89.5, 91.8, 92.5, 500.0, 101.0)
    assert 0.0 <= places["now"] <= 100.0


# --- the opportunity card -------------------------------------------------------

def card(**overrides):
    defaults = dict(
        ticker="COMI", name="البنك التجاري الدولي", sector="Banks",
        regime="BULL", signal="BUY", tone="green",
        figures=(("السعر", "92.40", None), ("الوقف", "89.50", "stop")),
        stop=89.5, buy_low=91.8, buy_high=92.5, price=92.4, target2=101.0)
    defaults.update(overrides)
    return opportunity_card(**defaults)


def test_a_card_names_what_it_is_and_what_was_decided():
    markup = card()
    assert "COMI" in markup and "البنك التجاري الدولي" in markup
    assert "Banks · BULL" in markup
    assert ">BUY<" in markup


def test_the_card_is_toned_by_its_decision():
    assert 'class="egx-opp buy"' in card(signal="BUY")
    assert 'class="egx-opp watch"' in card(signal="WATCH", tone="blue")
    assert 'class="egx-opp avoid"' in card(signal="AVOID", tone="red")


def test_a_card_without_levels_carries_no_rail():
    markup = card(stop=0, buy_low=0, buy_high=0, target2=0)
    assert 'class="scale"' not in markup
    assert "COMI" in markup            # the card still renders


def test_a_card_with_levels_carries_the_rail_and_its_legend():
    markup = card()
    assert 'class="scale"' in markup
    assert "STOP 89.50" in markup and "TGT2 101.00" in markup
    assert "[91.80 – 92.50]" in markup


def test_the_four_figures_keep_their_own_emphasis():
    markup = card()
    assert 'class="v stop"' in markup


def test_a_card_cannot_inject_markup():
    markup = card(ticker="<script>a</script>", name="<script>b</script>",
                  sector="<script>c</script>")
    assert "<script>" not in markup


def test_the_grid_holds_the_cards():
    assert opportunity_grid([card(), card()]).count("egx-opp ") == 2


# --- the page ------------------------------------------------------------------

def test_the_headline_section_is_no_longer_the_reference_table(monkeypatch):
    written = []
    monkeypatch.setattr(home.st, "markdown", lambda body, **kw: written.append(body))
    monkeypatch.setattr(home.st, "dataframe",
                        lambda *a, **k: written.append("DATAFRAME"))
    frame = pd.DataFrame([{
        "Ticker": "COMI.CA", "Signal": "BUY", "Regime": "BULL", "Sector": "Banks",
        "Price": 92.4, "BuyLow": 91.8, "BuyHigh": 92.5, "StopLoss": 89.5,
        "Target1": 96.5, "Target2": 101.0, "RR": 2.45, "Confidence": 88,
    }])
    home._render_opportunities(frame)
    assert written and "DATAFRAME" not in written
    assert "egx-opps" in written[-1]


def test_a_refused_row_still_renders_as_a_card_without_a_rail(monkeypatch):
    written = []
    monkeypatch.setattr(home.st, "markdown", lambda body, **kw: written.append(body))
    frame = pd.DataFrame([{
        "Ticker": "ESRS.CA", "Signal": "AVOID", "Regime": "BEAR", "Sector": "Steel",
        "Price": 1120.0, "BuyLow": 0.0, "BuyHigh": 0.0, "StopLoss": 0.0,
        "Target1": 0.0, "Target2": 0.0, "RR": 0.0, "Confidence": 0,
    }])
    home._render_opportunities(frame)
    markup = written[-1]
    assert "ESRS" in markup
    assert 'class="scale"' not in markup
    # The close is real and is shown. Every level the engine left at zero is an
    # em dash -- checked cell by cell, because "1,120.00" legitimately contains
    # the digits a cruder assertion would trip on.
    cells = dict(zip(re.findall(r'class="k">([^<]*)</div>', markup),
                     re.findall(r'class="v[^"]*">([^<]*)</div>', markup)))
    assert cells["السعر"] == "1,120.00"
    for absent in ("الوقف", "هدف 1", "R:R"):
        assert cells[absent] == "—", f"{absent} quoted a level that does not exist"


def test_the_page_uses_the_strip_and_the_notice_rather_than_tiles_and_a_warning():
    import inspect

    source = inspect.getsource(home.show_dashboard)
    assert "context_strip(" in source
    assert "notice(" in source
    assert "_render_opportunities(" in source
    assert "metric_card(" not in source


def test_a_name_with_no_sector_gets_no_orphan_separator():
    """It rendered "· BULL" -- a separator with nothing on one side of it."""
    assert ">BULL<" in card(sector="", regime="BULL")
    assert "· BULL" not in card(sector="", regime="BULL")
    assert "Banks · BULL" in card(sector="Banks", regime="BULL")
    assert ">Banks<" in card(sector="Banks", regime="")


def test_a_name_with_neither_sector_nor_regime_has_no_line_at_all():
    assert 'class="where"' not in card(sector="", regime="")
