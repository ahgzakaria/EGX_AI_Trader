"""Did the portfolio's exit rules earn their place? Score what they said.

    venv/Scripts/python.exe scripts/research/score_portfolio_recommendations.py

`holdings/store.py` logs every actionable recommendation once per session with
its price, its plan version and the evidence that produced it. The page labels
the liquidity rules "قاعدة غير مُقاسة بعد" because none of that had been read
back. This reads it back.

## What is measured, fixed before running

An exit rule earns its place if the stocks it flagged **fell after it spoke**,
by more than two things:

* **the market.** Scoring against zero credits a rule for a fall everybody
  had. Every return here is a lift: the stock's return minus the median return
  of every symbol in the measured record over the same sessions -- the typical
  stock, which is the benchmark a holder of one name actually faces. EGX30 is
  reported beside it where the index record reaches that far; it is a
  cap-weighted thirty and it lagged the stock record by ten sessions when this
  was written, so it cannot be the primary benchmark.
* **the cost of acting.** Leaving and coming back costs the measured round trip
  for that symbol (`core.effective_cost`), or twice the billed per-side fee
  when that symbol has no measured cost. A rule whose median lift is smaller
  than its own cost is noise the account pays for.

The primary horizon is **10 sessions**, chosen before running: the exit plan's
own time stop is 20, and half of it is the shortest window over which "the
trend broke" can be said to have happened rather than to be a day's noise.
1, 5 and 20 are reported beside it as shape, not as a menu to pick a winner
from. Every rule is reported separately and none is pooled with another.

## What it cannot say

These recommendations were produced on sessions the user visited the page, not
on a schedule, so the sample is what it is: no rule is scored on a sample it
did not produce. A rule with a handful of rows is reported with its count and
nothing is concluded from it. This measures whether the *reading* was right,
not what the user did -- the log records advice, not trades, and the account's
own record is `data/portfolio.db`'s trades table.

A TRIM or RAISE_STOP is not an exit: banking part of a winner or tightening a
stop is scored the same way here (what the price did next), but a negative
lift after RAISE_STOP means the stop was tightened before a fall, which is the
rule working. The sign is read per action in the summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

from core.measured_benchmark import median_return           # noqa: E402

DB = PROJECT_ROOT / "data" / "portfolio.db"
OUT = PROJECT_ROOT / "reports" / "research" / "portfolio_recommendation_scores.csv"

HORIZONS = (1, 5, 10, 20)
PRIMARY = 10


def recommendations(database=DB):
    """Every logged recommendation, oldest first."""
    with sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT symbol, session_date, action, rule, urgency, price, "
            "price_basis, net_percent, evidence_json FROM recommendations "
            "ORDER BY session_date, symbol")]


def measured(symbols):
    """``(closes, index, panel)`` from the measured record.

    ``panel`` is every symbol the store holds, one column each, which is what
    the cross-section benchmark is taken from. ``index`` may be ``None`` or
    short: the EGX30 record lagged the stock record by ten sessions when this
    was written, so nothing here depends on it.
    """
    from core.measured_benchmark import close_panel
    from core.mubasher_live_history import (READY, mubasher_live_history,
                                            mubasher_live_index)

    closes = {}
    for symbol in sorted(symbols):
        frame, status, _ = mubasher_live_history(symbol, min_bars=30)
        if status == READY and frame is not None and not frame.empty:
            series = frame["Close"].astype(float)
            series.index = pd.to_datetime(series.index).normalize()
            closes[symbol] = series[~series.index.duplicated(keep="last")].sort_index()

    panel = close_panel()

    frame, status, _ = mubasher_live_index(min_bars=30)
    index = None
    if status == READY and frame is not None and not frame.empty:
        index = frame["Close"].astype(float)
        index.index = pd.to_datetime(index.index).normalize()
        index = index[~index.index.duplicated(keep="last")].sort_index()
    return closes, index, panel


#: The median symbol over the same sessions. One definition, in
#: ``core.measured_benchmark``, so two measurements of this project's rules can
#: never be scored against two different markets.
peer_return = median_return


def forward(series, session, horizon):
    """``(reference close, close ``horizon`` sessions later)`` or ``None``.

    The reference is the session's own close in the measured record -- not the
    price on the recommendation, which may have been read mid-session -- so
    every rule is scored on one basis.
    """
    if series is None or series.empty:
        return None
    day = pd.Timestamp(session).normalize()
    position = series.index.searchsorted(day, side="right") - 1
    if position < 0 or series.index[position] != day:
        return None                       # the symbol did not trade that session
    if position + horizon >= len(series):
        return None                       # not enough sessions have happened yet
    return float(series.iloc[position]), float(series.iloc[position + horizon])


def cost_of_acting(symbol, costs, fee_model):
    """The measured round trip for this symbol, or the billed fee twice."""
    from core.effective_cost import round_trip_for

    measured_cost = round_trip_for(costs, symbol)
    if measured_cost is not None:
        return float(measured_cost), "measured"
    return float(fee_model.percent_per_side) * 2.0, "billed"


def score(rows, closes, index, panel, costs, fee_model):
    """One scored row per recommendation, with a lift at every horizon."""
    scored = []
    for row in rows:
        symbol = str(row["symbol"]).strip().upper()
        series = closes.get(symbol)
        record = {
            "symbol": symbol,
            "session_date": row["session_date"],
            "action": row["action"],
            "rule": row["rule"],
            "urgency": row["urgency"],
            "net_percent_at_the_time": round(float(row["net_percent"] or 0.0), 2),
        }
        record["cost_percent"], record["cost_basis"] = cost_of_acting(
            symbol, costs, fee_model)
        record["cost_percent"] = round(record["cost_percent"], 3)

        status = "SCORED"
        for horizon in HORIZONS:
            pair = forward(series, row["session_date"], horizon)
            if pair is None:
                record[f"return_{horizon}"] = None
                record[f"lift_{horizon}"] = None
                record[f"index_lift_{horizon}"] = None
                if horizon <= PRIMARY:
                    status = "NO_DATA" if series is None else "PENDING"
                continue
            reference, later = pair
            ret = (later / reference - 1.0) * 100.0
            record[f"return_{horizon}"] = round(ret, 2)

            peer = peer_return(panel, row["session_date"], horizon)
            record[f"lift_{horizon}"] = (None if peer is None
                                         else round(ret - peer, 2))

            index_pair = forward(index, row["session_date"], horizon)
            if index_pair is None:
                record[f"index_lift_{horizon}"] = None
            else:
                index_reference, index_later = index_pair
                index_ret = (index_later / index_reference - 1.0) * 100.0
                record[f"index_lift_{horizon}"] = round(ret - index_ret, 2)
        record["status"] = status
        scored.append(record)
    return scored


def summarize(scored):
    """Per rule and action: what the primary horizon says, and whether it pays."""
    groups = defaultdict(list)
    for row in scored:
        if row[f"lift_{PRIMARY}"] is None:
            continue
        groups[(row["action"], row["rule"])].append(row)

    summary = []
    for (action, rule), rows in sorted(groups.items()):
        lifts = [row[f"lift_{PRIMARY}"] for row in rows]
        returns = [row[f"return_{PRIMARY}"] for row in rows]
        cost = median(row["cost_percent"] for row in rows)
        fell = sum(1 for value in lifts if value < 0)
        summary.append({
            "action": action,
            "rule": rule,
            "scored": len(rows),
            "median_lift": round(median(lifts), 2),
            "mean_lift": round(sum(lifts) / len(lifts), 2),
            "underperformed_after": f"{fell}/{len(rows)}",
            "median_return": round(median(returns), 2),
            "median_cost": round(cost, 2),
            # Worth acting on only if what it avoided is bigger than what
            # acting costs. Reported, never rounded away.
            "beats_its_cost": "yes" if -median(lifts) > cost else "no",
        })
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default=str(DB))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)

    from core.effective_cost import load_symbol_costs
    from core.environment import load_project_environment
    from holdings.book import load_fee_model

    load_project_environment()
    rows = recommendations(args.database)
    if not rows:
        print("No recommendations are logged yet.")
        return 1

    closes, index, panel = measured({str(r["symbol"]).strip().upper() for r in rows})
    if panel is None or panel.empty:
        print("The measured record is empty: there is no cross-section to score "
              "against. Refusing.")
        return 1
    costs = load_symbol_costs()
    scored = score(rows, closes, index, panel, costs, load_fee_model())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(scored[0].keys()))
        writer.writeheader()
        writer.writerows(scored)

    states = defaultdict(int)
    for row in scored:
        states[row["status"]] += 1
    print(f"logged recommendations : {len(rows)}")
    print(f"  {dict(states)}")
    print(f"  sessions {rows[0]['session_date']} .. {rows[-1]['session_date']}")
    print(f"\nat the primary horizon ({PRIMARY} sessions), "
          f"lift against the median symbol:")
    summary = summarize(scored)
    if not summary:
        print("  nothing has a full window yet.")
    else:
        frame = pd.DataFrame(summary)
        print(frame.to_string(index=False))
        print("\n  median_lift is the stock minus the median symbol in the "
              "measured record,")
        print("  over the same sessions. For an EXIT a negative lift is the "
              "rule being right.")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
