# EGX Dynamic Holiday Calendar — Official Sync + Manual Approval + Safe Gating

**Date:** 2026-07-23 · **Scope:** calendar infrastructure, session safety, and UI only.
No trading strategy, scenario logic, scoring, threshold, TP/SL, provider, or database
**data** was changed; production stays disabled; no Python source is auto-edited; no
scheduled sync task was registered. **494 tests pass** (22 new dynamic-calendar tests).

---

## What was built

| Area | Deliverable |
|------|-------------|
| Central service | `core/calendar/egx_calendar_service.py` — JSON-backed, source-priority resolution, rich `SessionStatus`, manual actions + audit + versioning, conflict detection |
| Data (editable, not code) | `data/calendar/egx_official_holidays.json`, `egx_discovered_holidays.json`, `egx_calendar_overrides.json`, `egx_calendar_sync_state.json`, `egx_calendar_audit.json` |
| Facade | `core/egx_calendar.py` now delegates to the service (same public API → all consumers + prior tests unchanged) + `session_status` / `review_required` / `session_is_uncertain` |
| Official sync (dry-run) | `core/calendar/egx_holiday_sync.py` + `scripts/sync_egx_holidays.py` — official-EGX only, conservative extraction, strict auto-confirm policy, dedup + hash-versioning |
| Pre-flight | `scripts/egx_session_preflight.py` — verdicts CLEAR / NON_TRADING_DAY / REVIEW_REQUIRED / CALENDAR_SOURCE_UNAVAILABLE / CONFLICTING_OFFICIAL_RECORDS |
| UI | `dashboard/trading_calendar.py` (SYSTEM → Trading Calendar) — review/confirm/reject/override, conflicts, sync history, audit, Run-Sync |
| Reports | `core/calendar/egx_calendar_reports.py` → `reports/calendar/{calendar_records,discovered_announcements,conflicts,sync_history,manual_actions}.csv` |
| Safe gating | UI banners (holiday / review-required / uncertain / partial); pre-session emits a safe non-trading manifest with `session_calendar_status` on pending/uncertain days |

The service owns the calendar/session dimension only. Every existing session consumer
already routes through `core/egx_session.py`, which defaults `holidays=None` → the
facade → the service, so the dynamic calendar reaches session-phase logic, Dashboard,
Opportunities, Expected Range, the Rubix launcher, pre-session, live monitor, outcome
finalizer, daily finalizer, session validator, paper counting and freshness.

## Final questions

**1. Can new holidays be added without editing Python code?**
Yes. Add them to `data/calendar/egx_official_holidays.json` (or approve a discovery /
save an override) in `data/calendar/egx_calendar_overrides.json`, or use the Trading
Calendar UI. `2026-07-23` was migrated out of Python into the official JSON. A curated
built-in fallback remains only as a last resort if the JSON files are missing.

**2. Which official EGX sources are synchronized?**
Official EGX only — the EGX Trading Calendar page and EGX News/Disclosures
(`egx.com.eg`). Auto-confirmation is gated on the `egx.com.eg` domain. Government/bank
holiday lists, search snippets, third-party sites, and feed silence are never sources
for confirmation.

**3. Which records are auto-confirmed?**
By policy (`classify_confirmation`): only an official-EGX-domain record with explicit
closure wording, an explicit/deterministically-resolved date, EGX-trading scope, a known
closure type, and no conflict. In practice the sync routes **everything through manual
review by default** (it writes `NEEDS_REVIEW`, never a `CONFIRMED` record), so a human
approves each closure. Nothing is auto-confirmed from ambiguity.

**4. Which records require manual review?**
Any discovery that is ambiguous (e.g. "next Thursday" with no reliable article date,
"on the occasion of…" without an explicit closure date, a government/bank-only holiday,
a republished old announcement), lacks a resolvable date, has an UNKNOWN closure type,
or conflicts with an existing record → `NEEDS_REVIEW`, surfaced in the UI.

**5. How are moved holidays handled?**
Moved holidays are ordinary JSON records on their actual weekday (not a fixed date), so
`is_trading_day` / `session_status` honor them directly (tested with a Wednesday
`2026-07-15` moved holiday). Weekend rules and next/previous-session math skip them.

**6. How are conflicts resolved?**
Deterministic priority: MANUAL_OVERRIDE → CONFIRMED EGX_OFFICIAL → CONFIRMED
EGX_TRADING_CALENDAR → built-in fallback → unconfirmed discovery → weekend → normal day.
A manual override wins operationally, but any conflict (closure-type disagreement,
multiple confirmed sources) is detected, attached to the `SessionStatus`, shown in the
UI Conflicts tab and exported to `conflicts.csv` — never hidden.

