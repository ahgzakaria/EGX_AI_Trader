"""Presentation adapter for the future Uptrend Pullback strategy.

The UI owns this interface, not the strategy.  Until the separately developed
backend is integrated, the adapter must stay unavailable and return no rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd
import streamlit as st

from dashboard.ui import empty_state


UPTREND_PRIMARY_COLUMNS = (
    "السهم",
    "الحالة",
    "منطقة الدعم",
    "السعر الحالي",
    "البعد عن الدعم",
    "الهدف البحثي الأول",
    "القرار",
)

UPTREND_DETAIL_FIELDS = (
    "EMA5",
    "EMA10",
    "slopes",
    "support_sources",
    "pullback_depth",
    "invalidation",
    "liquidity",
    "deterministic_reasons",
)


@dataclass(frozen=True)
class UptrendPullbackView:
    """UI-facing backend contract; calculations remain outside the dashboard."""

    available: bool = False
    candidates: tuple[Mapping[str, object], ...] = ()
    data_cutoff: str | None = None
    source: str | None = None


def load_uptrend_pullback_view() -> UptrendPullbackView:
    """Return the truthful empty adapter until the backend is connected."""

    return UptrendPullbackView()


def primary_uptrend_frame(view: UptrendPullbackView) -> pd.DataFrame:
    """Normalize backend-provided display fields without calculating signals."""

    if not view.available or not view.candidates:
        return pd.DataFrame(columns=UPTREND_PRIMARY_COLUMNS)
    return pd.DataFrame(
        [
            {column: candidate.get(column) for column in UPTREND_PRIMARY_COLUMNS}
            for candidate in view.candidates
        ],
        columns=UPTREND_PRIMARY_COLUMNS,
    )


def render_uptrend_pullback_tab(view: UptrendPullbackView | None = None):
    """Render either the adapter output or the required honest empty state."""

    view = view or load_uptrend_pullback_view()
    frame = primary_uptrend_frame(view)
    if not view.available:
        empty_state(
            "محرك الاتجاه الصاعد قرب الدعم لم يتم ربطه بعد",
            "لن يتم عرض أسهم أو درجات تجريبية قبل ربط المحرك المعتمد.",
            icon="↗",
        )
        st.caption("Research Only")
        return frame
    if frame.empty:
        empty_state(
            "لا توجد فرص مطابقة",
            "لم يرسل المحرك المعتمد أي مرشح تاريخي لهذه الجلسة.",
            icon="○",
        )
        return frame
    st.dataframe(frame, use_container_width=True, hide_index=True)
    return frame
