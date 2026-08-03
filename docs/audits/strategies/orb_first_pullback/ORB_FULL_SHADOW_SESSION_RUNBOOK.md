# ORB Full Shadow Session — Operating Runbook

How to run the first full live Shadow session, and what it does and does not
prove.

**Research Only. Production execution is disabled.** The highest state anything
below can reach is `ENTRY_READY_RESEARCH`, which is a research candidate and is
never a buy instruction. Nothing here places an order, sizes a position, sends
an alert or touches the Dashboard, because no such code path exists.

---

## 0. What this session is for

To observe one complete continuous session end to end and record, honestly,
**what was knowable live versus what only reconstruction can see.** It is not to
find setups, and not to tune anything.

---

## 1. Pre-session checklist

Run through this the evening before, and again at T-20 minutes.

| # | Check | How |
|---|---|---|
| 1 | The session date is a regular trading day | `is_regular_trading_day` in `core.egx_session` |
| 2 | The production collector is already running and healthy | inspect the process list; **do not start, stop or restart it** |
| 3 | The Rubix source path is correct and readable | `--list-runs` needs no source; a wrong path fails fast on the live command |
| 4 | The research DB path is **not** the source, nor its `-wal`/`-shm` | enforced by `_assert_distinct_databases`, but check the command line |
| 5 | The research DB path is on an ignored path (`data/research/…`) | `git check-ignore -v <path>` |
| 6 | Disk headroom for the research DB | a dense session is tens of MB |
| 7 | Clock sanity | negative receive lag is recorded as clock skew, not treated as fresh |
| 8 | You are in the full-session worktree, not main | `git rev-parse --abbrev-ref HEAD` |

**Never** start the collector, stop it, restart it, change its subscriptions,
checkpoint or truncate its WAL, or take an exclusive lock on its database.

## 2. Start window

Start the runner **09:45–09:50 Cairo**, i.e. 10–15 minutes before the continuous
session opens at 10:00.

This is not a nicety. A run started at or after 10:00 can never observe the
opening range forming, and is classified `PARTIAL_SHADOW_SESSION` for that
reason alone. There is no override: `classify_session` has no `force`, `full` or
`override` parameter, and `--smoke` only ever downgrades.

| Start time | Best achievable classification |
|---|---|
| ≤ 10:00 Cairo, healthy through 14:15 | `FULL_SHADOW_SESSION` |
| after 10:00 | `PARTIAL_SHADOW_SESSION` |
| after the close | `PARTIAL_SMOKE_SESSION` |

## 3. The live command

Run from `F:\EGX_ORB_full_shadow_wt`. Set `<SESSION_DATE>` to the actual session
date.

```bash
python scripts/run_orb_shadow_session.py --follow --rubix-db-path "F:\EGX_AI_Trader\data\rubix_live_market.db" --research-db-path "data\research\orb_full_shadow_<SESSION_DATE>.db" --session-date <SESSION_DATE> --poll-seconds 15 --lateness-grace-seconds 90 --allowed-polling-gap-seconds 300 --minimum-heartbeats 60 --minimum-exchange-minutes 200 --active-universe-only --stop-at-continuous-end --max-runtime-seconds 18000 --no-network
```

What each choice means:

- `--follow` — poll until an explicit stop condition. This run holds **both**
  lanes, so it produces the genuine Lane A vs Lane B comparison at shutdown.
- `--active-universe-only` — evaluate Phase 2B only for active-universe members
  with a verified Rubix mapping and operational eligibility. **Observation is
  not narrowed**: source rows, cursor progress, deduplication and every quality
  total still cover every symbol the collector delivered.
- `--stop-at-continuous-end` — stop at 14:15 Cairo.
- `--max-runtime-seconds 18000` — a 5-hour ceiling so the process cannot outlive
  the session if the clock check fails.
- `--no-network` — default and only supported behaviour.

