from copy import deepcopy
from types import SimpleNamespace
import unittest

import pandas as pd

from services.ranking_robustness import _bootstrap_rows, _reprice_execution_path


def trade(profit, portfolio_profit):
    return SimpleNamespace(
        entry_price=100.05,
        exit_price=110.0,
        stop_loss=95.0,
        profit=profit,
        profit_percent=profit / 100 * 100,
        risk_per_share=5.05,
        reward_per_share=9.95,
        r_multiple=1.97,
        portfolio_profit=portfolio_profit,
    )


class RankingRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(
            COMMISSION=0.003,
            SLIPPAGE=0.0005,
            INITIAL_CAPITAL=100000,
        )

    def test_higher_cost_or_slippage_never_improves_same_trade_profit(self):
        original = [trade(9.3, 930.0)]
        cost_stress = _reprice_execution_path(original, self.cfg, 1.5, 1.0)
        slippage_stress = _reprice_execution_path(original, self.cfg, 1.0, 1.5)
        self.assertLessEqual(cost_stress[0].profit, original[0].profit)
        self.assertLessEqual(slippage_stress[0].profit, original[0].profit)

    def test_bootstrap_is_reproducible_with_fixed_seed(self):
        results = {
            "STRATEGY_ONLY": {"executed": [trade(1, 100), trade(-1, -100)]},
            "AI_RANKING_ONLY": {"executed": [trade(2, 200), trade(-1, -100)]},
        }
        first = _bootstrap_rows(deepcopy(results), self.cfg, samples=25, seed=99)
        second = _bootstrap_rows(deepcopy(results), self.cfg, samples=25, seed=99)
        pd.testing.assert_frame_equal(first, second)
