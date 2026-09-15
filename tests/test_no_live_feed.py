"""No page presents Rubix as a live price source after it was retired.

The Rubix live feed was retired on 2026-09-10 and nothing has written its
database since. Pages kept presenting it anyway: green "Rubix" as the Daily
Dashboard's price source, "Live quote overlay: Rubix Partially Available" in the
scan banner, "Live provider: Rubix" on System Health, a "Rubix Intraday" chart
tab, and a Sector Liquidity "Rest of today" forecast built from that Thursday's
opening window. The fix was not to judge those quotes fresh or stale -- there
is no live source -- so these pin that every page says what is true.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pandas as pd
import pytest

from core import live_feed


# --- the one place that says it --------------------------------------------------

def test_the_retirement_date_and_wording_are_stated_once():
    assert live_feed.RUBIX_RETIRED_ON == "2026-09-10"
    assert "2026-09-10" in live_feed.NO_LIVE_FEED_AR
    assert "2026-09-10" in live_feed.NO_LIVE_FEED_EN
    assert "2026-09-10" in live_feed.LIVE_QUOTES_STATUS_EN
    assert "auction" in live_feed.RETIREMENT_NOTE


@pytest.mark.parametrize("values, label", [
    (["eodhd"], "EODHD"),
    (["eodhd_plus_mubasher", "eodhd_plus_mubasher"], "EODHD + Mubasher"),
    (["local_plus_mubasher", "eodhd"], "EODHD · Local + Mubasher"),
    (["something_new"], "something_new"),     # unknown is shown, not guessed
    ([None, "", "unknown"], "—"),
    ([], "—"),
])
def test_the_history_source_label(values, label):
    assert live_feed.history_source_label(values) == label


# --- the Daily Dashboard ------------------------------------------------------------

def test_the_scan_banner_no_longer_grades_a_dead_overlay():
    from dashboard import home

    source = inspect.getsource(home._render_scan_status)
    assert "Live quote overlay" not in source
    assert "LIVE_QUOTES_STATUS_EN" in source
    assert '"Rubix Fresh"' not in source


# --- Sector Liquidity -----------------------------------------------------------------

def test_sector_liquidity_opens_no_minute_store(monkeypatch):
    from dashboard import sector_flow as page
    import sector_flow.intraday as intraday

    shown = []
    monkeypatch.setattr(page, "section_header", lambda *a, **k: None)
    monkeypatch.setattr(intraday, "load_minute_turnover", refuse)
    monkeypatch.setattr(page, "unavailable_state",
                        lambda title, message, **k: shown.append((title, message)))
    page._intraday_section(pd.DataFrame())
    assert shown, "nothing told the reader there is no data for today"
    title, message = shown[-1]
    assert "today" in title.lower()
    assert "2026-09-10" in message
    assert "load_minute_turnover" not in inspect.getsource(page)


# --- Stock Details --------------------------------------------------------------------

class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args))


def test_the_status_panel_never_labels_a_quote_live(monkeypatch):
    from dashboard import freshness_panel

    fake = Recorder()
    monkeypatch.setattr(freshness_panel, "st", fake)
    context = SimpleNamespace(
        may_label_rubix_live=True,                    # even if a stale flag said so
        rubix_overlay_applied=True,
        rubix_message=lambda: "RUBIX LIVE CURRENT",
        overlay_denial_message=lambda: "")
    freshness_panel.render_rubix_status(context)
    kinds = [name for name, _ in fake.calls]
    assert "success" not in kinds
    text = " ".join(str(args) for _, args in fake.calls)
    assert "2026-09-10" in text and "completed session close" in text


def test_the_old_quote_is_named_as_a_retired_quote():
    from dashboard import stock_details

    source = inspect.getsource(stock_details)
    assert '"Rubix Overlay Price"' not in source
    assert "Last Rubix quote (retired)" in source


# --- AI Analysis and System Health ------------------------------------------------------

def test_the_ai_analysis_chart_does_not_advertise_rubix_intraday():
    from dashboard import ai_stock_analysis

    source = inspect.getsource(ai_stock_analysis._chart_section)
    assert "Rubix Intraday" not in source
    assert "Typed Daily + Rubix" not in source
    assert "2026-09-10" in source


def test_system_health_says_there_is_no_live_provider():
    from pathlib import Path

    source = Path("dashboard/system_health.py").read_text(encoding="utf-8")
    assert 'metric("Live provider", "Rubix")' not in source
    assert "LIVE QUOTES · none" in source
    assert "Rubix Daily Bridge sessions" not in source
    assert "_rubix_latest_event" not in source
    assert "قاعدة Rubix" not in source


# --- the scan reads no quote --------------------------------------------------------------
#
# Removing the wording left the scan still opening the Rubix database and
# attaching a days-old quote to every row. These pin that it reads nothing.

def refuse(*args, **kwargs):
    pytest.fail("the retired Rubix database was read")


def test_a_scan_context_opens_no_rubix_database(monkeypatch):
    from core import data_provider, scan_context
    from providers.rubix_sqlite_provider import RubixSQLiteProvider

    monkeypatch.setattr(data_provider, "_provider_instances", refuse)
    monkeypatch.setattr(RubixSQLiteProvider, "load_latest_quote_overlays", refuse)
    context = scan_context.build_scan_context(["COMI.CA"], eodhd_client=object())
    assert not hasattr(context, "rubix_overlays")
    assert "rubix_provider" not in inspect.signature(scan_context.build_scan_context).parameters


def test_the_daily_loader_attaches_no_quote(monkeypatch):
    from core import data_provider, research_router
    from providers.rubix_sqlite_provider import RubixSQLiteProvider

    monkeypatch.setattr(RubixSQLiteProvider, "quote_overlay", refuse)
    monkeypatch.setattr(RubixSQLiteProvider, "load_latest_quote_overlays", refuse)
    index = pd.bdate_range("2025-01-01", periods=300)
    history = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5,
                            "Volume": 1000.0}, index=index)
    history.attrs["market_data"] = {"provider": "eodhd"}
    monkeypatch.setattr(research_router, "get_current_research_history",
                        lambda *a, **k: history)
    frame = data_provider._load_swing_daily_history(
        "COMI.CA", "2y", "1d", 250, False, None, None, "scanner")
    md = frame.attrs["market_data"]
    assert md["live_quote_available"] is False
    assert md["live_quote_last"] is None
    assert md["live_quote_status"] == live_feed.RETIRED_QUOTE_STATUS
    assert md["live_quote_provider"] == "unavailable"
    assert "2026-09-10" in md["fallback_reason"]


def test_a_scan_has_no_loading_rubix_stage():
    from core import scan_job_manager, scanner

    assert "RUBIX_PREPARING" not in inspect.getsource(scanner.scan_symbols)
    assert "RUBIX_READY" not in inspect.getsource(scanner.scan_symbols)
    assert "Loading Rubix" not in inspect.getsource(scan_job_manager)


# --- the other pages read no quote either --------------------------------------------------

def test_provider_health_does_not_open_the_rubix_database(monkeypatch):
    from config.settings_manager import settings
    from core import data_provider

    monkeypatch.setitem(settings.data, "dashboard_provider", "rubix")
    monkeypatch.setattr(data_provider, "_provider_instances", refuse)
    health = data_provider.provider_health("dashboard")
    assert health["status"] == live_feed.RETIRED_QUOTE_STATUS
    assert health["database_status"] == "NOT_READ"


def test_the_scan_audit_evidence_reads_no_quote(monkeypatch):
    from core import data_provider
    from providers.rubix_sqlite_provider import RubixSQLiteProvider

    class Cache:
        def inspect_cached(self, *args):
            return {"cached": True}

    monkeypatch.setattr(RubixSQLiteProvider, "symbol_availability", refuse)
    monkeypatch.setattr(data_provider, "_provider_instances",
                        lambda: {"local_cache": Cache(), "rubix": RubixSQLiteProvider()})
    evidence = data_provider.symbol_data_coverage("COMI.CA")
    assert evidence["cached"] is True
    assert evidence["rubix_quote_available"] is False
    assert evidence["rubix_status"] == live_feed.RETIRED_QUOTE_STATUS


def test_the_portfolio_and_ai_analysis_read_no_quote(monkeypatch):
    from core import ai_stock_analysis_service as ai
    from holdings import assistant
    from providers.rubix_sqlite_provider import RubixSQLiteProvider
    import sector_flow.intraday as intraday

    for name in ("quote_overlay", "load_latest_quote_overlays", "load_history"):
        monkeypatch.setattr(RubixSQLiteProvider, name, refuse)
    monkeypatch.setattr(intraday, "load_minute_turnover", refuse)
    assert ai._default_live_quote("COMI.CA") is None
    assert ai._default_intraday("COMI.CA") is None
    assert assistant.default_quote_loader("COMI") is None
    assert assistant.default_quote_overlays(("COMI", "SWDY")) == {}
    assert assistant.default_sector_intraday() == {}
    assert not hasattr(ai, "_rubix_provider")


def test_system_health_opens_no_rubix_file(monkeypatch):
    from services import system_health

    opened = []
    real = system_health._sqlite_health
    monkeypatch.setattr(system_health, "_sqlite_health",
                        lambda path, **k: opened.append(str(path)) or real(path, **k))
    monkeypatch.setattr(system_health, "_experiment_health",
                        lambda: {"replay_ready_runs": 0, "archived_dataset_coverage_pct": 0})
    health = system_health.collect_system_health()
    assert opened, "no database was checked at all"
    assert not any("rubix" in path.lower() for path in opened)
    assert "rubix_database" not in health and "rubix_supervisor" not in health
    assert health["provider"]["fallback_active"] is False
    assert "Yahoo" not in health["safety"]["message"]