The banner must show `RESEARCH ONLY`, `PRODUCTION EXECUTION DISABLED`,
`mode=ro, query_only=ON`, and the universe-filter state. If it does not, stop.

## 4. Health checks during the session

Watch the per-cycle output and the research DB.

| Signal | Healthy | Act if |
|---|---|---|
| `session_loads` per cycle | exactly 1 | ever >1 — the batching contract broke |
| `event_rows_read` growth | roughly linear in rows | growing like the square of symbol count |
| cursor `last_source_id` | strictly increasing | flat for several cycles while the market is open |
| `live_status` | `LIVE_SHADOW_HEALTHY` | sustained `LIVE_SHADOW_STALE` |
| polling gap | < 300 s | a gap beyond the limit disqualifies `FULL` |
| heartbeats | one per cycle | none for minutes |

At ~15 s polling over 4h35m expect roughly 1,100 cycles.

## 5. Stop conditions

The runner stops on the first of: continuous-session end (14:15), maximum
runtime, or Ctrl+C. **Ctrl+C is graceful** — the in-flight cycle finishes and
commits, cursor included, then the loop exits. Do not `kill -9`; a hard kill
loses the final cycle's commit (though the cursor stays consistent, because it
commits in the same transaction as its batch).

## 6. After the session — list the runs

```bash
python scripts/run_orb_shadow_session.py --list-runs --research-db-path "data\research\orb_full_shadow_<SESSION_DATE>.db"
```

Read-only: it opens no source and evaluates nothing. It prints run id, session
date, mode, classification, Lane A / Lane B row counts, cycles, universe-filter
state, and completion — plus the full run ids at the end. The source appears
only as an opaque path hash; no credential and no quote payload is shown.

**Copy the `run_id` of the `FOLLOW` run with `LANE_A > 0`.** That is the only
kind of run eligible as a comparison source.

## 7. Post-session reconstruction with cross-run comparison

```bash
python scripts/run_orb_shadow_session.py --reconstruct --rubix-db-path "F:\EGX_AI_Trader\data\rubix_live_market.db" --research-db-path "data\research\orb_full_shadow_<SESSION_DATE>.db" --session-date <SESSION_DATE> --lateness-grace-seconds 90 --active-universe-only --compare-live-run-id <LIVE_RUN_ID> --no-network
```

`--compare-latest-live-run` may replace `--compare-live-run-id <id>` when
exactly one eligible run exists. It requires an exact match on session date,
source identity, config identity, a non-reconstruction mode, Research Only
status, and Lane A rows present. **Zero matches fails with a clear message
rather than emitting an all-`HISTORICAL_ONLY` report that would look like a
finding and is not one. Multiple matches demand an explicit run id — the
selector never guesses.**

The reconstruction run:

- writes **Lane B only** and never a Lane A row (it does not even call the live
  evaluator);
- reads the chosen prior run's Lane A **read-only**;
- persists comparison rows naming **both** `live_run_id` and
  `reconstruction_run_id`, in `orb_shadow_cross_run_comparison`;
- preserves each side's opening-range version and state identity;
- is idempotent — rerunning the same pair inserts nothing further.

Neither run is merged into the other. The comparison is a third record *about*
the pair.

## 8. Reading the comparison

Seven categories, deterministic precedence, most decisive first:

```
DATA_UNAVAILABLE → HISTORICAL_ONLY → LIVE_ONLY → IDENTICAL
                 → FRESHNESS_REJECTED_LIVE → OPENING_RANGE_REVISED
                 → STATE_DIFFERENCE
```

Every row also stores `difference_evidence_json` — the exact facts that selected
the category — so a stored classification can be **re-derived rather than
trusted**.

Two things worth knowing before you read it:

- **Freshness outranks an opening-range revision.** When both are true,
  staleness is why the state was unreachable live; the revision is secondary.
- **"Freshness" covers two different things.** The feed can be stale, *or* the
  live-decision capability can be disabled while the feed is perfectly healthy.
  Both count. In the earlier partial smoke, keying only on feed status
  mis-attributed all 34 lane differences to opening-range revisions.

