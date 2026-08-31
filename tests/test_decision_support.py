"""Regression and evidence tests for the additive Phase 9 advisory layer."""

from copy import deepcopy
from datetime import datetime, timezone
import ast
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from config.settings_manager import settings
from decision_support.database import DecisionSupportDatabase
from decision_support.edge_score import calculate_edge_score
from decision_support.market_health import calculate_market_health
from decision_support.quality import atr_feasibility, liquidity_metrics, relative_volume, spread_metrics
from decision_support.reporting import write_daily_report
from decision_support.sector_analysis import load_sector_map
from decision_support.service import DecisionSupportService


NOW = datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc)


def sample_frame():
    index = pd.date_range("2026-06-01", periods=25, freq="D")
    volume = [100_000.0] * 24 + [200_000.0]
    frame = pd.DataFrame({
        "Open": [99.0] * 25, "High": [102.0] * 25,
        "Low": [98.0] * 25, "Close": [100.0 + i * 0.1 for i in range(25)],
        "Volume": volume, "EMA20": [100.0] * 25, "EMA50": [99.0] * 25,
        "EMA200": [90.0] * 25, "RSI": [60.0] * 25, "MACD": [1.0] * 25,
        "ADX": [30.0] * 25, "ATR": [2.5] * 25, "OBV": range(25),
    }, index=index)
    frame.attrs["market_data"] = {
        "effective_provider": "rubix", "freshness": "FRESH",
        "received_timestamp": NOW.isoformat(),
    }
    return frame


def sample_result(ticker="COMI.CA"):
    return {
        "Ticker": ticker, "Signal": "BUY", "Rank": 7, "Score": 78,
        "Confidence": 84, "RR": 2.25, "Trend": 27, "Momentum": 14,
        "Volume": 8, "Candles": 12, "Breakout": 15,
        "Support": 99, "Resistance": 104, "BuyLow": 100, "BuyHigh": 101,
        "StopLoss": 98, "Target1": 103, "Target2": 105,
        "Reasons": "TRENDING | BREAKOUT", "AIProbability": 72,
        "DataSource": "rubix", "OperationalStatus": "LIVE_DATA_OK",
        "Data": sample_frame(),
    }


