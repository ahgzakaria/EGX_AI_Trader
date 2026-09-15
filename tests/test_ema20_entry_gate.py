"""The EMA20 entry gate study: its thresholds, fixed before the result was read.

`scripts/research/ema20_entry_gate.py` pre-registers `ema20_dist <= 0` as the
one gate that was not fitted, and labels `<= -1` as fitted on the early era.
These pin both, and the gate's handling of a trade whose distance was never
recorded -- so neither the thresholds nor the population can be adjusted after
a disappointing number.
"""

from __future__ import annotations

from types import SimpleNamespace

from scripts.research import ema20_entry_gate as study


def trades(*distances):
    return [SimpleNamespace(ema20_dist=d) for d in distances]


def test_no_gate_keeps_everything_including_unmeasured():
    assert len(study.apply_gate(trades(-3, 0, 2, None), None)) == 4


def test_the_neutral_gate_keeps_at_or_below_zero():
    kept = study.apply_gate(trades(-3.0, -0.01, 0.0, 0.01, 5.0), 0.0)
    assert [t.ema20_dist for t in kept] == [-3.0, -0.01, 0.0]


def test_an_entry_with_no_recorded_distance_is_refused_not_admitted():
    kept = study.apply_gate(trades(None, float("nan"), -1.0), 0.0)
    assert [t.ema20_dist for t in kept] == [-1.0]


def test_the_fitted_gate_is_stricter():
    kept = study.apply_gate(trades(-2.0, -1.0, -0.5, 0.0), -1.0)
    assert [t.ema20_dist for t in kept] == [-2.0, -1.0]


def test_the_gates_were_registered_before_the_result():
    registered = {label: (threshold, note) for label, threshold, note in study.GATES}
    assert registered["no gate (as shipped)"][0] is None
    assert registered["ema20_dist <= 0"] == (0.0, "pre-registered, not fitted")
    threshold, note = registered["ema20_dist <= -1"]
    assert threshold == -1.0 and "fitted" in note and "only >=2023" in note
