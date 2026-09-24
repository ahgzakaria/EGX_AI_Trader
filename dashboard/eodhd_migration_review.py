"""SYSTEM → EODHD Migration Review — read-only shadow status (no activation).

Presents the Phase-2/3 EODHD migration evidence and the four inactive routing tiers.
There is deliberately NO activate control: routing stays inactive, Yahoo stays the
active historical provider, Rubix stays live, production stays disabled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.ui import badge_html, page_header, section_header
from core.universe import read_symbol_frame

REP = Path("reports/eodhd")
REVIEW = Path("data/eodhd/historical_symbol_routing_review.json")


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _csv(name):
    try:
        return read_symbol_frame(REP / name)
    except Exception:
        return pd.DataFrame()


def show_eodhd_migration_review():
    page_header("EODHD Migration Review",
                "مراجعة ترحيل بيانات EODHD · Shadow evidence · routing INACTIVE · decision support only",
                icon="🧭", badge="SHADOW")
    from providers.provider_mode import active_historical_provider, current_mode
    st.markdown(
        badge_html(f"Mode: {current_mode()}", "blue") + "  "
        + badge_html(f"Active historical: {active_historical_provider()}", "green") + "  "
        + badge_html("Rubix live: unchanged", "gray") + "  "
        + badge_html("Routing: INACTIVE", "amber") + "  "
        + badge_html("Production: disabled", "gray"), unsafe_allow_html=True)
    st.caption("This screen is read-only. No provider is switched and no routing tier is active.")

    review = _json(REVIEW)
    tiers = review.get("symbols", [])
    tier_summary = _json(REP / "routing_tier_summary.json")

    section_header("Approval tiers (all approved:false)", "proposed only — nothing enabled")
    from collections import Counter
    tc = Counter(e.get("tier") for e in tiers)
    c = st.columns(4)
    c[0].metric("TIER A · forward-safe", tc.get("TIER_A_FORWARD_SAFE", 0))
    c[1].metric("TIER B · EODHD no-fallback", tc.get("TIER_B_FORWARD_EODHD_NO_FALLBACK", 0))
    c[2].metric("TIER C · historical review", tc.get("TIER_C_HISTORICAL_REVIEW", 0))
    c[3].metric("TIER D · unsupported/manual", tc.get("TIER_D_UNSUPPORTED_OR_MANUAL", 0))

    section_header("Coverage & evidence", "")
    mapping = _csv("../eodhd_symbol_mapping.csv")
    c2 = st.columns(4)
    verified = int((mapping["mapping_status"] == "VERIFIED_EXACT").sum()) if not mapping.empty else 0
    c2[0].metric("Verified mappings", f"{verified} / {len(mapping) if not mapping.empty else 0}")
    c2[1].metric("Forward would use EODHD", tier_summary.get("forward_uses_eodhd", "—"))
    c2[2].metric("No-fallback symbols", tier_summary.get("symbols_with_no_fallback", "—"))
    c2[3].metric("Blocked from migration", tier_summary.get("symbols_blocked_from_migration", "—"))

    bt = _csv("real_backtest_summary.csv")
    if not bt.empty and "classification" in bt:
        section_header("Real frozen-engine backtest (Yahoo vs EODHD)", "genuine engine runs")
        st.caption("Historical/frozen backtests remain on Yahoo. EODHD's more complete bar "
                   "coverage and corporate-action handling change some backtests — see below.")
        st.dataframe(bt[["symbol", "window", "yahoo_bars", "eodhd_bars", "yahoo_trades",
                         "eodhd_trades", "classification"]].dropna(subset=["classification"]),
                     width="stretch", hide_index=True)

    st.markdown("**ORAS special rule:** " + badge_html("EODHD primary · Yahoo forbidden", "red"),
                unsafe_allow_html=True)
    st.caption("ORAS: EODHD ≈ Rubix (713.5); Yahoo .CA is stale/mis-scaled (71.05, zero volume). "
               "On EODHD failure ORAS returns DATA_UNAVAILABLE — it never falls back to Yahoo.")

    scale = _csv("price_scale_anomalies.csv")
    if not scale.empty:
        section_header("Price-scale anomalies", "")
        st.dataframe(scale, width="stretch", hide_index=True)

    st.info("Routing remains INACTIVE. Enabling any tier is a separate, explicit approval + "
            "wiring step not performed here. Historical backtests are never repointed.")
