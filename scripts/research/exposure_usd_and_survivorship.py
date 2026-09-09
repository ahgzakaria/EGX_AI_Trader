r"""Tasks B and C of the exposure investigation, plus the breakout module's line.

**B — restate in USD.** Every figure this project publishes is nominal EGP across
a decade in which the currency lost most of its value, so "+586% buy-and-hold"
and "+60% strategy" are both quoted in a unit that shrank underneath them.
`EGP=X` gives the daily rate. Egyptian CPI is deliberately out of scope here and
recorded as a limit: the exchange rate carries most of the effect and needs no
external source.

**C — bound the survivorship bias without prices.** The delisted snapshot names
65 EGX symbols and carries no price history for them, and only one has a cached
series. Rather than chase the prices, the benchmark is rebuilt twice: once with
every delisted name written to -100%, and once with each performing at the
surviving pool's median. The truth is inside that range.

**The breakout module.** `reports/phase10_breakout_trades.csv` is the one
completed backtest outside `reports/experiments/`, and it carries three modes.
Each gets the same treatment as an archived run so question 3 can be answered.

    venv\Scripts\python.exe scripts\research\exposure_usd_and_survivorship.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from exposure_vs_drift import (buy_and_hold, eligible_universe,   # noqa: E402
                               prices_for, round_trip)

DELISTED = (PROJECT_ROOT / "data" / "universe" / "snapshots"
            / "eodhd_egx_delisted_20260730T203942Z.json")
BREAKOUT_TRADES = PROJECT_ROOT / "reports" / "phase10_breakout_trades.csv"
OUT_DIR = PROJECT_ROOT / "reports" / "audits" / "strategies"

#: The shipped daily run's window, the one DAILY_STRATEGY_DIAGNOSIS §2 quotes.
WINDOW = (pd.Timestamp("2017-08-03"), pd.Timestamp("2026-06-28"))


# --- B: the currency ---------------------------------------------------------

def usd_rate() -> pd.Series | None:
    """USD per EGP, daily. None when the rate cannot be fetched."""

    import yfinance as yf
    for ticker in ("EGP=X", "USDEGP=X"):
        try:
            frame = yf.download(ticker, start="2016-01-01", end="2026-09-11",
                                interval="1d", auto_adjust=False,
                                progress=False, threads=False)
        except Exception:                                        # noqa: BLE001
            continue
        if frame is None or not len(frame):
            continue
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        # These tickers quote EGP per USD. A number near 50 in 2026 confirms it.
        egp_per_usd = frame["Close"].dropna()
        if len(egp_per_usd) and egp_per_usd.iloc[-1] > 5:
            return egp_per_usd
    return None


def in_usd(egp_return_percent: float, rate: pd.Series,
           start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Convert an EGP total return into USD over the same window."""

    window = rate.loc[(rate.index >= start) & (rate.index <= end)]
    if len(window) < 2:
        return {}
    first, last = float(window.iloc[0]), float(window.iloc[-1])
    # EGP per USD rose, so a pound bought fewer dollars at the end.
    fx_factor = first / last
    growth_egp = 1 + egp_return_percent / 100.0
    growth_usd = growth_egp * fx_factor
    years = (end - start).days / 365.25
    return {
        "egp_per_usd_start": round(first, 3),
        "egp_per_usd_end": round(last, 3),
        "currency_loss_pct": round((fx_factor - 1) * 100, 2),
        "usd_total_return": round((growth_usd - 1) * 100, 2),
        "usd_cagr": (round((growth_usd ** (1 / years) - 1) * 100, 2)
                     if years > 0 and growth_usd > 0 else None),
    }


# --- C: the names that are gone ---------------------------------------------

def delisted_codes() -> list[str]:
    if not DELISTED.exists():
        return []
    payload = json.loads(DELISTED.read_text(encoding="utf-8"))
    return [str(r.get("Code")) for r in payload.get("rows", []) if r.get("Code")]


def survivorship_range(surviving: dict, delisted_count: int) -> dict:
    """The benchmark rebuilt under both assumptions about the missing names.

    Worst case writes every delisted name to -100% from the window start. That
    is deliberately harsher than reality -- a delisting is not always a wipeout,
    and some of these are mergers -- so it is a bound, not an estimate.
    """

    survivors = surviving.get("names")
    mean = surviving.get("total_return")
    median = surviving.get("median_name")
    if not survivors or mean is None or median is None:
        return {}
    total = survivors + delisted_count
    return {
        "surviving_names": survivors,
        "delisted_names": delisted_count,
        "reported_equal_weight": round(mean, 2),
        "worst_case_equal_weight": round((mean * survivors + (-100.0) * delisted_count)
                                         / total, 2),
        "neutral_case_equal_weight": round((mean * survivors + median * delisted_count)
                                           / total, 2),
    }


# --- the breakout module -----------------------------------------------------

