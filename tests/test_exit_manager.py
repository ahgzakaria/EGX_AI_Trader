from types import SimpleNamespace
import unittest

from backtesting.context import BacktestContext
from backtesting.costs import TradingCosts
from backtesting.managers.exit_manager import ExitManager


class ExitManagerTests(unittest.TestCase):

 def test_stop_loss_wins_when_daily_bar_hits_target_and_stop(self):
    """OHLC data cannot order intraday moves, so the simulator is conservative."""
    data = SimpleNamespace(
        high=[110.0],
        low=[90.0],
        close=[100.0],
        ema20=[100.0],
        atr=[1.0],
        index=[],
        length=1,
    )
    data.index = [SimpleNamespace(date=lambda: __import__("datetime").date(2026, 1, 2))]

    context = BacktestContext(
        symbol="TEST",
        data=data,
        signal_index=0,
        signal={"StopLoss": 95.0, "Target1": 105.0, "Target2": 110.0},
        entry_price=100.0,
        entry_date="2026-01-01",
        entry_index=0,
    )

    manager = ExitManager(TradingCosts(commission=0, slippage=0, spread_percent=0))
    closed = manager.manage(context)

    self.assertTrue(closed)
    self.assertEqual(context.exit_reason, "StopLoss")
    self.assertEqual(context.result, "LOSS")
    self.assertEqual(context.exit_price, 95.0)


 def test_live_trade_does_not_timeout_before_holding_window_ends(self):
    data = SimpleNamespace(
        high=[101.0],
        low=[99.0],
        close=[100.0],
        ema20=[100.0],
        atr=[1.0],
        index=[],
        length=1,
    )
    data.index = [SimpleNamespace(date=lambda: __import__("datetime").date(2026, 1, 2))]

    context = BacktestContext(
        symbol="TEST",
        data=data,
        signal_index=0,
        signal={"StopLoss": 90.0, "Target1": 110.0, "Target2": 120.0},
        entry_price=100.0,
        entry_date="2026-01-01",
        entry_index=0,
    )

    closed = ExitManager(TradingCosts(commission=0, slippage=0, spread_percent=0)).manage(
        context,
        allow_timeout=False,
    )

    self.assertFalse(closed)
    self.assertIsNone(context.exit_price)

 def test_completed_holding_window_closes_at_timeout(self):
    data = SimpleNamespace(
        high=[101.0] * 20,
        low=[99.0] * 20,
        close=[102.0] * 20,
        ema20=[90.0] * 20,
        atr=[1.0] * 20,
        index=[],
        length=20,
    )
    data.index = [
        SimpleNamespace(date=lambda day=day: __import__("datetime").date(2026, 1, day))
        for day in range(1, 21)
    ]
    context = BacktestContext(
        symbol="TEST", data=data, signal_index=0,
        signal={"StopLoss": 90.0, "Target1": 110.0, "Target2": 120.0},
        entry_price=100.0, entry_date="2026-01-01", entry_index=0,
    )

    closed = ExitManager(TradingCosts(commission=0, slippage=0, spread_percent=0)).manage(
        context, allow_timeout=False
    )

    self.assertTrue(closed)
    self.assertEqual(context.exit_reason, "Timeout")
    self.assertEqual(context.exit_price, 102.0)
