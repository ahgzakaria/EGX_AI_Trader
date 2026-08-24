r"""Does a value filter add anything to the volume breakout?

The frontier-market literature rates value the strongest factor of all, and
until now this was the one thing the strategy could not test: EODHD returns
HTTP 403 on fundamentals for this subscription. Yahoo carries them for EGX
under the .CA suffix -- the same tickers the universe already stores as
``engine_symbol``.

Yahoo was retired here as an *operational* provider because its daily candle
arrives hours to days late. That objection does not apply to this: annual
equity is published quarterly and is being read months after the fact.

Two things bound what this can prove, and both are stated in the output rather
than buried:

* **Coverage.** 39 of the 60 names the strategy trades have three or more
  years of equity together with a share count. The other 21 simply cannot be
  scored, so every result below describes a subset of the universe.
* **History.** The oldest annual period has a median of 2022-12; only four
  names reach before 2022. The momentum work that justified this strategy ran
  over fourteen years. This runs over about three, split at the same
  2024-01-01 boundary -- roughly two years of training and two of validation.

A weak test that returns a strong number is more dangerous than no test, so
the sample size behind every figure is printed beside it.

Point-in-time is enforced by a publication lag: an annual figure for a period
ending 2022-12-31 is treated as unknown until 120 days later. Egyptian annual
reports typically land two to three months after the year end; 120 days is
deliberately conservative. Without this the test would score a 2023 breakout
using a balance sheet nobody had yet, which is the purest form of the
look-ahead that makes a backtest lie.

One bias cannot be removed: Yahoo serves *restated* figures, not what was
first reported. That inflates results slightly and is not correctable here.

    venv\Scripts\python.exe scripts\research\value_factor.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import warnings

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from scripts.research.breakout_filters import COST, HOLD, SPLIT, build, load

#: Days after a fiscal period ends before its balance sheet is treated as known.
PUBLICATION_LAG_DAYS = 120

#: Minimum annual periods for a name to be scored at all.
MINIMUM_PERIODS = 3

CACHE = PROJECT_ROOT / "data" / "research" / "yahoo_equity_cache.json"


def fetch_equity(symbols) -> dict:
    """Annual equity and share count per symbol, from Yahoo, cached on disk.

    Cached because the panel is rebuilt often and the network is the slow part;
    the cache is research scratch, never a runtime data source.
    """
    if CACHE.is_file():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if set(cached) >= set(symbols):
            return cached

    import yfinance as yf

    out = {}
    for index, symbol in enumerate(sorted(symbols), 1):
        record = {"periods": {}}
        try:
            sheet = yf.Ticker(f"{symbol.split('.')[0]}.CA").balance_sheet
            if sheet is not None and not sheet.empty:
                equity_rows = [r for r in sheet.index if "Total Equity" in str(r)]
                share_rows = [r for r in sheet.index if "Ordinary Shares Number" in str(r)]
                if equity_rows and share_rows:
                    equity = sheet.loc[equity_rows[0]]
                    shares = sheet.loc[share_rows[0]]
                    for period in sheet.columns:
                        e, s = equity.get(period), shares.get(period)
                        if pd.notna(e) and pd.notna(s) and s > 0:
                            record["periods"][str(period)[:10]] = float(e) / float(s)
        except Exception as error:  # noqa: BLE001 - a probe always answers
            record["error"] = str(error)[:80]
        out[symbol] = record
        if index % 10 == 0:
            print(f"  fetched {index}/{len(symbols)}", flush=True)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out), encoding="utf-8")
    return out


def attach_book_value(panel: pd.DataFrame, equity: dict) -> pd.DataFrame:
    """Book value per share known *as of* each row's date.

    For every date the most recent annual figure whose publication lag has
    already elapsed is used -- never a later one, however much closer it sits
    to the truth.
    """
    panel = panel.copy()
    panel["Date"] = pd.to_datetime(panel["Date"])
    known = []
    for symbol, record in equity.items():
        for period, book_per_share in record.get("periods", {}).items():
            known.append({
                "Symbol": symbol,
                "known_from": pd.Timestamp(period) + pd.Timedelta(days=PUBLICATION_LAG_DAYS),
                "book_per_share": book_per_share,
            })
    if not known:
        return panel.assign(book_per_share=pd.NA)

    facts = pd.DataFrame(known).sort_values("known_from")
    panel = panel.sort_values("Date")
    merged = pd.merge_asof(
        panel, facts, left_on="Date", right_on="known_from", by="Symbol",
        direction="backward",
    )
    return merged


def describe(values, label: str) -> str:
    if not values:
        return f"  {label:<34} no trades"
    mean = statistics.fmean(values)
    wins = sum(1 for v in values if v > 0) / len(values) * 100
    return (f"  {label:<34} {mean:+6.2f}%   win {wins:4.1f}%   "
            f"n={len(values):5d}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="ignore the cache and re-fetch from Yahoo")
    args = parser.parse_args()

    if args.refresh and CACHE.is_file():
        CACHE.unlink()

    frames = load()
    print(f"Universe: {len(frames)} names")
    equity = fetch_equity(list(frames))
    scored = [s for s, r in equity.items() if len(r.get("periods", {})) >= MINIMUM_PERIODS]
    print(f"Scoreable ({MINIMUM_PERIODS}+ annual periods with a share count): "
          f"{len(scored)}/{len(frames)}")

    full = attach_book_value(build(frames), equity)
    full["price_to_book"] = full["Close"] / full["book_per_share"]

    # Counted before the scoreable subset is taken, or the share is tautological:
    # filtering first and then reporting "100% scoreable" says nothing.
    all_breakouts = full[full["breakout"] & (full["volume_ratio"] >= 2.5)]

    panel = full[full["price_to_book"] > 0].copy()
    panel["value_rank"] = panel.groupby("Date")["price_to_book"].rank(pct=True)
    with_value = panel[panel["breakout"] & (panel["volume_ratio"] >= 2.5)]

    print(f"Breakouts in the whole panel: {len(all_breakouts)}   "
          f"of which scoreable: {len(with_value)} "
          f"({len(with_value)/max(len(all_breakouts),1)*100:.0f}%)")
    print(f"Costs {COST:.2f}% a round trip, held {HOLD} sessions, "
          f"split at {SPLIT}, publication lag {PUBLICATION_LAG_DAYS} days.\n")

    for era, frame in (("TRAINING (before " + SPLIT + ")", with_value[with_value["Date"] < SPLIT]),
                       ("VALIDATION (from " + SPLIT + ")", with_value[with_value["Date"] >= SPLIT])):
        print(era)
        base = frame["forward"].tolist()
        print(describe(base, "every scoreable breakout"))
        # The benchmark: the same days, the whole scoreable cross-section.
        market = panel[panel["Date"].isin(frame["Date"])].dropna(subset=["value_rank"])
        print(describe(market["forward"].tolist(), "the universe on those days"))
        print()
        # Monotone response is the test that matters, and quintiles show it or
        # expose its absence. A factor that works only at one cut is a cut that
        # was fitted; a real one grades smoothly from cheap to dear and weakens
        # when inverted. This is the same test that admitted momentum.
        print("  by P/B quintile, cheapest first:")
        for quintile in range(5):
            low, high = quintile * 0.2, (quintile + 1) * 0.2
            mask = (frame["value_rank"] > low) & (frame["value_rank"] <= high)
            label = f"Q{quintile + 1} ({low:.1f}-{high:.1f})"
            print(describe(frame[mask]["forward"].tolist(), f"    {label}"))
        print()
        for name, mask in (
            ("cheapest half (P/B rank <= .50)", frame["value_rank"] <= 0.50),
            ("dearest half  (P/B rank >  .50)", frame["value_rank"] > 0.50),
        ):
            print(describe(frame[mask]["forward"].tolist(), name))
        print()

    print("Read the sample sizes before the averages. Yahoo's annual history "
          "begins around 2022 for most of this universe, so the validation era "
          "here is about two years -- against fourteen for the momentum work "
          "that justified the strategy. Yahoo also serves restated figures, "
          "not what was first reported, which flatters any result above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
