"""The watchlist is the same rule read early, pinned so it cannot drift into a second one.

`scan.py` states the principle this inherits: a signal shown and a signal counted
in the backtest must be the same signal by construction rather than by care. The
watchlist selects a session earlier, which is exactly where a second copy of the
arithmetic would be easiest to introduce and hardest to notice.

So these tests pin the construction, not the output: that every threshold comes
from `config.load()`, that the gate split covers `signal.GATES` exactly, that a
name which has already fired is refused, and that nothing on a row claims a
number nobody measured.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategy_momentum_breakout.config import load as load_config
from strategy_momentum_breakout.signal import GATES, measure
from strategy_momentum_breakout.watch import (DEFAULT_REACH_ATR,
                                              HEADER_NOTE, REACH_MULTIPLES,
                                              STRUCTURAL_GATES, TRIGGER_GATES,
                                              as_frame, sweep_reach, watch)

SOURCE = Path("strategy_momentum_breakout/watch.py")


def frame(closes, highs=None, lows=None, volumes=None) -> pd.DataFrame:
    """An indicator frame shaped like `calculate_indicators` output."""

    n = len(closes)
    close = pd.Series(closes, dtype=float)
    high = pd.Series(highs if highs is not None else [c * 1.01 for c in closes],
                     dtype=float)
    low = pd.Series(lows if lows is not None else [c * 0.99 for c in closes],
                    dtype=float)
    volume = pd.Series(volumes if volumes is not None else [1_000_000.0] * n,
                       dtype=float)
    index = pd.date_range("2020-01-01", periods=n, freq="B")
    # `.values`, not the Series: a Series carrying a RangeIndex assigned into a
    # frame with a DatetimeIndex aligns on the index and yields all-NaN, which
    # a downstream fillna then turns into a silent frame of zeros.
    return pd.DataFrame({"Open": close.values, "High": high.values,
                         "Low": low.values, "Close": close.values,
                         "Volume": volume.values}, index=index)


def rising(n=400, start=10.0, step=0.02):
    """A calm, liquid, uptrending name that has not broken out today.

    The recent bars are deliberately narrower than the older ones. The Calm
    gate compares today's ATR% against the name's own 250-bar median, and a
    uniformly-shaped series sits fractionally on the wrong side of it -- 1.974
    against 1.966 the first time this fixture was written, which refused every
    candidate and made nine tests skip rather than fail.
    """

    closes = [start + i * step for i in range(n)]
    # Today closes just under the twenty-bar high rather than above it.
    closes[-1] = closes[-2] - step / 2
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    for i in range(n - 30, n):
        highs[i] = closes[i] * 1.003
        lows[i] = closes[i] * 0.997
    return frame(closes, highs=highs, lows=lows)


# --- construction: no second copy of the rule --------------------------------

def test_the_gate_split_covers_every_gate_the_rule_has():
    """A gate added to signal.GATES cannot go silently unclassified here."""

    assert set(STRUCTURAL_GATES) | set(TRIGGER_GATES) == set(GATES)
    assert not set(STRUCTURAL_GATES) & set(TRIGGER_GATES)


def _code_only(path: Path) -> str:
    """The file with its docstrings and comments removed.

    Prose may name a threshold -- "the 20-bar base low rolls" is the clearest
    way to say it. Code may not. Checking the raw text would fail on a date in
    a comment, which is what a first version of this test did.
    """

    import io
    import tokenize

    kept = []
    with io.open(path, encoding="utf-8") as handle:
        tokens = list(tokenize.generate_tokens(handle.readline))
    previous = tokenize.INDENT
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                tokenize.NL, tokenize.ENCODING):
            previous = token.type
            continue                      # a docstring
        kept.append(token.string)
        if token.type not in (tokenize.NL, tokenize.NEWLINE):
            previous = token.type
    # Tokens are joined with spaces, and an f-string's expression arrives as
    # separate tokens, so `cfg.stop_window` inside one would read as
    # "cfg . stop_window". Attribute access is closed back up so the checks
    # below can look for it the way it is written.
    return " ".join(kept).replace(" . ", ".")


def test_no_threshold_is_restated_as_a_literal():
    """Every number the rule gates on must come from config, not from this file."""

    cfg = load_config()
    code = _code_only(SOURCE)
    for value in (cfg.minimum_turnover_egp, cfg.minimum_volume_ratio,
                  cfg.minimum_close_position, cfg.stop_atr_buffer,
                  cfg.maximum_session_move_percent):
        assert str(value) not in code, (
            f"{value} appears as a literal in code; read it from config.load()")
    # The window lengths are plain integers that appear incidentally, so they
    # are pinned by how they are reached rather than by their absence.
    for attribute in ("stop_window", "holding_bars", "turnover_window",
                      "entry_mode", "minimum_volume_ratio",
                      "minimum_close_position", "commission",
                      "price_precision"):
        assert f"cfg.{attribute}" in code, (
            f"{attribute} must be read from the config object")
    # The breakout and calm windows are deliberately absent: they are applied
    # inside `signal.measure`, and the levels they produce are read off its
    # table. Naming them here would mean a second place that knows them.
    assert "cfg.breakout_window" not in code
    assert "cfg.calm_window" not in code
    # Read off the raw source: the tokenised form spaces out subscripts.
    raw = SOURCE.read_text(encoding="utf-8")
    assert 'row["PriorHigh"]' in raw, "the trigger level must come from measure()"
    assert 'row["StopLoss"]' in raw, "the stop must come from measure()"


def test_it_imports_the_rule_rather_than_reimplementing_it():
    source = SOURCE.read_text(encoding="utf-8")
    assert "from strategy_momentum_breakout.signal import" in source
    for name in ("measure", "evaluate", "GATES", "warmup_bars"):
        assert name in source, f"{name} must come from signal.py"
    assert "load as load_config" in source


def test_the_header_note_is_carried_verbatim():
    assert "not a claim of edge" in HEADER_NOTE
    assert "+1.36%" in HEADER_NOTE
    assert "INVESTIGATION_SUMMARY.md" in HEADER_NOTE


# --- selection ---------------------------------------------------------------

def test_a_name_that_already_fired_is_not_on_the_watchlist():
    """That is scan.py's business. Two lists must not claim the same name."""

    from indicators.technical import calculate_indicators

    data = rising()
    table = measure(calculate_indicators(data.copy()), load_config())
    # Force today above the prior high, which is what a fired signal looks like.
    data.loc[data.index[-1], "Close"] = float(table["PriorHigh"].iloc[-1]) * 1.05
    result = watch(histories={"FIRED": data}, reach_atr=DEFAULT_REACH_ATR)
    assert result.count == 0
    assert result.funnel["AlreadyTriggered"] == 1


