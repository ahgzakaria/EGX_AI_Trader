import traceback
import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.statistics import BacktestStatistics
from backtesting.report import BacktestReport


def main():

    # ==================================
    # Load All Symbols
    # ==================================

    symbols = (
        pd.read_csv("data/symbols.csv")["Ticker"]
        .dropna()
        .unique()
        .tolist()
    )

    print("=" * 60)
    print("EGX AI Trader Backtest")
    print("=" * 60)
    print(f"Total Symbols : {len(symbols)}")

    all_trades = []

    successful = 0
    failed = 0

    # ==================================
    # Run Backtest
    # ==================================

    for index, symbol in enumerate(symbols, start=1):

        print(f"[{index}/{len(symbols)}] {symbol}")

        try:

            engine = BacktestEngine(symbol)

            trades = engine.run()

            all_trades.extend(trades)

            successful += 1

        except Exception:

            failed += 1

            print("\n" + "=" * 60)
            print(f"ERROR -> {symbol}")
            print("=" * 60)

            traceback.print_exc()

            print("-" * 60)

    # ==================================
    # Statistics
    # ==================================

    stats = BacktestStatistics(all_trades)

    summary = stats.summary()

    print("\n")
    print("=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)

    for key, value in summary.items():

        print(f"{key:20} : {value}")

    # ==================================
    # Exit Reasons
    # ==================================

    print("\n")
    print("=" * 60)
    print("EXIT REASONS")
    print("=" * 60)

    exit_reasons = stats.exit_reasons()

    total = sum(exit_reasons.values())

    for reason, count in exit_reasons.items():

        percent = round(count / total * 100, 2) if total else 0

        print(f"{reason:20} : {count:5} ({percent:6.2f}%)")

    # ==================================
    # Reports
    # ==================================

    report = BacktestReport(all_trades)

    report.save_all()

    print("\n")
    print("=" * 60)
    print("REPORTS")
    print("=" * 60)

    print("Trades      : reports/backtest_results.csv")
    print("Statistics  : reports/backtest_statistics.csv")
    print("Equity      : reports/equity_curve.csv")
    print("Symbols     : reports/symbol_statistics.csv")

    print("\n")
    print("=" * 60)
    print("RUN SUMMARY")
    print("=" * 60)

    print(f"Symbols Loaded     : {len(symbols)}")
    print(f"Successful         : {successful}")
    print(f"Failed             : {failed}")
    print(f"Total Trades       : {len(all_trades)}")


if __name__ == "__main__":

    main()