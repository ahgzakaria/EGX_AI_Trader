"""Validated symbol loading shared by live scans, watchlists and backtests.

Membership always originates from the authoritative universe in
:mod:`core.universe` (the official EODHD EGX active list). The retired
265-symbol ``data/symbols.csv`` is archived and refused here, so no caller can
silently fall back to it.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.universe import (
    UNIVERSE_SOURCE,
    read_symbol_frame,
    UniverseUnavailable,
    active_universe,
    canonical,
    display_label as universe_display_label,
)

#: The one operational symbol source. Callers pass this instead of a literal path.
SYMBOL_SOURCE = UNIVERSE_SOURCE

#: Paths retired by the EODHD 241 migration. Reading one is a defect, not a
#: fallback, so it fails loudly wherever it survives.
RETIRED_SYMBOL_SOURCES = frozenset({
    "data/symbols.csv",
    "data\\symbols.csv",
})


@dataclass(frozen=True)
class ApprovedSymbol:
    """One approved UI symbol plus its full company name.

    ``ticker`` is the canonical base symbol consumed by strategy and analysis
    code. ``english_name`` carries the authoritative EODHD company name so every
    selector can render ``TICKER — Full Company Name``. This loader performs no
    provider, network, price, indicator, or analysis operation.
    """

    ticker: str
    source_symbol: str
    english_name: str = ""
    arabic_name: str = ""
    eodhd_symbol: str = ""
    is_active: bool = True

    @property
    def company_name(self):
        """The full company name, whatever local field supplied it."""

        return self.english_name or self.arabic_name

    @property
    def display_label(self):
        """``COMI — Commercial International Bank-Egypt (CIB)``."""

        return universe_display_label(self.ticker) if not self.company_name \
            else f"{self.ticker} — {self.company_name}"


def _base_symbol(value):
    """Normalize any accepted spelling into one canonical UI ticker."""

    return canonical(value)


def _reject_retired_source(source):
    """Refuse the archived 265-symbol universe instead of degrading to it."""

    if isinstance(source, (str, Path)):
        text = str(source).replace("/", "\\")
        if any(text.endswith(retired.replace("/", "\\"))
               for retired in RETIRED_SYMBOL_SOURCES):
            raise UniverseUnavailable(
                f"'{source}' is the retired 265-symbol universe and was archived by "
                "the EODHD 241 migration. Load core.universe.UNIVERSE_SOURCE; there "
                "is no operational fallback."
            )


def _clean_metadata(value):
    """Keep optional names missing instead of rendering pandas ``NaN``."""

    return str(value).strip() if pd.notna(value) else ""


def _universe_frame_tickers(frame):
    """Active engine tickers from an authoritative-universe frame, else ``None``."""

    if "canonical_symbol" not in frame.columns:
        return None
    rows = frame
    if "is_active" in frame.columns:
        active = frame["is_active"].astype(str).str.strip().str.lower().isin(
            {"1", "true", "yes", "y", "active"}
        )
        rows = frame[active]

    column = "engine_symbol" if "engine_symbol" in rows.columns else "canonical_symbol"
    return rows[column].tolist()


def load_symbols(source=SYMBOL_SOURCE):
    """Return unique, non-empty tickers from a CSV path or iterable.

    Keeping this at the application boundary prevents dashboard, optimizer and
    backtest from silently using different symbol-cleaning rules. An
    authoritative-universe file yields only its ACTIVE rows.
    """
    _reject_retired_source(source)
    if isinstance(source, (str, Path)):
        try:
            frame = read_symbol_frame(source)
        except (OSError, pd.errors.ParserError) as error:
            raise ValueError(f"Unable to read symbols source: {error}") from error

        values = _universe_frame_tickers(frame)
        if values is None:
            if "Ticker" not in frame.columns:
                raise ValueError("Symbols file must contain a 'Ticker' column")
            values = frame["Ticker"].tolist()
    else:
        try:
            values = list(source)
        except TypeError as error:
            raise ValueError("Symbols source must be a CSV path or iterable") from error

    symbols = []
    seen = set()
    for value in values:
        symbol = str(value).strip() if pd.notna(value) else ""
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)

    return symbols


def load_approved_symbol_options(source=SYMBOL_SOURCE):
    """Return the complete de-duplicated approved universe for UI selection.

    Membership is the ACTIVE authoritative universe, and every option carries its
    full company name so a selector can show ``TICKER — Full Company Name`` while
    still returning the canonical ticker to strategy code.
    """

    _reject_retired_source(source)
    if str(source) == str(SYMBOL_SOURCE):
        return tuple(
            ApprovedSymbol(
                ticker=record.canonical_symbol,
                source_symbol=record.engine_symbol,
                english_name=record.company_name,
                eodhd_symbol=record.eodhd_symbol,
                is_active=True,
            )
            for record in sorted(active_universe(),
                                 key=lambda item: item.canonical_symbol)
        )

    approved = load_symbols(source)
    metadata = {}
    if isinstance(source, (str, Path)):
        try:
            frame = read_symbol_frame(source)
        except (OSError, pd.errors.ParserError) as error:
            raise ValueError(f"Unable to read symbols source: {error}") from error
        for row in frame.to_dict("records"):
            ticker = _base_symbol(row.get("Ticker", ""))
            if not ticker:
                continue
            current = metadata.setdefault(
                ticker,
                {"source_symbol": str(row.get("Ticker", "")).strip(),
                 "english_name": "", "arabic_name": ""},
            )
            # Duplicate aliases may contribute missing metadata, but never
            # create a second option or overwrite an existing approved name.
            current["english_name"] = (
                current["english_name"]
                or _clean_metadata(row.get("EnglishName"))
            )
            current["arabic_name"] = (
                current["arabic_name"]
                or _clean_metadata(row.get("ArabicName"))
            )

    options = {}
    for source_symbol in approved:
        ticker = _base_symbol(source_symbol)
        if not ticker or ticker in options:
            continue
        names = metadata.get(ticker, {})
        options[ticker] = ApprovedSymbol(
            ticker=ticker,
            source_symbol=names.get("source_symbol") or str(source_symbol).strip(),
            english_name=names.get("english_name", ""),
            arabic_name=names.get("arabic_name", ""),
        )
    return tuple(sorted(options.values(), key=lambda option: option.ticker))


def search_approved_symbol_options(options, query, *, limit=50):
    """Rank full-universe local matches without performing any analysis.

    Ranking is exact ticker, ticker prefix, ticker substring, name prefix, then
    name substring. Matching is case-insensitive, whitespace-normalized, and
    tolerant of the EGX ``.CA`` suffix.
    """

    normalized_query = " ".join(str(query or "").strip().casefold().split())
    ticker_query = (
        normalized_query[:-3]
        if normalized_query.endswith(".ca")
        else normalized_query
    )

    ranked = []
    for option in options:
        ticker = option.ticker.casefold()
        full_ticker = f"{ticker}.ca"
        names = tuple(
            " ".join(name.casefold().split())
            for name in (option.english_name, option.arabic_name)
            if name
        )
        if not normalized_query:
            rank = 5
        elif ticker_query == ticker or normalized_query == full_ticker:
            rank = 0
        elif ticker.startswith(ticker_query):
            rank = 1
        elif ticker_query in ticker:
            rank = 2
        elif any(name.startswith(normalized_query) for name in names):
            rank = 3
        elif any(normalized_query in name for name in names):
            rank = 4
        else:
            continue
        ranked.append((rank, option.ticker, option))

    matches = [item[2] for item in sorted(ranked)]
    if limit is None:
        return tuple(matches)
    return tuple(matches[:max(0, int(limit))])


def resolve_approved_symbol(value, options):
    """Resolve only an exact approved option/ticker/name; reject arbitrary text."""

    by_ticker = {option.ticker: option for option in options}
    if isinstance(value, ApprovedSymbol):
        return by_ticker.get(value.ticker)
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    ticker = _base_symbol(text)
    if ticker in by_ticker:
        return by_ticker[ticker]
    folded = text.casefold()
    exact_names = [
        option for option in options
        if folded in {
            option.english_name.casefold(),
            option.arabic_name.casefold(),
            option.display_label.casefold(),
        }
    ]
    return sorted(exact_names, key=lambda option: option.ticker)[0] if exact_names else None


def load_active_symbols(source=SYMBOL_SOURCE):
    """Return the canonical universe, honoring an optional Active flag.

    Existing Scanner/Backtest callers remain on ``load_symbols`` unchanged.
    The external collector alone uses this additive deployment filter. The
    authoritative universe already filters on ``is_active``; a legacy curated CSV
    with no Active column keeps every row so no symbol is silently removed.
    """

    _reject_retired_source(source)
    if not isinstance(source, (str, Path)):
        return load_symbols(source)
    try:
        frame = read_symbol_frame(source)
    except (OSError, pd.errors.ParserError) as error:
        raise ValueError(f"Unable to read symbols source: {error}") from error
    universe_values = _universe_frame_tickers(frame)
    if universe_values is not None:
        return load_symbols(universe_values)
    if "Ticker" not in frame.columns:
        raise ValueError("Symbols file must contain a 'Ticker' column")
    if "Active" in frame.columns:
        active = frame["Active"].astype(str).str.strip().str.lower().isin(
            {"1", "true", "yes", "y", "active"}
        )
        frame = frame[active]
    return load_symbols(frame["Ticker"].tolist())


def filter_by_spread(symbols, maximum_percent, spreads=None):
    """Keep only symbols quoting at or inside `maximum_percent`.

    Separate from `load_symbols` on purpose. That function is the shared
    boundary the dashboard, optimizer and backtest all pass through, and its
    whole value is that they cannot diverge on symbol cleaning. This is a
    strategy-scoped universe decision, so it is applied by the caller that wants
    it rather than imposed on every caller.

    `maximum_percent` of None means no filter, which is the default everywhere.

    Symbols with no measured spread are **dropped** when a filter is active. The
    point of the filter is to trade only what is known to be cheap, and an
    unmeasured symbol is not known to be anything. Failing open here would
    quietly re-admit exactly the illiquid names the filter exists to exclude.

    Returns `(kept, dropped_reasons)` so a caller can report what it removed
    instead of silently shrinking its own universe.
    """
    if maximum_percent is None:
        return list(symbols), {}

    from backtesting.costs import spread_table

    table = spread_table()
    kept, dropped = [], {"too_wide": [], "unmeasured": []}
    for symbol in symbols:
        key = str(symbol).strip().upper()
        value = table.get(key)
        if value is None and key.endswith(".CA"):
            value = table.get(key[:-3])
        if value is None:
            dropped["unmeasured"].append(symbol)
        elif value > maximum_percent:
            dropped["too_wide"].append(symbol)
        else:
            kept.append(symbol)
    return kept, dropped


#: The currency this account settles in.
SETTLEMENT_CURRENCY = "EGP"


def foreign_quoted_symbols(source=SYMBOL_SOURCE):
    """Return the universe entries priced in something other than EGP.

    Twelve EGX symbols are quoted in dollars and one in euros, and the universe
    called all thirteen EGP until their own reported turnover gave them away:
    ``turnover / (volume x close)`` is the price over the price for an EGP
    symbol -- COMI sits at 1.0004 -- and the exchange rate for these, 5.77 in
    2005 and 48.36 in 2024, tracking the currency year by year. One of them is
    named "Faisal Islamic Bank of Egypt - In US Dollars" in that very file.
    """

    try:
        frame = read_symbol_frame(source)
    except (OSError, pd.errors.ParserError):
        return []
    if "currency" not in frame.columns or "canonical_symbol" not in frame.columns:
        return []
    # fillna before the string methods: .str.upper() propagates NaN rather than
    # producing "NAN", so a blank currency compared unequal to everything and
    # eleven rows with no currency at all came back as foreign.
    currency = frame["currency"].fillna("").astype(str).str.strip().str.upper()
    foreign = frame[~currency.isin({SETTLEMENT_CURRENCY, "", "NAN", "NONE"})]
    column = "engine_symbol" if "engine_symbol" in foreign.columns else "canonical_symbol"
    return [str(value) for value in foreign[column].tolist()]


def load_tradeable_symbols(source=SYMBOL_SOURCE):
    """``load_active_symbols`` minus what an EGP account cannot buy.

    Deliberately a second function rather than a filter inside the first.
    ``load_active_symbols`` answers what the exchange lists and this project
    tracks -- 241 symbols, a number a migration validated and several tests
    pin -- and the collector is right to keep receiving all of it. Whether an
    order could be placed is a different question, and the answer changes with
    the account rather than with the exchange.

    Ten of the foreign-quoted symbols had already produced forward-test
    signals, which is a recommendation to buy something the account cannot
    settle.
    """

    excluded = {str(symbol).upper() for symbol in foreign_quoted_symbols(source)}
    return [symbol for symbol in load_active_symbols(source)
            if str(symbol).upper() not in excluded]
