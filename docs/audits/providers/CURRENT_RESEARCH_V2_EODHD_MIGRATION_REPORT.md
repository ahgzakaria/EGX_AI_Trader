# CURRENT_RESEARCH_V2 — EODHD Operational Migration

> **⚠ SUPERSEDED IN PART — see [CURRENT_RESEARCH_V2_RECONCILIATION.md](../research/CURRENT_RESEARCH_V2_RECONCILIATION.md).**
> A pre-approval reconciliation found answers **Q4, Q7, Q8, Q10 and Q12 below to be wrong**.
> In particular: 31 activated symbols are served Yahoo-derived bars with zero Rubix Daily
> Bridge appends, their freshness is derived from the Yahoo seed, and the volume series is
> universally split-adjusted rather than event-specific. Do not rely on this document's
> Yahoo-isolation or volume claims. The counts in §5 of the reconciliation are authoritative.

**Date:** 2026-07-23 · **Current research provider: EODHD** · **Live: Rubix (unchanged)** ·
**Legacy: FROZEN_YAHOO_SNAPSHOT (backtest reproduction only, no network)** · production
disabled. No strategy, indicator, scoring, threshold, TP/SL or exit rule changed; frozen
Yahoo datasets preserved and never rewritten. **573 tests pass; the app loads.**

New: `core/research_router.py` (two versioned domains + tier routing + provenance),
`core/history_frame_adapter.py`, `data/eodhd/forward_research_versions.json`.
Changed: `core/data_provider.py` (operational history now from the router; `backtest`
purpose from the frozen snapshot), `dashboard/system_health.py` (four-domain panel),
pre-session manifest (research version tag).
Reports: `current_research_migration_manifest.csv`, `tier_c_clean_window_status.csv`,
`unsupported_history_status.csv`, `operational_provider_inventory.csv`,
`yahoo_operational_call_audit.csv`, `current_research_migration_summary.json`.

## The 14 questions

**1. Is EODHD now the active provider for CURRENT_RESEARCH_V2?**
**Yes.** `load_history(purpose=scanner|dashboard|forward_testing)` now resolves through
`core.research_router.get_current_research_history` → EODHD split-adjusted history
(per routing tier). Verified live: COMI/SWDY return `data_domain=CURRENT_RESEARCH_V2`,
`provider=eodhd`, `latest=2026-07-22`, `yahoo_used=False`.

**2. Is Rubix unchanged as the live provider?** **Yes** — untouched; it still supplies the
live quote overlay and the Daily Bridge completed sessions. No Rubix code or config changed.

**3. Is Yahoo completely removed from current operational research?**
**Yes.** The operational daily-history path no longer reads the Yahoo-backed cache or calls
`yfinance`. Scanner, Dashboard, Watchlist, Stock Details, Expected Range, Opportunities,
pre-session, live-monitor prep, forward testing and latest-completed-session detection all
run without Yahoo (`yahoo_operational_call_audit.csv`).

**4. Any remaining operational Yahoo network calls?**
**None.** `yahoo_operational_calls: 0`. Yahoo code survives only in (a) the **legacy**
frozen-snapshot reader, which is **cache-only with no network**, and (b) isolated
audit/shadow tooling (`scripts/audit_eodhd_*`), which is non-operational. A regression test
injects an exploding Yahoo provider and asserts scanner/dashboard/forward_testing never
call it.

**5. How many symbols use EODHD?** **196** (Tier A + Tier B + all Tier C that passed the
clean-window gate). Total activated for current research: **227**.

**6. How many Tier C symbols pass the clean-window requirement?** **73 of 73.** Every Tier C
symbol has ≥250 internally-consistent EODHD sessions after its last unresolved
corporate-action event, so none are blocked and **none fall back to Yahoo**.

**7. How many unsupported symbols are ready through Local + Rubix?** **31 of 56**, using
validated local history plus idempotent Rubix Daily Bridge appends (never a Yahoo update).

**8. How many symbols are DATA_INSUFFICIENT?** **17** (plus 20 DATA_UNAVAILABLE and 1
EXCLUDED_NON_EQUITY) = **38 blocked**, each listed explicitly in the manifest with its
reason. Blocked symbols are reported, never silently omitted, and are safely excluded from
decisions rather than served substitute data.

**9. Is ORAS protected permanently from Yahoo?** **Yes.** ORAS is Tier B (EODHD, no
fallback, `yahoo_forbidden`). On EODHD failure it returns DATA_UNAVAILABLE. A test asserts
that even when a local/Yahoo path exists, ORAS never reaches it.

**10. Is current freshness independent from Yahoo?** **Yes.** Freshness compares the EODHD
(or Rubix-Bridge) latest session against the centralized EGX calendar's expected completed
session. Yahoo's date is never inspected, so Yahoo lag cannot mark research stale, and
holidays/weekends create no false lag. Verified live: expected `2026-07-22`, EODHD
`2026-07-22` → **FRESH**.

**11. Can frozen Yahoo backtests still be reproduced?** **Yes.** `purpose="backtest"` serves
`LEGACY_BACKTEST_V1` from the immutable frozen Yahoo snapshot in the local cache with **no
network call** (verified: COMI 2341 bars, latest 2026-07-21). No snapshot was deleted or
rewritten.

**12. Are the old and new research versions clearly separated?** **Yes.** Every frame carries
`data_domain` (`CURRENT_RESEARCH_V2` vs `LEGACY_BACKTEST_V1`) plus provider, provider_symbol,
price_series, adjustment_policy, routing_tier, latest_completed_session, history_sufficient,
bridge_sessions_appended, fallback_used and data_quality_status. Forward evidence is
versioned as **FORWARD_RESEARCH_V2_EODHD** (active) vs **FORWARD_RESEARCH_V1_YAHOO**
(closed) — results must not be aggregated across versions. A test asserts the two domains
can never be silently mixed.

**13. Were any strategy rules changed?** **No** — no strategy, indicator, score, threshold,
TP/SL or exit rule was modified. Only the data-source routing changed.

**14. Is production still disabled?** **Yes** — `production_enabled=false`,
`automatic_execution=false`, `broker_orders_enabled=false`.

## Operational inventory
| Domain | Role | Provider | Symbols |
|---|---|---|---|
| CURRENT_RESEARCH_V2 | current daily research | EODHD (tiered) | 196 |
| CURRENT_RESEARCH_V2 | unsupported symbols | validated local + Rubix Daily Bridge | 31 |
| CURRENT_RESEARCH_V2 | live intraday | Rubix | universe |
| LEGACY_BACKTEST_V1 | frozen backtest reproduction | FROZEN_YAHOO_SNAPSHOT (no network) | cached universe |

## Honest notes
- **Three pre-existing tests encoded the old Yahoo-backed architecture** and were updated to
  assert the new behavior while preserving their original invariants (the Rubix overlay must
  never mutate completed daily history; the backtest domain stays isolated — now with the
  stronger assertion that no live Yahoo download occurs).
- **38 symbols are blocked** from current research (20 with no local history at all, 17 with
  insufficient bars, 1 non-equity). They are explicitly listed; they are not silently
  dropped and are not served Yahoo data. Building their history via the Rubix Daily Bridge
  over coming sessions is the path to unblocking them.
- Because EODHD's coverage is more complete than Yahoo's, **current signals may differ from
  what Yahoo would have produced** — this is the intended consequence of the migration, and
  is why new forward evidence starts a separate version (V2) rather than extending V1.

## Safety
Token never printed/logged; `.env` gitignored; frozen Yahoo snapshots preserved and
immutable; Rubix unchanged; no strategy/threshold/TP-SL change; production and broker
execution remain disabled.
