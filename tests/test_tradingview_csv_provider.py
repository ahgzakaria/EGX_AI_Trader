"""Acceptance tests for the TradingView manual-CSV-export provider.

All CSVs here are synthetic, matching the documented export column shape
(time/open/high/low/close/volume). No real TradingView export was available
in this environment.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from providers.base_provider import ProviderSchemaError
from providers.tradingview_csv_provider import TradingViewCsvProvider

DURING_OPEN = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)  # 11:00 Cairo
AFTER_CLOSE_20 = datetime(2026, 7, 20, 11, 50, tzinfo=timezone.utc)  # 14:50 Cairo


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_valid_completed_rows_are_normalized(tmp_path):
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": ["2026-07-14", "2026-07-15", "2026-07-16"],
        "open": [100, 101, 102], "high": [102, 103, 104],
        "low": [99, 100, 101], "close": [101, 102, 103],
        "Volume": [1000, 2000, 3000],
    })
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert outcome.valid_rows == 3
    assert outcome.rejected_rows == 0
    assert list(outcome.frame["Close"]) == [101.0, 102.0, 103.0]
    assert outcome.frame.attrs["market_data"]["import_method"] == "manual_csv_export"
    assert outcome.frame.attrs["market_data"]["source_filename"] == csv.name


def test_forming_session_excluded_regardless_of_export_time(tmp_path):
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": ["2026-07-16", "2026-07-20"],
        "open": [100, 105], "high": [102, 106],
        "low": [99, 104], "close": [101, 105.5],
        "Volume": [1000, 4000],
    })
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert outcome.valid_rows == 1
    assert outcome.excluded_forming_rows == 1
    assert pd.Timestamp("2026-07-20") not in outcome.frame.index


def test_completed_session_included_after_close_safety(tmp_path):
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": ["2026-07-20"], "open": [105], "high": [106],
        "low": [104], "close": [105.5], "Volume": [4000],
    })
    provider = TradingViewCsvProvider(now=lambda: AFTER_CLOSE_20)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert outcome.valid_rows == 1
    assert outcome.excluded_forming_rows == 0


@pytest.mark.parametrize("field,value", [("low", 0), ("open", -1), ("volume", -5)])
def test_invalid_ohlcv_row_rejected_not_repaired(tmp_path, field, value):
    rows = {
        "time": ["2026-07-14"], "open": [100], "high": [102],
        "low": [99], "close": [101], "Volume": [1000],
    }
    rows[field if field != "volume" else "Volume"] = [value]
    csv = _write_csv(tmp_path / "comi.csv", rows)
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert outcome.valid_rows == 0
    assert outcome.rejected_rows == 1
    assert outcome.frame is None


def test_ohlc_ordering_violation_rejected(tmp_path):
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": ["2026-07-14"], "open": [100], "high": [90],  # high < open
        "low": [99], "close": [101], "Volume": [1000],
    })
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert outcome.rejected_rows == 1
    assert "ordering" in outcome.rejections[0]["reason"]


def test_missing_required_column_raises_schema_error(tmp_path):
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": ["2026-07-14"], "open": [100], "high": [102],
        "low": [99], "close": [101],  # no volume column
    })
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    with pytest.raises(ProviderSchemaError):
        provider.import_csv(csv, "COMI.CA")


def test_missing_file_raises(tmp_path):
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    with pytest.raises(Exception):
        provider.import_csv(tmp_path / "does_not_exist.csv", "COMI.CA")


def test_epoch_time_column_is_parsed(tmp_path):
    # Unix ms epoch for 2026-07-14 08:00 UTC.
    epoch_ms = int(datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": [epoch_ms], "open": [100], "high": [102],
        "low": [99], "close": [101], "Volume": [1000],
    })
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert outcome.valid_rows == 1
    assert pd.Timestamp("2026-07-14") in outcome.frame.index


def test_never_fabricates_missing_dates(tmp_path):
    # Two dates with a gap (07-15 absent) -> output must NOT contain 07-15.
    csv = _write_csv(tmp_path / "comi.csv", {
        "time": ["2026-07-14", "2026-07-16"],
        "open": [100, 102], "high": [102, 104],
        "low": [99, 101], "close": [101, 103], "Volume": [1000, 3000],
    })
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    outcome = provider.import_csv(csv, "COMI.CA")
    assert len(outcome.frame) == 2
    assert pd.Timestamp("2026-07-15") not in outcome.frame.index


def test_load_history_requires_explicit_import(tmp_path):
    provider = TradingViewCsvProvider(now=lambda: DURING_OPEN)
    with pytest.raises(NotImplementedError):
        provider.load_history("COMI.CA", "10y", "1d")
