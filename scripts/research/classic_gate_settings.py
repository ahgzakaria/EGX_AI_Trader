"""The same gate comparison, but through the strategy's own exit logic.

The fixed twenty-day hold measured signal quality, not the strategy: it
ignored the stop, the target and every exit rule the engine actually applies.
This runs the project's own BacktestEngine, so entries, exits, costs and
rejections are the ones that ship.

`load_history` is redirected to the frozen EODHD archive rather than the
provider, so the comparison is offline, reproducible, and identical across
configurations.
"""
import glob
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, ".")

TOP_N = 40
DEFAULTS = {
    "min_score": 65, "min_trend": 25, "min_confidence": 80,
    "min_momentum": 5, "min_rr": 2.0, "min_volume": 5,
}

frames = {}
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    symbol = os.path.basename(path)[:-4]
    f = pd.read_csv(path)
    if len(f) < 500:
        continue
    f = f.rename(columns=str.title)[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
    f = f[(f[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (f["Volume"] > 0)]
    f = f[(f["High"] - f["Low"]) / f["Open"] * 100 <= 25]
    if len(f) < 500:
        continue
    f["Date"] = pd.to_datetime(f["Date"])
    frames[symbol] = f.set_index("Date")

liquidity = {s: (f["Close"] * f["Volume"]).tail(250).median() for s, f in frames.items()}
universe = sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]
print(f"{len(universe)} symbols from the frozen archive")

import backtesting.engine as engine_module

engine_module.load_history = lambda symbol, purpose=None, **kw: frames[symbol].copy()


def run(overrides):
    from config.settings_manager import settings
    from backtesting.engine import BacktestEngine

    original = dict(settings.get("strategy") or {})
    patched = dict(original)
    patched.update(overrides)
    settings.data["strategy"] = patched
    try:
        trades = []
        for symbol in universe:
            try:
                engine = BacktestEngine(symbol)
                engine.load()
                engine.run()
                trades.extend(engine.trades)
            except Exception:
                continue
        return trades
    finally:
        settings.data["strategy"] = original


def report(label, trades):
    if not trades:
        print(f"{label:<26} no trades")
        return
    profits = []
    for trade in trades:
        value = getattr(trade, "profit_percent", None)
        if value is None:
            value = getattr(trade, "profit", None)
        if value is not None:
            profits.append(float(value))
    if not profits:
        print(f"{label:<26} {len(trades)} trades, no profit field")
        return
    wins = sum(1 for p in profits if p > 0)
    print(f"{label:<26}{len(profits):>8,}{statistics.fmean(profits):>10.2f}%"
          f"{wins / len(profits) * 100:>9.1f}%{statistics.median(profits):>10.2f}%"
          f"{sum(profits):>12,.0f}%")


print(f"{'configuration':<26}{'trades':>8}{'avg':>10}{'win%':>9}{'median':>10}{'total':>12}")
print("-" * 75)
report("saved (market filter ON)", run({}))
report("market filter OFF", run({"require_market_analyzer": False}))
report("filter OFF + defaults", run({**DEFAULTS, "require_market_analyzer": False}))
report("shipped defaults", run(DEFAULTS))
report("saved + min_trend 25", run({"min_trend": 25}))
report("saved + min_rr 2.0", run({"min_rr": 2.0}))
report("saved + min_score 65", run({"min_score": 65}))
print()
print("Exits, costs and rejections are the engine's own. Profit is whatever")
print("field the trade object carries, so it already reflects the configured")
print("commission -- which was corrected from 0.003 to 0.001819 on 2026-08-18.")
