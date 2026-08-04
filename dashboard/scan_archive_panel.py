"""Rendering for schema-v2 scan archives. Reads the typed archive, decides nothing.

Every page shares this so Run History, Compare Runs and the download controls
cannot describe the same archive three different ways - which is how a legacy
run ends up presented as though its row count were market coverage.
"""

from __future__ import annotations

import streamlit as st

from services.daily_scan_archive_reader import (
    COVERAGE_BEARING,
    RECOVERED_WARNING,
    SchemaStatus,
    resolve_artifact,
)
from services.daily_scan_export import (
    COMPATIBILITY_FILENAME,
    COVERAGE_AUDIT_FILENAME,
    CURRENT_DECISIONS_FILENAME,
)


def render_archive_summary(archive):
    """The Run History panel for one archive."""

    if archive.invalid_data_provenance:
        st.error(f"{archive.run_id} — NOT FOR DECISION USE")
        st.caption(
            "This run carries an INVALID_DATA_PROVENANCE marker. Its rows may "
            "be inspected as historical evidence only; they are not a valid "
            "decision product."
        )

    for warning in archive.archive_warnings:
        if warning != "NOT FOR DECISION USE":
            st.warning(warning)

    st.caption(f"Schema: {archive.schema_status.value} "
               f"(declared version {archive.schema_version!r})")

    if not archive.coverage_available:
        # A legacy or invalid archive must never be given a coverage number.
        st.info(
            "Full-universe coverage is not available for this archive, so no "
            "coverage percentage is shown. Row counts are not coverage."
        )
        return

    st.markdown(
        f"**CURRENT DAILY COVERAGE**  \n"
        f"{archive.current_count}/{archive.operational_universe_count} "
        f"({archive.current_coverage_percent:.1f}%)"
    )
    counts = archive.decision_counts
    st.markdown(
        f"**DECISIONS AMONG CURRENT SYMBOLS**  \n"
        f"BUY {counts['BUY']} · WATCH {counts['WATCH']} · AVOID {counts['AVOID']}"
    )
    st.caption(
        "Scoped to the current subset — this is not the whole-market "
        "distribution."
    )

    row = st.columns(4)
    row[0].metric("Expected session", archive.expected_completed_session or "—")
    row[1].metric("Dominant observed", archive.dominant_observed_session or "—")
    row[2].metric("Excluded", archive.excluded_count)
    row[3].metric("Of loaded results",
                  f"{archive.loaded_result_current_percent:.1f}%",
                  help="Secondary metric: current rows / rows that loaded. The "
                       "headline coverage uses the operational universe.")

    if not archive.market_wide_summary_allowed:
        st.error("MARKET-WIDE SUMMARY BLOCKED\n\n"
                 f"Reason: {archive.market_wide_summary_block_reason}")

    if archive.observed_session_distribution:
        st.caption("Observed candle sessions: " + " · ".join(
            f"`{date}` {count}" for date, count
            in sorted(archive.observed_session_distribution.items(), reverse=True)))


def render_download_controls(archive):
    """Clearly-named exports. The audit is never called opportunities."""

    if archive.schema_status not in COVERAGE_BEARING:
        st.info(
            "Schema-v2 downloads are unavailable for this archive "
            f"({archive.schema_status.value})."
        )
        if archive.missing_artifacts:
            st.caption("Missing or invalid: " + ", ".join(archive.missing_artifacts))
        return

    if not archive.decision_use_allowed:
        st.error("NOT FOR DECISION USE — downloads are labelled historical only.")

    if archive.metadata_recovered:
        # The files are the evidence; the metadata describing them was lost.
        # Serving them silently would imply a provenance this run cannot show.
        st.warning(RECOVERED_WARNING)
        st.caption(
            "Unavailable from the overwritten metadata: "
            + ", ".join(f"`{name}`" for name in archive.unavailable_metadata_fields)
        )

    st.caption(
        f"Current Decisions: {len(archive.current_decisions)}  ·  "
        f"Coverage Audit: {len(archive.coverage_audit)}/"
        f"{archive.operational_universe_count}"
    )
    if archive.current_count != archive.operational_universe_count:
        st.info(
            "Only CURRENT symbols appear in Current Decisions.\n"
            "Use Full Coverage Audit to review stale, missing and unavailable "
            "symbols."
        )

    columns = st.columns(3)
    _download(columns[0], archive, CURRENT_DECISIONS_FILENAME,
              "Download Current Decisions",
              "Contains only CURRENT symbols for which a decision was calculated.")
    _download(columns[1], archive, COVERAGE_AUDIT_FILENAME,
              "Download Full Coverage Audit",
              "Contains every operational-universe symbol and its "
              "data-freshness/outcome status. Not opportunities.")
    _download(columns[2], archive, COMPATIBILITY_FILENAME,
              "Download Compatibility Results",
              "Compatibility alias — CURRENT decisions only.")


def _download(column, archive, filename, label, help_text):
    try:
        path = resolve_artifact(archive.run_path, filename)
    except ValueError:
        column.caption(f"{label}: refused (path escapes the run directory)")
        return
    if not path.is_file():
        column.caption(f"{label}: unavailable")
        return
    column.download_button(label, data=path.read_bytes(), file_name=filename,
                           mime="text/csv", help=help_text,
                           key=f"dl_{archive.run_id}_{filename}")


def render_comparison(comparison, archive_a, archive_b):
    """Two separate sections: strategy, then data coverage."""

    if not comparison.comparable:
        st.error("COMPARISON BLOCKED\n\n" + comparison.blocked_reason)
        return

    if comparison.legacy_warning:
        st.warning(comparison.legacy_warning)
    if comparison.coverage_caveat:
        st.warning(comparison.coverage_caveat)

    st.subheader("Strategy / current-decision comparison")
    st.caption(
        "Read from scan_current_decisions.csv. Excluded coverage-audit rows are "
        "never treated as decisions."
    )
    row = st.columns(3)
    row[0].metric("CURRENT in both", len(comparison.current_in_both))
    row[1].metric("CURRENT only in A", len(comparison.current_only_in_a))
    row[2].metric("CURRENT only in B", len(comparison.current_only_in_b))

    if not comparison.strategy_claim_allowed:
        st.info(
            "No symbol is CURRENT in both runs, so no statement about strategy "
            "behaviour can be made from this pair."
        )
    else:
        changed = [c for c in comparison.decision_changes if c["Changed"]]
        st.caption(
            f"{len(changed)} of {len(comparison.current_in_both)} like-for-like "
            "symbols changed decision."
        )

    if comparison.disappearance_reasons:
        st.subheader("Why symbols left the decision set")
        st.caption(
            "A symbol whose data went stale is not a strategy exit."
        )
        st.dataframe(
            [{"Symbol": symbol, "Reason": reason}
             for symbol, reason in sorted(comparison.disappearance_reasons.items())],
            use_container_width=True, hide_index=True,
        )

    st.subheader("Data coverage comparison")
    if not comparison.coverage_available:
        st.info("Coverage comparison unavailable — at least one run predates "
                "schema v2.")
        return
    row = st.columns(4)
    row[0].metric("Universe A", archive_a.operational_universe_count)
    row[1].metric("Universe B", archive_b.operational_universe_count)
    row[2].metric("CURRENT A", archive_a.current_count)
    row[3].metric("CURRENT B", archive_b.current_count)


__all__ = [
    "render_archive_summary",
    "render_comparison",
    "render_download_controls",
]
