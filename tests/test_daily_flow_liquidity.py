"""Measured turnover in the swing liquidity gate, and what it must not change.

No test reads the production bank. Every bank here is a temporary file the test
wrote, and the gate is pointed at it explicitly.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from core.daily_flow import MINIMUM_SESSIONS, median_trades, median_turnover
from scripts.bank_daily_flow import SCHEMA
from services.swing_breakout import SwingConfig, most_traded

FLOOR = SwingConfig().minimum_daily_turnover_egp


def build_bank(path: Path, rows) -> Path:
    """rows: (symbol, session_date, turnover, trades)."""

    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO daily_flow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(symbol, session, 10.0, 1000.0, turnover, trades, None, None,
          None, None, None, None, None, None, None, 0, None,
          "history", "2026-09-09T00:00:00+00:00")
         for symbol, session, turnover, trades in rows],
    )
    connection.commit()
    connection.close()
    return path


def sessions_for(symbol, turnover, trades=500, count=MINIMUM_SESSIONS):
    return [(symbol, f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}", turnover, trades)
            for day in range(count)]


def history(proxy_turnover, rows=300):
    """A frame whose close x volume gives `proxy_turnover` per session."""

    return pd.DataFrame({"close": [10.0] * rows,
                         "volume": [proxy_turnover / 10.0] * rows})


# --- the reader --------------------------------------------------------------

def test_the_median_is_taken_over_the_recent_window(tmp_path):
    rows = sessions_for("COMI", 1_000_000.0)
    rows += [("COMI", "2026-12-01", 9_000_000.0, 500)]
    bank = build_bank(tmp_path / "flow.db", rows)
    assert median_turnover(path=bank)["COMI"] == pytest.approx(1_000_000.0)


def test_a_symbol_with_too_little_history_is_not_reported(tmp_path):
    bank = build_bank(tmp_path / "flow.db",
                      sessions_for("THIN", 9_000_000.0, count=MINIMUM_SESSIONS - 1))
    assert "THIN" not in median_turnover(path=bank)


def test_a_bank_that_was_never_built_reads_as_empty(tmp_path):
    assert median_turnover(path=tmp_path / "absent.db") == {}


def test_a_corrupt_bank_reads_as_empty_rather_than_raising(tmp_path):
    broken = tmp_path / "flow.db"
    broken.write_bytes(b"not a database")
    assert median_turnover(path=broken) == {}


def test_trades_are_read_the_same_way(tmp_path):
    bank = build_bank(tmp_path / "flow.db", sessions_for("COMI", 9e6, trades=321))
    assert median_trades(path=bank)["COMI"] == pytest.approx(321)


# --- the gate ----------------------------------------------------------------

def test_a_dollar_quoted_name_is_no_longer_dropped(tmp_path, monkeypatch):
    """The bug this change exists for.

    MOIL's close is quoted in dollars while its turnover is reported in pounds,
    so close x volume understates it by the exchange rate: 154,398 against a
    measured 6,555,994. It clears the floor and was being excluded by it.
    """

    bank = build_bank(tmp_path / "flow.db", sessions_for("MOIL", 6_555_994.0))
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: median_turnover(symbols, path=bank))

    histories = {"MOIL": history(154_398.0)}
    assert "MOIL" in most_traded(histories)


def test_a_name_the_proxy_flattered_is_now_excluded(tmp_path, monkeypatch):
    """GRCA: proxy 5,023,288 over the floor, measured 4,972,165 under it."""

    bank = build_bank(tmp_path / "flow.db", sessions_for("GRCA", 4_972_165.0))
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: median_turnover(symbols, path=bank))

    assert "GRCA" not in most_traded({"GRCA": history(5_023_288.0)})


def test_an_unmeasured_symbol_still_uses_the_old_estimate(tmp_path, monkeypatch):
    """Eleven universe names are not in the terminal's store at all."""

    bank = build_bank(tmp_path / "flow.db", sessions_for("COMI", 9_000_000.0))
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: median_turnover(symbols, path=bank))

    kept = most_traded({"COMI": history(9_000_000.0),
                        "UNMEASURED_LIQUID": history(FLOOR * 4),
                        "UNMEASURED_THIN": history(FLOOR / 10)})
    assert "UNMEASURED_LIQUID" in kept
    assert "UNMEASURED_THIN" not in kept


