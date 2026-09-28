r"""Do classic chart patterns carry information on EGX? Measured, with rules fixed first.

    venv\Scripts\python.exe scripts\research\chart_patterns.py

The project has measured candlestick confirmation (removed), Fibonacci and
measured-move targets (failed), support/resistance scalping (lost) and a plain
twenty-day breakout confirmed by volume (the one rule that survived). It has
never measured a chart pattern -- a flag, a triangle, a double bottom, a
head-and-shoulders. This does, on the frozen Mubasher record the backtests read,
with the same yardstick every study here uses (``signal_scan``): the forward
return over the hold, net of each symbol's own measured round trip, as **lift**
over owning the liquid half of the universe on the same days, in two eras split
at 2023-01-01.

## Fixed before running

**The patterns**, each with the textbook definition and parameters not tuned:

* *bull flag* (continuation): a pole of at least +15% over ten sessions, then ten
  sessions of flag whose range is at most half the pole and which does not drift
  up more than 2%; triggers on the first close above the flag's high.
* *ascending triangle* (continuation): over forty sessions, at least two swing
  highs within 2% of the resistance and at least ten sessions apart, and a later
  swing low at least 2% above an earlier one; triggers on the first close above
  the resistance.
* *double bottom* (reversal): two swing lows within 3%, ten to sixty sessions
  apart, a rally of at least 10% between them, below the 50-session average at
  the first low; triggers on the first close above that rally's high within
  thirty sessions of the second low.
* *inverse head and shoulders* (reversal): three consecutive swing lows, the
  middle one at least 5% below both shoulders, the shoulders within 5% of each
  other, each leg at least five sessions and all three within eighty, below the
  50-session average at the left shoulder; triggers on the first close above the
  higher of the two peaks between them within thirty sessions.
* *double top* and *head and shoulders top*: the mirrors, above the 50-session
  average, triggering on the first close below the neckline. The program only
  buys, so these are tested as a warning -- a reason to exit or not to enter.

**No look-ahead.** A swing high or low is a bar that is the extreme of three
sessions on each side; it is used only from the third session after it, when it
could first have been seen. Every trigger reads bars up to its own close.

**The baseline a bullish pattern must also beat**: a plain first close above the
prior twenty-session high. CONFIRMED_VOLUME_BREAKOUT is built on that; a pattern
that does not beat it adds nothing the program does not already have.

**What survives**, at the primary hold of twenty sessions (ten is shown for shape):

* bullish: lift > 0 in both eras, Welch t >= 2 in both eras against the liquid
  base, at least 40 events in each era, and lift above the plain breakout's in
  both eras;
* bearish: lift < 0 in both eras, t <= -2 in both eras, at least 40 in each.

Events of one symbol overlap across a twenty-session hold, so t overstates the
independent evidence; the count of losing years is printed beside it.

**A close of zero is not a price.** The frozen Mubasher record holds 13 bars with
``Close == 0`` and real volume -- five on EGREF in 2019-20, and 2025-12-29 and
2026-02-10 on several symbols at once. The first run put an infinite forward
return into the base and left every lift in the first era undefined; those bars
are treated as missing (price columns set to NaN), which removes them and every
forward window that reaches one. Found by the run, applied to every row alike,
and no pattern's definition was touched.
"""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np                                                   # noqa: E402
import pandas as pd                                                  # noqa: E402

#: A swing point is the extreme of this many sessions on each side, and is
#: known this many sessions after it.
PIVOT_K = 3

FLAG_BARS, POLE_BARS = 10, 10
POLE_MIN_RISE = 0.15
FLAG_MAX_OF_POLE = 0.5
FLAG_MAX_DRIFT = 0.02

TRIANGLE_BARS = 40
TRIANGLE_TOLERANCE = 0.02
TRIANGLE_MIN_SEPARATION = 10
TRIANGLE_LOW_RISE = 0.02

DOUBLE_MIN_SEPARATION, DOUBLE_MAX_SEPARATION = 10, 60
DOUBLE_TOLERANCE = 0.03
DOUBLE_RALLY = 0.10

HS_HEAD_DEPTH = 0.05
HS_SHOULDER_TOLERANCE = 0.05
HS_MIN_LEG, HS_MAX_SPAN = 5, 80

BREAK_WITHIN = 30
TREND_WINDOW = 50

