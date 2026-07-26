"""Validated symbol loading shared by live scans, watchlists and backtests."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class ApprovedSymbol:
    """One approved UI symbol plus optional local display metadata.

    ``ticker`` is the normalized base symbol consumed by AI Stock Analysis.
    Names are read only from the approved universe file; this loader performs
    no provider, network, price, indicator, or analysis operation.
    """

    ticker: str
    source_symbol: str
    english_name: str = ""
    arabic_name: str = ""

    @property
    def display_label(self):
        names = [name for name in (self.arabic_name, self.english_name) if name]
        return f"{self.ticker} — {' · '.join(names)}" if names else self.ticker


def _base_symbol(value):
    """Normalize the approved EGX ``.CA`` alias into one canonical UI ticker."""

    text = str(value).strip().upper()
    return text[:-3] if text.endswith(".CA") else text


def _clean_metadata(value):
    """Keep optional names missing instead of rendering pandas ``NaN``."""

    return str(value).strip() if pd.notna(value) else ""


def load_symbols(source):
    """Return unique, non-empty tickers from a CSV path or iterable.

    Keeping this at the application boundary prevents dashboard, optimizer and
    backtest from silently using different symbol-cleaning rules.
    """
    if isinstance(source, (str, Path)):
        try:
            frame = pd.read_csv(source)
        except (OSError, pd.errors.ParserError) as error:
            raise ValueError(f"Unable to read symbols source: {error}") from error

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


def load_approved_symbol_options(source="data/symbols.csv"):
    """Return the complete de-duplicated approved universe for UI selection.

    The canonical membership and order come from :func:`load_symbols`. Optional
    ``EnglishName`` and ``ArabicName`` columns in the same approved CSV enrich
    display/search only. ``COMI`` and ``COMI.CA`` therefore become one option,
    while the analysis service receives the normalized ``COMI`` value.
    """

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


def load_active_symbols(source):
    """Return the canonical universe, honoring an optional Active flag.

    Existing Scanner/Backtest callers remain on ``load_symbols`` unchanged.
    The external collector alone uses this additive deployment filter. When
    the canonical CSV has no Active column, its curated rows are treated as
    the active universe and no symbol is silently removed.
    """

    if not isinstance(source, (str, Path)):
        return load_symbols(source)
    try:
        frame = pd.read_csv(source)
    except (OSError, pd.errors.ParserError) as error:
        raise ValueError(f"Unable to read symbols source: {error}") from error
    if "Ticker" not in frame.columns:
        raise ValueError("Symbols file must contain a 'Ticker' column")
    if "Active" in frame.columns:
        active = frame["Active"].astype(str).str.strip().str.lower().isin(
            {"1", "true", "yes", "y", "active"}
        )
        frame = frame[active]
    return load_symbols(frame["Ticker"].tolist())
