"""The watchlist is a list you can actually curate, and it caveats its own score.

Two things were wrong with this page beyond its layout:

* there was no way to add or remove a symbol from it. The empty state told the
  reader to go to another page and come back. A list you can only edit
  somewhere else is not a list you curate.
* it sorted by the score and drew it as a progress bar -- the strongest visual
  weight the table has -- while the Daily Dashboard, which computes the same
  number, carries a caption saying its rank correlation with the outcome is
  +0.01 (p = 0.76) and to read the gates instead. The same number was promoted
  on one screen and caveated on the other.

Presentation only.
"""

from __future__ import annotations

import inspect

import pytest

from dashboard import home, watchlist


def test_the_page_can_add_and_remove_symbols():
    source = inspect.getsource(watchlist._manage)
    assert "watchlist.add(" in source
    assert "watchlist.remove(" in source


def test_management_is_reachable_before_the_list_has_anything_in_it():
    """The empty state used to be a dead end pointing at another page."""
    source = inspect.getsource(watchlist.show_watchlist)
    assert source.index("_manage(") < source.index("if not symbols:")


def test_the_empty_state_no_longer_sends_the_reader_elsewhere_to_start():
    source = inspect.getsource(watchlist.show_watchlist)
    assert "Add a symbol above" in source


def test_a_symbol_already_tracked_is_not_offered_again():
    assert "not in set(symbols)" in inspect.getsource(watchlist._manage)


def test_a_broken_symbol_list_does_not_lose_the_page():
    """Curation must survive the universe being unreadable."""
    source = inspect.getsource(watchlist._manage)
    assert "except Exception" in source and "options = []" in source


# --- the score ----------------------------------------------------------------

def test_the_page_carries_the_same_score_caveat_the_dashboard_does():
    assert "not a quality ranking" in watchlist.SCORE_CAVEAT
    assert "p = 0.76" in watchlist.SCORE_CAVEAT
    assert "Read the gates" in watchlist.SCORE_CAVEAT


def test_the_caveat_matches_the_measurement_the_dashboard_quotes():
    """One number, one claim about it, on every page that shows it."""
    dashboard = inspect.getsource(home.show_dashboard)
    for figure in ("+0.15", "p = 0.76"):
        assert figure in dashboard
        assert figure in watchlist.SCORE_CAVEAT


def test_the_caveat_is_rendered_under_the_table():
    source = inspect.getsource(watchlist.show_watchlist)
    assert "st.caption(SCORE_CAVEAT)" in source
    assert source.rindex("st.dataframe(") < source.index("st.caption(SCORE_CAVEAT)")


def test_the_headline_tile_no_longer_averages_the_score():
    """An average of a number with no rank correlation is a number with no
    meaning, sat beside three tiles that count real decisions."""
    source = inspect.getsource(watchlist.show_watchlist)
    assert '.metric("Average Score"' not in source      # the call, not the comment
    assert "Withheld" in source


def test_the_results_are_named_as_the_users_own_subset():
    source = inspect.getsource(watchlist.show_watchlist)
    assert "of your" in source and "tracked symbols" in source


# --- the scan that returns nothing --------------------------------------------

def test_a_scan_that_returned_nothing_is_quiet_not_empty():
    """The scan ran. Nothing came back is an answer, not a missing page."""
    source = inspect.getsource(watchlist.show_watchlist)
    body = source.split("if not results:")[1].split("return")[0]
    assert "quiet_state(" in body
    assert "empty_state(" not in body


def test_an_empty_list_is_still_reported_as_empty():
    """`empty_state` is right for one case here: the list really is empty."""
    source = inspect.getsource(watchlist.show_watchlist)
    body = source.split("if not symbols:")[1].split("return")[0]
    assert "empty_state(" in body
