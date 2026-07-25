"""Independent BREAKOUT_SWING research strategy.

Nothing in this package replaces or mutates the validated Classic strategy.
"""

from strategy_breakout.breakout_strategy import (
    BreakoutConfig,
    BreakoutSwingStrategy,
    load_breakout_config,
)

__all__ = ["BreakoutConfig", "BreakoutSwingStrategy", "load_breakout_config"]
