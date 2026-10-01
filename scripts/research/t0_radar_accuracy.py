r"""How accurate is the T+0 radar? Replayed session by session on the measured record.

    venv\Scripts\python.exe scripts\research\t0_radar_accuracy.py
    venv\Scripts\python.exe scripts\research\t0_radar_accuracy.py --sessions 500

The radar makes one prediction: the names it ranks first will move more in the
next session than the rest of its list. This replays it on every session of the
window, as if it had run after each close, and scores that prediction --
the rank correlation between the score and the next session's range, and how
the first ten did against the rest. It also asks the question the radar does
not claim to answer -- does anything on it say which way a name will close --
because that is the question a reader will put to it.

It mirrors ``t0_radar.radar`` rather than calling it per session (a few hundred
thousand calls), and proves the mirror first: on the last session its scores are
compared with the radar's own.

Limits, stated where they bite:

* the round trip is each name's cost banked today, applied to every past
  session. The bank shows cost is a property of the name (r = 0.836 between the
  halves of its sessions), but in an older year a name's cost was not this one;
* the record carries no traded open before the minute store's window, so the
  next session is judged high to low and close to close, never from the open.

Read-only. Writes nothing.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

TOP = 10
MIN_CANDIDATES = 20

#: The first version's 0-100 score, kept here so the replay can still compare it
#: with what replaced it. ``t0_radar.radar`` no longer carries these.
V1_POINTS = {"room": 40.0, "in_play": 30.0, "expansion": 20.0, "consistency": 10.0}
V1_FULL_ROOM, V1_FULL_RELATIVE_TURNOVER, V1_FULL_EXPANSION = 8.0, 2.5, 2.0


def load_frames():
    from core.universe import active_symbols
    from sector_flow import measured_turnover

    frames = {}
    for symbol in sorted(active_symbols()):
        frame = measured_turnover.frame_for(f"{symbol}.CA")
        if frame is None or frame.empty:
            continue
        frame = frame[pd.to_numeric(frame["Close"], errors="coerce") > 0]
        frames[symbol] = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frames


def build_panel(frames, costs):
    """Every symbol on every session, with the radar's inputs and the next session."""

    from t0_radar import radar as R

    counts = pd.Series(np.concatenate([f.index.values for f in frames.values()])).value_counts()
    calendar = pd.DatetimeIndex(sorted(counts[counts >= R.SESSION_QUORUM].index))
    next_session = pd.Series(list(calendar[1:]) + [pd.NaT], index=calendar)

    parts = []
    for symbol, f in frames.items():
        cost = costs.get(symbol, np.nan)
        close, high, low = f["Close"], f["High"], f["Low"]
        rng = (high - low) / close.shift(1) * 100
        prior = rng.shift(1)
        base = prior.rolling(R.BASE_WINDOW, min_periods=R.BASE_WINDOW).median()
        bars = pd.DataFrame({
            "close": close, "high": high, "low": low, "range": rng, "base": base,
            "mean5": rng.rolling(5, min_periods=5).mean(),
            "room_days": (prior > R.ROOM_DAY_MULTIPLE * cost).astype(float)
                         .rolling(R.BASE_WINDOW, min_periods=R.BASE_WINDOW).sum(),
            "action": ((close.pct_change() * 100).abs() > R.DAILY_LIMIT_PERCENT)
                      .astype(float).rolling(R.CORPORATE_ACTION_WINDOW, min_periods=1).max(),
            "bars": np.arange(1, len(f) + 1),
            "ret": close.pct_change() * 100,
            "position": (close - low) / (high - low).replace(0, np.nan),
            "dist20": (close / close.rolling(20).mean() - 1) * 100,
            "next_date": pd.Series(f.index, index=f.index).shift(-1),
            "next_high": high.shift(-1), "next_low": low.shift(-1),
            "next_close": close.shift(-1),
        })
        panel = bars.reindex(calendar)
        panel["traded"] = panel.index.isin(f.index)
        flow = pd.to_numeric(f["Turnover"], errors="coerce").reindex(calendar).fillna(0.0)
        panel["turnover"] = flow
        panel["avg_prior"] = flow.shift(1).rolling(R.BASE_WINDOW, min_periods=R.BASE_WINDOW).mean()
        panel["avg_recent"] = flow.rolling(R.BASE_WINDOW, min_periods=R.BASE_WINDOW).mean()
        # The outcome counts only when the name traded on the very next session.
        panel["next_valid"] = panel["next_date"] == next_session
        panel["cost"] = cost
        panel["symbol"] = symbol
        parts.append(panel)
    long = pd.concat(parts).rename_axis("date").reset_index()

    long["liquid"] = (long["traded"] & (long["bars"] >= R.MIN_SESSIONS)
                      & (long["action"] == 0) & (long["range"] > 0)
                      & (long["avg_recent"] >= R.MIN_TURNOVER_EGP))
    long["room"] = long["base"] / long["cost"]
    long["candidate"] = long["liquid"] & long["cost"].notna() & (long["room"] >= R.MIN_ROOM_MULTIPLE)
    long["rel"] = long["turnover"] / long["avg_prior"].replace(0, np.nan)
    long["expansion"] = long["range"] / long["base"]

    def clip(x, lo, hi):
        return ((x - lo) / (hi - lo)).clip(0, 1).fillna(0)

    P = V1_POINTS
    long["score"] = (P["room"] * clip(long["room"], R.MIN_ROOM_MULTIPLE, V1_FULL_ROOM)
                     + P["in_play"] * clip(long["rel"], 1.0, V1_FULL_RELATIVE_TURNOVER)
                     + P["expansion"] * clip(long["expansion"], 1.0, V1_FULL_EXPANSION)
                     + P["consistency"] * long["room_days"] / R.BASE_WINDOW)
    # The v1 composite with its room term read from the last five sessions
    # rather than the median of twenty -- the change the first replay pointed to.
    long["room5"] = long["mean5"] / long["cost"]
    long["score_v2"] = (P["room"] * clip(long["room5"], R.MIN_ROOM_MULTIPLE, V1_FULL_ROOM)
                        + P["in_play"] * clip(long["rel"], 1.0, V1_FULL_RELATIVE_TURNOVER)
                        + P["expansion"] * clip(long["expansion"], 1.0, V1_FULL_EXPANSION)
                        + P["consistency"] * long["room_days"] / R.BASE_WINDOW)
    long["next_range"] = (long["next_high"] - long["next_low"]) / long["close"] * 100
    long["next_change"] = (long["next_close"] / long["close"] - 1) * 100
    long.loc[~long["next_valid"], ["next_range", "next_change"]] = np.nan
    return calendar, long


