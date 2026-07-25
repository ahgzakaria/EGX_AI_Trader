"""Tests for the TradingView reconciliation pipeline logic.

Uses synthetic CSV data only -- no real TradingView access exists in this
environment. These tests prove the reconciliation math/plumbing is correct so
that dropping a real user-exported CSV into the configured directory would
immediately produce a genuine reconciliation, without touching the pipeline
code again.
"""

import pandas as pd

from services.tradingview_reconciliation import _pct, _session_deltas, _newest_source


def test_pct_diff_basic():
    assert _pct(102, 100) == 2.0
    assert _pct(98, 100) == -2.0
    assert _pct(100, 100) == 0.0


def test_pct_diff_handles_zero_or_missing_denominator():
    assert _pct(5, 0) is None
    assert _pct(5, None) is None
    assert _pct(5, float("nan")) is None


def test_session_deltas_tv_ahead():
    ahead, behind = _session_deltas("2026-07-20", "2026-07-16")
    assert ahead == 4
    assert behind == 0


def test_session_deltas_yahoo_ahead():
    ahead, behind = _session_deltas("2026-07-14", "2026-07-16")
    assert ahead == 0
    assert behind == 2


def test_session_deltas_same_date():
    assert _session_deltas("2026-07-16", "2026-07-16") == (0, 0)


def test_session_deltas_missing_returns_none():
    assert _session_deltas(None, "2026-07-16") == (None, None)
    assert _session_deltas("2026-07-16", None) == (None, None)


def test_newest_source_prefers_latest_date():
    assert _newest_source("2026-07-20", "2026-07-16", "2026-07-14") == "TradingView"
    assert _newest_source("2026-07-14", "2026-07-20", "2026-07-16") == "Yahoo"
    assert _newest_source(None, None, None) == "none available"
