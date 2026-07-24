from copy import deepcopy
import random
from types import SimpleNamespace
import unittest

from portfolio.portfolio_simulator import PortfolioSimulator


def trade(symbol, rank, probability, score, confidence, rr, profit=1.0):
    return SimpleNamespace(
        symbol=symbol,
        entry_date="2024-01-02",
        exit_date="2024-01-04",
        entry_price=10.0,
        exit_price=11.0,
        stop_loss=9.0,
        profit=profit,
        ai_rank=rank,
        ai_probability=probability,
        score=score,
        confidence=confidence,
        rr=rr,
        ai_multiplier=1.0,
        ai_mode="AI_RANKING_ONLY",
        executed=False,
        shares=0,
        portfolio_profit=0.0,
        final_position_size=0,
        portfolio_rejection_reason="",
    )


def run(trades):
    simulation = PortfolioSimulator(
        trades,
        initial_capital=1000,
        risk_percent=2,
        allow_overlapping_trades=True,
        max_open_positions=2,
        max_portfolio_risk_percent=4,
    ).run()
    return (
        tuple((item.symbol, item.final_position_size, item.portfolio_profit)
              for item in simulation["executed_trades"]),
        simulation["final_cash"],
        tuple(sorted(simulation["rejection_reasons"].items())),
    )


class PortfolioDeterminismTests(unittest.TestCase):
    def setUp(self):
        # All entries have the same date. The expected order exercises every
        # documented tie breaker before the final alphabetical symbol key.
        self.candidates = [
            trade("ZZZ.CA", 80, 70, 70, 70, 2.0),
            trade("BBB.CA", 80, 80, 60, 60, 2.0),
            trade("AAA.CA", 80, 80, 60, 60, 2.0),
            trade("CCC.CA", 79, 99, 99, 99, 9.0),
        ]

    def test_tie_breakers_choose_rank_then_probability_then_symbol(self):
        signature, _cash, _rejections = run(deepcopy(self.candidates))
        self.assertEqual([row[0] for row in signature], ["AAA.CA", "BBB.CA"])

    def test_reversed_and_seeded_symbol_orders_are_identical(self):
        expected = run(deepcopy(self.candidates))
        permutations = [list(reversed(self.candidates))]
        for seed in (7, 11, 29, 101, 20260711):
            ordered = deepcopy(self.candidates)
            random.Random(seed).shuffle(ordered)
            permutations.append(ordered)

        for ordered in permutations:
            self.assertEqual(run(deepcopy(ordered)), expected)
