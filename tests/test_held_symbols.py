"""A symbol held for manual review: shown, and marked everywhere it is shown.

Sixteen symbols were classified TIER_D on 2026-08-27. On the EODHD route the
router sends those to the local-seed path, and for the ones with no seed that
produced `DATA_UNAVAILABLE` -- so the AI Analysis page reported "insufficient
data" about symbols with a complete current EODHD series sitting behind the
classification. JUFO had 3,551 sessions through 2026-09-10 and showed an empty
price summary.

The router already had the door: `allow_held=True` serves those frames marked
`automatic_use_permitted: False` with a `held_reason` -- display and analysis
only. Nothing in the interface read either flag, which is the part that makes
opening the door safe rather than reckless: a held symbol rendered exactly like
any other is worse than a held symbol not rendered at all.

These pin that the flag travels, and that it is visible on the status bar, in
the warnings, above the numbers, and on the exported card -- the card
especially, because the card is the thing that leaves the application.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from core.ai_stock_analysis_contract import DataQualitySummary
from dashboard import ai_stock_analysis as page
from dashboard.ai_stock_analysis_components import (build_card_payload,
                                                    data_quality_warnings,
                                                    fixture_analysis)


@pytest.fixture()
def result():
    return fixture_analysis("COMI").result


def held(result, reason="TIER_D held for manual review"):
    return replace(result, data_quality=replace(
        result.data_quality, automatic_use_permitted=False, held_reason=reason))


# --- the contract ---------------------------------------------------------------

def test_permission_is_a_typed_field_not_a_note():
    """A note is a string in a tuple that every consumer may ignore. This one
    decides what the reader is allowed to conclude."""
    quality = DataQualitySummary(status=None)
    assert quality.automatic_use_permitted is True
    assert quality.held_reason == ""


def test_an_ordinary_symbol_is_permitted_by_default(result):
    assert result.data_quality.automatic_use_permitted is True


# --- the page -------------------------------------------------------------------

def test_a_held_symbol_is_named_before_its_numbers(result):
    """Below the numbers is after they have been read."""
    source = inspect.getsource(page._status_section)
    assert "automatic_use_permitted" in source
    assert source.index("automatic_use_permitted") < source.index("is_auction(")


def test_the_status_bar_carries_the_permission(result):
    source = inspect.getsource(page._status_section)
    assert "READ ONLY" in source
    assert "PERMITTED" in source


def test_the_held_state_is_an_error_not_a_caption(result):
    """It qualifies every conclusion on the screen, not one figure on it."""
    source = inspect.getsource(page._status_section)
    block = source[source.index("automatic_use_permitted"):]
    assert "st.error(" in block
    assert "st.caption(" not in block.split("is_auction(")[0]


# --- the warnings ---------------------------------------------------------------

def test_a_held_symbol_raises_an_error_level_warning(result):
    severities = {severity for severity, _, _ in
                  data_quality_warnings(held(result))}
    assert "error" in severities


def test_the_warning_says_what_may_not_be_done(result):
    english = " ".join(text for _, _, text in data_quality_warnings(held(result)))
    assert "HELD FOR MANUAL REVIEW" in english
    assert "position sizing" in english


def test_the_warning_carries_the_routers_own_reason(result):
    reason = "TIER_D held for manual review; the local-seed path cannot advance"
    english = " ".join(text for _, _, text in
                       data_quality_warnings(held(result, reason)))
    assert reason in english


def test_a_permitted_symbol_raises_no_held_warning(result):
    english = " ".join(text for _, _, text in data_quality_warnings(result))
    assert "HELD FOR MANUAL REVIEW" not in english


# --- the exported card ----------------------------------------------------------

def test_the_exported_card_is_marked(result):
    """The card leaves the application; the page does not."""
    payload = build_card_payload(held(result), None)
    assert "HELD FOR MANUAL REVIEW" in payload.data_quality_label
    assert "READ ONLY" in payload.data_quality_label


def test_the_mark_comes_first_on_the_card(result):
    payload = build_card_payload(held(result), None)
    assert payload.data_quality_label.startswith("HELD FOR MANUAL REVIEW")


def test_an_ordinary_cards_quality_line_is_unchanged(result):
    payload = build_card_payload(result, None)
    assert "HELD" not in payload.data_quality_label
    assert payload.data_quality_label.startswith("Provider")


# --- the loader -----------------------------------------------------------------

def test_the_analysis_service_is_the_one_caller_that_opens_the_door():
    """One symbol, asked for by hand, display only, production disabled. A scan
    that opened the same door would put held symbols into decisions."""
    from core import ai_stock_analysis_service as service

    source = inspect.getsource(service._default_history_loader)
    assert "allow_held=True" in source


def test_the_scanner_does_not_open_it():
    from core import data_provider

    source = inspect.getsource(data_provider)
    assert "allow_held=True" not in source, (
        "the scan would put symbols held for manual review into decisions")
