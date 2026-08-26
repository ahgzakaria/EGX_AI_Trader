"""Sector liquidity flow: daily turnover aggregation across the EGX universe.

The storage constants live here rather than in ``builder`` so that
``sector_flow.strength`` can reach them without importing the builder, which
reaches ``decision_support`` and would close an import cycle back onto itself.
"""

#: Where the per-sector daily history is persisted.
DEFAULT_DATABASE = "data/sector_flow.db"
#: Table holding one row per (session, sector).
HISTORY_TABLE = "sector_daily"
#: Table holding one provenance row per build.
METADATA_TABLE = "sector_flow_builds"