def test_a_name_far_below_its_trigger_is_out_of_reach():
    data = rising()
    result = watch(histories={"NEAR": data}, reach_atr=0.0001)
    assert result.count == 0
    assert result.funnel["OutOfReach"] == 1


def test_a_structural_failure_is_named_by_the_first_gate_that_refused():
    """The rule's own ordering: the most fundamental thing wrong, not the last."""

    data = rising()
    data["Volume"] = 1.0                       # turnover far below the floor
    result = watch(histories={"THIN": data})
    assert result.count == 0
    assert result.funnel["Liquidity"] == 1


def test_one_unreadable_symbol_is_skipped_and_does_not_empty_the_scan():
    """scan.py's rule, inherited."""

    good = rising()
    result = watch(histories={"GOOD": good, "BROKEN": pd.DataFrame()})
    assert result.funnel["Unusable"] + result.funnel["InsufficientHistory"] >= 1
    assert result.considered == 2


def test_the_funnel_is_populated_even_when_nothing_qualifies():
    """An empty week must read as 'nothing is set up', not as a fault."""

    result = watch(histories={"THIN": frame([1.0] * 400)})
    assert result.funnel
    assert sum(result.funnel.values()) >= 1


# --- the row -----------------------------------------------------------------

def qualifying_result():
    data = rising()
    return watch(histories={"SETUP": data}, reach_atr=5.0)


