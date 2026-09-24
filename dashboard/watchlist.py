"""Watchlist workspace with the same frozen live scanner."""

import pandas as pd
import streamlit as st

from core.scanner import scan_symbols
from dashboard.freshness_panel import withheld_badge
from core.watchlist import Watchlist
from core.symbols import load_approved_symbol_options
from dashboard.formatting import (NAME_COLUMN, symbol_option_label,
                                  with_company_name_column)
from dashboard.ui import (badge_html, empty_state, page_header, quiet_state,
                          section_header)

#: The same sentence the Daily Dashboard carries under its own table. It was
#: missing here, on the page that sorted by the score and drew it as a progress
#: bar -- the strongest visual weight available -- so the one number this
#: project has measured NOT to rank by was promoted on one screen and
#: caveated on the other.
SCORE_CAVEAT = (
    "الترتيب ثابت وليس تقييمًا للجودة · **The order is stable, not a quality "
    "ranking.** Measured among the candidates that reach it, the score's rank "
    "correlation with the outcome is +0.15 in 2016–2022 and **+0.01 "
    "(p = 0.76)** in 2023–2026. Read the gates, not the position in the table."
)


def render_data_update_required(symbols, results):
    """Symbols the user still tracks whose data cannot support a decision.

    The scanner excludes a stale symbol before the decision engine, so it
    produces no row at all - which previously made it vanish from the user's
    own watchlist. It stays here instead, with its decision withheld rather
    than guessed, and it is never removed automatically.
    """

    freshness = {item.symbol: item for item in getattr(results, "freshness", []) or []}
    analysed = {str(row.get("Ticker")) for row in results or ()}
    excluded = [symbol for symbol in symbols if symbol not in analysed]
    if not excluded:
        return

    rows = []
    for symbol in excluded:
        item = freshness.get(symbol)
        rows.append({
            "Ticker": symbol,
            "ExpectedSession": getattr(item, "expected_session", "") or "—",
            "ActualSession": getattr(item, "actual_latest_session", "") or "—",
            "FreshnessStatus": (item.freshness_status.value if item else "UNAVAILABLE"),
            "SessionsBehind": getattr(item, "trading_sessions_behind", 0),
            "Decision": withheld_badge(),
            "ExclusionReason": getattr(item, "exclusion_reason", "")
                               or "no current daily data was loaded",
        })

    section_header("Data Update Required",
                   f"{len(rows)} tracked symbols without current daily data")
    st.warning(withheld_badge())
    st.dataframe(
        with_company_name_column(pd.DataFrame(rows), "Ticker"),
        hide_index=True, width="stretch",
    )
    st.caption(
        "These symbols remain on your watchlist. They carry no current "
        "BUY/WATCH/AVOID decision because their latest daily candle is not the "
        "expected completed session. Any previous decision shown elsewhere is "
        "historical and NOT CURRENT."
    )


def _manage(watchlist, symbols):
    """Add and remove names, on the page whose whole subject is the list.

    There was no way to do either from here: the empty state told the reader to
    go to another page and come back. A list you can only edit somewhere else
    is not a list you curate.
    """
    section_header("إدارة القائمة · The list",
                   f"{len(symbols)} of your own choosing" if symbols
                   else "Nothing tracked yet")
    add_col, remove_col = st.columns(2)
    with add_col:
        try:
            options = [s for s in load_approved_symbol_options()
                       if str(getattr(s, "canonical_symbol", s)) not in set(symbols)]
        except Exception:                       # noqa: BLE001 - never lose the page
            options = []
        chosen = st.selectbox(
            "أضف سهماً · Add a symbol", [None, *options],
            format_func=lambda s: "—" if s is None else symbol_option_label(s),
            key="watchlist_add",
        )
        if chosen is not None and st.button("أضف · Add", key="watchlist_add_go"):
            watchlist.add(str(getattr(chosen, "canonical_symbol", chosen)))
            st.rerun()
    with remove_col:
        drop = st.multiselect("أزل · Remove", symbols, key="watchlist_remove")
        if drop and st.button("أزل المحدد · Remove selected",
                              key="watchlist_remove_go"):
            for symbol in drop:
                watchlist.remove(symbol)
            st.rerun()


def show_watchlist():
    page_header(
        "Watchlist",
        "Focused monitoring for the symbols you care about most",
        icon="⭐",
        badge="MARKET",
    )
    watchlist = Watchlist()
    symbols = watchlist.load()
    _manage(watchlist, symbols)
    symbols = watchlist.load()
    if not symbols:
        empty_state(
            "Your watchlist is empty",
            "Add a symbol above, or open one from the Dashboard.",
            icon="☆",
        )
        return

    count_col, action_col = st.columns([1, 3])
    count_col.metric("Tracked Symbols", len(symbols))
    with action_col:
        if st.button(
            "🔍 Scan Watchlist",
            type="primary",
            width="stretch",
        ):
            with st.spinner("Scanning watchlist symbols..."):
                st.session_state.watchlist_results = scan_symbols(symbols)

    results = st.session_state.get("watchlist_results")
    if results is None:
        section_header("Symbols", "Ready for a focused scan")
        st.dataframe(
            with_company_name_column(pd.DataFrame({"Ticker": symbols}), "Ticker"),
            hide_index=True,
            width="stretch",
        )
        return
    if not results:
        # The scan ran. Nothing came back is an answer, not a missing page.
        quiet_state(
            "لا توجد نتائج · The scan returned nothing",
            "Every tracked symbol was excluded before the decision engine, so "
            "no row carries a current decision. The panel above names each one "
            "and why.",
            count=f"0 / {len(symbols)}",
        )
        return

    frame = pd.DataFrame(results).sort_values(
        ["Confidence", "Score"], ascending=False
    )
    metrics = st.columns(4)
    # Counted from CURRENT rows only: the scanner excludes stale symbols before
    # the decision engine, so no withheld symbol can inflate a badge count.
    metrics[0].metric("BUY", int((frame["Signal"] == "BUY").sum()))
    metrics[1].metric("WATCH", int((frame["Signal"] == "WATCH").sum()))
    metrics[2].metric("AVOID", int((frame["Signal"] == "AVOID").sum()))
    # Not "Average Score". Averaging a number whose rank correlation with the
    # outcome is +0.01 produces a number with no meaning at all, sat in a tile
    # beside three that count real decisions.
    metrics[3].metric("لم تُقيَّم · Withheld",
                      max(0, len(symbols) - len(frame)))

    render_data_update_required(symbols, results)

    section_header(
        "Current Opportunities",
        f"{len(frame)} of your {len(symbols)} tracked symbols, with current "
        f"daily data — the same rule the Daily Dashboard runs, on your subset")
    st.dataframe(
        with_company_name_column(frame[[
            "Rank", "Ticker", "Rating", "Regime", "Signal", "Confidence",
            "Score", "AIProbability", "Price", "RR",
        ]], "Ticker"),
        hide_index=True,
        width="stretch",
        column_config={
            NAME_COLUMN: st.column_config.TextColumn("اسم السهم", width="large"),
            "Confidence": st.column_config.ProgressColumn(
                "Confidence", min_value=0, max_value=100, format="%d%%"
            ),
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100
            ),
            "Price": st.column_config.NumberColumn("Price", format="%.2f"),
            "RR": st.column_config.NumberColumn("R/R", format="%.2f"),
        },
    )
    st.caption(SCORE_CAVEAT)
