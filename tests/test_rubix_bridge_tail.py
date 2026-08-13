"""The Rubix tail appended to a provider's history must never restate it.

`append_bridge_bars` closes a real gap: EODHD publishes a completed session a
day late and sometimes two, so a history can stop before the last completed
session and be refused as stale while Rubix already holds those sessions. The
risk it introduces is the opposite one — a second source quietly rewriting bars
the first source already published. These tests pin the boundary.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.local_rubix_history import CONTRACT_COLUMNS, append_bridge_bars


class StubCache:
    """A normalized daily cache holding exactly the bars a test declares.

    Mirrors `NormalizedDailyCache.final_bars_after`, which returns sessions
    STRICTLY newer than the given date.
    """

    def __init__(self, bars):
        self._bars = bars

    def final_bars_after(self, after_date, source_type="RUBIX_DERIVED"):
        return [b for b in self._bars if b["session_date"] > str(after_date)[:10]]


class OverlappingStubCache(StubCache):
    """Deliberately looser: also returns a bar the frame already has.

    Today's cache cannot do this, so the no-overwrite guard is unreachable
    through the normal path. It is still the property that matters most — a
    second source must never restate a published bar — so it is tested against
    a cache that breaks the contract, which is exactly the case the guard
    exists for.
    """

    def final_bars_after(self, after_date, source_type="RUBIX_DERIVED"):
        return list(self._bars)


def _bar(session_date, *, close, symbol="MFPC", status="FINAL",
         continuous="FINAL_CONTINUOUS", volume=1000.0):
    return {
        "canonical_symbol": f"{symbol}.CA",
        "session_date": session_date,
        "open": close - 0.5, "high": close + 0.5, "low": close - 1.0,
        "official_close": close, "continuous_close": close,
        "volume": volume,
        "finalization_status": status,
        "continuous_bar_status": continuous,
    }


def _frame(dates, closes):
    index = pd.DatetimeIndex(pd.to_datetime(dates), name="Date")
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes,
         "Close": closes, "Adj Close": closes, "Volume": [100.0] * len(closes)},
        index=index,
    )[CONTRACT_COLUMNS]


def test_appends_only_sessions_newer_than_the_frame():
    frame = _frame(["2026-08-10", "2026-08-11"], [37.0, 37.5])
    cache = StubCache([_bar("2026-08-12", close=38.2),
                       _bar("2026-08-13", close=38.9)])

    result, provenance = append_bridge_bars(frame, "MFPC", cache=cache)

    assert provenance["bridge_sessions_appended"] == 2
    assert [d.date().isoformat() for d in result.index] == [
        "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"
    ]
    assert result["Close"].iloc[-1] == pytest.approx(38.9)


def test_never_overwrites_a_session_the_provider_already_published():
    """The owning provider keeps its bar even when the bridge disagrees."""

    frame = _frame(["2026-08-11", "2026-08-12"], [37.5, 38.2])
    cache = OverlappingStubCache([_bar("2026-08-12", close=99.0)])

    result, provenance = append_bridge_bars(frame, "MFPC", cache=cache)

    assert provenance["bridge_sessions_appended"] == 0
    assert provenance["conflict_count"] == 1
    assert result["Close"].loc["2026-08-12"] == pytest.approx(38.2)
    assert len(result) == 2


def test_an_agreeing_duplicate_is_counted_not_appended():
    """Same session, same OHLC — recorded as a duplicate, not a disagreement."""

    agreeing = _bar("2026-08-12", close=38.2)
    frame = _frame(["2026-08-11"], [37.5])
    # Give the existing bar exactly the bridge's OHLC so the two agree.
    frame.loc[pd.Timestamp("2026-08-12")] = {
        "Open": agreeing["open"], "High": agreeing["high"],
        "Low": agreeing["low"], "Close": agreeing["official_close"],
        "Adj Close": agreeing["official_close"], "Volume": agreeing["volume"],
    }

    result, provenance = append_bridge_bars(
        frame, "MFPC", cache=OverlappingStubCache([agreeing])
    )

    assert provenance["duplicate_count"] == 1
    assert provenance["conflict_count"] == 0
    assert len(result) == 2


@pytest.mark.parametrize("bar", [
    _bar("2026-08-13", close=38.9, status="INCOMPLETE"),
    _bar("2026-08-13", close=38.9, continuous="PARTIAL"),
])
def test_only_final_bars_are_appended(bar):
    frame = _frame(["2026-08-12"], [38.2])
    result, provenance = append_bridge_bars(frame, "MFPC", cache=StubCache([bar]))

    assert provenance["bridge_sessions_appended"] == 0
    assert len(result) == 1


def test_another_symbols_bars_are_ignored():
    frame = _frame(["2026-08-12"], [38.2])
    cache = StubCache([_bar("2026-08-13", close=999.0, symbol="EGAL")])

    _result, provenance = append_bridge_bars(frame, "MFPC", cache=cache)
    assert provenance["bridge_sessions_appended"] == 0


def test_no_cache_returns_the_frame_untouched():
    """A missing cache must degrade to 'no tail', never to a damaged history."""

    frame = _frame(["2026-08-11", "2026-08-12"], [37.5, 38.2])
    result, provenance = append_bridge_bars(frame, "MFPC", cache=StubCache([]))

    assert result is frame
    assert provenance["bridge_sessions_appended"] == 0


def test_empty_frame_is_returned_unchanged():
    empty = _frame([], [])
    result, provenance = append_bridge_bars(empty, "MFPC", cache=StubCache([
        _bar("2026-08-13", close=38.9)
    ]))
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
    through the published series, against a bridge bar quoted on the current
    basis. The two agree; only an action inside the window can split them.
    """

    from core.research_router import bridge_tail_blocked

    assert bridge_tail_blocked(action_date, "2026-08-12") is blocked


def test_no_provider_last_session_never_blocks():
    from core.research_router import bridge_tail_blocked

    assert bridge_tail_blocked("2026-08-13", None) is False


def test_provenance_names_the_appended_sessions():
    """A report must be able to say which bar came from which source."""

    frame = _frame(["2026-08-11"], [37.5])
    cache = StubCache([_bar("2026-08-12", close=38.2),
                       _bar("2026-08-13", close=38.9)])

    _result, provenance = append_bridge_bars(frame, "MFPC", cache=cache)

    assert provenance["bridge_dates"] == ("2026-08-12", "2026-08-13")
    assert provenance["bridge_first_session"] == "2026-08-12"
    assert provenance["bridge_latest_session"] == "2026-08-13"
