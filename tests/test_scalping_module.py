"""Safety and accounting tests for the isolated paper-only scalping module."""

from copy import deepcopy
from datetime import datetime, timedelta
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from config.settings_manager import settings
from scalping.backtest_engine import ScalpingBacktest
from scalping.config import ScalpingConfig
from scalping.database import ScalpingDatabase
from scalping.data_sources import RubixScalpingDataSource, ScalpingDataSourceError
from scalping.decision import assess_actionability
from scalping.entry_engine import build_entry_fill
from scalping.exit_engine import evaluate_bar, exit_required, session_close_exit
from scalping.models import (
    DailyRiskState, MarketSnapshot, Opportunity, Position, SetupType, TradeStatus,
)
from scalping.paper_portfolio import ScalpingPaperPortfolio
from scalping.risk_engine import can_open, position_size
from scalping.scanner import intraday_evidence_description
from scalping.setup_detector import detect_setups, sanitize_intraday_bars


CAIRO = ZoneInfo("Africa/Cairo")


def enabled_config(**updates):
    values = ScalpingConfig(enabled=True, commission=0.0, slippage=0.0).as_dict()
    values.update(updates)
    return ScalpingConfig.from_mapping(values)


def opportunity(timestamp=None, actionable=True):
    return Opportunity(
        ticker="COMI.CA", setup=SetupType.MOMENTUM_BREAKOUT,
        timestamp=timestamp or datetime(2026, 7, 14, 11, 0, tzinfo=CAIRO),
        signal_price=99.0, ask=100.0, bid=99.9, volume=1_000_000,
        spread_percent=0.1, score=80.0, reasons=("qualified",),
        freshness="FRESH", actionable=actionable,
        blocked_reason=None if actionable else "DATA_STALE",
    )


def position(config=None, ask=100.0, signal=99.0, quantity=10):
    config = config or enabled_config()
    fill = build_entry_fill(signal, ask, quantity, config)
    return Position(
        position_id="P1", signal_id="S1", ticker="COMI.CA",
        setup=SetupType.MOMENTUM_BREAKOUT.value,
        opened_at=datetime(2026, 7, 14, 11, 0, tzinfo=CAIRO), entry=fill,
    )


def test_fixed_two_percent_levels_use_actual_fill_not_signal_price():
    config = enabled_config(slippage=0.001)
    fill = build_entry_fill(90.0, 100.0, 10, config)
    assert fill.actual_entry_fill == 100.1
    assert fill.target_price == 102.11
    assert fill.stop_price == 98.09
    assert fill.target_price != pytest.approx(90.0 * 1.02)
    assert fill.stop_price != pytest.approx(90.0 * 0.98)


def test_tick_size_rounding_uses_configured_price_bands():
    config = enabled_config()
    assert config.round_price(1.2344, "up") == 1.235
    assert config.round_price(1.2346, "down") == 1.234
    assert config.round_price(10.001, "up") == 10.01
    assert config.round_price(10.009, "down") == 10.0


def test_paper_entry_buys_at_ask_and_exit_sells_at_bid_with_costs():
    config = enabled_config(commission=0.003)
    trade = position(config=config, ask=100.0, signal=95.0, quantity=10)
    assert trade.entry.requested_entry_price == 100.0
    result = evaluate_bar(
        trade, high=103.0, low=100.0, bid=102.0,
        timestamp=datetime(2026, 7, 14, 11, 10, tzinfo=CAIRO), config=config,
    )
    assert result.exit_fill == 102.0
    assert result.trading_costs == pytest.approx(6.06)
    assert result.realized_pnl == pytest.approx(13.94)
    assert result.net_return_percent == pytest.approx(1.394)


