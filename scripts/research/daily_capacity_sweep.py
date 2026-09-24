r"""What the portfolio limits cost the Daily Dashboard strategy, measured.

    venv\Scripts\python.exe scripts\research\daily_capacity_sweep.py

The shipped policy is 2% risk per trade under a 10% portfolio-risk ceiling with
ten positions allowed. ``PortfolioSimulator`` takes
``min(max_open_positions, floor(max_portfolio_risk_percent / risk_percent))``,
so ten is never reached: the cap is **five**. That is the arithmetic
``CAPACITY_IS_THE_CONSTRAINT.md`` found in CONFIRMED_VOLUME_BREAKOUT, where
spreading the same risk over fifteen positions raised the return and cut the
drawdown. The fix was shipped for that strategy and never measured for this
one. On the live record it refused 19 of 51 admissible decisions -- 15 for
capital, 4 for the position cap.

## Fixed before running

* **The rule is untouched.** Trades are generated once by the frozen engine
  (`services.backtest_service._run_pass`) and re-simulated under each policy by
  the frozen simulator, so every row sees the identical signal stream. Nothing
  in `strategy/` is read differently.
* **The policies**, chosen before any result:

  ====  ======  ============  ======  ==========================================
  row   risk %  portfolio %   max     what it tests
  ====  ======  ============  ======  ==========================================
  A     2.0     10            10      shipped -- effectively 5 positions
  B     1.0     10            10      the same 10% budget over 10 positions
  C     0.67    10            15      the same 10% budget over 15 positions
  D     1.0     15            15      CONFIRMED_VOLUME_BREAKOUT's policy
  ====  ======  ============  ======  ==========================================

* **What decides.** ``TotalReturn`` is close to linear in risk -- position size
  comes from initial capital, never from equity -- so it cannot rank rows at
  different budgets. Sharpe, Calmar, the marked drawdown and the share of
  signals taken can. A policy is better only if it raises Sharpe **and** Calmar
  over the whole window **and** does not lower net profit in the >=2023 era,
  where the gate stack's lift over the market is already negative
  (DAILY_STRATEGY_DIAGNOSIS.md §1). Rows B and C have A's budget, so their
  total return may be compared with A's directly.
* **The benchmark is printed beside every row.** This strategy's own record is
  that it does not beat owning the names it trades (INVESTIGATION_SUMMARY.md);
  a better policy that still trails the market is reported as that.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment        # noqa: E402

load_project_environment()

from backtesting.config import load as load_backtest_config    # noqa: E402
from config.settings_manager import settings                   # noqa: E402
from core.symbols import SYMBOL_SOURCE, filter_by_spread, load_symbols  # noqa: E402
from services.backtest_service import (_decision_service,      # noqa: E402
                                       _portfolio_result, _run_pass)
from strategy.trading_decision import TradingDecisionService   # noqa: E402

#: Registered before the first run. Label, risk %, portfolio %, max positions.
POLICIES = (
    ("A shipped (cap 5)", 2.0, 10.0, 10),
    ("B same budget, 10", 1.0, 10.0, 10),
    ("C same budget, 15", 0.67, 10.0, 15),
    ("D breakout policy", 1.0, 15.0, 15),
)

ERA_SPLIT = "2023-01-01"


def effective_cap(risk, portfolio, positions):
    """The simulator's own arithmetic, so the table says which cap binds."""
    return min(int(positions), int(float(portfolio) // float(risk)))


def era(executed, before):
    chosen = [t for t in executed if (str(t.entry_date) < ERA_SPLIT) == before]
    profits = [float(getattr(t, "portfolio_profit", t.profit)) for t in chosen]
    if not profits:
        return 0, float("nan"), 0.0
    gains = sum(p for p in profits if p > 0)
    losses = -sum(p for p in profits if p < 0)
    return len(profits), (gains / losses if losses else float("inf")), sum(profits)


def simulate(trades, risk, portfolio, positions, mode):
    original = dict(settings.data["backtest"])
    settings.data["backtest"] = {**original, "risk_percent": risk,
                                 "max_portfolio_risk_percent": portfolio,
                                 "max_open_positions": positions}
    try:
        return _portfolio_result(trades, load_backtest_config(), mode)
    finally:
        settings.data["backtest"] = original


def main():
    settings.reload()
    cfg = load_backtest_config()
    symbols, dropped = filter_by_spread(load_symbols(SYMBOL_SOURCE),
                                        getattr(cfg, "MAX_SPREAD_PERCENT", None))
    mode = TradingDecisionService.STRATEGY_ONLY
    started = time.time()
    trades, failures, _, _ = _run_pass(symbols, _decision_service(mode))
    print(f"{len(symbols)} symbols, {len(trades)} candidate trades from the frozen "
          f"engine in {time.time() - started:.0f}s ({len(failures)} symbol failures)\n")

    header = (f"{'policy':<20}{'cap':>4}{'taken':>7}{'PF':>6}{'return%':>9}"
              f"{'maxDD%':>8}{'sharpe':>8}{'calmar':>8}{'bench%':>9}")
    print(header)
    print("-" * len(header))
    rows = []
    for label, risk, portfolio, positions in POLICIES:
        result = simulate(trades, risk, portfolio, positions, mode)
        s, executed = result["summary"], result["executed"]
        rows.append((label, executed))
        print(f"{label:<20}{effective_cap(risk, portfolio, positions):>4}"
              f"{len(executed):>7}{s['ProfitFactor']:>6}{s['TotalReturn']:>9}"
              f"{s['MaxDrawdown']:>8}{s['SharpeRatio']:>8}{s['CalmarRatio']:>8}"
              f"{s.get('BenchmarkReturn', float('nan')):>9}")

    print(f"\n{'policy':<20}{'era':>8}{'trades':>8}{'PF':>7}{'net EGP':>12}")
    for label, executed in rows:
        for name, before in ((f"<{ERA_SPLIT[:4]}", True), (f">={ERA_SPLIT[:4]}", False)):
            n, pf, net = era(executed, before)
            print(f"{label:<20}{name:>8}{n:>8}{pf:>7.2f}{net:>12,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
