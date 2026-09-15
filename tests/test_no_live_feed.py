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

def minutes(session):
    return pd.DataFrame({"SessionDate": [session, session], "Minute": ["10:00", "10:01"],
                         "Sector": ["Banks", "Banks"], "Turnover": [1.0, 2.0]})


def test_a_past_sessions_minutes_are_not_shown_as_today(monkeypatch):
    from dashboard import sector_flow as page

    shown = []
    monkeypatch.setattr(page, "section_header", lambda *a, **k: None)
    monkeypatch.setattr(page, "_intraday", lambda *a, **k: minutes("2026-09-10"))
    monkeypatch.setattr(page, "unavailable_state",
                        lambda title, message, **k: shown.append((title, message)))
    monkeypatch.setattr(page, "forecast_rest_of_day",
                        lambda *a, **k: pytest.fail("a past session reached the live forecast"))
    page._intraday_section(pd.DataFrame())
    assert shown, "nothing told the reader there is no data for today"
    title, message = shown[-1]
    assert "today" in title.lower()
    assert "2026-09-10" in message


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