def create_rubix_db(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE quotes (
          ticker TEXT,last_price REAL,bid REAL,ask REAL,volume REAL,
          market_timestamp TEXT,received_at TEXT
        );
        """)
        connection.execute(
            "INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
            ("COMI", 102.4, 102.35, 102.45, 200000, NOW.isoformat(), NOW.isoformat()),
        )


def service_config(tmp_path):
    config = deepcopy(settings.get("decision_support"))
    config["database_path"] = str(tmp_path / "decision_support.db")
    config["sector_file"] = str(tmp_path / "missing_sectors.csv")
    return config


def test_edge_score_is_bounded_and_discloses_missing_evidence():
    result = calculate_edge_score({"trend_quality": 1.5, "momentum": 0.5})
    assert 0 <= result["edge_score"] <= 10
    assert result["evidence_completeness_percent"] < 100
    assert "liquidity" in result["missing_edge_factors"]


def test_relative_volume_uses_prior_bars_only():
    frame = pd.DataFrame({"Volume": [100.0] * 20 + [200.0]})
    assert relative_volume(frame, 20) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "bid,ask,label", [(100, 100.1, "Excellent"), (100, 100.2, "Good"), (100, 100.4, "Fair"), (100, 101, "Poor")]
)
def test_spread_quality_labels_are_deterministic(bid, ask, label):
    assert spread_metrics(bid, ask)["spread_quality"] == label


def test_liquidity_does_not_invent_unavailable_depth_or_trade_count():
    result = liquidity_metrics(100, 200_000, 2.0, 1.0)
    assert "depth" not in result["liquidity_components"]
    assert "trades" not in result["liquidity_components"]


def test_atr_feasibility_flags_low_two_percent_potential():
    result = atr_feasibility(1.0, 100.0, 2.0)
    assert result["atr_feasible"] is False
    assert result["atr_score"] == pytest.approx(0.5)


def test_market_health_uses_scan_rows_without_changing_them():
    row = sample_result()
    before = (row["Signal"], row["Score"], row["Rank"])
    health = calculate_market_health([row])
    assert 0 <= health["overall_market_score"] <= 10
    assert before == (row["Signal"], row["Score"], row["Rank"])


def test_service_ranks_additively_and_preserves_frozen_fields(tmp_path):
    rubix = tmp_path / "rubix.db"
    create_rubix_db(rubix)
    first = sample_result("COMI.CA")
    second = sample_result("SWDY.CA")
    second["Signal"], second["Score"], second["Rank"] = "WATCH", 60, 2
    originals = [(row["Signal"], row["Score"], row["Rank"]) for row in (first, second)]
    service = DecisionSupportService(service_config(tmp_path), rubix, now=lambda: NOW)
    service._historical_setup_scores = lambda: {}
    snapshot = service.analyze([first, second], persist=False, write_report=False)
    assert [row["EdgeRank"] for row in snapshot["rows"]] == [1, 2]
    assert originals == [(row["Signal"], row["Score"], row["Rank"]) for row in (first, second)]
    assert all("EdgeScore" not in row for row in (first, second))


def test_missing_quote_never_claims_rubix_quality_evidence(tmp_path):
    rubix = tmp_path / "rubix.db"
    create_rubix_db(rubix)
    row = sample_result("SWDY.CA")
    row["DataSource"] = "yahoo"
    row["Data"].attrs["market_data"] = {"effective_provider": "yahoo", "freshness": "FALLBACK"}
    service = DecisionSupportService(service_config(tmp_path), rubix, now=lambda: NOW)
    service._historical_setup_scores = lambda: {}
    result = service.analyze([row], persist=False, write_report=False)["rows"][0]
    assert result["QualityGate"] == "BLOCKED"
    assert result["Provider"] == "yahoo"
    assert any(value.startswith("DATA_FALLBACK") for value in result["Warnings"])


def test_recommendation_language_never_directs_an_order(tmp_path):
    rubix = tmp_path / "rubix.db"
    create_rubix_db(rubix)
    service = DecisionSupportService(service_config(tmp_path), rubix, now=lambda: NOW)
    service._historical_setup_scores = lambda: {}
    recommendation = service.analyze(
        [sample_result()], persist=False, write_report=False
    )["rows"][0]["Recommendation"].lower()
    assert "you should buy" not in recommendation
    assert "place order" not in recommendation


def test_missing_sector_file_is_explicitly_unknown(tmp_path):
    assert load_sector_map(tmp_path / "not-there.csv") == {}


def test_database_observations_are_immutable_and_pins_are_user_managed(tmp_path):
    database = DecisionSupportDatabase(tmp_path / "decision_support.db")
    row = {
        "Ticker": "COMI.CA", "ObservedAt": NOW.isoformat(), "EdgeScore": 8.2,
        "SpreadPercent": 0.1, "LiquidityScore": 0.8,
        "Provider": "rubix", "Freshness": "FRESH",
    }
    _, inserted = database.record_observation(row)
    assert inserted is True
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute("UPDATE observations SET edge_score=9")
    database.set_pinned("COMI.CA", True)
    assert database.rows("SELECT ticker FROM pinned_symbols") == [{"ticker": "COMI.CA"}]
    database.set_pinned("COMI.CA", False)
    assert database.rows("SELECT ticker FROM pinned_symbols") == []


def test_daily_report_is_reproducible_and_states_no_execution(tmp_path):
    rows = [{
        "EdgeRank": 1, "Ticker": "COMI.CA", "EdgeScore": 8.5,
        "Recommendation": "This opportunity meets the configured criteria.",
        "QualityGate": "MEETS_CRITERIA", "Warnings": [],
    }]
    market = {"market_bias": "CONSTRUCTIVE", "overall_market_score": 7, "advances": 2, "declines": 1, "advance_decline": 1, "volume_strength": 1.2}
    path = write_daily_report(rows, market, pd.DataFrame(), tmp_path, NOW)
    text = path.read_text(encoding="utf-8")
    assert "No order execution" in text
    assert "COMI.CA" in text


def test_phase9_settings_do_not_mutate_frozen_engine_sections():
    frozen = deepcopy({
        key: settings.data[key]
        for key in ("strategy", "backtest", "ai", "ai_risk_overlay", "scalping")
    })
    assert settings.get("decision_support")["enabled"] is True
    assert frozen == {
        key: settings.data[key]
        for key in ("strategy", "backtest", "ai", "ai_risk_overlay", "scalping")
    }


def test_decision_support_has_no_execution_network_or_strategy_dependency():
    forbidden_roots = {
        "requests", "websocket", "websockets", "selenium", "playwright",
        "strategy", "backtesting", "portfolio",
    }
    discovered = set()
    for path in Path("decision_support").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                discovered.update(name.name.split(".", 1)[0] for name in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                discovered.add(node.module.split(".", 1)[0])
    assert not discovered.intersection(forbidden_roots)


# --- the collector-quote read -------------------------------------------------
#
# This ran inside the Daily Dashboard's post-scan render as
# `SELECT ... FROM quotes ORDER BY received_at DESC` + fetchall(): a sort of 22
# million rows on an unindexed column, every row materialised as a Python dict,
# to keep about 240. On 2026-08-31 it held the page on a stale progress panel
# reading "Unknown - no dated candle" for over fifteen minutes and 3.8 GB while
# the scan behind it had already finished. Per-ticker through the index: 0.20s.

def _quotes_db(path, rows):
    """rows: (ticker, last_price, market_timestamp, received_at)."""
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE quotes (
          ticker TEXT,last_price REAL,bid REAL,ask REAL,volume REAL,
          market_timestamp TEXT,received_at TEXT
        );
        CREATE INDEX idx_quotes_ticker_time ON quotes (ticker, market_timestamp);
        """)
        for ticker, price, market, received in rows:
            connection.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                (ticker, price, price - 0.1, price + 0.1, 1000.0, market, received))
    return path


