"""A target below the entry is refused, not reported as a negative RR.

`entry_signal` anchors both targets to `resistance`, the highest high of the
previous twenty bars *excluding today*. When today's close has already passed
that level, target1 sits below the entry and the reward is negative before
anything has happened. OFH.CA on 2026-08-26 closed at 1.10 against a 1.00
resistance and reached the dashboard as RR -0.05, with nothing on the row to
explain it.

The contradiction is in the function itself: the same condition -- price above
resistance -- is rewarded eight points and the reason "Confirmed Breakout" forty
lines before the target is placed at that same resistance. The stronger the
breakout, the more negative the reward.

It is resolved by refusing rather than by inventing a target, because this
strategy's quality filter requires at least 3% of room *below* resistance, and
dissolving the contradiction the other way was measured and is worse: profit
factor 0.90 against 0.94, return -23.79% against -14.27%. A stock above its
resistance is not a setup this strategy takes, so it says so -- exactly as the
neighbouring `risk <= 0` guard already did.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.entry import entry_signal


def _frame(closes, atr=0.05):
    """A frame whose highs equal its closes, so resistance is easy to reason about."""
    n = len(closes)
    frame = pd.DataFrame({
        "Open": closes,
        "High": closes,
        "Low": [c * 0.97 for c in closes],
        "Close": closes,
        "Volume": [1_000_000.0] * n,
        "ATR": [atr] * n,
    }, index=pd.date_range("2025-01-01", periods=n, freq="B"))
    return frame


def test_a_close_above_the_prior_high_is_refused_not_scored_negative():
    # Twenty flat bars at 1.00, then a breakout close at 1.10 -- the OFH shape.
    closes = [1.00] * 20 + [1.10]
    result = entry_signal(_frame(closes, atr=0.04), 20)
    assert result["RR"] == 0
    assert result["score"] == 0
    assert result["reasons"] == ["No Reward Above Entry"]


def test_the_refusal_says_why():
    # The defect was silence, not the number. A rejected row has to carry its
    # reason, the way "Invalid Risk" already did.
    closes = [1.00] * 20 + [1.10]
    reasons = entry_signal(_frame(closes, atr=0.04), 20)["reasons"]
    assert reasons and "Reward" in reasons[0]


def test_targets_are_still_reported_so_the_cause_is_visible():
    # Zeroing them would hide that target1 sits below the entry, which is the
    # one fact a reader needs to understand the rejection.
    closes = [1.00] * 20 + [1.10]
    result = entry_signal(_frame(closes, atr=0.04), 20)
    assert result["Target1"] < result["BuyHigh"]


def test_a_normal_setup_below_resistance_is_untouched():
    # Resistance at 1.20, price at 1.00: room to run, so the guard must not fire.
    closes = [1.20] + [1.00] * 19 + [1.00]
    result = entry_signal(_frame(closes, atr=0.05), 20)
    assert result["RR"] > 0
    assert "No Reward Above Entry" not in result["reasons"]


def test_the_guard_fires_only_on_a_non_positive_reward():
    # target2 = resistance + 2*ATR, so a large enough ATR keeps the reward
    # positive even when price has passed resistance. That case is a real
    # setup and must survive.
    closes = [1.00] * 20 + [1.05]
    generous = entry_signal(_frame(closes, atr=0.50), 20)
    assert generous["RR"] > 0
    tight = entry_signal(_frame(closes, atr=0.01), 20)
    assert tight["RR"] == 0


def test_no_buy_can_change_because_both_values_fail_the_gate():
    # The practical claim behind shipping this: RR -0.05 and RR 0 both fail
    # min_rr 3.0, so nothing that was a BUY stops being one. The guard changes
    # what a rejected row *says*, not which rows are rejected.
    import json
    from pathlib import Path

    min_rr = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "settings.json")
        .read_text(encoding="utf-8"))["strategy"]["min_rr"]
    assert min_rr > 0
    for rr in (-0.05, 0):
        assert not (min_rr <= rr)
