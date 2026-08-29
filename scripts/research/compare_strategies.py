r"""The two swing strategies, side by side, on terms neither of them chose.

The Daily Dashboard strategy's line comes from its own isolated run. The new
strategy's line is produced here, live. Both use the same universe, the same
backtest provider, the same per-symbol cost table, the same simulator and the
same `BacktestStatistics`.

They do **not** use the same portfolio policy, and that is deliberate: 2% risk
with a five-position cap for the Daily Dashboard, 1% with fifteen for this one,
because each was measured for its own strategy
(`CAPACITY_IS_THE_CONSTRAINT.md`). The comparison is between two strategies as
each is configured to run, not between two rules at one arbitrary policy.

Two traps this file exists to not fall into:

* **The run it reads must have a marked-to-market drawdown.** Until 2026-08-29
  `MaxDrawdown` was a closed-trade figure, and comparing one of those against a
  marked one flatters whichever side is marked by around two points. The
  baseline is asserted, not assumed.
* **A corporate action in unadjusted prices is not a trade.** The last section
  measures how much of each record is one, and it is the least flattering thing
  here to both sides.

    venv\Scripts\python.exe scripts\research\compare_strategies.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from scripts.research.panel import load as load_panel               # noqa: E402
from strategy_momentum_breakout.runner import run, simulate         # noqa: E402
from strategy_momentum_breakout.config import load as load_config   # noqa: E402

#: The as-configured Daily Dashboard run, re-measured after `equity.py` began
#: marking open positions. `20260827_193224_reward_guard` is the same 484 trades
#: and the same +60.08%, but its drawdown is the closed-trade 16.17%.
SHIPPED = PROJECT_ROOT / "reports" / "experiments" / "20260829_123910_drawdown_marked"

#: Beyond EGX's daily price limit in either direction: a split, a bonus issue, a
#: resumption, or bad data -- never a session's trading.
LIMIT = 30.0

ROWS = [
    ("Trades", "{:.0f}", False),
    ("WinRate", "{:.2f}%", True),
    ("ProfitFactor", "{:.2f}", True),
    ("TotalReturn", "{:+.2f}%", True),
    ("CAGR", "{:+.2f}%", True),
    ("MaxDrawdown", "{:.2f}%", False),
    ("MaxDrawdownClosedTrades", "{:.2f}%", False),
    ("SharpeRatio", "{:.2f}", True),
    ("SortinoRatio", "{:.2f}", True),
    ("CalmarRatio", "{:.2f}", True),
    ("AverageProfitPercent", "{:+.2f}%", True),
    ("AverageHoldingDays", "{:.1f}", False),
    ("MaxConsecutiveLosses", "{:.0f}", False),
    ("ExposurePercent", "{:.2f}%", False),
    ("TradesPerYear", "{:.1f}", False),
    ("BestTrade", "{:+,.0f}", False),
    ("WorstTrade", "{:+,.0f}", False),
]


def corporate_actions(panel):
    panel = panel.sort_values(["Symbol", "Date"])
    move = panel.groupby("Symbol", sort=False)["Close"].pct_change() * 100
    flagged = panel[move.abs() >= LIMIT]
    return {symbol: set(frame["Date"].dt.date)
            for symbol, frame in flagged.groupby("Symbol")}


def spanning(rows, events):
    """Rows whose holding period contains a session outside the price limit."""
    hit = []
    for _, row in rows.iterrows():
        dates = events.get(row["symbol"], ())
        if not dates:
            continue
        entry = pd.Timestamp(row["entry_date"]).date()
        exit_ = pd.Timestamp(row["exit_date"]).date()
        if any(entry <= date <= exit_ for date in dates):
            hit.append(row)
    return pd.DataFrame(hit)


def main() -> int:
    # One header row and one value row, not a key/value table.
    shipped = pd.read_csv(SHIPPED / "backtest_statistics.csv",
                          encoding="utf-8-sig").iloc[0].to_dict()

    cfg = load_config()
    trades, errors, _ = run()
    _simulation, executed, summary = simulate(trades, cfg)

    # Refuse the comparison rather than print a misleading row. A closed-trade
    # drawdown against a marked one is not a difference between the strategies.
    bases = (shipped.get("DrawdownBasis"), summary.get("DrawdownBasis"))
    if bases[0] != bases[1]:
        raise SystemExit(
            f"drawdown bases differ: baseline {bases[0]!r} against "
            f"{bases[1]!r}.\n"
            "Re-run the baseline with\n"
            "  venv/Scripts/python.exe scripts/research/isolated_backtest.py "
            "--label drawdown_marked\n"
            "and point SHIPPED at it."
        )

    print("=" * 78)
    print("DAILY DASHBOARD (classic score)  vs  CONFIRMED VOLUME BREAKOUT")
    print("=" * 78)
    print(f"{'':<24}{'Daily Dashboard':>18}{'New strategy':>18}{'':>10}")
    print("-" * 72)
    for key, fmt, higher_is_better in ROWS:
        try:
            old = float(shipped[key])
            new = float(summary[key])
        except (KeyError, TypeError, ValueError):
            continue
        if higher_is_better is None:
            mark = ""
        elif abs(new - old) < 1e-9:
            mark = "  ="
        else:
            better = (new > old) if higher_is_better else (new < old)
            mark = "  new" if better else "  old"
        print(f"{key:<24}{fmt.format(old):>18}{fmt.format(new):>18}{mark:>10}")

    print(f"\nBoth drawdowns are {bases[0]}; the closed-trade row is what this")
    print("project reported for both strategies until 2026-08-29.")
    print("\nThe two are not the same bet, and not the same portfolio policy.")
    print("The Daily Dashboard strategy takes 484 trades holding 4.5 days behind")
    print("a trailing stop, at 2% risk across five positions; this one takes")
    print(f"{summary['Trades']} holding {summary['AverageHoldingDays']:.0f} days "
          f"behind a wide fixed stop, at {cfg.risk_percent:g}% across "
          f"{cfg.max_open_positions}.")

    print("\n" + "=" * 78)
    print("HOW MUCH OF EACH RECORD IS A DATA ARTIFACT")
    print("=" * 78)
    events = corporate_actions(load_panel())
    print(f"sessions beyond ±{LIMIT:.0f}% across the panel: "
          f"{sum(len(v) for v in events.values())} bars, "
          f"{len(events)} symbols\n")

    old_rows = pd.read_csv(SHIPPED / "backtest_results.csv",
                           encoding="utf-8-sig")
    old_hit = spanning(old_rows, events)
    print(f"Daily Dashboard : {len(old_hit)} of {len(old_rows)} trades span one")
    if not old_hit.empty:
        for _, row in old_hit.nsmallest(4, "profit_percent").iterrows():
            print(f"    {row['symbol']:<10} {row['entry_date']} -> {row['exit_date']}"
                  f"  {row['profit_percent']:+8.2f}%  {row['exit_reason']}")
        print(f"    their share of that run's net profit: "
              f"{old_hit['profit'].sum() / old_rows['profit'].sum() * 100:+.1f}%")

    new_rows = pd.DataFrame([{
        "symbol": t.symbol, "entry_date": t.entry_date, "exit_date": t.exit_date,
        "profit_percent": (t.exit_price / t.entry_price - 1) * 100,
        "exit_reason": t.exit_reason,
    } for t in executed])
    new_hit = spanning(new_rows, events)
    print(f"\nNew strategy    : {len(new_hit)} of {len(new_rows)} trades span one")
    print("    it closes the position at the last clean price instead, and")
    print("    refuses a signal while one is inside the windows it reads.")
    if errors:
        print(f"\n{len(errors)} symbols had no usable history in either run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
