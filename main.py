from core.scanner import scan_symbols
from core.symbols import SYMBOL_SOURCE


def print_stock(stock):

    print("=" * 70)

    print(f"📈 {stock['Ticker']}")

    print(f"السعر الحالي : {stock['Price']:.2f}")

    print(f"Score        : {stock['Score']}")

    print(f"Trend Score  : {stock['Trend']}")

    print(f"Volume Score : {stock['Volume']}")

    print()

    print(f"Support      : {stock['Support']:.2f}")

    print(f"Resistance   : {stock['Resistance']:.2f}")

    print()

    print(
        f"Buy Zone     : "
        f"{stock['BuyLow']:.2f}  →  {stock['BuyHigh']:.2f}"
    )

    print(f"Stop Loss    : {stock['StopLoss']:.2f}")

    print(f"Target 1     : {stock['Target1']:.2f}")

    print(f"Target 2     : {stock['Target2']:.2f}")

    print(f"Risk/Reward  : {stock['RR']}")

    print("=" * 70)


def main():

    results = scan_symbols(SYMBOL_SOURCE)

    print("\n")
    print("=" * 70)
    print("            EGX AI TRADER V4.1")
    print("=" * 70)

    for stock in results:

        print_stock(stock)


if __name__ == "__main__":
    main()