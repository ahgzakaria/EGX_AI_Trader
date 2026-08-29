"""CONFIRMED_VOLUME_BREAKOUT: a second swing strategy, built from measurement.

See `strategy_momentum_breakout/signal.py` for the rule and
`docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md` for how each threshold was
chosen and what was rejected on the way.
"""

from strategy_momentum_breakout.config import BreakoutConfig, load as load_config
from strategy_momentum_breakout.signal import evaluate

__all__ = ["BreakoutConfig", "load_config", "evaluate"]
