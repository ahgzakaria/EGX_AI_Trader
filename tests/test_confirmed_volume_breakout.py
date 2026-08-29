"""What CONFIRMED_VOLUME_BREAKOUT must keep doing.

The defects this strategy was built to avoid all reached production in the
strategy beside it, and every one of them was invisible from a summary: a target
placed below the entry, a scoring component that could never fire, a gate whose
data source has one bar, a stop hunted by wicks, and a largest-winner that was a
corporate action. So the tests here are not about the numbers being good. They
are about the specific shapes of wrongness that this project has actually
suffered being impossible to reintroduce silently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.costs import TradingCosts
from strategy_momentum_breakout.backtest import MomentumBreakoutBacktest
from strategy_momentum_breakout.config import BreakoutConfig
from strategy_momentum_breakout.signal import GATES, evaluate, measure, warmup_bars


def frame(closes, highs=None, lows=None, volumes=None, atr=1.0, ema200=None):
    """An indicator frame shaped like `calculate_indicators` output.

    `Open` is filled with the previous close deliberately -- that is what this
    project's data actually contains, and a strategy that reads it would pass
    here and fail in the market.
    """
    n = len(closes)
    closes = [float(c) for c in closes]
    highs = [float(h) for h in (highs if highs is not None else closes)]
    lows = [float(x) for x in
            (lows if lows is not None else [c * 0.97 for c in closes])]
    volumes = [float(v) for v in
               (volumes if volumes is not None else [1_000_000.0] * n)]
    opens = [closes[0]] + closes[:-1]
    data = {
        "Open": opens, "High": highs, "Low": lows, "Close": closes,
        "Volume": volumes,
        "ATR": [atr] * n,
        "EMA200": ema200 if ema200 is not None else [min(closes) * 0.5] * n,
    }
    # Columns the rule never reads but `Trade` records for the AI dataset. They
    # are filled rather than omitted so that a future reader of `_record` cannot
    # accidentally start depending on one and have the tests still pass.
    for column in ("RSI", "RSI7", "ADX", "MACD", "EMA20_DIST", "EMA50_DIST",
                   "EMA200_DIST", "VOLUME_RATIO", "ATR_PERCENT", "BB_POSITION",
                   "OBV", "EMA20_SLOPE", "EMA50_SLOPE", "RSI_SLOPE",
                   "ADX_RISING", "BB_WIDTH", "OBV_SLOPE", "DIST_HIGH20",
                   "DIST_LOW20", "MACD_CROSS_AGE"):
        data[column] = [0.0] * n
    return pd.DataFrame(
        data, index=pd.date_range("2016-01-01", periods=n, freq="B"))


def free():
    """Costs of zero, so a fill assertion is about the price and not the toll.

    `TradingCosts` resolves the spread once, at construction, from the measured
    table or the conservative fallback -- so it has to be asked for zero rather
    than patched afterwards.
    """
    return TradingCosts(commission=0.0, slippage=0.0, spread_percent=0.0)


def base_config(**overrides):
    """A config with short windows, so a test frame need not be 251 bars long."""
    defaults = dict(calm_window=10, breakout_window=5, turnover_window=5,
                    stop_window=5, holding_bars=5, minimum_turnover_egp=0.0,
                    trend_warmup_bars=20)
    return BreakoutConfig(**{**defaults, **overrides})


def flat_then(spike_close, spike_high, spike_low, spike_volume, n=40,
              level=10.0, volume=1_000_000.0):
    """`n` quiet bars in a narrow band, then one bar the test controls."""
    closes = [level] * n + [spike_close]
    highs = [level * 1.005] * n + [spike_high]
    lows = [level * 0.995] * n + [spike_low]
    volumes = [volume] * n + [spike_volume]
    return closes, highs, lows, volumes


# ---------------------------------------------------------------------------
# The rule fires when, and only when, all of it is true
# ---------------------------------------------------------------------------

def test_a_confirmed_breakout_is_a_buy():
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["Signal"] == "BUY"
    assert decision["RejectReason"] is None
    assert all(decision["Checks"].values())


@pytest.mark.parametrize("gate,kwargs", [
    # Closes below the prior high: no breakout.
    ("Breakout", dict(spike_close=10.0, spike_high=10.0)),
    # Breaks out on ordinary volume.
    ("VolumeConfirmation", dict(spike_volume=1_100_000.0)),
    # Breaks out, then gives most of the day back: closes low in its range.
    ("ClosePosition", dict(spike_low=10.6, spike_high=12.0, spike_close=10.75)),
])
def test_each_gate_can_refuse_on_its_own(gate, kwargs):
    cfg = base_config()
    spec = dict(spike_close=11.0, spike_high=11.0, spike_low=10.6,
                spike_volume=6_000_000.0)
    spec.update(kwargs)
    closes, highs, lows, volumes = flat_then(**spec)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["Signal"] == "AVOID"
    assert decision["Checks"][gate] is False


def test_a_name_below_its_own_ema200_is_refused():
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    data = frame(closes, highs, lows, volumes, ema200=[20.0] * len(closes))
    decision = evaluate(data, len(closes) - 1, cfg)

    assert decision["Signal"] == "AVOID"
    assert decision["RejectReason"] == "LongTermTrend"


def test_an_illiquid_name_is_refused_however_good_the_breakout():
    cfg = base_config(minimum_turnover_egp=50_000_000)
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["RejectReason"] == "Liquidity"


# ---------------------------------------------------------------------------
# The defects it exists to not have
# ---------------------------------------------------------------------------

def test_today_cannot_break_out_of_a_high_that_includes_itself():
    """The level is the previous window's high, closed before this bar.

    `strategy/support.py` and `strategy/entry.py` disagree on exactly this, and
    the two definitions differ on 22.4% of real bars -- one places the targets,
    the other measures the room the quality filter demands.
    """
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    table = measure(frame(closes, highs, lows, volumes), cfg)
    last = len(closes) - 1

    # The prior high is the flat band's high, not this bar's 11.0.
    assert table["PriorHigh"].iloc[last] == pytest.approx(10.05)
    assert table["PriorHigh"].iloc[last] < closes[last]


def test_the_rule_never_reads_the_open():
    """`Open` is carried forward from the previous close on 96-98% of bars.

    Corrupting it must change nothing, because nothing may depend on it.
    """
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    data = frame(closes, highs, lows, volumes)
    honest = measure(data, cfg)

    corrupted = data.copy()
    corrupted["Open"] = 999.0
    assert measure(corrupted, cfg).equals(honest)


def test_no_target_is_invented():
    """The neighbouring strategy reports a target below the entry on a breakout.

    This one exits on time or on the stop, so it has no target to misplace and
    must not grow one.
    """
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert "Target1" not in decision
    assert "Target2" not in decision
    assert "RR" not in decision


def test_the_stop_sits_below_the_close_or_the_signal_is_refused():
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["StopLoss"] < decision["Measurements"]["Close"]
    assert decision["ReferenceRiskPercent"] > 0


def test_a_rejection_says_which_number_failed():
    """`RR -0.05` reached the dashboard with nothing on the row to explain it."""
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 1_100_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    volume_line = next(line for line in decision["Reasons"] if "Volume:" in line)
    measured = decision["Measurements"]["VolumeRatio"]
    assert volume_line.startswith("FAIL")
    assert measured < cfg.minimum_volume_ratio
    assert f"{measured}x" in volume_line
    assert f"{cfg.minimum_volume_ratio}x" in volume_line


def test_the_first_failing_gate_is_the_reason_not_the_last():
    cfg = base_config(minimum_turnover_egp=50_000_000)
    # Illiquid *and* no breakout: the fundamental problem is reported.
    closes, highs, lows, volumes = flat_then(10.0, 10.0, 9.9, 1_000_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["RejectReason"] == "Liquidity"
    assert GATES.index("Liquidity") < GATES.index("Breakout")


def test_bars_before_the_warmup_are_refused_rather_than_guessed():
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    decision = evaluate(frame(closes, highs, lows, volumes), 3, cfg)

    assert decision["RejectReason"] == "InsufficientHistory"
    assert warmup_bars(cfg) > 3


# ---------------------------------------------------------------------------
# Prices this market cannot produce
# ---------------------------------------------------------------------------

def test_a_session_beyond_the_price_limit_blocks_a_signal():
    """EGX's daily limit is ±10% on most listings. A 50% session is a split."""
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    # Halve everything from bar 38 on, the way an unadjusted two-for-one
    # arrives, so it sits inside the windows the rule reads at bar 40.
    for series in (closes, highs, lows):
        for k in range(38, len(series)):
            series[k] /= 2
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["Signal"] == "AVOID"
    assert decision["RejectReason"] == "PriceIntegrity"


