"""Phase 2/3/4 — Rubix local daily-bar builder (event-driven, read-only).

Builds a completed daily bar for each symbol from genuine chronological market
events in the read-only Rubix DB (`mode=ro`). Uses RECEIPT timestamps for
chronology (the exchange market_timestamp can freeze — see the timestamp-freeze
audit), the EGX Cairo phase windows (continuous 10:00-14:15, auction 14:15-14:25),
and Last-only prices for High/Low (never Bid/Ask). Volume is treated as CUMULATIVE
traded shares (validated monotonic; the daily total is the cumulative max). Never
repairs, fabricates, or forward-fills; a symbol/session failing validation is
rejected with an explicit reason.

Isolated from all trading logic — imports no strategy, indicator, ranking,
backtest or AI code, and never writes the external Rubix DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd

from core.daily_bridge.schema import (
    AUCTION_CONFIRMED,
    AUCTION_INCOMPLETE,
    AUCTION_MISSING,
    COMPLETE_CONTINUOUS_AUCTION_MISSING,
    COMPLETE_DAILY_BAR,
    COMPLETENESS_DATA_GAP,
    FINAL,
    FINAL_CONTINUOUS,
    FINAL_OFFICIAL,
    INCOMPLETE,
    INVALID,
    INVALID_OHLC,
    INVALID_VOLUME,
    OFFICIAL_CLOSE_UNCONFIRMED,
    PARTIAL_EARLY_STOP,
    PARTIAL_LATE_START,
    PARTIAL_SESSION,
    POST_AUCTION_REPEAT,
    PROVISIONAL,
    RUBIX_DERIVED,
    SUBSCRIPTION_LOST,
    SYMBOL_INACTIVE,
    NormalizedDailyBar,
)

# Auction is CONFIRMED genuine when a volume increment occurs in the auction window
# (real auction trades) OR the auction clears at a materially different price.
AUCTION_PRICE_MOVE_PCT = 0.05
from providers.symbol_mapping import to_rubix_symbol
from core.symbols import SYMBOL_SOURCE

CAIRO = ZoneInfo("Africa/Cairo")
CONT_OPEN = time(10, 0)
CONT_CLOSE = time(14, 15)
AUCTION_END = time(14, 25)
# coverage tolerances (declared in advance)
OPEN_TOLERANCE = time(10, 15)      # first continuous event must be at/before this
CLOSE_TOLERANCE = time(14, 0)      # last continuous event must be at/after this
MIN_CONTINUOUS_EVENTS = 5          # fewer genuine prints => naturally inactive
MAX_INTRA_GAP_MINUTES = 45         # a longer continuous gap => DATA_GAP


@dataclass
class BuildConfig:
    open_tolerance: time = OPEN_TOLERANCE
    close_tolerance: time = CLOSE_TOLERANCE
    min_continuous_events: int = MIN_CONTINUOUS_EVENTS
    max_intra_gap_minutes: float = MAX_INTRA_GAP_MINUTES


class RubixDailyBuilder:
    def __init__(self, db_path, config=None):
        self.db_path = Path(db_path)
        self.cfg = config or BuildConfig()

    def available(self) -> bool:
        return self.db_path.is_file()

    # -- public -----------------------------------------------------------

    def build_session(self, session_date: str, symbols=None):
        """Build a NormalizedDailyBar for each symbol for one Cairo session date."""
        frame = self._load_session_quotes(session_date)
        bars = []
        if frame.empty:
            return bars
        active_tickers = set(frame["ticker"].str.upper())
        universe = symbols or self._universe()
        for canonical in universe:
            mapped = to_rubix_symbol(canonical).upper()
            sub = frame[frame["ticker"].str.upper() == mapped]
            bars.append(self._build_symbol(canonical, session_date, sub,
                                            present=mapped in active_tickers))
        return bars

    # -- per-symbol build -------------------------------------------------

    def _build_symbol(self, canonical, session_date, sub, present):
        bar = NormalizedDailyBar(canonical_symbol=canonical, session_date=session_date,
                                 provider="rubix_local", source_type=RUBIX_DERIVED)
        if sub.empty:
            bar.session_completeness = SUBSCRIPTION_LOST if not present else SYMBOL_INACTIVE
            bar.finalization_status = INCOMPLETE
            bar.validation_warnings = "no events captured for symbol this session"
            return bar

        # material events only: a genuine traded Last (>0). De-dupe consecutive
        # identical (last, volume) snapshots — duplicates carry no new information.
        ev = sub.copy()
        ev["last"] = pd.to_numeric(ev["last_price"], errors="coerce")
        ev["vol"] = pd.to_numeric(ev["volume"], errors="coerce")
        ev = ev[ev["last"] > 0].sort_values("recv")
        dup = (ev["last"].eq(ev["last"].shift()) & ev["vol"].eq(ev["vol"].shift()))
        ev = ev[~dup]

        tod = ev["recv"].dt.time
        cont = ev[(tod >= CONT_OPEN) & (tod < CONT_CLOSE)]
        auction = ev[(tod >= CONT_CLOSE) & (tod < AUCTION_END)]

        warns = []
        # --- volume semantics validation (cumulative shares, monotonic) ---
        vol_status = self._validate_volume(ev)
        if vol_status:
            warns.append(vol_status)

        if len(cont) < self.cfg.min_continuous_events:
            bar.session_completeness = SYMBOL_INACTIVE
            bar.finalization_status = INCOMPLETE
            bar.validation_warnings = f"only {len(cont)} continuous prints (naturally inactive)"
            self._fill_volume(bar, ev, cont, auction)
            return bar

        # --- continuous OHLC from Last-only ---
        cont = cont.sort_values("recv")
        bar.open = _r(cont["last"].iloc[0])
        bar.high = _r(cont["last"].max())          # continuous-only per HL_POLICY
        bar.low = _r(cont["last"].min())
        bar.continuous_close = _r(cont["last"].iloc[-1])
        bar.auction_included_in_hl = False

        self._fill_volume(bar, ev, cont, auction)

        # --- auction analysis (genuine close vs repeated snapshot) ---
        self._classify_auction(bar, cont, auction, warns)
        # official_close is set ONLY when the auction is confirmed; never a silent
        # fallback to continuous_close.
        bar.official_close = bar.auction_last if bar.official_close_confirmed else None

        # --- continuous-session completeness ---
        first_t = cont["recv"].iloc[0].time()
        last_t = cont["recv"].iloc[-1].time()
        max_gap = cont["recv"].diff().dt.total_seconds().max() / 60.0 if len(cont) > 1 else 0.0
        ohlc_fail = _validate_ohlc_continuous(bar)

        if ohlc_fail:
            bar.continuous_bar_status = "INVALID"; bar.session_completeness = INVALID_OHLC
            warns.append(ohlc_fail)
        elif vol_status:
            bar.continuous_bar_status = "INVALID"; bar.session_completeness = INVALID_VOLUME
        elif max_gap > self.cfg.max_intra_gap_minutes:
            bar.continuous_bar_status = PARTIAL_SESSION; bar.session_completeness = COMPLETENESS_DATA_GAP
            warns.append(f"continuous gap {max_gap:.0f}min > {self.cfg.max_intra_gap_minutes}min")
        elif first_t > self.cfg.open_tolerance:
            bar.continuous_bar_status = PARTIAL_SESSION; bar.session_completeness = PARTIAL_LATE_START
            warns.append(f"first continuous print {first_t} > {self.cfg.open_tolerance}")
        elif last_t < self.cfg.close_tolerance:
            bar.continuous_bar_status = PARTIAL_SESSION; bar.session_completeness = PARTIAL_EARLY_STOP
            warns.append(f"last continuous print {last_t} < {self.cfg.close_tolerance}")
        else:
            bar.continuous_bar_status = FINAL_CONTINUOUS
            bar.session_completeness = (COMPLETE_DAILY_BAR if bar.official_close_confirmed
                                        else COMPLETE_CONTINUOUS_AUCTION_MISSING)

        # --- official-bar status (stricter; requires confirmed auction) ---
        if bar.continuous_bar_status == FINAL_CONTINUOUS and bar.official_close_confirmed:
            bar.official_bar_status = FINAL_OFFICIAL
        elif bar.continuous_bar_status == FINAL_CONTINUOUS:
            bar.official_bar_status = OFFICIAL_CLOSE_UNCONFIRMED
        elif bar.continuous_bar_status == "INVALID":
            bar.official_bar_status = "INVALID"
        else:
            bar.official_bar_status = OFFICIAL_CLOSE_UNCONFIRMED

        # --- eligibility ---
        # ERS consumes continuous-session history; it is eligible when the continuous
        # bar is FINAL and OHLCV valid — auction confirmation is NOT required.
        bar.expected_range_eligible = bar.continuous_bar_status == FINAL_CONTINUOUS and not vol_status
        # Swing/Daily must NOT consume Rubix rows until official OHLC + corporate
        # actions + external reconciliation are validated. Always false for now.
        bar.swing_daily_eligible = False

        # legacy finalization_status: FINAL == continuous-final (what the ERS overlay
        # selects). It never implies an official/auction-confirmed daily bar.
        if bar.continuous_bar_status == FINAL_CONTINUOUS and not vol_status:
            bar.finalization_status = FINAL
        elif bar.continuous_bar_status == "INVALID":
            bar.finalization_status = INVALID
        else:
            bar.finalization_status = INCOMPLETE

        ref_close = bar.official_close if bar.official_close_confirmed else bar.continuous_close
        if ref_close and bar.volume:
            bar.turnover_egp = _r(ref_close * bar.volume)
        bar.validation_warnings = " | ".join(w for w in warns if w)
        bar.source_row_hash = bar.compute_source_row_hash()
        return bar

    def _classify_auction(self, bar, cont, auction, warns):
        """Confirm the auction is a genuine randomized close, not a repeated snapshot.

        Confirmed when the auction window shows a real volume increment (auction
        trades executed) or a materially different clearing price; otherwise the
        auction frames are treated as post-auction repeats and the official close is
        left unconfirmed (never silently set to the continuous close).
        """
        if auction.empty:
            bar.auction_status = AUCTION_MISSING
            bar.official_close_confirmed = False
            warns.append("no auction events — official close unconfirmed")
            return
        auction = auction.sort_values("recv")
        bar.auction_last = _r(auction["last"].iloc[-1])
        cont_vol = bar.continuous_volume or 0.0
        avol_max = float(auction["vol"].dropna().max()) if auction["vol"].notna().any() else cont_vol
        increment = max(0.0, avol_max - cont_vol)
        bar.auction_volume_increment = _r(increment)
        price_move = (abs((bar.auction_last or 0) - (bar.continuous_close or 0))
                      / bar.continuous_close * 100.0) if bar.continuous_close else 0.0

        if increment > 0 or price_move >= AUCTION_PRICE_MOVE_PCT:
            bar.auction_status = AUCTION_CONFIRMED
            bar.official_close_confirmed = True
        elif price_move > 0:
            bar.auction_status = AUCTION_INCOMPLETE      # price moved but no volume — suspicious
            bar.official_close_confirmed = False
            warns.append("auction price moved without volume increment — unconfirmed")
        else:
            bar.auction_status = POST_AUCTION_REPEAT     # identical repeats, no genuine auction
            bar.official_close_confirmed = False
            warns.append("auction window shows repeated snapshots only — official close unconfirmed")

    def _fill_volume(self, bar, ev, cont, auction):
        """Daily volume = cumulative-shares max; auction increment stored separately."""
        vall = ev["vol"].dropna()
        bar.volume = _r(float(vall.max())) if len(vall) else None
        cvol = cont["vol"].dropna()
        bar.continuous_volume = _r(float(cvol.max())) if len(cvol) else None
        if not auction.empty and bar.continuous_volume is not None:
            avol = auction["vol"].dropna()
            if len(avol):
                bar.auction_volume = _r(max(0.0, float(avol.max()) - bar.continuous_volume))

    @staticmethod
    def _validate_volume(ev):
        """Return a warning string if cumulative volume is not clean, else ''."""
        v = ev["vol"].dropna()
        if v.empty:
            return "no volume observed"
        diffs = v.diff().dropna()
        if (diffs < 0).any():
            return "cumulative volume DECREASED (reset/remap) — semantics unresolved"
        if float(v.iloc[-1]) <= 0:
            return "final cumulative volume is zero"
        return ""

    # -- read-only DB -----------------------------------------------------

    def _load_session_quotes(self, session_date):
        if not self.available():
            return pd.DataFrame()
        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            rows = conn.execute(
                "SELECT ticker,last_price,bid,ask,volume,market_timestamp,received_at "
                "FROM quotes WHERE substr(received_at,1,10)=?", (session_date,)).fetchall()
        finally:
            conn.close()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["ticker", "last_price", "bid", "ask", "volume",
                                         "market_timestamp", "received_at"])
        df["recv"] = pd.to_datetime(df["received_at"], utc=True, errors="coerce").dt.tz_convert(CAIRO)
        return df.dropna(subset=["recv"])

    def _universe(self):
        from core.symbols import load_symbols
        try:
            return load_symbols(SYMBOL_SOURCE)
        except Exception:
            return []


def _r(x, digits=6):
    try:
        if x is None or pd.isna(x):
            return None
        return round(float(x), digits)
    except (TypeError, ValueError):
        return None


def _validate_ohlc_continuous(bar):
    o, h, l, c = bar.open, bar.high, bar.low, bar.continuous_close
    vals = [o, h, l, c]
    if any(v is None or v <= 0 for v in vals):
        return "non-positive continuous OHLC"
    if not (h >= l and h >= o and h >= c and l <= o and l <= c):
        return "continuous OHLC ordering violation"
    return None
