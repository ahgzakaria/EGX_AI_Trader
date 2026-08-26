"""Tests for liquidity-measured sector strength and its use in Edge scoring."""

import sqlite3

import pandas as pd
import pytest

from decision_support.service import DecisionSupportService
from sector_flow.builder import HISTORY_TABLE
from sector_flow.strength import (
    FULL_STRENGTH_RVOL,
    liquidity_strength,
    load_latest_strength,
    rvol_to_strength,
    strength_frame,
)


def history_frame(rows):
    frame = pd.DataFrame(rows)
    frame["SessionDate"] = pd.to_datetime(frame["SessionDate"])
    return frame


def sample_history():
    return history_frame([
        {"SessionDate": "2026-08-25", "Sector": "Banks", "TurnoverShare": 0.10,
         "RVOL": 0.5, "TurnoverZ": -0.5, "SessionCoverage": 1.0},
        {"SessionDate": "2026-08-25", "Sector": "Real Estate", "TurnoverShare": 0.90,
         "RVOL": 1.0, "TurnoverZ": 0.0, "SessionCoverage": 1.0},
        {"SessionDate": "2026-08-26", "Sector": "Banks", "TurnoverShare": 0.30,
         "RVOL": 1.6, "TurnoverZ": 1.8, "SessionCoverage": 1.0},
        {"SessionDate": "2026-08-26", "Sector": "Real Estate", "TurnoverShare": 0.70,
         "RVOL": 0.4, "TurnoverZ": -1.2, "SessionCoverage": 1.0},
    ])


# --------------------------------------------------------------------------- #
# The scale
# --------------------------------------------------------------------------- #

def test_a_normal_session_maps_to_the_middle_of_the_scale():
    assert rvol_to_strength(1.0) == pytest.approx(0.5)


def test_the_scale_saturates_rather_than_exceeding_one():
    assert rvol_to_strength(FULL_STRENGTH_RVOL) == pytest.approx(1.0)
    assert rvol_to_strength(FULL_STRENGTH_RVOL * 5) == pytest.approx(1.0)


def test_a_dead_sector_maps_to_zero_not_to_a_negative():
    assert rvol_to_strength(0.0) == pytest.approx(0.0)
    assert rvol_to_strength(-1.0) == pytest.approx(0.0)


def test_a_missing_rvol_yields_no_measurement():
    assert rvol_to_strength(None) is None
    assert rvol_to_strength(float("nan")) is None


# --------------------------------------------------------------------------- #
# Reading the history
# --------------------------------------------------------------------------- #

def test_strength_comes_from_the_latest_complete_session():
    strengths = liquidity_strength(sample_history())
    # 2026-08-26, not the calmer 08-25 that precedes it.
    assert strengths["Banks"] == pytest.approx(0.8)
    assert strengths["Real Estate"] == pytest.approx(0.2)


def test_an_incomplete_latest_session_is_not_measured():
    history = sample_history()
    history.loc[history["SessionDate"] == pd.Timestamp("2026-08-26"), "SessionCoverage"] = 0.1
    strengths = liquidity_strength(history)
    assert strengths["Banks"] == pytest.approx(0.25)


def test_an_empty_history_measures_nothing():
    assert liquidity_strength(pd.DataFrame()) == {}
    assert liquidity_strength(None) == {}
    assert strength_frame(pd.DataFrame()).empty


def test_strength_frame_discloses_the_measurement_behind_the_score():
    frame = strength_frame(sample_history())
    assert list(frame["Sector"]) == ["Banks", "Real Estate"]
    assert {"RVOL", "TurnoverShare", "TurnoverZ"}.issubset(frame.columns)
    assert frame["SectorStrength"].is_monotonic_decreasing


# --------------------------------------------------------------------------- #
# The lightweight loader used on the scan path
# --------------------------------------------------------------------------- #

def write_history_db(path, frame):
    stored = frame.copy()
    stored["SessionDate"] = stored["SessionDate"].dt.strftime("%Y-%m-%d")
    with sqlite3.connect(path) as connection:
        stored.to_sql(HISTORY_TABLE, connection, if_exists="replace", index=False)


def test_loader_returns_the_latest_complete_session(tmp_path):
    database = str(tmp_path / "sector_flow.db")
    write_history_db(database, sample_history())
    strengths = load_latest_strength(database)
    assert strengths == {"Banks": pytest.approx(0.8), "Real Estate": pytest.approx(0.2)}