def test_the_price_limit_guard_is_symmetric():
    """The untreated record's largest gain was a +394.9% session, not a trade."""
    cfg = base_config()
    closes, highs, lows, volumes = flat_then(11.0, 11.0, 10.6, 6_000_000.0)
    for series in (closes, highs, lows):
        for k in range(38, len(series)):
            series[k] *= 5
    decision = evaluate(frame(closes, highs, lows, volumes), len(closes) - 1, cfg)

    assert decision["RejectReason"] == "PriceIntegrity"


def test_a_split_during_a_hold_is_not_booked_as_a_stop_out():
    """A holder of a split stock is adjusted by their broker, not halved."""
    cfg = base_config()
    # Quiet, one clean breakout, then a collapse two bars into the hold.
    closes = [10.0] * 40 + [11.0, 11.2, 5.5, 5.4, 5.4, 5.4, 5.4, 5.4]
    highs = [10.05] * 40 + [11.0, 11.3, 5.6, 5.5, 5.5, 5.5, 5.5, 5.5]
    lows = [9.95] * 40 + [10.6, 11.0, 5.4, 5.3, 5.3, 5.3, 5.3, 5.3]
    volumes = [1_000_000.0] * 40 + [6_000_000.0] + [1_000_000.0] * 7
    data = frame(closes, highs, lows, volumes)

    engine = MomentumBreakoutBacktest("TEST.CA", cfg, costs=free())
    trades = engine.run(df=data)

    assert len(trades) == 1
    assert trades[0].exit_reason == "CorporateAction"
    # Closed at the last clean price, which is a small gain, not a halving.
    assert trades[0].exit_price == pytest.approx(11.2)


