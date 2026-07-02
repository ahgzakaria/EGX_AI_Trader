import pandas as pd

from core.data_loader import load_data
from indicators.technical import calculate_indicators
from core.scoring import score_stock


def scan_symbols(file_path):

    symbols = pd.read_csv(file_path)["Ticker"].tolist()

    results = []

    for symbol in symbols:

        try:

            df = load_data(symbol)
            df = calculate_indicators(df)

            # آخر شمعة
            i = len(df) - 1

            result = score_stock(df, i)

            # ==========================
            # Rating
            # ==========================

            if result["Confidence"] >= 90:
                rating = "A+"

            elif result["Confidence"] >= 80:
                rating = "A"

            elif result["Confidence"] >= 70:
                rating = "B+"

            elif result["Confidence"] >= 60:
                rating = "B"

            else:
                rating = "C"

            results.append({

                "Ticker": symbol,

                "Rating": rating,

                "Price": round(float(df["Close"].iloc[-1]), 2),

                "Signal": result["Signal"],
                "Stars": result["Stars"],

                "Confidence": result["Confidence"],
                "Score": result["Score"],

                "Trend": result["Trend"],
                "Volume": result["Volume"],
                "Momentum": result["Momentum"],
                "Candles": result["Candles"],
                "Breakout": result["Breakout"],

                "Support": result["Support"],
                "Resistance": result["Resistance"],

                "BuyLow": result["BuyLow"],
                "BuyHigh": result["BuyHigh"],

                "StopLoss": result["StopLoss"],

                "Target1": result["Target1"],
                "Target2": result["Target2"],

                "RR": result["RR"],

                "Reasons": " | ".join(result["Reasons"]),

                # هنستخدمه فى صفحة تفاصيل السهم
                "Data": df

            })

        except Exception as e:

            print(f"{symbol} -> {e}")

    results.sort(

        key=lambda x: (

            x["Confidence"],
            x["Score"],
            x["RR"]

        ),

        reverse=True

    )

    # Rank
    for rank, stock in enumerate(results, start=1):

        stock["Rank"] = rank

    return results