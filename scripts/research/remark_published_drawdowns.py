r"""Every published drawdown, re-measured against its own trade file.

`backtesting/equity.py` booked profit only at exit until 2026-08-29, so every
`MaxDrawdown` this project has published is a closed-trade drawdown. Fifteen
documents in `docs/audits/strategies/` quote one, and at least one *decision*
rests on one: `min_rr 3.0` shipped explicitly on the grounds that drawdown fell
from 58.89% to 17.62% across a sweep (`MIN_RR_AS_RISK_CONTROL.md`).

Re-running those experiments would cost hours and would not reproduce them
exactly. But it is not necessary: each run directory holds `backtest_results.csv`
with `shares`, `entry_price`, `entry_date` and `exit_date` per executed trade,
which is everything the marked curve needs. So each historical run is re-priced
here from its own record.

The understatement grows with how many positions are held at once, so a sweep
over anything that changes concurrency is exactly where the old number could
have ranked two settings the wrong way round. That is the question this answers.

    venv\Scripts\python.exe scripts\research\remark_published_drawdowns.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from backtesting.equity import EquityCurve                       # noqa: E402
from backtesting.prices import daily_closes                      # noqa: E402

EXPERIMENTS = PROJECT_ROOT / "reports" / "experiments"

#: Loaded once and shared: the runs overlap almost completely in their symbols.
_PRICES = None


class Row:
    """The subset of `Trade` the curve reads, rebuilt from a results CSV."""

    __slots__ = ("symbol", "entry_date", "exit_date", "entry_price",
                 "shares", "portfolio_profit", "executed")

    def __init__(self, record):
        self.symbol = str(record["symbol"])
        self.entry_date = str(record["entry_date"])
        self.exit_date = str(record["exit_date"])
        self.entry_price = float(record["entry_price"])
        self.shares = int(float(record.get("shares") or 0))
        self.portfolio_profit = float(record.get("portfolio_profit") or 0.0)
        self.executed = str(record.get("executed", "True")).strip().lower() not in (
            "false", "0", "")


def prices_for(symbols):
    global _PRICES
    if _PRICES is None:
        _PRICES = daily_closes(symbols)
        return _PRICES
    missing = [s for s in symbols if s not in _PRICES.columns]
    if missing:
        extra = daily_closes(missing)
        if len(extra):
            _PRICES = _PRICES.join(extra, how="outer").sort_index()
    return _PRICES


def remark(run_dir: Path):
    results = run_dir / "backtest_results.csv"
    stats = run_dir / "backtest_statistics.csv"
    if not results.is_file() or not stats.is_file():
        return None
    frame = pd.read_csv(results, encoding="utf-8-sig")
    if frame.empty or "shares" not in frame:
        return None
    published = pd.read_csv(stats, encoding="utf-8-sig").iloc[0].to_dict()

    trades = [Row(record) for record in frame.to_dict("records")]
    trades = [t for t in trades if t.executed and t.shares]
    if not trades:
        return None

    initial = float(published.get("InitialCapital") or 100_000)
    curve = EquityCurve(trades, initial_capital=initial,
                        prices=prices_for(sorted({t.symbol for t in trades})))
    return {
        "run": run_dir.name,
        "trades": len(trades),
        "published": float(published.get("MaxDrawdown") or 0.0),
        "recomputed_closed": curve.closed_trade_max_drawdown(),
        "marked": curve.max_drawdown(),
        "basis": curve.basis(),
        "return": float(published.get("TotalReturn") or 0.0),
        "cagr": published.get("CAGR"),
    }


def main() -> int:
    runs = sorted(p for p in EXPERIMENTS.iterdir() if p.is_dir())
    print(f"{len(runs)} run directories under reports/experiments\n")
    print(f"{'run':<44}{'trades':>7}{'published':>11}{'re-closed':>11}"
          f"{'marked':>9}{'under by':>10}")
    print("-" * 92)

    rows = []
    for run_dir in runs:
        result = remark(run_dir)
        if result is None:
            continue
        rows.append(result)
        drift = ("" if abs(result["recomputed_closed"] - result["published"]) < 0.02
                 else "  <- does not reproduce")
        print(f"{result['run']:<44}{result['trades']:>7}"
              f"{result['published']:>10.2f}%{result['recomputed_closed']:>10.2f}%"
              f"{result['marked']:>8.2f}%"
              f"{result['marked'] - result['published']:>9.2f}pp{drift}")

    if not rows:
        print("No run carried a usable results file.")
        return 1

    frame = pd.DataFrame(rows)
    print(f"\nunderstated by a median of "
          f"{(frame['marked'] - frame['published']).median():.2f}pp, "
          f"worst {(frame['marked'] - frame['published']).max():.2f}pp")

    print("\n" + "=" * 92)
    print("THE ONE DECISION THAT RESTED ON A DRAWDOWN")
    print("=" * 92)
    print("`min_rr 3.0` shipped on a monotone drawdown response across a sweep")
    print("(MIN_RR_AS_RISK_CONTROL.md section 2). If the old measure ranked those")
    print("settings wrongly, the shipped value is wrong. Same runs, re-marked:\n")
    sweep = frame[frame["run"].str.contains("persym_rr|throttle_rr", regex=True)]
    if sweep.empty:
        print("  the rr sweep runs are not in reports/experiments")
    else:
        print(f"  {'run':<44}{'published':>11}{'marked':>9}{'return':>10}")
        print("  " + "-" * 72)
        for _, row in sweep.sort_values("run").iterrows():
            print(f"  {row['run']:<44}{row['published']:>10.2f}%"
                  f"{row['marked']:>8.2f}%{row['return']:>9.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
