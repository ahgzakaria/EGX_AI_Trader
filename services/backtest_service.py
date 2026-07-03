import traceback
import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.statistics import BacktestStatistics
from backtesting.report import BacktestReport


def run_backtest():

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

    for index, symbol in enumerate(symbols, start=1):

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

    # ==================================
    # Save Reports
    # ==================================

    report = BacktestReport(all_trades)

    report.save_all()

    return {

        "summary": summary,

        "exit_reasons": exit_reasons,

        "symbols": len(symbols),

        "successful": successful,

        "failed": failed,

        "trades": len(all_trades),

        "errors": errors

    }