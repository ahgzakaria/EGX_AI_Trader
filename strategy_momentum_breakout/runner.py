r"""A whole-universe run of CONFIRMED_VOLUME_BREAKOUT, scored like the shipped one.

Trades from every symbol go through the project's own `PortfolioSimulator` and
then through `BacktestStatistics`, so the summary this prints is the same
summary the Daily Dashboard strategy's backtest prints, field for field.

The *limits* are this strategy's own -- 1% risk across fifteen positions, from
`CAPACITY_IS_THE_CONSTRAINT.md` -- not the Daily Dashboard's 2% across five.
Each was measured for its own strategy, and pretending otherwise is how
`min_rr 3.0` nearly came to be inherited into a configuration nobody had
measured. `scripts/research/compare_strategies.py` reads the two side by side
and says so.

Two things it adds that the shipped harness does not have, both because the
shipped strategy's own audits needed them and had to compute them by hand:

* **A benchmark.** Owning an equal-weighted slice of the same universe over the
  same window. In EGP terms this market compounded enormously across the test
  period, and a strategy that returns less than the market it trades has not
  earned its costs however good its profit factor looks.
* **Year by year.** An aggregate is how a result carried by five trades comes to
  be quoted as a strategy.

    venv\Scripts\python.exe -m strategy_momentum_breakout.runner
    venv\Scripts\python.exe -m strategy_momentum_breakout.runner --limit 40
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from backtesting.config import load as load_backtest_config      # noqa: E402
from backtesting.prices import closes_for_trades                 # noqa: E402
from backtesting.statistics import BacktestStatistics            # noqa: E402
from core.data_provider import load_history, provider_purpose    # noqa: E402
from core.symbols import load_symbols                            # noqa: E402
from portfolio.portfolio_simulator import PortfolioSimulator     # noqa: E402
from strategy_momentum_breakout.backtest import run_symbol       # noqa: E402
from strategy_momentum_breakout.config import load as load_config  # noqa: E402


def run(symbols=None, cfg=None, progress=None):
    cfg = cfg or load_config()
    symbols = symbols or load_symbols()
    trades, errors = [], []
    rejections: dict[str, int] = {}
    for position, symbol in enumerate(symbols, 1):
        result = run_symbol(symbol, cfg)
        if result.error:
            errors.append({"Symbol": symbol, "Error": result.error})
        trades.extend(result.trades)
        for reason, count in result.rejections.items():
            rejections[reason] = rejections.get(reason, 0) + count
        if progress:
            progress(position, len(symbols), symbol)
    return trades, errors, rejections


def simulate(trades, cfg=None, prices=None):
    cfg = cfg or load_config()
    trades = sorted(trades, key=lambda trade: trade.entry_date)
    simulation = PortfolioSimulator(
        trades,
        initial_capital=cfg.initial_capital,
        risk_percent=cfg.risk_percent,
        allow_overlapping_trades=cfg.allow_overlapping_trades,
        max_open_positions=cfg.max_open_positions,
        max_portfolio_risk_percent=cfg.max_portfolio_risk_percent,
    ).run()
    executed = simulation["executed_trades"]
    # Without prices `MaxDrawdown` is the realised, closed-trade figure, which
    # cannot fall while a position is open and losing. This strategy holds
    # fifteen positions for twenty sessions, so that is precisely the case it
    # would hide. Pass them once and reuse across a sweep.
    if prices is None:
        prices = closes_for_trades(executed)
    summary = BacktestStatistics(
        executed, initial_capital=cfg.initial_capital, prices=prices).summary()
    return simulation, executed, summary


def benchmark(symbols, start, end):
    """What not trading was worth over the same window, two honest ways.

    Both are reported because they answer different questions and disagree by a
    factor of six, and quoting only one of them would be a choice about what to
    make the strategy look like.

    * **Buy and hold the median name.** Pick one symbol at the start, hold it to
      the end. This is what an investor actually does, and it is a geometric
      return on a single decision.
    * **Equal-weighted, rebalanced daily.** The cross-sectional mean daily
      return, compounded. It is the right *comparison* for a rule that picks a
      different name every few weeks, and it is also an upper bound nobody could
      trade: rebalancing 190 EGX names daily would pay the ~1.4% round trip
      hundreds of times, and an arithmetic mean of noisy small-cap returns
      exceeds any weighted portfolio's geometric return.

    Both carry the same survivorship inflation as the strategy: the panel is
    what the provider still serves, so names that delisted are absent from the
    benchmark and from the strategy alike.
    """
    closes, totals = [], []
    for symbol in symbols:
        try:
            with provider_purpose("backtest"):
                frame = load_history(symbol, purpose="backtest")
        except Exception:                             # noqa: BLE001 - skipped
            continue
        if frame is None or frame.empty:
            continue
        window = frame.loc[(frame.index >= start) & (frame.index <= end), "Close"]
        if len(window) < 100:
            continue
        closes.append(window.pct_change().rename(symbol))
        totals.append((window.iloc[-1] / window.iloc[0] - 1) * 100)
    if not closes:
        return None
    daily = pd.concat(closes, axis=1, sort=True).mean(axis=1)
    level = float((1 + daily.fillna(0)).cumprod().iloc[-1])
    years = max((end - start).days / 365.25, 1e-9)
    median_total = float(np.median(totals))
    return {
        "Symbols": len(closes),
        "Years": round(years, 1),
        "MedianBuyHoldReturn": round(median_total, 2),
        "MedianBuyHoldCAGR": round(
            ((1 + median_total / 100) ** (1 / years) - 1) * 100, 2),
        "EqualWeightedReturn": round((level - 1) * 100, 2),
        "EqualWeightedCAGR": round((level ** (1 / years) - 1) * 100, 2),
    }


def per_year(executed):
    rows = []
    for trade in executed:
        rows.append({
            "year": int(str(trade.entry_date)[:4]),
            "percent": (trade.exit_price / trade.entry_price - 1) * 100,
            "profit": getattr(trade, "portfolio_profit", 0.0),
        })
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    table = frame.groupby("year").agg(
        trades=("percent", "size"),
        avg_percent=("percent", "mean"),
        median_percent=("percent", "median"),
        net_profit=("profit", "sum"),
    )
    table["win_rate"] = frame.groupby("year")["percent"].apply(
        lambda s: (s > 0).mean() * 100)
    return table.round(2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run CONFIRMED_VOLUME_BREAKOUT over the universe.")
    parser.add_argument("--limit", type=int, default=None,
                        help="use only the first N symbols (a smoke test)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    symbols = load_symbols()
    if args.limit:
        symbols = symbols[:args.limit]

    print("=" * 64)
    print(f"{cfg.strategy_name} -- {len(symbols)} symbols")
    print("=" * 64)

    started = time.time()

    def progress(position, total, symbol):
        if not args.quiet and position % 20 == 0:
            print(f"  [{position}/{total}] {symbol}")

    trades, errors, rejections = run(symbols, cfg, progress)
    print(f"\nsignals -> trades: {len(trades):,}   "
          f"symbols with no data: {len(errors)}   "
          f"elapsed {time.time() - started:.0f}s")

    if not trades:
        print("No trades. Nothing to score.")
        return 1

    simulation, executed, summary = simulate(trades, cfg)
    print("\nPORTFOLIO SUMMARY (same simulator, capital and sizing as the "
          "Daily Dashboard backtest)")
    for key, value in summary.items():
        print(f"  {key:24}: {value}")

    print("\nPER YEAR")
    print(per_year(executed).to_string())

    entries = pd.to_datetime([t.entry_date for t in executed])
    exits = pd.to_datetime([t.exit_date for t in executed])
    mark = benchmark(symbols, entries.min(), exits.max())
    if mark:
        print(f"\nBENCHMARK over the same window "
              f"({entries.min().date()} -> {exits.max().date()}, "
              f"{mark['Years']} years, {mark['Symbols']} symbols)")
        print(f"  this strategy                  : "
              f"{summary['TotalReturn']:+9.2f}%  (CAGR "
              f"{summary['CAGR']:+.2f}%, exposure {summary['ExposurePercent']}%)")
        print(f"  buy and hold the median name   : "
              f"{mark['MedianBuyHoldReturn']:+9.2f}%  (CAGR "
              f"{mark['MedianBuyHoldCAGR']:+.2f}%, exposure 100%)")
        print(f"  equal-weighted, daily rebalance: "
              f"{mark['EqualWeightedReturn']:+9.2f}%  (CAGR "
              f"{mark['EqualWeightedCAGR']:+.2f}%, untradeable -- see docstring)")
        print("\n  Read this honestly. In nominal EGP across a decade in which"
              "\n  the currency lost most of its value, doing nothing beat this"
              "\n  strategy and beat the Daily Dashboard strategy by more. What"
              "\n  is being claimed is that this rule selects better than the"
              "\n  one beside it, not that it beats owning the market.")

    print("\nWHY SIGNALS WERE REJECTED (first failing gate, per bar)")
    for reason, count in sorted(rejections.items(), key=lambda kv: -kv[1]):
        if count:
            print(f"  {reason:22}: {count:,}")

    print("\nPORTFOLIO-LEVEL REJECTIONS")
    for reason, count in sorted(simulation["rejection_reasons"].items(),
                                key=lambda kv: -kv[1]):
        if count:
            print(f"  {reason:22}: {count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
