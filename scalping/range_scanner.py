"""Scalping V2 — market-wide volatility & range scanner (isolated, read-only).

Orchestrates the full pipeline on REAL data:
  * historical volatility context (ADR%, ATR%) from Yahoo/local daily cache,
  * intraday metrics (session range, move, turnover, spread, coverage) from the
    read-only Rubix DB,
  * percentile ranking across the current EGX universe,
  * an explainable 0-100 Scalping Suitability Score with separate components,
  * hard rejection filters with exact reasons,
  * range detection + long-only entry model + opportunity output.

Never modifies the Swing/Daily engine, the Adaptive Selector, the preserved
fixed-2% scalping strategy, or the external Rubix database. Never fabricates a
missing minute; sparse-data symbols are marked DATA_INSUFFICIENT (never AVOID).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from config.settings_manager import settings
from core.symbols import SYMBOL_SOURCE, load_symbols
from providers.local_cache_provider import LocalCacheProvider
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.symbol_mapping import to_rubix_symbol
from scalping.range_config import RangeScalperConfig
from scalping.range_detector import RangeClass, detect_range
from scalping.range_entry import OpportunityStatus, evaluate_entry

CAIRO = "Africa/Cairo"
SESSION_MINUTES = 270


class RangeScanner:
    """Scan the EGX universe for range-scalping suitability from real data."""

    def __init__(self, config=None, rubix_db_path=None, now=None):
        self.config = config or RangeScalperConfig.load()
        market = settings.get("market_data")
        self.rubix_path = Path(rubix_db_path or market.get(
            "rubix_db_path", "data/rubix_live_market.db"))
        cache_path = market.get("cache_path", "data/market_data_cache.sqlite")
        self.cache = LocalCacheProvider(cache_path, source_provider="yahoo")
        self.now = now or (lambda: datetime.now(timezone.utc))
        data_cfg = settings.get("data")
        self.period = data_cfg.get("history_period", "10y")

    def health(self):
        provider = RubixSQLiteProvider(
            self.rubix_path,
            expected_symbols=load_symbols(SYMBOL_SOURCE),
            now=self.now,
        )
        return provider.health()

    def scan(self, symbols=None):
        symbols = tuple(symbols or load_symbols(SYMBOL_SOURCE))
        health = self.health()
        session_date = self._latest_session_date()
        records = []
        for ticker in symbols:
            records.append(self._raw_metrics(ticker, session_date))

        frame = pd.DataFrame(records)
        frame = self._add_percentiles(frame)
        frame = self._add_scores(frame)
        frame = self._apply_rejections(frame)
        opportunities = self._build_opportunities(frame, session_date)

        return {
            "session_date": session_date,
            "health": health,
            "universe": frame,
            "opportunities": opportunities,
            "summary": self._summary(frame, opportunities),
        }

    # -- raw per-symbol metrics -------------------------------------------

    def _raw_metrics(self, ticker, session_date):
        rec = {"Symbol": ticker, "SessionDate": session_date}
        adr, atr = self._historical_volatility(ticker)
        rec["ADR_PERCENT"] = adr
        rec["ATR_PERCENT"] = atr

        intraday = self._intraday_metrics(ticker, session_date)
        rec.update(intraday)
        return rec

    def _historical_volatility(self, ticker):
        """ADR% (median 20-session) and ATR%(14) from Yahoo daily cache."""

        try:
            daily = self.cache.load_cached("yahoo", ticker, self.period, "1d",
                                           allow_expired=True)
        except Exception:
            return (None, None)
        if daily is None or len(daily) < 15:
            return (None, None)
        d = daily.tail(60).copy()
        prev_close = d["Close"].shift(1)
        adr_series = (d["High"] - d["Low"]) / prev_close * 100.0
        adr = float(adr_series.tail(self.config.adr_lookback_sessions).median())
        # ATR(14) via true range.
        tr = pd.concat([
            d["High"] - d["Low"],
            (d["High"] - prev_close).abs(),
            (d["Low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(self.config.atr_lookback_sessions).mean().iloc[-1])
        last_prev_close = float(prev_close.iloc[-1]) if pd.notna(prev_close.iloc[-1]) else None
        atr_pct = (atr / last_prev_close * 100.0) if last_prev_close else None
        return (round(adr, 4) if pd.notna(adr) else None,
                round(atr_pct, 4) if atr_pct is not None else None)

    def _intraday_metrics(self, ticker, session_date):
        mapped = to_rubix_symbol(ticker)
        out = {
            "BarCount": 0, "ValidBarCount": 0, "CoverageRatio": 0.0,
            "SESSION_RANGE_PERCENT": None, "CURRENT_MOVE_PERCENT": None,
            "REALIZED_VOL_PERCENT": None, "TURNOVER_EGP": None, "VOLUME": None,
            "SPREAD_PERCENT": None, "QuoteUpdates": 0, "QuoteAgeSec": None,
            "BidAvailable": "No", "AskAvailable": "No", "Last": None,
            "SessionOpen": None, "SessionHigh": None, "SessionLow": None,
            "DataStatus": "DATA_INSUFFICIENT", "DataReason": "not evaluated",
        }
        if not self.rubix_path.is_file():
            out["DataReason"] = "RUBIX_DB_MISSING"
            return out
        with self._connect() as conn:
            q = conn.execute(
                "SELECT last_price,bid,ask,volume,market_timestamp,received_at "
                "FROM quotes WHERE UPPER(ticker)=? ORDER BY received_at DESC LIMIT 1",
                (mapped.upper(),),
            ).fetchone()
            qcount = conn.execute(
                "SELECT COUNT(*) FROM quotes WHERE UPPER(ticker)=? AND substr(market_timestamp,1,10)=?",
                (mapped.upper(), session_date),
            ).fetchone()[0]
            crows = conn.execute(
                "SELECT minute,open,high,low,close,volume FROM candles_1m "
                "WHERE UPPER(ticker)=? AND substr(minute,1,10)=? ORDER BY minute",
                (mapped.upper(), session_date),
            ).fetchall()

        out["QuoteUpdates"] = int(qcount)
        if q is None:
            out["DataReason"] = "SYMBOL_MISSING"
            return out
        out["Last"] = _f(q["last_price"])
        out["BidAvailable"] = "Yes" if q["bid"] is not None else "No"
        out["AskAvailable"] = "Yes" if q["ask"] is not None else "No"
        bid, ask = _f(q["bid"]), _f(q["ask"])
        if bid and ask and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            out["SPREAD_PERCENT"] = round((ask - bid) / mid * 100.0, 4)
        if q["received_at"]:
            age = (pd.Timestamp.now(tz="UTC") -
                   pd.to_datetime(q["received_at"], utc=True, errors="coerce")).total_seconds()
            out["QuoteAgeSec"] = round(age, 1)

        if not crows:
            out["DataReason"] = "INTRADAY_CANDLES_MISSING"
            return out

        df = pd.DataFrame([dict(r) for r in crows])
        df["ts"] = pd.to_datetime(df["minute"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).sort_values("ts").set_index("ts")
        out["BarCount"] = len(df)
        valid = self._valid_bars(df)
        out["ValidBarCount"] = len(valid)
        out["CoverageRatio"] = round(len(valid) / SESSION_MINUTES, 4)
        if valid.empty:
            out["DataReason"] = "INTRADAY_VALID_CANDLES_MISSING"
            return out

        s_open = float(valid["open"].iloc[0])
        s_high = float(valid["high"].max())
        s_low = float(valid["low"].min())
        last = out["Last"] or float(valid["close"].iloc[-1])
        out["SessionOpen"], out["SessionHigh"], out["SessionLow"] = s_open, s_high, s_low
        if s_open > 0:
            out["SESSION_RANGE_PERCENT"] = round((s_high - s_low) / s_open * 100.0, 4)
            out["CURRENT_MOVE_PERCENT"] = round((last - s_open) / s_open * 100.0, 4)
        # realized intraday vol: std of 1-min returns, annualized-free (per session)
        rets = valid["close"].pct_change().dropna()
        out["REALIZED_VOL_PERCENT"] = round(float(rets.std() * 100.0), 4) if len(rets) > 2 else None
        volume = float(valid["volume"].sum())
        out["VOLUME"] = volume
        out["TURNOVER_EGP"] = round(last * volume, 2) if last else None

        # Coverage gate.
        if out["CoverageRatio"] >= self.config.minimum_data_coverage:
            out["DataStatus"] = "OK"
            out["DataReason"] = "sufficient intraday coverage"
        else:
            out["DataStatus"] = "DATA_INSUFFICIENT"
            out["DataReason"] = (
                f"SPARSE_COVERAGE: {len(valid)}/{SESSION_MINUTES} valid minutes "
                f"({out['CoverageRatio']:.0%} < {self.config.minimum_data_coverage:.0%})")
        return out

    @staticmethod
    def _valid_bars(df):
        ohlc = df[["open", "high", "low", "close"]]
        mask = (
            ohlc.notna().all(axis=1) & ohlc.gt(0).all(axis=1)
            & df["high"].ge(ohlc[["open", "low", "close"]].max(axis=1))
            & df["low"].le(ohlc[["open", "high", "close"]].min(axis=1))
        )
        return df[mask]

    # -- percentiles + scores ---------------------------------------------

    def _add_percentiles(self, frame):
        for col, pct in [
            ("ADR_PERCENT", "ADR_PCTL"), ("ATR_PERCENT", "ATR_PCTL"),
            ("SESSION_RANGE_PERCENT", "SESSION_RANGE_PCTL"),
            ("TURNOVER_EGP", "TURNOVER_PCTL"), ("VOLUME", "VOLUME_PCTL"),
            ("QuoteUpdates", "QUOTE_UPDATE_PCTL"),
        ]:
            frame[pct] = _percentile(frame[col])
        # Spread: lower is better, so invert.
        frame["SPREAD_PCTL"] = 100.0 - _percentile(frame["SPREAD_PERCENT"])
        return frame

    def _add_scores(self, frame):
        # Explainable component scores (0-100 each).
        frame["VolatilityScore"] = frame[["ADR_PCTL", "ATR_PCTL", "SESSION_RANGE_PCTL"]].mean(axis=1)
        frame["SessionRangeScore"] = frame["SESSION_RANGE_PCTL"]
        frame["LiquidityScore"] = frame[["TURNOVER_PCTL", "VOLUME_PCTL"]].mean(axis=1)
        frame["TurnoverScore"] = frame["TURNOVER_PCTL"]
        frame["SpreadScore"] = frame["SPREAD_PCTL"]
        frame["DataCoverageScore"] = (frame["CoverageRatio"] * 100.0).clip(0, 100)
        frame["QuoteActivityScore"] = frame["QUOTE_UPDATE_PCTL"]
        frame["VOLATILITY_RANK"] = frame[["ADR_PCTL", "ATR_PCTL", "SESSION_RANGE_PCTL"]].mean(axis=1).round(1)
        frame["LIQUIDITY_RANK"] = frame[["TURNOVER_PCTL", "VOLUME_PCTL", "QUOTE_UPDATE_PCTL"]].mean(axis=1).round(1)

        # Suitability: liquidity/spread/data GATE volatility — high volatility
        # must never fully compensate for bad liquidity/spread/data.
        # Weighted average of components, then multiplied by a data-quality gate
        # so sparse-data symbols cannot score high.
        weights = {
            "VolatilityScore": 0.25, "SessionRangeScore": 0.15,
            "LiquidityScore": 0.20, "SpreadScore": 0.15,
            "QuoteActivityScore": 0.10, "DataCoverageScore": 0.15,
        }
        weighted = sum(frame[c].fillna(0) * w for c, w in weights.items())
        data_gate = (frame["CoverageRatio"] / self.config.minimum_data_coverage).clip(0, 1)
        frame["SCALPING_SUITABILITY_SCORE"] = (weighted * data_gate).round(1)
        frame["RANGE_QUALITY_SCORE"] = frame["SessionRangeScore"].round(1)
        return frame

    # -- hard rejection filters -------------------------------------------

    def _apply_rejections(self, frame):
        reasons = []
        rejected = []
        cfg = self.config
        for _, row in frame.iterrows():
            r = []
            if row["DataStatus"] != "OK":
                r.append(row["DataReason"])
            if row["QuoteAgeSec"] is not None and row["QuoteAgeSec"] > cfg.maximum_quote_age_seconds:
                r.append(f"STALE_QUOTE: {row['QuoteAgeSec']:.0f}s > {cfg.maximum_quote_age_seconds}s")
            if row["BidAvailable"] == "No" or row["AskAvailable"] == "No":
                r.append("MISSING_BID_OR_ASK")
            if row["SPREAD_PERCENT"] is not None and row["SPREAD_PERCENT"] > cfg.maximum_spread_percent:
                r.append(f"SPREAD_TOO_WIDE: {row['SPREAD_PERCENT']:.2f}% > {cfg.maximum_spread_percent}%")
            if row["TURNOVER_EGP"] is not None and row["TURNOVER_EGP"] < cfg.minimum_turnover_egp:
                r.append(f"LOW_TURNOVER: {row['TURNOVER_EGP']:.0f} < {cfg.minimum_turnover_egp:.0f}")
            if row["QuoteUpdates"] < cfg.minimum_quote_updates:
                r.append(f"TOO_FEW_UPDATES: {row['QuoteUpdates']} < {cfg.minimum_quote_updates}")
            if row["Last"] is None or (row["Last"] is not None and row["Last"] <= 0):
                r.append("INVALID_PRICE")
            reasons.append(" | ".join(r) if r else "")
            rejected.append(bool(r))
        frame["RejectionReason"] = reasons
        frame["Rejected"] = rejected
        return frame

    # -- opportunity building ---------------------------------------------

    def _build_opportunities(self, frame, session_date):
        rows = []
        for _, row in frame.iterrows():
            ticker = row["Symbol"]
            if row["DataStatus"] != "OK":
                rows.append(self._opp_row(ticker, OpportunityStatus.DATA_INSUFFICIENT,
                                          row, reason=row["DataReason"]))
                continue
            if row["Rejected"]:
                status = self._reject_status(row["RejectionReason"])
                rows.append(self._opp_row(ticker, status, row, reason=row["RejectionReason"]))
                continue
            # Data OK and not hard-rejected -> run range detection + entry model.
            session = self._load_session_frame(ticker, session_date)
            prev_day = self._prev_day_levels(ticker)
            levels = detect_range(
                session, prev_day=prev_day,
                lower_zone_percent=self.config.range_lower_zone_percent,
                upper_zone_percent=self.config.range_upper_zone_percent,
            )
            atr_value = self._intraday_atr(session)
            opp = evaluate_entry(ticker, levels, bid=None, ask=None,
                                 atr_value=atr_value, config=self.config)
            rows.append(self._merge_opp(ticker, opp, levels, row))
        return pd.DataFrame(rows)

    def _load_session_frame(self, ticker, session_date):
        mapped = to_rubix_symbol(ticker)
        with self._connect() as conn:
            crows = conn.execute(
                "SELECT minute,open,high,low,close,volume FROM candles_1m "
                "WHERE UPPER(ticker)=? AND substr(minute,1,10)=? ORDER BY minute",
                (mapped.upper(), session_date),
            ).fetchall()
        if not crows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in crows])
        df["ts"] = pd.to_datetime(df["minute"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"]).sort_values("ts").set_index("ts")
        valid = self._valid_bars(df)
        valid.index = valid.index.tz_convert(CAIRO)
        return valid.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]

    def _prev_day_levels(self, ticker):
        try:
            daily = self.cache.load_cached("yahoo", ticker, self.period, "1d", allow_expired=True)
        except Exception:
            return None
        if daily is None or daily.empty:
            return None
        last = daily.iloc[-1]
        return {"High": float(last["High"]), "Low": float(last["Low"]),
                "Close": float(last["Close"])}

    def _intraday_atr(self, session):
        if session is None or len(session) < 3:
            return None
        tr = pd.concat([
            session["High"] - session["Low"],
            (session["High"] - session["Close"].shift(1)).abs(),
            (session["Low"] - session["Close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        value = tr.rolling(min(self.config.intraday_atr_period, len(tr))).mean().iloc[-1]
        return float(value) if pd.notna(value) else None

    @staticmethod
    def _reject_status(reason):
        if "SPREAD_TOO_WIDE" in reason:
            return OpportunityStatus.SPREAD_TOO_WIDE
        if "LOW_TURNOVER" in reason:
            return OpportunityStatus.LIQUIDITY_TOO_LOW
        if "SPARSE_COVERAGE" in reason or "MISSING" in reason or "CANDLES" in reason:
            return OpportunityStatus.DATA_INSUFFICIENT
        return OpportunityStatus.NO_TRADE

    def _opp_row(self, ticker, status, row, *, reason):
        return {
            "Symbol": ticker, "Status": status.value, "Pattern": None,
            "Last": row.get("Last"), "SessionRange%": row.get("SESSION_RANGE_PERCENT"),
            "ADR%": row.get("ADR_PERCENT"), "ATR%": row.get("ATR_PERCENT"),
            "Turnover": row.get("TURNOVER_EGP"), "Spread%": row.get("SPREAD_PERCENT"),
            "Coverage": row.get("CoverageRatio"),
            "Classification": None, "RangePosition%": None,
            "EntryZone": None, "Stop": None, "T1": None, "T2": None, "T3": None,
            "NetRR": None, "SuitabilityScore": row.get("SCALPING_SUITABILITY_SCORE"),
            "Reason": reason, "Invalidation": None,
        }

    def _merge_opp(self, ticker, opp, levels, row):
        entry = None
        if opp.entry_zone_low is not None:
            entry = f"{opp.entry_zone_low}-{opp.entry_zone_high}"
        best_rr = max([r for r in (opp.net_rr_t1, opp.net_rr_t2, opp.net_rr_t3)
                       if r is not None], default=None)
        return {
            "Symbol": ticker, "Status": opp.status.value, "Pattern": opp.pattern,
            "Last": levels.last, "SessionRange%": levels.session_range_percent,
            "ADR%": row.get("ADR_PERCENT"), "ATR%": row.get("ATR_PERCENT"),
            "Turnover": row.get("TURNOVER_EGP"), "Spread%": row.get("SPREAD_PERCENT"),
            "Coverage": row.get("CoverageRatio"),
            "Classification": levels.classification.value,
            "RangePosition%": opp.range_position_percent,
            "EntryZone": entry, "Stop": opp.stop,
            "T1": opp.target_1, "T2": opp.target_2, "T3": opp.target_3,
            "NetRR": best_rr, "SuitabilityScore": row.get("SCALPING_SUITABILITY_SCORE"),
            "Reason": opp.reason, "Invalidation": opp.invalidation,
        }

    def _summary(self, frame, opportunities):
        return {
            "active_symbols": len(frame),
            "valid_intraday_data": int((frame["DataStatus"] == "OK").sum()),
            "data_insufficient": int((frame["DataStatus"] != "OK").sum()),
            "hard_rejected": int(frame["Rejected"].sum()),
            "ready_opportunities": int((opportunities["Status"] == "RANGE_BUY_READY").sum())
            if not opportunities.empty else 0,
            "spread_rejections": int(frame["RejectionReason"].str.contains("SPREAD_TOO_WIDE").sum()),
            "data_quality_rejections": int(frame["RejectionReason"].str.contains(
                "SPARSE_COVERAGE|MISSING|CANDLES").sum()),
        }

    # -- infra -------------------------------------------------------------

    def _latest_session_date(self):
        if not self.rubix_path.is_file():
            return None
        with self._connect() as conn:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT substr(minute,1,10) d FROM candles_1m ORDER BY d DESC LIMIT 8"
            ).fetchall()]
            for d in dates:
                n = conn.execute(
                    "SELECT COUNT(DISTINCT ticker) FROM candles_1m WHERE substr(minute,1,10)=?",
                    (d,)).fetchone()[0]
                if n >= 50:
                    return d
        return dates[0] if dates else None

    def _connect(self):
        uri = f"file:{self.rubix_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


def _percentile(series):
    numeric = pd.to_numeric(series, errors="coerce")
    return (numeric.rank(pct=True) * 100.0).round(1)


def _f(value):
    if value is None or pd.isna(value):
        return None
    return float(value)
