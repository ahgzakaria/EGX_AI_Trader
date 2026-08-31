"""Storage rules for the one database this project cannot rebuild.

Everything here is about refusing a write that would make the recorded history
untrue, because the cost of a silent bad row is a wrong average price acted on
with real money.
"""

from __future__ import annotations

import sqlite3

import pytest

from holdings.book import FeeModel
from holdings.store import HoldingsStore, StoreError


FREE = FeeModel(rates_loaded=True)


@pytest.fixture
def store(tmp_path):
    return HoldingsStore(tmp_path / "portfolio.db")


def test_a_recorded_buy_comes_back_as_a_position(store):
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)
    book = store.book(FREE)

    assert book.positions["ABUK"].quantity == 100
    assert book.positions["ABUK"].average_price == pytest.approx(10.0)


def test_symbols_are_stored_canonically_however_they_were_typed(store):
    """``ABUK.CA``, ``ABUK.EGX`` and ``abuk`` are one holding, not three."""

    store.record_trade("abuk", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)
    store.record_trade("ABUK.CA", "2026-08-10", "BUY", 100, 12.0, fee_model=FREE)
    book = store.book(FREE)

    assert list(book.positions) == ["ABUK"]
    assert book.positions["ABUK"].quantity == 200


def test_an_oversized_sale_is_refused_before_it_is_stored(store):
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)

    with pytest.raises(StoreError):
        store.record_trade("ABUK", "2026-08-24", "SELL", 150, 11.0, fee_model=FREE)

    assert len(store.trades()) == 1


def test_a_backdated_sale_is_judged_at_its_own_date(store):
    """Recording order is not history: a sale dated before the buy is impossible
    even though it was typed afterwards."""

    store.record_trade("ABUK", "2026-08-17", "BUY", 100, 10.0, fee_model=FREE)

    with pytest.raises(StoreError):
        store.record_trade("ABUK", "2026-08-03", "SELL", 50, 11.0, fee_model=FREE)


def test_a_malformed_date_is_refused_with_the_expected_shape(store):
    with pytest.raises(StoreError) as error:
        store.record_trade("ABUK", "03/08/2026", "BUY", 100, 10.0, fee_model=FREE)

    assert "YYYY-MM-DD" in str(error.value)


def test_zero_and_negative_quantities_are_refused(store):
    for quantity in (0, -10):
        with pytest.raises(StoreError):
            store.record_trade("ABUK", "2026-08-03", "BUY", quantity, 10.0,
                               fee_model=FREE)


def test_deleting_a_buy_that_a_later_sale_depends_on_is_refused(store):
    """A correction must not be able to quietly invalidate the rest of the book."""

    first = store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)
    store.record_trade("ABUK", "2026-08-24", "SELL", 100, 11.0, fee_model=FREE)

    with pytest.raises(StoreError):
        store.delete_trade(first, fee_model=FREE)

    assert len(store.trades()) == 2


def test_deleting_a_standalone_mistake_works(store):
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)
    mistake = store.record_trade("HRHO", "2026-08-04", "BUY", 50, 20.0, fee_model=FREE)

    assert store.delete_trade(mistake, fee_model=FREE)
    assert [row["symbol"] for row in store.trades()] == ["ABUK"]


def test_a_bonus_issue_is_recorded_as_a_factor_and_reaches_the_average(store):
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)
    store.record_corporate_action("ABUK", "2026-08-20", "STOCK_DIVIDEND",
                                  factor=1.1, fee_model=FREE)

    position = store.book(FREE).positions["ABUK"]
    assert position.quantity == pytest.approx(110.0)
    assert position.average_price == pytest.approx(9.0909, abs=1e-4)


def test_a_cash_dividend_needs_an_amount_and_a_split_needs_a_factor(store):
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)

    with pytest.raises(StoreError):
        store.record_corporate_action("ABUK", "2026-08-20", "CASH_DIVIDEND",
                                      factor=1.1, fee_model=FREE)
    with pytest.raises(StoreError):
        store.record_corporate_action("ABUK", "2026-08-20", "SPLIT",
                                      amount_per_share=0.5, fee_model=FREE)


