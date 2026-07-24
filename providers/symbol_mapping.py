"""Central symbol conversion for market-data providers.

TickerChart's collector stores ``SYMBOL.EGY`` while the frozen trading engine
uses Yahoo-style ``SYMBOL.CA`` identifiers.  Keeping both directions here
prevents provider-specific conversions from leaking into Scanner or strategy
code.
"""


def to_egx_code(symbol):
    """Return the exchange code without a provider suffix (for example COMI)."""

    value = str(symbol).strip().upper()
    if value in {"^CASE30", "EGX30.EGY", "EGX30.CA"}:
        return "EGX30"
    for suffix in (".CA", ".EGY"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value.split(".", 1)[0]


def to_tickerchart_symbol(symbol):
    """Map the engine symbol to the adapter's subscription/storage symbol."""

    return f"{to_egx_code(symbol)}.EGY"


def to_rubix_symbol(symbol):
    """Map the engine symbol to Rubix SQLite's suffix-free ticker code."""

    return to_egx_code(symbol)


def to_eodhd_symbol(symbol):
    """Map the engine's Yahoo-style EGX ticker to EODHD's EGX suffix."""

    code = to_egx_code(symbol)
    return f"{code}.EGX"


def to_rubix_subscription_symbol(symbol, exchange="CASE"):
    """Map an engine ticker to the external collector's subscription key.

    Rubix subscriptions use ``EXCHANGE~SYMBOL`` while adapter SQLite rows use
    the suffix-free symbol.  Both conversions live here so they cannot drift.
    """

    code = to_egx_code(symbol)
    exchange = str(exchange).strip().upper()
    if not exchange or "~" in exchange:
        raise ValueError(f"Invalid Rubix exchange code: {exchange!r}")
    return f"{exchange}~{code}"


def to_engine_symbol(symbol):
    """Map a TickerChart identifier back to the engine's EGX convention."""

    code = to_egx_code(symbol)
    return "^CASE30" if code == "EGX30" else f"{code}.CA"
