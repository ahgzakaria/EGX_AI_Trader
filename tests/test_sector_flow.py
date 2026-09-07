"""Tests for the sector map build and the per-sector liquidity history."""

from pathlib import Path
from types import SimpleNamespace
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


def universe_record(code, isin, name="Example"):
    return SimpleNamespace(
        canonical_symbol=code, engine_symbol=f"{code}.CA",
        isin=isin, company_name=name,
    )


def test_reconciliation_separates_non_equity_from_missing():
    from scripts.build_sector_map import build_rows

    universe = [
        universe_record("COMI", "EGS60121C018"),
        universe_record("EGX30ETF", ""),
        universe_record("ESRS", "EGS27101C012"),
    ]
    by_isin = {"EGS60121C018": {"Sector": "Banks", "MarketCapEGP": 1}}
    excluded = {"EGX30ETF": "Excluded - non-equity instrument"}

    mapped, unmapped = build_rows(universe, by_isin, {}, excluded)
    assert list(mapped["Ticker"]) == ["COMI.CA"]

    reasons = dict(zip(unmapped["Ticker"], unmapped["Reason"]))
    assert reasons["EGX30ETF.CA"] == "NON_EQUITY_INSTRUMENT"
    assert reasons["ESRS.CA"] == "NOT_IN_WORKBOOK"


def test_isin_wins_when_ticker_points_at_a_different_company():
    """EGX reuses tickers, so a ticker collision must not override the ISIN."""

    from scripts.build_sector_map import build_rows

    universe = [universe_record("ABCD", "EGS00000C001")]
    by_isin = {"EGS00000C001": {"Sector": "Banks", "MarketCapEGP": 10}}
    by_code = {"ABCD": {"Sector": "Real Estate", "MarketCapEGP": 20}}

    mapped, _ = build_rows(universe, by_isin, by_code, {})
    assert mapped["Sector"].iloc[0] == "Banks"
    assert mapped["MatchedBy"].iloc[0] == "ISIN"


def test_ticker_match_is_recorded_when_the_isin_is_absent():
    from scripts.build_sector_map import build_rows

    universe = [universe_record("ABCD", "")]
    by_code = {"ABCD": {"Sector": "Real Estate", "MarketCapEGP": 20}}

    mapped, unmapped = build_rows(universe, {}, by_code, {})
    assert unmapped.empty
    assert mapped["MatchedBy"].iloc[0] == "TICKER"
    assert mapped["Sector"].iloc[0] == "Real Estate"


def test_shipped_reconciliation_report_covers_every_unmapped_symbol():
    """The map plus the report must together account for the whole universe."""

    from core.universe import active_engine_symbols

    report = Path("reports/sector_map_reconciliation.csv")
    assert report.is_file(), "run scripts/build_sector_map.py"
    unmapped = set(pd.read_csv(report)["Ticker"])
    mapped = set(load_sector_map("data/sectors.csv"))
    universe = set(active_engine_symbols())
    assert universe - mapped == unmapped
    assert not (unmapped & mapped)


def test_sector_map_is_built_from_the_operational_universe():
    """Guards against rebuilding the map from the retired 265-symbol list."""

    from core.universe import active_engine_symbols

    mapped = set(load_sector_map("data/sectors.csv"))
    universe = set(active_engine_symbols())
    # Every classified ticker must be an operational symbol, never an archived one.
    assert mapped.issubset(universe)
    assert len(mapped) / len(universe) > 0.9


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


def test_no_two_mapped_tickers_share_an_isin():
    """A renamed company must not put its turnover into a sector twice."""

    mapped = pd.read_csv("data/sectors.csv")
    isins = mapped["ISIN"].dropna().astype(str).str.strip().str.upper()
    isins = isins[(isins != "") & (isins != "NAN")]
    duplicated = sorted(isins[isins.duplicated()].unique())
    assert not duplicated, f"ISINs classified more than once: {duplicated}"


def test_superseded_tickers_are_reported_not_silently_dropped():
    from scripts.build_sector_map import deduplicate_by_isin

    live = SimpleNamespace(canonical_symbol="ASPI", engine_symbol="ASPI.CA",
                           isin="EGS691L1C018", company_name="Aspire",
                           rubix_mapping_status="VERIFIED_FEED_OBSERVED")
    retired = SimpleNamespace(canonical_symbol="PIOH", engine_symbol="PIOH.CA",
                              isin="EGS691L1C018", company_name="Pioneers",
                              rubix_mapping_status="UNVERIFIED_NO_FEED_OBSERVATION")

    for order in ([retired, live], [live, retired]):
        kept, dropped = deduplicate_by_isin(order)
        assert [record.canonical_symbol for record in kept] == ["ASPI"]
        assert [record.canonical_symbol for record in dropped] == ["PIOH"]


