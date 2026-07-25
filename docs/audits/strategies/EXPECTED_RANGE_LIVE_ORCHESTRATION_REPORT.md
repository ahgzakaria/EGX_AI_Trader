# Expected Range Scalper — Live Paper Session Orchestration

**The paper pipeline is now a proper three-stage daily orchestration**, replacing
the single late post-session scan. Recording remains records-only — no orders, no
auto-execution, production disabled. Range percentiles, score weights and the
fixed +2%/-2% target/stop are unchanged.

| Flag | Value |
|---|---|
| `paper_enabled` | true |
| `production_enabled` | **false** |
| `decision_support_only` | true |
| `automatic_execution` | **false** |
| `broker_orders_enabled` | **false** |

## Three stages (Africa/Cairo)

| Stage | Script | When | Role |
|---|---|---|---|
| A — Pre-session | `run_expected_range_pre_session.py` | 09:45 | Immutable snapshot + manifest from completed history only. A snapshot after 10:00 is `SNAPSHOT_LATE`. |
| B — Live monitor | `run_expected_range_live_monitor.py` | 09:55 → 14:15 | Long-running, event-driven, **durable & restart-safe**: reads only new Rubix events past a SQLite cursor, drives 7 scenario state machines, appends only real transitions, immutable READY signals (one per activation cycle), no new entry after 14:15, single-instance lock. |
| C — Finalizer | `run_expected_range_outcome_finalizer.py` | 14:40 | Chronological outcomes with per-horizon maturity; classifies session completeness; idempotent. |

Durability lives in `data/expected_range_paper_state.db` (WAL): event cursor,
per-(symbol,scenario) state + activation cycle, signals with a
`UNIQUE(session,symbol,scenario,activation_cycle)` guard (atomic, so a restart can
never duplicate a signal), the transition log and per-session counters.

## Declared operational tolerances (fixed in advance)

- Snapshot valid only if created **before 10:00** Cairo.
- Monitor start tolerance **10:05** Cairo.
- Unexplained processing gap threshold **15 min** → `DATA_GAP`.
- Continuous close **14:15**; auction **14:15–14:25** (stored separately).

## Session classifications (only one counts)

`COMPLETE_FORWARD_SESSION` is the **only** class counted toward the 20-session
minimum. Others: `PARTIAL_LATE_START`, `PARTIAL_EARLY_STOP`, `DATA_GAP`,
`INVALID_SNAPSHOT`, `MONITOR_NOT_RUNNING`, `PILOT_SESSION`, `NON_TRADING_DAY`.

## Outcome-horizon maturity (never finalize a forming horizon)

Each 1/3/5/10/20-minute horizon carries an explicit status: `PENDING`, `MATURED`,
`UNAVAILABLE_SESSION_END`, `DATA_INSUFFICIENT`, `INVALID`. A horizon that would run
past 14:15 is `UNAVAILABLE_SESSION_END` — **never** recorded as NEITHER. Returns are
stored only for `MATURED` horizons.

## Recording-bias controls (Phase 7)

Per session the monitor keeps separate counters: `evaluations_processed`,
`transitions_recorded`, `ready_activations`, `rejected_activations`,
`unchanged_suppressed`. An unchanged evaluation is **suppressed** — the same state
is never appended for every quote. All states are captured: initial WAIT,
transitions, READY, INVALID, RANGE_CONSUMED, SPREAD_TOO_WIDE, DATA_STALE, etc.

## Scheduling

Three Windows scheduled tasks, **Sunday–Thursday** (Fri/Sat excluded; EGX holidays
honored inside the Python stages), venv-activated, dated logs under
`logs/expected_range_paper/`, non-zero exit on real failure, `WakeToRun`,
single-instance (`IgnoreNew`); the monitor restarts up to 3× on failure:

- `EGX Expected Range Pre-Session` — 09:45
- `EGX Expected Range Live Monitor` — 09:55 (runs through 14:15)
- `EGX Expected Range Outcome Finalizer` — 14:40

## Evidence counters (Phase 8)

`paper_evidence.py` counts **complete forward sessions only** and tracks Pilot /
Partial / Complete sessions, total & counted signals, matured-executable, pending
and invalid outcomes. Judgment begins only at ≥20 complete sessions, ≥30 executable
signals, ≥20 per scenario. Production is never recommended by this tooling.

