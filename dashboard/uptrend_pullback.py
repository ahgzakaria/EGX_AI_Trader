"""Presentation adapter around the typed Uptrend Pullback package API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd
import streamlit as st

from dashboard.formatting import NAME_COLUMN, company_name, symbol_option_label
from scalping_expected_range.live_readiness import RUBIX_MAPPING_UNAVAILABLE
from dashboard.ui import empty_state
from scalping_uptrend_pullback.frozen_watchlist import (
    WATCHLIST_READY,
    FrozenUptrendWatchlistService,
    UptrendWatchlistServiceResult,
)
from scalping_uptrend_pullback.states import (
    DATA_UNAVAILABLE,
    EMA_ALIGNMENT_FAILED,
    EMA5_SLOPE_FAILED,
    EMA10_SLOPE_FAILED,
    INSUFFICIENT_HISTORY,
    INSUFFICIENT_LIQUIDITY,
    INSUFFICIENT_UPSIDE,
    PROVENANCE_REJECTED,
    SUPPORT_BROKEN,
    SUPPORT_NOT_CONFIRMED,
    TREND_STRUCTURE_FAILED,
    UPTREND_EXTENDED_NO_CHASE,
    UPTREND_NEAR_SUPPORT,
    UPTREND_PULLBACK_TOO_DEEP,
    UPTREND_WAIT_FOR_PULLBACK,
)


UPTREND_PRIMARY_COLUMNS = (
    "السهم",
    NAME_COLUMN,
    "الحالة",
    "منطقة الدعم",
    "السعر الحالي",
    "البعد عن الدعم",
    "الهدف البحثي الأول",
    "القرار",
)

UPTREND_LIVE_COLUMNS = (
    "السهم",
    NAME_COLUMN,
    "الاستراتيجية",
    "السعر الحالي",
    "المنطقة المطلوبة",
    "حالة Rubix",
    "جاهزية الدخول البحثية",
    "سبب المنع",
)

UPTREND_DETAIL_FIELDS = (
    "Uptrend Pullback Score",
    "EMA5",
    "EMA10",
    "EMA5 slope",
    "EMA10 slope",
    "support lower / upper / centre",
    "support strength",
    "support sources",
    "pullback depth",
    "first research target",
    "available upside percentage",
    "invalidation level",
    "risk/reward proxy",
    "liquidity score",
    "D-1 cutoff",
    "deterministic reasons",
    "provider provenance",
    "valid session count",
    "first_touch_order_available",
)

UPTREND_STATE_AR = {
    UPTREND_NEAR_SUPPORT: "اتجاه صاعد — قريب من الدعم",
    UPTREND_WAIT_FOR_PULLBACK: "اتجاه صاعد — انتظار تصحيح",
    UPTREND_EXTENDED_NO_CHASE: "ممتد — لا تطارده",
    UPTREND_PULLBACK_TOO_DEEP: "التصحيح أعمق من المسموح",
    INSUFFICIENT_UPSIDE: "مساحة الصعود غير كافية",
    SUPPORT_BROKEN: "كسر الدعم — مرفوض",
    SUPPORT_NOT_CONFIRMED: "دعم غير مؤكد",
    EMA_ALIGNMENT_FAILED: "ترتيب EMA5 وEMA10 غير صالح",
    EMA5_SLOPE_FAILED: "ميل EMA5 غير كافٍ",
    EMA10_SLOPE_FAILED: "ميل EMA10 غير كافٍ",
    TREND_STRUCTURE_FAILED: "هيكل الاتجاه غير مؤكد",
    INSUFFICIENT_LIQUIDITY: "سيولة غير كافية",
    INSUFFICIENT_HISTORY: "تاريخ يومي غير كافٍ",
    DATA_UNAVAILABLE: "البيانات غير متاحة",
    PROVENANCE_REJECTED: "مصدر البيانات مرفوض",
}


@dataclass(frozen=True)
class UptrendPullbackView:
    """UI-facing projection of typed service results."""

    available: bool
    status: str
    record: object | None = None
    candidates: tuple[Mapping[str, object], ...] = ()
    rejected: tuple[object, ...] = ()
    data_cutoff: str | None = None
    source: str | None = None
    detail: str | None = None


def uptrend_watchlist_service():
    return FrozenUptrendWatchlistService()


def view_from_uptrend_result(
    result: UptrendWatchlistServiceResult,
) -> UptrendPullbackView:
    record = result.record
    snapshot = result.snapshot
    rejected = tuple(
        item for item in getattr(snapshot, "results", ()) if not item.eligible
    )
    header = record.header if record is not None else {}
    return UptrendPullbackView(
        available=True,
        status=result.status,
        record=record,
        candidates=tuple(record.displayed) if record is not None else (),
        rejected=rejected,
        data_cutoff=header.get("historical_data_cutoff"),
        source=header.get("provider"),
        detail=result.detail,
    )


def load_uptrend_pullback_view(
    service=None,
    *,
    target_session_date=None,
) -> UptrendPullbackView:
    """Read existing immutable state only; never generate on page load."""

    service = service or uptrend_watchlist_service()
    return view_from_uptrend_result(
        service.get_for_session(target_session_date)
    )


def prepare_uptrend_pullback_view(
    service=None,
    *,
    target_session_date=None,
) -> UptrendPullbackView:
    """Explicitly invoke the backend service from the single prep action."""

    service = service or uptrend_watchlist_service()
    existing = service.get_for_session(target_session_date)
    if existing.status == WATCHLIST_READY and existing.record is not None:
        return view_from_uptrend_result(existing)
    return view_from_uptrend_result(
        service.prepare_for_session(target_session_date)
    )


def uptrend_state_label(state):
    return UPTREND_STATE_AR.get(str(state or ""), str(state or "غير متاح"))


def _zone(lower, upper):
    if lower is None or upper is None:
        return "—"
    return f"{float(lower):.3f} – {float(upper):.3f}"


def _live_items(live_batch):
    return {
        item.symbol: item
        for item in getattr(live_batch, "results", ())
    }


def primary_uptrend_frame(
    view: UptrendPullbackView,
    live_batch=None,
) -> pd.DataFrame:
    """Format hard-eligible frozen members; no strategy calculation occurs."""

    if not view.available or not view.candidates:
        return pd.DataFrame(columns=UPTREND_PRIMARY_COLUMNS)
    live = _live_items(live_batch)
    rows = []
    for member in view.candidates:
        if member["candidate_state"] == INSUFFICIENT_UPSIDE:
            continue
        item = live.get(member["symbol"])
        rows.append(
            {
                "السهم": member["symbol"],
                NAME_COLUMN: company_name(member["symbol"]),
                "الحالة": "مؤهل تاريخياً",
                "منطقة الدعم": _zone(
                    member["support_zone_lower"],
                    member["support_zone_upper"],
                ),
                "السعر الحالي": getattr(item, "current_price", None),
                "البعد عن الدعم": getattr(
                    item,
                    "distance_from_support_percent",
                    member["distance_from_support_percent"],
                ),
                "الهدف البحثي الأول": member["first_research_target"],
                "القرار": uptrend_state_label(member["candidate_state"]),
            }
        )
    return pd.DataFrame(rows, columns=UPTREND_PRIMARY_COLUMNS)


def uptrend_live_frame(view, live_batch=None):
    """Overlay live fields onto the same frozen Uptrend membership and order."""

    live = _live_items(live_batch)
    rows = []
    for member in view.candidates:
        item = live.get(member["symbol"])
        rows.append(
            {
                "السهم": member["symbol"],
                NAME_COLUMN: company_name(member["symbol"]),
                "الاستراتيجية": (
                    f"{member['strategy_identity']} · اتجاه صاعد قرب الدعم"
                ),
                "السعر الحالي": getattr(item, "current_price", None),
                "المنطقة المطلوبة": _zone(
                    member["support_zone_lower"],
                    member["support_zone_upper"],
                ),
                "حالة Rubix": _rubix_label(item),
                "جاهزية الدخول البحثية": _live_label(item),
                "سبب المنع": _block_reason(item),
            }
        )
    return pd.DataFrame(rows, columns=UPTREND_LIVE_COLUMNS)


def render_uptrend_pullback_tab(
    view: UptrendPullbackView,
    live_batch=None,
):
    """Render typed candidates, full details and optional rejection diagnostics."""

    frame = primary_uptrend_frame(view, live_batch)
    if view.status != WATCHLIST_READY or view.record is None:
        empty_state(
            "لا توجد قائمة اتجاه صاعد ثابتة جاهزة",
            "استخدم زر تجهيز قائمة السكالبنج لإنشاء أو تحميل القائمة المستقلة.",
            icon="↗",
        )
        if view.detail:
            st.caption(view.detail)
        st.caption("Research Only")
        return frame

    header = view.record.header
    st.caption(
        f"{header['displayed_count']} مرشحاً من حد أقصى "
        f"{header['candidate_limit']} · قطع D-1: "
        f"{header['historical_data_cutoff']}"
    )
    st.dataframe(frame, width="stretch", hide_index=True)
    _render_details(view)
    _render_rejections(view)
    st.warning(
        "الشموع اليومية لا تثبت ترتيب لمس الدعم والمقاومة داخل الجلسة. "
        "first_touch_order_available = False"
    )
    return frame


def _render_details(view):
    if not view.candidates:
        return
    selected = st.selectbox(
        "اختر سهماً لعرض تفاصيل الاتجاه",
        [member["symbol"] for member in view.candidates],
        format_func=symbol_option_label,
        key=f"uptrend_detail_{view.record.header['watchlist_id']}",
    )
    member = next(
        item for item in view.candidates if item["symbol"] == selected
    )
    with st.expander(f"تفاصيل {selected}", expanded=False):
        cards = st.columns(4)
        cards[0].metric("الترتيب التاريخي", member["eligible_rank"])
        cards[1].metric("Uptrend Pullback Score", member["total_score"])
        cards[2].metric(
            "الحالة",
            uptrend_state_label(member["candidate_state"]),
        )
        cards[3].metric("الجلسات الصالحة", member["valid_session_count"])
        details = [
            ("Typed state", member["candidate_state"]),
            ("EMA5", member["ema5"]),
            ("EMA10", member["ema10"]),
            ("EMA5 slope", member["ema5_slope"]),
            ("EMA10 slope", member["ema10_slope"]),
            (
                "support lower / upper / centre",
                (
                    member["support_zone_lower"],
                    member["support_zone_upper"],
                    member["support_zone_centre"],
                ),
            ),
            ("support strength", member["support_strength"]),
            ("support sources", ", ".join(member["support_sources"])),
            ("pullback depth", member["pullback_depth_percent"]),
            ("first research target", member["first_research_target"]),
            ("available upside percentage", member["available_upside_percent"]),
            ("invalidation level", member["invalidation_level"]),
            ("risk/reward proxy", member["reward_risk_ratio"]),
            ("liquidity score", member["liquidity_score"]),
            ("D-1 cutoff", member["data_cutoff"]),
            (
                "deterministic reasons",
                ", ".join(member["deterministic_reasons"]),
            ),
            (
                "provider provenance",
                f"{member['source']} · {member['source_fingerprint']}",
            ),
            ("valid session count", member["valid_session_count"]),
            (
                "first_touch_order_available",
                member["first_touch_order_available"],
            ),
        ]
        st.dataframe(
            pd.DataFrame(details, columns=["البند", "القيمة"]),
            width="stretch",
            hide_index=True,
        )


def _render_rejections(view):
    if not view.rejected:
        return
    with st.expander("أسباب استبعاد الأسهم", expanded=False):
        rows = [
            {
                "السهم": item.symbol,
                NAME_COLUMN: company_name(item.symbol),
                "الحالة": uptrend_state_label(item.candidate_state),
                "Typed state": item.candidate_state,
                "الأسباب": ", ".join(item.deterministic_reasons),
            }
            for item in view.rejected
        ]
        st.dataframe(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
        )


def _rubix_label(item):
    if getattr(item, "live_state", None) == RUBIX_MAPPING_UNAVAILABLE:
        # Say plainly that no mapping exists; never imply a broken collector.
        return "لا يوجد ربط Rubix · No Rubix mapping"
    quality = str(getattr(item, "data_quality_status", "") or "")
    return {
        "AVAILABLE_AND_VALIDATED": "متصل",
        "STALE": "بيانات قديمة",
        "UNAVAILABLE": "غير متاح",
        "NOT_QUERIED_SESSION_PHASE": "خارج وقت المتابعة",
        "NOT_QUERIED_NO_RUBIX_MAPPING": "لا يوجد ربط Rubix · No Rubix mapping",
    }.get(quality, "لم يتم التحديث")


def _live_label(item):
    if item is None:
        return "انتظار الاقتراب"
    return {
        "PRE_OPEN_WAIT": "انتظار الاقتراب",
        "ENTRY_TRIGGER_FORMING": "تأكيد يتكوّن",
        "ENTRY_READY_RESEARCH_ONLY": "جاهزية بحثية",
        "UPTREND_WAIT_FOR_PULLBACK_LIVE": "انتظار الاقتراب",
        "MOVE_EXTENDED_DO_NOT_CHASE": "ممتد — لا تطارده",
        "LIVE_DATA_STALE": "بيانات قديمة",
        "LIVE_DATA_UNAVAILABLE": "بيانات غير متاحة",
        "SPREAD_TOO_WIDE": "سبريد غير مناسب",
        "CLOSING_AUCTION_NO_NEW_ENTRY": "مزاد الإغلاق — لا دخول جديد",
        "SESSION_CLOSED": "الجلسة مغلقة",
        "UPTREND_SUPPORT_BROKEN_LIVE": "كسر الدعم — مرفوض",
        RUBIX_MAPPING_UNAVAILABLE: "تاريخي فقط — لا ربط لحظي (No live mapping)",
    }.get(item.live_state, str(item.live_state))


def _block_reason(item):
    if item is None:
        return "حدّث المتابعة اللحظية"
    if item.live_state == RUBIX_MAPPING_UNAVAILABLE:
        return (
            "البيانات التاريخية من EODHD متاحة، لكن لا يوجد ربط Rubix موثّق "
            "لهذا السهم — ليست مشكلة في المجمِّع · Historical EODHD data is "
            "available; no verified Rubix mapping, so no live quote. Not a "
            "collector failure."
        )
    if item.live_state == "ENTRY_READY_RESEARCH_ONLY":
        return "لا يوجد مانع بحثي"
    return (
        item.no_chase_reason
        or item.invalidation_condition
        or (item.explanations[-1] if item.explanations else None)
        or _live_label(item)
    )
