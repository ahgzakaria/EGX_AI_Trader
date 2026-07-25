"""Command-line presentation for the shared backtest service.

The service owns all backtest behaviour.  This file intentionally contains no
trading rules so command-line and dashboard runs cannot diverge.
"""

from services.backtest_service import run_backtest


def main():
    print("=" * 60)
    print("EGX AI Trader Backtest")
    print("=" * 60)

    final = None
    for update in run_backtest():
        if update["type"] == "progress":
            print(
                f"[{update['current']}/{update['total']}] "
                f"{update['symbol']}"
            )
        elif update["type"] == "stage":
            print(f"[{update['phase']}] {update.get('message', '')}")
        elif update["type"] == "started":
            print(f"Run ID: {update['run_id']}")
        elif update["type"] == "finished":
            final = update

    if final is None:
        print("Backtest did not produce a result.")
        return

    print("\nBACKTEST SUMMARY (Portfolio-Realistic)")
    for key, value in final["summary"].items():
        print(f"{key:20} : {value}")

    print("\nREJECTED TRADES (Signal Level)")
    for reason, count in sorted(
        final["signal_rejections"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(f"{reason:15} : {count}")

    print("\nREJECTED TRADES (Portfolio Level)")
    for reason, count in sorted(
        final["portfolio_rejections"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(f"{reason:15} : {count}")

    print("\nRUN SUMMARY")
    print(f"Symbols Loaded     : {final['symbols']}")
    print(f"Successful         : {final['successful']}")
    print(f"Failed             : {final['failed']}")
    print(f"Total Signals      : {final['signals']}")
    print(f"Total Trades       : {final['trades']}")
    print(f"Elapsed            : {final['elapsed']}")

    if final.get("walk_forward"):
        comparison = final["comparison"]
        print("\nWALK-FORWARD AI VALIDATION")
        print(f"OOS Accuracy       : {final['walk_forward']['accuracy']}")
        print(f"OOS F1             : {final['walk_forward']['f1']}")
        print(f"Strategy PF        : {comparison['StrategyOnlyProfitFactor']}")
        print(f"AI-filtered PF     : {comparison['AIFilteredProfitFactor']}")
        print(f"AI Rejected        : {comparison['AIRejectedTrades']}")


if __name__ == "__main__":
    main()
