import yfinance as yf
import pandas as pd


MIN_BARS = 250


def load_data(
    symbol,
    period="10y",
    interval="1d"
):

    df = yf.download(

        symbol,

        period=period,

        interval=interval,

        auto_adjust=False,

        progress=False,

        threads=False

    )

    # ==========================
    # No Data
    # ==========================

    if df is None or df.empty:

        raise ValueError(

            f"No data found for {symbol}"

        )

    # ==========================
    # Multi Index
    # ==========================

    if isinstance(df.columns, pd.MultiIndex):

        df.columns = df.columns.get_level_values(0)

    # ==========================
    # Keep Needed Columns
    # ==========================

    required = [

        "Open",
        "High",
        "Low",
        "Close",
        "Volume"

    ]

    df = df[required].copy()

    # ==========================
    # Clean
    # ==========================

    df = df.dropna()

    df = df[df["Volume"] > 0]

    # ==========================
    # Enough History
    # ==========================

    if len(df) < MIN_BARS:

        raise ValueError(

            f"Not enough history ({len(df)} bars)"

        )

    df.reset_index(inplace=True)

    df.set_index("Date", inplace=True)

    return df