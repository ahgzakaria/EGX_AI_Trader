"""The frozen Mubasher record serves what was frozen, or nothing.

Pins the three properties a backtest input must have: the bytes cannot change
unnoticed, the export's placeholder columns are never served as data, and
reading never writes. The freeze script's refusals are pinned beside them.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core import frozen_mubasher_store as store


def write_export(directory, ticker, rows):
    """An Export History CSV, newest first, with Open copied from High as the terminal does."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ticker}.csv"
    lines = [store.EXPORT_HEADER]
    for date, high, low, close, volume, turnover in rows:
        lines.append(f"{ticker},{date},{high},{high},{low},{close},0,0,-1,{volume},{turnover}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


ROWS = [  # newest first, as exported
    ("2026-09-07", 12.0, 11.0, 11.5, 3000, 34500.0),
    ("2026-09-06", 11.2, 10.5, 11.0, 2000, 22000.0),
    ("2026-09-03", 10.8, 10.0, 10.2, 1000, 10200.0),
]


def freeze_into(root, ticker="ABCD", rows=ROWS, fmt=store.EXPORT_FORMAT):
    path = write_export(root / "export", ticker, rows)
    manifest = {"frozen_at": "2026-09-11T00:00:00+00:00", "symbols": {ticker: {
        "symbol": ticker, "file": f"export/{path.name}", "format": fmt,
        "sha256": store.sha256_file(path), "source": "test export",
        "mubasher_ticker": ticker}}}
    (root / store.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_the_frame_is_oldest_first_with_the_exported_prices(tmp_path):
    freeze_into(tmp_path)
    frame = store.load_frozen("ABCD", root=tmp_path)
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-09-03", "2026-09-06", "2026-09-07"]
    assert list(frame["Close"]) == [10.2, 11.0, 11.5]
    assert list(frame["High"]) == [10.8, 11.2, 12.0]
    assert list(frame["Volume"]) == [1000, 2000, 3000]
    assert list(frame.columns) == store.COLUMNS


def test_the_placeholder_open_is_never_served(tmp_path):
    """The export's Open is the high. Served, every bar would open at its top."""
    freeze_into(tmp_path)
    frame = store.load_frozen("ABCD", root=tmp_path)
    assert np.isnan(frame["Open"].iloc[0])
    assert list(frame["Open"].iloc[1:]) == list(frame["Close"].iloc[:-1])
    assert not (frame["Open"].iloc[1:] == frame["High"].iloc[1:]).any()
    assert frame.attrs["market_data"]["open_policy"] == store.OPEN_POLICY


def test_adj_close_is_the_close_because_nothing_is_dividend_adjusted(tmp_path):
    freeze_into(tmp_path)
    frame = store.load_frozen("ABCD", root=tmp_path)
    assert (frame["Adj Close"] == frame["Close"]).all()
    assert frame.attrs["market_data"]["price_adjustment"] == store.PRICE_ADJUSTMENT


def test_a_changed_byte_is_refused(tmp_path):
    path = freeze_into(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("11.5", "11.6"), encoding="utf-8")
    with pytest.raises(store.FrozenStoreTampered):
        store.load_frozen("ABCD", root=tmp_path)


def test_a_missing_file_is_refused_not_skipped(tmp_path):
    path = freeze_into(tmp_path)
    path.unlink()
    with pytest.raises(store.FrozenStoreTampered):
        store.load_frozen("ABCD", root=tmp_path)


def test_a_symbol_the_store_does_not_hold_is_none(tmp_path):
    freeze_into(tmp_path)
    assert store.load_frozen("ZZZZ", root=tmp_path) is None
    assert store.load_frozen("ZZZZ", root=tmp_path / "nowhere") is None


def test_an_engine_suffix_reads_the_same_symbol(tmp_path):
    freeze_into(tmp_path)
    assert store.load_frozen("abcd.CA", root=tmp_path) is not None


def test_the_history_db_format_loads(tmp_path):
    (tmp_path / "history_db").mkdir()
    path = tmp_path / "history_db" / "WXYZ.csv"
    path.write_text(store.HISTORY_DB_HEADER + "\n2026-09-06,5.5,5.0,5.2,100,520.0\n"
                    "2026-09-07,5.6,5.1,5.4,200,1080.0\n", encoding="utf-8")
    manifest = {"symbols": {"WXYZ": {"symbol": "WXYZ", "file": "history_db/WXYZ.csv",
                                     "format": store.HISTORY_DB_FORMAT,
                                     "sha256": store.sha256_file(path),
                                     "mubasher_ticker": "WXYZ2"}}}
    (tmp_path / store.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    frame = store.load_frozen("WXYZ", root=tmp_path)
    assert list(frame["Close"]) == [5.2, 5.4]
    assert frame["Open"].iloc[1] == 5.2
    assert frame.attrs["market_data"]["mubasher_ticker"] == "WXYZ2"


def test_an_unknown_format_is_refused(tmp_path):
    freeze_into(tmp_path, fmt="something_else")
    with pytest.raises(store.FrozenStoreTampered):
        store.load_frozen("ABCD", root=tmp_path)


def test_the_manifest_is_parsed_once_until_the_file_changes(tmp_path, monkeypatch):
    """A symbol the store lacks is asked for on every bar; each ask must be cheap."""
    freeze_into(tmp_path)
    parses = []
    real_loads = store.json.loads
    monkeypatch.setattr(store.json, "loads",
                        lambda text: parses.append(1) or real_loads(text))
    for _ in range(50):
        assert store.load_frozen("^CASE30", root=tmp_path) is None
    assert len(parses) == 1

    manifest_path = tmp_path / store.MANIFEST_NAME
    # The unpatched parser: this read is the test's own, not the store's.
    rewritten = real_loads(manifest_path.read_text(encoding="utf-8"))
    rewritten["frozen_at"] = "2026-09-12T00:00:00+00:00 (rewritten)"
    manifest_path.write_text(json.dumps(rewritten), encoding="utf-8")
    assert store.read_manifest(root=tmp_path)["frozen_at"].endswith("(rewritten)")
    assert len(parses) == 2


def test_reading_writes_nothing(tmp_path):
    freeze_into(tmp_path)
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in tmp_path.rglob("*") if p.is_file()}
    store.load_frozen("ABCD", root=tmp_path)
    store.frozen_symbols(root=tmp_path)
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


# --- the freeze script's refusals --------------------------------------------

def test_the_script_refuses_to_overwrite_a_store(tmp_path):
    from scripts import freeze_mubasher_export as freeze

    dest = tmp_path / "store"
    freeze_into(dest)
    source = tmp_path / "source"
    write_export(source, "ABCD", ROWS)
    assert freeze.main(["--source", str(source), "--dest", str(dest)]) == 2
    assert store.load_frozen("ABCD", root=dest) is not None


def test_a_wrong_header_is_refused(tmp_path):
    from scripts import freeze_mubasher_export as freeze

    path = tmp_path / "ABCD.csv"
    path.write_text("Symbol,Date,Close\nABCD,2026-09-07,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected header"):
        freeze.read_checked(path)


def test_a_file_naming_another_ticker_is_refused(tmp_path):
    from scripts import freeze_mubasher_export as freeze

    path = write_export(tmp_path, "ABCD", ROWS)
    renamed = path.with_name("EFGH.csv")
    path.rename(renamed)
    with pytest.raises(ValueError, match="Symbol column"):
        freeze.read_checked(renamed)


def test_a_history_db_copy_written_from_numpy_values_reads_back_exactly(tmp_path):
    """The first freeze wrote ``np.float64(12.5)`` into the file and served no bars."""
    from scripts import freeze_mubasher_export as freeze

    index = pd.DatetimeIndex(pd.to_datetime(["2026-09-06", "2026-09-07"]), name="Date")
    history = pd.DataFrame({
        "High": np.array([12.5, 13.25]), "Low": np.array([11.0, 12.1]),
        "Close": np.array([12.0, 13.123]), "Volume": np.array([1500.0, 2400.0]),
        "Turnover": np.array([18000.5, 31495.2]),
    }, index=index)
    path = tmp_path / "WXYZ.csv"
    freeze.write_history_db_copy(history, path)
    assert "np.float64" not in path.read_text(encoding="utf-8")
    assert freeze.round_trips(history, path)
    served = store.read_history_db_copy(path)
    assert list(served["Close"]) == [12.0, 13.123]


def test_round_trip_detects_a_file_that_does_not_read_back(tmp_path):
    from scripts import freeze_mubasher_export as freeze

    index = pd.DatetimeIndex(pd.to_datetime(["2026-09-07"]), name="Date")
    history = pd.DataFrame({"High": [1.0], "Low": [1.0], "Close": [1.0],
                            "Volume": [1.0], "Turnover": [1.0]}, index=index)
    path = tmp_path / "BAD.csv"
    path.write_text(store.HISTORY_DB_HEADER + "\n2026-09-07,np.float64(1.0),1,1,1,1\n",
                    encoding="utf-8")
    assert not freeze.round_trips(history, path)


def test_verification_finds_a_single_differing_close(tmp_path):
    from scripts import freeze_mubasher_export as freeze

    export = store.read_export(write_export(tmp_path, "ABCD", ROWS))
    history = export.copy()
    assert freeze.verify_against_history(export, history) == (3, {})
    history.loc[history.index[0], "Close"] += 0.01
    shared, differing = freeze.verify_against_history(export, history)
    assert shared == 3 and differing == {"Close": 1}