def parity(long, session):
    """The mirror against the radar itself, on the last session.

    The radar ranks by the five-session mean range, which the replay calls
    ``mean5``; the two have to agree name by name before anything else here is
    worth reading.
    """

    from t0_radar import radar

    result = radar.build()
    mine = long[(long["date"] == session) & long["candidate"]].set_index("symbol")["mean5"]
    theirs = result.candidates.set_index("symbol")["score"]
    both = mine.index.intersection(theirs.index)
    return {
        "radar_session": result.session_date, "radar_candidates": len(theirs),
        "replay_candidates": len(mine), "shared": len(both),
        "max_score_gap": float((mine[both] - theirs[both]).abs().max()) if len(both) else None,
        "rank_corr": float(mine[both].corr(theirs[both], method="spearman")) if len(both) > 2 else None,
    }


VARIANTS = {
    "v1: 0-100 score": "score",
    "room only (typical range / cost)": "room",
    "typical range, 20 sessions": "base",
    "last session's range": "range",
    "v2: mean range, last 5": "mean5",
    "relative turnover": "rel",
    "mean range last 5 / cost": "room5",
    "v1 with room from last 5": "score_v2",
}


def size_accuracy(long, sessions):
    """Per variant: rank correlation with the next range, and top ten against the rest."""

    rows = []
    for name, column in VARIANTS.items():
        ics, top_med, rest_med, top_hit, rest_hit = [], [], [], [], []
        for _, day in long[long["date"].isin(sessions) & long["candidate"]
                           & long["next_range"].notna()].groupby("date"):
            if len(day) < MIN_CANDIDATES:
                continue
            ics.append(day[column].corr(day["next_range"], method="spearman"))
            ranked = day.sort_values(column, ascending=False)
            top, rest = ranked.head(TOP), ranked.iloc[TOP:]
            top_med.append(top["next_range"].median())
            rest_med.append(rest["next_range"].median())
            top_hit.append((top["next_range"] >= 2 * top["cost"]).mean())
            rest_hit.append((rest["next_range"] >= 2 * rest["cost"]).mean())
        ics = pd.Series(ics).dropna()
        rows.append({
            "ranking": name, "sessions": len(ics),
            "rank corr": ics.mean(),
            "t": ics.mean() / ics.std() * np.sqrt(len(ics)) if len(ics) > 1 else np.nan,
            "sessions > 0": (ics > 0).mean(),
            "top10 next range": np.median(top_med), "rest": np.median(rest_med),
            "top10 >= 2x cost": np.mean(top_hit), "rest >= 2x cost": np.mean(rest_hit),
        })
    return pd.DataFrame(rows)


