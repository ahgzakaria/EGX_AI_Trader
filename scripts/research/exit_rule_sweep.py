r"""What the exit rules cost the Daily Dashboard strategy, on the frozen record.

    venv\Scripts\python.exe scripts\research\exit_rule_sweep.py

The forward replay of the dashboard's real BUY decisions
(`scripts/evaluate_daily_dashboard_buys.py`) found 35 of 47 closed trades
leaving through the EMA20 trailing stop at an average of -1.83%, 22 of them
exactly three sessions after entry: the trail starts on the second bar and the
entry band sits almost on EMA20, so an ordinary day's range reaches it. Forty-
three trades over two months cannot justify changing a rule. This asks the same
question of the frozen history, where there are hundreds.

It runs the canonical FULL_HISTORY Strategy-Only pass exactly as
`services.backtest_service.run_backtest` does -- same symbol universe, same
spread filter, same decision service, same engine, same `PortfolioSimulator`,
same `BacktestStatistics` -- once per exit configuration, with the configuration
applied to the in-memory settings and restored afterwards.

It deliberately does NOT call `BacktestReport.save_all()` or open an
`ExperimentRun`: those write `reports/backtest_*.csv` and a run directory, and
this is a comparison, not a result to publish. Nothing on disk changes.

Every configuration is also split into two eras. A rule that only helps in one
of them is a rule fitted to that era.

**Result, 2026-09-15** (230 symbols, current settings; the first row reproduces
the current-configuration measurement of 609 trades / PF 1.54 / +142.71%):

    configuration            trades  win%    PF   return   maxDD   PF <2023  PF >=2023
    EMA20 trail (configured)    609  39.1  1.54  +142.7%   18.0%      1.44       1.59
    no trailing stop            205  28.3  0.83   -45.2%   56.7%      0.65       1.00
    ATR trail x2                180  22.8  0.73   -62.1%   65.1%      0.54       0.96
    ATR trail x3                177  25.4  0.80   -44.5%   65.0%      0.56       1.11

The hypothesis this was written to test is refuted. The trail that looked like
the leak is the edge: every looser exit loses money before 2023 and is flat at
best after it. Held for ~16 days instead of ~5, trades run into the stop, and
positions sitting that long also block new entries under the five-position cap,
so the trade count collapses from 609 to under 210. Whatever improves this
strategy has to change which trades are entered, not how quickly losers are cut.
"""

from __future__ import annotations

import statistics
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment       # noqa: E402

load_project_environment()

from backtesting.config import load as load_backtest_config  # noqa: E402
from config.settings_manager import settings                 # noqa: E402
from core.symbols import SYMBOL_SOURCE, filter_by_spread, load_symbols  # noqa: E402
from services.backtest_service import (_decision_service,    # noqa: E402
                                       _portfolio_result, _run_pass)
from strategy.trading_decision import TradingDecisionService  # noqa: E402

#: The configured rules first, so the first row can be checked against
#: reports/backtest_statistics.csv before any other row is believed.
VARIANTS = [
    ("configured (EMA20 trail)", {}),
    ("no trailing stop", {"trailing_enabled": False}),
    ("ATR trail x2", {"trailing_mode": "ATR", "trailing_atr": 2.0}),
    ("ATR trail x3", {"trailing_mode": "ATR", "trailing_atr": 3.0}),
]

ERA_SPLIT = "2023-01-01"


def run(overrides, symbols):
    original = dict(settings.data["backtest"])
    settings.data["backtest"] = {**original, **overrides}
    try:
        cfg = load_backtest_config()
        mode = TradingDecisionService.STRATEGY_ONLY
        trades, failures, _, _ = _run_pass(symbols, _decision_service(mode))
        return _portfolio_result(trades, cfg, mode), failures
    finally:
        settings.data["backtest"] = original


def era(executed, before):
    """(trades, win %, profit factor, net) for one side of the split."""
    chosen = [t for t in executed
              if (str(t.entry_date) < ERA_SPLIT) == before]
    profits = [float(getattr(t, "portfolio_profit", t.profit)) for t in chosen]
    if not profits:
        return 0, float("nan"), float("nan"), 0.0
    gains = sum(p for p in profits if p > 0)
    losses = -sum(p for p in profits if p < 0)
    return (len(profits), sum(p > 0 for p in profits) / len(profits) * 100,
            gains / losses if losses else float("inf"), sum(profits))


def main():
    settings.reload()
    cfg = load_backtest_config()
    symbols, dropped = filter_by_spread(load_symbols(SYMBOL_SOURCE),
                                        getattr(cfg, "MAX_SPREAD_PERCENT", None))
    print(f"{len(symbols)} symbols ({len(dropped) if dropped else 0} dropped for spread)\n")

    rows = []
    for label, overrides in VARIANTS:
        started = time.time()
        result, failures = run(overrides, symbols)
        s = result["summary"]
        executed = result["executed"]
        reasons = Counter(str(t.exit_reason) for t in executed)
        holding = [float(getattr(t, "holding_days", 0) or 0) for t in executed]
        early = sum(1 for t in executed
                    if "Trailing" in str(t.exit_reason)
                    and float(getattr(t, "holding_days", 99) or 99) <= 3)
        rows.append((label, s, era(executed, True), era(executed, False)))
        print(f"== {label}  ({time.time() - started:.0f}s, {len(failures)} symbol failures)")
        print(f"   trades {s['Trades']}  win {s['WinRate']}%  PF {s['ProfitFactor']}  "
              f"return {s['TotalReturn']}%  maxDD {s['MaxDrawdown']}%  "
              f"sharpe {s['SharpeRatio']}  calmar {s['CalmarRatio']}  "
              f"avg hold {statistics.fmean(holding) if holding else 0:.1f}d")
        print(f"   trailing exits within 3 days: {early}   exits: {dict(reasons.most_common(6))}")
        sys.stdout.flush()

    print(f"\n{'configuration':<26}{'era':>10}{'trades':>8}{'win%':>7}{'PF':>7}{'net EGP':>12}")
    for label, _, before, after in rows:
        for name, (n, win, pf, net) in ((f"<{ERA_SPLIT[:4]}", before),
                                        (f">={ERA_SPLIT[:4]}", after)):
            print(f"{label:<26}{name:>10}{n:>8}{win:>7.1f}{pf:>7.2f}{net:>12,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
