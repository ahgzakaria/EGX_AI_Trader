"""Choose a setting on the past, measure it on the future, and repeat.

`min_rr 3.0` shipped on an in-sample sweep. That is defensible for a risk
control with a monotone response, but nothing about it has been tested out of
sample, and 84% of its profit came from 5 of 468 trades.

This does the honest version. Walking forward through the history, at each fold
boundary it picks the setting that looked best on everything *before* that date,
then scores that choice on the fold *after* it. The score is therefore always
earned on data the choice could not see.

Two things it deliberately does not do:

* It does not re-run backtests per fold. Each setting's full trade record already
  exists; partitioning those records by entry date is equivalent for per-trade
  measures and far cheaper. Portfolio-level figures do not partition cleanly and
  are not reported here.
* It does not select on return. Return is the noisiest measure available and the
  reason the in-sample sweep bounced between -36% and +40%. The selection
  criterion is configurable and defaults to the risk measure the setting was
  actually shipped for.

Read-only over run directories produced by `isolated_backtest.py`.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "reports" / "experiments"


def load_trades(run_dir: Path):
    out = []
    with (run_dir / "backtest_results.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append((row["entry_date"], float(row["profit_percent"])))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(out)


def worst_drawdown(profits):
    """Peak-to-trough of the cumulative per-trade series, in percentage points.

    Not the portfolio drawdown -- position sizing does not partition by date --
    but it is the same shape and it is comparable between settings.
    """
    peak = running = 0.0
    worst = 0.0
    for value in profits:
        running += value
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return worst


def longest_losing_streak(profits):
    worst = streak = 0
    for value in profits:
        streak = streak + 1 if value <= 0 else 0
        worst = max(worst, streak)
    return worst


SCORERS = {
    # Lower is better for drawdown, so negate to keep "higher is better".
    "drawdown": lambda p: worst_drawdown(p),
    "streak": lambda p: -longest_losing_streak(p),
    "mean": lambda p: statistics.mean(p) if p else 0.0,
    "median": lambda p: statistics.median(p) if p else 0.0,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="*persym_rr*",
                    help="glob for the run directories forming the candidate set")
    ap.add_argument("--criterion", choices=sorted(SCORERS), default="drawdown")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--min-train", type=int, default=60,
                    help="trades required before a choice is allowed")
    args = ap.parse_args()

    runs = {}
    for path in sorted(EXPERIMENTS.glob(args.pattern)):
        if not (path / "backtest_results.csv").exists():
            continue
        label = path.name.split("_persym_rr")[-1] if "_persym_rr" in path.name \
            else path.name
        runs[label] = load_trades(path)
    if len(runs) < 2:
        raise SystemExit(f"need at least two settings to choose between; "
                         f"found {sorted(runs)}")

    dates = sorted({d for trades in runs.values() for d, _p in trades})
    edges = [dates[int(len(dates) * (i + 1) / (args.folds + 1))]
             for i in range(args.folds)]

    print(f"candidates: {', '.join(sorted(runs))}")
    print(f"criterion : {args.criterion} (chosen on the past, scored on the future)")
    print(f"folds     : {args.folds}\n")
    print(f"  {'from':>12}{'to':>12}{'chosen':>9}{'n':>6}"
          f"{'oos mean':>11}{'oos median':>12}{'oos DD':>9}")

    chosen_history, oos_means, oos_dd = [], [], []
    for index, edge in enumerate(edges):
        end = edges[index + 1] if index + 1 < len(edges) else dates[-1] + "~"
        scores = {}
        for label, trades in runs.items():
            train = [p for d, p in trades if d < edge]
            if len(train) < args.min_train:
                continue
            scores[label] = SCORERS[args.criterion](train)
        if not scores:
            continue
        chosen = max(scores, key=scores.get)
        future = [p for d, p in runs[chosen] if edge <= d < end]
        if not future:
            continue
        chosen_history.append(chosen)
        oos_means.append(statistics.mean(future))
        oos_dd.append(worst_drawdown(future))
        print(f"  {edge:>12}{end[:10]:>12}{chosen:>9}{len(future):>6}"
              f"{statistics.mean(future):>+10.3f}%"
              f"{statistics.median(future):>+11.3f}%"
              f"{worst_drawdown(future):>+8.1f}%")

    if not chosen_history:
        raise SystemExit("no fold had enough history to choose on")

    print(f"\n  settings chosen: {chosen_history}")
    stable = len(set(chosen_history)) == 1
    print(f"  stable choice  : {'yes, always ' + chosen_history[0] if stable else 'NO'}")
    print(f"  oos mean of fold means : {statistics.mean(oos_means):+.3f}%")
    print(f"  oos worst fold drawdown: {min(oos_dd):+.1f}%")

    # The null: what would always taking the widest and narrowest have given?
    print(f"\n  {'setting':>9}{'full-sample mean':>19}{'full-sample DD':>17}")
    for label in sorted(runs):
        profits = [p for _d, p in runs[label]]
        print(f"  {label:>9}{statistics.mean(profits):>+18.3f}%"
              f"{worst_drawdown(profits):>+16.1f}%")


if __name__ == "__main__":
    main()
