from config.settings_manager import settings

# ==================================
# Backtest Settings
# ==================================

backtest = settings.get("backtest")

# ==================================
# ENTRY
# ==================================

ENTRY_WAIT_DAYS = backtest["entry_wait_days"]

# ==================================
# EXIT
# ==================================

EXIT_MODE = backtest["exit_mode"]

MAX_HOLDING_DAYS = backtest["max_holding_days"]

MOVE_TO_BREAKEVEN = backtest["move_to_breakeven"]

PARTIAL_EXIT = backtest["partial_exit"]

PARTIAL_PERCENT = backtest["partial_percent"]

# ==================================
# RISK
# ==================================

RISK_MODE = backtest["risk_mode"]

RISK_PERCENT = backtest["risk_percent"]

# ==================================
# TRAILING STOP
# ==================================

TRAILING_MODE = backtest["trailing_mode"]

TRAILING_ATR = backtest["trailing_atr"]

# ==================================
# BACKTEST
# ==================================

ALLOW_OVERLAPPING_TRADES = backtest["allow_overlapping_trades"]

INITIAL_CAPITAL = backtest["initial_capital"]

# ==================================
# COSTS
# ==================================

COMMISSION = backtest["commission"]

SLIPPAGE = backtest["slippage"]