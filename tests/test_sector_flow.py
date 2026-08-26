"""Tests for the sector map build and the per-sector liquidity history."""

from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from decision_support.sector_analysis import load_sector_map
from sector_flow.builder import HISTORY_TABLE, latest_build_metadata, load_saved, save
from sector_flow.history import (
    aggregate_sectors,
    complete_sessions,
    latest_snapshot,
    rotation_matrix,
    sector_history,
    turnover_series,
)


SESSIONS = pd.date_range("2026-01-01", periods=40, freq="B")


def candles(seed, volume, start=100.0):
    generator = np.random.default_rng(seed)
    close = start + np.cumsum(generator.normal(0, 1, len(SESSIONS)))
    return pd.DataFrame({
        "Open": close,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Close": close,
        "Volume": generator.integers(volume, volume * 2, len(SESSIONS)).astype(float),
    }, index=SESSIONS)


def universe():
    frames = {
        "COMI.CA": candles(1, 10_000),
        "HDBK.CA": candles(2, 5_000),
        "TMGH.CA": candles(3, 20_000),
    }
    sectors = {
        "COMI.CA": "Banks", "HDBK.CA": "Banks",
        "TMGH.CA": "Real Estate", "ZZZZ.CA": "Utilities",
    }
    return frames, sectors


def test_turnover_uses_typical_price_by_default():
    frame = candles(7, 1_000)
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    assert np.allclose(turnover_series(frame), typical * frame["Volume"])
    assert np.allclose(turnover_series(frame, "close"), frame["Close"] * frame["Volume"])


def test_unknown_turnover_method_is_rejected():
    with pytest.raises(ValueError):
        turnover_series(candles(7, 1_000), "vwap")


def test_missing_columns_fail_loudly():
    with pytest.raises(ValueError):
        turnover_series(candles(7, 1_000).drop(columns=["Volume"]))


def test_unclassified_symbols_never_enter_the_aggregate():
    frames, sectors = universe()
    frames["UNKN.CA"] = candles(9, 999_999)
    aggregated = aggregate_sectors(frames, sectors)
    assert set(aggregated["Sector"]) == {"Banks", "Real Estate"}
    assert aggregated[aggregated["Sector"] == "Banks"]["Symbols"].max() == 2


def test_shares_sum_to_one_each_session():
    frames, sectors = universe()
    aggregated = aggregate_sectors(frames, sectors)
    totals = aggregated.groupby("SessionDate")["TurnoverShare"].sum()
    assert np.allclose(totals, 1.0)


def test_baselines_exclude_the_session_they_score():
    frames, sectors = universe()
    history = sector_history(frames, sectors, window=20)
    banks = history[history["Sector"] == "Banks"].sort_values("SessionDate")
    log_turnover = np.log(banks["Turnover"].to_numpy())

    prior = log_turnover[-21:-1]
    expected_z = (log_turnover[-1] - prior.mean()) / prior.std(ddof=1)
    expected_rvol = np.exp(log_turnover[-1] - np.median(prior))

    assert banks["TurnoverZ"].iloc[-1] == pytest.approx(expected_z)
    assert banks["RVOL"].iloc[-1] == pytest.approx(expected_rvol)
    # The first `window` sessions have no complete trailing baseline.
    assert banks["TurnoverZ"].iloc[:20].isna().all()


def test_features_are_computed_per_sector_not_across_the_frame():
    frames, sectors = universe()
    history = sector_history(frames, sectors, window=20)
    for sector in ("Banks", "Real Estate"):
        rows = history[history["Sector"] == sector].sort_values("SessionDate")
        expected = np.log(rows["Turnover"].to_numpy()[-21:-1]).mean()
        assert rows["BaselineLogTurnover"].iloc[-1] == pytest.approx(expected)


def test_share_change_uses_the_configured_lookback():
    frames, sectors = universe()
    history = sector_history(frames, sectors, share_lookback=5)
    banks = history[history["Sector"] == "Banks"].sort_values("SessionDate")
    shares = banks["TurnoverShare"].to_numpy()
    assert banks["ShareChange"].iloc[-1] == pytest.approx(shares[-1] - shares[-6])


def test_latest_snapshot_is_ranked_by_share():
    frames, sectors = universe()
    history = sector_history(frames, sectors)
    snapshot = latest_snapshot(history)
    assert snapshot["SessionDate"].nunique() == 1
    assert snapshot["SessionDate"].iloc[0] == history["SessionDate"].max()
    assert snapshot["TurnoverShare"].is_monotonic_decreasing


