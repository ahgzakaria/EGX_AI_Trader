"""One provenance panel for every analysis view. Renders, decides nothing.

Every field here comes from ``AnalysisFreshnessContext``. A provider or cache
update timestamp is deliberately never shown beside a candle date - presenting
them together is what let "Latest completed candle 2026-08-03" sit above
2026-07-30 prices.
"""

from __future__ import annotations

import streamlit as st

from services.analysis_freshness_service import (
    HISTORICAL_LABELS,
    WATCHLIST_WITHHELD,
)


def render_freshness_provenance(context, *, title="Data provenance"):
    """The shared panel. Identical wording in Stock Details, Watchlist and AI."""

    with st.expander(title, expanded=not context.current_analysis_allowed):
        for label, value in context.provenance_rows():
            st.markdown(f"**{label}:** {value}")
        if not context.rubix_overlay_applied:
            st.caption(context.overlay_denial_message())


def render_stale_block(context):
    """The blocking panel a non-current symbol shows instead of a decision."""

    st.error(context.stale_message())
    render_freshness_provenance(context)


def render_historical_labels():
    """Explicit labels for any historical section. Never optional."""

    st.warning(" · ".join(HISTORICAL_LABELS))


def render_rubix_status(context):
    """Typed Rubix wording only - never a generic FRESH / LIVE / UPDATED."""

    message = context.rubix_message()
    if context.may_label_rubix_live:
        st.success(message)
    else:
        st.info(message)
        if not context.rubix_overlay_applied:
            st.caption(context.overlay_denial_message())


def withheld_badge() -> str:
    """What a stale Watchlist row shows instead of a current decision."""

    return WATCHLIST_WITHHELD


__all__ = [
    "render_freshness_provenance",
    "render_historical_labels",
    "render_rubix_status",
    "render_stale_block",
    "withheld_badge",
]
