"""Phase 10 isolation, chronology, geometry, and portfolio tests."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.config import load as load_backtest_config
from core.scanner import _safe_breakout_evaluation
from indicators.technical import calculate_indicators
from strategy_breakout.breakout_backtest import BreakoutBacktester
from strategy_breakout.breakout_entry import STOP_MODELS, select_stop
from strategy_breakout.breakout_exit import simulate_exit
from strategy_breakout.breakout_strategy import BreakoutSwingStrategy, load_breakout_config
from strategy_breakout.breakout_targets import TARGET_MODELS, select_targets


ROOT = Path(__file__).resolve().parents[1]


def breakout_frame(rows=270):
    index = pd.bdate_range("2024-01-01", periods=rows)
    close = np.linspace(90.0, 108.0, rows)
    close[-25:-1] = np.linspace(105.0, 108.0, 24)
    close[-1] = 115.0
    frame = pd.DataFrame({
        "Open": close * 0.997,
        "High": close * 1.006,
        "Low": close * 0.994,
        "Close": close,
        "Adj Close": close,
        "Volume": np.full(rows, 1_000_000.0),
    }, index=index)
    frame.loc[index[-1], "Open"] = 109.0
    frame.loc[index[-1], "High"] = 116.5
    frame.loc[index[-1], "Low"] = 108.5
    frame.loc[index[-1], "Volume"] = 3_000_000.0
    return calculate_indicators(frame)


def test_breakout_is_disabled_by_default_and_decision_support_only():
    config = load_breakout_config()
    assert config.enabled_by_default is False
    assert config.decision_support_only is True
    assert BreakoutSwingStrategy(config).evaluate(breakout_frame())["DecisionSupportOnly"] is True


def test_breakout_geometry_is_independent_and_produces_configured_rr():
    result = BreakoutSwingStrategy().evaluate(breakout_frame())
    assert result["Signal"] == "BUY"
    assert result["Entry"] > result["StopLoss"]
    assert result["Target2"] > result["Entry"]
    expected = (result["Target2"] - result["Entry"]) / (result["Entry"] - result["StopLoss"])
    assert result["RR"] == round(expected, 3)
    assert result["Strategy"] == "BREAKOUT_SWING"


def test_every_stop_model_is_independently_selectable():
    frame = breakout_frame()
    base = load_breakout_config()
    entry = BreakoutSwingStrategy(base).evaluate(frame)
    entry_geometry = {
        "entry_price": entry["Entry"],
        "breakout_low": float(frame["Low"].iloc[-1]),
        "retest_low": float(frame["Low"].iloc[-2]),
    }
    observed = {}
    for model in STOP_MODELS:
        selected = select_stop(
            frame, len(frame) - 1, entry_geometry, replace(base, stop_model=model)
        )
        assert set(selected["candidates"]) == set(STOP_MODELS)
        assert selected["model"] == model
        assert selected["stop"] == selected["candidates"][model]
        assert selected["valid"] is True
        observed[model] = selected["stop"]
    assert len(set(observed.values())) >= 4


def test_every_target_model_is_independently_selectable():
    frame = breakout_frame()
    base = load_breakout_config()
    decision = BreakoutSwingStrategy(base).evaluate(frame)
    entry = {
        "entry_price": decision["Entry"],
        "breakout_level": decision["BreakoutLevel"],
    }
    for model in TARGET_MODELS:
        selected = select_targets(
            frame,
            len(frame) - 1,
            entry,
            decision["StopLoss"],
            replace(base, target_model=model),
        )
        assert set(selected["candidates"]) == set(TARGET_MODELS)
        assert selected["model"] == model
        assert selected["target1"] > decision["Entry"]
        assert selected["target2"] > decision["Entry"]
        assert selected["rr"] > 0


def test_evaluation_never_reads_future_rows():
    frame = breakout_frame(285)
    index = 269
    full = BreakoutSwingStrategy().evaluate(frame, index)
    truncated = BreakoutSwingStrategy().evaluate(frame.iloc[: index + 1].copy())
    fields = (
        "Signal", "Score", "Confidence", "RR", "Entry", "StopLoss",
        "Target1", "Target2", "BreakoutLevel", "DecisionTrace",
    )
    assert {field: full[field] for field in fields} == {
        field: truncated[field] for field in fields
    }


def test_same_bar_ambiguity_is_conservative_stop_first():
    frame = breakout_frame()
    entry_index = len(frame) - 3
    ambiguous_index = entry_index + 1
    frame.loc[frame.index[ambiguous_index], ["Low", "High", "Close"]] = [95.0, 125.0, 110.0]
    config = load_breakout_config()
    result = simulate_exit(
        frame, entry_index - 1, entry_index, 110.0, 100.0, 120.0, 125.0,
        108.0, config,
    )
    assert result.exit_reason == "STOP_LOSS"
    assert result.target1_hit is False


def test_backtester_tags_new_strategy_without_ai_or_broker_execution():
    frame = breakout_frame(320)
    result = BreakoutBacktester(
        load_breakout_config(), load_backtest_config()
    ).run({"TEST.CA": frame})
    assert result["mode"] == "BREAKOUT_SWING"
    assert all(trade.ai_mode == "BREAKOUT_SWING" for trade in result["candidates"])
    assert all(not hasattr(trade, "broker_order_id") for trade in result["candidates"])


def test_frozen_engine_files_match_release_candidate():
    frozen = (
        "strategy/config.py",
        "strategy/filter.py",
        "strategy/decision_engine.py",
        "strategy/market_analyzer.py",
        "strategy/market_regime.py",
        "strategy/quality_filter.py",
        "strategy/trading_decision.py",
        "indicators/technical.py",
        "backtesting/engine.py",
        "portfolio/portfolio_simulator.py",
    )
    manifest = json.loads(
        (ROOT / "strategy_selector" / "frozen_strategy_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for relative in frozen:
        # The release snapshot is archived outside the active repository. The normalized
        # manifest preserves the same semantic baseline without a duplicate source tree.
        data = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n").replace(
            b"\r", b"\n"
        )
        assert hashlib.sha256(data).hexdigest() == manifest[relative], relative


def test_breakout_package_does_not_import_classic_strategy():
    for path in (ROOT / "strategy_breakout").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from strategy." not in source
        assert "import strategy." not in source


def test_optional_breakout_failure_cannot_remove_classic_scan_row():
    class BrokenBreakout:
        def evaluate(self, frame, index):
            raise RuntimeError("research failure")

    result = _safe_breakout_evaluation(
        BrokenBreakout(), breakout_frame(), 269, "TEST.CA"
    )
    assert result["Signal"] == "UNAVAILABLE"
    assert result["DecisionSupportOnly"] is True