DIRECTION = {
    "v2 ranking (mean range, last 5)": "mean5",
    "v1 score": "score",
    "last session's return": "ret",
    "close position in its range": "position",
    "distance from SMA20": "dist20",
    "relative turnover": "rel",
}


def direction_accuracy(long, sessions):
    """Does anything on the radar say which way a name closes next session?"""

    rows = []
    frame = long[long["date"].isin(sessions) & long["candidate"] & long["next_change"].notna()]
    for name, column in DIRECTION.items():
        ics, top_up, top_mean = [], [], []
        for _, day in frame.groupby("date"):
            if len(day) < MIN_CANDIDATES:
                continue
            ics.append(day[column].corr(day["next_change"], method="spearman"))
            top = day.sort_values(column, ascending=False).head(TOP)
            top_up.append((top["next_change"] > 0).mean())
            top_mean.append(top["next_change"].mean())
        ics = pd.Series(ics).dropna()
        rows.append({
            "reading": name, "sessions": len(ics), "rank corr": ics.mean(),
            "t": ics.mean() / ics.std() * np.sqrt(len(ics)) if len(ics) > 1 else np.nan,
            "top10 closed up": np.mean(top_up), "top10 mean change %": np.mean(top_mean),
        })
    days = frame.groupby("date")["next_change"]
    rows.append({"reading": "every candidate (base rate)", "sessions": days.ngroups,
                 "rank corr": np.nan, "t": np.nan,
                 "top10 closed up": float(days.apply(lambda s: (s > 0).mean()).mean()),
                 "top10 mean change %": float(days.mean().mean())})
    return pd.DataFrame(rows), float(days.apply(lambda s: (s > 0).mean()).mean())


def calibration(long, sessions):
    """The next session's range as a multiple of the mean of the last five.

    If the ratio is steady, ``ratio x mean5`` is a forecast a reader can hold the
    market to, and its quartiles say how wide the miss usually is.
    """

    frame = long[long["date"].isin(sessions) & long["candidate"]
                 & long["next_range"].notna() & (long["mean5"] > 0)]
    ratio = frame["next_range"] / frame["mean5"]
    top = frame.assign(rank=frame.groupby("date")["mean5"].rank(ascending=False))
    top = top[top["rank"] <= TOP]
    top_ratio = top["next_range"] / top["mean5"]
    quantiles = [0.1, 0.25, 0.5, 0.75, 0.9]
    return pd.DataFrame({
        "all candidates": ratio.quantile(quantiles).values,
        f"top {TOP}": top_ratio.quantile(quantiles).values,
    }, index=[f"q{int(q * 100)}" for q in quantiles]), {
        "top within q25..q75 of its own forecast": float(
            ((top_ratio >= ratio.quantile(0.25)) & (top_ratio <= ratio.quantile(0.75))).mean()),
        "top next range >= 3x cost": float((top["next_range"] >= 3 * top["cost"]).mean()),
        "top next range >= 5x cost": float((top["next_range"] >= 5 * top["cost"]).mean()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=int, default=500)
    parser.add_argument("--skip-parity", action="store_true")
    args = parser.parse_args(argv)
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.3f}")

    from core.effective_cost import load_symbol_costs

    costs = {s: c.round_trip_percent for s, c in load_symbol_costs().items()}
    frames = load_frames()
    calendar, long = build_panel(frames, costs)
    print(f"{len(frames)} symbols, {len(calendar)} sessions "
          f"({calendar[0].date()} .. {calendar[-1].date()}), {len(costs)} with a banked cost")

    if not args.skip_parity:
        print("\nmirror against the radar on", calendar[-1].date(), parity(long, calendar[-1]))

    window = calendar[-(args.sessions + 1):-1]          # the last has no next session yet
    halves = {"older half": window[: len(window) // 2], "recent half": window[len(window) // 2:],
              "whole window": window}
    for label, sessions in halves.items():
        print(f"\n=== {label}: {sessions[0].date()} .. {sessions[-1].date()} "
              f"({len(sessions)} sessions) ===")
        print("\nSIZE -- does the ranking say which names move most next session?")
        print(size_accuracy(long, sessions).to_string(index=False))
        quartiles, hits = calibration(long, sessions)
        print("\nCALIBRATION -- next range / mean range of the last five sessions")
        print(quartiles.to_string())
        print({k: round(v, 3) for k, v in hits.items()})
        table, base_up = direction_accuracy(long, sessions)
        print(f"\nDIRECTION -- next close vs this close (all candidates closed up "
              f"{base_up:.1%} of the time)")
        print(table.to_string(index=False))


if __name__ == "__main__":
    main()