def test_with_no_bank_at_all_the_gate_behaves_exactly_as_before(monkeypatch):
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: {})
    kept = most_traded({"LIQUID": history(FLOOR * 4),
                        "AT_THE_FLOOR": history(FLOOR),
                        "TOO_THIN": history(FLOOR / 10)})
    assert set(kept) == {"LIQUID", "AT_THE_FLOOR"}


def test_the_floor_stays_inclusive_on_measured_turnover(tmp_path, monkeypatch):
    bank = build_bank(tmp_path / "flow.db", sessions_for("EXACT", FLOOR))
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: median_turnover(symbols, path=bank))
    assert "EXACT" in most_traded({"EXACT": history(1.0)})


def test_ranking_uses_the_measured_figure_not_the_estimate(tmp_path, monkeypatch):
    """The research count override slices a ranked list, so the order matters."""

    bank = build_bank(tmp_path / "flow.db",
                      sessions_for("REAL_BIG", 90_000_000.0)
                      + sessions_for("REAL_SMALL", 6_000_000.0))
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: median_turnover(symbols, path=bank))

    # The proxy would rank these the other way round.
    kept = most_traded({"REAL_BIG": history(6_000_000.0),
                        "REAL_SMALL": history(90_000_000.0)}, count=1)
    assert list(kept) == ["REAL_BIG"]


def test_the_gate_never_invents_turnover_for_a_frame_it_cannot_read(monkeypatch):
    monkeypatch.setattr("services.swing_breakout.median_turnover",
                        lambda symbols=None: {})
    assert most_traded({"NO_COLUMNS": pd.DataFrame({"x": [1, 2, 3]})}) == {}


# --- the session a signal fired on -------------------------------------------

def test_the_session_count_is_the_named_day_not_the_median(tmp_path):
    from core.daily_flow import session_trades

    bank = build_bank(tmp_path / "flow.db", [
        ("COMI", "2026-09-07", 9e6, 5000),
        ("COMI", "2026-09-08", 9e6, 12),
    ])
    assert session_trades(path=bank, session_date="2026-09-08")["COMI"] == 12
    assert session_trades(path=bank, session_date="2026-09-07")["COMI"] == 5000


def test_with_no_date_the_latest_banked_session_is_used(tmp_path):
    from core.daily_flow import session_trades

    bank = build_bank(tmp_path / "flow.db", [
        ("COMI", "2026-09-07", 9e6, 5000),
        ("COMI", "2026-09-08", 9e6, 12),
    ])
    assert session_trades(path=bank)["COMI"] == 12


def test_a_symbol_absent_from_that_session_is_absent_not_zero(tmp_path):
    """A session nobody measured and a session nobody traded are different."""

    from core.daily_flow import session_trades, trades_for

    bank = build_bank(tmp_path / "flow.db", [("COMI", "2026-09-08", 9e6, 500)])
    counts = session_trades(path=bank, session_date="2026-09-08")
    assert trades_for(counts, "ABUK") is None
    assert trades_for(counts, "COMI") == 500


def test_a_session_count_survives_a_missing_bank(tmp_path):
    from core.daily_flow import session_trades

    assert session_trades(path=tmp_path / "absent.db") == {}


def test_asking_for_some_symbols_returns_only_those(tmp_path):
    from core.daily_flow import session_trades

    bank = build_bank(tmp_path / "flow.db", [
        ("COMI", "2026-09-08", 9e6, 500), ("ABUK", "2026-09-08", 9e6, 400)])
    assert set(session_trades(["COMI"], "2026-09-08", path=bank)) == {"COMI"}


# --- the pages ---------------------------------------------------------------