`STATE_DIFFERENCE` means a real divergence whose cause was **not** established.
It is not a placeholder for "probably the opening range".

## 9. Expected reports

Under the ignored output directory:

| File | Contents |
|---|---|
| `shadow_run_summary.json` | classification, counts, lag stats, filter state, comparison scope |
| `shadow_cycle_metrics.csv` | per-cycle timings, cursor range, session loads |
| `shadow_session_quality.csv` | one row of session quality |
| `shadow_live_states.csv` | Lane A observations |
| `shadow_reconstruction_states.csv` | Lane B results, labelled `HISTORICAL_REPLAY` |
| `shadow_live_vs_reconstruction.csv` | comparison, with both run ids and the evidence |

All generated output stays under ignored `reports/` and `data/research/`. None
of it is committed.

## 10. Failure recovery

| Failure | Do this |
|---|---|
| Runner crashes mid-session | restart the **same command**. The cursor commits in the same transaction as its batch, so it resumes exactly after the last committed row: no loss, no duplicate. |
| Source briefly unreadable | the cycle records `LIVE_SHADOW_SOURCE_UNAVAILABLE` and continues; a gap beyond `--allowed-polling-gap-seconds` disqualifies `FULL` |
| Started late by accident | let it run — the data is still useful; the classification will honestly say `PARTIAL_SHADOW_SESSION` |
| Wrong `--session-date` | classification computes from the runner's real start/finish against that date's window, so backdating makes a run look *more* partial, never full |
| `--list-runs` shows no eligible live run | you have no Lane A rows; a `--once` or post-close run never produces them. Only an in-session `--follow` run does. |
| Reconstruction refuses to select a run | that is the guard working. Use `--list-runs` and pass an explicit `--compare-live-run-id`. |
| Disk full | stop gracefully with Ctrl+C; committed cycles are intact |

## 11. Prohibitions

- **Do not calibrate thresholds from this session.** One session is not a
  sample. Defaults remain `INITIAL_RESEARCH_DEFAULTS` and are not optimal.
- **Do not treat `ENTRY_READY_RESEARCH` as a trade signal.** It is a research
  candidate. It is not a BUY, not an alert, not a paper trade, not sized, and
  carries no profitability claim.
- **Do not treat a Lane B result as something that was actionable live.** That
  is exactly what the comparison exists to distinguish.
- Do not loosen a threshold, admit wick breakouts, use incomplete bars,
  substitute vendor daily candles, or drop to 1-minute closes to obtain more
  candidates.
- Do not start, stop or restart the collector. Do not register this runner with
  the production launcher.
- Do not report profitability. No performance measure exists.

## 12. Success criteria for `FULL_SHADOW_SESSION_OBSERVED`

The session may claim this verdict only when **all** hold:

1. the runner began **before** the continuous session started;
2. the continuous session remained covered through at least 14:15;
3. the opening range was observed **live** (not reconstructed);
4. the source cursor progressed throughout;
5. polling outages stayed within `--allowed-polling-gap-seconds`;
6. heartbeat coverage met the configured minimum;
7. exchange-minute coverage met the configured minimum;
8. the auction stayed separated from continuous-session state;
9. freshness and latency distributions were recorded;
10. complete 1-minute and 5-minute bars were produced;
11. Lane A remained append-only, and delayed evidence did not rewrite it;
12. Lane B reconstruction was deterministic and idempotent;
13. a genuine cross-run Lane A / Lane B comparison was generated;
14. shutdown was graceful;
15. no production execution occurred.

The runner classifies 1–8 itself and will not be talked into `FULL`. Items 9–15
are verified from the generated reports and the research database.

**Achieving `FULL_SHADOW_SESSION_OBSERVED` still does not mean live-ready,
calibrated, profitable or production-ready.** It means one session was observed
cleanly enough to reason about.
