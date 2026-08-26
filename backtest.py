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

    # The service reports `errors`, not `successful`/`failed`. Asking it for
    # those keys raised KeyError on every run, after every result had already
    # been written -- so the numbers were correct on disk and the run still
    # looked like a crash. Derived here from what the payload actually carries.
    failed = len(final.get("errors", []))
    print("\nRUN SUMMARY")
    print(f"Symbols Loaded     : {final['symbols']}")
    print(f"Successful         : {final['symbols'] - failed}")
    print(f"Failed             : {failed}")
    print(f"Total Signals      : {final['signals']}")
    print(f"Total Trades       : {final['trades']}")
    print(f"Elapsed            : {final['elapsed']}")

    if final.get("walk_forward"):
        print("\nWALK-FORWARD AI VALIDATION")
        print(f"OOS Accuracy       : {final['walk_forward']['accuracy']}")
        print(f"OOS F1             : {final['walk_forward']['f1']}")
        # `comparison` is absent from this payload too. It is only ever
        # populated by the overlay-comparison run, so print it when it is there
        # and say so when it is not, rather than crashing a finished backtest.
        comparison = final.get("comparison")
        if comparison:
            print(f"Strategy PF        : {comparison['StrategyOnlyProfitFactor']}")
            print(f"AI-filtered PF     : {comparison['AIFilteredProfitFactor']}")
            print(f"AI Rejected        : {comparison['AIRejectedTrades']}")
        else:
            print("Strategy vs AI     : not produced by this run mode")


if __name__ == "__main__":
    main()
