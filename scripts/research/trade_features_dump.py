r"""Every trade the daily strategy takes on the frozen record, with its features.

    venv\Scripts\python.exe scripts\research\trade_features_dump.py

One canonical FULL_HISTORY Strategy-Only pass -- the configured rules, nothing
overridden -- through the same pipeline as `exit_rule_sweep.py`, written out as
one row per trade with every field the engine recorded at the signal: the score
and its components, RSI, ADX, ATR, the EMA distances and slopes, volume ratio,
Bollinger position, the 20-day high/low distances, regime, and the realised
outcome under the real entry band, EMA20 trail and costs.

Both populations are kept and flagged: every trade the engine produced, and the
subset `PortfolioSimulator` executed under the position and heat limits. The
limits refuse trades for capacity, not for quality, so the full set is the
larger and less biased sample for asking which entries work.

The pass is expensive (about seven minutes); the analysis that reads this file
is not, so the two are separate. Writes only under reports/research/, which is
gitignored.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                          # noqa: E402

from scripts.research.exit_rule_sweep import run             # noqa: E402

from backtesting.config import load as load_backtest_config  # noqa: E402
from config.settings_manager import settings                 # noqa: E402
from core.symbols import SYMBOL_SOURCE, filter_by_spread, load_symbols  # noqa: E402

OUT = PROJECT_ROOT / "reports" / "research" / "daily_strategy_trades.csv"


def main():
    settings.reload()
    cfg = load_backtest_config()
    symbols, _ = filter_by_spread(load_symbols(SYMBOL_SOURCE),
                                  getattr(cfg, "MAX_SPREAD_PERCENT", None))
    started = time.time()
    result, failures = run({}, symbols)
    rows = []
    for trade in result["trades"]:
        row = dataclasses.asdict(trade)
        row["executed"] = bool(getattr(trade, "executed", False))
        rows.append(row)
    frame = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT, index=False, encoding="utf-8-sig")
    s = result["summary"]
    print(f"{len(frame)} trades ({int(frame['executed'].sum())} executed) from "
          f"{len(symbols)} symbols, {len(failures)} failures, "
          f"{time.time() - started:.0f}s")
    print(f"executed summary: trades {s['Trades']} PF {s['ProfitFactor']} "
          f"return {s['TotalReturn']}%")
    print(f"wrote {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
