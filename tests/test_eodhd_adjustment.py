"""Tests for the EODHD split-adjustment engine (deterministic, no network)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from providers.eodhd_adjustment import (
    InvalidSplit,
    adjust,
    cumulative_factor_for,
    normalize_splits,
    parse_split_ratio,
)


def _frame(rows):
    return pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])


# --- split ratio parsing ----------------------------------------------------

def test_parse_split_ratio_variants():
    assert parse_split_ratio("2.000000/1.000000") == 2.0
    assert parse_split_ratio("3.000000/2.000000") == 1.5
    assert abs(parse_split_ratio("11.000000/10.000000") - 1.1) < 1e-9
    assert parse_split_ratio("2") == 2.0


def test_parse_split_ratio_rejects_malformed():
    for bad in ("2/0", "abc", "0", "-2/1", "", None):
        with pytest.raises(InvalidSplit):
            parse_split_ratio(bad)


# --- cumulative factor ------------------------------------------------------

def test_cumulative_factor_only_future_splits():
    splits = [(date(2020, 1, 1), 2.0), (date(2022, 1, 1), 1.5)]
    assert cumulative_factor_for(date(2019, 1, 1), splits) == 3.0    # both apply
    assert cumulative_factor_for(date(2021, 1, 1), splits) == 1.5    # only the 2022 split
    assert cumulative_factor_for(date(2023, 1, 1), splits) == 1.0    # none after
    assert cumulative_factor_for(date(2020, 1, 1), splits) == 1.5    # on ex-date: post-split


def test_multiple_sequential_splits():
    splits = [(date(2018, 1, 1), 2.0), (date(2019, 1, 1), 2.0), (date(2020, 1, 1), 1.25)]
    assert cumulative_factor_for(date(2017, 1, 1), splits) == 5.0    # 2*2*1.25


def test_reverse_split_factor_below_one():
    # a 1-for-2 reverse split (ratio 0.5) raises historical prices
    splits = normalize_splits([{"date": "2021-01-01", "split": "1/2"}])
    assert cumulative_factor_for(date(2020, 1, 1), splits) == 0.5


def test_same_day_duplicate_action_combined_once():
    s = normalize_splits([{"date": "2020-01-01", "split": "2/1"},
                          {"date": "2020-01-01", "split": "3/2"}])
    assert len(s) == 1 and abs(s[0][1] - 3.0) < 1e-9      # 2 * 1.5 combined


# --- adjust() ---------------------------------------------------------------

def _sample():
    return _frame([
        (date(2019, 12, 31), 100, 110, 95, 105, 1000),   # before a 2:1 split
        (date(2020, 6, 30), 60, 66, 57, 63, 2000),        # after split
    ])


def test_split_adjusted_ohlc_and_volume():
    res = adjust(_sample(), [{"date": "2020-01-01", "split": "2/1"}])
    r0 = res.frame.iloc[0]
    assert r0["Split Factor"] == 2.0
    assert r0["Close"] == pytest.approx(52.5) and r0["Open"] == pytest.approx(50.0)
    assert r0["Raw Close"] == 105                          # raw preserved
    assert r0["Volume"] == pytest.approx(2000)             # 1000 * 2 (share count grows)
    r1 = res.frame.iloc[1]
    assert r1["Split Factor"] == 1.0 and r1["Close"] == 63  # post-split unchanged


def test_ohlc_ordering_preserved():
    res = adjust(_sample(), [{"date": "2020-01-01", "split": "2/1"}])
    f = res.frame
    assert (f["Low"] <= f["Open"] + 1e-9).all() and (f["Open"] <= f["High"] + 1e-9).all()
    assert (f["Low"] <= f["Close"] + 1e-9).all() and (f["Close"] <= f["High"] + 1e-9).all()


def test_idempotent_and_no_raw_mutation():
    frame = _sample()
    before = frame.copy(deep=True)
    a = adjust(frame, [{"date": "2020-01-01", "split": "2/1"}])
    b = adjust(frame, [{"date": "2020-01-01", "split": "2/1"}])
    pd.testing.assert_frame_equal(frame, before)           # input untouched
    pd.testing.assert_frame_equal(a.frame, b.frame)        # deterministic


def test_total_return_separate_from_split_close():
    tr = {date(2019, 12, 31): 50.0, date(2020, 6, 30): 63.0}
    res = adjust(_sample(), [{"date": "2020-01-01", "split": "2/1"}], total_return_close=tr)
    r0 = res.frame.iloc[0]
    # split-adjusted Close (52.5) is NOT the same as total-return close (50.0 incl dividends)
    assert r0["Close"] == pytest.approx(52.5)
    assert r0["Total Return Adj Close"] == 50.0


def test_no_split_is_identity():
    res = adjust(_sample(), [])
    assert (res.frame["Split Factor"] == 1.0).all()
    assert res.frame.iloc[0]["Close"] == 105               # unchanged


def test_deep_history_factor_like_comi():
    # sequence resembling COMI-style repeated splits accumulates a large factor
    splits = [{"date": f"{y}-07-15", "split": "2/1"} for y in (2011, 2013, 2015)]
    res = adjust(_frame([(date(2010, 1, 1), 80, 82, 78, 80, 100)]), splits)
    assert res.frame.iloc[0]["Split Factor"] == 8.0        # 2^3
    assert res.frame.iloc[0]["Close"] == pytest.approx(10.0)


def test_empty_frame_safe():
    res = adjust(_frame([]), [{"date": "2020-01-01", "split": "2/1"}])
    assert res.frame.empty