HOLDS = (10, 20)
PRIMARY_HOLD = 20
MIN_EVENTS = 40
MIN_T = 2.0

BULLISH = ("bull_flag", "ascending_triangle", "double_bottom", "inverse_head_shoulders")
BEARISH = ("double_top", "head_shoulders_top")
BASELINE = "plain_breakout_20"


# --- swing points -------------------------------------------------------------

def swing_points(values, kind, k=PIVOT_K):
    """Indices that are the strict extreme of ``k`` bars on each side."""
    v = np.asarray(values, dtype=float)
    points = []
    for j in range(k, len(v) - k):
        window = v[j - k:j + k + 1]
        if np.isnan(window).any():
            continue
        extreme = window.min() if kind == "low" else window.max()
        if v[j] == extreme and int((window == extreme).sum()) == 1:
            points.append(j)
    return points


def _first_break(closes, start, stop, level, above, floor=None, ceiling=None):
    """First index in [start, stop] closing through ``level``, or None.

    ``floor`` (for a bullish break) or ``ceiling`` (bearish) invalidates the
    pattern if a close goes through it first.
    """
    for t in range(max(start, 1), min(stop, len(closes) - 1) + 1):
        if floor is not None and closes[t] < floor:
            return None
        if ceiling is not None and closes[t] > ceiling:
            return None
        if above and closes[t] > level:
            return t
        if not above and closes[t] < level:
            return t
    return None


# --- the detectors: each returns a boolean array, True on the trigger bar -----

