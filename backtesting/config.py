from types import SimpleNamespace

from config.settings_manager import settings


def load():

    # ==================================
    # بنقرا settings.json بشكل Live كل مرة الدالة دي بتتنادى،
    # بدل ما القيم تتجمّد وقت أول Import للملف ده. من غير كده،
    # أي تعديل من شاشة Settings مش هيتطبق إلا لو السيرفر
    # اتقفل واتفتح تاني.
    # ==================================

    backtest = settings.get("backtest")

    return SimpleNamespace(

        # ENTRY
        ENTRY_WAIT_DAYS=backtest["entry_wait_days"],
        AI_MODE=backtest.get("ai_mode", "STRATEGY_ONLY"),
        WALK_FORWARD_SPLITS=backtest.get("walk_forward_splits", 5),

        # EXIT
        EXIT_MODE=backtest["exit_mode"],
        MAX_HOLDING_DAYS=backtest["max_holding_days"],
        MOVE_TO_BREAKEVEN=backtest["move_to_breakeven"],
        PARTIAL_EXIT=backtest["partial_exit"],
        PARTIAL_PERCENT=backtest["partial_percent"],

        # RISK
        RISK_MODE=backtest["risk_mode"],
        RISK_PERCENT=backtest["risk_percent"],

        # TRAILING STOP
        TRAILING_MODE=backtest["trailing_mode"],
        TRAILING_ATR=backtest["trailing_atr"],
        TRAILING_ENABLED=backtest.get("trailing_enabled", False),

        # BACKTEST
        ALLOW_OVERLAPPING_TRADES=backtest["allow_overlapping_trades"],
        INITIAL_CAPITAL=backtest["initial_capital"],

        # PORTFOLIO HEAT
        MAX_OPEN_POSITIONS=backtest.get(
            "max_open_positions",
            10
        ),
        MAX_PORTFOLIO_RISK_PERCENT=backtest.get(
            "max_portfolio_risk_percent",
            10
        ),

        # COSTS
        COMMISSION=backtest["commission"],
        SLIPPAGE=backtest["slippage"]

    )