def test_rotation_matrix_is_session_by_sector():
    frames, sectors = universe()
    matrix = rotation_matrix(sector_history(frames, sectors), sessions=10)
    assert list(matrix.columns) == ["Banks", "Real Estate"]
    assert len(matrix) == 10
    assert matrix.index.is_monotonic_increasing


def test_empty_inputs_produce_an_empty_history():
    assert sector_history({}, {"COMI.CA": "Banks"}).empty
    assert latest_snapshot(pd.DataFrame()).empty
    assert rotation_matrix(pd.DataFrame()).empty


def test_saved_history_round_trips(tmp_path):
    frames, sectors = universe()
    history = sector_history(frames, sectors)
    database = str(tmp_path / "sector_flow.db")
    metadata = {"built_at": "2026-08-26T10:00:00+02:00", "sectors": 2}

    save(history, metadata, database)
    restored = load_saved(database)

    assert len(restored) == len(history)
    assert restored["SessionDate"].max() == history["SessionDate"].max()
    assert latest_build_metadata(database)["sectors"] == 2


def test_load_saved_returns_empty_without_a_database(tmp_path):
    assert load_saved(str(tmp_path / "absent.db")).empty
    assert latest_build_metadata(str(tmp_path / "absent.db")) == {}


def test_sector_map_matches_the_universe_convention():
    """The shipped map must be readable by the existing decision-support loader."""

    mapping = load_sector_map("data/sectors.csv")
    assert mapping, "data/sectors.csv is missing; run scripts/build_sector_map.py"
    assert all(ticker.endswith(".CA") for ticker in mapping)
    assert "Unknown" not in set(mapping.values())


def test_reconciliation_separates_non_equity_from_delisted():
    from scripts.build_sector_map import reconcile

    mapped = pd.DataFrame({"Ticker": ["COMI.CA"], "Sector": ["Banks"]})
    excluded = {"EGX30ETF": "Excluded - non-equity instrument"}
    unmapped = reconcile(["COMI.CA", "EGX30ETF.CA", "ESRS.CA"], mapped, excluded)

    reasons = dict(zip(unmapped["Ticker"], unmapped["Reason"]))
    assert "COMI.CA" not in reasons
    assert reasons["EGX30ETF.CA"] == "NON_EQUITY_INSTRUMENT"
    assert reasons["ESRS.CA"] == "NOT_IN_ACTIVE_LISTING"


def test_shipped_reconciliation_report_covers_every_unmapped_symbol():
    from core.symbols import load_symbols

    report = Path("reports/sector_map_reconciliation.csv")
    assert report.is_file(), "run scripts/build_sector_map.py"
    unmapped = set(pd.read_csv(report)["Ticker"])
    mapped = set(load_sector_map("data/sectors.csv"))
    universe = set(load_symbols("data/symbols.csv"))
    assert universe - mapped == unmapped
    assert not (unmapped & mapped)


def partial_last_session(frames):
    """Reproduce an in-progress session: only one symbol has printed a bar."""

    extra = SESSIONS[-1] + pd.Timedelta(days=3)
    trimmed = {}
    for index, (symbol, frame) in enumerate(frames.items()):
        if index == 0:
            row = frame.iloc[[-1]].copy()
            row.index = [extra]
            trimmed[symbol] = pd.concat([frame, row])
        else:
            trimmed[symbol] = frame
    return trimmed, extra


def test_in_progress_session_is_flagged_as_low_coverage():
    frames, sectors = universe()
    frames, partial_date = partial_last_session(frames)
    history = sector_history(frames, sectors)

    partial = history[history["SessionDate"] == partial_date]
    assert not partial.empty
    assert partial["MarketSymbols"].iloc[0] == 1
    assert partial["SessionCoverage"].iloc[0] < 0.6


def test_latest_snapshot_skips_the_in_progress_session():
    frames, sectors = universe()
    frames, partial_date = partial_last_session(frames)
    history = sector_history(frames, sectors)

    snapshot = latest_snapshot(history)
    assert snapshot["SessionDate"].iloc[0] != partial_date
    assert snapshot["SessionDate"].iloc[0] == SESSIONS[-1]
    # The guard is a default, not a hard filter -- callers can still opt in.
    assert latest_snapshot(history, min_coverage=0.0)["SessionDate"].iloc[0] == partial_date


def test_rotation_matrix_excludes_low_coverage_sessions():
    frames, sectors = universe()
    frames, partial_date = partial_last_session(frames)
    matrix = rotation_matrix(sector_history(frames, sectors), sessions=5)
    assert partial_date not in matrix.index


def test_full_sessions_keep_full_coverage():
    frames, sectors = universe()
    history = sector_history(frames, sectors)
    assert (history["SessionCoverage"] >= 0.99).all()