# ---------------------------------------------------------------------------
# The two readings of the rule are one reading
# ---------------------------------------------------------------------------

def test_the_per_bar_and_whole_frame_paths_agree_everywhere():
    """`measure` is the strategy; `evaluate` reads a row of it.

    The backtest walks the table and the dashboard calls `evaluate`. If those
    ever became two implementations, live and backtest would quietly disagree --
    which is the failure mode `backtest.py` in the project root exists to
    prevent for the other strategy.
    """
    rng = np.random.default_rng(20260829)
    n = 400
    closes = list(10 * np.cumprod(1 + rng.normal(0.0004, 0.02, n)))
    highs = [c * (1 + abs(rng.normal(0, 0.01))) for c in closes]
    lows = [c * (1 - abs(rng.normal(0, 0.01))) for c in closes]
    volumes = list(rng.lognormal(13, 0.8, n))
    cfg = BreakoutConfig(minimum_turnover_egp=0.0)
    data = frame(closes, highs, lows, volumes)

    table = measure(data, cfg)
    for i in range(warmup_bars(cfg), n):
        row_says = evaluate(data, i, cfg, measured=table)["Signal"] == "BUY"
        assert row_says == bool(table["Passed"].iloc[i]), f"bar {i}"


def test_a_trade_is_entered_at_the_next_close_never_at_the_signal_close():
    """The signal is a close. You cannot buy at a price you have only just seen."""
    cfg = base_config()
    closes = [10.0] * 40 + [11.0, 12.5] + [12.5] * 8
    highs = [10.05] * 40 + [11.0, 12.6] + [12.6] * 8
    lows = [9.95] * 40 + [10.6, 12.4] + [12.4] * 8
    volumes = [1_000_000.0] * 40 + [6_000_000.0] + [1_000_000.0] * 9
    data = frame(closes, highs, lows, volumes)

    engine = MomentumBreakoutBacktest("TEST.CA", cfg, costs=free())
    trades = engine.run(df=data)

    assert len(trades) == 1
    # 12.5, the bar after the signal -- not 11.0, the signal's own close.
    assert trades[0].entry_price == pytest.approx(12.5)
    assert trades[0].signal_date < trades[0].entry_date