def test_symbols_without_an_isin_are_all_kept():
    from scripts.build_sector_map import deduplicate_by_isin

    records = [
        SimpleNamespace(canonical_symbol=code, engine_symbol=f"{code}.CA", isin="",
                        company_name=code, rubix_mapping_status="")
        for code in ("AAA", "BBB")
    ]
    kept, dropped = deduplicate_by_isin(records)
    assert len(kept) == 2 and not dropped


def test_turnover_aggregation_does_not_inherit_the_indicator_bar_minimum():
    """250 bars is what indicators need, not what turnover needs."""

    from sector_flow.builder import TURNOVER_MIN_BARS

    assert TURNOVER_MIN_BARS < 250
    assert TURNOVER_MIN_BARS >= 2  # pct_change needs a predecessor


# --- a deal is not a flow ----------------------------------------------------
#
# On 2026-09-07 a single 5.14 billion transaction in EFIC -- 502x its own
# sixty-session median and 27% of the whole market's turnover -- put Basic
# Resources at 32.21% of the market and first. Without it the sector is not in
# the top four and Real Estate leads at 16.35%. The trade was real and
# on-market, so the turnover is not wrong. It is simply not liquidity anyone
# could have joined, and this page exists to answer where to trade.

def _series(ticker, sector, values, start="2020-01-01"):
    import pandas as pd

    dates = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({
        "SessionDate": dates, "Sector": sector, "Ticker": ticker,
        "Turnover": values, "Return": 0.0,
    })


def test_one_symbol_dominating_its_sector_is_flagged():
    import pandas as pd

    from sector_flow.history import _concentration

    quiet = [1_000_000.0] * 60
    tidy = pd.concat([
        _series("EFIC", "Basic Resources", quiet + [5_138_710_016.0]),
        _series("MFPC", "Basic Resources", quiet + [313_287_328.0]),
    ], ignore_index=True)

    last = _concentration(tidy).sort_values("SessionDate").iloc[-1]
    assert last["TopTicker"] == "EFIC"
    assert last["TopTickerTurnoverMultiple"] > 500
    assert last["TopTickerShare"] > 0.9
    assert bool(last["ConcentratedSession"]) is True


def test_a_busy_but_shared_session_is_not_flagged():
    """Concentration, not activity. A sector all of which traded is a flow."""

    import pandas as pd

    from sector_flow.history import _concentration

    quiet = [1_000_000.0] * 60
    busy = 80_000_000.0                      # 80x for every symbol, none dominant
    tidy = pd.concat([
        _series("AAAA", "Basic Resources", quiet + [busy]),
        _series("BBBB", "Basic Resources", quiet + [busy]),
        _series("CCCC", "Basic Resources", quiet + [busy]),
    ], ignore_index=True)

    last = _concentration(tidy).sort_values("SessionDate").iloc[-1]
    assert last["TopTickerTurnoverMultiple"] > 50, "each symbol is well above its own history"
    assert last["TopTickerShare"] < 0.70
    assert bool(last["ConcentratedSession"]) is False


def test_a_dominant_symbol_at_its_normal_size_is_not_flagged():
    """A sector that is always one large name is not a deal."""

    import pandas as pd

    from sector_flow.history import _concentration

    tidy = pd.concat([
        _series("BIG", "Banks", [900_000_000.0] * 61),
        _series("SMALL", "Banks", [10_000_000.0] * 61),
    ], ignore_index=True)

    last = _concentration(tidy).sort_values("SessionDate").iloc[-1]
    assert last["TopTickerShare"] > 0.70, "it does dominate"
    assert last["TopTickerTurnoverMultiple"] < 2, "but it always has"
    assert bool(last["ConcentratedSession"]) is False


def test_the_flagged_session_is_kept_not_deleted():
    """Deleting it would make the history claim a day that did not happen."""

    import pandas as pd

    from sector_flow.history import aggregate_sectors

    quiet = [1_000_000.0] * 60
    frames = {}
    for ticker, tail in (("EFIC", 5_138_710_016.0), ("MFPC", 313_287_328.0)):
        dates = pd.date_range("2020-01-01", periods=61, freq="D")
        values = quiet + [tail]
        frames[ticker] = pd.DataFrame(
            {"High": 10.0, "Low": 10.0, "Close": 10.0,
             "Volume": [v / 10.0 for v in values], "Turnover": values},
            index=dates)

    result = aggregate_sectors(frames, {"EFIC": "Basic Resources",
                                        "MFPC": "Basic Resources"})
    last = result.sort_values("SessionDate").iloc[-1]
    assert last["Turnover"] == pytest.approx(5_138_710_016.0 + 313_287_328.0), (
        "the turnover happened and stays in the total")
    assert bool(last["ConcentratedSession"]) is True