---

## The 2026-07-22 pilot

Classified **`PILOT_SESSION`** — flags `PILOT_PARTIAL_SESSION`, `LATE_START`,
`NOT_COUNTED_TOWARD_FORWARD_SESSION_MINIMUM`. Full metadata in
`reports/expected_range_paper/2026-07-22/PILOT_RESULTS.json`.

| Field | Value |
|---|---|
| Actual recorder start | ~13:07 Cairo (post-session recorder) |
| First available 07-22 event | 09:22:39 Cairo |
| Last observed 07-22 event | 16:46:02 Cairo |
| Missing continuous window | 10:00–13:07 Cairo (**~187 min unobserved**) |
| Pre-session snapshot before 10:00 | **No** |
| All outcome horizons available | **No** |
| End-of-session results finalized | **No** |
| Original pilot READY signals | 7 (kept for technical validation only) |

**Excluded** from the 20-session minimum, scenario-profitability judgment and
production-readiness statistics. Kept only to prove the components run.

**Orchestration re-run (validation):** re-running the new three-stage pipeline over
the full 07-22 session (session-scoped from 10:00, drained ~179k events, cursor
reached 14:56 Cairo) produced **0 READY signals** — correctly, because (a) the daily
history was `HISTORY_STALE` (ranges a session behind, since the local cache lacked
the latest completed session) and (b) the offline replay anchored past 14:15, where
no new entry may open. The session is classified `PILOT_SESSION` and does not count.
Counters: 1,855 evaluations, 1,855 transitions, 1,761 rejected activations, 0
unchanged-suppressed (single drain pass). This confirms the pipeline is honest under
stale history and the 14:15 cutoff — it neither fabricated signals nor counted the
session.

---

## Final questions

1. **Is 2026-07-22 complete or partial?** **Neither counts** — it is classified
   `PILOT_SESSION` (late start, no valid pre-10:00 snapshot) and is **excluded** from
   the 20-session minimum and from scenario-profitability judgment.
2. **Did its snapshot exist before the session?** **No** — the pilot began ~13:07
   Cairo with no valid pre-session snapshot; a snapshot generated intraday is
   `SNAPSHOT_LATE`. Going forward, Stage A creates it at 09:45.
3. **Were any outcome horizons finalized prematurely?** **No** — horizons carry
   PENDING/MATURED/UNAVAILABLE_SESSION_END; a horizon past 14:15 is
   UNAVAILABLE_SESSION_END, never NEITHER.
4. **Can the monitor survive a restart without duplication?** **Yes** — durable
   SQLite cursor + state + a unique signal key make it idempotent; proven by
   `test_monitor_restart_no_duplicate_signal` and the reset/reactivation test.
5. **Are all three scheduled stages registered?** **Yes** — Pre-Session (09:45),
   Live Monitor (09:55, restart-on-fail, single-instance), Finalizer (14:40),
   Sun–Thu.
6. **Does the monitor operate throughout continuous trading?** **Yes** — it is a
   long-running event-driven loop from ~10:00 to 14:15, not a single post-session
   scan; it reads only new events past a durable cursor.
7. **Are unchanged evaluations suppressed?** **Yes** — only real transitions are
   appended; the `unchanged_suppressed` counter records the rest.
8. **Which sessions count toward the 20-session requirement?** **Only**
   `COMPLETE_FORWARD_SESSION`. The pilot and any partial/late/gap sessions do not.
9. **Are all production and execution flags still disabled?** **Yes** —
   `production_enabled=false`, `automatic_execution=false`,
   `broker_orders_enabled=false`.
10. **Did any strategy parameter change?** **No** — TP/SL, ranking weights and range
    percentiles are unchanged; only orchestration, durability and classification
    were added.

## Verification

Full regression passes including 19 new orchestration tests (snapshot timing,
session classification, horizon maturity PENDING-vs-NEITHER, cursor persistence,
restart-no-duplicate, activation-cycle persistence, no-entry-after-14:15,
single-instance lock, transition suppression, finalizer idempotency,
partial/pilot exclusion) plus the app/dashboard smoke test.