def breakout_modes() -> pd.DataFrame:
    if not BREAKOUT_TRADES.exists():
        return pd.DataFrame()
    frame = pd.read_csv(BREAKOUT_TRADES, encoding="utf-8-sig")
    rows = []
    universe = eligible_universe()
    for mode, group in frame.groupby("Mode"):
        group = group.dropna(subset=["EntryDate", "ExitDate", "Symbol"])
        if not len(group):
            continue
        start = pd.to_datetime(group["EntryDate"]).min()
        end = pd.to_datetime(group["ExitDate"]).max()
        traded = sorted(set(group["Symbol"].astype(str)))
        profit = float(group["PortfolioProfit"].sum())
        capital = 100_000.0
        total = profit / capital * 100
        years = (end - start).days / 365.25
        own = buy_and_hold(traded, start, end)
        market = buy_and_hold(universe, start, end)
        rows.append({
            "mode": mode, "trades": len(group),
            "start": start.date(), "end": end.date(), "years": round(years, 2),
            "total_return": round(total, 2),
            "cagr": (round(((1 + total / 100) ** (1 / years) - 1) * 100, 2)
                     if years > 0 and total > -100 else None),
            "traded_names": len(traded),
            "bh_own": own.get("total_return"),
            "bh_own_median_name": own.get("median_name"),
            "bh_market": market.get("total_return"),
            "vs_own": (round(total - own["total_return"], 2)
                       if own.get("total_return") is not None else None),
            "vs_market": (round(total - market["total_return"], 2)
                          if market.get("total_return") is not None else None),
        })
    return pd.DataFrame(rows)


def main() -> int:
    universe = eligible_universe()
    prices_for(universe)
    start, end = WINDOW

    print("=" * 78)
    print("THE BREAKOUT MODULE, SAME TREATMENT AS THE ARCHIVED RUNS")
    print("=" * 78)
    modes = breakout_modes()
    if len(modes):
        print(modes.to_string(index=False))
        modes.to_csv(OUT_DIR / "exposure_breakout_modes.csv", index=False)
    else:
        print("  reports/phase10_breakout_trades.csv not readable - reported as None")

    print()
    print("=" * 78)
    print("TASK C - SURVIVORSHIP, BOUNDED RATHER THAN CHASED")
    print("=" * 78)
    codes = delisted_codes()
    print(f"  delisted names in the snapshot: {len(codes)}")
    engine_codes = [f"{c}.CA" for c in codes]
    have_prices = [c for c in engine_codes
                   if prices_for(universe) is not None
                   and c in prices_for(universe).columns]
    print(f"  of those, with prices in hand: {len(have_prices)} "
          f"-> the other {len(codes) - len(have_prices)} are bounded, not measured")

    market = buy_and_hold(universe, start, end)
    bounds = survivorship_range(market, len(codes) - len(have_prices))
    for key, value in bounds.items():
        print(f"    {key:<28} {value}")

    print()
    print("=" * 78)
    print("TASK B - THE SAME NUMBERS IN USD")
    print("=" * 78)
    rate = usd_rate()
    if rate is None:
        print("  EGP=X unavailable - every USD figure reported as None")
        return 0

    own_shipped = buy_and_hold(
        sorted(set(pd.read_csv(
            PROJECT_ROOT / "reports" / "experiments"
            / "20260827_193224_reward_guard" / "backtest_results.csv",
            encoding="utf-8-sig")["symbol"].astype(str))), start, end)

    lines = [
        ("daily strategy, shipped (reward_guard)", 60.08),
        ("best archived run (spread1.0)", 75.67),
        ("worst archived run (code_defaults_config)", -36.23),
        ("buy-and-hold, names the strategy traded", own_shipped.get("total_return")),
        ("buy-and-hold, full universe equal weight", market.get("total_return")),
        ("buy-and-hold, median name (audit §2 figure)", market.get("median_name")),
        ("survivorship worst case, full universe", bounds.get("worst_case_equal_weight")),
    ]
    sample = in_usd(0.0, rate, start, end)
    print(f"  EGP per USD {sample['egp_per_usd_start']} -> {sample['egp_per_usd_end']}"
          f"   a pound at the end buys {abs(sample['currency_loss_pct']):.1f}% less")
    print()
    print(f"  {'':<44}{'EGP total':>12}{'USD total':>12}{'USD CAGR':>11}")
    usd_rows = []
    for label, value in lines:
        if value is None:
            print(f"  {label:<44}{'None':>12}{'None':>12}{'None':>11}")
            usd_rows.append({"line": label, "egp_total": None,
                             "usd_total": None, "usd_cagr": None})
            continue
        converted = in_usd(float(value), rate, start, end)
        print(f"  {label:<44}{value:>11.2f}%{converted['usd_total_return']:>11.2f}%"
              f"{converted['usd_cagr']:>10.2f}%")
        usd_rows.append({"line": label, "egp_total": value,
                         "usd_total": converted["usd_total_return"],
                         "usd_cagr": converted["usd_cagr"]})
    pd.DataFrame(usd_rows).to_csv(OUT_DIR / "exposure_usd.csv", index=False)
    print(f"\n  wrote {(OUT_DIR / 'exposure_usd.csv').relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
