"""Durable storage for what the user actually owns.

Transactions are stored, not balances. The average price is never written down
anywhere: it is recomputed from the trades every time it is shown. That is the
whole point of the design -- an average kept as a stored number drifts the
moment a partial sale, a bonus issue or a corrected entry touches it, and
nothing in the file says which of the two numbers is wrong. Here there is only
one number, and it is derived.

This database holds the only data in the project that no provider can rebuild.
A quote, a candle, a scan result can all be fetched again; what the user bought
and at what price exists nowhere else. So:

* every write is a single committed transaction with foreign-key enforcement on;
* a write that would make the history impossible is refused before it lands,
  with the reason, rather than stored and reconciled later;
* nothing is ever silently overwritten -- an exit plan is versioned, and a
  correction to a trade is an explicit delete of a row the user can see.

``data/portfolio.db`` is gitignored like every other ``*.db`` here, so real
positions never reach the repository.
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timezone
import json
from pathlib import Path
import sqlite3

from core.universe import canonical
from holdings import DEFAULT_DATABASE
from holdings.book import (
    BUY,
    CASH_DIVIDEND,
    CASH_EVENTS,
    SELL,
    SPLIT,
    STOCK_DIVIDEND,
    BookError,
    build_book,
)

SCHEMA_VERSION = 1

TRADE_SIDES = (BUY, SELL)
CASH_KINDS = CASH_EVENTS
CORPORATE_ACTION_KINDS = (SPLIT, STOCK_DIVIDEND, CASH_DIVIDEND)

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol      TEXT    NOT NULL,
        trade_date  TEXT    NOT NULL,
        side        TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
        quantity    REAL    NOT NULL CHECK (quantity > 0),
        price       REAL    NOT NULL CHECK (price > 0),
        -- NULL means "charge the configured fee schedule". A number means the
        -- user copied the real total from a contract note, which always wins.
        fees_egp    REAL    CHECK (fees_egp IS NULL OR fees_egp >= 0),
        note        TEXT    NOT NULL DEFAULT '',
        -- The broker's own execution numbers for the invoice this row came
        -- from. Unique per fill, so re-importing the same invoice is a no-op
        -- instead of a doubled position.
        source_reference TEXT NOT NULL DEFAULT '',
        source_file      TEXT NOT NULL DEFAULT '',
        recorded_at TEXT    NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_source_reference "
    "ON trades(source_reference) WHERE source_reference <> ''",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol_date ON trades(symbol, trade_date)",
    """
    CREATE TABLE IF NOT EXISTS cash_movements (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        movement_date TEXT    NOT NULL,
        kind          TEXT    NOT NULL CHECK (kind IN ('DEPOSIT', 'WITHDRAW')),
        amount_egp    REAL    NOT NULL CHECK (amount_egp > 0),
        note          TEXT    NOT NULL DEFAULT '',
        -- Set when the movement came from an invoice (a money-market fund
        -- leg), so re-importing that invoice cannot double the cash.
        source_reference TEXT NOT NULL DEFAULT '',
        source_file      TEXT NOT NULL DEFAULT '',
        recorded_at   TEXT    NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_source_reference "
    "ON cash_movements(source_reference) WHERE source_reference <> ''",
    """
    CREATE TABLE IF NOT EXISTS corporate_actions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol           TEXT    NOT NULL,
        effective_date   TEXT    NOT NULL,
        kind             TEXT    NOT NULL CHECK (
                             kind IN ('SPLIT', 'STOCK_DIVIDEND', 'CASH_DIVIDEND')),
        -- Shares held afterwards per share held before: 2.0 for a 2-for-1
        -- split, 1.1 for a 10% bonus issue. Unused by a cash dividend.
        factor           REAL    CHECK (factor IS NULL OR factor > 0),
        amount_per_share REAL    CHECK (amount_per_share IS NULL OR amount_per_share > 0),
        note             TEXT    NOT NULL DEFAULT '',
        recorded_at      TEXT    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_actions_symbol ON corporate_actions(symbol)",
    """
    -- Exit plans are versioned, never updated. "Why did it want 12.00 last
    -- week and 11.40 today?" has to be answerable, and it cannot be if the
    -- previous answer was overwritten by the current one.
    CREATE TABLE IF NOT EXISTS exit_plans (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol         TEXT    NOT NULL,
        version        INTEGER NOT NULL,
        created_at     TEXT    NOT NULL,
        session_date   TEXT    NOT NULL DEFAULT '',
        stop           REAL,
        target_partial REAL,
        target_final   REAL,
        partial_fraction REAL,
        source         TEXT    NOT NULL DEFAULT '',
        reason         TEXT    NOT NULL DEFAULT '',
        evidence_json  TEXT    NOT NULL DEFAULT '{}',
        superseded_at  TEXT,
        UNIQUE (symbol, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plans_symbol ON exit_plans(symbol, version DESC)",
    """
    -- Every recommendation ever shown, so the rules can be judged later on
    -- what they actually did rather than on how convincing they sounded.
    CREATE TABLE IF NOT EXISTS recommendations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol        TEXT    NOT NULL,
        generated_at  TEXT    NOT NULL,
        session_date  TEXT    NOT NULL DEFAULT '',
        action        TEXT    NOT NULL,
        urgency       TEXT    NOT NULL DEFAULT '',
        rule          TEXT    NOT NULL DEFAULT '',
        reason        TEXT    NOT NULL DEFAULT '',
        price         REAL,
        price_basis   TEXT    NOT NULL DEFAULT '',
        plan_version  INTEGER,
        net_percent   REAL,
        evidence_json TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_recs_symbol ON recommendations(symbol, generated_at DESC)",
)


class StoreError(RuntimeError):
    """A write that was refused, with the reason a human needs to fix it."""


def default_database_path() -> Path:
    """Where the portfolio lives, from settings, or the packaged default.

    Configurable for the same reason every other store here is: a second copy
    of the app, a restored backup or a dry run must be able to point somewhere
    else without editing code. A missing or unreadable section is not an error;
    it means nobody moved the file.
    """

    configured = None
    try:
        from config.settings_manager import settings

        configured = (settings.get("portfolio") or {}).get("database_path")
    except Exception:                                            # noqa: BLE001
        configured = None
    return Path(str(configured).strip() or DEFAULT_DATABASE) if configured \
        else Path(DEFAULT_DATABASE)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _valid_date(value, *, name="date") -> str:
    """Accept a date object or ``YYYY-MM-DD``; refuse anything else."""

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_type):
        return value.isoformat()
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as error:
        raise StoreError(f"{name} must be YYYY-MM-DD, got {value!r}") from error


def _valid_symbol(value) -> str:
    symbol = canonical(value)
    if not symbol:
        raise StoreError(f"not a usable symbol: {value!r}")
    return symbol


def _positive(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise StoreError(f"{name} is not a number: {value!r}") from error
    if not number > 0 or number != number:
        raise StoreError(f"{name} must be greater than zero, got {value!r}")
    return number


class HoldingsStore:
    """Read/write access to one portfolio database."""

    def __init__(self, database_path=None):
        self.database_path = (
            Path(database_path) if database_path else default_database_path()
        )
        self._initialized = False

    # -- connection --------------------------------------------------------- #

    def _connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialize(self):
        """Create the schema once per process; safe to call repeatedly."""

        if self._initialized:
            return
        with self._connect() as connection:
            # Tables, then migrations, then indexes -- in that order and not
            # any other. ``CREATE TABLE IF NOT EXISTS`` does nothing to a table
            # that already exists, so an index naming a column added later
            # would be created against a file that does not have it yet and
            # fail on every page load.
            statements = list(SCHEMA)
            for statement in statements:
                if "CREATE TABLE" in statement:
                    connection.execute(statement)
            self._migrate(connection)
            for statement in statements:
                if "CREATE TABLE" not in statement:
                    connection.execute(statement)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
        self._initialized = True

    @staticmethod
    def _migrate(connection):
        """Add columns a database created by an earlier version is missing.

        ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already
        exists, so a file written before invoice import would silently keep the
        old shape and every insert naming the new columns would fail.
        """

        for table in ("trades", "cash_movements"):
            existing = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column in ("source_reference", "source_file"):
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} "
                        "TEXT NOT NULL DEFAULT ''")

    def _read(self, query, parameters=()):
        self.initialize()
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    # -- reads -------------------------------------------------------------- #

    def trades(self, symbol=None) -> list:
        if symbol:
            return self._read(
                "SELECT * FROM trades WHERE symbol=? ORDER BY trade_date, id",
                (_valid_symbol(symbol),),
            )
        return self._read("SELECT * FROM trades ORDER BY trade_date, id")

    def has_reference(self, reference) -> bool:
        """Has an invoice with these execution numbers already been recorded?"""

        text = str(reference or "")
        if not text:
            return False
        return bool(self._read(
            "SELECT 1 FROM trades WHERE source_reference=? "
            "UNION ALL SELECT 1 FROM cash_movements WHERE source_reference=? LIMIT 1",
            (text, text),
        ))

    def imported_references(self) -> set:
        """Every invoice reference already in the book, for a bulk preview."""

        rows = self._read(
            "SELECT source_reference FROM trades WHERE source_reference <> '' "
            "UNION SELECT source_reference FROM cash_movements "
            "WHERE source_reference <> ''")
        return {str(row["source_reference"]) for row in rows}

    def cash_movements(self) -> list:
        return self._read("SELECT * FROM cash_movements ORDER BY movement_date, id")

    def corporate_actions(self, symbol=None) -> list:
        if symbol:
            return self._read(
                "SELECT * FROM corporate_actions WHERE symbol=? "
                "ORDER BY effective_date, id",
                (_valid_symbol(symbol),),
            )
        return self._read(
            "SELECT * FROM corporate_actions ORDER BY effective_date, id")

    def events(self) -> list:
        """Every stored record as one chronological stream for the book.

        Ids are namespaced per table so that two records created on the same
        date keep a stable, reproducible order instead of depending on which
        table happened to be read first.
        """

        events = []
        for row in self.trades():
            events.append({
                "id": row["id"],
                "kind": row["side"],
                "date": row["trade_date"],
                "symbol": row["symbol"],
                "quantity": row["quantity"],
                "price": row["price"],
                "fees_egp": row["fees_egp"],
                "note": row["note"],
                "source": "trades",
            })
        for row in self.corporate_actions():
            events.append({
                "id": 1_000_000 + row["id"],
                "kind": row["kind"],
                "date": row["effective_date"],
                "symbol": row["symbol"],
                "factor": row["factor"],
                "amount_per_share": row["amount_per_share"],
                "note": row["note"],
                "source": "corporate_actions",
            })
        for row in self.cash_movements():
            events.append({
                "id": 2_000_000 + row["id"],
                "kind": row["kind"],
                "date": row["movement_date"],
                "amount_egp": row["amount_egp"],
                "note": row["note"],
                "source": "cash_movements",
            })
        return events

    def book(self, fee_model=None):
        """The account as it stands. Raises ``BookError`` on impossible history."""

        return build_book(self.events(), fee_model)

    # -- validation --------------------------------------------------------- #

    def _validate(self, events, fee_model=None):
        """Refuse a write that would make the stored history impossible."""

        try:
            build_book(events, fee_model)
        except BookError as error:
            raise StoreError(str(error)) from error

    def _next_event_id(self, table, offset):
        rows = self._read(f"SELECT COALESCE(MAX(id), 0) AS top FROM {table}")
        return offset + int(rows[0]["top"]) + 1

    # -- writes ------------------------------------------------------------- #

    def record_trade(self, symbol, trade_date, side, quantity, price,
                     fees_egp=None, note="", fee_model=None,
                     source_reference="", source_file="") -> int:
        """Record one buy or sell. Returns the new row id.

        ``source_reference`` carries the broker's execution numbers when the
        row came from an invoice. It is unique, so importing the same invoice
        twice is refused by the database rather than by a check that could be
        skipped.
        """

        self.initialize()
        symbol = _valid_symbol(symbol)
        trade_date = _valid_date(trade_date, name="trade date")
        side = str(side or "").strip().upper()
        if side not in TRADE_SIDES:
            raise StoreError(f"side must be BUY or SELL, got {side!r}")
        quantity = _positive(quantity, "quantity")
        price = _positive(price, "price")
        if fees_egp is not None:
            fees_egp = float(fees_egp)
            if fees_egp < 0:
                raise StoreError("fees cannot be negative")

        candidate = {
            "id": self._next_event_id("trades", 0),
            "kind": side, "date": trade_date, "symbol": symbol,
            "quantity": quantity, "price": price, "fees_egp": fees_egp,
        }
        self._validate(self.events() + [candidate], fee_model)

        reference = str(source_reference or "")
        if reference and self.has_reference(reference):
            raise StoreError(
                f"this invoice was already imported ({reference[:40]}...)"
                if len(reference) > 40 else
                f"this invoice was already imported ({reference})"
            )
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO trades (symbol, trade_date, side, quantity, price, "
                    "fees_egp, note, source_reference, source_file, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (symbol, trade_date, side, quantity, price, fees_egp,
                     str(note or ""), reference, str(source_file or ""), _now()),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise StoreError(f"this invoice was already imported: {error}") from error

    def record_cash(self, movement_date, kind, amount_egp, note="",
                    source_reference="", source_file="") -> int:
        """Record a deposit or a withdrawal of cash."""

        self.initialize()
        movement_date = _valid_date(movement_date, name="movement date")
        kind = str(kind or "").strip().upper()
        if kind not in CASH_KINDS:
            raise StoreError(f"kind must be DEPOSIT or WITHDRAW, got {kind!r}")
        amount = _positive(amount_egp, "amount")
        reference = str(source_reference or "")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO cash_movements (movement_date, kind, amount_egp, "
                    "note, source_reference, source_file, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (movement_date, kind, amount, str(note or ""), reference,
                     str(source_file or ""), _now()),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise StoreError(
                f"this cash movement was already imported: {error}") from error

    def record_corporate_action(self, symbol, effective_date, kind, factor=None,
                                amount_per_share=None, note="",
                                fee_model=None) -> int:
        """Record a split, a bonus issue, or a cash dividend.

        ``factor`` is how many shares are held afterwards for each one held
        before -- 1.1 for a 10% bonus issue, 2.0 for a 2-for-1 split. It is the
        form a holder can read straight off the announcement without arithmetic,
        and getting it wrong in the other direction (0.1 for a 10% bonus) is
        caught here rather than after the average has been quietly ruined.
        """

        self.initialize()
        symbol = _valid_symbol(symbol)
        effective_date = _valid_date(effective_date, name="effective date")
        kind = str(kind or "").strip().upper()
        if kind not in CORPORATE_ACTION_KINDS:
            raise StoreError(
                f"kind must be one of {', '.join(CORPORATE_ACTION_KINDS)}, got {kind!r}")
        if kind == CASH_DIVIDEND:
            amount_per_share = _positive(amount_per_share, "dividend per share")
            factor = None
        else:
            factor = _positive(factor, "factor")
            amount_per_share = None

        candidate = {
            "id": self._next_event_id("corporate_actions", 1_000_000),
            "kind": kind, "date": effective_date, "symbol": symbol,
            "factor": factor, "amount_per_share": amount_per_share,
        }
        self._validate(self.events() + [candidate], fee_model)

        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO corporate_actions (symbol, effective_date, kind, "
                "factor, amount_per_share, note, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol, effective_date, kind, factor, amount_per_share,
                 str(note or ""), _now()),
            )
            return int(cursor.lastrowid)

    # -- corrections -------------------------------------------------------- #

    def delete_trade(self, trade_id, fee_model=None) -> bool:
        """Remove one recorded trade, if the rest of the history survives it.

        Deleting the buy that a later sale drew its shares from would leave a
        history that cannot be true, so the deletion is refused and the user is
        told which record depends on it.
        """

        return self._delete("trades", int(trade_id), fee_model)

    def delete_corporate_action(self, action_id, fee_model=None) -> bool:
        return self._delete("corporate_actions", int(action_id), fee_model)

    def delete_cash_movement(self, movement_id) -> bool:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM cash_movements WHERE id=?", (int(movement_id),))
            return cursor.rowcount > 0

    def _delete(self, table, row_id, fee_model=None) -> bool:
        self.initialize()
        offset = 1_000_000 if table == "corporate_actions" else 0
        events = self.events()
        remaining = [
            event for event in events
            if not (event["source"] == table and event["id"] == row_id + offset)
        ]
        if len(remaining) == len(events):
            return False
        self._validate(remaining, fee_model)
        with self._connect() as connection:
            cursor = connection.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
            return cursor.rowcount > 0

    # -- exit plans --------------------------------------------------------- #

    def active_plan(self, symbol):
        """The current plan version for a symbol, or ``None``."""

        rows = self._read(
            "SELECT * FROM exit_plans WHERE symbol=? AND superseded_at IS NULL "
            "ORDER BY version DESC LIMIT 1",
            (_valid_symbol(symbol),),
        )
        return rows[0] if rows else None

    def plan_history(self, symbol) -> list:
        return self._read(
            "SELECT * FROM exit_plans WHERE symbol=? ORDER BY version DESC",
            (_valid_symbol(symbol),),
        )

    def save_plan(self, symbol, *, stop=None, target_partial=None,
                  target_final=None, partial_fraction=None, source="",
                  reason="", evidence=None, session_date="") -> int:
        """Write a new plan version and retire the previous one.

        Always an insert. The superseded row keeps its numbers so the history
        of what was intended, and when it changed, stays readable.
        """

        self.initialize()
        symbol = _valid_symbol(symbol)
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS top FROM exit_plans WHERE symbol=?",
                (symbol,),
            ).fetchone()
            version = int(row["top"]) + 1
            connection.execute(
                "UPDATE exit_plans SET superseded_at=? "
                "WHERE symbol=? AND superseded_at IS NULL",
                (now, symbol),
            )
            connection.execute(
                "INSERT INTO exit_plans (symbol, version, created_at, session_date, "
                "stop, target_partial, target_final, partial_fraction, source, "
                "reason, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, version, now, str(session_date or ""), stop,
                 target_partial, target_final, partial_fraction, str(source or ""),
                 str(reason or ""), json.dumps(evidence or {}, ensure_ascii=False)),
            )
        return version

    def refresh_plan(self, symbol, *, session_date, evidence=None):
        """Restamp the active plan for a new session without a new version.

        A new session that produces the same levels is not a revision. Writing
        a version for it would bury the revisions that meant something under a
        daily stream of identical rows, and "why did it change?" would stop
        being answerable from the version list. The row's session stamp and its
        evidence are updated in place; every level is left exactly as it was.
        """

        self.initialize()
        symbol = _valid_symbol(symbol)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM exit_plans WHERE symbol=? AND superseded_at IS NULL "
                "ORDER BY version DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE exit_plans SET session_date=?, evidence_json=? "
                "WHERE symbol=? AND version=?",
                (str(session_date or ""),
                 json.dumps(evidence or {}, ensure_ascii=False),
                 symbol, int(row["version"])),
            )
            return int(row["version"])

    # -- recommendation log ------------------------------------------------- #

    def log_recommendation(self, symbol, *, action, urgency="", rule="", reason="",
                           price=None, price_basis="", plan_version=None,
                           net_percent=None, evidence=None, session_date="",
                           generated_at=None) -> int:
        """Append one recommendation exactly as it was shown to the user."""

        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO recommendations (symbol, generated_at, session_date, "
                "action, urgency, rule, reason, price, price_basis, plan_version, "
                "net_percent, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_valid_symbol(symbol), generated_at or _now(), str(session_date or ""),
                 str(action), str(urgency or ""), str(rule or ""), str(reason or ""),
                 price, str(price_basis or ""), plan_version, net_percent,
                 json.dumps(evidence or {}, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def recommendations(self, symbol=None, limit=200) -> list:
        if symbol:
            return self._read(
                "SELECT * FROM recommendations WHERE symbol=? "
                "ORDER BY generated_at DESC LIMIT ?",
                (_valid_symbol(symbol), int(limit)),
            )
        return self._read(
            "SELECT * FROM recommendations ORDER BY generated_at DESC LIMIT ?",
            (int(limit),),
        )

    def last_recommendation(self, symbol, action=None):
        """The most recent recommendation for a symbol, optionally by action."""

        if action:
            rows = self._read(
                "SELECT * FROM recommendations WHERE symbol=? AND action=? "
                "ORDER BY generated_at DESC LIMIT 1",
                (_valid_symbol(symbol), str(action)),
            )
        else:
            rows = self._read(
                "SELECT * FROM recommendations WHERE symbol=? "
                "ORDER BY generated_at DESC LIMIT 1",
                (_valid_symbol(symbol),),
            )
        return rows[0] if rows else None