def test_both_signal_pages_show_the_session_trade_count():
    for name, key in (("dashboard/swing_signals.py", "c.symbol"),
                      ("dashboard/confirmed_breakout.py", "signal.symbol")):
        source = Path(name).read_text(encoding="utf-8")
        assert "from core.daily_flow import" in source
        assert "session_trades" in source and "trades_for" in source
        assert f'"Trades": trades_for(trades, {key})' in source
        assert '"Trades": st.column_config.NumberColumn' in source


def test_the_count_is_read_for_the_scanned_session_not_today():
    for name in ("dashboard/swing_signals.py", "dashboard/confirmed_breakout.py"):
        source = Path(name).read_text(encoding="utf-8")
        assert "result.session_date" in source, (
            f"{name} must ask for the session it scanned")


def test_neither_page_filters_on_the_trade_count():
    """Shown, never applied: these pages are read-only research views."""

    for name in ("dashboard/swing_signals.py", "dashboard/confirmed_breakout.py"):
        source = Path(name).read_text(encoding="utf-8")
        for forbidden in ("trades <", "trades >", "if trades_for"):
            assert forbidden not in source, f"{name} appears to filter on trades"


# --- the exchange's own buy/sell split ---------------------------------------

def build_bank_with_share(path: Path, rows) -> Path:
    """rows: (symbol, session_date, buy_share | None)."""

    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO daily_flow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(symbol, session, 10.0, 1000.0, 9e6, 500, None, None,
          None, None, None, None, None, None, share,
          1 if share is not None else 0, None,
          "history" if share is not None else "intraday",
          "2026-09-09T00:00:00+00:00")
         for symbol, session, share in rows],
    )
    connection.commit()
    connection.close()
    return path


def test_the_buy_share_is_read_for_the_named_session(tmp_path):
    from core.daily_flow import session_buy_share

    bank = build_bank_with_share(tmp_path / "flow.db", [
        ("COMI", "2026-09-07", 0.62), ("COMI", "2026-09-06", 0.41)])
    assert session_buy_share(path=bank, session_date="2026-09-07")["COMI"] == \
        pytest.approx(0.62)


def test_a_session_with_no_split_is_absent_not_balanced(tmp_path):
    """An unknown flow is not an even one. The minute-sourced tail has no side."""

    from core.daily_flow import buy_share_for, session_buy_share

    bank = build_bank_with_share(tmp_path / "flow.db", [
        ("COMI", "2026-09-09", None), ("ABUK", "2026-09-09", 0.55)])
    shares = session_buy_share(path=bank, session_date="2026-09-09")
    assert buy_share_for(shares, "COMI") is None
    assert buy_share_for(shares, "ABUK") == pytest.approx(0.55)


def test_with_no_date_the_latest_MEASURED_session_is_used(tmp_path):
    """Not simply the latest session: the newest rows often carry no split."""

    from core.daily_flow import session_buy_share

    bank = build_bank_with_share(tmp_path / "flow.db", [
        ("COMI", "2026-09-07", 0.62),
        ("COMI", "2026-09-08", None),
        ("COMI", "2026-09-09", None),
    ])
    assert session_buy_share(path=bank)["COMI"] == pytest.approx(0.62)


def test_the_buy_share_survives_a_missing_bank(tmp_path):
    from core.daily_flow import session_buy_share

    assert session_buy_share(path=tmp_path / "absent.db") == {}


def test_both_pages_show_the_measured_buy_share():
    for name, key in (("dashboard/swing_signals.py", "c.symbol"),
                      ("dashboard/confirmed_breakout.py", "signal.symbol")):
        source = Path(name).read_text(encoding="utf-8")
        assert "session_buy_share" in source
        assert f'"Buy share": buy_share_for(shares, {key})' in source
        assert '"Buy share": st.column_config.NumberColumn' in source


def test_neither_page_filters_on_the_buy_share():
    for name in ("dashboard/swing_signals.py", "dashboard/confirmed_breakout.py"):
        source = Path(name).read_text(encoding="utf-8")
        for forbidden in ("buy_share >", "buy_share <", "if buy_share_for"):
            assert forbidden not in source, f"{name} appears to filter on flow"
