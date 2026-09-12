"""The tier rule reads the manual queue's verdict.

`full_universe_symbol_results.csv` classifies a symbol MANUAL_REVIEW when the
audit could not decide between EODHD and Yahoo and deferred to a human.
`manual_queue_resolution.csv` is that human's answer, adjudicated against a
Rubix price.

The rule held every MANUAL_REVIEW symbol regardless of whether the answer
existed. Ten of the sixteen held symbols had one, and it said EODHD was
correct -- seven with all three sources agreeing and four with Yahoo simply out
of date. The branch immediately above it in the same chain already consulted
the queue (`PRICE_SCALE_ANOMALY and not q`); this one did not, so a resolved
question and an unasked one produced the same hold.

These pin the four outcomes the queue can produce, and the one thing an
adjudication must never do: hand back a Yahoo fallback to a symbol whose
adjudication was that Yahoo is the wrong series.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

REVIEW = Path("data/eodhd/historical_symbol_routing_review.json")
REPORTS = Path("reports/eodhd")


@pytest.fixture(scope="module")
def entries():
    data = json.loads(REVIEW.read_text(encoding="utf-8"))
    return {e["symbol"]: e for e in data["symbols"]}


@pytest.fixture(scope="module")
def queue():
    frame = pd.read_csv(REPORTS / "manual_queue_resolution.csv", keep_default_na=False)
    return {str(row["symbol"]): (str(row["classification"]), str(row["evidence"]))
            for _, row in frame.iterrows()}


@pytest.fixture(scope="module")
def categories():
    frame = pd.read_csv(REPORTS / "full_universe_symbol_results.csv",
                        keep_default_na=False)
    return dict(zip(frame["symbol"], frame["category"]))


def adjudicated(queue, categories, verdict):
    return sorted(sym for sym, (cls, _) in queue.items()
                  if cls == verdict and categories.get(sym) == "MANUAL_REVIEW")


# --- the verdict is read --------------------------------------------------------

def test_a_symbol_the_queue_cleared_is_no_longer_held(entries, queue, categories):
    cleared = adjudicated(queue, categories, "EODHD_CORRECT")
    assert cleared, "fixture drift: no adjudicated symbols to check"
    still_held = [s for s in cleared
                  if entries[s]["tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL"]
    assert not still_held, f"held despite a queue verdict: {still_held}"


def test_a_cleared_symbol_says_what_cleared_it(entries, queue, categories):
    for sym in adjudicated(queue, categories, "EODHD_CORRECT"):
        assert entries[sym]["evidence_status"] == "rubix_adjudicated"
        assert "adjudicated EODHD correct against Rubix" in entries[sym]["approval_reason"]


def test_a_cleared_symbol_never_gets_a_yahoo_fallback(entries, queue, categories):
    """The adjudication IS that Yahoo was the wrong series. Falling back to it
    would undo the review that released the symbol."""
    for sym in adjudicated(queue, categories, "EODHD_CORRECT"):
        assert entries[sym]["fallback_provider"] is None, sym
        assert entries[sym]["tier"] != "TIER_A_FORWARD_SAFE", (
            f"{sym} is TIER_A, which is defined as forward-safe WITH a Yahoo "
            f"fallback")


def test_clearing_a_hold_does_not_clear_the_other_evidence(entries, queue, categories):
    """A recent split or a deep-history difference still sends a symbol to
    TIER_C. The queue answered one question, not all of them."""
    cleared = adjudicated(queue, categories, "EODHD_CORRECT")
    tiers = {entries[s]["tier"] for s in cleared}
    assert tiers <= {"TIER_B_FORWARD_EODHD_NO_FALLBACK", "TIER_C_HISTORICAL_REVIEW"}
    assert "TIER_C_HISTORICAL_REVIEW" in tiers, (
        "every cleared symbol skipped the corp-action branch, which is suspicious")


# --- and the verdicts that do not clear -----------------------------------------

def test_a_verdict_against_eodhd_stays_held(entries, queue, categories):
    """YAHOO_CORRECT / STALE_EODHD means EODHD is the stale series here."""
    for sym in adjudicated(queue, categories, "YAHOO_CORRECT"):
        assert entries[sym]["tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL", sym
        assert "stale" in entries[sym]["approval_reason"].lower()


def test_an_unadjudicated_symbol_stays_held(entries, queue, categories):
    """No valid Rubix price to judge against. An absence of a verdict is not
    a verdict."""
    for sym, (cls, _) in queue.items():
        if cls == "SYMBOL_NOT_LIQUID":
            assert entries[sym]["tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL", sym
            assert "no valid Rubix price" in entries[sym]["approval_reason"]


def test_a_manual_review_with_no_queue_row_stays_held(entries, queue, categories):
    unanswered = [sym for sym, cat in categories.items()
                  if cat == "MANUAL_REVIEW" and sym not in queue
                  and sym in entries]
    for sym in unanswered:
        assert entries[sym]["tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL", sym


# --- the proposal is still a proposal --------------------------------------------

def test_regenerating_the_review_activates_nothing(entries):
    """The review file is the proposal; the active file is the authority, and
    promotion is a separate deliberate act."""
    data = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert data["active"] is False
    assert not any(e["approved"] for e in data["symbols"])
