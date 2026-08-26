"""Per-year expectancy, pinned, because an aggregate hid an unstable effect.

Disabling the trailing stop looked decisive in aggregate: -0.046% to +0.317%
per trade. Split by entry year it helped in five years and hurt in five, the
aggregate carried by 2017, 2020 and 2025. That split was done by hand, after
the recommendation had already been made, and it reversed it.

So the split belongs in every run rather than in whether someone remembers to
look. These tests pin what it reports.
"""

import csv

import pytest

from scripts.research.isolated_backtest import _period_stability


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "entry_date", "profit_percent"])
        writer.writerows(rows)
    return path


def test_an_aggregate_win_built_on_split_years_is_visible(tmp_path):
    # Mean is strongly positive; half the years are not. This is the shape the
    # trailing-stop recommendation had, and the shape that must not hide.
    rows = (
        [("A.CA", "2020-03-01", 12.0)] * 5
        + [("A.CA", "2021-03-01", -2.0)] * 5
        + [("A.CA", "2022-03-01", 11.0)] * 5
        + [("A.CA", "2023-03-01", -3.0)] * 5
    )
    stability = _period_stability(_write(tmp_path / "r.csv", rows))
    summary = stability.pop("_summary")
    assert summary == {"years": 4, "years_positive": 2, "years_negative": 2}
    assert stability["2020"]["avg_profit_percent"] == pytest.approx(12.0)
    assert stability["2021"]["avg_profit_percent"] == pytest.approx(-2.0)


def test_every_year_is_counted_and_ordered(tmp_path):
    rows = [("A.CA", f"{year}-06-01", 1.0) for year in (2019, 2017, 2018)]
    stability = _period_stability(_write(tmp_path / "r.csv", rows))
    stability.pop("_summary")
    assert list(stability) == ["2017", "2018", "2019"]


def test_win_rate_counts_strictly_positive_trades(tmp_path):
    rows = [("A.CA", "2020-01-01", v) for v in (1.0, 0.0, -1.0, 2.0)]
    stability = _period_stability(_write(tmp_path / "r.csv", rows))
    assert stability["2020"]["win_rate"] == pytest.approx(50.0)
    assert stability["2020"]["trades"] == 4


def test_median_is_reported_beside_the_mean(tmp_path):
    # A positive mean over a negative median is a tail carrying the result,
    # which is exactly what this strategy does. Both have to be visible.
    rows = [("A.CA", "2020-01-01", v) for v in (-1.0, -1.0, -1.0, 30.0)]
    stability = _period_stability(_write(tmp_path / "r.csv", rows))
    assert stability["2020"]["avg_profit_percent"] > 0
    assert stability["2020"]["median_profit_percent"] < 0


def test_unparseable_and_undated_rows_are_skipped_not_counted_as_zero(tmp_path):
    # A blank profit read as 0.0 would dilute the mean toward a false stability.
    rows = [
        ("A.CA", "2020-01-01", 4.0),
        ("A.CA", "2020-01-02", ""),
        ("A.CA", "", 99.0),
    ]
    stability = _period_stability(_write(tmp_path / "r.csv", rows))
    assert stability["2020"]["trades"] == 1
    assert stability["2020"]["avg_profit_percent"] == pytest.approx(4.0)


def test_a_missing_results_file_is_none_rather_than_an_exception(tmp_path):
    assert _period_stability(tmp_path / "absent.csv") is None


def test_a_results_file_with_no_usable_rows_is_none(tmp_path):
    assert _period_stability(_write(tmp_path / "r.csv", [])) is None
