"""Resolving a split event means having evidence, not having an opinion.

An EODHD "split" on EGX is a mix of true share-count events, bonus and
capital-increase events, and provider disagreements. The volume rule differs
between them, so an event with no validated entry is UNRESOLVED and the
symbol's volume is withheld -- six symbols were blocked that way, JUFO among
them.

`scripts/audit_corporate_actions_against_mubasher` resolves one on two legs
that have to agree: EODHD's RAW price dividing by the declared ratio, and the
measured Mubasher volume stepping by that same ratio across the ex-date. These
pin the classifier, including the two mistakes the first run of it made:
measuring the price on the already-adjusted series (which shows no drop at all),
and testing the volume LEVEL after the event instead of the STEP across it
(which called three genuine events EVENT_SPECIFIC, because the ratio carries
every later adjustment too).
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.audit_corporate_actions_against_mubasher import (PRICE_TOLERANCE,
                                                              VOLUME_TOLERANCE,
                                                              classify)


def ratios(value, count=5):
    return [value] * count


# --- both legs agree ------------------------------------------------------------

def test_a_true_split_is_resolved_when_both_legs_agree():
    """OCPH 2026-08-06: raw price 480.00 -> 238.76 on a declared 2.0, and
    Mubasher volume exactly 2x EODHD raw before the ex-date, 1x after."""
    action, rule, evidence = classify(2.0, 2.0104, ratios(2.0), ratios(1.0))
    assert (action, rule) == ("SPLIT", "MULTIPLY_BY_FACTOR")
    assert "2.0104" in evidence and "steps by x2.0000" in evidence


def test_the_step_is_measured_not_the_level():
    """JUFO 2025-11-06 sits behind a later event, so the ratio is 1.5625 before
    and 1.2500 after -- a step of exactly the declared 1.25, at neither 1.25
    nor 1.0. Testing the level refused three genuine events."""
    action, rule, _ = classify(1.25, 1.2321, ratios(1.5625), ratios(1.25))
    assert (action, rule) == ("SPLIT", "MULTIPLY_BY_FACTOR")


def test_the_most_recent_event_steps_to_one():
    action, rule, _ = classify(1.25, 1.2608, ratios(1.25), ratios(1.0))
    assert rule == "MULTIPLY_BY_FACTOR"


def test_a_noisy_session_after_the_event_does_not_overturn_the_median():
    """EEII's fourth and fifth sessions after the ex-date drift to 0.92 and
    0.96 -- two providers disagreeing about recent raw volume, which is not
    evidence about this event."""
    action, rule, _ = classify(1.206583, 1.2340,
                               ratios(1.206583), [1.0, 1.0, 1.0, 0.9193, 0.9619])
    assert rule == "MULTIPLY_BY_FACTOR"


# --- the legs disagree ----------------------------------------------------------

def test_a_price_that_does_not_divide_is_not_resolved_as_a_split():
    """This is the measurement the whole thing rests on: a bonus issue does not
    divide the price."""
    action, rule, _ = classify(2.0, 1.005, ratios(1.0), ratios(1.0))
    assert rule != "MULTIPLY_BY_FACTOR"
    assert action == "STOCK_DIVIDEND_BONUS"


def test_volume_evidence_without_price_evidence_is_not_resolved():
    action, rule, evidence = classify(1.25, 1.01, ratios(1.25), ratios(1.0))
    assert rule == "EVENT_SPECIFIC"
    assert "disagree" in evidence


def test_price_evidence_without_volume_evidence_is_not_resolved():
    action, rule, evidence = classify(1.25, 1.2500, ratios(1.0), ratios(1.0))
    assert (action, rule) == ("SPLIT_PROVIDER_DIFF", "EVENT_SPECIFIC")
    assert "no share-count adjustment" in evidence


def test_no_second_source_means_unresolved_rather_than_a_guess():
    action, rule, evidence = classify(2.0, 2.0, [], [])
    assert rule == "UNRESOLVED"
    assert "no measured Mubasher volume" in evidence


def test_a_missing_price_never_crashes_the_classifier():
    """The evidence line interpolates the drop; None used to reach the format."""
    for before, after in ((ratios(1.25), ratios(1.0)), (ratios(1.0), ratios(1.0))):
        action, rule, evidence = classify(1.25, None, before, after)
        assert rule in ("EVENT_SPECIFIC", "KEEP_RAW")
        assert "no price evidence" in evidence


# --- the tolerances -------------------------------------------------------------

def test_the_volume_leg_is_held_far_tighter_than_the_price_leg():
    """The price drop is a market measurement; the volume ratio is an exact
    arithmetic adjustment, and it came back exact to four decimals."""
    assert VOLUME_TOLERANCE < PRICE_TOLERANCE / 10


def test_a_volume_step_that_is_merely_close_is_not_resolved():
    action, rule, _ = classify(1.25, 1.25, ratios(1.20), ratios(1.0))
    assert rule != "MULTIPLY_BY_FACTOR"


# --- what the resolution does to the served series ------------------------------

def test_the_reconciliation_now_answers_the_events_that_were_blocking():
    from providers.eodhd_volume_adjustment import (MULTIPLY_BY_FACTOR,
                                                   event_volume_policy)

    for symbol, day in (("ACGC", "2026-08-20"), ("EEII", "2026-09-03"),
                        ("JUFO", "2026-08-06"), ("MTIE", "2026-08-13"),
                        ("NCCW", "2026-08-06"), ("OCPH", "2026-08-06")):
        assert event_volume_policy(symbol, day) == MULTIPLY_BY_FACTOR, symbol


def test_every_recorded_rule_is_one_the_policy_knows():
    import pandas as pd

    from providers.eodhd_volume_adjustment import (EVENT_SPECIFIC, KEEP_RAW,
                                                   MULTIPLY_BY_FACTOR,
                                                   PROVIDER_ALREADY_ADJUSTED,
                                                   UNRESOLVED)

    known = {MULTIPLY_BY_FACTOR, KEEP_RAW, PROVIDER_ALREADY_ADJUSTED,
             EVENT_SPECIFIC, UNRESOLVED}
    frame = pd.read_csv("reports/eodhd/corporate_action_reconciliation.csv")
    assert set(frame["volume_rule"].str.upper()) <= known


def test_every_resolved_row_states_its_evidence():
    frame = pd.read_csv("reports/eodhd/corporate_action_reconciliation.csv")
    resolved = frame[frame["volume_rule"] != "UNRESOLVED"]
    assert not resolved["evidence"].isna().any()
    assert (resolved["evidence"].str.len() > 20).all()


def test_the_policy_version_moved_with_the_file():
    """It travels on every frame as provenance. A file that changed under a
    version that did not is a frame claiming to have been built by rules it was
    not built by."""
    from providers.eodhd_volume_adjustment import CORPORATE_ACTION_POLICY_VERSION

    assert CORPORATE_ACTION_POLICY_VERSION != "corporate_action_reconciliation@2026-07-23"
