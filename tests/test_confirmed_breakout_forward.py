"""The forward record has to be unfalsifiable, or it is not evidence.

Everything measured about CONFIRMED_VOLUME_BREAKOUT so far is in-sample. The
forward record is the only thing that will ever be otherwise, and it is worth
exactly as much as its ability to refuse a convenient edit. So these tests are
mostly about what the store will *not* let you do.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from strategy_momentum_breakout.config import BreakoutConfig
from strategy_momentum_breakout.forward import (
    ForwardStore, ForwardTest, config_fingerprint,
)


@pytest.fixture
def store(tmp_path):
    return ForwardStore(tmp_path / "forward.db")


def session(date="2026-08-26", signals=0, config_hash="abc123"):
    return {
        "session_id": f"S|{date}", "session_date": date,
        "recorded_at": f"{date}T17:00:00+02:00", "symbols_scanned": 216,
        "symbols_unreadable": 25, "signal_count": signals,
        "funnel_json": "{}", "config_hash": config_hash, "config_json": "{}",
    }


def signal(ticker="COMI.CA", date="2026-08-26", config_hash="abc123",
           holding_bars=20):
    return {
        "signal_id": f"SIG|{ticker}|{date}", "dedupe_key": f"{ticker}|{date}",
        "session_date": date, "recorded_at": f"{date}T17:00:00+02:00",
        "ticker": ticker, "close": 100.0, "prior_high": 98.0,
        "volume_ratio": 3.1, "close_position": 0.92, "turnover_egp": 5_000_000.0,
        "atr_percent": 2.1, "calm_reference": 2.6, "stop_loss": 88.0,
        "reference_risk_percent": 12.0, "holding_bars": holding_bars,
        "entry_plan": "NEXT_CLOSE", "reasons": "PASS everything",
        "config_hash": config_hash,
    }


# ---------------------------------------------------------------------------
# The record cannot be quietly improved after the fact
# ---------------------------------------------------------------------------

def test_a_recorded_signal_cannot_be_edited(store):
    store.record_session(session(signals=1), [signal()])

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store._connect() as connection:
            connection.execute(
                "UPDATE signals SET stop_loss = 1.0 WHERE ticker = 'COMI.CA'")


def test_a_recorded_signal_cannot_be_deleted(store):
    store.record_session(session(signals=1), [signal()])

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store._connect() as connection:
            connection.execute("DELETE FROM signals")


def test_a_recorded_outcome_cannot_be_edited(store):
    store.record_session(session(signals=1), [signal()])
    store.record_outcome({
        "outcome_id": "O1", "signal_id": "SIG|COMI.CA|2026-08-26",
        "resolved_at": "2026-09-30T17:00:00+02:00", "status": "CLOSED",
        "entry_date": "2026-08-27", "entry_price": 100.0,
        "exit_date": "2026-09-24", "exit_price": 90.0, "bars_held": 20,
        "exit_reason": "HoldingCap", "net_percent": -10.0,
        "benchmark_percent": 1.0, "lift_percent": -11.0, "note": "",
    })

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with store._connect() as connection:
            connection.execute("UPDATE outcomes SET net_percent = 50.0")


def test_recording_the_same_session_twice_changes_nothing(store):
    store.record_session(session(signals=1), [signal()])
    written = store.record_session(session(signals=1), [signal()])

    assert written == 0
    assert len(store.rows("SELECT * FROM signals")) == 1
    assert len(store.rows("SELECT * FROM sessions")) == 1


# ---------------------------------------------------------------------------
# The denominator is part of the evidence
# ---------------------------------------------------------------------------

def test_a_session_with_no_signals_is_still_recorded(store):
    """"The rule fired three times" means nothing without "in how many sessions"."""
    store.record_session(session(signals=0), [])

    sessions = store.rows("SELECT * FROM sessions")
    assert len(sessions) == 1
    assert sessions[0]["signal_count"] == 0
    assert sessions[0]["symbols_scanned"] == 216


def test_the_report_counts_sessions_even_with_nothing_to_show(store):
    store.record_session(session(date="2026-08-24"), [])
    store.record_session(session(date="2026-08-25"), [])
    test = ForwardTest(store=store)

    summary = test.report()

    assert summary["sessions"] == 2
    assert summary["signals"] == 0
    assert summary["closed"] == 0
    assert summary["mean_lift"] is None


# ---------------------------------------------------------------------------
# Two calibrations are never pooled
# ---------------------------------------------------------------------------

def test_the_configuration_is_fingerprinted_with_the_signal(store):
    one, _ = config_fingerprint(BreakoutConfig())
    other, _ = config_fingerprint(BreakoutConfig(minimum_volume_ratio=3.0))

    assert one != other


def test_the_report_flags_a_record_spanning_two_configurations(store):
    store.record_session(session(date="2026-08-24", signals=1, config_hash="aaa"),
                         [signal(date="2026-08-24", config_hash="aaa")])
    store.record_session(session(date="2026-08-25", signals=1, config_hash="bbb"),
                         [signal(date="2026-08-25", config_hash="bbb")])

    summary = ForwardTest(store=store).report()

    assert summary["configs"] == ["aaa", "bbb"]


# ---------------------------------------------------------------------------
# Resolution scores nothing early, and scores it the same way the backtest does
# ---------------------------------------------------------------------------

#: Where the breakout bar sits in the fixture below.
SIGNAL_BAR = 280


def history(tail=12):
    """Raw OHLCV producing exactly one signal under real indicators.

    `resolve` runs `calculate_indicators` on whatever the router returns, so a
    fixture that hard-codes ATR and EMA200 has them overwritten and the rule
    then refuses it. This is a base with real daily range, so the breakout bar
    is not the most volatile thing in its own history and the Calm gate can
    pass -- the condition a hand-built fixture is most likely to miss.
    """
    base = 280
    closes = [10.0] * base + [11.0] + [12.0] * tail
    highs = [10.3] * base + [11.0] + [12.2] * tail
    lows = [9.7] * base + [10.6] + [11.8] * tail
    volumes = [1_000_000.0] * base + [6_000_000.0] + [1_000_000.0] * tail
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes,
         "Volume": volumes},
        index=pd.date_range("2024-01-01", periods=len(closes), freq="B"))


def short_config(**overrides):
    defaults = dict(calm_window=10, breakout_window=5, turnover_window=5,
                    stop_window=5, holding_bars=5, minimum_turnover_egp=0.0,
                    trend_warmup_bars=20)
    return BreakoutConfig(**{**defaults, **overrides})


def test_a_signal_is_not_scored_before_its_window_completes(store, monkeypatch):
    """A partial window is not a result, however tempting it is to look."""
    cfg = short_config(holding_bars=5)
    # Only two bars after the signal, against a five-bar cap.
    data = history(tail=2)
    signal_date = str(data.index[SIGNAL_BAR].date())
    fingerprint, _ = config_fingerprint(cfg)
    store.record_session(
        session(date=signal_date, signals=1, config_hash=fingerprint),
        [signal(date=signal_date, config_hash=fingerprint,
                holding_bars=cfg.holding_bars)])
    test = ForwardTest(store=store, cfg=cfg)
    monkeypatch.setattr(
        "core.research_router.get_current_research_history", lambda ticker: data)

    outcome = test.resolve()

    assert outcome["still_running"] == 1
    assert outcome["resolved"] == 0
    assert store.rows("SELECT * FROM outcomes") == []


def test_a_completed_window_resolves_through_the_backtest_walker(store, monkeypatch):
    """Forward and backtest must produce the same trade from the same bar.

    They share `MomentumBreakoutBacktest.resolve_signal` for exactly this
    reason, and this is the test that keeps them sharing it.
    """
    from strategy_momentum_breakout.backtest import MomentumBreakoutBacktest

    from indicators.technical import calculate_indicators

    cfg = short_config()
    data = history()
    signal_date = str(data.index[SIGNAL_BAR].date())

    fingerprint, _ = config_fingerprint(cfg)
    store.record_session(
        session(date=signal_date, signals=1, config_hash=fingerprint),
        [signal(date=signal_date, config_hash=fingerprint,
                holding_bars=cfg.holding_bars)])
    test = ForwardTest(store=store, cfg=cfg)
    monkeypatch.setattr(
        "core.research_router.get_current_research_history", lambda ticker: data)
    # No pool, so the benchmark is absent rather than invented.
    monkeypatch.setattr(ForwardTest, "_pool", lambda self: pd.DataFrame())

    assert test.resolve()["resolved"] == 1

    recorded = store.rows("SELECT * FROM outcomes")[0]
    expected = MomentumBreakoutBacktest("COMI.CA", cfg).resolve_signal(
        calculate_indicators(data), SIGNAL_BAR)
    assert recorded["status"] == "CLOSED"
    assert recorded["entry_date"] == expected.entry_date
    assert recorded["exit_date"] == expected.exit_date
    assert recorded["exit_price"] == pytest.approx(expected.exit_price)
    assert recorded["exit_reason"] == expected.exit_reason
    assert recorded["benchmark_percent"] is None
    assert recorded["lift_percent"] is None


def test_lift_is_recorded_against_the_pool_over_the_same_window(store, monkeypatch):
    """A forward record of raw returns would measure the market, not the rule."""
    cfg = short_config()
    data = history()
    signal_date = str(data.index[SIGNAL_BAR].date())
    fingerprint, _ = config_fingerprint(cfg)
    store.record_session(
        session(date=signal_date, signals=1, config_hash=fingerprint),
        [signal(date=signal_date, config_hash=fingerprint,
                holding_bars=cfg.holding_bars)])

    # A pool that rises steadily across the whole frame.
    pool = pd.DataFrame(
        {"OTHER.CA": [100.0 + i for i in range(len(data))]}, index=data.index)
    test = ForwardTest(store=store, cfg=cfg)
    monkeypatch.setattr(
        "core.research_router.get_current_research_history", lambda ticker: data)
    monkeypatch.setattr(ForwardTest, "_pool", lambda self: pool)

    test.resolve()

    recorded = store.rows("SELECT * FROM outcomes")[0]
    assert recorded["benchmark_percent"] is not None
    assert recorded["lift_percent"] == pytest.approx(
        recorded["net_percent"] - recorded["benchmark_percent"], abs=1e-6)


def test_resolving_twice_does_not_double_count(store, monkeypatch):
    cfg = short_config()
    data = history()
    signal_date = str(data.index[SIGNAL_BAR].date())
    fingerprint, _ = config_fingerprint(cfg)
    store.record_session(
        session(date=signal_date, signals=1, config_hash=fingerprint),
        [signal(date=signal_date, config_hash=fingerprint,
                holding_bars=cfg.holding_bars)])
    test = ForwardTest(store=store, cfg=cfg)
    monkeypatch.setattr(
        "core.research_router.get_current_research_history", lambda ticker: data)
    monkeypatch.setattr(ForwardTest, "_pool", lambda self: pd.DataFrame())

    assert test.resolve()["resolved"] == 1
    assert test.resolve()["checked"] == 0
    assert len(store.rows("SELECT * FROM outcomes")) == 1


def test_the_forward_store_is_not_the_daily_dashboard_store():
    """They must not share a file: the other one is live evidence.

    `forward_testing/`'s `dedupe_key` is `ticker|date|signal_type` with no
    strategy in it, so a shared table would silently drop whichever of the two
    strategies wrote second.
    """
    from forward_testing.database import DEFAULT_DATABASE as CLASSIC
    from strategy_momentum_breakout.forward import DEFAULT_DATABASE as BREAKOUT

    assert CLASSIC.name != BREAKOUT.name


# ---------------------------------------------------------------------------
# An unfinished session is refused, not recorded
# ---------------------------------------------------------------------------

def test_a_session_the_exchange_has_not_completed_is_refused(store, monkeypatch):
    """A recorded signal is immutable, so a half-formed bar is wrong forever.

    The safety is in the recorder, not in the schedule: a task that fires ten
    minutes early must write nothing rather than write a signal computed from
    a partial close. This is the same discipline the gap recorder uses.
    """
    import datetime as _datetime

    from strategy_momentum_breakout import forward as module

    class Result:
        session_date = "2026-08-27"
        considered = 200
        unreadable = {}
        funnel = {}
        signals = []
        count = 0

    monkeypatch.setattr(
        "strategy_momentum_breakout.scan.scan",
        lambda histories=None, cfg=None: Result())
    # The exchange has only finished the 26th.
    monkeypatch.setattr(
        "core.egx_session.authoritative_completed_session",
        lambda now=None: _datetime.date(2026, 8, 26))

    outcome = ForwardTest(store=store).record()

    assert outcome["session"] is None
    assert outcome["written"] == 0
    assert "refusing to record an unfinished session" in outcome["note"]
    assert store.rows("SELECT * FROM sessions") == []
    assert store.rows("SELECT * FROM signals") == []


def test_a_completed_session_is_recorded(store, monkeypatch):
    import datetime as _datetime

    class Result:
        session_date = "2026-08-26"
        considered = 200
        unreadable = {}
        funnel = {"Calm": 129}
        signals = []
        count = 0

    monkeypatch.setattr(
        "strategy_momentum_breakout.scan.scan",
        lambda histories=None, cfg=None: Result())
    monkeypatch.setattr(
        "core.egx_session.authoritative_completed_session",
        lambda now=None: _datetime.date(2026, 8, 26))

    outcome = ForwardTest(store=store).record()

    assert outcome["session"] == "2026-08-26"
    assert len(store.rows("SELECT * FROM sessions")) == 1


# ---------------------------------------------------------------------------
# The fast path, which is what makes hourly polling affordable
# ---------------------------------------------------------------------------

def test_a_poll_with_nothing_new_skips_the_scan(store, monkeypatch):
    """Most polls find nothing, and must not pay for a 214-symbol scan.

    No provider here publishes the completed daily bar on a stated schedule, so
    the recorder polls instead of firing at a guessed time. That is only viable
    if "nothing new" is cheap to establish.
    """
    store.record_session(session(date="2026-08-26"), [])
    test = ForwardTest(store=store)
    monkeypatch.setattr(ForwardTest, "probe_latest_session",
                        lambda self, **kw: "2026-08-26")

    def refuse(*args, **kwargs):
        raise AssertionError("the full scan ran when the probe said nothing new")

    monkeypatch.setattr("strategy_momentum_breakout.scan.scan", refuse)

    outcome = test.record()

    assert outcome["skipped"] is True
    assert outcome["session"] == "2026-08-26"
    assert outcome["written"] == 0


def test_a_poll_that_finds_a_newer_session_does_the_full_scan(store, monkeypatch):
    store.record_session(session(date="2026-08-26"), [])
    test = ForwardTest(store=store)
    monkeypatch.setattr(ForwardTest, "probe_latest_session",
                        lambda self, **kw: "2026-08-30")

    class Result:
        session_date = "2026-08-30"
        considered = 214
        unreadable = {}
        funnel = {"Calm": 100}
        signals = []
        count = 0

    monkeypatch.setattr("strategy_momentum_breakout.scan.scan",
                        lambda histories=None, cfg=None: Result())
    import datetime as _datetime
    monkeypatch.setattr("core.egx_session.authoritative_completed_session",
                        lambda now=None: _datetime.date(2026, 8, 30))

    outcome = test.record()

    assert not outcome.get("skipped")
    assert outcome["session"] == "2026-08-30"
    assert len(store.rows("SELECT * FROM sessions")) == 2


def test_an_unreadable_probe_falls_back_to_the_full_scan(store, monkeypatch):
    """The probe may be approximate, but only in one direction.

    Saying "nothing new" when there is something new merely delays a poll.
    Saying it on bad information would skip a session permanently, so too few
    answering symbols returns None and the caller scans properly.
    """
    store.record_session(session(date="2026-08-26"), [])
    test = ForwardTest(store=store)
    monkeypatch.setattr(ForwardTest, "probe_latest_session",
                        lambda self, **kw: None)

    scanned = []

    class Result:
        session_date = "2026-08-26"
        considered = 214
        unreadable = {}
        funnel = {}
        signals = []
        count = 0

    def record_call(histories=None, cfg=None):
        scanned.append(True)
        return Result()

    monkeypatch.setattr("strategy_momentum_breakout.scan.scan", record_call)
    import datetime as _datetime
    monkeypatch.setattr("core.egx_session.authoritative_completed_session",
                        lambda now=None: _datetime.date(2026, 8, 30))

    test.record()

    assert scanned, "an unreadable probe must not be treated as 'nothing new'"


def test_the_probe_needs_enough_symbols_to_answer(store, monkeypatch):
    test = ForwardTest(store=store)
    monkeypatch.setattr("core.universe.active_symbols", lambda: ["A.CA", "B.CA"])
    monkeypatch.setattr(
        "core.research_router.get_current_research_history",
        lambda ticker: pd.DataFrame(
            {"Close": [1.0]}, index=pd.to_datetime(["2026-08-30"])))

    # Two symbols answered against a minimum of three.
    assert test.probe_latest_session(sample=8, minimum=3) is None
    assert test.probe_latest_session(sample=8, minimum=2) == "2026-08-30"


def test_the_probe_takes_the_newest_date_it_sees(store, monkeypatch):
    """One lagging symbol must not hold the whole record back."""
    test = ForwardTest(store=store)
    dates = {"A.CA": "2026-08-26", "B.CA": "2026-08-30", "C.CA": "2026-08-26"}
    monkeypatch.setattr("core.universe.active_symbols", lambda: list(dates))
    monkeypatch.setattr(
        "core.research_router.get_current_research_history",
        lambda ticker: pd.DataFrame(
            {"Close": [1.0]}, index=pd.to_datetime([dates[ticker]])))

    assert test.probe_latest_session(sample=8, minimum=3) == "2026-08-30"
