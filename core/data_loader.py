import yfinance as yf


def load_data(symbol, period="1y", interval="1d"):
    df = yf.download(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        group_by="column"
    )

    # حل مشكلة MultiIndex
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    return df