"""Chronological forward-testing and paper-portfolio orchestration.

This module consumes frozen scanner decisions. It never recalculates or
changes a signal; future evaluation always uses candles strictly later than
the immutable signal timestamp.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtesting.config import load as load_backtest_config
from backtesting.context import BacktestContext
from backtesting.costs import TradingCosts
from backtesting.managers.exit_manager import ExitManager
from config.settings_manager import settings
from core.data_loader import load_data
from core.market_data import MarketData
from forward_testing.database import ForwardDatabase
from indicators.technical import calculate_indicators
from portfolio.sizing import PositionSizer


HORIZONS = (1, 3, 5, 10, 20)
UUID_NAMESPACE = uuid.UUID("790041c6-3946-4d28-8249-8d2eb53f78ba")


def _now(value=None):
    return value or datetime.now(timezone.utc).astimezone()


def _number(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def settings_hash():
    """Hash the effective merged settings used by the live decision."""
    settings.reload()
    encoded = json.dumps(
        _json_safe(settings.data), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ForwardTestingService:
    """Resume-safe session processor backed by immutable evidence tables."""

    def __init__(self, database=None):
        self.database = database or ForwardDatabase()

    def process_scan(self, results, run_id, run_directory=None, now=None):
        timestamp = _now(now)
        current_hash = settings_hash()
        session_id = str(uuid.uuid5(UUID_NAMESPACE, f"SESSION|{run_id}"))
        frames = {
            row["Ticker"]: row.get("Data")
            for row in results
            if row.get("Ticker") and row.get("Data") is not None
        }

        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO live_sessions
                   (session_id, run_id, session_date, started_at, settings_hash, status)
                   VALUES (?, ?, ?, ?, ?, 'RUNNING')""",
                (session_id, run_id, timestamp.date().isoformat(),
                 timestamp.isoformat(), current_hash),
            )

        try:
            # Old signals and positions are processed before today's immutable
            # signals are inserted, making same-session look-ahead impossible.
            evaluations = self.evaluate_pending(frames, timestamp)
            portfolio_updates = self.update_paper_portfolio(frames, timestamp)
            inserted = self.record_signals(results, run_id, current_hash, timestamp)
            snapshot = self.snapshot_portfolio(run_id, frames, timestamp)

            from forward_testing.reporting import ForwardReportGenerator
            reports = ForwardReportGenerator(self).generate_all(
                timestamp, run_id, Path(run_directory) if run_directory else None
            )

            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE live_sessions SET completed_at=?, status='COMPLETED',
                       signal_count=? WHERE run_id=?""",
                    (timestamp.isoformat(), inserted, run_id),
                )
            return {
                "session_id": session_id,
                "new_signals": inserted,
                "new_evaluations": evaluations,
                "portfolio_updates": portfolio_updates,
                "portfolio": snapshot,
                "reports": reports,
            }
        except Exception as error:
            # A restart can inspect and diagnose the failed session instead of
            # mistaking an incomplete run for a successful daily scan.
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE live_sessions SET completed_at=?, status='FAILED',
                       error=? WHERE run_id=?""",
                    (timestamp.isoformat(), str(error), run_id),
                )
            raise

    def record_signals(self, results, run_id, current_hash, timestamp):
        inserted = 0
        with self.database.transaction() as connection:
            for stock in results:
                frame = stock.get("Data")
                if frame is None or frame.empty:
                    continue
                candle_time = pd.Timestamp(frame.index[-1])
                signal_date = candle_time.date().isoformat()
                signal_type = str(stock.get("Signal", "UNKNOWN"))
                ticker = str(stock["Ticker"])
                dedupe = f"{ticker}|{signal_date}|{signal_type}"
                signal_id = str(uuid.uuid5(UUID_NAMESPACE, dedupe))
                indicators = _json_safe(stock.get("AIFeatures", frame.iloc[-1]))
                values = (
                    signal_id, dedupe, signal_date,
                    # Date belongs to the source candle; time belongs to the
                    # real scan execution, so a daily candle never appears as
                    # if it was generated at midnight.
                    timestamp.time().isoformat(),
                    timestamp.isoformat(), ticker, signal_type,
                    _number(stock.get("Price")), _number(stock.get("Score")),
                    _number(stock.get("Confidence")), _number(stock.get("RR")),
                    _number(stock.get("AIProbability")), stock.get("Regime"),
                    _number(stock.get("Rank")), stock.get("Reasons", ""),
                    json.dumps(indicators, sort_keys=True, ensure_ascii=False),
                    current_hash, run_id, _number(stock.get("BuyLow")),
                    _number(stock.get("BuyHigh")), _number(stock.get("StopLoss")),
                    _number(stock.get("Target1")), _number(stock.get("Target2")),
                )
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO signals
                       (signal_id,dedupe_key,signal_date,signal_time,recorded_at,
                        ticker,signal_type,price,score,confidence,rr,ai_probability,
                        market_regime,ranking,reason,indicators_json,settings_hash,
                        run_id,buy_low,buy_high,stop_loss,target1,target2)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                if cursor.rowcount != 1:
                    continue
                inserted += 1
                self._event(connection, signal_id, "SIGNAL_CREATED", timestamp, {
                    "signal_type": signal_type, "run_id": run_id,
                })
                # The strategy signal remains immutable in the evidence table.
                # Only fresh, open-session Rubix BUYs may create a new alert or
                # paper position; legacy callers without this additive field
                # retain their existing behavior.
                if signal_type == "BUY" and stock.get("Actionable", True):
                    self._alert(
                        connection, signal_id, "NEW_BUY", timestamp,
                        f"New BUY signal: {ticker} at {stock.get('Price')}",
                    )
                    position_id = str(uuid.uuid5(UUID_NAMESPACE, f"POSITION|{signal_id}"))
                    connection.execute(
                        """INSERT OR IGNORE INTO paper_positions
                           (position_id,signal_id,ticker,status,created_at,stop_loss,target1,target2)
                           VALUES (?, ?, ?, 'PENDING_ENTRY', ?, ?, ?, ?)""",
                        (position_id, signal_id, ticker, timestamp.isoformat(),
                         _number(stock.get("StopLoss")), _number(stock.get("Target1")),
                         _number(stock.get("Target2"))),
                    )
                    self._paper_event(connection, position_id, "PENDING_ENTRY", timestamp, {})
        return inserted

    def evaluate_pending(self, frames=None, timestamp=None):
        frames = frames or {}
        timestamp = _now(timestamp)
        signals = self.database.rows(
            """SELECT s.* FROM signals s WHERE s.signal_type='BUY'
               AND EXISTS (SELECT 1 FROM paper_positions p WHERE p.signal_id=s.signal_id)
               AND NOT EXISTS (SELECT 1 FROM signal_evaluations e
                 WHERE e.signal_id=s.signal_id AND e.horizon_days=20)"""
        )
        inserted = 0
        for signal in signals:
            frame = self._frame(signal["ticker"], frames)
            if frame is None:
                continue
            future = frame[pd.to_datetime(frame.index).date > pd.Timestamp(signal["signal_date"]).date()]
            completed = {
                row["horizon_days"] for row in self.database.rows(
                    "SELECT horizon_days FROM signal_evaluations WHERE signal_id=?",
                    (signal["signal_id"],),
                )
            }
            for horizon in HORIZONS:
                if horizon in completed or len(future) < horizon:
                    continue
                evaluation = self._evaluate_horizon(signal, future.iloc[:horizon], horizon, timestamp)
                with self.database.transaction() as connection:
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO signal_evaluations
                           (evaluation_id,signal_id,horizon_days,evaluated_at,as_of_date,
                            bars_available,return_pct,mfe_pct,mae_pct,hit_tp,hit_sl,status)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        tuple(evaluation.values()),
                    )
                    if cursor.rowcount == 1:
                        inserted += 1
                        self._event(
                            connection, signal["signal_id"],
                            f"EVALUATED_{horizon}D", timestamp, evaluation,
                        )
                        if evaluation["status"] in {"HIT_TP", "HIT_SL", "EXPIRED"}:
                            alert_type = {
                                "HIT_TP": "TP_REACHED", "HIT_SL": "SL_REACHED",
                                "EXPIRED": "SIGNAL_EXPIRED",
                            }[evaluation["status"]]
                            self._alert(
                                connection, signal["signal_id"], alert_type, timestamp,
                                f"{signal['ticker']}: {evaluation['status']} by {horizon} bars",
                            )
        return inserted

    def _evaluate_horizon(self, signal, candles, horizon, timestamp):
        price = float(signal["price"])
        stop = _number(signal.get("stop_loss"))
        target = _number(signal.get("target1"))
        hit_tp = hit_sl = False
        status = "STILL_OPEN"
        # Conservative OHLC ordering matches the frozen ExitManager: stop is
        # checked before target when both occur inside one daily candle.
        for _, candle in candles.iterrows():
            if stop is not None and float(candle["Low"]) <= stop:
                hit_sl, status = True, "HIT_SL"
                break
            if target is not None and float(candle["High"]) >= target:
                hit_tp, status = True, "HIT_TP"
                break
        if status == "STILL_OPEN" and horizon == max(HORIZONS):
            status = "EXPIRED"
        evaluation_id = str(uuid.uuid5(
            UUID_NAMESPACE, f"EVAL|{signal['signal_id']}|{horizon}"
        ))
        return {
            "evaluation_id": evaluation_id,
            "signal_id": signal["signal_id"],
            "horizon_days": horizon,
            "evaluated_at": timestamp.isoformat(),
            "as_of_date": pd.Timestamp(candles.index[-1]).date().isoformat(),
            "bars_available": len(candles),
            "return_pct": round((float(candles.iloc[-1]["Close"]) / price - 1) * 100, 4),
            "mfe_pct": round((float(candles["High"].max()) / price - 1) * 100, 4),
            "mae_pct": round((float(candles["Low"].min()) / price - 1) * 100, 4),
            "hit_tp": int(hit_tp),
            "hit_sl": int(hit_sl),
            "status": status,
        }

    def update_paper_portfolio(self, frames=None, timestamp=None):
        frames = frames or {}
        timestamp = _now(timestamp)
        updated = 0
        # Close existing positions before allocating cash to pending entries.
        for position in self.database.rows(
            """SELECT p.*,s.signal_date FROM paper_positions p JOIN signals s
               ON s.signal_id=p.signal_id WHERE p.status='OPEN'"""
        ):
            frame = self._frame(position["ticker"], frames)
            if frame is not None and self._try_close(position, frame, timestamp):
                updated += 1

        candidates = []
        cfg = load_backtest_config()
        for position in self.database.rows(
            """SELECT p.*,s.signal_date,s.buy_low,s.buy_high,s.ranking
               FROM paper_positions p JOIN signals s ON s.signal_id=p.signal_id
               WHERE p.status='PENDING_ENTRY' ORDER BY s.ranking, s.ticker"""
        ):
            frame = self._frame(position["ticker"], frames)
            if frame is None:
                continue
            after = frame[pd.to_datetime(frame.index).date > pd.Timestamp(position["signal_date"]).date()]
            if after.empty:
                continue
            buy_low = float(position["buy_low"])
            buy_high = float(position["buy_high"])
            touches = [
                index for index in range(min(len(after), cfg.ENTRY_WAIT_DAYS))
                if float(after.iloc[index]["Low"]) <= buy_high
                and float(after.iloc[index]["High"]) >= buy_low
            ]
            if touches:
                candidates.append((position, frame, after.index[touches[0]]))
            elif len(after) >= cfg.ENTRY_WAIT_DAYS:
                self._transition(position, "EXPIRED", timestamp, {
                    "exit_date": pd.Timestamp(after.index[cfg.ENTRY_WAIT_DAYS - 1]).date().isoformat(),
                    "exit_reason": "EntryTimeout",
                })
                with self.database.transaction() as connection:
                    self._alert(
                        connection, position["signal_id"], "SIGNAL_EXPIRED", timestamp,
                        f"{position['ticker']}: paper entry window expired",
                    )
                updated += 1

        for position, frame, fill_date in candidates:
            if self._try_open(position, fill_date, timestamp):
                updated += 1
        return updated

    def _try_open(self, position, fill_date, timestamp):
        cfg = load_backtest_config()
        status = self.portfolio_status()
        open_positions = self.database.rows(
            "SELECT * FROM paper_positions WHERE status='OPEN'"
        )
        effective_max = min(
            int(cfg.MAX_OPEN_POSITIONS),
            math.floor(cfg.MAX_PORTFOLIO_RISK_PERCENT / cfg.RISK_PERCENT)
            if cfg.RISK_PERCENT > 0 else int(cfg.MAX_OPEN_POSITIONS),
        )
        reason = None
        if not cfg.ALLOW_OVERLAPPING_TRADES and any(
            row["ticker"] == position["ticker"] for row in open_positions
        ):
            reason = "Overlap"
        elif len(open_positions) >= effective_max:
            reason = "MaxPositions"

        # Per symbol, exactly as backtesting/engine.py does. Without the
        # ticker this charges the conservative unmeasured rate to every
        # name, so paper results would not be comparable to the backtest
        # they exist to validate.
        costs = TradingCosts(symbol=position["ticker"])
        entry = costs.entry_price(float(position["buy_high"]))
        sizing = PositionSizer(
            cfg.INITIAL_CAPITAL, cfg.RISK_PERCENT
        ).calculate(entry, float(position["stop_loss"]))
        shares = int(sizing["Shares"])
        position_value = round(shares * entry, 2)
        risk_amount = round(shares * abs(entry - float(position["stop_loss"])), 2)
        max_heat = cfg.INITIAL_CAPITAL * cfg.MAX_PORTFOLIO_RISK_PERCENT / 100
        if not reason and status["open_risk"] + risk_amount > max_heat:
            reason = "Heat"
        if not reason and (shares <= 0 or position_value > status["cash"]):
            reason = "Capital"
        if reason:
            self._transition(position, "REJECTED", timestamp, {"exit_reason": reason})
            return True

        self._transition(position, "OPEN", timestamp, {
            "entry_date": pd.Timestamp(fill_date).date().isoformat(),
            "entry_price": entry, "shares": shares,
            "position_value": position_value, "risk_amount": risk_amount,
        })
        return True

    def _try_close(self, position, frame, timestamp):
        positions = [
            index for index in range(len(frame))
            if pd.Timestamp(frame.index[index]).date()
            == pd.Timestamp(position["entry_date"]).date()
        ]
        if not positions:
            return False
        data = MarketData(calculate_indicators(frame.copy()))
        context = BacktestContext(
            symbol=position["ticker"], data=data, signal_index=positions[0],
            signal={
                "StopLoss": float(position["stop_loss"]),
                "Target1": float(position["target1"]),
                "Target2": float(position["target2"]),
            },
            entry_price=float(position["entry_price"]),
            entry_date=position["entry_date"], entry_index=positions[0] + 1,
        )
        if context.entry_index >= data.length:
            return False
        costs = TradingCosts(symbol=position["ticker"])
        if not ExitManager(costs).manage(context, allow_timeout=False):
            return False
        profit_per_share = costs.net_profit(
            float(position["entry_price"]), float(context.exit_price)
        )
        realized = round(int(position["shares"]) * profit_per_share, 2)
        holding = (
            pd.Timestamp(context.exit_date).date()
            - pd.Timestamp(position["entry_date"]).date()
        ).days
        self._transition(position, "CLOSED", timestamp, {
            "exit_date": context.exit_date, "exit_price": context.exit_price,
            "exit_reason": context.exit_reason, "realized_profit": realized,
            "holding_days": holding,
        })
        with self.database.transaction() as connection:
            alert_type = (
                "TP_REACHED" if "Target" in str(context.exit_reason)
                else "SL_REACHED" if "Stop" in str(context.exit_reason)
                else "POSITION_CLOSED"
            )
            self._alert(
                connection, position["signal_id"], alert_type, timestamp,
                f"{position['ticker']} closed: {context.exit_reason} ({realized:.2f})",
            )
            self._alert(
                connection, position["signal_id"], "POSITION_CLOSED", timestamp,
                f"{position['ticker']} position closed: {context.exit_reason} ({realized:.2f})",
            )
        return True

    def _transition(self, position, status, timestamp, values):
        allowed = {
            "entry_date", "entry_price", "shares", "position_value",
            "risk_amount", "exit_date", "exit_price", "exit_reason",
            "realized_profit", "holding_days",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        assignments = ["status=?"] + [f"{key}=?" for key in updates]
        parameters = [status, *updates.values(), position["position_id"]]
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE paper_positions SET {', '.join(assignments)} WHERE position_id=?",
                parameters,
            )
            self._paper_event(connection, position["position_id"], status, timestamp, values)

    def portfolio_status(self, frames=None):
        frames = frames or {}
        cfg = load_backtest_config()
        open_positions = self.database.rows(
            "SELECT * FROM paper_positions WHERE status='OPEN'"
        )
        closed = self.database.rows(
            "SELECT * FROM paper_positions WHERE status='CLOSED'"
        )
        realized = sum(float(row["realized_profit"] or 0) for row in closed)
        invested = sum(float(row["position_value"] or 0) for row in open_positions)
        cash = round(cfg.INITIAL_CAPITAL + realized - invested, 2)
        market_value = 0.0
        allocation = {}
        for position in open_positions:
            frame = self._frame(position["ticker"], frames, load_missing=False)
            price = (
                float(frame.iloc[-1]["Close"])
                if frame is not None and not frame.empty
                else float(position["entry_price"])
            )
            value = int(position["shares"]) * price
            market_value += value
            allocation[position.get("sector") or "Unknown"] = (
                allocation.get(position.get("sector") or "Unknown", 0) + value
            )
        equity = round(cash + market_value, 2)
        peak_row = self.database.row("SELECT MAX(equity) AS peak FROM portfolio_snapshots")
        peak = max(float((peak_row or {}).get("peak") or cfg.INITIAL_CAPITAL), equity)
        drawdown = round((peak - equity) / peak * 100, 4) if peak else 0
        open_risk = round(sum(float(row["risk_amount"] or 0) for row in open_positions), 2)
        return {
            "cash": cash, "equity": equity,
            "exposure_pct": round(market_value / equity * 100, 4) if equity else 0,
            "current_drawdown_pct": drawdown, "open_risk": open_risk,
            "open_positions": len(open_positions), "closed_trades": len(closed),
            "sector_allocation": allocation,
        }

    def snapshot_portfolio(self, run_id, frames=None, timestamp=None):
        timestamp = _now(timestamp)
        status = self.portfolio_status(frames)
        snapshot_id = str(uuid.uuid5(UUID_NAMESPACE, f"SNAPSHOT|{run_id}"))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO portfolio_snapshots
                   (snapshot_id,snapshot_date,snapshot_time,run_id,cash,equity,
                    exposure_pct,current_drawdown_pct,open_risk,open_positions,
                    closed_trades,sector_allocation_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_id, timestamp.date().isoformat(), timestamp.time().isoformat(),
                 run_id, status["cash"], status["equity"], status["exposure_pct"],
                 status["current_drawdown_pct"], status["open_risk"],
                 status["open_positions"], status["closed_trades"],
                 json.dumps(status["sector_allocation"], sort_keys=True)),
            )
        return status

    def outcome_summary(self):
        rows = self.database.rows(
            """SELECT horizon_days, COUNT(*) signals,
               AVG(CASE WHEN return_pct>0 THEN 1.0 ELSE 0 END)*100 win_rate,
               AVG(return_pct) average_return, AVG(return_pct) expectancy,
               AVG(mae_pct) average_mae, AVG(mfe_pct) average_mfe
               FROM signal_evaluations GROUP BY horizon_days ORDER BY horizon_days"""
        )
        for row in rows:
            values = self.database.rows(
                "SELECT return_pct FROM signal_evaluations WHERE horizon_days=? ORDER BY return_pct",
                (row["horizon_days"],),
            )
            series = pd.Series([item["return_pct"] for item in values], dtype=float)
            row["median_return"] = float(series.median()) if len(series) else 0
        holding = self.database.row(
            "SELECT AVG(holding_days) value FROM paper_positions WHERE status='CLOSED'"
        )
        for row in rows:
            row["average_holding_days"] = float((holding or {}).get("value") or 0)
        return rows

    def pending_signals(self):
        return self.database.rows(
            """SELECT s.*, MAX(e.horizon_days) last_horizon
               FROM signals s LEFT JOIN signal_evaluations e ON e.signal_id=s.signal_id
               WHERE s.signal_type='BUY'
               AND EXISTS (SELECT 1 FROM paper_positions p WHERE p.signal_id=s.signal_id)
               AND NOT EXISTS (
                 SELECT 1 FROM signal_evaluations x WHERE x.signal_id=s.signal_id
                 AND x.status IN ('HIT_TP','HIT_SL','EXPIRED'))
               GROUP BY s.signal_id ORDER BY s.signal_date DESC, s.ranking"""
        )

    def closed_signals(self):
        return self.database.rows(
            """SELECT s.signal_id,s.signal_date,s.ticker,s.price,s.score,s.confidence,
                      s.ai_probability,e.horizon_days,e.return_pct,e.mfe_pct,e.mae_pct,e.status
               FROM signals s JOIN signal_evaluations e ON e.signal_id=s.signal_id
               WHERE e.status IN ('HIT_TP','HIT_SL','EXPIRED')
               AND e.horizon_days=(SELECT MIN(x.horizon_days) FROM signal_evaluations x
                 WHERE x.signal_id=s.signal_id AND x.status IN ('HIT_TP','HIT_SL','EXPIRED'))
               ORDER BY s.signal_date DESC"""
        )

    def dataframes(self):
        return {
            "pending": pd.DataFrame(self.pending_signals()),
            "closed": pd.DataFrame(self.closed_signals()),
            "positions": pd.DataFrame(self.database.rows(
                "SELECT * FROM paper_positions ORDER BY created_at DESC"
            )),
            "snapshots": pd.DataFrame(self.database.rows(
                "SELECT * FROM portfolio_snapshots ORDER BY snapshot_date,snapshot_time"
            )),
            "alerts": pd.DataFrame(self.database.rows(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 200"
            )),
            "outcomes": pd.DataFrame(self.outcome_summary()),
        }

    def _frame(self, ticker, frames, load_missing=True):
        frame = frames.get(ticker)
        if frame is not None:
            return frame
        if not load_missing:
            return None
        try:
            # Forward evaluation follows the dedicated live-data route.  This
            # changes only the source; chronology and all frozen calculations
            # remain untouched.
            return load_data(ticker, purpose="forward_testing")
        except Exception:
            return None

    @staticmethod
    def _event(connection, signal_id, event_type, timestamp, details):
        event_key = f"{signal_id}|{event_type}"
        connection.execute(
            """INSERT OR IGNORE INTO signal_events
               (event_id,signal_id,event_type,event_time,details_json,event_key)
               VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid5(UUID_NAMESPACE, f"EVENT|{event_key}")), signal_id,
             event_type, timestamp.isoformat(),
             json.dumps(_json_safe(details), sort_keys=True), event_key),
        )

    @staticmethod
    def _paper_event(connection, position_id, event_type, timestamp, details):
        event_key = f"{position_id}|{event_type}"
        connection.execute(
            """INSERT OR IGNORE INTO paper_events
               (event_id,position_id,event_type,event_time,details_json,event_key)
               VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid5(UUID_NAMESPACE, f"PAPER|{event_key}")), position_id,
             event_type, timestamp.isoformat(),
             json.dumps(_json_safe(details), sort_keys=True), event_key),
        )

    @staticmethod
    def _alert(connection, signal_id, alert_type, timestamp, message):
        alert_key = f"{signal_id}|{alert_type}"
        connection.execute(
            """INSERT OR IGNORE INTO alerts
               (alert_id,alert_key,signal_id,alert_type,created_at,message)
               VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid5(UUID_NAMESPACE, f"ALERT|{alert_key}")), alert_key,
             signal_id, alert_type, timestamp.isoformat(), message),
        )
