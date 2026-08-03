"""Read-only incremental reader over the existing Rubix collector output.

This module reads a file another process already writes. It is **not** a
collector: it opens no websocket, authenticates to nothing, changes no
subscription and spawns no process. The production launcher remains the sole
owner of collector lifecycle.

Every connection is ``mode=ro`` with ``PRAGMA query_only=ON``, so a write is
refused by SQLite itself rather than by our own discipline. There is no
``ATTACH``, no ``VACUUM``, no checkpoint, no WAL truncation and no schema
change. A WAL reader does not block the writer.

Cursor design, in one line:

    ``quotes.id`` is a *source-progress cursor*, never a market event identity.

``quotes.sequence`` is 100% NULL in the production database, so no ordering or
identity may depend on it. ``id`` is ``INTEGER PRIMARY KEY AUTOINCREMENT``:
monotonic, never reused, and backed by the rowid B-tree. Market identity stays
where Phase 2A put it — ``_source_identity`` and ``_sequence_payload_identity``
— because ``(ticker, market_timestamp)`` collides constantly at the source's
one-second granularity and would silently discard genuinely distinct events.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3

from scalping_orb.config import OrbDataConfig
from scalping_orb.events import RubixQuoteInput, SourceEventType
from scalping_orb.session import OrbSessionClassifier


#: The only source table Phase 2C reads. ``candles_1m`` is deliberately not
#: used: Phase 2A builds quote-derived bars with their own completeness and
#: volume-validity semantics, and a second OHLC source would not carry them.
SOURCE_TABLE = "quotes"

#: Exchange code the collector records for the Egyptian Exchange.
DEFAULT_EXCHANGE = "CASE"


class ShadowSourceUnavailable(RuntimeError):
    """The source could not be read. Never raised for an empty result."""


@dataclass(frozen=True)
class ShadowCursor:
    """Restart-safe position in the collector's append-only quote stream.

    ``last_source_id`` is an *exclusive* low-water mark: the next read is
    ``id > last_source_id``. It is persisted in the same transaction as the
    events derived from that batch, so it can never describe data that was not
    committed.
    """

    source_table: str = SOURCE_TABLE
    last_source_id: int = 0
    last_market_timestamp_utc: datetime | None = None
    last_receive_timestamp_utc: datetime | None = None
    rows_observed: int = 0

    def advanced_to(
        self,
        source_id: int,
        *,
        market_timestamp: datetime | None,
        receive_timestamp: datetime | None,
        rows: int,
    ) -> "ShadowCursor":
        if source_id < self.last_source_id:
            raise ValueError(
                f"cursor cannot move backwards: {self.last_source_id} -> {source_id}"
            )
        return replace(
            self,
            last_source_id=int(source_id),
            last_market_timestamp_utc=market_timestamp,
            last_receive_timestamp_utc=receive_timestamp,
            rows_observed=self.rows_observed + int(rows),
        )


@dataclass(frozen=True)
class SourceBatch:
    """One incremental read. Empty is a normal outcome, not a failure."""

    quotes: tuple[RubixQuoteInput, ...]
    cursor_before: ShadowCursor
    cursor_after: ShadowCursor
    rows_read: int
    exhausted: bool
    latest_market_timestamp_utc: datetime | None
    latest_receive_timestamp_utc: datetime | None

    @property
    def empty(self) -> bool:
        return not self.quotes


def _parse_aware(value) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ShadowSourceReader:
    """Incremental ``mode=ro`` reader over the collector's quote table."""

    def __init__(
        self,
        path,
        config: OrbDataConfig | None = None,
        *,
        batch_size: int = 50_000,
        busy_timeout_ms: int = 30_000,
    ):
        self.path = Path(path)
        self.config = config or OrbDataConfig()
        self.classifier = OrbSessionClassifier(self.config)
        if int(batch_size) < 1:
            raise ValueError("batch_size must be positive")
        self.batch_size = int(batch_size)
        self.busy_timeout_ms = int(busy_timeout_ms)
        if not self.path.is_file():
            raise ShadowSourceUnavailable(f"Rubix source not found: {self.path}")

    @contextmanager
    def connect(self):
        """Read-only, query-only, non-blocking against the live writer."""

        try:
            connection = sqlite3.connect(
                f"file:{self.path.resolve().as_posix()}?mode=ro",
                uri=True,
                timeout=self.busy_timeout_ms / 1000.0,
            )
        except sqlite3.Error as error:  # pragma: no cover - defensive
            raise ShadowSourceUnavailable(str(error)) from error
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            yield connection
        finally:
            connection.close()

    # -- introspection ----------------------------------------------------

    def max_source_id(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT max(id) FROM {SOURCE_TABLE}"
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def session_dates(self) -> tuple[date, ...]:
        """Exchange session dates present, from Cairo-classified market time."""

        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT substr(market_timestamp,1,10) d "
                f"FROM {SOURCE_TABLE} ORDER BY d"
            ).fetchall()
        dates: list[date] = []
        for row in rows:
            try:
                dates.append(date.fromisoformat(row["d"]))
            except (TypeError, ValueError):
                continue
        return tuple(dates)

    def source_id_bounds_for_session(self, session_date: date) -> tuple[int, int]:
        """Inclusive ``id`` range covering one Cairo session, auction included.

        Used to seed a reconstruction cursor without scanning the whole table.
        """

        window = self.classifier.window(session_date)
        with self.connect() as connection:
            row = connection.execute(
                f"""SELECT min(id), max(id) FROM {SOURCE_TABLE}
                    WHERE market_timestamp>=? AND market_timestamp<?""",
                (
                    window.continuous_start_utc.isoformat(),
                    window.auction_end_utc.isoformat(),
                ),
            ).fetchone()
        if not row or row[0] is None:
            return (0, 0)
        return (int(row[0]), int(row[1]))

    # -- incremental read --------------------------------------------------

    def read_batch(
        self,
        cursor: ShadowCursor,
        *,
        session_date: date | None = None,
        limit: int | None = None,
    ) -> SourceBatch:
        """Read the next rows after ``cursor``, in deterministic ``id`` order.

        Ordering by ``id`` is collector *insertion* order — the only ordering
        the source actually guarantees. It is not claimed to be exchange order;
        exchange ordering is re-established downstream from
        ``market_timestamp``.
        """

        if cursor.source_table != SOURCE_TABLE:
            raise ValueError(f"cursor is for {cursor.source_table!r}, not {SOURCE_TABLE!r}")
        size = self.batch_size if limit is None else max(1, int(limit))
        sql = (
            f"SELECT id,upper(ticker) ticker,last_price,bid,ask,volume,"
            f"market_timestamp,received_at,exchange,sequence,has_feed_timestamp "
            f"FROM {SOURCE_TABLE} WHERE id>?"
        )
        parameters: tuple = (int(cursor.last_source_id),)
        if session_date is not None:
            window = self.classifier.window(session_date)
            sql += " AND market_timestamp>=? AND market_timestamp<?"
            parameters += (
                window.continuous_start_utc.isoformat(),
                window.auction_end_utc.isoformat(),
            )
        sql += " ORDER BY id LIMIT ?"
        parameters += (size,)

        try:
            with self.connect() as connection:
                rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.Error as error:
            raise ShadowSourceUnavailable(str(error)) from error

        quotes: list[RubixQuoteInput] = []
        last_id = cursor.last_source_id
        latest_market: datetime | None = None
        latest_receive: datetime | None = None
        for row in rows:
            last_id = int(row["id"])
            market = _parse_aware(row["market_timestamp"])
            received = _parse_aware(row["received_at"])
            if market is None:
                # Unusable exchange time. The cursor still advances past it, so
                # the row is never re-read, but it contributes no evidence.
                continue
            if received is None:
                received = market
            latest_market = market if latest_market is None else max(latest_market, market)
            latest_receive = (
                received if latest_receive is None else max(latest_receive, received)
            )
            ticker = str(row["ticker"] or "").strip().upper()
            exchange = str(row["exchange"] or DEFAULT_EXCHANGE).strip().upper()
            quotes.append(
                RubixQuoteInput(
                    canonical_ticker=ticker,
                    verified_rubix_symbol=f"{exchange}~{ticker}",
                    market_timestamp=market,
                    receive_timestamp=received,
                    # 100% NULL at the source; passed through untouched rather
                    # than synthesised, so downstream sees the truth.
                    sequence=row["sequence"],
                    last_price=row["last_price"],
                    cumulative_volume=row["volume"],
                    bid=row["bid"],
                    ask=row["ask"],
                    has_feed_timestamp=bool(row["has_feed_timestamp"]),
                    source_event_type=SourceEventType.RUBIX_QUOTE,
                    # Progress cursor value, carried so Phase 2A's identity hash
                    # stays row-unique. Never used as market identity itself.
                    source_row_id=int(row["id"]),
                )
            )

        advanced = (
            cursor.advanced_to(
                last_id,
                market_timestamp=latest_market or cursor.last_market_timestamp_utc,
                receive_timestamp=latest_receive or cursor.last_receive_timestamp_utc,
                rows=len(rows),
            )
            if rows
            else cursor
        )
        return SourceBatch(
            quotes=tuple(quotes),
            cursor_before=cursor,
            cursor_after=advanced,
            rows_read=len(rows),
            exhausted=len(rows) < size,
            latest_market_timestamp_utc=latest_market,
            latest_receive_timestamp_utc=latest_receive,
        )


__all__ = [
    "DEFAULT_EXCHANGE",
    "SOURCE_TABLE",
    "ShadowCursor",
    "ShadowSourceReader",
    "ShadowSourceUnavailable",
    "SourceBatch",
]
