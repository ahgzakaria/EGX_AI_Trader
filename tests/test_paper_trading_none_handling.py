from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import core.paper_trading as paper
import backtesting.managers.exit_manager as exit_manager_module
from providers.base_provider import ProviderDataError


class FakeMarketData:
    def __init__(self, dates, *, low=None, high=None, close=None):
        self.index = pd.DatetimeIndex(dates)
        self.length = len(self.index)
        self.low = np.asarray(low or [90.0] * self.length, dtype=float)
        self.high = np.asarray(high or [105.0] * self.length, dtype=float)
        self.close = np.asarray(close or [100.0] * self.length, dtype=float)
        self.ema20 = self.close.copy()
        self.atr = np.ones(self.length, dtype=float)


class StableCosts:
    """Fixed costs, so these tests measure the tracker and not the spread.

    Accepts and ignores the constructor arguments the real TradingCosts takes.
    Since 2026-08-26 that includes `symbol`, because the paper path prices per
    symbol exactly as the backtest does; a double that rejected it would fail
    for a reason that has nothing to do with what these tests are about.
    """

    def __init__(self, *_args, **_kwargs):
        pass

    def entry_price(self, value):
        return round(float(value) * 1.0005, 4)

    def exit_price(self, value):
        return round(float(value) * 0.9995, 4)


class NoExitManager:
    def __init__(self, costs):
        self.costs = costs

    def manage(self, context, allow_timeout=True):
        return False


class InvalidExitManager:
    def __init__(self, costs):
        self.costs = costs

    def manage(self, context, allow_timeout=True):
        context.exit_price = None
        context.exit_date = None
        context.exit_reason = "InvalidFixture"
        return True


@pytest.fixture(autouse=True)
def isolated_tracker(monkeypatch, tmp_path):
    path = tmp_path / "paper_trades.csv"
    monkeypatch.setattr(paper, "PAPER_TRADES_FILE", str(path))
    monkeypatch.setattr(
        paper,
        "load_backtest_config",
        lambda: SimpleNamespace(
            ENTRY_WAIT_DAYS=2,
            COMMISSION=0.003,
            SLIPPAGE=0.0005,
        ),
    )
    monkeypatch.setattr(paper, "calculate_indicators", lambda value: value)
    monkeypatch.setattr(paper, "MarketData", lambda value: value)
    monkeypatch.setattr(paper, "TradingCosts", StableCosts)
    monkeypatch.setattr(paper, "ExitManager", NoExitManager)
    paper._WARNED_CONTRACT_KEYS.clear()
    return path


def _active_row(**overrides):
    row = {
        "Symbol": "TEST.CA",
        "SignalDate": "2026-07-01",
        "BuyLow": 99.0,
        "EntryPrice": 101.0,
        "EntryDate": "2026-07-01",
        "StopLoss": 95.0,
        "Target1": 110.0,
        "Target2": 115.0,
        "Score": 80,
        "Confidence": 75,
        "RR": 1.8,
        "AIProbability": 0.7,
        "Status": "OPEN",
        "ExitDate": "",
        "ExitPrice": "",
        "ExitReason": "",
        "HoldingDays": "",
    }
    row.update(overrides)
    return row


def _write(path, rows, columns=None):
    frame = pd.DataFrame(rows)
    if columns is not None:
        frame = frame.loc[:, columns]
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _history():
    return FakeMarketData(
        ["2026-07-01", "2026-07-02", "2026-07-03"],
        low=[99.0, 98.0, 97.0],
        high=[102.0, 104.0, 106.0],
    )


def test_legacy_open_missing_buylow_and_entrydate_is_tracked_without_warning(
    monkeypatch, isolated_tracker, caplog
):
    # Match the production legacy schema: BuyLow did not exist in the file.
    row = _active_row(EntryDate="")
    columns = [name for name in paper.COLUMNS if name != "BuyLow"]
    _write(isolated_tracker, [row], columns=columns)
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: _history())

    tracker = paper.PaperTradingTracker()
    before = isolated_tracker.read_bytes()
    assert tracker.update_open_trades() == 0

    assert isolated_tracker.read_bytes() == before
    states = {(item.state, item.field) for item in tracker.last_outcomes}
    assert (paper.TrackerState.LEGACY_INCOMPLETE, "BuyLow") in states
    assert (paper.TrackerState.LEGACY_INCOMPLETE, "EntryDate") in states
    assert not [record for record in caplog.records if record.levelno >= 30]