def bull_flag(frame):
    c = frame["Close"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    n = len(c)
    fired = np.zeros(n, dtype=bool)
    last = -10 ** 9
    for t in range(POLE_BARS + FLAG_BARS, n):
        pole_start, pole_end = t - FLAG_BARS - POLE_BARS, t - FLAG_BARS - 1
        if not c[pole_start] > 0:
            continue
        rise = c[pole_end] / c[pole_start] - 1.0
        if rise < POLE_MIN_RISE:
            continue
        flag_high = h[t - FLAG_BARS:t].max()
        flag_low = low[t - FLAG_BARS:t].min()
        if flag_high - flag_low > FLAG_MAX_OF_POLE * (c[pole_end] - c[pole_start]):
            continue
        if c[t - 1] > c[pole_end] * (1.0 + FLAG_MAX_DRIFT):
            continue
        if c[t] > flag_high and t - last > FLAG_BARS:
            fired[t] = True
            last = t
    return fired


def ascending_triangle(frame):
    c = frame["Close"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    n = len(c)
    highs, lows = swing_points(h, "high"), swing_points(low, "low")
    fired = np.zeros(n, dtype=bool)
    for t in range(TRIANGLE_BARS + PIVOT_K, n):
        start = t - TRIANGLE_BARS
        known = t - 1 - PIVOT_K                     # a swing seen before bar t
        resistance = h[start:t].max()
        if not (c[t] > resistance and c[t - 1] <= resistance):
            continue
        near = [j for j in highs if start <= j <= known
                and h[j] >= resistance * (1.0 - TRIANGLE_TOLERANCE)]
        if len(near) < 2 or near[-1] - near[0] < TRIANGLE_MIN_SEPARATION:
            continue
        rising = [j for j in lows if start <= j <= known]
        if len(rising) < 2 or low[rising[-1]] < low[rising[0]] * (1.0 + TRIANGLE_LOW_RISE):
            continue
        fired[t] = True
    return fired


def _trend(frame):
    return frame["Close"].rolling(TREND_WINDOW).mean().to_numpy(float)


def double_bottom(frame):
    c = frame["Close"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    trend = _trend(frame)
    fired = np.zeros(len(c), dtype=bool)
    lows = swing_points(low, "low")
    for a, b in zip(lows, lows[1:]):
        if not DOUBLE_MIN_SEPARATION <= b - a <= DOUBLE_MAX_SEPARATION:
            continue
        l1, l2 = low[a], low[b]
        if abs(l2 - l1) / l1 > DOUBLE_TOLERANCE:
            continue
        neckline = h[a:b + 1].max()
        if neckline < max(l1, l2) * (1.0 + DOUBLE_RALLY):
            continue
        if not (np.isfinite(trend[a]) and c[a] < trend[a]):
            continue
        t = _first_break(c, b + PIVOT_K, b + BREAK_WITHIN, neckline, above=True,
                         floor=min(l1, l2) * (1.0 - DOUBLE_TOLERANCE))
        if t is not None:
            fired[t] = True
    return fired


def double_top(frame):
    c = frame["Close"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    trend = _trend(frame)
    fired = np.zeros(len(c), dtype=bool)
    highs = swing_points(h, "high")
    for a, b in zip(highs, highs[1:]):
        if not DOUBLE_MIN_SEPARATION <= b - a <= DOUBLE_MAX_SEPARATION:
            continue
        h1, h2 = h[a], h[b]
        if abs(h2 - h1) / h1 > DOUBLE_TOLERANCE:
            continue
        neckline = low[a:b + 1].min()
        if neckline > min(h1, h2) * (1.0 - DOUBLE_RALLY):
            continue
        if not (np.isfinite(trend[a]) and c[a] > trend[a]):
            continue
        t = _first_break(c, b + PIVOT_K, b + BREAK_WITHIN, neckline, above=False,
                         ceiling=max(h1, h2) * (1.0 + DOUBLE_TOLERANCE))
        if t is not None:
            fired[t] = True
    return fired


def inverse_head_shoulders(frame):
    c = frame["Close"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    trend = _trend(frame)
    fired = np.zeros(len(c), dtype=bool)
    lows = swing_points(low, "low")
    for a, b, d in zip(lows, lows[1:], lows[2:]):
        if b - a < HS_MIN_LEG or d - b < HS_MIN_LEG or d - a > HS_MAX_SPAN:
            continue
        left, head, right = low[a], low[b], low[d]
        if head > min(left, right) * (1.0 - HS_HEAD_DEPTH):
            continue
        if abs(left - right) / left > HS_SHOULDER_TOLERANCE:
            continue
        if not (np.isfinite(trend[a]) and c[a] < trend[a]):
            continue
        neckline = max(h[a:b + 1].max(), h[b:d + 1].max())
        t = _first_break(c, d + PIVOT_K, d + BREAK_WITHIN, neckline, above=True,
                         floor=head)
        if t is not None:
            fired[t] = True
    return fired


def head_shoulders_top(frame):
    c = frame["Close"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    trend = _trend(frame)
    fired = np.zeros(len(c), dtype=bool)
    highs = swing_points(h, "high")
    for a, b, d in zip(highs, highs[1:], highs[2:]):
        if b - a < HS_MIN_LEG or d - b < HS_MIN_LEG or d - a > HS_MAX_SPAN:
            continue
        left, head, right = h[a], h[b], h[d]
        if head < max(left, right) * (1.0 + HS_HEAD_DEPTH):
            continue
        if abs(left - right) / left > HS_SHOULDER_TOLERANCE:
            continue
        if not (np.isfinite(trend[a]) and c[a] > trend[a]):
            continue
        neckline = min(low[a:b + 1].min(), low[b:d + 1].min())
        t = _first_break(c, d + PIVOT_K, d + BREAK_WITHIN, neckline, above=False,
                         ceiling=head)
        if t is not None:
            fired[t] = True
    return fired


def plain_breakout_20(frame):
    c = frame["Close"]
    level = frame["High"].rolling(20).max().shift(1)
    fresh = (c > level) & (c.shift(1) <= level.shift(1))
    return fresh.fillna(False).to_numpy(bool)


DETECTORS = {
    "bull_flag": bull_flag,
    "ascending_triangle": ascending_triangle,
    "double_bottom": double_bottom,
    "inverse_head_shoulders": inverse_head_shoulders,
    "double_top": double_top,
    "head_shoulders_top": head_shoulders_top,
    BASELINE: plain_breakout_20,
}


# --- measurement --------------------------------------------------------------

def without_zero_prices(panel):
    """Price columns set to NaN on any bar whose close is not positive.

    Kept as a row, so the hold is still counted in sessions; the NaN removes the
    bar from every forward window that touches it.
    """
    panel = panel.copy()
    bad = ~(panel["Close"] > 0)
    for column in ("Open", "High", "Low", "Close"):
        if column in panel:
            panel.loc[bad, column] = np.nan
    return panel


def mark_patterns(panel):
    """The panel with one boolean column per detector, per symbol, in date order."""
    frames = []
    for _symbol, frame in panel.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").reset_index(drop=True)
        for name, detector in DETECTORS.items():
            f[name] = detector(f)
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def welch_t(sample, base):
    sample, base = np.asarray(sample, float), np.asarray(base, float)
    if len(sample) < 2 or len(base) < 2:
        return float("nan")
    se = np.sqrt(sample.var(ddof=1) / len(sample) + base.var(ddof=1) / len(base))
    return float((sample.mean() - base.mean()) / se) if se else float("nan")


def era_stats(base, column, split):
    """Per era: (n, lift, t) of the rows where ``column`` fired, against the base."""
    out = {}
    for label, part in (("<", base[base["Date"] < split]), (">=", base[base["Date"] >= split])):
        chosen = part[part[column]]["net"].dropna()
        everyone = part["net"].dropna()
        out[label] = (len(chosen),
                      float(chosen.mean() - everyone.mean()) if len(chosen) else float("nan"),
                      welch_t(chosen, everyone))
    return out


def losing_years(base, column):
    chosen = base[base[column]].assign(year=lambda d: d["Date"].dt.year)
    counted = chosen.groupby("year")["net"].agg(["mean", "count"])
    counted = counted[counted["count"] >= 10]
    return int((counted["mean"] < 0).sum()), len(counted)


def verdict(name, stats, baseline_stats):
    """The registered rule, applied. Returns SURVIVES or the first reason it fails."""
    before, after = stats["<"], stats[">="]
    if min(before[0], after[0]) < MIN_EVENTS:
        return "too few events"
    if name in BEARISH:
        if not (before[1] < 0 and after[1] < 0):
            return "does not lag the market in both eras"
        if not (before[2] <= -MIN_T and after[2] <= -MIN_T):
            return f"|t| below {MIN_T:g} in an era"
        return "SURVIVES"
    if not (before[1] > 0 and after[1] > 0):
        return "does not beat the market in both eras"
    if not (before[2] >= MIN_T and after[2] >= MIN_T):
        return f"t below {MIN_T:g} in an era"
    if baseline_stats is not None and not (
            before[1] > baseline_stats["<"][1] and after[1] > baseline_stats[">="][1]):
        return "does not beat the plain breakout in both eras"
    return "SURVIVES"


def main(source="frozen_mubasher") -> int:
    from scripts.research.panel import load
    from scripts.research.signal_scan import (SPLIT, add_cross_section,
                                              costs_by_symbol, features)

    panel = load(source=source)
    print(f"panel: {source}, {panel['Symbol'].nunique()} symbols, "
          f"{panel['Date'].min().date()} .. {panel['Date'].max().date()}, "
          f"split at {SPLIT}")
    prices = panel[["Symbol", "Date", "Open", "High", "Low", "Close", "Volume"]]
    zero = int((~(prices["Close"] > 0)).sum())
    print(f"bars with a close that is not a price (set missing): {zero}")
    marked = mark_patterns(without_zero_prices(prices))
    costs = costs_by_symbol(marked["Symbol"].unique())

    for hold in HOLDS:
        data = add_cross_section(features(marked, hold))
        data["net"] = data["fwd"] - data["Symbol"].map(costs)
        base = data[data["rank_turnover_20"] >= 0.5]
        primary = " (primary)" if hold == PRIMARY_HOLD else ""
        print(f"\nhold {hold} sessions{primary}, liquid half, net of each symbol's "
              f"own round trip; lift against owning that half")
        print(f"{'pattern':<24}{'n <2023':>9}{'lift':>8}{'t':>7}"
              f"{'n >=2023':>10}{'lift':>8}{'t':>7}{'bad yrs':>9}  verdict")
        print("-" * 104)
        baseline_stats = era_stats(base, BASELINE, SPLIT)
        for name in (BASELINE, *BULLISH, *BEARISH):
            stats = era_stats(base, name, SPLIT)
            bad, years = losing_years(base, name)
            (n1, l1, t1), (n2, l2, t2) = stats["<"], stats[">="]
            verdict_text = ("reference" if name == BASELINE
                            else verdict(name, stats, baseline_stats))
            print(f"{name:<24}{n1:>9,}{l1:>+8.2f}{t1:>7.2f}{n2:>10,}{l2:>+8.2f}"
                  f"{t2:>7.2f}{bad:>5}/{years:<3}  {verdict_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
