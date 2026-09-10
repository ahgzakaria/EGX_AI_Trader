"""The measured tail appended to a provider's history must never restate it.

`append_bridge_bars` closes a real gap: EODHD publishes a completed session a
day late and sometimes two, so a history can stop before the last completed
session and be refused as stale while the measured store already holds those
sessions. The risk it introduces is the opposite one — a second source quietly
rewriting bars the first source already published. These tests pin the boundary.

The tail used to come from the Rubix daily bridge. It comes from
MubasherTrade PRO's own daily record now, because the Rubix feed never sent an
auction price and an EGX session closes on one. The guarantees below did not
change with the source, which is why they are the ones worth pinning.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.local_daily_history import CONTRACT_COLUMNS, append_bridge_bars


def _bar(session_date, *, close, volume=1000.0, confirmed=True):
    """One measured-store row in the shape the appender consumes."""
    return {
        "session_date": pd.Timestamp(session_date),
        "Open": close - 0.5, "High": close + 0.5, "Low": close - 1.0,
        "Close": close, "Adj Close": close,
        "Volume": volume,
        "_official_close": confirmed,
    }


def _tail(bars):
    """A store lookup returning sessions STRICTLY newer than the given date."""
    return lambda base, after_date: [b for b in bars
                                     if b["session_date"] > pd.Timestamp(after_date)]


def _overlapping_tail(bars):
    """Deliberately looser: also returns a bar the frame already has.

    The real lookup cannot do this, so the no-overwrite guard is unreachable
    through the normal path. It is still the property that matters most — a
    second source must never restate a published bar — so it is tested against
    a lookup that breaks the contract, which is exactly the case the guard
    exists for.
    """

    return lambda base, after_date: list(bars)


def _frame(dates, closes):
    index = pd.DatetimeIndex(pd.to_datetime(dates), name="Date")
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes,
         "Close": closes, "Adj Close": closes, "Volume": [100.0] * len(closes)},
        index=index,
    )[CONTRACT_COLUMNS]


def test_appends_only_sessions_newer_than_the_frame():
    frame = _frame(["2026-08-10", "2026-08-11"], [37.0, 37.5])
    tail = _tail([_bar("2026-08-12", close=38.2), _bar("2026-08-13", close=38.9)])

    result, provenance = append_bridge_bars(frame, "MFPC", tail_rows=tail)

    assert provenance["bridge_sessions_appended"] == 2
    assert [d.date().isoformat() for d in result.index] == [
        "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"
    ]
    assert result["Close"].iloc[-1] == pytest.approx(38.9)


def test_never_overwrites_a_session_the_provider_already_published():
    """The owning provider keeps its bar even when the tail disagrees."""

    frame = _frame(["2026-08-11", "2026-08-12"], [37.5, 38.2])
    tail = _overlapping_tail([_bar("2026-08-12", close=99.0)])

    result, provenance = append_bridge_bars(frame, "MFPC", tail_rows=tail)

    assert provenance["bridge_sessions_appended"] == 0
    assert provenance["conflict_count"] == 1
    assert result["Close"].loc["2026-08-12"] == pytest.approx(38.2)
    assert len(result) == 2


def test_an_agreeing_duplicate_is_counted_not_appended():
    """Same session, same OHLC — recorded as a duplicate, not a disagreement."""

    agreeing = _bar("2026-08-12", close=38.2)
    frame = _frame(["2026-08-11"], [37.5])
    # Give the existing bar exactly the tail's OHLC so the two agree.
    frame.loc[pd.Timestamp("2026-08-12")] = {
        "Open": agreeing["Open"], "High": agreeing["High"],
        "Low": agreeing["Low"], "Close": agreeing["Close"],
        "Adj Close": agreeing["Adj Close"], "Volume": agreeing["Volume"],
    }

    result, provenance = append_bridge_bars(
        frame, "MFPC", tail_rows=_overlapping_tail([agreeing])
    )

    assert provenance["duplicate_count"] == 1
    assert provenance["conflict_count"] == 0
    assert len(result) == 2


def test_a_session_with_no_close_is_never_appended(monkeypatch):
    """The store's reader drops it, and nothing downstream invents one.

    A row without a close is not a quiet day -- it is a session the store
    cannot describe, and appending it would put a NaN where the last completed
    session should be.
    """

    import sector_flow.measured_turnover as store
    from core import local_daily_history as history

    stored = pd.DataFrame(
        {"Open": [1.0, 1.1], "High": [1.2, 1.3], "Low": [0.9, 1.0],
         "Close": [float("nan"), 1.25], "Volume": [10.0, 20.0],
         "Turnover": [10.0, 25.0]},
        index=pd.DatetimeIndex(["2026-08-13", "2026-08-16"], name="Date"))
    monkeypatch.setattr(store, "frame_for", lambda ticker, *a, **k: stored)

    rows = history._measured_rows_after("MFPC", "2026-08-12")

    assert [r["session_date"].date().isoformat() for r in rows] == ["2026-08-16"]


def test_the_true_open_survives_and_a_missing_one_is_not_invented(monkeypatch):
    """The store carries a real open only for sessions rebuilt from minute bars.

    Every session sourced from Mubasher's daily record has none, because that
    file's OP column is the previous close. The tail passes through whatever
    the store holds -- a float or a NaN -- and never substitutes one for the
    other.
    """

    import sector_flow.measured_turnover as store
    from core import local_daily_history as history

    stored = pd.DataFrame(
        {"Open": [float("nan"), 139.52], "High": [142.8, 139.58],
         "Low": [140.06, 138.17], "Close": [140.06, 138.17],
         "Volume": [1.0, 2.0], "Turnover": [1.0, 2.0]},
        index=pd.DatetimeIndex(["2026-09-07", "2026-09-10"], name="Date"))
    monkeypatch.setattr(store, "frame_for", lambda ticker, *a, **k: stored)

    rows = history._measured_rows_after("COMI", "2026-09-06")

    assert pd.isna(rows[0]["Open"])
    assert rows[1]["Open"] == pytest.approx(139.52)


def test_no_tail_returns_the_frame_untouched():
    """A missing store must degrade to 'no tail', never to a damaged history."""

    frame = _frame(["2026-08-11", "2026-08-12"], [37.5, 38.2])
    result, provenance = append_bridge_bars(frame, "MFPC", tail_rows=_tail([]))

    assert result is frame
    assert provenance["bridge_sessions_appended"] == 0


def test_an_unreadable_store_is_not_an_error():
    """`_measured_rows_after` returns nothing rather than raising.

    The measured store is an enrichment. A machine that has never run the
    import must still be able to analyse a symbol.
    """

    from core import local_daily_history as history

    frame = _frame(["2026-08-11"], [37.5])
    result, provenance = append_bridge_bars(
        frame, "NOSUCHSYMBOL", tail_rows=history._measured_rows_after)

    assert result is frame
    assert provenance["bridge_sessions_appended"] == 0


def test_empty_frame_is_returned_unchanged():
    empty = _frame([], [])
    result, provenance = append_bridge_bars(
        empty, "MFPC", tail_rows=_tail([_bar("2026-08-13", close=38.9)]))
    assert len(result) == 0
    assert provenance["bridge_sessions_appended"] == 0


@pytest.mark.parametrize("action_date,blocked", [
    ("2006-04-11", False),   # long resolved into everything EODHD publishes
    ("2017-11-09", False),
    ("2025-06-01", False),   # still before the provider's last bar
    ("2026-08-12", True),    # on the boundary: the appended window starts here
    ("2026-08-13", True),    # inside the appended window
    (None, False),           # no unresolved action at all
])
def test_only_an_action_inside_the_append_window_withholds_the_tail(
    action_date, blocked
):
    """An old adjustment cannot reach a bar appended for the current session.

    The first version of this guard skipped the tail whenever a symbol had any
    unresolved action on record. That withheld it from 8 of 209 EODHD symbols
    over actions dated 2006-2025 — an adjustment already propagated backwards
    through the published series, against a tail bar quoted on the current
    basis. The two agree; only an action inside the window can split them.
    """

    from core.research_router import bridge_tail_blocked

    assert bridge_tail_blocked(action_date, "2026-08-12") is blocked


def test_no_provider_last_session_never_blocks():
    from core.research_router import bridge_tail_blocked

    assert bridge_tail_blocked("2026-08-13", None) is False


def test_the_tail_never_runs_ahead_of_the_expected_session():
    """Filling a gap is the job; getting ahead of the guard is a regression.

    The measured store holds a session as soon as the import is run, while the
    daily guard only advances its expected session after a settlement grace.
    Between the two the store legitimately holds a session the guard still
    treats as unpublished — and appending it made the guard reject 186 of 241
    symbols with SKIPPED_FUTURE_DAILY_DATE, symbols that were fine before the
    tail existed.
    """

    frame = _frame(["2026-08-11"], [37.5])
    tail = _tail([_bar("2026-08-12", close=38.2), _bar("2026-08-13", close=38.9)])

    result, provenance = append_bridge_bars(
        frame, "MFPC", tail_rows=tail, not_after="2026-08-12"
    )

    assert provenance["bridge_sessions_appended"] == 1
    assert provenance["bridge_sessions_withheld_ahead"] == 1
    assert result.index[-1].date().isoformat() == "2026-08-12"


def test_no_ceiling_appends_everything_available():
    frame = _frame(["2026-08-11"], [37.5])
    tail = _tail([_bar("2026-08-12", close=38.2), _bar("2026-08-13", close=38.9)])

    _result, provenance = append_bridge_bars(frame, "MFPC", tail_rows=tail)
    assert provenance["bridge_sessions_appended"] == 2


def test_a_ceiling_that_excludes_everything_leaves_the_frame_alone():
    frame = _frame(["2026-08-11"], [37.5])
    tail = _tail([_bar("2026-08-12", close=38.2)])

    result, provenance = append_bridge_bars(
        frame, "MFPC", tail_rows=tail, not_after="2026-08-11"
    )

    assert result is frame
    assert provenance["bridge_sessions_appended"] == 0
    assert provenance["bridge_sessions_withheld_ahead"] == 1


def test_provenance_names_the_appended_sessions():
    """A report must be able to say which bar came from which source."""

    frame = _frame(["2026-08-11"], [37.5])
    tail = _tail([_bar("2026-08-12", close=38.2), _bar("2026-08-13", close=38.9)])

    _result, provenance = append_bridge_bars(frame, "MFPC", tail_rows=tail)

    assert provenance["bridge_dates"] == ("2026-08-12", "2026-08-13")
    assert provenance["bridge_first_session"] == "2026-08-12"
    assert provenance["bridge_latest_session"] == "2026-08-13"
    assert provenance["bridge_provider"] == "MUBASHER_DAILY_TAIL"


def test_a_close_the_auction_never_set_is_appended_and_named():
    """Kept because it is the only record of the day, and flagged because it is
    a last trade rather than the price the 14:25 cross set."""

    frame = _frame(["2026-08-11"], [37.5])
    tail = _tail([_bar("2026-08-12", close=38.2, confirmed=False),
                  _bar("2026-08-13", close=38.9)])

    _result, provenance = append_bridge_bars(frame, "MFPC", tail_rows=tail)

    assert provenance["bridge_sessions_appended"] == 2
    assert provenance["bridge_unconfirmed_close_count"] == 1
    assert provenance["bridge_unconfirmed_close_dates"] == ("2026-08-12",)