def test_loader_skips_an_incomplete_latest_session(tmp_path):
    database = str(tmp_path / "sector_flow.db")
    history = sample_history()
    history.loc[history["SessionDate"] == pd.Timestamp("2026-08-26"), "SessionCoverage"] = 0.1
    write_history_db(database, history)
    assert load_latest_strength(database)["Banks"] == pytest.approx(0.25)


def test_loader_never_raises_on_a_missing_database(tmp_path):
    """Sector evidence is advisory; its absence must not stop a scan."""

    assert load_latest_strength(str(tmp_path / "absent.db")) == {}


def test_loader_never_raises_on_a_table_that_is_not_there(tmp_path):
    database = str(tmp_path / "empty.db")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (x INTEGER)")
    assert load_latest_strength(database) == {}


# --------------------------------------------------------------------------- #
# Use in Edge scoring
# --------------------------------------------------------------------------- #

def scan_rows():
    return [
        {"Ticker": "COMI.CA", "Sector": "Banks", "EdgeFactors": {}},
        {"Ticker": "TMGH.CA", "Sector": "Real Estate", "EdgeFactors": {}},
        {"Ticker": "JUFO.CA", "Sector": "Food and Beverages", "EdgeFactors": {}},
    ]


def derived_summary():
    return pd.DataFrame([
        {"Sector": "Banks", "SectorStrength": 0.10},
        {"Sector": "Real Estate", "SectorStrength": 0.20},
        {"Sector": "Food and Beverages", "SectorStrength": 0.30},
    ])


def test_measured_strength_is_preferred_over_the_scan_derived_one(tmp_path, monkeypatch):
    database = str(tmp_path / "sector_flow.db")
    write_history_db(database, sample_history())

    service = DecisionSupportService()
    monkeypatch.setitem(service.config, "sector_flow_database", database)
    rows = scan_rows()
    service._apply_sector_strength(rows, derived_summary())

    banks = next(row for row in rows if row["Sector"] == "Banks")
    assert banks["SectorStrength"] == pytest.approx(0.8)
    assert banks["SectorStrengthSource"] == "LIQUIDITY"


def test_a_sector_the_history_cannot_measure_falls_back(tmp_path, monkeypatch):
    """The circular value is weaker evidence, but dropping the factor is worse."""

    database = str(tmp_path / "sector_flow.db")
    write_history_db(database, sample_history())

    service = DecisionSupportService()
    monkeypatch.setitem(service.config, "sector_flow_database", database)
    rows = scan_rows()
    service._apply_sector_strength(rows, derived_summary())

    # Food and Beverages is absent from the liquidity history.
    food = next(row for row in rows if row["Sector"] == "Food and Beverages")
    assert food["SectorStrength"] == pytest.approx(0.30)
    assert food["SectorStrengthSource"] == "SCAN_DERIVED"


def test_scoring_still_works_with_no_liquidity_history(tmp_path, monkeypatch):
    service = DecisionSupportService()
    monkeypatch.setitem(service.config, "sector_flow_database", str(tmp_path / "absent.db"))
    rows = scan_rows()
    service._apply_sector_strength(rows, derived_summary())

    assert all(row["SectorStrengthSource"] == "SCAN_DERIVED" for row in rows)
    assert all(row["EdgeScore"] is not None for row in rows)


def test_edge_score_is_recomputed_after_the_sector_factor_lands(tmp_path, monkeypatch):
    database = str(tmp_path / "sector_flow.db")
    write_history_db(database, sample_history())

    service = DecisionSupportService()
    monkeypatch.setitem(service.config, "sector_flow_database", database)
    rows = scan_rows()
    service._apply_sector_strength(rows, derived_summary())

    for row in rows:
        assert row["EdgeFactors"]["sector_strength"] == row["SectorStrength"]
        assert "EdgeScore" in row and "EdgeContributions" in row


def test_the_sector_modules_import_in_any_order():
    """sector_flow.strength must not reach decision_support and back again.

    decision_support.service imports sector_flow.strength, and sector_flow's
    builder imports decision_support. If strength also imported the builder,
    importing the builder first would close the cycle and fail.
    """

    import subprocess
    import sys

    for module in ("sector_flow.builder", "sector_flow.strength",
                   "decision_support.service"):
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"importing {module} first failed:\n{result.stderr}"