def test_a_position_is_held_no_longer_than_the_cap():
    cfg = base_config(holding_bars=5)
    closes = [10.0] * 40 + [11.0] + [11.0] * 20
    highs = [10.05] * 40 + [11.0] + [11.0] * 20
    lows = [9.95] * 40 + [10.6] + [10.9] * 20
    volumes = [1_000_000.0] * 40 + [6_000_000.0] + [1_000_000.0] * 20
    data = frame(closes, highs, lows, volumes)

    engine = MomentumBreakoutBacktest("TEST.CA", cfg)
    trades = engine.run(df=data)

    assert len(trades) == 1
    assert trades[0].exit_reason == "HoldingCap"
    bars_held = data.index.get_loc(pd.Timestamp(trades[0].exit_date)) - \
        data.index.get_loc(pd.Timestamp(trades[0].entry_date))
    assert bars_held == cfg.holding_bars


def test_one_position_per_symbol_at_a_time():
    """A second signal inside an open position must not open a second position."""
    cfg = base_config(holding_bars=10)
    closes = [10.0] * 40 + [11.0, 11.5, 12.0, 12.5, 13.0] + [13.0] * 20
    highs = [10.05] * 40 + [11.0, 11.5, 12.0, 12.5, 13.0] + [13.0] * 20
    lows = [9.95] * 40 + [10.6, 11.2, 11.8, 12.2, 12.8] + [12.9] * 20
    volumes = [1_000_000.0] * 40 + [6_000_000.0] * 5 + [1_000_000.0] * 20
    data = frame(closes, highs, lows, volumes)

    engine = MomentumBreakoutBacktest("TEST.CA", cfg)
    trades = engine.run(df=data)

    windows = [(t.entry_date, t.exit_date) for t in trades]
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[1] < later[0], f"{earlier} overlaps {later}"


def test_a_trade_the_data_cannot_see_the_end_of_is_not_counted():
    """No signal is taken so late that its holding period runs off the frame."""
    cfg = base_config(holding_bars=10)
    closes = [10.0] * 40 + [11.0, 11.0, 11.0]
    highs = [10.05] * 40 + [11.0, 11.0, 11.0]
    lows = [9.95] * 40 + [10.6, 10.9, 10.9]
    volumes = [1_000_000.0] * 40 + [6_000_000.0, 1_000_000.0, 1_000_000.0]

    engine = MomentumBreakoutBacktest("TEST.CA", cfg)
    assert engine.run(df=frame(closes, highs, lows, volumes)) == []


# ---------------------------------------------------------------------------
# The portfolio limits, which are what the result is actually made of
# ---------------------------------------------------------------------------

def test_the_shipped_portfolio_limits_are_the_measured_ones():
    """Pinned to the values, not to agreement between two files.

    `settings.json` is meant to be edited, so a test demanding it match the
    dataclass would fail on the first legitimate tweak. What is pinned is the
    measured recommendation from CAPACITY_IS_THE_CONSTRAINT.md, so that a clean
    checkout behaves the way the run behaved.
    """
    cfg = BreakoutConfig()
    assert cfg.risk_percent == 1.0
    assert cfg.max_portfolio_risk_percent == 15.0
    assert cfg.max_open_positions == 15


def test_neither_position_limit_silently_overrides_the_other():
    """The defect this change was found through, made impossible to reintroduce.

    `PortfolioSimulator` holds `min(max_open_positions, floor(heat / risk))`.
    The previous settings were 2% / 10% / 10, which is a cap of **five** — so
    the file said ten, the simulator did five, and nothing anywhere reported the
    difference. Whatever the numbers become, the two terms must agree, or the
    one that loses is a number nobody is reading.
    """
    cfg = BreakoutConfig()
    by_heat = int(cfg.max_portfolio_risk_percent // cfg.risk_percent)
    assert by_heat == cfg.max_open_positions, (
        f"heat/risk allows {by_heat} positions but max_open_positions is "
        f"{cfg.max_open_positions}; the smaller one is the real cap and the "
        f"larger one is decoration"
    )


def test_the_simulator_agrees_with_what_the_config_claims():
    """Read the cap off the simulator, not off the settings file."""
    from portfolio.portfolio_simulator import PortfolioSimulator

    cfg = BreakoutConfig()
    simulator = PortfolioSimulator(
        [],
        initial_capital=cfg.initial_capital,
        risk_percent=cfg.risk_percent,
        allow_overlapping_trades=cfg.allow_overlapping_trades,
        max_open_positions=cfg.max_open_positions,
        max_portfolio_risk_percent=cfg.max_portfolio_risk_percent,
    )
    assert simulator.effective_max_positions == cfg.max_open_positions == 15
