"""One provenance panel shared by Stock Details and AI Stock Analysis.

Each page computes its levels with its own engine on its own evidence snapshot, so
the two are *not* required to agree. What they must never do is hide which engine,
which snapshot and which moment produced the numbers on screen. This panel states
that plainly and identically on both pages, so a reader can tell in one glance
whether two figures are comparable.

Display only: it renders values supplied by the caller and computes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

EM_DASH = "—"

FROZEN = "FROZEN"
LIVE = "LIVE"
MIXED = "FROZEN + LIVE OVERLAY"


@dataclass(frozen=True)
class Provenance:
    """Everything needed to judge whether two on-screen numbers are comparable."""
    data_timestamp: str = ""        # when the price data was observed
    signal_timestamp: str = ""      # when these levels were calculated
    session: str = ""               # last completed trading session
    provider: str = ""              # historical data provider
    live_provider: str = ""         # live quote provider, when overlaid
    engine: str = ""                # calculation engine + version
    evidence_hash: str = ""         # snapshot identity, when the engine records one
    status: str = FROZEN            # FROZEN / LIVE / MIXED

    def rows(self):
        return (
            ("مصدر البيانات", "Provider", self.provider or EM_DASH),
            ("مزود السعر الحي", "Live Quote Provider", self.live_provider or EM_DASH),
            ("آخر جلسة مكتملة", "Last Completed Session", self.session or EM_DASH),
            ("وقت البيانات", "Data Timestamp", self.data_timestamp or EM_DASH),
            ("وقت حساب المستويات", "Signal Timestamp", self.signal_timestamp or EM_DASH),
            ("محرك الحساب", "Calculation Engine", self.engine or EM_DASH),
            ("بصمة الأدلة", "Evidence Hash", self.evidence_hash or EM_DASH),
            ("حالة اللقطة", "Snapshot Status", self.status or EM_DASH),
        )


def render_provenance_panel(provenance: Provenance, *, title="مصدر الأرقام",
                            subtitle="Provenance — which engine and snapshot produced these numbers",
                            expanded: bool = False):
    """Render the panel. Kept compact so it never competes with the levels."""
    import html as _html

    with st.expander(f"{title} · {subtitle}", expanded=expanded):
        body = "".join(
            f'<tr><td class="k">{_html.escape(str(label_ar))}'
            f'<span class="en">{_html.escape(str(label_en))}</span></td>'
            f'<td class="v">{_html.escape(str(value))}</td></tr>'
            for label_ar, label_en, value in provenance.rows())
        st.markdown(
            "<style>"
            ".egx-prov { width:100%; border-collapse:collapse; direction:rtl; }"
            ".egx-prov td { padding:.3rem .55rem; border-bottom:1px solid rgba(148,163,184,.25);"
            " font-size:.82rem; }"
            ".egx-prov tr:last-child td { border-bottom:0; }"
            ".egx-prov td.k { color:#64748b; white-space:nowrap; }"
            ".egx-prov td.k .en { display:block; font-size:.6rem; letter-spacing:.03em;"
            " text-transform:uppercase; opacity:.75; direction:ltr; text-align:right; }"
            ".egx-prov td.v { font-weight:700; text-align:left; direction:ltr;"
            " font-variant-numeric:tabular-nums; word-break:break-all; }"
            "</style>"
            f'<table class="egx-prov">{body}</table>',
            unsafe_allow_html=True)
        st.caption(
            "قد تنتج المحركات ولقطات الأدلة المختلفة مستويات صحيحة مختلفة؛ لا تُقارن "
            "الأرقام إلا عند تطابق المحرك وبصمة الأدلة. · Different engines and "
            "evidence snapshots may produce different valid levels; compare figures "
            "only when the engine AND evidence snapshot match.")


__all__ = ["Provenance", "render_provenance_panel", "FROZEN", "LIVE", "MIXED", "EM_DASH"]
