"""Tests for the intraday sector liquidity blend and its session guard."""

import numpy as np
import pandas as pd
import pytest

from sector_flow.intraday import (
    CLOSE_OBSERVED_BY,
    DEFAULT_BLEND_WEIGHT,
    MIN_OPENING_MINUTES,
    OPENING_END,
    SESSION_CLOSE,
    SESSION_OPEN,
    blend,
    complete_sessions,
    evaluate,
    forecast_rest_of_day,
    opening_shares,
    previous_daily_shares,
    rest_of_day_shares,
    score,
    sector_shares,
    session_coverage,
    weight_sweep,
)


SECTORS = ["Banks", "Real Estate", "Food and Beverages", "Construction"]
TICKERS = {"COMI": "Banks", "TMGH": "Real Estate", "JUFO": "Food and Beverages",
           "ORAS": "Construction"}


def session_minutes(date, first="07:00", last="11:30", step=1, tickers=None):
    """Build one session's minute rows between two clock times."""

    start = pd.Timestamp(f"2026-01-01 {first}")
    end = pd.Timestamp(f"2026-01-01 {last}")
    stamps = pd.date_range(start, end, freq=f"{step}min").strftime("%H:%M")
    generator = np.random.default_rng(abs(hash(date)) % 10_000)
    rows = []
    for minute in stamps:
        for ticker in (tickers or TICKERS):
            rows.append({
                "SessionDate": date,
                "Minute": minute,
                "ticker": ticker,
                "Sector": TICKERS[ticker],
                "Turnover": float(generator.integers(1_000, 50_000)),
            })
    return pd.DataFrame(rows)


def minutes_fixture():
    return pd.concat(
        [session_minutes(f"2026-08-{day:02d}") for day in range(3, 19)],
        ignore_index=True,
    )


