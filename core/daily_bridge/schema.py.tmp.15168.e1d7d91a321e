"""Phase 1 — canonical normalized daily-bar schema + status vocabularies.

One canonical row shape carries prices, the continuous/auction split, volume
semantics, provenance and validation state. Every row preserves its provider and
source. This schema is additive; the strategy-facing normalized OHLCV interface
(Open/High/Low/Close/Volume) is derived from it and remains unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib

# source_type
EXTERNAL_HISTORICAL = "EXTERNAL_HISTORICAL"
RUBIX_DERIVED = "RUBIX_DERIVED"
YAHOO_LEGACY = "YAHOO_LEGACY"
MANUAL_OFFICIAL_REFERENCE = "MANUAL_OFFICIAL_REFERENCE"

# finalization_status
PROVISIONAL = "PROVISIONAL"
FINAL = "FINAL"
INCOMPLETE = "INCOMPLETE"
DATA_GAP = "DATA_GAP"
PROVIDER_PENDING = "PROVIDER_PENDING"
INVALID = "INVALID"

# session_completeness (per symbol)
COMPLETE_DAILY_BAR = "COMPLETE_DAILY_BAR"
COMPLETE_CONTINUOUS_AUCTION_MISSING = "COMPLETE_CONTINUOUS_AUCTION_MISSING"
PARTIAL_LATE_START = "PARTIAL_LATE_START"
PARTIAL_EARLY_STOP = "PARTIAL_EARLY_STOP"
SYMBOL_INACTIVE = "SYMBOL_INACTIVE"
SUBSCRIPTION_LOST = "SUBSCRIPTION_LOST"
COMPLETENESS_DATA_GAP = "DATA_GAP"
INVALID_OHLC = "INVALID_OHLC"
INVALID_VOLUME = "INVALID_VOLUME"

# --- separated bar-semantics statuses (Phase 1) ---
# continuous_bar_status
FINAL_CONTINUOUS = "FINAL_CONTINUOUS"
PARTIAL_SESSION = "PARTIAL_SESSION"
CONTINUOUS_INVALID = "INVALID"
# auction_status
AUCTION_CONFIRMED = "AUCTION_CONFIRMED"
AUCTION_MISSING = "AUCTION_MISSING"
AUCTION_INCOMPLETE = "AUCTION_INCOMPLETE"
POST_AUCTION_REPEAT = "POST_AUCTION_REPEAT"
# official_bar_status
FINAL_OFFICIAL = "FINAL_OFFICIAL"
OFFICIAL_CLOSE_UNCONFIRMED = "OFFICIAL_CLOSE_UNCONFIRMED"
OFFICIAL_INVALID = "INVALID"

# High/Low policy (declared + tested): daily High/Low come from the CONTINUOUS
# session only; the single closing-auction clearing price is stored separately and
# is NOT mixed into the continuous High/Low used by the Range Scalper.
HL_POLICY = "CONTINUOUS_SESSION_HL"


@dataclass
class NormalizedDailyBar:
    canonical_symbol: str
    session_date: str                       # ISO date
    open: float | None = None
    high: float | None = None               # continuous-session high (per HL_POLICY)
    low: float | None = None                # continuous-session low
    continuous_close: float | None = None
    auction_last: float | None = None
    official_close: float | None = None     # auction_last when available, else continuous_close
    volume: float | None = None             # total daily traded shares (cumulative max)
    continuous_volume: float | None = None
    auction_volume: float | None = None
    turnover_egp: float | None = None

    provider: str = "unknown"
    source_type: str = RUBIX_DERIVED
    is_adjusted: bool = False
    finalization_status: str = PROVISIONAL   # legacy: FINAL == continuous-final (ERS-usable)
    session_completeness: str = SYMBOL_INACTIVE
    hl_policy: str = HL_POLICY
    auction_included_in_hl: bool = False

    # --- separated bar semantics (Phase 1) ---
    bar_semantics: str = "CONTINUOUS_SESSION+OFFICIAL_DISCLOSURE"
    continuous_bar_status: str = PARTIAL_SESSION
    auction_status: str = AUCTION_MISSING
    official_bar_status: str = OFFICIAL_CLOSE_UNCONFIRMED
    official_close_confirmed: bool = False
    auction_volume_increment: float | None = None
    expected_range_eligible: bool = False    # continuous-session use (ERS)
    swing_daily_eligible: bool = False       # always false until official OHLC + CA + external recon

    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat())
    dataset_hash: str | None = None
    source_row_hash: str | None = None
    validation_warnings: str = ""

    # -- derived strategy-facing OHLCV (interface unchanged) ---------------

    def to_engine_ohlcv(self) -> dict:
        """The frozen strategy interface: Close is the OFFICIAL close."""
        return {"Open": self.open, "High": self.high, "Low": self.low,
                "Close": self.official_close, "Volume": self.volume}

    def compute_source_row_hash(self) -> str:
        payload = "|".join(str(x) for x in (
            self.canonical_symbol, self.session_date, self.open, self.high, self.low,
            self.continuous_close, self.auction_last, self.official_close, self.volume,
            self.provider, self.source_type))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def as_row(self) -> dict:
        if self.source_row_hash is None:
            self.source_row_hash = self.compute_source_row_hash()
        return asdict(self)


CSV_FIELDS = [
    "canonical_symbol", "session_date", "open", "high", "low", "continuous_close",
    "auction_last", "official_close", "volume", "continuous_volume", "auction_volume",
    "turnover_egp", "provider", "source_type", "is_adjusted", "finalization_status",
    "session_completeness", "hl_policy", "auction_included_in_hl",
    "bar_semantics", "continuous_bar_status", "auction_status", "official_bar_status",
    "official_close_confirmed", "auction_volume_increment", "expected_range_eligible",
    "swing_daily_eligible", "generated_at",
    "dataset_hash", "source_row_hash", "validation_warnings",
]