def test_pending_entry_requires_buylow_without_fallback_or_partial_write(
    monkeypatch, isolated_tracker, caplog
):
    _write(
        isolated_tracker,
        [_active_row(Status="PENDING_ENTRY", BuyLow=None, EntryDate="")],
    )
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: _history())
    tracker = paper.PaperTradingTracker()
    before = isolated_tracker.read_bytes()

    with caplog.at_level("WARNING", logger="core.paper_trading"):
        assert tracker.update_open_trades() == 0
        assert tracker.update_open_trades() == 0

    assert isolated_tracker.read_bytes() == before
    warnings = [
        record for record in caplog.records
        if "field=BuyLow" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert tracker.last_outcomes[-1].state == (
        paper.TrackerState.SKIPPED_MISSING_REQUIRED_FIELD
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("BuyLow", None),
        ("BuyHigh", float("nan")),
        ("StopLoss", float("inf")),
        ("Target1", 0),
        ("Target2", "not-a-number"),
    ],
)
def test_record_signal_missing_required_price_is_skipped(
    field, value, isolated_tracker
):
    frame = pd.DataFrame(
        {"Close": [100.0]},
        index=pd.DatetimeIndex(["2026-07-01"]),
    )
    stock = {
        "Signal": "BUY",
        "Ticker": "TEST.CA",
        "Data": frame,
        "BuyLow": 99.0,
        "BuyHigh": 101.0,
        "StopLoss": 95.0,
        "Target1": 110.0,
        "Target2": 115.0,
        "Score": 80,
        "Confidence": 75,
        "RR": 1.8,
        "AIProbability": 0.7,
    }
    stock[field] = value

    tracker = paper.PaperTradingTracker()
    assert tracker.record_signals([stock]) == 0
    assert tracker.load().empty
    assert tracker.last_outcomes[-1].state == (
        paper.TrackerState.SKIPPED_MISSING_REQUIRED_FIELD
    )


def test_unavailable_provider_is_typed_and_non_mutating(
    monkeypatch, isolated_tracker
):
    _write(isolated_tracker, [_active_row()])

    def unavailable(*args, **kwargs):
        raise ProviderDataError("provider stale")

    monkeypatch.setattr(paper, "load_history", unavailable)
    tracker = paper.PaperTradingTracker()
    before = isolated_tracker.read_bytes()

    assert tracker.update_open_trades() == 0
    assert isolated_tracker.read_bytes() == before
    assert tracker.last_outcomes[-1].state == paper.TrackerState.PRICE_UNAVAILABLE


def test_unexpected_market_data_bug_is_not_swallowed(
    monkeypatch, isolated_tracker
):
    _write(isolated_tracker, [_active_row()])
    monkeypatch.setattr(
        paper,
        "load_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("unexpected implementation failure")
        ),
    )
    tracker = paper.PaperTradingTracker()

    with pytest.raises(RuntimeError, match="unexpected implementation failure"):
        tracker.update_open_trades()


def test_no_future_completed_candle_is_evaluation_not_due(
    monkeypatch, isolated_tracker
):
    _write(isolated_tracker, [_active_row()])
    monkeypatch.setattr(
        paper,
        "load_history",
        lambda *args, **kwargs: FakeMarketData(["2026-07-01"]),
    )
    tracker = paper.PaperTradingTracker()

    assert tracker.update_open_trades() == 0
    assert tracker.last_outcomes[-1].state == (
        paper.TrackerState.EVALUATION_NOT_DUE
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("EntryPrice", None),
        ("StopLoss", None),
        ("Target1", float("nan")),
        ("Target2", float("inf")),
    ],
)
def test_active_record_missing_required_price_is_invalid_and_unchanged(
    field, value, monkeypatch, isolated_tracker
):
    _write(isolated_tracker, [_active_row(**{field: value})])
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: _history())
    tracker = paper.PaperTradingTracker()
    before = isolated_tracker.read_bytes()

    assert tracker.update_open_trades() == 0
    assert isolated_tracker.read_bytes() == before
    assert tracker.last_outcomes[-1].state == paper.TrackerState.INVALID_RECORD
    assert field in tracker.last_outcomes[-1].field


@pytest.mark.parametrize(
    "field,value",
    [
        ("COMMISSION", None),
        ("COMMISSION", float("nan")),
        ("COMMISSION", float("inf")),
        ("COMMISSION", -0.1),
        ("SLIPPAGE", None),
    ],
)
def test_invalid_cost_input_is_not_zero_filled(
    field, value, monkeypatch, isolated_tracker
):
    _write(isolated_tracker, [_active_row()])
    values = {"COMMISSION": 0.003, "SLIPPAGE": 0.0005}
    values[field] = value
    monkeypatch.setattr(
        paper,
        "load_backtest_config",
        lambda: SimpleNamespace(
            ENTRY_WAIT_DAYS=2,
            COMMISSION=values["COMMISSION"],
            SLIPPAGE=values["SLIPPAGE"],
        ),
    )
    tracker = paper.PaperTradingTracker()
    before = isolated_tracker.read_bytes()

    assert tracker.update_open_trades() == 0
    assert isolated_tracker.read_bytes() == before
    assert tracker.last_outcomes[-1].field == field
    assert tracker.last_outcomes[-1].state == paper.TrackerState.INVALID_RECORD


def test_zero_costs_are_valid_values(monkeypatch, isolated_tracker):
    _write(isolated_tracker, [_active_row()])
    monkeypatch.setattr(
        paper,
        "load_backtest_config",
        lambda: SimpleNamespace(
            ENTRY_WAIT_DAYS=2,
            COMMISSION=0,
            SLIPPAGE=0,
        ),
    )
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: _history())
    tracker = paper.PaperTradingTracker()

    assert tracker.update_open_trades() == 0
    assert not any(item.symbol == "CONFIG" for item in tracker.last_outcomes)


