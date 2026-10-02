"""اختبار متوسط 200 يوم · liquid names pulling back to a rising 200-day average.

Shown on the T+0 Radar page at the owner's request (2026-10-02). It is not a
same-session reading: its evidence is about the twenty sessions after a test.

A name is testing its average when all of these hold:

* the 200-day simple average is rising (above where it was 20 sessions earlier);
* the name came from above: within the previous 60 sessions it closed at least
  10% over the average;
* it is at the average now: the low reached within 2% above it, and the close is
  no more than 3% below it;
* it is liquid: at least 5M EGP a session over the last 20 (Swing Breakout's
  floor), with a session without a trade counted as zero;
* it made no move past EGX's +/-20% limit in the last 200 sessions. Such a move
  is an unadjusted split or bonus issue, and it distorts the average itself.

What decides the outcome, replayed over 2014-2026 by
``scripts/research/sma200_pullback.py`` (docs/audits/strategies/
SMA200_PULLBACK.md):

* a test on its own is a coin flip against the median liquid stock;
* in a broad sell-off (at least 75% of liquid names below their own 20-day
  average) a test that **closed on or above** the average beat that stock over
  the next 20 sessions in 20 of 27 independent episodes. The median lift was
  +1.5%, and 81% reached 5% above the average before falling 5% below it;
* one that **closed below** it beat the market in 5 of 13 episodes, with a
  median of -1.8%, and broke first 64% of the time.

The page reads the definitions and that evidence from here, and the research
script reads the same ``features`` and ``state``. The two cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SMA = 200
SLOPE_SESSIONS = 20
CAME_FROM = 60
CAME_FROM_ABOVE = 0.10
ZONE_ABOVE, ZONE_BELOW = 0.02, 0.03
APPROACH_ABOVE = 0.06         # "approaching": 2-6% above a qualifying average
BROKE_BELOW = 0.10            # "already through it": 3-10% below
LIQUIDITY_EGP = 5_000_000
LIQUIDITY_WINDOW = 20
DAILY_LIMIT = 0.205
SELLOFF_SHARE = 0.75          # a broad sell-off: this share of liquid names below their SMA20
CROSS_SECTION = 30            # core.measured_benchmark.MINIMUM_SYMBOLS

ON_OR_ABOVE, BELOW, APPROACHING, BROKE = "on_or_above", "below", "approaching", "broke"

#: What the replay found, 2014-01-01 .. 2026-09 (lift = 20-session return over the
#: median liquid stock across the same sessions; an episode is a run of tests no
#: more than 10 sessions apart). Re-run the research script to refresh.
EVIDENCE = {
    "selloff_on_or_above": {"episodes": 27, "beat": 20, "lift_median": 1.52,
                            "bounce": 81, "break": 16},
    "selloff_below": {"episodes": 13, "beat": 5, "lift_median": -1.82,
                      "bounce": 32, "break": 64},
    "normal_on_or_above": {"episodes": 61, "beat": 31, "lift_median": 0.00,
                           "bounce": 72, "break": 18},
}


def features(frame, calendar):
    """One symbol's test inputs on every market session (NaN where it did not trade)."""

    close, low = frame["Close"], frame["Low"]
    sma = close.rolling(SMA, min_periods=SMA).mean()
    dist = close / sma - 1
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    bars = pd.DataFrame({
        "close": close, "sma": sma, "dist": dist,
        "low_dist": low / sma - 1,
        "slope": sma / sma.shift(SLOPE_SESSIONS) - 1,
        "peak": dist.shift(1).rolling(CAME_FROM, min_periods=CAME_FROM).max(),
        "action": (close.pct_change().abs() > DAILY_LIMIT).astype(float)
                  .rolling(SMA, min_periods=1).max(),
        "below_sma20": (close < close.rolling(20, min_periods=20).mean()).astype(float),
        "rsi": 100 - 100 / (1 + gain / loss.replace(0, np.nan)),
    })
    out = bars.reindex(calendar)
    out["traded"] = out.index.isin(frame.index)
    flow = pd.to_numeric(frame["Turnover"], errors="coerce").reindex(calendar).fillna(0.0)
    out["turnover"] = flow.rolling(LIQUIDITY_WINDOW, min_periods=LIQUIDITY_WINDOW).mean()
    return out


def liquid(df):
    return df["traded"].astype(bool) & (df["turnover"] >= LIQUIDITY_EGP)


def in_zone(df):
    return (df["low_dist"] <= ZONE_ABOVE) & (df["dist"] >= -ZONE_BELOW)


def state(df):
    """Per row: on_or_above / below (testing), approaching, broke, or ''."""

    pulled_back = (liquid(df) & (df["slope"] > 0) & (df["peak"] >= CAME_FROM_ABOVE)
                   & (df["action"] == 0))
    zone = in_zone(df)
    return pd.Series(np.select(
        [pulled_back & zone & (df["dist"] >= 0),
         pulled_back & zone & (df["dist"] < 0),
         pulled_back & ~zone & (df["dist"] > ZONE_ABOVE) & (df["dist"] <= APPROACH_ABOVE),
         pulled_back & (df["dist"] < -ZONE_BELOW) & (df["dist"] >= -BROKE_BELOW)],
        [ON_OR_ABOVE, BELOW, APPROACHING, BROKE], default=""), index=df.index)


def market_calendar(frames, quorum=CROSS_SECTION):
    counts = pd.Series(np.concatenate([f.index.values for f in frames.values()])).value_counts()
    return pd.DatetimeIndex(sorted(counts[counts >= quorum].index))


def load_frames(symbols=None):
    """The measured record for the active universe, as the replay read it."""

    from sector_flow import measured_turnover

    if symbols is None:
        from core.universe import active_symbols

        symbols = sorted(active_symbols())
    frames = {}
    for symbol in symbols:
        frame = measured_turnover.frame_for(f"{symbol}.CA")
        if frame is None or frame.empty:
            continue
        frame = frame[pd.to_numeric(frame["Close"], errors="coerce") > 0]
        frames[symbol] = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frames


@dataclass
class Sma200Scan:
    session_date: str | None
    below_sma20_share: float | None
    selloff: bool
    names: pd.DataFrame = field(default_factory=pd.DataFrame)


def scan(frames=None):
    """The last session's tests, approaches and breaks, with the market's state."""

    frames = load_frames() if frames is None else frames
    if not frames:
        return Sma200Scan(None, None, False)
    calendar = market_calendar(frames)
    rows = []
    for symbol, frame in frames.items():
        last = features(frame, calendar).iloc[[-1]]
        last.insert(0, "symbol", symbol)
        rows.append(last)
    latest = pd.concat(rows)
    latest["state"] = state(latest).to_numpy()
    breadth = latest.loc[liquid(latest), "below_sma20"].mean()
    names = latest[latest["state"] != ""].reset_index(drop=True)
    try:
        from core.universe import company_name

        names.insert(1, "name", [company_name(s) for s in names["symbol"]])
    except Exception:                                       # noqa: BLE001 - display only
        names.insert(1, "name", "")
    return Sma200Scan(calendar[-1].date().isoformat(),
                      None if pd.isna(breadth) else float(breadth),
                      bool(breadth >= SELLOFF_SHARE) if pd.notna(breadth) else False,
                      names.sort_values("dist").reset_index(drop=True))
