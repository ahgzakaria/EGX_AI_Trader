import pandas as pd

from core.data_loader import load_data
from indicators.technical import calculate_indicators
from scoring import score_stock


def scan_symbols(file_path):

    symbols = pd.read_csv(file_path)["Ticker"].tolist()

    results = []

    for symbol in symbols:

        try:

            df = load_data(symbol)

            df = calculate_indicators(df)

            result = score_stock(df)

            results.append({

                "Ticker": symbol,

                "Price": float(df["Close"].iloc[-1]),

                "Score": result["Score"],

                "Trend": result["Trend"],

                "Volume": result["Volume"],

                "Support": result["Support"],

                "Resistance": result["Resistance"],

                "BuyLow": result["BuyLow"],

                "BuyHigh": result["BuyHigh"],

                "StopLoss": result["StopLoss"],

                "Target1": result["Target1"],

                "Target2": result["Target2"],

                "RR": result["RR"],

                "Signal": result["Signal"],

                "Stars": result["Stars"]

            })

        except Exception as e:

            print(f"{symbol} -> {e}")

    results.sort(key=lambda x: x["Score"], reverse=True)

    return results