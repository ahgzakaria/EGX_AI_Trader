"""Phase 1-3 orchestration — historical selection for EXPECTED_RANGE_SCALPER.

Consumes the existing normalized daily-data interface (the frozen Yahoo-backed
local completed-daily cache), applies the strict cleaning rules, and combines the
liquidity, volatility and expected-range models into one immutable per-symbol
record with full data provenance.

Never uses the current incomplete daily candle, closing-auction movement,
malformed OHLCV, zero High/Low, or forward-filled candles. Never mutates any
provider, cache, or existing strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core.egx_session import (
    classify_history_freshness,
    expected_latest_completed_session,
    last_completed_exchange_session,
    latest_completed_session_date,
)
from providers.local_cache_provider import LocalCacheProvider
from providers.base_provider import ProviderError
from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.expected_range import compute_expected_range
from scalping_expected_range.liquidity_model import (
    DATA_INSUFFICIENT,
    compute_liquidity,
)
from scalping_expected_range.volatility_model import compute_volatility


@dataclass
class Provenance:
    historical_provider: str = "unknown"
    latest_completed_session: str | None = None
    expected_latest_session: str | None = None
    data_age_sessions: int | None = None
    row_count: int = 0
    rows_after_cleaning: int = 0
    rows_dropped: int = 0
    fallback_reason: str | None = None
    adjusted_status: str = "UNKNOWN"
    # data_status is the pipeline gate: OK / DATA_STALE / DATA_INSUFFICIENT / MISSING
    data_status: str = "OK"
    # freshness_status is the auction-aware disclosure (HISTORY_CURRENT,
    # TODAY_CANDLE_NOT_YET_COMPLETE, PROVIDER_FINALIZATION_PENDING, HISTORY_STALE,
    # HISTORY_UNAVAILABLE). A current-but-forming-today session is NOT stale.
    freshness_status: str = "UNKNOWN"
    session_phase: str = "UNKNOWN"
    provider_publication_pending: bool = False
    rubix_overlay_sessions: int = 0
    rubix_overlay_official_confirmed: int = 0
    rubix_overlay_auction_unconfirmed: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class SymbolAnalysis:
    symbol: str
    prev_close: float | None
    liquidity: object
    volatility: object
    expected: object
    provenance: Provenance

    def flat(self) -> dict:
        row = {"Symbol": self.symbol, "PrevClose": self.prev_close}
        row.update({f"liq_{k}": v for k, v in self.liquidity.as_dict().items()})
        row.update({f"vol_{k}": v for k, v in self.volatility.as_dict().items()})
        row.update({f"er_{k}": v for k, v in self.expected.as_dict().items()})
        row.update({f"prov_{k}": v for k, v in self.provenance.as_dict().items()})
        return row


class HistoricalSelector:
    """Provider-agnostic completed-daily accessor + per-symbol analysis."""

    def __init__(self, config=None, cache=None, period="10y", holidays=(), now=None):
        self.config = config or ExpectedRangeConfig.load()
        self.period = period
        self.holidays = tuple(holidays or ())
        self._now = now
        if cache is not None:
            self.cache = cache
        else:
            from config.settings_manager import settings
            market = settings.get("market_data")
            self.cache = LocalCacheProvider(
                market.get("cache_path", "data/market_data_cache.sqlite"),
                source_provider="yahoo",
            )
        self._rubix_cache = None            # lazy NormalizedDailyCache for the overlay
        self._completed = latest_completed_session_date(
            holidays=self.holidays, **({"value": now} if now else {}))
        # Provider finalization for TODAY's just-completed candle is unknown here
        # (inferred from the data itself in classify_history_freshness).
        self._provider_finalized = None
        self._expected = expected_latest_completed_session(
            now=now, provider_finalized=self._provider_finalized, holidays=self.holidays)

    # -- public ------------------------------------------------------------

    def analyze(self, symbol: str) -> SymbolAnalysis:
        daily, prov = self._load_clean_daily(symbol)
        if daily is None or daily.empty:
            return SymbolAnalysis(
                symbol, None, compute_liquidity(None, self.config),
                compute_volatility(None, self.config),
                compute_expected_range(None, self.config), prov)

        prev_close = float(pd.to_numeric(daily["Close"], errors="coerce").dropna().iloc[-1])
        liquidity = compute_liquidity(daily, self.config)
        volatility = compute_volatility(daily, self.config)
        expected = compute_expected_range(daily, self.config, prev_close=prev_close)
        return SymbolAnalysis(symbol, prev_close, liquidity, volatility, expected, prov)

    # -- daily data accessor with cleaning + provenance --------------------

    def _load_clean_daily(self, symbol: str):
        prov = Provenance(
            expected_latest_session=self._expected.isoformat() if self._expected else None,
        )
        try:
            frame = self.cache.load_cached("yahoo", symbol, self.period, "1d",
                                           allow_expired=True)
            prov.historical_provider = "local_cache:yahoo"
        except ProviderError as error:
            prov.historical_provider = "local_cache:yahoo"
            prov.fallback_reason = f"CACHE_MISS: {error}"
            prov.data_status = "MISSING"
            return None, prov

        prov.row_count = len(frame)
        prov.adjusted_status = (
            "ADJUSTED_AVAILABLE" if "Adj Close" in frame.columns else "UNADJUSTED_OHLC")

        cleaned = self._clean(frame)
        prov.rows_after_cleaning = len(cleaned)
        prov.rows_dropped = prov.row_count - prov.rows_after_cleaning
        if cleaned.empty:
            prov.data_status = "MISSING"
            prov.fallback_reason = "ALL_ROWS_MALFORMED"
            return None, prov

        # Phase 9 — overlay locally-finalized Rubix FINAL bars newer than Yahoo.
        # This makes history current when Yahoo has not updated, WITHOUT changing any
        # expected-range formula and WITHOUT overwriting a Yahoo row. Disclosure only.
        cleaned, prov = self._overlay_rubix_final(symbol, cleaned, prov)

        last_date = pd.Timestamp(cleaned.index[-1]).date()
        prov.latest_completed_session = last_date.isoformat()
        prov.expected_latest_session = self._expected.isoformat() if self._expected else None

        # Auction-aware freshness: a correct previous-session candle during a
        # forming today session is HISTORY_CURRENT / TODAY_CANDLE_NOT_YET_COMPLETE,
        # never DATA_STALE. Only genuinely lagging history is stale.
        fresh = classify_history_freshness(
            last_date, now=self._now, provider_finalized=self._provider_finalized,
            holidays=self.holidays)
        prov.freshness_status = fresh.status
        prov.session_phase = fresh.session_phase
        prov.provider_publication_pending = fresh.provider_publication_pending
        prov.data_age_sessions = int(fresh.lag_sessions)

        if len(cleaned) < self.config.minimum_history_sessions:
            prov.data_status = "DATA_INSUFFICIENT"
            prov.fallback_reason = (
                f"ONLY_{len(cleaned)}_SESSIONS<{self.config.minimum_history_sessions}")
        elif fresh.is_current:
            # Includes HISTORY_CURRENT, TODAY_CANDLE_NOT_YET_COMPLETE and
            # PROVIDER_FINALIZATION_PENDING — all usable, not stale.
            prov.data_status = "OK"
            if fresh.provider_publication_pending:
                prov.fallback_reason = fresh.note
        else:
            # Preserve the last calculated result but disclose genuine staleness.
            prov.data_status = "DATA_STALE"
            prov.fallback_reason = (
                f"HISTORY_STALE_BY_{fresh.lag_sessions}_SESSIONS "
                f"(latest {last_date.isoformat()} < expected completed "
                f"{prov.expected_latest_session or 'NA'})")
        return cleaned, prov

    def _overlay_rubix_final(self, symbol, cleaned, prov):
        """Append locally-finalized Rubix FINAL daily bars newer than Yahoo's last.

        Never overwrites an existing (Yahoo) row; only appends completed sessions
        that Yahoo does not yet have. Records provenance. No-op when disabled or the
        cache is unavailable.
        """
        if not getattr(self.config, "use_rubix_final_daily", False):
            return cleaned, prov
        try:
            if self._rubix_cache is None:
                from core.daily_bridge.normalized_cache import NormalizedDailyCache
                self._rubix_cache = NormalizedDailyCache(
                    getattr(self.config, "normalized_daily_cache_path",
                            "data/normalized_daily_cache.db"))
            yahoo_last = pd.Timestamp(cleaned.index[-1]).date().isoformat()
            newer = [r for r in self._rubix_cache.final_bars_after(yahoo_last, "RUBIX_DERIVED")
                     if r.get("canonical_symbol") == symbol]
        except Exception as error:                      # overlay must never break selection
            prov.fallback_reason = (prov.fallback_reason or "") + f" | rubix_overlay_error:{error}"
            return cleaned, prov
        if not newer:
            return cleaned, prov

        # Never inject a session newer than the latest COMPLETED session as of now
        # (i.e. never today's still-forming bar), and never overwrite a Yahoo row.
        # ERS is a CONTINUOUS-session strategy, so the overlaid Close is the
        # continuous_close (RUBIX_CONTINUOUS_DERIVED) — never a silent official close.
        yahoo_last_date = pd.Timestamp(cleaned.index[-1]).date()
        rows = []
        official_confirmed = 0
        for r in newer:
            try:
                if str(r.get("expected_range_eligible")) not in ("True", "1", "true"):
                    continue                            # only continuous-final bars
                idx = pd.Timestamp(r["session_date"])
                d = idx.date()
                if d <= yahoo_last_date:
                    continue                            # never overwrite an existing row
                if self._completed is not None and d > self._completed:
                    continue                            # never inject a not-yet-completed session
                rows.append((idx, float(r["open"]), float(r["high"]), float(r["low"]),
                             float(r["continuous_close"]), float(r["volume"])))
                if str(r.get("official_close_confirmed")) in ("True", "1", "true"):
                    official_confirmed += 1
            except (TypeError, ValueError, KeyError):
                continue
        if not rows:
            return cleaned, prov
        add = pd.DataFrame(
            [{"Open": o, "High": h, "Low": l, "Close": c, "Volume": v} for _, o, h, l, c, v in rows],
            index=pd.DatetimeIndex([r[0] for r in rows]))
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col not in cleaned.columns:
                add = add.drop(columns=[col], errors="ignore")
        merged = pd.concat([cleaned, add[[c for c in cleaned.columns if c in add.columns]]])
        merged = merged[~merged.index.duplicated(keep="first")].sort_index()
        # Phase 7 disclosure: the overlaid rows are Rubix CONTINUOUS-derived (Close =
        # continuous_close), with per-session auction/official-close confirmation noted.
        prov.historical_provider = "local_cache:yahoo + RUBIX_CONTINUOUS_DERIVED"
        prov.rubix_overlay_sessions = len(rows)
        prov.rubix_overlay_official_confirmed = official_confirmed
        prov.rubix_overlay_auction_unconfirmed = len(rows) - official_confirmed
        return merged, prov

    def _clean(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Drop malformed / zero / forward-filled rows and the incomplete candle."""

        df = frame.copy()
        cols = ["Open", "High", "Low", "Close", "Volume"]
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        # Malformed OHLCV.
        df = df.dropna(subset=cols)
        # Zero or negative High/Low (and impossible OHLC ordering).
        df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0) & (df["Close"] > 0)]
        df = df[df["High"] >= df["Low"]]
        # Forward-filled flat candle: identical OHLC with no traded volume.
        flat = (
            (df["High"] == df["Low"]) & (df["Open"] == df["Close"])
            & (df["High"] == df["Close"]) & (df["Volume"] <= 0)
        )
        df = df[~flat]
        # Never include the current incomplete session (or any not-yet-completed
        # date). Completed history only.
        if self._completed is not None:
            idx_dates = pd.to_datetime(df.index).date
            keep = [d <= self._completed for d in idx_dates]
            df = df[pd.Series(keep, index=df.index)]
        return df
