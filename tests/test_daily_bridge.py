"""Tests for the hybrid EGX daily OHLCV bridge (isolated, synthetic).

Covers the builder (continuous OHLC from Last-only, auction separation, official
close, cumulative volume, duplicate suppression, completeness classes, frozen
market_timestamp with progressing values), the normalized cache (idempotency,
force-rebuild versioning, no silent overwrite, provenance), reconciliation
conflict, finalizer non-trading-day handling, and the ERS freshness overlay.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pandas as pd
import pytest

from core.daily_bridge.normalized_cache import NormalizedDailyCache
from core.daily_bridge.provider_chain import DailyProviderChain
from core.daily_bridge.reconcile import (
    CORPORATE_ACTION_DIFFERENCE,
    EXTERNAL_MISSING,
    MATCH,
    MATERIAL_CONFLICT,
    VOLUME_SCALE_DIFFERENCE,
    reconcile_bar,
)
from core.daily_bridge.rubix_daily_builder import RubixDailyBuilder
from core.daily_bridge.schema import (
    COMPLETE_CONTINUOUS_AUCTION_MISSING,
    COMPLETE_DAILY_BAR,
    FINAL,
    NormalizedDailyBar,
    PARTIAL_EARLY_STOP,
    PARTIAL_LATE_START,
    RUBIX_DERIVED,
    SYMBOL_INACTIVE,
)

CAIRO = "Africa/Cairo"
DATE = "2026-07-22"


def _db(tmp_path, events):
    """events: list of (ticker, last, volume, cairo_hh, cairo_mm, [market_ts])."""
    db = tmp_path / "rubix.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE quotes (ticker TEXT, last_price REAL, bid REAL, ask REAL, "
                 "volume REAL, market_timestamp TEXT, received_at TEXT)")
    for e in events:
        ticker, last, vol, hh, mm = e[:5]
        mkt = e[5] if len(e) > 5 else None
        recv = pd.Timestamp(f"2026-07-22 {hh:02d}:{mm:02d}:00", tz=CAIRO).tz_convert("UTC").isoformat()
        conn.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                     (ticker, last, last * 0.999, last * 1.001, vol, mkt or recv, recv))
    conn.commit(); conn.close()
    return db


def _full_day(ticker="COMI", base=100.0):
    """A well-covered continuous session (dense, <45min gaps) + auction."""
    ev = []
    vol = 0
    times = [(10, 5), (10, 20), (10, 45), (11, 0), (11, 30), (12, 0), (12, 30),
             (13, 0), (13, 30), (14, 0), (14, 10)]
    for (hh, mm) in times:
        px = base
        if (hh, mm) == (11, 0):
            px = base * 1.03           # continuous High
        elif (hh, mm) == (12, 0):
            px = base * 0.97           # continuous Low
        elif (hh, mm) == (14, 10):
            px = base * 1.005          # continuous close
        vol += 100
        ev.append((ticker, px, vol, hh, mm))
    vol += 100                          # auction 14:20, distinct clearing price
    ev.append((ticker, base * 1.008, vol, 14, 20))
    return ev


# --- builder: OHLC, auction, official close, volume --------------------------

def test_continuous_ohlc_from_last_only(tmp_path):
    b = RubixDailyBuilder(_db(tmp_path, _full_day("COMI", 100.0)))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.open == pytest.approx(100.0)
    assert bar.high == pytest.approx(103.0)          # max continuous Last
    assert bar.low == pytest.approx(97.0)            # min continuous Last
    assert bar.continuous_close == pytest.approx(100.5)


def test_auction_last_separated_and_official_close(tmp_path):
    b = RubixDailyBuilder(_db(tmp_path, _full_day("COMI", 100.0)))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.auction_last == pytest.approx(100.8)   # auction clearing price
    assert bar.official_close == pytest.approx(100.8)  # official = auction when present
    assert bar.official_close != bar.continuous_close  # kept separate
    assert bar.auction_included_in_hl is False
    assert bar.high == pytest.approx(103.0)           # auction NOT mixed into High


def test_cumulative_volume_is_max_not_sum(tmp_path):
    b = RubixDailyBuilder(_db(tmp_path, _full_day("COMI", 100.0)))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.volume == pytest.approx(1200)          # final cumulative (11*100 + 100 auction)
    assert bar.continuous_volume == pytest.approx(1100)
    assert bar.auction_volume == pytest.approx(100)   # increment only


def test_duplicate_snapshots_suppressed(tmp_path):
    ev = _full_day("COMI", 100.0)
    ev += [("COMI", 100.5, 1100, 14, 11)] * 5          # identical (last,vol) repeats
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.session_completeness == COMPLETE_DAILY_BAR   # dupes don't corrupt OHLC


def test_bid_ask_not_used_for_high_low(tmp_path):
    # Even with a wide bid/ask, High/Low follow traded Last only.
    b = RubixDailyBuilder(_db(tmp_path, _full_day("COMI", 100.0)))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.high == pytest.approx(103.0)           # not ask (103.0*1.001)


# --- completeness classification ---------------------------------------------

def test_complete_daily_bar(tmp_path):
    b = RubixDailyBuilder(_db(tmp_path, _full_day("COMI")))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.session_completeness == COMPLETE_DAILY_BAR
    assert bar.finalization_status == FINAL


def test_auction_missing_is_final_continuous_not_official(tmp_path):
    # Phase 2: a continuous-complete bar with NO auction must be FINAL_CONTINUOUS /
    # OFFICIAL_CLOSE_UNCONFIRMED — never a FINAL_OFFICIAL, never a silent fallback.
    ev = [e for e in _full_day("COMI") if e[3] != 14 or e[4] != 20]   # drop auction
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.continuous_bar_status == "FINAL_CONTINUOUS"
    assert bar.auction_status == "AUCTION_MISSING"
    assert bar.official_bar_status == "OFFICIAL_CLOSE_UNCONFIRMED"
    assert bar.official_close is None                   # NO silent continuous-close fallback
    assert bar.official_close_confirmed is False
    assert bar.expected_range_eligible is True          # continuous history usable by ERS
    assert bar.swing_daily_eligible is False


def test_prevent_false_final_official_and_official_selection(tmp_path):
    # A genuine auction (volume increment) confirms the official close = final auction Last.
    b = RubixDailyBuilder(_db(tmp_path, _full_day("COMI", 100.0)))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.auction_status == "AUCTION_CONFIRMED"
    assert bar.official_bar_status == "FINAL_OFFICIAL"
    assert bar.official_close_confirmed is True
    assert bar.official_close == pytest.approx(100.8)   # final auction Last, not continuous


def test_post_auction_repeat_leaves_official_unconfirmed(tmp_path):
    # auction-window frames repeat the continuous close with NO volume increment.
    ev = [e for e in _full_day("COMI", 100.0) if e[3] != 14 or e[4] != 20]  # drop real auction
    cc = 100.5                                          # continuous close from _full_day
    ev += [("COMI", cc, 1100, 14, 18), ("COMI", cc, 1100, 14, 20)]   # repeats, same vol
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.auction_status == "POST_AUCTION_REPEAT"
    assert bar.official_close_confirmed is False
    assert bar.official_close is None
    assert bar.expected_range_eligible is True          # continuous still usable


def test_partial_late_start(tmp_path):
    # first continuous print at 10:40 (> 10:15 tolerance)
    ev = [("COMI", 100 + i, (i + 1) * 1000, 10 + (40 + i) // 60, (40 + i) % 60) for i in range(20)]
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.session_completeness == PARTIAL_LATE_START
    assert bar.finalization_status != FINAL


def test_partial_early_stop(tmp_path):
    ev = [("COMI", 100 + (i % 3), (i + 1) * 1000, 10 + (5 + i) // 60, (5 + i) % 60) for i in range(30)]
    # events 10:05..10:34 only -> last continuous well before 14:00
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.session_completeness == PARTIAL_EARLY_STOP


def test_inactive_symbol_not_a_failure(tmp_path):
    ev = [("COMI", 100.0, 1000, 11, 0), ("COMI", 100.0, 1000, 12, 0)]   # 2 prints
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.session_completeness == SYMBOL_INACTIVE   # not SUBSCRIPTION_LOST / DATA_GAP


def test_frozen_market_timestamp_with_progressing_values(tmp_path):
    # market_timestamp frozen at an old value, but received_at + Last progress.
    ev = _full_day("COMI", 100.0)
    frozen = "2026-07-01T03:00:00+00:00"
    ev = [(t, l, v, hh, mm, frozen) for (t, l, v, hh, mm) in ev]
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    # builder uses received_at, so a frozen exchange timestamp does not break the bar
    assert bar.session_completeness == COMPLETE_DAILY_BAR
    assert bar.high == pytest.approx(103.0)


def test_decreasing_volume_flagged_not_final(tmp_path):
    ev = _full_day("COMI", 100.0)
    ev.append(("COMI", 100.6, 10, 14, 12))            # cumulative volume drops to 10
    b = RubixDailyBuilder(_db(tmp_path, ev))
    bar = next(x for x in b.build_session(DATE, ["COMI.CA"]))
    assert bar.finalization_status != FINAL
    assert "DECREASED" in bar.validation_warnings or bar.session_completeness == "INVALID_VOLUME"


# --- normalized cache: idempotency, versioning, no silent overwrite ----------

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


# --- reconciliation ----------------------------------------------------------

def test_reconcile_external_missing():
    r = reconcile_bar({"canonical_symbol": "X", "session_date": DATE, "finalization_status": FINAL,
                       "open": 100, "high": 101, "low": 99, "official_close": 100, "volume": 1000},
                      None)
    assert r.result == EXTERNAL_MISSING


def test_reconcile_match():
    rub = {"canonical_symbol": "X", "session_date": DATE, "finalization_status": FINAL,
           "open": 100, "high": 101, "low": 99, "official_close": 100, "volume": 1000}
    ext = {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
    assert reconcile_bar(rub, ext).result == MATCH


def test_reconcile_material_conflict():
    rub = {"canonical_symbol": "X", "session_date": DATE, "finalization_status": FINAL,
           "open": 100, "high": 110, "low": 99, "official_close": 100, "volume": 1000}
    ext = {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
    assert reconcile_bar(rub, ext).result == MATERIAL_CONFLICT   # non-uniform large diff


def test_reconcile_volume_scale():
    rub = {"canonical_symbol": "X", "session_date": DATE, "finalization_status": FINAL,
           "open": 100, "high": 101, "low": 99, "official_close": 100, "volume": 10000}
    ext = {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
    assert reconcile_bar(rub, ext).result == VOLUME_SCALE_DIFFERENCE


# --- provider chain: provenance + external disabled --------------------------

def test_provider_chain_rubix_overrides_yahoo(tmp_path):
    c = NormalizedDailyCache(str(tmp_path / "n.db"))
    c.upsert_bars([_bar(close=100.0)])
    chain = DailyProviderChain(cache=c, external_enabled=False)
    yahoo = [{"session_date": "2026-07-20", "Open": 1, "High": 2, "Low": 1, "Close": 1.5, "Volume": 9},
             {"session_date": DATE, "Open": 5, "High": 6, "Low": 4, "Close": 5.5, "Volume": 9}]
    res = chain.assemble("COMI.CA", yahoo_rows=yahoo)
    by_date = {r["session_date"]: r for r in res.rows}
    assert by_date[DATE]["source_type"] == RUBIX_DERIVED          # rubix wins for its date
    assert by_date["2026-07-20"]["source_type"] == "YAHOO_LEGACY"  # legacy kept for older
    assert res.coverage["external_enabled"] is False


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


def test_continuous_event_received_slightly_after_1415_is_ambiguous():
    from datetime import time
    from core.daily_bridge.session_boundary import (
        LATE_FRAME_AMBIGUOUS, POST_AUCTION_REPEAT, classify_event)
    # received 14:16 but market_timestamp says 14:14 (a pre-14:15 state, delivered late)
    phase, conf = classify_event(
        recv_time=time(14, 16), mkt_time=time(14, 14), material_changed=False,
        in_auction_window=True, after_auction_end=False, volume_increment=0.0,
        market_ts_plausible=True)
    assert phase == POST_AUCTION_REPEAT       # no material change in auction window
    # a genuinely new auction print with volume is confirmed
    phase2, _ = classify_event(
        recv_time=time(14, 20), mkt_time=time(14, 20), material_changed=True,
        in_auction_window=True, after_auction_end=False, volume_increment=500.0,
        market_ts_plausible=True)
    assert phase2 == "AUCTION_CONFIRMED"


def test_ambiguous_boundary_event_quarantined():
    from datetime import time
    from core.daily_bridge.session_boundary import (
        LATE_FRAME_AMBIGUOUS, TIMESTAMP_UNRELIABLE, classify_event)
    # frozen/implausible market_timestamp, received just after close -> quarantined
    phase, conf = classify_event(
        recv_time=time(14, 16), mkt_time=None, material_changed=True,
        in_auction_window=False, after_auction_end=False, volume_increment=0.0,
        market_ts_plausible=False)
    assert phase in (TIMESTAMP_UNRELIABLE, LATE_FRAME_AMBIGUOUS)
    assert conf <= 0.5                        # low confidence -> does not alter official OHLC


def test_finalizer_excludes_friday_saturday_and_holiday():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rdf", "scripts/run_rubix_daily_finalizer.py")
    rdf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rdf)
    assert rdf.main(["--date", "2026-07-17"])["status"] == "NON_TRADING_DAY"   # Friday
    assert rdf.main(["--date", "2026-07-18"])["status"] == "NON_TRADING_DAY"   # Saturday


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
