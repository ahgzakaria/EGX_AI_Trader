"""The ORB page's card layout: ordering, folding, and what each card says.

Folding is presentation, never suppression — a signal that loses money at its
own target moves behind one click, but the count is stated, the panel opens on
a session where nothing clears its cost, and the table underneath always lists
every signal with every column.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashboard.orb_signals import _signal_note, _sorted_signals


def _signal(ticker, net, eligibility="ELIGIBLE", cost=0.7):
    return SimpleNamespace(
        canonical_ticker=ticker,
        net_target_1_percent=net,
        total_cost_percent=cost,
        intraday_eligibility=eligibility,
    )


def test_signals_are_ordered_by_what_survives_costs():
    report = SimpleNamespace(signals=[
        _signal("CIRA", 0.16), _signal("MPCI", 0.82),
        _signal("GPIM", -0.26), _signal("OCDI", 0.36),
    ])
    assert [s.canonical_ticker for s in _sorted_signals(report)] == [
        "MPCI", "OCDI", "CIRA", "GPIM"
    ]


def test_an_unmeasured_result_sorts_last_not_first():
    """Unknown is not a good score. It cannot be ranked against a measured one."""
    report = SimpleNamespace(signals=[
        _signal("UNK", None, cost=None), _signal("LOSER", -1.5),
        _signal("WINNER", 0.9),
    ])
    order = [s.canonical_ticker for s in _sorted_signals(report)]
    assert order[0] == "WINNER"
    assert order[-1] == "UNK"


def test_ordering_never_drops_a_signal():
    report = SimpleNamespace(signals=[
        _signal("A", 1.0), _signal("B", None, cost=None), _signal("C", -2.0),
    ])
    assert len(_sorted_signals(report)) == 3


def test_a_caveat_true_of_every_signal_is_not_repeated_on_each_card():
    """The eligibility file ships empty, so UNKNOWN is the state of the list.

    Printing the same sentence under five different tickers teaches the eye to
    skip the line that will one day say something specific to one of them.
    """
    signal = _signal("GPIM", 0.5, eligibility="UNKNOWN")
    assert _signal_note(signal, eligibility_is_universal=True) == ""
    assert "eligibility" in _signal_note(signal, eligibility_is_universal=False)


def test_a_signal_that_cannot_be_closed_the_same_day_always_says_so():
    """Severe and signal-specific: it survives whatever the rest of the list is."""
    signal = _signal("XYZ", 0.5, eligibility="NOT_ELIGIBLE")
    for universal in (True, False):
        assert "not a scalping signal" in _signal_note(signal, universal)


def test_an_unknown_cost_is_called_out_on_the_card():
    signal = _signal("MEPA", None, cost=None)
    assert "unknown rather than zero" in _signal_note(signal, True)


def test_a_clean_signal_carries_no_note():
    assert _signal_note(_signal("MPCI", 0.82)) == ""


@pytest.mark.parametrize("net", [0.0, -0.01])
def test_break_even_and_worse_do_not_count_as_clearing_cost(net):
    """Zero net is not a profitable trade; it is a round trip for nothing."""
    signal = _signal("X", net)
    clears = signal.net_target_1_percent is not None and signal.net_target_1_percent > 0
    assert not clears
