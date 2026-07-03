import time
from datetime import timedelta
import traceback
import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.statistics import BacktestStatistics
from backtesting.report import BacktestReport


def run_backtest():

    start_time = time.time()

    symbols = (
        pd.read_csv("data/symbols.csv")["Ticker"]
        .dropna()
        .unique()
        .tolist()
    )

    all_trades = []

    successful = 0
    failed = 0

    errors = []

    total = len(symbols)

    for index, symbol in enumerate(symbols, start=1):

        # ==============================
        # Progress Update
        # ==============================

        yield {

            "type": "progress",

            "current": index,

            "total": total,

            "percent": round(index / total * 100, 1),

            "symbol": symbol

        }

        try:

            engine = BacktestEngine(symbol)

            trades = engine.run()

            all_trades.extend(trades)

            successful += 1

        except Exception:

            failed += 1

            errors.append(symbol)

            traceback.print_exc()

    # ==================================
    # Statistics
    # ==================================

    stats = BacktestStatistics(all_trades)

    summary = stats.summary()

    exit_reasons = stats.exit_reasons()

    report = BacktestReport(all_trades)

    report.save_all()

    elapsed_seconds = round(time.time() - start_time, 2)

    elapsed = str(
        timedelta(seconds=elapsed_seconds)
    )

    # ==============================
    # Final Result
    # ==============================

    yield {

        "type": "finished",

        "summary": summary,

        "exit_reasons": exit_reasons,

        "symbols": total,

        "successful": successful,

        "failed": failed,

        "trades": len(all_trades),

        "errors": errors,

        "elapsed": elapsed,

        "elapsed_seconds": elapsed_seconds

    }