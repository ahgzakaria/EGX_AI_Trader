"""Tables show COMI, not COMI.CA -- and nothing but the display changes.

The engine keys tickers with ``.CA`` and almost every report and store the
dashboard reads carries it, so the suffix is removed where every table passes
through Streamlit. These pin that the rule is narrow, that the page's own data
is never mutated, and that the install reaches every table and happens once.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from dashboard import formatting
from dashboard.formatting import display_ticker, with_display_tickers


@pytest.mark.parametrize("given, shown", [
    ("COMI.CA", "COMI"),
    ("comi.ca", "comi"),
    ("SWDY.EGX", "SWDY"),
    ("COMI", "COMI"),
    ("NULL", "NULL"),
    ("COMI — Commercial International Bank", "COMI — Commercial International Bank"),
    (".CA", ".CA"),
    (None, None),
    (12.5, 12.5),
])
def test_only_a_trailing_exchange_suffix_is_removed(given, shown):
    assert display_ticker(given) == shown


def test_ticker_columns_are_shown_bare_and_the_pages_frame_is_untouched():
    frame = pd.DataFrame({"Symbol": ["COMI.CA", "SWDY.CA"],
                          "Company": ["Bank.CA", "Cables"],
                          "NetProfit": [10.0, -2.0]})
    shown = with_display_tickers(frame)
    assert list(shown["Symbol"]) == ["COMI", "SWDY"]
    assert list(shown["Company"]) == ["Bank.CA", "Cables"]      # not a ticker column
    assert list(frame["Symbol"]) == ["COMI.CA", "SWDY.CA"]       # the original stays
    assert shown is not frame


@pytest.mark.parametrize("column", ["symbol", "Ticker", "ticker", "engine_symbol",
                                    formatting.SYMBOL_COLUMN])
def test_every_ticker_column_spelling_is_recognised(column):
    shown = with_display_tickers(pd.DataFrame({column: ["ABUK.CA"]}))
    assert shown[column].iloc[0] == "ABUK"


def test_a_frame_without_tickers_is_passed_through_as_is():
    frame = pd.DataFrame({"Metric": ["Trades"], "Value": [953]})
    assert with_display_tickers(frame) is frame


def test_a_ticker_index_is_shown_bare():
    frame = pd.DataFrame({"Trades": [4]}, index=pd.Index(["MOIL.CA"], name="Symbol"))
    assert list(with_display_tickers(frame).index) == ["MOIL"]


def test_rows_given_as_dicts_are_shown_bare():
    rows = [{"Symbol": "AMII.CA", "Error": "not enough history"}]
    shown = with_display_tickers(rows)
    assert list(shown["Symbol"]) == ["AMII"]
    assert rows[0]["Symbol"] == "AMII.CA"


def test_a_styled_table_keeps_its_styling_and_shows_bare_tickers():
    frame = pd.DataFrame({"Symbol": ["COMI.CA"], "Score": [90]})
    styler = frame.style.apply(lambda column: ["color: red"] * len(column), subset=["Score"])
    shown = with_display_tickers(styler)
    html = shown.to_html()
    assert ">COMI<" in html and "COMI.CA" not in html
    assert "color: red" in html
    assert styler.data["Symbol"].iloc[0] == "COMI.CA"


def test_anything_else_is_returned_as_given():
    for data in (None, "text", [("a", 1)], {"Symbol": "COMI.CA"}):
        assert with_display_tickers(data) is data


def test_install_reaches_every_table_and_happens_once(monkeypatch):
    import streamlit as st
    from streamlit.delta_generator import DeltaGenerator

    # The install re-binds `st.dataframe` and `st.table` to the main container.
    # Registered here so they are restored with the class methods; the first
    # version left them bound to this test's fakes, and three later tests that
    # render pages found no table at all.
    monkeypatch.setattr(st, "dataframe", st.dataframe)
    monkeypatch.setattr(st, "table", st.table)
    received = {}

    def fake_dataframe(self, data=None, *args, **kwargs):
        received["dataframe"] = data

    def fake_table(self, data=None, *args, **kwargs):
        received["table"] = data

    monkeypatch.setattr(DeltaGenerator, "dataframe", fake_dataframe)
    monkeypatch.setattr(DeltaGenerator, "table", fake_table)
    formatting.install_ticker_display()
    wrapped = DeltaGenerator.dataframe
    formatting.install_ticker_display()
    assert DeltaGenerator.dataframe is wrapped                   # not wrapped twice

    frame = pd.DataFrame({"Ticker": ["TMGH.CA"]})
    DeltaGenerator.dataframe(None, frame, hide_index=True)
    DeltaGenerator.table(None, data=frame)
    assert list(received["dataframe"]["Ticker"]) == ["TMGH"]
    assert list(received["table"]["Ticker"]) == ["TMGH"]
    assert list(frame["Ticker"]) == ["TMGH.CA"]


def test_a_real_streamlit_render_shows_bare_tickers_in_containers_too():
    """Through Streamlit's own renderer, from `st` and from a column alike."""
    from streamlit.testing.v1 import AppTest

    def page():
        import pandas as pd
        import streamlit as st

        from dashboard.formatting import install_ticker_display

        install_ticker_display()
        st.dataframe(pd.DataFrame({"Symbol": ["COMI.CA"], "Trades": [4]}))
        left, _ = st.columns(2)
        left.table(pd.DataFrame({"Ticker": ["SWDY.CA"], "Name": ["Elsewedy.CA"]}))

    app = AppTest.from_function(page).run()
    assert not app.exception
    assert list(app.dataframe[0].value["Symbol"]) == ["COMI"]
    table = app.table[0].value
    assert list(table["Ticker"]) == ["SWDY"]
    assert list(table["Name"]) == ["Elsewedy.CA"]


def test_the_app_installs_the_display_once_at_startup():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "install_ticker_display()" in source
    assert source.index("install_ticker_display()") < source.index("from dashboard.backtest_state")
