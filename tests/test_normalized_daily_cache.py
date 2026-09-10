"""The normalized daily cache, and the freshness overlay that reads it.

What this file used to test was the whole Rubix daily bridge -- a builder that
turned captured quote events into a daily bar, a reconciler that compared it
against Yahoo, and a provider chain that ranked the two. All of that is gone
with the feed: the daily candle comes from MubasherTrade PRO's own databases
now, and nothing builds a bar out of events this project captured.

The cache outlived it. `scalping_expected_range` and two dashboards still read
it, and the overlay tests below are the reason -- a stale history that a cached
completed session can bring current, and the guard that stops it injecting a
session still forming.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from core.daily_bridge.normalized_cache import NormalizedDailyCache
from core.daily_bridge.schema import (
    COMPLETE_DAILY_BAR,
    FINAL,
    NormalizedDailyBar,
    RUBIX_DERIVED,
)

CAIRO = "Africa/Cairo"
DATE = "2026-07-22"


def _bar(sym="COMI.CA", close=100.0, status=FINAL):
    return NormalizedDailyBar(canonical_symbol=sym, session_date=DATE, open=99.0, high=101.0,
                              low=98.0, continuous_close=close, official_close=close, volume=1000,
                              finalization_status=status, session_completeness=COMPLETE_DAILY_BAR,
                              source_type=RUBIX_DERIVED, provider="rubix_local")


def test_cache_idempotent_normal_run(tmp_path):
    c = NormalizedDailyCache(str(tmp_path / "n.db"))
    c.upsert_bars([_bar()])
    ins, skipped, ver = c.upsert_bars([_bar()])       # identical re-run
    assert ins == 0 and ver == 0 and skipped == 1
    assert len([r for r in c.all_active(DATE)]) == 1


def test_cache_force_rebuild_versions(tmp_path):
    c = NormalizedDailyCache(str(tmp_path / "n.db"))
    c.upsert_bars([_bar(close=100.0)])
    _, _, ver = c.upsert_bars([_bar(close=100.0)], force_rebuild=True)
    assert ver == 1
    with c._connect() as conn:
        versions = [r["version"] for r in conn.execute(
            "SELECT version FROM daily_bars WHERE canonical_symbol='COMI.CA'")]
    assert set(versions) == {1, 2}                    # prior version preserved


def test_cache_no_silent_overwrite_on_change(tmp_path):
    c = NormalizedDailyCache(str(tmp_path / "n.db"))
    c.upsert_bars([_bar(close=100.0)])
    c.upsert_bars([_bar(close=105.0)])                # changed, no force
    active = c.symbol_final("COMI.CA", DATE)
    assert float(active["official_close"]) == 100.0   # original active row unchanged


def test_cache_latest_final_session(tmp_path):
    c = NormalizedDailyCache(str(tmp_path / "n.db"))
    c.upsert_bars([_bar()])
    assert c.latest_final_session("RUBIX_DERIVED") == DATE


# --- freshness overlay (ERS Stage A integration) -----------------------------

def test_rubix_overlay_makes_history_current(tmp_path):
    from scalping_expected_range.config import ExpectedRangeConfig
    from scalping_expected_range.historical_selector import HistoricalSelector

    cfg = ExpectedRangeConfig.load()
    # a normalized cache with a FINAL bar for 2026-07-21 (which Yahoo lacks)
    ndc = NormalizedDailyCache(str(tmp_path / "n.db"))
    ndc.upsert_bars([NormalizedDailyBar(
        canonical_symbol="X", session_date="2026-07-21", open=100, high=102, low=98,
        continuous_close=101, official_close=101, volume=1000, finalization_status=FINAL,
        continuous_bar_status="FINAL_CONTINUOUS", expected_range_eligible=True,
        source_type=RUBIX_DERIVED)])

    class _Cache:   # yahoo history ends 2026-07-20
        def load_cached(self, provider, symbol, period, interval, allow_expired=False):
            idx = pd.bdate_range(end="2026-07-20", periods=40)
            return pd.DataFrame([{"Open": 100, "High": 102, "Low": 98, "Close": 100, "Volume": 1e6}
                                 for _ in range(40)], index=idx)

    now = datetime(2026, 7, 22, 11, 0, tzinfo=timezone.utc)
    sel = HistoricalSelector(config=cfg, cache=_Cache(), now=now)
    sel._rubix_cache = ndc                              # inject the test cache
    daily, prov = sel._load_clean_daily("X")
    assert prov.rubix_overlay_sessions == 1             # 2026-07-21 appended
    assert prov.latest_completed_session == "2026-07-21"
    assert prov.data_status == "OK"                     # no longer HISTORY_STALE
    assert "RUBIX_CONTINUOUS_DERIVED" in prov.historical_provider


def test_overlay_never_injects_forming_today(tmp_path):
    from scalping_expected_range.config import ExpectedRangeConfig
    from scalping_expected_range.historical_selector import HistoricalSelector
    cfg = ExpectedRangeConfig.load()
    ndc = NormalizedDailyCache(str(tmp_path / "n.db"))
    # cache has a FINAL bar dated 2026-07-22 (today, still forming at 11:00)
    ndc.upsert_bars([NormalizedDailyBar(
        canonical_symbol="X", session_date="2026-07-22", open=100, high=102, low=98,
        continuous_close=101, official_close=101, volume=1000, finalization_status=FINAL,
        continuous_bar_status="FINAL_CONTINUOUS", expected_range_eligible=True,
        source_type=RUBIX_DERIVED)])

    class _Cache:
        def load_cached(self, *a, **k):
            idx = pd.bdate_range(end="2026-07-20", periods=40)
            return pd.DataFrame([{"Open": 100, "High": 102, "Low": 98, "Close": 100, "Volume": 1e6}
                                 for _ in range(40)], index=idx)

    now = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)   # 11:00 Cairo, session forming
    sel = HistoricalSelector(config=cfg, cache=_Cache(), now=now)
    sel._rubix_cache = ndc
    daily, prov = sel._load_clean_daily("X")
    assert prov.rubix_overlay_sessions == 0             # today's forming bar NOT injected