def test_stale_or_yahoo_data_is_never_actionable():
    config = enabled_config()
    base = dict(
        ticker="COMI.CA", timestamp=datetime(2026, 7, 14, 11, 0, tzinfo=CAIRO),
        last=100, bid=99.9, ask=100, volume=1_000_000,
        quote_age_seconds=2, session_phase="OPEN",
    )
    stale = MarketSnapshot(provider="rubix", freshness="STALE", **base)
    yahoo = MarketSnapshot(provider="yahoo", freshness="FRESH", **base)
    assert assess_actionability(stale, config)[0] is False
    assert "DATA_STALE" in assess_actionability(stale, config)[1]
    assert assess_actionability(yahoo, config)[0] is False
    assert "RUBIX_REQUIRED" in assess_actionability(yahoo, config)[1]
    disconnected = MarketSnapshot(
        provider="rubix", freshness="FRESH", collector_status="DISCONNECTED", **base
    )
    assert "COLLECTOR_DISCONNECTED" in assess_actionability(disconnected, config)[1]


def test_same_bar_target_and_stop_uses_conservative_stop_and_records_ambiguity():
    config = enabled_config()
    trade = position(config)
    result = evaluate_bar(
        trade, high=103, low=97, bid=97.9,
        timestamp=datetime(2026, 7, 14, 11, 2, tzinfo=CAIRO), config=config,
    )
    assert result.status == TradeStatus.STOP_HIT
    assert result.ambiguous_same_bar is True


def test_session_close_exit_and_no_overnight_guard():
    config = enabled_config()
    trade = position(config)
    close_time = datetime(2026, 7, 14, 14, 25, tzinfo=CAIRO)
    assert exit_required(trade, close_time, config) is True
    result = session_close_exit(trade, bid=100.5, timestamp=close_time, config=config)
    assert result.status == TradeStatus.SESSION_CLOSE
    assert exit_required(
        trade, datetime(2026, 7, 15, 10, 0, tzinfo=CAIRO), config
    ) is True


def test_daily_loss_and_consecutive_loss_kill_switches():
    config = enabled_config(max_daily_loss_percent=2.0, max_consecutive_losses=3)
    daily = DailyRiskState("2026-07-14", 100_000, realized_pnl=-2_000)
    assert can_open(daily, "COMI.CA", datetime(2026, 7, 14, 11, tzinfo=CAIRO), config) == (
        False, "DAILY_LOSS_KILL_SWITCH"
    )
    consecutive = DailyRiskState("2026-07-14", 100_000, consecutive_losses=3)
    assert can_open(consecutive, "COMI.CA", datetime(2026, 7, 14, 11, tzinfo=CAIRO), config) == (
        False, "CONSECUTIVE_LOSS_KILL_SWITCH"
    )


def test_position_size_separates_two_percent_price_stop_from_account_risk():
    config = enabled_config(risk_per_trade_percent=0.5, max_exposure_per_symbol_percent=100)
    assert position_size(100_000, 100, config) == 250


def test_duplicate_signal_prevention_and_database_recovery(tmp_path):
    path = tmp_path / "scalping.db"
    database = ScalpingDatabase(path)
    item = opportunity()
    first_id, first = database.insert_signal(item)
    second_id, second = database.insert_signal(item)
    assert first is True and second is False and first_id == second_id
    recovered = ScalpingDatabase(path)
    assert recovered.integrity_check() == {
        "status": "ok", "journal_mode": "WAL", "path": str(path),
    }
    assert len(recovered.rows("SELECT * FROM signals")) == 1


def test_paper_fill_persists_and_is_forced_closed_at_session_end(tmp_path):
    config = enabled_config(database_path=str(tmp_path / "paper.db"))
    database = ScalpingDatabase(config.database_path)
    item = opportunity()
    signal_id, _ = database.insert_signal(item)
    state = DailyRiskState("2026-07-14", config.initial_capital)
    portfolio = ScalpingPaperPortfolio(config, database)
    trade, reason = portfolio.execute(item, signal_id, state, now=item.timestamp)
    assert reason is None and trade is not None
    assert database.rows("SELECT actual_fill FROM fills")[0]["actual_fill"] == 100.0
    result = portfolio.evaluate(
        trade, high=101, low=99, bid=100.5,
        timestamp=datetime(2026, 7, 14, 14, 25, tzinfo=CAIRO), state=state,
    )
    assert result.status == TradeStatus.SESSION_CLOSE
    assert database.open_positions() == []
    assert database.rows("SELECT status FROM exits")[0]["status"] == "SESSION_CLOSE"


