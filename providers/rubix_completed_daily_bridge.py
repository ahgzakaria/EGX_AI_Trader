"""Phase D: Rubix Completed-Daily-Candle Bridge (safe history merge).

Combines immutable Yahoo/local-cache daily history with *validated, completed*
Rubix daily sessions that are newer than Yahoo, without ever overwriting an
existing Yahoo candle, inserting a forming session, or repairing bad data.

This module is intentionally decoupled from production routing.  It never
mutates the Yahoo frame it is given, never writes the external Rubix database,
and never imports strategy, indicator, ranking, backtest, or AI code.  Enabling
it in production is a separate, explicit, user-approved change to
``core/data_provider`` — this file only builds the merged frame and its
provenance so it can be evaluated in shadow mode first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from providers.base_provider import REQUIRED_COLUMNS, normalize_history
from providers.rubix_daily_aggregator import (
    RUBIX_COMPLETED_DAILY,
    DailyCandleResult,
    RubixDailyAggregator,
)


@dataclass
class ReconciliationRecord:
    symbol: str
    trading_date: str
    rubix_valid: bool
    reason: str | None
    open_diff_pct: float | None
    high_diff_pct: float | None
    low_diff_pct: float | None
    close_diff_pct: float | None
    volume_diff_pct: float | None
    yahoo: dict = field(default_factory=dict)
    rubix: dict = field(default_factory=dict)


@dataclass
class BridgeResult:
    symbol: str
    frame: pd.DataFrame
    yahoo_latest_date: str | None
    merged_latest_date: str | None
    sessions_appended: int
    appended_dates: list
    provenance_rows: list
    reconciliations: list
    rubix_available: bool
    note: str | None = None


class RubixCompletedDailyBridge:
    """Append validated newer Rubix sessions onto Yahoo history, safely."""

    def __init__(self, aggregator: RubixDailyAggregator, *, canonical_source="yahoo"):
        self.aggregator = aggregator
        self.canonical_source = canonical_source

    def merge(self, symbol, yahoo_frame: pd.DataFrame) -> BridgeResult:
        """Return Yahoo history plus newer validated Rubix sessions.

        The input ``yahoo_frame`` is treated as immutable and is always the
        canonical source for any date it already contains.
        """

        if yahoo_frame is None or yahoo_frame.empty:
            return BridgeResult(
                symbol=symbol, frame=yahoo_frame, yahoo_latest_date=None,
                merged_latest_date=None, sessions_appended=0, appended_dates=[],
                provenance_rows=[], reconciliations=[], rubix_available=False,
                note="empty Yahoo history; bridge made no changes",
            )

        base = yahoo_frame.copy()
        base.attrs["market_data"] = dict(yahoo_frame.attrs.get("market_data", {}))
        yahoo_dates = {pd.Timestamp(idx).date() for idx in base.index}
        yahoo_latest = max(yahoo_dates)

        if not self.aggregator.available():
            return BridgeResult(
                symbol=symbol, frame=base, yahoo_latest_date=yahoo_latest.isoformat(),
                merged_latest_date=yahoo_latest.isoformat(), sessions_appended=0,
                appended_dates=[], provenance_rows=[], reconciliations=[],
                rubix_available=False,
                note="Rubix database unavailable; Yahoo history retained unchanged",
            )

        # Only sessions strictly newer than Yahoo are candidates to append.
        candidates = self.aggregator.build_completed_daily(symbol, after=yahoo_latest)
        provenance_rows = [result.as_provenance_row() for result in candidates]

        appended: list[DailyCandleResult] = []
        for result in candidates:
            # Absent-date guard is belt-and-suspenders: `after=` already excludes
            # every Yahoo date, but we never silently overwrite Yahoo regardless.
            if result.trading_date in yahoo_dates:
                continue
            if result.valid and result.source == RUBIX_COMPLETED_DAILY:
                appended.append(result)

        merged = self._append_rows(symbol, base, appended)
        merged_latest = max(pd.Timestamp(idx).date() for idx in merged.index)

        return BridgeResult(
            symbol=symbol,
            frame=merged,
            yahoo_latest_date=yahoo_latest.isoformat(),
            merged_latest_date=merged_latest.isoformat(),
            sessions_appended=len(appended),
            appended_dates=[r.trading_date.isoformat() for r in appended],
            provenance_rows=provenance_rows,
            reconciliations=[],
            rubix_available=True,
        )

    def reconcile(self, symbol, yahoo_frame: pd.DataFrame) -> list:
        """Compare Rubix vs Yahoo on overlapping sessions (never replaces data)."""

        if yahoo_frame is None or yahoo_frame.empty or not self.aggregator.available():
            return []
        yahoo_by_date = {
            pd.Timestamp(idx).date(): row for idx, row in yahoo_frame.iterrows()
        }
        records = []
        for result in self.aggregator.build_completed_daily(symbol, after=None):
            if result.ohlcv is None:
                continue
            yahoo_row = yahoo_by_date.get(result.trading_date)
            if yahoo_row is None:
                continue
            records.append(_reconcile_one(symbol, result, yahoo_row))
        return records

    def _append_rows(self, symbol, base: pd.DataFrame, appended):
        if not appended:
            return base
        has_adj = "Adj Close" in base.columns
        new_index = []
        new_rows = []
        for result in appended:
            ohlcv = result.ohlcv
            row = {
                "Open": ohlcv["Open"], "High": ohlcv["High"], "Low": ohlcv["Low"],
                "Close": ohlcv["Close"], "Volume": ohlcv["Volume"],
            }
            if has_adj:
                # Rubix has no adjusted price; use Close so downstream selectors
                # that read Adj Close keep a defined, non-null value.
                row["Adj Close"] = ohlcv["Close"]
            new_index.append(pd.Timestamp(result.trading_date))
            new_rows.append(row)

        addition = pd.DataFrame(new_rows, index=pd.DatetimeIndex(new_index))
        addition = addition[[c for c in base.columns if c in addition.columns]]
        combined = pd.concat([base, addition])
        # Yahoo (canonical) always wins any exact-date collision.
        combined = combined[~combined.index.duplicated(keep="first")].sort_index()
        combined.index.name = base.index.name or "Date"
        normalized = normalize_history(
            combined.reset_index().rename(columns={combined.index.name or "index": "Date"}),
            symbol, "yahoo+rubix_completed_daily",
        )
        metadata = dict(base.attrs.get("market_data", {}))
        metadata.update({
            "historical_provider": metadata.get("historical_provider", "local_cache:yahoo"),
            "bridge_sessions_appended": len(appended),
            "bridge_appended_dates": [r.trading_date.isoformat() for r in appended],
            "latest_completed_candle": pd.Timestamp(
                max(r.trading_date for r in appended)
            ).isoformat(),
            "latest_completed_candle_source": RUBIX_COMPLETED_DAILY,
        })
        normalized.attrs["market_data"] = metadata
        return normalized


def _pct_diff(rubix_value, yahoo_value):
    if yahoo_value in (None, 0) or pd.isna(yahoo_value):
        return None
    return round((float(rubix_value) - float(yahoo_value)) / float(yahoo_value) * 100, 4)


def _reconcile_one(symbol, result: DailyCandleResult, yahoo_row):
    ohlcv = result.ohlcv
    yahoo = {
        "Open": float(yahoo_row["Open"]), "High": float(yahoo_row["High"]),
        "Low": float(yahoo_row["Low"]), "Close": float(yahoo_row["Close"]),
        "Volume": float(yahoo_row["Volume"]),
    }
    return ReconciliationRecord(
        symbol=symbol,
        trading_date=result.trading_date.isoformat(),
        rubix_valid=bool(result.valid),
        reason=result.reason,
        open_diff_pct=_pct_diff(ohlcv["Open"], yahoo["Open"]),
        high_diff_pct=_pct_diff(ohlcv["High"], yahoo["High"]),
        low_diff_pct=_pct_diff(ohlcv["Low"], yahoo["Low"]),
        close_diff_pct=_pct_diff(ohlcv["Close"], yahoo["Close"]),
        volume_diff_pct=_pct_diff(ohlcv["Volume"], yahoo["Volume"]),
        yahoo=yahoo,
        rubix=dict(ohlcv),
    )