def daily_fixture(sessions):
    """A daily panel covering the sessions plus the day before the first."""

    dates = ["2026-08-02", *sessions]
    generator = np.random.default_rng(5)
    rows = []
    for date in dates:
        weights = generator.random(len(SECTORS))
        weights = weights / weights.sum()
        for sector, share in zip(SECTORS, weights):
            rows.append({"SessionDate": pd.Timestamp(date), "Sector": sector,
                         "TurnoverShare": float(share)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Session guard
# --------------------------------------------------------------------------- #

def test_a_fully_observed_session_is_complete():
    coverage = session_coverage(session_minutes("2026-08-03"))
    assert coverage["Complete"].iloc[0]
    assert coverage["OpeningMinutes"].iloc[0] >= MIN_OPENING_MINUTES


def test_a_session_cut_short_is_rejected():
    """The machine was switched off mid-session."""

    coverage = session_coverage(session_minutes("2026-08-16", last="10:17"))
    assert not coverage["Complete"].iloc[0]
    assert coverage["LastMinute"].iloc[0] < CLOSE_OBSERVED_BY


def test_a_session_observed_only_at_the_close_is_rejected():
    coverage = session_coverage(session_minutes("2026-08-17", first="11:25"))
    assert not coverage["Complete"].iloc[0]
    assert coverage["OpeningMinutes"].iloc[0] == 0


def test_a_session_missing_the_opening_window_is_rejected():
    coverage = session_coverage(session_minutes("2026-08-20", first="08:30"))
    assert not coverage["Complete"].iloc[0]


def test_a_sparsely_sampled_session_is_rejected():
    """July's collector captured the session, but only every tenth minute."""

    coverage = session_coverage(session_minutes("2026-07-21", step=10))
    assert not coverage["Complete"].iloc[0]


def test_complete_sessions_keeps_only_admitted_days():
    minutes = pd.concat([
        session_minutes("2026-08-03"),
        session_minutes("2026-08-04", last="09:00"),
    ], ignore_index=True)
    kept = complete_sessions(minutes)
    assert set(kept["SessionDate"]) == {"2026-08-03"}


# --------------------------------------------------------------------------- #
# Shares
# --------------------------------------------------------------------------- #

def test_shares_sum_to_one_per_session():
    matrix = sector_shares(minutes_fixture())
    assert np.allclose(matrix.sum(axis=1), 1.0)


def test_rest_of_day_excludes_the_opening_window():
    """The target must not contain the window the forecast is built from."""

    minutes = session_minutes("2026-08-03")
    rest = minutes[minutes["Minute"] >= OPENING_END]
    opening = minutes[minutes["Minute"] < OPENING_END]
    assert not opening.empty and not rest.empty

    expected = rest.groupby("Sector")["Turnover"].sum()
    expected = expected / expected.sum()
    actual = rest_of_day_shares(minutes).loc["2026-08-03"]
    for sector in expected.index:
        assert actual[sector] == pytest.approx(expected[sector])


def test_opening_and_rest_partition_the_session():
    minutes = session_minutes("2026-08-03")
    opening = minutes[minutes["Minute"] < OPENING_END]["Turnover"].sum()
    rest = minutes[minutes["Minute"] >= OPENING_END]["Turnover"].sum()
    assert opening + rest == pytest.approx(minutes["Turnover"].sum())


def test_session_window_bounds_are_respected():
    outside = session_minutes("2026-08-03", first="05:20", last="12:30")
    kept = outside[(outside["Minute"] >= SESSION_OPEN) & (outside["Minute"] <= SESSION_CLOSE)]
    assert kept["Minute"].min() >= SESSION_OPEN
    assert kept["Minute"].max() <= SESSION_CLOSE


# --------------------------------------------------------------------------- #
# Previous-session lookup
# --------------------------------------------------------------------------- #

def test_previous_daily_shares_returns_the_prior_session():
    daily = daily_fixture(["2026-08-03", "2026-08-04"])
    previous = previous_daily_shares(daily, ["2026-08-04"], SECTORS)
    expected = daily[daily["SessionDate"] == pd.Timestamp("2026-08-03")].set_index("Sector")
    for sector in SECTORS:
        assert previous.loc["2026-08-04", sector] == pytest.approx(
            expected.loc[sector, "TurnoverShare"]
        )


def test_previous_daily_shares_skips_a_session_with_no_predecessor():
    daily = daily_fixture(["2026-08-03"])
    assert previous_daily_shares(daily, ["2026-08-02"], SECTORS).empty


def test_previous_daily_shares_for_today_before_the_panel_holds_it():
    """Mid-session the daily panel ends yesterday; yesterday is still the answer."""
    daily = daily_fixture(["2026-08-03", "2026-08-04"])
    previous = previous_daily_shares(daily, ["2026-08-05"], SECTORS)
    expected = daily[daily["SessionDate"] == pd.Timestamp("2026-08-04")].set_index("Sector")
    for sector in SECTORS:
        assert previous.loc["2026-08-05", sector] == pytest.approx(
            expected.loc[sector, "TurnoverShare"]
        )


def test_the_forecast_runs_before_the_daily_panel_holds_today():
    """The existing mid-session test put today into the daily panel, which the
    live page never has, so it passed while the page showed no forecast."""
    minutes = pd.concat([
        minutes_fixture(),
        session_minutes("2026-08-19", last="08:15"),
    ], ignore_index=True)
    daily = daily_fixture(sorted(minutes_fixture()["SessionDate"].unique()))
    assert pd.Timestamp("2026-08-19") not in set(daily["SessionDate"])

    forecast = forecast_rest_of_day(minutes, daily, session="2026-08-19")
    assert not forecast.empty
    assert forecast["Forecast"].sum() == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Blend
# --------------------------------------------------------------------------- #

def test_blend_endpoints_return_each_input():
    opening = pd.DataFrame({"A": [0.8], "B": [0.2]}, index=["d1"])
    previous = pd.DataFrame({"A": [0.3], "B": [0.7]}, index=["d1"])
    assert blend(opening, previous, 1.0).loc["d1", "A"] == pytest.approx(0.8)
    assert blend(opening, previous, 0.0).loc["d1", "A"] == pytest.approx(0.3)


def test_blend_is_renormalised():
    opening = pd.DataFrame({"A": [0.6], "B": [0.2]}, index=["d1"])
    previous = pd.DataFrame({"A": [0.2], "B": [0.2]}, index=["d1"])
    blended = blend(opening, previous, 0.5)
    assert blended.loc["d1"].sum() == pytest.approx(1.0)


def test_blend_rejects_a_weight_outside_the_unit_interval():
    frame = pd.DataFrame({"A": [1.0]}, index=["d1"])
    for weight in (-0.1, 1.5):
        with pytest.raises(ValueError):
            blend(frame, frame, weight)


def test_blend_only_covers_sessions_both_inputs_have():
    opening = pd.DataFrame({"A": [0.5, 1.0], "B": [0.5, 0.0]}, index=["d1", "d2"])
    previous = pd.DataFrame({"A": [1.0], "B": [0.0]}, index=["d1"])
    assert list(blend(opening, previous, 0.5).index) == ["d1"]


# --------------------------------------------------------------------------- #
# Scoring and evaluation
# --------------------------------------------------------------------------- #

def test_a_perfect_predictor_scores_zero_error():
    actual = pd.DataFrame({"A": [0.5, 0.4], "B": [0.5, 0.6]}, index=["d1", "d2"])
    result = score(actual, actual, top_k=1)
    assert result["mae"] == pytest.approx(0.0)
    assert result["top1_hit_rate"] == pytest.approx(1.0)


def test_evaluate_scores_the_blend_against_both_of_its_inputs():
    minutes = minutes_fixture()
    daily = daily_fixture(sorted(minutes["SessionDate"].unique()))
    scores = evaluate(minutes, daily)
    assert list(scores["predictor"]) == [
        "opening_window", "previous_session", f"blend_{DEFAULT_BLEND_WEIGHT:.0%}_opening",
    ]
    assert (scores["sessions"] > 0).all()


def test_weight_sweep_endpoints_match_the_standalone_inputs():
    minutes = minutes_fixture()
    daily = daily_fixture(sorted(minutes["SessionDate"].unique()))
    sweep = weight_sweep(minutes, daily, weights=(0.0, 1.0))
    scores = evaluate(minutes, daily)

    previous_only = scores[scores["predictor"] == "previous_session"]["mae"].iloc[0]
    opening_only = scores[scores["predictor"] == "opening_window"]["mae"].iloc[0]
    assert sweep[sweep["weight"] == 0.0]["mae"].iloc[0] == pytest.approx(previous_only)
    assert sweep[sweep["weight"] == 1.0]["mae"].iloc[0] == pytest.approx(opening_only)


def test_evaluate_excludes_incomplete_sessions():
    minutes = pd.concat([
        minutes_fixture(),
        session_minutes("2026-08-19", last="08:00"),
    ], ignore_index=True)
    daily = daily_fixture(sorted(minutes["SessionDate"].unique()))
    assert evaluate(minutes, daily)["sessions"].max() == 16


# --------------------------------------------------------------------------- #
# Live forecast
# --------------------------------------------------------------------------- #

def test_forecast_runs_on_a_session_still_in_progress():
    """The forecast must work mid-session, when the close has not happened."""

    minutes = pd.concat([
        minutes_fixture(),
        session_minutes("2026-08-19", last="08:15"),
    ], ignore_index=True)
    daily = daily_fixture(sorted(minutes["SessionDate"].unique()))

    forecast = forecast_rest_of_day(minutes, daily, session="2026-08-19")
    assert not forecast.empty
    assert forecast["Forecast"].sum() == pytest.approx(1.0)
    assert forecast["OpeningMinutesObserved"].iloc[0] >= MIN_OPENING_MINUTES


def test_forecast_refuses_when_the_opening_was_not_observed():
    minutes = pd.concat([
        minutes_fixture(),
        session_minutes("2026-08-19", first="09:00"),
    ], ignore_index=True)
    daily = daily_fixture(sorted(minutes["SessionDate"].unique()))
    assert forecast_rest_of_day(minutes, daily, session="2026-08-19").empty


def test_forecast_is_ranked_and_reports_its_inputs():
    minutes = minutes_fixture()
    daily = daily_fixture(sorted(minutes["SessionDate"].unique()))
    forecast = forecast_rest_of_day(minutes, daily)
    assert forecast["Forecast"].is_monotonic_decreasing
    assert {"OpeningShare", "PreviousShare", "Change"}.issubset(forecast.columns)


# --------------------------------------------------------------------------- #
# MubasherTrade PRO minute store
# --------------------------------------------------------------------------- #

def _tmin(year, month, day, hour, minute):
    """``TMIN`` for a UTC wall-clock time: minutes since the Unix epoch."""
    from datetime import datetime, timezone

    stamp = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return str(int(stamp.timestamp() // 60))


def _mubasher_store(root, tables):
    import sqlite3

    store = root / "Intraday" / "CASE"
    store.mkdir(parents=True)
    with sqlite3.connect(store / "INTRADAY_MASTER.db") as connection:
        for table, rows in tables.items():
            connection.execute(f'CREATE TABLE "{table}" (TMIN TEXT, TOVR TEXT)')
            connection.executemany(f'INSERT INTO "{table}" VALUES (?, ?)', rows)


def test_the_mubasher_loader_puts_minutes_on_the_session_clock(tmp_path):
    from sector_flow.intraday import MINUTE_COLUMNS, load_mubasher_minute_turnover

    _mubasher_store(tmp_path, {
        "_COMI": [
            (_tmin(2026, 9, 15, 6, 59), "5"),             # 09:59 Cairo, pre-open
            (_tmin(2026, 9, 15, 7, 0), "2515778.97"),     # 10:00 Cairo
            (_tmin(2026, 9, 15, 7, 1), "0"),              # nothing traded
            (_tmin(2026, 9, 15, 11, 29), "925910.0"),     # 14:29 Cairo, auction
        ],
        "_EGX30": [(_tmin(2026, 9, 15, 7, 0), "999")],    # an index, not a stock
        "_TMGH": [(_tmin(2026, 9, 15, 7, 0), "10")],      # no sector in the map
    })
    frame = load_mubasher_minute_turnover({"COMI.CA": "Banks"}, root=tmp_path)
    assert list(frame.columns) == MINUTE_COLUMNS
    assert frame["Minute"].tolist() == ["07:00", "11:29"]
    assert frame["SessionDate"].tolist() == ["2026-09-15", "2026-09-15"]
    assert frame["Turnover"].tolist() == [2515778.97, 925910.0]
    assert set(frame["ticker"]) == {"COMI"}


def test_the_opening_stays_ten_oclock_cairo_after_summer_time(tmp_path):
    """10:00 Cairo is 08:00 UTC in December. Placed on UTC it would miss the
    whole opening window, and the forecast would refuse every winter day."""
    from sector_flow.intraday import load_mubasher_minute_turnover

    _mubasher_store(tmp_path, {"_COMI": [(_tmin(2026, 12, 1, 8, 0), "100")]})
    frame = load_mubasher_minute_turnover({"COMI.CA": "Banks"}, root=tmp_path)
    assert frame["Minute"].tolist() == ["07:00"]
    assert frame["Minute"].iloc[0] < OPENING_END


def test_no_terminal_installed_is_an_empty_frame(tmp_path):
    from sector_flow.intraday import MINUTE_COLUMNS, load_mubasher_minute_turnover

    frame = load_mubasher_minute_turnover({"COMI.CA": "Banks"}, root=tmp_path)
    assert frame.empty and list(frame.columns) == MINUTE_COLUMNS


def test_empty_inputs_are_handled():
    assert forecast_rest_of_day(pd.DataFrame(), pd.DataFrame()).empty
    assert session_coverage(pd.DataFrame()).empty
    assert sector_shares(pd.DataFrame()).empty