def test_daily_ohlcv_is_rejected_by_scalping_backtest():
    index = pd.date_range("2026-01-01", periods=50, freq="D")
    frame = pd.DataFrame({
        "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1_000_000,
    }, index=index)
    result = ScalpingBacktest(enabled_config()).run({"COMI.CA": frame})
    assert result["metrics"]["total_trades"] == 0
    assert "daily/non-intraday data rejected" in result["limitations"][0]


def test_intraday_backtest_executes_only_with_minute_evidence():
    index = pd.date_range(
        "2026-07-14 10:00", periods=45, freq="min", tz="Africa/Cairo"
    )
    close = [100.0] * 21 + [103.0] + [103.2] * 23
    high = [100.2] * 21 + [103.2] + [106.0] + [103.5] * 22
    low = [99.8] * 21 + [102.8] + [102.9] * 23
    volume = [100_000.0] * 21 + [300_000.0] + [200_000.0] * 23
    frame = pd.DataFrame({
        "Open": close, "High": high, "Low": low, "Close": close,
        "Volume": volume,
        "Bid": [value * 0.9995 for value in close],
        "Ask": [value * 1.0005 for value in close],
    }, index=index)
    config = enabled_config(
        minimum_liquidity=50_000, minimum_relative_volume=1.0,
        max_spread_percent=0.5,
    )
    result = ScalpingBacktest(config).run({"COMI.CA": frame})
    assert result["metrics"]["total_trades"] >= 1
    assert all(row["exit_status"] in {
        "TARGET_HIT", "STOP_HIT", "SESSION_CLOSE"
    } for row in result["trades"])
    assert all(
        pd.Timestamp(row["entry_time"]).date() == pd.Timestamp(row["exit_time"]).date()
        for row in result["trades"]
    )


def test_zero_price_heartbeat_bars_are_rejected_before_setup_calculation():
    index = pd.date_range(
        "2026-07-19 09:30", periods=45, freq="min", tz="Africa/Cairo"
    )
    clean = pd.DataFrame({
        "Open": 100.0, "High": 100.5, "Low": 99.5,
        "Close": 100.0, "Volume": 100_000.0,
    }, index=index)
    dirty = clean.copy()
    dirty.iloc[:15, dirty.columns.get_indexer(["Open", "High", "Low", "Close"])] = 0.0
    sanitized = sanitize_intraday_bars(dirty)
    assert len(sanitized) == 30
    assert sanitized.attrs["invalid_bars_removed"] == 15
    assert (sanitized[["Open", "High", "Low", "Close"]] > 0).all().all()

    snapshot = MarketSnapshot(
        ticker="COMI.CA", timestamp=datetime(2026, 7, 19, 11, 0, tzinfo=CAIRO),
        last=100, bid=99.9, ask=100.1, volume=1_000_000,
        provider="rubix", freshness="FRESH", quote_age_seconds=1,
        session_phase="OPEN", collector_status="CONNECTED",
    )
    # Regression proof: a zero denominator can no longer escape as an error.
    assert isinstance(detect_setups(dirty, snapshot, enabled_config()), list)


def test_missing_current_session_is_classified_as_insufficient_evidence_not_crash():
    assert intraday_evidence_description("CURRENT_SESSION_CANDLES_MISSING") == (
        "No valid traded candle exists for the current EGX session."
    )
    assert "zero or invalid" in intraday_evidence_description(
        "INTRADAY_VALID_CANDLES_MISSING"
    )
    assert intraday_evidence_description("unexpected database exception") is None


