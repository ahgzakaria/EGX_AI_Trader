r"""How the surviving trigger should be held, and how it should be let go.

`breakout_study.py` established the trigger. This module stops using forward
returns -- which assume you hold blindly for N bars and never get stopped -- and
simulates the trade.

Modelling choices, and why each is the conservative one:

* **Entry at the next bar's close.** The signal is a close, so it is known only
  after the close. The open is carried forward from the previous close on 96-98%
  of bars in this data and is not a tradeable price, so the first honest fill is
  the next session's close.
* **Stop before target, within a bar.** Daily OHLC does not say which came
  first. When both are touched the loss is taken.
* **Per-symbol costs.** The measured round trip the backtest charges, not a flat
  rate.
* **A stop gap-through fills at the bar's low**, not at the stop price, whenever
  the bar's high is already below the stop.

Every exit rule is measured in both eras, and the shipped strategy's own exit --
a stop trailed to EMA20 from the day after entry -- is one of the rows, so the
comparison is against what is actually running rather than against a strawman.

    venv\Scripts\python.exe scripts\research\breakout_exits.py
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
from scripts.research.signal_scan import (
    SPLIT, add_cross_section, costs_by_symbol, features,
)

#: Minimum median daily turnover, in EGP, for a fill to be worth believing. A
#: 2% position out of 100,000 EGP is 2,000 EGP; this floor keeps a signal from
#: being generated in a name that trades less than that in a day.
TURNOVER_FLOOR = 2_000_000


def signals(data: pd.DataFrame) -> pd.Series:
    """The trigger and the three filters that improved BOTH eras."""
    bar_range = (data["High"] - data["Low"]).replace(0, np.nan)
    close_position = (data["Close"] - data["Low"]) / bar_range
    return (
        data["breakout_20"]
        & (data["VOLUME_RATIO"] >= 2.5)
        & (close_position >= 0.70)
        & data["market_above_200"].fillna(False)
        & (data["rank_atrp"] <= 0.5)
        & (data["rank_mom_12_1"] >= 0.5)
        & (data["turnover_20"] >= TURNOVER_FLOOR)
    )


def simulate(frame, entries, rule, cost, max_bars=60):
    """Walk each signalled trade forward under one exit rule.

    `frame` is one symbol, sorted, positionally indexed. `entries` are the
    positions of signal bars. Returns one row per trade.
    """
    close = frame["Close"].to_numpy(float)
    high = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    atr = frame["ATR"].to_numpy(float)
    ema20 = frame["EMA20"].to_numpy(float)
    ema50 = frame["EMA50"].to_numpy(float)
    dates = frame["Date"].to_numpy()

    trades = []
    busy_until = -1
    for signal_index in entries:
        if signal_index <= busy_until:
            continue
        entry_index = signal_index + 1
        if entry_index >= len(close) - 1:
            continue
        entry = close[entry_index]
        if not np.isfinite(entry) or entry <= 0:
            continue

        initial_stop = rule["stop"](signal_index, entry_index, close, high, low, atr)
        if not np.isfinite(initial_stop) or initial_stop >= entry:
            continue

        stop = initial_stop
        peak = entry
        exit_index, exit_price, reason = None, None, None
        last = min(entry_index + rule["max_bars"], len(close) - 1)

        close_exit = rule.get("close_exit")
        for k in range(entry_index + 1, last + 1):
            if low[k] <= stop:
                # A bar that never traded above the stop gapped through it.
                exit_price = low[k] if high[k] < stop else stop
                exit_index, reason = k, "stop"
                break
            peak = max(peak, close[k])
            target = rule.get("target")
            if target is not None:
                level = entry + target * (entry - initial_stop)
                if high[k] >= level:
                    exit_price, exit_index, reason = level, k, "target"
                    break
            # A stop a wick can reach is a stop the market can hunt. A
            # close-based exit is decided once a day on a settled price, and
            # fills at that close.
            if close_exit is not None and close_exit(k, entry, close, ema20, ema50):
                exit_price, exit_index, reason = close[k], k, "close_exit"
                break
            stop = max(stop, rule["trail"](k, entry, initial_stop, peak,
                                           close, atr, ema20, ema50))
        if exit_index is None:
            exit_index, exit_price, reason = last, close[last], "timeout"

        gross = (exit_price / entry - 1) * 100
        trades.append({
            "signal_date": dates[signal_index],
            "entry_date": dates[entry_index],
            "bars": exit_index - entry_index,
            "gross": gross,
            "net": gross - cost,
            "reason": reason,
            "risk_percent": (entry - initial_stop) / entry * 100,
        })
        busy_until = exit_index
    return trades


def no_trail(k, entry, initial_stop, peak, close, atr, ema20, ema50):
    return -np.inf


def chandelier(multiple):
    def trail(k, entry, initial_stop, peak, close, atr, ema20, ema50):
        return peak - multiple * atr[k - 1]
    return trail


def ema_trail(which):
    def trail(k, entry, initial_stop, peak, close, atr, ema20, ema50):
        return (ema20 if which == 20 else ema50)[k - 1]
    return trail


def breakeven_after(bars, then=None):
    inner = then or no_trail

    def trail(k, entry, initial_stop, peak, close, atr, ema20, ema50):
        candidate = inner(k, entry, initial_stop, peak, close, atr, ema20, ema50)
        return max(candidate, entry) if k - bars >= 0 and peak >= entry * 1.03 else candidate
    return trail


def atr_stop(multiple):
    def stop(signal_index, entry_index, close, high, low, atr):
        return close[entry_index] - multiple * atr[signal_index]
    return stop


def base_stop(signal_index, entry_index, close, high, low, atr):
    """Under the twenty-bar base that was just broken, with an ATR buffer."""
    start = max(0, signal_index - 19)
    return float(np.min(low[start:signal_index + 1])) - 0.3 * atr[signal_index]


def summarise(trades, label, split=SPLIT):
    frame = pd.DataFrame(trades)
    if frame.empty:
        print(f"{label:<34}{'no trades':>10}")
        return None
    frame["era"] = np.where(frame["signal_date"] < np.datetime64(split),
                            "train", "valid")
    out = {"label": label, "n": len(frame)}
    parts = []
    for era in ("train", "valid"):
        part = frame[frame["era"] == era]
        if part.empty:
            parts.append((0, 0.0, 0.0, 0.0))
            continue
        wins = part[part["net"] > 0]["net"].sum()
        losses = -part[part["net"] <= 0]["net"].sum()
        parts.append((len(part), part["net"].mean(),
                      (part["net"] > 0).mean() * 100,
                      wins / losses if losses else float("inf")))
    (n_tr, avg_tr, win_tr, pf_tr), (n_te, avg_te, win_te, pf_te) = parts
    print(f"{label:<34}{n_tr:>7,}{avg_tr:>+8.2f}{win_tr:>6.0f}{pf_tr:>6.2f}"
          f"{n_te:>8,}{avg_te:>+8.2f}{win_te:>6.0f}{pf_te:>6.2f}"
          f"{frame['bars'].median():>7.0f}")
    out.update(avg_train=avg_tr, avg_valid=avg_te, pf_train=pf_tr, pf_valid=pf_te)
    return frame


def main() -> int:
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = features(panel, hold=20)
    data = add_cross_section(data)
    data = data.dropna(subset=["mom_12_1", "above_200", "atrp",
                               "rank_turnover_20", "VOLUME_RATIO", "ATR"])
    data["signal"] = signals(data)
    print(f"{int(data['signal'].sum()):,} signal bars over "
          f"{data['Symbol'].nunique()} symbols")

    rules = {
        "hold 20, no stop": dict(stop=atr_stop(99), trail=no_trail, max_bars=20),
        "hold 40, no stop": dict(stop=atr_stop(99), trail=no_trail, max_bars=40),
        "2 ATR stop, hold 20": dict(stop=atr_stop(2.0), trail=no_trail, max_bars=20),
        "2 ATR stop, hold 40": dict(stop=atr_stop(2.0), trail=no_trail, max_bars=40),
        "2 ATR stop, hold 60": dict(stop=atr_stop(2.0), trail=no_trail, max_bars=60),
        "3 ATR stop, hold 40": dict(stop=atr_stop(3.0), trail=no_trail, max_bars=40),
        "base stop, hold 40": dict(stop=base_stop, trail=no_trail, max_bars=40),
        "2 ATR + target 2R, 40": dict(stop=atr_stop(2.0), trail=no_trail,
                                      max_bars=40, target=2.0),
        "2 ATR + target 3R, 40": dict(stop=atr_stop(2.0), trail=no_trail,
                                      max_bars=40, target=3.0),
        "chandelier 3 ATR, cap 60": dict(stop=atr_stop(2.0), trail=chandelier(3.0),
                                         max_bars=60),
        "chandelier 4 ATR, cap 60": dict(stop=atr_stop(2.0), trail=chandelier(4.0),
                                         max_bars=60),
        "EMA50 trail, cap 60": dict(stop=atr_stop(2.0), trail=ema_trail(50),
                                    max_bars=60),
        "EMA20 trail (shipped), cap 20": dict(stop=base_stop, trail=ema_trail(20),
                                              max_bars=20),
        "2 ATR + breakeven at +3%, 40": dict(stop=atr_stop(2.0),
                                             trail=breakeven_after(1), max_bars=40),
    }

    print(f"\n{'exit rule':<34}{'tr n':>7}{'avg':>8}{'win%':>6}{'PF':>6}"
          f"{'val n':>8}{'avg':>8}{'win%':>6}{'PF':>6}{'bars':>7}")
    print("-" * 96)
    collected = {}
    for label, rule in rules.items():
        rule.setdefault("target", None)
        trades = []
        for symbol, frame in data.groupby("Symbol", sort=False):
            frame = frame.sort_values("Date").reset_index(drop=True)
            entries = np.flatnonzero(frame["signal"].to_numpy())
            if not len(entries):
                continue
            trades.extend(simulate(frame, entries, rule, costs[symbol]))
        collected[label] = summarise(trades, label)
    print("\navg = mean net % per trade; PF = gross wins / gross losses;")
    print("bars = median bars held. Split at", SPLIT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
