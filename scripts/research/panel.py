r"""One panel of every symbol the backtest can reach, built once and cached.

The engine loads and re-indicators one symbol at a time and takes ~5.6 minutes
for a full pass. That is the right cost for a decision and the wrong cost for a
search. This module builds the *same* frames -- `load_history(purpose="backtest")`
followed by `calculate_indicators`, byte for byte what `BacktestEngine.load`
does -- concatenates them into one long panel, and caches it.

Anything measured here is therefore measured on the engine's own inputs. It is
not a substitute for the engine: it has no limit entry, no position sizing, no
portfolio, and no per-symbol spread. It is for ranking hypotheses cheaply enough
to throw most of them away.

    venv\Scripts\python.exe scripts\research\panel.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from core.data_provider import load_history, provider_purpose  # noqa: E402
from core.symbols import load_symbols  # noqa: E402
from indicators.technical import calculate_indicators  # noqa: E402

CACHE = PROJECT_ROOT / "data" / "research" / "backtest_panel.parquet"

#: The engine refuses to evaluate before bar 200 and stops 20 bars from the end.
#: A symbol shorter than that can never produce a trade, so it is not in the
#: panel either.
MIN_BARS = 260


def build(symbols=None) -> pd.DataFrame:
    frames = []
    failures = []
    for symbol in symbols or load_symbols():
        try:
            with provider_purpose("backtest"):
                frame = load_history(symbol, purpose="backtest")
            if frame is None or len(frame) < MIN_BARS:
                failures.append((symbol, f"only {0 if frame is None else len(frame)} bars"))
                continue
            frame = calculate_indicators(frame)
        except Exception as error:                       # noqa: BLE001 - recorded
            failures.append((symbol, f"{type(error).__name__}: {error}"))
            continue
        frame = frame.copy()
        frame["Symbol"] = symbol
        frame["Date"] = frame.index
        frame["Bar"] = range(len(frame))
        frames.append(frame.reset_index(drop=True))
    if failures:
        print(f"{len(failures)} symbols unusable, e.g. {failures[:3]}")
    panel = pd.concat(frames, ignore_index=True)
    return panel


def load(rebuild: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not rebuild:
        return pd.read_parquet(CACHE)
    panel = build()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(CACHE, index=False)
    return panel


if __name__ == "__main__":
    panel = load(rebuild="--rebuild" in sys.argv)
    print(f"{panel['Symbol'].nunique()} symbols, {len(panel):,} bars, "
          f"{panel['Date'].min().date()} -> {panel['Date'].max().date()}")
    print(f"cached at {CACHE.relative_to(PROJECT_ROOT)}")
