"""Read-only Rubix intraday scanner isolated from the daily scanner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from config.settings_manager import settings
from core.egx_session import cairo_now, egx_session_phase
from core.symbols import SYMBOL_SOURCE, load_symbols
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.symbol_mapping import to_rubix_symbol
from scalping.config import ScalpingConfig
from scalping.database import ScalpingDatabase
from scalping.models import MarketSnapshot
from scalping.paper_portfolio import ScalpingPaperPortfolio
from scalping.setup_detector import detect_setups, sanitize_intraday_bars


# These are expected data-coverage outcomes, not scanner crashes.  Scalping must
# never manufacture a price or reuse an older session merely to evaluate them.
INTRADAY_EVIDENCE_MESSAGES = {
    "SYMBOL_MISSING": "Rubix has no quote for this symbol.",
    "INTRADAY_CANDLES_MISSING": "Rubix has no one-minute candles for this symbol.",
    "INTRADAY_VALID_CANDLES_MISSING": "Rubix has only zero or invalid intraday candles for this symbol.",
    "CURRENT_SESSION_CANDLES_MISSING": "No valid traded candle exists for the current EGX session.",
}


def intraday_evidence_description(reason):
    """Return a user-facing explanation for a known no-data outcome."""

    return INTRADAY_EVIDENCE_MESSAGES.get(str(reason))


class ScalpingScanner:
    def __init__(self, config=None, rubix_db_path=None, database=None, now=None):
        self.config = config or ScalpingConfig.from_mapping(settings.get("scalping"))
        market = settings.get("market_data")
        self.rubix_path = Path(rubix_db_path or market.get("rubix_db_path", "data/rubix_live_market.db"))
        self.database = database or ScalpingDatabase(self.config.database_path)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def health(self):
        provider = RubixSQLiteProvider(
            self.rubix_path,
            stale_after_minutes=self.config.quote_max_age_seconds / 60.0,
            bar_stale_after_minutes=max(1.0, self.config.quote_max_age_seconds / 60.0),
            expected_symbols=load_symbols(SYMBOL_SOURCE),
            now=self.now,
        )
        return provider.health()

    def scan(self, symbols=None):
        symbols = tuple(symbols or load_symbols(SYMBOL_SOURCE))
        health = self.health()
        opportunities = []
        failures = []
        skipped = []
        evaluated_symbols = 0
        invalid_bars_removed = 0
        affected_symbols = 0
        if str(health.get("collector_status", "")).upper() == "DISCONNECTED":
            self.database.add_alert(
                "COLLECTOR_DISCONNECTED", "Rubix collector is disconnected; scalping entries are non-actionable",
                key=f"COLLECTOR_DISCONNECTED|{cairo_now(self.now()).date().isoformat()}",
            )
        if str(health.get("freshness", "")).upper() not in {"FRESH", "RUBIX_FRESH"}:
            self.database.add_alert(
                "DATA_STALE", "Rubix data is not fresh; scalping entries are blocked",
                key=f"DATA_STALE|{cairo_now(self.now()).date().isoformat()}",
            )
        if not self.rubix_path.is_file():
            return {"opportunities": [], "failures": [{"reason": "RUBIX_DB_MISSING"}], "health": health}
        # Resume-safe lifecycle processing runs before new opportunity
        # detection. It closes targets/stops/cutoff positions from the
        # dedicated paper portfolio and can never place a broker order.
        self._process_open_positions(health, failures)
        for ticker in symbols:
            try:
                snapshot, bars = self._read_symbol(ticker, health)
                removed = int(bars.attrs.get("invalid_bars_removed", 0))
                invalid_bars_removed += removed
                affected_symbols += int(removed > 0)
                evaluated_symbols += 1
                for opportunity in detect_setups(bars, snapshot, self.config):
                    signal_id, inserted = self.database.insert_signal(opportunity)
                    if not opportunity.actionable:
                        self.database.record_rejection(
                            opportunity, opportunity.blocked_reason or "NON_ACTIONABLE"
                        )
                    elif inserted:
                        self.database.add_alert(
                            "NEW_QUALIFIED_SETUP",
                            f"{ticker} {opportunity.setup.value} qualified for paper review",
                            ticker=ticker, key=f"SETUP|{signal_id}",
                        )
                    row = dict(opportunity.__dict__)
                    row["setup"] = opportunity.setup.value
                    row["signal_id"] = signal_id
                    row["inserted"] = inserted
                    opportunities.append(row)
            except Exception as error:
                reason = str(error)
                description = intraday_evidence_description(reason)
                if description:
                    skipped.append({
                        "ticker": ticker,
                        "reason": reason,
                        "explanation": description,
                    })
                else:
                    failures.append({"ticker": ticker, "reason": reason})
        self.database.save_session_summary(
            cairo_now(self.now()).date().isoformat(),
            {
                "opportunities": len(opportunities),
                "actionable": sum(bool(row.get("actionable")) for row in opportunities),
                "blocked": sum(not bool(row.get("actionable")) for row in opportunities),
                "failures": len(failures), "insufficient_evidence": len(skipped),
                "mode": self.config.mode,
                "provider": "rubix", "freshness": health.get("freshness"),
            },
        )
        return {
            "opportunities": opportunities,
            "failures": failures,
            "skipped": skipped,
            "health": health,
            "coverage": {
                "requested_symbols": len(symbols),
                "evaluated_symbols": evaluated_symbols,
                "insufficient_evidence": len(skipped),
                "unexpected_failures": len(failures),
            },
            "data_quality": {
                "invalid_bars_removed": invalid_bars_removed,
                "affected_symbols": affected_symbols,
                "policy": "REJECT_IMPOSSIBLE_OHLCV",
            },
        }

    def update_open_positions(self):
        """Apply paper exits without detecting or creating new opportunities."""

        health = self.health()
        failures = []
        before = len(self.database.open_positions())
        self._process_open_positions(health, failures)
        after = len(self.database.open_positions())
        return {
            "closed": max(0, before - after), "remaining": after,
            "failures": failures, "health": health,
        }

    def _connect_read_only(self):
        uri = f"file:{self.rubix_path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _read_symbol(self, ticker, health, require_current_session=True):
        mapped = to_rubix_symbol(ticker)
        with self._connect_read_only() as connection:
            quote = connection.execute(
                """SELECT ticker,last_price,bid,ask,volume,market_timestamp,received_at
                   FROM quotes WHERE UPPER(ticker)=? ORDER BY received_at DESC LIMIT 1""",
                (mapped.upper(),),
            ).fetchone()
            candles = connection.execute(
                """SELECT minute AS Date,open AS Open,high AS High,low AS Low,
                          close AS Close,volume AS Volume
                   FROM candles_1m WHERE UPPER(ticker)=? ORDER BY minute DESC LIMIT 600""",
                (mapped.upper(),),
            ).fetchall()
        if quote is None:
            raise ValueError("SYMBOL_MISSING")
        if not candles:
            raise ValueError("INTRADAY_CANDLES_MISSING")
        timestamp = pd.Timestamp(quote["market_timestamp"])
        received = pd.Timestamp(quote["received_at"])
        timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
        received = received.tz_localize("UTC") if received.tzinfo is None else received.tz_convert("UTC")
        current = pd.Timestamp(self.now())
        current = current.tz_localize("UTC") if current.tzinfo is None else current.tz_convert("UTC")
        age = max(0.0, (current - received).total_seconds())
        frame = pd.DataFrame([dict(row) for row in reversed(candles)])
        frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
        frame = frame.set_index("Date").sort_index()
        frame = sanitize_intraday_bars(frame)
        if frame.empty:
            raise ValueError("INTRADAY_VALID_CANDLES_MISSING")
        quality = dict(frame.attrs)
        local_dates = frame.index.tz_convert("Africa/Cairo").date
        today = cairo_now(self.now()).date()
        session = frame[pd.Index(local_dates) == today]
        if session.empty and require_current_session:
            raise ValueError("CURRENT_SESSION_CANDLES_MISSING")
        if session.empty:
            latest_date = frame.index.tz_convert("Africa/Cairo")[-1].date()
            session = frame[pd.Index(local_dates) == latest_date]
        session.attrs.update(quality)
        snapshot = MarketSnapshot(
            ticker=ticker, timestamp=cairo_now(timestamp.to_pydatetime()),
            last=float(quote["last_price"]) if quote["last_price"] is not None else None,
            bid=float(quote["bid"]) if quote["bid"] is not None else None,
            ask=float(quote["ask"]) if quote["ask"] is not None else None,
            volume=float(quote["volume"]) if quote["volume"] is not None else None,
            provider="rubix", freshness=str(health.get("freshness") or health.get("status")),
            quote_age_seconds=age, session_phase=egx_session_phase(self.now()),
            collector_status=health.get("collector_status"),
        )
        return snapshot, session

    def _process_open_positions(self, health, failures):
        rows = self.database.open_positions()
        if not rows:
            return
        state = self.database.load_risk_state(
            cairo_now(self.now()).date().isoformat(), self.config.initial_capital
        )
        portfolio = ScalpingPaperPortfolio(self.config, self.database)
        for row in rows:
            try:
                snapshot, bars = self._read_symbol(
                    row["ticker"], health, require_current_session=False
                )
                latest = bars.iloc[-1]
                portfolio.evaluate(
                    portfolio.restore_position(row), latest["High"], latest["Low"],
                    snapshot.bid or latest["Close"], cairo_now(self.now()), state,
                )
            except Exception as error:
                failures.append({
                    "ticker": row["ticker"], "reason": f"OPEN_POSITION_UPDATE: {error}",
                })
