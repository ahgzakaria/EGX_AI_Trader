"""The single authoritative EGX operational universe.

Membership comes from the official EODHD ``exchange-symbol-list/EGX`` active
ticker list, materialized by ``scripts/migrate_eodhd_241_universe.py`` into one
structured file (``data/universe/egx_universe.csv``). Every operational
consumer — daily refresh, historical loading, scans, the Stable Range-Bound and
Uptrend Pullback selectors, AI analysis, backtests, Watchlist, Stock Details and
Rubix subscription planning — reads membership from here and nowhere else.

Four deliberate properties:

* **No fallback.** A missing, empty, malformed or duplicated universe file
  raises :class:`UniverseUnavailable`. The retired 265-symbol
  ``data/symbols.csv`` list is archived under ``data/universe/archive/`` and is
  never consulted at runtime.
* **Never ticker-only.** Every record carries the full company name, so any page
  or report can render ``TICKER — Full Company Name``.
* **Inactive rows stay readable.** Symbols that left the exchange are kept in the
  same file with ``is_active`` false. They can never enter an operational
  selector, but historical trades, saved runs and reports can still resolve their
  archived name.
* **One instrument, one active ticker.** EODHD lists some companies under a
  second code: a copy of the live ticker's series (``AUTO`` for ``GBCO``) or
  the ticker a renamed company traded under before (``ARVA`` for ``AMII``).
  Both were active, so every scan counted the company twice. Those codes are
  registered in ``data/universe/symbol_aliases.csv``; an alias that is active,
  or that points at a ticker which is not, raises.

This module performs no network, price, indicator or strategy operation.
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from pathlib import Path

#: The one authoritative universe file.
UNIVERSE_SOURCE = "data/universe/egx_universe.csv"

#: The retired pre-migration universe, kept for audit only. Loading it at runtime
#: is a defect; it is referenced here purely so tests can assert it is inert.
ARCHIVED_LEGACY_SOURCE = "data/universe/archive/legacy_symbols_265.csv"

#: Timestamped raw EODHD responses captured at migration time.
SNAPSHOT_DIR = "data/universe/snapshots"

#: EODHD codes that duplicate an instrument already listed under its live EGX
#: ticker. The registry sits beside the universe file it governs, so a universe
#: loaded from any other directory carries its own registry or none.
ALIAS_FILENAME = "symbol_aliases.csv"
ALIAS_FIELDNAMES = ("alias_symbol", "live_symbol", "isin", "evidence", "reviewed_on")

#: Listed but not trading. EODHD's exchange list still names these as active,
#: and prints a zero-volume placeholder bar for them every session, so a
#: rebuild from it would put them straight back into every scan. SIMO's last
#: session with any volume was 2014-03-03; it was scanned, and refused as
#: 3,278 sessions stale, on every run until 2026-09-24.
DORMANT_FILENAME = "dormant_symbols.csv"
DORMANT_FIELDNAMES = ("symbol", "isin", "last_traded", "evidence", "reviewed_on")

EXCHANGE = "EGX"
EODHD_SUFFIX = ".EGX"
#: Legacy internal alias still used as the wire format by providers, on-disk
#: caches and recorded paper trades. It is an identifier only — no Yahoo route
#: participates in universe membership.
ENGINE_SUFFIX = ".CA"
RUBIX_EXCHANGE = "CASE"

EM_DASH = "—"
INACTIVE_NAME = "Historical / Inactive Symbol"

FIELDNAMES = (
    "canonical_symbol",
    "eodhd_symbol",
    "engine_symbol",
    "company_name",
    "exchange",
    "currency",
    "instrument_type",
    "isin",
    "rubix_symbol",
    "rubix_mapping_status",
    "is_active",
    "source",
    "source_as_of",
)

#: Rubix mapping is explicit: a subscription key exists only for a ticker actually
#: observed on the Rubix feed under the CASE exchange. Nothing is suffix-guessed.
RUBIX_VERIFIED = "VERIFIED_FEED_OBSERVED"
RUBIX_UNVERIFIED = "UNVERIFIED_NO_FEED_OBSERVATION"


class UniverseUnavailable(RuntimeError):
    """The authoritative universe could not be loaded — never fall back."""


@dataclass(frozen=True)
class UniverseSymbol:
    """One universe record. ``canonical_symbol`` is the value strategy code uses."""

    canonical_symbol: str
    eodhd_symbol: str
    engine_symbol: str
    company_name: str
    exchange: str
    currency: str
    instrument_type: str
    isin: str
    rubix_symbol: str
    rubix_mapping_status: str
    is_active: bool
    source: str
    source_as_of: str

    @property
    def display_label(self) -> str:
        """``GRCA — Grand Investment Capital`` (never a bare ticker)."""

        name = self.company_name.strip() or INACTIVE_NAME
        return f"{self.canonical_symbol} {EM_DASH} {name}"

    @property
    def has_verified_rubix_mapping(self) -> bool:
        return bool(self.rubix_symbol) and self.rubix_mapping_status == RUBIX_VERIFIED


#: EODHD lists a ticker literally spelled ``NULL`` (Fitness Prime, kept inactive
#: as an alias of FTNS), and saved runs still name it. pandas' default NA tokens
#: include "NULL", "NA", "N/A", "NaN" and "None", so a plain ``pd.read_csv``
#: silently turns that ticker into NaN — and any ``dropna()`` downstream then
#: deletes the row outright. Every reader of a symbol-bearing file must therefore
#: go through :func:`read_symbol_frame`.
NA_SAFE_READ_OPTIONS = {"keep_default_na": False, "na_values": [""]}


def read_symbol_frame(path_or_buffer, **kwargs):
    """``pd.read_csv`` that never mistakes a real ticker for a missing value.

    Only a genuinely EMPTY cell counts as missing, so numeric columns still
    parse normally while ``NULL``, ``NA`` and ``NaN`` survive as literal tickers.
    """

    import pandas as pd

    options = dict(NA_SAFE_READ_OPTIONS)
    options.update(kwargs)
    return pd.read_csv(path_or_buffer, **options)


def _is_missing(value) -> bool:
    """True only for a genuinely absent value (None, float NaN, pandas NA).

    Deliberately identity/type based: the STRINGS ``"NULL"``, ``"NA"`` and
    ``"NAN"`` are legitimate tickers and must never be treated as missing.
    """

    if value is None:
        return True
    if isinstance(value, float) and value != value:          # float NaN
        return True
    return value.__class__.__name__ in {"NAType", "NaTType"}  # pandas NA / NaT


def canonical(symbol) -> str:
    """Normalize any accepted spelling to the canonical ticker.

    Accepts ``GRCA``, ``GRCA.EGX``, the legacy ``GRCA.CA`` alias and the Rubix
    ``CASE~GRCA`` subscription key.

    A real float NaN is MISSING and yields ``""`` — it must never be stringified
    into a bogus ``NAN`` ticker. The literal strings ``NULL`` and ``NA`` are the
    opposite case: they are legitimate EGX tickers and are preserved verbatim.
    """

    if _is_missing(symbol):
        return ""
    text = str(symbol).strip().upper()
    if not text:
        return ""
    if "~" in text:
        text = text.rsplit("~", 1)[-1]
    for suffix in (EODHD_SUFFIX, ENGINE_SUFFIX, ".EGY"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "active"}


_CACHE = {}
_ALIAS_CACHE = {}
_LOCK = threading.Lock()


def clear_cache():
    """Drop the parsed universe (tests and the migration script use this)."""

    with _LOCK:
        _CACHE.clear()
        _ALIAS_CACHE.clear()


def read_alias_registry(universe_path=None):
    """``{alias: live ticker}`` from the registry beside the universe file.

    An absent registry means no aliases. A present but malformed one raises: an
    alias row that cannot be read is a duplicate that cannot be excluded.
    """

    path = Path(universe_path or UNIVERSE_SOURCE).with_name(ALIAS_FILENAME)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise UniverseUnavailable(f"Alias registry unreadable at '{path}': {error}") from error

    reader = csv.DictReader(text.splitlines())
    missing = [name for name in ALIAS_FIELDNAMES if name not in (reader.fieldnames or [])]
    if missing:
        raise UniverseUnavailable(
            f"Alias registry '{path}' is missing columns: {', '.join(missing)}"
        )
    aliases = {}
    for line_no, row in enumerate(reader, start=2):
        alias = canonical(row.get("alias_symbol"))
        live = canonical(row.get("live_symbol"))
        if not alias or not live or alias == live:
            raise UniverseUnavailable(f"Malformed alias row in '{path}' line {line_no}")
        if alias in aliases:
            raise UniverseUnavailable(f"Duplicate alias '{alias}' in '{path}'")
        aliases[alias] = live
    chained = sorted(set(aliases) & set(aliases.values()))
    if chained:
        raise UniverseUnavailable(
            f"Alias registry '{path}' names {', '.join(chained)} as both alias and live ticker"
        )
    return aliases


def read_dormant_registry(universe_path=None):
    """``{symbol: last traded session}`` for listed symbols that do not trade.

    Absent means none. Present but malformed raises, for the same reason the
    alias registry does: a row that cannot be read is a symbol that cannot be
    kept out of the scan.
    """

    path = Path(universe_path or UNIVERSE_SOURCE).with_name(DORMANT_FILENAME)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise UniverseUnavailable(f"Dormant registry unreadable at '{path}': {error}") from error

    reader = csv.DictReader(text.splitlines())
    missing = [name for name in DORMANT_FIELDNAMES if name not in (reader.fieldnames or [])]
    if missing:
        raise UniverseUnavailable(
            f"Dormant registry '{path}' is missing columns: {', '.join(missing)}"
        )
    dormant = {}
    for line_no, row in enumerate(reader, start=2):
        symbol = canonical(row.get("symbol"))
        if not symbol or not str(row.get("last_traded") or "").strip():
            raise UniverseUnavailable(f"Malformed dormant row in '{path}' line {line_no}")
        if symbol in dormant:
            raise UniverseUnavailable(f"Duplicate dormant symbol '{symbol}' in '{path}'")
        dormant[symbol] = str(row["last_traded"]).strip()
    return dormant


def _check_dormant(records, dormant, path):
    for record in records:
        if record.canonical_symbol in dormant and record.is_active:
            raise UniverseUnavailable(
                f"'{record.canonical_symbol}' is registered as dormant (last traded "
                f"{dormant[record.canonical_symbol]}) but is active in '{path}'; "
                "every scan would refuse it as stale"
            )


def _check_aliases(records, aliases, path):
    by_symbol = {record.canonical_symbol: record for record in records}
    for alias, live in sorted(aliases.items()):
        record = by_symbol.get(alias)
        if record is None:
            continue
        if record.is_active:
            raise UniverseUnavailable(
                f"'{alias}' is registered as an alias of '{live}' but is active in "
                f"'{path}'; the same instrument would be scanned twice"
            )
        target = by_symbol.get(live)
        if target is None or not target.is_active:
            raise UniverseUnavailable(
                f"Alias '{alias}' points at '{live}', which is not an active symbol "
                f"in '{path}'"
            )


def _parse(path: Path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise UniverseUnavailable(
            f"Authoritative universe unreadable at '{path}': {error}. "
            "Run scripts/migrate_eodhd_241_universe.py; the retired 265-symbol "
            "list is archived and must not be used as a fallback."
        ) from error

    reader = csv.DictReader(text.splitlines())
    missing = [name for name in FIELDNAMES if name not in (reader.fieldnames or [])]
    if missing:
        raise UniverseUnavailable(
            f"Authoritative universe '{path}' is missing columns: {', '.join(missing)}"
        )

    records = []
    seen_canonical = set()
    seen_eodhd = set()
    for line_no, row in enumerate(reader, start=2):
        symbol = canonical(row.get("canonical_symbol"))
        if not symbol:
            raise UniverseUnavailable(f"Blank canonical_symbol in '{path}' line {line_no}")
        name = str(row.get("company_name") or "").strip()
        active = _truthy(row.get("is_active"))
        if active and not name:
            raise UniverseUnavailable(
                f"Active symbol '{symbol}' has no company name in '{path}' line {line_no}"
            )
        eodhd_symbol = str(row.get("eodhd_symbol") or "").strip().upper()
        if active and not eodhd_symbol.endswith(EODHD_SUFFIX):
            raise UniverseUnavailable(
                f"Active symbol '{symbol}' must carry the official '{EODHD_SUFFIX}' "
                f"suffix in '{path}' line {line_no} (found '{eodhd_symbol}')"
            )
        if symbol in seen_canonical:
            raise UniverseUnavailable(f"Duplicate canonical symbol '{symbol}' in '{path}'")
        seen_canonical.add(symbol)
        if eodhd_symbol:
            if eodhd_symbol in seen_eodhd:
                raise UniverseUnavailable(
                    f"Duplicate EODHD symbol '{eodhd_symbol}' in '{path}'"
                )
            seen_eodhd.add(eodhd_symbol)

        records.append(UniverseSymbol(
            canonical_symbol=symbol,
            eodhd_symbol=eodhd_symbol,
            engine_symbol=str(row.get("engine_symbol") or "").strip().upper()
            or f"{symbol}{ENGINE_SUFFIX}",
            company_name=name,
            exchange=str(row.get("exchange") or "").strip().upper(),
            currency=str(row.get("currency") or "").strip().upper(),
            instrument_type=str(row.get("instrument_type") or "").strip(),
            isin=str(row.get("isin") or "").strip(),
            rubix_symbol=str(row.get("rubix_symbol") or "").strip().upper(),
            rubix_mapping_status=str(row.get("rubix_mapping_status") or "").strip(),
            is_active=active,
            source=str(row.get("source") or "").strip(),
            source_as_of=str(row.get("source_as_of") or "").strip(),
        ))

    if not records:
        raise UniverseUnavailable(f"Authoritative universe '{path}' contains no rows")
    if not any(record.is_active for record in records):
        raise UniverseUnavailable(
            f"Authoritative universe '{path}' contains no active symbol"
        )
    bad_exchange = sorted(
        {r.canonical_symbol for r in records if r.is_active and r.exchange != EXCHANGE}
    )
    if bad_exchange:
        raise UniverseUnavailable(
            f"Active symbols outside {EXCHANGE} in '{path}': {', '.join(bad_exchange)}"
        )
    return tuple(records)


def load_universe(path=None):
    """Return every universe record (active first, then archived), sorted.

    Raises :class:`UniverseUnavailable` rather than degrading to any other list.
    """

    resolved = Path(path or UNIVERSE_SOURCE)
    key = str(resolved)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    records = tuple(sorted(_parse(resolved),
                           key=lambda r: (not r.is_active, r.canonical_symbol)))
    aliases = read_alias_registry(resolved)
    _check_aliases(records, aliases, resolved)
    _check_dormant(records, read_dormant_registry(resolved), resolved)
    with _LOCK:
        _CACHE[key] = records
        _ALIAS_CACHE[key] = aliases
    return records


def live_symbol(symbol, path=None) -> str:
    """The live ticker for any spelling: the ticker an alias duplicates, or itself."""

    ticker = canonical(symbol)
    load_universe(path)
    with _LOCK:
        aliases = _ALIAS_CACHE.get(str(Path(path or UNIVERSE_SOURCE)), {})
    return aliases.get(ticker, ticker)


def active_universe(path=None):
    """Only the operational (active) records."""

    return tuple(record for record in load_universe(path) if record.is_active)


def inactive_universe(path=None):
    """Archived records — readable in history, never eligible for new entries."""

    return tuple(record for record in load_universe(path) if not record.is_active)


def active_symbols(path=None):
    """Canonical tickers of the operational universe (``GRCA``)."""

    return tuple(record.canonical_symbol for record in active_universe(path))


def active_engine_symbols(path=None):
    """Operational tickers in the engine/provider wire format (``GRCA.CA``)."""

    return tuple(record.engine_symbol for record in active_universe(path))


def active_eodhd_symbols(path=None):
    """Operational tickers in the official EODHD format (``GRCA.EGX``)."""

    return tuple(record.eodhd_symbol for record in active_universe(path))


def _index(path=None):
    return {record.canonical_symbol: record for record in load_universe(path)}


def lookup(symbol, path=None):
    """Return the record for any accepted spelling, or ``None`` when unknown."""

    return _index(path).get(canonical(symbol))


def is_active(symbol, path=None) -> bool:
    """True only for a symbol eligible for a NEW operational entry."""

    record = lookup(symbol, path)
    return bool(record and record.is_active)


def company_name(symbol, path=None) -> str:
    """Full company name, or the archived name, or '' when nothing is known."""

    record = lookup(symbol, path)
    return record.company_name if record else ""


def display_label(symbol, path=None) -> str:
    """``TICKER — Full Company Name`` for any ticker, including historical ones.

    A ticker with no known name renders ``TICKER — Historical / Inactive Symbol``
    so a report never shows a bare, unexplained code.
    """

    ticker = canonical(symbol)
    if not ticker:
        return ""
    record = lookup(ticker, path)
    if record is not None:
        return record.display_label
    return f"{ticker} {EM_DASH} {INACTIVE_NAME}"


def display_labels(symbols, path=None):
    """Ordered ``TICKER — Name`` labels for an iterable of tickers."""

    return tuple(display_label(symbol, path) for symbol in symbols)


def eligible_for_new_entry(symbols, path=None):
    """Filter an arbitrary list down to symbols that may receive a NEW entry."""

    return tuple(symbol for symbol in symbols if is_active(symbol, path))


def rubix_subscription_symbols(open_position_symbols=(), path=None):
    """Deterministic Rubix subscription keys.

    Active candidates plus any non-active ticker that still holds an open paper
    position and therefore needs exit monitoring. Mappings are explicit: a symbol
    with no verified feed observation contributes no key.
    """

    keys = []
    seen = set()

    def _add(record):
        if record is not None and record.has_verified_rubix_mapping:
            if record.rubix_symbol not in seen:
                seen.add(record.rubix_symbol)
                keys.append(record.rubix_symbol)

    for record in active_universe(path):
        _add(record)
    for symbol in open_position_symbols or ():
        record = lookup(symbol, path)
        if record is not None and not record.is_active:
            _add(record)
    return tuple(keys)


def universe_provenance(path=None):
    """Source and retrieval stamp of the loaded universe, for status surfaces."""

    records = load_universe(path)
    active = [record for record in records if record.is_active]
    return {
        "source": active[0].source if active else "",
        "source_as_of": active[0].source_as_of if active else "",
        "active_count": len(active),
        "archived_count": len(records) - len(active),
        "path": str(Path(path or UNIVERSE_SOURCE)),
    }
