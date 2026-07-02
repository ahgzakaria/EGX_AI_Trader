# ==================================
# ENTRY
# ==================================

ENTRY_WAIT_DAYS = 5

# ==================================
# EXIT
# ==================================

EXIT_MODE = "TARGET1"

# TARGET1
# TARGET2
# TRAILING
# PARTIAL

MAX_HOLDING_DAYS = 20

MOVE_TO_BREAKEVEN = False

PARTIAL_EXIT = False

PARTIAL_PERCENT = 0.50

# ==================================
# RISK
# ==================================

RISK_MODE = "FIXED"

# FIXED
# PERCENT
# ATR

RISK_PERCENT = 2

# ==================================
# TRAILING STOP
# ==================================

TRAILING_MODE = "EMA20"

# EMA20
# ATR
# LOW5

TRAILING_ATR = 2

# ==================================
# BACKTEST
# ==================================

ALLOW_OVERLAPPING_TRADES = False

INITIAL_CAPITAL = 100000

# ==================================
# COSTS
# ==================================

COMMISSION = 0.003

SLIPPAGE = 0.0005