**7. What happens when the session status is uncertain?**
`HOLIDAY_PENDING_REVIEW` / `SESSION_STATUS_UNCERTAIN` → `is_trading_day = None` (neither
auto-holiday nor auto-live), `entries_allowed = False`, `review_required = True`. The UI
shows a *CALENDAR REVIEW REQUIRED* / *SESSION STATUS UNCERTAIN* banner (never EGX OPEN /
Ready Now / a normal Live-Quote-Stale blocker), and the pre-session writes a safe
non-trading manifest — no new paper scenarios, no forward-session count. It is never
auto-classified as a holiday nor as a normal live session.

**8. Do scheduled tasks stop safely?**
Yes. `TRADING_DAY_CONFIRMED` → run normally; `HOLIDAY_CONFIRMED`/`WEEKEND`/
`EXCEPTIONAL_CLOSURE` → exit 0, `NON_TRADING_DAY`, no artifacts; pending/uncertain → the
pre-session writes a safe non-trading manifest (carrying `session_calendar_status`) so
the live monitor skips and no forward session is counted. Verified on 2026-07-23: pre-
session, live monitor, daily finalizer and session validator all exit 0.

**9. Does history freshness skip holidays correctly?**
Yes. On 2026-07-23 the expected latest completed session stays **2026-07-22** (no
2026-07-23 candle expected, no false one-session lag), `HISTORY_CURRENT`, and the next
completed-session expectation follows the next actual trading day (2026-07-26). All
history/freshness/lag helpers use the shared calendar.

**10. Can the user approve a holiday from the UI?**
Yes. SYSTEM → Trading Calendar → Needs Review → Confirm/Reject (with an audit reason),
Manual Overrides to declare exceptional/partial closures, and Run Sync (dry-run). All
actions write JSON + an append-only audit trail; confirmed history is never silently
rewritten (superseded records are retained).

**11. Was any strategy, threshold, provider, database or execution flag changed?**
No. Only calendar/session-classification code, the calendar UI/banners, the pre-session
safe-gate, and JSON calendar data. No strategy/scenario/score/threshold/TP-SL/provider/
database-**data**/paper/production change; no automatic execution.

**12. Is production still disabled?**
Yes — `production_enabled=false`, `automatic_execution=false`, `broker_orders_enabled=
false`. The pre-flight and sync outputs assert production disabled.

---

## Validation

- **Live app:** SYSTEM → Trading Calendar renders **HOLIDAY CONFIRMED · source
  EGX_OFFICIAL · Next session 2026-07-26 · Review required No**, sourced from JSON, with
  review/override/conflict/audit tabs and a Run-Sync button. Dashboard/Opportunities/
  Expected Range still show the EGX HOLIDAY banner (facade is transparent).
- **Pre-flight (today):** `NON_TRADING_DAY` / `HOLIDAY_CONFIRMED` / next `2026-07-26`,
  exit 0.
- **Sync:** `--offline` and a real dry-run both returned `CALENDAR_SOURCE_UNAVAILABLE`
  (EGX web is not reachable from this sandbox — `ConnectionResetError`) and exited 0 with
  **zero** confirmations — the honest degraded behavior. A synthetic explicit-official
  announcement is discovered as `NEEDS_REVIEW` (never auto-confirmed); an ambiguous
  "next Thursday / on the occasion of" announcement is forced to `NEEDS_REVIEW`.
- **Tests (22 new):** fixed & moved holiday, weekend, normal day, override-beats-official,
  built-in fallback, pending→safe-gating, unknown-closure→uncertain, early-close/late-open
  partial sessions, next-session, confirm/reject audit, no-silent-rewrite, conflict
  detection, ambiguous→NEEDS_REVIEW, sync idempotency + hash-versioning, source-unavailable,
  pre-flight verdicts, CSV export, import smoke. **Full regression: 494 passed.**

## Honest limitations / deferrals
- **Live EGX parsing is a validated *framework*, not a live-confirmed parser.** The EGX
  site is unreachable from this environment, so the HTML/PDF extractors are exercised
  against synthetic fixtures only; real-site selectors may need tuning on first online
  run. The conservative design means the failure mode is "everything → NEEDS_REVIEW /
  SOURCE_UNAVAILABLE", never a false auto-confirm.
- **No scheduled sync task was registered** (as required) — do so only after reviewing a
  real online dry-run.
- **Phase 10 live-anomaly check** (declare `SESSION_STATUS_UNCERTAIN` when a confirmed
  trading day shows no post-10:00 market progression) is modeled by the status enum and
  the pre-session safe-gate, but a dedicated intraday watcher script is not yet wired;
  the safe default (calendar-confirmed trading day proceeds; feed silence alone is never
  a holiday) holds.
