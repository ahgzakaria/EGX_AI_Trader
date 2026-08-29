r"""Is there anything in this data worth trading, once the toll is charged?

The shipped strategy clears its costs by about a sixth of a percent per round
trip and returns 5.43% a year while the median EGX name returned 17.5%. Before
building anything new, this asks the only question that matters: over the
engine's own inputs, does any computable condition beat *owning the same
universe on the same days*, in both eras, net of that symbol's own spread?

"Lift" is always against the equal-weighted universe on the identical dates, so
the market's own drift -- which in EGP terms is enormous and is not an edge --
cancels out.

Rules obeyed throughout:

* Every rolling window is shifted by one bar where it defines a threshold, so
  today never sets the level it is then measured against.
* ``Open`` is never read. It is carried forward from the previous close in
  96-98% of bars and is not a real price.
* Costs are the per-symbol measured round trip the backtest charges, not a flat
  rate.

    venv\Scripts\python.exe scripts\research\signal_scan.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from backtesting.costs import spread_table
from scripts.research.panel import load

#: Broker contract note (0.1819% per side) + slippage (0.05% per side), plus the
#: symbol's own measured spread charged once. Same components as
#: `backtesting/costs.py`.
FIXED_ROUND_TRIP = (0.001819 + 0.0005) * 2 * 100
UNMEASURED_SPREAD = 0.927

#: Two eras. The split leaves the 2024 devaluation and the 2025-26 run in the
#: half that no rule was chosen on.
SPLIT = "2023-01-01"

MONTH = 21


def costs_by_symbol(symbols):
    table = spread_table()
    return {s: FIXED_ROUND_TRIP + table.get(s.upper(), UNMEASURED_SPREAD)
            for s in symbols}


def features(panel: pd.DataFrame, hold: int) -> pd.DataFrame:
    frames = []
    for _symbol, frame in panel.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close, high, low, volume = f["Close"], f["High"], f["Low"], f["Volume"]

        f["above_200"] = close > close.rolling(200).mean()
        f["above_50"] = close > close.rolling(50).mean()
        f["high_252"] = high.rolling(252).max().shift(1)
        f["pct_of_52w_high"] = close / f["high_252"] * 100
        f["high_20"] = high.rolling(20).max().shift(1)
        f["high_60"] = high.rolling(60).max().shift(1)
        f["breakout_20"] = close > f["high_20"]
        f["breakout_60"] = close > f["high_60"]

        f["mom_12_1"] = (close.shift(MONTH) / close.shift(12 * MONTH) - 1) * 100
        f["mom_6_1"] = (close.shift(MONTH) / close.shift(6 * MONTH) - 1) * 100
        f["mom_3_0"] = (close / close.shift(3 * MONTH) - 1) * 100
        f["mom_1_0"] = (close / close.shift(MONTH) - 1) * 100

        turnover = close * volume
        f["turnover_20"] = turnover.rolling(20).mean()
        f["turnover_ratio"] = turnover / turnover.rolling(20).mean().shift(1)

        previous = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - previous).abs(), (low - previous).abs()],
            axis=1).max(axis=1)
        f["atrp"] = true_range.rolling(14).mean() / close * 100
        f["ret_vol_60"] = close.pct_change().rolling(60).std() * 100

        f["fwd"] = (close.shift(-hold) / close - 1) * 100
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


def add_cross_section(panel: pd.DataFrame) -> pd.DataFrame:
    for column in ("mom_12_1", "mom_6_1", "mom_3_0", "mom_1_0",
                   "turnover_20", "atrp", "pct_of_52w_high"):
        panel[f"rank_{column}"] = panel.groupby("Date")[column].rank(pct=True)
    return panel.merge(market_index(panel), left_on="Date", right_index=True,
                       how="left")


def market_index(panel: pd.DataFrame) -> pd.DataFrame:
    """An equal-weighted index of the universe, and where it sits.

    Built by compounding the cross-sectional *mean daily return*, not by
    averaging the cross-sectional *price*. Averaging prices makes the level
    depend on which symbols happen to have data on a given date and on how
    expensive they are, so a name entering the panel at 300 EGP moves the
    "index" by more than the whole market moving 1%. There is no EGX30 series in
    this panel, so the cross-section has to be the index -- but it has to be a
    return index.
    """
    panel = panel.sort_values(["Symbol", "Date"])
    returns = panel.groupby("Symbol", sort=False)["Close"].pct_change()
    daily = returns.groupby(panel["Date"]).mean().sort_index()
    level = (1 + daily.fillna(0)).cumprod()
    return pd.DataFrame({
        "market_above_50": level > level.rolling(50).mean(),
        "market_above_200": level > level.rolling(200).mean(),
        "market_level": level,
    })


def report(panel, rules, hold, liquid_only=True):
    base = panel
    if liquid_only:
        base = panel[panel["rank_turnover_20"] >= 0.5]
    train_all = base[base["Date"] < SPLIT]["net"]
    valid_all = base[base["Date"] >= SPLIT]["net"]
    scope = "top-half turnover" if liquid_only else "all names"
    print(f"\nhold {hold} bars, {scope}, net of each symbol's own round trip")
    print(f"owning everything: train {train_all.mean():+.2f}%  "
          f"validation {valid_all.mean():+.2f}%   "
          f"(n {len(train_all):,} / {len(valid_all):,})")
    print(f"{'rule':<34}{'train n':>9}{'lift':>8}{'valid n':>9}{'lift':>8}"
          f"{'win%':>7}{'badyr':>8}")
    print("-" * 84)
    for name, rule in rules.items():
        selected = base[rule(base)]
        tr = selected[selected["Date"] < SPLIT]["net"]
        te = selected[selected["Date"] >= SPLIT]["net"]
        if len(te) < 40 or len(tr) < 40:
            print(f"{name:<34}{len(tr):>9,}{'--':>8}{len(te):>9,}{'too few':>8}")
            continue
        years = selected.assign(year=selected["Date"].dt.year).groupby("year")["net"]
        counted = years.agg(["mean", "count"])
        counted = counted[counted["count"] >= 15]
        bad = int((counted["mean"] < 0).sum())
        print(f"{name:<34}{len(tr):>9,}{tr.mean() - train_all.mean():>+8.2f}"
              f"{len(te):>9,}{te.mean() - valid_all.mean():>+8.2f}"
              f"{(te > 0).mean() * 100:>7.0f}{bad:>4}/{len(counted):<3}")


def main() -> int:
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())

    for hold in (20, 40):
        data = features(panel, hold)
        data = add_cross_section(data)
        data = data.dropna(subset=["fwd", "mom_12_1", "above_200", "atrp",
                                   "rank_turnover_20"])
        data["net"] = data["fwd"] - data["Symbol"].map(costs)

        rules = {
            "market above its own 200d": lambda d: d["market_above_200"].fillna(False),
            "price above 200d": lambda d: d["above_200"],
            "above 200d + mkt above 200d": lambda d: d["above_200"] & d["market_above_200"].fillna(False),
            "12-1 momentum top third": lambda d: d["rank_mom_12_1"] >= 0.67,
            "12-1 momentum top decile": lambda d: d["rank_mom_12_1"] >= 0.90,
            "6-1 momentum top third": lambda d: d["rank_mom_6_1"] >= 0.67,
            "3-0 momentum top third": lambda d: d["rank_mom_3_0"] >= 0.67,
            "1-0 momentum top third": lambda d: d["rank_mom_1_0"] >= 0.67,
            "1-0 momentum bottom third": lambda d: d["rank_mom_1_0"] <= 0.33,
            "within 5% of 52w high": lambda d: d["pct_of_52w_high"] >= 95,
            "20d breakout": lambda d: d["breakout_20"],
            "20d breakout + vol 2.5x": lambda d: d["breakout_20"] & (d["VOLUME_RATIO"] >= 2.5),
            "60d breakout": lambda d: d["breakout_60"],
            "turnover surge 3x, no breakout": lambda d: (d["turnover_ratio"] >= 3) & ~d["breakout_20"],
            "calm third by ATR%": lambda d: d["rank_atrp"] <= 0.33,
            "wild third by ATR%": lambda d: d["rank_atrp"] >= 0.67,
            "ADX >= 25": lambda d: d["ADX"] >= 25,
            "RSI 45-65": lambda d: d["RSI"].between(45, 65),
            "MACD > signal > 0": lambda d: (d["MACD"] > d["MACD_Signal"]) & (d["MACD"] > 0),
            "shipped-like gate stack": lambda d: (
                d["above_200"] & (d["EMA20"] > d["EMA50"]) & (d["ADX"] >= 21)
                & (d["VOLUME_RATIO"] >= 1.0) & (d["ATR_PERCENT"] >= 1.5)),
        }
        report(data, rules, hold, liquid_only=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
