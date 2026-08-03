# ORB Shadow Automation Runbook

How the unattended daily Shadow workflow runs, what it guarantees, and what it
deliberately refuses to do.

**Research Only. Production execution is disabled.** The orchestrator observes,
reconstructs and reports. It emits no BUY, SELL, order, position, size or
alert; no state in its vocabulary can represent a trade. It never starts, stops
or configures the Rubix collector.

---

## 1. What it replaces

Previously the operator ran four things by hand every session: `--follow` before
10:00, `--list-runs` afterwards, copy a run id, then `--reconstruct
--compare-live-run-id <id>`. The copy-paste step was the fragile one — the wrong
id produces a plausible report about the wrong run.

One process now does the whole day:

```
pre-session checks -> wait -> Lane A live -> stop at 14:15 ->
locate the live run automatically -> Lane B reconstruction ->
cross-run comparison -> Markdown report
```

## 2. Architecture

`scripts/run_orb_shadow_orchestrator.py` drives the existing Phase 2C services
**in-process**. It does not shell out to `run_orb_shadow_session.py` and scrape
console text: an exit code cannot tell you whether Lane A committed, and a
parsed run id is exactly the manual step being removed.

Reused unchanged: the read-only source reader, the shadow session service, the
repository, run discovery, the active-universe filter, the reconstruction lane,
the cross-run comparison, the session classifier and the config identities.
Nothing from Phase 2C is duplicated.

New: `scalping_orb/shadow_calendar.py` (trading-day boundary),
`scalping_orb/shadow_orchestrator.py` (states, lease, criteria), migration 7.

## 3. Daily workflow

**Pre-session (~09:40 Cairo).** Trading-day check → source exists → opens
`mode=ro` → `query_only` confirmed → collector health from cursor/file progress
→ destination independent and safe → run registered → **lease acquired** →
pre-session health recorded.

**Live (from ~09:45).** Waits until `--start-lead-minutes` before the open,
heart-beating the lease, then runs the `--follow` workflow with
`--active-universe-only` and `--stop-at-continuous-end`. Lane A is append-only
and the cursor commits transactionally with its batch. The runner classifies the
session itself; the orchestrator never upgrades it.

**Post-session.** Selects the live run by exact match, runs Lane B, passes the
run id into the cross-run comparison automatically, writes the report, exits.

## 4. Trading calendar — fail closed

`core.egx_session._resolve_holidays` swallows exceptions and returns an **empty
set** when the holiday calendar cannot be loaded. Fine for display code;
unacceptable unattended, because it silently makes every weekday a trading day.

The orchestrator uses its own boundary:

| Status | Trading? |
|---|---|
| `TRADING_DAY` | yes |
| `SPECIAL_TRADING_DAY` (explicit override) | yes |
| `WEEKEND` (Fri/Sat) | no |
| `CONFIGURED_HOLIDAY` | no |
| `EXCEPTIONAL_CLOSURE` | no |
| `CALENDAR_UNAVAILABLE` | **no — refuses to start** |

Availability is typed separately from the day decision, because "no holidays"
and "the calendar broke" are different operator problems:

| Availability | Usable? | Meaning |
|---|---|---|
| `AVAILABLE` | yes | holidays resolved |
| `VALID_EMPTY_CALENDAR` | **yes** | asserted empty — a structurally valid calendar with no holidays in range |
| `CALENDAR_LOAD_FAILED` | no | could not resolve, or an *unattested* empty result |
| `CALENDAR_MALFORMED` | no | override file unreadable or invalid |

An empty set from the shared helper is `CALENDAR_LOAD_FAILED` by default,
because that helper swallows the exception that would tell the two apart. Pass
`--allow-empty-calendar` to assert it is genuinely empty — that promotes it to
`VALID_EMPTY_CALENDAR`, which **is** usable. This distinction means a
deliberately empty calendar is no longer rejected, without weakening
fail-closed behaviour for a real failure.
Optional local overrides via `--calendar-overrides` (JSON with
`exceptional_closures` and `special_trading_days`). The calendar identity is
persisted with every run, so a decision is reproducible.

## 5. Single-instance safety

A **transactional database lease**, not a lock file. A lock file left by a hard
kill stays stale forever and eventually gets deleted by hand — which is how two
runners end up writing at once.

Recorded per lease: instance id, PID, machine identity (hashed hostname, not a
user name), session date, acquired-at, last heartbeat, expiry.

- Acquisition happens entirely inside one `BEGIN IMMEDIATE`, so two processes
  racing at 09:40 cannot both see "free".
- A second instance is refused with the holder's details.
- An **active** lease cannot be stolen at any point before expiry.
- A **stale** lease (expired, or explicitly released) is taken over cleanly.
- Heartbeats extend the lease; a displaced holder's heartbeat fails.
- Scope is `(session_date, source_identity)` — two sources are not one workflow.

The Windows task additionally sets `MultipleInstances=IgnoreNew`. That is a
second belt, not the guarantee.

## 6. Recovery

| Failure | Behaviour |
|---|---|
| Source missing before session | `SKIPPED_SOURCE_UNAVAILABLE`, recorded, exit |
| Source unreadable during session | cycle records the failure, polling continues; a gap beyond the limit disqualifies FULL |
| Crash **before** cursor commit | cursor unchanged; restart re-reads the same rows; `INSERT OR IGNORE` makes the retry harmless |
| Crash **after** cursor commit | restart resumes exactly after the committed row |
| Windows restart | rerun the command; the stale lease ages out |
| No eligible live run | fails visibly; **no fabricated all-`HISTORICAL_ONLY` comparison** |
| Reconstruction / comparison / report failure | recorded as a failure, run ends `SESSION_FAILED`, verdict never FULL |

