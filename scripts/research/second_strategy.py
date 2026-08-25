r"""What else could earn a place beside the volume breakout?

The constraints are already measured and none of them are negotiable here.
The horizon cannot go below ten days -- the round trip is 1,030% of the average
intraday move on EGX and 292% at three days. The universe cannot go below a 5M
EGP turnover floor without the fills becoming fiction. And the entry has to beat
owning the whole universe on the same days, in both eras, not just in the bull
market that is the validation era.

Two things have already been tested and rejected against exactly this bar:
pullback entries (``pullback_entry.py``, loses by two to four points in both
eras) and a value filter (``value_factor.py``, the two eras disagree about
where the effect even lives).

What is tested here is whether a *different kind of trigger* fires on different
days than the shipped rule. A second strategy that selects the same trades adds
nothing to a portfolio however good its numbers look, so the overlap with the
shipped signal is reported beside the lift, and it is the first column to read.

* **52-week high proximity** -- a long-horizon anomaly rather than a
  twenty-day event. Different literature, different signal.
* **turnover surge without a breakout** -- accumulation before the move
  instead of confirmation after it.
* **gap up on volume** -- the same idea as a breakout but resolved at the
  open, which is a different day and a different fill.
* **60-day breakout** -- the shipped structure over a longer base, which
  should fire less often and on bigger bases.
* **calm uptrend** -- no event at all: the quietest names in the strongest
  third, which is the closest thing to an anti-breakout.

    venv\Scripts\python.exe scripts\research\second_strategy.py
"""

from __future__ import annotations

from pathlib import Path
import statistics
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from scripts.research.breakout_filters import COST, HOLD, SPLIT, build, load

#: The shipped rule, which everything else is measured against and compared to.
def shipped(data: pd.DataFrame) -> pd.Series:
    return (data["breakout"] & (data["volume_ratio"] >= 2.5)
            & (data["momentum_rank"] >= 0.67))


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol measures, every window shifted so today never sets its own
    threshold -- the rule that keeps a breakout from breaking out of itself."""
    frames = []
    for _, frame in panel.groupby("Symbol", sort=False):
        frame = frame.sort_values("Date").copy()
        close, high, low, volume = (frame["Close"], frame["High"],
                                    frame["Low"], frame["Open"])
        frame["high_252"] = frame["High"].rolling(252).max().shift(1)
        frame["near_52w_high"] = close >= frame["high_252"] * 0.95
        frame["high_60"] = frame["High"].rolling(60).max().shift(1)
        frame["breakout_60"] = close > frame["high_60"]
        # Turnover, not share volume: a surge in a cheap name is not the same
        # money as a surge in an expensive one.
        turnover = close * frame["Volume"]
        frame["turnover_ratio"] = turnover / turnover.rolling(20).mean().shift(1)
        frame["gap_up"] = frame["Open"] > frame["High"].rolling(20).max().shift(1)
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True)
    out["calm_rank"] = out.groupby("Date")["atr_percent"].rank(pct=True)
    return out


def strong(data: pd.DataFrame) -> pd.Series:
    """The regime every candidate shares, so only the trigger differs."""
    return data["above_200"] & (data["momentum_rank"] >= 0.67)


CANDIDATES = {
    "breakout 20d on 2.5x volume (shipped)": shipped,
    "within 5% of the 52-week high":
        lambda d: strong(d) & d["near_52w_high"],
    "turnover surge 3x, no breakout":
        lambda d: strong(d) & (d["turnover_ratio"] >= 3.0) & ~d["breakout"],
    "gap above the 20-day high":
        lambda d: strong(d) & d["gap_up"],
    "breakout 60d on 2.5x volume":
        lambda d: strong(d) & d["breakout_60"] & (d["volume_ratio"] >= 2.5),
    "calmest third, no event":
        lambda d: strong(d) & (d["calm_rank"] <= 0.33),
}


def main() -> int:
    panel = add_features(build(load()))
    years = ((pd.to_datetime(panel["Date"]).max()
              - pd.to_datetime(panel["Date"]).min()).days / 365.25)
    base = shipped(panel).fillna(False)
    shipped_days = set(zip(panel[base]["Date"], panel[base]["Symbol"]))

    print(f"hold {HOLD} sessions, cost {COST:.2f}%, split at {SPLIT}\n")
    print("Overlap is the share of a candidate's trades the shipped rule "
          "already takes.\nA high overlap means it is the same strategy "
          "wearing a different name.\n")
    print(f"  {'trigger':<38} {'overlap':>7} {'/yr':>5} "
          f"{'train':>7} {'valid':>7} {'annual':>7} {'win':>6}")
    print(f"  {'-' * 80}")

    for label, rule in CANDIDATES.items():
        selected = panel[rule(panel).fillna(False)]
        if len(selected) < 100:
            print(f"  {label:<38} {len(selected):>5} trades - too few")
            continue
        pairs = set(zip(selected["Date"], selected["Symbol"]))
        overlap = len(pairs & shipped_days) / len(pairs) * 100

        lifts = {}
        for era, mask in (("train", selected["Date"] < SPLIT),
                          ("valid", selected["Date"] >= SPLIT)):
            era_rows = selected[mask]
            days = panel[panel["Date"].isin(era_rows["Date"])]
            days = days[days["Date"] < SPLIT] if era == "train" else days[days["Date"] >= SPLIT]
            if era_rows.empty or days.empty:
                lifts[era] = 0.0
                continue
            lifts[era] = (statistics.fmean(era_rows["forward"].tolist())
                          - statistics.fmean(days["forward"].tolist()))
        per_year = len(selected) / years
        wins = sum(1 for v in selected["forward"] if v > 0) / len(selected) * 100
        print(f"  {label:<38} {overlap:>6.0f}% {per_year:>5.0f} "
              f"{lifts['train']:>+6.2f}% {lifts['valid']:>+6.2f}% "
              f"{per_year * lifts['valid']:>+6.0f}% {wins:>5.1f}%")

    print("\nA candidate earns a place only if it clears three bars at once: "
          "positive in\nboth eras, overlap low enough to be adding trades "
          "rather than renaming them,\nand an annual figure worth the "
          "attention. Two of three is a no.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