def _service(tmp_path, db):
    return DecisionSupportService(service_config(tmp_path), db, now=lambda: NOW)


def test_only_the_requested_symbols_are_read(tmp_path):
    db = _quotes_db(tmp_path / "r.db", [
        ("COMI", 100.0, "2026-08-31T11:00:00+00:00", "2026-08-31T11:00:00+00:00"),
        ("SWDY", 50.0, "2026-08-31T11:00:00+00:00", "2026-08-31T11:00:00+00:00"),
    ])
    quotes = _service(tmp_path, db)._latest_quotes(["COMI.CA"])
    assert set(quotes) == {"COMI.CA"}


def test_the_newest_row_by_exchange_time_wins(tmp_path):
    db = _quotes_db(tmp_path / "r.db", [
        ("COMI", 100.0, "2026-08-31T09:00:00+00:00", "2026-08-31T09:00:00+00:00"),
        ("COMI", 109.0, "2026-08-31T11:30:00+00:00", "2026-08-31T11:30:00+00:00"),
        ("COMI", 104.0, "2026-08-31T10:00:00+00:00", "2026-08-31T10:00:00+00:00"),
    ])
    quotes = _service(tmp_path, db)._latest_quotes(["COMI.CA"])
    assert quotes["COMI.CA"]["last_price"] == 109.0


def test_asking_for_nothing_reads_nothing(tmp_path):
    """There is no whole-table path left: an unbounded read of this table is
    never the right answer, and an empty request must not become one."""

    db = _quotes_db(tmp_path / "r.db", [
        ("COMI", 100.0, "2026-08-31T11:00:00+00:00", "2026-08-31T11:00:00+00:00")])
    assert _service(tmp_path, db)._latest_quotes([]) == {}
    assert _service(tmp_path, db)._latest_quotes(()) == {}


def test_a_symbol_with_no_quote_is_simply_absent(tmp_path):
    db = _quotes_db(tmp_path / "r.db", [
        ("COMI", 100.0, "2026-08-31T11:00:00+00:00", "2026-08-31T11:00:00+00:00")])
    quotes = _service(tmp_path, db)._latest_quotes(["COMI.CA", "NOPE.CA"])
    assert set(quotes) == {"COMI.CA"}


def test_a_missing_database_is_evidence_not_an_exception(tmp_path):
    service = _service(tmp_path, tmp_path / "absent.db")
    assert service._latest_quotes(["COMI.CA"]) == {}


def test_a_quotes_table_missing_columns_is_refused(tmp_path):
    path = tmp_path / "r.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE quotes (ticker TEXT, last_price REAL);")
        connection.execute("INSERT INTO quotes VALUES ('COMI', 100.0)")
    assert _service(tmp_path, path)._latest_quotes(["COMI.CA"]) == {}


def test_the_read_never_scans_the_whole_table(tmp_path):
    """A behavioural test cannot catch this: the old query returned the right
    quotes, it just took a quarter of an hour and 3.8 GB to do it."""

    import ast
    import inspect
    import textwrap

    function = ast.parse(textwrap.dedent(
        inspect.getsource(DecisionSupportService._latest_quotes))).body[0]
    docstring = ast.get_docstring(function, clean=False)
    queries = [node.value for node in ast.walk(function)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and node.value != docstring and "FROM quotes" in node.value]
    assert queries, "the read must query the quotes table"
    for query in queries:
        assert "WHERE ticker=?" in query
        assert "LIMIT 1" in query
        assert "ORDER BY received_at" not in query
