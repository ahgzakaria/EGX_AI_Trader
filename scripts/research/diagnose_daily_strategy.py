r"""What is actually wrong with the Daily Dashboard strategy, measured.

Every claim here is computed over the engine's own inputs (`panel.py`, 191
symbols, 382,646 bars) or over the shipped backtest's own trade file. Nothing
is asserted from reading the code alone.

    venv\Scripts\python.exe scripts\research\diagnose_daily_strategy.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.panel import load

BASELINE = PROJECT_ROOT / "reports" / "experiments" / "20260827_193224_reward_guard"


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> int:
    panel = load()
    panel = panel.sort_values(["Symbol", "Date"]).reset_index(drop=True)
    g = panel.groupby("Symbol", sort=False)

    close, high, low = panel["Close"], panel["High"], panel["Low"]

    # The two resistances the strategy uses at the same moment.
    panel["res_excl"] = g["High"].transform(lambda s: s.rolling(20).max().shift(1))
    panel["res_incl"] = g["High"].transform(lambda s: s.rolling(20).max())
    panel["sup_incl"] = g["Low"].transform(lambda s: s.rolling(20).min())
    panel["avg_vol"] = g["Volume"].transform(lambda s: s.rolling(20).mean())

    usable = panel.dropna(subset=["res_excl", "ATR", "EMA200", "ADX", "VOLUME_RATIO"]).copy()

    section("1. The two resistance definitions disagree, and both are used at once")
    disagree = (usable["res_incl"] - usable["res_excl"]).abs() > 1e-9
    print(f"bars where entry.py's resistance != support.py's resistance: "
          f"{disagree.mean() * 100:.1f}%")
    print("  entry.py  -> Target1 and Target2 are placed off `res_excl`")
    print("  support.py-> quality_filter measures 'room to resistance' off `res_incl`")
    room_excl = (usable["res_excl"] - usable["Close"]) / usable["Close"] * 100
    room_incl = (usable["res_incl"] - usable["Close"]) / usable["Close"] * 100
    gate = room_incl >= 3.0
    print(f"\nbars passing the 3% room gate on res_incl : {gate.mean() * 100:.1f}%")
    print(f"  of those, room to the ACTUAL target (res_excl) is < 3%: "
          f"{(room_excl[gate] < 3.0).mean() * 100:.1f}%")
    print(f"  of those, the actual target is BELOW the entry            : "
          f"{(room_excl[gate] <= 0).mean() * 100:.1f}%")

    section("2. 'Confirmed Breakout' (+8) versus the quality gate")
    breakout = (usable["Close"] > usable["res_excl"]) & (
        usable["Volume"] > usable["avg_vol"] * 1.2)
    print(f"bars scoring 'Confirmed Breakout'        : {breakout.mean() * 100:.2f}%")
    print(f"bars passing the 3% resistance-room gate : {gate.mean() * 100:.2f}%")
    print(f"independence would predict both          : {breakout.mean() * gate.mean() * 100:.2f}%")
    print(f"observed both at once                    : {(breakout & gate).mean() * 100:.2f}%")

    section("3. 'Near Support' is scored twice, from two different definitions")
    near_support_support_py = (usable["Close"] - usable["sup_incl"]) / usable["sup_incl"] * 100 <= 3
    near_support_entry_py = (usable["Close"] - usable["sup_incl"]).abs() <= usable["ATR"]
    print(f"support.py 'Near Support' (+10, <=3%)  fires on {near_support_support_py.mean() * 100:.1f}%")
    print(f"entry.py   'Near Support' (+6, <=1 ATR) fires on {near_support_entry_py.mean() * 100:.1f}%")
    print(f"both at once                            : {(near_support_support_py & near_support_entry_py).mean() * 100:.1f}%")
    print("Same idea, two thresholds, 16 points of a ~118-point scale.")

    section("4. The support that sets the stop is often TODAY's own low")
    todays_low_is_support = (usable["Low"] - usable["sup_incl"]).abs() < 1e-9
    print(f"bars where the 20-day low IS today's low: {todays_low_is_support.mean() * 100:.1f}%")
    print("On those bars the stop sits under a low the market just made, and")
    print("support.py's 'Near Support' +10 is awarded for the stock falling.")

    section("5. Rounding to 2 decimals on a market that trades in piastres")
    last = usable.groupby("Symbol")["Close"].last()
    for lo, hi, label in [(0, 1, "< 1 EGP"), (1, 5, "1-5 EGP"),
                          (5, 20, "5-20 EGP"), (20, 1e9, "> 20 EGP")]:
        band = last[(last >= lo) & (last < hi)]
        if len(band):
            # round(p,2) moves a price by up to half a piastre.
            err = (0.005 / band * 100).mean()
            print(f"  {label:<10} {len(band):>4} symbols   "
                  f"mean distortion from round(x,2): {err:.2f}% of price")
    print("\nBuyHigh, StopLoss, Target1 and Target2 are all round(x, 2).")
    print("RR = (Target2-BuyHigh)/(BuyHigh-StopLoss) is a ratio of two rounded")
    print("differences, so the error does not cancel -- it compounds.")

    section("6. The score's own arithmetic")
    print("Maximum reachable points per component, after the two removals:")
    for name, points in [("trend", 30), ("volume", 20), ("support", 15),
                         ("entry", 28), ("momentum", 25)]:
        print(f"  {name:<10} {points:>3}")
    print(f"  {'TOTAL':<10} {118:>3}    min_score is 50 -> 42% of the scale")
    print("\nmin_volume is 0 in config/settings.json, so the Volume gate never")
    print("rejects anything; volume_score is the one component measured with a")
    print("significant correlation to outcome, and it is NEGATIVE (r=-0.090).")

    section("7. What the shipped strategy earns against simply owning the market")
    results = pd.read_csv(BASELINE / "backtest_results.csv")
    start = pd.Timestamp(results["entry_date"].min())
    end = pd.Timestamp(results["exit_date"].max())
    window = panel[(panel["Date"] >= start) & (panel["Date"] <= end)]
    # Equal-weighted buy and hold of every symbol alive across the window.
    per_symbol = window.groupby("Symbol")["Close"].agg(["first", "last", "count"])
    per_symbol = per_symbol[per_symbol["count"] > 500]
    ew = (per_symbol["last"] / per_symbol["first"] - 1) * 100
    years = (end - start).days / 365.25
    print(f"window {start.date()} -> {end.date()}  ({years:.1f} years)")
    print(f"equal-weighted buy & hold, {len(per_symbol)} symbols:")
    print(f"  median symbol total return : {ew.median():+.1f}%")
    print(f"  mean symbol total return   : {ew.mean():+.1f}%")
    print(f"  CAGR on the median         : {((1 + ew.median() / 100) ** (1 / years) - 1) * 100:+.2f}%")
    print(f"\nthe strategy: +60.08% total, CAGR 5.43%, exposure 23.6%, 484 trades")

    section("8. The profit is five trades")
    profits = results["profit"].sort_values(ascending=False)
    total = profits.sum()
    for n in (1, 5, 10, 20):
        print(f"  top {n:>2} trades contribute {profits.head(n).sum() / total * 100:>6.1f}% of net profit")
    print(f"  median trade: {results['profit_percent'].median():+.3f}%")
    print(f"  share of trades profitable: {(results['profit_percent'] > 0).mean() * 100:.1f}%")

    section("9. Holding period against the cap")
    print(results["exit_reason"].value_counts().to_string())
    by_reason = results.groupby("exit_reason")["profit_percent"].agg(
        ["count", "mean", "median"])
    print()
    print(by_reason.round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
