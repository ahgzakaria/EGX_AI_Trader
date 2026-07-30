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
            frame = pd.read_csv(source)
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
            frame = pd.read_csv(source)
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
        frame = pd.read_csv(source)
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
