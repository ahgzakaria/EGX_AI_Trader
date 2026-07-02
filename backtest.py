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

        except Exception as e:

            failed += 1

            print(f"ERROR -> {symbol}")
            print(e)
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