def test_invalid_exit_contract_never_partially_closes_row(
    monkeypatch, isolated_tracker
):
    _write(isolated_tracker, [_active_row()])
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: _history())
    monkeypatch.setattr(paper, "ExitManager", InvalidExitManager)
    tracker = paper.PaperTradingTracker()
    before = isolated_tracker.read_bytes()

    assert tracker.update_open_trades() == 0
    assert isolated_tracker.read_bytes() == before
    assert tracker.last_outcomes[-1].state == paper.TrackerState.INVALID_RECORD
    assert "ExitPrice" in tracker.last_outcomes[-1].field


def test_missing_quantity_does_not_change_tracker_contract(
    monkeypatch, isolated_tracker
):
    row = _active_row()
    row["Quantity"] = None
    _write(isolated_tracker, [row])
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: _history())
    tracker = paper.PaperTradingTracker()

    assert tracker.update_open_trades() == 0
    stored = pd.read_csv(isolated_tracker, encoding="utf-8-sig")
    assert "Quantity" in stored.columns
    assert stored.loc[0, "Status"] == "OPEN"


def test_non_buy_or_blocked_signal_is_not_recorded(isolated_tracker):
    tracker = paper.PaperTradingTracker()
    assert tracker.record_signals(
        [
            {"Signal": "WATCH", "Ticker": "WATCH.CA"},
            {"Signal": "AVOID", "Ticker": "AVOID.CA"},
        ]
    ) == 0
    assert tracker.load().empty


def test_duplicate_valid_signal_is_recorded_once(isolated_tracker):
    frame = pd.DataFrame(
        {"Close": [100.0]},
        index=pd.DatetimeIndex(["2026-07-01"]),
    )
    stock = {
        "Signal": "BUY",
        "Ticker": "TEST.CA",
        "Data": frame,
        "BuyLow": "99",
        "BuyHigh": "101",
        "StopLoss": "95",
        "Target1": "110",
        "Target2": "115",
        "Score": 80,
        "Confidence": 75,
        "RR": 1.8,
        "AIProbability": 0.7,
    }
    tracker = paper.PaperTradingTracker()

    assert tracker.record_signals([stock]) == 1
    assert tracker.record_signals([stock]) == 0
    assert len(tracker.load()) == 1


def test_complete_pending_to_closed_lifecycle_matches_existing_exit_engine(
    monkeypatch, isolated_tracker
):
    _write(
        isolated_tracker,
        [_active_row(Status="PENDING_ENTRY", EntryDate="")],
    )
    history = FakeMarketData(
        ["2026-07-01", "2026-07-02", "2026-07-03"],
        low=[100.0, 99.5, 100.0],
        high=[101.0, 102.0, 111.0],
        close=[100.0, 101.0, 110.0],
    )
    monkeypatch.setattr(paper, "load_history", lambda *args, **kwargs: history)
    monkeypatch.setattr(paper, "ExitManager", exit_manager_module.ExitManager)
    monkeypatch.setattr(
        exit_manager_module,
        "load_backtest_config",
        lambda: SimpleNamespace(
            EXIT_MODE="TARGET1",
            MAX_HOLDING_DAYS=10,
            MOVE_TO_BREAKEVEN=False,
            TRAILING_ENABLED=False,
            TRAILING_MODE="ATR",
            TRAILING_ATR=2.0,
            PARTIAL_EXIT=False,
            PARTIAL_PERCENT=0.5,
        ),
    )
    tracker = paper.PaperTradingTracker()

    assert tracker.update_open_trades() == 1
    stored = pd.read_csv(isolated_tracker, encoding="utf-8-sig")
    assert stored.loc[0, "Status"] == "CLOSED"
    assert stored.loc[0, "EntryDate"] == "2026-07-02"
    assert stored.loc[0, "EntryPrice"] == pytest.approx(101.0505)
    assert stored.loc[0, "ExitDate"] == "2026-07-03"
    assert stored.loc[0, "ExitPrice"] == pytest.approx(109.945)
    assert stored.loc[0, "ExitReason"] == "Target1"
    assert stored.loc[0, "HoldingDays"] == 1
    assert stored.loc[0, "StopLoss"] == pytest.approx(95.0)
    assert stored.loc[0, "Target1"] == pytest.approx(110.0)
    assert stored.loc[0, "Target2"] == pytest.approx(115.0)


def test_date_only_contract_does_not_invent_timezone(isolated_tracker):
    _write(isolated_tracker, [_active_row(SignalDate="not-a-date")])
    tracker = paper.PaperTradingTracker()

    assert tracker.update_open_trades() == 0
    outcome = tracker.last_outcomes[-1]
    assert outcome.state == paper.TrackerState.INVALID_RECORD
    assert outcome.field == "SignalDate"
    assert outcome.signal_date == "not-a-date"