def test_cash_movements_are_recorded_and_reduce_on_withdrawal(store):
    store.record_cash("2026-08-01", "DEPOSIT", 100_000.0)
    store.record_cash("2026-08-25", "WITHDRAW", 20_000.0)

    assert store.book(FREE).cash_egp == pytest.approx(80_000.0)


# --------------------------------------------------------------------------- #
# Plans and the recommendation log
# --------------------------------------------------------------------------- #

def test_a_new_plan_version_supersedes_the_previous_one_without_erasing_it(store):
    """"Why did it want 12.00 last week?" has to stay answerable."""

    store.save_plan("ABUK", stop=9.0, target_partial=12.0, target_final=13.0,
                    source="TEST", reason="initial")
    store.save_plan("ABUK", stop=10.0, target_partial=12.0, target_final=13.0,
                    source="TEST", reason="stop raised to breakeven")

    active = store.active_plan("ABUK")
    history = store.plan_history("ABUK")

    assert active["version"] == 2
    assert active["stop"] == pytest.approx(10.0)
    assert len(history) == 2
    assert history[-1]["stop"] == pytest.approx(9.0)
    assert history[-1]["superseded_at"]


def test_recommendations_are_appended_and_read_back_newest_first(store):
    store.log_recommendation("ABUK", action="HOLD", rule="NONE",
                             generated_at="2026-08-30T10:00:00+02:00")
    store.log_recommendation("ABUK", action="TRIM", rule="TARGET1_REACHED",
                             generated_at="2026-08-31T10:00:00+02:00")

    rows = store.recommendations("ABUK")

    assert [row["action"] for row in rows] == ["TRIM", "HOLD"]
    assert store.last_recommendation("ABUK")["rule"] == "TARGET1_REACHED"


def test_a_database_written_before_invoice_import_still_opens(tmp_path):
    """The upgrade path, with real rows in it.

    ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists,
    so the columns invoice import needs must be added by migration -- and the
    unique index over one of them must be created after that migration, not
    before it. Getting the order wrong made every page load raise
    ``no such column: source_reference``.
    """

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "symbol TEXT NOT NULL, trade_date TEXT NOT NULL, side TEXT NOT NULL, "
        "quantity REAL NOT NULL, price REAL NOT NULL, fees_egp REAL, "
        "note TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL)"
    )
    legacy.execute(
        "CREATE TABLE cash_movements (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "movement_date TEXT NOT NULL, kind TEXT NOT NULL, amount_egp REAL NOT NULL, "
        "note TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO trades (symbol, trade_date, side, quantity, price, fees_egp, "
        "note, recorded_at) VALUES ('ABUK','2026-08-03','BUY',100,10.0,5.0,'','x')"
    )
    legacy.commit()
    legacy.close()

    store = HoldingsStore(path)

    assert store.book(FREE).positions["ABUK"].cost_basis_egp == pytest.approx(1005.0)
    store.record_trade("ABUK", "2026-08-04", "BUY", 50, 11.0, fees_egp=2.0,
                       fee_model=FREE, source_reference="REF-1")
    assert store.has_reference("REF-1")


def test_an_invoice_already_recorded_is_refused_by_the_database(tmp_path):
    """Not by a check a later caller could forget: by a unique index."""

    store = HoldingsStore(tmp_path / "portfolio.db")
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE,
                       source_reference="N0001|N0002")

    with pytest.raises(StoreError):
        store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE,
                           source_reference="N0001|N0002")

    assert len(store.trades()) == 1


def test_rows_entered_by_hand_share_no_reference_and_never_collide(tmp_path):
    """The unique index is partial: it must not make manual entry impossible."""

    store = HoldingsStore(tmp_path / "portfolio.db")
    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)
    store.record_trade("ABUK", "2026-08-04", "BUY", 100, 11.0, fee_model=FREE)

    assert len(store.trades()) == 2


def test_the_database_is_created_on_first_use_without_ceremony(tmp_path):
    path = tmp_path / "nested" / "portfolio.db"
    store = HoldingsStore(path)

    store.record_trade("ABUK", "2026-08-03", "BUY", 100, 10.0, fee_model=FREE)

    assert path.exists()
    assert store.trades()[0]["recorded_at"]
