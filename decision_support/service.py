"""Read-only advisory analysis over already-final scanner decisions.

This module deliberately does not call the scanner, strategy, AI, portfolio,
or broker code.  It enriches a copy of completed scan rows for presentation,
paper alerts, and reproducible decision-support reporting only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from config.settings_manager import settings
from decision_support.analytics import performance_analytics
from decision_support.database import DecisionSupportDatabase
from decision_support.edge_score import calculate_edge_score
from decision_support.market_health import calculate_market_health
from decision_support.quality import (
    atr_feasibility,
    bid_ask_balance,
    finite,
    liquidity_metrics,
    relative_volume,
    spread_metrics,
)
from decision_support.reporting import write_daily_report
from decision_support.sector_analysis import load_sector_map, sector_summary
from sector_flow.strength import load_latest_strength
from providers.symbol_mapping import to_engine_symbol, to_rubix_symbol


class DecisionSupportService:
    """Build transparent Edge evidence without mutating frozen decisions."""

    def __init__(self, config=None, rubix_db=None, database=None, now=None):
        self.config = dict(config or settings.get("decision_support"))
        market_data = settings.get("market_data")
        self.rubix_db = Path(
            rubix_db or market_data.get("rubix_db_path") or "data/rubix_live_market.db"
        )
        self.database = database or DecisionSupportDatabase(
            self.config.get("database_path", "data/decision_support.db")
        )
        self._now = now or (lambda: datetime.now(timezone.utc).astimezone())

    def analyze(self, results, persist=True, write_report=True):
        """Return an additive advisory snapshot for completed scanner rows."""

        source_rows = list(results or [])
        market = calculate_market_health(
            source_rows, int(self.config.get("rvol_lookback", 20))
        )
        quotes = self._latest_quotes(
            row.get("Ticker") for row in source_rows)
        sectors = load_sector_map(self.config.get("sector_file", "data/sectors.csv"))
        historical = self._historical_setup_scores()
        now = self._aware_now()
        rows = [
            self._enrich(row, quotes.get(str(row.get("Ticker", "")).upper()), sectors, historical, market, now)
            for row in source_rows
        ]
        rows.sort(
            key=lambda row: (
                -row["EdgeScore"],
                -_number(row.get("StrategyScore")),
                -_number(row.get("Confidence")),
                -_number(row.get("RiskReward")),
                row["Ticker"],
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["EdgeRank"] = rank

        sectors_frame = sector_summary(rows)
        self._apply_sector_strength(rows, sectors_frame)
        # Sector evidence may alter only the additive Edge metric, so perform
        # one final deterministic pass after the optional sector aggregation.
        rows.sort(
            key=lambda row: (
                -row["EdgeScore"],
                -_number(row.get("StrategyScore")),
                -_number(row.get("Confidence")),
                -_number(row.get("RiskReward")),
                row["Ticker"],
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["EdgeRank"] = rank

        if persist:
            self._persist(rows, market)
        report = None
        if write_report:
            report = write_daily_report(rows, market, sectors_frame, now=now)
        return {
            "rows": rows,
            "market_health": market,
            "sectors": sectors_frame,
            "alerts": self.database.rows(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 100"
            ),
            "pinned": {
                row["ticker"] for row in self.database.rows(
                    "SELECT ticker FROM pinned_symbols ORDER BY ticker"
                )
            },
            "daily_report": str(report) if report else None,
        }

    def _enrich(self, original, quote, sectors, historical, market, now):
        # A shallow copy plus copied lists/dicts protects all frozen values in
        # the original session-state row. The price DataFrame is read-only.
        row = dict(original)
        ticker = str(row.get("Ticker") or "UNKNOWN").upper()
        frame = row.get("Data")
        metadata = dict(getattr(frame, "attrs", {}).get("market_data", {}))
        last = frame.iloc[-1] if isinstance(frame, pd.DataFrame) and not frame.empty else {}
        price = _first(quote, "last_price", row.get("Price"), _value(last, "Close"))
        volume = _first(quote, "volume", _value(last, "Volume"))
        bid = _first(quote, "bid")
        ask = _first(quote, "ask")
        rvol = relative_volume(frame, int(self.config.get("rvol_lookback", 20)))
        spread = spread_metrics(bid, ask)
        liquidity = liquidity_metrics(
            price, volume, rvol, spread["spread_score"],
            bid_size=_first(quote, "bid_size"),
            ask_size=_first(quote, "ask_size"),
            trades=_first(quote, "trades"),
            config=self.config,
        )
        atr = _value(last, "ATR")
        atr_data = atr_feasibility(
            atr, price, self.config.get("atr_target_percent", 2.0)
        )
        provider = str(
            row.get("DataSource")
            or metadata.get("effective_provider")
            or metadata.get("provider")
            or "unknown"
        ).lower()
        freshness = str(
            metadata.get("freshness")
            or metadata.get("provider_status")
            or row.get("OperationalStatus")
            or "UNKNOWN"
        ).upper()
        received = _first(quote, "received_at", metadata.get("received_timestamp"))
        latency = _age_seconds(now, received)
        sector = sectors.get(ticker, sectors.get(to_rubix_symbol(ticker), "Unknown"))
        setup = _classify_setup(row)
        resistance_room = _resistance_room(price, row.get("Resistance"))
        support_quality = _support_quality(price, row.get("Support"))
        factors = {
            "trend_quality": _scaled(row.get("Trend"), 30.0),
            "momentum": _scaled(row.get("Momentum"), 20.0),
            "relative_volume": min(1.0, rvol / 2.0) if rvol is not None else None,
            "liquidity": liquidity["liquidity_score"],
            "spread": spread["spread_score"],
            "bid_ask_balance": bid_ask_balance(
                _first(quote, "bid_size"), _first(quote, "ask_size")
            ),
            "atr_feasibility": atr_data["atr_score"],
            "resistance_room": resistance_room,
            "support_quality": support_quality,
            "market_strength": market.get("factor_score"),
            "sector_strength": None,
            "setup_quality": max(
                value for value in (
                    _scaled(row.get("Breakout"), 20.0),
                    _scaled(row.get("Candles"), 20.0),
                    _scaled(row.get("Score"), 100.0),
                    0.0,
                ) if value is not None
            ),
            "rubix_freshness": _freshness_factor(provider, freshness, latency),
            "historical_performance": historical.get(setup),
            "volatility": _volatility_factor(atr_data.get("atr_percent")),
            "ai_probability": (
                _scaled(row.get("AIProbability"), 100.0)
                if self.config.get("include_ai_probability", True) else None
            ),
        }
        edge = calculate_edge_score(factors, self.config.get("edge_weights"))
        warnings = _warnings(
            provider, freshness, latency, spread, liquidity, atr_data,
            resistance_room, market, edge,
            max_spread=float(self.config.get("max_spread_percent", 0.5)),
        )
        gate = "BLOCKED" if any(
            warning.startswith(("DATA_", "SPREAD_TOO_WIDE", "LIQUIDITY_POOR"))
            for warning in warnings
        ) else "MEETS_CRITERIA"
        entry_low = finite(row.get("BuyLow"))
        entry_high = finite(row.get("BuyHigh"))
        entry_zone = (
            f"{entry_low:.2f} – {entry_high:.2f}"
            if entry_low is not None and entry_high is not None else "Unavailable"
        )
        recommendation = _recommendation(edge["edge_score"], gate, warnings)
        advisory_risk = (
            "HIGH" if gate == "BLOCKED" else "ELEVATED" if edge["edge_score"] < 7.0 else "CONTROLLED"
        )
        observed_at = now.isoformat()
        return {
            "Ticker": ticker,
            "EdgeScore": edge["edge_score"],
            "EvidenceCompleteness": edge["evidence_completeness_percent"],
            "EdgeContributions": edge["edge_contributions"],
            "MissingEvidence": edge["missing_edge_factors"],
            "EdgeFactors": factors,
            "ExistingSignal": row.get("Signal"),
            "StrategyRank": row.get("Rank"),
            "StrategyScore": row.get("Score"),
            "Confidence": row.get("Confidence"),
            "RiskReward": row.get("RR"),
            "Recommendation": recommendation,
            "QualityGate": gate,
            "AdvisoryRisk": advisory_risk,
            "Warnings": warnings,
            "TechnicalReasons": row.get("Reasons") or "",
            "EntryZone": entry_zone,
            "CurrentPrice": price,
            "TargetPlus2Percent": price * 1.02 if price is not None else None,
            "StopMinus2Percent": price * 0.98 if price is not None else None,
            "Invalidation": (
                f"Below {finite(row.get('StopLoss')):.2f}"
                if finite(row.get("StopLoss")) is not None else "Unavailable"
            ),
            "EstimatedHoldingTime": "Intraday for paper scalping; strategy horizon unchanged",
            "ATR": atr,
            "ATRPercent": atr_data["atr_percent"],
            "ATRFeasibleFor2Percent": atr_data["atr_feasible"],
            "Volume": volume,
            "RelativeVolume": rvol,
            "Turnover": liquidity["turnover"],
            "LiquidityScore": liquidity["liquidity_score"],
            "LiquidityQuality": liquidity["liquidity_quality"],
            "LiquidityComponents": liquidity["liquidity_components"],
            "SpreadPercent": spread["spread_percent"],
            "SpreadQuality": spread["spread_quality"],
            "Bid": bid,
            "Ask": ask,
            "BidSize": _first(quote, "bid_size"),
            "AskSize": _first(quote, "ask_size"),
            "Trades": _first(quote, "trades"),
            "BidAskBalance": factors["bid_ask_balance"],
            "MomentumScore": factors["momentum"],
            "SetupType": setup,
            "Sector": sector,
            "SectorStrength": None,
            "MarketStatus": market.get("market_bias"),
            "Provider": provider,
            "Freshness": freshness,
            "LatencySeconds": latency,
            "ObservedAt": observed_at,
        }

    def _latest_quotes(self, symbols=()):
        """Newest quote per requested symbol, read through the ticker index.

        Read-only, as every reader of the collector's database is; absence is
        evidence and never an exception.

        This used to be ``SELECT ... FROM quotes ORDER BY received_at DESC``
        followed by ``fetchall()``, keeping the first row seen per ticker. That
        asks SQLite to sort 22 million rows by a column no index covers and then
        materialises every one of them as a Python dict, to keep about 240.

        It ran inside the Daily Dashboard's post-scan render, so the page could
        not finish drawing until it did: after a completed scan on 2026-08-31
        the tab sat on a stale progress panel reading "Unknown -- no dated
        candle" while this consumed 3.8 GB, and only redrew once it returned.
        The scan was correct and finished the whole time; the page was blocked
        behind this.

        Now each requested ticker is fetched through
        ``idx_quotes_ticker_time (ticker, market_timestamp)`` and only the
        newest row is read. Ordering by ``market_timestamp`` rather than
        ``received_at`` is the same substitution made in
        ``RubixSQLiteProvider.load_latest_quote_overlays``, for the same reason:
        ``received_at`` is in no index, and it can only make a quote look older
        than it is, never fresher.

        Asking for nothing returns nothing. There is no whole-table path left --
        an unbounded read of this table is never the right answer.
        """

        wanted = {}
        for symbol in symbols or ():
            engine = str(symbol or "").strip().upper()
            if engine:
                wanted.setdefault(str(to_rubix_symbol(engine)).upper(), engine)
        if not wanted or not self.rubix_db.is_file():
            return {}
        uri = f"file:{self.rubix_db.resolve().as_posix()}?mode=ro"
        latest = {}
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                connection.row_factory = sqlite3.Row
                columns = {
                    str(row[1]).lower()
                    for row in connection.execute("PRAGMA table_info(quotes)")
                }
                required = {
                    "ticker", "last_price", "bid", "ask", "volume",
                    "market_timestamp", "received_at",
                }
                if not required.issubset(columns):
                    return {}
                optional = [name for name in ("bid_size", "ask_size", "trades") if name in columns]
                projection = ",".join(sorted(required) + optional)
                query = (f"SELECT {projection} FROM quotes WHERE ticker=? "
                         "ORDER BY market_timestamp DESC LIMIT 1")
                for ticker in wanted:
                    raw = connection.execute(query, (ticker,)).fetchone()
                    if raw is None:
                        continue
                    value = dict(raw)
                    # The key is still derived from the row's own ticker, so the
                    # mapping back to the engine symbol is unchanged.
                    latest.setdefault(
                        to_engine_symbol(value.get("ticker")).upper(), value)
        except (OSError, sqlite3.Error):
            return {}
        return latest

    def _historical_setup_scores(self):
        analytics = performance_analytics()
        frame = analytics.get("by_setup")
        if frame is None or frame.empty:
            return {}
        return {
            str(row["setup"]): min(1.0, max(0.0, float(row["WinRate"]) / 100.0))
            for _, row in frame.iterrows()
        }

    def _apply_sector_strength(self, rows, sectors):
        """Score each row's sector, preferring an independently measured strength.

        ``sector_summary`` derives a sector's strength from the Edge and
        Momentum of the very rows being scored, and that strength then feeds
        back into their Edge scores -- the quantity is partly a function of
        itself. ``sector_flow`` measures it instead from how far the sector's
        traded value exceeded its own trailing median, which nothing in this
        scan can influence, so it is preferred wherever it is available.

        The circular value remains the fallback. It is a weaker measurement,
        but it is a real one, and dropping the factor entirely would silently
        change what an Edge score means.
        """

        derived = {}
        if sectors is not None and not sectors.empty:
            derived = {
                str(row["Sector"]): float(row["SectorStrength"])
                for _, row in sectors.iterrows() if str(row["Sector"]) != "Unknown"
            }
        measured = load_latest_strength(
            self.config.get("sector_flow_database", "data/sector_flow.db")
        )
        if not derived and not measured:
            return

        for row in rows:
            sector = str(row.get("Sector"))
            strength = measured.get(sector)
            source = "LIQUIDITY"
            if strength is None:
                strength, source = derived.get(sector), "SCAN_DERIVED"
            row["SectorStrengthSource"] = source if strength is not None else None
            row["SectorStrength"] = strength
            row["EdgeFactors"]["sector_strength"] = strength
            edge = calculate_edge_score(
                row["EdgeFactors"], self.config.get("edge_weights")
            )
            row["EdgeScore"] = edge["edge_score"]
            row["EvidenceCompleteness"] = edge["evidence_completeness_percent"]
            row["EdgeContributions"] = edge["edge_contributions"]
            row["MissingEvidence"] = edge["missing_edge_factors"]

    def _persist(self, rows, market):
        if market.get("market_bias") == "WEAK":
            self.database.alert(
                "MARKET_WEAKNESS", "Overall market evidence is currently weak.",
                key=f"MARKET_WEAKNESS|{self._aware_now().date().isoformat()}",
            )
        for row in rows:
            previous, inserted = self.database.record_observation(row)
            if not inserted:
                continue
            ticker = row["Ticker"]
            if row["EdgeScore"] >= float(self.config.get("edge_alert_threshold", 9.0)):
                self.database.alert(
                    "EDGE_ABOVE_THRESHOLD",
                    f"{ticker} Edge reached {row['EdgeScore']:.2f}/10 (paper alert only).",
                    ticker=ticker,
                )
            if str(row.get("ExistingSignal")).upper() == "BUY":
                self.database.alert(
                    "NEW_OPPORTUNITY",
                    f"{ticker} has a frozen-strategy BUY signal; user decision required.",
                    ticker=ticker,
                )
            if row.get("Provider") != "rubix":
                self.database.alert(
                    "YAHOO_FALLBACK", f"{ticker} is using {row.get('Provider')} data.", ticker=ticker
                )
            if previous:
                old_spread = finite(previous.get("spread_percent"))
                new_spread = finite(row.get("SpreadPercent"))
                if old_spread and new_spread is not None and new_spread <= old_spread * 0.75:
                    self.database.alert(
                        "SPREAD_IMPROVED", f"{ticker} spread improved materially.", ticker=ticker
                    )
                old_liquidity = finite(previous.get("liquidity_score"))
                new_liquidity = finite(row.get("LiquidityScore"))
                if old_liquidity is not None and new_liquidity is not None and new_liquidity >= old_liquidity + 0.2:
                    self.database.alert(
                        "LIQUIDITY_IMPROVED", f"{ticker} liquidity evidence improved.", ticker=ticker
                    )

    def _aware_now(self):
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _number(value):
    return finite(value) or 0.0


def _first(mapping, key, *fallbacks):
    if mapping:
        value = finite(mapping.get(key)) if key not in {"received_at"} else mapping.get(key)
        if value is not None:
            return value
    for value in fallbacks:
        candidate = value if key == "received_at" else finite(value)
        if candidate is not None:
            return candidate
    return None


def _value(row, key):
    try:
        return finite(row.get(key))
    except AttributeError:
        return None


def _scaled(value, maximum):
    value = finite(value)
    return min(1.0, max(0.0, value / maximum)) if value is not None else None


def _age_seconds(now, value):
    if value is None:
        return None
    try:
        current = pd.Timestamp(now)
        current = current.tz_localize("UTC") if current.tzinfo is None else current.tz_convert("UTC")
        stamp = pd.Timestamp(value)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        return max(0.0, (current - stamp).total_seconds())
    except Exception:
        return None


def _freshness_factor(provider, freshness, latency):
    if provider != "rubix":
        return 0.0
    if "FRESH" not in freshness:
        return 0.0
    if latency is None:
        return 0.5
    return max(0.0, min(1.0, 1.0 - latency / 300.0))


def _resistance_room(price, resistance):
    price, resistance = finite(price), finite(resistance)
    if price is None or resistance is None or price <= 0:
        return None
    if resistance <= price:
        return 1.0
    return min(1.0, max(0.0, (resistance / price - 1.0) / 0.02))


def _support_quality(price, support):
    price, support = finite(price), finite(support)
    if price is None or support is None or price <= 0 or support > price:
        return None
    distance = (price - support) / price
    return max(0.0, min(1.0, 1.0 - distance / 0.05))


def _volatility_factor(atr_percent):
    value = finite(atr_percent)
    if value is None:
        return None
    if value <= 2.0:
        return max(0.0, value / 2.0)
    return max(0.0, 1.0 - max(0.0, value - 5.0) / 5.0)


def _classify_setup(row):
    reasons = str(row.get("Reasons") or "").upper()
    if "BREAKOUT" in reasons or _number(row.get("Breakout")) >= 10:
        return "MOMENTUM_BREAKOUT"
    if "PULLBACK" in reasons:
        return "PULLBACK"
    if "REVERS" in reasons:
        return "REVERSAL"
    if _number(row.get("Momentum")) >= 10:
        return "MOMENTUM"
    return "GENERAL"


def _warnings(provider, freshness, latency, spread, liquidity, atr, resistance_room, market, edge, max_spread):
    warnings = []
    if provider != "rubix":
        warnings.append("DATA_FALLBACK: Rubix is not the effective provider")
    if "FRESH" not in freshness:
        warnings.append(f"DATA_STALE: freshness is {freshness}")
    if latency is None:
        warnings.append(
            "DATA_QUOTE_UNAVAILABLE" if provider == "rubix" else "LATENCY_UNAVAILABLE"
        )
    if spread["spread_percent"] is None:
        warnings.append("SPREAD_UNAVAILABLE")
    elif spread["spread_percent"] > max_spread:
        warnings.append("SPREAD_TOO_WIDE")
    if liquidity["liquidity_score"] is None:
        warnings.append("LIQUIDITY_EVIDENCE_INCOMPLETE")
    elif liquidity["liquidity_quality"] == "Poor":
        warnings.append("LIQUIDITY_POOR")
    if atr["atr_feasible"] is False:
        warnings.append("LOW_PROFIT_POTENTIAL")
    if resistance_room is not None and resistance_room < 1.0:
        warnings.append("LIMITED_RESISTANCE_ROOM")
    if market.get("market_bias") == "WEAK":
        warnings.append("MARKET_WEAK")
    if edge["evidence_completeness_percent"] < 70:
        warnings.append("EVIDENCE_INCOMPLETE")
    return list(dict.fromkeys(warnings))


def _recommendation(edge, gate, warnings):
    if "SPREAD_TOO_WIDE" in warnings:
        return "The spread is too wide; I would wait for better execution quality."
    if "LIQUIDITY_POOR" in warnings:
        return "Volume or liquidity is insufficient; I would wait."
    if "MARKET_WEAK" in warnings:
        return "The market is weak; this opportunity needs extra confirmation."
    if gate == "MEETS_CRITERIA" and edge >= 8.5:
        return "This opportunity meets the configured high-quality criteria."
    if gate == "MEETS_CRITERIA" and edge >= 7.0:
        return "This setup looks promising; wait for execution-quality confirmation."
    return "I would wait; quality evidence is incomplete or weak."