Rules that hold throughout: the cursor never advances before successful
persistence; Lane A stays append-only; reconstruction and comparison are
idempotent; there is no fake FULL and no fake successful report; failures remain
visible in the final status and in the report.

## 7. Commands

**Manual orchestrator run** (from the worktree):

```bash
python scripts/run_orb_shadow_orchestrator.py --session-date 2026-08-04 --rubix-db-path "F:\EGX_AI_Trader\data\rubix_live_market.db" --research-root "data\research\orb_full_shadow" --no-network
```

**Status — read-only, starts nothing, takes no lease:**

```bash
python scripts/run_orb_shadow_orchestrator.py --status --session-date 2026-08-04 --rubix-db-path "F:\EGX_AI_Trader\data\rubix_live_market.db"
```

**Retry post-session only — never creates Lane A:**

```bash
python scripts/run_orb_shadow_orchestrator.py --retry-post-session --session-date 2026-08-04 --rubix-db-path "F:\EGX_AI_Trader\data\rubix_live_market.db"
```

`--resume` continues the current session's workflow from committed state.

`--maximum-wait-iterations` bounds the pre-start wait so a stopped or
misconfigured clock fails with `WAIT_LOOP_EXCEEDED` instead of spinning. The
clock and sleeper are injectable seams, so tests never wait on real time — a
mutation once reached the real wait loop and left four sleeping pytest
processes, which is why both are now parameters.

The manual Phase 2C commands (`--follow`, `--list-runs`, `--reconstruct`) remain
fully supported and are asserted by test.

## 8. Windows Task Scheduler

Install (nothing is registered until you run it):

```powershell
.\scripts\windows\install_orb_shadow_scheduled_task.ps1 -WorktreePath "F:\EGX_ORB_full_shadow_live_wt" -PythonExe "F:\EGX_AI_Trader\venv\Scripts\python.exe" -RubixDbPath "F:\EGX_AI_Trader\data\rubix_live_market.db"
```

Add `-WhatIfOnly` to print the exact command without registering.

Remove:

```powershell
.\scripts\windows\remove_orb_shadow_scheduled_task.ps1
```

**Timezone.** Task Scheduler triggers fire on **local** wall-clock time; the
exchange runs on Africa/Cairo, which observes DST. The installer resolves the
Cairo timezone, compares it with the machine timezone, and:

- machine already on Cairo time → uses `-CairoStartTime` directly and says so;
- machine elsewhere → converts explicitly, prints `TIMEZONE MISMATCH` with both
  zones and the resolved local trigger, and warns that a Cairo DST change
  requires re-running the installer;
- Cairo timezone unresolvable → **throws**, and asks for
  `-LocalStartTimeOverride HH:mm`.

It never silently assumes the machine timezone.

The task: runs Sunday–Thursday at the resolved local time, as the current user,
**no stored credential**, `-RunLevel Limited` (no administrator), `MultipleInstances
IgnoreNew`, bounded `ExecutionTimeLimit`, restarts twice on failure, working
directory set to the worktree. It launches the orchestrator and **nothing
else** — never Rubix, never the production launcher.

The weekday schedule is a coarse filter only. **The Python trading-day check is
authoritative**, so a holiday falling on a Tuesday still exits with
`SKIPPED_NON_TRADING_DAY`. Removal unregisters the schedule and deletes no
database, log or report — removing a schedule must never destroy evidence.

## 9. Generated paths

| Kind | Path | Committed? |
|---|---|---|
| research DB | `data/research/orb_full_shadow/orb_full_shadow_YYYY-MM-DD.db` | no (ignored) |
| logs | `logs/orb_shadow/YYYY-MM-DD/orchestrator.log` | no (ignored) |
| reports | `reports/audits/strategies/orb_first_pullback/full_shadow/YYYY-MM-DD/` | no (ignored) |

All derived from the session date. A production database is refused as a write
target by the repository deny-list, the content probe, and the
source/destination collision guard.

## 10. The report

`FULL_SHADOW_SESSION_REPORT.md` covers classification, runner timing and whether
it started before 10:00, source health, cursor progress, freshness distribution
(median/p90/p95/% over budget/negative-lag), 1m and 5m coverage, opening-range
readiness, Lane A and Lane B state counts, the comparison taxonomy,
`ENTRY_READY_RESEARCH` in each lane, operational failures, and the FULL criteria
table with any unmet items named.

It states explicitly that **reconstructed results were not necessarily
actionable live**. It reports no profitability, win rate, expectancy, position
size or order instruction, because none exists.

## 11. Verdict

`FULL_SHADOW_SESSION_OBSERVED` requires **all thirteen** criteria: started
before session start, covered through 14:15, opening range observed live,
sufficient heartbeat coverage, cursor progressed, no excessive polling outage,
sufficient exchange-minute coverage, graceful shutdown, Lane A persisted,
reconstruction completed, cross-run comparison completed, report completed, no
production execution.

Otherwise `PARTIAL_SHADOW_SESSION`, or `FAILED_SHADOW_SESSION` when the live
lane failed. **Post-session data availability alone never produces FULL**, and
`FullSessionCriteria.verdict` has no force/override parameter.

## 12. Prohibitions

Do not calibrate thresholds from one session. Do not treat
`ENTRY_READY_RESEARCH` as a trade signal — in either lane. Do not treat a Lane B
result as something that was actionable live. Do not start, stop or restart the
collector. Do not report profitability. Do not add alerting, paper trading or
Dashboard integration to this layer.
