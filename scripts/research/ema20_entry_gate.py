r"""Does entering only at or below EMA20 improve the daily strategy's portfolio?

    venv\Scripts\python.exe scripts\research\ema20_entry_gate.py

`which_entries_work.py` found thirteen entry features ranking realised trades in
both eras, and the follow-up showed they are one effect: RSI, Bollinger
position, momentum and the rest correlate 0.6-0.9 with the close's distance from
EMA20 at the signal, and none of them ranks anything once that distance is held
fixed. Trades entered well below EMA20 won about 80% of the time; above it,
about 15-20%. The fixed-hold path says why: those entries bounce for a day or
three and then fade, and the EMA20 trail books the bounce.

A ranking is not a strategy. This asks whether the effect survives becoming a
gate -- fewer trades, the same capacity limits, the same costs -- through the
canonical pipeline.

**Pre-registered, before this was run:**

* the gate is `ema20_dist <= 0`: the close at or below its own twenty-day
  average. Zero is the indicator's neutral point, not a cut chosen from the
  fifths, so it is the one threshold that was not fitted to this data;
* `ema20_dist <= -1` is also reported, and is labelled for what it is: the
  boundary the early era's fifths suggested. Its early-era figures are
  therefore in-sample and only its >=2023 figures are evidence;
* the gate is applied to the engine's trades before `PortfolioSimulator`, which
  is exactly a signal-time gate for this configuration: every field it reads is
  known at the signal, and with overlapping trades allowed the engine produces
  each symbol's trades independently of the others;
* a result is only called an improvement if it holds in both eras.

Nothing is written except to stdout.

**Result, 2026-09-15** (230 symbols, 662 engine trades, current settings):

    gate                      exec  win%    PF   return  maxDD  sharpe  calmar  exposure
    no gate (as shipped)       609  39.1  1.54  +142.7%  18.0%    0.81    0.57     32.2%
    ema20_dist <= 0            284  68.7  2.42  +125.0%  10.9%    1.55    0.86     15.3%
    ema20_dist <= -1 (fitted)  198  80.8  3.14  +125.2%   9.6%    1.62    0.98     10.8%

    by era                 PF <2023  PF >=2023   net <2023   net >=2023
    no gate                    1.44       1.59      37,231      105,479
    ema20_dist <= 0            2.15       2.64      44,627       80,325
    ema20_dist <= -1           2.23       4.20      39,111       86,125

The pre-registered gate holds in both eras on every measure of quality: profit
factor, win rate, drawdown, Sharpe and Calmar. It does not raise total return.
It gives up 18 points over the period, all of it after 2023, where the fattest
winners moved above EMA20. It earns that return with less than half the
exposure, so the account sits in cash most of the time. Sizing stays on initial
capital, so this is a smaller and steadier book, not a leveraged one.
Recovering the return by raising risk or the position cap would be a new
choice, and it would need its own pre-registered test. The -1 row is in-sample
before 2023; only its >=2023 column is evidence.

Not implemented: the decision lives in sealed `strategy/` files, and adopting
a gate is the owner's decision, not this script's.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.exit_rule_sweep import era              # noqa: E402

from backtesting.config import load as load_backtest_config   # noqa: E402
from config.settings_manager import settings                  # noqa: E402
from core.symbols import SYMBOL_SOURCE, filter_by_spread, load_symbols  # noqa: E402
from services.backtest_service import (_decision_service,     # noqa: E402
                                       _portfolio_result, _run_pass)
from strategy.trading_decision import TradingDecisionService   # noqa: E402

GATES = [
    ("no gate (as shipped)", None, "baseline"),
    ("ema20_dist <= 0", 0.0, "pre-registered, not fitted"),
    ("ema20_dist <= -1", -1.0, "fitted on <2023: only >=2023 is evidence"),
]


def apply_gate(trades, threshold):
    """The trades a signal-time `ema20_dist <= threshold` gate lets through.

    ``threshold=None`` is no gate. A trade whose distance was never recorded is
    refused rather than admitted: a gate that lets unmeasured entries through is
    not the gate that was measured.
    """
    if threshold is None:
        return list(trades)
    return [t for t in trades
            if getattr(t, "ema20_dist", None) is not None
            and float(t.ema20_dist) == float(t.ema20_dist)          # not NaN
            and float(t.ema20_dist) <= threshold]


def main():
    settings.reload()
    cfg = load_backtest_config()
    symbols, _ = filter_by_spread(load_symbols(SYMBOL_SOURCE),
                                  getattr(cfg, "MAX_SPREAD_PERCENT", None))
    mode = TradingDecisionService.STRATEGY_ONLY
    started = time.time()
    trades, failures, _, _ = _run_pass(symbols, _decision_service(mode))
    print(f"{len(trades)} engine trades from {len(symbols)} symbols "
          f"({len(failures)} failures, {time.time() - started:.0f}s)\n")

    rows = []
    for label, threshold, note in GATES:
        kept = apply_gate(trades, threshold)
        # _portfolio_result mutates the trades it simulates (shares, executed,
        # portfolio_profit), so every gate simulates fresh copies.
        import copy
        result = _portfolio_result([copy.deepcopy(t) for t in kept], cfg, mode)
        s = result["summary"]
        rows.append((label, note, s, era(result["executed"], True),
                     era(result["executed"], False), len(kept)))
        print(f"== {label}  [{note}]")
        print(f"   engine trades kept {len(kept)}  executed {s['Trades']}  "
              f"win {s['WinRate']}%  PF {s['ProfitFactor']}  return {s['TotalReturn']}%  "
              f"maxDD {s['MaxDrawdown']}%  sharpe {s['SharpeRatio']}  "
              f"calmar {s['CalmarRatio']}  exposure {s['ExposurePercent']}%")
        sys.stdout.flush()

    print(f"\n{'gate':<22}{'era':>8}{'trades':>8}{'win%':>7}{'PF':>7}{'net EGP':>12}")
    for label, _, _, before, after, _ in rows:
        for name, (n, win, pf, net) in (("<2023", before), (">=2023", after)):
            print(f"{label:<22}{name:>8}{n:>8}{win:>7.1f}{pf:>7.2f}{net:>12,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