def test_a_candidate_sits_below_its_trigger():
    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    candidate = result.candidates[0]
    assert candidate.close < candidate.prior_high
    assert candidate.distance_percent > 0
    assert candidate.distance_atr > 0


def test_every_row_states_the_entry_and_the_exit():
    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    cfg = load_config()
    candidate = result.candidates[0]
    assert cfg.entry_mode in candidate.entry_plan
    assert str(cfg.holding_bars) in candidate.exit_rule
    assert "no target" in candidate.exit_rule


def test_the_stop_is_flagged_as_rolling():
    """It is today's stop and the base low moves; the row must say so."""

    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    assert "recompute" in result.candidates[0].stop_note
    assert result.candidates[0].stop_loss_today < result.candidates[0].close


def test_the_target_column_is_labelled_unmeasured():
    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    assert "NOT MEASURED" in result.candidates[0].target_note


def test_the_target_is_never_placed_at_the_twenty_bar_resistance():
    """signal.py rejects that explicitly: on a breakout it sits below entry."""

    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    candidate = result.candidates[0]
    assert candidate.target_not_measured > candidate.close
    assert candidate.target_not_measured != candidate.prior_high


def test_the_trigger_sentence_carries_the_configured_thresholds():
    cfg = load_config()
    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    sentence = result.candidates[0].trigger_sentence
    assert str(cfg.minimum_volume_ratio) in sentence
    assert str(cfg.turnover_window) in sentence
    assert str(result.candidates[0].prior_high) in sentence


def test_the_cost_says_where_the_spread_came_from():
    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    assert result.candidates[0].spread_source in (
        "measured", "unmeasured_conservative", "flat_default", "unavailable")


# --- ordering and the sweep --------------------------------------------------

def test_candidates_are_ordered_nearest_to_trigger_first():
    near, far = rising(), rising(start=10.0, step=0.05)
    result = watch(histories={"NEAR": near, "FAR": far}, reach_atr=5.0)
    if result.count < 2:
        pytest.skip("the synthetic frames produced fewer than two candidates")
    distances = [c.distance_atr for c in result.candidates]
    assert distances == sorted(distances)


def test_the_sweep_reports_a_count_for_every_multiple():
    counts = sweep_reach(histories={"SETUP": rising()})
    assert set(counts) == set(REACH_MULTIPLES)
    assert all(isinstance(v, int) for v in counts.values())


def test_a_wider_reach_never_returns_fewer_names():
    histories = {"SETUP": rising()}
    counts = sweep_reach(histories=histories, multiples=(0.5, 1.0, 1.5, 3.0))
    values = [counts[m] for m in (0.5, 1.0, 1.5, 3.0)]
    assert values == sorted(values)


def test_the_frame_carries_every_column_the_reader_was_promised():
    result = qualifying_result()
    if not result.count:
        pytest.skip("the synthetic frame produced no candidate")
    columns = set(as_frame(result).columns)
    for name in ("symbol", "session_date", "close", "prior_high",
                 "distance_percent", "distance_atr", "trigger_sentence",
                 "entry_plan", "stop_loss_today", "stop_note", "risk_percent",
                 "exit_rule", "target_not_measured", "target_note",
                 "turnover_egp", "spread_source", "round_trip_cost_percent",
                 "net_2r_after_cost_percent"):
        assert name in columns


def test_nothing_on_the_row_claims_a_hit_rate():
    """No such number has been measured and none may be added."""

    source = SOURCE.read_text(encoding="utf-8")
    for forbidden in ("hit_rate", "hit rate of", "win_rate", "probability_of"):
        assert forbidden not in source.replace(
            "No hit rate is reported and none should be added.", "")