def test_module_defaults_are_disabled_paper_only_and_existing_settings_unchanged():
    frozen = deepcopy({
        key: settings.data[key] for key in ("strategy", "backtest", "ai", "ai_risk_overlay")
    })
    config = ScalpingConfig()
    assert config.enabled is False
    assert config.mode == "PAPER_ONLY"
    assert config.take_profit_percent == config.stop_loss_percent == 2.0
    assert config.allow_yahoo_actionable is False
    assert config.close_at_session_end is True
    assert frozen == {
        key: settings.data[key] for key in ("strategy", "backtest", "ai", "ai_risk_overlay")
    }


def _rubix_minute_database(path, symbols=("COMI",), periods=45):
    index = pd.date_range("2026-07-14 07:00", periods=periods, freq="min", tz="UTC")
    close = [100.0] * 21 + [103.0] + [103.2] * (periods - 22)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE candles_1m (
                ticker TEXT NOT NULL, minute TEXT NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, updates INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(ticker,minute))"""
        )
        for symbol in symbols:
            for number, timestamp in enumerate(index):
                value = close[number]
                high = 106.0 if number == 22 else value + 0.2
                low = value - 0.2
                volume = 300_000.0 if number == 21 else 100_000.0
                connection.execute(
                    "INSERT INTO candles_1m VALUES (?,?,?,?,?,?,?,1)",
                    (symbol, timestamp.isoformat(), value, high, low, value, volume),
                )
    return index


def test_rubix_scalping_source_lists_and_loads_session_read_only(tmp_path):
    database = tmp_path / "rubix.db"
    _rubix_minute_database(database, symbols=("COMI", "SWDY"))
    source = RubixScalpingDataSource(database)
    status = source.status()
    assert status.database_status == "READ_ONLY_OK"
    assert status.session_count == 1
    assert status.symbol_count == 2
    assert status.minute_bar_count == 90
    assert source.session_dates()[0].isoformat() == "2026-07-14"
    assert source.symbols("2026-07-14") == ("COMI.CA", "SWDY.CA")
    frames = source.load_session("2026-07-14", ("COMI.CA",))
    assert set(frames) == {"COMI.CA"}
    assert list(frames["COMI.CA"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert str(frames["COMI.CA"].index.tz) == "Africa/Cairo"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candles_1m").fetchone()[0] == 90


def test_rubix_database_and_direct_frames_produce_identical_scalping_results(tmp_path):
    database = tmp_path / "rubix.db"
    _rubix_minute_database(database)
    source_frame = RubixScalpingDataSource(database).load_session(
        "2026-07-14", ("COMI.CA",)
    )["COMI.CA"]
    direct_frame = source_frame.copy()
    config = enabled_config(
        minimum_liquidity=50_000, minimum_relative_volume=1.0,
        max_spread_percent=0.5,
    )
    sqlite_result = ScalpingBacktest(config).run({"COMI.CA": source_frame})
    direct_result = ScalpingBacktest(config).run({"COMI.CA": direct_frame})
    assert sqlite_result["metrics"] == direct_result["metrics"]
    assert sqlite_result["trades"] == direct_result["trades"]
    assert sqlite_result["limitations"] == direct_result["limitations"]


def test_rubix_scalping_source_fails_clearly_for_missing_or_wrong_schema(tmp_path):
    with pytest.raises(ScalpingDataSourceError, match="not found"):
        RubixScalpingDataSource(tmp_path / "missing.db").status()
    invalid = tmp_path / "invalid.db"
    with sqlite3.connect(invalid) as connection:
        connection.execute("CREATE TABLE other(value TEXT)")
    with pytest.raises(ScalpingDataSourceError, match="candles_1m"):
        RubixScalpingDataSource(invalid).status()
