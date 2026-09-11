"""Immutable frozen copy of MubasherTrade PRO's daily record, for backtests.

The Yahoo snapshot every backtest here was measured on is neither complete --
33 active symbols have no rows in it -- nor frozen in practice: the Rubix
warm-up loader rewrote six symbols after its freeze
(docs/audits/providers/MUBASHER_VS_YAHOO.md). Mubasher's own record covers the
whole universe, but the terminal back-adjusts history on every download, so a
backtest reading it live would get a different input each time. This store is
one fixed copy of it:

``export/``
    The terminal's Export History CSVs, byte for byte as the owner exported
    them, verified identical to ``history.db`` of the same date on close,
    high, low, volume and turnover before they were accepted.
``history_db/``
    Active symbols the export left out because Mubasher files them under
    another ticker, read from ``history.db`` of the same session and resolved
    by ISIN.
``_manifest.json``
    One record per symbol: file, format, the SHA-256 of its bytes, rows, span
    and source.

Reading verifies the SHA-256 before parsing and refuses a file that no longer
matches. Nothing in this module writes; freezing is
``scripts/freeze_mubasher_export.py``.

**Two export columns are placeholders and are never served as data.** ``Open``
is a copy of the high on every one of 746,434 rows, and ``Prev. Closed`` is -1
on every row. The frame served carries ``Open`` equal to the previous
session's close. That is what the terminal's own ``OP`` column holds, and what
the Yahoo snapshot's open already was on 98.2% of sessions since 2024, so a
backtest sees the same thing it saw before. The provenance says so, so nothing
downstream mistakes it for a traded open.

Prices are split-adjusted and not dividend-adjusted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

FROZEN_DIR = Path("data/frozen_mubasher")
MANIFEST_NAME = "_manifest.json"
PROVIDER = "FROZEN_MUBASHER"
OPEN_POLICY = "PREVIOUS_CLOSE_NOT_A_TRADED_OPEN"
PRICE_ADJUSTMENT = "SPLIT_ADJUSTED_NOT_DIVIDEND_ADJUSTED"
#: The value of the ``backtest_provider`` setting that selects this store, and
#: the data domain its frames carry.
PROVIDER_KEY = "frozen_mubasher"
DATA_DOMAIN = "FROZEN_MUBASHER_BACKTEST_V1"

EXPORT_FORMAT = "mubasher_export_csv"
HISTORY_DB_FORMAT = "history_db_csv"

#: The exact header the terminal writes. A file with any other header is a
#: different export, and a reordered column must fail rather than load turnover
#: out of the volume column.
EXPORT_HEADER = ("Symbol,Date Range,Open,High,Low,Closed,% Change,Change,"
                 "Prev. Closed,Volume,Turnover")
HISTORY_DB_HEADER = "Date,High,Low,Close,Volume,Turnover"

COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume", "Turnover"]


class FrozenStoreTampered(RuntimeError):
    """A frozen file is missing or no longer matches the hash it was frozen with."""


_VERIFIED = set()


def _root(root=None) -> Path:
    return Path(root) if root is not None else FROZEN_DIR


def _base(symbol) -> str:
    return str(symbol).strip().upper().split(".")[0]


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(root=None) -> dict:
    path = _root(root) / MANIFEST_NAME
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def frozen_symbols(root=None) -> list:
    return sorted(read_manifest(root).get("symbols", {}))


def verify(symbol, root=None):
    """The manifest entry for ``symbol`` once its bytes are proven unchanged.

    Returns ``None`` for a symbol the store does not hold. Raises
    ``FrozenStoreTampered`` for one it holds but can no longer prove. A file is
    hashed once per process for a given size and modification time; any change
    to either hashes it again.
    """
    entry = read_manifest(root).get("symbols", {}).get(_base(symbol))
    if entry is None:
        return None
    path = _root(root) / entry["file"]
    if not path.is_file():
        raise FrozenStoreTampered(f"{entry['file']} is missing from the frozen store")
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, entry["sha256"])
    if key not in _VERIFIED:
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            raise FrozenStoreTampered(
                f"{entry['file']}: sha256 {actual[:16]} does not match the frozen "
                f"{entry['sha256'][:16]}")
        _VERIFIED.add(key)
    return entry


def read_export(path) -> pd.DataFrame:
    """One Export History CSV as High, Low, Close, Volume, Turnover by date.

    ``Open`` and ``Prev. Closed`` are not read: both are placeholders.
    """
    raw = pd.read_csv(path, encoding="utf-8-sig")
    raw.columns = [str(column).strip() for column in raw.columns]
    frame = pd.DataFrame({
        "High": pd.to_numeric(raw["High"], errors="coerce").to_numpy(),
        "Low": pd.to_numeric(raw["Low"], errors="coerce").to_numpy(),
        "Close": pd.to_numeric(raw["Closed"], errors="coerce").to_numpy(),
        "Volume": pd.to_numeric(raw["Volume"], errors="coerce").to_numpy(),
        "Turnover": pd.to_numeric(raw["Turnover"], errors="coerce").to_numpy(),
    }, index=pd.DatetimeIndex(pd.to_datetime(raw["Date Range"], errors="coerce"),
                              name="Date"))
    return frame


def read_history_db_copy(path) -> pd.DataFrame:
    raw = pd.read_csv(path, encoding="utf-8")
    frame = pd.DataFrame({
        column: pd.to_numeric(raw[column], errors="coerce").to_numpy()
        for column in ("High", "Low", "Close", "Volume", "Turnover")
    }, index=pd.DatetimeIndex(pd.to_datetime(raw["Date"], errors="coerce"), name="Date"))
    return frame


_READERS = {EXPORT_FORMAT: read_export, HISTORY_DB_FORMAT: read_history_db_copy}


def load_frozen(symbol, root=None):
    """The frozen daily frame for ``symbol``, oldest first, or ``None``.

    Columns are ``COLUMNS``. ``Open`` is the previous close (``OPEN_POLICY``),
    ``Adj Close`` equals ``Close``, and the first row's ``Open`` is NaN because
    no previous close exists. Raises ``FrozenStoreTampered`` rather than serve
    a file that changed.
    """
    entry = verify(symbol, root)
    if entry is None:
        return None
    reader = _READERS.get(entry.get("format"))
    if reader is None:
        raise FrozenStoreTampered(f"{entry['file']}: unknown format {entry.get('format')!r}")
    frame = reader(_root(root) / entry["file"])
    frame = frame[frame.index.notna()]
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    frame["Open"] = frame["Close"].shift(1)
    frame["Adj Close"] = frame["Close"]
    frame = frame[COLUMNS]
    manifest = read_manifest(root)
    frame.attrs["market_data"] = {
        "provider": PROVIDER,
        "effective_provider": PROVIDER,
        "immutable": True,
        "network_used": False,
        "frozen_at": manifest.get("frozen_at"),
        "source": entry.get("source"),
        "file": entry["file"],
        "sha256": entry["sha256"],
        "mubasher_ticker": entry.get("mubasher_ticker"),
        "open_policy": OPEN_POLICY,
        "price_adjustment": PRICE_ADJUSTMENT,
    }
    return frame
