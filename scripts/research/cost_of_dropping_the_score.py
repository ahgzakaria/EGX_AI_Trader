r"""What would it cost to convert the Daily Dashboard strategy to gates only?

"Gate only" means one specific change: dropping `score >= min_score` and
`confidence >= min_confidence` from the BUY condition and keeping everything
else -- the market regime, trend, momentum, volume, reward ratio and the four
quality checks. That is the design
[CONFIRMED_VOLUME_BREAKOUT.md](../../docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md)
uses and the one [SCORE_CANNOT_BE_REBUILT.md](../../docs/audits/strategies/SCORE_CANNOT_BE_REBUILT.md)
points at.

The score has no ranking power, so the prediction is specific and falsifiable:
lowering the threshold should add candidates whose mean lift is **the same** as
the ones already there. If instead the added candidates are worse, the score is
doing something after all and the conversion has a real cost.

That is what this measures, and it is worth more than the headline: a threshold
on a field with no signal is a throttle, and a throttle's whole effect is on how
often the round trip is paid.

    venv\Scripts\python.exe scripts\research\cost_of_dropping_the_score.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from scipy import stats

from core.environment import load_project_environment

load_project_environment()

from scripts.research.score_can_it_rank import eligible, prepare  # noqa: E402
from scripts.research.signal_scan import SPLIT                    # noqa: E402

#: What ships today.
SHIPPED_SCORE, SHIPPED_CONFIDENCE = 50, 65


def summarise(frame, label, total_candidates):
    if len(frame) < 20:
        print(f"{label:<28}{len(frame):>8,}{'too few':>12}")
        return
    train = frame[frame["Date"] < SPLIT]["lift"]
    valid = frame[frame["Date"] >= SPLIT]["lift"]
    share = len(frame) / total_candidates * 100
    t = (stats.ttest_1samp(valid, 0).statistic if len(valid) > 2 else float("nan"))
    print(f"{label:<28}{len(frame):>8,}{share:>7.0f}%"
          f"{frame['lift'].mean():>10.2f}{train.mean():>10.2f}"
          f"{valid.mean():>10.2f}{t:>8.2f}"
          f"{frame['lift'].sum() / 100:>12.1f}")


def main() -> int:
    data = prepare()
    pool = data[data["net"].notna()]
    per_date = pool.groupby("Date")["net"].mean()
    data["lift"] = data["net"] - data["Date"].map(per_date)

    candidates = data[eligible(data)].copy()
    total = len(candidates)
    shipped = candidates[(candidates["score"] >= SHIPPED_SCORE)
                         & (candidates["confidence"] >= SHIPPED_CONFIDENCE)]

    print(f"{total:,} bars pass every gate except the score and the confidence.")
    print(f"Of those, {len(shipped):,} ({len(shipped) / total * 100:.0f}%) also "
          f"clear the shipped {SHIPPED_SCORE}/{SHIPPED_CONFIDENCE}.\n")

    header = (f"{'threshold':<28}{'kept':>8}{'of all':>8}{'lift':>10}"
              f"{'train':>10}{'valid':>10}{'t':>8}{'sum lift':>12}")

    print("=" * 96)
    print("1. THE SCORE THRESHOLD, ALONE (confidence left at 0)")
    print("=" * 96)
    print("If the score carried information, mean lift would rise with the")
    print("threshold. `sum lift` is what a portfolio taking every survivor")
    print("would collect: count times mean, in percentage points.")
    print(header)
    print("-" * 96)
    for threshold in (0, 30, 40, 50, 60, 70, 80):
        marker = "  <- shipped" if threshold == SHIPPED_SCORE else ""
        summarise(candidates[candidates["score"] >= threshold],
                  f"score >= {threshold}{marker}", total)

    print("\n" + "=" * 96)
    print("2. THE CONFIDENCE THRESHOLD, ALONE (score left at 0)")
    print("=" * 96)
    print(header)
    print("-" * 96)
    for threshold in (0, 50, 60, 65, 70, 80):
        marker = "  <- shipped" if threshold == SHIPPED_CONFIDENCE else ""
        summarise(candidates[candidates["confidence"] >= threshold],
                  f"confidence >= {threshold}{marker}", total)

    print("\n" + "=" * 96)
    print("3. THE CONVERSION ITSELF")
    print("=" * 96)
    print(header)
    print("-" * 96)
    summarise(candidates, "gates only (0 / 0)", total)
    summarise(shipped, f"shipped ({SHIPPED_SCORE} / {SHIPPED_CONFIDENCE})", total)

    dropped = candidates.drop(shipped.index)
    print()
    summarise(dropped, "what the thresholds refuse", total)

    print("\n" + "=" * 96)
    print("4. ARE THE REFUSED CANDIDATES WORSE?")
    print("=" * 96)
    print("This is the whole question. A throttle that removes trades at random")
    print("costs only the round trip on them; one that removes the bad ones is")
    print("doing real work and should be kept.\n")
    kept_lift, refused_lift = shipped["lift"], dropped["lift"]
    difference = kept_lift.mean() - refused_lift.mean()
    test = stats.ttest_ind(kept_lift, refused_lift, equal_var=False)
    print(f"  kept    {len(kept_lift):>6,} candidates, mean lift "
          f"{kept_lift.mean():+.2f}%, median {kept_lift.median():+.2f}%")
    print(f"  refused {len(refused_lift):>6,} candidates, mean lift "
          f"{refused_lift.mean():+.2f}%, median {refused_lift.median():+.2f}%")
    print(f"  difference {difference:+.2f}%   t = {test.statistic:+.2f}   "
          f"p = {test.pvalue:.2f}")

    for era, mask in (("2016-2022", candidates["Date"] < SPLIT),
                      ("2023-2026", candidates["Date"] >= SPLIT)):
        k = shipped[shipped["Date"].isin(candidates.loc[mask, "Date"])]["lift"]
        r = dropped[dropped["Date"].isin(candidates.loc[mask, "Date"])]["lift"]
        if len(k) > 2 and len(r) > 2:
            era_test = stats.ttest_ind(k, r, equal_var=False)
            print(f"  {era}: kept {k.mean():+.2f}% vs refused {r.mean():+.2f}%"
                  f"   t = {era_test.statistic:+.2f}   p = {era_test.pvalue:.2f}")

    print("\n" + "=" * 96)
    print("5. WHAT THE CONVERSION COSTS, IN THE ONLY TERMS THAT SURVIVE")
    print("=" * 96)
    ratio = len(candidates) / max(len(shipped), 1)
    print(f"  {ratio:.1f}x as many trades taken.")
    print(f"  Mean lift per trade moves {shipped['lift'].mean():+.2f}% -> "
          f"{candidates['lift'].mean():+.2f}%.")
    print(f"  Total lift collected moves {shipped['lift'].sum() / 100:+.1f} -> "
          f"{candidates['lift'].sum() / 100:+.1f} points.")
    print("\n  Those are per-signal figures over a fixed universe. They are NOT")
    print("  a portfolio result: with fifteen slots and a twenty-day hold, most")
    print("  of the extra candidates would never be funded. The engine run is")
    print("  what settles that, and it is the next step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
