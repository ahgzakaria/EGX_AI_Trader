r"""Can the Rubix daily bar stand beside EODHD as a source for the swing scan?

The scan is always a session behind, because EODHD publishes its daily candle
late. Rubix has today's session live, and the daily bridge already builds
validated daily bars from it. So the question is whether those bars can be
appended to EODHD history.

``core/daily_bridge/rubix_daily_builder.py`` already refuses, in a comment
naming three prerequisites: official OHLC, corporate actions, and external
reconciliation. This performs the third one, so the refusal rests on a
measurement rather than on caution.

The answer is no, and the reason is not the one expected.

**Volume reconciles perfectly.** Rubix over EODHD is 1.000x at every
percentile from the 5th to the 95th. Whatever the two disagree about, it is
not how much traded.

**Closes do not, and the gap is not an adjustment.** On the best subset --
official close, complete session with the auction captured, 1,421 bars -- only
22.8% match to within 0.01%, a quarter differ by more than 0.5%, and the
largest gap is 27%. If that were corporate-action drift, EODHD being adjusted
and Rubix raw, the ratio would hold steady within a symbol between actions.
Across 175 symbols with five or more comparisons, **not one** has a ratio
stable to within 0.2%, and 125 of them wobble by more than a full percent. The
two sources disagree about the closing price day by day, which is a different
problem from one being unadjusted and is not correctable by a factor.

**What it would cost.** Substituting the Rubix close flips 1.06% of breakout
decisions: 6 breakouts that EODHD does not see, and 9 that it sees and Rubix
loses. Small, and entirely concentrated where it hurts most -- on closes
sitting within a few thousandths of the level, which is exactly the population
the strategy selects from.

Reads only. It opens both stores read-only and writes nothing.

    venv\Scripts\python.exe scripts\research\rubix_eodhd_reconciliation.py
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from services.swing_breakout import SwingConfig, load_universe_histories

CACHE = PROJECT_ROOT / "data" / "normalized_daily_cache.db"


def rubix_bars() -> pd.DataFrame:
    """Finalized bars only, opened read-only. This store belongs to the bridge."""
    connection = sqlite3.connect(f"file:{CACHE.as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        return pd.read_sql(
            "SELECT canonical_symbol, session_date, official_close, volume, "
            "session_completeness FROM daily_bars WHERE active=1", connection)
    finally:
        connection.close()


def main() -> int:
    bars = rubix_bars()
    complete = bars[bars["session_completeness"] == "COMPLETE_DAILY_BAR"]
    print(f"Rubix: {len(bars):,} bars over {bars['session_date'].nunique()} "
          f"sessions, {len(complete):,} with the auction captured")

    histories = load_universe_histories(on_error=lambda symbol, reason: None)
    print(f"EODHD: {len(histories)} symbols\n")

    config = SwingConfig()
    rows, decisions = [], {"same": 0, "phantom": 0, "lost": 0}
    for symbol, frame in histories.items():
        base = symbol.split(".")[0]
        frame = frame.copy()
        frame.columns = [c.lower() for c in frame.columns]
        index = {str(d)[:10]: i for i, d in enumerate(frame.index)}
        levels = frame["high"].rolling(config.breakout_lookback).max().shift(1)

        for bar in complete[complete["canonical_symbol"] == f"{base}.CA"].itertuples():
            position = index.get(bar.session_date)
            if position is None or position < config.breakout_lookback:
                continue
            try:
                rubix = float(bar.official_close)
                eodhd = float(frame["close"].iloc[position])
                level = float(levels.iloc[position])
                rubix_volume = float(bar.volume)
                eodhd_volume = float(frame["volume"].iloc[position])
            except (TypeError, ValueError):
                continue
            if min(rubix, eodhd, level, eodhd_volume) <= 0:
                continue

            rows.append({"symbol": base, "ratio": rubix / eodhd,
                         "difference": (rubix - eodhd) / eodhd * 100,
                         "volume_ratio": rubix_volume / eodhd_volume})
            broke_eodhd, broke_rubix = eodhd > level, rubix > level
            if broke_eodhd == broke_rubix:
                decisions["same"] += 1
            elif broke_rubix:
                decisions["phantom"] += 1
            else:
                decisions["lost"] += 1

    frame = pd.DataFrame(rows)
    if frame.empty:
        print("No overlapping bars to compare.")
        return 1

    print(f"=== {len(frame):,} comparable bars ===\n")
    absolute = frame["difference"].abs()
    print("Close, Rubix against EODHD:")
    print(f"  match to within 0.01%   {(absolute < 0.01).mean() * 100:>5.1f}%")
    print(f"  differ by over 0.5%     {(absolute > 0.5).mean() * 100:>5.1f}%")
    print(f"  differ by over 2%       {(absolute > 2.0).mean() * 100:>5.1f}%")
    print(f"  largest difference      {absolute.max():>5.2f}%")

    print("\nVolume, Rubix over EODHD:")
    for percentile in (5, 50, 95):
        print(f"  p{percentile:<3}                    "
              f"{np.percentile(frame['volume_ratio'], percentile):>6.3f}x")

    # An adjustment offset holds steady within a symbol. A disagreement does not.
    grouped = frame.groupby("symbol")["ratio"].agg(["count", "min", "max"])
    grouped = grouped[grouped["count"] >= 5]
    spread = (grouped["max"] - grouped["min"]) * 100
    print(f"\nIs it a corporate-action offset? "
          f"({len(grouped)} symbols with 5+ bars)")
    print(f"  median spread of the ratio within a symbol {spread.median():>6.3f}%")
    print(f"  symbols stable to within 0.2%              "
          f"{(spread < 0.2).sum():>3}/{len(grouped)}")
    print(f"  symbols wobbling by over 1%                "
          f"{(spread > 1.0).sum():>3}/{len(grouped)}")

    total = sum(decisions.values())
    flipped = decisions["phantom"] + decisions["lost"]
    print(f"\nWhat it would cost, on {total:,} breakout decisions:")
    print(f"  unchanged            {decisions['same']:>5,} "
          f"({decisions['same'] / total * 100:.1f}%)")
    print(f"  breakout Rubix sees and EODHD does not  {decisions['phantom']:>3}")
    print(f"  breakout EODHD sees and Rubix does not  {decisions['lost']:>3}")
    print(f"  flipped              {flipped:>5,} ({flipped / total * 100:.2f}%)")

    print("\nA flip lands on a close sitting within thousandths of its level, "
          "which is\nexactly the population this strategy selects from. "
          "swing_daily_eligible stays\nFalse, and now on a measurement rather "
          "than on caution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
